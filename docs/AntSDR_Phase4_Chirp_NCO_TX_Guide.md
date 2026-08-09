# AntSDR E200 - Phase 4: chirp NCO + TX from fabric (Part I rungs I2 + I3)

Follow-up to the Phase 3 guide (its "Where this leads" section was the sketch;
this is the worked version). Assumes Phase 3 is complete: the AXI-Lite register
bank in `rx_tap.vhd` is proven on hardware - MAGIC/SCRATCH/COUNT read correctly,
`ramp_en` toggles live over UIO via `rxtap`.

Same contract as before: **this guide specifies and explains, you write the
code.** Exact text is given where a typo costs a synthesis run (Tcl, port
names, register offsets); behavioral specs, failure modes, and hints everywhere
the learning lives (the NCO, the shadow/commit CDC, the testbench).

Scope decided up front: this phase covers **I2 (chirp NCO in fabric) and I3
(TX from fabric)**. The architecture section and the register map are designed
through I5 (dechirp + decimation) so nothing built now gets torn up later, but
the worked exercises stop at the TX mux. Dechirp is Phase 5.

---

## The three rules of Phase 4

1. **The golden model is the law.** `dsp.generate_chirp` defines what a chirp
   is. The NCO is correct when a fixed-point numpy model of it matches the VHDL
   bit-for-bit AND that fixed-point model matches the float chirp to below the
   DAC's noise floor. Two separate comparisons, two separate failure causes.
2. **Nothing changes mid-chirp.** Every parameter that shapes the waveform
   (FTW start, slope, sweep length) latches into the l_clk domain only at a
   chirp boundary, via the shadow/commit discipline in Section 3. A parameter
   that can change mid-sweep is a spectral-splatter generator with a register
   interface.
3. **Power-on defaults = current behavior.** CTRL resets to zero, and zero
   means: DMA drives the DAC, TDD drives the DMA sync, no debug mux, no ramp.
   A bitstream with your core in it and nobody talking to the registers must be
   indistinguishable from today's radar. This is the bypass-bit philosophy from
   TODO Part I, applied to every new bit.

---

## 0. Where everything goes (the architecture, once)

See `fmcw_fabric_architecture.svg` next to this file for the picture. In words:

The RX side you already own: `axi_ad9361 -> rx_fir_decimator -> rx_tap ->
cpack -> adc_dma`. The TX side today: `dac_dma -> tx_upack ->
tx_fir_interpolator -> axi_ad9361/dac_data_i0,q0`. Channel 1 (dac_*_i1/q1,
adc_*_1) passes by untouched on both sides.

**Decision (2026-07-27): `rx_tap` grows into a single core module** - suggested
name `fmcw_core` - with ports on BOTH sides:

- the existing RX ports (rx_fir_decimator in, cpack out), where the future
  dechirp mixer and decimator will sit;
- new DAC-side ports: data in from `tx_fir_interpolator`, data out to
  `axi_ad9361/dac_data_i0,q0`, with a mux selecting DMA passthrough or NCO;
- one AXI-Lite slave, one register map, same 0x43C10000 address.

Why one module and not a separate `chirp_gen` block: the dechirp mixer (Phase
5) needs a *delayed, conjugated replica* of the exact TX NCO output. If TX
generation and dechirp live in one entity, phase coherence and the delay line
are internal wiring, simulable in one VUnit testbench. Split across two block
design cells, the replica (or its phase word) becomes an inter-block bus whose
alignment you get to debug in hardware. Both taps are in the same `l_clk`
domain, so nothing forces the split. One module, one register file, one tb.

What lands where on the ladder:

- **I2 (this guide, Section 5):** NCO inside the core, muxed onto the RX
  output channel 0 by a CTRL debug bit. The radar GUI spectrogram becomes the
  fabric debug scope. No TX ports yet if you want to stage it - but adding the
  ports and leaving the mux at passthrough costs one build, so do both at once
  if you prefer.
- **I3 (this guide, Section 6):** the DAC-side mux goes live: `tx_src = NCO`
  and the board transmits a fabric-generated chirp while Python still does
  everything else (frame sync, mix, process_cpi).
- **I4/I5 (Phase 5):** complex multiply RX x conj(replica), then CIC/FIR
  decimation, `IF_SEL` switches cpack input between raw RX and decimated IF.
  The register map below already reserves their rows.

### Frame start: the answer is already in the block design

The question from the roadmap - "how does the host learn where frames start
once frame sync dies?" - turns out to be pre-answered by the stock e200 fabric:

`axi_ad9361_adc_dma` is instantiated with `CONFIG.SYNC_TRANSFER_START {true}`,
and its `sync` input pin is wired to `axi_tdd_0/tdd_channel_1`
(`system_bd.tcl`, TDD block near the end). The ADI DMAC's contract with that
parameter: **a transfer does not begin until the `sync` input is high on an
accepted source beat; leading data is dropped.** In other words, the fabric
already contains a hardware trigger that aligns every RX DMA transfer to a
pulse of your choosing.

Two consequences:

1. Your core emits a `chirp_start` pulse; route it to the DMA sync pin and
   every `read_block()` starts at a chirp boundary. Since all chirps are
   identical, every chirp boundary IS a CPI boundary - the host-side CPI is
   pure framing. Frame sync (the leakage correlation) is then dead code in
   IF mode. That is the I6 endgame.
2. **The trap:** `TDD_DEFAULT_POL` is `0b010` - bit 1 set - which means
   `tdd_channel_1` idles HIGH when the TDD engine is disabled (which it is,
   in normal operation). That is the only reason RX streaming works today:
   sync is permanently asserted, so SYNC_TRANSFER_START gates nothing.
   Therefore you must **MUX** your pulse against the TDD signal, not OR it -
   an OR with an idles-high signal is a constant 1 and your pulse does
   nothing. And the mux must default to TDD passthrough (rule 3), or a
   bitstream with `nco_en = 0` never asserts sync and **every RX capture
   hangs forever** - the DMA waits for a pulse that never comes. This is the
   Phase 4 equivalent of the Phase 3 bus hang.

During I3 you do NOT flip this mux yet. Ethernet still carries raw RX, Python
frame sync still runs - and that is a feature, see the I3 exit test.

### Digital latency: name it now, calibrate it in Phase 5

Once TX is fabric-generated, the round trip NCO -> dac port -> AD9361 digital
TX path -> (digital loopback point, or DAC/RF/ADC) -> RX digital path -> adc
port -> rx_fir_decimator -> core is a **fixed but unknown** number of samples:
interface pipelines plus AD9361 half-band/FIR group delays. One sample at
56.6 MSPS reads as 2.65 m of apparent range, and the budget is tens of
samples, so it is not ignorable.

- In I3 it is harmless: Python frame sync absorbs it, exactly as it absorbs
  the DMA buffer offset today. But where today's offset is random per start,
  the fabric TX offset is **constant** - measure it (I3 exit test) and write
  it down.
- In Phase 5 it becomes the `DECHIRP_DELAY` register: the dechirp replica is
  the NCO output delayed by that many samples (a shift-register/BRAM line),
  so the replica aligns with the leakage return. Calibration procedure:
  digital loopback, sweep the register until the leakage beat lands in range
  bin 0. Expect **different constants for BIST loopback vs the real RF path**
  (the loopback point sits inside the AD9361 digital chain, upstream of parts
  of it) - recalibrate in Part F and keep both values in `config.py`.

---

## 1. Growing rx_tap into fmcw_core

Housekeeping first, and it is load-bearing:

1. **Close the I1 milestone** per `docs/firmware-branch-workflow.md`: in `hdl`,
   merge `feature/rx-tap` into `e200-custom`, push, bump the gitlinks bottom-up
   (plutosdr-fw -> firmware -> radar repo). I1 is proven on hardware; that is
   the definition of merge time.
2. New branch in `hdl` from `e200-custom`: `feat/chirp-nco`.
3. Rename: `src/rx_tap.vhd -> src/fmcw_core.vhd`, entity `rx_tap ->
   fmcw_core`. Chase the name through:
   - `system_bd.tcl`: `add_files` path, `create_bd_cell -type module
     -reference fmcw_core fmcw_core_0`, every `ad_connect rx_tap_0/... ->
     fmcw_core_0/...`, and `ad_cpu_interconnect 0x43C10000 fmcw_core_0`.
   - `Makefile` `M_DEPS` (the rx_tap.vhd entry).
   - the testbench (`test/tb_rx_tap.vhd -> test/tb_fmcw_core.vhd`); `run.py`
     globs `src/*.vhd` and `tb_*` so it follows the renames by itself.
4. **New MAGIC**: `0x464D4331` (ASCII "FMC1"). The register map below is a
   superset of RXT1's but the contract changed; software that greps for RXT1
   (any bring-up script, and your own muscle memory) must fail loudly, not
   read garbage politely. Keep MAGIC-bumping as the versioning discipline for
   every future map change.

The new DAC-side ports, following the existing naming (all `l_clk` domain;
these names matter only to you, not to interface inference - AXI inference
already works and none of these are AXI):

```
-- TX side: from tx_fir_interpolator
i_dac_valid_0  : in  std_logic;                                  -- rate strobe, see 4.5
i_dac_data_0   : in  std_logic_vector(G_DATA_WIDTH - 1 downto 0); -- I
i_dac_data_1   : in  std_logic_vector(G_DATA_WIDTH - 1 downto 0); -- Q
-- TX side: to axi_ad9361 dac_data_i0 / dac_data_q0
o_dac_data_0   : out std_logic_vector(G_DATA_WIDTH - 1 downto 0);
o_dac_data_1   : out std_logic_vector(G_DATA_WIDTH - 1 downto 0);
-- DMA sync mux
i_sync_in      : in  std_logic;   -- from axi_tdd_0/tdd_channel_1
o_dma_sync     : out std_logic;   -- to axi_ad9361_adc_dma/sync
```

**Data only - there are no TX enable ports, and there cannot be.** The RX side
needs the core in the enable chain because `cpack/enable_N` are INPUTS and the
core sits between source and sink. TX runs the other way: `dac_enable_i0` is an
OUTPUT of `axi_ad9361`, the interpolator passes it through as `enable_out_0`
(it is just the bus-mux forwarding `dac_enable_$i`), and the sink is
`tx_upack/enable_0`. Every consumer of a TX enable sits UPSTREAM of the core,
and `axi_ad9361` has no dac-enable input at all - it drives that signal. So an
`o_dac_enable_*` has no legal destination; wiring one to `tx_upack/enable_0`
would only replace the interpolator's drive with a delayed copy. Channel 1
(`dac_*_i1/q1`) likewise stays wired straight to `tx_upack` - do not give the
core ch1 TX ports, they would dangle.

(The interpolator's `valid_out_0`/`enable_out_*` handshake toward `tx_upack`
stays wired exactly as it is - the core only intercepts the DATA on its way
into `axi_ad9361`, it does not sit in the upack read-enable loop. That loop
(`logic_or` -> `tx_upack/fifo_rd_en`) keeps running untouched whichever way
the mux points; see the pitfalls for what that means for underflow flags.)

One optional input IS worth taking: `i_dac_enable_0`. In
`axi_ad9361_tx_channel.v` the port you are driving (`dac_data_i0`, internally
`dma_data`) is only selected when `dac_data_sel == 4'h2` (DMA); the default is
the core's own internal DDS. `dac_enable_i0` is exactly that condition. If the
driver has not put the channel in DMA mode, your NCO output is discarded and
the AD9361 transmits its own tone - and the only symptom is "tx_src does
nothing". Exposing that bit in a STATUS register turns a bring-up mystery into
one register read. It is also the hardware reason the "keep pushing the cyclic
buffer from Python" advice below matters: pushing the buffer is what keeps
`dac_data_sel` at DMA.

---

## 2. Register map (FMC1)

Designed through Phase 5; Phase 4 implements down to CHIRP_COUNT plus COMMIT.

| Offset | Name         | Access | Function |
|---|---|---|---|
| 0x00 | MAGIC        | R  | `0x464D4331` "FMC1" |
| 0x04 | CTRL         | RW | bit 0 `ramp_en` (kept from RXT1); bit 1 `nco_en`; bit 2 `tx_src` (0 = DMA passthrough, 1 = NCO); bit 3 `triangle_en`; bit 4 `rx_dbg_mux` (NCO onto RX ch 0); bit 5 `sync_src` (0 = TDD passthrough, 1 = chirp_start) |
| 0x08 | SCRATCH      | RW | unchanged |
| 0x0C | COUNT        | R  | `valid_in` counter, unchanged (live FS meter) |
| 0x10 | FTW_START    | RW | signed 32-bit, shadow (Section 3) |
| 0x14 | FTW_SLOPE    | RW | signed 32-bit, shadow |
| 0x18 | SWEEP_LEN    | RW | samples per sweep leg, shadow |
| 0x1C | CHIRP_COUNT  | R  | counts chirp_start pulses, gray-crossed like COUNT. Read twice 1 s apart: delta = PRF (expect 10000 at T = 100 us) |
| 0x20 | COMMIT       | W  | any write arms the shadow -> active transfer (Section 3) |
| 0x24 | DECHIRP_DELAY| RW | reserved, Phase 5 (replica delay, samples) |
| 0x28 | DECIM_SEL    | RW | reserved, Phase 5 (decimation select) |
| 0x2C | IF_SEL       | RW | reserved, Phase 5 (cpack source: raw RX / IF) |

Semantics that need stating:

- CTRL bits are quasi-static singles: each gets its own 2-flop synchronizer
  into `l_clk`, exactly the `ramp_en` idiom. They take effect "soon, cleanly"
  (see 4.5 for `tx_src` and the one refinement worth making).
- `nco_en` 0 -> 1 starts the NCO from phase 0, chirp sample 0, using the
  active (committed) parameter set. `nco_en = 0` holds it in reset. So the
  power-on sequence is: write FTW_START/FTW_SLOPE/SWEEP_LEN, COMMIT, then set
  `nco_en` - and the guide's register-write ORDER is the natural one.
- SWEEP_LEN is per LEG. Sawtooth (`triangle_en = 0`): period = SWEEP_LEN.
  Triangle: up-leg SWEEP_LEN samples, then down-leg SWEEP_LEN samples with
  negated slope, period = 2 x SWEEP_LEN. This matches `generate_chirp`, which
  concatenates two n-sample legs.
- `chirp_start` (the sync pulse and the CHIRP_COUNT increment) fires at sample
  0 of every PERIOD (not every leg).
- Unmapped reads keep returning `0xDEADC0DE`; unmapped writes keep answering
  OKAY. Nothing about the slave protocol changes in this phase.

---

## 3. The shadow/commit crossing (this phase's CDC lesson)

Phase 3 crossed one quasi-static bit. Phase 4 crosses a **parameter SET whose
words are meaningless unless they change together**: FTW_START, FTW_SLOPE, and
SWEEP_LEN describe one waveform; an update that takes effect between words -
or worse, mid-sweep - produces a chirp that never existed in any config.
Per-register 2-flop synchronizers cannot fix this: each word would land at its
own time.

The standard discipline, and what you should build:

1. Writes to 0x10/0x14/0x18 land in **shadow registers** in the `s_axi_aclk`
   domain. Writing them changes nothing downstream. (Readback reads the
   shadow - what you last wrote - which is the useful behavior for a config
   interface.)
2. A write to COMMIT (value ignored) flips a **toggle** flip-flop in the AXI
   domain. The shadow registers MUST NOT change again until the commit is
   consumed - that is your software's obligation, not enforced in fabric
   (note the debt; a BUSY status bit is the v2 fix).
3. In `l_clk`: 2-flop synchronize the toggle, detect an edge (XOR of the two
   newest stages... think it through - you need a third register to compare
   against). The edge sets a local `r_pending` flag.
4. At the **next chirp boundary** (period sample 0) with `r_pending` set: copy
   all three shadow words into the ACTIVE registers the NCO actually reads,
   clear `r_pending`. If the NCO is disabled, load immediately - there is no
   boundary to wait for.

Why this is safe without handshaking the 96 bits themselves: the shadow words
are **stable** from before the toggle flips until after the load (your
software holds up its end), so the l_clk side samples signals that are not
moving. Only the toggle actually crosses "live", and a single bit through two
flops is the solved problem. This pattern - stable payload + synchronized
flag - is the workhorse for every multi-word crossing you will ever build; the
gray counter covered the other case (payload that never stops moving).

Constraints: the toggle's meta flop joins the false-path/ASYNC_REG club from
Phase 3. The shadow-to-active data paths cross domains too - the clean idiom
is `set_false_path` (or `set_max_delay -datapath_only`) from the shadow
registers to the active registers; add it, and verify the pattern matched
cells after the first build, same drill as Phase 3 Section 2.

Testbench check for this section (add to the Section 7 list): change all
three shadows while a sweep is in flight, COMMIT mid-sweep, and assert the
in-flight sweep finishes on the OLD parameters to the last sample - the new
set may only appear from the next period's sample 0.

---

## 4. The NCO spec

You have built DDS before, so this is a spec with the project-specific traps
flagged, not a DDS tutorial.

### 4.1 Structure: second-order phase accumulator

Two accumulators in series, both advancing once per sample strobe:

```
r_ftw   <= r_ftw + r_slope;     -- frequency ramps linearly
r_phase <= r_phase + r_ftw;     -- phase integrates frequency
```

32 bits each, wrapping (plain unsigned add; two's-complement wrap IS the
modulo-2pi arithmetic - signedness is bookkeeping for the humans and for
Python, the adder does not care). At period start: `r_phase <= 0`,
`r_ftw <= FTW_START` (active copy). At leg boundary in triangle mode:
`r_slope` negates, `r_phase` continues untouched, and `r_ftw` is HELD for
exactly one update - see the turnaround discussion below.

Why phase resets to zero every period: `generate_chirp_sequence` is
`np.tile(chirp, reps)` - the golden model's phase restarts at 0 each period,
and coherent processing assumes it. This also kills accumulated rounding
drift across the CPI for free.

**The two grids (this is the key to 4.2 and to the turnaround).** `phase[n]`
is the phase AT sample n. `ftw[n]` is not the frequency at sample n - it is
the phase INCREMENT from sample n to n+1, i.e. the average frequency over that
interval, which for a linear ramp is the frequency at the MIDPOINT `n + 1/2`.
Phase lives on the sample grid; FTW lives on the half-sample grid. Every
"off by half a sample" surprise in this phase comes from forgetting that.

**Triangle continuity does NOT come out automatically.** The phase part does:
`phase_up(T) = -0.5*B*T + 0.5*B*T = 0` cycles, exactly where `phase_down(0)`
begins, so the legs join at zero phase and the accumulator (having integrated
to ~0 mod 2^32) joins the same way. The FREQUENCY part does not. The peak of
the frequency ramp sits at the turnaround SAMPLE, so on the half-sample FTW
grid it falls exactly BETWEEN two entries - and the two straddling it are
mirror images at equal distance, hence equal:

```
f(N - 1/2) = f0 + (N - 1/2)*s = g0 - s/2      last up-leg interval
f(N + 1/2) = mirror of it     = g0 - s/2      first down-leg interval
                                              g0 = +B/(2*FS), s = slope
```

So the correct FTW sequence repeats the apex value once:

```
... g0-5s/2,  g0-3s/2,  g0-s/2,  g0-s/2,  g0-3s/2,  g0-5s/2 ...
                                 ^^^^^^^^^^^^^^^^ apex lies between these
```

A naive "negate the slope and keep stepping" turns around ON an FTW entry
instead of between two, never repeats the apex, and leaves every down-leg FTW
one full `s` too low - a constant **-8834 Hz (0.88 range bins) across the
entire down leg** at the config defaults. Hence the one-update hold: a flag
raised at the leg boundary with the same registered timing as the negation,

```vhdl
if (i_cfg_ftw_hold = '1') then
    r_ftw_incr <= r_ftw_incr;                          -- apex used twice
else
    r_ftw_incr <= r_ftw_incr + unsigned(i_cfg_ftw_incr);
end if;
```

One flag, one mux, and the down leg becomes exact. Prove it in sim against the
model (Section 7 check 2b) - this is precisely the class of thing the guide
told you to verify rather than trust.

### 4.2 Register values (the contract with Python)

For sweep -B/2 -> +B/2 over T seconds at sample rate FS, with
`s = B / (T * FS^2)` (the slope in cycles per sample squared):

```
FTW_START = round((-B/(2*FS) + s/2) * 2^32)    as two's complement
FTW_SLOPE = round(  s              * 2^32)
SWEEP_LEN = round(T * FS)
```

**Mind the `+ s/2`** - it is not a fudge factor, it is the half-sample grid
offset from 4.1. The golden model evaluates `p_gold(n) = f0*n + (s/2)*n^2` at
the sample instant; the accumulator produces the rectangular sum
`p_hw(n) = sum_{k<n}(f0 + k*s) = f0*n + (s/2)*n^2 - (s/2)*n`. The difference
is linear in n, which is a **constant frequency error of -s/2**. Preloading
the FTW with half a slope makes the first entry `f(1/2)` instead of `f(0)`
and the two expressions become algebraically identical.

Leave it out and, at the config defaults, the fabric chirp is the golden chirp
delayed by exactly **half a sample**: a -4417 Hz beat offset against a 10 kHz
bin (`FS/N`), i.e. **0.44 range bins = 1.33 m** of range bias on every target
the moment you flip `tx_src`. That is exactly the "difference you cannot
attribute to +-1 LSB" that Section 6.2 tells you to stop and investigate, and
it looks like an HDL bug when it is a one-line host-side bug.

With the config defaults (B = 50e6, T = 100e-6, FS = 56.6e6):
`SWEEP_LEN = 5660`, `FTW_START = -1896735189 = 0x8EF21E2B`,
`FTW_SLOPE = 670343 = 0x000A3A87`.
(The uncorrected value was `-1897070360 = 0x8EED00E8` - if you see that
constant anywhere, it predates this correction.)

### 4.3 The error budget (do this arithmetic, don't inherit bit widths)

The reflex is "slope needs fractional bits, widen to 48". Check it instead:
rounding FTW_SLOPE to an integer costs at most 0.5 LSB of FTW per sample,
accumulating linearly: after N = 5660 samples the frequency error is bounded
by `N * 0.5 * FS / 2^32` = **~37 Hz worst case (8.7 Hz for these exact
numbers) against a 50 MHz sweep** - eight orders of magnitude down, and the
corresponding phase error across a whole sweep is milliradians. 32/32 is
enough, by arithmetic and not by luck. Re-run this budget if parameters ever
head toward short sweeps at low FS with long CPIs; the day it fails is the
day you widen - the accumulator, not the register interface (the registers
can stay 32-bit with an implied fractional shift).

### 4.4 Phase-to-amplitude, and matching the DMA path's scale

- Output is complex and the convention is **`I = cos(p)`, `Q = sin(p)`**, so
  that `I + jQ = e^{jp}` matches `dsp.generate_chirp` exactly. One
  quarter-wave folded LUT serves both, and it only ever computes SINE - you
  get cosine by feeding it a shifted phase, since `cos(x) = sin(x + 90)`. The
  accumulator is 32 bits for a full turn, so 90 degrees is `2^30`:

  ```
  I = LUT(phase + 2^30)   -- sin(p + 90) =  cos(p)
  Q = LUT(phase)          -- sin(p)
  ```

  **Two traps here, and they compound.** First, `phase - 2^30` is NOT the
  same shift: mod 2^32 it equals `phase + 3*2^30`, i.e. 270 degrees, and
  `sin(p + 270) = -cos(p)`. Taking the shifted tap as I with a minus sign
  gives `I + jQ = sin p - j cos p = -j*e^{jp}` - a constant -90 degree
  rotation. That one is physically harmless (it cancels in
  `RX * conj(replica)` and is invisible in `|RD|`) but it breaks bit-exactness
  and therefore rule 1. Second, and much worse: fixing the sign WITHOUT also
  swapping which tap drives which port gives `I = sin p, Q = cos p`, i.e.
  `I + jQ = j*e^{-jp}` - a **conjugated, spectrally inverted** chirp sweeping
  +25 -> -25 MHz. Sign and port assignment must move together.
  Pipeline alignment is unaffected by the swap (the shifted tap's extra
  register is already compensated by delaying the direct tap), but rename the
  signals afterwards or the `_q` pipeline ends up driving I.
- LUT address: top 12 bits of `r_phase` -> phase-truncation spurs at roughly
  `-6.02 * 12 = -72 dBc`, comfortably under a 12-bit DAC's ~-74 dB floor;
  14 bits (-84 dBc, 16k x 16 = one BRAM after folding: 4k x 16) if you want
  margin. Skip dithering; this is not a synthesizer product.
- **Output amplitude: full-scale 16-bit** (LUT values spanning +-32767-ish).
  `sdr.py` scales the DMA chirp by `2^15 - 1` into int16, i.e. the existing
  radar transmits full-scale - the NCO must match, or the leakage level, MGC
  window, and every gain number in your notes shift when you flip `tx_src`.
  (`axi_ad9361` takes the 16-bit word; the DAC uses the top 12 bits.)
- LUT read latency: whatever you pipeline (register the address, register the
  BRAM output - do both, timing is then free), I and Q must ride the SAME
  pipeline depth, and the `chirp_start` pulse you export must be delayed to
  match the data it labels. A one-cycle I/Q skew is an image tone; a
  mislabeled chirp start is a constant range bias that will confuse the
  Phase 5 calibration.

### 4.5 The sample strobe (not raw l_clk)

The DAC datapath meters itself: `dac_valid_i0` out of `axi_ad9361` is the
sample-rate strobe (that is what paces `tx_fir_interpolator` today), and
depending on interface mode it is NOT continuously high. The NCO advances
**only on the strobe**, and the strobe is **always `i_dac_valid_0`** - one
source, no mux, in both I2 and I3.

Why not switch to the RX strobe for I2, as an earlier draft of this guide
suggested: `dac_valid_i0` is generated by the AD9361 TX interface and runs
whenever the interface is up, independent of whether the DMA is feeding
anything, so it is available during I2 anyway. A mode-dependent strobe buys
nothing and costs you a select that has to be synchronized (and if you take
it off the FIRST flop of a 2-flop synchronizer instead of the second, you have
put a metastable signal in the datapath). More importantly, Phase 5's replica
delay line wants ONE time base it can count in.

Do not let it free-run on `l_clk`, or the chirp duration scales by the
(mode-dependent) strobe duty and nothing matches the golden model.

First hardware sanity check, either way: CHIRP_COUNT delta over one second
equals 10000. If it reads 5000 or 20000, the strobe assumption is wrong -
that meter exists precisely to catch this before you stare at spectrograms.

`tx_src` mid-flight: flipping CTRL bit 2 while transmitting glitches one
sample at the DAC - harmless for a config action, but since you already have
the chirp-boundary machinery, registering the mux select at period start too
costs one line and makes the switch clean. Optional, recommended.

### 4.6 The four ways the NCO can disagree with the golden model

Rule 1 says the golden model is the law. Here is the complete list of places
where a working, sensible-looking NCO still fails to be `generate_chirp`, and
which of them you FIX versus which you REPLICATE in `nco_reference`. Three of
the four cost real range accuracy; only the last is cosmetic.

| # | Mismatch | Size at config defaults | Action |
|---|---|---|---|
| 1 | Half-sample grid offset (4.2) | -4417 Hz = 0.44 bins = 1.33 m | **Fix** in `register_image`: `+ s/2` on FTW_START |
| 2 | Triangle turnaround (4.1) | -8834 Hz = 0.88 bins on the down leg | **Fix** in HDL: one-update FTW hold |
| 3 | I/Q convention (4.4) | constant -90 deg, or spectral inversion if half-fixed | **Fix** in HDL: `+2^30` tap AND port swap |
| 4 | Fixed-point quantization | <= 1 LSB, ~-72 dBc spurs | **Replicate** in `nco_reference` |

Category 4 in detail, because "replicate" means bit-for-bit and each of these
has a plausible-looking wrong answer:

- The LUT is `floor(sin(i * pi/2 / 4096) * 32767)` - truncated, not rounded.
  Peak entry is 32766, and the top ~20 entries saturate there.
- The address is the top 12 bits of the 32-bit phase, truncated.
- The quadrant fold maps the odd quadrants to `C_LUT_SIZE - 1 - idx`, not
  `C_LUT_SIZE - idx`. That one-index asymmetry is worth about -74 dBc, on par
  with the truncation spurs, so it is not worth fixing - but a model that
  mirrors "correctly" will never match the fabric.

The structural move that stops this list from growing: have `fabric_regs.py`
**generate** `dds_lut.txt`. Then the ROM contents, the VUnit reference
vectors, and the register image all come from one function and cannot drift.

For the amplitude comparison in the I3 exit test: `sdr.py` does
`chirp * (2**15 - 1)` then `.astype(np.int16)`, which truncates toward zero
rather than rounding. So the DMA and NCO paths differ by at most 1 LSB -
0.0003 dB, invisible in the leakage level.

---

## 5. I2 - see the chirp with zero new tooling

Wire the NCO's I/Q onto `o_data_0`/`o_data_1` when `rx_dbg_mux = 1`
(pass-through and `ramp_en` behavior otherwise unchanged - the priority order
between `ramp_en` and `rx_dbg_mux` is yours to define; define it in the tb).

On hardware:

```bash
# on the board (or via rxtap word-index equivalents)
rxtap 4 0x8EF21E2B    # FTW_START, includes the +s/2 half-sample term (4.2)
rxtap 5 0x000A3A87    # FTW_SLOPE
rxtap 6 5660          # SWEEP_LEN
rxtap 8 1             # COMMIT
rxtap 1 0x12          # CTRL: nco_en | rx_dbg_mux
```

Then start the Python radar as usual and open the Signals tab: the RX
spectrogram shows a clean 100 us sawtooth sweeping -25 -> +25 MHz. Frame sync
will do something nonsensical (there is no leakage to lock to - the "RX" IS
the chirp); ignore the RD map, this checkpoint is the spectrogram and the
CHIRP_COUNT meter. Flip `triangle_en`, COMMIT is not needed (it is a CTRL
bit - though nothing stops you from making triangle a committed parameter
instead; if mid-period slope-shape changes offend you, that is the cleaner
choice), and watch the sawtooth become a triangle.

Checkpoint: **live waveform reconfiguration from a shell, observed in the GUI,
no rebuild.** The I1 checkpoint was one bit; this one is the whole waveform.

---

## 6. I3 - the DAC mux, and the determinism proof

### 6.1 Block design changes

In `system_bd.tcl`, the TX-side wiring currently reads (three connections to
change, near the `tx_fir_interpolator` block):

```tcl
ad_connect axi_ad9361/dac_valid_i0 tx_fir_interpolator/dac_valid_0
ad_connect axi_ad9361/dac_data_i0 tx_fir_interpolator/data_out_0
ad_connect axi_ad9361/dac_data_q0 tx_fir_interpolator/data_out_1
```

becomes (interpolator output now lands in the core; core output feeds the
DAC; the valid strobe fans out to both the interpolator - as before - and the
core):

```tcl
ad_connect axi_ad9361/dac_valid_i0 tx_fir_interpolator/dac_valid_0
ad_connect axi_ad9361/dac_valid_i0 fmcw_core_0/i_dac_valid_0
ad_connect fmcw_core_0/i_dac_data_0 tx_fir_interpolator/data_out_0
ad_connect fmcw_core_0/i_dac_data_1 tx_fir_interpolator/data_out_1
ad_connect axi_ad9361/dac_data_i0 fmcw_core_0/o_dac_data_0
ad_connect axi_ad9361/dac_data_q0 fmcw_core_0/o_dac_data_1
```

**Data wires only.** Per Section 1, there are no TX enable connections to the
core - `enable_out_0/1` of the interpolator stay connected to
`tx_upack/enable_0/1` and nowhere else. If you take nothing else from this
section: the upack `fifo_rd_en` loop (`logic_or` -> `tx_upack/fifo_rd_en`) and
the whole enable path stay EXACTLY as they are. The core only cuts the two
data wires into `axi_ad9361`. Channel 1 (`dac_data_i1/q1`) is not touched
either - it keeps its direct path from `tx_upack`.

Trap while editing this file: in Tcl, `#` only starts a comment at the start
of a command. `ad_connect a b # todo` passes `#` and `todo` to `ad_connect` as
two extra arguments. Use `;# todo` or put the note on its own line.

And the sync mux (new lines, plus ONE existing line to delete):

```tcl
# delete: ad_connect axi_tdd_0/tdd_channel_1 axi_ad9361_adc_dma/sync
ad_connect axi_tdd_0/tdd_channel_1 fmcw_core_0/i_sync_in
ad_connect fmcw_core_0/o_dma_sync  axi_ad9361_adc_dma/sync
```

With `sync_src = 0` this is a wire - stock behavior preserved (remember WHY:
tdd_channel_1 idles high by default polarity, and sync-high is what lets
transfers start at all). Do not set `sync_src = 1` in this phase unless you
are deliberately running the Section 6.3 experiment.

Refresh Module in the GUI first (the port list changed - watch for the
19-loose-pins inference failure on the AXI side if any AXI port name got
touched during the rename), then build.

### 6.2 The exit test (this is the phase gate)

Digital loopback on, Python radar completely unchanged, fake targets running:

1. Baseline: `tx_src = 0` (DMA chirp). Note the RD map, target positions,
   leakage level.
2. `tx_src = 1`, `nco_en = 1`, waveform registers = the same B/T/FS the
   Python config computes. The radar must keep working identically: same RD
   map, same target detections, same leakage level (the full-scale amplitude
   match from 4.4). Python is still generating and pushing its DMA chirp -
   it just is not reaching the DAC; that is fine and expected in this phase.
3. **The determinism proof:** log `estimate_chirp_offset`'s result over many
   consecutive blocks and across several `start()` cycles. With the DMA
   chirp it wanders (buffer timing is software-paced). With the NCO it must
   return the SAME value every block, every run. That constant is your
   TX -> RX digital latency through the loopback path, in samples.
   **Write it in TODO.md** - it is the Phase 5 `DECHIRP_DELAY` seed and the
   first hard evidence that fabric timing is deterministic.

If step 2's RD map differs from baseline in any way you cannot attribute to
the +-1 LSB of quantization difference between the float DMA chirp and the
fixed-point NCO: stop and find out why in simulation. "Close enough" here
compounds into "dechirp mysteriously 20 dB worse" in Phase 5.

### 6.3 Optional now, mandatory later: first sync experiment

Flip `sync_src = 1` and restart the Python side: every `read_block()` should
now begin at (near) a chirp boundary - `estimate_chirp_offset` should report
approximately zero. Not exactly zero: cpack packs samples into 64-bit beats,
sync is sampled on beats, and the RX-side latency from the core's tap point
to the DMA adds a fixed few samples. Constant again, and small. Two things to
verify while you are here, because they matter for I6:

- pulse width: a single-cycle `chirp_start` can fall between packed beats and
  be missed - stretch `o_dma_sync` (when `sync_src = 1`) over a handful of
  cycles so it is guaranteed to overlap an accepted beat. The transfer then
  starts on the first beat inside the window: still deterministic, since
  beats are phase-locked to samples.
- one transfer per buffer: libiio splits very large buffers into multiple DMA
  transfers, and SYNC_TRANSFER_START gates EACH transfer - a mid-CPI re-sync
  would drop samples inside your buffer. At 2.9 MB per CPI (128 reps) you are
  under the DMAC's per-transfer ceiling, but confirm empirically: if the
  captured CPI shows a phase/timing discontinuity partway through with
  `sync_src = 1` but not with 0, this is what it is.

Then set `sync_src` back to 0 for daily use until I6 formally retires frame
sync.

---

## 7. The testbench (extend, don't rewrite)

`tb_rx_tap` (now `tb_fmcw_core`) keeps its two unrelated clocks and the
axi_write/axi_read procedures. The existing six checks stay green. New checks:

1. **NCO vs golden vector, bit-exact.** A Python script (see Section 8 - the
   same fixed-point model that computes register values) writes a reference
   file of N samples of (I, Q) for the default config; the tb configures the
   core via real AXI writes (values read from the file header or hard
   constants - either way, THE SAME numbers the script used), enables, and
   `check_equal`s every strobed output sample against the file. VUnit's
   convenient path: plain text integers via std.textio, one I/Q pair per
   line, file dropped in the tb directory and the path passed as a generic
   (or via VUnit's `tb.set_generic`). Bit-exact means `=`, not "close".
2. **Period framing:** chirp_start fires at sample 0 of every period;
   period length = SWEEP_LEN (sawtooth) and 2x (triangle); CHIRP_COUNT
   increments per period and survives the gray crossing (reuse check 5's
   pattern).
   2b. **Triangle turnaround (4.1):** capture the FTW sequence across the leg
   boundary and assert the apex value appears TWICE - `..., g0-3s/2, g0-s/2,
   g0-s/2, g0-3s/2, ...`. If it appears once, the one-update hold is missing
   and the whole down leg is a constant `s` low. Cheaper and far more
   diagnostic than eyeballing the phase error at the end of the leg.
   2c. **I/Q convention (4.4):** assert `I[0] > 0` and `Q[0] = 0` at the first
   sample of a period (phase 0 -> `cos = full scale`, `sin = 0`). Two
   `check_equal`s that catch both the -90 rotation and the spectral inversion
   instantly. Follow with a mid-sweep sample where I and Q differ in sign to
   pin the rotation direction.
3. **Shadow/commit atomicity:** the Section 3 check - commit mid-sweep, old
   parameters run to the last sample, new set active from next period sample
   0. Also: commit while `nco_en = 0` loads immediately; enabling then starts
   on the new set.
4. **Mux sanity:** `tx_src = 0` passes `i_dac_data_*` through untouched
   (delta = pipeline delay only); `rx_dbg_mux = 0` keeps the RX path
   transparent (the Phase 2 pass-through property, re-proven for the grown
   core); power-on register state = all CTRL zeros = both paths transparent
   and `o_dma_sync = i_sync_in`.
5. **Strobe discipline:** drop `i_dac_valid_0` low for a stretch mid-sweep -
   the NCO must freeze (phase, ftw, sample counter all hold), not skid.

The reference-file workflow is the piece to get right, because Phase 5's
dechirp verification ("matches `dsp.mix_signal` sample-for-sample") is this
exact machinery with a longer file. Build it as: config in ->
`register_image()` + `nco_reference()` out, one script, no hand-typed
constants anywhere in the tb.

---

## 8. Host side: config.py -> registers

Two small Python pieces, cleanly separated (spec; you write them):

1. **`src/python/common/fabric_regs.py`** - pure, no I/O, no radio:
   - `register_image(cfg: RadarConfig) -> dict[int, int]`: offset -> value
     for FTW_START/FTW_SLOPE/SWEEP_LEN (+ CTRL bits from TRIANGLE_EN),
     implementing exactly the 4.2 formulas. Two's-complement encode signed
     values into the 32-bit unsigned register words here, in one place.
   - `nco_reference(cfg, n) -> np.ndarray[complex]` (or int32 I/Q pair
     array): the bit-true fixed-point NCO model - integer 32-bit wrapping
     adds, the same LUT quantization you implemented, NOT a call to
     `generate_chirp`. This is the single source of truth the VUnit
     reference files come from. It must reproduce every item in the 4.6
     "replicate" list: floor-quantized LUT, truncated 12-bit address, the
     `C_LUT_SIZE - 1 - idx` fold, and the `I = cos` / `Q = sin` convention.
   - `dds_lut_table()` -> the 4096 quarter-wave entries, and a small
     entry point that WRITES `src/nco/dds/dds_lut.txt`. Generating the ROM
     from the same function the model uses is what stops the fabric and the
     golden vectors from drifting apart (4.6). Regenerate and diff in CI, or
     at least assert the checked-in file matches on import.
   - Unit tests (offline, pytest style like the detector self-test):
     (a) `nco_reference` vs `generate_chirp` float output: spectral error
     below ~-70 dBc, peak phase error bounded - this test is only meaningful
     once the 4.6 fixes are in, otherwise it quietly absorbs a 0.44-bin bias;
     (b) round-trip sanity of the encodings (FTW_START for -25 MHz decodes
     back to -25 MHz within FS/2^32, remembering the `+ s/2` term);
     (c) triangle: the last up-leg FTW and the first down-leg FTW are equal.
2. **`src/python/online/fabric_ctl.py`** - transport, dumb on purpose:
   `write_regs(image: dict, ip: str)` shelling out over ssh to the on-board
   tool: `ssh root@{ip} /root/rxtap <word_index> <value>` per register, then
   COMMIT. (`rxtap` addresses by word index = offset/4 - keep the /4 in ONE
   named place.) Batch it in one ssh invocation (`rxtap 4 ... && rxtap 5
   ...`) - ssh session setup dwarfs the writes. paramiko vs subprocess:
   subprocess is zero new dependencies and you already live in a shell;
   swap later if session reuse starts to matter. GUI integration (a "fabric"
   section in the config tab, bypass toggles) is I6 - resist it for now.

---

## 9. Troubleshooting catalogue (Phase 4 edition)

- **RX capture hangs forever (no data, no error)** -> the DMA sync trap:
  `sync_src = 1` with the NCO disabled (no pulses), or the sync mux default
  is wrong, or you OR'd instead of MUXing and then "fixed" the idle-high by
  inverting something. `read_block` blocking on the first buffer is the
  signature. Recovery: `rxtap` CTRL sync_src back to 0 - the bus is alive,
  only the stream is starved (unlike the Phase 3 hang, no power cycle
  needed).
- **Chirp duration wrong by a clean factor (2x, 4x)** -> NCO advancing on
  raw `l_clk` instead of the strobe, or on the wrong strobe. CHIRP_COUNT
  meter catches it in one second.
- **Spectrogram shows the sweep but at half/double the intended bandwidth**
  -> FTW_SLOPE off by a factor (the FS^2 in the denominator is the usual
  victim), or SWEEP_LEN and slope computed from inconsistent FS.
- **Mirrored sweep (+25 -> -25 when you wanted -25 -> +25)** -> sign error in
  FTW_START, or I/Q swapped at the DAC ports (spectral inversion). The other
  way in is fixing the quadrature tap sign without also swapping which tap
  drives which port (4.4) - that gives `j*e^{-jp}`, a conjugated chirp.
  Compare against the DMA-chirp spectrogram, which is known-good.
- **Everything works but every target moved by ~1.3 m when you flipped
  tx_src** -> the `+ s/2` half-sample term is missing from FTW_START (4.2).
  It is 0.44 of a range bin, so it looks like a small calibration error
  rather than a bug. Check the register value before touching the HDL.
- **Sawtooth is perfect, triangle targets smear or split** -> the FTW hold at
  the turnaround is missing (4.1): the down leg runs a constant 0.88 bins off
  the up leg, so up-leg and down-leg detections disagree about range.
- **Discrete tones around the chirp in the RD map / spectrogram** -> I/Q
  pipeline skew in the LUT path (image tone), or phase truncation taken from
  the wrong end of `r_phase` (take the TOP bits).
- **Leakage level shifts when flipping tx_src** -> amplitude mismatch vs the
  `2^15 - 1` DMA scaling (Section 4.4).
- **estimate_chirp_offset constant but different after every reboot** ->
  it will be constant per bitstream and interface bring-up; if it moves
  between boots, suspect the AD9361 interface calibration (ADC_INIT_DELAY
  timing) or that you are measuring across a `set_loopback` toggle. Constant
  within a run is what I3 requires; note per-boot behavior for Phase 5.
- **dac_dunf / underflow complaints in dmesg while tx_src = NCO** -> expected
  noise IF you stopped pushing the TX buffer from Python: upack keeps being
  read (its rd_en loop is untouched) with no DMA feeding it. Either keep
  pushing the cyclic buffer (harmless, this phase's default since Python is
  unchanged) or ignore the flag; do NOT re-plumb the rd_en loop to silence
  it.
- **Vivado: interface inference broke after the rename** -> the `s_axi_*`
  names must survive `rx_tap -> fmcw_core` untouched; 19 loose pins means a
  find-replace ate a port name. Refresh Module after fixing.
- **Timing failures on shadow -> active paths** -> the Section 3 constraint
  is missing or its pattern matched nothing (same verification drill as
  Phase 3: grep `timing_impl.log`, confirm the false path hit cells).
- **It all worked and then vanished after a rebuild** -> same answer as every
  phase. Which branch, and did you commit?

---

## Where this leads

Phase 5 = rungs I4 + I5, and every piece is already parked in this design:

- **dechirp**: complex multiply `RX x conj(replica)` at the RX insertion
  point; the replica is the NCO output through a DECHIRP_DELAY-deep delay
  line (register 0x24, reserved). Verification is the Section 7 reference-
  file machinery pointed at `dsp.mix_signal` in digital loopback - the
  golden-model test that needs no RF.
- **decimation**: CIC or halfband chain after the mixer, DECIM_SEL (0x28),
  and IF_SEL (0x2C) finally switches cpack onto the IF stream - the moment
  Ethernet stops carrying raw IQ and the frame-rate/duty-cycle appendix of
  TODO.md comes due. The valid-strobe discipline you built into the core
  (everything advances on strobes, nothing assumes continuous valid) is what
  makes a decimated, duty-cycled stream into cpack a wiring change instead
  of a redesign.
- **I6**: `sync_src = 1` becomes the default, `estimate_chirp_offset` and
  frame sync are deleted from the IF path, and `fabric_ctl.write_regs`
  becomes what RE-CONFIGURE does. The cutover still waits for Parts F + G on
  real RF - the rule from Part I stands.

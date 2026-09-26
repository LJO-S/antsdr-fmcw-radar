> **Archived 2026-09-15.** I2-I4 are closed on hardware. The register map here is
> FMC1 and stale (current: FMC3, see `CLAUDE.md` "Firmware / HDL track"); Section 8.3
> (`fabric_ctl`) was superseded by the implemented module; Sections 6.2/6.3 (I3) were
> absorbed into I4. Still-live rules and troubleshooting entries were carried into
> `docs/archive/AntSDR_Phase5_IF_Decimation_Guide.md` Section 0. Kept for the NCO derivation
> (Section 4), which is still the reference for `chirp_ftw` and `nco_reference.py`.

# AntSDR E200 - Phase 4: chirp NCO + TX from fabric (Part I2 + I3)

Follows the Phase 3 guide. Assumes Phase 3 is done: the AXI-Lite register bank
in `rx_tap.vhd` works on hardware (MAGIC/SCRATCH/COUNT correct, `ramp_en`
toggles live via `rxtap`).

Same contract: this guide specifies, you write the code. Exact text is given
for anything a typo breaks (Tcl, port names, register offsets).

Scope: **I2 (chirp NCO in fabric)** and **I3 (TX from fabric)**. The register
map is designed through I5 (dechirp) so nothing gets torn up later, but the
exercises stop at the TX mux. Dechirp is Phase 5.

## Three rules

1. **The golden model is the law.** `dsp.generate_chirp` defines a chirp. The
   NCO is correct when a fixed-point numpy model matches the VHDL bit-for-bit,
   AND that model matches the float chirp below the DAC noise floor - two
   separate comparisons.
2. **Nothing changes mid-chirp.** FTW start, slope, sweep length latch into
   `l_clk` only at a chirp boundary (shadow/commit, Section 3). Changing
   mid-sweep splatters the spectrum.
3. **Power-on defaults = current behavior.** CTRL = 0 means: DMA drives the
   DAC, TDD drives DMA sync, no debug mux, no ramp. A bitstream nobody has
   configured must behave exactly like today's radar.

---

## 0. Architecture

See `fmcw_fabric_architecture.svg`. RX chain today: `axi_ad9361 ->
rx_fir_decimator -> rx_tap -> cpack -> adc_dma`. TX chain: `dac_dma ->
tx_upack -> tx_fir_interpolator -> axi_ad9361`. Channel 1 passes through
untouched on both sides.

**`rx_tap` grows into `fmcw_core`**, one module with:
- existing RX ports (future dechirp mixer/decimator sits here)
- new DAC-side ports: interpolator in, `axi_ad9361` out, mux for
  DMA-passthrough vs NCO
- one AXI-Lite slave, same 0x43C10000 address

One module, not a separate `chirp_gen`, because Phase 5's dechirp mixer needs
a delayed, conjugated replica of the exact TX NCO output - same entity means
the phase coherence and delay line are internal wiring, not a cross-block bus
to debug in hardware.

Ladder:
- **I2** (Section 5): NCO muxed onto RX channel 0 by a CTRL debug bit - GUI
  spectrogram becomes the fabric scope.
- **I3** (Section 6): DAC-side mux goes live (`tx_src = NCO`); board
  transmits a fabric chirp, Python still does everything else.
- **I4/I5** (Phase 5): complex multiply RX x conj(replica), CIC/FIR
  decimation, `IF_SEL` switches cpack between raw RX and IF. Register map
  below reserves their rows.

### Frame start is already solved

`axi_ad9361_adc_dma` has `SYNC_TRANSFER_START=true`, wired to
`axi_tdd_0/tdd_channel_1` (`system_bd.tcl`). Contract: a transfer doesn't
start until `sync` goes high on an accepted beat; leading data is dropped. So
the fabric already has a hardware trigger for RX DMA alignment.

- Emit a `chirp_start` pulse, route to DMA sync -> every `read_block()` starts
  at a chirp boundary. Since chirps are identical, that's also a CPI
  boundary; frame sync becomes dead code once IF mode lands (I6).
- **Trap:** `TDD_DEFAULT_POL = 0b010` - `tdd_channel_1` idles HIGH when TDD is
  disabled (the normal case), which is the only reason RX streaming works
  today. **MUX your pulse against TDD, don't OR it** (OR'ing an idle-high
  signal is a no-op). Default the mux to TDD passthrough, or a bitstream with
  `nco_en=0` never asserts sync and every RX capture hangs forever.

Don't flip this mux during I3 - Ethernet still carries raw RX, frame sync
still runs; that's the I3 exit test.

### Digital latency

Once TX is fabric-generated, NCO -> DAC -> AD9361 TX -> (loopback or RF) ->
AD9361 RX -> core is a fixed but unknown sample delay (interface pipelines +
AD9361 filter group delay). At 56.6 MSPS one sample = 2.65 m; budget is tens
of samples.

- I3: harmless, frame sync absorbs it - but unlike today's random DMA offset,
  this one is **constant**. Measure it (I3 exit test), record it.
- Phase 5: becomes `DECHIRP_DELAY` - sweep it in digital loopback until
  leakage lands in range bin 0. Loopback and real-RF constants differ
  (loopback taps inside the AD9361 chain); calibrate both, store in
  `config.py`.

---

## 1. Growing rx_tap into fmcw_core

1. Close I1 (`docs/firmware-branch-workflow.md`): merge `feature/rx-tap` ->
   `e200-custom`, push, bump gitlinks bottom-up.
2. New branch `feat/chirp-nco` from `e200-custom`.
3. Rename `src/rx_tap.vhd -> src/fmcw_core.vhd`, entity `rx_tap ->
   fmcw_core`. Chase the name through `system_bd.tcl` (`add_files`,
   `create_bd_cell`, every `ad_connect`, `ad_cpu_interconnect`), `Makefile`
   `M_DEPS`, and the testbench (`run.py` globs so it follows automatically).
4. **New MAGIC**: `0x464D4331` ("FMC1"). Superset of RXT1's map but the
   contract changed - anything reading MAGIC must fail loudly on the old
   value, not read garbage. Bump MAGIC on every future map change.

New DAC-side ports (`l_clk` domain):

```vhdl
-- from tx_fir_interpolator
i_dac_valid_0  : in  std_logic;                                   -- rate strobe, see 4.5
i_dac_data_0   : in  std_logic_vector(G_DATA_WIDTH - 1 downto 0);  -- I
i_dac_data_1   : in  std_logic_vector(G_DATA_WIDTH - 1 downto 0);  -- Q
-- to axi_ad9361 dac_data_i0/q0
o_dac_data_0   : out std_logic_vector(G_DATA_WIDTH - 1 downto 0);
o_dac_data_1   : out std_logic_vector(G_DATA_WIDTH - 1 downto 0);
-- DMA sync mux
i_sync_in      : in  std_logic;   -- from axi_tdd_0/tdd_channel_1
o_dma_sync     : out std_logic;   -- to axi_ad9361_adc_dma/sync
```

**Data only - no TX enable ports.** On RX, the core sits in the enable chain
because `cpack/enable_N` are inputs. On TX it's the reverse: `dac_enable_i0`
is an *output* of `axi_ad9361`, forwarded by the interpolator to `tx_upack`.
Every TX-enable consumer sits upstream of the core, and `axi_ad9361` has no
enable input to drive. So there's no legal destination for an
`o_dac_enable_*`. Channel 1 also skips the core entirely, wired straight
`tx_upack <-> axi_ad9361`.

(The interpolator's `valid_out_0`/`enable_out_*` handshake to `tx_upack` is
untouched - the core only intercepts data on the way to `axi_ad9361`, not the
upack read-enable loop.)

Worth taking: **`i_dac_enable_0`** - in `axi_ad9361_tx_channel.v`,
`dac_data_i0` is only driven from DMA when `dac_data_sel==4'h2`; otherwise the
chip transmits its own internal DDS and your NCO output is silently
discarded. Expose `dac_enable_i0` in STATUS so that failure mode is a single
register read. It's also why pushing the cyclic TX buffer from Python
matters: that's what keeps `dac_data_sel` at DMA.

---

## 2. Register map (FMC1)

Designed through Phase 5; Phase 4 implements down through CHIRP_COUNT +
COMMIT.

| Offset | Name | Access | Function |
|---|---|---|---|
| 0x00 | MAGIC | R | `0x464D4331` "FMC1" |
| 0x04 | CTRL | RW | bit0 `ramp_en` (from RXT1); bit1 `nco_en`; bit2 `tx_src` (0=DMA, 1=NCO); bit3 `triangle_en`; bit4 `rx_dbg_mux` (NCO onto RX ch0); bit5 `sync_src` (0=TDD, 1=chirp_start) |
| 0x08 | SCRATCH | RW | unchanged |
| 0x0C | COUNT | R | `valid_in` counter, unchanged |
| 0x10 | FTW_START | RW | signed 32-bit, shadow |
| 0x14 | FTW_SLOPE | RW | signed 32-bit, shadow |
| 0x18 | SWEEP_LEN | RW | samples per sweep leg, shadow |
| 0x1C | CHIRP_COUNT | R | counts chirp_start pulses, gray-crossed. Delta over 1 s = PRF (10000 expected at T=100 us) |
| 0x20 | COMMIT | W | any write arms shadow -> active transfer (Section 3) |
| 0x24 | DECHIRP_DELAY | RW | reserved, Phase 5 |
| 0x28 | DECIM_SEL | RW | reserved, Phase 5 |
| 0x2C | IF_SEL | RW | reserved, Phase 5 |

- CTRL bits are quasi-static: each gets its own 2-flop synchronizer (the
  `ramp_en` idiom). **Exception observed on hardware (I2 bring-up
  2026-08-30):** `triangle_en` only takes effect on a COMMIT.
- `nco_en` 0->1 starts the NCO from phase 0, sample 0, using the active
  parameter set. So the boot sequence is: write FTW_START/SLOPE/SWEEP_LEN ->
  COMMIT -> set `nco_en`.
- SWEEP_LEN is per leg. Sawtooth: period = SWEEP_LEN. Triangle: up-leg then
  down-leg (negated slope), period = 2xSWEEP_LEN - matches
  `generate_chirp`'s two-leg concatenation.
- `chirp_start` fires at sample 0 of every **period** (not leg).
- Unmapped reads still return `0xDEADC0DE`; unmapped writes still OKAY.

---

## 3. Shadow/commit crossing

Phase 3 crossed one bit. Here we cross a **parameter set** - FTW_START,
FTW_SLOPE, SWEEP_LEN describe one waveform, and if they update at different
times you get a chirp shape that never existed in any config. Per-register
synchronizers can't fix this.

1. Writes to 0x10/0x14/0x18 land in shadow registers (`s_axi_aclk` domain),
   no effect downstream. Readback returns the shadow.
2. Writing COMMIT flips a toggle flip-flop in the AXI domain. Software must
   not touch the shadows again until the commit is consumed (not enforced in
   fabric - a BUSY bit is the v2 fix).
3. In `l_clk`: 2-flop synchronize the toggle, detect the edge (needs a 3rd
   register to compare against), set `r_pending`.
4. At the next chirp boundary with `r_pending` set: copy the three shadow
   words into the active registers the NCO reads, clear `r_pending`. If
   disabled, load immediately.

Safe without handshaking the full 96 bits because the shadow words are stable
between toggle-flip and load - only the toggle crosses live, and one bit
through two flops is solved. (The gray counter from Phase 3 handles the
opposite case: a payload that never stops moving.)

Constrain the toggle's meta-flop (ASYNC_REG/false-path, as in Phase 3) and
the shadow->active data paths (`set_false_path` or `set_max_delay
-datapath_only`); verify matched cells after the first build.

**Testbench check:** change all three shadows mid-sweep, COMMIT mid-sweep,
assert the in-flight sweep finishes on the OLD parameters - new set only
takes effect from the next period's sample 0.

---

## 4. The NCO spec

### 4.1 Second-order phase accumulator

```vhdl
r_ftw   <= r_ftw + r_slope;     -- frequency ramps linearly
r_phase <= r_phase + r_ftw;     -- phase integrates frequency
```

Both 32-bit, wrapping (unsigned add - two's-complement wrap *is* mod-2pi;
signedness is bookkeeping for humans/Python only). At period start: `r_phase
<= 0`, `r_ftw <= FTW_START`. At a triangle leg boundary: negate `r_slope`,
let `r_phase` continue, **hold `r_ftw` for one update** (see turnaround
below).

Phase resets to 0 every period because `generate_chirp_sequence` is
`np.tile(chirp, reps)` - the golden model restarts phase at 0 each period,
and coherent processing assumes it. This also avoids accumulated rounding
drift across a CPI.

**The two grids.** `phase[n]` is the phase *at* sample n. `ftw[n]` is the
phase increment from n to n+1 - for a linear ramp, that's the frequency at
the *midpoint* n+1/2. Phase lives on the sample grid, FTW on the half-sample
grid. Every off-by-half-sample bug in this section comes from forgetting
that.

**Triangle turnaround isn't automatically continuous.** Phase is fine:
`phase_up(T) = -0.5BT + 0.5BT = 0`, so legs join at zero phase. Frequency
isn't: the ramp's peak sits at the turnaround *sample*, which on the
half-sample FTW grid falls *between* two entries - and those two entries are
mirror images, hence equal:

```
f(N - 1/2) = g0 - s/2   (last up-leg interval)
f(N + 1/2) = g0 - s/2   (first down-leg interval)     g0 = B/(2*FS), s = slope
```

So the correct FTW sequence repeats the apex once:

```
... g0-5s/2, g0-3s/2, g0-s/2, g0-s/2, g0-3s/2, g0-5s/2 ...
                       ^^^^^^^^^^^^^ apex between these
```

Naively negating the slope and continuing to step turns around *on* an entry
instead of between two - the apex never repeats and every down-leg FTW is one
full `s` too low: **-8834 Hz (0.88 range bins) across the whole down leg**, at
config defaults. Fix: a one-update hold flag at the leg boundary, registered
with the same timing as the negation:

```vhdl
if (i_cfg_ftw_hold = '1') then
    r_ftw_incr <= r_ftw_incr;               -- apex used twice
else
    r_ftw_incr <= r_ftw_incr + unsigned(i_cfg_ftw_incr);
end if;
```

Verify against the model in sim (Section 7, check 2b).

### 4.2 Register values

Sweep -B/2 -> +B/2 over T seconds at rate FS, `s = B / (T * FS^2)`:

```
FTW_START = round((-B/(2*FS) + s/2) * 2^32)   two's complement
FTW_SLOPE = round(s * 2^32)
SWEEP_LEN = round(T * FS)
```

**The `+ s/2` is not a fudge factor** - it's the half-sample grid offset from
4.1. The golden model evaluates `p_gold(n) = f0*n + (s/2)n^2` at the sample
instant; the accumulator produces the rectangular sum `p_hw(n) = f0*n +
(s/2)n^2 - (s/2)n`. The difference is a constant frequency error of `-s/2`;
preloading half a slope makes the first FTW entry `f(1/2)` instead of `f(0)`
and the two expressions match.

Omit it and the fabric chirp is the golden chirp delayed by exactly **half a
sample**: -4417 Hz beat offset against a 10 kHz bin (`FS/N`) = **0.44 range
bins = 1.33 m** of bias the moment `tx_src` flips. Looks like an HDL bug; is
a one-line host bug.

At config defaults (B=50e6, T=100e-6, FS=56.6e6): `SWEEP_LEN=5660`,
`FTW_START=-1896735189=0x8EF21E2B`, `FTW_SLOPE=670343=0x000A3A87`.
(Uncorrected value was `-1897070360=0x8EED00E8` - if you see that constant,
it predates this fix.)

### 4.3 Error budget

Rounding FTW_SLOPE to an integer costs <=0.5 LSB of FTW per sample,
accumulating linearly: after N=5660 samples the frequency error is bounded by
`N*0.5*FS/2^32` ~= **37 Hz worst case (8.7 Hz actual)** against a 50 MHz
sweep - eight orders of magnitude below it, so 32/32 bits is enough.
Re-check this if parameters ever move toward short sweeps / low FS / long
CPIs - widen the accumulator, not the register width (registers can
stay 32-bit with an implied fractional shift).

### 4.4 Phase-to-amplitude, DMA-path scale match

- Convention: **`I = cos(p)`, `Q = sin(p)`**, matching `dsp.generate_chirp`
  (`I+jQ = e^{jp}`). One quarter-wave LUT computes only sine; cosine comes
  from a 90 degree phase shift (90 degrees = `2^30` on a 32-bit accumulator):

  ```
  I = LUT(phase + 2^30)   -- cos(p)
  Q = LUT(phase)          -- sin(p)
  ```

  Two traps: `phase - 2^30` is *not* the same shift (mod 2^32 it's `+3*2^30`
  = 270 degrees, giving `sin(p+270)=-cos(p)` -> a constant -90 degree
  rotation - harmless in `RX*conj(replica)` and invisible in `|RD|`, but
  breaks bit-exactness). Worse: fixing the sign *without* swapping which tap
  drives which port gives `I=sin p, Q=cos p` = `j*e^{-jp}` - a **conjugated,
  spectrally inverted** chirp. Sign and port assignment must move together;
  rename signals after any swap so `_q` doesn't end up driving I.
- LUT address: top 12 bits of phase -> truncation spurs ~= -72 dBc, under a
  12-bit DAC's ~-74 dB floor; use 14 bits (-84 dBc, 4k x 16 BRAM after
  folding) for margin. No dithering needed.
- **Output: full-scale 16-bit** (matches `sdr.py`'s `2^15-1` DMA scaling) or
  the leakage level, MGC window, and every gain number shift when `tx_src`
  flips. (`axi_ad9361` takes the 16-bit word; DAC uses the top 12 bits.)
- I and Q must share the same LUT pipeline depth, and the exported
  `chirp_start` pulse must be delayed to match. I/Q skew -> image tone;
  mislabeled chirp start -> constant range bias that confuses Phase 5
  calibration.

### 4.5 Sample strobe

`dac_valid_i0` out of `axi_ad9361` is the sample-rate strobe (already paces
`tx_fir_interpolator`) and is not always continuously high. The NCO advances
**only on this strobe** - one source, no mux, in both I2 and I3. (An RX-side
strobe was considered and rejected: `dac_valid_i0` is already available
whenever the TX interface is up regardless of DMA activity, and Phase 5's
replica delay line wants one shared time base.)

Don't free-run on `l_clk` - chirp duration would scale with strobe duty and
stop matching the golden model.

Sanity check: CHIRP_COUNT delta over 1 s should be 10000; 5000 or 20000 means
the strobe assumption is wrong.

`tx_src` mid-flight glitches one DAC sample - harmless, but since the
chirp-boundary machinery already exists, registering the mux select at
period start is one line and makes it clean. Recommended.

### 4.6 Where the NCO can disagree with the golden model

| # | Mismatch | Size at defaults | Action |
|---|---|---|---|
| 1 | Half-sample grid offset (4.2) | -4417 Hz = 0.44 bins = 1.33 m | **Fix** in `register_image`: `+s/2` on FTW_START |
| 2 | Triangle turnaround (4.1) | -8834 Hz = 0.88 bins, down leg | **Fix** in HDL: one-update FTW hold |
| 3 | I/Q convention (4.4) | constant -90 deg, or spectral inversion if half-fixed | **Fix** in HDL: `+2^30` tap + port swap |
| 4 | Fixed-point quantization | <=1 LSB, ~-72 dBc spurs | **Replicate** in `nco_reference` |

Category 4, bit-for-bit, each has a plausible wrong answer:
- LUT is `floor(sin(i*pi/2/4096)*32767)` - truncated, not rounded. Peak entry
  32766; top ~20 entries saturate there.
- Address is the top 12 bits of phase, truncated.
- Quadrant fold maps odd quadrants to `C_LUT_SIZE-1-idx`, not
  `C_LUT_SIZE-idx` - worth ~-74 dBc, same order as the truncation spurs; not
  worth fixing, but a "correctly" mirrored model will never match.

Structural fix: have `fabric_regs.py` **generate** `dds_init.txt`, so the ROM,
VUnit vectors, and register image all come from one function.

Amplitude note: `sdr.py` does `chirp * (2**15-1)` then truncating
`.astype(np.int16)`, so DMA vs NCO differ by <=1 LSB (0.0003 dB) - invisible
in leakage level.

---

## 5. I2 - see the chirp, zero new tooling

Wire NCO I/Q onto `o_data_0`/`o_data_1` when `rx_dbg_mux=1`
(passthrough/`ramp_en` unchanged otherwise; define their priority in the tb).

```bash
rxtap 4 0x8EF21E2B    # FTW_START (includes +s/2, 4.2)
rxtap 5 0x000A3A87    # FTW_SLOPE
rxtap 6 5660          # SWEEP_LEN
rxtap 8 1             # COMMIT
rxtap 1 0x12          # CTRL: nco_en | rx_dbg_mux
```

Start the Python radar, open the Signals tab: RX spectrogram shows a clean
100 us sawtooth, -25->+25 MHz. Frame sync will do something nonsensical (no
leakage to lock to - the "RX" *is* the chirp); ignore the RD map. Set
`triangle_en` **and write COMMIT** and watch it become a triangle - on
hardware (I2 bring-up 2026-08-30) the bit does not act on its own, it latches
with the shadow parameter set at the next chirp boundary (see Section 2).

Checkpoint: live waveform reconfiguration from a shell, visible in the GUI,
no rebuild.

---

## 6. I3 - the DAC mux, and the determinism proof

### 6.1 Block design changes

In `system_bd.tcl`, near `tx_fir_interpolator`, current wiring:

```tcl
ad_connect axi_ad9361/dac_valid_i0 tx_fir_interpolator/dac_valid_0
ad_connect axi_ad9361/dac_data_i0 tx_fir_interpolator/data_out_0
ad_connect axi_ad9361/dac_data_q0 tx_fir_interpolator/data_out_1
```

becomes:

```tcl
ad_connect axi_ad9361/dac_valid_i0 tx_fir_interpolator/dac_valid_0
ad_connect axi_ad9361/dac_valid_i0 fmcw_core_0/i_dac_valid_0
ad_connect fmcw_core_0/i_dac_data_0 tx_fir_interpolator/data_out_0
ad_connect fmcw_core_0/i_dac_data_1 tx_fir_interpolator/data_out_1
ad_connect axi_ad9361/dac_data_i0 fmcw_core_0/o_dac_data_0
ad_connect axi_ad9361/dac_data_q0 fmcw_core_0/o_dac_data_1
```

**Data wires only** - no TX enable connections (Section 1). `enable_out_0/1`
stays wired `tx_fir_interpolator <-> tx_upack`, untouched; ch1 also
untouched.

*Tcl trap:* `#` only comments at the start of a command - `ad_connect a b #
todo` passes `#`/`todo` as extra args. Use `;# todo` or a separate line.

Sync mux (delete one line, add two):

```tcl
# delete: ad_connect axi_tdd_0/tdd_channel_1 axi_ad9361_adc_dma/sync
ad_connect axi_tdd_0/tdd_channel_1 fmcw_core_0/i_sync_in
ad_connect fmcw_core_0/o_dma_sync  axi_ad9361_adc_dma/sync
```

`sync_src=0` makes this a plain wire (stock behavior preserved -
`tdd_channel_1` idles high, which is what lets transfers start at all).
Don't set `sync_src=1` this phase except deliberately (Section 6.3).

Refresh Module first (watch for 19-loose-pins AXI inference failure if any
AXI port name got touched during rename), then build.

### 6.2 Exit test (phase gate)

Digital loopback on, Python unchanged, fake targets running:

1. Baseline: `tx_src=0` (DMA chirp). Note RD map, target positions, leakage
   level.
2. `tx_src=1`, `nco_en=1`, waveform registers = same B/T/FS as the Python
   config. Radar must behave identically - same RD map, detections, leakage
   (full-scale amplitude match, 4.4). Python is still generating/pushing its
   DMA chirp; it's just not reaching the DAC.
3. **Determinism proof:** log `estimate_chirp_offset` over many blocks and
   several `start()` cycles. DMA chirp wanders (software-paced); NCO must
   return the *same* value every time - that constant is the TX->RX digital
   latency. **Write it in TODO.md** - Phase 5's `DECHIRP_DELAY` seed.

If step 2 differs from baseline beyond +-1 LSB of float-vs-fixed-point
quantization: stop, investigate in simulation before it becomes an
unexplained 20 dB hit in Phase 5.

### 6.3 Optional now, mandatory later: sync experiment

Flip `sync_src=1`, restart Python: every `read_block()` should start near a
chirp boundary (`estimate_chirp_offset` ~= 0, not exact - cpack packs into
64-bit beats, plus a small fixed RX-tap-to-DMA latency). Verify while here,
for I6:

- **Pulse width:** a single-cycle `chirp_start` can fall between packed beats
  and be missed - stretch `o_dma_sync` over a few cycles (when `sync_src=1`)
  so it overlaps an accepted beat. Still deterministic since beats are
  phase-locked to samples.
- **One transfer per buffer:** libiio splits very large buffers into
  multiple DMA transfers, and SYNC_TRANSFER_START gates each one - a
  mid-CPI re-sync would drop samples. At 2.9 MB/CPI (128 reps) you're under
  the ceiling, but confirm: a phase/timing discontinuity partway through a
  CPI with `sync_src=1` but not 0 is this.

Set `sync_src` back to 0 for daily use until I6 retires frame sync.

---

## 7. Testbench (extend, don't rewrite)

`tb_rx_tap` -> `tb_fmcw_core`, keeps its two clocks and axi_write/axi_read
procedures; existing six checks stay green. New checks:

1. **NCO vs golden vector, bit-exact.** A Python script (Section 8's
   fixed-point model) writes N samples of reference (I,Q); the tb configures
   the core via real AXI writes using the same numbers, enables, and
   `check_equal`s every strobed sample against the file. VUnit path:
   plain-text integers via std.textio, path passed as a generic. Bit-exact
   means `=`, not close.
2. **Period framing:** `chirp_start` at sample 0 of every period (length
   SWEEP_LEN sawtooth, 2x triangle); CHIRP_COUNT increments per period,
   survives the gray crossing.
   - **2b Triangle turnaround (4.1):** capture the FTW sequence across the
     leg boundary, assert the apex value appears *twice*. If once, the hold
     is missing and the whole down leg is off by `s`.
   - **2c I/Q convention (4.4):** assert `I[0]>0`, `Q[0]=0` at sample 0 of a
     period (phase 0 -> cos=full scale, sin=0) - catches both the -90 degree
     rotation and spectral inversion. Follow with a mid-sweep sample where
     I/Q differ in sign to pin rotation direction.
3. **Shadow/commit atomicity:** Section 3's check - commit mid-sweep, old
   parameters run to the last sample, new set active from next period's
   sample 0. Also: commit while `nco_en=0` loads immediately.
4. **Mux sanity:** `tx_src=0` passes `i_dac_data_*` through untouched
   (pipeline delay only); `rx_dbg_mux=0` keeps RX transparent; power-on state
   (all CTRL zero) = both paths transparent, `o_dma_sync = i_sync_in`.
5. **Strobe discipline:** drop `i_dac_valid_0` low mid-sweep - phase, ftw,
   and the sample counter must all hold.

The reference-file machinery is worth getting right: Phase 5's dechirp
verification (`dsp.mix_signal` sample-for-sample) reuses it directly. Build
as: config -> `register_image()` + `nco_reference()` -> one script, no
hand-typed constants in the tb.

---

## 8. Host side: config.py -> registers

1. **`src/python/common/fabric_regs.py`** - pure, no I/O, production code
   (the online app writes real registers with it):
   - `register_image(cfg) -> dict[int,int]`: offset->value for
     FTW_START/SLOPE/SWEEP_LEN + CTRL bits (from TRIANGLE_EN), implementing
     4.2 exactly. Two's-complement encoding happens here, in one place.
2. **`.../hdl/projects/e200/test/nco_reference.py`** - HDL test
   infrastructure, not radar application code, so it lives with `run.py`
   rather than in `src/python`. Reaches into `src/python` only to reuse
   `dsp.generate_chirp` (the golden model) and `fabric_regs.chirp_ftw` (so it
   checks the exact FTW values `run.py` writes into the DUT):
   - `nco_reference(cfg, n) -> np.ndarray[complex]`: bit-true fixed-point
     model (32-bit wrapping adds, same LUT quantization as HDL - *not* a call
     to `generate_chirp`). Source of truth for VUnit reference files; must
     reproduce every 4.6 "replicate" item (floor-quantized LUT, truncated
     12-bit address, `C_LUT_SIZE-1-idx` fold, `I=cos`/`Q=sin`).
   - `dds_lut_table()` -> the 4096 quarter-wave entries, plus an entry point
     that writes `src/nco/dds/dds_init.txt`. Generating the ROM from the same
     function as the model keeps them from drifting apart. Regenerate-and-
     diff in CI, or assert the checked-in file matches on import.
   - Unit tests (offline): (a) `nco_reference` vs `generate_chirp` float -
     spectral error < ~-70 dBc, bounded phase error (only meaningful once
     4.6 fixes land); (b) round-trip encoding sanity (FTW_START for -25 MHz
     decodes back within FS/2^32, including `+s/2`); (c) triangle: last
     up-leg FTW == first down-leg FTW.
3. **`src/python/online/fabric_ctl.py`** - dumb transport: `write_regs(image,
   ip)` shells `ssh root@{ip} /root/rxtap <word_index> <value>` per register,
   then COMMIT. (`rxtap` addresses by word index = offset/4 - keep the `/4`
   in one named place.) Batch in one ssh call (`rxtap 4 ... && rxtap 5 ...`)
   since session setup dominates. subprocess over paramiko for now (zero new
   deps); swap later if session reuse matters. GUI integration is I6 - resist
   it now.

---

## 9. Troubleshooting

- **RX capture hangs forever, no data/error** -> DMA sync trap: `sync_src=1`
  with NCO disabled (no pulses), wrong mux default, or OR'd instead of
  MUXed. Signature: `read_block` blocks on the first buffer. Fix: `rxtap`
  CTRL `sync_src` back to 0 (no power cycle needed, unlike the Phase 3 hang).
- **Chirp duration off by 2x/4x** -> NCO advancing on raw `l_clk` or the
  wrong strobe. CHIRP_COUNT meter (10000/s) catches it.
- **Spectrogram sweeps at half/double bandwidth** -> FTW_SLOPE off by a
  factor (check the `FS^2` denominator), or SWEEP_LEN/slope computed from
  inconsistent FS.
- **Mirrored sweep (+25->-25 instead of -25->+25)** -> sign error in
  FTW_START, or I/Q swapped at DAC ports. Also: fixing the quadrature sign
  without swapping ports gives `j*e^{-jp}`, a conjugated chirp (4.4). Compare
  against the known-good DMA-chirp spectrogram.
- **Every target moves ~1.3 m when flipping tx_src** -> missing `+s/2` in
  FTW_START (4.2); 0.44 range bins reads like a calibration error, not a bug.
  Check the register value first.
- **Sawtooth fine, triangle targets smear/split** -> missing FTW hold at
  turnaround (4.1); down leg runs 0.88 bins off the up leg.
- **Discrete tones around the chirp** -> I/Q pipeline skew (image tone), or
  phase truncation taken from the wrong end of `r_phase` (must be the top
  bits).
- **Leakage level shifts with tx_src** -> amplitude mismatch vs the
  `2^15-1` DMA scaling (4.4).
- **`estimate_chirp_offset` constant but different after each reboot** ->
  expected (per-bitstream/bring-up constant); if it moves *within* a run,
  suspect AD9361 interface calibration or a `set_loopback` toggle mid-
  measurement.
- **`dac_dunf`/underflow in dmesg with `tx_src=NCO`** -> expected if you
  stopped pushing the TX buffer (upack's `rd_en` loop keeps reading with
  nothing feeding it). Keep pushing the cyclic buffer, or ignore the flag -
  don't re-plumb `rd_en`.
- **Vivado: interface inference broke after rename** -> an `s_axi_*` name
  got eaten by find-replace; 19 loose pins is the symptom. Refresh Module.
- **Timing failures on shadow->active paths** -> Section 3's false-path
  constraint missing or matched nothing (grep `timing_impl.log`, same drill
  as Phase 3).
- **It all worked, then vanished after a rebuild** -> which branch, did you
  commit?

---

## Where this leads

Phase 5 = I4+I5, everything already parked in this design:

- **Dechirp:** complex multiply `RX x conj(replica)` at the RX insertion
  point; replica = NCO output through a DECHIRP_DELAY-deep delay line (0x24,
  reserved). Verified with the Section 7 reference-file machinery against
  `dsp.mix_signal` in digital loopback - no RF needed.
- **Decimation:** CIC/halfband chain after the mixer, DECIM_SEL (0x28);
  IF_SEL (0x2C) finally switches cpack onto the IF stream - Ethernet stops
  carrying raw IQ. The core's strobe discipline (everything advances on
  strobes, nothing assumes continuous valid) is what makes this a wiring
  change.
- **I6:** `sync_src=1` becomes default, `estimate_chirp_offset`/frame sync
  are deleted from the IF path, `fabric_ctl.write_regs` becomes
  RE-CONFIGURE. Still waits on Parts F+G on real RF.

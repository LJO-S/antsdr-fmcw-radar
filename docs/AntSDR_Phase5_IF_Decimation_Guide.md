# AntSDR E200 - Phase 5: decimation to IF rate (Part I5)

Follows Phase 4 (archived). Assumes I4 and I4b are done: fabric NCO transmits,
`mixer_dechirp` runs inside `fmcw_core`, IF_SEL=1 puts the IF on the capture path,
the DMA sync is level-held, dechirp delay is 34 in digital loopback (61 on cable),
and `fabric_ctl.py` brings the core up from the app. MAGIC is FMC3.

Same contract: this guide specifies, you write the code. Exact text where a typo
costs a synthesis run. The register map, sequencing rules and hardware gotchas
from Phase 4 live in `CLAUDE.md` ("Firmware / HDL track"); this guide only states
what changes.

## 1. What we are doing and why

The fabric dechirps, but the IF still leaves the board at 56.6 MSPS: 2.9 MB per
CPI, 226 MB/s if streamed, over a link that does 118. The radar captures a block,
ships it, and is blind in between. The IF only needs the beat bandwidth, which
is set by how far we want to see. A lowpass and a divide-by-8 bring the stream
to 28 MB/s. Then the link stops being the limit, CPIs run back to back, and the
frame rate is set by the PC instead of the wire.

### The decided envelope (2026-09-15)

- **Operational range 800 m** at the steepest chirp we will use, **56 MHz over
  100 us**. That beat is 3.0 MHz.
- **Divide by 8, fixed.** IF rate 7.075 MSPS at FS = 56.6, flat passband to
  2.8 MHz: 750 m at the steepest chirp, 850 m at today's 50 MHz.
- Behind the number: at the legal 14 dBm EIRP only the RX aperture buys range.
  A 4x4 patch array sees a person to ~500 m, the 30 dBi dish to ~1 km. No PA
  is planned. Hundreds of metres is the honest envelope.

### What stays free

Everything: sweep time, bandwidth, chirp count, sample rate, triangle. The
decimator restarts its output grid at every chirp leg (Section 2.4), so any
sweep length gives a whole number of IF samples per leg, `SWEEP_LEN // 8`. The
*effect* of a config change is a derived number, **effective range**, in the
Configuration tab:

```
effective_range = 0.4 * FS_IF * c * T / (2 * B)        FS_IF = FS / 8
```

| Chirp (FS = 56.6) | Effective range |
|---|---|
| 56 MHz / 100 us | 750 m |
| 50 MHz / 100 us | 850 m |
| 50 MHz / 50 us | 425 m |
| 25 MHz / 100 us | 1700 m |

`OP_RANGE_FACTOR` goes. In its place: `MAX_RANGE_M` (default 800, metres),
which caps the range axis and the CFAR search, plus the derived effective
range with a GUI warning when it is below `MAX_RANGE_M`.

Decimation does not change SNR after the FFTs: noise bandwidth and processing
gain shrink together. Range resolution is set by B, untouched. The range-bin
spacing is identical before and after; only the bin count shrinks.

---

## 2. The filter: three halfband stages

### 2.1 Decision

Own HDL, inside `fmcw_core`: **three cascaded decimate-by-2 halfband stages**,
one entity instantiated three times. The source is `halfband_decimate` from
`~/work/projects/fpga/fpga-filters`, which already builds
`log2(G_MULTIRATE_FACTOR)` stages from a generate loop - `G_MULTIRATE_FACTOR = 8`
is the whole chain, and `M = 32` is the config its own testbench runs. Fork it
for the two things it does not have: a `restart` input and a tag passthrough
(2.4, 3.1).

Not the Xilinx `fir_compiler` IP, the first plan: ~16 DSP slices, an encrypted
behavioural model Questa FSE cannot run (Appendix A has the workaround if it is
ever needed), and no per-chirp phase restart, which forced a sample-dropping
gate in the core.

Not CIC-by-4 plus one halfband, the second plan: a CIC is only valid on a
**uniform** decimation grid, so restarting its output phase per leg means also
clearing its integrator and comb registers - and if that is missed, the combs
stop cancelling the integrators across the irregular gap, the residual tracks
the never-resetting integrator state, and the output runs away past the 24-bit
range. Simulated at leg 5661: 1e18 against a 2^23 range, about four wrapped
full-scale samples per leg, once per chirp, forever. It hides too: it only
appears when `SWEEP_LEN mod 4 != 0`, and today's 5660 is a multiple of 4. The
CIC also carries 2.2 dB of passband droop. Three halfbands cost 12 more DSP
slices out of ~185 free and delete both problems - every stage is an FIR with
no feedback, nothing to overflow, and the droop is gone.

### 2.2 The numbers (FS = 56.6, protect 0.4 FS_IF = 2.83 MHz)

A halfband's band edges are symmetric about a quarter of its input rate:
`fpass + fstop = fs_in / 2`. Protect the same 2.83 MHz all the way down and the
transition band tightens by 2x per stage, which is why the first two stages are
nearly free and the last one carries the work.

| Stage | Rate in -> out | Taps | Multiplies /ch | Stopband |
|---|---|---|---|---|
| 1 | 56.6 -> 28.3 MSPS | 7 | 2 | -69.7 dB |
| 2 | 28.3 -> 14.15 MSPS | 11 | 3 | -66.7 dB |
| 3 | 14.15 -> 7.075 MSPS | 27 | 7 | -51.0 dB |
| **Chain** | 56.6 -> 7.075 | | **12** | **-51.4 dB worst alias** |

Passband droop at 2.83 MHz: **-0.03 dB**, ripple 0.059 dB peak to peak. Flat, so
nothing in `dsp.py` needs a compensation curve and the range axis carries no
amplitude tilt.

Stage 3 is the dial - it owns the tight transition, and stages 1 and 2 stay at
7 and 11 taps in every variant:

| Stage 3 taps | Multiplies /ch | I+Q DSPs | Cascade alias |
|---|---|---|---|
| 23 | 6 | 22 | -45.2 dB |
| **27** | **7** | **24** | **-51.4 dB** |
| 31 | 8 | 26 | -56.9 dB |

Take 27. 23 clears the 42 dB floor, but that floor is not a radar-derived
requirement - it is what the rejected CIC chain happened to deliver - and two DSP
slices out of ~170 idle is not a saving worth 6 dB of margin against things not
yet in the model (mixer spurs, the Q15 saturation nonlinearity, any later push
past 800 m). Above 35 taps the cascade starts running into stage 1's own
stopband, so the 7/11/x geometry stops scaling there.

**DSP budget** (7020: 220 slices; last build 75 used, 3 of them ours): 12
multiplies per channel, I and Q, = **24 slices**. The repo's stage is a
fully-parallel transposed FIR whose MAC array runs at half its input rate, so
the multiplies are not time-shared and 24 is the honest number. Section 4.2
reclaims the two unused ADI filters, worth about 50.

### 2.3 Word widths and rounding, so the model can be exact

One stage, as `halfband_decimate_stage` builds it:

- Data 16-bit in, 16-bit out (Q15 from the mixer). Coefficients 16-bit signed,
  Q15.
- **The centre tap is not in the coefficient memory.** It is 0.5, applied to
  the delayed input sample as a shift. The stored taps are the non-centre ones,
  folded so a symmetric pair shares a multiply: `U/2` entries for `U` non-centre
  non-zero taps, and `N = 2U - 1` taps in total. Our three stages are
  `U` = 4, 6, 14 -> 2, 3, 7 multiplies -> `N` = 7, 11, 27, with
  `G_NUM_TAPS_LOWER` (the centre-path delay) = `U/2 - 1` = 1, 2, 6.
- The upper branch accumulates in `16 + 16 + ceil(log2(U))` bits: **34, 35, 36**
  for the three stages.
- The output is

  ```
  y = clip16( (acc_upper >> 15) + (acc_lower >> 1) )
  ```

  **Two independent arithmetic shifts, so two independent floors** - not one
  floor over a combined accumulator. This is the likeliest place for model and
  RTL to part company by one LSB. Write it exactly this way in both.
- `clip16` saturates, it does not wrap, and it is reachable: worst-case gain is
  `sum|h|` = 1.14, 1.23, 1.54 for the three stages.
- Every stage advances on `valid` only, like everything else in the core. A
  valid gap freezes it.

**Each stage's non-centre taps must sum to exactly 16384**, and this is the one
place where the structure bites back. With the centre contributing 0.5, that sum
is the only way a stage has unity DC gain - but an ordinary equiripple halfband
*cannot* deliver it, for a reason worth understanding rather than patching.

Because the centre tap sits at offset 0 and every other tap at an odd offset,
and `cos(k(pi - w)) = -cos(kw)` for odd k, the odd-offset terms flip sign under
`w -> pi - w` while the centre does not:

```
A(w) + A(pi - w) = 2 * 0.5 = 1          for ANY tap values
```

Set `w = 0`: `A(0) + A(fs/2) = 1`. The DC-gain error and the response at Nyquist
are **the same number with opposite sign**. An equiripple stopband has
`|A(fs/2)| = delta`, so an unconstrained equiripple halfband of this structure
always has DC gain `1 -/+ delta`. Measured on our three stages: residuals of
+6, -12 and -88 counts, i.e. -74.8, -68.7 and -51.4 dB - and those *are* the
stopband levels of those designs. Nothing is broken; the identity forces it.

So exact unity gain means `A(fs/2) = 0` - a spectral null at Nyquist - which
deletes one alternation from the equiripple solution and pushes the rest up.
**Constrain the design; do not patch the taps afterwards.** Minimax stopband
subject to `sum = 16384` costs **0.22 dB** (-51.64 dB unconstrained, -51.42 dB
constrained). Patching a finished design costs 3.3 to 5.9 dB depending on where
the residual is dumped - measured: innermost pair -48.3 dB, outermost pair
-45.8 dB, spread proportionally -45.8 dB. The generator does the constrained
solve (4.1); nothing here patches taps.

What the null buys is perfect rejection of whatever folds onto DC - range bin 0,
which `CFAR_MASK_N` already masks. Almost nothing, spectrally. Unity gain is
worth having for 0.2 dB because it makes 7.1 a clean test and stops full-scale
input clipping silently; it would not be worth 3.4.

With the sums forced, the chain is exact where it matters and honest where it
cannot be:

- A ramp of step 1 in gives a ramp of **step exactly 8** out. That is 7.1.
- DC in gives DC out **to within one count**: +32767 comes back as 32766,
  -32768 as -32768. That count is the floor bias of the two shifts. It is
  deterministic, the model reproduces it, and it lands in range bin 0 where the
  fast-time mean subtraction already removes it. Do not chase it in fabric.

### 2.4 Phase restart on the leg tag

Each stage has a `restart` input that resets **the decimation phase only** -
`r_sel`, one flip-flop, saying whether the next input sample produces an output.
The delay lines keep their history.

That is the whole mechanism, because every stage is an FIR: an irregular gap in
the output grid changes which input phase an output lands on, and nothing else.
No feedback to destabilise, no accumulator to overflow.

Restart chains down the cascade: stage 1 restarts on the leg's first input
sample, stage 2 on stage 1's first output of that leg, stage 3 on stage 2's. The
tag lane carries it - each stage passes the tag through with the output it
belongs to (3.1) - so a stage's restart is simply the previous stage's tag.

A leg of N input samples then yields `((N // 2) // 2) // 2 = N // 8` outputs for
**any** N: 5660, 5661 and 5663 all give 707, and 262 gives 32. The last
`N mod 8` inputs enter the filters and produce no output. This is what keeps
every chirp parameter free.

The first outputs of a leg are computed partly from the previous leg's samples,
since the delay lines are not cleared. They sit at the chirp discontinuity,
under the range window's edge. Clearing the delay lines instead would swap that
for a startup transient of the same length, so it is not worth the logic.

**Second line of defence, on the host: make `SWEEP_LEN` a multiple of 8**
(Section 6). Nothing in the chain needs it - unlike the CIC design, which did -
but it makes `N_IF = SWEEP_LEN / 8` exact rather than a floor, and costs at most
124 ns of sweep time.

The tag that says "first sample of a leg" is generated by the NCO and rides
alongside the TX replica through the delay line and the mixer, so it arrives at
the IF sample it belongs to (Section 3.1).

---

## 3. `fmcw_core` changes (MAGIC -> FMC4 = `0x464D4334`)

### 3.1 Tag lane

Two bits, leg start and period start, travel with the data:

- `chirp_generator`: new output `o_new_leg` (pulse at sample 0 of every leg;
  equals `o_data_new_period` in sawtooth).
- `delay_line`: a 2-bit tag lane, shifted exactly like the valid lane,
  same tap select. `i_tag` in, `o_tag` out.
- `mixer_dechirp`: `i_tx_tag` in, `o_if_tag` out, through the same 6-stage
  register as the valid. `o_if_tag(0)` is then high on the first IF sample
  of a leg, `o_if_tag(1)` on the first IF sample of a period.
- Stage 1 takes `o_if_tag(0)` as its `restart`. Every stage passes both tag
  bits through to its output alongside the valid (`o_tag`), so stage k+1's
  restart is stage k's `o_tag(0)`, and the fully decimated stream still carries
  "first sample of a period" in stage 3's `o_tag(1)`.

### 3.2 Datapath

The decimator chain sits after the existing ADC output mux (ramp / NCO debug /
IF / passthrough) and before the output register:

```
mux -> [ hb_decimate x3 ] -> o_adc_data_0/1, o_adc_valid
         bypassed when decimate_sel = 0
```

After the mux, not only on the IF branch, so the ramp test (Section 7) goes
through the real filters. `decimate_sel = 0` is a wire: passthrough delivers all N
samples as today.

`o_adc_valid` is now the decimated valid, which is what cpack sees, so
**`p_dma_sync_latch` needs no change**: it already counts `r_adc_valid`.
Change only its trigger: when `if_sel = 1`, set the latch on the decimated
stream's period tag (`o_tag(1)` out of stage 3) instead of the NCO's
`new_period`. The DMA transfer then starts on the first IF sample of a period,
give or take the cpack pair phase.

Ramp mode gets two changes, both so the ramp is a *bit-exact* test vector and
not just an approximately-right sawtooth.

**The counter advances on the strobe the ramp mux itself emits on.** In ramp
mode the mux takes its valid from the NCO, so with `nco_en = 1` the counter
must count NCO valids, not ADC valids. They run 1:1 on hardware, so counting
either one *looks* right - but 7.1 is an `np.array_equal` over the whole
block, and a single doubled or missed increment fails it. With `nco_en = 0`
the NCO emits no valids at all, so the counter falls back to `i_adc_valid`;
that is the only thing keeping the mux-priority sanity check (`ramp_en` beats
`rx_dbg_mux`, NCO idle) able to see the counter move.

**The counter resets on the period tag the ramp mux emits**, i.e. the NCO's
own `o_data_new_period`, not the mixer's IF tag - in ramp mode `IF_SEL = 0`
(7.1), so there is no IF tag in the datapath, and the tag travelling with the
ramp is the one the decimator restarts on. Resetting on anything else puts the
drop a pipeline's worth of samples away from the restart.

Take the ramp value **combinatorially**, so the sample carrying the tag *is*
the ramp origin, and reload the counter at 1 behind it. A registered reset
puts 0 on the sample *after* the tag, leaving the tagged sample holding the
previous leg's last value - the ramp still steps by 8, so it passes 7.1 and
then quietly reads a first drop index of 1 in 7.2.

The reset is on the period tag, not the leg tag: in triangle mode the
decimator restarts every leg but the ramp drops once per period, which is what
7.2 measures against the DMA sync. The ramp is then a sawtooth with its drop at
the chirp boundary, which is how the frame offset is measured after the
decimator (7.2).

### 3.3 Registers

| Offset | Name | Change |
|---|---|---|
| 0x28 | DECIMATE_SEL | Live. `0` = passthrough, `1` = divide by 8. Takes effect immediately; **not** commit-latched. |
| 0x34 | STATUS | bit0 unchanged; **bit1 = `decimate_sel` as seen in `l_clk`** |
| 0x00 | MAGIC | `0x464D4334` "FMC4" |

No new ports on `fmcw_core`. No block-design change for the decimator.

Constraints (`system_constr.xdc`): a 2-flop synchronizer for `decimate_sel` (copy
the `r_if_sel` idiom, ASYNC_REG) and its `set_false_path -to *r_decimate_sel_meta*`.
While there: `r_dechirp_dly -> delay_line` is an unconstrained clock crossing
today; give it `set_max_delay -datapath_only`.

Makefile `M_DEPS`: add every `src/decimate/` file - `halfband_decimate.vhd`,
`halfband_decimate_stage.vhd`, `halfband_decimate_pkg.vhd` and the three coefficient
init files - and the four missing today
(`src/mixer/mixer_dechirp.vhd`, `src/delay/delay_line.vhd`,
`src/arithmetic/complex_mult.vhd`, `src/nco/dds/dds_init.txt`). Without them
`make` does not notice an edit to the mixer.

### 3.4 Testbenches

One new standalone bench, same shape as `tb_mixer_dechirp` (stimulus file in,
`output_data.txt` out, Python `post_check` bit-exact):

- `tb_halfband_decimate`, run per stage (7, 11 and 27 taps) and as the `M = 8`
  cascade. Configs: random int16 stimulus including full-scale; valid gaps
  mid-stream; `restart` at a spacing that is not a multiple of 8; a sign-matched
  full-scale burst `x[n] = 32767 * sign(h[K - n])` to reach the saturation - a
  DC stimulus cannot, its gain is 1.0. Output must equal `decimate_golden` sample
  for sample, and the cascade must emit exactly `N // 8` samples per leg.
  **Tag placement:** a restart-tagged input sample is vetoed (produces no
  output itself); check `o_tag` lands on the NEXT output, not on whatever
  output happens to align with the tagged input's own index - data/valid can
  look correct while the tag is silently one output off.

`tb_fmcw_core`, extend:

- Registers: DECIMATE_SEL write/readback, STATUS bit1, MAGIC FMC4, `0x30` still
  `0xDEADC0DE`.
- **Samples per leg:** dechirp config with `chirp_len = 262`, `decimate_sel = 1`,
  sawtooth and triangle: exactly 32 decimated samples between consecutive leg
  tags; `decimate_sel = 0`: 262. Use 262, not 260 - both give 32, but 262 is not a
  multiple of 8, so the restart actually has an irregular gap to absorb.
- **Chain bit-exact:** the dechirp config's captured decimated IF equals
  `decimate_golden(mixer_golden(...))` with the restarts at the leg tags.
- **Sync at decimated rate:** `period-framing` with `decimate_sel = 1` across
  `pack_phase` x `adc_phase`; the stretched sync must overlap a modelled cpack
  beat in every config. Existing configs stay green.
- **Ramp reset:** with `nco_en = 1` the ramp reads exactly 0 **on** the
  period-tagged sample itself (not the one after it) and 1 on the next, and
  steps by 1 per emitted sample across the whole block. Check the tagged
  sample's value directly - checking only "the ramp drops somewhere near the
  boundary" is what lets the off-by-one through to 7.2.

---

## 4. Coefficients and block design

### 4.1 Coefficients

Two tools, with a clean split: the filters repo supplies the **RTL and the
geometry**, this repo designs the **coefficients**.

**Step one, in `~/work/projects/fpga/fpga-filters`.** Its `test/run.py` already
has a halfband-decimate config; set `M = 8`, `FS = 56.6e6`, `FPASS = 2.83e6`,
`atten_db = 55` and run `cd test && python run.py "lib.halfband_decimate_tb.*"`
(3 seconds). `Halfband_decimate` takes only `fpass`, `atten_db`, `fs` and
`multirate_factor`, deriving each stage as `fstop = fs_stage/2 - fpass` with
`fs_stage` halving per stage - the design in 2.2. What you want from this run is
`src/halfband/decimate/halfband_decimate_pkg.vhd`, which at `atten_db = 55` comes
out `C_NUM_TAPS_UPPER = (4, 6, 14)` and `C_NUM_TAPS_LOWER = (1, 2, 6)`, i.e.
`U` = 4, 6, 14 and `N` = 7, 11, 27 exactly as 2.3 expects. (That file is
gitignored there - a build artifact in that repo, a checked-in source here.)

`atten_db` is only the generator's knob for choosing tap counts. It selects a
row of 2.2's stage-3 table and then stops mattering: **it does not appear
anywhere downstream**, because the coefficients it emits are not the ones we
ship.

The coefficients come from the same run. `Halfband_filter` takes
`a_unity_dc_gain` (added 2026-09-18, default off so the interpolation path is
untouched); with it set, the upper branch is re-solved as a constrained minimax -
minimise peak stopband subject to `sum = 16384`, via
`scipy.optimize.linprog(method="highs")` in `scripts/model/unity_dc_halfband.py`,
whose docstring carries the identity from 2.3. `run.py`'s halfband-decimate cfg
sets `unity_dc_gain=True`, so **what it emits is directly shippable** - there is
one coefficient designer, not a generator plus a fix-up.

The taps are stored as floats that quantise back exactly, so the float model the
VUnit `post_check` uses and the fixed-point file the RTL reads cannot disagree.
Verified 2026-09-18: all seven tests in that repo pass with the flag on, and the
emitted cascade measures -51.42 dB.

Copy the package and the three `HBF_16_{0,1,2}.txt` into `src/decimate/` and check
them in. Do not regenerate at build time - RTL/model coefficient drift is exactly
what checked-in files prevent.

For the record, the taps at 4 / 6 / 14:

```
stage 1: [-1089, 9281, 9281, -1089]
stage 2: [307, -1890, 9775, 9775, -1890, 307]
stage 3: [119, -264, 501, -928, 1637, -3201, 10328, 10328, -3201, 1637, -928,
          501, -264, 119]
```

### 4.2 Block design

Nothing for the decimator; it lives inside the core. One optional build,
**after Section 7.3 passes**, to reclaim the ADI filters the radar never uses
(two `fir_compiler` instances each on RX and TX, some 50 DSP slices between
them):

- `system_bd.tcl`: delete `rx_fir_decimator` and `decim_slice`, wire
  `fmcw_core_0/i_adc_*` straight from `axi_ad9361/adc_*_0`; delete
  `tx_fir_interpolator` and `interp_slice`, wire `fmcw_core_0/i_dac_data_*`
  straight from `tx_upack/fifo_rd_data_*` and `tx_upack/enable_*` from
  `axi_ad9361/dac_enable_*0`.
- linux submodule (branch first, per `docs/firmware-branch-workflow.md`):
  drop `adi,axi-decimation-core-available` and
  `adi,axi-interpolation-core-available` from `zynq-e200.dtsi`.
- `sdr.py`: remove the `cf-ad9361-lpc` rate pin; with the property gone that
  attribute stops accepting writes.
- Full image (device tree changed). Done when 7.3 still passes and the
  utilization report shows roughly 50 DSPs: 75 today, minus the ~50 the four
  reclaimed filters hold, plus our 24.

Two changes, two builds, one suspect each.

---

## 5. The model: `decimate_reference.py`

Next to `nco_reference.py` and `mixer_reference.py`, built on the filters repo's
`scripts/model/halfband_filter.py` rather than written from nothing. Jobs:

1. `load_taps() -> [int array x3]`: read the three checked-in coefficient init
   files - **the same files `init_ram_from_file` reads** - plus the package's
   per-stage `C_NUM_TAPS_UPPER` / `C_NUM_TAPS_LOWER`. The model never designs its
   own taps; the generator does (4.1), once, and its output is checked in. If the
   model solved for a second set, model and fabric could drift by a count and the
   bit-exact test would be grading the wrong thing.
2. `check_taps()`, run at import (the `dds_init.txt` pattern): per stage, odd taps
   zero, non-centre taps symmetric, and **summing to exactly 16384**; and the
   cascade response meets 2.2 - droop better than -0.1 dB at 0.4 FS_IF, worst
   alias below -50 dB. A failure here means someone regenerated the coefficients
   with `unity_dc_gain` off; re-run the generator with it on rather than patching
   the taps, for the reason in 2.3.
3. `hb_stage_golden(x, nc, restart_idx) -> y`: integer FIR, decimate by 2, and
   for every output

   ```
   y = clip16( (acc_upper >> 15) + (acc_lower >> 1) )
   ```

   with **two separate floors** (2.3). `restart_idx` resets the phase counter
   only; the delay line keeps its history.
4. `decimate_golden(x, restart_idx)`: the three stages in sequence, each stage's
   restart indices being the previous stage's output indices that carried the
   leg tag.

Offline tests (plain asserts, `__main__`, no board):

- (a) chain frequency response from the taps: droop at 0.4 FS_IF better than
  -0.1 dB, worst alias below -50 dB (the design above measures -0.03 and -51.4).
- (b) DC in gives DC out to within **one** count, and equal to what
  `decimate_golden` says exactly (+32767 -> 32766, -32768 -> -32768). Both halves
  matter: the bound catches a tap sum that is not 16384, the equality catches a
  model that rounds where the RTL floors.
- (c) a ramp of step 1 with restarts at its drops gives `N // 8` outputs per
  period and a ramp of **step exactly 8** inside each period. Run it for
  `N mod 8` = 0..7 (5656, 5660, 5661, 5663, 262) - all must give `N // 8`.
- (d) the checked-in init files parse and pass `check_taps()` - this is the one
  that fails when someone regenerates coefficients and forgets the 16384 rule.
- (e) the saturation path: a sign-matched full-scale burst clips at +-32767/-32768
  and does not wrap.

`dsp.py` needs nothing new: the passband is flat, so `process_cpi` sees an
ordinary IF at a lower rate.

---

## 6. Host side

**`config.py`**
- Delete `OP_RANGE_FACTOR`. Add `MAX_RANGE_M: float` (800, group `radar`).
- Add `FABRIC_DECIMATE: int` (8; allowed `{1, 8}`, 1 = passthrough for A/B) and
  `FABRIC_FRAME_TRIM: int` (0 until measured, IF samples), group `fabric`.
- Properties: `FS_IF = FS / FABRIC_DECIMATE if FABRIC_DECHIRP_EN else FS`,
  `SWEEP_LEN` (the note below), `T_EFF = SWEEP_LEN / FS`,
  `N_IF = SWEEP_LEN // FABRIC_DECIMATE` (exact, not a floor, once SWEEP_LEN is a
  multiple of the ratio), `EFFECTIVE_RANGE = 0.4 * FS_IF * c * T_EFF / (2 * B)`,
  `MAX_RANGE = min(MAX_RANGE_M, EFFECTIVE_RANGE)`. Properties, not fields.
- `describe()`: print FS_IF, N_IF, effective range.

**`fabric_regs.py`**: `MAGIC = 0x464D4334`; `DECIMATE_CODE = {1: 0, 8: 1}`;
`register_image` adds `REG_DECIMATE_SEL` when the fabric flag is on;
`STATUS_DECIMATE_ACTIVE = 1 << 1`.

Also: **`SWEEP_LEN` becomes a multiple of the ratio** (2.4). Put it in one
place - a `RadarConfig.SWEEP_LEN` property, `n = round(CHIRP_DUR_S * FS)` then
`n -= n % FABRIC_DECIMATE` when `FABRIC_DECHIRP_EN` - and have `chirp_ftw` take
that sample count instead of `dur_s`. **Recompute the slope from it**, or the
chirp sweeps less than the configured bandwidth:

```
slope     = bw_hz / (sweep_len * fs_hz)          # cycles / sample^2
ftw_start = round((-bw_hz / (2 * fs_hz) + slope / 2) * 2**32)
```

Identical numbers when `sweep_len == dur_s * fs_hz`, so software mode is
untouched. The leg shortens by at most 7 samples (124 ns in 100 us) and B stays
exactly as configured, which is the right way round: B sets the range axis, T
does not. The effective sweep time is then `SWEEP_LEN / FS` - use it, not
`CHIRP_DUR_S`, wherever the slope `S = B / T` feeds a range axis
(`build_cpi_context`, `target_sim.apply_if`, `EFFECTIVE_RANGE`) - that is what
`T_EFF` above is for. Otherwise the range scale is off by up to 0.12 % (1 m at
800 m, well under a bin, but free to get right).

**`fabric_ctl.py`**: `enable()`: ... -> CTRL with `nco_en` -> DECIMATE_SEL ->
IF_SEL last. `disable()`: IF_SEL=0 -> DECIMATE_SEL=0 -> CTRL=0 -> COMMIT. After
the STATUS read, require bit1 to match. `test_fabric_ctl.py`: assert the
positions; a software-mode image writes no DECIMATE_SEL.

**`dsp.py`**: `CPIContext` gains `N_if_samples`, `fs_if`. Range axis from
`fftfreq(N_if_samples, 1/fs_if)`; `pos` up to `cfg.MAX_RANGE`; window of
`N_if_samples`; `process_cpi` reshapes on it. Software mode: same values as
today. `spectrogram(..., a_fs=None)`; the worker passes `FS_IF` in fabric mode.

**`sdr.py`**: RX buffer `(CHIRP_REPS + SDR_RX_MARGIN_PERIODS) * N_IF`. Read
back the phy's `sampling_frequency` after writing it, warn if it differs from
FS; unchecked since Part B, and a mismatch scales the range axis.

**`capture.py`**: fabric branch slices `rx[TRIM : TRIM + rows * N_IF]` (rows =
`2 * CHIRP_REPS` in triangle) before `apply_if`.

**`target_sim.apply_if`**: `FS_IF` and `N_if_samples`; skip at `|f_b| >=
FS_IF / 2`, logged once.

**GUI**: nothing structural. Derived table: FS_IF, N_IF, effective range,
highlighted when below `MAX_RANGE_M`.

---

## 7. Bring-up, in order

Sim green first (3.4). Then digital loopback, fabric mode.

**7.1 Ramp: ratio and gain.** CTRL = `ramp_en | nco_en`, DECIMATE_SEL = 1,
IF_SEL = 0. The block is a sawtooth: consecutive samples differ by exactly 8
(every stage is exact unity gain, by the forced 16384 tap sum of 2.3) and the
drop repeats every `SWEEP_LEN // 8` samples. Compare the whole block with
`decimate_golden` test (c): `np.array_equal`, bit for bit. A step reading
8, 8, 8, 7 is a tap sum that is not 16384. Compare the whole block with `decimate_golden` test
(c): `np.array_equal`, bit for bit.

**7.2 Frame trim.** Same capture with `sync_src = 1`. The first drop's index
is `FABRIC_FRAME_TRIM`; expect 0 or 1 (the cpack pair phase), since the sync
now fires on the decimated period tag. Record it, re-run, drop at index 0.

**7.3 RD map A/B.** `dechirp_verify.py --decimate 1` then `--decimate 8`, fake
targets 100 / 300 / 500 m, +-20 m/s. Below 750 m: same detections in the same
bins, peak-relative maps within 1 dB where above -60 dB, leakage line at bin 0
the same shape. There is no droop to allow for - the chain is flat to 0.04 dB
(2.2) - so a tilt toward the far end of the range axis is a real finding, not an
expected artifact.

**7.4 Config freedom.** RE-CONFIGURE to T = 50 us, then B = 25 MHz, then
triangle. No hang; effective range reads 425 m, 1700 m; a fake target placed
beyond the effective range disappears, one inside stays.

**7.5 Exit.** CPIs per second in the GUI for a minute at ratio 8. Expect the
worker bound by `process_cpi` + Qt, not `refill()`; above ~10 fps is the exit.
Record the number in `TODO.md`. Then gitlink bump, I5 to one line, conclusions
into `CLAUDE.md`. Then Section 4.2's reclaim build.

---

## 8. Troubleshooting

- **Capture hangs after DECIMATE_SEL=1** -> the sync latch is not being set from
  the decimated period tag (the NCO-time pulse is gone before the first
  decimated valid), or the tag did not survive all three stages. Same signature
  as the I4 hang. Recovery: DECIMATE_SEL=0 from a shell.
- **Samples per leg off by one, or drifting** -> a `restart` is not landing on
  the right sample. Stage 1 restarts on the leg tag; stage k+1 restarts on
  stage k's tagged output, not on the leg tag directly. A tag lane one stage
  late gives exactly this.
- **Bit-exact fails by one LSB on scattered samples** -> the two floors of 2.3.
  The RTL does `(acc_upper >> 15) + (acc_lower >> 1)`; a model that combines the
  branches before one shift, or that rounds instead of flooring, drifts by a
  count. Check the saturation is a clip and not a `resize` wrap while you are
  there.
- **Ramp step is not 8** -> a stage's non-centre taps do not sum to 16384 (2.3)
  if it reads 8, 8, 8, 7 (or 9s scattered through 8s, which is the sum being
  *over* 16384); a valid counted twice or a stage bypassed if it is 4 or 16.
- **Halfband saturation never trips in the testbench** -> a DC stimulus cannot
  do it, the gain is 1.0. Use the sign-matched burst from 3.4.
- **Everything about 1 count low on a DC input** -> expected, that is the floor
  bias of the two shifts (2.3). It sits in range bin 0 behind the fast-time mean
  subtraction. Leave it.
- **Alias energy folding into the far range bins** -> stage 3's stopband; it is
  the binding one. 27 taps buys -51.4 dB, 31 buys -56.9 (2.2).
- **Fake targets vanish beyond ~1 km** -> the `apply_if` skip at FS_IF/2.
- **Range axis off by a fraction of a percent** -> FS readback (Section 6), or
  the range axis still deriving `S` from `CHIRP_DUR_S` instead of `T_EFF`.
- **Chirp aliased, NCO strobe at 1/8** -> `rx_fir_decimator` came alive; the
  `cf-ad9361-lpc` rate pin in `start()` is the guard until Section 4.2 removes
  the block.
- **Timing failure on `r_dechirp_dly` or `r_decimate_sel_*`** -> Section 3.3
  constraints; grep `timing_impl.log` for the cell names.

---

## 9. After this

I6 is small: `sync_src=1` is the fabric default, the trim is a constant,
`fabric_ctl` is behind RE-CONFIGURE. Left: remove frame sync from the *fabric*
path (it stays for software mode) and expose the bypass bits (already
auto-generated). Daily-driver cutover still waits for Parts F+G on real RF.
With 100 % duty, track filters and a clutter map become possible.

---

## Appendix A - simulating a Xilinx IP in Questa FSE, if it is ever needed

The free Intel edition cannot decrypt Xilinx's behavioural IP models, so
`compile_simlib` and Vivado's export flow are out. What works: every IP in the
block design gets a structural netlist generated next to it
(`e200.gen/sources_1/bd/system/ip/<name>/<name>_sim_netlist.vhdl`) whose only
dependency is `UNISIM.VCOMPONENTS`, plain VHDL under
`$VIVADO/data/vhdl/src/unisims/`. Compile that once:

```bash
U=~/tools/Xilinx/Vivado/2023.2/data/vhdl/src/unisims
vlib ~/xsim_libs/unisim
vcom -93 -work ~/xsim_libs/unisim $U/unisim_VPKG.vhd $U/unisim_VCOMP.vhd $U/primitive/*.vhd
```

Then in `run.py`: `VU.add_external_library("unisim", "~/xsim_libs/unisim")`,
add the netlist to a `xil_defaultlib` library with `no_parse=True`, and
instantiate the IP entity in a testbench. Bit- and cycle-accurate, tens of
times slower than RTL, and the netlist must be regenerated whenever the IP's
parameters (e.g. coefficients) change, because they are baked in.

## Appendix B - why one ratio

The envelope is decided (800 m at 56 MHz / 100 us) and the goal is to clear the
wire, not to minimise bytes. Divide by 4 would also clear it (57 MB/s) and
covers 1.7 km at today's chirp. With a halfband cascade the ratio is a generic:
drop `G_MULTIRATE_FACTOR` to 4 and stage 3 disappears, taking 7 of the 12
multiplies with it. Going the other way, /16 is one more stage and one more
coefficient set. Nothing else in the design cares.

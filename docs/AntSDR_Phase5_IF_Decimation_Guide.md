# AntSDR E200 - Phase 5: decimation to IF rate (Part I5)

Follows Phase 4 (archived). Assumes I4 and I4b are done: fabric NCO transmits,
`mixer_dechirp` runs inside `fmcw_core`, IF_SEL=1 puts the IF on the capture path,
the DMA sync is level-held, loopback and non-loopback dechirp delay is 34 and 61 respectively, and `fabric_ctl.py`
brings the core up from the app. MAGIC is FMC3.

Same contract: this guide specifies, you write the code. Exact text where a typo
costs a synthesis run. The register map, sequencing rules and hardware gotchas
from Phase 4 now live in `CLAUDE.md` ("Firmware / HDL track"); this guide only
states what changes.

## 1. What we are doing and why

The fabric dechirps, but the IF still leaves the board at 56.6 MSPS: 2.9 MB per
CPI, 226 MB/s if streamed, over a link that does 118. So the radar captures a
block, ships it, and is blind in between. The IF only needs the beat bandwidth,
and the beat bandwidth is set by how far we want to see. A lowpass filter and
a divide-by-8 bring the stream to 28 MB/s. That is the whole job: the link
stops being the limit, CPIs can run back to back, and the frame rate is set by
the PC instead of the wire.

### The decided envelope (2026-09-15)

- **Operational range: 800 m** at the steepest chirp we will use, **56 MHz over
  100 us**. That beat is 3.0 MHz.
- **Ratio: divide by 8**, fixed. IF rate 7.075 MSPS at FS = 56.6, flat passband
  to 2.8 MHz. That is 750 m at the steepest chirp, 850 m at today's 50 MHz.
- Hardware reality behind the number: at the legal 14 dBm EIRP only the RX
  aperture buys range. A 4x4 patch array sees a person to ~500 m, the 30 dBi
  dish to ~1 km. Hundreds of metres is the honest envelope; no PA is planned.

### What stays free

Everything. Sweep time, bandwidth, chirp count, sample rate, triangle. The
fabric takes whatever sample count per chirp that gives (Section 3 explains
how). The *effect* of a config change is a derived number, **effective range**,
shown in the Configuration tab:

```
effective_range = 0.4 * FS_IF * c * T / (2 * B)        FS_IF = FS / 8
```

Steeper chirp (shorter T or wider B) -> shorter effective range. Shallower ->
longer. Fewer chirps per CPI -> no change. The table below is at FS = 56.6:

| Chirp | Effective range |
|---|---|
| 56 MHz / 100 us | 750 m |
| 50 MHz / 100 us | 850 m |
| 50 MHz / 50 us | 425 m |
| 25 MHz / 100 us | 1700 m |

The old `OP_RANGE_FACTOR` (a fraction of the sweep) goes. In its place: one
config field in metres, `MAX_RANGE_M` (default 800), which caps the range axis
and the CFAR search; and the derived effective range, with a warning in the GUI
when it is below `MAX_RANGE_M`.

Decimation does not change SNR after the FFTs: it removes noise bandwidth and
processing gain in equal measure. Range resolution is untouched; it is set by
B. The range-bin spacing is identical before and after (FS_IF / N_IF = FS / N).
Only the number of bins shrinks.

---

## 2. The filter

### 2.1 Decision

Reuse ADI's `ad_add_decimation_filter` block (a Xilinx `fir_compiler`, decimate
by 8) with **our own coefficients**, placed in the block design between
`fmcw_core` and cpack. Reasons:

- Zero new filter HDL. The block already has the bypass mux and the clock
  crossing on its `active` pin.
- Exact model: an integer convolution plus one rounding step. The only unknown
  (how many low bits the IP drops) is pinned by one ramp test.
- Own coefficients because the stock ADI set only rejects aliases by 30 dB; a
  128-tap set designed in Python gives 57 dB with 0.02 dB passband ripple, in
  the same IP, for free.

Costed and rejected: a CIC alone (7 dB droop at the band edge, 18 dB alias
rejection at this passband width; would need a second compensating filter).
Appendix A keeps the numbers, and the cheaper own-HDL structure if DSP slices
ever get tight.

### DSP budget (the 7020 has 220 DSP48 slices)

From the last build's utilization report: **75 used**, of which `fmcw_core` uses
3 (the complex multiply). The other 72 are ADI's blocks: the AD9361 IQ
correction and four `fir_compiler` filters - `rx_fir_decimator` and
`tx_fir_interpolator`, two channels each - **which the radar never uses**
(both are held inactive; they exist so a Pluto can run below 2.083 MSPS).

The new filter at 128 taps, one sample per clock, symmetric coefficients,
costs about 8 slices per channel, **16 total**. The IP's summary page states
the exact count before you build. So:

- As an addition: 75 -> ~91 of 220. Not tight.
- As a replacement, which is the right way to do it: `if_decimator` *is*
  `rx_fir_decimator` moved to the other side of the mixer with our
  coefficients, and `tx_fir_interpolator` is deleted outright. Net DSP usage
  goes **down**, roughly 75 -> 60. Two things follow from deleting them: drop
  `adi,axi-decimation-core-available` and `adi,axi-interpolation-core-available`
  from the `cf-ad9361-lpc` / dds nodes in `zynq-e200.dtsi` (linux submodule,
  branch first per the workflow doc), and remove the `cf-ad9361-lpc` rate pin
  from `sdr.start()` - with the property gone that attribute stops accepting
  writes.

If the fabric later needs slices for a range FFT, a second RX channel or a
patch-array beamformer, Appendix A's CIC-plus-halfband does the same job in 2
to 4 slices instead of 16.

### 2.2 Where it sits, and what that changes

```
axi_ad9361 -> rx_fir_decimator (inactive) -> fmcw_core -> if_decimator -> cpack -> dma
```

Outside the core because the core is plain HDL and `fir_compiler` is an IP.
Two consequences:

1. **The DMA sync must count the valid that reaches cpack.** Today the latch
   holds `o_dma_sync` until two `o_adc_valid` strobes pass. After decimation a
   cpack beat comes every 16 input samples, so that hold would miss it - the I4
   hang again, at 1/8 rate. Fix: feed the decimator's `valid_out_0` back into
   the core as `i_dma_valid` and count that. In passthrough it equals
   `o_adc_valid`, so nothing changes at ratio 1.
2. **RX channel 2 is not usable while decimating.** cpack has one write enable
   and it will be the decimated one; channels 2/3 pass through unfiltered.
   They are disabled in `sdr.py` today. The second-RX idea (Part G) needs its
   own filter instance later. Note it, don't build it.

---

## 3. `fmcw_core` changes (MAGIC -> FMC4 = `0x464D4334`)

### 3.1 Whole samples per chirp: the leg gate

The host reshapes the IF into `[chirps x samples-per-chirp]`, so every chirp
must produce the same whole number of IF samples. With a fixed divide-by-8 and
a free sweep time that is true only by luck (5660 / 8 = 707.5). The core makes
it true: **at the end of every leg it drops the last `SWEEP_LEN mod 8` IF
samples**, so each leg feeds the decimator a multiple of 8 and the host gets
exactly `SWEEP_LEN // 8` samples per leg. The dropped samples sit at the chirp
discontinuity, under the window edge; at most 7 of 5660.

To do that the core must know where a leg starts *in the IF stream*, which is
the NCO's leg start delayed through the mixer. Carry two marker bits alongside
the TX replica:

- `chirp_generator`: new outputs `o_new_leg` (pulse at sample 0 of every leg;
  in sawtooth this equals `new_period`) and the existing `o_data_new_period`.
  Also expose the *active* `o_chirp_len` (the value in use, not the shadow).
- `delay_line`: a 2-bit marker lane, shifted exactly like the valid lane, with
  the same tap select. `i_mark` in, `o_mark` out.
- `mixer_dechirp`: `i_tx_mark` in, `o_if_mark` out, delayed through the same
  6-stage register as the valid. `o_if_mark(0)` is then high on the first IF
  sample of a leg, `o_if_mark(1)` on the first IF sample of a period.
- `fmcw_core`: a counter `r_if_leg_cnt` that resets on `o_if_mark(0)` and
  increments on every IF valid. When `decim_sel = 1` and `r_if_leg_cnt >=
  (o_chirp_len(31 downto 3) & "000")`, the IF valid presented on `o_adc_valid`
  is held low until the next leg mark. No gating when `decim_sel = 0`
  (passthrough must deliver all N samples, as today).

The period mark also fixes the sync: when `if_sel = 1`, drive the sync latch
from `o_if_mark(1)` instead of the NCO's `new_period`, so the DMA transfer
starts at the boundary *as it appears in the IF stream* and the mixer latency
drops out of the frame offset.

### 3.2 Registers and ports

| Offset | Name | Change |
|---|---|---|
| 0x28 | DECIM_SEL | Live. `0` = passthrough, `1` = divide by 8. Takes effect immediately (it drives the IP's `active` pin and the leg gate); **not** commit-latched. |
| 0x34 | STATUS | bit0 `dac_enable_i0` unchanged; **bit1 = `decim_active`** as seen in `l_clk` |
| 0x00 | MAGIC | `0x464D4334` "FMC4" |

New ports:

```vhdl
o_decim_active : out std_logic;   -- r_decim_sel(0), AXI-clock domain, raw: the
                                  --   if_decimator block synchronizes it itself
i_dma_valid    : in  std_logic;   -- if_decimator/valid_out_0, fed back
```

Internals: `p_dma_sync_latch` counts `i_dma_valid`; a 2-flop synchronizer for
the STATUS bit (copy the `r_if_sel` idiom, ASYNC_REG, false path to the meta
flop); the leg counter and gate from 3.1.

Ramp mode gets one change that pays for itself in Section 7: **when `nco_en =
1`, the ramp counter resets on the IF period mark** instead of free-running.
The ramp becomes a sawtooth with its drop at the chirp boundary, which is how
the frame offset is measured after the decimator.

Constraints (`system_constr.xdc`): one new `set_false_path -to
*r_decim_active_meta*`. While there: the `r_dechirp_dly -> delay_line` path is
an unconstrained clock crossing today (it happens to be timed as a normal path);
give it a `set_max_delay -datapath_only`.

Makefile `M_DEPS`: add the coe file (Section 4) and the four files missing
today: `src/mixer/mixer_dechirp.vhd`, `src/delay/delay_line.vhd`,
`src/arithmetic/complex_mult.vhd`, `src/nco/dds/dds_lut.txt`. Without them
`make` does not notice an edit to the mixer.

### 3.3 Testbench (`tb_fmcw_core`, extend)

- **Registers:** DECIM_SEL write/readback, STATUS bit1 follows it, MAGIC = FMC4,
  `0x30` still reads `0xDEADC0DE`.
- **Leg gate:** dechirp config with `chirp_len` not a multiple of 8 (e.g. 260)
  and `decim_sel = 1`: count IF valids between consecutive period marks; must be
  `chirp_len // 8 * 8` per leg, in sawtooth and triangle. With `decim_sel = 0`
  it must be `chirp_len`.
- **Marker alignment:** the first IF sample after `o_if_mark(0)` is the
  mixer's output for TX replica sample 0 of the leg (compare against
  `mixer_golden`, which knows the delay).
- **Sync at decimated rate:** model the decimator's valid as every 8th
  `o_adc_valid` delayed by a configurable number of clocks; extend
  `period-framing` with `decim_sel = 1` x `pack_phase` x `adc_phase`; the
  stretched sync must overlap a beat in every config. The existing configs
  stay green.
- **Ramp reset:** with `nco_en = 1`, the ramp drops to 0 on the IF period mark.

The IP itself is not simulated; it is verified on hardware against the model
(Section 7). That is the trade for not writing the filter.

---

## 4. Block design, coefficients, Makefile

### 4.1 Coefficients

`hdl/projects/e200/scripts/decim_reference.py` (Section 5) designs the taps and
writes `hdl/projects/e200/src/decim/if_decim.coe`. Check the coe in; the script
asserts on import that the checked-in file matches (the `dds_lut.txt` pattern).

Design: `scipy.signal.remez(128, [0, 0.4*FS_IF, 0.6*FS_IF, FS/2], [1, 0],
fs=FS)`, scaled to 16-bit integers with `sum(|taps|) = 2**17 - 1`. Copy the COE
header format from `library/util_fir_int/coefile_int.coe`; the parser is picky.

### 4.2 `system_bd.tcl`

Next to `rx_fir_decimator`:

```tcl
ad_add_decimation_filter "if_decimator" 8 2 1 {61.44} {61.44} \
                         "$ad_hdl_dir/projects/e200/src/decim/if_decim.coe"
```

Replace the channel-0/1 and `fifo_wr_en` lines of the "ADC outputs into cpack"
block with:

```tcl
ad_connect axi_ad9361/l_clk if_decimator/aclk
ad_connect if_decimator/data_in_0   fmcw_core_0/o_adc_data_0
ad_connect if_decimator/data_in_1   fmcw_core_0/o_adc_data_1
ad_connect if_decimator/enable_in_0 fmcw_core_0/o_adc_enable_0
ad_connect if_decimator/enable_in_1 fmcw_core_0/o_adc_enable_1
ad_connect if_decimator/valid_in_0  fmcw_core_0/o_adc_valid
ad_connect if_decimator/valid_in_1  fmcw_core_0/o_adc_valid
ad_connect if_decimator/active      fmcw_core_0/o_decim_active

ad_connect cpack/fifo_wr_data_0 if_decimator/data_out_0
ad_connect cpack/fifo_wr_data_1 if_decimator/data_out_1
ad_connect cpack/enable_0       if_decimator/enable_out_0
ad_connect cpack/enable_1       if_decimator/enable_out_1
ad_connect cpack/fifo_wr_en     if_decimator/valid_out_0

ad_connect fmcw_core_0/i_dma_valid if_decimator/valid_out_0
```

Channels 2/3 stay `fmcw_core_0 -> cpack`. Then reclaim the dead filters
(Section 2.1 DSP budget): delete the `rx_fir_decimator` instance and
`decim_slice`, wire `fmcw_core_0/i_adc_*` straight from `axi_ad9361/adc_*_0`;
delete `tx_fir_interpolator` and `interp_slice`, wire `fmcw_core_0/i_dac_data_*`
straight from `tx_upack/fifo_rd_data_*` and `tx_upack/enable_*` from
`axi_ad9361/dac_enable_*0`. Do the reclaim as its own build after the decimator
works, not in the same one - two changes, two builds, one suspect each. Refresh
Module after adding the ports (watch for the loose-pins AXI inference failure),
then build the full image.

### 4.3 Makefile

```make
M_DEPS += src/decim/if_decim.coe
M_DEPS += src/mixer/mixer_dechirp.vhd
M_DEPS += src/delay/delay_line.vhd
M_DEPS += src/arithmetic/complex_mult.vhd
M_DEPS += src/nco/dds/dds_lut.txt
```

---

## 5. The model: `decim_reference.py`

Next to `nco_reference.py` and `mixer_reference.py`. Three jobs:

1. `design_taps(fs_hz) -> int array` - the design from 4.1. Deterministic.
2. `write_coe(path)` plus the import-time check against the checked-in file.
3. `decim_golden(x_re, x_im, taps, shift, phase) -> (y_re, y_im)` - the IP,
   exactly: integer convolution in int64; keep every 8th output starting at
   `phase` (0-7); drop `shift` low bits **rounding toward zero**
   (`np.fix(acc / 2**shift)`, not `>>`, which rounds down); clip to int16.

`shift` and the pipeline latency are constants pinned on hardware (7.1), kept
in the module with the measurement in a comment. Expected: `shift = 18` for
`sum|taps|` just under 2^17, giving a DC gain of about 0.29. Harmless in
peak-relative maps; do not try to fix it in fabric.

Offline tests (plain asserts, `__main__`, no board): (a) tap response, ripple
< 0.05 dB below 0.4 FS_IF and stopband < -50 dB above 0.6 FS_IF; (b) DC input
gives `fix(sum(taps) * x / 2**shift)`; (c) a sawtooth ramp with period N
through the model gives a scaled sawtooth whose drop lands at a known output
index (this is the vector Section 7.2 compares against); (d) coe round-trip.

`dsp.py` needs nothing new: the passband is flat, so `process_cpi` sees an
ordinary IF at a lower rate.

---

## 6. Host side

**`config.py`**
- Delete `OP_RANGE_FACTOR`. Add `MAX_RANGE_M: float` (default 800, metres,
  group `radar`) - caps the range axis and the CFAR search.
- Add `FABRIC_DECIM: int` (default 8, allowed `{1, 8}`; 1 = passthrough for
  A/B tests) and `FABRIC_FRAME_TRIM: int` (default 0 until measured, IF
  samples), group `fabric`.
- Properties: `FS_IF = FS / FABRIC_DECIM if FABRIC_DECHIRP_EN else FS`,
  `N_IF = round(T * FS) // FABRIC_DECIM` (same gate; in software mode it is
  just N), `EFFECTIVE_RANGE = 0.4 * FS_IF * c * T / (2 * B)`, and `MAX_RANGE =
  min(MAX_RANGE_M, EFFECTIVE_RANGE)`. Properties, not fields: RE-CONFIGURE
  builds fresh instances.
- `describe()`: print FS_IF, N_IF, effective range.

**`fabric_regs.py`**: `MAGIC = 0x464D4334`; `DECIM_CODE = {1: 0, 8: 1}`;
`register_image` adds `REG_DECIM_SEL` when the fabric flag is on;
`STATUS_DECIM_ACTIVE = 1 << 1`.

**`fabric_ctl.py`**: DECIM_SEL is a path-shape register like IF_SEL.
`enable()`: ... -> CTRL with `nco_en` -> DECIM_SEL -> IF_SEL last. `disable()`:
IF_SEL=0 -> DECIM_SEL=0 -> CTRL=0 -> COMMIT. After the STATUS read, also require
bit1 to match the requested state. `test_fabric_ctl.py`: assert those
positions; a software-mode image writes no DECIM_SEL.

**`dsp.py`**: `CPIContext` gains `N_if_samples` and `fs_if`. Range axis from
`fftfreq(N_if_samples, 1/fs_if)`; `pos` mask up to `cfg.MAX_RANGE`; range
window of `N_if_samples`; `process_cpi` reshapes on `N_if_samples`. In software
mode these equal today's values. `spectrogram(..., a_fs=None)` takes the rate
as an argument; the worker passes `FS_IF` in fabric mode.

**`sdr.py`**: RX buffer `(CHIRP_REPS + SDR_RX_MARGIN_PERIODS) * N_IF` samples
(created in `start()`, before the fabric is enabled - fine). Read back the
phy's `sampling_frequency` after writing it and warn if it differs from FS;
nobody has checked this since Part B, and a mismatch scales the range axis.

**`capture.py`**: fabric branch slices `rx[TRIM : TRIM + rows * N_IF]` (rows =
`2 * CHIRP_REPS` in triangle mode) before `apply_if`.

**`target_sim.apply_if`**: use `FS_IF` and `N_if_samples`; the skip becomes
`|f_b| >= FS_IF / 2` (targets beyond ~1060 m are dropped, logged once).

**GUI**: nothing structural. The config tab picks up the new fields from
metadata. Derived table: add FS_IF, N_IF, effective range; colour the
effective-range row when it is below `MAX_RANGE_M`.

---

## 7. Bring-up, in order

Sim green first (3.3). Then digital loopback, fabric mode, in this order.

**7.1 Ramp: ratio, gain, bits dropped.** CTRL = `ramp_en | nco_en`, DECIM_SEL =
1, IF_SEL = 0. The block is a sawtooth: consecutive samples differ by
`8 * sum(taps) / 2**shift` (mod 2^16) - pins `shift` and proves the ratio - and
the drop repeats every `SWEEP_LEN // 8` samples.

**7.2 Frame trim.** Same capture, `sync_src = 1`. The first drop sits at index
`FABRIC_FRAME_TRIM`; the lowpass smears it over ~8 samples, so take the
midpoint crossing. Record it in `config.py`, re-run, the drop must now sit at
index 0 of every period. Compare the whole block against `decim_golden` test
(c) with `np.array_equal`: bit-exact means `=`.

**7.3 RD map A/B.** `dechirp_verify.py --decim {1,8}`, same fake targets
(100, 300, 500 m, +-20 m/s) via `apply_if`. Below 750 m: same detections in
the same bins, peak-relative maps within 1 dB where both are above -60 dB,
leakage line at bin 0 the same shape (`CFAR_MASK_N` is in bins and the bin
spacing did not change).

**7.4 Config freedom.** RE-CONFIGURE to T = 50 us, then B = 25 MHz, then
triangle on. Each must run without hanging; effective range in the derived
table must read 425 m, 1700 m, and the fake targets must track it (a target
placed beyond the effective range disappears, inside it stays).

**7.5 Exit.** CPIs per second in the GUI for a minute at ratio 8. Expect the
worker to be bound by `process_cpi` + Qt, not by `refill()`; above ~10 fps is
the exit. Record the number in `TODO.md`. Then gitlink bump, I5 to one line,
this guide's conclusions into `CLAUDE.md`.

---

## 8. Troubleshooting

- **Capture hangs after DECIM_SEL=1** -> sync latch still counts `o_adc_valid`,
  or `i_dma_valid` is unconnected (Vivado ties it low: never clears). Same
  signature as the I4 hang. Recovery: DECIM_SEL=0 from a shell.
- **RD map smears, targets walk in range chirp to chirp** -> the leg gate is
  not dropping the remainder, or the host uses `N // 8` but the fabric counts
  something else. Check 3.3's gate test.
- **Block 8x too long or short** -> `sdr.py` sized the buffer with N instead of
  N_IF, or config and register disagree on the ratio.
- **Everything 6 dB low, otherwise fine** -> `shift` in the model vs the IP.
  Only matters for the bit-exact test.
- **Bit-exact off by one LSB on some samples** -> rounding: `>>` rounds down,
  the IP rounds toward zero.
- **Sawtooth drop wanders by < 1 sample between runs** -> expected (which
  input sample of 8 becomes an output is set at enable time). By >= 8 samples
  -> a real latency change; re-measure the trim.
- **Fake targets vanish beyond ~1 km** -> the `apply_if` skip at FS_IF/2.
  Correct; they are outside the band.
- **Range axis off by a fraction of a percent on a known cable** -> the FS
  readback in `sdr.py`: the AD9361 did not give the rate asked for.
- **Chirp aliased, NCO strobe at 1/8** -> `rx_fir_decimator` came alive; the
  `cf-ad9361-lpc` rate pin in `start()` is the guard.
- **Timing failure on `r_dechirp_dly` or `r_decim_*`** -> Section 3.2
  constraints; grep `timing_impl.log` for the cell names.

---

## 9. After this

I6 is small now: `sync_src=1` is already the fabric default, the trim is a
constant, `fabric_ctl` is already behind RE-CONFIGURE. Left: remove frame sync
from the *fabric* path (it stays for software mode) and expose the bypass bits
(already auto-generated). The daily-driver cutover still waits for Parts F+G on
real RF. With 100 % duty available, track filters and a clutter map become
possible (see the `TODO.md` appendix).

---

## Appendix A - the cheap filter, and why one ratio

A CIC costs **zero** DSP slices (adders only), which is why it exists. At a
0.4 FS_IF passband a CIC by 8 is not good enough on its own (3 stages: 7.2 dB
droop at the band edge, 17.6 dB alias rejection), but split the ratio and it
is:

- **CIC by 4, 3 stages**: 1.6 dB droop at the band edge, 36 dB alias
  rejection, 6 bits of growth (22-bit integrators), 0 DSPs.
- **Halfband by 2, 23 taps**, with the CIC's droop baked into its passband:
  12 non-zero taps, 6 unique multiplies, and 8 clocks per output at this
  point in the chain, so **1 DSP per channel** time-multiplexed, 6 if you do
  not bother. About 46 dB alias rejection overall.

Total: 2 to 12 slices for both channels against 16 for the IP, integer model
exact by construction, and the decimation phase can be reset on the leg mark,
which is the one thing the IP cannot do. This is the design to write if slices
run short or if writing the filter is the point. It is more HDL than the
mixer was: two small entities plus a testbench each.

One fixed ratio, not a menu: the envelope is decided (800 m at 56 MHz / 100 us)
and the goal is to clear the wire, not to minimise bytes. Divide by 4 would
also clear it (57 MB/s) and covers 1.7 km at today's chirp. Changing the ratio
later is one regenerated IP and one coefficient file.

One fixed ratio, not a menu: the envelope is decided (800 m at 56 MHz / 100 us)
and the goal is to clear the wire, not to minimise bytes. Divide by 4 would
also clear it (57 MB/s) and covers 1.7 km at today's chirp. Changing the ratio
later is one regenerated IP and one coefficient file.

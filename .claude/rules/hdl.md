---
paths:
  - "firmware/**"
  - "scripts/bringup/**"
  - "src/python/common/fabric_regs.py"
  - "src/python/online/{fabric_ctl,test_fabric_ctl}.py"
  - "vhdl_ls.toml"
---

# HDL and fabric

Four nested forks, all on branch `e200-custom`: `antsdr-fmcw-radar -> firmware/ ->
plutosdr-fw/ -> hdl/` (+ linux/, u-boot, buildroot). Read `docs/firmware-branch-workflow.md`
before touching submodules. Architecture: chirp NCO and dechirp in fabric, Ethernet carries IF
samples, no frame sync; every bypass bit defaults to the software path; `dsp.py` is the golden
model the fabric output is verified against sample-for-sample. Specs: `docs/archive/AntSDR_Phase4_Chirp_NCO_TX_Guide.md` (NCO, dechirp),
`docs/archive/AntSDR_Phase5_IF_Decimation_Guide.md` (decimation).

## Layout and build

- `firmware/plutosdr-fw/hdl/projects/e200/`: `src/` (top `fmcw_core.vhd`: AXI-Lite slave, CDC,
  muxes; `nco/`, `mixer/`, `delay/`, `arithmetic/`, `decimate/`), golden models
  `scripts/*_reference.py`, VUnit in `test/` (`run.py`). `src/nco/dds_init.txt` is generated;
  the `HBF_16_*.txt` taps are vendored from `~/work/projects/fpga/fpga-filters` (see its
  `README.md`).
- **`test/run.py` rglobs** `src/**/*.vhd` and `test/**/tb_*`: new HDL needs no list edit.
  **`Makefile` `M_DEPS` is manual**: list every source *and* init file, or `make` misses the edit.
- AXI-Lite base **0x43C10000**. MAGIC bumps on every register-map change (`RXT1` -> `FMC1` ->
  `FMC3` -> `FMC4` -> `FMC5`); `common/fabric_regs.py` must match `C_MAGIC`. On hardware: **FMC5**.
- **The Vivado IP cache can ship a stale `fmcw_core`.** The ADI scripts keep a shared cache of
  OOC synthesis (on by default) in `hdl/ipcache`, which `make clean` / `clean-all` never touch,
  and the cache key of a BD module reference covers `fmcw_core.vhd` and its generics only, not
  the files below it. So `system_bd.tcl` md5s every `src/**/*.vhd` and `*.txt` into the unused
  generic `G_SRC_HASH` (logged as `fmcw_core G_SRC_HASH = ...`): any edit gets a new key. After a build,
  `e200.runs/system_fmcw_core_0_0_synth_1/system_fmcw_core_0_0.vds` must *not* say
  `Using cached IP synthesis design`. Fallbacks: delete the `fmcw_core` entries
  (`grep -l fmcw_core ipcache/*/*/*/*.xci`, root-owned after `sudo -E make`) or build with
  `ADI_USE_OOC_SYNTHESIS=n`. MAGIC cannot tell such builds apart (the folded FIR kept the map
  and FMC4); the DSP count in the utilization report can (99 vs 123).

## fmcw_core register map (FMC5, hardware-proven)

| Offset | Name | Access | Function |
|---|---|---|---|
| 0x00 | MAGIC | R | `0x464D4335` "FMC5" |
| 0x04 | CTRL | RW | bit0 `ramp_en`, bit1 `nco_en`, bit2 `tx_src` (0=DMA, 1=NCO), bit3 `triangle_en`, bit4 `rx_dbg_mux` (NCO onto RX ch0), bit5 `sync_src` (0=TDD, 1=chirp_start), bit6 `tdm_en` (pre-COMMIT word, like `triangle_en`) |
| 0x08 | SCRATCH | RW | |
| 0x0C | COUNT | R | `valid_in` counter |
| 0x10 / 0x14 / 0x18 | FTW_START / FTW_SLOPE / SWEEP_LEN | RW | shadow, latched at the next chirp boundary after COMMIT |
| 0x1C | CHIRP_COUNT | R | chirp periods, gray-crossed. Delta/s = 10000 sawtooth, **5000 triangle or TDM, 2500 both** (fires per period, not per leg) |
| 0x20 | COMMIT | W | any write arms shadow -> active |
| 0x24 | DECHIRP_DELAY | RW | shadow, same COMMIT; replica delay in samples, **must be < 128** (`G_MAX_DELAY`, wraps silently above) |
| 0x28 | DECIM_SEL | RW | `0` = passthrough, `1` = decimate by 8 (three halfband stages after the ADC output mux, so ramp and IF both go through them). Live, **not** commit-latched (2-flop sync). The guide calls it DECIMATE_SEL |
| 0x2C | IF_SEL | RW | 1 = dechirped IF onto the capture (ADC) path, 0 = raw passthrough. A register, **not** a CTRL bit |
| 0x30 | - | - | unmapped on purpose (tb probe), reads `0xDEADC0DE` |
| 0x34 | STATUS | R | bit0 = `dac_enable_i0`. 0 means `dac_data_sel != DMA` and the NCO output is being discarded at the DAC mux |

## Rules learned on hardware (each cost a hang or a wrong map once)

- **`triangle_en` is NOT quasi-static** despite its own synchronizer: it only takes effect on a
  COMMIT. Write it in the pre-COMMIT CTRL word.
- **`nco_en` with SWEEP_LEN=0 wedges the core ~76 s** (counter never wraps, COMMIT never
  consumed). Always: shadows -> COMMIT -> `nco_en`. Recovery: clear `nco_en`, re-COMMIT.
- **`sync_src=1` while `nco_en=0` hangs every RX capture** (no chirp_start pulses, DMA
  SYNC_TRANSFER_START waits forever). So `nco_en | tx_src | sync_src` land in ONE write, after
  COMMIT. No power cycle needed: write CTRL back to 0.
- **`ramp_en` outranks `if_sel` in the ADC output mux** - never set it in IF mode.
- **IF_SEL goes up LAST and comes down FIRST.** While IF_SEL=1 the ADC stream *is* the mixer
  output, its valid gated by the NCO: IF_SEL=1 with the NCO off means zero ADC valids, and any
  `refill()` in flight starves (ETIMEDOUT).
- **Proven order.** Enable: FTW_START, FTW_SLOPE, SWEEP_LEN, DECHIRP_DELAY -> CTRL
  (`triangle_en | tdm_en` only) -> COMMIT -> CTRL = `nco_en|tx_src|sync_src(|triangle_en|tdm_en)`
  -> DECIM_SEL -> IF_SEL=1 -> read STATUS, require bit0. Disable: IF_SEL=0 -> DECIM_SEL=0 ->
  CTRL=0 -> COMMIT. Recovery from a shell: `devmem 0x43C1002C 32 0; devmem 0x43C10028 32 0;
  devmem 0x43C10004 32 0; devmem 0x43C10020 32 1`.
- DECHIRP_DELAY can be re-committed while the NCO runs (reloads the delay line at the next
  boundary, ~100 us): that is how `dechirp_verify.py --sweep` works without touching CTRL.
- **DECHIRP_DELAY (`FABRIC_DECHIRP_DELAY`) is the TX->RX latency in samples, so it scales with
  FS.** Digital loopback:
  34 at 56.6 MSPS, 20 at 30.72 (both ~0.6 us). Real RF: 61 on cable (the loopback taps inside
  the AD9361 chain); not yet confirmed with antennas (Part G ran in software mode). Measure with
  `dechirp_verify.py --sweep` (leakage peak into range bin 0) at every FS, starting at 0: above
  the true delay the beat goes negative, off the positive range axis, and the sweep just
  reports bin 0. Ignore the first sweep row (a stale block).
- **DMA sync is a level, not a pulse.** `axi_dmac` SYNC_TRANSFER_START clears only on a beat
  *accepted* with sync high in the same cycle, and cpack packs 2 samples per 64-bit beat, so a 1-sample pulse hit
  a beat only by luck of pack phase (re-rolled every `start()` and by the IF_SEL switch).
  `o_dma_sync` is held from the output period tag for 2 output valids (+1 cycle), counted on
  `o_adc_valid`, so it is rate- and IF_SEL-latency-independent. The tag is `w_out_tag` (since
  FMC4): the **pre-output-register** tag, muxed between the decimated and full-rate streams by
  DECIM_SEL, so sync rises with the sample it
  marks in both modes (one register later it opened a whole IF sample late at /8). Safe only
  because `mixer_dechirp` gates its tag with its valid; ungated, the tag rides `delay_line`'s
  held valid level and rises a cycle early. Result: with an even RX buffer the capture starts on
  the tagged sample, trim 0 on every `start()` (measured in 2R2T and on the folded FIR); the
  0-or-1 trim seen in I5 came from the odd buffer (even DMA buffers: `.claude/rules/sdr.md`). `FABRIC_FRAME_TRIM` stays 0. DECHIRP_DELAY
  is pre-DMA, so dechirp alignment is unaffected.
- One sync per `refill()`: the DMA holds off until the first sync-tagged beat, then free-runs
  for the programmed length.
- `mixer_dechirp` computes **IF = delayed_TX * conj(RX)**, the same convention as
  `dsp.mix_signal`, Q15 with saturation; bit-exact vs the Python reference in VUnit. A 1-sample
  artifact at valid-gap edges is pipeline timing, not an RTL bug.

## Fabric IF mode, Python side

- `fabric_regs.register_image(cfg)` is the **only encoder** RadarConfig -> register values: adds
  DECHIRP_DELAY, IF_SEL and DECIM_SEL and sets `tx_src|sync_src` in CTRL when `FABRIC_DECHIRP_EN`;
  `chirp_ftw` recomputes the slope from `SWEEP_LEN`, so B stays exact when the sweep is rounded
  to a multiple of 8; never sets `nco_en` or COMMIT; raises on a delay outside `[0, 128)`.
- `fabric_ctl.FabricCtl` owns **sequencing only**: `enable(image)` / `disable()` follow the
  proven order above, STATUS as a separate round trip (also the settle margin the first
  `refill()` needs); `set_calib_delay()` = DECHIRP_DELAY + COMMIT; `check_magic()`; refuses an
  image with `ramp_en`. Write order unit-tested through a recording transport
  (`online/test_fabric_ctl.py`). `dechirp_verify.py` uses the same module.
- Transport `SshDevmem`: one ssh call per sequence (`devmem` at base+offset, `&&`-chained),
  BatchMode so a missing key fails fast instead of hanging the worker on a password prompt.
  Once: `ssh-copy-id root@<ip>`.

## Decimator (I5): three halfband stages in `fmcw_core`

Fixed /8 as own HDL: three cascaded decimate-by-2 halfbands (7 / 11 / 27 taps, 2 / 3 / 7
multiplies per channel), sized for 800 m at the steepest chirp (56 MHz / 100 us). Each stage
restarts its decimation phase on the chirp-leg tag, so every chirp parameter stays free and a
leg of N inputs yields `N // 8` outputs. 0.03 dB droop, -51.4 dB worst alias.
`OP_RANGE_FACTOR` was replaced by `MAX_RANGE_M` plus a derived effective range.

Rejected, do not re-propose:
- **Xilinx `fir_compiler` IP**: ~16 DSP48s, an encrypted model Questa FSE cannot run, no
  per-chirp phase restart. That also rules out a second `ad_add_decimation_filter` between
  `fmcw_core` and cpack.
- **CIC-by-4 + one halfband**: a CIC is only valid on a uniform decimation grid, so a per-leg
  restart must also clear integrator and comb state (miss it and the output runs past the
  24-bit range, visible only when `SWEEP_LEN mod 4 != 0`); plus 2.2 dB droop.

- **Taps are checked in, never regenerated at build time**: `src/decimate/HBF_16_{0,1,2}.txt`
  (4 / 6 / 14 non-centre taps). Each set **sums to exactly 16384**: the centre tap is a
  hardcoded `>> 1`, so that sum alone gives a stage unity DC gain.
- **An equiripple halfband cannot have unity DC gain**: `A(w) + A(pi - w) = 1` forces
  `A(0) = 1 - A(fs/2)`, so the DC error *is* the stopband ripple, opposite sign. Constrain the
  design (costs 0.22 dB), never patch a finished one (3.3-5.9 dB). The one designer is
  `~/work/projects/fpga/fpga-filters` (`scripts/model/unity_dc_halfband.py`, behind
  `Halfband_filter(a_unity_dc_gain=...)`); this repo only vendors its output.
- **Rounding is two independent floors**: `y = clip16((acc_upper >> 15) + (acc_lower >> 1))`,
  not one floor over a combined accumulator. Model and RTL must both write it this way (the
  likeliest 1-LSB split). So DC comes out one count low (+32767 -> 32766), deterministically,
  in range bin 0 behind the fast-time mean subtraction. Not a bug.
- **Restart resets the decimation phase only** (`r_sel`, one flip-flop); the delay lines keep
  their history (safe: FIR, no feedback). Stage k+1 restarts on stage k's *tagged output*, not
  on the leg tag; a tag lane one stage late shows as "samples per leg drifting".
- **Leg-boundary transient**: the delay lines are never cleared, so ~13 outputs after each
  chirp boundary mix two legs (on a ramp:
  excursions outside its range). `decimate_golden` reproduces it exactly; grade against the
  model, never against "step 8". On a real IF it sits under the range window's edge.
- **Golden model** `scripts/decimate_reference.py` (`load_taps`, `check_taps`,
  `hb_stage_golden`, `decimate_golden`): a restart-tagged input vetoes its own output and the
  tag lands on the next one, the only convention giving `N // 8` per leg for odd N.
- **The ramp is a bit-exact test vector only if built like one**: with `nco_en` the counter
  advances on NCO valids (the strobe the ramp mux emits on; `i_adc_valid` only when the NCO is
  idle), resets on the NCO's own period tag, and is read combinationally so the tagged sample
  reads 0. A registered reset still steps by 8 and passes the ramp test, then reads a
  first-drop index of 1 in the trim test.
- **24 DSPs for I+Q** (folded FIR, `hdl` `feature/folded-fir`; total 99/220 = 27 core + 28
  `axi_ad9361` + 44 ADI FIRs). `halfband_decimate_stage` is a transposed FIR folding *after* the
  multiplier:
  each of the U/2 products feeds two chain adders (`tap`, `high - tap`). A DSP48E1 has no M
  output and one post-adder, so Vivado gave every chain add+register its own slice (P = PCIN +
  M) and duplicated the multiplier: 48 DSPs, U per channel. A DSP48 shares only on the *input*
  side (pre-adder, `(x_a + x_b) * h`). Fix (option A): register the product (`r_prod`) and put
  `use_dsp "no"` on the partial-sum chain (`r_delay_line_upper`): one slice per product, same
  arithmetic, +1 clock per stage. **Drop either and the count doubles.** The 3 extra clocks
  move the DMA sync across one more DAC strobe at one `period-framing` phase (adc 9), which
  `run.py` expects. Not taken: (B) direct form with the pre-adder and a fabric adder tree
  (also 24, rewrites the stage core and the centre-tap alignment); (C) time-share one slice per
  stage (>= 2 / 4 / 8 clocks per output): ~6 DSPs, most control logic, the lever if DSPs run
  short again.

Verification: VUnit 45/45 `tb_fmcw_core` (incl. `decimate-legs` at `chirp_len`
256/257/262/263, `period-framing` x `decimate_sel=1`, `ramp-reset`); `tb_halfband_decimate`, 5
configs (random, square, dc, restart-random, restart-short) at one valid per clock (the
tightest spacing; `tb_fmcw_core` drives 1 in 16), data and tags bit-exact vs `decimate_golden`
(`halfband_decimate_checker`); 7/7 in `decimate_reference.py`. Hardware, digital loopback:
`decim_ramp_check.py` through /8 bit-exact vs `decimate_golden` with and without `--sync`, also
on the folded FIR (needs the even RX buffer);
`dechirp_verify.py --ab` /1 vs /8 matched; RE-CONFIGURE to T = 50 us, B = 25 MHz and triangle
without a hang. STATUS bit1 mirror skipped (DECIM_SEL reads back).

## Stock RX FIR decimator (upstream, bypassed)

`rx_fir_decimator` is ADI's `ad_add_decimation_filter` (Xilinx `fir_compiler`, fixed /8, 128-tap
`coefile_int.coe`), **upstream** of `fmcw_core`. Its `active` pin is `ADI_REG_GP_CONTROL` bit0 of
the ADC core (`axi_ad9361/up_adc_gpio_out` bit 0 via `decim_slice`), written by the
`cf_axi_adc` driver when the **`cf-ad9361-lpc` channel attr `sampling_frequency`** is set: phy
rate -> factor 1, phy rate / 8 -> factor 8 (`decimation_factors_available = {1, 8}`, DT
`adi,axi-decimation-core-available`). `active=0` is a pure `ad_bus_mux` passthrough of data
*and* valid. The driver boots at factor 1, and `sdr.start()` pins the lpc rate to FS anyway:
the NCO advances on `dac_valid_i0` at full rate, so factor 8 would alias the chirp. Without the
DT property the lpc write is **not** rejected: it falls through to `clk_set_rate(RX_SAMPL_CLK)`
on the AD9361. `phy.filter_fir_en` is the AD9361's internal FIR, unrelated. Not reusable for
I5 (wrong side of the mixer). The two unused ADI filter pairs (44 DSPs) can be deleted once the
FFT work needs DSPs (Phase 5 guide 4.2 has the reviewed recipe).

## 2R2T mode (K0)

- **The switch is one dtb property.** `zynq-e200.dts` already sets `adi,2rx-2tx-mode-enable`
  and the ad9361 DDS compatible; only the phy's `compatible = "adi,ad9364"` keeps 1R1T (the
  driver forces 1R1T for that part: `ad9361.c`, `ID_AD9364`). The SD boot partition holds
  `devicetree_1r1t.dtb` / `devicetree_2r2t.dtb`: copy one over `devicetree.dtb`, reboot. To make
  one: `fdtput -t s devicetree.dtb /amba/spi@e0006000/ad9361-phy@0 compatible adi,ad9361`.
  Verify: `/proc/device-tree/amba/spi@e0006000/ad9361-phy@0/compatible`, dmesg
  `probe : enter (ad9361)`, `cf-ad9361-lpc` with `voltage0..3`. The firmware README's
  `uEnv.txt` route (u-boot `adi_loadvals`) does the same; `mode=1r1t` there strips the 2R2T
  property again at boot.
- **FS <= 30.72 MSPS in 2R2T.** The E200 runs the AD9361 interface in CMOS (HDL
  `CMOS_OR_LVDS_N 1`, LVCMOS18), where DATA_CLK = 2 x FS in 2R2T and must stay <= 61.44 MHz.
  56.6 MSPS returns EINVAL ("Failed CMOS MODE DATA_CLK > 61.44MSPS"). LVDS is board wiring, not
  a firmware switch. So 2R means a ~25 MHz chirp and ~6 m range bins. The phy boots at 30.72
  MSPS, so the 2R2T probe itself is fine. TX `rf_bandwidth` max is 40 MHz.
- **The fabric is transparent to it** (HDL `MODE_1R1T 0`; `fmcw_core` channels 2/3 are
  passthrough; 2T1R never touches them). At FS 30.72 / BW 25 MHz / 100 us:
  `decim_ramp_check.py` bit-exact (trim 0), `dechirp_verify.py --ab` PASS.
- The bringup scripts build their config from `RadarConfig()` defaults (FS 56.6), so they need
  an FS/BW override to run in 2R2T.
- RX2 data path proven (4-lane capture, noise follows RX2 gain, uncorrelated with RX1). RX2 on
  a real signal is still open (not needed for the 2T1R plan).
- **RX2 and TX2 are IPEX (U.FL) connectors**, not SMA (MicroPhase docs: "SMA:1T1R IPEX:1T1R").

## E200 GPIO header J30 (schematic `docs/datasheets/ANT-E200_Public.pdf` p.10)

10-pin **2.0 mm pitch** footprint, **not fitted** on this board: pin 1 VCC_3V3, pin 2 GND, pins
3-10 GPIO_00-07 (ESD diodes). HDL `GPIOB[7:0]` (bank 13, LVCMOS33, XDC order V5, U7, V7, T9,
U10, Y7, Y6, Y9), routed to the PS through `ad_iobuf` as EMIO 35-42 = Linux sysfs gpio
995-1002. Upstream MicroPhase constraints reuse Y9/Y6 as a UART; this project maps all eight to
`GPIOB`. Pin map measured (multimeter, sysfs): all eight match the schematic. Since FMC5, pin 3
(`GPIOB[0]`) is `tx_sel` from `fmcw_core` (0 = A, 1 = B; gpio 995 reads it back). The switch
module draws ~60 mA: power it from pin 1, not a GPIO pin.

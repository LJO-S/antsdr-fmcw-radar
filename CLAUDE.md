# ANTSDR FMCW Radar

FMCW radar at **5.8 GHz** on an ANTSDR E200 (Zynq + AD9361). Two tracks run in parallel:

1. **Python radar** — a reusable `common` DSP/GUI core, an `offline` simulation, and an
   `online` (live SDR) path. Working end to end.
2. **PL fabric offload** — custom HDL in the forked firmware stack moving chirp generation
   and dechirp into the FPGA. Done in digital loopback (`TODO.md` Part I); next is running
   it on real RF, then making it the default.

**Terminology:** *offline* = simulation / soft model. *online* = real-time hardware.

**`TODO.md` is the roadmap and the authority on status** — read it before planning work.
It tracks **unfinished work only**: closed parts are one line each and their
design conclusions live *here* instead. When a part closes, summarize it there and move
anything worth keeping into this file — don't let narrative grow back in `TODO.md`.
Short version: Parts A–G done (offline model, online app, GUI, target sim, DSP
hardening, cable loopback, antenna field test), with a handful of small open items still listed.
**Part I (HDL offload) is done** in digital loopback, only I5b (the ADI FIR reclaim) deferred:
fabric NCO + fabric dechirp (I4), the app drives the fabric through
`online/fabric_ctl.py` (I4b), fake targets work on the IF stream (I4c), and **I5, decimation
to IF rate** (MAGIC FMC4; spec
`docs/archive/AntSDR_Phase5_IF_Decimation_Guide.md`). I6 closed with it (the fabric path
needs no frame sync). I5 design and rejected routes: `.claude/rules/hdl.md`.
Part G (antennas, first radiated RF) passed in software mode. **Next: Part K**
(`docs/AntSDR_Phase6_Tracking_Antennas_Angle_Guide.md`): software tracking, an RF bench lane
(VNA, soldering), own patch antennas, **2T1R angle through a fabric-switched TX** (time-division
MIMO), and fabric mode on real RF on the first field day. Fabric FFT/CFAR sits behind a decision
gate (guide Section 11). Part H (PA/LNA/BPF) deferred. Part J (synthetic wideband) blocks
nothing. K0 (2R2T boot check) passed, but **2R2T caps FS at 30.72 MSPS** on this
board (~6 m bins, see "2R2T mode" below), which is why angle moved from 2R1T to 2T1R. The user's
goal is to learn RF engineering, so Part K favours hands-on RF work over more FPGA work.

## Project structure

```
src/python/                 # import root
  common/
    config.py    # RadarConfig dataclass — ALL parameters (source of truth)
    fabric_regs.py # fmcw_core register map (offsets, CTRL bits, MAGIC) + register_image(cfg):
                 #   the ONLY encoder of RadarConfig -> register values (chirp_ftw, _u32)
    dsp.py       # pure DSP: generate_chirp(_sequence), estimate_chirp_offset,
                 #   frame_sync_linear/circ, mix_signal, cfar_ca_2d (+ box_sum), nms, subbin_refine,
                 #   dilate_doppler, wrap_velocity,
                 #   align_down_doppler, apply_doppler_shift, apply_noise, inst_freq,
                 #   spectrogram, CPIContext, build_cpi_context, process_cpi,
                 #   iq_from_raw (int16 block -> complex64; live and replay both use it)
    gui.py       # RadarDisplay (PySide6 + pyqtgraph), 4 tabs; set_playback_mode() +
                 #   PlayerBar (the playback toolbar)
  offline/
    soft_model.py        # SoftFMCWModel — target/noise/impairment sim, __main__ entry
    test_soft_model.py   # numeric detector self-test
    profile_cpi.py       # K1: median ms per process_cpi stage + apply_if vs fake-target
                         #   count (staged mirror of process_cpi, asserted equal to it)
    replay.py            # K2: recorded session -> prepare_block -> process_rx_data;
                         #   --check compares with detections.jsonl, exact. No libiio
                         #   needed (never imports online.sdr). Player = random access
                         #   to any block; replay() walks it in order
    playback.py          # K2 visual playback: PlaybackWorker (QThread, owns a Player)
                         #   + RadarDisplay in playback mode + PlayerBar
  online/
    sdr.py         # AntSDR: connect/find_device in __init__; attrs+buffers in start();
                   #   read_raw_block() (int16), read_block() = dsp.iq_from_raw of it
    capture.py     # capture_rx_data(): read_raw_block -> Recorder.write_block ->
                   #   prepare_block(): TargetSim.apply -> frame_sync_linear
                   #   (fabric mode: FABRIC_FRAME_TRIM slice -> TargetSim.apply_if, no sync)
    recorder.py    # K2 Recorder: session dir = session.json, blocks.bin, index.csv,
                   #   detections.jsonl (Phase 6 guide Section 3)
    target_sim.py  # FakeTarget / TargetSim — moving fake targets: apply() on raw RX (roll delay),
                   #   apply_if() on the fabric IF stream (beat-tone synthesis)
    processing.py  # process_rx_data(): stateless mix_signal + process_cpi wrapper
                   #   (skips mix_signal when FABRIC_DECHIRP_EN); spectrograms() for
                   #   the Signals tab (live and playback)
    app.py         # RadarWorker (QThread) + main(); owns (config, ctx, sdr)
    fabric_ctl.py  # FabricCtl (enable/disable/set_calib_delay sequencing) + SshDevmem transport
    test_fabric_ctl.py, test_target_sim.py, test_recorder.py  # board-free, run as __main__
docs/              # AntSDR_Phase6_Tracking_Antennas_Angle_Guide.md (Part K spec, active),
                   #   tracking_algorithms.md (K3/K11 research + reading),
                   #   firmware-branch-workflow.md,
                   #   rf-power-and-spectrum-sweden.pdf, fmcw_fabric_architecture.svg,
                   #   datasheets/, archive/ (Phase 1-5
                   #   guides;
hardware/          # materials.md (phased buy list), bench_measurements.md (K4 predict /
                   #   measure / explain tables), fmcw_58ghz_block_diagram.svg
firmware/          # submodule stack (see below), branch e200-custom
scripts/bringup/   # hardware bringup references (reference.py, trx_loopback.py, pluto_sdr_ref.py)
                   #   + dechirp_verify.py (sw vs fabric RD map at --decimate 1|8; --ab fabric
                   #   /1 vs /8 with fake targets; --rate S headless CPIs/s; --sweep delay)
                   #   + decim_ramp_check.py (ramp through the decimator, bit-exact vs
                   #   decimate_golden; --sync measures the frame trim)
scripts/antenna_design/  # K8 openEMS patch sim + eps_r sweep + KiCad (patch, line-pair); own README
                   #   (runs in the openEMS venv, not .venv)
data/info.txt      # iio_info dump of the board
vhdl_ls.toml       # rust_hdl LSP config, points at firmware/.../projects/e200
pypkgs.txt         # numpy, matplotlib, pylibiio, scipy, PySide6, pyqtgraph, vunit_hdl
```

`src/python/README.md` is a short map of the package; this file stays
the authority on conventions. `src/python/run_tests.py` runs every `*/test_*.py` suite as
`python -m`, one process each, failing on a non-zero exit.

## Running

`src/python/` is the import root. Activate the venv first: `source .venv/bin/activate`.

```bash
cd src/python
python -m offline.soft_model        # run simulation + GUI
python -m common.gui                # GUI with pseudo-data only
python -m online.app                # live SDR app (needs AntSDR at SDR_IP)
python -m online.sdr                # standalone loopback test with matplotlib debug plots
python -m offline.playback [dir]    # recorded session in the GUI (no board); Open... picks one
python -m offline.replay <dir> [--check]  # headless replay; --check vs the live detections
python run_tests.py [-v] [name]     # all board-free test suites (name = substring filter)
```

Antenna simulations run in openEMS's own venv (`~/opt/openEMS/venv`, built from source),
not `.venv`: see `scripts/antenna_design/README.md`.

## Key classes / functions

- **`RadarConfig`** (`common/config.py`) — dataclass holding every parameter. **Authoritative
  source for parameter values**; this file's table below is a convenience copy, keep it in sync.
  Fields carry `metadata` (`label`/`unit`/`scale`/`group`, optional `readonly`) that the GUI
  config tab auto-generates its form from — adding a tagged field adds a GUI row for free.
  `MAX_RANGE` / `MAX_VELOCITY` are `@property` (recomputed per access; `MAX_VELOCITY` is
  triangle-aware via `T_rep`), and so are the I5 rate properties `FS_IF`, `SWEEP_LEN`
  (rounded down to a multiple of 8 when decimating), `T_EFF = SWEEP_LEN / FS`, `N_IF`,
  `EFFECTIVE_RANGE`, with `MAX_RANGE = min(MAX_RANGE_M, EFFECTIVE_RANGE)`. Range and velocity
  axes (incl. `MAX_VELOCITY`) use `T_EFF`, the real chirp period, never
  `CHIRP_DUR_S` — do NOT turn them back into class attributes; RE-CONFIGURE
  builds fresh instances. The parameter banner is `describe()`, called by the entry points,
  never at import. Configs are **immutable by convention**: never mutate a live instance,
  construct a new one and swap the reference. (The one deliberate exception is
  `config.MTI_EN`, written per-CPI by the worker so the live checkbox survives config swaps.)

## Parameters (`common/config.py` — source of truth)

| Parameter | Value |
|-----------|-------|
| fc (CHIRP_FC_HZ) | **5.8 GHz** |
| BW (CHIRP_BW_HZ) | 50 MHz |
| T (CHIRP_DUR_S) | 100 µs |
| chirp_reps (CHIRP_REPS) | **128** |
| TRIANGLE_EN / MTI_EN | False / False |
| fs (FS) | 56.6 MHz (AD9361 caps: FS 2.083–61.44 MSPS, rf_bandwidth 0.2–56 MHz; **30.72 MSPS max in 2R2T**) |
| CFAR guard / training / pfa / mask_N | 4 / 5 / 1e-6 / 5 |
| MAX_RANGE_M | 800 m → MAX_RANGE 800 m (effective range 848 m at /8, 50 MHz / 100 µs) |
| Derived | range res 3.0 m, velocity res 2.02 m/s, MAX_VELOCITY 129 m/s, proc gain 58.6 dB |
| Soft-model only | TX_PWR_DBM 10, TX_GAIN_DB 13, RX_GAIN_DB 14, ISOLATION_DB 40, DC/IQ errors 0 |
| SDR_IP | 192.168.5.10 |
| SDR_TX_GAIN_DB / SDR_TX_GAIN_MAX_DB | −60.0 / **0.0** (AD9361 hardwaregain = attenuation) |
| SDR_RX_GAIN_MODE / SDR_RX_GAIN_DB | manual / 40.0 |
| SDR_RX_MARGIN_PERIODS | 1 (software frame sync needs >= 1; `start()` refuses 0 there) |
| SDR_LOOPBACK_EN / SDR_LOOPBACK_NOISE_SNR_DB | True / 1.0 |
| FABRIC_DECHIRP_EN / FABRIC_DECHIRP_DELAY | False / **61** (pure path delay, non-loopback). **34** is the digital-loopback constant, kept commented out in `config.py` - swap when `SDR_LOOPBACK_EN` flips. Both are at 56.6 MSPS: the delay is in samples and scales with FS (loopback **20** at 30.72 MSPS) |
| FABRIC_DECIM_EN / FABRIC_FRAME_TRIM | True / 0 (only active with `FABRIC_DECHIRP_EN`; ratio fixed at `FABRIC_DECIMATE_RATIO` = 8 → FS_IF 7.075 MSPS, SWEEP_LEN 5656, N_IF 707) |

Noise figure is a `run_simulation` argument (`a_noise_figure_db`), not a config field.

## Signal processing conventions — do not change these

### IF mixing
`IF = TX * conj(RX)` (`mix_signal`) — positive beat frequency for positive range. Swapping to
`conj(TX) * RX` flips range beats negative and kills all detections through the `pos` mask.

### Doppler sign
`apply_doppler_shift` uses `exp(-j*2π*f_d*t)` (minus sign). A plus sign gives measured
velocity = −v_true. The minus matches the stop-and-hop model where slow-time frequency =
+f_d for an approaching target.

## Firmware stack

- A parent repo records only a gitlink SHA. Commit **inside** the repo where files live, then
  bump pointers **bottom-up**. Feature branches (`feature/chirp-nco`, `feature/fabric-dechirp`,
  ...) exist in `hdl` only.
- **Do not run `resetGit.sh`** — it strips the MicroPhase patches and the fork checkpoints.
- Cutover to the fabric path waited for Parts F+G on real RF; both passed in software mode,
  so it is unblocked. The rule stands: never debug new RF and new HDL at the same time.

## RF safety

Applies the moment anything is radiated. Cable/attenuator work (Part F) is unregulated.

- **RF input absolute maximum is +2.5 dBm peak.** With `SDR_TX_GAIN_MAX_DB = 0.0` a mistyped
  gain puts ~+6.5 dBm straight at the RX port, over the absolute max, so **the pads are now the
  only protection**: keep >=30 dB of attenuation in any TX->RX cable path, and check the pad is
  actually in line before raising `SDR_TX_GAIN_DB`.
- TX gain is **clamped** in `start()` to `SDR_TX_GAIN_MAX_DB` (warns, does not raise). The
  clamp is **0.0 dB**, i.e. full TX power (~+6.5 dBm at 5.8 GHz) is reachable:
  with physical attenuators on the bench the software ceiling was only getting in the way.
  The consequence is that **the EIRP guard is now the operator and the pads, not the code** -
  read the regulatory section before radiating, and set `SDR_TX_GAIN_DB` by hand.
- SRD route, with the **16 dBi TX sector** (Ubiquiti AM-5G16-120, the one used in Part G):
  EIRP = port - losses + 16 dBi <= 14 dBm. Ignoring losses, the port must be **<= -2 dBm**:
  `SDR_TX_GAIN_DB` ~ **-9 dB** (full power is ~+6.5 dBm). Counting the Part G TX chain (2 m
  HF240 1.9 dB measured in K4 + VBFZ filter ~1.4 dB; one unit measured 1.8, so 1.4 errs
  safe), the port may reach +1.3 dBm: **-5.2 dB**
  (Part G planned -5.7 from the 1.4 dB datasheet cable figure; start at -15 dB and walk up). The clamp is 0.0 dB and enforces nothing, so set the
  gain by hand. If an unattended/long radiated run is ever left running, put the ceiling
  into `SDR_TX_GAIN_MAX_DB` for that session rather than trusting the field.
- **Legal fc window** for a 50 MHz chirp: 5.750-5.850 GHz (SRD) or 5.675-5.825 GHz
  (amateur). That is the room for trimming fc to a PCB batch's eps_r (Phase 6 guide 9.4).
  Never 5.6 GHz: outside both, and it is C-band weather radar.
- 880–960 MHz (the old 900 MHz carrier) is cellular uplink/downlink — never radiate there.

## Rules
Be short and concise. To the point. No extra jargong and over-explaining unless asked for.

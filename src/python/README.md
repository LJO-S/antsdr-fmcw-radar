# Python radar

The host side of the 5.8 GHz FMCW radar on the ANTSDR E200: DSP, GUI, the live SDR
path and an offline simulation. This file is a map. The conventions (sign rules,
detection pipeline, worker invariants, register map) live in the repo's `CLAUDE.md`,
and the roadmap in `TODO.md`.

## Setup

`src/python/` is the import root; run everything from here.

```bash
source ../../.venv/bin/activate      # packages: ../../pypkgs.txt
```

## Run

```bash
python -m online.app                 # live radar (needs the E200 at SDR_IP)
python -m offline.playback [dir]     # a recorded session in the GUI, no board
python -m offline.replay <dir> [--check]   # headless replay; --check vs the live detections
python -m offline.soft_model         # simulated radar + GUI
python -m common.gui                 # the GUI with pseudo-data
python -m online.sdr                 # loopback test with matplotlib plots
python run_tests.py [-v] [name]      # every board-free test suite
```

Recordings go to `data/sessions/<timestamp>/` (Record checkbox in the Radar tab).

## Layout

```
common/    shared by everything
  config.py       RadarConfig: every parameter; the GUI config form is generated from it
  dsp.py          pure DSP: chirps, frame sync, mixing, process_cpi (2D FFT, CFAR, NMS, ...)
  fabric_regs.py  fmcw_core register map and the RadarConfig -> register encoder
  gui.py          RadarDisplay (Radar, History, Signals, Configuration) + PlayerBar
offline/   no board needed
  soft_model.py   SoftFMCWModel: targets, noise and impairments in simulation
  replay.py       Player: random access to a recorded session; replay() and --check
  playback.py     the GUI on top of Player
  profile_cpi.py  ms per process_cpi stage (K1)
online/    the live radar
  sdr.py          AntSDR: libiio setup, TX waveform, RX blocks
  fabric_ctl.py   fabric enable/disable sequencing over ssh + devmem
  capture.py      one block: record it, then prepare_block (trim or frame sync, fake targets)
  target_sim.py   moving fake targets, on raw RX or on the fabric IF stream
  processing.py   mix (software mode) + process_cpi; the Signals-tab spectrograms
  recorder.py     session writer: session.json, blocks.bin, index.csv, detections.jsonl
  app.py          RadarWorker (QThread) + main()
*/test_*.py  board-free test suites, run by run_tests.py
```

## One CPI, live

```
sdr.read_raw_block()                 int16 block from the DMA
  -> Recorder.write_block()          if recording
  -> capture.prepare_block()         fake targets; plus trim (fabric) or frame sync (software)
  -> processing.process_rx_data()    mix (software mode) + process_cpi
  -> RadarDisplay                    via Qt signals, on the GUI thread
```

Playback runs the same `prepare_block` and `process_rx_data` on recorded blocks,
which is why a noise-free session replays to identical detections.

## GUI inspiration

`image.png` and `image-1.png` are screenshots of other radar GUIs, kept as reference.

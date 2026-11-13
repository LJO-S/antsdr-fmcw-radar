---
paths:
  - "src/python/online/{app,capture,recorder}.py"
  - "src/python/common/gui.py"
  - "src/python/tests/test_recorder.py"
  - "src/python/offline/{replay,playback}.py"
---

# Online app, GUI, recording, playback

## Online app architecture (`online/app.py`) — keep these invariants

- **`RadarWorker(QThread)` owns the `(config, ctx, sdr)` triple**; they are only ever
  touched from `run()` (the worker thread), including `AntSDR` construction. GUI thread
  never touches sdr/ctx, worker never touches widgets.
- Worker → GUI: Qt signals only (`results`, `error`, `reconfigure_done`, `signals` —
  payload `object` for numpy arrays). Cross-thread emits are auto-queued to the GUI thread.
- GUI → worker: `reconfigure_requested` → `pending_cfg` (a fresh `RadarConfig`), swapped in
  between CPIs (new ctx, `radio.config = new`, `close()`/`start()`), with **last-good revert**
  on exception and `reconfigure_done(ok, cfg)` back to the GUI. `fake_targets_changed` ->
  `pending_targets` (the worker calls `TargetSim.set_targets` between CPIs, so a block never
  mixes two target sets) and `mti_signal_changed` -> `_mti_en` need **no radio restart**.
  No locks: single-reference / bool assignment is atomic under CPython.
- **Recording (K2) is worker-owned too** (session format: Phase 6 guide Section 3): `Recorder`
  is built in `run()`; `record_changed(on)`
  and `record_notes_changed(text)` (every edit) only set fields, and `rec_sync()`
  opens/closes sessions between CPIs. One session = one config and one fake-target set, so
  RE-CONFIGURE (incl. the revert) and a target edit start a new session. The detections line
  stores the `MTI_EN` the CPI ran with. `recording(dir | None)` mirrors the state to the GUI
  checkbox, including `None` from `finally` when the worker dies.
  **A disk error ends the session, never the radar**: `write_block` / `write_detections` /
  `close` never raise, they close the session and park the error in `Recorder.failure`;
  `rec_sync` takes it before the next CPI, reports it once and clears `_record_en`, so a full
  disk does not reopen a session every CPI. `open()` raises instead and removes its half-made
  directory (`session.json` is written last). Replay is exact only without random input:
  loopback noise off is `SDR_LOOPBACK_NOISE_SNR_DB = 0` (`apply_noise` returns early at
  SNR <= 0). It survives a crash (a torn last line in `index.csv` / `detections.jsonl` is
  skipped, an index line past the end of `blocks.bin` stops it) and config drift (removed
  fields dropped, new ones defaulted, both printed). Exact equality holds on the recording
  machine; another CPU can differ in the last bit (numpy SIMD dispatch).
- **Playback** (`offline/playback.py`) mirrors this: `PlaybackWorker` owns a `replay.Player`
  and emits the same `results` (plus the recorded CPI time, for the History tab) and
  `signals`, plus `recorded` (the live detections, drawn as magenta rings, toggleable),
  `position` and `playing`. GUI -> worker goes through a
  `queue.SimpleQueue` (open / play / step / seek / MTI), so no seek is lost or reordered;
  the speed is a plain float. Pacing follows the recorded CPI times (0.25x-4x, or max) and
  never skips a block to catch up (~24 CPIs/s at /8 is the processing bound). Seeking
  backwards is cheap: `Player.render` restores the fake-target snapshot and runs
  `TargetSim.advance()` (kinematics only) up to the block. MTI follows the recording or is
  forced on/off from the bar. The display's config form and fake-target table are
  read-only there (`set_playback_mode`). `offline/replay.py` never imports `online.sdr` (no
  libiio needed); `Player` gives random access to any block, `replay()` walks them in order.
- **Tracks (L3)**: `RadarWorker` and `Player` each own a `Tracker` (only with `TRACK_EN`) and emit
  `tracks` (`Track.snapshot()` dicts, never live `Track`s) after `process_rx_data`, fed the
  capture time the recorder writes. The worker rebuilds it on RE-CONFIGURE (incl. revert);
  `Player` resets it on any jump except a step forward. `python -m offline.replay`
  prints `{block, t, tracks}` lines (NIS included; null without `TRACK_EN` in the session).
  `--track` forces the tracker on, `--set TRACK_X=V` overrides a tracking field (L6 tuning).
  Only `TRACK_*` fields: they cannot change the detections, so replay stays exact.
  Warnings go to stderr, so stdout stays pure JSON.
- Spectrogram emits are **throttled to ~2 Hz** (`_last_spec_update`) and cover only 2 chirps.
- Cleanup: `radio = None` before `try`, `finally: if radio is not None: set_loopback(False);
  close()`. `stop()` (GUI thread) flips `_running` then `wait(5000)`s — never `terminate()`.
- **Fabric bring-up is worker-owned too**: `fabric = FabricCtl(SshDevmem(SDR_IP))` is built in
  `run()` next to `radio`; two closures `fabric_up(cfg)` / `fabric_down()` wrap it. Order at
  every `start()`: `radio.start()` -> `fabric_up` (check_magic, enable, 3 throwaway
  `read_block()`s because the IF_SEL switch re-rolls the cpack pack phase). Before every
  `radio.close()` (normal, reconfigure, last-good revert): `fabric_down()`, best-effort in
  `finally`. **`enable()` can raise after its `write_seq` already ran** (the STATUS check), so
  `fabric_up` must catch, `disable()`, then re-raise - otherwise `fabric_on` stays False on a
  live fabric. No ssh inside the capture loop: enable costs ~2 round trips, only at
  start/reconfigure.

## GUI

- **`RadarDisplay`** (`common/gui.py`) — Tab 0 "Radar" (up/down RD maps + detections scatter +
  MTI checkbox; with `TDM_EN` also a Sector-PPI (x-y), `x = r sin(theta)` computed there,
  and an azimuth column), Tab 1 "History" (last 10-120 s: range vs time and velocity vs time of the
  up-chirp map, detections as dots), Tab 2 "Signals" (RX/IF spectrograms), Tab 3
  "Configuration" (auto-generated config form, TX instantaneous-frequency plot,
  derived-characteristics table, fake-target editor, RE-CONFIGURE). `QApplication` is owned
  by the caller, not the widget.
  History: `update(..., a_t=None)` takes the CPI time (None = now, the live app; playback
  passes the recorded one). Image columns are fixed 0.1 s bins (max-hold), so a varying
  CPI rate cannot stretch the time axis; images redraw at <= 5 Hz. It restarts on
  `set_config` and when time runs backwards (a playback seek). The window sets the x range
  explicitly: pyqtgraph auto-range does not follow the image.
  Two pyqtgraph/Qt gotchas that cost real time: `setClipToView` + auto-downsampling silently
  hides curves whose data is set **before** the first show; and `QDoubleValidator` does *not*
  hard-reject out-of-range input, so `read_cfg_reg` is the actual gate on config values.

## Fabric IF mode in the app

- `capture.prepare_block` takes the block with no frame sync (`sync_src=1` makes every
  DMA transfer start on a chirp boundary), slices `rx[TRIM : TRIM + rows * N_IF]`
  (`FABRIC_FRAME_TRIM`) and runs `TargetSim.apply_if` on it; `process_rx_data` skips
  `mix_signal`. `sdr.start()` sizes the RX buffer from `N_IF` (rounded up to an even sample
  count, see `.claude/rules/sdr.md`) and warns if the phy's
  `sampling_frequency` reads back different from FS.
- Signals tab in fabric mode: the RX spectrogram *is* the IF, so the worker emits `if_spec =
  None` and the GUI hides the IF plot and relabels the remaining one. The Configuration tab's
  TX instantaneous-frequency plot stays: it is derived from the config, and the NCO sweeps the
  same BW/T/FS.

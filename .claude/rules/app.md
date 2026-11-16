---
paths:
  - "src/python/online/{app,capture,recorder}.py"
  - "src/python/common/{gui,gui_tracks}.py"
  - "src/python/tests/{test_recorder,test_gui}.py"
  - "src/python/offline/{replay,playback,profile_gui}.py"
---

# Online app, GUI, recording, playback

## Online app architecture (`online/app.py`) — keep these invariants

- **`RadarWorker(QThread)` owns `(config, ctx, sdr)` triple**; only touched from `run()` (worker
  thread), incl. `AntSDR` construction. GUI thread never touches sdr/ctx, worker never touches
  widgets.
- Worker -> GUI: Qt signals only (`results`, `error`, `reconfigure_done`, `signals` -
  payload `object` for numpy arrays). Cross-thread emits auto-queued to GUI thread.
- GUI -> worker: `reconfigure_requested` -> `pending_cfg` (fresh `RadarConfig`), swapped in
  between CPIs (new ctx, `radio.config = new`, `close()`/`start()`), with **last-good revert**
  on exception and `reconfigure_done(ok, cfg)` back to GUI. `fake_targets_changed` ->
  `pending_targets` (worker calls `TargetSim.set_targets` between CPIs, so a block never
  mixes two target sets) and `mti_signal_changed` -> `_mti_en` need **no radio restart**.
  No locks: single-reference / bool assignment is atomic under CPython.
- **Recording (K2) is worker-owned too** (session format: Phase 6 guide Section 3): `Recorder`
  built in `run()`; `record_changed(on)`
  and `record_notes_changed(text)` (every edit) only set fields, `rec_sync()`
  opens/closes sessions between CPIs. One session = one config and one fake-target set, so
  RE-CONFIGURE (incl. revert) and a target edit start a new session. Detections line
  stores the `MTI_EN` the CPI ran with. `recording(dir | None)` mirrors state to GUI
  checkbox, incl. `None` from `finally` when worker dies.
  **A disk error ends the session, never the radar**: `write_block` / `write_detections` /
  `close` never raise, they close the session and park the error in `Recorder.failure`;
  `rec_sync` takes it before next CPI, reports once and clears `_record_en`, so a full
  disk does not reopen a session every CPI. `open()` raises instead and removes its half-made
  directory (`session.json` written last). Replay exact only without random input:
  loopback noise off is `SDR_LOOPBACK_NOISE_SNR_DB = 0` (`apply_noise` returns early at
  SNR <= 0). Survives a crash (torn last line in `index.csv` / `detections.jsonl`
  skipped, index line past end of `blocks.bin` stops it) and config drift (removed
  fields dropped, new ones defaulted, both printed). Exact equality holds on the recording
  machine; another CPU can differ in last bit (numpy SIMD dispatch).
- **Playback** (`offline/playback.py`) mirrors this: `PlaybackWorker` owns a `replay.Player`
  and emits same `results` (plus recorded CPI time, for History tab) and
  `signals`, plus `recorded` (live detections, drawn as magenta rings, toggleable),
  `position` and `playing`. GUI -> worker goes through a
  `queue.SimpleQueue` (open / play / step / seek / MTI), so no seek lost or reordered;
  speed is a plain float. Pacing follows recorded CPI times (0.25x-4x, or max) and
  never skips a block to catch up (~24 CPIs/s at /8 is the processing bound). Seeking
  backwards is cheap: `Player.render` restores the fake-target snapshot and runs
  `TargetSim.advance()` (kinematics only) up to the block. MTI follows the recording or is
  forced on/off from the bar. Display's config form and fake-target table are
  read-only there (`set_playback_mode`). `offline/replay.py` never imports `online.sdr` (no
  libiio needed); `Player` gives random access to any block, `replay()` walks them in order.
- **Tracks (L3, L4)**: `RadarWorker` and `Player` each own a `Tracker` (only with `TRACK_EN`,
  default True), updated after `process_rx_data` with capture time the recorder writes,
  and emit `tracks = Signal(object, object)`: `(Track.snapshot() dicts, capture time)`
  (playback: recorded CPI time), never live `Track`s. `([], None)` = cleared: RE-CONFIGURE
  (incl. revert), session without `TRACK_EN`, or tracker exception (ends tracking, never
  the radar). Connected to `RadarDisplay.update_tracks`; order against `results` does not
  matter. Worker rebuilds tracker on RE-CONFIGURE (incl. revert);
  `Player` resets it on any jump except a step forward. `python -m offline.replay`
  prints `{block, t, tracks}` lines (NIS included; null without `TRACK_EN` in session).
  `--track` forces tracker on, `--set TRACK_X=V` overrides a tracking field (L6 tuning).
  Only `TRACK_*` fields: they cannot change detections, so replay stays exact.
  Warnings go to stderr, so stdout stays pure JSON.
- Spectrogram emits **throttled to ~2 Hz** (`_last_spec_update`) and cover only 2 chirps.
- Cleanup: `radio = None` before `try`, `finally: if radio is not None: set_loopback(False);
  close()`. `stop()` (GUI thread) flips `_running` then `wait(5000)`s - never `terminate()`.
- **Fabric bring-up is worker-owned too**: `fabric = FabricCtl(SshDevmem(SDR_IP))` built in
  `run()` next to `radio`; two closures `fabric_up(cfg)` / `fabric_down()` wrap it. Order at
  every `start()`: `radio.start()` -> `fabric_up` (check_magic, enable, `RX_KERNEL_BUFFERS - 1`
  throwaway `read_block()`s: blocks queued before IF_SEL switch, `.claude/rules/sdr.md`;
  was 3 at libiio default 4). Before every
  `radio.close()` (normal, reconfigure, last-good revert): `fabric_down()`, best-effort in
  `finally`. **`enable()` can raise after its `write_seq` already ran** (STATUS check), so
  `fabric_up` must catch, `disable()`, then re-raise - otherwise `fabric_on` stays False on a
  live fabric. No ssh inside capture loop: enable costs ~2 round trips, only at
  start/reconfigure.

## GUI (`common/gui.py`, `common/gui_tracks.py`)

- **`RadarDisplay`**: tabs Tracks / Signal / History / Config. `QApplication` owned by
  caller, not widget. `update(a_rd_up_db, a_rd_down_db, a_ranges, a_velocities,
  a_targets, a_t=None)` keeps its signature: `app.py`, `playback.py`, `soft_model.py` call it.
- **Intake / draw split**: `update`, `update_signals`, `update_recorded`, `update_tracks` only
  take data in (every CPI) and mark display dirty. One `QTimer` draws current tab
  when dirty, tab change draws at once, `draw_now()` draws once without timer (tests,
  profiler). Reason: GUI shares GIL with worker; drawing every CPI took ~half the
  time at 20 detections. Frame held by reference until drawn, so callers pass fresh
  arrays per CPI (`process_cpi` does).
- **Adaptive draw interval**: clamp(4 x smoothed draw cost, 33, 250) ms (cost = tick to after
  Qt's paint, via zero timer behind paint events; EMA alpha 0.3). Heavy scene drawn
  less often (never under 4/s) instead of slowing radar. A tick that draws nothing adds no cost.
- **Tracks tab**: with TDM Sector-PPI left, (r, v) plot over track list right;
  without TDM (r, v) plot left, list alone right, no az column. `_layout_tracks` moves the
  same widgets on `set_config`, never rebuilds. (r, v) plot lies over up-chirp RD
  map, dimmed in lookup table (item opacity would blend on every paint), on a
  pixel-centred rect (`_image_rect`) so a dot sits on its blob. Until L5 gives `x`, `y`,
  PPI puts a track at (filtered r, `theta` of its last detection). Paired detections (all in
  sawtooth) fade over ~1 s as dots; up-only/down-only ones (triangle, Up/Down ticked) are
  crosses of newest CPI only. Playback adds magenta rings for recorded detections,
  shows MTI disabled and hides Record + notes.
- **`gui_tracks.py`**:
  - `TrackStore` (no Qt): a track is new when its `id` is unseen, its `age` dropped or its
    `birth_t` changed (tracker rebuilt: IDs restart at 0). Empty list or time running
    backwards clears store. Direction class has hysteresis (in at 0.5 m/s, out at 0.3);
    a display-wrapped v breaks the tail.
  - `TrackLayer` (one per plot) built once; `draw()` only moves items.
  - `TrackTableModel`: same ids -> one `dataChanged`, else model reset (display
    restores selected row by `id`).
- **Selection**: one confirmed track `id`, set by list row or left click within
  `CLICK_PICK_PX` of a head on either plot; click on empty plot clears it. Also cleared when
  track dies or restarts (store holds a new `TrackState`), on `set_config` and on an
  empty track list.
- **History tab**: `update(..., a_t=None)` takes CPI time: both apps pass capture
  time, None = now (soft model, `common.gui`). Columns are fixed 0.1 s bins (max-hold), so a
  varying CPI rate cannot stretch time axis. Drawn at <= 5 Hz, only while current.
  Restarts on `set_config`, window change and time running backwards (playback seek).
  x range set explicitly: pyqtgraph auto-range does not follow the image.
  Detections and tracks are class masks on image grid (0 none, 1 detection, 2/3/4 track
  neutral/approaching/receding), written with `np.maximum`, so a track wins over a detection
  in either signal order; one-bin widening at draw keeps the maximum too.
  `update()` and `update_tracks()` must run on one clock: a track time older than newest
  map is taken as a seek and not written to masks (GUI-now for map and capture time
  for tracks dropped nearly every track write live).
- Two pyqtgraph/Qt gotchas that cost real time: `setClipToView` + auto-downsampling silently
  hides curves whose data is set **before** first show; `QDoubleValidator` does *not*
  hard-reject out-of-range input, so `read_cfg_reg` is the actual gate on config values.

### Speed rules

Measured offscreen, 1600x900, 128 x 707 map; table in the Phase 7 guide, Section 8.

- Draw only current tab; others take data in and draw when shown (old per-CPI
  Radar tab took 48-58 % of time at 20 detections, 81-92 % at 100).
- No Qt item created or removed per frame: build once, pool, then `setData` / `setPos` /
  `setVisible`.
- No `mkPen` / `mkBrush` / `QColor` per frame: build once, keep in tables.
- Dense data is an image (mask + lookup table), never a scatter: `ScatterPlotItem` looks up a
  style per point on every `setData` (1000 dots on two plots ~7 ms -> `DotImage`). Its pixels
  are premultiplied ARGB (`_PremultImage`): non-premultiplied RGBA makes alpha blit ~3x slower.
- Every count capped (1000 dots, 30 labels): unbounded scatter stalled the loop once (3b4f861).
- No `showGrid` (~4 ms per axis per paint): one NaN-separated curve per plot, cached as a
  pixmap (`DeviceCoordinateCache`).
- Selection ring = `QGraphicsEllipseItem` + `setPos` (one-point scatter `setData` ~0.2 ms per plot and draw).
- `gui_tracks.set_points` skips `setData` when an empty item stays empty (~0.15 ms each).
- Tables refresh at <= 5 Hz and never rebuild items (one track-list repaint ~6 ms).
- Overlay items use `ignoreBounds=True` and auto-range stays off: otherwise every `setData` re-ranges view.

### Tests and profiler

- `test_gui.py` sets `QT_QPA_PLATFORM=offscreen` before any Qt import (direct or via
  `common.gui` / `offline.playback`): Qt reads it once.
- PySide slot exception does not propagate (printed, loop carries on), so `test_gui`
  records `sys.excepthook` and every test asserts it saw nothing.
- Intake draws nothing: call `draw_now()` before asserting on drawn state
  (`a_table=True` also forces track list past its 5 Hz throttle).
- `profile_gui.py` runs each (mode, tab) cell in own process after an 8 s CPU warm-up
  (laptop boost makes cold cells read ~1.6x slow). Compare numbers only on one machine, on AC
  power. Its draw is `draw_now()` + `processEvents()`: 5 Hz track-list repaint (~3 % of time) not in it.

## Fabric IF mode in the app

- `capture.prepare_block` takes block with no frame sync (`sync_src=1` makes every
  DMA transfer start on a chirp boundary), slices `rx[TRIM : TRIM + rows * N_IF]`
  (`FABRIC_FRAME_TRIM`) and runs `TargetSim.apply_if` on it; `process_rx_data` skips
  `mix_signal`. `sdr.start()` sizes RX buffer from `N_IF` (rounded up to even sample
  count, see `.claude/rules/sdr.md`) and warns if phy's
  `sampling_frequency` reads back different from FS.
- Signal tab in fabric mode: RX spectrogram *is* the IF, so worker emits `if_spec =
  None` and GUI hides IF plot and relabels the remaining one. Config tab's
  TX instantaneous-frequency plot stays: derived from config, and NCO sweeps the
  same BW/T/FS.

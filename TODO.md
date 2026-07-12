# TODO

Roadmap for the FMCW radar. Parts A (offline consolidation) and B (online scaffolding)
are **done**: the SDR is up and running against raw libiio, the offline soft model and
the online model (worker thread + GUI thread) both work, and results flow to the GUI
via Qt signals. Part C (GUI refurbish + runtime reconfiguration) is **done**.
Next up: **Part D - moving fake targets**.

See `src/python/` for the `common` / `offline` / `online` package split.

---

## Part A - Offline consolidation done

Detector self-test, config/property cleanup, frame-sync helper. `config.py` is the
source of truth for parameters. A2 (MTI / slow-time DC removal in `dsp.process_cpi`)
remains a stretch item if live clutter demands it.

## Part B - Online scaffolding done

`online/sdr.py` (libiio `AntSDR` wrapper: cyclic TX buffer, CPI-sized RX buffer,
`start()/read_block()/close()`), `online/capture.py` (frame sync + simulated loopback
delay/velocity/noise), `online/processing.py`, `online/app.py` (`RadarWorker(QThread)`
→ `results` signal -> `gui.RadarDisplay`). Loopback bring-up validated.

---

## Part C - GUI refurbish + runtime reconfiguration done

Third "Configuration" tab: Tx inst-freq plot (2 chirp periods), derived-characteristics
table, and a config form auto-generated from `RadarConfig` field metadata
(label/unit/scale/group/min-max/readonly; untagged fields stay hidden). RE-CONFIGURE
button -> `reconfigure_requested` signal -> `worker.pending_cfg` -> `radio.close()` /
`start()` cycle with last-good revert on failure (`reconfigure_done = Signal(bool, cfg)`
re-enables the button and snaps the panel back via `set_config`). Signals tab: live
Rx/IF spectrograms (`dsp.spectrogram`, throttled worker emit), shared with the offline
model. Gotchas learned: `setClipToView`/auto-downsampling hides curves whose data is
set before first show; `QDoubleValidator` does not hard-reject out-of-range input, so
`read_cfg_reg` is the gate; AD9361 limits FS 2.083-61.44 MSPS, rf_bandwidth 0.2-56 MHz.

**Deferred from C6**: the loopback-disable test (untick `SDR_LOOPBACK_EN` +
RE-CONFIGURE -> `radio.set_loopback(False)`). Do NOT run it until antennas or dummy
loads are wired - disabling digital loopback routes TX to the physical frontend.

---

## Part D - Moving fake targets (loopback target simulator)

Goal: multiple simulated targets riding on the digital-loopback signal, each with
initial range, velocity, acceleration, and a finite duration; add/remove from the GUI.
Kinematics evaluated analytically per capture from the spawn timestamp:
`age = time.monotonic() - t_spawn`, `r(t) = r0 + v0*age + 0.5*a*age^2`,
`v(t) = v0 + a*age` (analytic, not incremental, so frame rate never distorts the
trajectory).

**Key design point - the current `capture.py` mechanism cannot do this as-is.** The
single loopback target works by shifting the *whole capture* through
`frame_sync_linear(a_delay_samples=...)`; one global shift can only ever produce one
echo. Multiple targets = a *sum* of per-target shifted/Doppler-shifted/scaled copies.

**Inject BEFORE frame sync, on the raw capture.** The RX buffer is
`(REPS + margin) * P` samples - an integer number of periods of a strictly periodic
signal - so `np.roll` is an exact circular delay on the raw block too. Injecting
pre-sync means `estimate_chirp_offset` sees the realistic composite
(leakage + echoes + noise) and the sync is honestly tested. Noise also moves pre-sync.
Leakage sync stays: no TX/RX trigger exists (and the non-cyclic RX buffer drops an
arbitrary gap between refills, so the offset changes per capture - never cache it),
the leakage is always the strongest and earliest return, and locking to it
self-calibrates all fixed pipeline delays into range zero.
Known weakness: argmax = strongest, not first-arrival; an echo at amp ~1 steals the
lock and biases all ranges. Keep target amps well below 1 (realistic anyway).
Stretch: earliest-peak-above-threshold within a MAX_RANGE window before the max.

### D1 - `online/target_sim.py`: simulation engine
- [ ] `FakeTarget` dataclass: `r0, v0, accel, duration, t_spawn, amp` (amp as simple
      linear scale or dB; optional 1/R^2 realism later - `soft_model.add_amplitude`
      has the full radar equation if wanted).
- [ ] `TargetSim` class owning a list of `FakeTarget`. Per capture call
      `apply(a_rx_raw, a_config)` on the RAW `read_block()` output (pre-sync): for
      each live target compute `r(t)`/`v(t)`, `tau = round(2 * r * FS / c)`,
      `echo = amp * np.roll(a_rx_raw, tau)` with
      `dsp.apply_doppler_shift(echo, v(t))`; sum all echoes; one `dsp.apply_noise`
      at the end (not per target).
- [ ] Expiry: drop a target when `age > duration` OR `r(t)` leaves
      `[0, config.MAX_RANGE]` (a receding target should die at the range edge, not
      wrap). Expiry means "stop injecting", removal from the list is fine.
- [ ] `set_targets(list)` swap method, same atomic-reference pattern as
      `pending_cfg` (GUI thread writes, worker thread reads).

### D2 - `capture.py`: swap mechanisms
- [ ] Order: `rx_raw = sdr.read_block()` -> `rx_raw = target_sim.apply(rx_raw, cfg)`
      (when loopback enabled) -> `frame_sync_linear(a_delay_samples=0)`. The direct
      TX leakage stays at range 0 (real radars have that too) and the sync now runs
      on the realistic composite.
- [ ] Retire `SDR_LOOPBACK_DELAY_M` / `SDR_LOOPBACK_VELOCITY_MPS` from `config.py`
      (they become per-target parameters); keep `SDR_LOOPBACK_EN` +
      `SDR_LOOPBACK_NOISE_SNR_DB`. Untag/remove from the GUI form + grey-out list.

### D3 - GUI: target editor
- [ ] Dynamic list does not fit the `RadarConfig` metadata form (scalar fields only).
      New `QGroupBox` (Configuration tab or Radar tab): `QTableWidget` with columns
      r0 [m], v0 [m/s], a [m/s^2], duration [s], amp [dB] + "+" / "-" buttons.
- [ ] New `RadarDisplay` signal `targets_changed = Signal(object)` emitting the full
      list of target param dicts on any edit; `main()` connects it to
      `worker.set_targets` -> `TargetSim`. No radio restart needed - purely
      software-side, unlike RE-CONFIGURE.
- [ ] Worker passes `TargetSim` into `capture.capture_rx_data`.

### D4 - Verification
- [ ] One target r0=500 m, v0=50 m/s: detection walks outward in the RD map and
      detections plot; IF spectrogram beat line drifts upward.
- [ ] Add a second target via "+" while running: two lines in the IF spectrogram,
      two detections, no restart.
- [ ] Negative accel: watch velocity color flip in the target table as v crosses 0.
- [ ] Duration expiry + range-edge expiry both remove the target cleanly.
- [ ] Sync robustness: crank one target's amp toward 1 and watch the documented
      failure mode appear (sync steals the lock, all ranges bias by that target's
      range); drop `SDR_LOOPBACK_NOISE_SNR_DB` low and confirm the 8-period
      correlation averaging still finds the offset.
- Gotchas: `v(t)` beyond `MAX_VELOCITY` aliases in Doppler (expected, educational);
  acceleration smears Doppler within one CPI only if `a * T_cpi` rivals the velocity
  resolution (negligible at ms CPIs); many targets at high amp can clip - keep the
  echo sum well under full scale.

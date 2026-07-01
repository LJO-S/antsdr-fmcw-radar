# TODO

Roadmap for taking the FMCW radar from a validated offline soft model to a live
online (SDR) system. Strategy: **consolidate the offline DSP first** — the gaps below
are far cheaper to fix in simulation (where there's ground truth) than on a free-running
SDR stream. Online layer targets **raw libiio (`pylibiio`)**.

See `src/python/` for the `common` / `offline` / `online` package split.

---

## Part A — Offline consolidation (do first)

### A4 — Bug & config cleanup ✅ done
- [x] Dict-iteration crash in `offline/soft_model.py` target readout (`target[0]` → `target["r"]`)
- [x] Removed `print("A=", amplitude)` debug line in `add_amplitude`
- [x] Reconcile `CLAUDE.md` parameter table with `common/config.py`.
      **`config.py` is the source of truth**; `CLAUDE.md` is the copy to keep in sync.

### A1 — Numeric detector self-test  🔧 in progress
File: `src/python/offline/test_soft_model.py`. Inject known targets, assert recovery
within bin tolerance, with a pinned `np.random.seed`.
- [x] Use correct truth-dict keys: truth is `{"range","velocity","rcs"}`, detections are `{"r","v","kind"}`
- [x] Fix `list.sort()` returning `None` (sort in place, then assign)
- [x] Detection tag is `"kind"`, not `"type"`
- [x] Guard against unbound `r`/`v` when a detection is skipped
- [x] Replace fragile index matching with **filter-then-zip** (or nearest-neighbour for close targets)
- [x] Tolerance from physics: `range_res = c/(2*BW)`, `vel_res = (c/fc)/(2*T_rep*REPS)`, allow ~1–2 bins
- [x] Drop the hard-coded `rel_tol=0.1` in `compare_value` so the abs (bin) tolerance governs
- [x] Run for both `TRIANGLE_EN = True / False`
- Gotchas: link budget (pick close, healthy-RCS targets), velocity ambiguity
  (`|v| < (c/fc)/(4*T_rep)`), range–Doppler coupling in non-triangle mode.

### A2 — Impairment compensation (MTI / DC removal)
- [ ] Add slow-time clutter/DC removal in `dsp.process_cpi` before the Doppler FFT:
      subtract per-range-bin mean across chirps (or a slow-time high-pass). Apply to up & down.
- [ ] Validate: enable impairments in `config.py` (`DC_I/DC_Q`, `IQ_AMPL_ERR`,
      `IQ_PHASE_ERR_DEG`, `ISOLATION_DB`) + `noise_figure_db=5`, confirm A1 still passes.
- [ ] Stretch: IQ-imbalance correction only if the test demands it.

### A3 — Frame-sync prototype helper
- [ ] In `common/dsp.py`, recover chirp alignment from an unknown RX start offset
      (cross-correlate against one reference chirp / detect chirp period) before reshaping.
- [ ] Validate in sim: inject a random sample offset, confirm detections still match truth.
- Reusable directly by the online capture layer.

---

## Part B — Online scaffolding (raw libiio / `pylibiio`)

Build against `common/` — no DSP duplication. Threaded capture → process → GUI loop.

- [ ] `online/sdr.py` — libiio device wrapper: open context, configure AD9361
      (`sampling_frequency`, RX/TX `frequency`, gains from config), cyclic TX buffer loaded
      with `dsp.generate_chirp_sequence()`, RX buffer sized to one CPI. `start()/read_block()/close()`.
- [ ] `online/capture.py` — pull RX blocks of CPI length, deinterleave I/Q to complex,
      run the A3 frame-sync helper.
- [ ] `online/processing.py` — thin wrapper: `dsp.build_cpi_context()` once, `dsp.process_cpi()` per block.
- [ ] `online/app.py` — reuse `gui.RadarDisplay`; capture+process in a worker thread,
      push results to the GUI via Qt signals.
- [ ] Bring-up: validate with TX→RX loopback first (single range/Doppler peak) before live.

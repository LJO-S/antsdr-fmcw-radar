---
paths:
  - "src/python/online/{sdr,capture}.py"
---

# AntSDR and libiio

## AntSDR

- `AntSDR` lifecycle: connect + `find_device` in `__init__` (once); all attr writes,
  TX waveform, buffers in `start()` — so `close()` + `start()` is a full reconfigure.
  `set_loopback()` works any time after construction and must be set before `start()`
  (the stale-RX flush fails if no signal path exists).
- Known doc/code mismatch: Part F's write-up said the `start()` RX-live check was made relative
  (a fixed 0.01 normalized amplitude is unreachable through 40 dB of pad at low RX gain), but
  `sdr.py` still tests an absolute `>= 0.01`.
- `sdr.start()` pins the `cf-ad9361-lpc` channel `sampling_frequency` to FS (I4b-2) so the
  stock `rx_fir_decimator` stays at factor 1.
- AD9361 limits: FS 2.083-61.44 MSPS (30.72 in 2R2T, see `hdl.md`), `rf_bandwidth`
  0.2-56 MHz. `SDR_TX_GAIN_DB` is the AD9361 `hardwaregain`, i.e. attenuation.
- `SDR_RX_MARGIN_PERIODS` (default 1): software frame sync needs >= 1; `start()` refuses 0
  there.

## libiio / `pylibiio` API notes (online path)

Verified against the v0.x binding source (the pip package's `iio.py`). **v0.x API only — libiio v1.x is incompatible (blocks/streams), don't upgrade.**

- `iio.Buffer(dev, samples_count)`: `samples_count` is in device **samples** (one I/Q pair
  = 4 bytes with both int16 channels enabled), not int16 words. Byte size is computed from
  the channels enabled **at creation time** → enabling channels BEFORE creating the buffer
  is mandatory, not style.
- `buf.read()` returns raw interleaved bytes in channel-index order (`voltage0`=I,
  `voltage1`=Q) → deinterleave with `raw[0::2] + 1j*raw[1::2]`. RX normalization is
  `/(2**11 - 1)` (12-bit ADC in an int16 word); TX scales by `2**15 - 1`. The RX
  conversion is `dsp.iq_from_raw`.
- `buf.write()` silently truncates to the buffer size — assert the returned byte count.
- **Every DMA buffer needs an even sample count.** The kernel silently rounds each transfer
  down to the DMA bus width (`industrialio-buffer-dmaengine.c`, `round_down(bytes_used,
  align)`); both DMAs are 64-bit, so 8 bytes = 2 I/Q samples with two channels enabled.
  RX: an odd count costs two DMA transfers per `refill()` and the last sample comes from the
  second (the default /8 sawtooth hits it: 129 x 707 = 91203, seen as 1 mismatch per block in
  the ramp check). TX, cyclic: an odd chirp loses its last sample every loop, so TX repeats
  every P-1 (software sawtooth with an odd `SWEEP_LEN`, e.g. T = 99.99 us). `sdr.start()`
  rounds RX up to even and writes two copies of an odd chirp. Both verified on hardware.
- No explicit buffer destroy; the kernel DMA buffer is freed on GC (`__del__`). To
  recreate (restart), `cancel()` + drop references first, else EBUSY (one buffer/device).
- `find_device()` / `find_channel()` return `None` silently on a bad name — guard.
- Attr values must be **strings** (`str(value)`); errors raise `OSError` with real errno
  (incl. `refill()` timeouts — `ctx.set_timeout(ms)` to adjust).
- `dev.set_kernel_buffers_count(n)` (default 4) trades RX staleness vs dropped data.
- Throughput: RX buffer = `(CHIRP_REPS + margin) * N_IF * 4 B` = 2.9 MB per CPI at 128 reps
  at full rate (software or fabric /1), a few frames per second over ~118 MB/s GbE. Fabric /8
  cuts it to 365 kB: measured **up to ~30 CPIs/s in the GUI** (I5 exit), falling
  as detections increase, so the bound is host processing, not the link.

## Real board behaviour

### Measured on the bench (Part F cable loopback)

- MGC window: `SDR_RX_GAIN_DB` is usable across [−90, +60] dB; the defaults are good.
- **Without artificially injected noise, CFAR misbehaves** — the cable-loopback noise floor is
  too low for the training estimator. That is what `SDR_LOOPBACK_NOISE_SNR_DB` is for; it is not
  cosmetic realism.
- AD9361 tracking knobs live on `ad9361-phy`: `calib_mode`, `bb_dc_offset_tracking_en`,
  `quadrature_tracking_en`. Defaults are right. **Quadrature error shows up as a mirrored ghost
  target in the RD map** — recognize it before chasing it as a DSP bug.

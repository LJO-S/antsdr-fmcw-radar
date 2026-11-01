# RF reality, regulatory, range

## RF reality (AD9361 numbers, verified against the datasheet)

Typical values, Table 1 of the AD9361 datasheet (nearest characterized band is 5.5 GHz):

| Spec | 800 MHz | 2.4 GHz | 5.5 GHz |
|---|---|---|---|
| Max TX output power (typ) | 8 dBm | 7.5 dBm | **6.5 dBm** |
| TX carrier leakage @ 0 dB atten | −50 dBc | −50 dBc | −50 dBc |
| TX carrier leakage @ 40 dB atten | −32 dBc | −32 dBc | **−30 dBc** |
| RX noise figure (max gain) | — | — | 3.8 dB |

- So **~6.5 dBm typ is the realistic 5.8 GHz TX ceiling**, not 7 — and the E200's own balun,
  filter and switch losses sit on top of that. Treat +5…+6.5 dBm as the practical figure.
  (Part F's "~7 dBm" was the optimistic reading.)
- TX power control range is 90 dB in 0.25 dB steps — that IS the variable attenuator, so no trim
  pads are needed on the bench.
- **Attenuating the TX makes leakage relatively worse**: at 40 dB attenuation carrier leakage is
  only −30 dBc, and that lands in the close-in range bins the mask already covers.
- FMCW is **100% duty and constant envelope**: p.e.p. = average power, and any PA needs a
  continuous-rated heatsink/dummy load, not a burst rating.

### Measured on the bench (K4 VNA)

Full table: `hardware/bench_measurements.md`. LiteVNA-64, SOL on port 1, thru = port-2 test
cable, 5.0-6.3 GHz, 201 points.

## Regulatory constraints (Sweden, PTS)

**5.8 GHz was a good choice: the 50 MHz chirp at fc = 5.8 GHz spans 5.775–5.825 GHz, which sits
inside BOTH license-free regimes.** Two routes, per PTSFS 2025:1 (PTS exemption regulation):

| Route | Band | Ceiling | Requires |
|---|---|---|---|
| SRD, non-specific (§204) | 5.725–5.875 GHz | **25 mW e.i.r.p.** (14 dBm) | nothing |
| Amateur (§203) | 5.65–5.85 GHz | **200 W p.e.p. fed to the antenna** (antenna gain not counted) | HAREC certificate + call sign |

- Amateur route (Swedish HAREC via SSA exam) unlocks the Part H PA legally: a 2 W FPV PA is
  33 dBm, far under the 200 W ceiling. Amateur use is defined as non-commercial "tekniska
  undersökningar", which covers radar experiments; an unattended radar counts as an automatic
  transmitter and must periodically send its call sign. Amateur is **secondary** at 5.65–5.85 GHz
  (radiolocation and military are primary) — no protection, no interference. Homebrew amateur gear
  is exempt from CE/RED; SRD gear is not.
- **Part J ceiling is legal, not technical**: synthetic bandwidth is capped at 150 MHz by the SRD
  band (5.725–5.875) or 200 MHz by the amateur band (5.65–5.85, centre ≈ 5.75 GHz) → ~1.0 m or
  ~0.75 m fine resolution. The 330 MHz plan in `TODO.md` is loopback-only.
- **EMF (SSMFS 2008:18, 10 W/m² public reference level above 2 GHz)**: a 2 W PA into the
  16 dBi sector = 79 W EIRP -> keep people ~0.8 m out of the main beam; into the 30 dBi dish =
  2 kW EIRP -> ~4 m. At 200 W into 16 dBi it is ~8 m. Dummy-load discipline until the antennas
  point away from people.

## Physical range note

Part G's link budget (0 dBm TX, 16 dBi sector + 30 dBi dish (mANT30), 1 m² target) gives
~-93 dBm at 100 m and ~-121 dBm at 500 m against a ~-91.5 dBm noise floor, which coherent
processing gain turns into ~57 dB / ~32 dB post-processing SNR. **Antennas before PA**: TX power buys range^(1/4),
NF buys SNR linearly. The real ceiling on a monostatic 100%-duty FMCW system is TX/RX isolation,
not the PA and not the law.

**Confirmed in the Part G field test** (software mode, every setting at its default,
no PA): cars, people on foot and bikes detected, with the TX sector and RX grid ~1.5 m apart.
That spacing was enough isolation; no septum was needed. Only video was recorded, no IQ.

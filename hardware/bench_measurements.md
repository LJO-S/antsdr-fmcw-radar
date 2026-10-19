# Bench measurements (K4, K7)

Guide Sections 5.2 (K4) and 8.2 (K7). For each row: prediction first (datasheet or hand
calculation), then measure, then explain the difference. K4 closed 2026-10-10; conclusions in
`CLAUDE.md` ("Measured on the bench (K4 VNA)").

## Setup

- VNA: LiteVNA-64 (1.5-port: S11 and S21 only, port 2's own reflection is not corrected),
  5.0-6.3 GHz, 201 points, no averaging. IFBW 12 Hz for the floor and isolation rows.
- K4 cal (2026-10-10): SOL on the bare port 1, thru = the port-2 test cable onto port 1
  (+ isolation). Male-male items: female-female barrel on the port-2 cable end, so the barrel
  (~0.1 dB) is in those readings.

## Floor

| Item | Quantity | Prediction | Measured | Why the difference |
|---|---|---|---|---|
| VNA floor (both ports terminated) | S21 at 5.8 GHz | below -40 dB (bound only, no spec) | -81 dB | IFBW 12 Hz, isolation cal. Leaves ~40 dB margin under the HMC849A's ~40 dB isolation at 5.8 GHz (K7). |

Isolation readings are trustworthy only ~10 dB above this floor.

## Cables, pads, filters, terminations (at 5.8 GHz)

| Item | Quantity | Prediction | Measured | Why the difference |
|---|---|---|---|---|
| HF240, TX (2 m) | S21 | -1.352 - 0.2 * 2 - 0.1 = -1.9 dB | -1.9 dB | Matches: 1.35 dB cable + ~0.5 dB connectors and barrel. |
| HF240, TX (2 m) | S11 | -15..-30 dB @ 60 MHz | -11..-34 dB @ 60 MHz | Connectors worse than assumed (~VSWR 1.35). Far end worst (barrel + uncorrected port 2). Near-equal reflections give the deep null. |
| HF240, TX (2 m) | velocity factor (group delay) | 0.84 | 0.7 (S11 ripple), 0.83 (S21 delay, ~8 ns) | 25 ripples in 1.3 GHz (52 MHz period) gives 0.7, but the ripple path includes the port-2 test cable (+~1.7 ns). S21 delay sees the cable only and matches. |
| HF240, RX (2 m) | S21 | -1.9 dB | -1.8 dB | Within 0.1 dB of TX: the two cables match. |
| HF240, RX (2 m) | velocity factor | 0.84 | 0.83 (S21 delay) | Matches datasheet and TX cable. |
| Wuerth RG316 jumper | S21 | -0.9 - 0.2 * 2 - 0.1 = -1.4 dB | -1.5 dB | Close. Connectors reflect more than the HF240's (S11 to -8 dB), and reflected power is lost from S21. ~1.4 dB without the barrel: at or just over the 1.2 dB max spec. |
| Wuerth RG316 jumper | S11 | -9..-29 dB | -8..-34 dB | Matches VSWR 1.5 max spec per end; far end a bit worse again (barrel + port 2). |
| VAT-30A+ pad | S21 | -30 dB | -31 dB | Datasheet typ 31.5 dB at 5.8 GHz (30 dB is the 10 MHz nominal). Flatness not read. |
| VAT-30A+ pad | S11 | -25 dB (typ VSWR 1.12), worst -18 dB | -18..-22 dB, worse with f | Inside the VSWR 1.3 spec (-17.7 dB), worse than typ. Likely this VNA's S11 accuracy limit (imperfect cal load, worse at high f). No ripple: the pad hides port 2. |
| VBFZ-5500-S+ filter | S11 | -11..-33 dB | -14..-40 dB | Worst matches datasheet typ (VSWR 1.43 = -15 dB at 5.5 GHz), far inside max 2.1 (-9 dB). Nulls are the filter's own reflection zeros, not cable ripple. |
| VBFZ-5500-S+ filter | S21 (IL) | -1.26 dB | -1.8 dB | Lossy unit: ~0.6 dB over typ, inside the 2 dB max. Port-2 padded re-check gave the same (-32.9 dB with the 31 dB pad). Second unit: same shape, a bit less IL (not recorded); it goes on RX. |
| VBFZ-5500-S+ filter | passband edges (2 dB) | ~4.56 GHz / ~6.48 GHz typ | skipped | Low edge below the 5.0 GHz cal, high edge above the VNA's 6.3 GHz. |
| Molex termination | return loss | 21 dB | 22 dB | Within the VSWR 1.2-1.3 class (no exact 5.8 GHz spec). |

## Skipped or not recorded

| Item | Quantity | Prediction | Measured | Why the difference |
|---|---|---|---|---|
| AM-5G16-120 sector | S11 (worst in 5.7-5.9 GHz) | <= -14 dB (VSWR 1.5 max, 5.10-5.85 GHz) | looked, not recorded | Skipped, datasheet trusted (indoors, no open sky). Out of band (1-2 GHz) S11 ~0 dB, as expected. |
| mANT30 dish | S11 (worst in 5.7-5.9 GHz) | - | skipped | Not measured. |
| Open-ended Wuerth RG316 (0.305 m, VF 0.695) | Smith chart: S11 rotation rate | 1.05 deg/MHz clockwise, one turn per 342 MHz, radius 0.72 | looked, not recorded | Skipped. Same round-trip delay physics as the HF240 S11 ripple and S21 delay rows. |

## K7 switch (HMC849A): datasheet, one line per spec

Guide 13.1. `docs/datasheets/hmc849a.pdf`, 4-6 GHz row, plots read at 5.8 GHz, VDD 3.3 V
(3 V curves where they differ). Module: DC-blocking caps on the RF ports fitted (checked
2026-10-12); still unverified (guide 8.1): header pinout, EN tied low, genuine chip.

| # | Spec | Datasheet | Means | Radar |
|---|---|---|---|---|
| 1 | Frequency range | DC-6 GHz | Band the specs cover | 5.8 GHz sits at the top edge: loss climbs and isolation falls fast there (both plots bend past 5 GHz). |
| 2 | Insertion loss | 1.8 typ, 2.5 max (4-6 GHz); ~1.7 dB at 5.8 GHz (IL plot, 25 C) | Power lost in the on path | Lowers EIRP 1:1. The SRD cap binds, not the AD9361, so raise `SDR_TX_GAIN_DB` by the same. With 2 m HF240 the TX antenna needs ~13 dBi to reach the cap (guide 1.3). |
| 3a | Isolation, RFC to off port | 35 min, 50 typ (4-6 GHz); ~40 dB at 5.8 GHz (plot: RF1 40, RF2 42) | How much leaks out of the off port | The off antenna radiates a copy ~40 dB down. Angle error up to `asin(10^(-I/20))`: 0.57 deg at 40 dB, 1.0 deg at the 35 dB min. Fixed per unit, so the K10 calibration takes most of it out. |
| 3b | Isolation, RF1 to RF2 | ~33 dB at 5.8 GHz (plot, no table value) | Leak between the two RF ports | Only the wave reflected from the on antenna takes this path: -14 dB (VSWR 1.5) - 33 = -47 dB, 7 dB under 3a's leak. |
| 4 | Return loss | On 14 dB, off 15 dB (4-6 GHz). At 5.8 GHz (plot): RFC ~12.5, on port ~19, off port ~18.5 dB | Match of the common port, an on port, and an off (terminated) port | RFC is the worst: 5.6 % reflected back to the TX, 0.25 dB mismatch loss. Harmless at +6.5 dBm. Off: the off antenna sees ~50 ohm (see 8). |
| 5 | Switching | Rise/fall 60 ns (RF 10-90 %); on/off 150 typ, 600 max ns (50 % V_CTL to 10/90 % RF); no settling spec | Time from the control edge until the RF has moved | 600 ns = 34 samples at 56.6 MSPS, ~4 IF samples at /8, 0.6 % of a 100 us chirp. Still under the ~13 IF-sample halfband transient at every chirp boundary: flip at the boundary, no lead time needed. |
| 6 | Compression, IP3 | P1dB 24 min / 27 typ at 3 V, 30 / 33 at 5 V. At 5.8 GHz, 3 V (plot): P1dB ~24.5, P0.1dB ~22 dBm. IP3 52 dBm typ, ~47 at 5.8 GHz | Input power where the gain drops 1 / 0.1 dB; intermod intercept | +6.5 dBm max is ~15 dB under P0.1dB: no compression at 3.3 V. FMCW is one tone at a time, so IP3 hardly applies. |
| 7 | Max RF input | Abs max only: through 31 dBm (3 V) / 33 (5 V), terminated 26.5 dBm. Footnote: heat is the only limit, so no separate hot-switching rating | Through = power on the on path. Terminated = power into an off port, burnt in its internal 50 ohm | +6.5 dBm is 24 dB under, hot switching every chirp included. A Part H 2 W PA (+33 dBm) breaks both limits: switch before the PA (one PA per antenna), or a bigger switch. |
| 8 | Reflective or not | Nonreflective: RF1/RF2 terminated in 50 ohm when off. EN high: all off, RFC reflective | Where the power at the off port goes | The off antenna is loaded with 50 ohm, so it absorbs what it picks up from the on antenna instead of re-radiating it with an unknown phase (which would bend the angle). EN high: TX sees an open, nothing radiates. |
| 9 | Supply | VDD 3-5 V (abs max 7), IDD 1.2 mA at 3 V | Chip supply range and draw | J30 pin 1 (3.3 V) has plenty, if the module takes 3.3 V (check its own draw: LED, regulator). Use J30, not USB: V_CTL and EN must stay <= VDD + 1 V, and J30's 3.3 V is up before the FPGA drives the pin. |
| 10 | Control | V_IL 0-0.8 V (< 1 uA), V_IH 2.0-5.0 V (40 uA). EN low: V_CTL low -> RF2 on, high -> RF1 on. EN high: all off. 134 k to GND inside on V_CTL and EN | Logic levels and truth table | One J30 pin (LVCMOS33) drives V_CTL with 1.3 V margin; 1.8 V logic would not. `tx_sel` high -> RF1 if wired straight. Floating reads low: EN floating = enabled, V_CTL floating = RF2 on (e.g. before the bitstream loads). |
| 11 | ESD | HBM class 1A (250-500 V) | Static discharge it survives | Fragile: touch ground before handling the module or plugging antennas and leads; a charge on an antenna lands on an RF pin. |
| 12 | Marking | "H849A" over a 4-digit lot code, QFN16 4x4 mm | What a genuine part shows | Check under a loupe: another or a blank marking means a relabel or clone (guide 8.1, "genuine chip"). |

## K7 switch (HMC849A module): VNA (5.8 GHz)

Guide 8.2. Two clone modules, 3.3 V bench supply. A = V_CTL high, B = V_CTL low (module
jumper). P1 = port 1 (cable A), P2 = port 2 (cable B), M = Molex load. Rows in measurement
order; each step moves one pair.

- Cal at the ends of the two test cables (male; module ports female): f-f barrel on cable A,
  SOL on the barrel face, ISOLT with both ends loaded, thru = cable B into the barrel, barrel
  removed. S21 reads ~0.1 dB optimistic.
- The 2026-10-18 cal was faulty: unit 1 read IL -6.0 / -6.6 dB on it, -3 / -3 dB on the
  2026-10-19 cal. Unit 1's other S21 rows marked (old cal) are still from the faulty one.
- New cal cross-checked: VAT-30A+ reads -31 dB, same as K4.
- Both units: polarity as the truth table (V_CTL high -> RF1 on).
- Supply current ~60 mA (module LED etc.; the chip is 1.2 mA): power it from J30 pin 1, not a
  GPIO pin (~12 mA drive). Open: marking.

| Step | Ports | Quantity | Prediction | Unit 1 | Unit 2 | Why the difference |
|---|---|---|---|---|---|---|
| A1 | P1 RFC, P2 RF1, M RF2 | S21, IL RFC -> RF1 | -1.7 (chip, IL plot) - ~0.3 (module traces + SMAs, guess) = -2.0 dB | -3 dB | -3 dB | |
| A2 | P1 RFC, P2 RF2, M RF1 | S21, isolation RFC -> RF2 (off) | -40 dB (plot; min -35). 41 dB above the K4 floor, so readable | -40 dB (old cal) | -40 dB | |
| A2 | same | S11, RFC | Chip -12.5 dB (G 0.24); load 22 dB RL seen through 2 x 2.0 dB (G 0.05): -11..-15 dB | -8 dB | -8 dB | |
| A3 | P1 RF1, P2 RF2, M RFC | S11, RF1 (on) | Chip -19 dB (G 0.11); load as above (G 0.05): -16..-24 dB | -10 dB | -7 dB | |
| A3 | same | S21, RF1 -> RF2 | ~-33 dB (plot, datasheet row 3b) | -33 dB (old cal) | -33 dB | |
| A4 | P1 RF2, P2 RF1, M RFC | S11, RF2 (off) | -18.5 dB (plot; table 15 typ). Internal 50 ohm, nothing behind it | -18 dB | -14 dB | |
| A4 | same | S21, RF2 -> RF1 | ~-33 dB | -35 dB (old cal) | -32 dB | |
| B1 | P1 RF2, P2 RF1, M RFC | S11, RF2 (on) | -16..-24 dB | -10 dB | -7 dB | |
| B1 | same | S21, RF2 -> RF1 | ~-33 dB | -33 dB (old cal) | -31 dB | |
| B2 | P1 RFC, P2 RF1, M RF2 | S21, isolation RFC -> RF1 (off) | -40 dB | -50 dB (old cal) | -45 dB | |
| B2 | same | S11, RFC | -11..-15 dB | -10 dB | -10 dB | |
| B3 | P1 RFC, P2 RF2, M RF1 | S21, IL RFC -> RF2 | -2.0 dB | -3 dB | -3 dB | |
| B4 | P1 RF1, P2 RF2, M RFC | S11, RF1 (off) | -18.5 dB | -11 dB | -15 dB | |
| B4 | same | S21, RF1 -> RF2 | ~-33 dB | -34 dB (old cal) | -31 dB | |

## K7 in-radar bench test (guide 8.4), 2026-10-19

FMC5, fabric /8, `TDM_EN`, cable path, RX gain 40 dB. Module VDD from J30 pin 1, V_CTL from
pin 3 (`tx_sel`), ground through the coax shields (2.54 mm Dupont leads on the 2.0 mm header:
pins 1 and 3 fit side by side, 1-2-3 do not). Level = mean IF power per row, the first 16 IF
samples of each row skipped. A = even rows (`tx_sel` 0), B = odd rows.

| Setup | TX gain | A rows | B rows | A - B |
|---|---|---|---|---|
| Baseline: TX1 -> 30 dB pad -> RX1, no switch | -30 dB | -49.7 dBFS | -49.7 dBFS | 0 |
| Board leak: TX1 -> switch, RX1 unplugged | -30 / 0 dB | -58.7 / -31.4 dBFS | same | 0 |
| RF2 -> 30 dB pad -> RX1, RF1 on the load | -30 dB | -60.7 dBFS | -55.8 dBFS | -4.9 dB |
| RF2 -> RX1, no pad, RF1 on the load | -30 dB | -23.9 dBFS | -54.3 dBFS | 30.4 dB |
| Swapped: RF1 -> RX1, no pad, RF2 on the load | -30 dB | -53.0 dBFS | -23.5 dBFS | -29.5 dB |
| Swapped, triangle + TDM (rows 0-1 A, 2-3 B) | -30 dB | -53.7 dBFS | -24.0 dBFS | -29.7 dB |

- Polarity as the datasheet: `tx_sel` 0 (A) -> V_CTL low -> RF2 on.
- Row 0 is A in every capture (6 TDM, 3 triangle + TDM): sync per period works.
- The switch settles within ~5-6 IF samples (~0.8 us) of each boundary, inside the ~13-sample
  halfband transient: no `TX_SEL_DELAY` needed.
- The E200's own TX1 -> RX1 leak (~40-44 dB board isolation at 5.8 GHz, scales with TX power)
  floors the in-system contrast at ~30 dB; the switch itself isolates 40-45 dB (VNA).
- Through the 30 dB pad, the cable path sits only ~9 dB above that leak, so the test with the
  pad shows nothing (row 3 of the table). Run it without the pad: TX -30 dB with
  `SDR_TX_GAIN_MAX_DB` -20 for the session (~-30 dBm at RX1, worst case at 0 dB ~-0.2 dBm).

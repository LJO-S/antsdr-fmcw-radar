# Hardware List

Phased to match TODO.md Parts F/G/H: buy the cheap cable-loopback kit NOW, order
the used antennas NOW (slow shipping/used-market hunting), defer the PA/LNA/BPF
until Part G shows the real range limit.

Prices researched 2026-07 (mostly US sources; ~1 USD = 10 SEK, ~1 EUR = 11 SEK,
add VAT/import for non-EU sellers). Check Blocket / eBay.de / wifi-stock first for
EU stock. **Everything must be rated to 6 GHz** - lots of cheap SMA parts (pads,
dummy loads) are DC-3 GHz only, silently 1-3 dB off at 5.8 GHz or worse.

---

## Phase F kit - cable loopback (order NOW, ~700-1100 SEK total)

Lead-time reality (2026-07): AliExpress/eBay China-stock = 2-4 weeks. EU-warehouse
filters exist (ebay.de -> Artikelstandort -> Europaeische Union; AliExpress ->
"Ships from" -> ES/PL/DE) but rarely stock niche RF parts.

**Plan: order the AliExpress kit + start the used-antenna hunt the same day** -
the antennas are the true long pole, and Part E + stretch items fill the wait.

**Fallback fast route if the wait chafes: [digikey.se](https://www.digikey.se/en)**
(1-3 working days DDP, SEK+VAT at checkout,
[free delivery over 430 SEK](https://www.digikey.se/en/help-support/delivery-information/delivery-time-and-cost)).
Minimal cart ~1250-1400 SEK incl VAT:
- 3x [VAT-15A+](https://www.digikey.com/en/products/detail/mini-circuits/VAT-15A/19186442)
  15 dB DC-6 GHz pad, $25.28 ea - stacked pairs give 30/45 dB blocks (stacking
  matched pads is standard; dB just add). Chosen 2026-07-17 when VAT-30A+ and
  VAT-30W2+ both went out of stock at digikey.se (18-week lead); three identical
  pads also make the pairwise acceptance test sharper than 2x30 did. If single
  30 dB units matter, check mouser.se stock of VAT-30+/-30A+ first.
- 2x [Molex 0733910680](https://www.digikey.se/sv/products/detail/molex/0733910680/3878259)
  SMA plug termination, 50 ohm, ~1 W class, 18 GHz-rated series, 58 SEK ea incl
  VAT - chosen over the 4x-pricier ANNE-50+ (whose 35 dB return loss is luxury
  here; Molex-typical ~1.2-1.3 VSWR reflects <2%, ample for port capping)
- 2x [Amphenol 132171](https://www.digikey.com/en/products/detail/amphenol-rf/132171/1011923)
  SMA m-f saver, ~$8 ea
- No small trim pads needed: `SDR_TX_GAIN_DB` spans ~90 dB in 0.25 dB steps - that
  is the variable attenuator. Only the fixed 30 dB protection block (RX survives
  accidental max TX gain) must be physical.

### Attenuators (~150-400 SEK)
Need >= 30-40 dB total in the TX->RX path, plus small pads for trimming.
Sourcing (2026-07: Amazon prices for these are not competitive - skip it):
- Cheapest: AliExpress multi-value listings, $3-8 per pad, 2-3 week shipping, VAT
  at checkout (IOSS, no Postnord fee). Prefer DC-8 GHz-rated for margin at 5.8 GHz:
  [2 W DC-8 GHz 10/20/30/40 dB](https://www.aliexpress.com/item/1005006414259412.html),
  [2 W DC-6 GHz 1-30 dB](https://www.aliexpress.us/item/3256801757708013.html)
- Faster/buyer protection: eBay, same Chinese stock marked up -
  [30 dB 2 W 6 GHz ~$13.49](https://www.ebay.com/itm/111480542272); filter by
  item location: Europe for EU-warehouse stock (no customs delay).
- Quality anchor (optional): one Mini-Circuits VAT pad via DigiKey/Mouser
  (ship to SE in 1-3 days),
  [VAT-30+ ~$25](https://www.digikey.com/en/products/detail/mini-circuits/VAT-30A/19186461)
- Buy 2x 30 dB + a few small values. Cheap is fine here: Phase F needs isolation,
  not calibrated attenuation (MGC sweep + leakage lock absorb the exact value).
  **Acceptance test on arrival**: insert the pad in the cable loop and confirm the
  leakage power drops by ~the labelled dB; cross-check the two 30 dB pads against
  each other - a dud reveals itself immediately.
- Note: 2 W-rated pads are fine for Phase F (TX ~0 dBm) but must NOT sit after the
  Phase H 2 W PA.

### Port terminations, 2x SMA-male (~100-250 SEK)
- Terminology: "termination/terminator" = small 50 ohm cap, 0.25-2 W, job is
  IMPEDANCE (good return loss so the capped port reflects nothing); "dummy load" =
  the same resistor with a thermal design, 5 W+, job is absorbing TX POWER. Phase F
  runs at mW levels, so a 1 W-class termination is the RIGHT part, not a
  compromise - for the C6 loopback-disable test the match is what matters (a bad
  cap reflects TX back in = phantom target at range ~0).
- Quality: [ANNE-50+](https://www.digikey.com/en/products/detail/mini-circuits/ANNE-50/16682797)
  1 W, ~35 dB return loss @ 5.8 GHz, $17.81; AliExpress/eBay generic DC-6 GHz
  ~$5-11 (check return-loss claims). Refuse anything rated only DC-3 GHz.
- EU-shop fallback, marked up ~3x:
  [sdrstore.eu 2 W DC-6 GHz SMA, EUR 29.45](https://www.sdrstore.eu/test-and-measurement/dummy-loads/2w-dc-6ghz-50-ohm-rf-dummy-load-sma-male-high-quality-coaxial-termination-load/)
- **NOT reusable as the Phase H PA load**: 2 W FMCW at 100% duty needs a true
  finned dummy load, >= 10 W continuous (cheap "10 W" often means burst) - buy it
  together with the PA. A 1 W termination on the PA output cooks, drifts off
  50 ohm, then opens - which can kill the PA too.

### SMA jumpers, 1-2x (~100-200 SEK)
- ONE short (0.3-0.5 m) RG316 SMA m-m jumper completes the Phase F loop: the VAT
  pads are in-line m-f, so TX(f)-pad-cable-pad-RX(f) mates with a single m-m
  cable. Split the pads one per port (better match at both cable ends, less
  cantilever on the PCB-mounted jacks) instead of stacking both on TX. Turn only
  the coupling nut, never the pad body/cable.
- A 2nd jumper = cheap substitution-debugging spare if the cart is already at
  free shipping. More jumpers are Phase G bench stock, not a Phase F need.
- Chosen part (2026-07): Wuerth 65503503530505, WR-CXASY RG316/U SMA plug-plug,
  304.8 mm - datasheet-specified DC-6 GHz, VSWR <= 1.5, IL <= 1.2 dB max,
  bend radius >= 12.7 mm, 500 mating cycles
  ([datasheet](https://www.we-online.com/components/products/datasheet/65503503530505.pdf)).
  The 152 mm sibling (65503503515305) is too short for the padded-port U-turn.
- Optional Phase F extra: one long (5-10 m) run as a fixed real "target" of known
  electrical length (TODO Part F).

### Adapters (~150-200 SEK)
- SMA savers (m-f straight-through, save the E200 connectors), a few f-f / m-m
  barrels. N-to-SMA adapters can wait for Phase G antennas.

---

## Phase G kit - antennas, no PA (order NOW, used market is slow; ~1500-2500 SEK)

### Rx antenna: grid/dish, 30 dBi class (~700-1100 SEK)
- MikroTik mANT30 (MTAD-5G-30D3), 30 dBi dish, ~80-92 EUR new EU stock -> ~900-1050
  SEK, no import hassle
  ([mikrotik-store.eu](https://www.mikrotik-store.eu/en/mant30-5ghz-30dbi-dish-antenna-precise),
  [wifi-stock](https://www.wifi-stock.com/details/mikrotik_parabolic_30dbi_5ghz_antenna_ma.html))
- TP-Link TL-ANT5830B, 30 dBi grid, ~$70-84 new -> ~700-900 SEK + shipping/import
  ([SuperTech $69.99](https://www.supertechsupplies.com/5ghz-30dbi-grid-antenna-part-tl-ant5830b/),
  [Telecom Creations](https://telecomcreations.com/products/tp-link-5ghz-30dbi-grid-antenna-part-tl-ant5830b))
- Ubiquiti RocketDish RD-5G30-LW, $109 new at
  [UI store](https://store.ui.com/us/en/products/rd-5g30-lw); used on eBay/Blocket
  often cheaper - WISP teardown surplus is the bargain bin.
- All are N-female or RP-SMA - check the connector before ordering pigtails.

### Tx antenna: sector, ~19 dBi 120 deg (~600-1000 SEK)
- Ubiquiti AM-5G19-120: used ~$100 / EUR 89 on eBay, new $129-139
  ([eBay listings](https://www.ebay.com/itm/284762727987),
  [UI store](https://store.ui.com/us/en/products/am-5g2)). Plentiful used WISP
  stock on eBay.de/Blocket - haggle.

### Cables and adapters (~400-600 SEK)
- 2x N-male to SMA-male LMR-240 pigtails (1-2 m): ~$20-30 each on eBay/Amazon
  ([example](https://www.ebay.com/itm/262839274818),
  [Pasternack PE3C0044](https://www.pasternack.com/sma-male-n-male-lmr240-cable-assembly-pe3c0044-p.aspx)
  is the expensive reference part)
- N-female to SMA adapters as fallback (~150 SEK)

### Mounting (~300 SEK)
- 2x cheap speaker/camera tripods, >= 1-2 m separation (isolation), sheet-metal
  septum from scrap.

---

## Phase H kit - PA/LNA/BPF (DEFER until Part G shows the range limit)

Reminder before spending here: EU 5.8 GHz SRD budget is ~25 mW (14 dBm) EIRP; the
PA chain is Faraday-cage / amateur-licence territory (5650-5850 MHz secondary).
LNA likely pays off before the PA (NF buys SNR linearly; TX power buys range^1/4).

### LNA (~200-1000 SEK depending on ambition)
- Cheap: generic 5.8 GHz FPV RX LNA boards (~14-20 dB), Banggood/AliExpress,
  ~200-350 SEK ([Banggood search](https://www.banggood.com/buy/5.8-ghz-lna.html));
  NF unspecified - assume 2-3 dB.
- Better: wideband 0.5-8 GHz LNA modules, ~30 dB gain / ~1.5 dB NF class
  ([Gwave](https://gwavetech.com/products/sma-female-low-noise-amplifier-lna-30db-gain-1-5db-noise-figure-0-5-8ghz));
  price on request, likely $50-150. DIY route documented at
  [VK4GHZ](https://vk4ghz.com/5-8-ghz-lna-for-fpv-receiver/).
- Plus a 3-6 dB pad between LNA and RX port (from the Phase F kit).

### PA (~200-500 SEK)
- FPV 5.8G 2 W (33 dBm) linear amp: ~$20 AliExpress
  ([example](https://www.aliexpress.com/item/32826971916.html)), $35-50 eBay.
  100% duty in FMCW -> heatsink + fan mandatory; bias sequencing per block diagram
  (never enable without load).
- Buy WITH the PA: a true finned dummy load, >= 10 W continuous, DC-6 GHz - the
  Phase F 1 W terminations must never cap the PA output (see Phase F notes).

### BPF 5725-5875 MHz (~300-1500 SEK)
- Surplus/eBay 4-pole cavity outdoor filters show up cheap
  ([example listing](https://www.ebay.com/itm/265906825054)) - the WISP surplus
  route again.
- New reference: Mini-Circuits
  [ZVBP-5800-S+](https://www.minicircuits.com/WebStore/dashboard.html?model=ZVBP-5800-S+)
  cavity (expensive, ~$100+).
- Matters most once the LNA raises gain and WiFi/LTE blockers become real; skip at
  first.

### PSU/bias (~200-300 SEK)
- 12 V supply, DC jack, wiring; PA_EN via Zynq GPIO per the block diagram.

### TX harmonic LPF
- Kills the 11.6 GHz harmonic once the PA is in; source when the PA is chosen
  (often built into better PA boards).

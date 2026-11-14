// K9 mounts: board brackets, 20 / 35 mm pipe clamps, turntable mast head + base, pole top.
// One part per run:  openscad -D 'part="bracket_tx"' -o bracket_tx.stl k9_mount.scad
// (scripts/antenna_design/mount/make_stl.sh exports all into ../local/mount/).
//
// Every bracket, clamp and the mast head share one bolt pattern: 4 bolts on a 60 mm square,
// so any two bolt together at 0 or 90 deg.
//   Radar:      pole -> clamp_35 + clamp_20 (cross) -> 20 mm pipe -> clamp_20 + bracket, TX and RX.
//               Rotating a clamp_20 on the pipe tilts its board (beam-tilt correction).
//   Turntable:  pole_top on the TX tripod, lazy susan on it, mast_base on the lazy susan,
//               20 mm pipe mast, mast_head on top, bracket_tx on that: upright, feed end up
//               (the switch clears the pipe) = azimuth cut,
//               turned 90 deg = elevation cut. The board face lies in a plane through the
//               lazy susan's centre and the board's middle sits over it, both by construction.
//               RX tripod: clamp_35 + bracket_rx.
//
// Print in PLA, 3 walls, 20 % infill. bracket_*: flat as exported, rotated 45 deg in the slicer
// (226 mm long). Others: as exported, no supports.
// Hardware (BOLT = 2.5), each with a nut (nuts sit in the traps):
//                              turntable   radar (re-uses clamp_35 and the brackets)
//   M2.5x12, pattern joints    8           12 (cross 4, TX 4, RX 4)
//   M2.5x20, clamp lug         1           4 (clamp_35, 3x clamp_20)
//   M2.5x8, switch             4           (stays on bracket_tx)
//   M2x12-16, board            8           (stay)
// Sockets (mast_head, mast_base, pole_top) have no screws: tape the pipe / pole end until it
// fits snug, then wrap tape round the joint. Lazy susan: taped to pole_top and mast_base
// (gaffer over the rim or double-sided foam).

part = "bracket_tx";

$fn = 96;
P = 72;        // bolt-pattern plate, square
PH = 30;       // pattern holes at +-PH
PT = 4;        // pattern plate thickness (clamp_35)
PB = 6.5;      // boss under each pattern nut (clamp_35)
PT_S = 3;      // the same, slim (clamp_20, mast_head)
PB_S = 5.5;
WALL = 3;      // clamp sleeves
WALL_S = 2;    // turntable sockets
BOLT = 2.5;    // pattern + lug bolts: 2.5, 3 or 4 (M2.5 / M3 / M4)
BOLT_D = BOLT + 0.4;                                          // clearance
BOLT_NUT = (BOLT == 2.5 ? 5.0 : BOLT == 3 ? 5.5 : 7.0) + 0.3;  // across flats, with clearance
BOLT_NUT_H = (BOLT == 2.5 ? 2.0 : BOLT == 3 ? 2.4 : 3.2) + 0.2;
M2 = 2.4;
M2NUT = 4.3;

// Board (KiCad tx / rx): u along the column from the feed edge, v across. Holes 5 mm in
// from the main strip's corners; both boards are symmetric in v.
L = 225.985;
UC = 116.3;    // middle of the 8 patches (u 26.6 .. 206.0)
T = 3;         // bracket frame
S = 6;         // stand-off: clears the SMA legs on the back
BOARD = 1.6;
FACE = T + S + BOARD;  // pattern face -> patch face

module hexnut(af, h) {
  rotate([0, 0, 30]) cylinder(r = af / sqrt(3), h = h, $fn = 6);
}

module pattern_holes(depth) {  // along z, centred on the origin
  for (i = [-1, 1], j = [-1, 1]) translate([PH * i, PH * j, -1]) cylinder(d = BOLT_D, h = depth + 2);
}

module frame2d() {  // the pattern plate, hollow: 12 mm frame around the holes
  difference() {
    square(P, center = true);
    square(P - 24, center = true);
  }
}

// Pattern plate: mating face at z = 0, bosses and nut traps on +z, a 16 mm bar along y.
module pattern_plate(pt = PT, pb = PB) {
  difference() {
    union() {
      linear_extrude(pt) {
        frame2d();
        square([16, P], center = true);
      }
      for (i = [-1, 1], j = [-1, 1]) translate([PH * i, PH * j, 0]) cylinder(d = 13, h = pb);
    }
    pattern_holes(pb);
    for (i = [-1, 1], j = [-1, 1]) translate([PH * i, PH * j, pb - BOLT_NUT_H]) hexnut(BOLT_NUT, BOLT_NUT_H + 0.1);
  }
}

// RF switch module (HMC849A, 30 x 25.5 mm, connectors along the 25.5 mm side): on the
// bracket's back near the feed end, connectors along u. 3 mm holes, centres 21 along u x 26
// along v; M2.5 from the module side into nuts trapped on the front (under the board). The
// pad stays SW_T thick so the nuts keep a floor.
U_SW = 40;
SW_T = 4;
SW_HU = 18 + 3;  // measured inner edge to inner edge, plus the 3 mm hole
SW_HV = 23 + 3;

module bracket(W, label, sw = false) {
  vc = W / 2;
  hu = [5, L - 5];
  hv = [5, W - 5];
  difference() {
    union() {
      for (u = hu) translate([u - 5, 0, 0]) cube([10, W, T]);
      translate([0, vc - 8, 0]) cube([L, 16, T]);                   // spine
      translate([UC, vc, 0]) linear_extrude(T) frame2d();           // pattern frame
      translate([12, vc - 2, 0]) cube([L - 24, 4, T + S - 1]);      // rib, 1 mm under the board
      for (u = hu, v = hv) translate([u, v, 0]) cylinder(d = 8, h = T + S);
      if (sw) translate([U_SW - SW_HU / 2 - 6.5, vc - SW_HV / 2 - 6, 0])
        cube([SW_HU + 13, SW_HV + 12, SW_T]);                        // switch pad
    }
    for (u = hu, v = hv) translate([u, v, -1]) {
      cylinder(d = M2, h = T + S + 2);
      hexnut(M2NUT, 1 + 1.8);
    }
    if (sw) for (i = [-1, 1], j = [-1, 1]) translate([U_SW + i * SW_HU / 2, vc + j * SW_HV / 2, 0]) {
      translate([0, 0, -1]) cylinder(d = 2.9, h = SW_T + 2);
      translate([0, 0, SW_T - 2.2]) hexnut(5.3, 2.3);
    }
    translate([UC, vc, 0]) pattern_holes(T);
    translate([UC - 30, vc + 25, T - 0.6]) linear_extrude(1) text(label, size = 7);
    translate([14, vc + 3, T - 0.6]) linear_extrude(SW_T - T + 1) text("< FEED", size = 4);  // also on the pad
  }
}

// Pipe clamp: 36 mm sleeve (axis z) with a slot and a lug bolt at +y, pattern plate at -y.
module clamp(d, pt = PT, pb = PB) {
  ri = (d + 0.5) / 2;
  ro = ri + WALL;
  h = P / 2;
  difference() {
    union() {
      cylinder(r = ro, h = h);
      translate([0, -(ro - 1 + pt), P / 2]) rotate([-90, 0, 0]) pattern_plate(pt, pb);
      for (x = [-9, 2]) translate([x, ri + 1, 0]) cube([7, ro + 9 - ri, h]);  // 7 mm lugs: M2.5x20
    }
    translate([0, 0, -1]) cylinder(r = ri, h = h + 2);
    translate([-2, 0, -1]) cube([4, ro + 12, h + 2]);
    translate([-11, ro + 5, h / 2]) rotate([0, 90, 0]) cylinder(d = BOLT_D, h = 22);
    translate([9 - BOLT_NUT_H, ro + 5, h / 2]) rotate([0, 90, 0]) hexnut(BOLT_NUT, BOLT_NUT_H + 0.1);
  }
}

// Mast head: socket on the mast pipe top, pattern plate in front of it. Origin = lazy-susan
// centre: the plate face sits FACE behind it, so the patch face lands on the centre, and the
// pipe sits YS behind (mast_base matches).
RI_M = 10.25;
RO_M = RI_M + WALL_S;
YS = -(FACE + PT_S + RO_M - 1);
module mast_head() {
  difference() {
    union() {
      translate([0, -FACE, P / 2]) rotate([90, 0, 0]) pattern_plate(PT_S, PB_S);
      translate([0, YS, 0]) cylinder(r = RO_M, h = 44);
    }
    translate([0, YS, -1]) cylinder(r = RI_M, h = 41);            // pipe from below, cap on top
  }
}

// Mast base: on the lazy susan, centre hole over its centre mark, arrow towards the RX tripod.
DISC = 3;      // mast_base / pole_top disc, taped to the lazy susan
module mast_base() {
  D = 100;
  hs = 30;     // socket depth
  difference() {
    union() {
      cylinder(d = D, h = DISC);
      translate([0, YS, 0]) {
        cylinder(r = RO_M, h = DISC + hs);
        cylinder(r1 = RO_M + 4, r2 = RO_M, h = DISC + 6);
      }
    }
    translate([0, YS, DISC]) cylinder(r = RI_M, h = hs + 1);
    translate([0, 0, -1]) cylinder(d = 10, h = 10);
    for (a = [0, 180]) rotate(a) translate([31, 0, -1]) cylinder(d = 28, h = 10);  // lightening
    translate([0, 14, DISC - 0.6]) linear_extrude(1) polygon([[-6, 0], [6, 0], [0, 12]]);
    translate([0, 30, DISC - 0.6]) linear_extrude(1) text("RX", size = 7, halign = "center");
  }
}

// Pole top: lazy-susan platform over the 35 mm tripod pole, which rests on the ledge round
// the 29 mm hole. Prints disc-down.
module pole_top() {
  D = 140;
  ri = 17.75;
  ro = ri + WALL_S;
  hs = 25;
  difference() {
    union() {
      cylinder(d = D, h = DISC);
      cylinder(r = ro, h = DISC + hs);
      cylinder(r1 = ro + 4, r2 = ro, h = DISC + 6);
    }
    translate([0, 0, DISC]) cylinder(r = ri, h = hs + 1);
    translate([0, 0, -1]) cylinder(d = 29, h = 10);
    for (a = [0:45:315]) rotate(a) hull() {                      // lightening
      translate([32, 0, -1]) cylinder(d = 10, h = 10);
      translate([52, 0, -1]) cylinder(d = 24, h = 10);
    }
  }
}

if (part == "bracket_tx") bracket(71.628, "TX", sw = true);
if (part == "bracket_rx") bracket(45.728, "RX");
if (part == "clamp_20") clamp(20, PT_S, PB_S);
if (part == "clamp_35") clamp(35);
if (part == "mast_head") mast_head();
if (part == "mast_base") mast_base();
if (part == "pole_top") pole_top();

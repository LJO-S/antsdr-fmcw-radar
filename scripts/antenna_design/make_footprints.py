"""
KiCad 10 footprints from the simulated copper (Part K9 columns, K8 witness patch).

Writes antenna.pretty/<name>.kicad_mod and <name>.png. Copper = one custom SMD pad "1"
(F.Cu + F.Mask, filled polygon, small anchor inside the feed line). Footprint origin = board
edge on the feed line centre, x into the board, so place a column at y = +-12.95 (TX board).
Geometry comes from column_sim.column_copper() and its parse_args() defaults; every
column_sim option works here (--N, --L-list, --W-list, --lc-list, --tw, --tl, ...).

  python make_footprints.py                      # K9 baseline column + K8 patch
  python make_footprints.py --L-list 11.7,11.8,... --W-list ...   # taper variant
  python make_footprints.py --launch-gap 0       # line from the board edge (as simulated)

Own options: --launch-gap MM (default 5.0, the K8 boards), --lib-dir, --name, --only
column|patch. Tested by test_make_footprints.py.
"""
import argparse, datetime, os, sys

import numpy as np
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

import column_sim as cs

LAUNCH_GAP = 5.0  # K8: copper starts at x 65 on a board edge at 60 (SMA centre pad ends at 5.08)
ANCHOR = 0.2  # anchor square, mm
ANCHOR_X = 0.5  # anchor centre this far past the launch, inside the feed line
FMT = "{:.6f}".format


def k8_patch(h=cs.H, eps_r=cs.DESIGN_EPSR):
    """results.md K8 board (eps_r 4.4, h 1.5): 12-vertex polygon, mm."""
    fw = cs.msl_width(cs.FEED_R, eps_r, h)
    L, W, inset, gap, x0 = 11.842, 15.728, 3.70, 1.0, 20.0
    a, b, c = fw / 2, fw / 2 + gap, W / 2
    xf = x0 + inset
    up = [(0, a), (xf, a), (xf, b), (x0, b), (x0, c), (x0 + L, c)]
    return [(x, -y) for x, y in up] + [(x, y) for x, y in reversed(up)]


def trim_launch(poly, gap):
    """Move the start of the feed line (vertices at the minimum x) to x = gap."""
    if gap <= 0:
        return [tuple(p) for p in poly]
    x_min = min(x for x, _ in poly)
    nxt = min(x for x, _ in poly if x > x_min + 1e-9)
    assert gap < nxt - 1e-6, "launch gap {} reaches the first feature at x {}".format(gap, nxt)
    return [(gap if abs(x - x_min) < 1e-9 else x, y) for x, y in poly]


def clean(poly):
    """Round, drop repeated points and a closing duplicate (KiCad closes the polygon itself)."""
    out = []
    for x, y in poly:
        p = (round(float(x), 6) + 0.0, round(float(y), 6) + 0.0)
        if not out or p != out[-1]:
            out.append(p)
    if out[0] == out[-1]:
        out.pop()
    return out


def footprint_text(name, poly, lines, descr):
    ax = poly[0][0] + ANCHOR_X  # anchor on the line centre, just past the line start
    pts = " ".join("(xy {} {})".format(FMT(x - ax), FMT(y)) for x, y in poly)
    tx = max(x for x, _ in poly) / 2
    ty = max(abs(y) for _, y in poly) + 3
    def txt(s, y, layer="F.Fab"):
        return ('\t(fp_text user "{}"\n\t\t(at {} {} 0)\n\t\t(layer "{}")\n\t\t(effects\n'
                "\t\t\t(font\n\t\t\t\t(size 1 1)\n\t\t\t\t(thickness 0.15)\n\t\t\t)\n\t\t)\n\t)\n"
                ).format(s, FMT(tx), FMT(y), layer)
    prop = lambda k, v, y: ('\t(property "{}" "{}"\n\t\t(at 0 {} 0)\n\t\t(layer "F.Fab")\n\t\t(effects\n'
                            "\t\t\t(font\n\t\t\t\t(size 1 1)\n\t\t\t\t(thickness 0.15)\n\t\t\t)\n\t\t)\n\t)\n"
                            ).format(k, v, FMT(y))
    s = '(footprint "{}"\n\t(version 20260206)\n\t(generator "pcbnew")\n\t(generator_version "10.0")\n'.format(name)
    s += '\t(layer "F.Cu")\n\t(descr "{}")\n\t(tags "patch antenna 5.8GHz")\n'.format(descr)
    s += prop("Reference", "REF**", -ty) + prop("Value", name, ty)
    s += "\t(attr smd)\n"
    for i, t in enumerate(lines):
        s += txt(t, ty + 1.5 * (i + 1))
    s += ('\t(pad "1" smd custom\n\t\t(at {} 0)\n\t\t(size {} {})\n\t\t(layers "F.Cu" "F.Mask")\n'
          "\t\t(options\n\t\t\t(clearance outline)\n\t\t\t(anchor rect)\n\t\t)\n"
          "\t\t(primitives\n\t\t\t(gr_poly\n\t\t\t\t(pts {})\n\t\t\t\t(width 0)\n\t\t\t\t(fill yes)\n\t\t\t)\n\t\t)\n\t)\n"
          ")\n").format(FMT(ax), ANCHOR, ANCHOR, pts)
    return s


def preview(path, poly, name, launch):
    P = np.array(poly + poly[:1])
    fig, ax = plt.subplots(figsize=(12, 3.5))
    ax.fill(P[:, 0], P[:, 1], color="#c87533", ec="k", lw=0.6)
    ax.axvline(0, color="b", lw=0.8, ls="--")
    ax.plot([poly[0][0] + ANCHOR_X], [0], "k+")
    ax.set_aspect("equal")
    ax.set_xlabel("x, mm (0 = board edge)")
    ax.set_ylabel("y, mm")
    ax.set_title("{}  (launch gap {:g} mm; blue = board edge)".format(name, launch))
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)


def write(lib, name, poly, lines, descr, launch):
    os.makedirs(lib, exist_ok=True)
    path = os.path.join(lib, name + ".kicad_mod")
    with open(path, "w") as f:
        f.write(footprint_text(name, poly, lines, descr))
    preview(os.path.join(lib, name + ".png"), poly, name, launch)
    xs, ys = [p[0] for p in poly], [p[1] for p in poly]
    print("wrote {}: {} vertices, x {:.3f}..{:.3f} mm, y {:.3f}..{:.3f} mm".format(
        path, len(poly), min(xs), max(xs), min(ys), max(ys)))
    return path


def column_polygon(args):
    """The column polygon exactly as column_sim.main() builds it (before the launch trim)."""
    N = args.N
    L_list, W_list, _, _, _, tw, tl = cs.column_params(args)
    feed_w = cs.msl_width(cs.FEED_R, cs.DESIGN_EPSR, cs.H)
    polys, _ = cs.column_copper(N, L_list, W_list, args.wc, args.lc, tw, tl, feed_w, args.L,
                                lc_list=args.lc_list, feed_len=args.feed_len)
    return polys[0], L_list, W_list


def main(argv=None):
    mine = argparse.ArgumentParser(add_help=False)
    mine.add_argument("--launch-gap", type=float, default=LAUNCH_GAP)
    mine.add_argument("--lib-dir", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "antenna.pretty"))
    mine.add_argument("--name")
    mine.add_argument("--only", choices=["column", "patch"])
    m, rest = mine.parse_known_args(sys.argv[1:] if argv is None else argv)
    old = sys.argv
    sys.argv = [old[0]] + rest  # column_sim.parse_args reads sys.argv
    try:
        args = cs.parse_args()
    finally:
        sys.argv = old
    today = datetime.date.today().isoformat()
    g = m.launch_gap
    note = "launch gap {:g} mm".format(g)
    if g > 0:
        print("launch: trimmed {:g} mm off the feed line start (the SMA centre pad provides it)".format(g))
    else:
        print("launch: none, line starts at the board edge as simulated")
    if m.only != "patch":
        poly, L_list, W_list = column_polygon(args)
        uniform = not (args.L_list or args.W_list or args.lc_list)
        name = m.name or "K9_column_N{}_{}".format(args.N, "uniform" if uniform else "taper")
        poly = clean(trim_launch(poly, g))
        lines = ["{} {}".format(name, today), "eps_r {} h {} mm, {}".format(args.eps_r, cs.H, note),
                 "L {:g}..{:g} W {:g}..{:g} wc {:g} lc {:g}".format(
                     min(L_list), max(L_list), min(W_list), max(W_list), args.wc, args.lc)]
        write(m.lib_dir, name, poly, lines, "K9 series-fed 1x{} patch column, simulated copper".format(args.N), g)
    if m.only != "column":
        name = "K8_patch_witness"
        poly = clean(trim_launch(k8_patch(), g))
        lines = ["{} {}".format(name, today), "eps_r {} h {} mm, {}".format(cs.DESIGN_EPSR, cs.H, note),
                 "L 11.842 W 15.728 inset 3.70"]
        write(m.lib_dir, name, poly, lines, "K8 witness patch, results.md K8 board", g)


if __name__ == "__main__":
    main()

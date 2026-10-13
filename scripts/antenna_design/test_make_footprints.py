"""Board-free checks for make_footprints.py. Run: python test_make_footprints.py"""
import os, re, sys, tempfile

import make_footprints as mf
import column_sim as cs


def parse_sexp(text):
    toks = re.findall(r'"(?:[^"\\]|\\.)*"|\(|\)|[^\s()]+', text)
    pos = 0
    def rd():
        nonlocal pos
        t = toks[pos]; pos += 1
        if t != "(":
            return t[1:-1] if t.startswith('"') else t
        out = []
        while toks[pos] != ")":
            out.append(rd())
        pos += 1
        return out
    return rd()


def find(node, key):
    return [c for c in node if isinstance(c, list) and c and c[0] == key]


def read_poly(path):
    fp = parse_sexp(open(path).read())
    assert fp[0] == "footprint"
    assert find(fp, "version")[0][1] == "20260206"
    assert find(fp, "generator_version")[0][1] == "10.0"
    assert find(fp, "layer")[0][1] == "F.Cu" and find(fp, "attr")[0][1] == "smd"
    pad = find(fp, "pad")
    assert len(pad) == 1
    pad = pad[0]
    assert pad[1:4] == ["1", "smd", "custom"]
    assert find(pad, "layers")[0][1:] == ["F.Cu", "F.Mask"]
    opt = find(pad, "options")[0]
    assert find(opt, "anchor")[0][1] == "rect"
    assert find(pad, "size")
    gp = find(find(pad, "primitives")[0], "gr_poly")[0]
    assert find(gp, "fill")[0][1] == "yes" and find(gp, "width")
    ax = float(find(pad, "at")[0][1])
    return [(float(p[1]) + ax, float(p[2])) for p in find(find(gp, "pts")[0], "xy")]


def seg_cross(a, b, c, d):
    def o(p, q, r):
        return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])
    return o(a, b, c) * o(a, b, d) < -1e-12 and o(c, d, a) * o(c, d, b) < -1e-12


def simple(poly):
    n = len(poly)
    assert len(set(poly)) == n, "duplicate points"
    assert poly[0] != poly[-1], "polygon must not repeat its first point"
    for i in range(n):
        for j in range(i + 2, n):
            if i == 0 and j == n - 1:
                continue
            assert not seg_cross(poly[i], poly[(i + 1) % n], poly[j], poly[(j + 1) % n]), (i, j)


def close(a, b):
    assert len(a) == len(b), (len(a), len(b))
    for p, q in zip(a, b):
        assert abs(p[0] - q[0]) < 2e-6 and abs(p[1] - q[1]) < 2e-6, (p, q)


def expected_column(gap):
    old = sys.argv; sys.argv = [old[0]]
    try:
        args = cs.parse_args()
    finally:
        sys.argv = old
    poly, _, _ = mf.column_polygon(args)
    return mf.clean(poly), mf.clean(mf.trim_launch(poly, gap))


def check_pad_vs_pcb():
    """The user's own KiCad 10 board uses the same pad fields."""
    here = os.path.dirname(os.path.abspath(__file__))
    pcb = parse_sexp(open(os.path.join(here, "antenna-bringup", "antenna-bringup.kicad_pcb")).read())
    pad = find(find(pcb, "footprint")[0], "pad")[0]
    have = {c[0] for c in pad if isinstance(c, list)}
    assert {"at", "size", "layers"} <= have


def main():
    check_pad_vs_pcb()
    for gap in (0.0, 5.0):
        with tempfile.TemporaryDirectory() as d:
            mf.main(["--lib-dir", d, "--launch-gap", str(gap)])
            full, trimmed = expected_column(gap)
            col = read_poly(os.path.join(d, "K9_column_N8_uniform.kicad_mod"))
            close(col, trimmed)
            if gap:
                assert min(x for x, _ in col) == gap
                assert len(col) == len(full)
            else:
                close(col, full)
            simple(col)
            patch = read_poly(os.path.join(d, "K8_patch_witness.kicad_mod"))
            assert len(patch) == 12
            simple(patch)
            assert abs(max(x for x, _ in patch) - 31.842) < 1e-6
            assert abs(max(y for _, y in patch) - 7.864) < 1e-6
            assert os.path.getsize(os.path.join(d, "K8_patch_witness.png")) > 1000
    with tempfile.TemporaryDirectory() as d:  # taper variant through column_sim options
        mf.main(["--lib-dir", d, "--only", "column", "--N", "3", "--L-list", "11.7,11.8,11.7",
                 "--W-list", "14,15.7,14", "--lc-list", "12.8,12.9"])
        names = os.listdir(d)
        assert "K9_column_N3_taper.kicad_mod" in names, names
        simple(read_poly(os.path.join(d, "K9_column_N3_taper.kicad_mod")))
    print("ALL OK")


if __name__ == "__main__":
    main()

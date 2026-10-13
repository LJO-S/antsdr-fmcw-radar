# -*- coding: utf-8 -*-
"""Board-free tests for k8_analyze.py. Run: python test_k8_analyze.py"""

import os, tempfile
import numpy as np
import k8_analyze as k8

HERE = os.path.dirname(os.path.abspath(__file__))


def test_touchstone_roundtrip():
    rng = np.random.default_rng(1)
    f = np.linspace(5e9, 6.5e9, 11)
    s = 0.5 * (rng.standard_normal((11, 2, 2)) + 1j * rng.standard_normal((11, 2, 2)))
    s1 = s[:, :1, :1]
    with tempfile.TemporaryDirectory() as d:
        for fmt in ("RI", "MA", "DB"):
            for unit in ("GHZ", "MHZ"):
                for arr, ext in ((s, "s2p"), (s1, "s1p")):
                    path = os.path.join(d, "t.{}".format(ext))
                    k8.write_touchstone(path, f, arr, fmt, unit)
                    f2, s2 = k8.read_touchstone(path)
                    assert np.allclose(f2, f, rtol=1e-8), (fmt, unit)
                    assert np.allclose(s2, arr, atol=1e-7), (fmt, unit, ext)
    # hand-written: KHZ, comments, S21 in column 2 of the row
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "h.s2p")
        with open(path, "w") as fh:
            fh.write("! c\n# KHZ S RI R 50 ! x\n5000000 0.1 0.2 0.3 0.4 0 0 0 0\n")
        f2, s2 = k8.read_touchstone(path)
        assert f2[0] == 5e9 and np.isclose(s2[0, 0, 0], 0.1 + 0.2j) and np.isclose(s2[0, 1, 0], 0.3 + 0.4j)


def abcd_to_s(a, b, c, d, z0=50.0):
    den = a + b / z0 + c * z0 + d
    return (a + b / z0 - c * z0 - d) / den, 2 / den


def synth_line(f, length, eps_eff, alpha_np_m, c_pf=0.05):
    """Matched 50 Ohm lossy line with a shunt C at each end -> (n, 2, 2) S."""
    gl = (alpha_np_m + 1j * 2 * np.pi * f * np.sqrt(eps_eff) / k8.C0) * length
    z0 = 50.0
    y = 1j * 2 * np.pi * f * c_pf * 1e-12
    s = np.zeros((len(f), 2, 2), complex)
    for i in range(len(f)):
        m = np.eye(2, dtype=complex)
        cap = np.array([[1, 0], [y[i], 1]])
        ln = np.array([[np.cosh(gl[i]), z0 * np.sinh(gl[i])], [np.sinh(gl[i]) / z0, np.cosh(gl[i])]])
        m = cap @ ln @ cap
        s11, s21 = abcd_to_s(m[0, 0], m[0, 1], m[1, 0], m[1, 1])
        s[i] = [[s11, s21], [s21, s11]]
    return s


def test_line_pair():
    f = np.linspace(5e9, 6.5e9, 301)
    eps_eff = 3.2 + 0.15 * (f - 5.8e9) / 1e9
    alpha_db_mm = 0.0145
    alpha = alpha_db_mm * 1000 / 8.686
    # shunt C of 0.35 pF per launch: S11 about -10 dB, the plain S21 difference is far off
    ss = synth_line(f, 25e-3, eps_eff, alpha, 0.35)
    sl = synth_line(f, 95e-3, eps_eff, alpha, 0.35)
    with tempfile.TemporaryDirectory() as d:  # through the files, as in real use
        k8.write_touchstone(os.path.join(d, "s.s2p"), f, ss, "MA", "MHZ")
        k8.write_touchstone(os.path.join(d, "l.s2p"), f, sl, "DB", "GHZ")
        f1, s1 = k8.read_touchstone(os.path.join(d, "s.s2p"))
        _, l1 = k8.read_touchstone(os.path.join(d, "l.s2p"))
    r = k8.analyze_lines(f1, s1, l1, 70.0, 2.868, 1.5)
    assert r["s11_worst_short"] > -13, r["s11_worst_short"]  # launches really are bad
    assert abs(r["eps_eff_58"] - 3.2) < 0.005, r["eps_eff_58"]
    assert abs(r["att_mean"] - alpha_db_mm) < 0.02 * alpha_db_mm, r["att_mean"]
    assert abs(r["lambda_g_mm"] - k8.C0 / 5.8e9 / np.sqrt(3.2) * 1e3) < 0.1


def test_reader_rejects():
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "bad.s2p")
        with open(p, "w") as fh:
            fh.write("# HZ S RI R 50\n5e9 0 0 0 0\n6e9 0 0 0 0\n")  # 5 columns
        try:
            k8.read_touchstone(p)
            raise SystemExit("5-column .s2p was accepted")
        except AssertionError:
            pass
        p = os.path.join(d, "y.s1p")
        with open(p, "w") as fh:
            fh.write("# HZ Y RI R 50\n5e9 0 0\n")
        try:
            k8.read_touchstone(p)
            raise SystemExit("Y parameters were accepted")
        except AssertionError:
            pass


def test_sim_tan_d():
    """PEC sim pair (true tan d 0.020, no conductor loss): alpha_c = 0 must give 0.020."""
    sim = os.path.join(HERE, "line_pair_sim")
    table = k8.sim_table(sim)
    pair = k8.sim_pair(sim, 4.4, table["ref"]) if table else None
    if pair is None:
        print("  (skipped: no line_pair_sim files)")
        return
    f, ss, sl = pair
    r = k8.analyze_lines(f, ss, sl, 70.0, 2.868, 1.5, table=table)
    assert abs(r["table"]["corrected"] - 4.4) < 0.03, r["table"]
    td = k8.tan_d_from_loss(r["att_mean"], k8.F0, 4.4, r["eps_eff_58"], 0.0, table["q"])
    assert abs(td - 0.020) < 0.001, td
    assert r["tan_d"][0] < td  # with conductor loss subtracted it is lower


def test_patch():
    d = np.load(os.path.join(HERE, "sweep", "d4.40_e4.50_r1.5.npz"))
    f = d["f"]
    s = (10 ** (d["s11_dB"] / 20)).astype(complex).reshape(-1, 1, 1)
    with tempfile.TemporaryDirectory() as t:
        path = os.path.join(t, "p.s1p")
        k8.write_touchstone(path, f, s, "RI", "GHZ")
        f2, s2 = k8.read_touchstone(path)
    r = k8.analyze_patch(f2, s2[:, 0, 0])
    assert abs(r["eps_r_table"] - 4.50) < 0.01, r["eps_r_table"]
    assert abs(r["eps_r_formula"] - 4.50) < 0.15, r["eps_r_formula"]
    assert r["warn"] is None
    # off-node: the 4.40 curve shifted so its minimum sits halfway to the 4.50 one
    tab = dict(k8.sweep_table())
    d = np.load(os.path.join(HERE, "sweep", "d4.40_e4.40_r1.5.npz"))
    shift = 0.5 * (tab[4.4] + tab[4.5]) - tab[4.4]
    s = (10 ** (d["s11_dB"] / 20)).astype(complex)
    r = k8.analyze_patch(d["f"] + shift, s)
    assert abs(r["eps_r_table"] - 4.45) < 0.01, r["eps_r_table"]
    # outside the table: extrapolated linearly with a warning, not clamped
    r = k8.analyze_patch(d["f"] - 0.2e9, s)
    assert r["warn"] and r["eps_r_table"] > 4.6, r["eps_r_table"]


def test_microstrip():
    w, h = 2.868e-3, 1.5e-3
    z = k8.hj_z0(w, h, 4.4)
    assert abs(z - 50) < 1.0, z
    # Simple_Patch_Antenna.msl_width (Pozar 3.197), copied: it sized the line for 50 Ohm
    er, z0 = 4.4, 50.0
    A = z0 / 60 * np.sqrt((er + 1) / 2) + (er - 1) / (er + 1) * (0.23 + 0.11 / er)
    w_h = 8 * np.exp(A) / (np.exp(2 * A) - 2)
    assert abs(w_h * h - w) < 0.01e-3, w_h * h
    e0 = k8.hj_eps_eff(w, h, 4.4)
    ef = k8.kj_eps_eff(5.8e9, w, h, 4.4)
    assert 3.2 < e0 < 3.5 and e0 < ef < 4.4, (e0, ef)
    assert abs(ef - 3.4747) < 0.001, ef  # pinned: fn = f*h in GHz*mm
    assert abs(k8.eps_r_from_eps_eff(ef, 5.8e9, w, h) - 4.4) < 1e-6


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
    print("all passed")

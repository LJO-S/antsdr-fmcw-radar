# -*- coding: utf-8 -*-
"""
TODO: remove me after K8
K8 measurement analysis (Part K8, guide 9.3)

Reads the LiteVNA Touchstone files of the two calibration boards and backs out the board:
  patch board: S11 minimum -> eps_r, via the sweep table in sweep/ (sweep_eps_r.py)
  line pair:   S21 phase difference -> eps_eff (launches cancel), loss difference -> dB/mm
               and an upper bound on tan d, lambda_g at 5.8 GHz (K9's element pitch)
  both:        the line pair also yields the launch, which is removed from the patch S11
               (same sweep needed); that resonance replaces the 25 MHz SMA correction
Only S11 and S21 are used (a LiteVNA .s2p holds S12/S22 as zeros). Any subset of inputs works.

    python k8_analyze.py --patch patch.s1p
    python k8_analyze.py --short short.s2p --long long.s2p [--dl 70.0]
    python k8_analyze.py --patch p.s1p --short s.s2p --long l.s2p --out DIR
"""

import argparse, glob, os, re
import numpy as np
import matplotlib

C0 = 299792458.0
BAND = (5.75e9, 5.85e9)
F0 = 5.8e9
DESIGN = 4.4  # eps_r of the geometry in the sweep table
EPS_EFF_PRIOR = (
    2.5,
    4.5,
)  # physical window for FR4 microstrip, resolves the 2 pi wraps
# conductor loss of the 2.868 mm line on 1.5 mm FR4, smooth copper: openEMS copper minus PEC
# (0.63 dB/m, Pucel 0.62). Roughness and HASL on a 1 oz board multiply it, an estimate.
ALPHA_C_SMOOTH_DB_M = 0.63
ROUGH_FACTOR = (1.7, 2.3)
SMA_SHIFT = 25e6  # the edge SMA moves the S11 minimum up vs the table's ideal port (sma_launch_sim.py)
HERE = os.path.dirname(os.path.abspath(__file__))

# --------------------
# Touchstone v1
# --------------------
UNITS = {"HZ": 1.0, "KHZ": 1e3, "MHZ": 1e6, "GHZ": 1e9}


def read_touchstone(path):
    """-> (f in Hz, S of shape (n, p, p)); p from the extension. 2-port order S11 S21 S12 S22."""
    p = int(re.search(r"\.s(\d+)p$", path.lower()).group(1))
    assert p in (1, 2), "only .s1p and .s2p"
    unit, fmt = 1e9, "MA"  # v1 defaults when the option line is bare
    per = 1 + 2 * p * p
    rows = []
    with open(path) as fh:
        for line in fh:
            line = line.split("!")[0].strip()
            if not line:
                continue
            if line.startswith("#"):
                t = line[1:].upper().split()
                for i, w in enumerate(t):
                    if w in UNITS:
                        unit = UNITS[w]
                    elif w in ("RI", "MA", "DB"):
                        fmt = w
                    elif w in ("Y", "Z", "G", "H"):
                        raise AssertionError(
                            "{}: parameter type {} is not S".format(path, w)
                        )
                    elif w == "R" and i + 1 < len(t):
                        assert (
                            float(t[i + 1]) == 50.0
                        ), "reference impedance is not 50 Ohm"
                continue
            row = [float(x) for x in line.split()]
            assert len(row) == per, "{}: {} values per row, a .s{}p needs {}".format(
                path, len(row), p, per
            )
            rows.append(row)
    a = np.array(rows)
    f = a[:, 0] * unit
    x, y = a[:, 1::2], a[:, 2::2]
    if fmt == "RI":
        s = x + 1j * y
    elif fmt == "MA":
        s = x * np.exp(1j * np.deg2rad(y))
    else:
        s = 10 ** (x / 20) * np.exp(1j * np.deg2rad(y))
    if p == 2:  # file order S11 S21 S12 S22 -> [row, col]
        s = s[:, [0, 2, 1, 3]]
    return f, s.reshape(-1, p, p)


def write_touchstone(path, f, s, fmt="RI", unit="GHZ"):
    """Writer for the tests (and for hand-made files). s shape (n, p, p)."""
    p = s.shape[1]
    flat = s.reshape(len(f), -1)
    if p == 2:
        flat = flat[:, [0, 2, 1, 3]]
    with open(path, "w") as fh:
        fh.write("! written by k8_analyze.py\n# {} S {} R 50\n".format(unit, fmt))
        for fi, row in zip(f, flat):
            out = ["{:.9e}".format(fi / UNITS[unit])]
            for v in row:
                if fmt == "RI":
                    out += ["{:.9e}".format(v.real), "{:.9e}".format(v.imag)]
                elif fmt == "MA":
                    out += [
                        "{:.9e}".format(abs(v)),
                        "{:.9e}".format(np.degrees(np.angle(v))),
                    ]
                else:
                    out += [
                        "{:.9e}".format(20 * np.log10(abs(v))),
                        "{:.9e}".format(np.degrees(np.angle(v))),
                    ]
            fh.write(" ".join(out) + "\n")


# --------------------
# Microstrip model
# --------------------
def hj_eps_eff(w, h, eps_r):
    """Static eps_eff, Hammerstad-Jensen 1980 (zero thickness copper)."""
    u = w / h
    a = (
        1
        + np.log((u**4 + (u / 52) ** 2) / (u**4 + 0.432)) / 49
        + np.log(1 + (u / 18.1) ** 3) / 18.7
    )
    b = 0.564 * ((eps_r - 0.9) / (eps_r + 3)) ** 0.053
    return (eps_r + 1) / 2 + (eps_r - 1) / 2 * (1 + 10 / u) ** (-a * b)


def hj_z0(w, h, eps_r):
    """Static Z0 in Ohm, Hammerstad-Jensen 1980: Z0 = Z0(air) / sqrt(eps_eff)."""
    u = w / h
    fu = 6 + (2 * np.pi - 6) * np.exp(-((30.666 / u) ** 0.7528))
    z_air = 60 * np.log(fu / u + np.sqrt(1 + (2 / u) ** 2))
    return z_air / np.sqrt(hj_eps_eff(w, h, eps_r))


def kj_eps_eff(f, w, h, eps_r):
    """eps_eff(f) with Kirschning-Jansen 1982 dispersion on the H-J static value.
    Their fn is f*h in GHz*mm (Qucs msline.cpp), also in P3."""
    u = w / h
    fn = np.asarray(f) / 1e9 * h * 1e3
    e0 = hj_eps_eff(w, h, eps_r)
    p1 = (
        0.27488
        + u * (0.6315 + 0.525 / (1 + 0.0157 * fn) ** 20)
        - 0.065683 * np.exp(-8.7513 * u)
    )
    p2 = 0.33622 * (1 - np.exp(-0.03442 * eps_r))
    p3 = 0.0363 * np.exp(-4.6 * u) * (1 - np.exp(-((fn / 38.7) ** 4.97)))
    p4 = 1 + 2.751 * (1 - np.exp(-((eps_r / 15.916) ** 8)))
    p = p1 * p2 * ((0.1844 + p3 * p4) * fn) ** 1.5763
    return eps_r - (eps_r - e0) / (1 + p)


def eps_r_from_eps_eff(eps_eff, f, w, h):
    """Invert kj_eps_eff for eps_r (bisection; eps_eff rises with eps_r)."""
    lo, hi = 1.5, 12.0
    for _ in range(60):
        mid = 0.5 * (lo + hi)
        if kj_eps_eff(f, w, h, mid) < eps_eff:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def tan_d_from_loss(alpha_db_mm, f, eps_r, eps_eff, alpha_c_db_m, q=None):
    """tan d from the measured total loss minus a conductor loss estimate.
    alpha_d = k0 eps_r tan_d q / (2 sqrt(eps_eff)), q = d eps_eff / d eps_r (the filling
    factor; from the simulated table, else Pozar 3.8's (eps_eff - 1) / (eps_r - 1))."""
    if q is None:
        q = (eps_eff - 1) / (eps_r - 1)
    alpha_d = alpha_db_mm * 1000 - alpha_c_db_m  # dB/m
    alpha_d /= 20 / np.log(10)  # Np/m
    k0 = 2 * np.pi * f / C0
    return alpha_d * 2 * np.sqrt(eps_eff) / (k0 * eps_r * q)


def interp_ext(x, xp, fp):
    """np.interp on sorted xp, but the end segments are extended linearly. -> (value, inside)."""
    xp, fp = np.asarray(xp, float), np.asarray(fp, float)
    o = np.argsort(xp)
    xp, fp = xp[o], fp[o]
    inside = xp[0] <= x <= xp[-1]
    if x < xp[0]:
        return fp[0] + (x - xp[0]) * (fp[1] - fp[0]) / (xp[1] - xp[0]), inside
    if x > xp[-1]:
        return fp[-1] + (x - xp[-1]) * (fp[-1] - fp[-2]) / (xp[-1] - xp[-2]), inside
    return float(np.interp(x, xp, fp)), inside


# --------------------
# Patch
# --------------------
def parabolic_min(f, y, i):
    """Vertex of the parabola through points i-1, i, i+1 -> (f, y)."""
    if i == 0 or i == len(f) - 1:
        return f[i], y[i]
    y0, ym, yp = y[i], y[i - 1], y[i + 1]
    den = ym + yp - 2 * y0
    if den <= 0:  # not a concave-up dip: keep the sample
        return f[i], y0
    d = 0.5 * (ym - yp) / den
    return f[i] + d * (f[i + 1] - f[i - 1]) / 2, y0 - 0.25 * (ym - yp) * d


def s11_resonance(f, s11_db, window=(5.0e9, 6.5e9)):
    """-> dict(f_res, depth, band (lo, hi) or None) of the S11 minimum inside the window."""
    idx = np.where((f >= window[0]) & (f <= window[1]))[0]
    i = idx[np.argmin(s11_db[idx])]
    f_res, _ = parabolic_min(
        f, 10 ** (s11_db / 10), i
    )  # fit |S11|^2: a dip is parabolic there, not in dB
    lo = hi = i
    while lo > 0 and s11_db[lo - 1] < -10:
        lo -= 1
    while hi < len(f) - 1 and s11_db[hi + 1] < -10:
        hi += 1
    return dict(
        f_res=f_res, depth=s11_db[i], band=(f[lo], f[hi]) if s11_db[i] < -10 else None
    )


def sweep_table(sweep_dir=None):
    """Sorted (eps_r, f_res) from sweep/d4.40_e*_r*.npz; eps_r is in the file name."""
    sweep_dir = sweep_dir or os.path.join(HERE, "sweep")
    rows = []
    for p in glob.glob(os.path.join(sweep_dir, "d{:.2f}_e*_r*.npz".format(DESIGN))):
        eps = float(re.search(r"_e(\d+\.\d+)_", os.path.basename(p)).group(1))
        d = np.load(p)
        rows.append((eps, s11_resonance(d["f"], d["s11_dB"])["f_res"]))
    assert rows, "no sweep/*.npz found"
    return sorted(rows)


def analyze_patch(f, s11, sweep_dir=None):
    s11_db = 20 * np.log10(np.abs(s11) + 1e-12)
    r = s11_resonance(f, s11_db)
    r["worst"] = s11_db[(f >= BAND[0]) & (f <= BAND[1])].max()
    tab = np.array(sweep_table(sweep_dir))
    eps, f_tab = tab[:, 0], tab[:, 1]
    r["eps_r_table"], inside = interp_ext(r["f_res"], f_tab, eps)
    r["eps_r_sma"] = interp_ext(r["f_res"] - SMA_SHIFT, f_tab, eps)[0]
    r["warn"] = None
    if not inside:
        r["warn"] = (
            "{:.3f} GHz is outside the table ({:.3f}-{:.3f} GHz): eps_r is an extrapolation".format(
                r["f_res"] / 1e9, f_tab.min() / 1e9, f_tab.max() / 1e9
            )
        )
    f_sim = f_tab[np.argmin(np.abs(eps - DESIGN))]
    r["eps_r_formula"] = DESIGN * (f_sim / r["f_res"]) ** 2
    r["s11_db"] = s11_db
    return r


# --------------------
# Line pair
# --------------------
def resolve_wraps(f, dphi0, dl):
    """Add n*2 pi to an unwrapped phase so eps_eff lands in the physical window (the sweep
    starts at 5 GHz, not DC). Asserts exactly one n works. -> (phase, n)."""
    ok = []
    for n in range(-6, 7):
        d = dphi0 + 2 * np.pi * n
        e = np.median((C0 * d / (2 * np.pi * f * dl)) ** 2)
        if d[0] > 0 and EPS_EFF_PRIOR[0] <= e <= EPS_EFF_PRIOR[1]:
            ok.append(n)
    assert (
        len(ok) == 1
    ), "wrap count not unique under the eps_eff prior: candidates {}".format(ok)
    return dphi0 + 2 * np.pi * ok[0], ok[0]


def t_matrix(s):
    """T of a reciprocal two-port with identical ports (S12 = S21, S22 = S11), (n, 2, 2).
    The LiteVNA does not measure S22; the board is symmetric. Cascade = matrix product.
    """
    s11, s21 = s[:, 0, 0], s[:, 1, 0]
    t = np.empty((len(s11), 2, 2), complex)
    t[:, 0, 0], t[:, 0, 1], t[:, 1, 0], t[:, 1, 1] = s21**2 - s11**2, s11, -s11, 1
    return t / s21[:, None, None]


def line_gamma(f, s_short, s_long, dl):
    """Propagation constant of the length difference dl (m), launches cancelled exactly:
    T_long = T_A T(l+dl) T_B and T_short = T_A T(l) T_B (same launches), so
    T_long inv(T_short) = T_A T(dl) inv(T_A) has eigenvalues exp(+-gamma dl).
    The forward one is the eigenvalue nearest S21_long / S21_short (the same quantity with
    the launches not cancelled). Picking the smaller magnitude fails where the loss is
    small next to the measurement error: the pair swaps and the phase jumps.
    -> (alpha Np/m, unwrapped beta*dl without the 2 pi integer)."""
    m = t_matrix(s_long) @ np.linalg.inv(t_matrix(s_short))
    ev = np.linalg.eigvals(m)
    ref = s_long[:, 1, 0] / s_short[:, 1, 0]
    k = np.argmin(np.abs(ev - ref[:, None]), axis=1)
    lam = ev[np.arange(len(ev)), k]
    return -np.log(np.abs(lam)) / dl, np.unwrap(-np.angle(lam))


def line_eps_eff(f, s_short, s_long, dl_mm):
    """Eigenvalue method -> dict(eps_eff(f), alpha_db_mm(f), n)."""
    dl = dl_mm * 1e-3
    alpha, ph0 = line_gamma(f, s_short, s_long, dl)
    ph, n = resolve_wraps(f, ph0, dl)
    return dict(
        eps_eff=(C0 * ph / (2 * np.pi * f * dl)) ** 2,
        phase=ph,
        n=n,
        att=alpha * (20 / np.log(10)) / 1000,
    )


def launch_box(f, s_short, s_long, dl_mm, l_short_mm):
    """Port-1 launch A (coax -> microstrip, reference plane at the board edge) from the line
    pair, as reciprocal S-parameters (a11, a22, a21^2), each (n,).
    The eigenvectors of T_long inv(T_short) = T_A T(dl) inv(T_A) are the columns of T_A up
    to scale: they give a11 and a11 - a21^2 / a22. The short line's S11, with its far launch
    taken as A mirrored, gives a22^2. a22 keeps its sqrt branch by continuity, and the sign
    pair (a22, a21^2) is set so a21^2 -> +1 at DC (a short launch)."""
    r = line_eps_eff(f, s_short, s_long, dl_mm)
    gamma = r["att"] * 1000 / (20 / np.log(10)) + 1j * r["phase"] / (dl_mm * 1e-3)
    m = t_matrix(s_long) @ np.linalg.inv(t_matrix(s_short))
    ev, vec = np.linalg.eig(m)
    ref = s_long[:, 1, 0] / s_short[:, 1, 0]
    k = np.argmin(np.abs(ev - ref[:, None]), axis=1)  # forward, as in line_gamma
    i = np.arange(len(f))
    c1, c2 = vec[i, :, k], vec[i, :, 1 - k]  # columns 1 and 2 of T_A, up to scale
    a11 = c2[:, 0] / c2[:, 1]
    r1 = c1[:, 0] / c1[:, 1]  # = a11 - a21^2 / a22
    s11 = s_short[:, 0, 0]
    e = np.exp(-2 * gamma * l_short_mm * 1e-3)
    a22 = np.sqrt((s11 - a11) / ((s11 - r1) * e))
    for j in range(1, len(f)):
        if abs(a22[j] - a22[j - 1]) > abs(a22[j] + a22[j - 1]):
            a22[j] = -a22[j]
    a21sq = a22 * (a11 - r1)
    if np.cos(np.polyfit(f, np.unwrap(np.angle(a21sq)), 1)[1]) < 0:
        a22, a21sq = -a22, -a21sq
    return a11, a22, a21sq


def deembed(s11, a11, a22, a21sq):
    """S11 measured through the launch -> S11 at the board edge (the sim's port)."""
    d = s11 - a11
    return d / (a21sq + a22 * d)


def sim_pair(sim_dir, eps, ref, thick=False):
    """Simulated PEC line pair from line_pair_sim.py -> (f, S short, S long, or None if absent)."""
    out = []
    for length in (25, 95):
        p = os.path.join(
            sim_dir,
            "L{}_e{:.2f}_pec_r{:g}{}.npz".format(
                length, eps, ref, "_t35" if thick else ""
            ),
        )
        if not os.path.exists(p):
            return None
        d = np.load(p)
        s = np.zeros((len(d["f"]), 2, 2), complex)
        s[:, 0, 0] = s[:, 1, 1] = d["s11"]
        s[:, 1, 0] = s[:, 0, 1] = d["s21"]
        out.append((d["f"], s))
    return out[0][0], out[0][1], out[1][1]


def sim_table(sim_dir=None, dl_mm=70.0):
    """eps_eff at 5.8 GHz per simulated eps_r, through the same line_eps_eff as the
    measurement. Refine 1.5 if it has >= 2 eps_r values, else refine 1.
    -> dict(ref, eps, eps_eff, q, delta) or None; delta = thick - zero-thickness at eps_r 4.4.
    """
    sim_dir = sim_dir or os.path.join(HERE, "line_pair_sim")
    for ref in (1.5, 1):
        eps, ee = [], []
        for p in sorted(
            glob.glob(os.path.join(sim_dir, "L25_e*_pec_r{:g}.npz".format(ref)))
        ):
            e = float(re.search(r"_e(\d+\.\d+)_", os.path.basename(p)).group(1))
            pair = sim_pair(sim_dir, e, ref)
            if pair:
                f, ss, sl = pair
                eps.append(e)
                ee.append(
                    float(np.interp(F0, f, line_eps_eff(f, ss, sl, dl_mm)["eps_eff"]))
                )
        if len(eps) >= 2:
            q = float(np.polyfit(eps, ee, 1)[0])
            delta = None
            if 4.4 in eps:
                pair = sim_pair(sim_dir, 4.4, ref, thick=True)
                if pair:
                    f, ss, sl = pair
                    delta = (
                        float(
                            np.interp(F0, f, line_eps_eff(f, ss, sl, dl_mm)["eps_eff"])
                        )
                        - ee[eps.index(4.4)]
                    )
            return dict(
                ref=ref, eps=np.array(eps), eps_eff=np.array(ee), q=q, delta=delta
            )
    return None


def analyze_lines(f, s_short, s_long, dl_mm, w_mm, h_mm, eps_r_patch=None, table=None):
    """s_short / s_long are (n, 2, 2). Returns a dict of results and curves.
    Primary eps_eff and dB/mm: eigenvalue method (line_eps_eff). The plain S21 difference is
    kept as a cross-check (the launches do not cancel in |S21|)."""
    dl = dl_mm * 1e-3
    inb = (f >= BAND[0]) & (f <= BAND[1])
    assert inb.sum() >= 3, "fewer than 3 points in 5.75-5.85 GHz"
    r = line_eps_eff(f, s_short, s_long, dl_mm)
    eps_eff, att = r["eps_eff"], r["att"]
    res = dict(f=f, eps_eff=eps_eff, n=r["n"], att=att)
    res["eps_eff_58"] = float(np.interp(F0, f, eps_eff))
    res["eps_eff_mean"] = float(eps_eff[inb].mean())
    res["eps_eff_ripple"] = float((eps_eff[inb].max() - eps_eff[inb].min()) / 2)
    res["lambda_g_mm"] = C0 / F0 / np.sqrt(res["eps_eff_58"]) * 1e3
    res["ng"] = float(np.polyfit(f, r["phase"], 1)[0] * C0 / (2 * np.pi * dl))
    res["att_mean"] = float(att[inb].mean())
    res["att_ripple"] = float((att[inb].max() - att[inb].min()) / 2)
    res["s11_worst_short"] = float(20 * np.log10(np.abs(s_short[inb, 0, 0])).max())
    res["s11_worst_long"] = float(20 * np.log10(np.abs(s_long[inb, 0, 0])).max())
    # cross-check: S21 phase and |S21| differences (launches only cancel approximately)
    s21s, s21l = s_short[:, 1, 0], s_long[:, 1, 0]
    ph, _ = resolve_wraps(f, np.unwrap(np.angle(s21s * np.conj(s21l))), dl)
    res["eps_eff_simple"] = float(
        np.interp(F0, f, (C0 * ph / (2 * np.pi * f * dl)) ** 2)
    )
    res["att_simple"] = float(
        ((20 * np.log10(np.abs(s21s)) - 20 * np.log10(np.abs(s21l))) / dl_mm)[
            inb
        ].mean()
    )
    w, h = w_mm * 1e-3, h_mm * 1e-3
    res["z0"] = float(hj_z0(w, h, 4.4))
    res["eps_r_kj"] = eps_r_from_eps_eff(res["eps_eff_58"], F0, w, h)
    res["table"] = None
    q = None
    if table:  # same code for the table and the measurement; thickness corrected
        delta = table["delta"] or 0.0
        lookup = res["eps_eff_58"] - delta
        raw, inside = interp_ext(res["eps_eff_58"], table["eps_eff"], table["eps"])
        cor, inside = interp_ext(lookup, table["eps_eff"], table["eps"])
        res["table"] = dict(
            raw=raw,
            corrected=cor,
            inside=inside,
            delta=table["delta"],
            ref=table["ref"],
            q=table["q"],
        )
        q = table["q"]
    res["eps_r_board"] = res["table"]["corrected"] if table else res["eps_r_kj"]
    # tan d range: conductor loss low/high (smooth copper times the roughness/HASL factor)
    res["tan_d"] = [
        tan_d_from_loss(
            res["att_mean"],
            F0,
            res["eps_r_board"],
            res["eps_eff_58"],
            ALPHA_C_SMOOTH_DB_M * k,
            q,
        )
        for k in ROUGH_FACTOR
    ]
    res["model_line"] = kj_eps_eff(f, w, h, res["eps_r_kj"])
    if eps_r_patch is not None:
        res["eps_eff_pred"] = float(kj_eps_eff(F0, w, h, eps_r_patch))
        res["model_patch"] = kj_eps_eff(f, w, h, eps_r_patch)
    return res


# --------------------
# Report
# --------------------
def report(patch, lines, args):
    t = ["K8 measurement analysis", ""]
    if patch:
        t += [
            "Patch board",
            "  S11 minimum:        {:.4f} GHz, {:.1f} dB".format(
                patch["f_res"] / 1e9, patch["depth"]
            ),
            "  -10 dB band:        "
            + (
                "{:.3f}-{:.3f} GHz".format(*np.divide(patch["band"], 1e9))
                if patch["band"]
                else "none"
            ),
            "  worst S11 5.75-5.85 GHz: {:.1f} dB".format(patch["worst"]),
            "  eps_r (table):      {:.3f}".format(patch["eps_r_table"]),
            "  eps_r (SMA-corr.):  {:.3f}   (f_min - 25 MHz, the simulated launch, +-10 MHz ~ +-0.02)".format(
                patch["eps_r_sma"]
            ),
            "  eps_r (formula):    {:.3f}   (4.4 * (f_sim / f_meas)^2, cross-check)".format(
                patch["eps_r_formula"]
            ),
        ]
        if patch["warn"]:
            t.append("  WARNING: " + patch["warn"])
        de = patch.get("deembedded")
        if de:
            t += [
                "  launch removed with the line pair (assumes the same launch on both boards):",
                "    S11 minimum:      {:.4f} GHz, {:.1f} dB   (launch shift {:+.0f} MHz)".format(
                    de["f_res"] / 1e9, de["depth"], (patch["f_res"] - de["f_res"]) / 1e6
                ),
                "    -10 dB band:      "
                + (
                    "{:.3f}-{:.3f} GHz".format(*np.divide(de["band"], 1e9))
                    if de["band"]
                    else "none"
                ),
                "    eps_r (table):    {:.3f}   (the patch value to use)".format(
                    de["eps_r_table"]
                ),
                "    max |S11|:        {:+.1f} dB   (> 0 dB: the two boards' launches differ)".format(
                    de["s11_db"].max()
                ),
            ]
            if de["warn"]:
                t.append("    WARNING: " + de["warn"])
        t.append("")
    if lines:
        t += [
            "Line pair (dL {:.1f} mm, W {:.3f} mm, h {:.2f} mm)".format(
                args.dl, args.width, args.h
            ),
            "  eps_eff at 5.8 GHz: {:.3f}   (eigenvalue method, launches cancelled; 2 pi wraps added: {})".format(
                lines["eps_eff_58"], lines["n"]
            ),
            "  eps_eff 5.75-5.85:  {:.3f} +- {:.3f} (ripple)".format(
                lines["eps_eff_mean"], lines["eps_eff_ripple"]
            ),
            "  group index:        {:.3f}".format(lines["ng"]),
            "  lambda_g at 5.8 GHz: {:.2f} mm   (K9 element pitch)".format(
                lines["lambda_g_mm"]
            ),
            "  loss:               {:.4f} +- {:.4f} dB/mm (mean over band, ripple)".format(
                lines["att_mean"], lines["att_ripple"]
            ),
            "  cross-check, plain S21 difference: eps_eff {:.3f}, {:.4f} dB/mm (launches do not cancel in |S21|)".format(
                lines["eps_eff_simple"], lines["att_simple"]
            ),
            "  worst S11 5.75-5.85 GHz: short {:.1f} dB, long {:.1f} dB".format(
                lines["s11_worst_short"], lines["s11_worst_long"]
            ),
            "  Z0 (H-J, eps_r 4.4): {:.1f} Ohm".format(lines["z0"]),
            "  eps_r, Kirschning-Jansen inverted: {:.3f}   (cross-check)".format(
                lines["eps_r_kj"]
            ),
        ]
        tb = lines["table"]
        if tb:
            t.append(
                "  eps_r, simulated table (refine {:g}): raw {:.3f}, corrected {:.3f}{}".format(
                    tb["ref"],
                    tb["raw"],
                    tb["corrected"],
                    (
                        " (35 um copper: eps_eff - {:.4f})".format(tb["delta"])
                        if tb["delta"] is not None
                        else " (no _t35 pair: copper thickness not corrected)"
                    ),
                )
            )
            if not tb["inside"]:
                t.append(
                    "  WARNING: measured eps_eff is outside the simulated table: eps_r is an extrapolation"
                )
        else:
            t.append(
                "  no simulated table in line_pair_sim/: eps_r and tan d use the K-J value"
            )
        t.append(
            "  (FR4 is anisotropic: the line and patch eps_r may differ by a few %)"
        )
        if "eps_eff_pred" in lines:
            t.append(
                "  eps_eff predicted from the patch eps_r: {:.3f} (measured {:.3f})".format(
                    lines["eps_eff_pred"], lines["eps_eff_58"]
                )
            )
        t += [
            "  conductor loss assumed: {:.2f}-{:.2f} dB/m ({:.2f} smooth x {:.1f}-{:.1f} roughness/HASL, an estimate)".format(
                ALPHA_C_SMOOTH_DB_M * ROUGH_FACTOR[0],
                ALPHA_C_SMOOTH_DB_M * ROUGH_FACTOR[1],
                ALPHA_C_SMOOTH_DB_M,
                *ROUGH_FACTOR
            ),
            "  tan d: {:.4f} - {:.4f}   (high end = low conductor loss)".format(
                min(lines["tan_d"]), max(lines["tan_d"])
            ),
        ]
    return "\n".join(t)


def plots(patch, f_p, lines, out):
    import matplotlib.pyplot as plt

    if patch:
        fig, ax = plt.subplots(figsize=(8, 5), tight_layout=True)
        ax.plot(f_p / 1e9, patch["s11_db"], label="measured")
        if patch.get("deembedded"):
            ax.plot(f_p / 1e9, patch["deembedded"]["s11_db"], label="launch removed")
        ax.axvspan(
            BAND[0] / 1e9, BAND[1] / 1e9, color="g", alpha=0.15, label="5.75-5.85 GHz"
        )
        ax.axhline(-10, color="k", linestyle=":", linewidth=1)
        ax.axvline(
            patch["f_res"] / 1e9,
            color="r",
            linestyle="--",
            label="{:.3f} GHz".format(patch["f_res"] / 1e9),
        )
        ax.set_xlabel("Frequency (GHz)")
        ax.set_ylabel("S11 (dB)")
        ax.set_title(
            "Patch board: eps_r {:.2f} (table)".format(
                patch.get("deembedded", patch)["eps_r_table"]
            )
        )
        ax.grid()
        ax.legend()
        fig.savefig(os.path.join(out, "k8_patch_s11.png"))
    if lines:
        f = lines["f"] / 1e9
        fig, ax = plt.subplots(figsize=(8, 5), tight_layout=True)
        ax.plot(f, lines["eps_eff"], label="measured")
        ax.plot(
            f,
            lines["model_line"],
            "--",
            label="model, eps_r {:.2f} (from line)".format(lines["eps_r_kj"]),
        )
        if "model_patch" in lines:
            ax.plot(f, lines["model_patch"], ":", label="model, eps_r from patch")
        ax.axvspan(BAND[0] / 1e9, BAND[1] / 1e9, color="g", alpha=0.15)
        ax.set_xlabel("Frequency (GHz)")
        ax.set_ylabel("eps_eff")
        ax.set_title("Line pair: eps_eff from the phase difference")
        ax.grid()
        ax.legend()
        fig.savefig(os.path.join(out, "k8_eps_eff.png"))
        fig, ax = plt.subplots(figsize=(8, 5), tight_layout=True)
        for k, lab in (("s21_short", "short"), ("s21_long", "long")):
            ax.plot(f, 20 * np.log10(np.abs(lines[k])), label=lab)
        ax.axvspan(BAND[0] / 1e9, BAND[1] / 1e9, color="g", alpha=0.15)
        ax.set_xlabel("Frequency (GHz)")
        ax.set_ylabel("|S21| (dB)")
        ax.set_title("Line pair S21")
        ax.grid()
        ax.legend()
        fig.savefig(os.path.join(out, "k8_s21.png"))


def main():
    p = argparse.ArgumentParser(
        description="K8: patch board and line pair measurement analysis"
    )
    p.add_argument("--patch", help="patch board S11, .s1p")
    p.add_argument("--short", help="short line, .s2p")
    p.add_argument("--long", help="long line, .s2p")
    p.add_argument(
        "--dl", type=float, default=70.0, help="length difference in mm (default 70)"
    )
    p.add_argument("--width", type=float, default=2.868, help="line width in mm")
    p.add_argument("--h", type=float, default=1.5, help="dielectric thickness in mm")
    p.add_argument(
        "--l-short",
        type=float,
        default=25.0,
        help="short line, board edge to board edge in mm (launch removal)",
    )
    p.add_argument("--out", help="write k8_summary.md and plots here")
    args = p.parse_args()
    if bool(args.short) != bool(args.long):
        p.error("--short and --long go together")
    if not (args.patch or args.short):
        p.error("nothing to do: give --patch and/or --short and --long")

    patch = lines = f_p = None
    if args.short:
        f, ss = read_touchstone(args.short)
        f2, sl = read_touchstone(args.long)
        assert ss.shape[1] == 2 and sl.shape[1] == 2, "line files must be .s2p"
        assert np.allclose(f, f2), "the two line files have different frequency grids"
    if args.patch:
        f_p, s = read_touchstone(args.patch)
        patch = analyze_patch(f_p, s[:, 0, 0])
        if args.short:
            assert np.allclose(
                f_p, f
            ), "patch and lines need the same sweep (span and points) to remove the launch"
            box = launch_box(f, ss, sl, args.dl, args.l_short)
            patch["deembedded"] = analyze_patch(f, deembed(s[:, 0, 0], *box))
    if args.short:
        lines = analyze_lines(
            f,
            ss,
            sl,
            args.dl,
            args.width,
            args.h,
            patch.get("deembedded", patch)["eps_r_table"] if patch else None,
            sim_table(),
        )
        lines["s21_short"], lines["s21_long"] = ss[:, 1, 0], sl[:, 1, 0]
    text = report(patch, lines, args)
    print(text)
    if args.out:
        matplotlib.use("Agg")
        os.makedirs(args.out, exist_ok=True)
        plots(patch, f_p, lines, args.out)
        with open(os.path.join(args.out, "k8_summary.md"), "w") as fh:
            fh.write(
                "# K8 summary\n\nGenerated by `k8_analyze.py`.\n\n```\n"
                + text
                + "\n```\n"
            )


if __name__ == "__main__":
    main()

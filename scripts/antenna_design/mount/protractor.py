"""Turntable angle scale: strips for the rim of the lazy susan's top, A4 landscape, 1:1.

    python scripts/antenna_design/mount/protractor.py [--d 295]   (venv on)
    -> scripts/antenna_design/local/mount/protractor.pdf

Two sets, -100..+100 deg in two pieces each (overlap +-3 to join). Set 1 reads more as the
top turns clockwise seen from above, set 2 counter-clockwise; k9_turntable.md 4.2 picks one.
"""

import argparse
import math
import os

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

ap = argparse.ArgumentParser()
ap.add_argument("--d", type=float, default=295.0, help="rim diameter, mm")
D = ap.parse_args().d
K = math.pi * D / 360  # mm per deg
H = 20                 # strip height, mm
LW = 0.5


def piece(ax, y, v0, v1, d):
    """Strip with its bottom edge at y; d = +1: values grow to the right."""
    xl = (297 - (v1 - v0) * K) / 2
    ax.add_patch(plt.Rectangle((xl, y), (v1 - v0) * K, H, fill=False, lw=0.3, ec="0.6"))
    for v in range(v0, v1 + 1):
        x = xl + ((v - v0) if d > 0 else (v1 - v)) * K
        h = 8 if v % 10 == 0 else 5.5 if v % 5 == 0 else 3
        ax.plot([x, x], [y, y + h], color="k", lw=1.2 * LW if v == 0 else LW)
        if v % 10 == 0:
            ax.text(x, y + 10, f"{v:+d}" if v else "0", ha="center", va="bottom", fontsize=7,
                    weight="bold" if v == 0 else "normal")


fig = plt.figure(figsize=(297 / 25.4, 210 / 25.4))
ax = fig.add_axes([0, 0, 1, 1])
ax.set_xlim(0, 297)
ax.set_ylim(0, 210)
ax.axis("off")
ax.text(16, 197, f"K9 turntable scale: rim D = {D:.0f} mm, 1 deg = {K:.3f} mm. Print at 100 % "
        "(actual size); the bar must measure 100 mm.", fontsize=8)
ax.plot([16, 116], [189, 189], color="k", lw=1)
for x in (16, 116):
    ax.plot([x, x], [186, 192], color="k", lw=1)
for y0, d, name in ((176, 1, "Set 1: reading grows as the top turns clockwise (seen from above)"),
                    (104, -1, "Set 2: reading grows as the top turns counter-clockwise")):
    ax.text(16, y0, name, fontsize=8)
    piece(ax, y0 - 26, -100, 3, d)
    piece(ax, y0 - 56, -3, 100, d)
ax.text(16, 30, "Cut out one set, join its two pieces at the overlap (-3..+3), wrap it round the "
        "rim, ticks down.\nPointer on the fixed base. Set 1 first; if 4.2 says + and - are "
        "swapped, use set 2.", fontsize=8, va="top")

out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "local", "mount", "protractor.pdf")
os.makedirs(os.path.dirname(out), exist_ok=True)
fig.savefig(out)
fig.savefig(out[:-3] + "png", dpi=120)
print(os.path.normpath(out))

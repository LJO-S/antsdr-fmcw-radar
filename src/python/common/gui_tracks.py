"""
Track layer of the Tracks tab (L4 WP5).

TrackStore  - no Qt. Per track id: ~2 s tail, direction class (hysteresis), age,
              latest snapshot. Fed Track.snapshot() dicts by RadarDisplay.update_tracks();
              read by layers (later track list, selection, History).
DetectionTrail - no Qt. Last ~1 s of detections as age buckets (fading dots).
TrackLayer  - Qt items of one plot, built once: heads, tails, labels, tentative
              rings. One per plot: (r, v) plot (x = v, y = r) and PPI (x, y).
nice_ticks  - round tick positions (1-2-5 x 10^n) for cheap grid.

Sign conventions as the repo: v > 0 receding, theta > 0 towards B,
x = r sin(theta), y = r cos(theta).
"""

import time
from collections import deque

import numpy as np
import pyqtgraph as pg
from pyqtgraph.Qt import QtCore, QtGui

TAIL_S = 2.0  # tail length
# Direction classes (also the index into the layer's brush table)
NEUTRAL, APPROACHING, RECEDING = 0, 1, 2
V_IN = 0.5  # m/s: neutral -> approaching / receding
V_OUT = 0.3  # m/s: back to neutral only below this
COLORS = {NEUTRAL: "#7cfc9a", APPROACHING: "#ff6b6b", RECEDING: "#4da3ff"}
COAST_ALPHA = 0.4
MAX_LABELS = 30

# Detection fade: bucket k holds ages in [k, k+1) * DET_FADE_S / 4
DET_FADE_S = 1.0
DET_ALPHAS = (0.6, 0.4, 0.25, 0.12)
DET_MAX_POINTS = 1000


def direction(a_prev, a_v):
    """Direction class of velocity a_v with hysteresis: in at V_IN, out at V_OUT."""
    if a_v < -V_IN:
        return APPROACHING
    if a_v > V_IN:
        return RECEDING
    if a_prev == APPROACHING and a_v < -V_OUT:
        return APPROACHING
    if a_prev == RECEDING and a_v > V_OUT:
        return RECEDING
    return NEUTRAL


# ===================================================================================
class TrackState:
    """One track as store sees it. Tail = float64 ring, rows (t, v, r, x, y):
    (v, r) and (x, y) contiguous column pairs, slice = plot-ready view.
    NaN row (t kept) = gap: velocity wrap, or no theta for PPI pair."""

    def __init__(self, a_snap, a_t):
        self.id = a_snap["id"]
        self.cls = NEUTRAL
        self.birth_t = a_snap["birth_t"]
        self._buf = np.empty((64, 5))
        self._s = 0  # first valid row
        self._e = 0  # one past the last
        self.snap = a_snap
        self.t = a_t
        self.v = self.r = self.x = self.y = np.nan  # latest, v wrapped
        self.age_s = 0.0

    @property
    def num(self):
        return self.snap["num"]

    @property
    def confirmed(self):
        return self.snap["status"] == "confirmed"

    @property
    def coasting(self):
        return bool(self.snap["coasting"])

    @property
    def tail(self):
        """(n, 5) view: t, v, r, x, y."""
        return self._buf[self._s : self._e]

    def tail_rv(self):
        return self._buf[self._s : self._e, 1:3]

    def tail_xy(self):
        return self._buf[self._s : self._e, 3:5]

    def _push(self, a_row):
        if self._e == len(self._buf):
            n = self._e - self._s
            if self._s > 0:  # compact first, grow only when full
                self._buf[:n] = self._buf[self._s : self._e].copy()
            else:
                self._buf = np.concatenate([self._buf, np.empty_like(self._buf)])
            self._s, self._e = 0, n
        self._buf[self._e] = a_row
        self._e += 1

    def add(self, a_t, a_v, a_r, a_x, a_y, a_break, a_tail_s):
        if self._e > self._s and self._buf[self._e - 1, 0] == a_t:
            self._e -= 1  # same CPI again (paused re-render): replace
        if a_break:
            self._push((a_t, np.nan, np.nan, np.nan, np.nan))
        self._push((a_t, a_v, a_r, a_x, a_y))
        cut = self._s + int(
            np.searchsorted(self._buf[self._s : self._e, 0], a_t - a_tail_s)
        )
        self._s = min(cut, self._e - 1)


class TrackStore:
    """Per-id track state for GUI. update() takes one snapshot list per CPI.

    - new track (state reset): id unseen, or age dropped / birth_t changed (tracker
      rebuilt: seek or RE-CONFIGURE restarts IDs at 0);
    - time backwards, or empty list, clears all;
    - tracks absent from list are gone.
    """

    def __init__(self, a_tail_s=TAIL_S, a_v_limit=None):
        self.tail_s = a_tail_s
        self._lo = None
        self._span = None
        self.set_v_limit(a_v_limit)
        self.tracks = {}  # id -> TrackState, in snapshot order
        self.t = None  # time of the newest update

    def set_v_limit(self, a_v_limit):
        """Display velocity wraps into [-a_v_limit, a_v_limit); None = no wrap."""
        if a_v_limit is None:
            self._lo = self._span = None
        else:
            self._lo, self._span = -float(a_v_limit), 2.0 * float(a_v_limit)

    def clear(self):
        self.tracks = {}
        self.t = None

    def _wrap(self, a_v):
        if self._span is None:
            return a_v
        return self._lo + (a_v - self._lo) % self._span

    def update(self, a_snaps, a_t):
        if not a_snaps:
            self.clear()
            return
        if self.t is not None and a_t < self.t:
            self.clear()
        self.t = a_t
        new = {}
        for s in a_snaps:
            st = self.tracks.get(s["id"])
            if st is None or s["age"] < st.snap["age"] or s["birth_t"] != st.birth_t:
                st = TrackState(s, a_t)
            r, v = float(s["r"]), float(s["v"])
            theta = s.get("theta")
            if s.get("x") is not None and s.get("y") is not None:
                x, y = float(s["x"]), float(s["y"])
            elif theta is not None:
                th = np.radians(theta)
                x, y = r * np.sin(th), r * np.cos(th)
            else:
                x = y = np.nan
            vw = self._wrap(v)
            # wrap jumps ~span: break line
            broke = (
                st.tail.shape[0] > 0
                and self._span is not None
                and abs(vw - st.v) > self._span / 2
            )
            st.snap, st.t = s, a_t
            st.cls = direction(st.cls, v)
            st.r, st.v, st.x, st.y = r, vw, x, y
            st.age_s = a_t - st.birth_t
            st.add(a_t, vw, r, x, y, broke, self.tail_s)
            new[s["id"]] = st
        self.tracks = new

    def confirmed(self):
        return [t for t in self.tracks.values() if t.confirmed]

    def tentative(self):
        return [t for t in self.tracks.values() if not t.confirmed]


# ===================================================================================
class DetectionTrail:
    """Last DET_FADE_S of detections, newest CPI last. Per CPI: (t, rv, xy), rv
    (n, 2) = (v, r), xy (m, 2) = (x, y) (only with theta). Max DET_MAX_POINTS (v, r)
    points total. Age bucket k = ages in [k, k+1) * fade / 4."""

    def __init__(self):
        self._cpis = deque()
        self._n = 0
        self.t = None

    def clear(self):
        self._cpis.clear()
        self._n = 0
        self.t = None

    def add(self, a_t, a_rv, a_xy):
        if self.t is not None and a_t < self.t:
            self.clear()
        self.t = a_t
        a_rv = a_rv[:DET_MAX_POINTS]
        self._cpis.append((a_t, a_rv, a_xy))
        self._n += len(a_rv)
        while self._cpis and (
            a_t - self._cpis[0][0] >= DET_FADE_S
            or (self._n > DET_MAX_POINTS and len(self._cpis) > 1)
        ):
            self._n -= len(self._cpis.popleft()[1])

    @property
    def n_points(self):
        return self._n

    def arrays(self):
        """(rv, rv_level, xy, xy_level), oldest CPI first (newest wins when drawn).
        Level = DET_LEVELS for newest age bucket down to 1; older than DET_FADE_S
        left out."""
        n = len(DET_ALPHAS)
        rv, xy, lrv, lxy = [], [], [], []
        for t, a, b in self._cpis:
            k = int((self.t - t) / (DET_FADE_S / n))
            if k < n:
                rv.append(a)
                xy.append(b)
                lrv.append(np.full(len(a), n - k, np.uint8))
                lxy.append(np.full(len(b), n - k, np.uint8))
        if not rv:
            e, el = np.empty((0, 2)), np.empty(0, np.uint8)
            return e, el, e, el
        return (
            np.concatenate(rv), np.concatenate(lrv),
            np.concatenate(xy), np.concatenate(lxy),
        )


# ===================================================================================
def nice_ticks(a_lo, a_hi, a_target=6):
    """Round tick positions in [a_lo, a_hi]: step 1, 2 or 5 x 10^n, ~a_target lines."""
    raw = (a_hi - a_lo) / a_target
    mag = 10.0 ** np.floor(np.log10(raw))
    step = next(m * mag for m in (1, 2, 5, 10) if m * mag >= raw)
    first = np.ceil(a_lo / step - 1e-9) * step
    return np.arange(first, a_hi + step * 1e-9, step)


def grid_lines(a_x_ticks, a_y_ticks, a_x_range, a_y_range):
    """NaN-separated (x, y) of full-width lines at the ticks (connect="finite")."""
    xs, ys = [], []
    for x in a_x_ticks:
        xs += [x, x, np.nan]
        ys += [a_y_range[0], a_y_range[1], np.nan]
    for y in a_y_ticks:
        xs += [a_x_range[0], a_x_range[1], np.nan]
        ys += [y, y, np.nan]
    return np.array(xs), np.array(ys)


def set_points(a_item, a_x, a_y, **a_kw):
    """setData on scatter / curve, skipped for empty update of already empty item
    (setData costs ~0.15 ms even then)."""
    if len(a_x) == 0:
        old = a_item.data if isinstance(a_item, pg.ScatterPlotItem) else a_item.xData
        if old is None or len(old) == 0:
            return
    a_item.setData(x=a_x, y=a_y, **a_kw)


class _PremultImage(pg.GraphicsObject):
    """Draws premultiplied ARGB32 QImage over data rect. pyqtgraph ImageItem hands Qt
    non-premultiplied RGBA, scaled alpha blit ~3x slower."""

    def __init__(self):
        super().__init__()
        self._rect = QtCore.QRectF()
        self._qimg = None
        self._buf = None

    def set_rect(self, a_rect):
        self.prepareGeometryChange()
        self._rect = a_rect

    def set_argb(self, a_u32):
        """a_u32 (ny, nx) uint32 premultiplied ARGB, row 0 at the rect's y0."""
        self._buf = np.ascontiguousarray(a_u32)
        h, w = self._buf.shape
        self._qimg = QtGui.QImage(
            self._buf.data, w, h, 4 * w, QtGui.QImage.Format_ARGB32_Premultiplied
        )
        self.update()

    def clear(self):
        self._qimg = self._buf = None
        self.update()

    def boundingRect(self):
        return self._rect

    def paint(self, a_painter, *_args):
        if self._qimg is not None:
            a_painter.drawImage(self._rect, self._qimg)


class DotImage:
    """Fading dots of one plot as one image on coarse (nx, ny) grid over plot data
    rect: cell = one dot, level 1..4 = age bucket (white, alpha DET_ALPHAS).
    Dense, so image: ScatterPlotItem looks up style per point on every setData
    (1000 dots on two plots ~7 ms), stroked curve of dots ~3 ms paint. Positions
    snap to cell (~5 px)."""

    def __init__(self, a_plot):
        n = len(DET_ALPHAS)
        alphas = np.zeros(n + 1, np.uint32)
        for level in range(1, n + 1):  # level n = newest
            alphas[level] = int(255 * DET_ALPHAS[n - level])
        self._lut = alphas * np.uint32(0x01010101)  # premultiplied white: alpha in every byte
        self.alphas = alphas
        self.item = _PremultImage()
        self.item.setZValue(0)
        a_plot.addItem(self.item, ignoreBounds=True)
        self._cfg = None
        self.n = 0  # dots drawn (cells set)
        self.level_img = None

    def configure(self, a_x0, a_x1, a_y0, a_y1, a_nx, a_ny):
        """Data rect and grid. Clears."""
        self._cfg = (a_x0, a_x1, a_y0, a_y1, a_nx, a_ny)
        self.item.set_rect(QtCore.QRectF(a_x0, a_y0, a_x1 - a_x0, a_y1 - a_y0))
        self.clear()

    def clear(self):
        self.n = 0
        self.level_img = None
        self.item.clear()
        self.item.setVisible(False)

    def set_points(self, a_pts, a_level):
        """a_pts (n, 2), a_level (n,) uint8, oldest first (later points win a cell)."""
        x0, x1, y0, y1, nx, ny = self._cfg
        img = np.zeros((nx, ny), np.uint8)
        if len(a_pts):
            ix = np.floor((a_pts[:, 0] - x0) / (x1 - x0) * nx).astype(int)
            iy = np.floor((a_pts[:, 1] - y0) / (y1 - y0) * ny).astype(int)
            # edges inclusive (v = +vmax is real bin): clip into last cell
            ok = (ix >= 0) & (ix <= nx) & (iy >= 0) & (iy <= ny)
            ix, iy = np.minimum(ix[ok], nx - 1), np.minimum(iy[ok], ny - 1)
            img[ix, iy] = a_level[ok]
        self.n = int(np.count_nonzero(img))
        if self.n == 0 and not self.item.isVisible():
            return
        self.level_img = img
        self.item.set_argb(self._lut[img.T])
        self.item.setVisible(self.n > 0)

    @property
    def image(self):
        """(nx, ny) levels of the last draw, 0 = none."""
        return self.level_img

    def setVisible(self, a_on):
        self.item.setVisible(a_on and self.n > 0)

    def isVisible(self):
        return self.item.isVisible()



# ===================================================================================
class TrackLayer:
    """Qt items of track layer on one plot, built once, added with ignoreBounds.
    a_kind "rv": x = v, y = r. "xy": PPI, x, y from snapshot / r and theta.
    draw() only setData / setVisible / setText on existing items."""

    Z_TAIL, Z_HEAD, Z_LABEL = 5, 6, 7
    HEAD_SIZE = 12

    def __init__(self, a_plot, a_kind):
        assert a_kind in ("rv", "xy")
        self._kind = a_kind
        self._plot = a_plot
        # Brush table: [direction][coasting]
        self._brushes = []
        for cls in (NEUTRAL, APPROACHING, RECEDING):
            c = pg.mkColor(COLORS[cls])
            dim = pg.mkColor(COLORS[cls])
            dim.setAlphaF(COAST_ALPHA)
            self._brushes.append((pg.mkBrush(c), pg.mkBrush(dim)))
        self.heads = pg.ScatterPlotItem(size=self.HEAD_SIZE, symbol="o", pen=pg.mkPen(None))
        self.tentative = pg.ScatterPlotItem(
            size=12, symbol="o", pen=pg.mkPen((190, 190, 190, 230), width=1.5),
            brush=pg.mkBrush(None),
        )
        self.tails = []
        for cls in (NEUTRAL, APPROACHING, RECEDING):
            c = pg.mkColor(COLORS[cls])
            c.setAlpha(170)
            self.tails.append(
                pg.PlotCurveItem(pen=pg.mkPen(c, width=2), connect="finite")
            )
        self.labels = []
        for _ in range(MAX_LABELS):
            lab = pg.TextItem("", color=(235, 235, 235), anchor=(0, 1))
            lab.setVisible(False)
            self.labels.append(lab)
        self._label_text = [""] * MAX_LABELS
        self._label_on = [False] * MAX_LABELS
        for it in self.tails:
            it.setZValue(self.Z_TAIL)
            a_plot.addItem(it, ignoreBounds=True)
        for it in (self.heads, self.tentative):
            it.setZValue(self.Z_HEAD)
            a_plot.addItem(it, ignoreBounds=True)
        for it in self.labels:
            it.setZValue(self.Z_LABEL)
            a_plot.addItem(it, ignoreBounds=True)
        self._gap = np.full((1, 2), np.nan)
        self._empty = np.empty(0)

    def clear(self):
        for it in (self.heads, self.tentative, *self.tails):
            set_points(it, self._empty, self._empty)
        self._hide_labels(0)

    def _hide_labels(self, a_from):
        for i in range(a_from, MAX_LABELS):
            if self._label_on[i]:
                self.labels[i].setVisible(False)
                self._label_on[i] = False

    def draw(self, a_store, a_show_tentative):
        rv = self._kind == "rv"
        hx, hy, hb = [], [], []
        per_cls = ([], [], [])
        n_lab = 0
        for st in a_store.confirmed():
            x, y = (st.v, st.r) if rv else (st.x, st.y)
            if x != x:  # NaN: no theta, not on the PPI
                continue
            hx.append(x)
            hy.append(y)
            hb.append(self._brushes[st.cls][1 if st.coasting else 0])
            per_cls[st.cls].append(st.tail_rv() if rv else st.tail_xy())
            if n_lab < MAX_LABELS:
                lab = self.labels[n_lab]
                text = f"T{st.num if st.num is not None else st.id}"
                if self._label_text[n_lab] != text:
                    lab.setText(text)
                    self._label_text[n_lab] = text
                lab.setPos(x, y)
                if not self._label_on[n_lab]:
                    lab.setVisible(True)
                    self._label_on[n_lab] = True
                n_lab += 1
        self._hide_labels(n_lab)
        set_points(self.heads, np.array(hx), np.array(hy), brush=hb)
        for cls, item in enumerate(self.tails):
            parts = []
            for a in per_cls[cls]:
                parts += [a, self._gap]
            if parts:
                xy = np.concatenate(parts)
                set_points(item, xy[:, 0], xy[:, 1])
            else:
                set_points(item, self._empty, self._empty)
        tx, ty = [], []
        if a_show_tentative:
            for st in a_store.tentative():
                x, y = (st.v, st.r) if rv else (st.x, st.y)
                if x == x:
                    tx.append(x)
                    ty.append(y)
        set_points(self.tentative, np.array(tx), np.array(ty))

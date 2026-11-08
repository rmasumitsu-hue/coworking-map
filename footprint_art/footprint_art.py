#!/usr/bin/env python3
"""
Footprint Art — YouTube Shorts 用アニメーション生成スクリプト

真っ白な無限の空間を、人のシルエットが真上から見下ろす視点でゆっくり歩く。
一歩ごとに足跡が残り、そこからカラフルなインク / 花びら模様がじわっと広がり、
最後に床全体が一枚の絵として完成する。ラスト2秒でゆっくりズームアウト。

  仕様: 1080x1920 / 30fps / 20秒 / 音声・文字なし
  依存: Python3, numpy, Pillow, ffmpeg (libx264)

使い方:
  python3 footprint_art.py                         # seed=1 → footprint_art.mp4
  python3 footprint_art.py --seed 42 --out footprint_art_seed42.mp4
  python3 footprint_art.py --seed 7 --preview 0,30,300,599   # 指定フレームだけ PNG に書き出し確認
  python3 footprint_art.py --frames-dir frames     # 連番 PNG も保存する

同じ seed なら毎回同じ動画、seed を変えると色・歩く道・模様がすべて変わる。
"""

import argparse
import colorsys
import math
import os
import subprocess
import sys
import time

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

# ---------------------------------------------------------------- constants
W_OUT, H_OUT = 1080, 1920
FPS = 30
DURATION = 20
NF = FPS * DURATION                    # 600 frames

ART_W, ART_H = 1080, 1920              # 絵として完成させる床の範囲（アート座標）
PAD = 260                              # 床の外側にはみ出す余白（アート座標）
SS = 1.4                               # ワールド解像度 = アート座標 × SS（ズーム時の解像感用）
WORLD_W = int((ART_W + 2 * PAD) * SS)
WORLD_H = int((ART_H + 2 * PAD) * SS)
COV_F = 8                              # 被覆マップの縮小率（ワールド px）

F_WALK_END = 468                       # 歩き終わり (15.6s)
F_GAP_START = 470                      # 隙間を埋める仕上げの開花を開始
F_GAP_END = 522
F_ZOOM = NF - 2 * FPS                  # ラスト2秒 = ズームアウト開始フレーム (540)

PAPER = np.array([255.0, 255.0, 255.0], np.float32)
INK_DARK = np.array([36.0, 36.0, 44.0], np.float32)


# ---------------------------------------------------------------- helpers
def sstep(x, e0, e1):
    t = np.clip((x - e0) / (e1 - e0), 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def smootherstep(t):
    t = min(max(t, 0.0), 1.0)
    return t * t * t * (t * (t * 6 - 15) + 10)


def art_to_world(x, y):
    return (x + PAD) * SS, (y + PAD) * SS


def value_noise(S, cells, rng):
    g = rng.random((cells + 3, cells + 3)).astype(np.float32)
    im = Image.fromarray(g).resize((S, S), Image.BICUBIC, box=(1, 1, cells + 1, cells + 1))
    return np.asarray(im, dtype=np.float32)


def fbm(S, rng, base=4):
    n = (0.55 * value_noise(S, base, rng)
         + 0.30 * value_noise(S, base * 2 + 1, rng)
         + 0.15 * value_noise(S, base * 5 + 2, rng))
    return np.clip((n - 0.5) * 2.4, -1.0, 1.0)


def mix(a, b, t):
    return np.asarray(a, np.float32) * (1 - t) + np.asarray(b, np.float32) * t


# ---------------------------------------------------------------- palette
def make_palette(rng):
    """毎回ランダムだが統一感のある配色（色相の関係＋明度・彩度レンジを揃える）"""
    h0 = rng.random()
    schemes = [
        ("analogous", [0.0, 0.06, 0.12, -0.06, 0.50]),
        ("split-complementary", [0.0, 0.42, 0.58, 0.04, -0.04]),
        ("triadic", [0.0, 0.333, 0.667, 0.04, 0.37]),
        ("warm-cool", [0.0, 0.07, 0.50, 0.56, 0.93]),
        ("tetradic", [0.0, 0.25, 0.50, 0.75, 0.03]),
    ]
    name, offs = schemes[rng.integers(len(schemes))]
    l_lo, l_hi = rng.uniform(0.44, 0.52), rng.uniform(0.62, 0.72)
    s_lo, s_hi = rng.uniform(0.50, 0.65), rng.uniform(0.75, 0.92)
    cols = []
    for o in offs:
        h = (h0 + o + rng.normal(0, 0.012)) % 1.0
        cols.append(colorsys.hls_to_rgb(h, rng.uniform(l_lo, l_hi), rng.uniform(s_lo, s_hi)))
    deep = colorsys.hls_to_rgb((h0 + rng.uniform(-0.04, 0.04)) % 1.0,
                               rng.uniform(0.20, 0.28), rng.uniform(0.35, 0.65))
    return name, np.array(cols, np.float32) * 255.0, np.array(deep, np.float32) * 255.0


# ---------------------------------------------------------------- bloom sprite
class Bloom:
    """一つの足跡から広がるインク/花の模様。
    P: 乗算済み RGB, A: アルファ, T: 各ピクセルが現れる閾値（中心→外側へ）"""

    def __init__(self, cx, cy, R_art, style, cols, deep, rng,
                 heading=None, side=0, start=0, G=48):
        self.start, self.G = start, G
        R = R_art * SS
        S = int(2 * R * 1.42) + 8
        wx, wy = art_to_world(cx, cy)
        self.x0 = int(round(wx - S / 2))
        self.y0 = int(round(wy - S / 2))
        c = S / 2.0

        yy, xx = np.mgrid[0:S, 0:S].astype(np.float32)
        dx = xx - c + 0.5
        dy = yy - c + 0.5
        r = np.hypot(dx, dy)
        th = np.arctan2(dy, dx)

        b = np.ones_like(r)
        for k in range(2, 8):
            b += rng.uniform(0.0, 0.10) / math.sqrt(k - 1) * np.sin(k * th + rng.uniform(0, 2 * math.pi))
        n1 = fbm(S, rng, 4)
        n2 = fbm(S, rng, 9)
        rn = r / (R * b) + 0.09 * n1

        P = np.zeros((S, S, 3), np.float32)
        A = np.zeros((S, S), np.float32)

        def over(col, a, sl=(slice(None), slice(None))):
            a = np.clip(a, 0.0, 1.0)
            col = np.asarray(col, np.float32)
            P[sl] = col * a[..., None] + P[sl] * (1.0 - a[..., None])
            A[sl] = a + A[sl] * (1.0 - a)

        perm = rng.permutation(len(cols))
        c_wash, c_pet, c_pet2, c_ctr = cols[perm[0]], cols[perm[1]], cols[perm[2]], cols[perm[3]]
        c_acc = cols[perm[4]]
        white = PAPER

        # --- 水彩のにじみ（外周が濃くなるエッジダークニング付き）
        inside = sstep(rn, 1.0, 0.965)
        aw = (0.38 + 0.12 * n2 + 0.40 * np.exp(-((rn - 0.955) / 0.04) ** 2)) * inside
        over(mix(c_wash, white, 0.12), np.clip(aw, 0, 0.9))

        b2 = np.ones_like(r)
        for k in range(2, 6):
            b2 += rng.uniform(0.0, 0.12) / math.sqrt(k - 1) * np.sin(k * th + rng.uniform(0, 2 * math.pi))
        rn2 = r / (R * rng.uniform(0.5, 0.7) * b2) + 0.10 * n2
        aw2 = (0.20 + 0.32 * np.exp(-((rn2 - 0.95) / 0.05) ** 2)) * sstep(rn2, 1.0, 0.96)
        over(c_pet2 if style != "ink" else c_acc, aw2 * 0.9)

        def petals(n, Rp, phi, sharp, col_in, col_out, alpha):
            ang = (th - phi) * n / 2.0
            cc = np.abs(np.cos(ang))
            prof = Rp * (0.16 + 0.84 * cc ** sharp) * (1.0 + 0.05 * n1)
            d = prof - r
            a = np.clip(d / (1.3 * SS), 0, 1) * alpha * (0.88 + 0.12 * n2)
            g = np.clip(r / np.maximum(prof, 1.0), 0, 1)[..., None]
            col = mix(col_in, col_out, g)
            edge = np.exp(-(d / (2.2 * SS)) ** 2) * (d > -2)
            col = col * (1 - 0.30 * edge[..., None])
            vein = (np.exp(-((np.abs(np.sin(ang)) * r * 2.0 / n) / (0.9 * SS)) ** 2)
                    * (r < prof * 0.85) * (r > Rp * 0.18))
            col = col * (1 - 0.22 * vein[..., None])
            over(col, a)

        if style == "flower":
            n = int(rng.integers(5, 10))
            phi = rng.uniform(0, 2 * math.pi)
            Rp = R * rng.uniform(0.58, 0.78)
            petals(n, Rp, phi, rng.uniform(0.45, 0.8),
                   mix(c_pet, deep, 0.35), mix(c_pet, white, 0.30), 0.86)
            petals(n, Rp * rng.uniform(0.45, 0.62), phi + math.pi / n, rng.uniform(0.5, 0.9),
                   mix(c_pet2, deep, 0.30), mix(c_pet2, white, 0.25), 0.88)
        elif style == "burst":
            n = int(rng.integers(12, 22))
            phi = rng.uniform(0, 2 * math.pi)
            Rp = R * rng.uniform(0.66, 0.84)
            petals(n, Rp, phi, rng.uniform(1.8, 3.2),
                   mix(c_pet, deep, 0.25), mix(c_pet, white, 0.20), 0.84)
            petals(n, Rp * 0.55, phi + math.pi / n, rng.uniform(1.0, 2.0),
                   mix(c_pet2, deep, 0.2), mix(c_pet2, white, 0.2), 0.85)
        else:  # ink: しずくの輪
            k = int(rng.integers(10, 18))
            rr = R * rng.uniform(0.45, 0.62)
            for i in range(k):
                a0 = 2 * math.pi * i / k + rng.normal(0, 0.06)
                px, py = c + math.cos(a0) * rr, c + math.sin(a0) * rr
                s = R * rng.uniform(0.04, 0.075)
                self._dot(over, px, py, s, mix(c_pet, deep, rng.uniform(0, 0.3)), 0.85, S)

        # --- 中心（花芯）
        Rc = R * (0.17 if style != "ink" else 0.12)
        ac = np.clip((Rc * (1 + 0.06 * n1) - r) / (1.3 * SS), 0, 1) * 0.95
        over(mix(c_ctr, white, np.clip(r / Rc, 0, 1)[..., None] * 0.25), ac)
        if style != "ink":
            k = int(rng.integers(12, 22))
            for i in range(k):
                a0 = 2 * math.pi * i / k
                self._dot(over, c + math.cos(a0) * Rc * 1.22, c + math.sin(a0) * Rc * 1.22,
                          R * 0.022, mix(deep, c_ctr, 0.2), 0.9, S)

        # --- しぶき（外側の点々）
        m = int(rng.integers(18, 40)) if style != "ink" else int(rng.integers(35, 60))
        for _ in range(m):
            a0 = rng.uniform(0, 2 * math.pi)
            rad = R * rng.uniform(0.80, 1.32)
            s = R * rng.uniform(0.008, 0.028) * (1.5 if style == "ink" else 1.0)
            col = cols[rng.integers(len(cols))]
            self._dot(over, c + math.cos(a0) * rad, c + math.sin(a0) * rad, s, col, 0.85, S)

        T = np.clip(rn, 0, None) / 1.6

        # --- 足跡（最初に現れ、花の中心として残る）
        if heading is not None:
            hx, hy = math.cos(heading), math.sin(heading)
            u = dx * hx + dy * hy            # 進行方向
            v = -dx * hy + dy * hx           # 右方向
            Lf = 56 * SS
            vs = 1.0 if side == 0 else -1.0  # 左足: 内側=右(+v)

            def ell(cu, cv, su, sv):
                d = np.sqrt(((u - cu) / su) ** 2 + ((v - cv) / sv) ** 2)
                return np.clip((1 - d) * min(su, sv) / (1.1 * SS), 0, 1)

            fm = np.maximum(ell(0.12 * Lf, vs * 0.03 * Lf, 0.31 * Lf, 0.165 * Lf),
                            ell(-0.28 * Lf, 0, 0.19 * Lf, 0.13 * Lf))
            toes = [(0.55, 0.10, 0.085), (0.54, 0.00, 0.060), (0.51, -0.075, 0.055),
                    (0.47, -0.14, 0.050), (0.42, -0.195, 0.045)]
            for tu, tv, tr in toes:
                fm = np.maximum(fm, ell(tu * Lf, vs * tv * Lf, tr * Lf, tr * Lf))
            fcol = mix(mix(c_ctr, deep, 0.75), INK_DARK, 0.25)
            over(fcol, fm * 0.93 * (0.86 + 0.14 * n2))
            T = np.where(fm > 0.01, -0.2, T)

        self.P, self.A, self.T = P, A, T.astype(np.float32)
        self.S = S

    @staticmethod
    def _dot(over, px, py, s, col, alpha, S):
        pad = s + 3
        x0, x1 = max(int(px - pad), 0), min(int(px + pad) + 1, S)
        y0, y1 = max(int(py - pad), 0), min(int(py + pad) + 1, S)
        if x0 >= x1 or y0 >= y1:
            return
        yy, xx = np.mgrid[y0:y1, x0:x1].astype(np.float32)
        d = np.hypot(xx + 0.5 - px, yy + 0.5 - py) - s
        a = np.clip(0.5 - d / (1.0 * SS), 0, 1) * alpha
        over(col, a, (slice(y0, y1), slice(x0, x1)))

    def progress(self, f):
        u = (f - self.start) / self.G
        return u

    def masked(self, u, sl):
        """進行度 u (0..1) で、中心から外へにじみ広がる状態を返す"""
        p = -0.25 + 1.3 * (1.0 - (1.0 - min(u, 1.0)) ** 2.2)
        T = self.T[sl]
        x = p - T
        m = sstep(x, 0.0, 0.05)
        if u < 1.0:
            m = m * (1.0 + 0.45 * np.exp(-((x - 0.03) / 0.03) ** 2))  # にじみの先端が濃い
        A = self.A[sl]
        Ae = np.clip(A * m, 0, 1)
        k = np.where(A > 1e-5, Ae / np.maximum(A, 1e-5), 0.0)
        return self.P[sl] * k[..., None], Ae


def isect(ox, oy, h, w, rx0, ry0, rx1, ry1):
    """スプライト(ox,oy,h,w) と矩形の交差 → (sprite slice, region slice)"""
    x0, y0 = max(ox, rx0), max(oy, ry0)
    x1, y1 = min(ox + w, rx1), min(oy + h, ry1)
    if x0 >= x1 or y0 >= y1:
        return None
    return ((slice(y0 - oy, y1 - oy), slice(x0 - ox, x1 - ox)),
            (slice(y0 - ry0, y1 - ry0), slice(x0 - rx0, x1 - rx0)))


# ---------------------------------------------------------------- path
def catmull_rom(pts, samples_per_seg=60):
    pts = np.asarray(pts, np.float64)
    pts = np.vstack([2 * pts[0] - pts[1], pts, 2 * pts[-1] - pts[-2]])
    out = []
    for i in range(1, len(pts) - 2):
        p0, p1, p2, p3 = pts[i - 1], pts[i], pts[i + 1], pts[i + 2]
        for t in np.linspace(0, 1, samples_per_seg, endpoint=False):
            t2, t3 = t * t, t * t * t
            out.append(0.5 * ((2 * p1) + (-p0 + p2) * t + (2 * p0 - 5 * p1 + 4 * p2 - p3) * t2
                              + (-p0 + 3 * p1 - 3 * p2 + p3) * t3))
    out.append(pts[-2])
    return np.array(out)


def make_path(rng):
    """床を塗りつぶせるように蛇行しながら2往復弱の道を作る"""
    xs = [270 + rng.uniform(-35, 35), 810 + rng.uniform(-35, 35)]
    y_lo, y_hi = 230, 1700
    wob = rng.uniform(45, 85)
    pts = []
    k = 6
    for ci, x in enumerate(xs):
        ys = np.linspace(y_hi, y_lo, k) if ci == 0 else np.linspace(y_lo, y_hi, k)
        ph = rng.uniform(0, 2 * math.pi)
        for j, y in enumerate(ys):
            pts.append((x + wob * math.sin(ph + j * 1.7) + rng.uniform(-20, 20), y + rng.uniform(-30, 30)))
        if ci == 0:
            pts.append((540 + rng.uniform(-40, 40), y_lo - 110 + rng.uniform(-20, 20)))
    pts = np.array(pts)
    if rng.random() < 0.5:                       # 左右反転
        pts[:, 0] = ART_W - pts[:, 0]
    if rng.random() < 0.5:                       # 上下反転（上から下へ歩き出す）
        pts[:, 1] = ART_H - pts[:, 1]
    curve = catmull_rom(pts)
    seg = np.hypot(*np.diff(curve, axis=0).T)
    arc = np.concatenate([[0], np.cumsum(seg)])
    return curve, arc


def path_at(curve, arc, s):
    s = min(max(s, 0.0), arc[-1])
    return np.array([np.interp(s, arc, curve[:, 0]), np.interp(s, arc, curve[:, 1])])


def heading_at(curve, arc, s):
    a = path_at(curve, arc, s - 25)
    b = path_at(curve, arc, s + 25)
    return math.atan2(b[1] - a[1], b[0] - a[0])


# ---------------------------------------------------------------- walker
def draw_walker(frame, sx, sy, heading, swing, scale):
    """真上から見た人のシルエット（頭・肩・腕・足）"""
    K = 3
    size = int(390 * scale) | 1
    big = size * K
    body = Image.new("L", (big, big), 0)
    head = Image.new("L", (big, big), 0)
    db, dh = ImageDraw.Draw(body), ImageDraw.Draw(head)
    ch, sh = math.cos(heading), math.sin(heading)

    def poly(cu, cv, ru, rv):
        pts = []
        for i in range(48):
            t = 2 * math.pi * i / 48
            uu, vv = cu + ru * math.cos(t), cv + rv * math.sin(t)
            x = big / 2 + (uu * ch - vv * sh) * 1.3 * scale * K
            y = big / 2 + (uu * sh + vv * ch) * 1.3 * scale * K
            pts.append((x, y))
        return pts

    sway = 3.0 * swing
    db.polygon(poly(swing * 27, -20, 26, 10.5), fill=255)          # 左足
    db.polygon(poly(-swing * 27, 20, 26, 10.5), fill=255)          # 右足
    db.polygon(poly(-swing * 15, -49 + sway, 19, 10), fill=255)    # 左腕
    db.polygon(poly(swing * 15, 49 + sway, 19, 10), fill=255)      # 右腕
    db.polygon(poly(0, sway, 19, 47), fill=255)                    # 胴・肩
    dh.polygon(poly(3, sway, 20, 19), fill=255)                    # 頭

    body = body.resize((size, size), Image.LANCZOS)
    head = head.resize((size, size), Image.LANCZOS)
    sil = Image.fromarray(np.maximum(np.asarray(body), np.asarray(head)))
    shadow = sil.filter(ImageFilter.GaussianBlur(7 * scale)).point(lambda v: int(v * 0.22))

    off = int(round(7 * scale)), int(round(10 * scale))
    x0, y0 = int(round(sx - size / 2)), int(round(sy - size / 2))
    frame.paste((0, 0, 0), (x0 + off[0], y0 + off[1], x0 + off[0] + size, y0 + off[1] + size), shadow)
    frame.paste(tuple(int(v) for v in INK_DARK), (x0, y0, x0 + size, y0 + size), body)
    frame.paste((70, 70, 80), (x0, y0, x0 + size, y0 + size), head)


# ---------------------------------------------------------------- main render
def render(seed, out, preview=None, frames_dir=None, crf=18):
    rng = np.random.default_rng(seed)
    pal_name, cols, deep = make_palette(rng)
    curve, arc = make_path(rng)
    L = arc[-1]
    print(f"[seed {seed}] palette={pal_name} path={L:.0f}px", flush=True)

    # --- 歩く速度プロファイル（歩き出しと止まりは滑らかに）
    fr = np.arange(F_WALK_END + 1)
    u = fr / F_WALK_END
    v = np.minimum(1.0, np.minimum(u / 0.05, (1.0 - u) / 0.07)).clip(0.05, 1.0)
    v[-1] = 0
    s_frames = np.concatenate([[0], np.cumsum(v[:-1])])
    s_frames = s_frames / s_frames[-1] * L
    vnorm = v / v.max()

    n_steps = int(round(L / 172)) + 1
    stride = L / (n_steps - 1)

    def s_of(f):
        return s_frames[min(f, F_WALK_END)]

    def speed_of(f):
        return vnorm[f] if f <= F_WALK_END else 0.0

    canvas = np.empty((WORLD_H, WORLD_W, 3), np.float32)
    canvas[:] = PAPER
    cov = np.zeros((WORLD_H // COV_F + 1, WORLD_W // COV_F + 1), np.float32)

    blooms = []        # まだ焼き付けていない模様
    styles_main = ["flower", "flower", "burst", "flower", "ink"]

    def add_cov(bl):
        S = bl.S
        k = S // COV_F
        if k < 1:
            return
        a = bl.A[:k * COV_F, :k * COV_F].reshape(k, COV_F, k, COV_F).mean(axis=(1, 3))
        cx0, cy0 = bl.x0 // COV_F, bl.y0 // COV_F
        r = isect(cx0, cy0, k, k, 0, 0, cov.shape[1], cov.shape[0])
        if r:
            ssl, rsl = r
            cov[rsl] = 1 - (1 - cov[rsl]) * (1 - a[ssl])

    def spawn(*args, **kw):
        bl = Bloom(*args, **kw)
        blooms.append(bl)
        add_cov(bl)
        return bl

    # --- 足跡の発生スケジュール
    step_frames = []
    for i in range(n_steps):
        target = i * stride
        f = int(np.searchsorted(s_frames, target - 1e-6))
        step_frames.append(max(2, min(f, F_WALK_END)) if i > 0 else 2)
    step_idx = 0

    gap_done = False
    final_view = None

    cam = None
    zoom_walk_end = 1.45

    def bake(bl):
        r = isect(bl.x0, bl.y0, bl.S, bl.S, 0, 0, WORLD_W, WORLD_H)
        if r:
            ssl, rsl = r
            Pm, Am = bl.masked(1.0, ssl)
            canvas[rsl] = Pm + canvas[rsl] * (1 - Am[..., None])

    if preview is not None:
        want = set(preview)
        last = max(want)
    else:
        want, last = None, NF - 1

    proc = None
    if want is None:
        cmd = ["ffmpeg", "-y", "-loglevel", "error",
               "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W_OUT}x{H_OUT}", "-r", str(FPS), "-i", "-",
               "-c:v", "libx264", "-preset", "slow", "-crf", str(crf), "-pix_fmt", "yuv420p",
               "-profile:v", "high", "-movflags", "+faststart", out]
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    if frames_dir:
        os.makedirs(frames_dir, exist_ok=True)

    t0 = time.time()
    for f in range(last + 1):
        # ---- 足跡を置く
        while step_idx < n_steps and step_frames[step_idx] <= f:
            i = step_idx
            s_i = i * stride
            p = path_at(curve, arc, s_i)
            h = heading_at(curve, arc, s_i)
            side = i % 2                      # 0 = 左足, 1 = 右足
            lat = -18 if side == 0 else 18
            fx = p[0] - math.sin(h) * lat
            fy = p[1] + math.cos(h) * lat
            if i == 0:
                R, style, G = 270, "flower", 27     # 冒頭1秒で色づき広がる
            else:
                R = rng.uniform(215, 285)
                style = styles_main[rng.integers(len(styles_main))]
                G = int(rng.integers(44, 56))
            spawn(fx, fy, R, style, cols, deep, rng, heading=h, side=side, start=f, G=G)
            # 周りに飛び散る小さな模様
            for _ in range(int(rng.integers(0, 3))):
                a0 = rng.uniform(0, 2 * math.pi)
                dd = R * rng.uniform(0.75, 1.15)
                spawn(fx + math.cos(a0) * dd, fy + math.sin(a0) * dd, R * rng.uniform(0.28, 0.45),
                      ["ink", "flower", "burst"][rng.integers(3)], cols, deep, rng,
                      start=f + int(rng.integers(8, 22)), G=int(rng.integers(36, 46)))
            step_idx += 1

        # ---- 仕上げ: 残った白い隙間を開花で埋めて一枚の絵にする
        if f == F_GAP_START and not gap_done:
            gap_done = True
            ax0, ay0 = art_to_world(-20, -20)
            ax1, ay1 = art_to_world(ART_W + 20, ART_H + 20)
            cx0, cy0, cx1, cy1 = (int(ax0 // COV_F), int(ay0 // COV_F),
                                  int(ax1 // COV_F), int(ay1 // COV_F))
            fills = []
            for _ in range(40):
                im = Image.fromarray((cov * 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(5))
                cb = np.asarray(im, np.float32)[cy0:cy1, cx0:cx1] / 255.0
                j = int(np.argmin(cb))
                if cb.flat[j] > 0.62:
                    break
                gy, gx = divmod(j, cb.shape[1])
                wx, wy = (gx + cx0) * COV_F, (gy + cy0) * COV_F
                axp, ayp = wx / SS - PAD, wy / SS - PAD
                fills.append((axp, ayp))
                # 仮の被覆を書き込み、次の候補探索へ
                rr = int(135 * SS / COV_F)
                yy, xx = np.ogrid[:cov.shape[0], :cov.shape[1]]
                disk = ((xx - (gx + cx0)) ** 2 + (yy - (gy + cy0)) ** 2) < rr * rr
                cov[disk] = np.maximum(cov[disk], 0.8)
            order = rng.permutation(len(fills))
            for n_, oi in enumerate(order):
                axp, ayp = fills[oi]
                st = F_GAP_START + int(n_ * (F_GAP_END - F_GAP_START) / max(1, len(fills)))
                spawn(axp, ayp, rng.uniform(150, 215), ["flower", "burst", "ink", "flower"][rng.integers(4)],
                      cols, deep, rng, start=st, G=int(rng.integers(40, 50)))
            # 完成した絵がぴったり収まる最終構図
            bx0, bx1, by0, by1 = 0, ART_W, 0, ART_H
            m_ = 120
            zf = min(W_OUT / (bx1 - bx0 + 2 * m_), H_OUT / (by1 - by0 + 2 * m_))
            final_view = (np.array([(bx0 + bx1) / 2, (by0 + by1) / 2]), zf)
            print(f"  gap fills: {len(fills)}, final zoom {zf:.3f}", flush=True)

        # ---- 歩く人とカメラ
        s = s_of(f)
        body = path_at(curve, arc, s)
        h = heading_at(curve, arc, max(s, 1.0))
        phase = math.pi * s / stride
        swing = math.cos(phase) * min(1.0, 0.25 + speed_of(f))
        if speed_of(f) < 0.06:
            swing *= speed_of(f) / 0.06

        target = body + np.array([math.cos(h), math.sin(h)]) * 70
        if cam is None:
            cam = target.copy()
        else:
            cam += (target - cam) * 0.07

        z = 2.0 + (zoom_walk_end - 2.0) * smootherstep(f / 90)
        center = cam.copy()
        if f >= F_ZOOM and final_view is not None:
            e = smootherstep((f - F_ZOOM) / (NF - 1 - F_ZOOM))
            center = cam * (1 - e) + final_view[0] * e
            z = math.exp(math.log(z) * (1 - e) + math.log(final_view[1]) * e)

        if want is not None and f not in want:
            # プレビュー時は焼き付けだけ進める
            for bl in list(blooms):
                if bl.progress(f) >= 1.0:
                    bake(bl)
                    blooms.remove(bl)
            continue

        # ---- フレーム合成
        wcx, wcy = art_to_world(*center)
        cw, chh = W_OUT * SS / z, H_OUT * SS / z
        x0, y0 = wcx - cw / 2, wcy - chh / 2
        ix0, iy0 = int(math.floor(x0)), int(math.floor(y0))
        ix1, iy1 = int(math.ceil(x0 + cw)) + 2, int(math.ceil(y0 + chh)) + 2
        region = np.empty((iy1 - iy0, ix1 - ix0, 3), np.float32)
        region[:] = PAPER
        r = isect(0, 0, WORLD_H, WORLD_W, ix0, iy0, ix1, iy1)
        if r:
            region[r[1]] = canvas[r[0]]

        for bl in list(blooms):
            u_ = bl.progress(f)
            if u_ < 0:
                continue
            if u_ >= 1.0:
                bake(bl)
                blooms.remove(bl)
                rr_ = isect(bl.x0, bl.y0, bl.S, bl.S, ix0, iy0, ix1, iy1)
                if rr_:
                    Pm, Am = bl.masked(1.0, rr_[0])
                    region[rr_[1]] = Pm + region[rr_[1]] * (1 - Am[..., None])
                continue
            rr_ = isect(bl.x0, bl.y0, bl.S, bl.S, ix0, iy0, ix1, iy1)
            if rr_:
                Pm, Am = bl.masked(u_, rr_[0])
                region[rr_[1]] = Pm + region[rr_[1]] * (1 - Am[..., None])

        img = Image.fromarray(np.clip(region, 0, 255).astype(np.uint8))
        img = img.resize((W_OUT, H_OUT), Image.BICUBIC,
                         box=(x0 - ix0, y0 - iy0, x0 - ix0 + cw, y0 - iy0 + chh))

        bwx, bwy = art_to_world(*body)
        sx, sy = (bwx - x0) * z / SS, (bwy - y0) * z / SS
        draw_walker(img, sx, sy, h, swing, z)

        if proc:
            proc.stdin.write(img.tobytes())
        if frames_dir:
            img.save(os.path.join(frames_dir, f"frame_{f:04d}.png"))
        if want is not None:
            base = os.path.splitext(out)[0]
            img.save(f"{base}_f{f:04d}.png")
        if f % 60 == 0:
            el = time.time() - t0
            print(f"  frame {f:4d}/{NF}  {el:6.1f}s", flush=True)

    if proc:
        proc.stdin.close()
        proc.wait()
        if proc.returncode != 0:
            sys.exit("ffmpeg failed")
        print(f"done → {out}  ({time.time() - t0:.1f}s)", flush=True)


def main():
    ap = argparse.ArgumentParser(description="Footprint Art Shorts generator")
    ap.add_argument("--seed", type=int, default=1, help="乱数シード（変えると別パターン）")
    ap.add_argument("--out", default="footprint_art.mp4", help="出力 MP4 ファイル名")
    ap.add_argument("--crf", type=int, default=18, help="x264 品質 (小さいほど高画質)")
    ap.add_argument("--frames-dir", default=None, help="連番 PNG も保存するディレクトリ")
    ap.add_argument("--preview", default=None,
                    help="カンマ区切りのフレーム番号だけ PNG 出力（動画は作らない）")
    a = ap.parse_args()
    preview = [int(x) for x in a.preview.split(",")] if a.preview else None
    render(a.seed, a.out, preview=preview, frames_dir=a.frames_dir, crf=a.crf)


if __name__ == "__main__":
    main()

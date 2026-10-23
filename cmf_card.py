# -*- coding: utf-8 -*-
"""
CMFCard · 产品配色与材质方案卡生成器  v0.3
==========================================
输入：主色相、配色数量、调和类型、材质、器型
输出：一张 CMF 提案板（PNG）
      —— 主产品效果 + 材质色板 + 完整色号 + 色觉关怀面板 + 配色变体

v0.3 三处改动：
  1. 色名不再按色相区间硬猜，而是把颜色吸附到真实存在的色库色上
     （常用色名 + 中国传统色 526 色），最近邻用 CIEDE2000 判定，
     所以「名字」和「颜色」不可能对不上。
  2. 每个色卡补齐色号：HEX / RGB / CMYK / 明度 L*。
  3. 新增色觉关怀（color vision）面板：
     红绿色盲 / 蓝黄色盲模拟 + 两两色差检查 + 明度自动分离。

用法：
    python cmf_card.py --hue 200 --n 4 --scheme analogous --material matte --shape bottle
    python cmf_card.py --cvd-fix                       # 开启色觉自动增强
"""
import os
import sys
import math
import argparse
import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageFilter

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cmf_color as CC

FBD = 'C:/Windows/Fonts/msyhbd.ttc'
FRG = 'C:/Windows/Fonts/msyh.ttc'
FMO = 'C:/Windows/Fonts/consola.ttf'

# 版式主题：深色海报版 / 浅色纸张版
THEMES = {
    'dark': dict(BG=(16, 18, 23), BG2=(23, 26, 33), INK=(242, 244, 250),
                 DIM=(128, 136, 152), FAINT=(74, 80, 96), LINE=(40, 44, 54),
                 OK=(140, 200, 150), WARN=(228, 148, 96), GLOW=0.60,
                 ARC=(52, 58, 72)),
    'light': dict(BG=(247, 250, 254), BG2=(234, 240, 249), INK=(48, 57, 71),
                  DIM=(106, 118, 135), FAINT=(150, 161, 178), LINE=(211, 213, 217),
                  OK=(52, 148, 88), WARN=(204, 106, 44), GLOW=1.00,
                  ARC=(166, 182, 207)),
}
BG = THEMES['dark']['BG']
INK = THEMES['dark']['INK']
DIM = THEMES['dark']['DIM']
FAINT = THEMES['dark']['FAINT']
LINE = THEMES['dark']['LINE']
BG2 = THEMES['dark']['BG2']
OK_C = THEMES['dark']['OK']
WARN_C = THEMES['dark']['WARN']


def F(size, bold=False, mono=False):
    try:
        if mono:
            return ImageFont.truetype(FMO, size)
        return ImageFont.truetype(FBD if bold else FRG, size)
    except Exception:
        return ImageFont.load_default()


# ------------------------------------------------------------------ 颜色
def hsv2rgb(h, s, v):
    h = h % 360.0
    c = v * s
    x = c * (1 - abs((h / 60.0) % 2 - 1))
    m = v - c
    if h < 60:
        r, g, b = c, x, 0
    elif h < 120:
        r, g, b = x, c, 0
    elif h < 180:
        r, g, b = 0, c, x
    elif h < 240:
        r, g, b = 0, x, c
    elif h < 300:
        r, g, b = x, 0, c
    else:
        r, g, b = c, 0, x
    return np.array([r + m, g + m, b + m])


hexs = CC.hexs
rgb_txt = CC.rgb_txt
cmyk_txt = CC.cmyk_txt

SCHEME = {
    'mono':          [0, 0, 0, 0, 0],
    'analogous':     [0, 22, -22, 44, -44],
    'complementary': [0, 180, 0, 180, 0],
    'triadic':       [0, 120, 240, 60, 180],
    'split':         [0, 150, 210, 165, 195],
}

VAL_RULE = {
    'mono':          lambda i, v: v - 0.16 * i,
    'complementary': lambda i, v: v - 0.18 * max(0, i - 1),
    'triadic':       lambda i, v: v - 0.10 * (i // 3),
    'split':         lambda i, v: v - 0.10 * (i // 3),
    'analogous':     lambda i, v: v - 0.05 * (i // 3),
}

NAME_CN = {
    'mono': '单色渐变', 'analogous': '类似色', 'complementary': '互补色',
    'triadic': '三分色', 'split': '分裂互补',
}
SRC_CN = {'common': '常用色名', 'trad': '中国传统色', 'raw': '计算色', 'preset': '预设配色'}


def build_palette(hue, n, scheme, sat=0.55, val=0.80, snap=True):
    """按配色方案生成 → 吸附到真实色库色 → 组内去重"""
    offs = SCHEME.get(scheme, SCHEME['analogous'])
    vrule = VAL_RULE.get(scheme, VAL_RULE['analogous'])
    raw = []
    for i in range(n):
        h = (hue + offs[i % len(offs)]) % 360
        v = float(np.clip(vrule(i, val), 0.22, 0.95))
        s = float(np.clip(sat + (0.06 if v < 0.5 else 0.0), 0, 1))
        raw.append(dict(h=h, s=s, v=v, rgb=hsv2rgb(h, s, v)))
    if not snap:
        for p in raw:
            info = CC.nearest_all(p['rgb'])
            p['name'], p['src'], p['cmyk_lib'] = info['name'], 'raw', None
            p['snap_de'] = None
            p['hex'] = hexs(p['rgb'])
        return raw, 0
    pal, hit = CC.snap_palette(raw)
    for p in pal:
        if p.get('hex') is None:
            p['hex'] = hexs(p['rgb'])
    return pal, hit


# ------------------------------------------------------------------ 材质
def _noise(h, w, scale, seed):
    rng = np.random.default_rng(seed)
    small = rng.random((max(2, h // scale), max(2, w // scale)))
    img = Image.fromarray((small * 255).astype(np.uint8)).resize((w, h), Image.BICUBIC)
    return np.asarray(img, dtype=np.float64) / 255.0


def make_field(kind, base, w, h, seed=1):
    """返回 (h,w,3) float 的材质贴图"""
    yy, xx = np.mgrid[0:h, 0:w]
    u = xx / max(w - 1, 1)
    v = yy / max(h - 1, 1)

    if kind == 'matte':                       # 哑光陶瓷
        g = 1.06 - 0.26 * v
        g *= 1.02 - 0.05 * np.abs(u - 0.5) * 2
        g += (_noise(h, w, 2, seed) - 0.5) * 0.045
        f = np.clip(g, 0, None)

    elif kind == 'frosted':                   # 磨砂
        g = 1.04 - 0.16 * v + (_noise(h, w, 1, seed) - 0.5) * 0.16
        f = np.clip(g, 0, None)

    elif kind == 'metal':                     # 金属拉丝（纵向细密丝纹）
        rng = np.random.default_rng(seed + 7)
        n = max(w, 4)
        streak = rng.random(n)
        streak = np.convolve(streak, np.ones(3) / 3, mode='same')
        streak = np.convolve(streak, np.ones(3) / 3, mode='same')
        shake = (_noise(h, w, 70, seed + 3) - 0.5) * 0.04      # 只做极轻微抖动
        idx = np.clip(((u + shake) * (n - 1)).astype(int), 0, n - 1)
        g = 0.90 + 0.16 * (streak[idx] - 0.5) - 0.22 * v
        g += 0.14 * np.exp(-((v - 0.28) ** 2) / 0.016)          # 一道横向高光
        f = np.clip(g, 0, None)

    elif kind == 'leather':                   # 皮革（整张粒面：卵石状颗粒 + 方向光）
        rng = np.random.default_rng(seed + 11)
        npts = max(160, int(w * h / 95))
        pts = rng.random((npts, 2)) * [w, h]
        d1 = np.full((h, w), 1e9)
        for px, py in pts:
            d = (xx - px) ** 2 + (yy - py) ** 2
            d1 = np.minimum(d1, d)
        spacing = max(math.sqrt(max(w * h, 1) / npts), 1.0)
        rr = np.sqrt(d1) / (spacing * 0.92)
        hgt = np.sqrt(np.clip(1.0 - rr ** 2, 0, 1))       # 每颗皮的隆起高度
        gy, gx = np.gradient(hgt)
        k = 3.0
        nx, ny, nz = -k * gx, -k * gy, np.ones_like(hgt)
        nn = np.sqrt(nx ** 2 + ny ** 2 + nz ** 2) + 1e-9
        Lx, Ly, Lz = -0.45, -0.52, 0.725                   # 左上打光
        lam = (nx * Lx + ny * Ly + nz * Lz) / nn
        g = 0.80 + 0.30 * (lam / 0.725)                    # 每颗皮都有自己的明暗
        g += (_noise(h, w, 1, seed) - 0.5) * 0.030
        g *= 1.03 - 0.14 * v
        f = np.clip(g, 0, None)

    elif kind == 'wood':                      # 木纹：纵向纹路 + 年轮感
        rng = np.random.default_rng(seed + 21)
        n = max(w, 8)
        line = rng.random(n)
        line = np.convolve(line, np.ones(7) / 7, mode='same')
        line = (line - line.min()) / max(line.max() - line.min(), 1e-9)
        grain = line[None, :] * np.ones((h, 1))
        # 几道深色年轮
        rings = 0.05 * np.sin((xx / max(w, 1)) * 26.0 + _noise(h, w, 40, seed) * 5.0)
        g = 0.92 + 0.16 * (grain - 0.5) + rings
        g += (_noise(h, w, 1, seed) - 0.5) * 0.035
        g *= 1.02 - 0.10 * v
        f = np.clip(g, 0, None)

    elif kind == 'gloss':                     # 高光塑料
        g = 1.10 - 0.42 * v
        band = np.exp(-((u * 0.7 + v * 0.5 - 0.42) ** 2) / 0.010)
        g += 0.30 * band
        g += (_noise(h, w, 3, seed) - 0.5) * 0.02
        f = np.clip(g, 0, None)

    else:
        f = np.ones((h, w))

    return np.clip(base[None, None, :] * f[:, :, None], 0, 1)


MAT_CN = {'matte': '哑光陶瓷', 'frosted': '磨砂', 'metal': '金属拉丝',
          'leather': '皮革', 'gloss': '高光塑料', 'wood': '木纹饰面'}
MAT_EN = {'matte': 'MATTE CERAMIC', 'frosted': 'FROSTED', 'metal': 'BRUSHED METAL',
          'leather': 'FULL-GRAIN LEATHER', 'gloss': 'GLOSS PLASTIC',
          'wood': 'WOOD VENEER'}


# ------------------------------------------------------------------ 器型
def _catmull(knots, n=240):
    P = np.array(knots, dtype=np.float64)
    P = np.vstack([P[0], P, P[-1]])
    out = []
    seg = len(P) - 3
    for i in range(seg):
        p0, p1, p2, p3 = P[i], P[i + 1], P[i + 2], P[i + 3]
        m = max(4, n // seg)
        for k in range(m):
            t = k / m
            t2, t3 = t * t, t * t * t
            out.append((
                0.5 * ((2 * p1[0]) + (-p0[0] + p2[0]) * t +
                       (2 * p0[0] - 5 * p1[0] + 4 * p2[0] - p3[0]) * t2 +
                       (-p0[0] + 3 * p1[0] - 3 * p2[0] + p3[0]) * t3),
                0.5 * ((2 * p1[1]) + (-p0[1] + p2[1]) * t +
                       (2 * p0[1] - 5 * p1[1] + 4 * p2[1] - p3[1]) * t2 +
                       (-p0[1] + 3 * p1[1] - 3 * p2[1] + p3[1]) * t3)))
    out.append(tuple(P[-2]))
    return np.array(out)


SHAPES = {
    'vase': [
        (0.000, 0.140), (0.020, 0.156), (0.060, 0.180), (0.180, 0.226),
        (0.340, 0.244), (0.500, 0.238), (0.620, 0.210), (0.720, 0.150),
        (0.790, 0.098), (0.850, 0.078), (0.920, 0.076), (0.970, 0.086),
        (1.000, 0.092),
    ],
    'flask': [
        (0.000, 0.180), (0.016, 0.196), (0.045, 0.206), (0.150, 0.214),
        (0.480, 0.216), (0.620, 0.208), (0.700, 0.170), (0.760, 0.110),
        (0.800, 0.070), (0.845, 0.052), (0.880, 0.048), (0.900, 0.062),
        (0.930, 0.066), (0.960, 0.060), (1.000, 0.058),
    ],
    'jar': [
        (0.000, 0.200), (0.020, 0.216), (0.060, 0.226), (0.160, 0.232),
        (0.800, 0.232), (0.880, 0.240), (0.920, 0.248), (0.955, 0.246),
        (0.975, 0.230), (0.995, 0.226), (1.000, 0.222),
    ],
    'tumbler': [
        (0.000, 0.130), (0.014, 0.142), (0.030, 0.152), (0.062, 0.146),
        (0.140, 0.150), (0.600, 0.172), (0.900, 0.186), (0.980, 0.188),
        (1.000, 0.186),
    ],
    'globe': [
        (0.000, 0.110), (0.014, 0.116), (0.026, 0.082), (0.100, 0.070),
        (0.130, 0.056), (0.160, 0.062), (0.200, 0.090), (0.280, 0.140),
        (0.380, 0.176), (0.480, 0.190), (0.580, 0.184), (0.680, 0.158),
        (0.760, 0.118), (0.820, 0.080), (0.880, 0.062), (1.000, 0.058),
    ],
    'carafe': [
        (0.000, 0.160), (0.020, 0.184), (0.070, 0.212), (0.180, 0.238),
        (0.320, 0.244), (0.450, 0.228), (0.550, 0.190), (0.640, 0.128),
        (0.710, 0.076), (0.780, 0.050), (0.860, 0.046), (0.930, 0.052),
        (1.000, 0.060),
    ],
    'diffuser': [
        (0.000, 0.120), (0.018, 0.138), (0.055, 0.150), (0.200, 0.162),
        (0.420, 0.164), (0.580, 0.150), (0.680, 0.120), (0.740, 0.078),
        (0.800, 0.052), (0.860, 0.044), (0.930, 0.048), (1.000, 0.056),
    ],
    'bottle': [
        (0.000, 0.196), (0.018, 0.214), (0.045, 0.226), (0.30, 0.240),
        (0.52, 0.238), (0.63, 0.222), (0.72, 0.160), (0.80, 0.094),
        (0.90, 0.086), (0.965, 0.100), (1.000, 0.104),
    ],
    'lamp': [
        (0.000, 0.245), (0.016, 0.245), (0.026, 0.212), (0.034, 0.044),
        (0.470, 0.038), (0.510, 0.044), (0.530, 0.058), (0.548, 0.150),
        (0.560, 0.290), (0.600, 0.312), (0.835, 0.222), (0.880, 0.206),
        (0.912, 0.126), (0.930, 0.116),
    ],
    'speaker': [
        (0.000, 0.250), (0.030, 0.288), (0.075, 0.306), (0.20, 0.312),
        (0.78, 0.312), (0.925, 0.300), (0.970, 0.268), (1.000, 0.226),
    ],
}

SHAPE_CN = {'vase': '花瓶', 'flask': '香水瓶', 'jar': '收纳罐', 'tumbler': '直筒杯',
            'globe': '球泡灯', 'carafe': '醒酒器', 'diffuser': '香薰瓶',
            'bottle': '造型瓶', 'lamp': '台式灯', 'speaker': '便携音箱'}


def vessel_profile(shape='bottle', n=240):
    return _catmull(SHAPES.get(shape, SHAPES['bottle']), n)


# ------------------------------------------------------------------ 纹样
# 与前端 view3d.js 同一套锚点：画布 row 0 = 器口，物理高度 0(底)..1(口)
PATTERNS = {
    'none': '无花纹',
    'meander': '回纹',
    'lotus': '莲瓣',
    'wave': '水波',
    'scroll': '缠枝',
    'stripe': '竖条纹',
    'dot': '波点',
    'custom': '手绘',
}

_PAT_CACHE = {}
PS = 512


def _cy(y_phys):
    """物理高度 → 画布行"""
    return PS * (1.0 - max(0.0, min(1.0, y_phys)))


def _bez(p0, p1, p2, p3, n=24):
    out = []
    for i in range(n + 1):
        t = i / float(n)
        u = 1 - t
        out.append((u ** 3 * p0[0] + 3 * u * u * t * p1[0] + 3 * u * t * t * p2[0] + t ** 3 * p3[0],
                    u ** 3 * p0[1] + 3 * u * u * t * p1[1] + 3 * u * t * t * p2[1] + t ** 3 * p3[1]))
    return out


def _stroke(dr, pts, w):
    dr.line([(round(x), round(y)) for x, y in pts], fill=255, width=max(1, int(round(w))), joint=1)


def _custom_cover(custom):
    """手绘遮罩可能是 bool / L / RGBA，统一取出「有没有笔迹」"""
    a = np.asarray(custom)
    if a.ndim == 3:
        if a.shape[-1] == 4:
            return a[..., 3] > 8
        return a.max(axis=2) > 8
    if a.dtype == np.bool_:
        return a
    return a > 8


def pattern_mask(pat, rbar=0.2, custom=None):
    """返回 (PS,PS) 的 bool 数组，True = 花纹处；pat='custom' 时用画笔遮罩"""
    if pat == 'custom':
        if custom is None:
            return np.zeros((PS, PS), dtype=bool)
        a = _custom_cover(custom)
        return a if a.shape == (PS, PS) else np.zeros((PS, PS), dtype=bool)
    aspect = max(0.32, min(1.0, 1.0 / (2 * math.pi * max(0.02, rbar))))
    ck = (pat, round(aspect, 2))
    if ck in _PAT_CACHE:
        return _PAT_CACHE[ck]
    im = Image.new('L', (PS, PS), 0)
    dr = ImageDraw.Draw(im)

    if pat == 'meander':
        y0 = _cy(0.745)
        h = _cy(0.585) - y0
        unit = PS / 8.0
        lw = max(4, h * 0.17)
        for i in range(8):
            x = i * unit
            _stroke(dr, [(x, y0 + h), (x, y0), (x + unit * 0.72, y0),
                         (x + unit * 0.72, y0 + h * 0.62),
                         (x + unit * 0.30, y0 + h * 0.62),
                         (x + unit * 0.30, y0 + h * 0.28),
                         (x + unit * 0.52, y0 + h * 0.28)], lw)
        lw2 = max(2, h * 0.075)
        _stroke(dr, [(0, y0 - h * 0.16), (PS, y0 - h * 0.16)], lw2)
        _stroke(dr, [(0, y0 + h * 1.16), (PS, y0 + h * 1.16)], lw2)

    elif pat == 'lotus':
        n, w = 8, PS / 8.0
        top = _cy(0.80)
        deep = _cy(0.56) - top
        lw = max(3, deep * 0.055)
        for i in range(n):
            cx = i * w + w / 2.0
            for sx, hw, dp in ((0.0, 0.42, 1.0),):
                _stroke(dr, _bez((cx + sx - w * hw, top),
                                 (cx + sx - w * hw, top + deep * dp * 0.74),
                                 (cx + sx - w * hw * 0.43, top + deep * dp * 0.96),
                                 (cx + sx, top + deep * dp)), lw)
                _stroke(dr, _bez((cx + sx + w * hw, top),
                                 (cx + sx + w * hw, top + deep * dp * 0.74),
                                 (cx + sx + w * hw * 0.43, top + deep * dp * 0.96),
                                 (cx + sx, top + deep * dp)), lw)
        _stroke(dr, [(0, top - deep * 0.07), (PS, top - deep * 0.07)], lw)
        _stroke(dr, [(0, top + deep * 1.07), (PS, top + deep * 1.07)], lw)

    elif pat == 'wave':
        top = _cy(0.215)
        span = PS - top
        step = span / 3.4
        lw = max(3, step * 0.16)
        for row in range(4):
            base = PS - row * step * 0.92
            for i in range(8):
                cx = i * 64 + (32 if row % 2 else 0)
                dr.arc([cx - 30, base - 30, cx + 30, base + 30], 180, 360,
                       fill=255, width=int(round(lw)))

    elif pat == 'scroll':
        yc = _cy(0.400)
        amp = (_cy(0.400 - 0.090) - _cy(0.400 + 0.090)) / 2.0
        K = math.pi * 6
        lw = max(3, amp * 0.30)
        pts = []
        for x in range(0, PS + 1, 4):
            pts.append((x, yc + amp * math.sin(x / float(PS) * K)))
        _stroke(dr, pts, lw)
        for i in range(8):
            x = i * 64 + 32
            ph = x / float(PS) * K
            y = yc + amp * math.sin(ph)
            ang = math.atan2(amp * math.cos(ph) * (K / PS), 1.0)
            for sd in (1, -1):
                ex = x
                ey = y + sd * amp * 0.38
                rx, ry = amp * 0.50, amp * 0.22
                ca, sa = math.cos(ang + sd * 1.10), math.sin(ang + sd * 1.10)
                poly = []
                for k in range(25):
                    tt = 2 * math.pi * k / 24.0
                    dx, dy = rx * math.cos(tt), ry * math.sin(tt)
                    poly.append((ex + dx * ca - dy * sa, ey + dx * sa + dy * ca))
                dr.polygon(poly, fill=255)

    elif pat == 'stripe':
        for i in range(8):
            dr.rectangle([i * 64 + 24, 0, i * 64 + 40, PS], fill=255)

    elif pat == 'dot':
        r = 9.5
        rx = r * aspect
        for row in range(8):
            for col in range(8):
                x = (col * 64 + (32 if row % 2 else 0) + 32) % PS
                y = row * 64 + 32
                dr.ellipse([x - rx, y - r, x + rx, y + r], fill=255)

    m = np.array(im) > 127
    _PAT_CACHE[ck] = m
    return m


_BLUR_CACHE = {}


def blurred_mask(pat, rbar=0.2, blur_px=2.8, custom=None):
    """花纹遮罩做高斯模糊 → 当浮雕高度场用（与 WebGL / STL 同一套参数）"""
    key = (pat, round(float(rbar), 2))
    if custom is None and key in _BLUR_CACHE:
        return _BLUR_CACHE[key]
    m = pattern_mask(pat, rbar, custom)
    img = Image.fromarray((np.asarray(m) * 255).astype(np.uint8), 'L')
    if blur_px:
        img = img.filter(ImageFilter.GaussianBlur(float(blur_px)))
    A = np.asarray(img, dtype=np.float64) / 255.0
    if custom is None:
        _BLUR_CACHE[key] = A
    return A


def _sample2(A, rows, cols):
    """双线性采样：rows/cols 与目标同形，返回同形数组"""
    H, W = A.shape
    rs = np.clip(np.asarray(rows, float), 0.0, H - 1.001)
    cs = np.clip(np.asarray(cols, float), 0.0, W - 1.001)
    r0 = np.clip(np.floor(rs).astype(np.int64), 0, H - 2)
    c0 = np.clip(np.floor(cs).astype(np.int64), 0, W - 2)
    dr = rs - r0
    dc = cs - c0
    a = A[r0, c0]
    b = A[r0, c0 + 1]
    c = A[r0 + 1, c0]
    d = A[r0 + 1, c0 + 1]
    return (a * (1 - dc) + b * dc) * (1 - dr) + (c * (1 - dc) + d * dc) * dr


def relief_grad(pat, sinT, cosT, py, rbar, emboss_norm, custom=None):
    """
    返回 (h, h_theta_over_r, h_y)：
        浮雕高度 h（归一化半径单位）、绕轴角方向的斜率 h_θ/r、高度方向斜率 h_y
    法线 = (sinθ - (h_θ/r)cosθ, -(r_y + h_y), cosθ + (h_θ/r)sinθ)
    """
    A = blurred_mask(pat, rbar, custom=custom)
    PS = A.shape[0]
    theta = np.arctan2(sinT, np.maximum(cosT, 1e-6))
    u = (theta / (2.0 * np.pi)) % 1.0          # 与 three.js 的 φ 对齐
    rows = (1.0 - py) * (PS - 1)
    cols = u * (PS - 1)

    h0 = _sample2(A, rows, cols)
    hp = _sample2(A, rows, cols + 1.0)
    hm = _sample2(A, rows, cols - 1.0)
    dHdu = (hp - hm) * 0.5 * (PS - 1)          # col = u*(PS-1)
    rp = _sample2(A, rows - 1.0, cols)         # 更高一行 = 更高的物理高度
    rm = _sample2(A, rows + 1.0, cols)
    dHdy = (rp - rm) * 0.5 * (PS - 1)          # row = (1-y)(PS-1)，符号已取反

    h = h0 * emboss_norm
    h_theta = dHdu * emboss_norm / (2.0 * np.pi)
    h_y = dHdy * emboss_norm
    return h, h_theta, h_y


def _uv_rgb(custom, sinT, cosT, py):
    """手绘笔迹的实际颜色（每像素 RGB 0..1）；不是 RGBA 遮罩就返回 None"""
    A = np.asarray(custom)
    if A.ndim != 3 or A.shape[-1] < 3:
        return None
    PS = A.shape[0]
    theta = np.arctan2(sinT, np.maximum(cosT, 1e-6))
    u = (theta / (2.0 * np.pi)) % 1.0
    row = (1.0 - py) * (PS - 1)
    col = u * (PS - 1)
    out = np.empty(sinT.shape + (3,), dtype=np.float64)
    for k in range(3):
        out[..., k] = _sample2(A[..., k].astype(np.float64) / 255.0, row, col)
    return out


def _uv_lookup(pat, sinT, cosT, py, inside, rbar=0.2, custom=None):
    """按环绕角 u 与物理高度 py 采样纹样"""
    m = pattern_mask(pat, rbar, custom)
    theta = np.arctan2(sinT, np.maximum(cosT, 1e-6))
    # 与 three.js 的 φ 对齐：正面(θ=0) 是 u=0，不是 u=0.5
    u = (theta / (2 * np.pi)) % 1.0
    ci = np.clip((u * PS).astype(int), 0, PS - 1)
    ri = np.clip(((1.0 - py) * PS).astype(int), 0, PS - 1)
    return m[ri, ci] & inside


def render_vessel(color, W, H, kind='matte', light=(-0.55, 0.45, 0.70), shape='bottle',
                 pattern='none', pat_color=None, emboss_mm=0.0, height_mm=300.0,
                 custom_mask=None):
    prof = vessel_profile(shape)
    ys = prof[:, 0]
    rs = prof[:, 1]
    rmax = rs.max()
    pad = 0.06
    sc = min(W / (2 * rmax * (1 + pad)), H / (1.0 * (1 + pad * 2)))
    cx = W / 2
    base = H * 0.96

    img = np.zeros((H, W, 4), dtype=np.float64)
    yy, xx = np.mgrid[0:H, 0:W]
    py = (base - yy) / sc
    px = (xx - cx) / sc
    rrow = np.interp(py, ys, rs, left=np.nan, right=np.nan)
    dr = np.gradient(rs, ys)
    drow = np.interp(py, ys, dr, left=0.0, right=0.0)
    with np.errstate(invalid='ignore'):
        inside = np.isfinite(rrow) & (np.abs(px) <= rrow) & (py >= 0) & (py <= 1)

    RR = np.where(np.isfinite(rrow) & (rrow > 1e-6), rrow, 1.0)
    sinT = np.clip(px / RR, -1, 1)
    cosT = np.sqrt(np.maximum(0.0, 1 - sinT ** 2))
    use_relief = bool(pattern and pattern != 'none' and emboss_mm and emboss_mm > 0)
    if use_relief:
        # 浮雕把法线顶歪：沿半径方向往外鼓出的高度场，梯度直接进法线
        _h, _hth, _hy = relief_grad(pattern, sinT, cosT, py,
                                    float(np.mean(rs)), float(emboss_mm) / float(height_mm),
                                    custom=custom_mask)
        _hr = _hth / np.maximum(RR, 1e-6)
        _hr = np.where(inside, _hr, 0.0)
        _hy = np.where(inside, _hy, 0.0)
        nrm = np.stack([sinT - _hr * cosT, -(drow + _hy), cosT + _hr * sinT], axis=2)
    else:
        nrm = np.stack([sinT, -drow, cosT], axis=2)
    nrm /= np.linalg.norm(nrm, axis=2, keepdims=True) + 1e-12

    L = np.array(light) / np.linalg.norm(light)
    V = np.array([0, 0, 1.0])
    Hv = L + V
    Hv /= np.linalg.norm(Hv)
    ndl = np.clip((nrm * L).sum(2), 0, 1)
    ndh = np.clip((nrm * Hv).sum(2), 0, 1)
    L2 = np.array([0.68, 0.18, 0.52])
    L2 /= np.linalg.norm(L2)
    ndl2 = np.clip((nrm * L2).sum(2), 0, 1)

    spec_exp = {'matte': 26, 'frosted': 12, 'metal': 60, 'leather': 8, 'gloss': 90, 'wood': 18}[kind]
    spec_amt = {'matte': 0.14, 'frosted': 0.10, 'metal': 0.55, 'leather': 0.06, 'gloss': 0.42, 'wood': 0.12}[kind]

    shade = 0.24 + 0.78 * ndl + 0.26 * ndl2
    col = color[None, None, :] * shade[:, :, None]
    col += (ndh ** spec_exp)[:, :, None] * spec_amt
    rim = (np.clip(1 - np.abs(cosT), 0, 1) ** 2.6)[:, :, None] * 0.16
    col += rim
    col = np.clip(col, 0, 1)

    if pattern and pattern != 'none' and pat_color is not None:
        hit = _uv_lookup(pattern, sinT, cosT, py, inside, float(np.mean(rs)),
                         custom=custom_mask)
        if hit.any():
            rgbmap = _uv_rgb(custom_mask, sinT, cosT, py) if pattern == 'custom' else None
            if rgbmap is not None:                       # 手绘：用笔迹自己的颜色
                colp = rgbmap * shade[:, :, None]
            else:                                        # 程序纹样：用花纹色
                pc = np.asarray(pat_color, dtype=np.float64)[None, :]
                colp = pc[None, :, :] * shade[:, :, None]
            colp = colp + (ndh ** spec_exp)[:, :, None] * spec_amt + rim
            colp = np.clip(colp, 0, 1)
            col = np.where(hit[:, :, None], colp, col)

    if shape == 'speaker':
        dotm = ((xx % 6) - 3) ** 2 + ((yy % 6) - 3) ** 2 < 3.2
        col = col * (1.0 - 0.10 * dotm[:, :, None])
    img[..., :3] = col
    img[..., 3] = inside.astype(np.float64)
    return Image.fromarray((img * 255).astype(np.uint8), 'RGBA')


# ------------------------------------------------------------------ 卡片
W_CARD, H_CARD = 1600, 1300


def rounded_mask(w, h, r):
    m = Image.new('L', (w, h), 0)
    ImageDraw.Draw(m).rounded_rectangle([0, 0, w - 1, h - 1], radius=r, fill=255)
    return m


def paste_field(canvas, field, box, radius=12, text=None, font=None, ink=(255, 255, 255)):
    x0, y0, x1, y1 = box
    w, h = x1 - x0, y1 - y0
    im = Image.fromarray((np.clip(field, 0, 1) * 255).astype(np.uint8), 'RGB')
    im = im.resize((w, h), Image.LANCZOS)
    canvas.paste(im, (x0, y0), rounded_mask(w, h, radius))
    if text:
        d = ImageDraw.Draw(canvas)
        # 角标：让色块除了颜色还有编号，色弱也能对上号
        d.text((x0 + 10, y0 + 8), text, font=font, fill=ink)


def radial_glow(w, h, color, strength=0.30):
    yy, xx = np.mgrid[0:h, 0:w]
    cx, cy = w / 2, h * 0.52
    r = np.hypot((xx - cx) / (w * 0.52), (yy - cy) / (h * 0.52))
    a = np.clip(1 - r, 0, 1) ** 2.0 * strength
    out = np.zeros((h, w, 3), dtype=np.float64)
    out[:] = np.array(color, dtype=np.float64)
    return Image.fromarray((np.clip(out, 0, 1) * 255).astype(np.uint8), 'RGB'), \
        Image.fromarray((a * 255).astype(np.uint8), 'L')


def _ink_on(c):
    """在某个底色上应该用白字还是黑字（WCAG 对比度取大者）"""
    _, which = CC.contrast_white_black(c)
    return (255, 255, 255) if which == '白' else (20, 22, 28)


def _lstar(rgb):
    return float(CC.rgb2lab(np.asarray(rgb, dtype=np.float64))[0])


def cmyk_str(p):
    """色号里的 CMYK：命中色库就用库里的值，否则自己算"""
    ck = p.get('cmyk_lib')
    if isinstance(ck, (list, tuple)):
        return '%d %d %d %d' % tuple(int(x) for x in ck)
    if isinstance(ck, str):
        return ck
    return cmyk_txt(p['rgb'])


def build_card(pal, name, material, W=W_CARD, H=H_CARD, out='out/card.png',
               shape='bottle', scheme='analogous', hue=205.0, cvd_fix=False,
               target=12.0, theme='dark', pattern='none', pat_idx=1,
               emboss_mm=0.0, height_mm=300.0, custom_mask=None):
    global BG, BG2, INK, DIM, FAINT, LINE, OK_C, WARN_C
    _t = THEMES.get(theme, THEMES['dark'])
    BG, BG2, INK = _t['BG'], _t['BG2'], _t['INK']
    DIM, FAINT, LINE = _t['DIM'], _t['FAINT'], _t['LINE']
    OK_C, WARN_C = _t['OK'], _t['WARN']
    g = np.linspace(0, 1, H)[:, None, None]
    bg = np.array(BG, float)[None, None, :] * (1 - g) + np.array(BG2, float) * g
    img = Image.fromarray(np.repeat(np.clip(bg, 0, 255).astype(np.uint8), W, axis=1), 'RGB')
    d = ImageDraw.Draw(img)

    # ---------------- 破形弧线（版面里的结构线）----------------
    # 圆心在色卡中下部，三道同心弧向上散开，穿过页眉横线与分区线
    if _t.get('ARC'):
        al = Image.new('RGBA', (W, H), (0, 0, 0, 0))
        ad = ImageDraw.Draw(al)
        acx, acy = int(W * 0.56), int(H * 0.48)
        for rr, a0, wd in ((700, 255, 3), (768, 205, 3), (836, 155, 2), (904, 105, 2)):
            bb = [acx - rr, acy - rr, acx + rr, acy + rr]
            for k in range(-180, 181, 3):
                t = 1.0 - (abs(k) / 180.0) ** 0.7
                a = int(a0 * t)
                if a <= 3:
                    continue
                ad.arc(bb, start=k, end=k + 3.2, fill=_t['ARC'] + (a,), width=wd)
        base = img.convert('RGBA')
        base.alpha_composite(al)
        img = base.convert('RGB')
        d = ImageDraw.Draw(img)

    M = 64
    boxes = []
    n = len(pal)
    main = pal[0]
    rep = CC.cvd_report(pal, target=target)

    # ---------------- 页眉 ----------------
    d.text((M, 44), 'C M F   P R O P O S A L', font=F(14, True), fill=FAINT)
    d.text((M, 72), name, font=F(44, True), fill=INK)
    d.text((W - M - 220, 48), '2026 / 10', font=F(14, True), fill=FAINT)
    d.text((W - M - 220, 72), 'COLOUR · MATERIAL · FINISH', font=F(11), fill=FAINT)
    d.line([(M, 150), (W - M, 150)], fill=LINE, width=1)

    # ---------------- 左：主产品 ----------------
    d.text((M, 172), 'A P P L I C A T I O N', font=F(12, True), fill=FAINT)
    hx0, hy0, hx1, hy1 = M, 200, 700, 940
    hw, hh = hx1 - hx0, hy1 - hy0
    gl, ga = radial_glow(hw, hh, np.clip(main['rgb'] * (0.6 if theme == 'dark' else 0.82), 0, 1), 0.34)
    img.paste(gl, (hx0, hy0), ga)

    pat_rgb = pal[min(max(pat_idx, 0), len(pal) - 1)]['rgb']
    vs = render_vessel(main['rgb'], hw - 30, hh - 30, material, shape=shape,
                       pattern=pattern, pat_color=pat_rgb,
                       emboss_mm=emboss_mm, height_mm=height_mm,
                       custom_mask=custom_mask)
    img.paste(vs, (hx0 + 15, hy0 + 15), vs)

    sh = Image.new('RGBA', (hw, 70), (0, 0, 0, 0))
    ImageDraw.Draw(sh).ellipse([110, 18, hw - 110, 58], fill=(0, 0, 0, 130))
    sh = sh.filter(ImageFilter.GaussianBlur(16))
    img.paste(sh, (hx0, hy1 - 58), sh)

    d.text((M, hy1 + 14), MAT_EN[material], font=F(12, True), fill=DIM)
    d.text((M, hy1 + 36), '%s　主体材质　·　%s 系列　·　%s' % (
        MAT_CN[material], main['name'], SHAPE_CN.get(shape, '')), font=F(14), fill=FAINT)

    # 主色色号条
    bx = M
    by = hy1 + 74
    d.rectangle([bx, by, bx + 34, by + 34], fill=tuple(int(v * 255) for v in np.clip(main['rgb'], 0, 1)),
                outline=LINE, width=1)
    boxes.append(dict(kind='main', i=0, x=bx, y=by, w=34, h=34, hex=main['hex']))
    d.text((bx + 46, by - 2), '主色  %s' % main['name'], font=F(16, True), fill=INK)
    d.text((bx + 46, by + 24), '%s   RGB %s   CMYK %s   L* %.0f' % (
        main['hex'], rgb_txt(main['rgb']), cmyk_str(main), _lstar(main['rgb'])),
        font=F(10, mono=True), fill=DIM)

    # 设计备注（数据全部来自本次生成结果，不编造）
    ls = [_lstar(p['rgb']) for p in pal]
    worst = min(rep['kinds'][k]['min'] for k in CC.CVD_ALL)
    worst_k = min(CC.CVD_ALL, key=lambda k: rep['kinds'][k]['min'])
    cr, _ = CC.contrast_white_black(main['rgb'])
    notes = [
        ('材质工艺', '%s · %s' % (MAT_CN[material], {
            'matte': '表面细腻无镜面反射，适合家居近距离观看',
            'frosted': '细颗粒磨砂，抑制指纹，握持手感干燥',
            'metal': '横向拉丝，随视角产生流动高光',
            'leather': '整张粒面，纹理深浅随光线变化',
            'gloss': '高光注塑，色相较哑光更艳，注意反光干扰',
            'wood': '木皮/木饰面，纹理随工艺变化明显，色差需按批次控制',
        }[material])),
        ('明度分布', 'L* %s，跨度 %.0f，层次%s' % (
            ' / '.join('%.0f' % v for v in ls), max(ls) - min(ls),
            '清晰' if max(ls) - min(ls) >= 25 else '偏平（同色系方案的正常表现）')),
        ('色觉友好', '最差条件下 %s型 ΔE00 %.1f%s' % (
            CC.CVD_CN[worst_k], worst, '，已达可辨识' if worst >= target else '，建议拉大明度差')),
        ('屏显与印刷', 'RGB/HEX 用于屏幕；CMYK 供打样参考，量产需以色号实物对色'),
    ]
    ny = hy1 + 136
    d.line([(M, ny - 16), (M + 636, ny - 16)], fill=LINE, width=1)
    d.text((M, ny), 'N O T E S', font=F(11, True), fill=FAINT)
    ny += 24
    for lab, txt in notes:
        d.text((M, ny), lab, font=F(11, True), fill=FAINT)
        d.text((M + 96, ny), txt, font=F(12), fill=DIM)
        ny += 26

    # ---------------- 右：色板 ----------------
    RX0, RX1 = 740, W - M
    d.text((RX0, 172), 'M A T E R I A L   &   C O L O U R', font=F(12, True), fill=FAINT)
    gap = 20
    cw = (RX1 - RX0 - gap * (n - 1)) // n
    ch = 224
    cy0 = 200
    fnum = F(13, True, mono=True)
    for i, p in enumerate(pal):
        cx0 = RX0 + i * (cw + gap)
        field = make_field(material, p['rgb'], max(cw, 8), max(ch, 8), seed=11 + i)
        paste_field(img, field, (cx0, cy0, cx0 + cw, cy0 + ch), 12,
                    text='%02d' % (i + 1), font=fnum, ink=_ink_on(p['rgb']))
        d.rounded_rectangle([cx0 + 0.5, cy0 + 0.5, cx0 + cw - 0.5, cy0 + ch - 0.5],
                            radius=12, outline=LINE, width=1)
        p['swatch_box'] = (cx0, cy0, cw, ch)
        boxes.append(dict(kind='swatch', i=i, x=cx0, y=cy0, w=cw, h=ch, hex=p['hex']))

    ty = cy0 + ch + 16
    for i, p in enumerate(pal):
        cx0 = p['swatch_box'][0]
        c = p['rgb']
        d.text((cx0 + 2, ty), p['name'], font=F(20, True), fill=INK)
        hint = p.get('trad_hint')
        sub = SRC_CN.get(p.get('src'), '')
        if hint:
            sub = (sub + '   传统色 ' + hint) if p.get('src') == 'common' else ('传统色 ' + hint)
        d.text((cx0 + 2, ty + 30), sub, font=F(10), fill=DIM)
        d.text((cx0 + 2, ty + 46), p['hex'], font=F(14, True, mono=True), fill=DIM)
        d.text((cx0 + 2, ty + 66), 'RGB ' + rgb_txt(c), font=F(10, mono=True), fill=FAINT)
        d.text((cx0 + 2, ty + 80), 'CMYK ' + cmyk_str(p), font=F(10, mono=True), fill=FAINT)
        d.text((cx0 + 2, ty + 94), 'L* %.0f' % _lstar(c), font=F(10, mono=True), fill=FAINT)

    # ---------------- 右：色觉关怀 ----------------
    vy_lab = 596
    d.line([(RX0, vy_lab - 16), (RX1, vy_lab - 16)], fill=LINE, width=1)
    d.text((RX0, vy_lab), 'C O L O U R   V I S I O N   S U P P O R T', font=F(12, True), fill=FAINT)
    d.text((RX1 - 300, vy_lab), '色觉可用性检查 · 阈值 ΔE00 %.0f' % target,
           font=F(10), fill=FAINT)

    rows = [('正常视觉', None)] + [(CC.CVD_CN[k] + '色盲', k) for k in CC.CVD_ALL]
    lb_w = 96
    bx0 = RX0 + lb_w
    bw = RX1 - bx0
    cg = 8
    chipw = (bw - cg * (n - 1)) // n
    chiph = 34
    ry = vy_lab + 26
    for lab, kind in rows:
        ok = (rep['normal'] if kind is None else rep['kinds'][kind])
        col = (150, 156, 170) if (kind is None or not ok['bad']) else WARN_C
        d.text((RX0, ry + 9), lab, font=F(11, True), fill=col)
        for i, p in enumerate(pal):
            base = p['rgb'] if kind is None else CC.simulate(p['rgb'], kind)
            field = make_field(material, base, max(chipw, 6), max(chiph, 6), seed=31 + i)
            x0 = bx0 + i * (chipw + cg)
            paste_field(img, field, (x0, ry, x0 + chipw, ry + chiph), 7,
                        text='%02d' % (i + 1), font=F(10, True, mono=True), ink=_ink_on(base))
            d.rounded_rectangle([x0 + 0.5, ry + 0.5, x0 + chipw - 0.5, ry + chiph - 0.5],
                                radius=7, outline=LINE, width=1)
        d.text((RX1 - 54, ry + 9), 'ΔE %.1f' % ok['min'], font=F(10, True, mono=True),
               fill=OK_C if ok['min'] >= target else WARN_C)
        ry += chiph + 10

    # 结论
    bad_pairs = []
    for k in CC.CVD_ALL:
        for (i, j), dv in rep['kinds'][k]['bad']:
            bad_pairs.append((dv, CC.CVD_CN[k], i, j))
    bad_pairs.sort()
    cvy = ry + 8
    if not bad_pairs:
        msg = '√ 四种视觉条件下，任意两色 ΔE00 均 ≥ %.0f，色卡可区分。' % target
        sub = '色块另附编号 01–%02d，避免仅靠颜色传递信息。' % n
        c1 = OK_C
    else:
        dv, kk, i, j = bad_pairs[0]
        msg = '▲ %s色觉下 第%02d与第%02d 色 ΔE00 仅 %.1f，容易混淆。' % (kk, i + 1, j + 1, dv)
        sub = '已开启明度自动分离' if cvd_fix else '建议开启「色觉增强」，用明度差把这一对拉开'
        c1 = WARN_C
    d.text((RX0, cvy), msg, font=F(12, True), fill=c1)
    d.text((RX0, cvy + 22), sub, font=F(11), fill=FAINT)

    # ---------------- 右：配色变体 ----------------
    vl = 874
    d.line([(RX0, vl - 14), (RX1, vl - 14)], fill=LINE, width=1)
    d.text((RX0, vl), 'C O L O U R   V A R I A N T S', font=F(12, True), fill=FAINT)
    vh = 272
    vy0 = vl + 26
    for i, p in enumerate(pal):
        cx0 = p['swatch_box'][0]
        v = render_vessel(p['rgb'], cw, vh, material, shape=shape,
                           pattern=pattern, pat_color=pat_rgb,
                           emboss_mm=emboss_mm, height_mm=height_mm,
                           custom_mask=custom_mask)
        img.paste(v, (cx0, vy0), v)
        d.text((cx0 + cw // 2 - 12, vy0 + vh + 8), '%02d' % (i + 1),
               font=F(11, True, mono=True), fill=FAINT)
        boxes.append(dict(kind='variant', i=i, x=cx0, y=vy0, w=cw, h=vh, hex=p['hex']))

    # ---------------- 页脚 ----------------
    fy = H - 58
    d.line([(M, fy - 16), (W - M, fy - 16)], fill=LINE, width=1)
    pat_txt = ''
    if pattern and pattern != 'none':
        pat_txt = '　纹样：%s（花纹色 色%02d）' % (
            PATTERNS.get(pattern, pattern), min(max(pat_idx, 0), len(pal) - 1) + 1)
        if emboss_mm and emboss_mm > 0:
            pat_txt += '　浮雕 %0.1fmm' % emboss_mm
    d.text((M, fy), '配色方案：%s　主色相 %d°　材质：%s　器型：%s　色数：%d%s'
           % (NAME_CN.get(scheme, ''), int(hue) % 360, MAT_CN[material],
              SHAPE_CN.get(shape, ''), n, pat_txt), font=F(12), fill=FAINT)
    d.text((W - M - 250, fy), 'CMFCard v0.3   %d colors' % (len(CC.COMMON) + len(CC._TRAD or [])),
           font=F(10, mono=True), fill=FAINT)

    rep['boxes'] = boxes
    rep['size'] = [W, H]
    os.makedirs(os.path.dirname(os.path.abspath(out)) or '.', exist_ok=True)
    img.save(out)
    return out, rep


# ------------------------------------------------------------------ 主程序
def main():
    ap = argparse.ArgumentParser(description='CMFCard 产品配色与材质方案卡')
    ap.add_argument('--hue', type=float, default=205, help='主色相 0~360')
    ap.add_argument('--n', type=int, default=4, help='配色数量 2~5')
    ap.add_argument('--scheme', default='analogous',
                    choices=['mono', 'analogous', 'complementary', 'triadic', 'split'])
    ap.add_argument('--material', default='matte',
                    choices=['matte', 'frosted', 'metal', 'leather', 'gloss'])
    ap.add_argument('--sat', type=float, default=0.55, help='饱和度 0~1')
    ap.add_argument('--val', type=float, default=0.80, help='明度 0~1')
    ap.add_argument('--shape', default='bottle', choices=list(SHAPES.keys()), help='器型')
    ap.add_argument('--name', default='曲水 · 陶瓷香薰器', help='产品名')
    ap.add_argument('--out', default='out/card.png')
    ap.add_argument('--no-snap', action='store_true', help='不吸附到色库（保留原始计算色）')
    ap.add_argument('--cvd-fix', action='store_true', help='开启色觉关怀自动增强（明度分离）')
    ap.add_argument('--cvd-target', type=float, default=12.0, help='可辨识色差阈值')
    ap.add_argument('--theme', default='dark', choices=['dark', 'light'],
                    help='版式主题：dark 深色海报 / light 浅色纸张')
    a = ap.parse_args()

    n = max(2, min(5, a.n))
    pal, hit = build_palette(a.hue, n, a.scheme, a.sat, a.val, snap=not a.no_snap)
    if a.cvd_fix:
        pal = CC.separate_for_cvd(pal, target=a.cvd_target)
        for p in pal:
            p['hex'] = hexs(p['rgb'])

    p, rep = build_card(pal, a.name, a.material, out=a.out, shape=a.shape,
                        scheme=a.scheme, hue=a.hue, cvd_fix=a.cvd_fix,
                        target=a.cvd_target, theme=a.theme)
    print('配色：%s（%d 色）　材质：%s　器型：%s'
          % (NAME_CN.get(a.scheme), n, MAT_CN[a.material], SHAPE_CN[a.shape]))
    print('色库吸附：%d/%d 色取自真实色库色' % (hit, n))
    for i, c in enumerate(pal):
        print('  %02d  %-6s  %s  RGB(%s)  CMYK %s  L* %.0f  [%s]'
              % (i + 1, c['name'], c['hex'], rgb_txt(c['rgb']),
                 cmyk_txt(c['rgb']), _lstar(c['rgb']), SRC_CN.get(c.get('src'), '')))
    print('  正常视觉最小色差 ΔE00 = %.1f' % rep['normal']['min'])
    for k in CC.CVD_ALL:
        r = rep['kinds'][k]
        print('  %-8s 最小色差 ΔE00 = %5.1f  %s'
              % (CC.CVD_CN[k], r['min'], '通过' if not r['bad'] else '有 %d 对易混' % len(r['bad'])))
    print('已保存 ->', p)


if __name__ == '__main__':
    main()

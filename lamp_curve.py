# -*- coding: utf-8 -*-
"""
LumiCurve · 灯具曲线与光斑模拟器
=================================
原理：灯罩母线用三次贝塞尔曲线描述，绕轴旋转即成灯罩。
      在轴截面内做二维几何光线追迹（镜面反射 + 朗伯漫反射），
      统计光线落到桌面上的落点半径，按旋转对称还原成地面照度分布。

    照度 E(R) = ΣW_i / (π(r₂² − r₁²))
    其中 W_i 是落在半径区间 [r₁, r₂) 内的光线权重。

诚实说明：这是二维截面 + 旋转对称近似，只算镜面反射和朗伯漫反射，
忽略折射、吸收、多次散射。用途是比较不同罩形的相对差异，不是光学级仿真。

用法：
    python lamp_curve.py --out out/base.png
    python lamp_curve.py --mouth 0.10 --top 0.03 --height 0.30 --waist 0.72 \
                         --flip 0.6 --bulb 0.72 --spec 0.9 --out out/deep.png
"""
import os
import sys
import csv
import math
import argparse
import numpy as np
from PIL import Image, ImageDraw, ImageFont

# Windows 控制台默认 GBK，统一成 UTF-8，否则中文会乱码/报错
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

KBEZ = 0.5522847498
FONT_BD = 'C:/Windows/Fonts/msyhbd.ttc'
FONT_RG = 'C:/Windows/Fonts/msyh.ttc'


def font(size, bold=False):
    try:
        return ImageFont.truetype(FONT_BD if bold else FONT_RG, size)
    except Exception:
        return ImageFont.load_default()


# ------------------------------------------------------------------ 母线
def build_profile(mouth=0.115, top=0.030, height=0.26, waist=0.82, flip=0.35, n=140):
    """返回母线折线 (n,2)，点序从罩口(下)到罩顶(上)，单位米"""
    rm, rt, h = mouth, top, height
    rw = (rm * 0.45 + rt * 0.55) * waist          # 腰部半径

    def bez(p0, c1, c2, p3, m):
        out = []
        for i in range(m):
            t = i / m
            mt = 1 - t
            out.append((mt**3 * p0[0] + 3 * mt**2 * t * c1[0] + 3 * mt * t**2 * c2[0] + t**3 * p3[0],
                        mt**3 * p0[1] + 3 * mt**2 * t * c1[1] + 3 * mt * t**2 * c2[1] + t**3 * p3[1]))
        return out

    P0 = (0.0, rm * (1.0 + 0.28 * flip))                 # 罩口（可外翻）
    P1 = (0.42 * h, rw)                                  # 腰
    P2 = (h, rt)                                         # 顶
    c1a = (0.16 * h, rm * (1.0 + 0.55 * flip))
    c2a = (0.30 * h, rw * 1.03)
    c1b = (0.60 * h, rw * 1.00)
    c2b = (0.84 * h, rt * 1.10)

    pts = bez(P0, c1a, c2a, P1, n)
    pts += bez(P1, c1b, c2b, P2, n)
    pts.append(P2)
    return np.array(pts, dtype=np.float64)


def profile_segments(prof):
    """母线是 (高度, 半径)，这里转成光线追迹用的笛卡尔 (x=半径, y=高度)。
    返回线段端点 A/B 和朝内的法线（指向轴线，即 x 变小方向）。"""
    P = np.stack([prof[:, 1], prof[:, 0]], axis=1)      # ← 关键：换成 (x=r, y=h)
    A = P[:-1]
    B = P[1:]
    s = B - A
    L = np.hypot(s[:, 0], s[:, 1])
    L[L < 1e-12] = 1e-12
    nx = -s[:, 1] / L
    ny = s[:, 0] / L
    flipmask = nx > 0                                   # 统一翻成指向轴线
    nx = np.where(flipmask, -nx, nx)
    ny = np.where(flipmask, -ny, ny)
    Nn = np.stack([nx, ny], axis=1)
    # 轴截面只建了右半边，左边要镜像补上，否则穿过轴线的光线会漏出去
    A = np.vstack([A, A * np.array([-1.0, 1.0])])
    B = np.vstack([B, B * np.array([-1.0, 1.0])])
    Nn = np.vstack([Nn, Nn * np.array([-1.0, 1.0])])
    return A, B, Nn


# ------------------------------------------------------------------ 追迹
def trace(prof, bulb=(0.0, 0.18), power=800.0, rays=3000, spec=0.85,
          max_bounce=8, ground=-0.60, seed=7, keep_paths=0):
    """二维截面蒙特卡洛追迹。返回 (落地半径, 权重, 示例路径)"""
    rng = np.random.default_rng(seed)
    A, B, N = profile_segments(prof)
    np_seg = len(A)
    m = int(rays)

    # 在轴截面里按角度均匀撒，但每根射线代表的立体角正比于 |cosθ|
    # （因为绕轴旋转一圈，靠近轴向的方向对应的立体角很小）
    th = rng.uniform(0.0, 2.0 * math.pi, m)
    cw = np.abs(np.cos(th))
    w = float(power) * cw / max(cw.sum(), 1e-12)
    p = np.tile(np.array(bulb, dtype=np.float64), (m, 1))
    d = np.stack([np.cos(th), np.sin(th)], axis=1)
    alive = np.ones(m, dtype=bool)

    land_r, land_w = [], []
    paths = []

    for bounce in range(max_bounce + 1):
        idx = np.where(alive)[0]
        if idx.size == 0:
            break
        pp = p[idx]
        dd = d[idx]
        # (M, S) 求交
        ap = A[None, :, :] - pp[:, None, :]              # (M,S,2)
        den = dd[:, 0:1] * (B[:, 1] - A[:, 1])[None, :] - dd[:, 1:2] * (B[:, 0] - A[:, 0])[None, :]
        with np.errstate(divide='ignore', invalid='ignore'):
            t = (ap[..., 0] * (B[:, 1] - A[:, 1])[None, :]
                 - ap[..., 1] * (B[:, 0] - A[:, 0])[None, :]) / den
            u = (ap[..., 0] * dd[:, 1:2] - ap[..., 1] * dd[:, 0:1]) / den
        valid = np.isfinite(t) & (t > 1e-6) & (u >= -1e-9) & (u <= 1.0 + 1e-9)
        t = np.where(valid, t, np.inf)
        tmin = t.min(axis=1)
        hit = np.isfinite(tmin)
        seg = t.argmin(axis=1)

        # 没打到罩壁的：看是不是往下穿过桌面
        miss = ~hit
        if miss.any():
            mpp = pp[miss]
            mdd = dd[miss]
            with np.errstate(divide='ignore', invalid='ignore'):
                tg = (ground - mpp[:, 1]) / mdd[:, 1]
            ok = np.isfinite(tg) & (tg > 0)
            if ok.any():
                q = mpp[ok] + tg[ok][:, None] * mdd[ok]
                land_r.append(np.abs(q[:, 0]))
                land_w.append(w[idx][miss][ok])
            alive[idx[miss]] = False

        if not hit.any():
            continue

        h_idx = idx[hit]
        hp = pp[hit] + tmin[hit][:, None] * dd[hit]
        hseg = seg[hit]
        hn = N[hseg]
        hd = dd[hit]
        # 反射
        dot = (hd * hn).sum(axis=1, keepdims=True)
        refl = hd - 2.0 * dot * hn

        rnd = rng.random(len(h_idx)) < spec
        # 漫反射：法线两侧均匀撒（二维朗伯）
        ang = rng.uniform(-math.pi / 2, math.pi / 2, len(h_idx))
        base_ang = np.arctan2(hn[:, 1], hn[:, 0])
        new_ang = base_ang + ang
        diff = np.stack([np.cos(new_ang), np.sin(new_ang)], axis=1)

        newd = np.where(rnd[:, None], refl, diff)
        newd /= np.linalg.norm(newd, axis=1, keepdims=True) + 1e-12

        if keep_paths and bounce < 4 and len(paths) < keep_paths:
            for k in range(min(keep_paths - len(paths), len(h_idx))):
                paths.append([tuple(pp[hit][k]), tuple(hp[k])])

        p[h_idx] = hp + newd * 1e-5
        d[h_idx] = newd

    if land_r:
        return np.concatenate(land_r), np.concatenate(land_w), paths
    return np.array([]), np.array([]), paths


# ------------------------------------------------------------------ 照度
def _smooth(y, sigma):
    if sigma <= 0 or len(y) < 5:
        return y
    r = int(3 * sigma)
    x = np.arange(-r, r + 1)
    k = np.exp(-0.5 * (x / sigma) ** 2)
    k /= k.sum()
    pad = np.pad(y, r, mode='edge')
    return np.convolve(pad, k, mode='same')[r:-r]


def illuminance(radii, weights, rmax=2.0, bins=300, sigma=3.5, r_floor=0.03):
    """先把落在每个环里的光通量平滑，再除以环面积。
    注意顺序：不能先除面积再平滑，那样会把中心小环的巨大数值扩散到整条曲线上。
    r_floor 以内不按环面积算——那里面积趋零、照度会发散，是公式奇点不是物理结果，
    统一取 r_floor 处的值当平台。"""
    edges = np.linspace(0.0, rmax, bins + 1)
    binw = rmax / bins
    area = math.pi * (edges[1:] ** 2 - edges[:-1] ** 2)
    idx = np.digitize(radii, edges) - 1
    flux = np.zeros(bins)
    ok = (idx >= 0) & (idx < bins)
    np.add.at(flux, idx[ok], weights[ok])
    flux = _smooth(flux, sigma)
    cen = 0.5 * (edges[1:] + edges[:-1])
    R = np.maximum(cen, r_floor)
    E = flux / np.maximum(2.0 * math.pi * R * binw, 1e-12)
    inner = cen < r_floor
    if inner.any() and (~inner).any():
        E[inner] = E[~inner][0]
    return cen, E, area


def metrics(cen, E, area, target=0.5):
    """峰值、中心、目标半径内的"通量加权"平均照度与均匀度"""
    peak = float(E[cen >= 0.03].max()) if (cen >= 0.03).any() else float(E.max())
    def at(r):
        return float(np.interp(r, cen, E))
    sel = cen <= target
    if not sel.any():
        return dict(peak=peak, center=at(0.05), avg=0.0, edge_min=0.0,
                    uniformity=0.0, hit_radius=0.0)
    flux_in = float((E[sel] * area[sel]).sum())
    avg = flux_in / (math.pi * target * target)      # 面内平均 = 总通量 / 圆面积
    p5 = float(np.percentile(E[sel], 5))
    thr = peak * 0.10
    below = np.where(E < thr)[0]
    hr = float(cen[below[0]]) if len(below) else float(cen[-1])
    return dict(peak=peak, center=at(0.05), avg=avg, edge_min=p5,
                uniformity=(p5 / avg) if avg > 1e-9 else 0.0, hit_radius=hr)


# ------------------------------------------------------------------ 出图
def warm_color(t):
    """0→暗橙 0.5→亮橙 1→近白"""
    t = float(np.clip(t, 0, 1))
    stops = [(0.00, (10, 8, 16)), (0.15, (60, 22, 8)), (0.35, (150, 62, 12)),
             (0.55, (222, 122, 26)), (0.75, (255, 186, 74)),
             (0.90, (255, 233, 160)), (1.00, (255, 255, 246))]
    for i in range(len(stops) - 1):
        a, ca = stops[i]
        b, cb = stops[i + 1]
        if a <= t <= b:
            k = (t - a) / (b - a)
            return tuple(int(ca[j] + (cb[j] - ca[j]) * k) for j in range(3))
    return stops[-1][1]


def render_pool(cen, E, out, size=820, rmax=1.2, target=0.5, title=''):
    img = Image.new('RGB', (size, size), (14, 14, 18))
    px = img.load()
    poke = E[cen >= 0.03] if (cen >= 0.03).any() else E
    epeak = max(float(poke.max()), 1e-9)
    lo = math.log10(max(epeak * 1.2e-2, 1.0))
    hi = math.log10(epeak)
    cx = cy = size / 2
    scale = (size * 0.47) / rmax
    bg = (14, 14, 18)
    r_fade0 = rmax * 0.86
    for y in range(size):
        dy = (y - cy) / scale
        for x in range(size):
            dx = (x - cx) / scale
            r = math.hypot(dx, dy)
            if r > rmax:
                continue
            e = float(np.interp(r, cen, E))
            t = (math.log10(max(e, 1e-6)) - lo) / (hi - lo)
            c = warm_color(max(0.0, min(1.0, t)))
            if r > r_fade0:
                k = (r - r_fade0) / (rmax - r_fade0)
                k = k * k * (3 - 2 * k)
                c = tuple(int(c[i] * (1 - k) + bg[i] * k) for i in range(3))
            px[x, y] = c
    d = ImageDraw.Draw(img, 'RGBA')

    # 等照度圈（只画落在画面内、且有意义的）
    lv_all = [3000, 2000, 1500, 1000, 700, 500, 300, 200, 150, 100, 70, 50, 30, 20, 10]
    lv_ok = [v for v in lv_all if epeak * 0.94 > v > epeak / 400.0]
    shown = 0
    last_r = -1e9
    for lv in lv_ok:
        below = np.where(E < lv)[0]
        if not len(below):
            continue
        rr = float(cen[below[0]]) * scale
        if rr < 18 or rr > size * 0.47:
            continue
        d.ellipse([cx - rr, cy - rr, cx + rr, cy + rr],
                  outline=(255, 255, 255, 105), width=1)
        if rr - last_r < 30:
            continue
        last_r = rr
        ang = -2.30 + 1.05 * (shown % 4)          # 四个方位轮流放标签
        lx, ly = cx + rr * math.cos(ang), cy + rr * math.sin(ang)
        d.text((lx + 6, ly - 9), '%d lx' % lv, fill=(255, 255, 255, 220), font=font(13))
        shown += 1

    # 目标半径
    rr = target * scale
    d.ellipse([cx - rr, cy - rr, cx + rr, cy + rr], outline=(120, 200, 255, 215), width=2)
    d.text((cx - 46, cy + rr - 26), '目标 Ø%.1f m' % (target * 2),
           fill=(150, 215, 255), font=font(15, True))
    d.line([(44, size - 46), (44 + 0.5 * scale, size - 46)], fill=(225, 225, 235), width=3)
    d.text((44, size - 74), '0.5 m', fill=(225, 225, 235), font=font(15))
    d.text((26, 20), title or '地面照度分布（俯视）', fill=(240, 240, 250), font=font(24, True))
    d.text((26, 52), '峰值 %.0f lux　等照度圈单位 lux' % epeak,
           fill=(255, 190, 110), font=font(16))
    return img


def _seg_hit(p, d, A, B, eps=1e-7):
    """单条射线与所有线段求交，返回 (t, 段号) 或 None"""
    s = B - A
    ap = A - p
    den = d[0] * s[:, 1] - d[1] * s[:, 0]
    with np.errstate(divide='ignore', invalid='ignore'):
        t = (ap[:, 0] * s[:, 1] - ap[:, 1] * s[:, 0]) / den
        u = (ap[:, 0] * d[1] - ap[:, 1] * d[0]) / den
    ok = np.isfinite(t) & (t > eps) & (u >= 0) & (u <= 1)
    if not ok.any():
        return None
    t = np.where(ok, t, np.inf)
    i = int(t.argmin())
    return float(t[i]), i


def trace_paths(prof, bulb, n_rays=40, max_bounce=7, ground=-0.6):
    """画示意图用：按固定角度发射射线，只做镜面反射，路径干净"""
    A, B, N = profile_segments(prof)
    paths = []
    for k in range(n_rays):
        ang = -math.pi / 2 + math.pi * (k + 0.5) / n_rays      # 只撒向下半圈
        p = np.array(bulb, dtype=float)
        d = np.array([math.cos(ang), math.sin(ang)], dtype=float)
        d /= np.linalg.norm(d)
        seg_pts = [tuple(p)]
        for _ in range(max_bounce + 1):
            hit = _seg_hit(p, d, A, B)
            if hit is None:
                if d[1] < -1e-6:
                    tg = (ground - p[1]) / d[1]
                    if tg > 0:
                        seg_pts.append(tuple(p + tg * d))
                break
            t, i = hit
            p = p + t * d
            seg_pts.append(tuple(p))
            n = N[i]
            d = d - 2.0 * float(np.dot(d, n)) * n
            d /= np.linalg.norm(d)
            p = p + d * 1e-5
        if len(seg_pts) > 1:
            paths.append(seg_pts)
    return paths


def render_raytrace(prof, bulb, paths, out, size=820, title='', spec=0.85, height=0.26, ground=-0.6):
    img = Image.new('RGB', (size, size), (250, 250, 253))
    d = ImageDraw.Draw(img, 'RGBA')
    rmax = max(float(prof[:, 1].max()), 0.14)
    span_x = rmax * 2.0
    h_extra = height * 0.42                    # 罩口以下可见的深度
    top_y, bot_y = 116, size - 56
    sc = min((size - 130) / (span_x * 2), (bot_y - top_y) / (height + h_extra))
    cx = size / 2
    y_min = -h_extra

    def T(x, y):
        return (cx + x * sc, top_y + (height - y) * sc)

    def clip(sp):
        """把折线裁到 y >= y_min"""
        out_pts = []
        for i, p in enumerate(sp):
            y = p[1]
            if y >= y_min:
                out_pts.append(p)
            elif out_pts:
                a = out_pts[-1]
                if a[1] > y_min:
                    k = (a[1] - y_min) / (a[1] - y)
                    out_pts.append((a[0] + (p[0] - a[0]) * k, y_min))
                break
        return out_pts

    # 光线（按反射次数换色）
    cols = [(255, 186, 70), (255, 150, 40), (246, 120, 30), (232, 96, 32), (208, 76, 40)]
    for sp in paths:
        cp = clip(sp)
        for i in range(len(cp) - 1):
            c = cols[min(i, len(cols) - 1)]
            d.line([T(*cp[i]), T(*cp[i + 1])], fill=c + (200,), width=2)

    # 灯罩
    pts = [T(x, y) for (y, x) in prof]
    d.line(pts, fill=(58, 60, 74), width=5)
    d.line([(2 * cx - pxx, pyy) for (pxx, pyy) in pts], fill=(58, 60, 74), width=5)
    d.line([pts[0], (2 * cx - pts[0][0], pts[0][1])], fill=(58, 60, 74), width=3)
    d.line([pts[-1], (2 * cx - pts[-1][0], pts[-1][1])], fill=(58, 60, 74), width=3)
    # 轴线
    d.line([T(0, height + 0.02), T(0, y_min)], fill=(210, 214, 224), width=1)

    # 灯泡
    bx, by = T(*bulb)
    for r, a in ((30, 32), (20, 70), (11, 255)):
        d.ellipse([bx - r, by - r, bx + r, by + r],
                  fill=(255, 232, 150, a) if a < 255 else (255, 232, 150))
    d.ellipse([bx - 4, by - 4, bx + 4, by + 4], fill=(255, 252, 230))

    # 底部提示：桌面在更下面
    for x in range(0, size, 14):
        d.line([(x, bot_y + 16), (min(x + 7, size), bot_y + 16)], fill=(190, 194, 204), width=1)
    d.text((26, bot_y + 22), '桌面在罩口下方 %.0f mm 处（图中已裁切）' % (abs(ground) * 1000),
           fill=(130, 136, 152), font=font(15))

    d.line([T(0, 0), T(prof[0][1], 0)], fill=(90, 140, 200), width=1)
    d.text((cx + 10, T(0, 0)[1] + 6), '罩口 R=%.0f mm' % (prof[0][1] * 1000),
           fill=(70, 120, 180), font=font(15))

    d.text((26, 22), title or '轴截面光线追迹', fill=(40, 44, 58), font=font(24, True))
    d.text((26, 56), '按固定角度发射，只计镜面分量的路径示意',
           fill=(120, 126, 142), font=font(16))
    d.text((26, 80), '灯罩高 %.0f mm　灯泡高度 %.0f mm　罩口半径 %.0f mm'
           % (height * 1000, bulb[1] * 1000, prof[0][1] * 1000),
           fill=(120, 126, 142), font=font(16))
    return img


def render_curve(cen, E, out, size=(760, 520), target=0.5, title='照度沿半径衰减'):
    W, H = size
    img = Image.new('RGB', (W, H), (250, 251, 253))
    d = ImageDraw.Draw(img)
    ml, mr, mt, mb = 80, 40, 84, 66
    x0, x1 = ml, W - mr
    y0, y1 = H - mb, mt
    rmax = max(1.0, float(cen[-1]))
    emax = max(float(E[cen >= 0.02].max()) if (cen >= 0.02).any() else float(E.max()), 1e-9) * 1.08

    def PX(r):
        return x0 + (x1 - x0) * (r / rmax)

    def PY(e):
        return y0 + (y1 - y0) * (e / emax)

    for i in range(6):
        e = emax * i / 5
        d.line([(x0, PY(e)), (x1, PY(e))], fill=(230, 233, 240), width=1)
        d.text((12, PY(e) - 9), '%.0f' % e, fill=(130, 136, 150), font=font(15))
    for i in range(6):
        r = rmax * i / 5
        d.line([(PX(r), y0), (PX(r), y1)], fill=(230, 233, 240), width=1)
        d.text((PX(r) - 14, y0 + 10), '%.1f' % r, fill=(130, 136, 150), font=font(15))
    # 目标圈
    d.line([(PX(target), y1), (PX(target), y0)], fill=(120, 200, 255), width=2)
    d.text((PX(target) + 6, y1 + 30), '目标半径 %.2f m' % target,
           fill=(70, 150, 210), font=font(15))
    # 曲线
    st = int(np.searchsorted(cen, 0.012))
    pts = [(PX(cen[i]), PY(E[i])) for i in range(st, len(cen))]
    d.line(pts, fill=(230, 120, 30), width=3)
    d.text((28, 22), title, fill=(40, 44, 58), font=font(24, True))
    d.text((28, 56), '横轴 半径 (m)　纵轴 照度 (lux)', fill=(120, 126, 142), font=font(16))
    return img


# ------------------------------------------------------------------ 主程序
def run(a):
    prof = build_profile(a.mouth, a.top, a.height, a.waist, a.flip)
    bulb_y = a.height * a.bulb
    radii, weights, _ = trace(prof, bulb=(0.0, bulb_y), power=a.power,
                              rays=a.rays, spec=a.spec, ground=-a.dist)
    if radii.size == 0:
        raise SystemExit('一条光线都没落到桌面上，参数可能不合理')
    cen, E, area = illuminance(radii, weights, rmax=max(1.8, a.target * 3.2), bins=160, sigma=8.0)
    m = metrics(cen, E, area, a.target)

    os.makedirs(os.path.dirname(os.path.abspath(a.out)) or '.', exist_ok=True)
    base = os.path.splitext(a.out)[0]
    render_pool(cen, E, base + '_pool.png', rmax=max(1.2, a.target * 2.2),
                target=a.target, title=a.title or '地面照度分布（俯视）').save(base + '_pool.png')
    render_curve(cen, E, base + '_curve.png', target=a.target,
                 title=a.title or '照度沿半径衰减').save(base + '_curve.png')
    if a.save_ray:
        paths = trace_paths(prof, (0.0, bulb_y), n_rays=44, ground=-a.dist)
        render_raytrace(prof, (0.0, bulb_y), paths, base + '_ray.png',
                        title=a.title or '轴截面光线追迹',
                        spec=a.spec, height=a.height, ground=-a.dist).save(base + '_ray.png')

    with open(base + '_report.csv', 'w', newline='', encoding='utf-8-sig') as f:
        w = csv.writer(f)
        w.writerow(['radius_m', 'illuminance_lux'])
        for r, e in zip(cen, E):
            w.writerow(['%.4f' % r, '%.2f' % e])

    print('罩口半径 %.3f m   罩高 %.3f m   灯泡高度 %.3f m   桌面距离 %.3f m'
          % (prof[0][1], a.height, bulb_y, a.dist))
    print('射线 %d 条，落到桌面 %d 条' % (a.rays, radii.size))
    print('峰值 %.0f lux   中心(0.05m) %.0f lux' % (m['peak'], m['center']))
    print('目标半径 %.2f m 内：平均 %.0f lux，5%%分位 %.0f lux，均匀度 %.2f'
          % (a.target, m['avg'], m['edge_min'], m['uniformity']))
    print('有效照明半径（降到峰值10%%）%.2f m' % m['hit_radius'])
    print('输出：')
    for suf in ['_pool.png', '_curve.png'] + (['_ray.png'] if a.save_ray else []):
        print('   ' + base + suf)
    print('   ' + base + '_report.csv')
    return m


def main():
    ap = argparse.ArgumentParser(description='LumiCurve 灯具曲线与光斑模拟器')
    ap.add_argument('--mouth', type=float, default=0.115, help='罩口半径 m')
    ap.add_argument('--top', type=float, default=0.030, help='罩顶半径 m')
    ap.add_argument('--height', type=float, default=0.260, help='灯罩高度 m')
    ap.add_argument('--waist', type=float, default=0.82, help='收腰系数 0.5~1.2')
    ap.add_argument('--flip', type=float, default=0.35, help='罩口外翻 0~1')
    ap.add_argument('--bulb', type=float, default=0.68, help='灯泡高度占罩高比例')
    ap.add_argument('--spec', type=float, default=0.85, help='内壁镜面反射率 0~1')
    ap.add_argument('--power', type=float, default=800.0, help='光通量 lm')
    ap.add_argument('--rays', type=int, default=12000, help='追迹射线数')
    ap.add_argument('--dist', type=float, default=0.60, help='灯罩口到桌面距离 m')
    ap.add_argument('--target', type=float, default=0.50, help='目标照明半径 m')
    ap.add_argument('--title', default='', help='图标题')
    ap.add_argument('--save-ray', action='store_true', help='额外输出光线追迹图')
    ap.add_argument('--out', default='out/lamp.png')
    run(ap.parse_args())


if __name__ == '__main__':
    main()

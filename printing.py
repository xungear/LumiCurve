# -*- coding: utf-8 -*-
"""
printing · 3D 打印支持
=====================
把回转体母线做成**带壁厚的封闭实体**，导出 STL，并给出可打印性报告。

单位换算：内部母线是归一化的（高度 1），导出时按用户给的打印高度（mm）缩放。
STL 采用 **Z 轴朝上**（切片软件的约定），底面落在 z=0。
"""
import io
import struct
import math
import numpy as np
from PIL import Image, ImageFilter

import cmf_card as MC

PLA_DENSITY = 1.24      # g/cm³

# 这些器型外形是封闭的（没有开口），但仍然是薄壁壳体 —— 不是实心块
CLOSED_SHAPES = {'speaker', 'globe'}


# ------------------------------------------------------------------ 母线
def shell_profile(pts, wall, closed=False, inner_stride=1):
    """
    把「外轮廓 (y, r)」扩成闭合的 (r, y) 环线：
        底面圆心 → 外底面 → 外壁 → 口沿 → 内壁 → 内底面
    与外轮廓等距偏移 wall（沿母线法向），并做下限保护，避免自交。

    inner_stride > 1 时内壁抽稀（浮雕只需要外壁密集，内壁看不见还占三角面）。
    """
    Q = np.array([[max(p[1], 1e-4), p[0]] for p in pts], dtype=np.float64)  # (r, y)
    N = len(Q)

    inner = np.zeros_like(Q)
    for i in range(N):
        a = Q[max(0, i - 1)]
        b = Q[min(N - 1, i + 1)]
        t = b - a
        L = math.hypot(t[0], t[1]) or 1.0
        t /= L
        # 外法线：把 (ty, -tx) 调成 r 分量为正的那一支
        nx, ny = t[1], -t[0]
        if nx < 0:
            nx, ny = -nx, -ny
        r = Q[i][0] - wall * nx
        y = Q[i][1] - wall * ny
        inner[i] = (max(r, wall * 0.55), min(max(y, wall * 0.55), 1.0))

    loop = [(0.0, 0.0)]
    loop.append((Q[0][0], Q[0][1]))
    for i in range(1, N):
        loop.append((Q[i][0], Q[i][1]))
    if closed:
        # 顶部封口：外壁先收到轴心，再跳到内壁顶端的轴心
        loop.append((0.0, 1.0))
        loop.append((0.0, inner[N - 1][1]))
    # 内壁：从口沿往回走，按 inner_stride 抽稀（首尾必须保留，否则不闭合）
    order = list(range(N - 1, -1, -int(max(1, inner_stride))))
    if order[-1] != 0:
        order.append(0)
    for i in order:
        loop.append((inner[i][0], inner[i][1]))
    loop.append((0.0, inner[0][1]))
    return np.array(loop, dtype=np.float64)


# ------------------------------------------------------------------ 网格
def lathe_mesh(loop, segments=96, height_mm=300.0, rad_off=None):
    """
    (r, y) 闭合环线 → 三角网格（Z 轴朝上）。

    轴心上的点（r≈0）**共用一个顶点**：否则 96 个位置重合但拓扑独立的顶点
    会让轴心的三角扇不是流形，STL 检查会报"不封闭"。

    rad_off: (nP, S) 的径向偏移，单位 mm —— 浮雕就是从外壁往外挤出这么多。
             拓扑完全不变，所以做完浮雕依然水密。
    """
    S = int(segments)
    P = np.asarray(loop, dtype=np.float64)
    nP = P.shape[0]
    ang = np.linspace(0.0, 2.0 * math.pi, S, endpoint=False)
    ca, sa = np.cos(ang), np.sin(ang)
    hmm = float(height_mm)

    if rad_off is None:
        rad_off = np.zeros((nP, S), dtype=np.float64)
    else:
        rad_off = np.asarray(rad_off, dtype=np.float64)
        if rad_off.shape != (nP, S):
            raise ValueError('rad_off shape %r != %r' % (rad_off.shape, (nP, S)))

    AXIS = 1e-6
    is_axis = np.array([P[i][0] < AXIS for i in range(nP)])
    n_axis = int(is_axis.sum())

    # 半径矩阵：外壁加浮雕偏移，内壁/底面为 0
    R = P[:, 0][:, None] * hmm + rad_off             # (nP, S)
    Z = P[:, 1] * hmm

    verts = np.empty(((nP - n_axis) * S + n_axis, 3), dtype=np.float64)
    base = np.zeros(nP, dtype=np.int64)
    cur = 0
    for i in range(nP):
        base[i] = cur
        if is_axis[i]:
            verts[cur] = (0.0, 0.0, Z[i])
            cur += 1
        else:
            verts[cur:cur + S, 0] = R[i] * ca
            verts[cur:cur + S, 1] = R[i] * sa
            verts[cur:cur + S, 2] = Z[i]
            cur += S

    # 顶点索引矩阵 IX[i, k]
    ks = np.arange(S)
    IX = base[:, None] + ks[None, :]
    IX[is_axis] = base[is_axis][:, None]

    nxt = np.arange(1, S + 1) % S
    A = IX
    B = IX[:, nxt]
    jrow = (np.arange(nP) + 1) % nP
    C = IX[jrow]
    D = IX[jrow][:, nxt]

    keep1 = ~is_axis
    keep2 = ~is_axis[jrow]
    f1 = np.stack([A, C, B], axis=-1)[keep1].reshape(-1, 3)
    f2 = np.stack([B, C, D], axis=-1)[keep2].reshape(-1, 3)
    faces = np.vstack([f1, f2]).astype(np.int64)
    return verts, faces


# ------------------------------------------------------------------ 浮雕
def _bilinear(A, rows, cols):
    """把 (nR, nC) 的求值点映射到 A 上做双线性采样 → (nR, nC)"""
    H, W = A.shape
    r0 = np.clip(np.floor(rows).astype(np.int64), 0, H - 2)
    c0 = np.clip(np.floor(cols).astype(np.int64), 0, W - 2)
    dr = (np.asarray(rows, float) - r0)[:, None]
    dc = (np.asarray(cols, float) - c0)[None, :]
    a = A[r0][:, c0]
    b = A[r0][:, c0 + 1]
    c = A[r0 + 1][:, c0]
    d = A[r0 + 1][:, c0 + 1]
    return (a * (1 - dc) + b * dc) * (1 - dr) + (c * (1 - dc) + d * dc) * dr


def emboss_map(pattern, y_vals, segs, emboss_mm, rbar=0.2, blur_px=2.8,
               custom_mask=None):
    """
    花纹遮罩（512×512，row0 = 口径）→ 外壁每个 (母线点, 周向段) 的径向偏移(mm)。

    先做高斯模糊把二值遮罩变成高度场：边缘是斜的，打印出来是**浮雕**而不是
    一刀切的台阶，切片也不会出现悬空的峭壁。
    """
    if not pattern or pattern == 'none' or not emboss_mm or emboss_mm <= 0:
        return None
    m = MC.pattern_mask(pattern, rbar, custom_mask)
    img = Image.fromarray((np.asarray(m) * 255).astype(np.uint8), 'L')
    if blur_px:
        img = img.filter(ImageFilter.GaussianBlur(float(blur_px)))
    A = np.asarray(img, dtype=np.float64) / 255.0

    PS = A.shape[0]
    ys = np.clip(np.asarray(y_vals, dtype=np.float64), 0.0, 1.0)
    rows = (1.0 - ys) * (PS - 1)                       # 物理高度 → 遮罩行
    # 与 three.js / 方案卡统一：正面(φ=0) 对应 a=90°，即 u=0
    frac = (0.25 - np.arange(int(segs)) / float(segs)) % 1.0
    cols = frac * (PS - 1)
    Hf = _bilinear(A, rows, cols)
    return Hf * float(emboss_mm)


# ------------------------------------------------------------------ STL
def stl_bytes(verts, faces, name='LumiCurve'):
    """二进制 STL"""
    V = np.asarray(verts, dtype=np.float32)
    F = np.asarray(faces, dtype=np.int64)
    v0 = V[F[:, 0]]
    v1 = V[F[:, 1]]
    v2 = V[F[:, 2]]
    nrm = np.cross(v1 - v0, v2 - v0)
    ln = np.linalg.norm(nrm, axis=1, keepdims=True)
    ln[ln < 1e-12] = 1.0
    nrm = nrm / ln

    head = name.encode('ascii', 'replace')[:80].ljust(80, b' ')
    out = io.BytesIO()
    out.write(head)
    out.write(struct.pack('<I', len(F)))
    rec = np.zeros(len(F), dtype=[
        ('n', '<f4', 3), ('a', '<f4', 3), ('b', '<f4', 3), ('c', '<f4', 3),
        ('attr', '<u2')])
    rec['n'] = nrm
    rec['a'] = v0
    rec['b'] = v1
    rec['c'] = v2
    out.write(rec.tobytes())
    return out.getvalue()


# ------------------------------------------------------------------ 检测
def report(verts, faces, wall_mm, height_mm):
    V = np.asarray(verts, dtype=np.float64)
    F = np.asarray(faces, dtype=np.int64)

    # 体积（散度定理，法线朝外）
    v0, v1, v2 = V[F[:, 0]], V[F[:, 1]], V[F[:, 2]]
    vol = float(np.einsum('ij,ij->i', v0, np.cross(v1, v2)).sum() / 6.0)
    vol = abs(vol) / 1000.0                        # mm³ → cm³

    # 表面积
    ar = 0.5 * np.linalg.norm(np.cross(v1 - v0, v2 - v0), axis=1).sum()
    ar_mm2 = float(ar)
    ar_cm2 = ar_mm2 / 100.0

    lo, hi = V.min(axis=0), V.max(axis=0)
    size = hi - lo

    # 壳体本身就是实心壁，体积 × 密度 就是耗材，不要再乘填充率
    vol_cm3 = vol
    mass_g = vol_cm3 * PLA_DENSITY

    # 真实的水密检查：每条边必须恰好被两个三角面共用（封闭流形）
    e = {}
    for tri in F:
        for k in range(3):
            a_, b_ = int(tri[k]), int(tri[(k + 1) % 3])
            key = (a_, b_) if a_ < b_ else (b_, a_)
            e[key] = e.get(key, 0) + 1
    closed = all(v == 2 for v in e.values())
    bad_edges = sum(1 for v in e.values() if v != 2)

    return dict(
        triangles=int(len(F)), verts=int(len(V)),
        size_mm=[round(float(size[0]), 1), round(float(size[1]), 1),
                 round(float(size[2]), 1)],
        volume_cm3=round(vol_cm3, 1),
        surface_cm2=round(ar_cm2, 1),
        wall_mm=round(wall_mm, 2),
        mass_g=round(mass_g, 1),
        watertight=bool(closed),
        edges=int(len(e)),
        bad_edges=int(bad_edges),
    )


def overhang(loop, wall):
    """
    母线段的倾角：dr<0（越往上越收）时，法向朝下，是典型的悬垂。
    返回 (最大倾角°, 对应的 y 位置 0~1)
    """
    P = np.asarray(loop, dtype=np.float64)
    best, pos = 0.0, 0.0
    for i in range(len(P) - 1):
        dr = P[i + 1][0] - P[i][0]
        dy = P[i + 1][1] - P[i][1]
        if dy <= 1e-9:
            continue
        if dr < -1e-9:
            ang = math.degrees(math.atan2(-dr, dy))
            if ang > best:
                best, pos = ang, (P[i][1] + P[i + 1][1]) / 2.0
    return best, pos


def make(shape='vase', wall_mm=3.0, height_mm=300.0, segments=96,
         pattern='none', emboss_mm=0.0, custom_mask=None):
    """一站式：母线 → 壳体 → 网格 → STL + 报告（壁厚按毫米给）"""
    wall = max(0.004, min(0.06, float(wall_mm) / float(height_mm)))
    prof_full = MC.vessel_profile(shape)
    closed = shape in CLOSED_SHAPES

    emb = float(emboss_mm or 0.0)
    use_emb = bool(pattern and pattern != 'none' and emb > 0.01)
    if use_emb:
        # 浮雕要分辨率：母线全保留、周向加密、内壁抽稀
        prof = prof_full
        segs = max(int(segments), 288)
        stride = 4
    else:
        # 抽稀：STL 不需要 240 个母线点，一半足够，三角面数直接减半
        step = 2 if len(prof_full) > 120 else 1
        prof = prof_full[::step]
        if not np.allclose(prof[-1], prof_full[-1]):
            prof = np.vstack([prof, prof_full[-1]])
        segs = int(segments)
        stride = 1

    loop = shell_profile(prof, wall, closed, inner_stride=stride)

    rad = None
    if use_emb:
        nO = len(prof)
        rbar = float(np.mean(prof[:, 1]))
        off = emboss_map(pattern, prof[:, 0], segs, emb, rbar,
                         custom_mask=custom_mask)
        rad = np.zeros((len(loop), segs), dtype=np.float64)
        rad[1:1 + nO, :] = off                    # 环线前 nO 点就是外壁
    verts, faces = lathe_mesh(loop, segs, height_mm, rad)
    data = stl_bytes(verts, faces, 'LumiCurve_%s' % shape)
    rep = report(verts, faces, wall * height_mm, height_mm)
    ang, pos = overhang(loop, wall)
    rep['overhang_deg'] = round(ang, 1)
    rep['overhang_at'] = round(pos, 2)
    rep['need_support'] = ang > 45.0
    rep['shape'] = shape
    rep['closed'] = bool(closed)
    rep['lid'] = '封闭壳体' if closed else '开口容器'
    rep['height_mm'] = height_mm
    rep['max_dia_mm'] = round(float(np.max([p[1] for p in prof]) * 2 * height_mm), 1)
    rep['pattern'] = pattern if use_emb else 'none'
    rep['emboss_mm'] = round(emb, 2) if use_emb else 0.0
    rep['segments'] = int(segs)
    if use_emb:
        rep['emboss_note'] = '浮雕实测最大隆起 %.2f mm（迷宫纹与地面无悬空台阶）' % (
            float(np.max(rad)) if rad is not None else 0.0)
    return data, rep

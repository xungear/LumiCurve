# -*- coding: utf-8 -*-
"""
cmf_color · 色彩引擎
====================
1. 中文色名：把任意 RGB 匹配到「常用色名」+「中国传统色」两个色库，
   匹配在 CIELAB 空间用 CIEDE2000 做最近邻 —— 名字和颜色一定对得上，
   不会出现「明明是浅蓝却叫群青」这种事。
2. 色号：HEX / RGB / CMYK。
3. 色觉关怀：Machado(2009) 三型色盲模拟 + 模拟后色差检查 + 明度自动分离。
"""
import os
import json
import math
import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
_TABLE_FILE = os.path.join(_HERE, 'cn_colors.json')


# ================================================================ 基础色彩换算
def to_lin(c):
    """sRGB(0~1) -> 线性 RGB"""
    c = np.clip(np.asarray(c, dtype=np.float64), 0, 1)
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def to_srgb(c):
    """线性 RGB -> sRGB(0~1)"""
    c = np.clip(np.asarray(c, dtype=np.float64), 0, 1)
    return np.where(c <= 0.0031308, c * 12.92, 1.055 * np.power(c, 1 / 2.4) - 0.055)


_M_RGB2XYZ = np.array([[0.4124564, 0.3575761, 0.1804375],
                       [0.2126729, 0.7151522, 0.0721750],
                       [0.0193339, 0.1191920, 0.9503041]])
_WHITE = np.array([0.95047, 1.00000, 1.08883])


def rgb2lab(rgb):
    """sRGB(0~1, 可为 N×3) -> CIELAB"""
    a = np.asarray(rgb, dtype=np.float64)
    shape = a.shape
    flat = a.reshape(-1, 3)
    xyz = to_lin(flat) @ _M_RGB2XYZ.T / _WHITE
    d = 6.0 / 29.0
    f = np.where(xyz > d ** 3, np.cbrt(xyz), xyz / (3 * d * d) + 4.0 / 29.0)
    lab = np.empty_like(f)
    lab[:, 0] = 116 * f[:, 1] - 16
    lab[:, 1] = 500 * (f[:, 0] - f[:, 1])
    lab[:, 2] = 200 * (f[:, 1] - f[:, 2])
    return lab.reshape(shape)


def lab2rgb(lab):
    a = np.asarray(lab, dtype=np.float64)
    shape = a.shape
    flat = a.reshape(-1, 3)
    fy = (flat[:, 0] + 16) / 116.0
    fx = fy + flat[:, 1] / 500.0
    fz = fy - flat[:, 2] / 200.0
    d = 6.0 / 29.0
    finv = lambda t: np.where(t > d, t ** 3, 3 * d * d * (t - 4.0 / 29.0))
    xyz = np.stack([finv(fx), finv(fy), finv(fz)], axis=1) * _WHITE
    lin = xyz @ np.linalg.inv(_M_RGB2XYZ).T
    return to_srgb(lin).reshape(shape)


def _hex2rgb01(h):
    h = h.lstrip('#')
    return np.array([int(h[i:i + 2], 16) for i in (0, 2, 4)], dtype=np.float64) / 255.0


def hexs(c):
    c = np.clip(np.asarray(c, dtype=np.float64), 0, 1)
    return '#%02X%02X%02X' % tuple(int(round(v * 255)) for v in c)


def rgb_txt(c):
    c = np.clip(np.asarray(c, dtype=np.float64), 0, 1)
    return '%d, %d, %d' % tuple(int(round(v * 255)) for v in c)


def cmyk_txt(c):
    r, g, b = np.clip(np.asarray(c, dtype=np.float64), 0, 1)
    k = 1.0 - max(r, g, b)
    if k >= 0.999:
        return '0 0 0 100'
    cc = (1 - r - k) / (1 - k)
    mm = (1 - g - k) / (1 - k)
    yy = (1 - b - k) / (1 - k)
    return '%d %d %d %d' % tuple(int(round(x * 100)) for x in (cc, mm, yy, k))


# ================================================================ CIEDE2000
def de2000(lab1, lab2):
    """支持 (3,) 与 (N,3) 广播"""
    a = np.asarray(lab1, dtype=np.float64)
    b = np.asarray(lab2, dtype=np.float64)
    L1, a1, b1 = a[..., 0], a[..., 1], a[..., 2]
    L2, a2, b2 = b[..., 0], b[..., 1], b[..., 2]
    C1 = np.hypot(a1, b1)
    C2 = np.hypot(a2, b2)
    Cm = (C1 + C2) / 2.0
    G = 0.5 * (1 - np.sqrt(Cm ** 7 / (Cm ** 7 + 25.0 ** 7 + 1e-30)))
    a1p = (1 + G) * a1
    a2p = (1 + G) * a2
    C1p = np.hypot(a1p, b1)
    C2p = np.hypot(a2p, b2)
    h1p = np.degrees(np.arctan2(b1, a1p)) % 360
    h2p = np.degrees(np.arctan2(b2, a2p)) % 360
    dLp = L2 - L1
    dCp = C2p - C1p
    dhp = h2p - h1p
    dhp = np.where(dhp > 180, dhp - 360, np.where(dhp < -180, dhp + 360, dhp))
    dhp = np.where(C1p * C2p < 1e-10, 0.0, dhp)
    dHp = 2 * np.sqrt(C1p * C2p) * np.sin(np.radians(dhp) / 2.0)
    Lpm = (L1 + L2) / 2.0
    Cpm = (C1p + C2p) / 2.0
    hsum = h1p + h2p
    hpm = np.where(np.abs(h1p - h2p) > 180,
                   np.where(hsum < 360, (hsum + 360) / 2.0, (hsum - 360) / 2.0),
                   hsum / 2.0)
    hpm = np.where(C1p * C2p < 1e-10, hsum, hpm)
    T = (1 - 0.17 * np.cos(np.radians(hpm - 30)) + 0.24 * np.cos(np.radians(2 * hpm))
         + 0.32 * np.cos(np.radians(3 * hpm + 6)) - 0.20 * np.cos(np.radians(4 * hpm - 63)))
    dth = 30 * np.exp(-(((hpm - 275) / 25.0) ** 2))
    Rc = 2 * np.sqrt(Cpm ** 7 / (Cpm ** 7 + 25.0 ** 7 + 1e-30))
    Sl = 1 + 0.015 * (Lpm - 50) ** 2 / np.sqrt(20 + (Lpm - 50) ** 2)
    Sc = 1 + 0.045 * Cpm
    Sh = 1 + 0.015 * Cpm * T
    Rt = -np.sin(np.radians(2 * dth)) * Rc
    return np.sqrt((dLp / Sl) ** 2 + (dCp / Sc) ** 2 + (dHp / Sh) ** 2
                   + Rt * (dCp / Sc) * (dHp / Sh))


# ================================================================ 色库
# 常用色名：现代设计/工业语境里真正会用到的名字，给它们一点优先权
COMMON = [
    # 无彩
    ('纯白', '#FFFFFF'), ('雪白', '#F8F8FF'), ('象牙白', '#FFFFF0'), ('米白', '#F5F0E1'),
    ('珍珠白', '#EAE6DA'), ('月白', '#D6ECF0'), ('银灰', '#C0C0C0'), ('浅灰', '#D3D3D3'),
    ('中灰', '#808080'), ('深灰', '#555555'), ('炭灰', '#36454F'), ('石墨灰', '#41424C'),
    ('铁灰', '#4F5559'), ('纯黑', '#000000'), ('墨黑', '#14161A'),
    # 红
    ('中国红', '#E60012'), ('正红', '#D3281F'), ('朱红', '#E8422F'), ('猩红', '#DC143C'),
    ('砖红', '#A8443A'), ('铁锈红', '#8B3A2E'), ('酒红', '#722F37'), ('勃艮第红', '#5B2333'),
    ('枣红', '#8C2B2B'), ('胭脂红', '#9D2933'), ('玫红', '#E4007F'), ('珊瑚红', '#FF6F61'),
    ('豆沙红', '#A8584F'), ('裸粉', '#E3BCA9'), ('藕粉', '#E7C6CE'), ('樱花粉', '#FBD3DC'),
    ('蜜桃粉', '#FFC2A8'), ('粉红', '#FFC0CB'), ('玫瑰粉', '#E39BB0'),
    # 橙黄
    ('橙色', '#FF7F00'), ('橘橙', '#FF8C00'), ('南瓜橙', '#DE6A2A'), ('焦糖橙', '#C0763C'),
    ('琥珀', '#E0A526'), ('蜜黄', '#F3C33C'), ('柠檬黄', '#FFF200'), ('鹅黄', '#FBE96A'),
    ('奶油黄', '#FBF0BE'), ('芥末黄', '#D3A72C'), ('姜黄', '#BE8A2C'), ('土黄', '#C0A05A'),
    ('卡其', '#C3B091'), ('沙色', '#C9BA94'), ('驼色', '#C19A6B'), ('亚麻色', '#E6D5B8'),
    # 绿
    ('嫩绿', '#A6D96A'), ('苹果绿', '#8DB600'), ('黄绿', '#9ACD32'), ('草绿', '#5FA83E'),
    ('橄榄绿', '#808000'), ('军绿', '#4B5320'), ('苔藓绿', '#8A9A5B'), ('鼠尾草绿', '#9CAF88'),
    ('森林绿', '#2E6B34'), ('松绿', '#2A5C45'), ('墨绿', '#173A2B'), ('翠绿', '#009E6A'),
    ('薄荷绿', '#9BE3C5'), ('湖水绿', '#38B49F'), ('孔雀绿', '#0F6B5C'), ('灰绿', '#7E9E7C'),
    # 青蓝
    ('天青', '#7FC4D6'), ('天蓝', '#87CEEB'), ('雾蓝', '#A8C4D8'), ('灰蓝', '#6E8CA0'),
    ('蔚蓝', '#2F6FB5'), ('湖蓝', '#1F8FC4'), ('钴蓝', '#0047AB'), ('宝蓝', '#0F52BA'),
    ('群青', '#2A3F9D'), ('藏青', '#22314F'), ('海军蓝', '#16304F'), ('普鲁士蓝', '#003153'),
    ('靛蓝', '#25437B'), ('蒂芙尼蓝', '#0ABAB5'), ('冰蓝', '#BFE3F0'), ('牛仔蓝', '#1560BD'),
    # 紫
    ('薰衣草紫', '#B57EDC'), ('丁香紫', '#C8A2C8'), ('藕荷色', '#D5B8C8'), ('紫罗兰', '#7D5BA6'),
    ('葡萄紫', '#5B3A70'), ('茄紫', '#614051'), ('梅子紫', '#8E4585'), ('灰紫', '#8A7F96'),
    ('深紫', '#4B0082'),
    # 棕
    ('咖啡色', '#6F4E37'), ('巧克力', '#4A2C2A'), ('栗色', '#6B3A2A'), ('赭石', '#8B4513'),
    ('焦糖色', '#A9722F'), ('摩卡', '#7B5B4A'), ('奶茶色', '#C8A78E'), ('豆沙色', '#B18A80'),
    ('米色', '#EFE6D2'), ('小麦色', '#E3C89C'),
]

_TRAD = None
_TRAD_LAB = None
_TRAD_CMYK = None
_TRAD_RGB = None
_CM_LAB = None


def _load():
    global _TRAD, _TRAD_LAB, _TRAD_CMYK, _TRAD_RGB, _CM_LAB
    if _TRAD is not None:
        return
    try:
        with open(_TABLE_FILE, 'r', encoding='utf-8') as f:
            raw = json.load(f)
    except Exception:
        raw = []
    names, rgbs, cm = [], [], []
    for it in raw:
        try:
            names.append(it['name'])
            rgbs.append(_hex2rgb01(it['hex']))
            cm.append(it.get('CMYK') or None)
        except Exception:
            continue
    _TRAD = names
    _TRAD_RGB = rgbs
    _TRAD_LAB = rgb2lab(np.array(rgbs)) if rgbs else np.zeros((0, 3))
    _TRAD_CMYK = cm
    _CM_LAB = rgb2lab(np.array([_hex2rgb01(h) for _, h in COMMON]))


_load()


def nearest_all(rgb, k=2, prefer_common=2.0):
    """
    返回 dict：
        name      主用名（常用色名优先，common 有 prefer_common 的色差折扣）
        trad      最近的中国传统色名
        de        主用名的 CIEDE2000 色差
        de_trad   到传统色的色差
        cmyk      色号（命中传统色时直接用库里的，否则算）
        others    次近的名字列表
    """
    lab = rgb2lab(np.asarray(rgb, dtype=np.float64))
    # 常用色名
    dcm = de2000(_CM_LAB, lab)
    icm = int(np.argmin(dcm))
    cm_name, cm_hex = COMMON[icm]
    cm_de = float(dcm[icm])

    # 传统色
    if len(_TRAD):
        dtr = de2000(_TRAD_LAB, lab)
        itr = int(np.argmin(dtr))
        tr_name, tr_de = _TRAD[itr], float(dtr[itr])
        tr_cmyk = _TRAD_CMYK[itr]
    else:
        tr_name, tr_de, tr_cmyk = '', 1e9, None

    if cm_de <= tr_de + prefer_common:
        name, de, cmyk_hit = cm_name, cm_de, None
    else:
        name, de, cmyk_hit = tr_name, tr_de, tr_cmyk

    # 次近名字（同一个库里的第二、第三）
    order = np.argsort(dcm)[:k + 1]
    others = []
    for i in order:
        if COMMON[i][0] == name:
            continue
        if dcm[i] < 14:
            others.append(COMMON[i][0])
        if len(others) >= 2:
            break

    cmyk = ('%d %d %d %d' % tuple(int(x) for x in cmyk_hit)) if cmyk_hit else cmyk_txt(rgb)
    return dict(name=name, de=round(de, 2), trad=tr_name, de_trad=round(tr_de, 2),
                cmyk=cmyk, others=others, hex=hexs(rgb))


def nearest_name(rgb):
    return nearest_all(rgb)['name']


def palette_from_hexes(hexes):
    """把一组设计好的 hex 直接做成色板（颜色保持原样，只补色名/色号）"""
    out = []
    for hx in hexes:
        rgb = _hex2rgb01(hx)
        info = nearest_all(rgb)
        out.append(dict(rgb=rgb, hex=hexs(rgb), name=info['name'], src='preset',
                        cmyk_lib=None, trad_hint=trad_hint(rgb, info['name']),
                        snap_de=0.0, h=rgb_hue(rgb)))
    return out


def rgb_hue(rgb):
    r, g, b = np.clip(np.asarray(rgb, dtype=np.float64), 0, 1)
    mx, mn = max(r, g, b), min(r, g, b)
    if mx - mn < 1e-9:
        return None                       # 无彩色，不限制色相
    import colorsys
    return colorsys.rgb_to_hsv(r, g, b)[0] * 360.0


_MERGED = None


def _merged():
    """把两个库拼成一张可吸附的总表：名字 / RGB / 来源 / CMYK"""
    global _MERGED
    if _MERGED is not None:
        return _MERGED
    rows = []
    for i, (nm, hx) in enumerate(COMMON):
        rows.append(dict(name=nm, rgb=_hex2rgb01(hx), src='common', cmyk=None, order=i))
    for i, nm in enumerate(_TRAD):
        rows.append(dict(name=nm, rgb=_TRAD_RGB[i], src='trad',
                         cmyk=_TRAD_CMYK[i] if _TRAD_CMYK else None,
                         order=len(COMMON) + i))
    _MERGED = rows
    return rows


def snap(rgb, hue_tol=20.0, de_max=15.0, avoid=(), bonus_common=1.6,
         l_weight=0.30, pool=None):
    """
    把一个任意颜色吸附到色库里确实存在的某个颜色上。
    吸附后名字与该颜色 100% 一致 —— 这是"名字必须对得上"的根本保证。
    pool='common' 只在常用色名里找；'trad' 只在传统色里找；None 两个都找。
    返回 dict(rgb, name, src, cmyk, hex, de, hue) ；吸附不到则返回 None
    """
    rows = _merged()
    lab = rgb2lab(np.asarray(rgb, dtype=np.float64))
    h0 = rgb_hue(rgb)
    hmap = [rgb_hue(r['rgb']) for r in rows]

    best, best_score = None, 1e18
    for r, hh in zip(rows, hmap):
        if r['name'] in avoid:
            continue
        if pool is not None and r['src'] != pool:
            continue
        if h0 is not None and hh is not None:
            d = abs((h0 - hh + 180) % 360 - 180)
            if d > hue_tol:
                continue
        elif h0 is not None and hh is None:
            continue
        clab = rgb2lab(r['rgb'])
        de = float(de2000(clab, lab))
        if de > de_max:
            continue
        # 明度贴近原意，避免单色渐变被吸成一片同明度
        score = de - (bonus_common if r['src'] == 'common' else 0.0) \
            + l_weight * abs(float(clab[0]) - float(lab[0]))
        if score < best_score:
            best_score, best = score, (r, de)
    if best is None:
        return None
    r, de = best
    return dict(rgb=np.array(r['rgb'], dtype=np.float64), name=r['name'], src=r['src'],
                cmyk=r['cmyk'], hex=hexs(r['rgb']), de=round(de, 2))


def snap_palette(pal, hue_tol=20.0, de_max=16.0, budget=2):
    """
    整组吸附：先在常用色名里找（用户要的是"常见的中文名"），
    找不到再退回中国传统色；同组内自动去重。
    """
    used, hit = set(), 0
    out = []
    for p in pal:
        got = None
        for pool in ('common', 'trad'):
            for widen in range(budget):
                got = snap(p['rgb'], hue_tol + 10 * widen, de_max + 8 * widen,
                           used, pool=pool)
                if got:
                    break
            if got:
                break
        q = dict(p)
        if got:
            q['rgb'] = got['rgb']
            q['name'] = got['name']
            q['src'] = got['src']
            q['cmyk_lib'] = got['cmyk']
            q['snap_de'] = got['de']
            used.add(got['name'])
            hit += 1
        else:
            info = nearest_all(p['rgb'])
            nm = info['name']
            k = 2
            while nm in used and k <= 6:
                nm = '%s%d' % (info['name'], k)
                k += 1
            q['name'], q['src'], q['cmyk_lib'] = nm, 'raw', None
            q['snap_de'] = None
        used.add(q['name'])
        q['h'] = rgb_hue(q['rgb'])
        q['hex'] = hexs(q['rgb'])
        q['trad_hint'] = trad_hint(q['rgb'], q['name'])
        out.append(q)
    return out, hit


def trad_hint(rgb, primary_name=None, de_max=6.0):
    """给常用色名补一个传统色名，方案书里看着更雅"""
    rows = _merged()
    best, bde = None, de_max
    lab = rgb2lab(np.asarray(rgb, dtype=np.float64))
    for r in rows:
        if r['src'] != 'trad' or r['name'] == primary_name:
            continue
        de = float(de2000(rgb2lab(r['rgb']), lab))
        if de < bde:
            bde, best = de, r['name']
    return best


def alt_name(rgb, prefer=None):
    """给出另一个库里的近义色名（相差很小的时候才有意义）"""
    rows = _merged()
    best, bde = None, 1e9
    for r in rows:
        if prefer and r['src'] == prefer:
            continue
        de = float(de2000(rgb2lab(r['rgb']), rgb2lab(np.asarray(rgb, np.float64))))
        if de < bde:
            bde, best = de, r
    if best is None or bde > 8.0:
        return None
    return dict(name=best['name'], src=best['src'], de=round(bde, 1))


# ================================================================ 色觉模拟
# Machado, Oliveira & Fernandes (2009) severity = 1.0
CVD_M = {
    'protan': np.array([[0.152286, 1.052583, -0.204868],
                        [0.114503, 0.786281, 0.099216],
                        [-0.003882, -0.048116, 1.051998]]),
    'deutan': np.array([[0.367322, 0.860646, -0.227968],
                        [0.280085, 0.672501, 0.047413],
                        [-0.011820, 0.042940, 0.968881]]),
    'tritan': np.array([[1.255528, -0.076749, -0.178779],
                        [-0.078411, 0.930809, 0.147602],
                        [0.004733, 0.691367, 0.303900]]),
}
CVD_CN = {'protan': '红色型', 'deutan': '绿色型', 'tritan': '蓝黄型'}
CVD_EN = {'protan': 'PROTANOPIA', 'deutan': 'DEUTERANOPIA', 'tritan': 'TRITANOPIA'}
CVD_ALL = ['protan', 'deutan', 'tritan']


def simulate(rgb, kind='deutan', severity=1.0):
    """色觉模拟。severity 0~1，0 = 正常视觉"""
    c = np.clip(np.asarray(rgb, dtype=np.float64), 0, 1)
    if severity <= 1e-6:
        return c
    lin = to_lin(c)
    if kind == 'achroma':
        y = lin @ np.array([0.2126, 0.7152, 0.0722])
        out = np.stack([y, y, y], axis=-1)
    else:
        M = np.eye(3) * (1 - severity) + CVD_M[kind] * severity
        out = lin @ M.T
    return to_srgb(out)


def simulate_named(rgb, kind='deutan'):
    """返回模拟后的颜色 + 该条件下最接近的色名（用于说明"看起来像什么"）"""
    s = simulate(rgb, kind)
    return s, nearest_name(s)


def cvd_report(pal, target=10.0):
    """
    检查一组颜色在各种色觉下两两之间的色差。
    target 是「可辨识」的经验阈值（ΔE00）。
    返回 dict：每型最小色差 + 冲突色对
    """
    rgbs = np.array([p['rgb'] for p in pal])
    out = {'normal': None, 'kinds': {}}
    lab = rgb2lab(rgbs)
    n = len(rgbs)
    pairs = [(i, j) for i in range(n) for j in range(i + 1, n)]

    def worst(labs):
        if not pairs:
            return 99.0, []
        d = [float(de2000(labs[i], labs[j])) for i, j in pairs]
        bad = [(pairs[k], round(d[k], 1)) for k in range(len(d)) if d[k] < target]
        bad.sort(key=lambda t: t[1])
        return round(min(d), 1) if d else 99.0, bad

    mn, bad = worst(lab)
    out['normal'] = dict(min=mn, bad=bad)
    for k in CVD_ALL:
        sim = simulate(rgbs, k)
        mn, bad = worst(rgb2lab(sim))
        out['kinds'][k] = dict(min=mn, bad=bad)
    return out


def snap_lightness(rgb, target_l, hue_tol=26.0, de_max=26.0, avoid=(), pool=None):
    """在色库里挑一个「色相家族和彩度都不跑偏、明度接近 target_l」的真实颜色"""
    rows = _merged()
    lab0 = rgb2lab(np.asarray(rgb, dtype=np.float64))
    c_src = math.hypot(float(lab0[1]), float(lab0[2]))
    h0 = rgb_hue(rgb)
    best, best_score = None, 1e18
    for r in rows:
        if r['name'] in avoid:
            continue
        if pool is not None and r['src'] != pool:
            continue
        hh = rgb_hue(r['rgb'])
        dh = 0.0
        if h0 is not None and hh is not None:
            dh = abs((h0 - hh + 180) % 360 - 180)
            if dh > hue_tol:
                continue
        elif h0 is not None:
            continue
        cl = rgb2lab(r['rgb'])
        if float(de2000(cl, lab0)) > de_max:
            continue
        c_c = math.hypot(float(cl[1]), float(cl[2]))
        # 掉彩度的候选要扣分，不然蓝会被吸成灰
        score = (abs(float(cl[0]) - target_l) + 0.55 * dh
                 + 0.60 * max(0.0, c_src - c_c))
        if score < best_score:
            best_score, best = score, r
    if best is None:
        return None
    return dict(rgb=np.array(best['rgb'], dtype=np.float64), name=best['name'],
                src=best['src'], cmyk=best['cmyk'], hex=hexs(best['rgb']))


def separate_for_cvd(pal, target=12.0, hue_tol=26.0, tries=4):
    """
    色觉关怀 · 明度均匀化。
    只重排明度（色相家族与彩度都不动），把 L* 等间距拉开，
    间距足够让任意一型色盲都能靠明暗分辨。
    调整是有方向的：提亮最多 +12（防止冲成白），压暗可以到 −34。
    重排时直接落到色库里真实存在的颜色上，所以色名依然精确对应。
    """
    n = len(pal)
    if n < 2:
        return [dict(p) for p in pal]
    labs = [rgb2lab(np.asarray(p['rgb'], dtype=np.float64)) for p in pal]
    Ls = [float(l[0]) for l in labs]
    cur = max(Ls) - min(Ls)
    mid = (max(Ls) + min(Ls)) / 2.0
    need = target * (n - 1) * 1.15
    order = sorted(range(n), key=lambda i: -Ls[i])

    best_out, best_min = None, -1.0
    for t in range(tries):
        span = min(max(cur, need * (1.0 + 0.22 * t)), 84.0)
        lo = mid - span / 2.0
        hi = mid + span / 2.0
        if lo < 14.0:
            lo, hi = 14.0, min(95.0, 14.0 + span)
        if hi > 93.0:
            hi, lo = 93.0, max(12.0, 93.0 - span)
        tg = [hi - (hi - lo) * k / (n - 1) for k in range(n)]

        trial, used = [], set()
        for k, i in enumerate(order):
            p = dict(pal[i])
            tl = float(np.clip(tg[k], Ls[i] - 34.0, Ls[i] + 12.0))
            got = snap_lightness(p['rgb'], tl, hue_tol, avoid=used)
            if got:
                got['trad_hint'] = trad_hint(got['rgb'], got['name'])
                p.update(got)
                used.add(got['name'])
            trial.append(p)

        rep = cvd_report(trial, target=target)
        worst = min([rep['kinds'][k]['min'] for k in CVD_ALL] + [rep['normal']['min']])
        if worst > best_min:
            best_min, best_out = worst, trial
        if worst >= target:
            break
    return best_out or [dict(p) for p in pal]


def _hsv(h, s, v):
    import colorsys
    r, g, b = colorsys.hsv_to_rgb((h % 360) / 360.0, float(np.clip(s, 0, 1)),
                                  float(np.clip(v, 0, 1)))
    return np.array([r, g, b], dtype=np.float64)


def luminance(rgb):
    lin = to_lin(np.asarray(rgb, dtype=np.float64))
    return float(lin @ np.array([0.2126, 0.7152, 0.0722]))


def contrast_white_black(rgb):
    """对比纯白/纯黑的 WCAG 对比度，取较大者"""
    L = luminance(rgb)
    cw = 1.05 / (L + 0.05)
    cb = (L + 0.05) / 0.05
    return round(max(cw, cb), 2), ('白' if cw >= cb else '黑')

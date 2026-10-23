# -*- coding: utf-8 -*-
"""
LumiCurve 本地服务
==================
起一个本地 HTTP 服务，把 cmf_card / cmf_color / lamp_curve 包成 JSON 接口，
前端是 app/web 里的新拟物界面。双击「打开应用.bat」即用。
"""
import os
import io
import sys
import json
import uuid
import base64
import datetime
import threading
import traceback
import urllib.parse
import webbrowser
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
WEB = os.path.join(HERE, 'web')
OUT = os.path.join(ROOT, 'out')
os.makedirs(OUT, exist_ok=True)
sys.path.insert(0, ROOT)

import numpy as np
import cmf_card as MC
import cmf_color as CC
import lamp_curve as LC
import io
import base64
from PIL import Image
import numpy as np
import printing as PR

SCHEMES = ['mono', 'analogous', 'complementary', 'triadic', 'split']
MATS = list(MC.MAT_CN.keys())   # 跟着 cmf_card 自动同步
SHAPES = list(MC.SHAPES.keys())   # 跟着 cmf_card 自动同步
THEMES = ['dark', 'light']

# 拼色预设
PRESETS = {}
try:
    with open(os.path.join(ROOT, 'presets.json'), 'r', encoding='utf-8') as _f:
        for _it in json.load(_f):
            PRESETS[_it['key']] = _it
except Exception:
    PRESETS = {}


def build_pal(q, target, cvd):
    """按拼色预设或参数生成色板，返回 (pal, 来源)"""
    pk = q.get('preset', [''])[0]
    if pk and pk in PRESETS:
        pal = CC.palette_from_hexes(PRESETS[pk]['colors'])
        src = 'preset:' + pk
    else:
        pal, _hit = MC.build_palette(_f(q, 'hue', 205, 0, 359),
                                     int(_f(q, 'n', 4, 2, 5)),
                                     _s(q, 'scheme', 'analogous', SCHEMES),
                                     _f(q, 'sat', 0.55, 0.1, 0.95),
                                     _f(q, 'val', 0.80, 0.3, 0.97))
        src = 'auto'
    if cvd:
        pal = CC.separate_for_cvd(pal, target=target)
        for p in pal:
            p['hex'] = MC.hexs(p['rgb'])
            p.setdefault('trad_hint', CC.trad_hint(p['rgb'], p['name']))
    return pal, src



def _f(q, k, d, lo=None, hi=None):
    try:
        v = float(q.get(k, [d])[0])
    except Exception:
        v = float(d)
    if lo is not None:
        v = max(lo, v)
    if hi is not None:
        v = min(hi, v)
    return v


def _s(q, k, d, allowed):
    v = q.get(k, [d])[0]
    return v if v in allowed else d


def _b(q, k, d):
    v = q.get(k, ['1' if d else '0'])[0]
    return v in ('1', 'true', 'True', 'on')


# ------------------------------------------------------------------ 配色方案卡
def do_cmf(q, preview=True):
    hue = _f(q, 'hue', 205, 0, 359)
    n = int(_f(q, 'n', 4, 2, 5))
    scheme = _s(q, 'scheme', 'analogous', SCHEMES)
    material = _s(q, 'material', 'matte', MATS)
    shape = _s(q, 'shape', 'bottle', SHAPES)
    theme = _s(q, 'theme', 'light', THEMES)
    sat = _f(q, 'sat', 0.55, 0.1, 0.95)
    val = _f(q, 'val', 0.80, 0.3, 0.97)
    cvd = _b(q, 'cvd', True)
    target = _f(q, 'target', 12, 8, 20)
    name = (q.get('name', ['曲水 · 陶瓷香薰器'])[0] or '未命名方案')[:40]
    pattern = (q.get('pattern', ['none'])[0] or 'none')
    if pattern not in MC.PATTERNS:
        pattern = 'none'
    pat_idx = int(_f(q, 'patIdx', 1, 0, 9))
    emb = _f(q, 'emboss', 0.0, 0.0, 1.2) if pattern != 'none' else 0.0
    hmm = _f(q, 'height', 300, 80, 500)
    cmask = mask_get(q) if pattern == 'custom' else None

    pal, src = build_pal(q, target, cvd)
    n = len(pal)
    rep = CC.cvd_report(pal, target=target)

    tmp = os.path.join(OUT, '_t_%s.png' % uuid.uuid4().hex)
    try:
        _o, rep = MC.build_card(pal, name, material, out=tmp, shape=shape,
                                scheme=scheme, hue=hue, cvd_fix=cvd,
                                target=target, theme=theme,
                                pattern=pattern, pat_idx=pat_idx,
                                emboss_mm=emb, height_mm=hmm,
                                custom_mask=cmask)
        with open(tmp, 'rb') as f:
            png = base64.b64encode(f.read()).decode('ascii')
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)

    chips = [dict(name=p['name'], hex=p['hex'], rgb=MC.rgb_txt(p['rgb']),
                  cmyk=MC.cmyk_str(p), l=round(MC._lstar(p['rgb'])),
                  trad=p.get('trad_hint') or '') for p in pal]
    kinds = {k: dict(min=rep['kinds'][k]['min'], bad=len(rep['kinds'][k]['bad']))
             for k in CC.CVD_ALL}
    return dict(png=png, chips=chips, normal=rep['normal']['min'], kinds=kinds,
                target=target, src=src,
                preset=(src.split(':')[1] if ':' in src else ''),
                        boxes=rep.get('boxes', []), size=rep.get('size', [1600, 1300]),
                a11y=a11y_stats(pal))


# ------------------------------------------------------------------ 灯具配光
def do_lamp(q, preview=True):
    mouth = _f(q, 'mouth', 0.115, 0.04, 0.20)
    height = _f(q, 'height', 0.26, 0.12, 0.42)
    waist = _f(q, 'waist', 0.82, 0.45, 1.2)
    power = _f(q, 'power', 800, 200, 2000)
    spec = _f(q, 'spec', 0.85, 0.2, 1.0)
    dist = _f(q, 'dist', 0.60, 0.20, 1.20)
    target = _f(q, 'target', 0.50, 0.20, 1.00)
    rays = 9000 if preview else 20000
    size = 460 if preview else 820

    prof = LC.build_profile(mouth, 0.030, height, waist, 0.35)
    bulb_y = height * 0.68
    radii, weights, _ = LC.trace(prof, (0.0, bulb_y), power=power, rays=rays,
                                 spec=spec, ground=-dist)
    if radii.size == 0:
        raise RuntimeError('一条光线都没落到桌面上：请调大罩口半径或减小离桌距离')
    rmax = max(1.8, target * 3.2)
    cen, E, area = LC.illuminance(radii, weights, rmax=rmax, bins=120, sigma=11.0)
    m = LC.metrics(cen, E, area, target)

    pool = LC.render_pool(cen, E, None, size=size, rmax=max(1.2, target * 2.2),
                          target=target, title='地面照度分布')
    ch = int(size * 0.70)
    curve = LC.render_curve(cen, E, None, size=(int(size * 1.10), ch),
                            target=target, title='照度沿半径衰减')
    W = pool.width + curve.width + 26
    H = max(pool.height, curve.height)
    sheet = __import__('PIL.Image', fromlist=['Image']).new('RGB', (W, H), (18, 20, 26))
    sheet.paste(pool, (0, 0))
    sheet.paste(curve, (pool.width + 26, (H - curve.height) // 2))
    buf = io.BytesIO()
    sheet.save(buf, 'PNG')
    png = base64.b64encode(buf.getvalue()).decode('ascii')

    return dict(png=png,
                metrics=dict(peak=round(m['peak']), center=round(m['center']),
                             avg=round(m['avg']), uniformity=round(m['uniformity'], 2),
                             hit=round(m['hit_radius'], 2)),
                args=dict(mouth=mouth, top=0.030, height=height, waist=waist,
                          bulb=0.68, spec=spec, power=power, dist=dist, target=target),
                rays=int(radii.size))


# ------------------------------------------------------------------ 保存
def save_files(kind, params):
    stamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
    q = {k: [str(v)] for k, v in params.items()}
    made = []
    if kind == 'cmf':
        hue = _f(q, 'hue', 205, 0, 359)
        n = int(_f(q, 'n', 4, 2, 5))
        scheme = _s(q, 'scheme', 'analogous', SCHEMES)
        material = _s(q, 'material', 'matte', MATS)
        shape = _s(q, 'shape', 'bottle', SHAPES)
        theme = _s(q, 'theme', 'light', THEMES)
        sat = _f(q, 'sat', 0.55, 0.1, 0.95)
        val = _f(q, 'val', 0.80, 0.3, 0.97)
        cvd = _b(q, 'cvd', True)
        target = _f(q, 'target', 12, 8, 20)
        name = (q.get('name', ['未命名方案'])[0] or '未命名方案')[:40]
        pattern = (q.get('pattern', ['none'])[0] or 'none')
        if pattern not in MC.PATTERNS:
            pattern = 'none'
        pat_idx = int(_f(q, 'patIdx', 1, 0, 9))
        emb = _f(q, 'emboss', 0.0, 0.0, 1.2) if pattern != 'none' else 0.0
        hmm = _f(q, 'height', 300, 80, 500)
        cmask = mask_get(q) if pattern == 'custom' else None
        pal, _src = build_pal(q, target, cvd)
        out = os.path.join(OUT, 'CMF_%s.png' % stamp)
        MC.build_card(pal, name, material, out=out, shape=shape, scheme=scheme,
                      hue=hue, cvd_fix=cvd, target=target, theme=theme,
                      pattern=pattern, pat_idx=pat_idx,
                      emboss_mm=emb, height_mm=hmm, custom_mask=cmask)
        made.append(out)
        txt = os.path.join(OUT, 'CMF_%s_色号.txt' % stamp)
        with open(txt, 'w', encoding='utf-8') as f:
            f.write('产品名称：%s\n配色方案：%s　主色相 %d°　材质：%s　器型：%s\n'
                    % (name, MC.NAME_CN.get(scheme, ''), int(hue),
                       MC.MAT_CN[material], MC.SHAPE_CN[shape]))
            f.write('色觉检查：正常 ΔE00 %.1f\n' % CC.cvd_report(pal, target)['normal']['min'])
            if pattern != 'none':
                f.write('纹样：%s　花纹色：色%02d %s　浮雕：%0.1f mm\n'
                        % (MC.PATTERNS[pattern], min(pat_idx, len(pal) - 1) + 1,
                           pal[min(pat_idx, len(pal) - 1)]['name'], emb))
            f.write('\n')
            for i, p in enumerate(pal):
                f.write('%02d\t%s\t%s\tRGB %s\tCMYK %s\tL* %.0f\n'
                        % (i + 1, p['name'], p['hex'], MC.rgb_txt(p['rgb']),
                           MC.cmyk_str(p), MC._lstar(p['rgb'])))
        made.append(txt)
    else:
        d = do_lamp(q, preview=False)
        a = d['args']
        prof = LC.build_profile(a['mouth'], a['top'], a['height'], a['waist'], 0.35)
        bulb_y = a['height'] * a['bulb']
        radii, weights, _ = LC.trace(prof, (0.0, bulb_y), power=a['power'],
                                     rays=14000, spec=a['spec'], ground=-a['dist'])
        rmax = max(1.8, a['target'] * 3.2)
        cen, E, area = LC.illuminance(radii, weights, rmax=rmax, bins=160, sigma=8.0)
        base = os.path.join(OUT, 'lamp_%s' % stamp)
        LC.render_pool(cen, E, base + '_pool.png', size=820,
                       rmax=max(1.2, a['target'] * 2.2), target=a['target'],
                       title='地面照度分布（俯视）').save(base + '_pool.png')
        LC.render_curve(cen, E, base + '_curve.png', target=a['target'],
                        title='照度沿半径衰减').save(base + '_curve.png')
        paths = LC.trace_paths(prof, (0.0, bulb_y), n_rays=44, ground=-a['dist'])
        LC.render_raytrace(prof, (0.0, bulb_y), paths, base + '_ray.png',
                           title='轴截面光线追迹', spec=a['spec'],
                           height=a['height'], ground=-a['dist']).save(base + '_ray.png')
        made += [base + '_pool.png', base + '_curve.png', base + '_ray.png']
        import csv
        with open(base + '_report.csv', 'w', newline='', encoding='utf-8-sig') as f:
            w = csv.writer(f)
            w.writerow(['radius_m', 'illuminance_lux'])
            for r, e in zip(cen, E):
                w.writerow(['%.4f' % r, '%.2f' % e])
        made.append(base + '_report.csv')
    return made


# ------------------------------------------------------------------ HTTP
class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=WEB, **kw)

    def log_message(self, *a):
        pass

    def end_headers(self):
        self.send_header('Cache-Control', 'no-store')
        super().end_headers()

    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)
        try:
            if u.path == '/api/cmf':
                return self._json(do_cmf(q))
            if u.path == '/api/lamp':
                return self._json(do_lamp(q))
            if u.path == '/api/presets':
                return self._json(dict(items=[dict(key=k, name=v['name'],
                                                  colors=v['colors'])
                                             for k, v in PRESETS.items()]))
            if u.path == '/api/sim':
                return self._json(do_sim(q))
            if u.path == '/api/print':
                return self._json(do_print(q))
            if u.path == '/api/stl':
                data, fn = stl_response(q)
                self.send_response(200)
                self.send_header('Content-Type', 'model/stl')
                self.send_header('Content-Disposition',
                                 'attachment; filename="%s"' % fn)
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return None
            if u.path == '/api/geo':
                return self._json(do_geo(q))
            if u.path == '/api/fix':
                return self._json(do_fix(q))
            if u.path == '/api/ping':
                return self._json(dict(ok=True, out=OUT))
        except Exception:
            return self._json(dict(error=traceback.format_exc(limit=3)), 500)
        return super().do_GET()

    def do_POST(self):
        u = urllib.parse.urlparse(self.path)
        try:
            n = int(self.headers.get('Content-Length') or 0)
            data = json.loads(self.rfile.read(n) or b'{}')
            if u.path == '/api/save':
                made = save_files(data.get('kind', 'cmf'), data.get('params', {}))
                return self._json(dict(files=made))
            if u.path == '/api/mask':
                k, px = mask_store(data.get('data', ''))
                return self._json(dict(id=k, px=px, size=512))
            if u.path == '/api/open':
                os.startfile(OUT)
                return self._json(dict(ok=True))
        except Exception:
            return self._json(dict(error=traceback.format_exc(limit=3)), 500)
        return self._json(dict(error='not found'), 404)



# ------------------------------------------------------------------ 无障碍
def wcag_ratio(c1, c2):
    l1, l2 = CC.luminance(c1), CC.luminance(c2)
    hi, lo = max(l1, l2), min(l1, l2)
    return (hi + 0.05) / (lo + 0.05)


def a11y_stats(pal, plinth=(0xB7 / 255, 0xC4 / 255, 0xD3 / 255)):
    """相邻色块之间 + 主色与台座之间的 WCAG 非文本对比度"""
    pairs = []
    for i in range(len(pal)):
        for j in range(i + 1, len(pal)):
            pairs.append(wcag_ratio(pal[i]['rgb'], pal[j]['rgb']))
    prod = wcag_ratio(pal[0]['rgb'], np.array(plinth))
    return dict(min_pair=round(min(pairs), 2) if pairs else 99.0,
                product=round(prod, 2))


def build_sim_strip(pal, material, shape):
    """四行：正常 / 红绿 / 蓝黄，每行画出整组配色 —— 色弱用户看到的样子"""
    from PIL import Image, ImageDraw, ImageFont
    CW, CH, LAB = 118, 152, 150
    gap, pad = 12, 14
    n = len(pal)
    rows = [('正常视觉', None)] + [(CC.CVD_CN[k], k) for k in CC.CVD_ALL]
    W = pad * 2 + LAB + n * (CW + gap)
    H = pad * 2 + len(rows) * (CH + 26)
    img = Image.new('RGB', (W, H), (247, 250, 254))
    d = ImageDraw.Draw(img)
    try:
        f1 = ImageFont.truetype('C:/Windows/Fonts/msyhbd.ttc', 13)
        f2 = ImageFont.truetype('C:/Windows/Fonts/consola.ttf', 10)
    except Exception:
        f1 = f2 = ImageFont.load_default()
    for r, (lab, kind) in enumerate(rows):
        y = pad + r * (CH + 26)
        d.text((pad, y + CH // 2 - 8), lab, font=f1, fill=(60, 72, 90))
        for i, p in enumerate(pal):
            col = p['rgb'] if kind is None else CC.simulate(p['rgb'], kind)
            v = MC.render_vessel(col, CW, CH, material, shape=shape)
            x = pad + LAB + i * (CW + gap)
            img.paste(v, (x, y), v)
            d.text((x + 2, y + CH + 3), p['hex'], font=f2, fill=(120, 132, 150))
    return img


def do_sim(q):
    target = _f(q, 'target', 12, 8, 20)
    cvd = _b(q, 'cvd', True)
    pal, src = build_pal(q, target, cvd)
    material = _s(q, 'material', 'matte', MATS)
    shape = _s(q, 'shape', 'vase', SHAPES)
    im = build_sim_strip(pal, material, shape)
    buf = io.BytesIO()
    im.save(buf, 'PNG')
    return dict(png=base64.b64encode(buf.getvalue()).decode('ascii'),
                size=list(im.size), a11y=a11y_stats(pal))


def do_fix(q):
    """
    搜一组能通过色觉检查的参数：
      1) 先在当前配色方案里调色相/饱和/明度（尽量少改设计意图）
      2) 若仍不达标，再试三分色 / 分裂互补（对比更强的方案）
      3) 入围的候选按"真实渲染流程"（含色觉明度重排）复核，取最优
    """
    target = _f(q, 'target', 12, 8, 20)
    hue0 = _f(q, 'hue', 205, 0, 359)
    n = int(_f(q, 'n', 4, 2, 5))
    scheme0 = _s(q, 'scheme', 'analogous', SCHEMES)
    cvd = _b(q, 'cvd', True)

    order = [scheme0] + [x for x in ('triadic', 'split', 'complementary')
                         if x != scheme0]

    scored = []
    for si, scheme in enumerate(order):
        for dh in (0, 12, -12, 24, -24, 36, -36):
            hue = (hue0 + dh) % 360
            for sat in (0.40, 0.55, 0.70):
                for val in (0.62, 0.76, 0.88):
                    pal, _ = MC.build_palette(hue, n, scheme, sat, val, snap=False)
                    rep = CC.cvd_report(pal, target=target)
                    m = min([rep['kinds'][k]['min'] for k in CC.CVD_ALL] +
                            [rep['normal']['min']])
                    score = m - si * 3.0 - abs(dh) * 0.03 - abs(sat - 0.55) * 3
                    scored.append((score, scheme, hue, sat, val))
    scored.sort(reverse=True)

    best = None
    for score, scheme, hue, sat, val in scored[:6]:
        pal = MC.build_palette(hue, n, scheme, sat, val)[0]
        if cvd:
            pal = CC.separate_for_cvd(pal, target=target)
        rep = CC.cvd_report(pal, target=target)
        m = min([rep['kinds'][k]['min'] for k in CC.CVD_ALL] +
                [rep['normal']['min']])
        if best is None or m > best['min']:
            best = dict(hue=int(round(hue)), sat=round(sat, 2), val=round(val, 2),
                        scheme=scheme, min=round(m, 1), target=target,
                        kinds={k: rep['kinds'][k]['min'] for k in CC.CVD_ALL})
    best['reached'] = best['min'] >= target
    best['scheme_changed'] = best['scheme'] != scheme0
    return best



def do_geo(q):
    """只算几何与配色（不渲染卡片），给 3D 视图用"""
    target = _f(q, 'target', 12, 8, 20)
    cvd = _b(q, 'cvd', True)
    pal, src = build_pal(q, target, cvd)
    shape = _s(q, 'shape', 'vase', SHAPES)
    prof = MC.vessel_profile(shape)
    pts = [[round(float(y), 4), round(float(r), 4)] for y, r in prof[::2]]
    wall_mm = _f(q, 'wall', 3.0, 1.2, 12.0)
    hmm = _f(q, 'height', 300, 80, 500)
    return dict(points=pts,
                wall=max(0.004, min(0.06, wall_mm / hmm)),
                wall_mm=wall_mm, height_mm=hmm,
                closed=shape in PR.CLOSED_SHAPES,
                colors=[MC.hexs(p['rgb']) for p in pal],
                names=[p['name'] for p in pal],
                material=_s(q, 'material', 'matte', MATS),
                shape=shape, src=src)




# ------------------------------------------------------------------ 手绘遮罩
# 画笔在浏览器里画，512×512 的遮罩通过 POST 传上来，存成短 token，
# 后面的 /api/cmf /api/print /api/stl 都用 token 引用它（避免把几十 KB 塞进 URL）。
_MASKS = {}
_MASK_SEQ = [0]


def _decode_mask(dataurl):
    """画笔遮罩：保留 RGBA，卡片才能按笔迹真实颜色上色"""
    raw = base64.b64decode((dataurl or '').split(',', 1)[-1])
    im = Image.open(io.BytesIO(raw)).convert('RGBA')
    if im.size != (512, 512):
        im = im.resize((512, 512), Image.LANCZOS)
    a = np.asarray(im).copy()          # PIL 的缓冲是只读的，要拷一份才能改
    # 透明像素的 RGB 可能是脏的，先归零，避免采样到黑边
    a[a[..., 3] <= 8, :3] = 0
    return a


def mask_store(dataurl):
    arr = _decode_mask(dataurl)
    _MASK_SEQ[0] += 1
    k = 'm%d' % _MASK_SEQ[0]
    _MASKS[k] = arr
    while len(_MASKS) > 24:                 # 只留最近 24 张
        _MASKS.pop(next(iter(_MASKS)), None)
    return k, int((arr[..., 3] > 8).sum())


def mask_get(q):
    k = (q.get('mask', [''])[0] or '')
    return _MASKS.get(k) if k else None


# ------------------------------------------------------------------ 3D 打印
def do_print(q):
    shape = _s(q, 'shape', 'vase', SHAPES)
    wall = _f(q, 'wall', 3.0, 1.2, 12.0)
    hmm = _f(q, 'height', 300, 80, 500)
    pat = q.get('pattern', ['none'])[0] or 'none'
    if pat not in MC.PATTERNS:
        pat = 'none'
    emb = _f(q, 'emboss', 0.6, 0.0, 1.2) if pat != 'none' else 0.0
    cmask = mask_get(q) if pat == 'custom' else None
    data, rep = PR.make(shape, wall, hmm, 96, pattern=pat, emboss_mm=emb,
                        custom_mask=cmask)
    rep['stl_mb'] = round(len(data) / 1048576.0, 2)
    return rep


def stl_response(q):
    shape = _s(q, 'shape', 'vase', SHAPES)
    wall = _f(q, 'wall', 3.0, 1.2, 12.0)
    hmm = _f(q, 'height', 300, 80, 500)
    pat = q.get('pattern', ['none'])[0] or 'none'
    if pat not in MC.PATTERNS:
        pat = 'none'
    emb = _f(q, 'emboss', 0.6, 0.0, 1.2) if pat != 'none' else 0.0
    cmask = mask_get(q) if pat == 'custom' else None
    data, _rep = PR.make(shape, wall, hmm, 96, pattern=pat, emboss_mm=emb,
                         custom_mask=cmask)
    tag = '' if pat == 'none' else '_%s%.1fmm' % (pat, emb)
    return data, 'LumiCurve_%s_%dx%smm%s.stl' % (
        shape, int(hmm), str(wall).replace('.', 'p'), tag)


def main():
    port = 8770
    for p in range(8770, 8790):
        try:
            srv = ThreadingHTTPServer(('127.0.0.1', p), Handler)
            port = p
            break
        except OSError:
            continue
    else:
        raise SystemExit('端口都被占用了')
    url = 'http://127.0.0.1:%d/' % port
    print('LumiCurve ->', url)
    threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    srv.serve_forever()


if __name__ == '__main__':
    main()

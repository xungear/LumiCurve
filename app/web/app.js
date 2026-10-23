/* LumiCurve 前端逻辑 */
(() => {
  const $ = id => document.getElementById(id);
  const state = { pane: 'cmf', hue: 205, cvd: true, busy: 0, preset: '', boxes: [], size: [1600, 1300], sim: false, v3d: false,
    pattern: 'none', patIdx: 1, emboss: 0.6, paint: false, viewLock: false };
  let timer = null, seq = 0, lastChips = [];

  /* ---------------- 色相盘 ---------------- */
  const dial = $('dial');
  const dctx = dial.getContext('2d');
  const DS = 150, DC = DS / 2;

  function drawDial(hue) {
    const dpr = window.devicePixelRatio || 1;
    if (dial.width !== DS * dpr) {
      dial.width = DS * dpr; dial.height = DS * dpr;
    }
    dctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    dctx.clearRect(0, 0, DS, DS);

    // 色相环
    const R = DC - 4, rIn = R - 13;
    for (let a = 0; a < 360; a += 2) {
      const a0 = (a - 1.4) * Math.PI / 180, a1 = (a + 2.4) * Math.PI / 180;
      dctx.beginPath();
      dctx.arc(DC, DC, R, a0 - Math.PI / 2, a1 - Math.PI / 2);
      dctx.arc(DC, DC, rIn, a1 - Math.PI / 2, a0 - Math.PI / 2, true);
      dctx.closePath();
      dctx.fillStyle = `hsl(${a} 62% 56%)`;
      dctx.fill();
    }
    // 内盘（凹陷感）
    const g = dctx.createRadialGradient(DC - 12, DC - 14, 4, DC, DC, rIn);
    g.addColorStop(0, '#EDF1F7'); g.addColorStop(1, '#E2E8F1');
    dctx.beginPath(); dctx.arc(DC, DC, rIn, 0, Math.PI * 2);
    dctx.fillStyle = g; dctx.fill();
    dctx.lineWidth = 2; dctx.strokeStyle = 'rgba(255,255,255,.85)'; dctx.stroke();

    // 指针
    const rad = hue * Math.PI / 180;
    const px = DC + R * Math.sin(rad), py = DC - R * Math.cos(rad);
    const qx = DC + (R - 13) * Math.sin(rad), qy = DC - (R - 13) * Math.cos(rad);
    dctx.beginPath(); dctx.moveTo(qx, qy); dctx.lineTo(px, py);
    dctx.lineWidth = 3; dctx.strokeStyle = '#2F3A4C'; dctx.lineCap = 'round'; dctx.stroke();
    dctx.beginPath(); dctx.arc(px, py, 3.6, 0, Math.PI * 2);
    dctx.fillStyle = '#2F3A4C'; dctx.fill();

    // 数值
    dctx.fillStyle = '#38414F';
    dctx.font = '600 22px Consolas,monospace';
    dctx.textAlign = 'center'; dctx.textBaseline = 'middle';
    dctx.fillText(String(Math.round(hue)), DC - 2, DC - 3);
    dctx.fillStyle = '#939FB2'; dctx.font = '11px sans-serif';
    dctx.fillText('°', DC + 21, DC + 2);
  }

  function hueFromEvent(e) {
    const b = dial.getBoundingClientRect();
    const x = (e.clientX - b.left) / b.width * DS - DC;
    const y = (e.clientY - b.top) / b.height * DS - DC;
    let a = Math.atan2(x, -y) * 180 / Math.PI;
    return (a + 360) % 360;
  }
  let dragging = false;
  dial.addEventListener('pointerdown', e => {
    dragging = true; dial.setPointerCapture(e.pointerId);
    state.hue = hueFromEvent(e); drawDial(state.hue); manualEdit(); schedule();
  });
  dial.addEventListener('pointermove', e => {
    if (!dragging) return;
    state.hue = hueFromEvent(e); drawDial(state.hue); manualEdit(); schedule();
  });
  dial.addEventListener('pointerup', e => {
    dragging = false;
    state.hue = Math.round(hueFromEvent(e)) % 360;
    drawDial(state.hue); manualEdit(); schedule(true);
  });

  /* ---------------- 面板内发散弧线（画成面板背景，保证在所有控件之下） ---------------- */
  function drawArcs() {
    const p = document.querySelector('.panel');
    const w = p.clientWidth, h = p.clientHeight;
    const cv = document.createElement('canvas');
    cv.width = Math.round(w * 2); cv.height = Math.round(h * 2);
    const g = cv.getContext('2d');
    g.scale(2, 2);

    const dr = dial.getBoundingClientRect(), pr = p.getBoundingClientRect();
    const cx = dr.left - pr.left + dr.width / 2;
    const cy = dr.top - pr.top + dr.height / 2;

    const specs = [[138, .95, 3], [164, .62, 2.5], [192, .38, 2], [222, .18, 2]];
    specs.forEach(([r, op, wd]) => {
      for (let a = -84; a < 84; a += 3) {
        const t = 1 - Math.pow(Math.abs(a) / 84, 0.8);      // 两端淡出
        g.beginPath();
        g.arc(cx, cy, r, (a - 90) * Math.PI / 180, (a + 3 - 90) * Math.PI / 180);
        g.strokeStyle = `rgba(150,170,200,${(op * t).toFixed(3)})`;
        g.lineWidth = wd; g.lineCap = 'round';
        g.stroke();
      }
    });
    p.style.backgroundImage = 'url(' + cv.toDataURL('image/png') + ')';
  }

  /* ---------------- 拼色预设 ---------------- */
  const presetBox = $('presets');
  let presetKeys = [''];

  async function loadPresets() {
    try {
      const r = await fetch('/api/presets');
      const d = await r.json();
      presetBox.innerHTML = '';
      presetKeys = [''];
      const auto = document.createElement('div');
      auto.className = 'pchip auto on';
      auto.dataset.key = '';
      auto.textContent = '自动';
      auto.title = '由色相 + 方案自动生成';
      auto.onclick = () => applyPreset('');
      presetBox.appendChild(auto);
      (d.items || []).forEach(it => {
        presetKeys.push(it.key);
        const c = document.createElement('div');
        c.className = 'pchip';
        c.dataset.key = it.key;
        c.title = it.name + '　' + it.colors.join(' ');
        it.colors.forEach(hx => {
          const i = document.createElement('i');
          i.style.background = hx;
          c.appendChild(i);
        });
        c.onclick = () => applyPreset(it.key);
        presetBox.appendChild(c);
      });
    } catch (e) { /* 忽略 */ }
  }

  function applyPreset(key) {
    state.preset = key || '';
    document.querySelectorAll('.pchip').forEach(c =>
      c.classList.toggle('on', c.dataset.key === state.preset));
    const act = [...document.querySelectorAll('.pchip')].find(c => c.dataset.key === state.preset);
    if (act && act.scrollIntoView) act.scrollIntoView({ block: 'nearest', inline: 'center' });
    if (key && state.cvd) {                 // 预设生效时先保住设计师给的配色
      state.cvd = false;
      $('cvd').classList.toggle('on', false);
      $('cvd').setAttribute('aria-pressed', 'false');
    }
    schedule(true);
  }

  function stepPreset(dir) {
    const i = presetKeys.indexOf(state.preset || '');
    const n = presetKeys.length;
    applyPreset(presetKeys[((i + dir) % n + n) % n]);
  }

  /** 手动改任何参数 => 退出预设，回到"自动" */
  function manualEdit() {
    if (state.preset) applyPreset('');
  }

  /* ---------------- 色卡热区：点色块切换主色 ---------------- */
  const hits = $('hits');
  const plot = document.querySelector('.plot');

  function rgb2hsv(r, g, b) {
    r /= 255; g /= 255; b /= 255;
    const mx = Math.max(r, g, b), mn = Math.min(r, g, b), d = mx - mn;
    let h = 0;
    if (d > 1e-6) {
      if (mx === r) h = ((g - b) / d) % 6;
      else if (mx === g) h = (b - r) / d + 2;
      else h = (r - g) / d + 4;
      h *= 60; if (h < 0) h += 360;
    }
    return [h, mx === 0 ? 0 : d / mx, mx];
  }

  function layoutHits(boxes, size) {
    if (!boxes || !boxes.length) { hits.style.width = '0px'; return; }
    const pr = plot.getBoundingClientRect();
    const s = Math.min(pr.width / size[0], pr.height / size[1]);
    const iw = size[0] * s, ih = size[1] * s;
    hits.style.left = ((pr.width - iw) / 2) + 'px';
    hits.style.top = ((pr.height - ih) / 2) + 'px';
    hits.style.width = iw + 'px';
    hits.style.height = ih + 'px';
    hits.innerHTML = '';
    boxes.forEach((b, k) => {
      const el = document.createElement('div');
      el.className = 'hit';
      el.style.left = (b.x * s) + 'px';
      el.style.top = (b.y * s) + 'px';
      el.style.width = (b.w * s) + 'px';
      el.style.height = (b.h * s) + 'px';
      el.dataset.n = b.hex;
      el.title = b.hex + '　点击设为主色';
      el.onclick = () => useAsMain(b.hex);
      hits.appendChild(el);
    });
  }

  function useAsMain(hex) {
    const n = parseInt(hex.slice(1), 16);
    const [h, s, v] = rgb2hsv((n >> 16) & 255, (n >> 8) & 255, n & 255);
    state.hue = h;
    $('sat').value = Math.round(Math.min(0.95, Math.max(0.10, s)) * 100);
    $('val').value = Math.round(Math.min(0.97, Math.max(0.30, v)) * 100);
    $('sat').dispatchEvent(new Event('input'));
    $('val').dispatchEvent(new Event('input'));
    drawDial(h);
    if (state.preset) applyPreset('');       // 手动点色 = 退出预设
    schedule(true);
  }

  /* ---------------- 参数收集 ---------------- */
  function params() {
    if (state.pane === 'cmf') {
      return {
        hue: Math.round(state.hue),
        n: +$('n').value,
        scheme: $('scheme').value,
        material: $('material').value,
        shape: $('shape').value,
        sat: (+$('sat').value / 100).toFixed(3),
        val: (+$('val').value / 100).toFixed(3),
        target: +$('target').value,
        cvd: state.cvd ? 1 : 0,
        theme: 'light',
        preset: state.preset || '',
        wall: $('pwall').value,
        height: $('pheight').value,
        pattern: state.pattern,
        patIdx: state.patIdx,
        emboss: state.pattern === 'none' ? 0 : $('pemboss').value,
        name: $('name').value || '未命名方案'
      };
    }
    return {};
  }

  const qs = o => Object.entries(o).map(([k, v]) =>
    encodeURIComponent(k) + '=' + encodeURIComponent(v)).join('&');

  /* ---------------- 渲染 ---------------- */
  function schedule(now) {
    clearTimeout(timer);
    timer = setTimeout(render, now ? 0 : 320);
  }

  const loading = $('loading'), shot = $('shot'), status = $('status');

  async function render() {
    if (state.v3d && state.pane === 'cmf') { refresh3d(); return; }
    const my = ++seq;
    if (state.pattern === 'custom' && maskDirty) await uploadMask();
    loading.classList.add('show');
    const t0 = performance.now();
    try {
      const api = (state.sim && state.pane === 'cmf') ? 'sim'
        : (state.pane === 'cmf' ? 'cmf' : 'lamp');
      const url = '/api/' + api + '?' + qs(params()) + maskQS();
      const r = await fetch(url);
      const d = await r.json();
      if (my !== seq) return;
      if (d.error) { status.textContent = '出错：' + d.error.split('\n').pop(); return; }
      if (d.chips) fillPatCol(d.chips.map(c => c.hex), d.chips.map(c => c.name));
      shot.src = 'data:image/png;base64,' + d.png;
      if (d.boxes) { state.boxes = d.boxes; state.size = d.size || [1600, 1300]; }
      const dt = ((performance.now() - t0) / 1000).toFixed(1);
      if (api === 'sim') {
        const al = d.a11y || {};
        status.innerHTML = '色弱视图：四行依次是 <b>正常 / 红色型 / 绿色型 / 蓝黄型</b> 眼中的同一组配色。'
          + '<br>相邻色最差对比度 <b>' + (al.min_pair || '-') + ':1</b>　产品/台座 '
          + '<b>' + (al.product || '-') + ':1</b>　（WCAG 非文本建议 ≥ 3:1）　用时 ' + dt + 's';
      } else if (state.pane === 'cmf') {
        lastChips = d.chips || [];
        const names = lastChips.map((c, i) =>
          `${String(i + 1).padStart(2, '0')} ${c.name}`).join('　');
        const kind = Object.entries(d.kinds);
        const okAll = kind.every(([, v]) => v.bad === 0);
        status.innerHTML = names + '<br>正常视觉 ΔE00 <b>' + d.normal + '</b>　' +
          kind.map(([k, v]) => kindCN(k) + ' ' + v.min).join('　') +
          '　阈值 ' + d.target + (okAll ? '　<span style="color:#349458">√ 全部可辨识</span>' : '　<span style="color:#CC6A2C">▲ 有易混色对</span>') +
          '　用时 ' + dt + 's';
        if (d.a11y) {
          status.innerHTML += '<br>对比度：相邻色最小 <b>' + d.a11y.min_pair + ':1</b>　'
            + '产品/台座 <b>' + d.a11y.product + ':1</b>　（WCAG 非文本建议 ≥ 3:1）';
        }
        const main = lastChips[0];
        if (main) $('dialName').textContent = main.name + ' ' + main.hex;
      } else {
        const m = d.metrics;
        status.innerHTML = `峰值 <b>${m.peak} lux</b>　目标半径内平均 <b>${m.avg} lux</b>　` +
          `均匀度 <b>${m.uniformity}</b>　有效照明半径 <b>${m.hit} m</b>　` +
          `落地 ${d.rays} 条射线　用时 ${dt}s`;
      }
    } catch (e) {
      if (my === seq) status.textContent = '请求失败：' + e.message;
    } finally {
      if (my === seq) {
        loading.classList.remove('show');
        if (state.sim) { hits.innerHTML = ''; hits.style.width = '0px'; }
        else layoutHits(state.boxes, state.size);
      }
    }
  }

  const kindCN = k => ({ protan: '红色型', deutan: '绿色型', tritan: '蓝黄型' }[k] || k);

  /* ---------------- 交互绑定 ---------------- */
  const sliders = {
    n: v => v + ' 色', sat: v => v + '%', val: v => v + '%', target: v => v,
    pheight: v => v + ' mm', pwall: v => (+v).toFixed(1) + ' mm',
    pemboss: v => (+v).toFixed(1) + ' mm'
  };
  const valId = { n: 'nVal', sat: 'satVal', val: 'valVal', target: 'tgtVal',
                  pheight: 'htVal', pwall: 'wallVal', pemboss: 'embVal' };

  Object.keys(sliders).forEach(id => {
    const el = $(id);
    const sync = () => { const t = $(valId[id]); if (t) t.textContent = sliders[id](el.value); };
    el.addEventListener('input', () => { sync(); manualEdit(); schedule(); });
    el.addEventListener('change', () => { sync(); manualEdit(); schedule(true); });
    sync();
  });

  ['scheme', 'material', 'shape'].forEach(id =>
    $(id).addEventListener('change', () => { manualEdit(); schedule(true); }));

  /* ---------------- 手绘遮罩上传 ---------------- */
  let maskToken = '', maskDirty = true, maskTimer = null;

  async function uploadMask() {
    if (state.pattern !== 'custom') return '';
    try {
      const r = await fetch('/api/mask', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ data: window.View3D.maskDataURL() })
      });
      const d = await r.json();
      if (d.id) { maskToken = d.id; maskDirty = false; }
    } catch (e) { /* 保留上一个 token */ }
    return maskToken;
  }

  function onMaskChanged() {
    maskDirty = true;
    clearTimeout(maskTimer);
    maskTimer = setTimeout(uploadMask, 350);
  }

  const maskQS = () => (maskToken ? '&mask=' + maskToken : '');

  /* ---------------- 图画笔 ---------------- */
  let penDebounce = null;

  function setPaintMode(on) {
    state.paint = !!on;
    $('btnBrush').classList.toggle('on', state.paint);
    $('penTools').classList.toggle('hide', !state.paint);
    if (on) {
      if (!state.v3d) {
        state.v3d = true;
        $('btn3d').classList.add('on');
        document.querySelector('.plot').classList.add('gl');
      }
      if ($('pattern').value !== 'custom') {
        $('pattern').value = 'custom';
        $('pattern').dispatchEvent(new Event('change'));
      }
      status.innerHTML = '画笔：<b>在器物上按住左键画</b>　右键拖动转视角　滚轮缩放　'
        + '花纹色跟着「花纹色」走';
    }
    v3dReady(() => {
      window.View3D.setPaint(state.paint);
      fillPenPal();
      syncPen();
      const hx0 = (lastChips[state.patIdx] || {}).hex;
      if (hx0) applyBrush(hx0, state.patIdx); else syncPen();
      if (window.View3D.setLock) window.View3D.setLock(state.viewLock);
      syncUndo();
      if (state.paint) refresh3d();
    });
  }

  $('btnBrush').addEventListener('click', () => setPaintMode(!state.paint));
  $('btnPenDone').addEventListener('click', () => setPaintMode(false));

  /* ---- 撤销 ---- */
  function syncUndo(nU, nR) {
    const b = $('btnUndo'), r = $('btnRedo');
    const V = window.View3D || {};
    const u = (nU === undefined) ? (V.undoCount ? V.undoCount() : 0) : nU;
    const d = (nR === undefined) ? (V.redoCount ? V.redoCount() : 0) : nR;
    if (b) { b.disabled = u <= 0; b.textContent = u > 0 ? '撤销 ' + u : '撤销'; }
    if (r) { r.disabled = d <= 0; r.textContent = d > 0 ? '重做 ' + d : '重做'; }
  }

  function doUndo() {
    if (!(window.View3D && window.View3D.undo && window.View3D.undo())) return false;
    status.textContent = '已撤销一笔';
    return true;
  }

  function doRedo() {
    if (!(window.View3D && window.View3D.redo && window.View3D.redo())) return false;
    status.textContent = '已重做一笔';
    return true;
  }

  $('btnUndo').addEventListener('click', doUndo);
  $('btnRedo').addEventListener('click', doRedo);

  /* ---- 调色盘：OKLCH → sRGB，明度看起来均匀 ---- */
  function oklchHex(L, C, hueDeg) {
    const h = hueDeg * Math.PI / 180;
    const a = C * Math.cos(h), b = C * Math.sin(h);
    const l_ = L + 0.3963377774 * a + 0.2158037573 * b;
    const m_ = L - 0.1055613458 * a - 0.0638541728 * b;
    const s_ = L - 0.0894841775 * a - 1.2914855480 * b;
    const l = l_ * l_ * l_, m = m_ * m_ * m_, s = s_ * s_ * s_;
    const r = 4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s;
    const g = -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s;
    const bb = -0.0041960863 * l - 0.7034186147 * m + 1.7076147010 * s;
    const enc = v => {
      v = Math.max(0, Math.min(1, v));
      v = v <= 0.0031308 ? 12.92 * v : 1.055 * Math.pow(v, 1 / 2.4) - 0.055;
      return Math.max(0, Math.min(255, Math.round(v * 255)));
    };
    return '#' + [enc(r), enc(g), enc(bb)]
      .map(v => v.toString(16).padStart(2, '0')).join('').toUpperCase();
  }

  // 真实色相锚点（OKLCH 的 h 角）：赤≈29° 橙≈55° 黄≈100° 绿≈150° 青≈200° 蓝≈265° 紫≈322°
  const HUES = [
    [29, '赤'], [55, '橙'], [82, '橙黄'], [100, '黄'], [125, '黄绿'],
    [150, '绿'], [172, '青绿'], [200, '青'], [228, '天青'], [265, '蓝'],
    [295, '蓝紫'], [322, '紫'],
  ];
  const LIGHT_STEPS = [0.88, 0.76, 0.63, 0.50, 0.37];

  // 某一明度下这个色相最多能饱和到什么程度（超出 sRGB 就会被削平变灰）
  function maxChroma(L, hueDeg) {
    let lo = 0, hi = 0.37;
    for (let i = 0; i < 16; i++) {
      const mid = (lo + hi) / 2;
      if (inGamut(L, mid, hueDeg)) lo = mid; else hi = mid;
    }
    return lo;
  }

  function inGamut(L, C, hueDeg) {
    const h = hueDeg * Math.PI / 180;
    const a = C * Math.cos(h), b = C * Math.sin(h);
    const l_ = L + 0.3963377774 * a + 0.2158037573 * b;
    const m_ = L - 0.1055613458 * a - 0.0638541728 * b;
    const s_ = L - 0.0894841775 * a - 1.2914855480 * b;
    const l = l_ * l_ * l_, m = m_ * m_ * m_, s = s_ * s_ * s_;
    const r = 4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s;
    const g = -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s;
    const bb = -0.0041960863 * l - 0.7034186147 * m + 1.7076147010 * s;
    const e = 1e-4;
    return r >= -e && r <= 1 + e && g >= -e && g <= 1 + e && bb >= -e && bb <= 1 + e;
  }

  function buildPalGrid() {
    const box = $('palGrid');
    if (!box || box.dataset.done) return;
    let html = '';
    for (let r = 0; r < LIGHT_STEPS.length; r++) {
      const L = LIGHT_STEPS[r];
      for (let k = 0; k < HUES.length; k++) {
        const hue = HUES[k][0], nm = HUES[k][1];
        const C = maxChroma(L, hue) * 0.94;      // 留一点余量，别贴着 sRGB 边界
        const hex = oklchHex(L, C, hue);
        const tip = nm + ' · 明度' + Math.round(L * 100) + ' · ' + hex;
        html += '<i class="psw" data-hex="' + hex + '" title="' + tip
          + '" style="background:' + hex + '"></i>';
      }
    }
    box.innerHTML = html;
    box.dataset.done = '1';
    box.querySelectorAll('.psw').forEach(el => {
      el.addEventListener('click', () => {
        applyBrush(el.dataset.hex, null);
        box.querySelectorAll('.psw').forEach(x => x.classList.toggle('on', x === el));
      });
    });
  }

  function fillPalScheme() {
    const box = $('palScheme');
    if (!box) return;
    box.innerHTML = lastChips.map((c, i) =>
      '<i class="pd' + (i === state.patIdx ? ' on' : '') + '" data-i="' + i
      + '" style="background:' + c.hex + '" title="' + (c.name || c.hex) + '"></i>').join('');
    box.querySelectorAll('.pd').forEach(el => {
      el.addEventListener('click', () => {
        applyBrush(lastChips[+el.dataset.i].hex, +el.dataset.i);
        box.querySelectorAll('.pd').forEach(x => x.classList.toggle('on', x === el));
      });
    });
  }

  function applyBrush(hex, idx) {
    if (!hex) return;
    if (idx !== null && idx !== undefined) {
      state.patIdx = idx;
      patTouched = true;
      if ($('patCol').value !== String(idx)) $('patCol').value = String(idx);
    }
    if (window.View3D && window.View3D.setBrushColor) {
      window.View3D.setBrushColor(hex);
      window.View3D.setBrush(+$('pen').value, $('btnEraser').classList.contains('on'), hex);
    }
    $('penCur').style.background = hex;
    $('palCur').style.background = hex;
    $('palHex').textContent = hex;
    document.querySelectorAll('.penpal .pd[data-i]').forEach(d =>
      d.classList.toggle('on', idx !== null && +d.dataset.i === idx));
    const nm = (idx !== null && idx !== undefined && lastChips[idx]) ? lastChips[idx].name : '自定义色';
    status.innerHTML = '画笔颜色：<b>' + nm + '</b>　' + hex;
  }

  /* ---- 色板弹层 ---- */
  function togglePal(show) {
    const p = $('palPop');
    if (!p) return;
    const on = (show === undefined) ? p.classList.contains('hide') : !!show;
    p.classList.toggle('hide', !on);
    if (on) { buildPalGrid(); fillPalScheme(); }
  }
  $('btnPal').addEventListener('click', () => togglePal());
  $('palClose').addEventListener('click', () => togglePal(false));
  document.addEventListener('click', e => {
    const p = $('palPop');
    if (!p || p.classList.contains('hide')) return;
    if (p.contains(e.target) || e.target === $('btnPal')) return;
    togglePal(false);
  });

  /* ---- 调色盘（方案色） ---- */
  function setBrushColor(i) {
    const hex = lastChips[i] && lastChips[i].hex;
    if (!hex) return;
    applyBrush(hex, i);
    const box = $('palScheme');
    if (box) box.querySelectorAll('.pd').forEach(x =>
      x.classList.toggle('on', +x.dataset.i === i));
    const grid = $('palGrid');
    if (grid) grid.querySelectorAll('.psw').forEach(x => x.classList.remove('on'));
  }

  function fillPenPal() {
    const box = $('penPal');
    if (!box || !lastChips.length) return;
    box.innerHTML = lastChips.map((c, i) =>
      '<i class="pd' + (i === state.patIdx ? ' on' : '') + '" data-i="' + i
      + '" style="background:' + c.hex + '" title="' + (c.name || c.hex) + '"></i>').join('');
    box.querySelectorAll('.pd').forEach(el => {
      el.addEventListener('click', () => setBrushColor(+el.dataset.i));
    });
    fillPalScheme();
    buildPalGrid();
  }

  /* ---- 笔刷粗细 ---- */
  function syncPen() {
    const v = +$('pen').value;
    $('penVal').textContent = v;
    const d = Math.max(4, Math.min(22, Math.round(v * 0.42)));
    $('penDot').style.width = d + 'px';
    $('penDot').style.height = d + 'px';
    if (window.View3D && window.View3D.setBrush) {
      window.View3D.setBrush(v, $('btnEraser').classList.contains('on'));
    }
  }

  window.addEventListener('keydown', e => {
    if (!state.paint) return;
    if ((e.ctrlKey || e.metaKey) && (e.key === 'z' || e.key === 'Z')) {
      e.preventDefault();
      if (e.shiftKey) doRedo(); else doUndo();
    } else if ((e.ctrlKey || e.metaKey) && (e.key === 'y' || e.key === 'Y')) {
      e.preventDefault();
      doRedo();
    } else if (e.key === '[') {
      $('pen').value = Math.max(2, +$('pen').value - 2); syncPen();
    } else if (e.key === ']') {
      $('pen').value = Math.min(96, +$('pen').value + 2); syncPen();
    }
  });

  /* ---- 锁视角 ---- */
  $('btnLock').addEventListener('click', () => {
    const on = !$('btnLock').classList.contains('on');
    $('btnLock').classList.toggle('on', on);
    $('btnLock').textContent = on ? '已锁定' : '锁定';
    state.viewLock = on;
    if (window.View3D && window.View3D.setLock) window.View3D.setLock(on);
    status.innerHTML = on
      ? '视角已<b>锁定</b>：画的时候模型不动（再点一次解锁）'
      : '视角已解锁：右键拖动转视角、滚轮缩放';
  });

  $('btnEraser').addEventListener('click', () => {
    const on = !$('btnEraser').classList.contains('on');
    $('btnEraser').classList.toggle('on', on);
    window.View3D.setBrush(0, on, (lastChips[state.patIdx] || {}).hex);
    status.textContent = on ? '橡皮：擦掉笔迹' : '回到画笔';
  });

  $('pen').addEventListener('input', syncPen);

  $('btnMaskClear').addEventListener('click', () => {
    window.View3D.clearMask();
    if (!state.v3d) schedule(true);
    status.textContent = '手绘笔迹已清空';
  });

  document.querySelector('.plot').addEventListener('pointerup', () => {
    if (state.paint && maskDirty) uploadMask();
  });

  if (window.View3D && window.View3D.bindMaskChange) {
    window.View3D.bindMaskChange(onMaskChanged);
    if (window.View3D.bindUndoChange) window.View3D.bindUndoChange(syncUndo);
    if (state.viewLock && window.View3D.setLock) window.View3D.setLock(true);
    syncUndo();
  } else {
    window.addEventListener('view3d-ready', () => {
      if (window.View3D.bindMaskChange) window.View3D.bindMaskChange(onMaskChanged);
      if (window.View3D.bindUndoChange) window.View3D.bindUndoChange(syncUndo);
      if (state.paint) window.View3D.setPaint(state.paint);
      if (state.viewLock && window.View3D.setLock) window.View3D.setLock(true);
      fillPenPal(); syncPen(); syncUndo();
    }, { once: true });
  }

  /* ---------------- 花纹 ---------------- */
  function patName(id) {
    const p = (window.View3D && window.View3D.patterns ? window.View3D.patterns() : [])
      .find(x => x.id === id);
    return p ? p.name : '无花纹';
  }

  function fillPatList() {
    const list = (window.View3D && window.View3D.patterns) ? window.View3D.patterns() : [];
    const sel = $('pattern');
    if (!list.length || sel.dataset.done) return;
    sel.innerHTML = list.map(p =>
      '<option value="' + p.id + '">' + p.name + '</option>').join('');
    sel.dataset.done = '1';
    syncEmbRow();
  }

  let patColSig = '', patTouched = false;

  // 相对亮度：用来挑"最深的一支"当花纹色
  function lumOf(hex) {
    const r = parseInt(hex.slice(1, 3), 16) / 255;
    const g = parseInt(hex.slice(3, 5), 16) / 255;
    const b = parseInt(hex.slice(5, 7), 16) / 255;
    const f = c => (c <= 0.04045 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4));
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b);
  }

  function fillPatCol(colors, names) {
    if (!colors || !colors.length) return;
    const sig = colors.join(',') + '|' + (names || []).join(',');
    if (sig === patColSig) return;
    patColSig = sig;
    if (!patTouched) {                   // 没手动选过：自动取与主色对比最大的一支
      const l0 = lumOf(colors[0]);
      let best = colors.length > 1 ? 1 : 0, bd = -1;
      colors.forEach((c, i) => {
        if (i === 0 && colors.length > 1) return;
        const d = Math.abs(lumOf(c) - l0);
        if (d > bd) { bd = d; best = i; }
      });
      state.patIdx = best;
    } else if (state.patIdx >= colors.length) {
      state.patIdx = colors.length - 1;
    }
    const sel = $('patCol');
    sel.innerHTML = colors.map((c, i) =>
      '<option value="' + i + '"' + (i === state.patIdx ? ' selected' : '') + '>'
      + '色' + (i + 1) + ' ' + ((names && names[i]) || '')
      + '</option>').join('');
    sel.value = String(state.patIdx);
    fillPenPal();
  }

  syncEmbRow();
  if (window.View3D && window.View3D.patterns) fillPatList();
  else window.addEventListener('view3d-ready', fillPatList, { once: true });

  function syncEmbRow() {
    const v = $('pattern').value;
    const off = (!v || v === 'none');
    const r = $('embRow');
    if (r) r.classList.toggle('off', off);
  }

  $('pattern').addEventListener('change', () => {
    state.pattern = $('pattern').value;
    syncEmbRow();
    if (state.v3d) refresh3d(); else schedule(true);
  });
  $('patCol').addEventListener('change', () => {
    patTouched = true;
    state.patIdx = +$('patCol').value;
    const hx = (lastChips[state.patIdx] || {}).hex;
    if (hx) applyBrush(hx, state.patIdx);
    if (state.v3d) refresh3d(); else schedule(true);
  });
  $('name').addEventListener('input', () => schedule());

  $('cvd').addEventListener('click', () => {
    state.cvd = !state.cvd;
    $('cvd').classList.toggle('on', state.cvd);
    $('cvd').setAttribute('aria-pressed', state.cvd);
    schedule(true);
  });

  document.querySelectorAll('.tab').forEach(t => {
    t.addEventListener('click', () => {
      document.querySelectorAll('.tab').forEach(x => x.classList.toggle('on', x === t));
      state.pane = t.dataset.pane;
      $('cmfPane').classList.toggle('hide', state.pane !== 'cmf');
      $('lampPane').classList.toggle('hide', state.pane !== 'lamp');
      $('save').textContent = state.pane === 'cmf' ? '保存方案卡' : '保存三张图 + CSV';
      $('copy').textContent = state.pane === 'cmf' ? '复制色号清单' : '复制指标摘要';
      schedule(true);
    });
  });

  $('save').addEventListener('click', async () => {
    const bt = $('save'), old = bt.textContent;
    bt.textContent = '正在保存…'; bt.disabled = true;
    try {
      const r = await fetch('/api/save', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          kind: state.pane,
          params: Object.assign({}, params(), maskToken ? { mask: maskToken } : {})
        })
      });
      const d = await r.json();
      if (d.error) throw new Error(d.error.split('\n').pop());
      status.textContent = '已保存：' + d.files.map(f => f.split('\\').pop()).join('、');
    } catch (e) {
      status.textContent = '保存失败：' + e.message;
    } finally {
      bt.textContent = old; bt.disabled = false;
    }
  });

  $('openout').addEventListener('click', () => {
    fetch('/api/open', { method: 'POST' });
  });

  $('copy').addEventListener('click', async () => {
    let txt = '';
    if (state.pane === 'cmf') {
      txt = lastChips.map((c, i) =>
        `${String(i + 1).padStart(2, '0')}\t${c.name}\t${c.hex}\tRGB ${c.rgb}\tCMYK ${c.cmyk}\tL* ${c.l}`).join('\n');
    } else {
      txt = status.textContent;
    }
    try {
      await navigator.clipboard.writeText(txt);
      status.textContent = '已复制到剪贴板';
    } catch (e) {
      status.textContent = '复制失败：' + e.message;
    }
  });

  /* ---------------- 3D 视图 ---------------- */
  const gl = $('gl');
  let v3dLoaded = false, v3dTimer = null;

  function v3dReady(cb) {
    if (window.View3D && window.View3D.ready()) return cb();
    if (!window.View3D) {
      window.addEventListener('view3d-ready', () => v3dReady(cb), { once: true });
      return;
    }
    cb();
  }

  async function refresh3d() {
    if (!state.v3d) return;
    try {
      const r = await fetch('/api/geo?' + qs(params()));
      const d = await r.json();
      if (d.error) { status.textContent = '3D 出错：' + d.error.split('\n').pop(); return; }
      d.pattern = state.pattern;
      d.patIdx = state.patIdx;
      d.emboss = state.pattern === 'none' ? 0 : +$('pemboss').value;
      fillPatCol(d.colors, d.names);
      let inf = { segments: 96, rows: 0 };
      v3dReady(() => {
        window.View3D.init(gl);
        window.View3D.update(d);
        window.View3D.setVisible(true);
        window.View3D.resize();
        inf = window.View3D.info ? window.View3D.info() : inf;
        v3dLoaded = true;
      });
      const gh = document.querySelector('.ghint');
      if (gh) {
        gh.textContent = (d.emboss > 0)
          ? '拖动旋转 · 滚轮缩放（放大可看浮雕）' : '拖动旋转 · 滚轮缩放';
      }
      status.innerHTML = '3D 预览：<b>' + d.names.join(' / ') + '</b>　'
        + '材质 ' + ({ matte: '哑光陶瓷', frosted: '磨砂', metal: '金属拉丝',
                       leather: '皮革', gloss: '高光塑料' }[d.material] || d.material)
        + '　' + (inf.rows || d.points.length) + ' 段母线 · ' + inf.segments + ' 等分旋转体　'
        + (d.closed ? '封闭壳体' : '开口容器') + ' · 壁厚 ' + d.wall_mm + 'mm / 外形 ' + d.height_mm + 'mm'
        + (d.emboss > 0 ? ' · 浮雕 ' + (+d.emboss).toFixed(1) + 'mm' : '')
        + '　花纹：' + patName(state.pattern)
        + (state.pattern === 'none' ? '' : '（' + (d.names[state.patIdx] || '——') + '）')
        + '　打光：' + LIGHT_NAMES[lightIdx];
    } catch (e) {
      status.textContent = '3D 请求失败：' + e.message;
    }
  }

  $('btn3d').addEventListener('click', () => {
    state.v3d = !state.v3d;
    $('btn3d').classList.toggle('on', state.v3d);
    document.querySelector('.plot').classList.toggle('gl', state.v3d);
    if (state.v3d) {
      state.sim = false;
      $('btnSim').classList.remove('on');
      loading.classList.remove('show');
      hits.innerHTML = ''; hits.style.width = '0px';
      refresh3d();
    } else {
      v3dReady(() => window.View3D.setVisible(false));
      schedule(true);
    }
  });

  window.addEventListener('resize', () => {
    if (state.v3d) v3dReady(() => window.View3D.resize());
  });

  /* ---------------- 无障碍工具 ---------------- */
  $('pPrev').addEventListener('click', () => stepPreset(-1));
  $('pNext').addEventListener('click', () => stepPreset(1));

  let lightIdx = 0;
  const LIGHT_NAMES = ['工作室', '侧逆光', '顶部光', '柔光箱'];
  $('btnLight').addEventListener('click', () => {
    lightIdx = (lightIdx + 1) % LIGHT_NAMES.length;
    $('btnLight').textContent = '光源：' + LIGHT_NAMES[lightIdx];
    $('btnLight').classList.toggle('on', lightIdx !== 0);
    if (!state.v3d) {                     // 不在 3D 视图也先切过去
      state.v3d = true;
      $('btn3d').classList.add('on');
      document.querySelector('.plot').classList.add('gl');
      refresh3d();
    }
    v3dReady(() => window.View3D.setLight(lightIdx));
    status.innerHTML = '打光：<b>' + LIGHT_NAMES[lightIdx] + '</b>　'
      + '（光源球亮一下表示主光方向，随后淡出）';
  });

  $('btnStl').addEventListener('click', async () => {
    const bt = $('btnStl');
    bt.disabled = true; bt.textContent = '生成中…';
    const p = params();
    // 先同步触发下载：await 之后用户手势就失效了，下载会被浏览器拦掉
    try {
      const a0 = document.createElement('a');
      a0.href = '/api/stl?' + qs(p) + maskQS();
      a0.download = 'LumiCurve_' + $('shape').value + '_'
        + $('pheight').value + 'mm_' + (+$('pwall').value).toFixed(1) + 'wall.stl';
      document.body.appendChild(a0);
      a0.click();
      a0.remove();
    } catch (e) { /* 忽略 */ }
    try {
      const r = await fetch('/api/print?' + qs(p) + maskQS());
      const d = await r.json();
      if (d.error) throw new Error(d.error.split('\n').pop());
      const ok = [];
      ok.push(d.wall_mm >= 1.2 ? '壁厚 ' + d.wall_mm + 'mm √' : '壁厚过薄 ×');
      ok.push(d.need_support ? '最大悬垂 ' + d.overhang_deg + '° ▲ 需支撑'
                             : '最大悬垂 ' + d.overhang_deg + '° √');
      status.innerHTML = '<b>可打印</b>　外形 ' + d.size_mm.join(' × ') + ' mm　'
        + '壁厚 ' + d.wall_mm + ' mm　体积 ' + d.volume_cm3 + ' cm³　'
        + '耗材约 <b>' + d.mass_g + ' g</b>（PLA）　三角面 ' + d.triangles
        + (d.emboss_mm > 0 ? '浮雕 ' + d.emboss_mm + ' mm　' : '')
        + '<br>' + ok.join('　') + '　封闭实体 √　'
        + '<span style="color:#349458">STL 已开始下载（' + d.stl_mb + ' MB）</span>';
    } catch (e) {
      status.textContent = '导出失败：' + e.message;
    } finally {
      bt.disabled = false; bt.textContent = '导出 STL';
    }
  });

  $('btnSim').addEventListener('click', () => {
    state.sim = !state.sim;
    $('btnSim').classList.toggle('on', state.sim);
    if (state.sim) { hits.innerHTML = ''; hits.style.width = '0px'; }
    schedule(true);
  });

  $('btnFix').addEventListener('click', async () => {
    const bt = $('btnFix');
    bt.disabled = true; bt.textContent = '搜索中…';
    try {
      const r = await fetch('/api/fix?' + qs(params()));
      const d = await r.json();
      if (d.error) throw new Error(d.error.split('\n').pop());
      applyPreset('');
      state.hue = d.hue;
      if (d.scheme) $('scheme').value = d.scheme;
      $('sat').value = Math.round(d.sat * 100);
      $('val').value = Math.round(d.val * 100);
      $('sat').dispatchEvent(new Event('input'));
      $('val').dispatchEvent(new Event('input'));
      drawDial(d.hue);
      const nameOf = { analogous: '类似色', complementary: '互补色',
                       triadic: '三分色', split: '分裂互补', mono: '单色渐变' };
      status.innerHTML = (d.reached ? '已找到达标方案' : '最接近的方案（仍差一点）')
        + '：主色相 <b>' + d.hue + '°</b>　饱和 ' + Math.round(d.sat * 100)
        + '%　明度 ' + Math.round(d.val * 100) + '%'
        + (d.scheme_changed ? '　<span style="color:#CC6A2C">方案改为「'
            + (nameOf[d.scheme] || d.scheme) + '」</span>' : '')
        + '<br>最差色差 ΔE00 <b>' + d.min + '</b>（阈值 ' + d.target + '）'
        + (d.reached ? '　√ 三种色觉都通过'
                     : '　▲ 该组颜色本身太接近，建议再加一档明度差或换材质');
      schedule(true);
    } catch (e) {
      status.textContent = '搜索失败：' + e.message;
    } finally {
      bt.disabled = false; bt.textContent = '找达标方案';
    }
  });

  /* ---------------- 启动 ---------------- */
  drawDial(state.hue);
  loadPresets();
  requestAnimationFrame(() => { drawArcs(); render(); });
  window.addEventListener('resize', () => { drawArcs(); layoutHits(state.boxes, state.size); });
})();

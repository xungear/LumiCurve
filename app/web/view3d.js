// LumiCurve · 3D 产品预览（three.js）
import * as THREE from 'three';
import { OrbitControls } from './vendor/OrbitControls.js';

// 材质配置：贴图 + PBR 参数
// 贴图来源：Poly Haven（CC0）的哑光/磨砂/皮革，金属与亮面的法线为程序生成
const MATCFG = {
  matte:   { rough: 1.00, metal: 0.00, nscale: 0.9, repeat: [2.0, 1.0], useRough: true },
  frosted: { rough: 1.00, metal: 0.00, nscale: 1.3, repeat: [2.4, 1.2], useRough: true },
  leather: { rough: 1.00, metal: 0.00, nscale: 1.5, repeat: [3.2, 1.6], useRough: true },
  metal:   { rough: 0.26, metal: 0.88, nscale: 0.7, repeat: [2.6, 1.0], useRough: false, env: 2.0 },
  gloss:   { rough: 0.12, metal: 0.02, nscale: 0.4, repeat: [1.0, 1.0], useRough: false, env: 1.35 },
  wood:    { rough: 1.00, metal: 0.00, nscale: 1.35, repeat: [1.6, 1.05], useRough: true },
};
const SPACING = 0.62;
const TEX = {};          // 材质键 -> {map, normalMap, roughnessMap}
let texLoader = null;

let renderer = null, scene = null, camera = null, controls = null;
let group = null, shadows = [], canvas = null;
let hemi = null, keyLight = null, fillLight = null, rimLight = null, gizmo = null;
let gizmoFade = 0, lightPreset = 0;
let visible = false;
const state = { points: [], colors: [], material: 'matte', wall: 0.011, emboss: 0, heightMm: 300,
                closed: false, pattern: 'none', patIdx: 1 };

/* ---------------- 环境贴图（程序生成，不用外部素材） ---------------- */
function envTexture() {
  const c = document.createElement('canvas');
  c.width = 512; c.height = 256;
  const g = c.getContext('2d');
  const lg = g.createLinearGradient(0, 0, 0, 256);
  lg.addColorStop(0.00, '#ffffff');
  lg.addColorStop(0.42, '#f2f6fb');
  lg.addColorStop(0.56, '#dde5ef');
  lg.addColorStop(1.00, '#c6cfdb');
  g.fillStyle = lg; g.fillRect(0, 0, 512, 256);
  const r = g.createRadialGradient(160, 66, 4, 160, 66, 130);
  r.addColorStop(0, '#ffffff');
  r.addColorStop(1, 'rgba(255,255,255,0)');
  g.fillStyle = r; g.fillRect(0, 0, 512, 256);
  const t = new THREE.CanvasTexture(c);
  t.mapping = THREE.EquirectangularReflectionMapping;
  t.colorSpace = THREE.SRGBColorSpace;
  return t;
}

function loadTexSet(key) {
  const set = {};
  const mk = (file, srgb) => {
    const t = texLoader.load('tex/' + file);
    t.wrapS = t.wrapT = THREE.RepeatWrapping;
    if (srgb) t.colorSpace = THREE.SRGBColorSpace;
    t.anisotropy = 4;
    return t;
  };
  try {
    // 金属与亮面塑料是"没有图案"的表面，只挂法线；颜色完全来自色卡
    if (key !== 'metal' && key !== 'gloss') {
      set.map = mk(key + '_diff.jpg', true);
      set.roughnessMap = mk(key + '_rough.jpg', false);
    }
    set.normalMap = mk(key + '_nor_gl.jpg', false);
  } catch (e) { /* 贴图缺失就退回纯色 */ }
  return set;
}

function applyTex(mat, key) {
  const cfg = MATCFG[key] || MATCFG.matte;
  const set = TEX[key] || {};
  mat.roughness = cfg.rough;
  mat.metalness = cfg.metal;
  mat.envMapIntensity = cfg.env != null ? cfg.env : 1.05;
  // albedo 由 buildAlbedo 逐色烘焙，这里不再挂 set.map
  if (set.normalMap) {
    mat.normalMap = set.normalMap;
    mat.normalScale = new THREE.Vector2(cfg.nscale, cfg.nscale);
  }
  if (cfg.useRough && set.roughnessMap) mat.roughnessMap = set.roughnessMap;
  const rp = cfg.repeat;
  [set.map, set.normalMap, cfg.useRough ? set.roughnessMap : null].forEach(t => {
    if (t && t.repeat) t.repeat.set(rp[0], rp[1]);
  });
  mat.needsUpdate = true;
  return mat;
}

function shadowTexture() {
  const c = document.createElement('canvas');
  c.width = c.height = 256;
  const g = c.getContext('2d');
  const r = g.createRadialGradient(128, 128, 8, 128, 128, 124);
  r.addColorStop(0.0, 'rgba(58,72,92,0.46)');
  r.addColorStop(0.5, 'rgba(58,72,92,0.16)');
  r.addColorStop(1.0, 'rgba(58,72,92,0)');
  g.fillStyle = r; g.fillRect(0, 0, 256, 256);
  return new THREE.CanvasTexture(c);
}

/* ---------------- 初始化 ---------------- */
export function init(el) {
  if (renderer) return;
  canvas = el;
  renderer = new THREE.WebGLRenderer({ canvas: el, antialias: true, alpha: true });
  renderer.setPixelRatio(Math.min(devicePixelRatio || 1, 2));
  renderer.outputColorSpace = THREE.SRGBColorSpace;

  scene = new THREE.Scene();
  scene.environment = envTexture();

  camera = new THREE.PerspectiveCamera(30, 1, 0.1, 60);
  camera.position.set(0, 0.70, 2.35);

  controls = new OrbitControls(camera, canvas);
  controls.target.set(0, 0.46, 0);
  controls.enableDamping = true;
  controls.dampingFactor = 0.08;
  controls.enablePan = false;
  controls.minDistance = 1.2;
  controls.maxDistance = 5.0;
  controls.maxPolarAngle = Math.PI * 0.495;
  controls.minPolarAngle = Math.PI * 0.08;

  // 画笔：射线拾取 + 遮罩绘制
  canvas.style.touchAction = 'none';
  canvas.addEventListener('pointerdown', onDown);
  canvas.addEventListener('pointermove', onMove);
  canvas.addEventListener('pointerup', onUp);
  canvas.addEventListener('pointercancel', onUp);
  window.addEventListener('pointerup', onUp);

  hemi = new THREE.HemisphereLight(0xffffff, 0xb7c4d3, 1.0);
  scene.add(hemi);
  keyLight = new THREE.DirectionalLight(0xffffff, 2.1);
  fillLight = new THREE.DirectionalLight(0xdfe8f5, 0.85);
  rimLight = new THREE.DirectionalLight(0xffffff, 0.95);
  scene.add(keyLight, fillLight, rimLight);

  // 光源指示球：切换光源档位时短暂亮一下，让你看见光从哪来
  gizmo = new THREE.Mesh(
    new THREE.SphereGeometry(0.035, 20, 16),
    new THREE.MeshBasicMaterial({ color: 0xffffff, transparent: true, opacity: 0 })
  );
  scene.add(gizmo);
  applyLight(0);

  group = new THREE.Group();
  scene.add(group);

  texLoader = new THREE.TextureLoader();
  Object.keys(MATCFG).forEach(k => { TEX[k] = loadTexSet(k); });

  const loop = () => {
    requestAnimationFrame(loop);
    if (!visible) return;
    controls.update();
    if (gizmo && gizmoFade > 0) {
      gizmoFade = Math.max(0, gizmoFade - 0.012);
      gizmo.material.opacity = Math.min(0.85, gizmoFade * 0.85);
      gizmo.visible = gizmoFade > 0.01;
    }
    renderer.render(scene, camera);
  };
  loop();
}

/* ---------------- 花纹 ---------------- */
// 纹样画在 512×512 的 UV 画布上：横向 = 绕器身一圈，纵向 = 瓶口(上) → 瓶底(下)。
// 关键：母线 UV 的 v 并不按高度均匀分布（细颈会吃掉大半 v），
// 所以纹样位置按"物理高度"锚定，再换算成画布行，换个器型也不会跑偏。
const PAT_CACHE = {};

const PATTERNS = [
  { id: 'none', name: '无花纹' },
  { id: 'meander', name: '回纹' },
  { id: 'lotus', name: '莲瓣' },
  { id: 'wave', name: '水波' },
  { id: 'scroll', name: '缠枝' },
  { id: 'stripe', name: '竖条纹' },
  { id: 'dot', name: '波点' },
  { id: 'custom', name: '手绘（自己在器物上画）' },
];

/** 物理高度 0(底)..1(口) → UV 画布行 y(0=口)。v 已在 rebuild() 里按高度重写。 */
function cyOf(yPhys) {
  return 512 * (1 - Math.max(0, Math.min(1, yPhys)));
}

function pcanvas() {
  const c = document.createElement('canvas');
  c.width = c.height = 512;
  const g = c.getContext('2d');
  g.strokeStyle = '#fff';
  g.fillStyle = '#fff';
  g.lineJoin = 'miter';
  g.lineCap = 'butt';
  return [c, g];
}

const PAT_GEO = {};   // 每种纹样的物理高度锚点

function patMask(id) {
  if (id === 'custom') return brushCanvas();
  // --- 锚点（物理高度，换个器型自动重算）---
  const g0 = {
    bandT: 0.745, bandB: 0.585,      // 回纹带
    lotT: 0.800, lotB: 0.560,        // 莲瓣
    wavB: 0.000, wavT: 0.215,        // 水波
    scrC: 0.400, scrA: 0.090,        // 缠枝中心与振幅
  };
  const geo = {
    bandT: cyOf(g0.bandT), bandB: cyOf(g0.bandB),
    lotT: cyOf(g0.lotT), lotB: cyOf(g0.lotB),
    wavT: cyOf(g0.wavT), wavB: cyOf(g0.wavB),
    scrC: cyOf(g0.scrC), scrA: (cyOf(g0.scrC - g0.scrA) - cyOf(g0.scrC + g0.scrA)) / 2,
  };
  // 圆点在 UV 里会被压扁/拉长：u 是整圈周长，v 是高度，比例 = 2πr̄ / H
  const pts0 = state.points || [];
  let rs = 0;
  for (let k = 0; k < pts0.length; k++) rs += pts0[k][1] || 0;
  const rbar = pts0.length ? rs / pts0.length : 0.2;
  const aspect = Math.max(0.32, Math.min(1.0, 1 / (2 * Math.PI * Math.max(0.02, rbar))));

  const key = id + '|' + Math.round(aspect * 100);
  if (PAT_CACHE[key]) return PAT_CACHE[key];

  const [c, g] = pcanvas();

  if (id === 'meander') {                     // 回纹带（器身上半部）
    const y0 = Math.round(geo.bandT);
    const h = Math.max(18, Math.round(geo.bandB - geo.bandT));
    const unit = 512 / 8;
    g.lineWidth = Math.max(4, h * 0.17);
    for (let i = 0; i < 8; i++) {
      const x = i * unit;
      g.beginPath();
      g.moveTo(x, y0 + h);
      g.lineTo(x, y0);
      g.lineTo(x + unit * 0.72, y0);
      g.lineTo(x + unit * 0.72, y0 + h * 0.62);
      g.lineTo(x + unit * 0.30, y0 + h * 0.62);
      g.lineTo(x + unit * 0.30, y0 + h * 0.28);
      g.lineTo(x + unit * 0.52, y0 + h * 0.28);
      g.stroke();
    }
    g.lineWidth = Math.max(2, h * 0.075);
    g.beginPath(); g.moveTo(0, y0 - h * 0.16); g.lineTo(512, y0 - h * 0.16); g.stroke();
    g.beginPath(); g.moveTo(0, y0 + h * 1.16); g.lineTo(512, y0 + h * 1.16); g.stroke();

  } else if (id === 'lotus') {                // 莲瓣（肩部下垂，双层）
    const n = 8, w = 512 / n;
    const top = Math.round(geo.lotT);
    const deep = Math.max(20, Math.round(geo.lotB - geo.lotT));
    g.lineWidth = Math.max(3, deep * 0.055);
    for (let i = 0; i < n; i++) {
      const cx = i * w + w / 2;
      g.beginPath();
      g.moveTo(cx - w * 0.42, top);
      g.bezierCurveTo(cx - w * 0.42, top + deep * 0.74,
                      cx - w * 0.18, top + deep * 0.96, cx, top + deep);
      g.bezierCurveTo(cx + w * 0.18, top + deep * 0.96,
                      cx + w * 0.42, top + deep * 0.74, cx + w * 0.42, top);
      g.stroke();
    }
    g.beginPath(); g.moveTo(0, top - deep * 0.07); g.lineTo(512, top - deep * 0.07); g.stroke();
    g.beginPath(); g.moveTo(0, top + deep * 1.07); g.lineTo(512, top + deep * 1.07); g.stroke();

  } else if (id === 'wave') {                 // 海水江崖（底部叠波）
    const top = Math.round(geo.wavT);
    const span = 512 - top;
    const step = span / 3.4;
    g.lineWidth = Math.max(3, step * 0.16);
    for (let row = 0; row < 4; row++) {
      const base = 512 - row * step * 0.92;
      for (let i = 0; i < 8; i++) {
        const cx = i * 64 + (row % 2 ? 32 : 0);
        g.beginPath();
        g.arc(cx, base, 30, Math.PI, 0);
        g.stroke();
      }
    }

  } else if (id === 'scroll') {               // 缠枝：单股藤蔓 + 节点左右对称叶
    const yc = geo.scrC, amp = Math.max(6, geo.scrA), K = Math.PI * 6;
    g.lineWidth = Math.max(3, amp * 0.30);
    g.beginPath();
    for (let x = 0; x <= 512; x += 4) {
      const y = yc + amp * Math.sin((x / 512) * K);
      if (x === 0) g.moveTo(x, y); else g.lineTo(x, y);
    }
    g.stroke();
    for (let i = 0; i < 8; i++) {
      const x = i * 64 + 32;
      const ph = (x / 512) * K;
      const y = yc + amp * Math.sin(ph);
      const ang = Math.atan2(amp * Math.cos(ph) * (K / 512), 1);
      for (const sd of [1, -1]) {
        g.save();
        g.translate(x, y + sd * amp * 0.38);
        g.rotate(ang + sd * 1.10);
        g.beginPath();
        g.ellipse(0, 0, amp * 0.50, amp * 0.22, 0, 0, Math.PI * 2);
        g.fill();
        g.restore();
      }
    }

  } else if (id === 'stripe') {               // 竖条纹
    for (let i = 0; i < 8; i++) g.fillRect(i * 64 + 24, 0, 16, 512);

  } else if (id === 'dot') {                  // 波点（错位网格，按口径比修正成圆）
    const r = 9.5, rx = r * aspect;
    for (let row = 0; row < 8; row++) {
      for (let col = 0; col < 8; col++) {
        const x = (col * 64 + (row % 2 ? 32 : 0) + 32) % 512;
        const y = row * 64 + 32;
        g.beginPath(); g.ellipse(x, y, rx, r, 0, 0, Math.PI * 2); g.fill();
      }
    }
  }
  PAT_CACHE[key] = c;
  return c;
}

/* ---------------- 画笔（手绘花纹） ---------------- */
// 遮罩画在 512×512 的 UV 画布上，白色笔迹 = 花纹处。
// 直接用射线拾取模型拿到 uv，所以是在器物上"落笔"，不是盯着一张平铺图。
let brushCv = null, brushCtx = null;
let brushVer = 0;                 // 遮罩版本号：变了就丢掉缓存的高度场
let paintMode = false, painting = false;
let paintLast = null, paintIdx = 0;
const UNDO_MAX = 14;
let undoStack = [], redoStack = [], undoBusy = false;
let viewLocked = false;
let brushColor = '#7b5ba6';          // 由界面里的调色盘设置
let brushPx = 18, brushErase = false;
let bodyMeshes = [], bodyTex = [];
const raycaster = new THREE.Raycaster();
const ndc = new THREE.Vector2();

function brushCanvas() {
  if (!brushCv) {
    brushCv = document.createElement('canvas');
    brushCv.width = brushCv.height = 512;
    brushCtx = brushCv.getContext('2d');
  }
  return brushCv;
}

function hasMask() {
  if (!brushCv) return false;
  const d = brushCtx.getImageData(0, 0, 512, 512).data;
  for (let i = 3; i < d.length; i += 4 * 7) if (d[i] > 8) return true;
  return false;
}

/** 每一笔开画之前存一张快照；撤销就是把快照贴回去 */
function snap() { return brushCtx.getImageData(0, 0, 512, 512); }

function restore(img) {
  undoBusy = true;                       // 回贴时不要被自己再次记录
  brushCtx.globalCompositeOperation = 'source-over';
  brushCtx.clearRect(0, 0, 512, 512);
  brushCtx.putImageData(img, 0, 0);
  undoBusy = false;
  maskChanged();
  if (group) rebuild();
  notifyUndo();
}

function pushUndo() {
  brushCanvas();
  if (undoBusy) return;
  undoStack.push(snap());
  if (undoStack.length > UNDO_MAX) undoStack.shift();
  redoStack.length = 0;                  // 有了新动作，之前撤掉的就不能再重做了
  notifyUndo();
}

function notifyUndo() {
  if (typeof onUndoChange === 'function') onUndoChange(undoStack.length, redoStack.length);
}

export function undo() {
  brushCanvas();
  if (!undoStack.length) return false;
  const img = undoStack.pop();
  redoStack.push(snap());
  if (redoStack.length > UNDO_MAX) redoStack.shift();
  restore(img);
  return true;
}

export function redo() {
  brushCanvas();
  if (!redoStack.length) return false;
  const img = redoStack.pop();
  undoStack.push(snap());
  if (undoStack.length > UNDO_MAX) undoStack.shift();
  restore(img);
  return true;
}

export function undoCount() { return undoStack.length; }
export function redoCount() { return redoStack.length; }
let onUndoChange = null;
export function bindUndoChange(fn) { onUndoChange = fn; }
export function brushState() {
  return { px: brushPx, erase: brushErase, color: brushColor };
}

function maskChanged() {
  brushVer++;
  delete HF_CACHE['custom'];
  if (typeof onMaskChange === 'function') onMaskChange();
}

function stamp(x, y) {
  const r = brushPx / 2;
  brushCtx.globalCompositeOperation = brushErase ? 'destination-out' : 'source-over';
  brushCtx.fillStyle = brushErase ? '#000000' : brushColor;
  for (const dx of [-512, 0, 512]) {          // 三个副本：笔迹跨接缝时自动接上
    brushCtx.beginPath();
    brushCtx.arc(x + dx, y, r, 0, Math.PI * 2);
    brushCtx.fill();
  }
}

function dabLine(x1, y1, x2, y2) {
  const d = Math.hypot(x2 - x1, y2 - y1);
  const step = Math.max(1.2, brushPx * 0.28);
  const n = Math.max(1, Math.ceil(d / step));
  for (let i = 1; i <= n; i++) {
    const t = i / n;
    stamp(x1 + (x2 - x1) * t, y1 + (y2 - y1) * t);
  }
}

function pickBody(ev) {
  if (!camera || !canvas) return null;
  const rect = canvas.getBoundingClientRect();
  ndc.set(((ev.clientX - rect.left) / rect.width) * 2 - 1,
          -((ev.clientY - rect.top) / rect.height) * 2 + 1);
  raycaster.setFromCamera(ndc, camera);
  const hits = raycaster.intersectObjects(bodyMeshes, false);
  if (!hits.length || !hits[0].uv) return null;
  return hits[0];
}

function repaintBodies() {
  const patHex = (state.colors[state.patIdx] || state.colors[1] || '#ffffff');
  for (const b of bodyTex) {
    buildAlbedo(state.material, b.hex, state.pattern, patHex, b.cv);
    b.tex.needsUpdate = true;
  }
}

function onDown(ev) {
  if (!paintMode || ev.button !== 0) return;
  brushCanvas();
  const hit = pickBody(ev);
  if (!hit) return;
  ev.preventDefault();
  painting = true;
  paintIdx = hit.object.userData.idx || 0;
  pushUndo();                          // 一笔一个撤销点
  // 记录"未展开"的 u，跨接缝也能连成一条线
  paintLast = { u: hit.uv.x, v: hit.uv.y };
  stamp(hit.uv.x * 511, (1 - hit.uv.y) * 511);
  repaintBodies();
  maskChanged();
  if (canvas.setPointerCapture && ev.pointerId !== undefined) {
    try { canvas.setPointerCapture(ev.pointerId); } catch (e) { }
  }
}

function onMove(ev) {
  if (!paintMode) return;
  const hit = pickBody(ev);
  if (!hit) return;
  if (!painting) {
    // 起笔点落在模型外面、拖着划进来：在第一次命中的地方起笔（别把整笔丢掉）
    if ((ev.buttons & 1) !== 1) return;
    painting = true;
    paintIdx = hit.object.userData.idx || 0;
    pushUndo();
    paintLast = { u: hit.uv.x, v: hit.uv.y };
    stamp(hit.uv.x * 511, (1 - hit.uv.y) * 511);
    repaintBodies();
    maskChanged();
    return;
  }
  ev.preventDefault();
  let u = hit.uv.x;
  const v = hit.uv.y;
  while (u - paintLast.u > 0.5) u -= 1;
  while (u - paintLast.u < -0.5) u += 1;
  dabLine(paintLast.u * 511, (1 - paintLast.v) * 511, u * 511, (1 - v) * 511);
  paintLast = { u, v };
  repaintBodies();
  maskChanged();
}

function onUp() {
  if (!painting) return;
  painting = false;
  paintLast = null;
  rebuild();                 // 落笔结束才重算几何（浮雕要重挤一次）
}

export function setPaint(on) {
  paintMode = !!on;
  if (canvas) canvas.style.cursor = on ? 'crosshair' : '';
  if (controls) {
    controls.mouseButtons = on
      ? { LEFT: null, MIDDLE: THREE.MOUSE.DOLLY, RIGHT: THREE.MOUSE.ROTATE }
      : { LEFT: THREE.MOUSE.ROTATE, MIDDLE: THREE.MOUSE.DOLLY, RIGHT: THREE.MOUSE.PAN };
  }
  if (!on) painting = false;
  return paintMode;
}

export function setLock(on) {
  viewLocked = !!on;
  if (controls) controls.enabled = !viewLocked;
  return viewLocked;
}

export function locked() { return viewLocked; }

export function setBrush(px, erase, color) {
  if (px) brushPx = Math.max(2, Math.min(96, px));
  if (color) brushColor = color;
  brushErase = !!erase;
  return { px: brushPx, erase: brushErase, color: brushColor };
}

export function setBrushColor(hex) {
  if (hex) brushColor = hex;
  return brushColor;
}

export function clearMask() {
  brushCanvas();
  pushUndo();
  brushCtx.clearRect(0, 0, 512, 512);
  maskChanged();
  if (group) rebuild();
}

export function maskDataURL() {
  brushCanvas();
  return brushCv.toDataURL('image/png');
}

export function maskPixels() {
  if (!brushCv) return 0;
  const d = brushCtx.getImageData(0, 0, 512, 512).data;
  let n = 0;
  for (let i = 3; i < d.length; i += 4) if (d[i] > 8) n++;
  return n;
}

/* ---------------- 浮雕高度场 ---------------- */
// 花纹遮罩做高斯模糊当高度场：边缘是斜的，看起来/打印出来都是浮雕而不是台阶
const HF_CACHE = {};

function heightField(id) {
  if (HF_CACHE[id]) return HF_CACHE[id];
  const m = patMask(id);
  const c = document.createElement('canvas');
  c.width = c.height = 512;
  const g = c.getContext('2d');
  if ('filter' in g) g.filter = 'blur(2.8px)';
  g.drawImage(m, 0, 0);
  g.filter = 'none';
  const px = g.getImageData(0, 0, 512, 512).data;
  const out = new Float32Array(512 * 512);
  for (let i = 0; i < out.length; i++) out[i] = px[i * 4 + 3] / 255;
  HF_CACHE[id] = out;
  return out;
}

/**
 * 把浮雕挤到外壁上：只动外壁顶点（环线索引 1..nOuter），沿半径方向外推。
 * dNorm 是「归一化模型单位」里的深度（模型高度 = 1）。
 */
function applyEmboss(geo, loop, nOuter, patId, dNorm, segs) {
  if (!(dNorm > 1e-6) || !patId || patId === 'none') return 0;
  let maxH = 0;
  const F = heightField(patId);
  const pos = geo.attributes.position;
  const nP = loop.length;

  const rows = new Float32Array(nP);
  for (let j = 1; j <= nOuter; j++) {
    const v = Math.max(0, Math.min(1, loop[j].y));
    rows[j] = (1 - v) * 511;
  }
  const cols = new Float32Array(segs);
  for (let k = 0; k < segs; k++) cols[k] = (k / segs) * 511;

  for (let i = 0; i <= segs; i++) {
    const col = cols[i % segs];
    const c0 = Math.min(511, Math.floor(col));
    const c1 = Math.min(511, c0 + 1);
    const dc = col - c0;
    for (let j = 1; j <= nOuter; j++) {
      const row = rows[j];
      const r0 = Math.min(511, Math.floor(row));
      const r1 = Math.min(511, r0 + 1);
      const dr = row - r0;
      const a = F[r0 * 512 + c0], b = F[r0 * 512 + c1];
      const cc = F[r1 * 512 + c0], dd = F[r1 * 512 + c1];
      const h = (a * (1 - dc) + b * dc) * (1 - dr) + (cc * (1 - dc) + dd * dc) * dr;
      if (h <= 0.002) continue;
      const vi = i * nP + j;
      const x = pos.getX(vi), z = pos.getZ(vi);
      const rr = Math.hypot(x, z);
      if (rr < 1e-9) continue;
      if (h > maxH) maxH = h;
      const f = (rr + h * dNorm) / rr;
      pos.setX(vi, x * f);
      pos.setZ(vi, z * f);
    }
  }
  pos.needsUpdate = true;
  return maxH;
}

// 材质细节在 UV 上平铺几次（花纹始终只绕一圈，避免纹样被打碎重复）
const TILE_N = { matte: 3, frosted: 3, leather: 4, wood: 1, metal: 1, gloss: 1 };

/**
 * 把"材质细节 × 主体色 + 花纹（花纹色）"烘焙成一张贴图。
 * material.color 保持白色，颜色全部落在贴图上，材质质感与花纹互不干扰。
 */
function buildAlbedo(key, bodyHex, patId, patHex, target) {
  const S = 512;
  let cv = target;
  if (!cv) {
    cv = document.createElement('canvas');
    cv.width = cv.height = S;
  }
  const g = cv.getContext('2d');
  g.setTransform(1, 0, 0, 1, 0, 0);
  g.globalCompositeOperation = 'source-over';
  g.clearRect(0, 0, S, S);
  g.fillStyle = '#ffffff';
  g.fillRect(0, 0, S, S);

  const set = TEX[key] || {};
  const n = TILE_N[key] || 1;
  if (set.map && set.map.image) {
    const t = S / n;
    for (let i = 0; i < n; i++) {
      for (let j = 0; j < n; j++) {
        try { g.drawImage(set.map.image, i * t, j * t, t, t); } catch (e) { }
      }
    }
  }
  g.globalCompositeOperation = 'multiply';
  g.fillStyle = bodyHex;
  g.fillRect(0, 0, S, S);
  g.globalCompositeOperation = 'source-over';

  if (patId && patId !== 'none') {
    const m = patMask(patId);
    if (patId === 'custom') {
      // 手绘：笔迹自带颜色，直接铺上去，不再统一染色
      g.drawImage(m, 0, 0);
    } else {
      const pc = document.createElement('canvas');
      pc.width = pc.height = S;
      const q = pc.getContext('2d');
      q.fillStyle = patHex;
      q.fillRect(0, 0, S, S);
      q.globalCompositeOperation = 'destination-in';
      q.drawImage(m, 0, 0);
      g.drawImage(pc, 0, 0);
    }
  }

  if (target) return cv;
  const tex = new THREE.CanvasTexture(cv);
  tex.colorSpace = THREE.SRGBColorSpace;
  tex.wrapS = tex.wrapT = THREE.RepeatWrapping;
  tex.anisotropy = 4;
  tex.needsUpdate = true;
  return tex;
}

/* ---------------- 光源 ---------------- */
// 四套打光：位置 / 强度
const LIGHTS = [
  { name: '工作室',  key: [-1.7, 2.5, 2.0],  fill: [2.3, 1.1, 1.3],  rim: [0.5, 1.5, -2.3], ki: 2.1, fi: 0.85, ri: 0.95, hi: 1.00 },
  { name: '侧逆光',  key: [-2.4, 1.6, -1.7], fill: [1.8, 0.9, 1.6],  rim: [0.2, 2.2, -1.0], ki: 2.5, fi: 0.55, ri: 1.70, hi: 0.80 },
  { name: '顶部光',  key: [0.1, 3.2, 0.7],   fill: [2.0, 0.6, 1.0],  rim: [-0.7, 1.2, -2.0], ki: 2.7, fi: 0.60, ri: 0.75, hi: 1.15 },
  { name: '柔光箱',  key: [-1.2, 2.0, 2.4],  fill: [2.0, 1.4, 1.6],  rim: [0.0, 1.2, -2.4], ki: 1.7, fi: 1.30, ri: 0.60, hi: 1.25 },
];

function applyLight(i) {
  if (!keyLight) return;
  const L = LIGHTS[i % LIGHTS.length];
  lightPreset = i % LIGHTS.length;
  keyLight.position.set(...L.key); keyLight.intensity = L.ki;
  fillLight.position.set(...L.fill); fillLight.intensity = L.fi;
  rimLight.position.set(...L.rim); rimLight.intensity = L.ri;
  hemi.intensity = L.hi;
  if (gizmo) {
    const v = new THREE.Vector3(...L.key).normalize().multiplyScalar(2.1);
    gizmo.position.copy(v);
    gizmoFade = 1.0;                    // 亮一下再淡出
  }
  return L.name;
}

/* ---------------- 建模型 ---------------- */
// 壁厚：产品高度归一化为 1，取 0.020 ≈ 300mm 高的器物壁厚 6mm
const WALL_DEFAULT = 0.011;   // 归一化壁厚，实际由后端按 mm/高度 给出

/**
 * 把 "外轮廓 (y,r)" 变成一条闭合的 (r,y) 环线：
 *   底面圆心 → 外底面 → 外壁向上 → 口沿 → 内壁向下 → 内底面 → 回到轴心
 * 这样旋转出来是有壁厚、开口朝上的实体壳，而不是一张无限薄的皮。
 */
function shellPoints(pts, t, closed) {
  const N = pts.length;
  const Q = pts.map(p => new THREE.Vector2(Math.max(p[1], 1e-4), p[0])); // (r, y)
  const inner = [];
  for (let i = 0; i < N; i++) {
    const a = Q[Math.max(0, i - 1)], b = Q[Math.min(N - 1, i + 1)];
    let tx = b.x - a.x, ty = b.y - a.y;
    const L = Math.hypot(tx, ty) || 1;
    tx /= L; ty /= L;
    // 外法线：把 (ty,-tx) 调成 r 分量为正的那一支
    let nx = ty, ny = -tx;
    if (nx < 0) { nx = -nx; ny = -ny; }
    let r = Q[i].x - t * nx;
    let y = Q[i].y - t * ny;
    inner.push(new THREE.Vector2(Math.max(r, t * 0.55),
                                 Math.min(Math.max(y, t * 0.55), 1)));
  }
  const out = [];
  out.push(new THREE.Vector2(0, 0));                 // 底面圆心
  out.push(Q[0]);                                    // 底面外沿
  for (let i = 1; i < N; i++) out.push(Q[i]);        // 外壁
  if (closed) {                                      // 封闭壳体：顶部封口
    out.push(new THREE.Vector2(0, 1));               //   外壁收到轴心
    out.push(new THREE.Vector2(0, inner[N - 1].y));  //   跳到内壁顶端轴心
  }
  out.push(inner[N - 1]);                            // 口沿（外→内）
  for (let i = N - 2; i >= 0; i--) out.push(inner[i]);// 内壁
  out.push(new THREE.Vector2(0, inner[0].y));        // 内底圆心
  return out;
}

let lastInfo = { segments: 96, rows: 0, tris: 0, emboss: 0, pattern: 'none' };

function rebuild() {
  if (!group) return;
  while (group.children.length) {
    const c = group.children.pop();
    if (c.geometry) c.geometry.dispose();
    if (c.material) c.material.dispose();
  }
  shadows.forEach(s => s.parent && s.parent.remove(s));
  shadows = [];

  const pts = state.points;
  if (!pts || pts.length < 3) return;

  const geoPts = shellPoints(pts, state.wall || WALL_DEFAULT, state.closed);
  // 浮雕需要分辨率：周向由 96 加密到 256，再沿半径外推
  const embMm = state.emboss || 0;
  const patId = state.pattern;
  const useEmb = embMm > 0.01 && patId && patId !== 'none';
  const segs = useEmb ? 256 : 96;
  const geo = new THREE.LatheGeometry(geoPts, segs);

  // 把 v 重写成"物理高度"：LatheGeometry 原本的 v 是按壳体点序排的
  // （外壳 / 口沿 / 内壁 / 底 各占一段），纹样按它定位会跑到口沿上去。
  {
    const pa = geo.attributes.position, uv = geo.attributes.uv;
    let lo = Infinity, hi = -Infinity;
    for (let k = 0; k < pa.count; k++) {
      const y = pa.getY(k);
      if (y < lo) lo = y;
      if (y > hi) hi = y;
    }
    const span = Math.max(1e-6, hi - lo);
    for (let k = 0; k < uv.count; k++) uv.setY(k, (pa.getY(k) - lo) / span);
    uv.needsUpdate = true;
  }

  let embApplied = 0;
  if (useEmb) {
    const hmm = state.heightMm || 300;
    const mh = applyEmboss(geo, geoPts, pts.length, patId, embMm / hmm, segs);
    if (mh > 0) {
      geo.computeVertexNormals();
      embApplied = mh * embMm;            // mh 是 0~1 的遮罩覆盖度，×深度 = 实测 mm
    }
  }

  const n = state.colors.length || 1;
  const mat = new THREE.MeshStandardMaterial({
    color: 0xffffff, envMapIntensity: 1.05, side: THREE.DoubleSide,
  });
  applyTex(mat, state.material);

  lastInfo = {
    segments: segs,
    rows: geoPts.length,
    tris: (geoPts.length - 1) * segs * 2,
    emboss: useEmb ? embMm : 0,
    embossApplied: Math.round(embApplied * 1000) / 1000,
    pattern: useEmb ? patId : 'none',
  };
  const shTex = shadowTexture();
  const patHex = (state.colors[state.patIdx] || state.colors[1] || '#ffffff');
  bodyMeshes = [];
  bodyTex = [];
  state.colors.forEach((hex, i) => {
    const m = mat.clone();
    const cv = document.createElement('canvas');
    cv.width = cv.height = 512;
    buildAlbedo(state.material, hex, patId, patHex, cv);
    const tx = new THREE.CanvasTexture(cv);
    tx.colorSpace = THREE.SRGBColorSpace;
    tx.wrapS = tx.wrapT = THREE.RepeatWrapping;
    tx.anisotropy = 4;
    m.map = tx;
    m.color = new THREE.Color(0xffffff);
    m.needsUpdate = true;
    const mesh = new THREE.Mesh(geo, m);
    mesh.position.x = (i - (n - 1) / 2) * SPACING;
    mesh.userData = { body: true, idx: i };
    group.add(mesh);
    bodyMeshes.push(mesh);
    bodyTex.push({ cv, tex: tx, hex });

    const sm = new THREE.Mesh(
      new THREE.PlaneGeometry(0.92, 0.92),
      new THREE.MeshBasicMaterial({
        map: shTex, transparent: true, depthWrite: false, opacity: 0.9,
      })
    );
    sm.rotation.x = -Math.PI / 2;
    sm.position.set(mesh.position.x, 0.002, 0);
    scene.add(sm);
    shadows.push(sm);
  });

  let maxR = 0;
  for (const p of geoPts) if (p.x > maxR) maxR = p.x;
  state.maxR = maxR;
  fitCamera(n);
}

let fitKey = '', fitAspect = 0;

/**
 * 取景。只有"排布/器型/画布比例"真的变了才重新落机位 ——
 * 否则用户转到的角度会被每一次 rebuild（每画一笔、每拖一次滑杆）掰回默认视角。
 */
function fitCamera(n, force) {
  if (!camera || !controls) return;
  const width = (n - 1) * SPACING + 0.62;
  const height = 1.10;
  const vFov = camera.fov * Math.PI / 180;
  const distV = (height / 2) / Math.tan(vFov / 2);
  const hFov = 2 * Math.atan(Math.tan(vFov / 2) * camera.aspect);
  const distH = (width / 2) / Math.tan(hFov / 2);
  const dist = Math.max(distV, distH) * 1.12;
  const y = 0.50;
  controls.maxDistance = Math.max(5.0, dist * 1.8);
  controls.minDistance = Math.min(1.2, dist * 0.45);

  const key = [n, state.points.length, Math.round((state.maxR || 0) * 200),
               state.closed ? 1 : 0].join('|');
  const aspectJump = fitAspect ? Math.abs(camera.aspect / fitAspect - 1) : 1;
  const farOut = camera.position.distanceTo(controls.target) > controls.maxDistance * 0.97;
  const need = !!force || fitKey !== key || aspectJump > 0.16 || farOut;
  if (!need) { controls.update(); return; }

  fitKey = key;
  fitAspect = camera.aspect;
  camera.position.set(0, y + dist * 0.30, dist * 0.95);
  controls.target.set(0, y, 0);
  controls.update();
}

/* ---------------- 对外接口 ---------------- */
export function update(data) {
  state.points = data.points || [];
  state.colors = data.colors || [];
  state.material = data.material || 'matte';
  state.wall = data.wall || WALL_DEFAULT;
  state.closed = !!data.closed;
  if (data.pattern !== undefined) state.pattern = data.pattern;
  if (data.patIdx !== undefined) state.patIdx = data.patIdx;
  if (data.emboss !== undefined) state.emboss = Number(data.emboss) || 0;
  if (data.height_mm) state.heightMm = Number(data.height_mm) || 300;
  rebuild();
}

export function setVisible(v) {
  visible = !!v;
  if (!canvas) return;
  canvas.style.display = visible ? 'block' : 'none';
  if (visible) { resize(); }
}

export function resize() {
  if (!renderer || !canvas) return;
  const box = canvas.parentElement.getBoundingClientRect();
  const w = Math.max(120, Math.round(box.width - 40));
  const h = Math.max(120, Math.round(box.height - 40));
  renderer.setSize(w, h, false);
  canvas.style.width = w + 'px';
  canvas.style.height = h + 'px';
  camera.aspect = w / h;
  camera.updateProjectionMatrix();
  fitCamera(state.colors.length || 1);
}

export function patterns() { return PATTERNS; }
export function info() {
  const d = (camera && controls) ? camera.position.distanceTo(controls.target) : 0;
  return Object.assign({}, lastInfo, {
    view: { locked: viewLocked, fitKey: fitKey, dist: Math.round(d * 1000) / 1000,
            paint: paintMode, meshes: bodyMeshes.length, brushing: painting },
    undo: undoStack.length,
  });
}
let onMaskChange = null;
export function bindMaskChange(fn) { onMaskChange = fn; }
export function setLight(i) { return applyLight(i); }
export function lightName() { return LIGHTS[lightPreset].name; }
export function ready() { return !!renderer; }

window.View3D = { init, update, setVisible, resize, ready, setLight, lightName, patterns,
                   patMask, buildAlbedo, info, setPaint, setBrush, clearMask,
                   maskDataURL, maskPixels, hasMask, bindMaskChange,
                   undo, redo, undoCount, redoCount, bindUndoChange,
                   setLock, locked, setBrushColor, brushState };
window.dispatchEvent(new Event('view3d-ready'));

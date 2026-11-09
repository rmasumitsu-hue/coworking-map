// 希望 — footprint kanji short. Runs inside Chromium (Canvas2D + WebGL).
// window.initScene(data) once, then window.renderFrame(t) -> dataURL.
'use strict';
(() => {
const W = 1080, H = 1920, TAU = Math.PI * 2, DEG = Math.PI / 180;
const clamp = (x, a, b) => x < a ? a : x > b ? b : x;
const lerp = (a, b, t) => a + (b - a) * t;
const smooth = (e0, e1, x) => { const t = clamp((x - e0) / (e1 - e0), 0, 1); return t * t * (3 - 2 * t); };
const easeIO = x => x < .5 ? 4 * x * x * x : 1 - Math.pow(-2 * x + 2, 3) / 2;
function rng(seed) { let a = seed >>> 0; return () => { a = a + 0x6D2B79F5 | 0; let t = Math.imul(a ^ a >>> 15, 1 | a); t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t; return ((t ^ t >>> 14) >>> 0) / 4294967296; }; }
const wrapA = a => { while (a > Math.PI) a -= TAU; while (a < -Math.PI) a += TAU; return a; };

// ---------------------------------------------------------------- config
const C = {
  unit: 0.165,          // metres per KanjiVG unit (109 units per glyph box)
  charGap: 0.6,         // metres between glyph boxes
  laneGap: 0.44,        // spacing of walkers side by side (brush width)
  footLat: 0.10,       // half distance between left/right feet
  stride: 0.60,         // unhurried, deliberate steps         // one step
  footLen: 0.44,      // slightly exaggerated so the ink reads as a brush stroke
  vStroke: 1.15, vTransit: 2.0, vRing: 1.2,
  tLeader: 0.45, tFinish: 21.9, tStop: 22.3,
  ringMargin: 2.6,
  pitch: 45 * DEG, F: 1000, horizonY: 384, camH: 10.5,
  textTop: 830, textBot: 1830, // screen band the text occupies in the main shot
};
C.cy = C.horizonY + C.F * Math.tan(C.pitch);
const cp = Math.cos(C.pitch), sp = Math.sin(C.pitch);

// ---------------------------------------------------------------- camera
const cam = { x: 0, y: 10, z: -10 };
function proj(x, y, z) {
  const dx = x - cam.x, dy = y - cam.y, dz = z - cam.z;
  const yc = dy * cp + dz * sp, zc = -dy * sp + dz * cp;
  return [W / 2 + C.F * dx / zc, C.cy - C.F * yc / zc, zc];
}
const projA = P => proj(P[0], P[1], P[2]);
function projDir(dx, dy, dz) {
  const yc = dy * cp + dz * sp, zc = -dy * sp + dz * cp;
  return [W / 2 + C.F * dx / zc, C.cy - C.F * yc / zc, zc];
}

// ---------------------------------------------------------------- paths
class Path {
  constructor(pts) {
    this.p = pts; const c = [0];
    for (let i = 1; i < pts.length; i++) c.push(c[i - 1] + Math.hypot(pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1]));
    this.c = c; this.L = c[c.length - 1];
  }
  at(s) {
    const p = this.p, c = this.c, n = p.length;
    if (s <= 0) { const d = this.d0 || (this.d0 = this.dirRaw(0)); return [p[0][0] + d[0] * s, p[0][1] + d[1] * s]; }
    if (s >= this.L) { const d = this.d1 || (this.d1 = this.dirRaw(1)); const e = s - this.L; return [p[n - 1][0] + d[0] * e, p[n - 1][1] + d[1] * e]; }
    let lo = 0, hi = n - 1; while (hi - lo > 1) { const m = (lo + hi) >> 1; if (c[m] <= s) lo = m; else hi = m; }
    const f = (s - c[lo]) / ((c[hi] - c[lo]) || 1);
    return [lerp(p[lo][0], p[hi][0], f), lerp(p[lo][1], p[hi][1], f)];
  }
  dirRaw(end) {
    const p = this.p, n = p.length; let a, b;
    if (end === 0) { a = p[0]; b = p[Math.min(n - 1, 3)]; } else { a = p[Math.max(0, n - 4)]; b = p[n - 1]; }
    const l = Math.hypot(b[0] - a[0], b[1] - a[1]) || 1; return [(b[0] - a[0]) / l, (b[1] - a[1]) / l];
  }
  dir(s, w = 0.3) { const a = this.at(s - w), b = this.at(s + w); const l = Math.hypot(b[0] - a[0], b[1] - a[1]) || 1; return [(b[0] - a[0]) / l, (b[1] - a[1]) / l]; }
}

// ---------------------------------------------------------------- globals
let D, R, strokes, walkers, prints, scen, washes, sprites, blobSprites, ring, bounds, camMain, camTarget, maskPts;
let gl, glc, glProg, glU, out, octx, shadowC, sctx, tmpC, tctx;

// ---------------------------------------------------------------- init
window.initScene = function (data) {
  D = data; R = rng(data.seed || 7);
  out = document.createElement('canvas'); out.width = W; out.height = H; octx = out.getContext('2d');
  shadowC = document.createElement('canvas'); shadowC.width = W; shadowC.height = H; sctx = shadowC.getContext('2d');
  tmpC = document.createElement('canvas'); tmpC.width = W; tmpC.height = H; tctx = tmpC.getContext('2d');
  buildStrokes(); buildRing(); buildCamera(); buildWalkers(); buildPrints(); buildScenery();
  buildSprites(); initGL();
  return { strokes: strokes.length, walkers: walkers.length, prints: prints.length, scenery: scen.length, cam: camMain };
};

function buildStrokes() {
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg'); document.body.appendChild(svg);
  strokes = []; const n = D.chars.length;
  // Perspective-corrected lettering (like road markings): each glyph gets a screen band in the
  // main shot; its world size is derived from that band, so the far glyph is physically larger.
  const zOfY = y => { const d = C.pitch + Math.atan((y - C.cy) / C.F); return C.camH / Math.tan(d); };
  const wts = Array.from({ length: n }, (_, i) => Math.pow(1.6, i)); const wsum = wts.reduce((a, b) => a + b, 0);
  const gapPx = 18; let y = C.textTop; const bands = [];
  for (let i = 0; i < n; i++) { const h = (C.textBot - C.textTop - gapPx * (n - 1)) * wts[i] / wsum; bands.push([y, y + h]); y += h + gapPx; }
  D.glyph = bands.map(([yt, yb], i) => {
    const zt = zOfY(yt), zb = zOfY(yb), ym = (yt + yb) / 2, zm = zOfY(ym);
    const zc = C.camH * sp + zm * cp; const wpx = n === 1 ? 880 : lerp(790, 880, i / (n - 1));
    return { zt, zb, kx: wpx * zc / C.F / 95, kz: (zt - zb) / 95 };
  });
  const baseK = D.glyph[n - 1].kx;
  D.chars.forEach((ch, ci) => {
    const G = D.glyph[ci], zmid = (G.zt + G.zb) / 2;
    ch.strokes.forEach((st, si) => {
      const pe = document.createElementNS('http://www.w3.org/2000/svg', 'path'); pe.setAttribute('d', st.d); svg.appendChild(pe);
      const L = pe.getTotalLength(), k = Math.max(8, Math.ceil(L / 0.6)); const pts = [];
      for (let i = 0; i <= k; i++) { const q = pe.getPointAtLength(L * i / k); pts.push([(q.x - 54.5) * G.kx, zmid - (q.y - 54.5) * G.kz]); }
      strokes.push({ ci, si, type: st.type || '', path: new Path(pts), pts, k: G.kx / baseK, unitLen: L });
    });
  });
  // bounds + mask sample points
  let x0 = 1e9, x1 = -1e9, z0 = 1e9, z1 = -1e9; maskPts = [];
  for (const s of strokes) for (let i = 0; i < s.pts.length; i++) { const p = s.pts[i]; x0 = Math.min(x0, p[0]); x1 = Math.max(x1, p[0]); z0 = Math.min(z0, p[1]); z1 = Math.max(z1, p[1]); if (i % 2 === 0) maskPts.push(p); }
  bounds = { x0, x1, z0, z1 };
  // lanes
  for (const s of strokes) {
    const P = s.path, nL = s.unitLen > 42 ? 3 : 2, gap = C.laneGap * Math.pow(s.k, 0.85), offs = nL === 3 ? [-1, 0, 1] : [-.5, .5];
    const sweep = /[㇒㇏㇓㇀㇔]/.test(s.type);
    s.nL = nL; s.lanes = [];
    const nrm = P.p.map((_, i) => { const d = P.dir(P.c[i], 1.1); return [d[1], -d[0]]; });
    for (const o of offs) {
      const lp = P.p.map((p, i) => {
        const u = P.c[i] / P.L; let g = 1;
        if (sweep) g *= 1 - 0.7 * smooth(0.5, 1, u);
        g *= 0.8 + 0.2 * smooth(0, 0.12, u);
        const off = o * gap * g; return [p[0] + nrm[i][0] * off, p[1] + nrm[i][1] * off];
      });
      s.lanes.push({ off: o, pts: lp, path: new Path(lp) });
    }
    let cx = 0, cz = 0; for (const p of P.p) { cx += p[0]; cz += p[1]; } s.centroid = [cx / P.p.length, cz / P.p.length];
  }
}

function inText(x, z, m) { return x > bounds.x0 - m && x < bounds.x1 + m && z > bounds.z0 - m && z < bounds.z1 + m; }
function maskDist(x, z) { let m = 1e9; for (const p of maskPts) { const d = (p[0] - x) ** 2 + (p[1] - z) ** 2; if (d < m) m = d; } return Math.sqrt(m); }

function buildRing() {
  const m = C.ringMargin, x0 = bounds.x0 - m, x1 = bounds.x1 + m, z0 = bounds.z0 - m, z1 = bounds.z1 + m, r = 3.2;
  const pts = []; const corners = [[x1 - r, z1 - r, 0], [x0 + r, z1 - r, 90], [x0 + r, z0 + r, 180], [x1 - r, z0 + r, 270]];
  for (const [cx, cz, a0] of corners) for (let a = 0; a <= 90; a += 6) { const A = (a0 + a) * DEG; pts.push([cx + r * Math.cos(A), cz + r * Math.sin(A)]); }
  const loop = []; // resample densely, 3 laps for long walks
  for (let lap = 0; lap < 3; lap++) for (let i = 0; i < pts.length; i++) { const a = pts[i], b = pts[(i + 1) % pts.length]; const L = Math.hypot(b[0] - a[0], b[1] - a[1]); const k = Math.max(1, Math.ceil(L / 0.3)); for (let j = 0; j < k; j++) loop.push([lerp(a[0], b[0], j / k), lerp(a[1], b[1], j / k)]); }
  ring = new Path(loop); ring.lap = ring.L / 3;
}

function buildCamera() {
  camMain = { x: 0, y: C.camH, z: 0 };
  camTarget = [0, 0, (bounds.z0 + bounds.z1) / 2];
}
function setCamera(t) {
  // opening: close on the first walker, easing out to the main shot; 26s+: pull back
  const e = easeIO(clamp((t - 0.6) / 8.0, 0, 1));
  const f0 = strokes[0].path.p[0];
  const fx = lerp(f0[0] * 0.6, camTarget[0], e), fz = lerp(f0[1], camTarget[2], e);
  const pull = easeIO(clamp((t - 26) / 3.6, 0, 1));
  const k = lerp(0.42, 1, e) + 0.6 * pull;
  cam.x = fx + (camMain.x - camTarget[0]) * k;
  cam.y = (camMain.y - camTarget[1]) * k;
  cam.z = fz + (camMain.z - camTarget[2]) * k;
}

// ---------------------------------------------------------------- walkers
function makeTimeTable(knots, pad, tEnd) {
  // knots: [[t,s],...] increasing. returns smoothed table sampled at 1/120 s
  const dt = 1 / 120, t0 = knots[0][0] - 1, t1 = Math.max(tEnd, knots[knots.length - 1][0]) + 1.5;
  const n = Math.ceil((t1 - t0) / dt) + 1, raw = new Float64Array(n);
  const v0 = knots.length > 1 ? (knots[1][1] - knots[0][1]) / Math.max(1e-3, knots[1][0] - knots[0][0]) : 0;
  let k = 0;
  for (let i = 0; i < n; i++) {
    const t = t0 + i * dt;
    if (t <= knots[0][0]) { raw[i] = pad === 'hold' ? knots[0][1] : knots[0][1] + v0 * (t - knots[0][0]); continue; }
    while (k < knots.length - 2 && knots[k + 1][0] < t) k++;
    const a = knots[k], b = knots[Math.min(k + 1, knots.length - 1)];
    raw[i] = t >= b[0] ? b[1] : lerp(a[1], b[1], (t - a[0]) / (b[0] - a[0]));
  }
  let s = raw; for (const win of [0.32, 0.32, 0.2]) { const h = Math.round(win / dt / 2), o = new Float64Array(n); let acc = 0, cnt = 0;
    // box filter with clamped edges
    const pre = new Float64Array(n + 1); for (let i = 0; i < n; i++) pre[i + 1] = pre[i] + s[i];
    for (let i = 0; i < n; i++) { const a = Math.max(0, i - h), b = Math.min(n - 1, i + h); o[i] = (pre[b + 1] - pre[a]) / (b - a + 1); }
    s = o; }
  // ensure exact final value
  const fin = knots[knots.length - 1][1]; for (let i = 0; i < n; i++) if (s[i] > fin) s[i] = fin;
  return { t0, dt, s, n };
}

class Walker {
  constructor(o) { Object.assign(this, o); }
  sAt(t) { const T = this.tab; const f = (t - T.t0) / T.dt; if (f <= 0) return T.s[0]; if (f >= T.n - 1) return T.s[T.n - 1]; const i = f | 0; return lerp(T.s[i], T.s[i + 1], f - i); }
  tAt(s) { const T = this.tab; let lo = 0, hi = T.n - 1; if (T.s[hi] < s - 1e-4) return Infinity; while (hi - lo > 1) { const m = (lo + hi) >> 1; if (T.s[m] < s - 1e-4) lo = m; else hi = m; } return T.t0 + hi * T.dt; }
  buildSteps() {
    const st = C.stride, s = [], sEnd = this.sEnd;
    const N = Math.round((sEnd - this.sigma - 0.5 * st) / st);
    const kmin = Math.floor(-this.sigma / st) - 4;
    for (let k = kmin; k <= N; k++) {
      const land = this.sigma + k * st, at = land + 0.5 * st, side = (k % 2 + 2) % 2; // 0 left, 1 right
      s.push({ k, land, at, side });
    }
    const last = s[s.length - 1]; s.push({ k: N + 1, land: sEnd, at: sEnd, side: 1 - last.side, closing: true });
    for (const q of s) {
      const P = this.path.at(q.at), d = this.path.dir(q.at, 0.25), r = [d[1], -d[0]], lat = (q.side === 0 ? -1 : 1) * C.footLat;
      q.x = P[0] + r[0] * lat; q.z = P[1] + r[1] * lat; q.ang = Math.atan2(d[1], d[0]);
    }
    for (let i = 0; i < s.length; i++) s[i].lift = i + 1 < s.length ? s[i + 1].land + 0.1 * st : Infinity;
    this.steps = s;
  }
  foot(side, sv) {
    const S = this.steps; let j = -1;
    // binary search last step with land <= sv
    let lo = 0, hi = S.length - 1; if (S[0].land > sv) j = -1; else { while (hi - lo > 1) { const m = (lo + hi) >> 1; if (S[m].land <= sv) lo = m; else hi = m; } j = S[hi].land <= sv ? hi : lo; }
    let i = j; while (i >= 0 && S[i].side !== side) i--;
    if (i < 0) { i = S[0].side === side ? 0 : 1; return { x: S[i].x, z: S[i].z, h: 0, ang: S[i].ang, i }; }
    const q = S[i];
    if (sv < q.lift || i + 2 >= S.length) return { x: q.x, z: q.z, h: 0, ang: q.ang, i };
    const n = S[i + 2], u = clamp((sv - q.lift) / Math.max(1e-3, n.land - q.lift), 0, 1), e = u * u * (3 - 2 * u);
    return { x: lerp(q.x, n.x, e), z: lerp(q.z, n.z, e), h: 0.13 * Math.sin(Math.PI * u), ang: q.ang + wrapA(n.ang - q.ang) * e, i, swing: u };
  }
}

function buildWalkers() {
  walkers = []; const M = strokes.length;
  const dur = strokes.map(s => s.path.L / C.vStroke);
  const S = new Array(M).fill(0);
  S[0] = C.tLeader + 1.0 / C.vStroke + 0.45;
  const delta = Math.max(0.2, (C.tFinish - S[0] - dur[M - 1]) / Math.max(1, M - 1));

  const alive = (t) => walkers.filter(w => t >= w.tSpawn + 0.35 && t < w.tStopS - 0.5);
  const posAt = (w, t) => w.path.at(w.sAt(t));

  for (let m = 0; m < M; m++) {
    const st = strokes[m]; const lanes = st.lanes; const t0dir = st.path.dir(0, 0.6);
    let Sm, v;
    const group = [];
    if (m === 0) {
      Sm = S[0]; v = C.vStroke;
      const lead = Math.floor(lanes.length / 2);
      const order = [lead, ...lanes.map((_, j) => j).filter(j => j !== lead)];
      for (const j of order) {
        const ln = lanes[j], lp = ln.path, pre = [lp.p[0][0] - t0dir[0] * 1.0, lp.p[0][1] - t0dir[1] * 1.0];
        let pts, knots, tSpawn, pad, sA;
        if (j === lead) {
          pts = [pre, ...lp.p]; sA = 1.0; tSpawn = -1; pad = 'hold';
          knots = [[C.tLeader, 0], [Sm, sA]];
          lp.c.forEach((c, i) => { if (i > 0) knots.push([Sm + st.path.c[i] / v, sA + c]); });
        } else {
          const leader = group[0];
          tSpawn = 1.0 + (j < lead ? 0 : 0.35);
          const sp0 = posAt(leader, tSpawn);
          const tJoin = tSpawn + 1.5, uJ = (tJoin - Sm) * v;
          let iJ = st.path.c.findIndex(c => c >= uJ); if (iJ < 1) iJ = 1;
          pts = [sp0, ...lp.p.slice(iJ)]; sA = 0; pad = 'extrap';
          const d0 = Math.hypot(lp.p[iJ][0] - sp0[0], lp.p[iJ][1] - sp0[1]);
          knots = [[tSpawn, 0], [Sm + st.path.c[iJ] / v, d0]];
          for (let i = iJ + 1; i < lp.p.length; i++) knots.push([Sm + st.path.c[i] / v, d0 + (lp.c[i] - lp.c[iJ])]);
        }
        const w = new Walker({ stroke: m, lane: j, pts, knots, tSpawn, pad, sA, lead: j === lead });
        finishWalker(w, st, Sm, v); group.push(w);
      }
      walkers.push(...group);
      continue;
    }
    // later groups: branch from nearest live walker
    Sm = Math.max(S[0] + m * delta, S[m - 1] + 0.15);
    const preMean = [0, 0]; const pres = lanes.map(ln => { const p = [ln.path.p[0][0] - t0dir[0] * 1.0, ln.path.p[0][1] - t0dir[1] * 1.0]; preMean[0] += p[0] / lanes.length; preMean[1] += p[1] / lanes.length; return p; });
    const tMin = 0.9 + 0.32 * m;
    let tsp = Sm - 3, src = null, dist = 0;
    for (let it = 0; it < 4; it++) {
      const cand = alive(Math.max(tsp, tMin)); src = null; let best = 1e9;
      for (const w of cand) { const p = posAt(w, Math.max(tsp, tMin)); const d = Math.hypot(p[0] - preMean[0], p[1] - preMean[1]); if (d < best) { best = d; src = w; } }
      dist = best + 1.0; tsp = Sm - dist / C.vTransit - 0.2;
    }
    if (tsp < tMin) { tsp = tMin; const p = posAt(src, tsp); dist = Math.hypot(p[0] - preMean[0], p[1] - preMean[1]) + 1.0; Sm = Math.max(Sm, tsp + dist / C.vTransit + 0.2); }
    S[m] = Sm;
    const avail = (m === M - 1 ? C.tFinish : C.tFinish - 0.35) - Sm;
    v = m === M - 1 ? clamp(st.path.L / avail, 0.9, 2.3) : clamp(Math.max(C.vStroke, st.path.L / avail), 0.9, 2.3);
    lanes.forEach((ln, j) => {
      const tS = tsp + j * 0.12; const p0 = posAt(src, tS); const lp = ln.path, pre = pres[j];
      const d1 = Math.hypot(pre[0] - p0[0], pre[1] - p0[1]), d2 = Math.hypot(lp.p[0][0] - pre[0], lp.p[0][1] - pre[1]);
      const pts = [p0, pre, ...lp.p]; const sA = d1 + d2;
      const knots = [[tS, 0], [Sm, sA]];
      lp.c.forEach((c, i) => { if (i > 0) knots.push([Sm + st.path.c[i] / v, sA + c]); });
      const w = new Walker({ stroke: m, lane: j, pts, knots, tSpawn: tS, pad: 'extrap', sA, src: walkers.indexOf(src) });
      finishWalker(w, st, Sm, v); walkers.push(w);
    });
  }
}

function finishWalker(w, st, Sm, v) {
  // append exit / stop
  const lastK = w.knots[w.knots.length - 1]; const tE = lastK[0], sB = lastK[1];
  let pts = w.pts.slice();
  const late = tE > C.tFinish - 1.4;
  w.sB = sB;
  let sEndRaw, tStop;
  if (!late) {
    const tmp = new Path(pts); const e = tmp.at(sB), d = tmp.dir(sB, 0.4);
    const p1 = [e[0] + d[0] * 1.2, e[1] + d[1] * 1.2];
    // nearest ring point to a point further out
    const probe = [e[0] + d[0] * 3, e[1] + d[1] * 3]; let bi = 0, bd = 1e9;
    for (let i = 0; i < ring.p.length; i++) { if (ring.c[i] > ring.lap) break; const q = ring.p[i]; const dd = (q[0] - probe[0]) ** 2 + (q[1] - probe[1]) ** 2; if (dd < bd) { bd = dd; bi = i; } }
    const rt = ring.dir(ring.c[bi], 0.5); let dirS = Math.sign(rt[0] * d[0] + rt[1] * d[1]) || (R() < .5 ? 1 : -1);
    let r0 = ring.c[bi] + ring.lap + dirS * (1.5 + R() * 1.5);
    pts.push(p1);
    const need = C.vRing * (C.tStop + 1.5 - tE) + 6;
    const ro = R() * 2.6 - 0.4;
    for (let s = 0; s < need; s += 0.3) { const q = ring.at(r0 + dirS * s), tg = ring.dir(r0 + dirS * s, 0.6); pts.push([q[0] + tg[1] * ro, q[1] - tg[0] * ro]); }
    tStop = C.tStop + R() * 0.7;
    sEndRaw = sB + C.vRing * (tStop - tE);
  } else { sEndRaw = sB; tStop = tE; }
  w.path = new Path(pts);
  if (!late) { // recompute actual path distance to keep speed constant: knots in path arc length
    w.knots.push([tStop, sEndRaw]);
  }
  // step phase so a print lands at stroke start
  const st_ = C.stride; let sig = ((w.sA + 0.04 - 0.5 * st_) % st_ + st_) % st_ - st_; w.sigma = sig;
  const N = Math.floor((sEndRaw - sig - 0.5 * st_) / st_ + 1e-6); w.sEnd = sig + (N + 0.5) * st_;
  // retime last knot to the snapped end
  const kn = w.knots; const kl = kn[kn.length - 1];
  if (!late) { kl[1] = w.sEnd; kl[0] = tE + (w.sEnd - sB) / C.vRing; }
  else { // stop slightly before/after lane end
    while (kn.length > 2 && kn[kn.length - 2][1] >= w.sEnd) kn.pop();
    const a = kn[kn.length - 2], b = kn[kn.length - 1];
    const tt = a[0] + (w.sEnd - a[1]) / ((b[1] - a[1]) / (b[0] - a[0]) || 1); kn[kn.length - 1] = [tt, w.sEnd];
  }
  w.printA = w.sA - 0.05; w.printB = sB + 0.25;
  w.tab = makeTimeTable(w.knots, w.pad, 30);
  w.tStopS = w.knots[w.knots.length - 1][0] + 0.3;
  w.buildSteps();
  // facing after stop: look back at own stroke
  const fin = w.path.at(w.sEnd); const tc = st.centroid;
  let ta = Math.atan2(tc[1] - fin[1], tc[0] - fin[0]);
  const hd = w.path.dir(w.sEnd, 0.5), ha = Math.atan2(hd[1], hd[0]);
  if (late) ta = ha + Math.PI + (R() - .5) * 0.6; // turn around to look along the finished stroke
  w.faceAng = ta; w.walkAng = ha;
  w.tTurn = Math.max(w.tStopS, C.tStop + 0.1) + 0.2 + R() * 0.6;
}

function buildPrints() {
  prints = []; scenSeeds = [];
  for (const w of walkers) {
    for (const q of w.steps) {
      if (q.land < 0) continue;
      const t = w.tAt(q.land); if (!isFinite(t) || t < w.tSpawn) continue;
      q.t = t;
      if (q.at >= w.printA && q.at <= w.printB) {
        prints.push({ x: q.x + (R() - .5) * 0.015, z: q.z + (R() - .5) * 0.015, ang: q.ang + (R() - .5) * 0.07, side: q.side, t, v: (R() * 3) | 0, sc: (0.95 + R() * 0.1) * Math.pow(strokes[w.stroke].k, 0.75) });
      } else if (t < 23.2) scenSeeds.push({ x: q.x, z: q.z, t, w });
    }
  }
  prints.sort((a, b) => a.t - b.t);
}
let scenSeeds = [];
function buildScenery() {
  scen = []; washes = [];
  for (const sd of scenSeeds) {
    if (inText(sd.x, sd.z, 0.8) || maskDist(sd.x, sd.z) < 2.1) continue;
    if (R() < 0.45) washes.push({ x: sd.x + (R() - .5) * .4, z: sd.z + (R() - .5) * .4, t: sd.t, r: 0.9 + R() * 0.8, kind: R() < 0.55 ? 0 : 1 });
    const n = 4 + (R() * 4 | 0);
    for (let i = 0; i < n; i++) {
      const delay = Math.pow(R(), 1.3) * 2.6; const a = R() * TAU, r = 0.12 + R() * 0.35 + delay * 0.6;
      const x = sd.x + Math.cos(a) * r, z = sd.z + Math.sin(a) * r;
      if (inText(x, z, 0.5) || maskDist(x, z) < 1.9) continue;
      const kind = R() < 0.58 ? 0 : 1; // 0 petal, 1 sprout
      scen.push({ x, z, t: sd.t + delay, kind, rot: R() * TAU, sc: 0.75 + R() * 0.6, hue: R() });
    }
  }
  scen.sort((a, b) => b.z - a.z);
}

// ---------------------------------------------------------------- sprites
function footShape(ctx, side, jit, Lpx, cx, cy, grow = 1) {
  const pts = [[0.0, 0.48], [0.105, 0.41], [0.12, 0.13], [0.16, -0.15], [0.145, -0.27], [0.045, -0.315], [-0.09, -0.305], [-0.165, -0.21], [-0.14, -0.05], [-0.065, 0.12], [-0.095, 0.32], [-0.045, 0.47]];
  const mir = side === 0 ? -1 : 1; const P = pts.map((p, i) => [cx + mir * (p[0] + jit[i % jit.length][0]) * Lpx * grow, cy + (p[1] + jit[i % jit.length][1]) * Lpx * grow]);
  ctx.beginPath(); const n = P.length;
  for (let i = 0; i < n; i++) { const p0 = P[(i - 1 + n) % n], p1 = P[i], p2 = P[(i + 1) % n], p3 = P[(i + 2) % n];
    if (i === 0) ctx.moveTo(p1[0], p1[1]);
    ctx.bezierCurveTo(p1[0] + (p2[0] - p0[0]) / 6, p1[1] + (p2[1] - p0[1]) / 6, p2[0] - (p3[0] - p1[0]) / 6, p2[1] - (p3[1] - p1[1]) / 6, p2[0], p2[1]); }
  ctx.closePath();
  const toes = [[-0.105, -0.395, 0.062], [-0.012, -0.415, 0.04], [0.052, -0.395, 0.034], [0.105, -0.365, 0.031], [0.15, -0.318, 0.027]];
  for (const t of toes) { ctx.moveTo(cx + mir * t[0] * Lpx * grow + t[2] * Lpx * grow, cy + t[1] * Lpx * grow); ctx.arc(cx + mir * t[0] * Lpx * grow, cy + t[1] * Lpx * grow, t[2] * Lpx * grow, 0, TAU); }
}
function buildSprites() {
  const Lpx = 200, pad = 30, cw = Math.ceil(0.36 * Lpx + 2 * pad), ch = Math.ceil(1.0 * Lpx + 2 * pad);
  const NST = 7; sprites = [];
  const r2 = rng(99);
  for (let side = 0; side < 2; side++) for (let v = 0; v < 3; v++) {
    const jit = Array.from({ length: 12 }, () => [(r2() - .5) * 0.022, (r2() - .5) * 0.02]);
    // textured core
    const core = document.createElement('canvas'); core.width = cw; core.height = ch; const c = core.getContext('2d');
    c.fillStyle = '#000'; footShape(c, side, jit, Lpx, cw / 2, ch / 2); c.fill();
    c.globalCompositeOperation = 'destination-out';
    for (let i = 0; i < 260; i++) { c.globalAlpha = 0.12 + r2() * 0.35; c.beginPath(); c.ellipse(r2() * cw, r2() * ch, 1 + r2() * 4, 0.6 + r2() * 2, r2() * TAU, 0, TAU); c.fill(); }
    // drag streaks (dry brush feel)
    for (let i = 0; i < 18; i++) { c.globalAlpha = 0.08 + r2() * 0.15; c.fillRect(r2() * cw, pad + r2() * Lpx, 1 + r2() * 2, 10 + r2() * 40); }
    c.globalCompositeOperation = 'source-over'; c.globalAlpha = 1;
    const stages = [];
    for (let s = 0; s < NST; s++) {
      const b = s / (NST - 1);
      const cv = document.createElement('canvas'); cv.width = cw; cv.height = ch; const x = cv.getContext('2d');
      // bleed halo: ink wicking into paper fibres
      x.filter = `blur(${1.5 + 7 * b}px)`; x.globalAlpha = 0.18 + 0.32 * b; x.fillStyle = '#000';
      footShape(x, side, jit, Lpx, cw / 2, ch / 2, 1 + 0.045 * b); x.fill();
      x.filter = `blur(${0.6 + 1.6 * b}px)`; x.globalAlpha = 0.7 + 0.3 * b; x.drawImage(core, 0, 0);
      x.filter = 'none'; x.globalAlpha = 0.25 * b; x.drawImage(core, 0, 0); // pooled darker centre
      stages.push(mips(cv));
    }
    sprites.push(stages);
  }
  sprites.meta = { Lpx, cw, ch };
  // soft colour wash blobs
  blobSprites = ['rgba(246,196,206,', 'rgba(196,226,170,'].map(col => { const cv = document.createElement('canvas'); cv.width = cv.height = 128; const x = cv.getContext('2d'); const g = x.createRadialGradient(64, 64, 0, 64, 64, 64); g.addColorStop(0, col + '1)'); g.addColorStop(0.5, col + '0.5)'); g.addColorStop(1, col + '0)'); x.fillStyle = g; x.fillRect(0, 0, 128, 128); return cv; });
}
function mips(cv) { const L = [cv]; let c = cv; for (let i = 0; i < 4; i++) { const n = document.createElement('canvas'); n.width = Math.max(2, c.width >> 1); n.height = Math.max(2, c.height >> 1); const x = n.getContext('2d'); x.imageSmoothingQuality = 'high'; x.drawImage(c, 0, 0, n.width, n.height); L.push(n); c = n; } return L; }

// ---------------------------------------------------------------- light / sun
const KT = [0, 6, 15, 21, 25.5, 28, 30];
const K = {
  light: [[1.0, .93, .78], [1.0, .985, .955], [1.0, 1.0, .995], [1.0, .95, .86], [1.0, .84, .74], [.99, .76, .67], [.99, .75, .66]],
  hor: [[1.0, .86, .64], [.94, .95, .96], [.93, .96, .99], [1.0, .9, .76], [1.0, .62, .44], [.97, .48, .36], [.95, .46, .36]],
  top: [[.84, .86, .9], [.74, .84, .95], [.7, .82, .96], [.72, .78, .9], [.6, .5, .64], [.44, .36, .54], [.42, .34, .52]],
  sun: [[1.0, .86, .6], [1.0, .97, .9], [1.0, 1.0, .97], [1.0, .9, .7], [1.0, .55, .3], [1.0, .42, .25], [1.0, .4, .25]],
  shadow: [[.62, .64, .78], [.62, .66, .78], [.6, .64, .74], [.6, .58, .7], [.55, .42, .55], [.5, .38, .52], [.5, .38, .52]],
};
function keyed(arr, t) { let i = 0; while (i < KT.length - 2 && t > KT[i + 1]) i++; const u = smooth(KT[i], KT[i + 1], t); return arr[i].map((v, j) => lerp(v, arr[i + 1][j], u)); }
function env(t) {
  const tR = -0.6, tS = 29.1, tau = (t - tR) / (tS - tR);
  const sn = Math.sin(Math.PI * clamp(tau, 0, 1));
  const el = (tau <= 0 || tau >= 1 ? -2 : 66 * Math.pow(sn, 2.6)) * DEG;
  const azDisc = (-19 + 38 * tau) * DEG, azSh = (-62 + 124 * tau) * DEG;
  const elSh = Math.max(el, 21 * DEG);
  const L = [Math.cos(elSh) * Math.sin(azSh), Math.sin(elSh), Math.cos(elSh) * Math.cos(azSh)];
  const disc = [Math.cos(el) * Math.sin(azDisc), Math.sin(el), Math.cos(el) * Math.cos(azDisc)];
  const strength = 0.3 * smooth(-1 * DEG, 3.5 * DEG, el);
  const fade = Math.max(1 - smooth(0.0, 0.9, t), smooth(29.05, 29.97, t));
  return { light: keyed(K.light, t), hor: keyed(K.hor, t), top: keyed(K.top, t), sunCol: keyed(K.sun, t), shadow: keyed(K.shadow, t), L, disc, el, strength, fade, exposure: 1 + 0.03 * smooth(4, 12, t) * (1 - smooth(18, 24, t)) };
}

// ---------------------------------------------------------------- WebGL floor + sky
const FS = `precision highp float;
uniform vec2 res; uniform vec3 camPos; uniform float pitch, F, cy, hy;
uniform vec3 light, skyTop, skyHor, sunDisc, sunCol, sunL; uniform float exposure, sunVis; uniform vec3 sunPx;
float hash(vec2 p){ p = fract(p*vec2(443.897, 441.423)); p += dot(p, p.yx+19.19); return fract((p.x+p.y)*p.x); }
float vn(vec2 p){ vec2 i=floor(p), f=fract(p); vec2 u=f*f*(3.-2.*f);
  return mix(mix(hash(i),hash(i+vec2(1.,0.)),u.x), mix(hash(i+vec2(0.,1.)),hash(i+vec2(1.,1.)),u.x), u.y); }
void main(){
  vec2 fc = vec2(gl_FragCoord.x, res.y - gl_FragCoord.y);
  vec3 R = vec3(1.,0.,0.), U = vec3(0.,cos(pitch),sin(pitch)), Fw = vec3(0.,-sin(pitch),cos(pitch));
  vec3 d = normalize(R*(fc.x-res.x*.5)/F + U*(-(fc.y-cy)/F) + Fw);
  vec3 col;
  vec2 sunH = normalize(sunDisc.xz + vec2(1e-4));
  if (d.y < -1e-5) {
    float t = -camPos.y / d.y; vec2 P = camPos.xz + d.xz*t;
    float pw = t / F / max(-d.y, 0.03);            // world size of a pixel along depth
    float n = 0.0;
    n += (vn(P*0.35)-.5)*0.028;                    // large mottling
    n += (vn(P*2.1+7.)-.5)*0.022*clamp(1.-pw*2.1*1.2,0.,1.);
    n += (vn(P*vec2(9.,4.)+3.)-.5)*0.03*clamp(1.-pw*9.*1.2,0.,1.);
    n += (vn(P*vec2(48.,13.))-.5)*0.035*clamp(1.-pw*48.*1.3,0.,1.); // fibres
    n += (vn(P*120.)-.5)*0.02*clamp(1.-pw*120.*1.4,0.,1.);
    vec3 paper = vec3(.968,.958,.94) * (1. + n);
    vec2 dh = normalize(d.xz);
    float sheen = pow(max(dot(dh, sunH),0.), 6.) * (1.-smoothstep(0.,0.5,sunDisc.y)) * (1.-exp(-t/40.));
    col = paper * light * exposure + sunCol*sheen*0.10*sunVis;
    float haze = (1.-exp(-t/160.))*0.4; haze = max(haze, smoothstep(0.06, 0.0, -d.y));
    col = mix(col, skyHor*1.0, haze);
  } else {
    float e = d.y;
    col = mix(skyHor, skyTop, pow(clamp(e/0.42,0.,1.),0.75));
    col = mix(col, skyHor*1.03+0.02, exp(-e*45.)*0.6);
    float a = acos(clamp(dot(d, normalize(sunDisc)),-1.,1.));
    float glow = exp(-a*18.)*0.55 + exp(-a*4.)*0.14;
    col += sunCol*glow*sunVis*(1.-smoothstep(-0.01,0.0,-e)*0.);
    float rpx = length(fc - sunPx.xy);
    float disc = sunPx.z > 0. ? smoothstep(sunPx.z + 1.2, sunPx.z - 1.2, rpx) * step(fc.y, hy) : 0.;
    if (sunPx.z > 0.) col += sunCol * exp(-rpx / (sunPx.z * 2.2)) * 0.35 * sunVis;
    col = mix(col, mix(sunCol, vec3(1., .97, .9), 0.6), disc*sunVis);
    // subtle cloud wisps
    vec2 sp2 = d.xz/max(e,0.02);
    float cl = vn(sp2*vec2(0.6,2.2)+3.)*vn(sp2*vec2(1.3,4.)+9.);
    col = mix(col, mix(skyHor, vec3(1.), .4), smoothstep(.3,.7,cl)*0.12*smoothstep(0.,0.05,e));
  }
  vec2 q = fc/res - .5; col *= 1. - 0.07*dot(q*vec2(1.2,0.8), q*vec2(1.2,0.8))*2.;
  gl_FragColor = vec4(clamp(col,0.,1.), 1.);
}`;
function initGL() {
  glc = document.createElement('canvas'); glc.width = W; glc.height = H;
  gl = glc.getContext('webgl', { preserveDrawingBuffer: true, antialias: false });
  const sh = (type, src) => { const s = gl.createShader(type); gl.shaderSource(s, src); gl.compileShader(s); if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(s)); return s; };
  glProg = gl.createProgram(); gl.attachShader(glProg, sh(gl.VERTEX_SHADER, 'attribute vec2 p; void main(){gl_Position=vec4(p,0.,1.);}')); gl.attachShader(glProg, sh(gl.FRAGMENT_SHADER, FS)); gl.linkProgram(glProg); gl.useProgram(glProg);
  const b = gl.createBuffer(); gl.bindBuffer(gl.ARRAY_BUFFER, b); gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 1, -1, -1, 1, 1, 1]), gl.STATIC_DRAW);
  const loc = gl.getAttribLocation(glProg, 'p'); gl.enableVertexAttribArray(loc); gl.vertexAttribPointer(loc, 2, gl.FLOAT, false, 0, 0);
  glU = {}; for (const n of ['res', 'camPos', 'pitch', 'F', 'cy', 'hy', 'sunPx', 'light', 'skyTop', 'skyHor', 'sunDisc', 'sunCol', 'exposure', 'sunVis']) glU[n] = gl.getUniformLocation(glProg, n);
  gl.viewport(0, 0, W, H);
}
function drawGL(E) {
  gl.uniform2f(glU.res, W, H); gl.uniform3f(glU.camPos, cam.x, cam.y, cam.z); gl.uniform1f(glU.pitch, C.pitch); gl.uniform1f(glU.F, C.F); gl.uniform1f(glU.cy, C.cy);
  gl.uniform3fv(glU.light, E.light); gl.uniform3fv(glU.skyTop, E.top); gl.uniform3fv(glU.skyHor, E.hor); gl.uniform3fv(glU.sunDisc, E.disc); gl.uniform3fv(glU.sunCol, E.sunCol);
  gl.uniform1f(glU.exposure, E.exposure); gl.uniform1f(glU.hy, C.horizonY); { const p = projDir(...E.disc); gl.uniform3f(glU.sunPx, p[0], p[1], p[2] > 0 ? 26 : 0); } gl.uniform1f(glU.sunVis, smooth(-3 * DEG, 0.5 * DEG, E.el));
  gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
}

// ---------------------------------------------------------------- figure
function legIK(Hp, A, fwd) {
  const L1 = 0.47, L2 = 0.455; let dx = A[0] - Hp[0], dy = A[1] - Hp[1], dz = A[2] - Hp[2]; let d = Math.hypot(dx, dy, dz);
  if (d > L1 + L2 - 1e-3) { const k = (L1 + L2 - 1e-3) / d; A = [Hp[0] + dx * k, Hp[1] + dy * k, Hp[2] + dz * k]; dx *= k; dy *= k; dz *= k; d = L1 + L2 - 1e-3; }
  const ux = dx / d, uy = dy / d, uz = dz / d; const x = (L1 * L1 - L2 * L2 + d * d) / (2 * d), h = Math.sqrt(Math.max(0, L1 * L1 - x * x));
  let px = fwd[0], py = 0, pz = fwd[1]; const dp = px * ux + py * uy + pz * uz; px -= dp * ux; py -= dp * uy; pz -= dp * uz; const pl = Math.hypot(px, py, pz) || 1;
  return { K: [Hp[0] + ux * x + px / pl * h, Hp[1] + uy * x + py / pl * h, Hp[2] + uz * x + pz / pl * h], A };
}
function poseOf(w, t) {
  const s = w.sAt(t);
  const v = (w.sAt(t + 0.06) - w.sAt(t - 0.06)) / 0.12; const amp = clamp(v / 1.15, 0, 1);
  const P = w.path.at(s); const hd = w.path.dir(s, 0.55); let ang = Math.atan2(hd[1], hd[0]);
  let fl = w.foot(0, s), fr = w.foot(1, s);
  if (t > w.tTurn) {
    const q = smooth(w.tTurn, w.tTurn + 1.15, t); const a0 = Math.atan2(w.path.dir(w.sEnd, 0.55)[1], w.path.dir(w.sEnd, 0.55)[0]);
    ang = a0 + wrapA(w.faceAng - a0) * q;
    const af = w.faceAng, r = [Math.sin(af), -Math.cos(af)];
    const tgt = side => [P[0] + r[0] * C.footLat * (side ? 1 : -1), P[1] + r[1] * C.footLat * (side ? 1 : -1)];
    const mv = (f, side, u) => { const g = tgt(side), e = u * u * (3 - 2 * u); return { x: lerp(f.x, g[0], e), z: lerp(f.z, g[1], e), h: 0.08 * Math.sin(Math.PI * u), ang: f.ang + wrapA(af - f.ang) * e }; };
    fl = mv(fl, 0, smooth(0, 0.55, q)); fr = mv(fr, 1, smooth(0.45, 1, q));
  }
  const f = [Math.cos(ang), Math.sin(ang)], r = [f[1], -f[0]];
  const st = C.stride; const frac = (((s - w.sigma) / st) % 1 + 1) % 1;
  const hy = 0.985 - 0.05 * amp * (1 - Math.pow(Math.sin(Math.PI * frac), 2));
  const sway = 0.018 * amp * Math.sin(Math.PI * (s - w.sigma) / st);
  const hip = [P[0] + r[0] * sway, hy, P[1] + r[1] * sway];
  const pel = sgn => [hip[0] + r[0] * 0.088 * sgn, hip[1], hip[2] + r[1] * 0.088 * sgn];
  const leg = (ft, sgn) => {
    const A = [ft.x, 0.075 + ft.h, ft.z]; const ik = legIK(pel(sgn), A, f);
    const fa = ft.ang; const toe = [ik.A[0] + Math.cos(fa) * 0.15, Math.max(0.02, ik.A[1] - 0.055 - ft.h * 0.3), ik.A[2] + Math.sin(fa) * 0.15];
    return { H: pel(sgn), K: ik.K, A: ik.A, T: toe };
  };
  const LL = leg(fl, -1), LR = leg(fr, 1);
  const lean = 0.05 + 0.04 * amp;
  const neck = [hip[0] + f[0] * lean, hip[1] + 0.535, hip[2] + f[1] * lean];
  const shY = neck[1] - 0.045;
  // shoulders counter-rotate with stride
  const twist = 0.06 * amp * Math.sin(Math.PI * (s - w.sigma) / st);
  const sh = sgn => [neck[0] + r[0] * 0.18 * sgn + f[0] * twist * sgn, shY, neck[2] + r[1] * 0.18 * sgn + f[1] * twist * sgn];
  const rel = ft => clamp(((ft.x - hip[0]) * f[0] + (ft.z - hip[2]) * f[1]) / 0.36, -1.2, 1.2);
  const arm = (sgn, other) => {
    const S = sh(sgn); const b = 0.5 * rel(other) * amp; const out = 0.035;
    const E = [S[0] + (f[0] * Math.sin(b) + r[0] * out * sgn) * 0.29, S[1] - Math.cos(b) * 0.29, S[2] + (f[1] * Math.sin(b) + r[1] * out * sgn) * 0.29];
    const b2 = b + 0.12 + 0.45 * Math.max(0, b);
    const Hn = [E[0] + (f[0] * Math.sin(b2) + r[0] * 0.02 * sgn) * 0.27, E[1] - Math.cos(b2) * 0.27, E[2] + (f[1] * Math.sin(b2) + r[1] * 0.02 * sgn) * 0.27];
    return { S, E, Hn };
  };
  const AL = arm(-1, fr), AR = arm(1, fl);
  const head = [neck[0] + f[0] * 0.025, neck[1] + 0.165, neck[2] + f[1] * 0.025];
  return { hip, LL, LR, AL, AR, neck, head, pelL: pel(-1), pelR: pel(1), alpha: w.tSpawn < 0 ? 1 : smooth(w.tSpawn, w.tSpawn + 0.4, t) };
}
function drawFigure(ctx, J, pf) {
  const seg = (a, b, th) => { const A = pf(a), B = pf(b); if (A[2] < 0.3 || B[2] < 0.3) return; ctx.lineWidth = Math.max(0.6, th * C.F * 2 / (A[2] + B[2])); ctx.beginPath(); ctx.moveTo(A[0], A[1]); ctx.lineTo(B[0], B[1]); ctx.stroke(); };
  const poly = (pts, th) => { const P = pts.map(pf); if (P.some(p => p[2] < 0.3)) return; const z = P.reduce((a, p) => a + p[2], 0) / P.length; ctx.lineWidth = th * C.F / z; ctx.beginPath(); P.forEach((p, i) => i ? ctx.lineTo(p[0], p[1]) : ctx.moveTo(p[0], p[1])); ctx.closePath(); ctx.fill(); ctx.stroke(); };
  for (const Lg of [J.LL, J.LR]) { seg(Lg.H, Lg.K, 0.155); seg(Lg.K, Lg.A, 0.115); seg(Lg.A, Lg.T, 0.085); }
  const mid = (a, b, u) => [lerp(a[0], b[0], u), lerp(a[1], b[1], u), lerp(a[2], b[2], u)];
  const pc = mid(J.pelL, J.pelR, .5); const chest = [J.neck[0], J.neck[1] - 0.12, J.neck[2]];
  seg([pc[0], pc[1] + 0.05, pc[2]], chest, 0.25);
  poly([J.pelL, J.pelR, J.AR.S, J.AL.S], 0.1);
  for (const A of [J.AL, J.AR]) { seg(A.S, A.E, 0.095); seg(A.E, A.Hn, 0.075); }
  seg(J.neck, J.head, 0.1);
  const Hd = pf(J.head); if (Hd[2] > 0.3) { ctx.beginPath(); ctx.ellipse(Hd[0], Hd[1], 0.105 * C.F / Hd[2], 0.12 * C.F / Hd[2], 0, 0, TAU); ctx.fill(); }
}

// ---------------------------------------------------------------- frame
window.renderFrame = function (t, fmt = 'image/jpeg', q = 0.95) {
  setCamera(t); const E = env(t);
  drawGL(E);
  const c = octx; c.setTransform(1, 0, 0, 1, 0, 0); c.globalCompositeOperation = 'source-over'; c.globalAlpha = 1; c.filter = 'none';
  c.drawImage(glc, 0, 0);
  const lt = E.light;
  const tint = (rgb, a) => `rgba(${Math.round(rgb[0] * lt[0] * 255)},${Math.round(rgb[1] * lt[1] * 255)},${Math.round(rgb[2] * lt[2] * 255)},${a})`;
  const aff = (x, y, z) => { const p = proj(x, y, z); if (p[2] < 0.5) return null; const a = proj(x + 0.05, y, z), b = proj(x, y, z + 0.05); return [(a[0] - p[0]) / 0.05, (a[1] - p[1]) / 0.05, (b[0] - p[0]) / 0.05, (b[1] - p[1]) / 0.05, p[0], p[1]]; };
  const onScreen = (p, m) => p[0] > -m && p[0] < W + m && p[1] > C.horizonY - 5 && p[1] < H + m;

  // --- colour washes (watercolour bloom where scenery grows)
  c.globalCompositeOperation = 'multiply';
  for (const w of washes) {
    if (w.t > t) continue; const u = smooth(w.t, w.t + 2.5, t); const A = aff(w.x, 0, w.z); if (!A || !onScreen([A[4], A[5]], 300)) continue;
    const r = w.r * (0.4 + 0.6 * u); c.setTransform(A[0] * r / 64, A[1] * r / 64, A[2] * r / 64, A[3] * r / 64, A[4], A[5]);
    c.globalAlpha = 0.07 * u; c.drawImage(blobSprites[w.kind], -64, -64);
  }
  c.globalCompositeOperation = 'source-over'; c.globalAlpha = 1;
  // --- petals & sprouts
  for (const it of scen) {
    if (it.t > t) continue; const u = clamp((t - it.t) / (it.kind ? 1.6 : 0.9), 0, 1);
    const hgt = it.kind ? 0 : 0.45 * (1 - u) * (1 - u);
    const A = aff(it.x, hgt, it.z); if (!A || !onScreen([A[4], A[5]], 30)) continue;
    const zk = Math.pow(clamp(proj(it.x, 0, it.z)[2] / 13, 1, 3), 0.75); c.setTransform(A[0] * zk, A[1] * zk, A[2] * zk, A[3] * zk, A[4], A[5]);
    if (it.kind === 0) {
      const col = it.hue < .5 ? [0.98, 0.70, 0.78] : [0.99, 0.80, 0.85];
      c.fillStyle = tint(col, 0.85 * smooth(0, .3, u)); const rot = it.rot + (1 - u) * 2.5; const s = 0.085 * it.sc;
      c.beginPath(); c.ellipse(0, 0, s, s * 0.62, rot, 0, TAU); c.fill();
    } else {
      const col = it.hue < .5 ? [0.56, 0.78, 0.43] : [0.68, 0.85, 0.5]; const e = u * u * (3 - 2 * u); const s = 0.11 * it.sc * e;
      c.fillStyle = tint(col, 0.8 * smooth(0, .25, u));
      for (const sg of [-1, 1]) { const a = it.rot + sg * 0.55; c.beginPath(); c.ellipse(Math.cos(a) * s * 0.6, Math.sin(a) * s * 0.6, s * 0.65, s * 0.3, a, 0, TAU); c.fill(); }
    }
  }
  // --- footprints (ink)
  const { Lpx, cw, ch } = sprites.meta; const NST = sprites[0].length;
  for (const p of prints) {
    if (p.t > t) break;
    const P0 = proj(p.x, 0, p.z); if (P0[2] < 0.5 || !onScreen(P0, 40)) continue;
    const fx = Math.cos(p.ang), fz = Math.sin(p.ang);
    const Pf = proj(p.x + fx * 0.05, 0, p.z + fz * 0.05), Pr = proj(p.x + fz * 0.05, 0, p.z - fx * 0.05);
    const Jf = [(Pf[0] - P0[0]) / 0.05, (Pf[1] - P0[1]) / 0.05], Jr = [(Pr[0] - P0[0]) / 0.05, (Pr[1] - P0[1]) / 0.05];
    const age = t - p.t; const stg = Math.min(NST - 1, Math.floor(clamp(age / 0.7, 0, 1) * (NST - 1) + 1e-6));
    const lenPx = Math.hypot(Jf[0], Jf[1]) * C.footLen * p.sc; const lvl = clamp(Math.floor(Math.log2(Lpx / Math.max(1, lenPx * 1.6))), 0, 4);
    const img = sprites[p.side * 3 + p.v][stg][lvl]; const k = (C.footLen * p.sc) / Lpx * (1 << lvl);
    // sprite x -> lateral (right), sprite y(down) -> backward
    c.setTransform(Jr[0] * k, Jr[1] * k, -Jf[0] * k, -Jf[1] * k, P0[0], P0[1]);
    c.globalAlpha = 0.93 * smooth(0, 0.05, age);
    c.drawImage(img, -img.width / 2, -img.height / 2);
  }
  c.globalAlpha = 1; c.setTransform(1, 0, 0, 1, 0, 0);

  // --- people
  const live = [];
  for (const w of walkers) { if (w.tSpawn > t) continue; const J = poseOf(w, t); const p = proj(J.hip[0], 0, J.hip[2]); live.push({ w, J, z: p[2], p }); }
  live.sort((a, b) => b.z - a.z);
  // shadows
  const L = E.L, sx = -L[0] / L[1], sz = -L[2] / L[1];
  const pfS = P => proj(P[0] + P[1] * sx, 0, P[2] + P[1] * sz);
  sctx.setTransform(1, 0, 0, 1, 0, 0); sctx.clearRect(0, 0, W, H); sctx.fillStyle = sctx.strokeStyle = '#000'; sctx.lineCap = sctx.lineJoin = 'round';
  for (const o of live) drawFigure(sctx, o.J, pfS);
  if (E.strength > 0.002) {
    c.globalCompositeOperation = 'source-over';
    // tint the shadow mask
    tctx.setTransform(1, 0, 0, 1, 0, 0); tctx.globalCompositeOperation = 'copy'; tctx.drawImage(shadowC, 0, 0); tctx.globalCompositeOperation = 'source-in';
    const sc = E.shadow; tctx.fillStyle = `rgb(${sc[0] * lt[0] * 255 | 0},${sc[1] * lt[1] * 255 | 0},${sc[2] * lt[2] * 255 | 0})`; tctx.fillRect(0, 0, W, H); tctx.globalCompositeOperation = 'source-over';
    c.globalCompositeOperation = 'multiply'; c.globalAlpha = Math.min(1, E.strength * 1.6); c.filter = 'blur(0.8px)'; c.drawImage(tmpC, 0, 0); c.filter = 'none';
    c.globalAlpha = 1; c.globalCompositeOperation = 'source-over';
  }
  // contact shadows
  c.globalCompositeOperation = 'multiply';
  for (const o of live) { const A = aff(o.J.hip[0], 0, o.J.hip[2]); if (!A) continue; c.setTransform(A[0] * 0.32 / 64, A[1] * 0.32 / 64, A[2] * 0.32 / 64, A[3] * 0.32 / 64, A[4], A[5]); c.globalAlpha = 0.35 * o.J.alpha; c.drawImage(contactBlob(), -64, -64); }
  c.globalCompositeOperation = 'source-over'; c.globalAlpha = 1; c.setTransform(1, 0, 0, 1, 0, 0);
  // bodies
  const bodyCol = '#16130f';
  for (const o of live) {
    if (o.J.alpha >= 0.999) { c.fillStyle = c.strokeStyle = bodyCol; c.lineCap = c.lineJoin = 'round'; drawFigure(c, o.J, projA); }
    else if (o.J.alpha > 0.001) {
      tctx.setTransform(1, 0, 0, 1, 0, 0); tctx.globalCompositeOperation = 'source-over'; tctx.clearRect(0, 0, W, H);
      tctx.fillStyle = tctx.strokeStyle = bodyCol; tctx.lineCap = tctx.lineJoin = 'round'; drawFigure(tctx, o.J, projA);
      c.globalAlpha = o.J.alpha; c.drawImage(tmpC, 0, 0); c.globalAlpha = 1;
    }
  }
  // final white (loop seam)
  if (E.fade > 0.0005) { c.fillStyle = `rgba(250,248,245,${E.fade})`; c.fillRect(0, 0, W, H); }
  return out.toDataURL(fmt, q);
};
let _cb = null;
function contactBlob() { if (_cb) return _cb; const cv = document.createElement('canvas'); cv.width = cv.height = 128; const x = cv.getContext('2d'); const g = x.createRadialGradient(64, 64, 0, 64, 64, 64); g.addColorStop(0, 'rgba(60,55,60,1)'); g.addColorStop(1, 'rgba(60,55,60,0)'); x.fillStyle = g; x.fillRect(0, 0, 128, 128); _cb = cv; return cv; }

window.debugInfo = () => ({ walkers: walkers.map(w => ({ stroke: w.stroke, lane: w.lane, tSpawn: +w.tSpawn.toFixed(2), start: +w.knots[1][0].toFixed(2), stop: +w.tStopS.toFixed(2), src: w.src })), cam: camMain });
})();

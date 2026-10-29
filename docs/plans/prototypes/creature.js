// Prototype copy of the voxel generator in zest-table-design.zip (tools/creature-generate.js), with three changes. P.form, when set, picks
// the body form (0-8), so each installed model can get its own (see the plan's archetype store). The extra row of small eyes is
// off unless P.eyeRow, so the eye count tracks the context window (with it, 128K and 256K models both showed 14 eyes).
// Three: skin grain draws from its own random stream, so Q4 and Q8 of a model keep the same body.
// Ported verbatim from reference/Creature.dc.html (lines 98 to 463).
// generate(P) takes { seed, params, quant, ctx, vision, reasoning, tools, tone }
// and returns SVG path runs plus eye and spark decals in a 480x420 viewBox.
// Render: one <path d fill> per run in `runs`, then `brainRuns`, then the three floater groups.
// Eyes and sparks are unit-square decals placed with the SVG matrix in `m`.
const PAL = [
  { t: '#7aa296', l: '#4f7a70', r: '#2f524b', iris: '#f7d916' },
  { t: '#b7b98a', l: '#8a8c61', r: '#5d5f3f', iris: '#e4572e' },
  { t: '#8a8fb0', l: '#5f6488', r: '#3b3e5c', iris: '#f7d916' },
  { t: '#6b6b78', l: '#3f3f4b', r: '#22222b', iris: '#f7d916' },
  { t: '#e0a24c', l: '#b8772a', r: '#7f4c18', iris: '#1d1d1a' }
];
const TRI = {
  bone: { t: '#f1ebd9', l: '#cfc7b0', r: '#a8a088' },
  ink: { t: '#4a4a55', l: '#2c2c36', r: '#16161d' },
  lemon: { t: '#f7e04a', l: '#d9bd14', r: '#a08c05' },
  brain: { t: '#f2c4bc', l: '#d99d94', r: '#b27a72' },
  void: { t: '#2a2a33', l: '#15151b', r: '#0a0a0e' }
};
const U = { w: 12.1, h: 7, H: 14 };
function hashStr(s) {
  let h = 2166136261;
  for (let i = 0; i < s.length; i++) { h ^= s.charCodeAt(i); h = Math.imul(h, 16777619); }
  return h >>> 0;
}
function mulberry(a) {
  return function () {
    a |= 0; a = (a + 0x6D2B79F5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}
const f1 = (v) => (Math.round(v * 10) / 10).toString();
function generate(P) {
  const rnd = mulberry(hashStr(P.seed));
  const pal = PAL[P.tone % PAL.length];
  const color = (m, f) => { const t = m === 'body' || m === 'tent' ? pal : TRI[m]; return f === 't' ? t.t : f === 'y' ? t.l : t.r; };
  const map = new Map();
  const K = (x, y, z) => ((x + 48) * 97 + (y + 48)) * 64 + z;
  const has = (x, y, z) => map.has(K(x, y, z));
  const put = (x, y, z, m, force) => {
    if (z < 0 || z > 58 || x < -44 || x > 44 || y < -44 || y > 44) return false;
    const k = K(x, y, z);
    if (map.has(k) && !force) return false;
    map.set(k, { x, y, z, m });
    return true;
  };
  const topAt = (x, y) => { for (let z = 58; z >= 0; z--) if (has(x, y, z)) return z; return -1; };
  const qd = parseInt((P.quant.match(/\d/) || ['4'])[0], 10);
  const rxv = 5 + 1.1 * Math.log2(P.params + 1);
  const sIdx = P.form != null ? P.form % 9 : hashStr(P.seed + '|' + P.params) % 9;
  const asp = 0.8 + rnd() * 0.45, asp2 = 0.85 + rnd() * 0.4;
  const ph = [rnd() * 6.28, rnd() * 6.28, rnd() * 6.28];
  const lump = 0.09 + (8 - Math.min(8, qd)) * 0.01;
  const R = Math.ceil(rxv * 1.8) + 2, ZM = Math.ceil(rxv * 3.6) + 2;
  let inside;
  if (sIdx === 0) {
    const ryv = rxv * asp, rzv = rxv * asp2 * 0.95, zc0 = rzv, buds = [];
    const nb = 2 + Math.floor(rnd() * 3);
    for (let i = 0; i < nb; i++) {
      const az = rnd() * 6.28, el = -0.1 + rnd() * 0.9, br = rxv * (0.3 + rnd() * 0.22), d = 0.92;
      buds.push({ x: Math.cos(az) * Math.cos(el) * rxv * d, y: Math.sin(az) * Math.cos(el) * ryv * d, z: zc0 + Math.sin(el) * rzv * d, r: br });
    }
    inside = (cx, cy, cz) => {
      const n = 1 + lump * (Math.sin(1.3 * cx + ph[0]) + Math.sin(1.1 * cy + ph[1]) * Math.cos(0.9 * cz + ph[2]));
      const dx = cx / rxv, dy = cy / ryv, dz = (cz - zc0) / rzv;
      if (dx * dx + dy * dy + dz * dz <= n * n) return true;
      for (const b of buds) if ((cx - b.x) * (cx - b.x) + (cy - b.y) * (cy - b.y) + (cz - b.z) * (cz - b.z) <= b.r * b.r) return true;
      return false;
    };
  } else if (sIdx === 1) {
    const Ht = rxv * (2.8 + rnd() * 0.8), r0 = rxv * 0.62, tier = 2 + Math.floor(rnd() * 2);
    inside = (cx, cy, cz) => {
      if (cz > Ht) return false;
      const zq = Math.floor(cz / tier) * tier, r = Math.max(1.3, r0 * (1 - 0.6 * zq / Ht));
      return Math.abs(cx) <= r && Math.abs(cy) <= r * asp;
    };
  } else if (sIdx === 2) {
    const a = rxv * 1.5, b = rxv * 1.5 * asp, c = rxv * 0.42, hr = rxv * 0.55;
    inside = (cx, cy, cz) => {
      const e = (cx / a) * (cx / a) + (cy / b) * (cy / b) + ((cz - c) / c) * ((cz - c) / c);
      const h = cx * cx + cy * cy + (cz - c * 0.6) * (cz - c * 0.6);
      return e <= 1 || h <= hr * hr;
    };
  } else if (sIdx === 3) {
    const Rm = rxv * 0.95, rm = rxv * 0.45;
    inside = (cx, cy, cz) => { const q = Math.hypot(Math.hypot(cx, cy / asp) - Rm, cz - rm); return q <= rm; };
  } else if (sIdx === 4) {
    const sp = [], n = 3 + Math.floor(rnd() * 3);
    for (let i = 0; i < n; i++) { const r = rxv * (0.45 + rnd() * 0.3); sp.push({ x: (rnd() - 0.5) * rxv * 1.2, y: (rnd() - 0.5) * rxv * 1.2, z: r * (0.8 + rnd() * 0.8), r }); }
    inside = (cx, cy, cz) => sp.some((q) => (cx - q.x) * (cx - q.x) + (cy - q.y) * (cy - q.y) + (cz - q.z) * (cz - q.z) <= q.r * q.r);
  } else if (sIdx === 5) {
    const Ht = rxv * (1.9 + rnd() * 0.5), r0 = rxv * 1.2;
    inside = (cx, cy, cz) => { if (cz > Ht) return false; const r = r0 * (1 - cz / Ht); return Math.abs(cx) <= r && Math.abs(cy) <= r * asp; };
  } else if (sIdx === 6) {
    const arm = rxv * 0.38, L = rxv * 1.1, mid = rxv * 0.9, hh = rxv * 0.7;
    inside = (cx, cy, cz) => (Math.abs(cx) <= arm && Math.abs(cy) <= L && Math.abs(cz - mid) <= hh) || (Math.abs(cx) <= L && Math.abs(cy) <= arm && Math.abs(cz - mid) <= hh) || (Math.abs(cx) <= arm && Math.abs(cy) <= arm && cz <= rxv * 2.7);
  } else if (sIdx === 7) {
    const Ht = rxv * (2.6 + rnd() * 0.6), turn = 0.28 + rnd() * 0.2;
    inside = (cx, cy, cz) => {
      if (cz > Ht) return false;
      const th = cz * turn, u = cx * Math.cos(th) + cy * Math.sin(th), v = -cx * Math.sin(th) + cy * Math.cos(th);
      const a = rxv * (1.1 - 0.4 * cz / Ht), b = rxv * 0.5;
      return (u / a) * (u / a) + (v / b) * (v / b) <= 1;
    };
  } else {
    const n = 4 + Math.floor(rnd() * 3), rs = [];
    for (let i = 0; i < n; i++) rs.push(rxv * (0.5 + rnd() * 0.7));
    inside = (cx, cy, cz) => {
      const zi = Math.floor(cz / 3);
      if (zi >= n) return false;
      if (Math.hypot(cx, cy) <= 1.7) return true;
      if (cz - 3 * zi >= 2) return false;
      return (cx / rs[zi]) * (cx / rs[zi]) + (cy / (rs[zi] * asp)) * (cy / (rs[zi] * asp)) <= 1;
    };
  }
  for (let x = -R; x <= R; x++) for (let y = -R; y <= R; y++) for (let z = 0; z <= ZM; z++) {
    if (inside(x + 0.5, y + 0.5, z + 0.5)) put(x, y, z, 'body');
  }
  const pe = { 8: 0, 6: 0.03, 5: 0.06, 4: 0.1, 3: 0.17, 2: 0.27 }[qd] ?? 0.1;
  const rq = mulberry(hashStr(P.seed + '|grain'));
  if (pe > 0) {
    const rem = [];
    for (const v of map.values()) {
      if (v.z > 0 && v.m === 'body' && (!has(v.x + 1, v.y, v.z) || !has(v.x, v.y + 1, v.z) || !has(v.x, v.y, v.z + 1) || !has(v.x - 1, v.y, v.z) || !has(v.x, v.y - 1, v.z)) && rq() < pe) rem.push(v);
    }
    rem.forEach((v) => map.delete(K(v.x, v.y, v.z)));
  }
  let zMax = 0, fp = 1, ax = 0, ay = 0, bestZ = -1;
  for (const v of map.values()) {
    zMax = Math.max(zMax, v.z);
    if (v.z <= 1) fp = Math.max(fp, Math.hypot(v.x, v.y));
  }
  for (const v of map.values()) {
    if (has(v.x, v.y, v.z + 1)) continue;
    if (v.z > bestZ || (v.z === bestZ && Math.hypot(v.x, v.y) < Math.hypot(ax, ay))) { bestZ = v.z; ax = v.x; ay = v.y; }
  }
  const zc = Math.max(1, zMax / 2);
  const nT = 5 + Math.floor(rnd() * 5);
  for (let i = 0; i < nT; i++) {
    const th = (i / nT) * 6.283 + rnd() * 0.6, ox = Math.cos(th), oy = Math.sin(th);
    let x = 0, y = 0, z = 0;
    for (let r = Math.ceil(fp) + 1; r >= 0; r--) { const px = Math.round(ox * r), py = Math.round(oy * r); if (has(px, py, 0) || has(px, py, 1)) { x = px; y = py; break; } }
    const L = 10 + Math.floor(rnd() * 9), arch = rnd() < 0.4, archH = 3 + Math.floor(rnd() * 6), domX = Math.abs(ox) >= Math.abs(oy);
    for (let s = 0; s < L; s++) {
      const tz = arch ? Math.round(archH * Math.sin(Math.PI * (s / L))) : rnd() < 0.1 ? 1 : 0, r = rnd();
      if (z < tz && r < 0.6) z++;
      else if (z > tz && z > 0 && r < 0.5) z--;
      else {
        const alongX = rnd() < 0.62 ? domX : !domX;
        const sgn = (alongX ? ox : oy) >= 0 ? 1 : -1, flip = rnd() < 0.2 ? -1 : 1;
        if (alongX) x += sgn * flip; else y += sgn * flip;
      }
      x = Math.max(-16, Math.min(16, x)); y = Math.max(-16, Math.min(16, y));
      put(x, y, z, s >= L - 2 ? 'bone' : 'tent', s >= L - 2);
      if (s < 5) { put(x + 1, y, z, 'tent'); put(x, y + 1, z, 'tent'); }
    }
  }
  if (P.tools) {
    [[1, -0.5, 'x'], [-0.5, 1, 'y']].forEach((a, i) => {
      let x = 0, y = 0, z = Math.max(3, Math.round(zMax * 0.6)), found = false;
      const an = Math.hypot(a[0], a[1]);
      for (let r = Math.ceil(fp) + 2; r >= 0; r--) { const px = Math.round(a[0] / an * r), py = Math.round(a[1] / an * r); if (has(px, py, z)) { x = px; y = py; found = true; break; } }
      if (!found) return;
      const L = 7 + Math.floor(rnd() * 3);
      for (let s = 0; s < L; s++) {
        if (a[2] === 'x') x++; else y++;
        if (s % 2 === 1 && z > 2) z--;
        else if (rnd() < 0.3) { if (a[2] === 'x') y += rnd() < 0.5 ? 1 : -1; else x += rnd() < 0.5 ? 1 : -1; }
        put(x, y, z, 'tent');
      }
      while (z > 2) { z--; put(x, y, z, 'tent'); }
      [[0, 0], [1, 0], [0, 1], [1, 1]].forEach((o) => put(x + o[0], y + o[1], z, 'bone', true));
      if (i === 0) { put(x, y, z + 1, 'ink', true); put(x, y, z + 2, 'ink', true); put(x, y, z + 3, 'lemon', true); }
      else { put(x, y, z + 1, 'lemon', true); put(x, y, z + 2, 'lemon', true); put(x, y, z + 3, 'ink', true); }
    });
  }
  if (rnd() < 0.75) {
    const nr = 4 + Math.floor(rnd() * 4), cxr = ax - Math.round(fp * 0.45), cyr = ay - Math.round(fp * 0.45);
    for (let i = 0; i < nr; i++) {
      const k = i - Math.floor(nr / 2), x = cxr + k, y = cyr - k, zt = topAt(x, y);
      if (zt < 0) continue;
      const hg = 2 + Math.floor(rnd() * 5);
      for (let j = 1; j <= hg; j++) put(x, y, zt + j, 'ink');
    }
  }
  const nEyes = 3 + Math.round(Math.log2(Math.max(8, P.ctx) / 4));
  const stalkTips = [];
  const nS = Math.min(Math.floor(rnd() * 3) + (P.ctx >= 64 ? 1 : 0), 3);
  const topC = [...map.values()].filter((v) => v.m === 'body' && !has(v.x, v.y, v.z + 1) && v.z >= zc * 0.8 && !(P.reasoning && Math.hypot(v.x - ax, v.y - ay) < 4));
  for (let i = 0; i < nS && topC.length; i++) {
    const v = topC[Math.floor(rnd() * topC.length)];
    let x = v.x, y = v.y, z = v.z;
    const len = 3 + Math.floor(rnd() * 4);
    for (let j = 0; j < len; j++) { z++; if (rnd() < 0.3) { if (rnd() < 0.5) x++; else y++; } put(x, y, z, 'tent'); }
    stalkTips.push({ x, y, z });
  }
  const brainSparks = [];
  let hasBrain = false;
  if (P.reasoning) {
    hasBrain = true;
    const zt = topAt(ax, ay);
    for (let x = ax - 5; x <= ax + 5; x++) for (let y = ay - 5; y <= ay + 5; y++) for (let z = zt - 1; z <= zt + 4; z++) {
      const dx = (x - ax + 0.5) / 3.4, dy = (y - ay + 0.5) / 3.0, dz = (z + 0.5 - (zt + 0.6)) / 2.5;
      const n = 1 + 0.12 * Math.sin(2.1 * x + ph[0] * 1.7) * Math.cos(1.8 * y + ph[1]);
      if (dx * dx + dy * dy + dz * dz > n * n) continue;
      if (has(x, y, z)) continue;
      if (x - ax === y - ay && z >= zt + 1) continue;
      put(x, y, z, 'brain');
    }
    for (let x = ax - 5; x <= ax + 5; x++) for (let y = ay - 5; y <= ay + 5; y++) {
      let cnt = 0, top = -1;
      for (let z = zt - 1; z <= zt + 4; z++) { const v = map.get(K(x, y, z)); if (v && v.m === 'brain') { cnt++; top = z; } }
      if (cnt >= 2 && rnd() < 0.25) map.delete(K(x, y, top));
    }
  }
  const yFace = [...map.values()].filter((v) => v.m === 'body' && !has(v.x, v.y + 1, v.z) && v.z >= 1 && v.z <= zMax * 0.8);
  if (yFace.length && rnd() < 0.85) {
    const sv = yFace[Math.floor(rnd() * yFace.length)];
    yFace.map((v) => ({ v, d: (v.x - sv.x) * (v.x - sv.x) + (v.z - sv.z) * (v.z - sv.z) + (v.y - sv.y) * (v.y - sv.y) }))
      .filter((o) => o.d <= 7).sort((a, b) => a.d - b.d).slice(0, 7).forEach((o) => { o.v.m = 'void'; });
  }
  let lens = null, lensTop = null;
  if (P.vision) {
    outer: for (const n of [3, 2]) {
      for (const zr of [0.5, 0.3, 0.7, 0.2]) {
        const z0 = Math.max(1, Math.round(zMax * zr));
        for (const x0 of [-1, 0, -2, 1, -3, 2, -4, 3, -5, 4]) {
          const cols = [];
          let ok = true, ymax = -99;
          for (let dx = 0; dx < n && ok; dx++) for (let dz = 0; dz < n && ok; dz++) {
            let yb = -99;
            for (let y = Math.ceil(fp) + 4; y >= -Math.ceil(fp); y--) if (has(x0 + dx, y, z0 + dz)) { yb = y; break; }
            if (yb < -90) ok = false; else { cols.push([x0 + dx, z0 + dz, yb]); ymax = Math.max(ymax, yb); }
          }
          if (!ok) continue;
          const plane = ymax + 1;
          cols.forEach((c) => { for (let y = c[2] + 1; y <= plane; y++) put(c[0], y, c[1], 'body'); });
          lens = { x0, z0, plane, n };
          break outer;
        }
      }
    }
    if (!lens) lensTop = { x: ax, y: ay, z: topAt(ax, ay) };
  }
  const mkM = (face, x, y, z, k) => {
    const off = (1 - k) / 2;
    let P0, a, b, c, d;
    if (face === 'y') { P0 = [x, y + 1, z]; a = U.w; b = U.h; c = 0; d = -U.H; }
    else if (face === 'x') { P0 = [x + 1, y, z]; a = -U.w; b = U.h; c = 0; d = -U.H; }
    else { P0 = [x, y, z + 1]; a = U.w; b = U.h; c = -U.w; d = U.h; }
    return { P0, a: a * k, b: b * k, c: c * k, d: d * k, off: [a * off + c * off, b * off + d * off] };
  };
  const centerOf = (v, f) => (f === 'y' ? [v.x + 0.5, v.y + 1, v.z + 0.5] : f === 'x' ? [v.x + 1, v.y + 0.5, v.z + 0.5] : [v.x + 0.5, v.y + 0.5, v.z + 1]);
  const scr = (c) => [(c[0] - c[1]) * U.w, (c[0] + c[1]) * U.h - c[2] * U.H];
  const vis = (f, v) => {
    const c = centerOf(v, f);
    for (let t = 0.55; t < 11; t += 0.6) if (has(Math.floor(c[0] + t), Math.floor(c[1] + t), Math.floor(c[2] + t))) return false;
    return true;
  };
  const decals = [];
  const makeEye = (f, v, k, special) => {
    const q = rnd();
    const shape = q < 0.6 ? 0 : q < 0.8 ? 1 : 2;
    const e = { f, v, k, mm: mkM(f, v.x, v.y, v.z, k), delay: (rnd() * 6).toFixed(1) + 's', ic: rnd() < 0.15 ? '#7fb7c4' : pal.iris, scl: '#efe8d2', sr: 0.44, ir: 0.3, prx: shape === 0 ? 0.06 : shape === 1 ? 0.13 : 0.26, pry: shape === 0 ? 0.26 : shape === 1 ? 0.13 : 0.07 };
    if (special === 'lens') { e.scl = '#14141a'; e.sr = 0.48; e.ir = 0.36; e.prx = 0.14; e.pry = 0.14; e.ic = '#f7d916'; }
    return e;
  };
  const used = [];
  if (lens) {
    const e = makeEye('y', { x: lens.x0, y: lens.plane, z: lens.z0 }, lens.n, 'lens');
    decals.push(e);
    used.push(scr([lens.x0 + lens.n / 2, lens.plane + 1, lens.z0 + lens.n / 2]).concat([lens.n * 16]));
  }
  if (lensTop && lensTop.z >= 0) { decals.push(makeEye('t', lensTop, 1, 'lens')); used.push(scr(centerOf(lensTop, 't')).concat([30])); }
  stalkTips.forEach((tp) => {
    ['y', 'x', 't'].forEach((f) => { if (vis(f, tp)) decals.push(makeEye(f, tp, 1)); });
    used.push(scr(centerOf(tp, 't')).concat([20]));
  });
  const bodyVs = [...map.values()].filter((v) => v.m === 'body');
  const cands = [];
  for (const v of bodyVs) {
    if (v.z < 1) continue;
    if (!has(v.x, v.y + 1, v.z) && vis('y', v)) cands.push({ v, f: 'y' });
    if (!has(v.x + 1, v.y, v.z) && vis('x', v)) cands.push({ v, f: 'x' });
    if (!has(v.x, v.y, v.z + 1) && vis('t', v) && rnd() < 0.35) cands.push({ v, f: 't' });
  }
  const need = Math.max(1, nEyes - stalkTips.length);
  let eyeCount = stalkTips.length;
  let tries = 0, placed = 0;
  while (placed < need && tries++ < 500 && cands.length) {
    const c = cands[Math.floor(rnd() * cands.length)];
    const s = scr(centerOf(c.v, c.f));
    if (used.some((u) => Math.hypot(u[0] - s[0], u[1] - s[1]) < (u[2] || 26))) continue;
    used.push(s.concat([26]));
    decals.push(makeEye(c.f, c.v, 1));
    placed++; eyeCount++;
  }
  if (P.eyeRow && nEyes >= 5 && rnd() < 0.55) {
    const byZ = {};
    cands.filter((c) => c.f === 'y').forEach((c) => { (byZ[c.v.z] = byZ[c.v.z] || []).push(c); });
    let best = [];
    Object.keys(byZ).forEach((z) => {
      const row = byZ[z].sort((a, b) => a.v.x - b.v.x);
      let cur = [row[0]];
      for (let i = 1; i < row.length; i++) {
        if (row[i].v.x === cur[cur.length - 1].v.x + 1) cur.push(row[i]); else { if (cur.length > best.length) best = cur; cur = [row[i]]; }
      }
      if (cur.length > best.length) best = cur;
    });
    if (best.length >= 4) best.slice(0, 6).forEach((c) => {
      const s = scr(centerOf(c.v, 'y'));
      if (used.some((u) => Math.hypot(u[0] - s[0], u[1] - s[1]) < 9)) return;
      decals.push(makeEye('y', c.v, 0.55)); eyeCount++;
    });
  }
  const sparkDecals = [];
  if (hasBrain) {
    const bt = [...map.values()].filter((v) => v.m === 'brain' && !has(v.x, v.y, v.z + 1));
    for (let i = 0; i < 4 && bt.length; i++) {
      const v = bt.splice(Math.floor(rnd() * bt.length), 1)[0];
      sparkDecals.push({ mm: mkM('t', v.x, v.y, v.z, 0.6), delay: (i * 0.3).toFixed(2) + 's' });
    }
  }
  let zT = 0;
  for (const v of map.values()) zT = Math.max(zT, v.z);
  const flo = [[], [], []];
  const nF = 5 + Math.floor(rnd() * 6);
  for (let i = 0; i < nF; i++) {
    const x = Math.round((rnd() - 0.5) * 2 * (rxv + 3)), y = Math.round((rnd() - 0.5) * 2 * (rxv + 3)), z = zT + 2 + Math.floor(rnd() * 6);
    const m = rnd() < 0.6 ? 'body' : rnd() < 0.6 ? 'bone' : 'lemon', two = rnd() < 0.4;
    if (x + y < -2) continue;
    flo[i % 3].push({ x, y, z, m });
    if (two) flo[i % 3].push({ x: x + 1, y, z, m });
  }
  const mainVs = [], brainVs = [];
  for (const v of map.values()) (v.m === 'brain' ? brainVs : mainVs).push(v);
  let minX = 0, maxX = 0, minY = 0, maxY = 0;
  const grow = (v) => {
    minX = Math.min(minX, (v.x - v.y - 1) * U.w); maxX = Math.max(maxX, (v.x - v.y + 1) * U.w);
    minY = Math.min(minY, (v.x + v.y) * U.h - (v.z + 1) * U.H); maxY = Math.max(maxY, (v.x + v.y + 2) * U.h - v.z * U.H);
  };
  mainVs.forEach(grow); brainVs.forEach(grow); flo.forEach((g) => g.forEach(grow));
  const sc = Math.min(1, 228 / Math.max(-minX, maxX, 1), 290 / Math.max(-minY, 1), 112 / Math.max(maxY, 1));
  const OX = 240, OY = 300;
  const pt = (x, y, z) => f1(OX + (x - y) * U.w * sc) + ' ' + f1(OY + ((x + y) * U.h - z * U.H) * sc);
  const emit = (vs, own) => {
    vs.sort((a, b) => (a.x + a.y + a.z) - (b.x + b.y + b.z) || a.z - b.z);
    const runs = [];
    let last = null;
    const add = (d, c) => { if (last && last.c === c) last.d += ' ' + d; else { last = { d, c }; runs.push(last); } };
    for (const v of vs) {
      const x = v.x, y = v.y, z = v.z;
      if (own || !has(x + 1, y, z)) add('M' + pt(x + 1, y, z) + ' L' + pt(x + 1, y + 1, z) + ' L' + pt(x + 1, y + 1, z + 1) + ' L' + pt(x + 1, y, z + 1) + ' Z', color(v.m, 'x'));
      if (own || !has(x, y + 1, z)) add('M' + pt(x, y + 1, z) + ' L' + pt(x + 1, y + 1, z) + ' L' + pt(x + 1, y + 1, z + 1) + ' L' + pt(x, y + 1, z + 1) + ' Z', color(v.m, 'y'));
      if (own || !has(x, y, z + 1)) add('M' + pt(x, y, z + 1) + ' L' + pt(x + 1, y, z + 1) + ' L' + pt(x + 1, y + 1, z + 1) + ' L' + pt(x, y + 1, z + 1) + ' Z', color(v.m, 't'));
    }
    return runs;
  };
  const matrix = (mm) => 'matrix(' + [mm.a * sc, mm.b * sc, mm.c * sc, mm.d * sc].map((n) => n.toFixed(3)).join(' ') + ' ' +
    f1(OX + ((mm.P0[0] - mm.P0[1]) * U.w + mm.off[0]) * sc) + ' ' + f1(OY + (((mm.P0[0] + mm.P0[1]) * U.h - mm.P0[2] * U.H) + mm.off[1]) * sc) + ')';
  const Rg = fp + 2;
  return {
    runs: emit(mainVs, false), brainRuns: emit(brainVs, false), hasBrain,
    fl1: emit(flo[0], true), fl2: emit(flo[1], true), fl3: emit(flo[2], true),
    eyes: decals.map((e) => ({ m: matrix(e.mm), face: e.f, delay: e.delay, ic: e.ic, scl: e.scl, sr: e.sr, ir: e.ir, prx: e.prx, pry: e.pry })),
    sparks: sparkDecals.map((s) => ({ m: matrix(s.mm), delay: s.delay })),
    shadow: { x: OX, y: OY, rx: Rg * 1.414 * U.w * sc, ry: Rg * 1.414 * U.h * sc },
    label: 'A voxel host grown from the model card of ' + P.seed + ', with ' + eyeCount + ' eyes'
  };
}
function hostParams(p) {
  return {
    seed: String(p.seed ?? 'Qwen3-4B-Instruct'), params: Number(p.params ?? 4), quant: String(p.quant ?? 'Q4_K_M'), ctx: Number(p.ctx ?? 32),
    vision: p.vision === true || p.vision === 'true', reasoning: !(p.reasoning === false || p.reasoning === 'false'), tools: !(p.tools === false || p.tools === 'false'),
    tone: p.tone === undefined || p.tone === null ? hashStr(String(p.seed ?? 'x')) % 5 : Number(p.tone)
  };
}
const archetype = (seed, params) => hashStr(seed + '|' + params) % 9;


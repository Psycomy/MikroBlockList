// The background "threat map": a world of twinkling cyan dots (world-atlas land-110m on a
// cover-fit equirectangular grid), red comets between random land points, cyan ripples where
// they land, and scan waves. Landscape screens show the whole world; portrait ones (phones)
// a cover-fit window on Europe and Africa, so the dots still read as continents. Uses the d3
// and topojson globals (vendored, classic scripts).
//
// All coordinates in the API are CSS pixels (client coordinates).

// Land mask: 0.5 degree per pixel, longitude -180..180, latitude 80..-58 (no Antarctica).
const MW = 720;
const MH = 276;
const LAT0 = 80;
const LATS = 138;
// Portrait view window (degrees): Europe + Africa, cover-fit. Its aspect is about a phone's,
// which then shows all of it with the search box over the Mediterranean; a wider portrait
// screen crops it, keeping `focus` [lon, lat] as central as the window allows.
const PORTRAIT = { lon0: -14, lon1: 50, lat0: 78, lat1: -61, focus: [18, 22] };
const NARROW = 700; // px: wider dot spacing, fewer comets
const CYAN = '0,217,255';
const GROUPS = 5; // twinkle phase groups
const LAYER_PX = 2.1e6; // max pixels per dot layer (memory: GROUPS x 4 bytes x this)
const LAND_URL = new URL('../data/land-110m.json', import.meta.url);

let maskPromise = null;
function loadMask() {
  maskPromise ||= fetch(LAND_URL)
    .then((r) => {
      if (!r.ok) throw new Error(`land-110m.json: HTTP ${r.status}`);
      return r.json();
    })
    .then((topo) => {
      const land = topojson.feature(topo, topo.objects.land);
      const oc = document.createElement('canvas');
      oc.width = MW;
      oc.height = MH;
      const ox = oc.getContext('2d', { willReadFrequently: true });
      const pr = d3.geoEquirectangular().scale(MW / (2 * Math.PI)).translate([MW / 2, LAT0 * 2]);
      ox.fillStyle = '#fff';
      ox.beginPath();
      d3.geoPath(pr, ox)(land);
      ox.fill();
      return ox.getImageData(0, 0, MW, MH).data;
    });
  return maskPromise;
}

const rgbText = (rgb) => (Array.isArray(rgb) ? rgb.join(',') : String(rgb || CYAN));

/**
 * Start the map on `canvas`. `opts.target()` returns the element big comets fly to (the
 * search box). Returns {comet, wave, project, reduced}.
 */
export function createMap(canvas, opts = {}) {
  const ctx = canvas.getContext('2d');
  const motion = matchMedia('(prefers-reduced-motion: reduce)');
  let reduced = motion.matches;
  let mask = null;
  let w = 0; let h = 0; let d = 1;
  // the view: device pixels per degree and the lon/lat at the centre of the canvas
  let S = 1; let CLON = 0; let CLAT = 0;
  let dots = [];
  let layers = [];
  let arcs = [];
  let pulses = [];
  let waves = [];
  let lastSpawn = 0;
  let lastFrame = 0;
  let f = 1; // frame time / 16.7 ms, capped: motion is time-based
  let running = false;

  const isLand = (lon, lat) => {
    if (!mask) return false;
    const px = Math.floor((lon + 180) * 2);
    const py = Math.floor((LAT0 - lat) * 2);
    if (px < 0 || py < 0 || px >= MW || py >= MH) return false;
    return mask[(py * MW + px) * 4 + 3] > 128;
  };

  // [lon, lat] <-> device pixels
  const proj = (lon, lat) => [w / 2 + (lon - CLON) * S, h / 2 + (CLAT - lat) * S];
  const unproj = (x, y) => [CLON + (x - w / 2) / S, CLAT - (y - h / 2) / S];

  function setView() {
    if (h > w) {
      const v = PORTRAIT;
      S = Math.max(w / (v.lon1 - v.lon0), h / (v.lat0 - v.lat1));
      const hw = w / 2 / S; const hh = h / 2 / S; // half the view, in degrees
      const clamp = (x, lo, hi) => (lo > hi ? (lo + hi) / 2 : Math.min(Math.max(x, lo), hi));
      CLON = clamp(v.focus[0], v.lon0 + hw, v.lon1 - hw);
      CLAT = clamp(v.focus[1], v.lat1 + hh, v.lat0 - hh);
    } else {
      // the whole world (lon -180..180, lat LAT0..LAT0-LATS), cover-fit and centred
      S = Math.max(w / 360, h / LATS);
      CLON = 0;
      CLAT = LAT0 - LATS / 2;
    }
  }

  // A point off the canvas moved to the canvas edge, on the line from the search box towards
  // it (a comet from a country outside the view starts at the edge in that direction).
  function toEdge(p) {
    if (p[0] >= 0 && p[0] <= w && p[1] >= 0 && p[1] <= h) return p;
    const H = home();
    const cx = Math.min(Math.max(H[0], 0), w);
    const cy = Math.min(Math.max(H[1], 0), h);
    const dx = p[0] - cx; const dy = p[1] - cy;
    const kx = dx > 0 ? (w - cx) / dx : dx < 0 ? -cx / dx : Infinity;
    const ky = dy > 0 ? (h - cy) / dy : dy < 0 ? -cy / dy : Infinity;
    const k = Math.min(kx, ky, 1);
    return [cx + dx * k, cy + dy * k];
  }

  function resize() {
    d = Math.min(window.devicePixelRatio || 1, 2);
    const cw = window.innerWidth;
    w = canvas.width = Math.round(cw * d);
    h = canvas.height = Math.round(window.innerHeight * d);
    setView();
    dots = [];
    const step = (cw < NARROW ? 10 : 7) * d; // about half the dots on narrow screens
    if (mask) {
      for (let yy = step / 2; yy < h; yy += step) {
        for (let xx = step / 2; xx < w; xx += step) {
          const [lon, lat] = unproj(xx, yy);
          if (isLand(lon, lat)) {
            dots.push([xx, yy, Math.floor(Math.random() * GROUPS)]);
          }
        }
      }
    }
    buildLayers(cw, window.innerHeight);
    // small comets would point at stale positions: drop them (settling their promises)
    for (const a of arcs) if (!a.big && a.done && !a.hit) a.done();
    arcs = arcs.filter((a) => a.big);
    if (reduced) draw(0);
  }

  const randomDot = () => (dots.length
    ? dots[Math.floor(Math.random() * dots.length)]
    : [w * Math.random(), h * Math.random()]);

  function home() {
    const el = opts.target && opts.target();
    if (!el) return [w / 2, h / 2];
    const b = el.getBoundingClientRect();
    return [(b.left + b.width / 2) * d, (b.top + b.height / 2) * d];
  }

  function spawn(from, big, done) {
    const p = from || randomDot();
    let t = big ? null : randomDot();
    if (t && Math.hypot(t[0] - p[0], t[1] - p[1]) < w * 0.12) t = randomDot();
    arcs.push({
      s: p, d: t, t: 0, big: !!big, done,
      sp: big ? 0.013 : 0.007 + Math.random() * 0.009,
    });
  }

  // Spawning lives in the animation loop (paused while the tab is hidden) and is capped, so
  // nothing piles up when the page comes back.
  function tick(now) {
    if (now - lastSpawn < 260) return;
    lastSpawn = now;
    const max = window.innerWidth < NARROW ? 16 : 40;
    const small = arcs.reduce((n, a) => n + (a.big ? 0 : 1), 0);
    if (small < max) spawn();
    if (small + 1 < max && Math.random() < 0.5) spawn();
  }

  // The dots are pre-rendered in GROUPS layers (random dots each) that twinkle out of phase:
  // a few full-screen blits per frame instead of thousands of arcs. Only the dots a wave
  // lights up are drawn one by one, on top.
  function buildLayers(cssW, cssH) {
    const ls = Math.max(0.75, Math.min(d, Math.sqrt(LAYER_PX / Math.max(1, cssW * cssH))));
    const lw = Math.max(1, Math.round(cssW * ls));
    const lh = Math.max(1, Math.round(cssH * ls));
    const k = ls / d;
    const old = layers;
    layers = [];
    for (let g = 0; g < GROUPS; g++) {
      const layer = old[g] || document.createElement('canvas');
      layer.width = lw;
      layer.height = lh;
      const lx = layer.getContext('2d');
      lx.fillStyle = `rgb(${CYAN})`;
      lx.beginPath();
      for (const p of dots) {
        if (p[2] !== g) continue;
        const x = p[0] * k; const y = p[1] * k; const r = 1.25 * ls;
        lx.moveTo(x + r, y);
        lx.arc(x, y, r, 0, 6.2832);
      }
      lx.fill();
      layers.push(layer);
    }
  }

  function drawDots(tm) {
    for (let g = 0; g < layers.length; g++) {
      ctx.globalAlpha = reduced ? 0.4 : 0.34 + 0.2 * Math.sin(tm * (1 + g * 0.09) + g * 1.9);
      ctx.drawImage(layers[g], 0, 0, w, h);
    }
    ctx.globalAlpha = 1;
    if (!waves.length) return;
    const band = 26 * d;
    for (const p of dots) {
      let boost = 0;
      let col = CYAN;
      for (const wv of waves) {
        const dd = Math.abs(Math.hypot(p[0] - wv.x, p[1] - wv.y) - wv.r);
        if (dd < band && 1 - dd / band > boost) { boost = 1 - dd / band; col = wv.col; }
      }
      if (boost <= 0) continue;
      ctx.fillStyle = `rgba(${col},${Math.min(1, 0.4 + boost)})`;
      ctx.beginPath();
      ctx.arc(p[0], p[1], (1.25 + boost * 1.6) * d, 0, 7);
      ctx.fill();
    }
  }

  function drawWaves(diag) {
    for (const wv of waves) {
      ctx.beginPath();
      ctx.lineWidth = 1.5 * d;
      ctx.strokeStyle = `rgba(${wv.col},${Math.max(0, 0.5 - (wv.r / diag) * 0.5)})`;
      ctx.arc(wv.x, wv.y, wv.r, 0, 7);
      ctx.stroke();
    }
  }

  function drawArcs() {
    const H = home();
    for (const a of arcs) {
      a.t += a.sp * f;
      const T = a.d || H;
      const e = Math.min(a.t, 1);
      const mx = (a.s[0] + T[0]) / 2;
      const my = Math.min(a.s[1], T[1]) - Math.hypot(T[0] - a.s[0], T[1] - a.s[1]) * 0.35;
      const N = 48;
      const pts = [];
      for (let k = 0; k <= N * e; k++) {
        const u = k / N;
        pts.push([
          (1 - u) * (1 - u) * a.s[0] + 2 * (1 - u) * u * mx + u * u * T[0],
          (1 - u) * (1 - u) * a.s[1] + 2 * (1 - u) * u * my + u * u * T[1],
        ]);
      }
      const fade = a.t > 1 ? Math.max(0, 1 - (a.t - 1) * 2.5) : 1;
      if (pts.length > 1) {
        // faint full trail
        ctx.beginPath();
        ctx.lineWidth = (a.big ? 2 : 0.8) * d;
        ctx.strokeStyle = `rgba(255,56,96,${(a.big ? 0.6 : 0.3) * fade})`;
        pts.forEach((p, i) => (i ? ctx.lineTo(p[0], p[1]) : ctx.moveTo(p[0], p[1])));
        ctx.stroke();
      }
      // bright comet head
      if (e < 1 && pts.length > 1) {
        const k0 = Math.max(0, pts.length - 10);
        ctx.beginPath();
        ctx.lineWidth = (a.big ? 3.5 : 1.6) * d;
        ctx.strokeStyle = a.big ? '#ff5c7d' : 'rgba(255,90,120,.95)';
        ctx.shadowColor = '#ff3860';
        ctx.shadowBlur = 12 * d;
        for (let i = k0; i < pts.length; i++) {
          if (i === k0) ctx.moveTo(pts[i][0], pts[i][1]); else ctx.lineTo(pts[i][0], pts[i][1]);
        }
        ctx.stroke();
        ctx.shadowBlur = 0;
        const hp = pts[pts.length - 1];
        ctx.beginPath();
        ctx.fillStyle = '#ffe0e7';
        ctx.arc(hp[0], hp[1], (a.big ? 4 : 2) * d, 0, 7);
        ctx.fill();
      }
      if (a.t >= 1 && !a.hit) {
        a.hit = true;
        pulses.push({ r: 0, big: a.big, at: T });
        if (a.done) a.done();
      }
    }
    arcs = arcs.filter((a) => a.t < 1.6);
  }

  function drawPulses() {
    for (const p of pulses) {
      const max = (p.big ? 220 : 22) * d;
      p.r += (p.big ? 3 : 0.9) * d * f;
      ctx.beginPath();
      ctx.lineWidth = (p.big ? 3 : 1.2) * d;
      ctx.strokeStyle = `rgba(${CYAN},${Math.max(0, 1 - p.r / max)})`;
      ctx.arc(p.at[0], p.at[1], p.r, 0, 7);
      ctx.stroke();
    }
    pulses = pulses.filter((p) => p.r < (p.big ? 220 : 22) * d);
  }

  function draw(now) {
    ctx.fillStyle = '#020812';
    ctx.fillRect(0, 0, w, h);
    const diag = Math.hypot(w, h);
    for (const wv of waves) wv.r += wv.v * d * f;
    waves = waves.filter((wv) => wv.r < diag);
    drawDots(now / 900);
    if (reduced) return;
    drawWaves(diag);
    drawArcs();
    drawPulses();
  }

  function loop(now) {
    if (reduced) { running = false; return; }
    f = lastFrame ? Math.min(now - lastFrame, 50) / (1000 / 60) : 1;
    lastFrame = now;
    tick(now);
    draw(now);
    requestAnimationFrame(loop);
  }

  function start() {
    if (reduced) {
      arcs.forEach((a) => a.done && !a.hit && a.done());
      arcs = []; pulses = []; waves = [];
      draw(0);
    } else if (!running) {
      running = true;
      lastFrame = 0;
      requestAnimationFrame(loop);
    }
  }

  motion.addEventListener('change', () => { reduced = motion.matches; start(); });
  let resizeTimer = 0; // debounced: phones fire resize while the address bar slides
  window.addEventListener('resize', () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(resize, 150);
  });
  resize();
  start();
  loadMask()
    .then((m) => { mask = m; resize(); })
    .catch((err) => console.warn('map: no land shape', err));

  return {
    /**
     * A comet from [lon, lat] (or a random land point when null; a point outside the view
     * starts at the canvas edge in its direction). A big comet flies to the search box; a
     * small one to another random land point. Resolves on impact (at once with reduced
     * motion).
     */
    comet(fromLonLat, big) {
      if (reduced) return Promise.resolve();
      const from = fromLonLat ? toEdge(proj(fromLonLat[0], fromLonLat[1])) : null;
      return new Promise((resolve) => spawn(from, big, resolve));
    },
    /** An expanding ring from (x, y) that lights the dots it crosses; rgb "r,g,b" or [r,g,b]. */
    wave(x, y, rgb) {
      if (reduced) return;
      waves.push({ x: x * d, y: y * d, r: 0, v: 14, col: rgbText(rgb) });
    },
    /** [lon, lat] to CSS pixels on the screen. */
    project(lon, lat) {
      const [px, py] = proj(lon, lat);
      return [px / d, py / d];
    },
    get reduced() { return reduced; },
  };
}

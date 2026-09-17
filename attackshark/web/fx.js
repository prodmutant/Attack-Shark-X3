/* Ambient particle layer.

   One canvas behind the UI, one rAF loop, a fixed pool per preset. Pauses when
   the tab is hidden and turns itself off for prefers-reduced-motion, so it
   costs nothing when nobody is looking at it. */
'use strict';

const FX = (() => {
  const canvas = document.getElementById('fx');
  if (!canvas) return { set() {}, presets: [] };
  const ctx = canvas.getContext('2d', { alpha: true });

  let W = 0, H = 0, dpr = 1;
  let parts = [];
  let preset = 'none';
  let raf = null;
  let last = 0;

  function resize() {
    dpr = Math.min(window.devicePixelRatio || 1, 2);
    W = canvas.clientWidth;
    H = canvas.clientHeight;
    canvas.width = Math.max(1, Math.round(W * dpr));
    canvas.height = Math.max(1, Math.round(H * dpr));
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  }
  window.addEventListener('resize', resize);

  const rnd = (a, b) => a + Math.random() * (b - a);

  /* Each preset: how many, how to make one, how to move it, how to draw it.
     `reset` is called when a particle leaves the viewport, so the pool is
     allocated once and never grows. */
  const PRESETS = {
    none: null,

    sakura: {
      count: () => Math.round(Math.min(70, W / 22)),
      make() {
        return {
          x: rnd(-40, W), y: rnd(-H, H), s: rnd(5, 11),
          vy: rnd(14, 34), sway: rnd(14, 40), phase: rnd(0, Math.PI * 2),
          spin: rnd(-1.4, 1.4), rot: rnd(0, Math.PI * 2),
          a: rnd(0.35, 0.8), hue: rnd(330, 350)
        };
      },
      step(p, dt) {
        p.phase += dt * 1.1;
        p.y += p.vy * dt;
        p.x += Math.sin(p.phase) * p.sway * dt;
        p.rot += p.spin * dt;
        if (p.y - p.s > H) { p.y = -p.s * 2; p.x = rnd(-40, W); }
      },
      draw(p) {
        ctx.save();
        ctx.translate(p.x, p.y);
        ctx.rotate(p.rot);
        ctx.globalAlpha = p.a;
        ctx.fillStyle = `hsl(${p.hue} 72% 78%)`;
        // a petal: two arcs meeting at a point
        ctx.beginPath();
        ctx.moveTo(0, -p.s);
        ctx.quadraticCurveTo(p.s * 0.9, -p.s * 0.2, 0, p.s);
        ctx.quadraticCurveTo(-p.s * 0.9, -p.s * 0.2, 0, -p.s);
        ctx.fill();
        ctx.restore();
      }
    },

    acid: {
      count: () => Math.round(Math.min(150, W / 9)),
      make() {
        return {
          x: rnd(0, W), y: rnd(-H, H), len: rnd(10, 26),
          vy: rnd(420, 900), drift: rnd(-40, -10), a: rnd(0.16, 0.5)
        };
      },
      step(p, dt) {
        p.y += p.vy * dt;
        p.x += p.drift * dt;
        if (p.y - p.len > H) { p.y = rnd(-60, -10); p.x = rnd(0, W + 60); }
      },
      draw(p) {
        ctx.globalAlpha = p.a;
        ctx.strokeStyle = '#7dff5a';
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.moveTo(p.x, p.y);
        ctx.lineTo(p.x - p.drift * 0.02, p.y - p.len);
        ctx.stroke();
      }
    },

    embers: {
      count: () => Math.round(Math.min(90, W / 18)),
      make() {
        return {
          x: rnd(0, W), y: rnd(0, H * 1.4), s: rnd(0.8, 2.4),
          vy: rnd(-46, -14), sway: rnd(8, 26), phase: rnd(0, Math.PI * 2),
          a: rnd(0.25, 0.85), hue: rnd(8, 38)
        };
      },
      step(p, dt) {
        p.phase += dt * 1.6;
        p.y += p.vy * dt;
        p.x += Math.sin(p.phase) * p.sway * dt;
        p.a -= dt * 0.06;
        if (p.y + p.s < 0 || p.a <= 0.02) {
          p.y = H + rnd(4, 60); p.x = rnd(0, W); p.a = rnd(0.35, 0.9);
        }
      },
      draw(p) {
        ctx.globalAlpha = Math.max(0, p.a);
        ctx.fillStyle = `hsl(${p.hue} 95% 60%)`;
        ctx.beginPath();
        ctx.arc(p.x, p.y, p.s, 0, Math.PI * 2);
        ctx.fill();
      }
    },

    snow: {
      count: () => Math.round(Math.min(110, W / 14)),
      make() {
        return {
          x: rnd(0, W), y: rnd(-H, H), s: rnd(1, 3),
          vy: rnd(18, 52), sway: rnd(8, 28), phase: rnd(0, Math.PI * 2),
          a: rnd(0.25, 0.7)
        };
      },
      step(p, dt) {
        p.phase += dt * 0.9;
        p.y += p.vy * dt;
        p.x += Math.sin(p.phase) * p.sway * dt;
        if (p.y - p.s > H) { p.y = -4; p.x = rnd(0, W); }
      },
      draw(p) {
        ctx.globalAlpha = p.a;
        ctx.fillStyle = '#ffffff';
        ctx.beginPath();
        ctx.arc(p.x, p.y, p.s, 0, Math.PI * 2);
        ctx.fill();
      }
    },

    /* drifting motes that pick up whatever the theme's accent is */
    motes: {
      count: () => Math.round(Math.min(80, W / 20)),
      make() {
        return {
          x: rnd(0, W), y: rnd(0, H), s: rnd(0.7, 2.2),
          vx: rnd(-11, 11), vy: rnd(-9, 9), a: rnd(0.12, 0.5)
        };
      },
      step(p, dt) {
        p.x += p.vx * dt; p.y += p.vy * dt;
        if (p.x < -6) p.x = W + 6; else if (p.x > W + 6) p.x = -6;
        if (p.y < -6) p.y = H + 6; else if (p.y > H + 6) p.y = -6;
      },
      draw(p) {
        ctx.globalAlpha = p.a;
        ctx.fillStyle = accent();
        ctx.beginPath();
        ctx.arc(p.x, p.y, p.s, 0, Math.PI * 2);
        ctx.fill();
      }
    }
  };

  function accent() {
    return getComputedStyle(document.documentElement)
      .getPropertyValue('--accent').trim() || '#888';
  }

  function build() {
    const def = PRESETS[preset];
    parts = [];
    if (!def) return;
    const n = def.count();
    for (let i = 0; i < n; i++) parts.push(def.make());
  }

  function frame(t) {
    raf = requestAnimationFrame(frame);
    const def = PRESETS[preset];
    if (!def) { ctx.clearRect(0, 0, W, H); return; }
    const dt = Math.min(0.05, (t - last) / 1000 || 0.016);
    last = t;
    ctx.clearRect(0, 0, W, H);
    for (const p of parts) { def.step(p, dt); def.draw(p); }
    ctx.globalAlpha = 1;
  }

  function start() {
    if (raf || preset === 'none') return;
    last = performance.now();
    raf = requestAnimationFrame(frame);
  }
  function stop() {
    if (raf) cancelAnimationFrame(raf);
    raf = null;
    ctx.clearRect(0, 0, W, H);
  }

  document.addEventListener('visibilitychange', () => {
    document.hidden ? stop() : start();
  });

  return {
    presets: ['none', 'sakura', 'acid', 'embers', 'snow', 'motes'],
    labels: {
      none: 'off', sakura: 'cherry blossom', acid: 'acid rain',
      embers: 'embers', snow: 'snow', motes: 'motes'
    },
    set(name) {
      preset = PRESETS[name] !== undefined ? name : 'none';
      resize();
      build();
      preset === 'none' ? stop() : start();
    },
    current() { return preset; }
  };
})();

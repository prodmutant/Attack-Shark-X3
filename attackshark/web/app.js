/* PRODMUTANT X3 driver - front end.
   All device knowledge comes from /api/state's catalog, so adding a protocol
   feature means adding a field server-side plus a control here, nothing more. */
'use strict';

let S = null;          // last snapshot {state, packets, device, catalog, button_labels}
let assignBtn = null;  // button number currently being edited
let chordValue = null;

const $ = (id) => document.getElementById(id);
const el = (tag, cls, text) => {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text != null) n.textContent = text;
  return n;
};

function toast(msg, ok) {
  const t = $('toast');
  t.textContent = msg;
  t.className = 'toast show' + (ok ? ' ok' : '');
  clearTimeout(toast._t);
  toast._t = setTimeout(() => { t.className = 'toast'; }, 3200);
}

async function api(path, body) {
  const res = await fetch(path, body === undefined ? {} : {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body)
  });
  const data = await res.json().catch(() => ({ error: 'bad response' }));
  if (!res.ok) throw new Error(data.error || res.statusText);
  return data;
}

/* patch queue: a slider drag must not overlap HID writes */
let pending = null, inflight = false;
async function patch(p) {
  pending = Object.assign(pending || {}, p);
  if (inflight) return;
  inflight = true;
  while (pending) {
    const body = pending; pending = null;
    try {
      render(await api('/api/set', body));
    } catch (e) {
      toast(e.message);
      try { render(await api('/api/state')); } catch (_) { /* keep last view */ }
    }
  }
  inflight = false;
}

/* ------------------------------------------------------------------ render */
function render(snap) {
  S = snap;
  const st = snap.state, dev = snap.device, cat = snap.catalog;

  const link = $('link');
  link.className = 'link ' + (dev.connected ? 'on' : 'off');
  $('linktext').textContent = dev.connected ? dev.product : 'not connected';
  $('linkmeta').textContent = dev.connected
    ? `${dev.link} · ${dev.vid}:${dev.pid} · fw ${dev.version}` : '';
  document.body.classList.toggle('disconnected', !dev.connected);
  $('logo').classList.toggle('round', !!cat.logo_round);

  renderBattery(snap.battery);
  renderButtons(snap);
  renderDpi(st, cat);
  segment($('polling'), cat.polling_rates.map(r => [r + ' Hz', r]),
          st.polling_hz, v => patch({ polling_hz: v }));
  segment($('lod'), [['1 mm', 1], ['2 mm', 2]], st.lod_mm,
          v => patch({ lod_mm: v }), () => false);
  for (const f of ['motion_sync', 'ripple', 'angle_snap']) {
    segment($(f), [['off', false], ['on', true]], !!st[f],
            v => patch({ [f]: v }), v => v === true);
  }
  slider('sleep_min', st.sleep_min, v => v.toFixed(1) + ' min');
  slider('deep_sleep_min', st.deep_sleep_min, v => v + ' min');
  slider('key_response_ms', st.key_response_ms, v => v + ' ms');

  if (window.renderMacros) window.renderMacros(snap);
  // a #macro deep link can only be honoured once there is a snapshot to edit
  if (!openedFromHash && location.hash.startsWith('#macro') && window.openMacroFromHash) {
    openedFromHash = true;
    window.openMacroFromHash();
  }
}
let openedFromHash = false;

function renderBattery(b) {
  const host = $('batt'), fill = $('battfill'), pct = $('battpct');
  if (!b) {
    host.className = 'batt unknown';
    fill.style.width = '0%';
    pct.textContent = '—';
    host.title = 'waiting for the mouse to report status';
    return;
  }
  if (b.charging) {
    /* the device is reporting bus voltage, not the cell, so there is no state
       of charge to show - say charging rather than invent 100% */
    host.className = 'batt charging';
    fill.style.width = '100%';
    pct.textContent = 'chg';
    host.title = b.volts.toFixed(2) + ' V on the bus (raw ' + b.raw + ')'
      + ' - the cable is in, so the cell level is not being reported';
    return;
  }
  if (typeof b.percent !== 'number') {
    host.className = 'batt unknown';
    fill.style.width = '0%';
    pct.textContent = '—';
    host.title = 'status report carried no usable level';
    return;
  }
  /* The device reports a cell voltage, not a percentage; the figure shown is
     that voltage mapped through a Li-ion curve. The voltage is the measured
     part, so the tooltip carries it. */
  host.className = 'batt' + (b.percent <= 20 ? ' low' : '');
  fill.style.width = Math.max(0, Math.min(100, b.percent)) + '%';
  pct.textContent = b.percent + '%';
  host.title = b.volts.toFixed(2) + ' V measured (raw ' + b.raw + ')'
    + ' - percentage is derived from a Li-ion discharge curve'
    + (b.age != null ? ', read ' + Math.round(b.age) + 's ago' : '');
}

function renderButtons(snap) {
  const list = $('btnlist');
  list.textContent = '';
  for (const n of snap.catalog.buttons) {
    const li = el('li');
    li.dataset.btn = n;
    li.append(el('i', 'n', n), el('span', 'a', snap.button_labels[n] || '—'));
    li.onclick = () => openAssign(n);
    li.onmouseenter = () => highlight(n, true);
    li.onmouseleave = () => highlight(n, false);
    list.append(li);
  }
  document.querySelectorAll('#mousemap .hit').forEach(g => {
    g.onclick = () => openAssign(+g.dataset.btn);
    g.onmouseenter = () => highlight(+g.dataset.btn, true);
    g.onmouseleave = () => highlight(+g.dataset.btn, false);
  });
}

function highlight(n, on) {
  document.querySelectorAll(`#mousemap .hit[data-btn="${n}"]`)
    .forEach(g => g.classList.toggle('sel', on));
  const labels = document.querySelectorAll('#mousemap .lbl');
  if (labels[n - 1]) labels[n - 1].classList.toggle('on', on);
  document.querySelectorAll(`#btnlist li[data-btn="${n}"]`)
    .forEach(li => li.classList.toggle('sel', on));
}

function renderDpi(st, cat) {
  const body = $('dpibody');
  body.textContent = '';
  st.dpi.forEach((dpi, i) => {
    const tr = el('tr');
    tr.append(el('td', null, i + 1));

    const num = el('input');
    num.type = 'number';
    num.min = cat.dpi.min; num.max = cat.dpi.max; num.step = cat.dpi.step;
    num.value = dpi;
    commitOn(num, () => setDpi(i, +num.value));
    const tdn = el('td'); tdn.append(num); tr.append(tdn);

    const rng = el('input');
    rng.type = 'range';
    rng.min = cat.dpi.min; rng.max = cat.dpi.max; rng.step = cat.dpi.step;
    rng.value = dpi;
    rng.oninput = () => { num.value = rng.value; };
    commitOn(rng, () => setDpi(i, +rng.value));
    const tdr = el('td', 'rng'); tdr.append(rng); tr.append(tdr);

    const col = el('input');
    col.type = 'color';
    col.value = rgbHex(st.colors[i] || [255, 255, 255]);
    commitOn(col, () => {
      const colors = st.colors.map(c => c.slice());
      colors[i] = hexRgb(col.value);
      patch({ colors });
    });
    const tdc = el('td'); tdc.append(col); tr.append(tdc);

    const act = el('button', 'radio' + (st.active_stage === i ? ' on' : ''));
    act.type = 'button';
    act.title = 'Make stage ' + (i + 1) + ' active';
    act.onclick = () => patch({ active_stage: i });
    const tda = el('td', 'act'); tda.append(act); tr.append(tda);

    body.append(tr);
  });
  $('dpihint').textContent =
    `${st.dpi.length} of ${cat.dpi.slots} stages · ${cat.dpi.min}–${cat.dpi.max} in steps of ${cat.dpi.step}`;
}

function setDpi(i, value) {
  const step = S.catalog.dpi.step;
  value = Math.min(S.catalog.dpi.max,
          Math.max(S.catalog.dpi.min, Math.round(value / step) * step));
  if (S.state.dpi[i] === value) return;      // nothing to write
  const dpi = S.state.dpi.slice();
  dpi[i] = value;
  patch({ dpi });
}

/* `hot` decides which selected value earns the accent colour: an engaged
   setting should stand out, an "off" one should not read as an alarm. */
function segment(host, options, current, onPick, hot) {
  host.textContent = '';
  hot = hot || (() => true);
  for (const [label, value] of options) {
    const sel = value === current;
    const b = el('button', sel ? (hot(value) ? 'on hot' : 'on') : '', label);
    b.type = 'button';
    b.onclick = () => onPick(value);
    host.append(b);
  }
}

function slider(id, value, fmt) {
  const input = $(id), out = $(id + '_o');
  if (document.activeElement !== input) input.value = value;
  out.textContent = fmt(+input.value);
  input.oninput = () => { out.textContent = fmt(+input.value); };
  if (!input.dataset.bound) {
    input.dataset.bound = '1';
    commitOn(input, () => patch({ [id]: +input.value }));
  }
}

/* Only commit a control when a human actually drove it.

   Range inputs can emit `change` during layout (seen in headless Chromium with
   a wide min..max), which silently wrote a bogus DPI to the mouse. Requiring a
   real pointer/key gesture first makes a spurious event harmless. */
function commitOn(input, fn) {
  const arm = () => { input.dataset.touched = '1'; };
  input.addEventListener('pointerdown', arm);
  // deliberate keyboard use only - arrows/Home on a control that merely *has*
  // focus must not write to the mouse, which is how settings kept drifting to
  // their minimum values.
  input.addEventListener('keydown', (e) => {
    if (e.key === 'Tab' || e.key === 'Shift') return;
    arm();
  });
  input.addEventListener('change', () => {
    if (input.dataset.touched !== '1') return;
    delete input.dataset.touched;
    fn();
  });
}

const rgbHex = (c) => '#' + c.map(v => v.toString(16).padStart(2, '0')).join('');
const hexRgb = (h) => [1, 3, 5].map(i => parseInt(h.substr(i, 2), 16));

/* ------------------------------------------------------------ assignment */
function openAssign(n) {
  assignBtn = n;
  chordValue = null;
  $('assignno').textContent = n;
  $('chord').value = '';
  $('chordbytes').textContent = '';
  showPane('preset');

  const host = $('presets');
  host.textContent = '';
  const current = S.button_labels[n];
  for (const name of S.catalog.actions) {
    const b = el('button', name === current ? 'on' : '', name.replace(/_/g, ' '));
    b.type = 'button';
    b.onclick = () => {
      host.querySelectorAll('button').forEach(x => x.classList.remove('on'));
      b.classList.add('on');
      chordValue = name;
    };
    host.append(b);
  }
  $('assign').showModal();
}

function showPane(which) {
  document.querySelectorAll('.tab').forEach(t =>
    t.classList.toggle('on', t.dataset.pane === which));
  document.querySelectorAll('.pane').forEach(p =>
    p.classList.toggle('on', p.id === 'pane-' + which));
}

document.querySelectorAll('.tab').forEach(t => {
  t.onclick = () => showPane(t.dataset.pane);
});

/* capture a real key chord rather than making the user type its name */
$('chord').addEventListener('keydown', (e) => {
  e.preventDefault();
  const mods = [];
  if (e.ctrlKey) mods.push('ctrl');
  if (e.shiftKey) mods.push('shift');
  if (e.altKey) mods.push('alt');
  if (e.metaKey) mods.push('win');
  let key = e.key.toLowerCase();
  const map = {
    ' ': 'space', 'arrowup': 'up', 'arrowdown': 'down',
    'arrowleft': 'left', 'arrowright': 'right', 'escape': 'esc',
    'control': null, 'shift': null, 'alt': null, 'meta': null
  };
  if (key in map) key = map[key];
  if (!key) { $('chord').value = mods.join('+') + (mods.length ? '+' : ''); return; }
  if (!S.catalog.keys.includes(key)) {
    $('chordbytes').textContent = `"${key}" is not in the device key table`;
    return;
  }
  chordValue = mods.concat(key).join('+');
  $('chord').value = chordValue;
  $('chordbytes').textContent = 'sent as action 0x11 + modifier mask + HID usage';
});

$('assign').addEventListener('close', () => {
  if ($('assign').returnValue !== 'ok' || !chordValue || assignBtn == null) return;
  patch({ button_set: { [assignBtn]: chordValue } });
});

/* ---------------------------------------------------------------- actions */
$('addstage').onclick = () => {
  const dpi = S.state.dpi.slice();
  if (dpi.length >= S.catalog.dpi.slots) return toast('all 8 stages are in use');
  dpi.push(dpi[dpi.length - 1]);
  patch({ dpi });
};
$('delstage').onclick = () => {
  const dpi = S.state.dpi.slice();
  if (dpi.length <= 1) return toast('at least one stage is required');
  dpi.pop();
  patch({ dpi });
};
$('apply').onclick = async () => {
  try { render(await api('/api/apply', {})); toast('configuration pushed', true); }
  catch (e) { toast(e.message); }
};
$('reset').onclick = async () => {
  if (!confirm('Reset the mouse to factory settings?')) return;
  try { render(await api('/api/reset', {})); toast('factory settings restored', true); }
  catch (e) { toast(e.message); }
};
$('export').onclick = () => {
  const blob = new Blob([JSON.stringify(S.state, null, 2)], { type: 'application/json' });
  const a = el('a');
  a.href = URL.createObjectURL(blob);
  a.download = 'x3-profile.json';
  a.click();
  URL.revokeObjectURL(a.href);
};
$('importbtn').onclick = () => $('importfile').click();
$('importfile').onchange = async (e) => {
  const file = e.target.files[0];
  if (!file) return;
  try {
    patch(JSON.parse(await file.text()));
    toast('profile imported', true);
  } catch (err) { toast('could not read that profile'); }
  e.target.value = '';
};

/* ------------------------------------------------------------ appearance */
const THEMES = [
  ['crimson', 'crimson', '#a51f27'],
  ['ember',   'ember',   '#e01b24'],
  ['venom',   'venom',   '#4ade4a'],
  ['paper',   'paper',   '#b3141c'],
  ['sakura',  'sakura',  '#e8558c'],
  ['citrine', 'citrine', '#c99700'],
];
const THEME_KEY = 'as.theme', FX_KEY = 'as.fx';

function readPref(key, fallback) {
  try { return localStorage.getItem(key) || fallback; } catch (e) { return fallback; }
}
function writePref(key, value) {
  try { localStorage.setItem(key, value); } catch (e) { /* private mode */ }
}

function applyTheme(name) {
  document.documentElement.setAttribute('data-theme', name);
  writePref(THEME_KEY, name);
  paintSwatches();
  // motes read --accent, so rebuild after the tokens change
  if (FX.current && FX.current() === 'motes') FX.set('motes');
}

function applyFx(name) {
  FX.set(name);
  writePref(FX_KEY, name);
  paintSwatches();
}

function paintSwatches() {
  const cur = document.documentElement.getAttribute('data-theme') || 'crimson';
  const host = $('themes');
  if (host) {
    host.textContent = '';
    for (const [id, label, dot] of THEMES) {
      const b = el('button', 'swatch' + (id === cur ? ' on' : ''));
      b.type = 'button';
      const i = el('i'); i.style.background = dot;
      b.append(i, document.createTextNode(label));
      b.onclick = () => applyTheme(id);
      host.append(b);
    }
  }
  const fxHost = $('fxpick');
  if (fxHost) {
    const now = FX.current ? FX.current() : 'none';
    fxHost.textContent = '';
    for (const id of FX.presets) {
      const b = el('button', 'swatch' + (id === now ? ' on' : ''),
                   (FX.labels && FX.labels[id]) || id);
      b.type = 'button';
      b.onclick = () => applyFx(id);
      fxHost.append(b);
    }
  }
}

/* ?theme=sakura&fx=acid overrides the saved choice - handy for previewing */
const _q = new URLSearchParams(location.search);
applyTheme(_q.get('theme') || readPref(THEME_KEY, 'crimson'));
applyFx(_q.get('fx') || readPref(FX_KEY, 'none'));

/* the logo is user supplied; hide the frame until one exists */
$('logo').addEventListener('error', () => { $('logo').style.visibility = 'hidden'; });

/* ------------------------------------------------------------------- boot */
Object.assign(window, { S: null, api, el, toast, segment, render, $ });
Object.defineProperty(window, 'S', { get: () => S, configurable: true });

(async () => {
  try { render(await api('/api/state')); }
  catch (e) { toast('cannot reach the driver service: ' + e.message); }
})();
setInterval(async () => {
  if (inflight || $('assign').open) return;
  try {
    const snap = await api('/api/state');
    const wasBatt = S && S.battery && S.battery.level_raw;
    const nowBatt = snap.battery && snap.battery.level_raw;
    if (snap.device.connected !== (S && S.device.connected) || wasBatt !== nowBatt)
      render(snap);
  } catch (_) { /* server restarting */ }
}, 2500);

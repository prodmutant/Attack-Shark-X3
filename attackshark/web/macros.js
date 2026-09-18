/* Macro list, button bindings and the step editor.

   The editor is deliberately a table of typed rows rather than a scripting
   box: every step is one record the engine executes directly, so what you see
   is exactly what runs. */
'use strict';

(() => {
  const S2 = () => S;                       // snapshot owned by app.js
  let editing = null;                       // macro being edited
  let recording = false;
  let recAt = 0;

  const NEW_STEP = {
    key:   () => ({ t: 'key', key: 'a', down: true }),
    mouse: () => ({ t: 'mouse', button: 'left', down: true }),
    move:  () => ({ t: 'move', dx: 0, dy: 0 }),
    wheel: () => ({ t: 'wheel', delta: 1 }),
    delay: () => ({ t: 'delay', ms: 20, jitter: 0 }),
  };

  /* ------------------------------------------------------------- listing */
  function renderMacros(snap) {
    const host = $('maclist');
    if (!host) return;
    host.textContent = '';
    const macros = snap.macros || [];
    if (!macros.length) {
      const li = el('li');
      li.append(el('span', 'hint', 'no macros yet'));
      host.append(li);
    }
    for (const m of macros) {
      const li = el('li');
      li.append(el('span', 'nm', m.name));
      li.append(el('span', 'tag ' + m.target, m.target));
      li.append(el('span', 'meta',
        `${m.steps} step${m.steps === 1 ? '' : 's'} · ${m.duration_ms}ms`));

      const test = el('button', 'mini', 'Test');
      test.type = 'button';
      test.title = 'play once, right now';
      test.onclick = () => post('/api/macro/test', { id: m.id },
                               `played ${m.name}`);

      const edit = el('button', 'mini', 'Edit');
      edit.type = 'button';
      edit.onclick = () => openEditor(m.id);

      const flash = el('button', 'mini', 'Flash');
      flash.type = 'button';
      flash.disabled = !m.device_ok;
      flash.title = m.device_note;
      flash.onclick = () => post('/api/macro/flash', { id: m.id },
                                `${m.name} written to the mouse`);

      const del = el('button', 'mini danger', 'Delete');
      del.type = 'button';
      del.onclick = () => {
        if (confirm(`Delete "${m.name}"?`)) post('/api/macro/delete', { id: m.id });
      };
      li.append(test, edit, flash, del);
      host.append(li);
    }

    // engine switch
    const eng = snap.engine || {};
    segment($('engine'), [['off', false], ['on', true]], !!eng.active,
            v => post('/api/macro/engine', { on: v }), v => v === true);
    // Which backend is live decides what the output actually is, so say so
    // rather than leaving it to be discovered.
    let hint;
    if (!eng.active) {
      hint = eng.error || 'host macros only fire while this is on';
    } else if (eng.backend === 'kernel') {
      const d = eng.driver || {};
      hint = d.connected
        ? 'asxfilter: movement comes from the mouse stack, buttons swallowed in the driver — Esc stops a running macro'
        : 'asxfilter loaded but not attached to the mouse — ' + (eng.driver_note || 'is the X3 connected?');
    } else {
      hint = 'SendInput fallback: output is flagged injected. ' +
             (eng.driver_note || 'install the filter driver for real reports') +
             ' — Esc stops a running macro';
    }
    $('enginehint').textContent = hint;

    renderBinds(snap);
  }

  function renderBinds(snap) {
    const host = $('binds');
    if (!host) return;
    host.textContent = '';
    const binds = (snap.state.macro_bindings) || {};
    const macros = snap.macros || [];
    for (const btn of snap.catalog.macro.buttons) {
      const tr = el('tr');
      tr.append(el('td', null, btn));

      const sel = el('select');
      const none = el('option', null, '— none —');
      none.value = '';
      sel.append(none);
      for (const m of macros) {
        const o = el('option', null, m.name);
        o.value = m.id;
        if ((binds[btn] || {}).id === m.id) o.selected = true;
        sel.append(o);
      }
      const tdS = el('td'); tdS.append(sel); tr.append(tdS);

      const pass = el('input');
      pass.type = 'checkbox';
      pass.checked = !!(binds[btn] || {}).passthrough;
      const lab = el('label');
      lab.append(pass, document.createTextNode('also send the normal click'));
      const tdP = el('td'); tdP.append(lab); tr.append(tdP);

      const send = () => post('/api/macro/bind', {
        button: btn, id: sel.value || null, passthrough: pass.checked });
      sel.onchange = send;
      pass.onchange = send;

      host.append(tr);
    }
  }

  async function post(path, body, okMsg) {
    try {
      const r = await api(path, body);
      if (r && r.state) render(r);
      if (okMsg) toast(okMsg, true);
    } catch (e) { toast(e.message); }
  }

  /* -------------------------------------------------------------- editor */
  function openEditor(id) {
    const found = (S2().macros || []).find(m => m.id === id);
    const raw = (S2().state.macros || []).find(m => m.id === id);
    editing = raw ? JSON.parse(JSON.stringify(raw))
                  : { name: 'new macro', steps: [], repeat: 'once', count: 1, speed: 1 };
    if (found) editing.id = found.id;
    $('mname').value = editing.name;
    $('mcount').value = editing.count || 1;
    $('mspeed').value = editing.speed || 1;
    paintRepeat();
    paintSteps();
    setRecording(false);
    $('mstatus').textContent = '';
    $('macroedit').showModal();
  }

  function paintRepeat() {
    segment($('mrepeat'),
      S2().catalog.macro.repeat_modes.map(m => [m, m]),
      editing.repeat,
      v => { editing.repeat = v; paintRepeat(); },
      v => v !== 'once');
    $('mcountwrap').style.visibility = editing.repeat === 'count' ? '' : 'hidden';
  }

  function paintSteps() {
    const host = $('msteps');
    host.textContent = '';
    editing.steps.forEach((st, i) => host.append(stepRow(st, i)));
    paintTarget();
  }

  function stepRow(st, i) {
    const row = el('div', 'step');
    row.append(el('span', 'ix', String(i + 1)));
    row.append(el('span', 'kind', st.t));

    // every step type puts its editors in one column, so the rows line up
    // with the header no matter how many fields a type needs
    const detail = el('div', 'detail');
    row.append(detail);
    const put = (node) => detail.append(node);

    if (st.t === 'key') {
      const k = el('select');
      for (const name of S2().catalog.keys) {
        const o = el('option', null, name); o.value = name;
        if (name === st.key) o.selected = true;
        k.append(o);
      }
      k.onchange = () => { st.key = k.value; };
      put(k);
      put(dirSelect(st));
    } else if (st.t === 'mouse') {
      const b = el('select');
      for (const name of S2().catalog.macro.mouse_buttons) {
        const o = el('option', null, name); o.value = name;
        if (name === st.button) o.selected = true;
        b.append(o);
      }
      b.onchange = () => { st.button = b.value; };
      put(b);
      put(dirSelect(st));
    } else if (st.t === 'move') {
      put(numField('dx', st, 'dx'));
      put(numField('dy', st, 'dy'));
    } else if (st.t === 'wheel') {
      put(numField('notches', st, 'delta'));
    } else if (st.t === 'delay') {
      put(numField('ms', st, 'ms'));
      put(numField('± jitter', st, 'jitter'));
    }

    const ctl = el('div', 'ctl');
    const up = el('button', 'x', '↑'); up.type = 'button';
    up.title = 'move up';
    up.onclick = () => move(i, -1);
    const dn = el('button', 'x', '↓'); dn.type = 'button';
    dn.title = 'move down';
    dn.onclick = () => move(i, +1);
    const rm = el('button', 'x', '×'); rm.type = 'button';
    rm.title = 'remove';
    rm.onclick = () => { editing.steps.splice(i, 1); paintSteps(); };
    ctl.append(up, dn, rm);
    row.append(ctl);
    return row;
  }

  /* Mirrors macro.py device_support(): says where this macro will run.
     A device macro needs nothing running afterwards, so it is worth telling
     the user before they save. */
  function targetOf(mac) {
    const kinds = new Set((mac.steps || []).map(s => s.t));
    if (kinds.has('move') || kinds.has('wheel'))
      return ['host', 'runs from this app - movement is not storable on the mouse'];
    if (kinds.has('mouse'))
      return ['host', 'runs from this app - mouse buttons in a device macro are not decoded'];
    if (kinds.has('delay'))
      return ['host', 'runs from this app - the device block has no delay field'];
    if (!kinds.has('key'))
      return ['host', 'nothing the mouse can store yet'];
    if (mac.repeat !== 'once' && mac.repeat !== 'count')
      return ['host', 'runs from this app - hold/toggle is a host behaviour'];
    return ['device', 'runs on the mouse itself - no injection, nothing resident'];
  }

  function paintTarget() {
    const node = $('mtarget');
    if (!node || !editing) return;
    const [where, why] = targetOf(editing);
    node.textContent = where === 'device' ? 'on the mouse' : 'on the host';
    node.className = 'target ' + where;
    node.title = why;
  }

  function dirSelect(st) {
    const d = el('select');
    for (const [label, val] of [['press', true], ['release', false]]) {
      const o = el('option', null, label);
      o.value = val ? '1' : '0';
      if (!!st.down === val) o.selected = true;
      d.append(o);
    }
    d.onchange = () => { st.down = d.value === '1'; };
    return d;
  }

  function numField(label, st, key) {
    const wrap = el('label');
    wrap.style.cssText = 'display:flex;gap:5px;align-items:center;color:inherit';
    wrap.append(el('span', null, label));
    const n = el('input');
    n.type = 'number';
    n.value = st[key];
    n.onchange = () => { st[key] = parseInt(n.value, 10) || 0; };
    wrap.append(n);
    return wrap;
  }

  function move(i, d) {
    const j = i + d;
    if (j < 0 || j >= editing.steps.length) return;
    const [s] = editing.steps.splice(i, 1);
    editing.steps.splice(j, 0, s);
    paintSteps();
  }

  /* ------------------------------------------------------------ recorder */
  function setRecording(on) {
    recording = on;
    $('mrec').classList.toggle('rec', on);
    $('mrec').textContent = on ? 'Stop' : 'Record';
    $('mstatus').textContent = on
      ? 'recording — keys and clicks in this dialog are captured with timing'
      : '';
    recAt = performance.now();
  }

  function gap() {
    const now = performance.now();
    const ms = Math.round(now - recAt);
    recAt = now;
    return ms;
  }

  function pushGap() {
    const ms = gap();
    if (ms > 4) editing.steps.push({ t: 'delay', ms: Math.min(ms, 60000), jitter: 0 });
  }

  function recKey(e, down) {
    if (!recording) return;
    const name = (e.key || '').toLowerCase();
    const map = { ' ': 'space', 'escape': 'esc', 'arrowup': 'up',
                  'arrowdown': 'down', 'arrowleft': 'left', 'arrowright': 'right' };
    const key = map[name] || name;
    if (!S2().catalog.keys.includes(key)) return;
    e.preventDefault();
    pushGap();
    editing.steps.push({ t: 'key', key, down });
    paintSteps();
  }

  document.addEventListener('keydown', e => {
    if (!$('macroedit').open) return;
    if (e.key === 'Escape' && recording) { e.preventDefault(); setRecording(false); return; }
    if (e.repeat) return;
    recKey(e, true);
  }, true);
  document.addEventListener('keyup', e => {
    if ($('macroedit').open) recKey(e, false);
  }, true);

  /* ---------------------------------------------------------------- wire */
  document.querySelectorAll('[data-add]').forEach(b => {
    b.onclick = () => {
      if (!editing) return;
      editing.steps.push(NEW_STEP[b.dataset.add]());
      paintSteps();
      $('msteps').scrollTop = $('msteps').scrollHeight;
    };
  });
  $('mrec').onclick = () => setRecording(!recording);
  $('macnew').onclick = () => openEditor(null);

  $('macroedit').addEventListener('close', async () => {
    setRecording(false);
    if ($('macroedit').returnValue !== 'ok' || !editing) return;
    editing.name = $('mname').value.trim() || 'macro';
    editing.count = parseInt($('mcount').value, 10) || 1;
    editing.speed = parseFloat($('mspeed').value) || 1;
    await post('/api/macro/save', { macro: editing }, 'macro saved');
    editing = null;
  });

  /* Deep link: #edit opens a new macro, #edit=<id> opens that one. Kept
     distinct from the #macros page route, and it is how the editor gets
     screenshotted without a human to click. */
  window.openMacroEditor = openEditor;
  window.addEventListener('hashchange', openFromHash);
  function openFromHash() {
    const h = decodeURIComponent(location.hash || '');
    if (!h.startsWith('#edit')) return;
    const id = h.includes('=') ? h.slice(h.indexOf('=') + 1) : null;
    if (S2()) openEditor(id);
  }
  window.openMacroFromHash = openFromHash;

  window.renderMacros = renderMacros;
})();

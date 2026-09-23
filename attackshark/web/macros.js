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
    call:  () => ({ t: 'call', id: '', times: 1 }),
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
    const names = snap.catalog.macro.button_names || {};
    for (const btn of snap.catalog.macro.buttons) {
      const tr = el('tr');
      const td = el('td', null, String(btn));
      if (names[btn]) {
        td.textContent = names[btn];
        td.title = 'button ' + btn;
      }
      tr.append(td);

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

      const wheel = String(names[btn] || '').startsWith('wheel');
      const pass = el('input');
      pass.type = 'checkbox';
      pass.checked = !!(binds[btn] || {}).passthrough;
      const lab = el('label');
      lab.append(pass, document.createTextNode(
        wheel ? 'also scroll as normal' : 'also send the normal click'));
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
                  : { name: 'new macro', steps: [], lanes: [],
                      repeat: 'once', count: 1, speed: 1 };
    if (found) editing.id = found.id;
    $('mname').value = editing.name;
    $('mcount').value = editing.count || 1;
    $('mspeed').value = editing.speed || 1;
    paintRepeat();
    paintMode();
    setRecording(false);
    $('mstatus').textContent = '';
    $('macroedit').showModal();
  }

  function paintRepeat() {
    segment($('mrepeat'),
      S2().catalog.macro.repeat_modes.map(m => [m, m]),
      editing.repeat,
      v => { editing.repeat = v; paintRepeat();
             if (Array.isArray(editing.lanes)) paintSpan(); },
      v => v !== 'once');
    $('mcountwrap').style.visibility = editing.repeat === 'count' ? '' : 'hidden';
  }

  /* Two ways to author the same macro, and the macro itself says which one
     it is in. A macro carrying `lanes` was drawn on the timeline and is shown
     there; anything else - recorded, imported, or using a step type lanes
     cannot express - stays in the list until someone converts it. Showing
     both at once would just raise the question of which one wins. */
  function paintMode() {
    const onLanes = Array.isArray(editing.lanes);
    $('macroedit').classList.toggle('lanes', onLanes);
    $('trksection').style.display = onLanes ? '' : 'none';
    $('stepsection').style.display = onLanes ? 'none' : '';
    document.querySelectorAll('[data-add]').forEach(b => {
      b.style.display = onLanes ? 'none' : '';
    });
    $('mrec').style.display = onLanes ? 'none' : '';
    $('mtolanes').style.display = onLanes ? 'none' : '';
    if (onLanes) {
      Track.mount(editing.lanes, S2().macros || [], () => {
        editing.lanes = Track.lanes();
        paintLaneInfo();
      });
      paintLaneWhat();
      paintSpan();
      paintLaneInfo();
    } else {
      paintSteps();
    }
  }

  /* Only a looping macro has anything to pad: a one-shot ends when its last
     press does, and a gap after it would mean nothing. */
  function paintSpan() {
    const loops = editing.repeat === 'hold' || editing.repeat === 'toggle'
      || editing.repeat === 'count';
    $('trkspanwrap').style.display = loops ? '' : 'none';
    $('trkspan').value = editing.span || 0;
  }

  function paintLaneInfo() {
    const lanes = editing.lanes || [];
    const blocks = lanes.reduce((n, l) => n + l.blocks.length, 0);
    let presses = 0;
    let notches = 0;
    for (const l of lanes) {
      const wheel = l.kind === 'wheel';
      for (const b of l.blocks) {
        const rate = b.rate || 1;
        const n = b.type !== 'spam' ? 1
          : wheel ? Math.floor(b.ms / rate) + 1
          : Math.max(0, Math.floor((b.ms - (b.hold || 0)) / rate) + 1);
        if (wheel) notches += n; else presses += n;
      }
    }
    const counts = [`${presses} press${presses === 1 ? '' : 'es'}`];
    if (notches) counts.push(`${notches} notch${notches === 1 ? '' : 'es'}`);
    $('trkinfo').textContent =
      `${lanes.length} lane${lanes.length === 1 ? '' : 's'}, ` +
      `${blocks} block${blocks === 1 ? '' : 's'}, ` + counts.join(', ');
    paintTarget();
  }

  /* The key list arrives alphabetically, which puts an apostrophe first and
     buries WASD in the middle of the letters. Grouping it by what the key is
     for costs nothing and means the keys these macros are actually built from
     are the first thing in the list. */
  const KEY_GROUPS = [
    ['Movement', ['w', 'a', 's', 'd']],
    ['Jump and crouch', ['space', 'c', 'v', 'z', 'x']],
    ['Common', ['e', 'q', 'r', 'f', 'g', 't', 'tab', 'esc', 'enter', 'backspace']],
    ['Arrows', ['up', 'down', 'left', 'right']],
  ];

  function groupKeys(all) {
    const used = new Set();
    const groups = [];
    for (const [label, keys] of KEY_GROUPS) {
      const have = keys.filter(k => all.includes(k));
      have.forEach(k => used.add(k));
      if (have.length) groups.push([label, have]);
    }
    const rest = all.filter(k => !used.has(k));
    const letters = rest.filter(k => /^[a-z]$/.test(k));
    const digits = rest.filter(k => /^[0-9]$/.test(k));
    const fkeys = rest.filter(k => /^f([1-9]|1[0-2])$/.test(k))
      .sort((x, y) => parseInt(x.slice(1), 10) - parseInt(y.slice(1), 10));
    const other = rest.filter(k => !letters.includes(k) && !digits.includes(k)
      && !fkeys.includes(k));
    if (letters.length) groups.push(['Letters', letters]);
    if (digits.length) groups.push(['Numbers', digits]);
    if (fkeys.length) groups.push(['Function', fkeys]);
    if (other.length) groups.push(['Other', other]);
    return groups;
  }

  function paintLaneWhat() {
    const kind = $('trkkind').value;
    const what = $('trkwhat');
    what.textContent = '';
    if (kind === 'key') {
      for (const [label, keys] of groupKeys(S2().catalog.keys)) {
        const g = document.createElement('optgroup');
        g.label = label;
        for (const k of keys) {
          const n = el('option', null, k.length === 1 ? k.toUpperCase() : k);
          n.value = k;
          g.append(n);
        }
        what.append(g);
      }
      what.value = 'w';
      return;
    }
    const opts = kind === 'mouse' ? S2().catalog.macro.mouse_buttons
      : kind === 'wheel'
        ? (S2().catalog.macro.wheel_dirs || ['up', 'down'])
            .map(d => [d, 'scroll ' + d])
      : (S2().macros || []).filter(m => m.id !== editing.id)
          .map(m => [m.id, m.name]);
    for (const o of opts) {
      const [val, label] = Array.isArray(o) ? o : [o, o];
      const n = el('option', null, label);
      n.value = val;
      what.append(n);
    }
  }

  /* Wheel events of one direction, split into runs that turn the same
     distance each. Returns [[notches, [at, ...]], ...] in time order. */
  function wheelRuns(list) {
    const runs = [];
    for (const [at, st] of list) {
      const n = Math.abs(st.delta) || 1;
      const last = runs[runs.length - 1];
      if (last && last[0] === n) last[1].push(at);
      else runs.push([n, [at]]);
    }
    return runs;
  }

  /* An evenly spaced run of three or more notches is a spam block; anything
     else is one tick each, which is what it will look like on the timeline. */
  function wheelBlocks(notches, times) {
    const out = [];
    let i = 0;
    while (i < times.length) {
      let j = i + 1;
      const gap = j < times.length ? times[j] - times[i] : 0;
      while (j < times.length && times[j] - times[j - 1] === gap) j++;
      if (j - i >= 3 && gap > 0) {
        out.push({ type: 'spam', at: times[i], ms: times[j - 1] - times[i],
                   rate: gap, notches });
        i = j;
      } else {
        out.push({ type: 'tick', at: times[i], ms: 20, notches });
        i += 1;
      }
    }
    return out;
  }

  /* steps -> lanes. A run of identical presses at an even spacing is what a
     spam block is, so it is recognised as one rather than becoming a dozen
     rectangles the user would have to merge by hand. */
  function lanesFromSteps(steps) {
    let cursor = 0;
    const evs = [];
    for (const st of steps) {
      const at = (st.at === undefined || st.at === null) ? cursor : st.at;
      if (st.t === 'delay') { if (st.at == null) cursor += st.ms; continue; }
      evs.push([at, st]);
    }
    evs.sort((a, b) => a[0] - b[0]);
    const byLane = new Map();
    for (const [at, st] of evs) {
      const key = st.t === 'key' ? 'key:' + st.key
        : st.t === 'mouse' ? 'mouse:' + st.button
        : st.t === 'wheel' ? 'wheel:' + (st.delta >= 0 ? 'up' : 'down')
        : st.t === 'call' ? 'macro:' + st.id : null;
      if (!key) return null;                       // move: not a lane
      if (!byLane.has(key)) byLane.set(key, []);
      byLane.get(key).push([at, st]);
    }
    const lanes = [];
    for (const [key, list] of byLane) {
      const [kind, what] = [key.slice(0, key.indexOf(':')), key.slice(key.indexOf(':') + 1)];
      const lane = { kind, blocks: [] };
      if (kind === 'key') lane.key = what;
      else if (kind === 'mouse') lane.button = what;
      else if (kind === 'wheel') lane.direction = what;
      else lane.id = what;

      /* A notch has no release to pair with, and two notches of different
         sizes are not the same event repeated, so a wheel lane is grouped by
         how far each one turns before its evenness is looked at. */
      if (kind === 'wheel') {
        for (const [notches, at] of wheelRuns(list))
          lane.blocks.push(...wheelBlocks(notches, at));
        lanes.push(lane);
        continue;
      }

      const pairs = [];
      let open = null;
      for (const [at, st] of list) {
        if (kind === 'macro') { pairs.push([at, 1]); continue; }
        if (st.down) open = at;
        else if (open !== null) { pairs.push([open, at - open]); open = null; }
      }
      if (open !== null) pairs.push([open, 10]);

      let i = 0;
      while (i < pairs.length) {
        let j = i + 1;
        const gap = j < pairs.length ? pairs[j][0] - pairs[i][0] : 0;
        while (j < pairs.length && pairs[j][1] === pairs[i][1]
               && pairs[j][0] - pairs[j - 1][0] === gap) j++;
        const n = j - i;
        if (n >= 3) {
          lane.blocks.push({ type: 'spam', at: pairs[i][0],
                             ms: (n - 1) * gap + pairs[i][1],
                             rate: gap, hold: pairs[i][1] });
        } else {
          for (let k = i; k < j; k++)
            lane.blocks.push({ type: 'hold', at: pairs[k][0], ms: pairs[k][1] });
        }
        i = j;
      }
      lanes.push(lane);
    }
    return lanes;
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
    } else if (st.t === 'call') {
      const m = el('select');
      const none = el('option', null, 'pick a macro'); none.value = '';
      m.append(none);
      for (const other of (S2().macros || [])) {
        if (editing && other.id === editing.id) continue;   // no self-call
        const o = el('option', null, other.name); o.value = other.id;
        if (other.id === st.id) o.selected = true;
        m.append(o);
      }
      m.onchange = () => { st.id = m.value; };
      put(m);
      put(numField('times', st, 'times'));
    }

    /* Every step can be positioned instead of sequenced. Blank means "after
       the one before", which is what a recorded macro wants; a number means
       that many ms from the start of the macro, and the running cursor is
       left alone. That is the only way to say "while": a held key and the
       taps underneath it are steps that overlap, not steps that follow. */
    if (st.t !== 'delay') put(atField(st));

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
    if (Array.isArray(mac.lanes) && mac.lanes.length)
      return ['host', 'runs from this app - lanes overlap, which the mouse cannot'];
    const kinds = new Set((mac.steps || []).map(s => s.t));
    if (kinds.has('call'))
      return ['host', 'runs from this app - a call is expanded as it plays'];
    if ((mac.steps || []).some(s => s.at !== undefined && s.at !== null))
      return ['host', 'runs from this app - overlapping steps are a host behaviour'];
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

  function atField(st) {
    const wrap = el('label');
    wrap.style.cssText = 'display:flex;gap:5px;align-items:center;color:inherit';
    wrap.append(el('span', null, 'at'));
    const n = el('input');
    n.type = 'number';
    n.placeholder = 'seq';
    n.title = 'milliseconds from the start of the macro; blank = in sequence';
    n.value = (st.at === undefined || st.at === null) ? '' : st.at;
    n.onchange = () => {
      const v = n.value.trim();
      if (v === '') delete st.at; else st.at = parseInt(v, 10) || 0;
    };
    wrap.append(n);
    return wrap;
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
  $('trkkind').onchange = paintLaneWhat;
  $('trkadd').onclick = () => {
    const kind = $('trkkind').value;
    const what = $('trkwhat').value;
    if (!what) return toast('nothing to add a lane for');
    const lane = { kind, blocks: [] };
    if (kind === 'key') lane.key = what;
    else if (kind === 'mouse') lane.button = what;
    else if (kind === 'wheel') lane.direction = what;
    else lane.id = what;
    const dup = (editing.lanes || []).some(l => l.kind === kind
      && (l.key || l.button || l.direction || l.id) === what);
    if (dup) return toast(what + ' already has a lane');
    Track.addLane(lane);
  };
  $('trkspan').onchange = () => {
    const v = Math.max(0, Math.min(60000, parseInt($('trkspan').value, 10) || 0));
    editing.span = v || undefined;
    $('trkspan').value = v;
    paintLaneInfo();
  };
  $('trkin').onclick = () => Track.zoom(1.5);
  $('trkout').onclick = () => Track.zoom(1 / 1.5);
  $('mtolanes').onclick = () => {
    const lanes = lanesFromSteps(editing.steps || []);
    if (!lanes) return toast('a move step has no lane yet');
    editing.lanes = lanes;
    paintMode();
  };

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
    if (Array.isArray(editing.lanes)) {
      editing.lanes = Track.lanes();
      delete editing.steps;         // the server derives them from the lanes
    }
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
    let rest = h.includes('=') ? h.slice(h.indexOf('=') + 1) : '';
    let pick = null;
    const bar = rest.indexOf('|');          // #edit=<id>|<lane>.<block>
    if (bar >= 0) { pick = rest.slice(bar + 1); rest = rest.slice(0, bar); }
    if (!S2()) return;
    openEditor(rest || null);
    if (pick && Array.isArray(editing.lanes)) {
      const [li, bi] = pick.split('.').map(n => parseInt(n, 10) || 0);
      Track.select(li, bi);
    }
  }
  window.openMacroFromHash = openFromHash;

  window.renderMacros = renderMacros;
})();

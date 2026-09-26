/* Per-app profiles page and editor.
   A profile is saved server-side; the server watches the foreground window
   and pushes the profile's overrides while one of its programs is in front. */
'use strict';
(() => {
  let editing = null;
  const OVER = [
    // key, label, kind
    ['dpi_value', 'DPI', 'dpi'],
    ['polling_hz', 'Polling rate', 'rate'],
    ['lod_mm', 'Lift-off distance', 'lod'],
    ['motion_sync', 'Motion sync', 'bool'],
    ['ripple', 'Ripple control', 'bool'],
    ['angle_snap', 'Angle snap', 'bool'],
    ['engine_on', 'Macro engine', 'bool'],
  ];

  function describe(p) {
    const s = p.set || {};
    const out = [];
    if (s.dpi_value != null) out.push(`${s.dpi_value} DPI`);
    if (s.polling_hz != null) out.push(`${s.polling_hz} Hz`);
    if (s.lod_mm != null) out.push(`LOD ${s.lod_mm} mm`);
    for (const [k, label] of [['motion_sync', 'motion sync'], ['ripple', 'ripple'],
                              ['angle_snap', 'angle snap'], ['engine_on', 'macros']]) {
      if (s[k] != null) out.push(`${label} ${s[k] ? 'on' : 'off'}`);
    }
    if (p.bindings) out.push(`${Object.keys(p.bindings).length} own binding(s)`);
    return out.join(', ') || 'nothing yet';
  }

  function renderProfiles(snap) {
    const pr = snap.profiles || { list: [] };
    const active = (pr.list || []).find(p => p.id === pr.active);

    const badge = $('profbadge');
    badge.hidden = !active;
    if (active) badge.textContent = 'profile: ' + active.name;

    segment($('profon'), [['off', false], ['on', true]], pr.on !== false,
            async (v) => { try { render(await api('/api/profile/toggle', { on: v })); } catch (e) { toast(e.message); } },
            v => v === true);
    $('profstatus').textContent = (pr.on === false ? 'profiles are off' :
      `in front: ${pr.foreground || '-'} · active: ${active ? active.name : 'none (normal settings)'}`) +
      (pr.error ? ` · last switch failed: ${pr.error}` : '');

    const body = $('profbody');
    body.textContent = '';
    $('proftable').hidden = !pr.list.length;
    for (const p of pr.list) {
      const tr = el('tr');
      const name = el('td', null, p.name);
      if (p.id === pr.active) name.append(' ', el('span', 'tag', 'active'));
      tr.append(name, el('td', null, p.exes.join(', ') || 'no programs'), el('td', null, describe(p)));
      const act = el('td', 'pfacts');
      const e = el('button', 'mini', 'Edit');
      e.type = 'button';
      e.onclick = () => openEditor(p);
      const d = el('button', 'mini danger', 'Delete');
      d.type = 'button';
      d.onclick = async () => {
        if (!confirm(`Delete the profile "${p.name}"?`)) return;
        try { render(await api('/api/profile/delete', { id: p.id })); } catch (err) { toast(err.message); }
      };
      act.append(e, d);
      tr.append(act);
      body.append(tr);
    }
  }

  /* --------------------------------------------------------------- editor */
  function openEditor(p) {
    const snap = window.S;
    editing = JSON.parse(JSON.stringify(p || { name: '', exes: [], set: {}, bindings: null }));
    $('pfname').value = editing.name;
    renderExes();
    loadRunning();

    const host = $('pfover');
    host.textContent = '';
    const st = snap.state;
    for (const [key, label, kind] of OVER) {
      const tr = el('tr');
      const tick = el('input');
      tick.type = 'checkbox';
      tick.checked = editing.set[key] != null;
      const lab = el('label', 'pfinline');
      lab.append(tick, ' ' + label);
      let ctl;
      if (kind === 'dpi') {
        ctl = el('input', 'num');
        ctl.type = 'number'; ctl.min = 50; ctl.max = 26000; ctl.step = 50;
        ctl.value = editing.set[key] ?? st.dpi[st.active_stage];
      } else {
        ctl = el('select');
        const opts = kind === 'rate' ? snap.catalog.polling_rates.map(r => [r + ' Hz', r])
          : kind === 'lod' ? [['1 mm', 1], ['2 mm', 2]] : [['off', false], ['on', true]];
        const cur = editing.set[key] ?? (key === 'dpi_value' ? null : st[key]);
        for (const [t, v] of opts) {
          const o = el('option', null, t);
          o.value = JSON.stringify(v);
          if (JSON.stringify(v) === JSON.stringify(kind === 'bool' ? !!cur : cur)) o.selected = true;
          ctl.append(o);
        }
      }
      ctl.disabled = !tick.checked;
      tick.onchange = () => { ctl.disabled = !tick.checked; };
      tr.dataset.key = key;
      tr.dataset.kind = kind;
      const c1 = el('td'); c1.append(lab);
      const c2 = el('td'); c2.append(ctl);
      tr.append(c1, c2);
      host.append(tr);
    }

    $('pfownbind').checked = !!editing.bindings;
    renderBinds();
    $('pfownbind').onchange = renderBinds;
    $('profedit').showModal();
  }

  function renderBinds() {
    const snap = window.S;
    const on = $('pfownbind').checked;
    $('pfbindtable').hidden = !on;
    const host = $('pfbinds');
    host.textContent = '';
    if (!on) return;
    const base = snap.state.macro_bindings || {};
    const src = editing.bindings || base;
    const names = snap.catalog.macro.button_names || {};
    for (const b of snap.catalog.macro.buttons) {
      const tr = el('tr');
      const sel = el('select');
      sel.dataset.btn = b;
      sel.append(new Option('- none -', ''));
      for (const m of snap.macros || []) {
        const o = new Option(m.name, m.id);
        if ((src[b] || {}).id === m.id) o.selected = true;
        sel.append(o);
      }
      const pt = el('input');
      pt.type = 'checkbox';
      pt.checked = !!(src[b] || {}).passthrough;
      pt.dataset.btn = b;
      const lab = el('label', 'pfinline');
      lab.append(pt, ' also send the normal click');
      const c0 = el('td', null, names[b] || String(b));
      const c1 = el('td'); c1.append(sel);
      const c2 = el('td'); c2.append(lab);
      tr.append(c0, c1, c2);
      host.append(tr);
    }
  }

  function renderExes() {
    const host = $('pfexes');
    host.textContent = '';
    if (!editing.exes.length) host.append(el('span', 'hint', 'No programs yet.'));
    for (const e of editing.exes) {
      const chip = el('span', 'pfexe', e);
      const x = el('button', 'mini', 'Remove');
      x.type = 'button';
      x.onclick = () => { editing.exes = editing.exes.filter(v => v !== e); renderExes(); };
      chip.append(x);
      host.append(chip);
    }
  }

  async function loadRunning() {
    const sel = $('pfrunning');
    sel.length = 1;
    try {
      const r = await api('/api/programs');
      for (const p of r.programs) sel.append(new Option(p.exe, p.exe));
    } catch (_) { /* leave it empty */ }
  }

  function addExe(name) {
    name = (name || '').trim().toLowerCase().split(/[\\/]/).pop();
    if (!name) return;
    if (!name.endsWith('.exe')) name += '.exe';
    if (!editing.exes.includes(name)) editing.exes.push(name);
    renderExes();
  }

  $('pfexeadd').onclick = () => {
    addExe($('pfexetxt').value || $('pfrunning').value);
    $('pfexetxt').value = '';
    $('pfrunning').value = '';
  };
  $('pfrunning').onchange = () => { addExe($('pfrunning').value); $('pfrunning').value = ''; };
  $('pfexetxt').onkeydown = (e) => { if (e.key === 'Enter') { e.preventDefault(); $('pfexeadd').click(); } };

  $('pfsave').onclick = async (ev) => {
    ev.preventDefault();
    editing.name = $('pfname').value;
    editing.set = {};
    for (const tr of $('pfover').querySelectorAll('tr')) {
      const tick = tr.querySelector('input[type=checkbox]');
      if (!tick.checked) continue;
      const ctl = tr.querySelector('input.num, select');
      editing.set[tr.dataset.key] = tr.dataset.kind === 'dpi' ? +ctl.value : JSON.parse(ctl.value);
    }
    if ($('pfownbind').checked) {
      const b = {};
      for (const sel of $('pfbinds').querySelectorAll('select')) {
        if (!sel.value) continue;
        const pt = $('pfbinds').querySelector(`input[data-btn="${sel.dataset.btn}"]`);
        b[sel.dataset.btn] = { id: sel.value, passthrough: pt.checked };
      }
      editing.bindings = b;
    } else {
      editing.bindings = null;
    }
    try {
      render(await api('/api/profile/save', { profile: editing }));
      $('profedit').close();
      toast('profile saved', true);
    } catch (e) { toast(e.message); }
  };

  $('profnew').onclick = () => openEditor(null);

  // ?profile=new opens the editor once the first snapshot is in (screenshots)
  let deep = new URLSearchParams(location.search).get('profile') === 'new';
  window.renderProfiles = (snap) => {
    renderProfiles(snap);
    if (deep) { deep = false; openEditor(null); }
  };
})();

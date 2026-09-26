/* Tools page: link, sensor, clicks, aim, PC.
   The server records Raw Input from the X3; this page only starts, stops and
   draws. One test runs at a time - starting one ends any other. */
'use strict';
(() => {
  const TESTS = {
    poll:  { btn: 'pfpoll',  live: 'pfpolllive',  every: 500 },
    link:  { btn: 'pflink',  live: 'pflinklive',  every: 400 },
    click: { btn: 'pfclick', live: 'pfclicklive', every: 300 },
    dpi:   { btn: 'pfdpi',   live: 'pfdpilive',   every: 300 },
    lod:   { btn: 'pflod',   live: 'pflodlive',   every: 300, label: 'Test' },
    speed: { btn: 'pfspeed', live: 'pfspeedlive', every: 700 },
    path:  { btn: 'pfpath',  live: 'pfpathlive',  every: 500 },
    still: { btn: 'pfstill', live: 'pfstilllive', every: 500 },
    wheel: { btn: 'pfwheel', live: 'pfwheellive', every: 300 },
  };
  let running = null, timer = null, last = null, lastSpeed = null;
  const dpiRuns = [], lodRuns = [], meterHist = [];
  let pathData = null;

  const fmt = (v, d = 2) => (v == null ? '-' : Number(v).toFixed(d));
  const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  const snap = () => window.S;

  function activeProfile(s) {
    const p = s && s.profiles;
    if (!p || !p.active) return null;
    return (p.list || []).find(x => x.id === p.active) || null;
  }

  function effDpi() {
    const s = snap();
    if (!s) return null;
    const st = s.state;
    const pr = activeProfile(s);
    if (pr && pr.set && pr.set.dpi_value) return pr.set.dpi_value;
    return st.dpi[st.active_stage];
  }

  function setButtons() {
    for (const [mode, t] of Object.entries(TESTS)) {
      $(t.btn).textContent = running === mode ? 'Stop' : (t.label || 'Start');
      $(t.btn).classList.toggle('primary', running === mode);
    }
  }

  async function start(mode) {
    const body = { mode };
    if (mode === 'dpi') body.distance_mm = +$('pfmm').value;
    if (mode === 'lod') body.height_mm = +$('pflodmm').value;
    if (mode === 'still') body.seconds = +$('pfstills').value;
    if (mode === 'wheel') body.expected = +$('pfwheeln').value || null;
    try {
      const r = await api('/api/perf/start', body);
      for (const t of Object.values(TESTS)) $(t.live).textContent = '';
      running = mode;
      if (mode === 'link') { meterHist.length = 0; $('pfmeter').hidden = false; }
      setButtons();
      show(r);
      clearInterval(timer);
      timer = setInterval(tick, TESTS[mode].every);
    } catch (e) { toast(e.message); }
  }

  async function stop() {
    clearInterval(timer);
    timer = null;
    try { finish(await api('/api/perf/stop', {})); }
    catch (e) { toast(e.message); }
  }

  async function tick() {
    try {
      const r = await api('/api/perf/results');
      if (!r.running) { clearInterval(timer); timer = null; finish(r); return; }
      show(r);
    } catch (_) { /* server busy - next tick */ }
  }

  function finish(r) {
    const mode = running;
    running = null;
    setButtons();
    show(r, true);
    if (mode === 'dpi' && r.dpi && r.dpi.measured_cpi) { dpiRuns.push(r.dpi); renderDpiRuns(); }
    if (mode === 'lod' && r.lod) { lodRuns.push(r.lod); renderLod(); }
    if (mode === 'poll') loadHistory();
  }

  /* ---------------------------------------------------------------- views */
  function show(r, final) {
    const t = TESTS[r.mode];
    if (!t) return;
    const base = `${fmt(r.elapsed_s, 1)} s · ${r.x3_reports} reports`;
    const stray = !r.x3_reports && r.other_reports
      ? ' · other mice are moving, the X3 is not' : '';
    const live = $(t.live);
    switch (r.mode) {
      case 'poll': {
        const hz = r.live && r.live.hz;
        live.innerHTML = (running ? (hz ? `<b>${Math.round(hz)} Hz</b> · ` : 'keep moving · ') : '') + base + stray;
        if (r.poll) renderPoll(r.poll);
        break;
      }
      case 'link': live.textContent = base + stray; renderMeter(r.link); break;
      case 'click': live.textContent = base + stray; renderClicks(r.click, final); break;
      case 'dpi': {
        const d = r.dpi || {};
        live.innerHTML = base + (d.measured_cpi != null ? ` · <b>${d.measured_cpi} cpi</b>` : '') + stray;
        break;
      }
      case 'lod': {
        const l = r.lod || {};
        live.innerHTML = base + ` · <b>${l.counts || 0} counts</b>` + stray;
        break;
      }
      case 'speed': live.textContent = base + stray; renderSpeed(r.speed, final); break;
      case 'path': live.textContent = base + stray; pathData = r.path; drawPath(); break;
      case 'still': live.textContent = base + stray; renderStill(r.still, final); break;
      case 'wheel': live.textContent = base + stray; renderWheel(r.wheel, final); break;
    }
  }

  function fig(label, value, note) {
    return `<div><span>${label}</span><b>${value}</b>${note ? `<em>${note}</em>` : ''}</div>`;
  }

  function notesInto(id, lines) {
    const host = $(id);
    host.textContent = '';
    for (const n of lines || []) host.append(el('li', null, n));
  }

  /* ---- report rate */
  function renderPoll(p) {
    last = p;
    notesInto('pfpollnotes', p.verdict);
    if (!p.intervals) { $('pfpollfig').hidden = true; $('pfcharts').hidden = true; return; }
    $('pfpollfig').hidden = false;
    $('pfpollfig').innerHTML =
      fig('Measured', `${Math.round(p.measured_hz)} Hz`, `set to ${p.configured_hz} Hz`) +
      fig('Median gap', `${fmt(p.median_ms, 3)} ms`, `expected ${fmt(p.expected_ms, 3)}`) +
      fig('Jitter', `${fmt(p.stdev_ms, 3)} ms`, `p99 gap ${fmt(p.p99_ms)} ms`) +
      fig('Lost', p.lost_pct == null ? '-' : `${fmt(p.lost_pct)} %`,
          `${p.lost} of ${p.fast_expected} in fast motion`) +
      fig('Delivered late', `${fmt(p.bunched_pct)} %`, `${p.bunched} bunched`);
    $('pfcharts').hidden = false;
    drawHist();
    drawTime();
  }

  async function loadHistory() {
    let h;
    try { h = (await api('/api/perf/history')).history; } catch (_) { return; }
    const body = $('pfhistbody');
    body.textContent = '';
    $('pfhisttable').hidden = !h.length;
    $('pfhistclear').hidden = !h.length;
    $('pfhistnote').textContent = h.length
      ? 'Every finished report-rate run, newest first. Lower jitter, lost and late are better.'
      : 'No runs yet. Finished report-rate runs are kept here.';
    h.forEach((r, i) => {
      const tr = el('tr');
      for (const v of [r.when, r.link, `${Math.round(r.measured_hz)} / ${r.configured_hz} Hz`,
                       fmt(r.median_ms, 3), fmt(r.stdev_ms, 3), fmt(r.p99_ms),
                       r.lost_pct == null ? '-' : `${fmt(r.lost_pct)} %`,
                       `${fmt(r.bunched_pct)} %`]) tr.append(el('td', null, String(v)));
      const td = el('td');
      const b = el('button', 'mini', 'Delete');
      b.type = 'button';
      b.onclick = async () => { await api('/api/perf/history/delete', { index: i }); loadHistory(); };
      td.append(b);
      tr.append(td);
      body.prepend(tr);
    });
  }

  /* ---- placement meter */
  function renderMeter(l) {
    if (!l) return;
    $('pfmeter').hidden = false;
    const ok = l.enough && l.fast_expected >= 150;
    const pct = ok ? l.lost_pct : null;
    $('pfmeterval').textContent = pct == null ? 'move faster' : `${fmt(pct)} %`;
    $('pfmeterlbl').textContent = pct == null ? 'needs fast movement to judge'
      : `lost in fast motion · ${Math.round(l.measured_hz)} Hz`;
    const fill = $('pfbarfill');
    fill.style.width = pct == null ? '0' : `${Math.min(100, pct * 20)}%`;   // 5 % fills it
    fill.className = pct != null && pct >= 1 ? 'bad' : '';
    if (pct != null) { meterHist.push(pct); if (meterHist.length > 60) meterHist.shift(); }
    drawSpark();
  }

  function drawSpark() {
    const { g, w, h } = canvasCtx('pfspark');
    g.fillStyle = css('--line2');
    g.fillRect(0, h - 1, w, 1);
    if (!meterHist.length) return;
    const top = Math.max(2, ...meterHist);
    const bw = w / 60;
    g.fillStyle = css('--dim');
    meterHist.forEach((v, i) => {
      const bh = Math.max(1, v / top * (h - 4));
      g.fillRect(i * bw + 1, h - 1 - bh, Math.max(1, bw - 2), bh);
    });
  }

  /* ---- usb */
  async function loadUsb() {
    const host = $('pfusb');
    host.textContent = 'reading…';
    let r;
    try { r = await api('/api/perf/usb'); } catch (e) { host.textContent = e.message; return; }
    host.textContent = '';
    if (!r.found) { host.append(el('p', 'hint', 'The mouse is not plugged in, or Windows does not list it.')); return; }
    const tbl = el('table', 'pftable pfusbt');
    const rows = [['Device', r.device.name, r.device.location]]
      .concat(r.chain.map((n, i) => [i === r.chain.length - 1 ? 'Controller' : 'Hub', n.name, n.location]));
    for (const [k, v, loc] of rows) {
      const tr = el('tr');
      tr.append(el('td', null, k), el('td', null, v + (loc ? `  ·  ${loc}` : '')));
      tbl.append(tr);
    }
    if (r.neighbours.length) {
      const tr = el('tr');
      tr.append(el('td', null, 'Same hub'), el('td', null, r.neighbours.map(n => n.name).join(', ')));
      tbl.append(tr);
    }
    host.append(tbl);
    const ul = el('ul', 'pfnotes');
    for (const n of r.notes) ul.append(el('li', null, n));
    host.append(ul);
  }

  /* ---- clicks */
  const ms = (v) => (v == null ? '-' : fmt(v, 1));

  function renderClicks(c, final) {
    if (!c) return;
    const body = $('pfclickbody');
    body.textContent = '';
    $('pfclicktable').hidden = !c.buttons.length;
    for (const b of c.buttons) {
      const tr = el('tr');
      for (const v of [b.button, b.presses, ms(b.hold_min_ms), ms(b.hold_median_ms),
                       ms(b.gap_min_ms)]) tr.append(el('td', null, String(v)));
      const td = el('td');
      if (b.chatter.length) {
        td.append(el('span', 'tag warn', `${b.chatter.length} at ${b.chatter.map(v => fmt(v, 1)).join(', ')} ms`));
      } else {
        td.className = 'pfok';
        td.textContent = 'none';
      }
      tr.append(td);
      body.append(tr);
    }
    const adv = c.advice;
    $('pfclickadvice').hidden = !(final && (adv || c.buttons.length));
    if (!final) return;
    if (adv) {
      $('pfclickwhy').textContent = adv.why;
      $('pfclickapply').hidden = adv.key_response_ms == null;
      $('pfclickapply').textContent = `Set key response to ${adv.key_response_ms} ms`;
      $('pfclickapply').onclick = () => setField({ key_response_ms: adv.key_response_ms },
                                                 `key response ${adv.key_response_ms} ms`);
    } else if (c.buttons.length) {
      $('pfclickwhy').textContent =
        `No chatter below ${c.chatter_ms} ms at key response ${c.key_response_ms} ms.` +
        (c.key_response_ms > 2 ? ' A lower key response registers clicks sooner; lower it one step and test again.' : '');
      $('pfclickapply').hidden = true;
    }
  }

  async function setField(p, msg) {
    try { render(await api('/api/set', p)); toast(msg, true); }
    catch (e) { toast(e.message); }
  }

  /* ---- dpi */
  function renderDpiRuns() {
    $('pfdpitable').hidden = !dpiRuns.length;
    const body = $('pfdpibody');
    body.textContent = '';
    dpiRuns.forEach((d, i) => {
      const tr = el('tr');
      for (const v of [i + 1, d.configured_dpi, `${d.measured_cpi} (${d.axis})`,
                       d.error_pct == null ? '-' : `${d.error_pct > 0 ? '+' : ''}${fmt(d.error_pct, 1)} %`,
                       `${fmt(d.drift_deg, 1)} deg`]) tr.append(el('td', null, String(v)));
      body.append(tr);
    });
    const cur = dpiRuns[dpiRuns.length - 1].configured_dpi;
    const same = dpiRuns.filter(d => d.configured_dpi === cur);
    if (same.length >= 2) {
      const avg = same.reduce((a, d) => a + d.measured_cpi, 0) / same.length;
      const want = Math.round(cur * cur / avg / 50) * 50;
      $('pfdpisum').textContent = `Average of ${same.length} runs at ${cur} DPI: ${Math.round(avg)} cpi` +
        ` (${((avg - cur) / cur * 100).toFixed(1)} %). ` +
        (want !== cur ? `To get a true ${cur}, set the stage to ${want}.` : 'Within one step of true.');
      const fixb = $('pfdpifix');
      // under a profile's DPI override the stage is not what was measured
      const ap = activeProfile(snap());
      fixb.hidden = want === cur || !!(ap && ap.set && ap.set.dpi_value);
      fixb.textContent = `Set this stage to ${want}`;
      fixb.onclick = () => {
        const st = snap().state;
        const dpi = st.dpi.slice();
        dpi[st.active_stage] = want;
        setField({ dpi, enabled_mask: st.enabled_mask }, `stage ${st.active_stage + 1} set to ${want} DPI`);
        fixb.hidden = true;
      };
    } else {
      $('pfdpisum').textContent = 'Do three or more runs; one stroke is only as accurate as the hand that made it.';
      $('pfdpifix').hidden = true;
    }
    const xs = same.filter(d => d.axis === 'x'), ys = same.filter(d => d.axis === 'y');
    if (xs.length && ys.length) {
      const mx = xs.reduce((a, d) => a + d.measured_cpi, 0) / xs.length;
      const my = ys.reduce((a, d) => a + d.measured_cpi, 0) / ys.length;
      const diff = (my - mx) / mx * 100;
      $('pfdpiaxes').textContent = `Left-right ${Math.round(mx)} cpi, up-down ${Math.round(my)} cpi: ` +
        `vertical is ${diff >= 0 ? '+' : ''}${diff.toFixed(1)} % against horizontal. ` +
        (Math.abs(diff) < 2 ? 'Within what a hand-drawn stroke can resolve.'
          : 'A real mismatch: vertical aim will feel faster or slower than horizontal.');
    } else {
      $('pfdpiaxes').textContent = 'Do runs in both directions to compare the axes.';
    }
  }

  /* ---- lift-off */
  function renderLod() {
    $('pflodtable').hidden = !lodRuns.length;
    const body = $('pflodbody');
    body.textContent = '';
    const sorted = [...lodRuns].sort((a, b) => a.height_mm - b.height_mm);
    for (const r of sorted) {
      const tr = el('tr');
      tr.append(el('td', null, `${fmt(r.height_mm, 1)} mm`), el('td', null, String(r.counts)));
      const td = el('td');
      if (r.verdict === 'tracks') { td.className = 'pfok'; td.textContent = 'tracks'; }
      else td.append(el('span', r.verdict === 'partial' ? 'tag' : 'tag warn', r.verdict));
      tr.append(td);
      body.append(tr);
    }
    const tracks = sorted.filter(r => r.verdict === 'tracks').map(r => r.height_mm);
    const stops = sorted.filter(r => r.verdict === 'stopped').map(r => r.height_mm);
    const hi = tracks.length ? Math.max(...tracks) : null;
    const lo = stops.length ? Math.min(...stops) : null;
    const set = snap() ? snap().state.lod_mm : null;
    let s = '';
    if (hi != null && lo != null && lo > hi) s = `It cuts out between ${fmt(hi, 1)} and ${fmt(lo, 1)} mm. `;
    else if (hi != null) s = `Still tracking at ${fmt(hi, 1)} mm. `;
    if (set && hi != null && hi > set + 0.3)
      s += `That is higher than the ${set} mm setting: the setting may not be taking effect, or your pad reflects unusually well.`;
    else if (set === 2 && lo != null && lo <= 2.2)
      s += 'If the cursor drifts when you lift and reposition, try 1 mm.';
    $('pflodsum').textContent = s || 'Test a few heights, low to high.';
  }

  /* ---- speed */
  function renderSpeed(sp, final) {
    if (!sp) return;
    lastSpeed = sp;
    if (sp.peak_ms == null) {
      $('pfspeedfig').hidden = true;
      if (final) notesInto('pfspeednotes', ['Not enough movement. Flick harder, several times.']);
      return;
    }
    $('pfspeedfig').hidden = false;
    const ev = sp.events || [];
    $('pfspeedfig').innerHTML =
      fig('Peak speed', `${fmt(sp.peak_ms)} m/s`, `${fmt(sp.peak_ips, 0)} in/s`) +
      fig('At DPI', `${sp.dpi}`, 'active stage') +
      fig('Tracking faults', `${ev.length}`, ev.length ? 'listed below' : 'none');
    if (final) {
      const lines = [];
      if (!ev.length) lines.push(`No spin-out up to ${fmt(sp.peak_ms)} m/s. The sensor kept up with every flick.`);
      for (const e of ev.slice(0, 8)) {
        const what = { dropout: 'reports stopped dead', stall: 'reports went empty',
                       reversal: 'a report pointed backwards' }[e.kind];
        lines.push(`${fmt(e.t, 2)} s at ${fmt(e.speed)} m/s: ${what}.`);
      }
      if (ev.length) lines.push('Faults at high speed mean the sensor lost tracking. A lower DPI stage, ' +
        'a clean sensor window and a pad the sensor reads well all help.');
      notesInto('pfspeednotes', lines);
    }
    $('pfspeedchart').hidden = false;
    drawSpeed();
  }

  function drawSpeed() {
    const sp = lastSpeed;
    if (!sp || !sp.trace || $('pfspeedchart').hidden) return;
    const { c, g, w, h } = canvasCtx('pfspeedcv');
    const pts = sp.trace;
    const tmax = Math.max(0.1, ...pts.map(p => p[0]));
    const top = Math.max(1, sp.peak_ms * 1.1);
    const pw = w - PAD.l - PAD.r, ph = h - PAD.t - PAD.b;
    const X = t => PAD.l + t / tmax * pw, Y = v => PAD.t + ph - v / top * ph;
    axes(g, w, h, fmt(top, 1), '0');
    g.fillStyle = css('--dim');
    g.textAlign = 'center';
    g.fillText(`${fmt(tmax, 1)} s`, w - 22, h - 5);
    g.fillStyle = css('--accent');
    for (const e of sp.events || []) g.fillRect(Math.round(X(e.t)) - 1, PAD.t, 2, ph);
    g.strokeStyle = css('--fg');
    g.lineWidth = 1.5;
    g.beginPath();
    pts.forEach(([t, v], i) => (i ? g.lineTo(X(t), Y(v)) : g.moveTo(X(t), Y(v))));
    g.stroke();
    c.onmousemove = (e) => {
      let best = null, bd = 1e9;
      for (const p of pts) { const d = Math.abs(X(p[0]) - e.offsetX); if (d < bd) { bd = d; best = p; } }
      $('pfspeedread').textContent = best && bd < 15 ? `${fmt(best[0], 2)} s: ${fmt(best[1])} m/s` : '';
    };
    c.onmouseleave = () => {
      $('pfspeedread').textContent = (sp.events || []).length ? 'red lines = tracking faults' : '';
    };
    c.onmouseleave();
  }

  /* ---- path */
  function drawPath() {
    const cv = $('pfpathcv');
    if (!cv.clientWidth) return;
    const { g, w, h } = canvasCtx('pfpathcv');
    g.fillStyle = css('--line');
    g.fillRect(0, h - 1, w, 1);
    const pts = pathData && pathData.points;
    if (!pts || pts.length < 2) return;
    let x0 = Infinity, x1 = -Infinity, y0 = Infinity, y1 = -Infinity;
    for (const [x, y] of pts) { x0 = Math.min(x0, x); x1 = Math.max(x1, x); y0 = Math.min(y0, y); y1 = Math.max(y1, y); }
    const pad = 10;
    const k = Math.min((w - 2 * pad) / Math.max(1, x1 - x0), (h - 2 * pad) / Math.max(1, y1 - y0));
    const ox = (w - (x1 - x0) * k) / 2, oy = (h - (y1 - y0) * k) / 2;
    const X = x => ox + (x - x0) * k, Y = y => oy + (y - y0) * k;     // equal scale on both axes
    g.strokeStyle = css('--line2');
    g.lineWidth = 1;
    g.beginPath();
    pts.forEach(([x, y], i) => (i ? g.lineTo(X(x), Y(y)) : g.moveTo(X(x), Y(y))));
    g.stroke();
    g.fillStyle = css('--fg');
    for (const [x, y] of pts) g.fillRect(X(x) - 1, Y(y) - 1, 2, 2);
    const d = pathData;
    $('pfpathsum').textContent = d.fast
      ? `${d.reports} reports${d.step > 1 ? ` (every ${d.step}th drawn)` : ''}. ` +
        `${fmt(d.flat_pct, 1)} % of moving reports had one axis exactly zero. ` +
        'Angle snap pushes that figure up; compare the same shapes with it on and off.'
      : '';
  }

  /* ---- stillness */
  function renderStill(s, final) {
    if (!s) return;
    const out = $('pfstillsum');
    if (!final) { out.textContent = `${s.reports} stray reports so far`; return; }
    out.textContent = s.reports === 0
      ? `Silent for ${fmt(s.seconds, 0)} s at ${effDpi()} DPI. No sensor noise.`
      : `${s.reports} reports (${s.counts} counts, the biggest ${s.biggest}) in ` +
        `${fmt(s.seconds, 0)} s with nobody touching it. ` +
        (s.biggest <= 1 && s.reports < 10
          ? 'Single-count twitches: harmless, but worth knowing at high DPI.'
          : 'That is enough to nudge the crosshair. Check the pad for glare or dust on the sensor window, or use a lower DPI stage.');
  }

  /* ---- wheel */
  function renderWheel(w, final) {
    if (!w) return;
    const out = $('pfwheelsum');
    const dir = w.down >= w.up ? 'down' : 'up';
    const main = Math.max(w.up, w.down), other = Math.min(w.up, w.down);
    let s = `${main} notches ${dir}` + (other ? `, ${other} ${dir === 'down' ? 'up' : 'down'}` : '') + '.';
    if (final) {
      if (w.wrong_way.length) {
        s += ` ${w.wrong_way.length} registered the wrong way in the middle of a scroll ` +
          `(at ${w.wrong_way.slice(0, 6).map(t => fmt(t, 1) + ' s').join(', ')}). ` +
          'That is the wheel encoder misreading: cleaning it often helps; if it keeps happening the encoder is worn.';
      } else if (main) {
        s += ' None went the wrong way.';
      }
      if (w.expected) {
        const d = w.difference;
        s += d === 0 ? ` Matches the ${w.expected} you counted.`
          : ` You counted ${w.expected}: ${Math.abs(d)} ${d < 0 ? 'missing' : 'extra'}.`;
      }
    }
    out.textContent = s;
  }

  /* ---- sensitivity calculator */
  function calc() {
    const dpi = +$('pfcdpi').value, sens = +$('pfcsens').value, yaw = +$('pfcyaw').value;
    if (!(dpi > 0 && sens > 0 && yaw > 0)) { $('pfcout').innerHTML = ''; $('pfcconv').textContent = ''; return; }
    const counts = 360 / (yaw * sens);
    const inches = counts / dpi;
    const cm = inches * 2.54;
    $('pfcout').innerHTML =
      fig('eDPI', `${Math.round(dpi * sens)}`, 'DPI x sensitivity') +
      fig('cm / 360', fmt(cm, 1), `${fmt(inches, 2)} in`) +
      fig('Counts / 360', `${Math.round(counts)}`, `${fmt(yaw * sens * 60, 3)} arcmin per count`);
    const lines = [];
    const nd = +$('pfcnewdpi').value;
    if (nd > 0 && nd !== dpi) lines.push(`At ${nd} DPI, sensitivity ${fmt(sens * dpi / nd, 4)} feels the same.`);
    const tg = +$('pfctarget').value;
    if (tg > 0) lines.push(`For ${fmt(tg, 1)} cm / 360 at ${dpi} DPI, set sensitivity ${fmt(360 / (yaw * dpi * tg / 2.54), 4)}.`);
    const perCount = yaw * sens * 60;
    if (perCount > 1.5) lines.push(`Each count turns ${fmt(perCount, 2)} arcminutes, coarse enough to see as stepping. ` +
      'Raise DPI and lower sensitivity to keep the same cm / 360 with finer steps.');
    $('pfcconv').textContent = lines.join(' ');
  }

  function primeCalc() {
    if (!$('pfcdpi').value && effDpi()) $('pfcdpi').value = effDpi();
    calc();
  }

  /* --------------------------------------------------------------- charts */
  function canvasCtx(id) {
    const c = $(id);
    const dpr = window.devicePixelRatio || 1;
    const w = c.clientWidth, h = c.clientHeight;
    c.width = Math.round(w * dpr);
    c.height = Math.round(h * dpr);
    const g = c.getContext('2d');
    g.setTransform(dpr, 0, 0, dpr, 0, 0);
    g.clearRect(0, 0, w, h);
    g.font = `11px ${css('--mono') || 'monospace'}`;
    return { c, g, w, h };
  }

  const PAD = { l: 44, r: 8, t: 8, b: 20 };

  function drawHist() {
    if (!last || !last.hist) return;
    const { c, g, w, h } = canvasCtx('pfhist');
    const counts = last.hist.counts, width = last.hist.width_ms;
    const n = counts.length, max = Math.max(1, ...counts);
    const pw = w - PAD.l - PAD.r, ph = h - PAD.t - PAD.b;
    const bw = pw / n;
    axes(g, w, h, `${max}`, '0');
    g.fillStyle = css('--dim');
    counts.forEach((v, i) => {
      if (!v) return;
      const bh = Math.max(1, v / max * ph);
      g.fillRect(PAD.l + i * bw + 1, PAD.t + ph - bh, Math.max(1, bw - 2), bh);
    });
    const ex = Math.round(PAD.l + last.expected_ms / width * bw) + 0.5;
    g.strokeStyle = css('--fg');
    g.setLineDash([3, 3]);
    g.beginPath(); g.moveTo(ex, PAD.t); g.lineTo(ex, PAD.t + ph); g.stroke();
    g.setLineDash([]);
    g.textAlign = 'center';
    g.fillStyle = css('--dim');
    for (const f of [0, 1, 2, 3]) {
      const x = PAD.l + f * last.expected_ms / width * bw;
      g.fillText(`${fmt(f * last.expected_ms, f ? 1 : 0)}`, Math.min(x, w - 14), h - 5);
    }
    c.onmousemove = (e) => {
      const i = Math.floor((e.offsetX - PAD.l) / bw);
      if (i < 0 || i >= n) { $('pfhistread').innerHTML = '&nbsp;'; return; }
      const lo = i * width, range = i === n - 1 ? `over ${fmt(lo, 2)}` : `${fmt(lo, 2)}-${fmt(lo + width, 2)}`;
      $('pfhistread').textContent = `${range} ms: ${counts[i]} gaps`;
    };
    c.onmouseleave = () => { $('pfhistread').textContent = `dashed line = expected ${fmt(last.expected_ms, 2)} ms`; };
    c.onmouseleave();
  }

  function drawTime() {
    if (!last) return;
    const pts = last.timeline || [];
    const { c, g, w, h } = canvasCtx('pftime');
    const cfg = last.configured_hz;
    const top = Math.max(cfg * 1.1, ...pts.map(p => p[1]));
    const pw = w - PAD.l - PAD.r, ph = h - PAD.t - PAD.b;
    const tmax = Math.max(1, ...pts.map(p => p[0]));
    const X = (t) => PAD.l + t / tmax * pw, Y = (v) => PAD.t + ph - v / top * ph;
    axes(g, w, h, '', '0');
    g.strokeStyle = css('--line2');
    g.setLineDash([4, 4]);
    g.beginPath(); g.moveTo(PAD.l, Y(cfg)); g.lineTo(w - PAD.r, Y(cfg)); g.stroke();
    g.setLineDash([]);
    g.fillStyle = css('--dim');
    g.textAlign = 'right';
    g.fillText(`${cfg}`, PAD.l - 6, Y(cfg) + 4);
    g.textAlign = 'center';
    g.fillText(`${fmt(tmax, 1)} s`, w - 22, h - 5);
    if (!pts.length) return;
    g.fillStyle = css('--fg');
    for (const [t, v] of pts) {
      g.beginPath(); g.arc(X(t), Y(v), 2.5, 0, Math.PI * 2); g.fill();
    }
    c.onmousemove = (e) => {
      let best = null, bd = 1e9;
      for (const p of pts) {
        const d = Math.abs(X(p[0]) - e.offsetX);
        if (d < bd) { bd = d; best = p; }
      }
      $('pftimeread').textContent = best && bd < 20 ? `${fmt(best[0], 2)} s: ${Math.round(best[1])} Hz` : '';
    };
    c.onmouseleave = () => { $('pftimeread').textContent = 'dashed line = configured rate'; };
    c.onmouseleave();
  }

  function axes(g, w, h, topLabel, zero) {
    const ph = h - PAD.t - PAD.b;
    g.fillStyle = css('--line');
    g.fillRect(PAD.l, PAD.t + ph, w - PAD.l - PAD.r, 1);
    g.fillStyle = css('--dim');
    g.textAlign = 'right';
    if (topLabel) g.fillText(topLabel, PAD.l - 6, PAD.t + 9);
    g.fillText(zero, PAD.l - 6, PAD.t + ph);
  }

  /* -------------------------------------------------------------- windows */
  async function loadSystem() {
    let r;
    try { r = await api('/api/perf/system'); } catch (e) { return; }
    const body = $('pfsys');
    body.textContent = '';
    for (const it of r.items) {
      const tr = el('tr');
      const name = el('td', null, it.name);
      const why = el('p', null, it.why);
      if (it.same_speed) {
        why.textContent += ` Your stage of ${it.same_speed.dpi} DPI at the current speed ` +
          `moves the cursor like ${it.same_speed.equivalent} DPI at 6/11 - set that on the ` +
          'dashboard after fixing this and nothing will feel different except the precision.';
      }
      name.append(why);
      const now = el('td');
      if (it.ok) { now.className = 'pfok'; now.textContent = it.value; }
      else {
        now.append(el('span', 'tag warn', it.value));
        now.append(el('div', 'hint', 'recommended: ' + it.want));
      }
      const act = el('td');
      if (!it.ok) {
        const b = el('button', 'btn', it.id === 'conflict' ? 'Close it' : 'Fix');
        b.type = 'button';
        b.onclick = () => fix(it.id, b);
        act.append(b);
      }
      tr.append(name, now, act);
      body.append(tr);
    }
  }

  async function fix(what, b) {
    b.disabled = true;
    try {
      const r = await api('/api/perf/fix', { what });
      toast(r.note || (r.ok ? 'fixed' : 'Windows refused the change'), r.ok);
    } catch (e) { toast(e.message); }
    b.disabled = false;
    setTimeout(loadSystem, what === 'usb' ? 4000 : 300);
  }

  /* ------------------------------------------------------------- sub tabs */
  const SUBS = ['link', 'sensor', 'clicks', 'aim', 'pc'];
  const loaded = {};

  function showSub(name) {
    if (!SUBS.includes(name)) name = SUBS[0];
    document.querySelectorAll('#page-tools [data-sub]').forEach(n => {
      if (n.tagName === 'BUTTON') n.classList.toggle('on', n.dataset.sub === name);
      else n.hidden = n.dataset.sub !== name;
    });
    try { localStorage.setItem('asx.toolsub', name); } catch (_) { /* private mode */ }
    if (name === 'link' && !loaded.link) { loaded.link = 1; loadHistory(); loadUsb(); }
    if (name === 'pc') loadSystem();
    if (name === 'aim') primeCalc();
    if (name === 'link' && last) { drawHist(); drawTime(); }
    if (name === 'sensor') { drawSpeed(); drawPath(); }
  }

  /* called by app.js on every render */
  window.renderTools = (s) => {
    const st = s.state;
    $('pflodnow').textContent = `${st.lod_mm} mm`;
    $('pfsnapnow').textContent = st.angle_snap ? 'on' : 'off';
    if (!$('pfcdpi').value) primeCalc();       // the calculator opened before the first snapshot
  };

  /* ----------------------------------------------------------------- boot */
  for (const [mode, t] of Object.entries(TESTS)) {
    $(t.btn).onclick = () => (running === mode ? stop() : start(mode));
  }
  document.querySelectorAll('#pftabs button').forEach(b => { b.onclick = () => showSub(b.dataset.sub); });
  $('pfhistclear').onclick = async () => { await api('/api/perf/history/delete', {}); loadHistory(); };
  $('pfusbbtn').onclick = loadUsb;
  $('pfsysbtn').onclick = loadSystem;
  for (const id of ['pfcdpi', 'pfcsens', 'pfcyaw', 'pfcnewdpi', 'pfctarget']) $(id).oninput = calc;

  let sub = 'link';
  try { sub = localStorage.getItem('asx.toolsub') || 'link'; } catch (_) { /* private mode */ }
  showSub(new URLSearchParams(location.search).get('sub') || sub);

  document.addEventListener('asx:page', (e) => {
    if (e.detail !== 'tools') return;
    const on = document.querySelector('#pftabs button.on');
    showSub(on ? on.dataset.sub : 'link');
  });
  window.addEventListener('resize', () => {
    if (last) { drawHist(); drawTime(); }
    drawSpeed();
    drawPath();
  });

  // a reload keeps the last result, and picks a running test back up
  api('/api/perf/results').then((r) => {
    if (!r.mode) return;
    if (r.running) {
      running = r.mode; setButtons(); show(r);
      timer = setInterval(tick, TESTS[r.mode].every);
    } else {
      show(r, true);
    }
  }).catch(() => {});
})();

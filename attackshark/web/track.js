/* Track editor: a macro as rectangles on lanes, instead of rows in a list.

   A step list says what happens next. It cannot say what happens *at the same
   time* - one input held while another is tapped underneath it - which is
   half of what any macro with more than one key in it does. Lanes fix that by
   construction: each lane is one input, horizontal position is time, and two
   lanes overlapping is simply two lanes overlapping.

   A block is either a `hold` - press at its left edge, release at its right -
   or a `spam`, the same pair repeated across its width every `rate` ms. Spam
   is not a convenience: a burst is often dozens of presses at a fixed rate,
   and dragging one rectangle is the difference between editing that and
   editing forty rows by hand.

   A wheel lane has no press and release to place, so its one-shot block is a
   `tick`: one notch at the left edge, the width only saying how long the
   block occupies the pass. Spam on a wheel lane is a notch every `rate` ms
   across that width - a wheel spun at a known speed for a known length of
   time, which is the thing a hand on a wheel cannot do twice the same way.

   Lanes are what gets saved. macro.py derives the steps from them on the way
   in, so the two cannot drift, and nothing downstream knows this file exists. */
(() => {
  const $ = (id) => document.getElementById(id);
  const el = (tag, cls, text) => {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined) n.textContent = text;
    return n;
  };

  let lanes = [];
  let pxms = 2;
  let sel = null;
  let onChange = () => {};
  let macroList = [];

  const nameOfMacro = (id) => (macroList.find((m) => m.id === id) || {}).name;
  const laneName = (l) => l.kind === 'key' ? l.key.toUpperCase()
    : l.kind === 'mouse' ? l.button
    : l.kind === 'wheel' ? 'scroll ' + l.direction
    : (nameOfMacro(l.id) || 'macro');

  /* How many times a block fires. A press has to be let go of before the next
     one starts, so its last press cannot begin inside the final `hold`; a
     notch has no such tail, so a wheel block's last one lands on its right
     edge. Mirrors macro.py's lane_steps() - if these two disagree, the count
     on screen is a lie about what will run. */
  const eventCount = (lane, b) => {
    if (b.type !== 'spam') return 1;
    const rate = b.rate || 1;
    if (lane && lane.kind === 'wheel') return Math.floor(b.ms / rate) + 1;
    return Math.max(0, Math.floor((b.ms - (b.hold || 0)) / rate) + 1);
  };

  function span() {
    let end = 260;
    for (const l of lanes) {
      for (const b of l.blocks) end = Math.max(end, b.at + b.ms);
    }
    return Math.ceil((end + 60) / 50) * 50;
  }

  /* ------------------------------------------------------------- render */
  function render() {
    const host = $('trklanes');
    if (!host) return;
    host.textContent = '';
    const total = span();
    const W = (ms) => (ms * pxms) + 'px';

    if (!lanes.length) {
      host.append(el('div', 'trkempty',
        'No lanes yet — pick an input above and press Add.'));
      renderConfig();
      return;
    }

    const paintGrid = (parent) => {
      for (let t = 50; t <= total; t += 50) {
        const g = el('div', 'trkgrid');
        g.style.left = W(t);
        parent.append(g);
      }
    };

    const rrow = el('div', 'trkrow ruler');
    rrow.append(el('div', 'trkhead', 'ms'));
    const rtrack = el('div', 'trktrack');
    const ruler = el('div', 'trkruler');
    ruler.style.width = W(total);
    const stepMs = pxms >= 3 ? 25 : pxms >= 1.2 ? 50 : 100;
    for (let t = 0; t <= total; t += stepMs) {
      const tick = el('span', 'trktick', String(t));
      tick.style.left = W(t);
      ruler.append(tick);
    }
    rtrack.append(ruler);
    rrow.append(rtrack);
    host.append(rrow);

    lanes.forEach((lane, li) => {
      const row = el('div', 'trkrow');
      const head = el('div', 'trkhead');
      head.append(el('span', 'trklabel', laneName(lane)));
      const del = el('button', 'trkx', '×');
      del.type = 'button';
      del.title = 'remove this lane';
      del.onclick = () => {
        lanes.splice(li, 1);
        sel = null;
        render();
        onChange();
      };
      head.append(del);
      row.append(head);

      const track = el('div', 'trktrack');
      const strip = el('div', 'trkstrip');
      strip.style.width = W(total);
      paintGrid(strip);
      strip.onpointerdown = (e) => {
        const c = e.target.classList;
        if (!c.contains('trkstrip') && !c.contains('trkgrid')) return;
        const x = e.clientX - strip.getBoundingClientRect().left;
        const wheel = lane.kind === 'wheel';
        const b = { type: wheel ? 'tick' : 'hold',
                    at: Math.max(0, Math.round(x / pxms)),
                    ms: wheel ? 20 : 60 };
        if (wheel) b.notches = 1;
        lane.blocks.push(b);
        sel = { lane: lane, block: b };
        render();
        onChange();
      };
      for (const b of lane.blocks) strip.append(blockEl(lane, b));
      track.append(strip);
      row.append(track);
      host.append(row);
    });

    renderConfig();
  }

  function blockEl(lane, b) {
    const n = el('div', 'trkblk ' + b.type
      + (sel && sel.block === b ? ' on' : ''));
    n.style.left = (b.at * pxms) + 'px';
    n.style.width = Math.max(4, b.ms * pxms) + 'px';
    const count = eventCount(lane, b);
    const unit = lane.kind === 'wheel'
      ? (count === 1 ? ' notch' : ' notches')
      : (count === 1 ? ' press' : ' presses');
    n.title = b.type + ' — starts ' + b.at + 'ms, ' + b.ms + 'ms long, '
      + count + unit + (lane.kind === 'wheel' && (b.notches || 1) > 1
        ? ' of ' + b.notches + ' each' : '');
    if (b.ms * pxms > 46) {
      n.append(el('span', 'trkblklbl',
        b.type === 'spam' ? 'spam ×' + count : b.type));
    }
    n.append(el('div', 'trkgrip'));

    n.onpointerdown = (e) => {
      e.stopPropagation();
      sel = { lane: lane, block: b };
      const resizing = e.target.classList.contains('trkgrip');
      const x0 = e.clientX;
      const at0 = b.at;
      const ms0 = b.ms;
      n.setPointerCapture(e.pointerId);
      const move = (ev) => {
        const d = Math.round((ev.clientX - x0) / pxms);
        if (resizing) b.ms = Math.max(5, ms0 + d);
        else b.at = Math.max(0, at0 + d);
        render();
      };
      const up = () => {
        n.removeEventListener('pointermove', move);
        n.removeEventListener('pointerup', up);
        onChange();
      };
      n.addEventListener('pointermove', move);
      n.addEventListener('pointerup', up);
      render();
    };
    return n;
  }

  /* A block can be four pixels wide, so its settings go in a bar under the
     lanes rather than on the rectangle itself. */
  function renderConfig() {
    const host = $('trkcfg');
    if (!host) return;
    host.textContent = '';
    if (!sel) {
      host.append(el('span', 'trkhelp', lanes.length
        ? 'Click a lane to drop a block · drag to move · drag the right edge to resize · Delete removes it'
        : 'Add a lane for each key this macro presses.'));
      return;
    }
    const b = sel.block;
    const wheel = sel.lane.kind === 'wheel';
    host.append(el('span', 'trktitle', laneName(sel.lane)));

    const type = el('div', 'seg');
    const once = wheel ? 'tick' : 'hold';
    for (const t of [once, 'spam']) {
      const opt = el('button', 'segb' + (b.type === t ? ' on' : ''), t);
      opt.type = 'button';
      opt.title = t === 'spam'
        ? (wheel ? 'a notch again and again across the width of the block'
                 : 'pressed again and again across the width of the block')
        : (wheel ? 'one notch, at the left edge of the block'
                 : 'pressed once, held for the width of the block');
      opt.onclick = () => {
        b.type = t;
        if (t === 'spam') {
          b.rate = b.rate || (wheel ? 40 : 18);
          if (!wheel) b.hold = b.hold || 9;
        }
        render();
        onChange();
      };
      type.append(opt);
    }
    host.append(field('type', type));
    host.append(field('starts at', num(b, 'at', 0, 60000), 'ms'));
    host.append(field('length', num(b, 'ms', wheel ? 0 : 1, 60000), 'ms'));
    if (b.type === 'spam') {
      host.append(field(wheel ? 'a notch every' : 'press every',
                        num(b, 'rate', 1, 60000), 'ms'));
      if (!wheel) host.append(field('holding for', num(b, 'hold', 1, 60000), 'ms'));
    }
    if (wheel) {
      /* One event can turn the wheel further than one notch. A game that
         counts events sees no difference; one that reads the delta sees
         three lines where a hand would have given it one. */
      host.append(field('notches', num(b, 'notches', 1, 16), 'per event'));
    }

    /* What the block actually costs in events, which is not obvious once a
       spam block is a few hundred ms wide: the width and the rate together
       decide it, and both are being dragged. */
    const count = eventCount(sel.lane, b);
    let note = wheel ? count + (count === 1 ? ' notch' : ' notches')
                     : count + (count === 1 ? ' press' : ' presses');
    if (wheel) {
      const total = count * (b.notches || 1);
      if (total !== count) note += ' — ' + total + ' lines of scroll in all';
    } else if (b.type === 'spam') {
      note += ' — ' + (b.rate || 0) + ' ms apart, ending at '
           + (b.at + b.ms) + ' ms';
    }
    host.append(el('span', 'trknote', note));

    const del = el('button', 'btn', 'Delete block');
    del.type = 'button';
    del.onclick = removeSelected;
    host.append(del);
  }

  function removeSelected() {
    if (!sel) return;
    const i = sel.lane.blocks.indexOf(sel.block);
    if (i >= 0) sel.lane.blocks.splice(i, 1);
    sel = null;
    render();
    onChange();
  }

  function field(label, node, unit) {
    const w = el('label', 'trkf');
    w.append(el('span', null, label));
    w.append(node);
    if (unit) w.append(el('span', 'trkunit', unit));
    return w;
  }

  function num(obj, key, lo, hi) {
    const n = el('input');
    n.type = 'number';
    n.className = 'num';
    n.min = lo;
    n.max = hi;
    n.value = obj[key];
    n.onchange = () => {
      obj[key] = Math.max(lo, Math.min(hi, parseInt(n.value, 10) || lo));
      render();
      onChange();
    };
    return n;
  }

  document.addEventListener('keydown', (e) => {
    const dlg = $('macroedit');
    if (!sel || !dlg || !dlg.open) return;
    if (e.target.tagName === 'INPUT' || e.target.tagName === 'SELECT') return;
    if (e.key === 'Delete') {
      e.preventDefault();
      removeSelected();
    }
  });

  window.Track = {
    mount(initial, macros, changed) {
      lanes = JSON.parse(JSON.stringify(initial || []));
      macroList = macros || [];
      onChange = changed || (() => {});
      sel = null;
      this.fit();
    },
    lanes: () => JSON.parse(JSON.stringify(lanes)),
    addLane(lane) {
      lanes.push(Object.assign({ blocks: [] }, lane));
      render();
      onChange();
    },
    zoom(f) {
      pxms = Math.max(0.2, Math.min(10, pxms * f));
      render();
    },
    /* Open showing the whole macro. A timeline that starts scrolled sideways
       is a timeline whose shape you cannot see. */
    fit() {
      const host = $('trklanes');
      const width = host ? host.clientWidth - 130 : 0;
      if (width > 80) pxms = Math.max(0.2, Math.min(10, width / span()));
      render();
    },
    select(li, bi) {
      const lane = lanes[li];
      if (!lane || !lane.blocks[bi]) return false;
      sel = { lane: lane, block: lane.blocks[bi] };
      render();
      return true;
    },
    empty: () => lanes.length === 0,
  };
})();

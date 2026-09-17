// Frida agent: log every HID exchange across all three layers used by X3.exe:
//   hiddriver_1/2.dll  ->  SetFeature / GetFeature / Hid_Write / Hid_Read
//   hidapi.dll         ->  _hid_send_feature_report@12 / _hid_write@12 / ...
//   hid.dll            ->  HidD_SetFeature / HidD_GetFeature
'use strict';

const handlePaths = {};

function findExport(mod, name) {
  try {
    const m = (Process.findModuleByName || Process.getModuleByName).call(Process, mod);
    if (m) { const p = m.findExportByName(name); if (p) return p; }
  } catch (e) {}
  try { if (Module.findExportByName) return Module.findExportByName(mod, name); } catch (e) {}
  return null;
}

function hex(ptr, len) {
  if (ptr.isNull() || len <= 0 || len > 4096) return null;
  try { return Array.from(new Uint8Array(ptr.readByteArray(len)))
      .map(b => b.toString(16).padStart(2, '0')).join(' '); }
  catch (e) { return null; }
}

const lastSeen = {};
function emit(o) {
  if (o.dir === '--' && (o.op === 'open' || /_hid_enumerate/.test(o.op))) {
    const k = o.op + '|' + o.data;
    if (lastSeen[k]) { lastSeen[k]++; return; }   // collapse the polling loop
    lastSeen[k] = 1;
  }
  send(o);
}

function shortPath(h) {
  const p = handlePaths[h.toString()];
  if (!p) return '';
  const m = p.match(/vid_([0-9a-f]+)&pid_([0-9a-f]+)(?:&mi_([0-9a-f]+))?(?:&col([0-9a-f]+))?/i);
  return m ? `${m[1]}:${m[2]} mi${m[3] || '?'} col${m[4] || '?'}` : p;
}

// ---- CreateFileW: map handles to device paths -------------------------------
(function () {
  const f = findExport('kernel32.dll', 'CreateFileW');
  if (!f) return;
  Interceptor.attach(f, {
    onEnter(a) { try { this.path = a[0].readUtf16String(); } catch (e) { this.path = null; } },
    onLeave(r) {
      if (this.path && /hid#/i.test(this.path)) {
        handlePaths[r.toString()] = this.path;
        emit({ dir: '--', op: 'open', data: this.path });
      }
    }
  });
})();

// ---- generic (buffer, length) logger ----------------------------------------
// `mode` is 'tx' (dump on entry) or 'rx' (dump on return).
const done = {};
function traceBuf(mod, name, bufIdx, lenIdx, mode, fixedLen) {
  const key = mod + '!' + name;
  if (done[key]) return false;
  const f = findExport(mod, name);
  if (!f) return false;
  done[key] = true;
  Interceptor.attach(f, {
    onEnter(a) {
      this.buf = a[bufIdx];
      this.n = fixedLen || (lenIdx >= 0 ? a[lenIdx].toInt32() : 64);
      this.args0 = a[0].toString();
      if (mode === 'tx')
        emit({ dir: 'TX', op: `${mod}!${name}`, dev: shortPath(a[0]),
               len: this.n, data: hex(this.buf, this.n) });
    },
    onLeave(r) {
      if (mode === 'rx')
        emit({ dir: 'RX', op: `${mod}!${name}`, ret: r.toInt32(),
               len: this.n, data: hex(this.buf, this.n) });
    }
  });
  emit({ dir: '--', op: 'hooked', data: `${mod}!${name}` });
  return true;
}

function traceInts(mod, name, count) {
  const key = mod + '!' + name;
  if (done[key]) return false;
  const f = findExport(mod, name);
  if (!f) return false;
  done[key] = true;
  Interceptor.attach(f, {
    onEnter(a) {
      const v = [];
      for (let i = 0; i < count; i++) v.push(a[i].toInt32());
      emit({ dir: '--', op: `${mod}!${name}`, data: v.map(x => '0x' + (x >>> 0).toString(16)).join(' ') });
    }
  });
  emit({ dir: '--', op: 'hooked', data: `${mod}!${name}` });
  return true;
}

function installAll() {
  let n = 0;
  for (const m of ['hiddriver_1.dll', 'hiddriver_2.dll']) {
    n += traceInts(m, 'Set_VIDPID', 2) ? 1 : 0;
    n += traceInts(m, 'Open_FeatureDevice', 2) ? 1 : 0;
    n += traceBuf(m, 'SetFeature', 0, 1, 'tx') ? 1 : 0;
    n += traceBuf(m, 'GetFeature', 0, 1, 'rx') ? 1 : 0;
    n += traceBuf(m, 'Hid_Write', 0, 1, 'tx') ? 1 : 0;
    n += traceBuf(m, 'Hid_Read', 0, 1, 'rx') ? 1 : 0;
  }
  n += traceInts('hidapi.dll', '_hid_enumerate@8', 2) ? 1 : 0;
  n += traceInts('hidapi.dll', '_hid_open@12', 3) ? 1 : 0;
  n += traceBuf('hidapi.dll', '_hid_send_feature_report@12', 1, 2, 'tx') ? 1 : 0;
  n += traceBuf('hidapi.dll', '_hid_get_feature_report@12', 1, 2, 'rx') ? 1 : 0;
  n += traceBuf('hidapi.dll', '_hid_write@12', 1, 2, 'tx') ? 1 : 0;
  n += traceBuf('hidapi.dll', '_hid_read@12', 1, 2, 'rx') ? 1 : 0;
  n += traceBuf('hidapi.dll', '_hid_read_timeout@16', 1, 2, 'rx') ? 1 : 0;
  n += traceBuf('hid.dll', 'HidD_SetFeature', 1, 2, 'tx') ? 1 : 0;
  n += traceBuf('hid.dll', 'HidD_GetFeature', 1, 2, 'rx') ? 1 : 0;
  n += traceBuf('hid.dll', 'HidD_SetOutputReport', 1, 2, 'tx') ? 1 : 0;
  return n;
}

// ---- install immediately, and again the instant a new DLL loads ------------
let installed = installAll();

for (const ll of ['LoadLibraryW', 'LoadLibraryA', 'LoadLibraryExW', 'LoadLibraryExA']) {
  const f = findExport('kernel32.dll', ll);
  if (!f) continue;
  Interceptor.attach(f, { onLeave() { installed += installAll(); } });
}

// backup sweep for anything loaded by a path we did not see
let tries = 0;
const timer = setInterval(() => {
  installed += installAll();
  if (++tries > 100) clearInterval(timer);
}, 100);

emit({ dir: '--', op: 'ready', data: `${installed} hook(s) installed at load` });

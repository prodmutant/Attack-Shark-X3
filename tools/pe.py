"""Minimal PE reader: sections, imports, RVA<->file-offset."""
import struct

class PE:
    def __init__(self, path):
        self.d = open(path, 'rb').read()
        d = self.d
        pe = struct.unpack_from('<I', d, 0x3c)[0]
        assert d[pe:pe+4] == b'PE\0\0', "not a PE"
        self.pe = pe
        self.machine = struct.unpack_from('<H', d, pe+4)[0]
        self.nsec = struct.unpack_from('<H', d, pe+6)[0]
        self.optsz = struct.unpack_from('<H', d, pe+20)[0]
        magic = struct.unpack_from('<H', d, pe+24)[0]
        self.pe32plus = (magic == 0x20b)
        self.imagebase = (struct.unpack_from('<Q', d, pe+24+24)[0] if self.pe32plus
                          else struct.unpack_from('<I', d, pe+24+28)[0])
        dd = pe + 24 + (112 if self.pe32plus else 96)
        self.datadirs = [struct.unpack_from('<II', d, dd + i*8) for i in range(16)]
        so = pe + 24 + self.optsz
        self.secs = []
        for i in range(self.nsec):
            s = d[so+i*40: so+(i+1)*40]
            name = s[:8].rstrip(b'\0').decode('latin1')
            vsz, va, rsz, pr = struct.unpack_from('<IIII', s, 8)
            self.secs.append(dict(name=name, va=va, vsz=vsz, rsz=rsz, pr=pr))

    def rva2off(self, rva):
        for s in self.secs:
            if s['va'] <= rva < s['va'] + max(s['vsz'], s['rsz']):
                return s['pr'] + (rva - s['va'])
        return None

    def off2rva(self, off):
        for s in self.secs:
            if s['pr'] <= off < s['pr'] + s['rsz']:
                return s['va'] + (off - s['pr'])
        return None

    def cstr(self, rva):
        o = self.rva2off(rva)
        if o is None: return ''
        e = self.d.index(b'\0', o)
        return self.d[o:e].decode('latin1')

    def imports(self):
        """-> {dll: [(thunk_rva, name)]}"""
        rva, size = self.datadirs[1]
        if not rva: return {}
        o = self.rva2off(rva)
        out = {}
        ptrsz = 8 if self.pe32plus else 4
        fmt = '<Q' if self.pe32plus else '<I'
        i = 0
        while True:
            ent = self.d[o+i*20: o+(i+1)*20]
            if len(ent) < 20 or ent == b'\0'*20: break
            oft, ts, fc, nameRva, firstThunk = struct.unpack('<IIIII', ent)
            dll = self.cstr(nameRva)
            lookup = oft or firstThunk
            lo = self.rva2off(lookup)
            items = []
            j = 0
            while lo is not None:
                v = struct.unpack_from(fmt, self.d, lo + j*ptrsz)[0]
                if v == 0: break
                ordinal_flag = (1 << (63 if self.pe32plus else 31))
                if v & ordinal_flag:
                    nm = f"#{v & 0xffff}"
                else:
                    no = self.rva2off(v)
                    nm = self.d[no+2: self.d.index(b'\0', no+2)].decode('latin1')
                items.append((firstThunk + j*ptrsz, nm))
                j += 1
            out[dll] = items
            i += 1
        return out

import struct,sys
def exports(path):
    d=open(path,'rb').read()
    pe=struct.unpack_from('<I',d,0x3c)[0]
    if d[pe:pe+4]!=b'PE\0\0': return []
    nsec=struct.unpack_from('<H',d,pe+6)[0]
    optsz=struct.unpack_from('<H',d,pe+20)[0]
    magic=struct.unpack_from('<H',d,pe+24)[0]
    dd=pe+24+(96 if magic==0x10b else 112)
    erva,esz=struct.unpack_from('<II',d,dd)
    if not erva: return []
    secs=[]
    off=pe+24+optsz
    for i in range(nsec):
        s=d[off+i*40:off+(i+1)*40]
        va=struct.unpack_from('<I',s,12)[0]; rs=struct.unpack_from('<I',s,16)[0]
        pr=struct.unpack_from('<I',s,20)[0]
        secs.append((va,rs,pr))
    def r2o(rva):
        for va,rs,pr in secs:
            if va<=rva<va+max(rs,1): return pr+(rva-va)
        return None
    eo=r2o(erva)
    nnames=struct.unpack_from('<I',d,eo+24)[0]
    anames=struct.unpack_from('<I',d,eo+32)[0]
    out=[]
    no=r2o(anames)
    for i in range(nnames):
        nr=struct.unpack_from('<I',d,no+i*4)[0]
        o=r2o(nr)
        e=d.index(b'\0',o)
        out.append(d[o:e].decode('latin1'))
    return out
for p in sys.argv[1:]:
    print(f"=== {p.split('/')[-1]} ===")
    try:
        for e in exports(p): print("   ",e)
    except Exception as ex: print("   err",ex)

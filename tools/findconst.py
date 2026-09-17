import sys,re,struct
path=sys.argv[1]
d=open(path,'rb').read()
targets={}
for name,val in [("0x1d57",0x1d57),("0xfa60",0xfa60),("0x3151",0x3151),("0x502d",0x502d)]:
    b=struct.pack('<H',val)
    idx=[m.start() for m in re.finditer(re.escape(b),d)]
    targets[name]=idx
for k,v in targets.items():
    print(f"{k}: {len(v)} hits")
    for off in v[:12]:
        ctx=d[max(0,off-12):off+12]
        print(f"   @0x{off:06x}  {ctx.hex(' ')}")

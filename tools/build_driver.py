"""Build and test-sign asxfilter.sys.

Deliberately does not use a .vcxproj. The WDK's Visual Studio integration
targets specific VS versions and this machine has VS 18, so rather than depend
on that matching, this drives cl.exe and link.exe directly with the include and
library paths the WDK would have supplied. It is also a readable statement of
what a KMDF driver build actually is, which a project file is not.

    python tools/build_driver.py                # build, catalog, test-sign
    python tools/build_driver.py --no-sign      # just the .sys
    python tools/build_driver.py --kmdf 1.31    # pin the KMDF version

Output lands in driver/out/: asxfilter.sys, asxfilter.inf, asxfilter.cat and
the test certificate that tools/install_driver.ps1 has to trust.
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "driver" / "asxfilter"
BUILD = ROOT / "driver" / "build"
OUT = ROOT / "driver" / "out"

SOURCES = ["driver.c", "filter.c", "inject.c"]
CERT_SUBJECT = "CN=attackshark-x3 test signing"

#: NTDDI_WIN10_VB (2004). ExAllocatePool2 arrived here; anything older has to
#: fall back to ExAllocatePoolWithTag.
NTDDI = "0x0A000008"


def fail(msg):
    print("error: " + msg, file=sys.stderr)
    raise SystemExit(1)


def find_msvc():
    """Newest MSVC toolset, via vswhere."""
    vswhere = Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) \
        / "Microsoft Visual Studio" / "Installer" / "vswhere.exe"
    roots = []
    if vswhere.exists():
        out = subprocess.run([str(vswhere), "-products", "*", "-latest",
                              "-property", "installationPath"],
                             capture_output=True, text=True).stdout.strip()
        if out:
            roots.append(Path(out))
    for base in (r"C:\Program Files\Microsoft Visual Studio",
                 r"C:\Program Files (x86)\Microsoft Visual Studio"):
        p = Path(base)
        if p.is_dir():
            for ver in sorted(p.iterdir(), reverse=True):
                for ed in ("Community", "Professional", "Enterprise", "BuildTools"):
                    if (ver / ed / "VC").is_dir():
                        roots.append(ver / ed)

    for root in roots:
        tools = root / "VC" / "Tools" / "MSVC"
        if not tools.is_dir():
            continue
        for ver in sorted(tools.iterdir(), key=lambda p: _vkey(p.name), reverse=True):
            if (ver / "bin" / "Hostx64" / "x64" / "cl.exe").exists():
                return ver
    fail("no MSVC toolset with a x64 cl.exe was found")


def _vkey(name):
    return tuple(int(x) for x in re.findall(r"\d+", name))


def find_wdk(kmdf=None):
    """Kit root, SDK version and KMDF version that this machine can build."""
    base = Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) \
        / "Windows Kits" / "10"
    if not base.is_dir():
        fail("Windows Kits 10 is not installed")

    versions = [d.name for d in (base / "Include").iterdir()
                if (d / "km" / "ntddk.h").exists()]
    if not versions:
        fail("the SDK is installed but the WDK is not: no Include\\<ver>\\km\\ntddk.h.\n"
             "  winget install --id Microsoft.WindowsWDK.10.0.26100")
    sdk = max(versions, key=_vkey)

    kmdfs = sorted((d.name for d in (base / "Include" / "wdf" / "kmdf").iterdir()),
                   key=_vkey, reverse=True)
    if kmdf:
        if kmdf not in kmdfs:
            fail(f"KMDF {kmdf} is not in the WDK (have: {', '.join(kmdfs)})")
        chosen = kmdf
    else:
        chosen = inbox_kmdf() or kmdfs[0]
        if chosen not in kmdfs:
            fail(f"this system has KMDF {chosen} but the WDK only ships {', '.join(kmdfs)}")
    return base, sdk, chosen


def inbox_kmdf():
    """KMDF version of the running system.

    A driver linked against a newer KMDF than Wdf01000.sys provides builds
    happily and then refuses to load, so the default is whatever is inbox
    rather than whatever is newest.
    """
    wdf = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "drivers" / "Wdf01000.sys"
    if not wdf.exists():
        return None
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             f"(Get-Item '{wdf}').VersionInfo.FileVersion"],
            capture_output=True, text=True).stdout.strip()
        m = re.match(r"(\d+)\.(\d+)", out)
        return f"{m.group(1)}.{m.group(2)}" if m else None
    except Exception:
        return None


def run(cmd, env=None, cwd=None):
    r = subprocess.run(cmd, env=env, cwd=cwd, capture_output=True, text=True)
    if r.returncode != 0:
        sys.stdout.write(r.stdout)
        sys.stderr.write(r.stderr)
        fail(f"{Path(cmd[0]).name} failed ({r.returncode})")
    return r.stdout


def build(args):
    msvc = find_msvc()
    kit, sdk, kmdf = find_wdk(args.kmdf)

    binx64 = msvc / "bin" / "Hostx64" / "x64"
    cl, link = binx64 / "cl.exe", binx64 / "link.exe"

    inc = kit / "Include" / sdk
    lib = kit / "Lib" / sdk
    kmdf_inc = kit / "Include" / "wdf" / "kmdf" / kmdf
    kmdf_lib = kit / "Lib" / "wdf" / "kmdf" / "x64" / kmdf

    for p in (inc / "km", inc / "shared", kmdf_inc, lib / "km" / "x64", kmdf_lib):
        if not p.is_dir():
            fail(f"missing from the kit: {p}")

    print(f"  MSVC   {msvc.name}")
    print(f"  SDK    {sdk}")
    print(f"  KMDF   {kmdf}" + ("" if args.kmdf else f"   (inbox: {inbox_kmdf()})"))

    BUILD.mkdir(parents=True, exist_ok=True)
    OUT.mkdir(parents=True, exist_ok=True)

    env = dict(os.environ)
    env["PATH"] = str(binx64) + os.pathsep + env.get("PATH", "")

    major, minor = kmdf.split(".")
    cflags = [
        "/c", "/nologo", "/W4", "/WX-", "/O2", "/Oi", "/Gy", "/GS",
        "/kernel",                       # kernel-mode code generation
        "/Zi", "/FS",
        "/D_AMD64_", "/DAMD64", "/D_WIN64", "/DNDEBUG",
        f"/DNTDDI_VERSION={NTDDI}", "/D_WIN32_WINNT=0x0A00", "/DWINVER=0x0A00",
        f"/DKMDF_VERSION_MAJOR={major}", f"/DKMDF_VERSION_MINOR={minor}",
        f"/I{kmdf_inc}", f"/I{inc / 'km'}", f"/I{inc / 'shared'}",
        f"/I{inc / 'km' / 'crt'}", f"/I{msvc / 'include'}",
        f"/Fd{BUILD / 'asxfilter.pdb'}",
    ]

    objs = []
    for name in SOURCES:
        obj = BUILD / (Path(name).stem + ".obj")
        print(f"  cl     {name}")
        out = run([str(cl)] + cflags + [f"/Fo{obj}", str(SRC / name)], env=env, cwd=str(SRC))
        for line in out.splitlines():
            if "warning" in line.lower():
                print("         " + line.strip())
        objs.append(str(obj))

    sys_path = OUT / "asxfilter.sys"
    lflags = [
        "/NOLOGO", "/MACHINE:X64", "/DRIVER", "/KERNEL",
        "/SUBSYSTEM:NATIVE,10.00", "/ENTRY:FxDriverEntry",
        "/NODEFAULTLIB", "/INTEGRITYCHECK", "/RELEASE",
        "/OPT:REF", "/OPT:ICF", "/MANIFEST:NO",
        "/NXCOMPAT", "/DYNAMICBASE",
        "/MERGE:_TEXT=.text", "/MERGE:_PAGE=PAGE",
        "/DEBUG", f"/PDB:{BUILD / 'asxfilter.pdb'}",
        f"/LIBPATH:{lib / 'km' / 'x64'}", f"/LIBPATH:{kmdf_lib}",
        "ntoskrnl.lib", "hal.lib", "wmilib.lib", "BufferOverflowFastFailK.lib",
        "WdfDriverEntry.lib", "WdfLdr.lib",
        f"/OUT:{sys_path}",
    ]
    print("  link   asxfilter.sys")
    run([str(link)] + lflags + objs, env=env)

    stage_inf(kit, sdk, kmdf)
    if not args.no_sign:
        catalog_and_sign(kit, sdk)

    print(f"\n  {sys_path}  ({sys_path.stat().st_size} bytes)")
    for f in sorted(OUT.iterdir()):
        if f.name != "asxfilter.sys":
            print(f"  {f}")


def kit_bin(kit, sdk, name, arch="x64"):
    p = kit / "bin" / sdk / arch / name
    if p.exists():
        return p
    for d in sorted((kit / "bin").iterdir(), reverse=True):
        q = d / arch / name
        if q.exists():
            return q
    fail(f"{name} not found under {kit / 'bin'}")


def stage_inf(kit, sdk, kmdf):
    """Copy the INF to the output and stamp date, version and KMDF into it."""
    dst = OUT / "asxfilter.inf"
    shutil.copy2(SRC / "asxfilter.inf", dst)
    stampinf = kit_bin(kit, sdk, "stampinf.exe")
    print("  stamp  asxfilter.inf")
    run([str(stampinf), "-f", str(dst), "-d", "*", "-a", "amd64",
         "-v", "1.0.0.0", "-k", kmdf])


def catalog_and_sign(kit, sdk):
    inf2cat = kit_bin(kit, sdk, "inf2cat.exe", arch="x86")
    signtool = kit_bin(kit, sdk, "signtool.exe")

    print("  cat    asxfilter.cat")
    run([str(inf2cat), f"/driver:{OUT}", "/os:10_X64", "/uselocaltime"])

    thumb = ensure_cert()
    print(f"  sign   {thumb[:16]}...")
    for target in ("asxfilter.sys", "asxfilter.cat"):
        run([str(signtool), "sign", "/fd", "sha256", "/sha1", thumb,
             "/s", "My", str(OUT / target)])

    cer = OUT / "asxfilter-test.cer"
    run(["powershell", "-NoProfile", "-Command",
         f"Export-Certificate -Cert Cert:\\CurrentUser\\My\\{thumb} "
         f"-FilePath '{cer}' -Force | Out-Null"])


def ensure_cert():
    """Reuse the test code-signing certificate, or make one.

    Self-signed and local only. It proves nothing about provenance; it exists
    because a test-signing kernel has to chain the driver to *some* trusted
    root, and install_driver.ps1 is what puts this one there.
    """
    ps = (
        f"$c = Get-ChildItem Cert:\\CurrentUser\\My | "
        f"Where-Object {{ $_.Subject -eq '{CERT_SUBJECT}' }} | "
        f"Sort-Object NotAfter -Descending | Select-Object -First 1; "
        f"if (-not $c) {{ $c = New-SelfSignedCertificate -Type CodeSigningCert "
        f"-Subject '{CERT_SUBJECT}' -CertStoreLocation Cert:\\CurrentUser\\My "
        f"-KeyUsage DigitalSignature -NotAfter (Get-Date).AddYears(5) "
        f"-TextExtension @('2.5.29.37={{text}}1.3.6.1.5.5.7.3.3') }}; "
        f"$c.Thumbprint"
    )
    out = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                         capture_output=True, text=True)
    thumb = out.stdout.strip().splitlines()[-1].strip() if out.stdout.strip() else ""
    if not re.fullmatch(r"[0-9A-Fa-f]{40}", thumb):
        sys.stderr.write(out.stdout + out.stderr)
        fail("could not create or find the test signing certificate")
    return thumb


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--kmdf", help="KMDF version to link against (default: the inbox one)")
    ap.add_argument("--no-sign", action="store_true", help="skip catalog and signing")
    ap.add_argument("--clean", action="store_true", help="delete intermediates first")
    args = ap.parse_args()

    if args.clean:
        for d in (BUILD, OUT):
            shutil.rmtree(d, ignore_errors=True)

    print("building asxfilter.sys")
    build(args)


if __name__ == "__main__":
    main()

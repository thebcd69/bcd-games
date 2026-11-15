#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""env.MADEIRA_WIN64_FRESH: the rebuilt 64-bit Wine set (round 2026-11-15; tools/build-wine-fresh-dlls.sh).

The committed Wine DLLs of app/Madeira/arm64ec-windows and aarch64-windows are frozen binaries. The rebuilt set ships next to them as <name>-fresh.<ext>
and is used only with env.MADEIRA_WIN64_FRESH = 1. What this check holds the change to:

  Lists    every tracked file of both folders is either a Wine module of tools/wine-fresh-modules.txt (with a make target of its own arch) or in
           tools/wine-fresh-not-wine.txt, nothing twice, and the not-Wine files are not Wine's.
  Key off  the unix loader's ntdll choice is compared with the function as it was before this change (the old code is DERIVED from the real source by taking
           out what the change added, so the test does not go stale when the neighbouring code is edited; at the time of writing the derivation equalled the
           text of 95e0fc7e (loader) and main 642a42e1 (bridge blocks) byte for byte, checked once) for every combination
           of ntdll-sync/xsec switches, files present and PE folder, with the key unset, "0", "", "true", "11": same answer AND same log lines.
           The session bridge's links are compared the same way: the new fresh block plus the new opt-in loop, kernel32-usd block and sync/xsec blocks
           against the old three (main's at 642a42e1, derived as above: kernel32-usd.dll is the default 64-bit kernel32 there), on a bundle and a prefix with every
           combination, for an ARM64EC and an ARM64 session: the links AND the log lines are identical afterwards.
           Both pieces of code are cut from the real sources and run on the host (a C harness and an Objective-C harness).
  Key on   the same two harnesses: kernelbase.dll and the other fresh files replace the farm's link in sysx64 (and system32 in an ARM64EC session), the
           aarch64 ones in sysaa64 (and system32 otherwise); ntdll.dll is linked to the file the loader maps (ntdll-fresh.dll, or its -xsec / -sync variant,
           with the older variants as the fall back when the file is missing): one ntdll per process, named alike by both pieces of code.
           kernel32: the rebuilt kernel32 carries tools/patch-wine-kernel32-usd-direct.py; with the key on it is the one linked (the kernel32-usd block runs last
           and leaves it), with the key off kernel32-usd.dll is; env.MADEIRA_USD_DIRECT = 0 keeps the committed kernel32 even with the key on.
  Plumbing the key is read as exactly "1" by both, unset at the end of a session, an allowed list name with a 0/1 check, a catalog entry; the build script
           stages the pinned source (git archive), applies only existing scripts, writes only *-fresh files, never touches the committed ones; the workflow
           builds it after the extra DLLs and verifies it; each patch script of its list applies, in order, to the pinned source (when it is checked out).
  PE tool  tools/wine-fresh-pe.py: "same" ignores time stamps, import hints and zero fill and nothing else, on the real committed files.
"""
from pathlib import Path
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[2]
ok = True


def check(what, cond, detail=""):
    global ok
    print(("ok   " if cond else "FAIL ") + what + (("\n     " + str(detail)[:1800]) if (detail and not cond) else ""))
    ok = ok and bool(cond)


def read(p):
    return (root / p).read_text()


def tsv(p):
    rows = []
    for line in read(p).splitlines():
        if line and not line.startswith("#"):
            rows.append(tuple(line.split("\t")))
    return rows


# ---------------------------------------------------------------- lists
mods = tsv("tools/wine-fresh-modules.txt")
notw = tsv("tools/wine-fresh-not-wine.txt")
mod_keys = [(a, n) for a, n, _t in mods]
notw_keys = [(a, n) for a, n, _r in notw]
check("no module is listed twice, none is also a not-Wine file", len(set(mod_keys)) == len(mod_keys) and not set(mod_keys) & set(notw_keys))
check("every make target is dlls/<d>/<arch>-windows/<file> or programs/<p>/<arch>-windows/<file> of its own arch and ends in the listed file name (lower case)",
      all(re.fullmatch(r"(dlls|programs)/[^/]+/%s-windows/[^/]+" % a, t) and t.rsplit("/", 1)[1].lower() == n.lower() for a, n, t in mods))
tracked = {}
r = subprocess.run(["git", "-C", str(root), "ls-files", "app/Madeira/arm64ec-windows", "app/Madeira/aarch64-windows"], capture_output=True, text=True)
if r.returncode == 0 and r.stdout:
    for p in r.stdout.split():
        arch = "arm64ec" if "/arm64ec-windows/" in p else "aarch64"
        tracked[(arch, p.rsplit("/", 1)[1])] = p
    listed = set(mod_keys) | set(notw_keys)
    check("every tracked file of both folders is in the module list or the not-Wine list",
          set(tracked) <= listed, sorted(set(tracked) - listed))
    check("every listed file is tracked (a stale list names a file the folders no longer have)", listed <= set(tracked), sorted(listed - set(tracked)))
else:
    print("SKIP: no git checkout; the lists are not compared with the tracked files")
check("the DXMT DLLs and the DXMT agent's d3d9 and d3d10core are not rebuilt here",
      not any(n in ("d3d9.dll", "d3d10core.dll", "d3d11.dll", "dxgi.dll", "winemetal.dll") for _a, n in mod_keys))

# ---------------------------------------------------------------- the key is read as exactly "1" and unset at the end of a session
loader = read("build/ntdll-unix/loader_ios.c")
bridge = read("app/Madeira/WineProcessBridge.m")
check("the unix loader reads MADEIRA_WIN64_FRESH as exactly 1",
      'getenv( "MADEIRA_WIN64_FRESH" )' in loader and "return e && e[0] == '1' && !e[1];" in loader[loader.index("ios_win64_fresh_on"):][:400])
check("the session bridge reads MADEIRA_WIN64_FRESH as exactly 1 and unsets it at the end of the session",
      'getenv("MADEIRA_WIN64_FRESH")' in bridge and "freshKey[0] == '1' && !freshKey[1]" in bridge and 'unsetenv("MADEIRA_WIN64_FRESH")' in bridge)
check("the fresh block runs before the opt-in loop, so a game's opt-in DLL keeps its variant",
      0 < bridge.index("BOOL freshNtdll = NO;") < bridge.index("env.MADEIRA_OPTIN_DLLS = dxva2,evr"))
check("the old sync and xsec links give way only to a fresh ntdll that was linked",
      "if (ntSyncOn && !freshNtdll && [fm fileExistsAtPath:syncDll])" in bridge and "if (xsecOn && !freshNtdll && [fm fileExistsAtPath:xsecDll])" in bridge)
policy = read("app/Madeira/GameRecommendationsList.swift")
check("the list policy allows the name and checks 0 or 1",
      '"MADEIRA_WIN64_FRESH",' in policy and 'case "MADEIRA_WIN64_FRESH":' in policy and "MADEIRA_WIN64_FRESH is 0 or 1" in policy)
gen = read("build/tools/gen-config-catalog.py")
check("the settings catalog knows the key (default off)", '"env.MADEIRA_WIN64_FRESH": {' in gen and
      re.search(r'"env\.MADEIRA_WIN64_FRESH": \{[^}]*"default": "0"', gen, re.S) is not None)

# ---------------------------------------------------------------- the build script
script = read("tools/build-wine-fresh-dlls.sh")
b = subprocess.run(["bash", "-n", str(root / "tools/build-wine-fresh-dlls.sh")], capture_output=True, text=True)
check("the build script has no syntax error", b.returncode == 0, b.stderr)
plist = re.search(r'FRESH_PATCHES="\n(.*?)"\n', script, re.S)
patches = [l.split() for l in plist.group(1).splitlines() if l.strip()] if plist else []
check("the patch list names only scripts that exist, each once", patches and all((root / "tools" / p[0]).is_file() for p in patches)
      and len({p[0] for p in patches}) == len(patches), [p[0] for p in patches if not (root / "tools" / p[0]).is_file()])
check("the build script stages the pinned source with git archive and syncs it into the tree, build directories excluded",
      "git -C \"$W\" archive HEAD | tar -x" in script and "rsync -rlpgoD --checksum --delete --exclude '/build-*'" in script)
dests = [m.group(2) for m in re.finditer(r'\b(cp|mv)(?: -p)? "[^"]+" "([^"]+)"', script)] + \
        [a for m in re.finditer(r'\brm -f ([^;&|\n]+)', script) for a in re.findall(r'"([^"]+)"', m.group(1))]
allowed = lambda d: "-fresh" in d or d.startswith(("$STAGE", "$base", "$tmp", "$F/", "$B", "$built", "$out"))   # $out is "$dir/$stem-fresh.$ext"
check("it writes into the two folders only files named *-fresh* (ntdll variants included), and never edits the committed tree",
      dests and all(allowed(d) for d in dests) and "git -C \"$W\" checkout" not in script and "git -C \"$R\" checkout" not in script, [d for d in dests if not allowed(d)])
check("the ntdll variants put the tree back with a plain cp (a restored Makefile.in must be newer than the generated Makefile) and a stale sync_x64 rule is healed before the build",
      'cp "$STAGE/$f" "$F/$f"' in script and 'cp -p "$STAGE/$f"' not in script and 'grep -q sync_x64 "$B/dlls/ntdll/Makefile"' in script
      and 'touch "$F/dlls/ntdll/Makefile.in"' in script)
check("a patch script that does not apply stops the build before anything is built or shipped",
      script.index("did not apply to the pinned source") < script.index("rsync -rlpgoD") < script.index("build_arch arm64ec"))
wf = read(".github/workflows/build-ipa.yml")
check("the workflow builds the set after the extra DLLs and verifies it before the package",
      "bash tools/build-wine-fresh-dlls.sh" in wf and wf.index("bash tools/build-wine-extra-dlls.sh") < wf.index("bash tools/build-wine-fresh-dlls.sh")
      and "check-wine-fresh-shipped.py" in wf and "check-win64-fresh.py" in wf)
check("the shipped-file check exists", (root / "tools/check-wine-fresh-shipped.py").is_file())

# ---------------------------------------------------------------- the PE tool on the real committed files
pe = str(root / "tools/wine-fresh-pe.py")
sample = root / "app/Madeira/arm64ec-windows/advapi32.dll"
if sample.is_file():
    with tempfile.TemporaryDirectory(prefix="win64-fresh-pe-") as t:
        t = Path(t)
        d = bytearray(sample.read_bytes())
        pe_off = struct.unpack_from("<I", d, 0x3c)[0]

        def run(*args):
            return subprocess.run([sys.executable, pe] + [str(a) for a in args], capture_output=True, text=True)

        def put(name, data):
            (t / name).write_bytes(bytes(data))
            return t / name

        # time stamps and checksum
        e = bytearray(d)
        e[pe_off + 8:pe_off + 12] = b"\x12\x34\x56\x78"
        e[pe_off + 24 + 64:pe_off + 24 + 68] = b"\xaa\xbb\xcc\xdd"
        check("a different time stamp and checksum is the same image", run("same", sample, put("ts.dll", e)).returncode == 0)
        # trailing zeros
        check("trailing zero padding is the same image", run("same", sample, put("pad.dll", bytes(d) + b"\0" * 4096)).returncode == 0)
        # a code byte
        sections = []
        nsec = struct.unpack_from("<H", d, pe_off + 6)[0]
        optsz = struct.unpack_from("<H", d, pe_off + 20)[0]
        so = pe_off + 24 + optsz
        for i in range(nsec):
            name = bytes(d[so + 40 * i:so + 40 * i + 8]).rstrip(b"\0").decode("latin1")
            vs, va, rs, rp = struct.unpack_from("<IIII", d, so + 40 * i + 8)
            sections.append((name, vs, rs, rp))
        text = next(s for s in sections if s[0] == ".text")
        f = bytearray(d)
        f[text[3] + 0x400] ^= 0xFF
        r1 = run("same", sample, put("code.dll", f))
        check("one changed byte of code is a different image", r1.returncode == 1 and ".text" in r1.stdout, r1.stdout + r1.stderr)
        # an import hint
        imp_rva, _ = struct.unpack_from("<II", d, pe_off + 24 + 112 + 8)

        def rva2off(rva):
            for i in range(nsec):
                vs, va, rs, rp = struct.unpack_from("<IIII", d, so + 40 * i + 8)
                if va <= rva < va + max(vs, rs):
                    return rva - va + rp
            return None
        desc = rva2off(imp_rva)
        ilt, _ts, _fc, nm, iat = struct.unpack_from("<IIIII", d, desc)
        first = struct.unpack_from("<Q", d, rva2off(ilt or iat))[0]
        hoff = rva2off(first & 0x7fffffff)
        g = bytearray(d)
        g[hoff:hoff + 2] = struct.pack("<H", struct.unpack_from("<H", d, hoff)[0] ^ 0x55)
        check("a different import hint is the same image", run("same", sample, put("hint.dll", g)).returncode == 0)
        # the import's NAME is not a hint
        h = bytearray(d)
        h[hoff + 2] ^= 0x20
        check("a different import NAME is a different image", run("same", sample, put("name.dll", h)).returncode == 1)
        check("a truncated file is an error or a difference, never the same", run("same", sample, put("cut.dll", d[:len(d) // 2])).returncode != 0)
        check("a file that is not a PE is an error", run("same", sample, put("junk.dll", b"MZ" + b"\0" * 100)).returncode == 2)
        sym = run("symbols", sample)
        check("symbols: the committed advapi32.dll keeps its COFF symbol table, ntdll.dll has none",
              sym.returncode == 0 and run("symbols", root / "app/Madeira/arm64ec-windows/ntdll.dll").returncode == 1)
else:
    print("SKIP: the committed advapi32.dll is not here")

# ---------------------------------------------------------------- the shipped-set check, on the real committed files
ro = None
for tc in sorted((Path.home() / "madeira-bcd/toolchains").glob("llvm-mingw-*/bin")) + sorted((root / "toolchains").glob("llvm-mingw-*/bin")):
    if (tc / "llvm-readobj").is_file():
        ro = str(tc / "llvm-readobj")
        break
ec = root / "app/Madeira/arm64ec-windows"
if ro and (ec / "ntdll.dll").is_file() and (ec / "kernelbase.dll").is_file() and (ec / "advapi32.dll").is_file():
    with tempfile.TemporaryDirectory(prefix="win64-fresh-ship-") as t:
        t = Path(t)
        (t / "arm64ec-windows").mkdir()
        for n in ("ntdll.dll", "kernelbase.dll"):
            shutil.copy(ec / n, t / "arm64ec-windows" / n)
        shutil.copy(ec / "ntdll.dll", t / "arm64ec-windows/ntdll-fresh.dll")
        shutil.copy(ec / "kernelbase.dll", t / "arm64ec-windows/kernelbase-fresh.dll")
        chk = str(root / "tools/check-wine-fresh-shipped.py")
        r = subprocess.run([sys.executable, chk, str(t), ro], capture_output=True, text=True)
        check("the shipped-set check accepts a fresh ntdll and kernelbase that equal the committed ones (machine, exports, the runtime patch's preconditions)",
              r.returncode == 0 and "2 fresh files checked" in r.stdout, r.stdout + r.stderr)
        shutil.copy(ec / "advapi32.dll", t / "arm64ec-windows/kernelbase-fresh.dll")
        r = subprocess.run([sys.executable, chk, str(t), ro], capture_output=True, text=True)
        check("it refuses a fresh file that lacks the exports of the file it replaces", r.returncode == 1 and "kernelbase-fresh.dll lacks exports" in r.stdout, r.stdout + r.stderr)
        shutil.copy(ec / "kernelbase.dll", t / "arm64ec-windows/kernelbase-fresh.dll")
        (t / "arm64ec-windows/ntdll-fresh.dll").unlink()
        r = subprocess.run([sys.executable, chk, str(t), ro], capture_output=True, text=True)
        check("it refuses a folder with fresh files and no ntdll-fresh.dll", r.returncode == 1 and "no ntdll-fresh.dll" in r.stdout, r.stdout + r.stderr)
        b = bytearray((ec / "kernelbase.dll").read_bytes())
        b[b.index(b"Wine builtin DLL"):b.index(b"Wine builtin DLL") + 4] = b"Wane"
        (t / "arm64ec-windows/kernelbase-fresh.dll").write_bytes(bytes(b))
        (t / "arm64ec-windows/ntdll-fresh.dll").write_bytes((ec / "ntdll.dll").read_bytes())
        r = subprocess.run([sys.executable, chk, str(t), ro], capture_output=True, text=True)
        check("it refuses a fresh file without the Wine builtin DLL signature", r.returncode == 1 and "signature" in r.stdout, r.stdout + r.stderr)
        shutil.copy(ec / "kernelbase.dll", t / "arm64ec-windows/kernelbase-fresh.dll")
        (t / "arm64ec-windows/ntdll-fresh.dll").write_bytes((ec / "kernelbase.dll").read_bytes())
        r = subprocess.run([sys.executable, chk, str(t), ro], capture_output=True, text=True)
        check("it refuses an ntdll-fresh.dll that is not ntdll (lost exports, the runtime patch's thunk)", r.returncode == 1, r.stdout + r.stderr)
        if (Path(ro).parent / "llvm-objdump").is_file() and (root / "tools/verify-kernel32-usd.py").is_file() and (ec / "kernel32.dll").is_file():
            shutil.copy(ec / "kernel32.dll", t / "arm64ec-windows/kernel32.dll")
            shutil.copy(ec / "kernel32.dll", t / "arm64ec-windows/kernel32-fresh.dll")
            shutil.copy(ec / "ntdll.dll", t / "arm64ec-windows/ntdll-fresh.dll")
            r = subprocess.run([sys.executable, chk, str(t), ro], capture_output=True, text=True)
            check("it refuses a kernel32-fresh.dll that still builds 0x7ffe0000 in GetTickCount (here the committed kernel32.dll): the rebuilt kernel32 must carry "
                  "tools/patch-wine-kernel32-usd-direct.py, or the key would bring the fault per tick-count read back",
                  r.returncode == 1 and "kernel32-fresh.dll" in r.stdout and "0x7ffe0000" in r.stdout, r.stdout + r.stderr)
else:
    print("SKIP: no llvm-readobj or no committed ntdll/kernelbase/advapi32: the shipped-set check is not run")

# ---------------------------------------------------------------- the farm import check knows the fresh names
sh32 = root / "app/Madeira/arm64ec-windows/shell32.dll"
if sh32.is_file():
    with tempfile.TemporaryDirectory(prefix="win64-fresh-farm-") as t:
        farm = Path(t) / "arm64ec-windows"
        farm.mkdir()
        for n in ("shell32.dll", "shell32-fresh.dll", "shell32-other.dll"):
            shutil.copy(sh32, farm / n)
        r = subprocess.run([sys.executable, str(root / "tools/check-farm-imports.py"), "--warn-only", str(farm)], capture_output=True, text=True)
        out = r.stdout + r.stderr
        check("tools/check-farm-imports.py reads <name>-fresh.<ext> as <name>.<ext>: shell32.dll's accepted delay gap (shdocvw.dll) is accepted for shell32-fresh.dll, "
              "and still a new gap for a file of any other name",
              "shell32-other.dll delay-imports shdocvw.dll" in out and "shell32-fresh.dll delay-imports shdocvw.dll" not in out
              and "shell32.dll delay-imports shdocvw.dll" not in out, out[-600:])
else:
    print("SKIP: no committed shell32.dll")

# ---------------------------------------------------------------- the impact tool (FRESH_AARCH64=patched)
with tempfile.TemporaryDirectory(prefix="win64-fresh-impact-") as t:
    t = Path(t)
    for side in ("pin", "new"):
        for rel, body in (("dlls/a/Makefile.in", "MODULE = a.dll\n"), ("dlls/b/Makefile.in", "MODULE = b.dll\nPARENTSRC = ../a\nSOURCES = f.c\n"), ("dlls/c/Makefile.in", "MODULE = c.dll\nPARENTSRC = ../a\nSOURCES = g.c\n"),
                          ("dlls/w/Makefile.in", "MODULE = w.dll\nIMPORTS = $(VKD3D_PE_LIBS) user32\n"), ("dlls/z/Makefile.in", "MODULE = z.dll\n"),
                          ("programs/p/Makefile.in", "MODULE = p.exe\n"), ("dlls/a/f.c", "int a;\n"), ("libs/vkd3d/x.c", "int v;\n"),
                          ("dlls/ntdll/unix/s.c", "int s;\n"), ("server/t.c", "int t;\n"), ("include/x.h", "int h;\n")):
            (t / side / rel).parent.mkdir(parents=True, exist_ok=True)
            (t / side / rel).write_text(body)
    imp = str(root / "tools/wine-fresh-impact.py")
    (t / "new/dlls/a/f.c").write_text("int a2;\n")
    (t / "new/libs/vkd3d/x.c").write_text("int v2;\n")
    (t / "new/dlls/ntdll/unix/s.c").write_text("int s2;\n")
    (t / "new/server/t.c").write_text("int t2;\n")
    (t / "new/programs/p/new.c").write_text("int n;\n")
    r = subprocess.run([sys.executable, imp, str(t / "pin"), str(t / "new")], capture_output=True, text=True)
    check("the impact tool lists the changed module, the PARENTSRC children that compile the changed file (not the one that does not), the VKD3D users and a program; the unix side and the server are not PE code",
          r.returncode == 0 and r.stdout.split() == ["dlls/a", "dlls/b", "dlls/w", "programs/p"], r.stdout + r.stderr)
    (t / "new/include/x.h").write_text("int h2;\n")
    r = subprocess.run([sys.executable, imp, str(t / "pin"), str(t / "new")], capture_output=True, text=True)
    check("a change outside dlls/, programs/ and libs/vkd3d is refused (exit 2), not guessed", r.returncode == 2 and "include/x.h" in r.stderr, r.stdout + r.stderr)

# ---------------------------------------------------------------- the loader and the bridge, cut from the real sources and run on the host
cc = shutil.which("cc") or shutil.which("clang")
xcrun = shutil.which("xcrun")


def cut(text, start, end):
    i = text.index(start)
    return text[i:text.index(end, i)]


new_loader = cut(loader, "/* madeira-bcd: ntdll-sync.dll, an opt-in ARM64EC ntdll", "/* ntdll-sync.dll's x64 bodies read madeira_sync_x64_off")


def derive_old_loader(new):
    """what the loader's ntdll-name code was before the rebuilt set: without ios_win64_fresh_on() and its branch at the top of ios_ec_ntdll_name"""
    try:
        a = new.index("/* madeira-bcd: env.MADEIRA_WIN64_FRESH = 1 (default off; tools/build-wine-fresh-dlls.sh)")
        b = new.index("static char *ios_ec_ntdll_name(", a)
        t = new[:a] + new[b:]
        i = t.index('    if (ios_win64_fresh_on() && (strstr( pe_dir, "arm64ec" )')
        j = t.index('    if (ios_ntdll_xsec_on() && !strcmp( pe_dir, "/arm64ec-windows" ))', i)
        return (t[:i] + t[j:])[t[:i].index("static int ios_ntdll_sync_mode( void )"):]
    except ValueError as e:
        sys.exit("check-win64-fresh: the loader's rebuilt-set code moved (%s): update derive_old_loader" % e)


old_loader = derive_old_loader(new_loader)
LOADER_H = r'''
#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#define WINE_IOS 1
@BLOCK@
int main(int argc, char **argv)
{
    char *n = ios_ec_ntdll_name( argv[1], argv[2] );
    printf( "%s\n", n ? n : "(null)" );
    return 0;
}
'''
if cc:
    with tempfile.TemporaryDirectory(prefix="win64-fresh-loader-") as t:
        t = Path(t)
        for tag, blk in (("old", old_loader), ("new", new_loader)):
            (t / (tag + ".c")).write_text(LOADER_H.replace("@BLOCK@", blk))
            c = subprocess.run([cc, "-std=gnu11", "-Wall", "-Wno-unused-function", "-o", str(t / tag), str(t / (tag + ".c"))], capture_output=True, text=True)
            check("the %s ntdll name function compiles on the host" % tag, c.returncode == 0, c.stderr[-1200:])
        if (t / "old").exists() and (t / "new").exists():
            files_all = ["ntdll.dll", "ntdll-sync.dll", "ntdll-xsec.dll", "ntdll-fresh.dll", "ntdll-fresh-sync.dll", "ntdll-fresh-xsec.dll"]
            subsets = [files_all, ["ntdll.dll", "ntdll-sync.dll", "ntdll-xsec.dll"], ["ntdll.dll"], ["ntdll.dll", "ntdll-fresh.dll"],
                       ["ntdll.dll", "ntdll-xsec.dll", "ntdll-fresh.dll"], ["ntdll.dll", "ntdll-sync.dll", "ntdll-fresh.dll", "ntdll-fresh-xsec.dll"],
                       ["ntdll.dll", "ntdll-fresh-sync.dll"]]

            def model(fresh, xsec, sync, ec, files):
                """what the loader must map: the older rules, with the rebuilt set first when the key is exactly 1"""
                x = ec and xsec == "1"
                s = ec and sync in ("1", "native")
                if fresh == "1":
                    v = "-xsec" if x else "-sync" if s else ""
                    if "ntdll-fresh%s.dll" % v in files:
                        return "ntdll-fresh%s.dll" % v
                if x and "ntdll-xsec.dll" in files:
                    return "ntdll-xsec.dll"
                if s and "ntdll-sync.dll" in files:
                    return "ntdll-sync.dll"
                return "ntdll.dll"

            def run_loader(tag, d, pe, env):
                e = {k: v for k, v in os.environ.items() if not k.startswith("MADEIRA_")}
                e.update({k: v for k, v in env.items() if v is not None})
                p = subprocess.run([str(t / tag), str(d), "/" + pe + "-windows"], capture_output=True, text=True, env=e)
                return p.stdout.strip(), p.stderr
            bad_off, bad_on, n_off, n_on = [], [], 0, 0
            for si, files in enumerate(subsets):
                d = t / ("bundle%d" % si)
                for pe in ("arm64ec", "aarch64", "i386"):
                    (d / (pe + "-windows")).mkdir(parents=True, exist_ok=True)
                    for f in files:
                        (d / (pe + "-windows") / f).write_text("x")
                for pe in ("arm64ec", "aarch64", "i386"):
                    for xsec in (None, "1"):
                        for sync in (None, "1", "native"):
                            for fresh in (None, "0", "", "true", "11", "1"):
                                env = {"MADEIRA_WIN64_FRESH": fresh, "MADEIRA_NTDLL_XSEC": xsec, "MADEIRA_NTDLL_SYNC": sync}
                                got, gerr = run_loader("new", d, pe, env)
                                name = got.rsplit("/", 1)[-1]
                                want = model(fresh if pe != "i386" else None, xsec, sync, pe == "arm64ec", files)
                                if fresh != "1" or pe == "i386":   # the 32-bit PE folder is never the rebuilt set's: the key changes nothing there
                                    n_off += 1
                                    o, oerr = run_loader("old", d, pe, env)
                                    if (got, gerr) != (o, oerr):
                                        bad_off.append((si, pe, xsec, sync, fresh, got, o, gerr[:120], oerr[:120]))
                                else:
                                    n_on += 1
                                    if name != want or not got.startswith(str(d)):
                                        bad_on.append((si, pe, xsec, sync, name, want))
            check("key off (unset, 0, empty, true, 11): the loader's answer and its log lines are the function of 95e0fc7e's, in %d combinations (the 32-bit folder included: the key changes nothing there)" % n_off,
                  not bad_off and n_off > 300, bad_off[:3])
            check("key on: the loader maps ntdll-fresh.dll / -xsec / -sync as the model says (older variants as the fall back), in %d combinations" % n_on,
                  not bad_on and n_on > 50, bad_on[:3])
            p = subprocess.run([str(t / "new"), str(t / "bundle3"), "/arm64ec-windows"], capture_output=True, text=True,
                               env={**{k: v for k, v in os.environ.items() if not k.startswith("MADEIRA_")}, "MADEIRA_WIN64_FRESH": "1", "MADEIRA_NTDLL_XSEC": "1"})
            check("key on with a variant the build lacks (bundle3 has ntdll-fresh.dll only): one [win64-fresh] line says so and the older ntdll.dll is mapped",
                  "[win64-fresh] this build has no ntdll-fresh-xsec.dll" in p.stderr and p.stdout.strip().endswith("/arm64ec-windows/ntdll.dll"), (p.stdout, p.stderr))
else:
    print("SKIP: no C compiler; the loader is not run")

BRIDGE_H = r'''
#import <Foundation/Foundation.h>
#include <unistd.h>
static void run_blocks(NSFileManager *fm, NSString *bundlePath, NSString *prefix, NSString *sys32Dir, BOOL use_arm64ec)
{
@BLOCK@
}
int main(int argc, char **argv)
{
    @autoreleasepool {
        NSFileManager *fm = [NSFileManager defaultManager];
        NSString *bundle = [NSString stringWithUTF8String:argv[1]], *prefix = [NSString stringWithUTF8String:argv[2]];
        run_blocks(fm, bundle, prefix, [prefix stringByAppendingPathComponent:@"drive_c/windows/system32"], argv[3][0] == '1');
    }
    return 0;
}
'''
new_fresh = cut(bridge, "/* madeira-bcd: env.MADEIRA_WIN64_FRESH = 1 (exactly", "/* madeira-bcd: env.MADEIRA_OPTIN_DLLS = dxva2,evr")
new_rest = cut(bridge, "/* madeira-bcd: env.MADEIRA_OPTIN_DLLS = dxva2,evr", "/* ml719: REPAIR THE SHELL FOLDERS.")   # the opt-in loop, the kernel32-usd block, the sync / xsec blocks


def derive_old_rest(new):
    """main's blocks as they were before the rebuilt set (642a42e1, kernel32-usd.dll included): the new text without the six additions (a comment, the
    freshKernel32 branch of the kernel32-usd block, four !freshNtdll guards of the sync / xsec blocks)"""
    t = new
    try:
        a = t.index("            /* This block runs after the rebuilt-set block and the opt-in loop above")
        t = t[:a] + t[t.index("*/\n", a) + 3:]
        a = t.index("                } else if (freshKernel32) {\n")
        t = t[:a] + t[t.index("                } else if ([fm fileExistsAtPath:usdSrc]) {\n", a):]
        for guarded, plain in (("if (ntSyncOn && !freshNtdll && [fm fileExistsAtPath:syncDll]) {", "if (ntSyncOn && [fm fileExistsAtPath:syncDll]) {"),
                               ("} else if (ntSyncOn && !freshNtdll) {", "} else if (ntSyncOn) {"),
                               ("if (xsecOn && !freshNtdll && [fm fileExistsAtPath:xsecDll]) {", "if (xsecOn && [fm fileExistsAtPath:xsecDll]) {"),
                               ("} else if (xsecOn && !freshNtdll) {", "} else if (xsecOn) {")):
            if t.count(guarded) != 1:
                raise ValueError(guarded)
            t = t.replace(guarded, plain)
    except ValueError as e:
        sys.exit("check-win64-fresh: the bridge's rebuilt-set additions moved (%s): update derive_old_rest" % e)
    return t


old_rest = derive_old_rest(new_rest)
check("the six additions are the only places the opt-in loop, the kernel32-usd block and the sync / xsec blocks know about the rebuilt set (nothing else mentions it)",
      "freshNtdll" not in old_rest and "freshKernel32" not in old_rest and "kernel32-fresh" not in old_rest and "MADEIRA_WIN64_FRESH" not in old_rest
      and old_rest.count("MADEIRA_USD_DIRECT") == new_rest.count("MADEIRA_USD_DIRECT"))
check("the kernel32-usd block runs after the rebuilt-set block and after the opt-in loop, before the sync / xsec blocks (the order that decides which kernel32 is linked last)",
      bridge.index("BOOL freshKernel32 = NO;") < bridge.index("env.MADEIRA_OPTIN_DLLS = dxva2,evr") < bridge.index("const char *usdDirect = getenv(\"MADEIRA_USD_DIRECT\");")
      < bridge.index("madeira-bcd: ntdll-sync.dll (tools/patch-wine-ntdll-sync.py, built by"))
check("the rebuilt-set block leaves kernel32-fresh.dll unlinked when env.MADEIRA_USD_DIRECT is 0 (it wins over the key), and says freshKernel32 only for the arm64ec file",
      'BOOL usdOff = usdKey && !strcmp(usdKey, "0");' in bridge and 'if (usdOff && [target isEqualToString:@"kernel32.dll"]) continue;' in bridge
      and 'if (k == 0 && [target isEqualToString:@"kernel32.dll"]) freshKernel32 = YES;' in bridge)
if xcrun:
    with tempfile.TemporaryDirectory(prefix="win64-fresh-bridge-") as t:
        t = Path(t)
        built = {}
        for tag, blk in (("old", old_rest), ("new", new_fresh + new_rest)):
            (t / (tag + ".m")).write_text(BRIDGE_H.replace("@BLOCK@", blk))
            c = subprocess.run(["xcrun", "clang", "-x", "objective-c", "-fobjc-arc", "-Wall", "-Wno-unused-variable", "-framework", "Foundation",
                                "-o", str(t / tag), str(t / (tag + ".m"))], capture_output=True, text=True)
            check("the %s bridge blocks compile on the host" % tag, c.returncode == 0, c.stderr[-1500:])
            built[tag] = c.returncode == 0
        if all(built.values()):
            EC = ["kernel32.dll", "kernelbase.dll", "ntdll.dll", "ntdll-sync.dll", "ntdll-xsec.dll", "winspool.drv", "user32.dll", "kernelbase-optin.dll"]
            AA = ["kernel32.dll", "kernelbase.dll", "ntdll.dll", "services.exe", "wow64.dll"]
            FRESH_EC = ["kernel32-fresh.dll", "kernelbase-fresh.dll", "user32-fresh.dll", "winspool-fresh.drv", "ntdll-fresh.dll", "ntdll-fresh-sync.dll", "ntdll-fresh-xsec.dll"]
            FRESH_AA = ["kernel32-fresh.dll", "kernelbase-fresh.dll", "services-fresh.exe", "ntdll-fresh.dll", "wow64-fresh.dll"]
            no_k32 = lambda files: [x for x in files if not x.startswith("kernel32-")]
            counts = {"off": 0, "on": 0, "k32": 0}
            problems = []

            def build_world(d, fresh_ec, fresh_aa, ec, usd_file):
                """a bundle and a prefix as the session's farm pass leaves them: system32 = the session's folder, sysx64 / sysaa64 = both farms"""
                ec_files = EC + fresh_ec + (["kernel32-usd.dll"] if usd_file else [])
                for folder, files in (("arm64ec-windows", ec_files), ("aarch64-windows", AA + fresh_aa)):
                    (d / "bundle" / folder).mkdir(parents=True)
                    for f in files:
                        (d / "bundle" / folder / f).write_text(folder + "/" + f)
                for sub in ("system32", "sysx64", "sysaa64"):
                    (d / "prefix/drive_c/windows" / sub).mkdir(parents=True)
                for folder, files, subs in (("arm64ec-windows", ec_files, ["sysx64"] + (["system32"] if ec else [])),
                                            ("aarch64-windows", AA + fresh_aa, ["sysaa64"] + ([] if ec else ["system32"]))):
                    for sub in subs:
                        for f in files:
                            os.symlink(d / "bundle" / folder / f, d / "prefix/drive_c/windows" / sub / f)

            def links(d):
                out = {}
                for sub in ("system32", "sysx64", "sysaa64"):
                    for f in sorted(os.listdir(d / "prefix/drive_c/windows" / sub)):
                        out[sub + "/" + f] = os.readlink(d / "prefix/drive_c/windows" / sub / f).replace(str(d), "")
                return out

            def run_bridge(tag, d, ec, env):
                e = {k: v for k, v in os.environ.items() if not k.startswith("MADEIRA_")}
                e.update({k: v for k, v in env.items() if v is not None})
                p = subprocess.run([str(t / tag), str(d / "bundle"), str(d / "prefix"), "1" if ec else "0"], capture_output=True, text=True, env=e)
                return p.returncode, p.stderr

            def expected(base, ec, fe, fa, env, usd_file):
                """what the new blocks must leave: the farm's links, then the rebuilt set (key exactly 1), then the opt-in loop, then the kernel32-usd block, then the older ntdll variants"""
                ex = dict(base)
                fresh_on = env.get("MADEIRA_WIN64_FRESH") == "1"
                usd0 = env.get("MADEIRA_USD_DIRECT") == "0"
                x = env.get("MADEIRA_NTDLL_XSEC") == "1"
                s = env.get("MADEIRA_NTDLL_SYNC") in ("1", "native")
                ec_dsts = ["sysx64"] + (["system32"] if ec else [])
                aa_dsts = ["sysaa64"] + ([] if ec else ["system32"])
                fresh_ntdll = False
                if fresh_on:
                    for f in fe:
                        stem, ext = f.rsplit("-fresh", 1)
                        if stem == "ntdll" or (usd0 and stem == "kernel32"):
                            continue
                        for sub in ec_dsts:
                            ex["%s/%s%s" % (sub, stem, ext)] = "/bundle/arm64ec-windows/" + f
                    for f in fa:
                        stem, ext = f.rsplit("-fresh", 1)
                        if stem == "ntdll" or (usd0 and stem == "kernel32"):
                            continue
                        # an ARM64 name the ARM64EC folder lacks is in system32 of an ARM64EC session by the farm's cross-link pass
                        only_aa = ("%s%s" % (stem, ext)) not in EC
                        for sub in aa_dsts + (["system32"] if (ec and only_aa) else []):
                            ex["%s/%s%s" % (sub, stem, ext)] = "/bundle/aarch64-windows/" + f
                    want_ec = "ntdll-fresh-xsec.dll" if x else "ntdll-fresh-sync.dll" if s else "ntdll-fresh.dll"
                    if want_ec in fe:
                        for sub in ec_dsts:
                            ex[sub + "/ntdll.dll"] = "/bundle/arm64ec-windows/" + want_ec
                        fresh_ntdll = True
                    if "ntdll-fresh.dll" in fa:
                        for sub in aa_dsts:
                            ex[sub + "/ntdll.dll"] = "/bundle/aarch64-windows/ntdll-fresh.dll"
                # the opt-in loop: a listed kernelbase takes the link over the rebuilt one
                if "kernelbase" in (env.get("MADEIRA_OPTIN_DLLS") or "").split(","):
                    for sub in ec_dsts:
                        ex[sub + "/kernelbase.dll"] = "/bundle/arm64ec-windows/kernelbase-optin.dll"
                # the kernel32-usd block: it leaves a rebuilt kernel32 that was linked, links kernel32-usd.dll otherwise, nothing with MADEIRA_USD_DIRECT=0
                fresh_k32 = fresh_on and "kernel32-fresh.dll" in fe and not usd0
                if not usd0 and not fresh_k32 and usd_file:
                    for sub in ec_dsts:
                        ex[sub + "/kernel32.dll"] = "/bundle/arm64ec-windows/kernel32-usd.dll"
                # the older ntdll variants: linked as before unless a fresh ntdll took the place (system32 + sysx64 of the ARM64EC folder)
                if not fresh_ntdll:
                    old_target = "ntdll-xsec.dll" if x else "ntdll-sync.dll" if s else None
                    if old_target:
                        for sub in ["sysx64"] + (["system32"] if ec else []):
                            ex[sub + "/ntdll.dll"] = "/bundle/arm64ec-windows/" + old_target
                return ex, fresh_k32

            def one(ec, fe, fa, env, usd_file):
                key = "ec=%s fe=%d fa=%d usd_file=%s %s" % (ec, len(fe), len(fa), usd_file, sorted((k[8:], v) for k, v in env.items() if v is not None))
                d_new = Path(tempfile.mkdtemp(dir=t))
                build_world(d_new, fe, fa, ec, usd_file)
                base = links(d_new)
                rc, err = run_bridge("new", d_new, ec, env)
                after = links(d_new)
                if rc != 0:
                    problems.append(("exit", key, err[-200:]))
                    return
                if env.get("MADEIRA_WIN64_FRESH") != "1":
                    d_old = Path(tempfile.mkdtemp(dir=t))
                    build_world(d_old, fe, fa, ec, usd_file)
                    rc_old, err_old = run_bridge("old", d_old, ec, env)
                    old_links = links(d_old)
                    counts["off"] += 1
                    if after != old_links:
                        problems.append(("off differs from the old blocks", key, {k: (after.get(k), old_links.get(k)) for k in after if after.get(k) != old_links.get(k)}))
                    if err != err_old:
                        problems.append(("off says something else", key, (err[:300], err_old[:300])))
                    shutil.rmtree(d_old, ignore_errors=True)
                else:
                    counts["on"] += 1
                    ex, fresh_k32 = expected(base, ec, fe, fa, env, usd_file)
                    if after != ex:
                        problems.append(("key on", key, {k: (after.get(k), ex.get(k)) for k in set(after) | set(ex) if after.get(k) != ex.get(k)}))
                    usd0 = env.get("MADEIRA_USD_DIRECT") == "0"
                    if fresh_k32:
                        said = "kernel32.dll stays kernel32-fresh.dll" in err and "kernel32.dll -> kernel32-usd.dll" not in err
                    elif usd0:
                        said = "MADEIRA_USD_DIRECT=0: the shipped kernel32.dll" in err and "kernel32-usd.dll" not in err.replace("kernel32-usd.dll is not linked", "")
                    elif usd_file:
                        said = "kernel32.dll -> kernel32-usd.dll" in err and "stays kernel32-fresh.dll" not in err
                    else:
                        said = "this build has no kernel32-usd.dll" in err
                    if not said:
                        problems.append(("key on, the log line about kernel32", key, err[-400:]))
                    rc2, _e2 = run_bridge("new", d_new, ec, env)
                    if links(d_new) != after:
                        problems.append(("not idempotent", key, ""))
                shutil.rmtree(d_new, ignore_errors=True)

            for ec in (True, False):
                for fe, fa in ((FRESH_EC, FRESH_AA), (no_k32([x for x in FRESH_EC if "ntdll" not in x]), no_k32(FRESH_AA)), (["ntdll-fresh.dll"], []), ([], [])):
                    for xsec in (None, "1"):
                        for sync in (None, "1", "native"):
                            for fresh in (None, "0", "", "true", "11", "1"):
                                one(ec, fe, fa, {"MADEIRA_WIN64_FRESH": fresh, "MADEIRA_NTDLL_XSEC": xsec, "MADEIRA_NTDLL_SYNC": sync}, True)
            # kernel32: the rebuilt one, kernel32-usd.dll and the shipped one, with every combination of the files, MADEIRA_USD_DIRECT and a listed opt-in kernelbase
            for ec in (True, False):
                for fe, fa in ((FRESH_EC, FRESH_AA), (FRESH_EC, no_k32(FRESH_AA)), (no_k32(FRESH_EC), FRESH_AA), (no_k32(FRESH_EC), no_k32(FRESH_AA))):
                    for usd_file in (True, False):
                        for usd_key in (None, "0", "1"):
                            for optin in (None, "kernelbase"):
                                for fresh in (None, "1"):
                                    counts["k32"] += 1
                                    one(ec, fe, fa, {"MADEIRA_WIN64_FRESH": fresh, "MADEIRA_USD_DIRECT": usd_key, "MADEIRA_OPTIN_DLLS": optin}, usd_file)
            check("key off (unset, 0, empty, true, 11): the bridge's links AND its log lines are the old blocks' (main's, 642a42e1: opt-in loop, kernel32-usd.dll as the "
                  "default 64-bit kernel32, sync / xsec), in %d worlds" % counts["off"],
                  not [p for p in problems if p[0] in ("off differs from the old blocks", "off says something else", "exit")] and counts["off"] > 300,
                  [p for p in problems if p[0] != "key on"][:3])
            check("key on: fresh files replace the farm links of their names in sysx64 / sysaa64 (and system32 of the session's own folder), ntdll.dll is the file "
                  "the loader maps, an older variant keeps its link when no fresh ntdll was linked, a listed opt-in kernelbase keeps its link, a second run changes nothing, in %d worlds" % counts["on"],
                  not [p for p in problems if p[0] in ("key on", "not idempotent")] and counts["on"] > 100, [p for p in problems if p[0] in ("key on", "not idempotent")][:3])
            check("kernel32 (%d worlds of the kernel32 loop): key off links kernel32-usd.dll (the shipped kernel32.dll with MADEIRA_USD_DIRECT=0 or without the file); key on links the rebuilt "
                  "kernel32-fresh.dll and the kernel32-usd block leaves it (the block that runs last is the kernel32-usd one, and it does nothing to a linked rebuilt kernel32); "
                  "key on without the file or with MADEIRA_USD_DIRECT=0: kernel32-usd.dll or the shipped one, as without the key; each case says which in one log line" % counts["k32"],
                  not [p for p in problems if p[0] in ("key on, the log line about kernel32",)] and counts["k32"] > 100,
                  [p for p in problems if p[0] == "key on, the log line about kernel32"][:3])
else:
    print("SKIP: no xcrun; the bridge is not run")

# ---------------------------------------------------------------- the patch scripts of the list apply, in order, to the pinned source
wine = None
for cand in (root / "wine", Path(os.environ.get("WINE_SRC", "/nonexistent")), Path.home() / "madeira-bcd/wine"):
    if (cand / "dlls/ntdll/loader.c").is_file() and (cand / ".git").exists():
        wine = cand
        break
if wine and patches:
    with tempfile.TemporaryDirectory(prefix="win64-fresh-src-") as t:
        t = Path(t)
        a = subprocess.run("git -C '%s' archive HEAD dlls libs | tar -x -C '%s'" % (wine, t), shell=True, capture_output=True, text=True)
        check("the pinned source can be staged with git archive", a.returncode == 0 and (t / "dlls/ntdll/loader.c").is_file(), a.stderr)
        if a.returncode == 0:
            def apply_all():
                out = []
                for p in patches:
                    args = [x.replace("@W@", str(t)) for x in p[1:]]
                    r = subprocess.run([sys.executable, str(root / "tools" / p[0])] + args, capture_output=True, text=True)
                    out.append((p[0], r.returncode, (r.stdout + r.stderr).strip()[:100]))
                return out
            first = apply_all()
            check("every patch script of the list applies, in the list's order, to the pinned source (%d scripts)" % len(patches),
                  all(rc == 0 for _n, rc, _o in first), [x for x in first if x[1] != 0])
            second = apply_all()
            check("a second pass changes nothing: every script says it is already applied, the xinput one says it is upstream's",
                  all(rc == 0 and ("already" in o.lower() or "nothing to do" in o.lower()) for _n, rc, o in second), [x for x in second if not ("already" in x[2].lower() or "nothing to do" in x[2].lower())])
else:
    print("SKIP: no pinned wine checkout (wine/, WINE_SRC or ~/madeira-bcd/wine); the patch list is not applied")

wfn = read(".github/workflows/build-ipa.yml")
check("the workflow runs this check", "python3 tests/host/check-win64-fresh.py" in wfn)
print("PASS: win64 fresh" if ok else "FAILED: win64 fresh")
sys.exit(0 if ok else 1)

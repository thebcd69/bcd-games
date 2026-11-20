#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""The patch scripts that exist for the rebuilt 64-bit Wine set (round 2026-11-15): each applies to the pinned source, says so once, refuses a moved anchor and compiles.

  tools/patch-wine-ntdll-rtcs-area.py       the `[rtcs] pre:` argument is not read through a NULL CPU area (source form of the committed binary's 4-byte patch)
  tools/patch-wine-loader-import-diag.py    round 660 M024 (d) / M093: [import-stub] and [delay-fail] detail (log only)
  tools/patch-wine-loader-import-appdir.py  round 669 (B S7, Doki Doki Literature Club): [import-miss] (log only) and the keyed application-folder search
  tools/patch-wine-msgbox-timeout.py        round 660 M106: MessageBoxTimeoutA/W honour the timeout (keyless)
  tools/patch-wine-wintrust-diag.py         round 660 M072: [wintrust] (log only)
  tools/patch-wine-wow64-token-null-sid.py  source form of tools/patch-wow64-token-null-sid.py (Garry's Mod)

For each: it applies to the file as pinned (read with git show, so a tree an earlier step patched does not matter), once ("patched"/"applied", then "already"), a file
with the anchor moved is refused by name and left alone, and with llvm-mingw and a Wine build tree (wine/build-arm64ec, ~/madeira-bcd/wine/build-arm64ec) the patched
file compiles with the module's own flags: no error, and no warning the unpatched file does not have. The loader's probe, cut out of the patched loader.c together with the
loader's real find_name_in_exports, runs on the host over a made-up export directory: a sorted table, a name that is not there, an UNSORTED table (the loader's binary search
misses a name a plain walk finds: the case the diagnostic is for), an ordinal, and the once-per-pair cap.
"""
from pathlib import Path
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[2]
ok = True


def check(what, cond, detail=""):
    global ok
    print(("ok   " if cond else "FAIL ") + what + (("\n     " + str(detail)[:1800]) if (detail and not cond) else ""))
    ok = ok and bool(cond)


wine = None
for cand in (root / "wine", Path(os.environ.get("WINE_SRC", "/nonexistent")), Path.home() / "madeira-bcd/wine"):
    if (cand / "dlls/ntdll/loader.c").is_file() and (cand / ".git").exists():
        wine = cand
        break
if not wine:
    print("SKIP: no pinned wine checkout (wine/, WINE_SRC or ~/madeira-bcd/wine)")
    print("PASS: wine PE patches (static part)")
    sys.exit(0)


def pinned(rel):
    r = subprocess.run(["git", "-C", str(wine), "show", "HEAD:" + rel], capture_output=True, text=True)
    return r.stdout if r.returncode == 0 else None


SCRIPTS = {
    "patch-wine-ntdll-rtcs-area.py": ["dlls/ntdll/signal_arm64ec.c"],
    "patch-wine-loader-import-diag.py": ["dlls/ntdll/loader.c", "dlls/ntdll/exception.c"],
    "patch-wine-loader-import-appdir.py": ["dlls/ntdll/loader.c"],
    "patch-wine-msgbox-timeout.py": ["dlls/user32/msgbox.c"],
    "patch-wine-wintrust-diag.py": ["dlls/wintrust/wintrust_main.c", "dlls/wintrust/softpub.c"],
    "patch-wine-wow64-token-null-sid.py": ["dlls/wow64/security.c"],
}
# a text of each script's anchor that the "moved" variant of the file breaks (one edit that keeps the file valid C)
MOVE = {
    "patch-wine-ntdll-rtcs-area.py": ("dlls/ntdll/signal_arm64ec.c", "get_arm64ec_cpu_area()->InSimulation );", "get_arm64ec_cpu_area( )->InSimulation );"),
    "patch-wine-loader-import-diag.py": ("dlls/ntdll/loader.c", "static BOOL import_dll( WINE_MODREF *wm,", "static BOOL import_dll(  WINE_MODREF *wm,"),
    "patch-wine-loader-import-appdir.py": ("dlls/ntdll/loader.c", "    if (!status) status = load_dll( load_path, buffer, 0, &wmImp, system );",
                                           "    if (!status) status = load_dll(  load_path, buffer, 0, &wmImp, system );"),
    "patch-wine-msgbox-timeout.py": ("dlls/user32/msgbox.c", "INT WINAPI MessageBoxTimeoutA( HWND hWnd,", "INT WINAPI MessageBoxTimeoutA(  HWND hWnd,"),
    "patch-wine-wintrust-diag.py": ("dlls/wintrust/softpub.c", "static DWORD WINTRUST_CopyChain(CRYPT_PROVIDER_DATA *data, DWORD signerIdx)", "static DWORD WINTRUST_CopyChain( CRYPT_PROVIDER_DATA *data, DWORD signerIdx)"),
    "patch-wine-wow64-token-null-sid.py": ("dlls/wow64/security.c", "        sid = owner->Owner;\n", "        sid = owner->Owner ;\n"),
}


def tool(*names):
    for tc in sorted((Path.home() / "madeira-bcd/toolchains").glob("llvm-mingw-*/bin")) + sorted((root / "toolchains").glob("llvm-mingw-*/bin")):
        for n in names:
            if (tc / n).is_file():
                return str(tc)
    return None


def build_dir(arch):
    for cand in (root / "wine" / ("build-" + arch), Path.home() / "madeira-bcd/wine" / ("build-" + arch)):
        if (cand / "compile_commands.json").is_file():
            return cand
    return None


def compile_flags(arch, rel, template_rel=None):
    """the compile command of a source of the module in a build tree, as (build dir, argv, file, exact): the tree of the architecture when it has the file,
    else (aarch64 only) the arm64ec tree's command of a template source, which the caller adapts"""
    import shlex
    for a in ([arch] if arch == "arm64ec" else [arch, "arm64ec"]):
        b = build_dir(a)
        if not b:
            continue
        cmds = json.loads((b / "compile_commands.json").read_text())
        entry = next((e for e in cmds if e["file"] == "../" + rel), None)
        if entry:
            return b, shlex.split(entry["command"]), entry["file"], a == arch
        if a != arch and template_rel:
            entry = next((e for e in cmds if e["file"] == "../" + template_rel), None)
            if entry:
                return b, shlex.split(entry["command"]), entry["file"], False
    return None


def syntax_compile(rel, path, template_rel=None, aarch64=False):
    tc = tool("arm64ec-w64-mingw32-clang")
    got = compile_flags("aarch64" if aarch64 else "arm64ec", rel, template_rel)
    if not tc or not got:
        return None, "no llvm-mingw or no wine build tree"
    b, argv, srcfile, exact = got
    out, skip = [], False
    for a in argv:
        if skip:
            skip = False
            continue
        if a == "-o":
            skip = True
            continue
        out.append(str(path) if a == srcfile else a)
    out = [a for a in out if a != "-c"] + ["-c", "-fsyntax-only"]
    if aarch64 and not exact:   # the arm64ec tree's command of another source, adapted
        out = [("aarch64-windows" if x == "arm64ec-windows" else x) for x in out] + ["-I../dlls/%s" % rel.split("/")[1]]
        out[0] = "aarch64-w64-mingw32-clang"
    out[0] = os.path.join(tc, "aarch64-w64-mingw32-clang" if aarch64 else "arm64ec-w64-mingw32-clang")
    r = subprocess.run(out, cwd=str(b), capture_output=True, text=True, env={**os.environ, "PATH": tc + ":" + os.environ.get("PATH", "")})
    return r, ""


def warnings(r):
    return sorted(set(re.findall(r"warning: [^\n]*", r.stderr)))


with tempfile.TemporaryDirectory(prefix="wine-pe-patches-") as tmp:
    tmp = Path(tmp)
    patched = {}
    for script, files in SCRIPTS.items():
        sp = root / "tools" / script
        check("%s exists" % script, sp.is_file())
        if not sp.is_file():
            continue
        t = tmp / script
        for rel in files:
            (t / rel).parent.mkdir(parents=True, exist_ok=True)
            text = pinned(rel)
            if text is None:
                check("%s: %s is in the pinned source" % (script, rel), False)
                break
            (t / rel).write_text(text)
        else:
            r1 = subprocess.run([sys.executable, str(sp), str(t)], capture_output=True, text=True)
            r2 = subprocess.run([sys.executable, str(sp), str(t)], capture_output=True, text=True)
            check("%s applies once and says it is already applied the second time" % script,
                  r1.returncode == 0 and re.search(r"patched|applied", r1.stdout) and not re.search(r"already", r1.stdout)
                  and r2.returncode == 0 and "already" in r2.stdout, r1.stdout + r1.stderr + r2.stdout + r2.stderr)
            changed = [rel for rel in files if (t / rel).read_text() != pinned(rel)]
            check("%s changes exactly its files" % script, changed == files, changed)
            patched[script] = t
            # a moved anchor: refused by name, the files left alone
            mrel, old, new = MOVE[script]
            t2 = tmp / (script + ".moved")
            for rel in files:
                (t2 / rel).parent.mkdir(parents=True, exist_ok=True)
                (t2 / rel).write_text(pinned(rel))
            src = (t2 / mrel).read_text()
            check("%s: the anchor used for the moved-anchor check is in the pinned file" % script, src.count(old) >= 1, old)
            (t2 / mrel).write_text(src.replace(old, new, 1))
            before = {rel: (t2 / rel).read_text() for rel in files}
            r3 = subprocess.run([sys.executable, str(sp), str(t2)], capture_output=True, text=True)
            check("%s: a file with a moved anchor is refused by name and left alone" % script,
                  r3.returncode != 0 and "patch-" in (r3.stdout + r3.stderr) and all((t2 / rel).read_text() == before[rel] for rel in files), r3.stdout + r3.stderr)

    # round 669: the application-folder patch applies after the import diagnostics too (the workflow's order), and keeps its search behind the key
    if "patch-wine-loader-import-appdir.py" in patched and pinned("dlls/ntdll/exception.c"):
        t = tmp / "order-loader"
        for rel in ("dlls/ntdll/loader.c", "dlls/ntdll/exception.c"):
            (t / rel).parent.mkdir(parents=True, exist_ok=True)
            (t / rel).write_text(pinned(rel))
        a = subprocess.run([sys.executable, str(root / "tools/patch-wine-loader-import-diag.py"), str(t)], capture_output=True, text=True)
        b2 = subprocess.run([sys.executable, str(root / "tools/patch-wine-loader-import-appdir.py"), str(t)], capture_output=True, text=True)
        lt = (t / "dlls/ntdll/loader.c").read_text()
        helper = lt[lt.index("static NTSTATUS ios_import_miss("):lt.index("static BOOL import_dll(")]
        check("import-appdir applies after the import diagnostics (the workflow's order); the second search runs only with MADEIRA_IMPORT_APPDIR=1, "
              "only after load_dll said STATUS_DLL_NOT_FOUND, and the log line is capped at 8",
              a.returncode == 0 and b2.returncode == 0 and "if (!on || !dir_len) return status;" in helper
              and helper.index("if (!on || !dir_len) return status;") < helper.index("status = load_dll( alt, libname, 0, pwm, system );")
              and 'RtlInitUnicodeString( &nm, L"MADEIRA_IMPORT_APPDIR" );' in helper and "if (InterlockedIncrement( &said ) <= 8)" in helper
              and "    if (status == STATUS_DLL_NOT_FOUND)   /* madeira-bcd: [import-miss]" in lt, a.stderr + b2.stderr + b2.stdout)

    # the msgbox patch also applies on the file after tools/patch-wine-cpu-error-diag.py (the workflow's order)
    cpu = root / "tools/patch-wine-cpu-error-diag.py"
    if cpu.is_file() and pinned("dlls/user32/msgbox.c") and pinned("dlls/wow64/syscall.c"):
        t = tmp / "order"
        for rel in ("dlls/user32/msgbox.c", "dlls/wow64/syscall.c"):
            (t / rel).parent.mkdir(parents=True, exist_ok=True)
            (t / rel).write_text(pinned(rel))
        a = subprocess.run([sys.executable, str(cpu), str(t)], capture_output=True, text=True)
        b2 = subprocess.run([sys.executable, str(root / "tools/patch-wine-msgbox-timeout.py"), str(t)], capture_output=True, text=True)
        check("the msgbox timeout patch applies after the CPU-error diagnostics too (the workflow's order)", a.returncode == 0 and b2.returncode == 0, a.stderr + b2.stderr + b2.stdout)

    # the patched files compile with the module's own flags
    if os.environ.get("WINE_PE_NO_COMPILE"):
        print("SKIP: WINE_PE_NO_COMPILE is set: the patched files are not compiled for the PE targets")
    elif tool("arm64ec-w64-mingw32-clang") and build_dir("arm64ec"):
        for script, files in SCRIPTS.items():
            if script not in patched:
                continue
            for rel in files:
                aa = script == "patch-wine-wow64-token-null-sid.py"
                pristine = tmp / ("pin-" + rel.replace("/", "_"))
                pristine.write_text(pinned(rel))
                before, why = syntax_compile(rel, pristine, template_rel="dlls/user32/msgbox.c" if aa else None, aarch64=aa)
                after, _w = syntax_compile(rel, patched[script] / rel, template_rel="dlls/user32/msgbox.c" if aa else None, aarch64=aa)
                if before is None or after is None:
                    print("SKIP: %s: %s" % (rel, why))
                    continue
                check("%s compiles with the module's flags (no error), the same warnings as the pinned file" % rel,
                      after.returncode == 0 and warnings(after) == warnings(before), (after.stderr or "")[-1200:])
    else:
        print("SKIP: no llvm-mingw or no Wine build tree: the patched files are not compiled")

    # the loader's probe, on the host
    lp = patched.get("patch-wine-loader-import-diag.py")
    cc = shutil.which("cc") or shutil.which("clang")
    if lp and cc:
        text = (lp / "dlls/ntdll/loader.c").read_text()
        orig = pinned("dlls/ntdll/loader.c")
        fn = re.search(r"static int find_name_in_exports\(.*?\n}\n", orig, re.S).group(0)
        probe = text[text.index("static void ios_import_probe("):text.index("/* the module that called a stub")]
        harness = r'''
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdarg.h>
#include <stdint.h>
typedef uint32_t DWORD; typedef uint16_t WORD; typedef uint32_t ULONG; typedef void *HMODULE; typedef unsigned int UINT;
typedef struct { DWORD Characteristics, TimeDateStamp; WORD MajorVersion, MinorVersion; DWORD Name, Base, NumberOfFunctions, NumberOfNames,
                 AddressOfFunctions, AddressOfNames, AddressOfNameOrdinals; } IMAGE_EXPORT_DIRECTORY;
typedef struct { struct { WORD Machine; } FileHeader; } IMAGE_NT_HEADERS;
typedef struct { uint16_t *Buffer; } USTR;
typedef struct { struct { void *DllBase; DWORD SizeOfImage; USTR FullDllName, BaseDllName; } ldr; } WINE_MODREF;
#define IMAGE_DIRECTORY_ENTRY_EXPORT 0
#define TRUE 1
#define ARRAY_SIZE(a) (sizeof(a) / sizeof((a)[0]))
#define __TRY {
#define __EXCEPT_PAGE_FAULT } if (0) {
#define __ENDTRY }
static const char *debugstr_a(const char *s) { static char b[8][200]; static int i; i = (i + 1) % 8; if (!s) return "(null)"; snprintf(b[i], 200, "\"%s\"", s); return b[i]; }
static const char *debugstr_w(const uint16_t *s) { static char b[8][200]; static int i; char *o; i = (i + 1) % 8; o = b[i]; *o++ = 'L'; *o++ = '"'; while (s && *s) *o++ = (char)*s++; *o++ = '"'; *o = 0; return b[i]; }
static const char *wine_dbg_sprintf(const char *fmt, ...) { static char b[8][300]; static int i; va_list ap; i = (i + 1) % 8; va_start(ap, fmt); vsnprintf(b[i], 300, fmt, ap); va_end(ap); return b[i]; }
static void err_out(const char *fmt, ...)
{
    char f[1024]; char *o = f; const char *p = fmt; va_list ap;
    while (*p && o < f + sizeof(f) - 8) { if (!strncmp(p, "%p", 2)) { memcpy(o, "0x%x", 4); o += 4; p += 2; } else *o++ = *p++; }
    *o = 0; va_start(ap, fmt); vprintf(f, ap); va_end(ap);
}
#define ERR(...) err_out(__VA_ARGS__)
static unsigned char image[0x4000];
static IMAGE_NT_HEADERS nt = { { 0xaa64 } };
static int have_exports = 1;
static void *get_rva(HMODULE module, DWORD va) { return (char *)module + va; }
static IMAGE_NT_HEADERS *RtlImageNtHeader(HMODULE m) { return &nt; }
static void *RtlImageDirectoryEntryToData(HMODULE m, int mapped, int dir, DWORD *size)
{
    *size = have_exports ? 0x200 : 0;
    return have_exports ? image + 0x100 : NULL;
}
@FIND@
@PROBE@
static void setup(const char **names, int n, int sorted, DWORD base)
{
    IMAGE_EXPORT_DIRECTORY *e = (IMAGE_EXPORT_DIRECTORY *)(image + 0x100);
    DWORD *nm = (DWORD *)(image + 0x400), *fn = (DWORD *)(image + 0x800); WORD *ord = (WORD *)(image + 0xc00);
    int i;
    memset(image, 0, sizeof(image));
    e->Base = base; e->NumberOfFunctions = n; e->NumberOfNames = n;
    e->AddressOfNames = 0x400; e->AddressOfFunctions = 0x800; e->AddressOfNameOrdinals = 0xc00;
    for (i = 0; i < n; i++)
    {
        strcpy((char *)image + 0x1000 + 0x40 * i, names[i]);
        nm[i] = 0x1000 + 0x40 * i; ord[i] = i; fn[i] = 0x2000 + 0x10 * i;
    }
    (void)sorted;
}
int main(int argc, char **argv)
{
    int mode = atoi(argv[1]);
    uint16_t imp_name[] = { 'g','a','m','e','.','e','x','e', 0 }, exp_name[] = { 's','t','e','a','m','_','a','p','i','6','4','.','d','l','l', 0 };
    WINE_MODREF imp, exp;
    memset(&imp, 0, sizeof(imp)); memset(&exp, 0, sizeof(exp));
    imp.ldr.DllBase = (void *)0x1000; imp.ldr.BaseDllName.Buffer = imp_name; imp.ldr.FullDllName.Buffer = imp_name;
    exp.ldr.DllBase = image; exp.ldr.SizeOfImage = 0x4000; exp.ldr.FullDllName.Buffer = exp_name; exp.ldr.BaseDllName.Buffer = exp_name;
    static const char *sorted_names[] = { "SteamAPI_GetHSteamPipe", "SteamAPI_Init", "SteamAPI_RunCallbacks", "SteamAPI_Shutdown" };
    static const char *unsorted_names[] = { "SteamAPI_RunCallbacks", "SteamAPI_Shutdown", "SteamAPI_GetHSteamPipe", "SteamAPI_Init" };   /* the loader probes 1, then 0: Init is at 3 */
    switch (mode)
    {
    case 0: setup(sorted_names, 4, 1, 1); ios_import_probe("[import-stub]", "x", &imp, &exp, "SteamAPI_Init", 0); break;
    case 1: setup(sorted_names, 4, 1, 1); ios_import_probe("[import-stub]", "x", &imp, &exp, "SteamAPI_IsSteamRunning", 0); break;
    case 2: setup(unsorted_names, 4, 0, 1); ios_import_probe("[import-stub]", "x", &imp, &exp, "SteamAPI_Init", 0); break;
    case 3: setup(sorted_names, 4, 1, 1); ios_import_probe("[import-stub]", "x", &imp, &exp, NULL, 3); break;
    case 4: setup(sorted_names, 4, 1, 1); ios_import_probe("[import-stub]", "x", &imp, &exp, NULL, 9); break;
    case 5: have_exports = 0; setup(sorted_names, 4, 1, 1); ios_import_probe("[delay-fail]", "x", &imp, &exp, "SteamAPI_Init", 0); break;
    case 6:   /* once per (importer, exporter): the second import of one pair prints nothing; nine pairs print eight */
        { WINE_MODREF e2[9]; int i; setup(sorted_names, 4, 1, 1);
          ios_import_stub_diag(&imp, &exp, "steam_api64.dll", "SteamAPI_Init", 0);
          ios_import_stub_diag(&imp, &exp, "steam_api64.dll", "SteamAPI_Other", 0);
          for (i = 0; i < 9; i++) { e2[i] = exp; ios_import_stub_diag(&imp, &e2[i], "d.dll", "N", 0); } }
        break;
    }
    return 0;
}
'''.replace("@FIND@", fn).replace("@PROBE@", probe)   # probe holds ios_import_probe and ios_import_stub_diag
        (tmp / "h.c").write_text(harness)
        c = subprocess.run([cc, "-std=gnu11", "-Wall", "-Wno-unused-function", "-Wno-format", "-Wno-unused-variable", "-o", str(tmp / "h"), str(tmp / "h.c")],
                           capture_output=True, text=True)
        check("the loader's probe and the pair-once function, with the loader's real find_name_in_exports, compile on the host", c.returncode == 0, c.stderr[-1500:])
        if c.returncode == 0:
            def run(mode):
                return subprocess.run([str(tmp / "h"), str(mode)], capture_output=True, text=True).stdout.splitlines()
            o = run(0)
            check("a name in a sorted table: bsearch and walk agree, the neighbours are named",
                  len(o) == 1 and "bsearch=1 walk=1 rva=0x2010" in o[0] and 'nearest: "SteamAPI_GetHSteamPipe" .. "SteamAPI_RunCallbacks"' in o[0]
                  and "names=4 funcs=4 base-ordinal=1" in o[0] and "machine=0xaa64" in o[0], o)
            o = run(1)
            check("a name that is not in the table: bsearch=-1 walk=-1, rva 0, the names on either side", len(o) == 1 and "bsearch=-1 walk=-1 rva=0 " in o[0]
                  and 'nearest: "SteamAPI_Init" .. "SteamAPI_RunCallbacks"' in o[0], o)
            o = run(2)
            check("an UNSORTED table: the loader's binary search misses a name that a plain walk finds (bsearch=-1, walk=3)", len(o) == 1 and "walk=3 " in o[0]
                  and "bsearch=-1" in o[0] and "rva=0x2030" in o[0], o)
            o = run(3)
            check("an ordinal in range: its RVA, no name", len(o) == 1 and "rva=0x2020" in o[0] and "bsearch=-1 walk=-1" in o[0], o)
            o = run(4)
            check("an ordinal out of range: rva 0", len(o) == 1 and "rva=0 " in o[0], o)
            o = run(5)
            check("a module without an export directory: export-dir=0 names=0 funcs=0", len(o) == 1 and "export-dir=0 names=0 funcs=0" in o[0], o)
            o = run(6)
            check("one line per (importer, exporter) pair, eight pairs at most", len(o) == 8 and all(l.startswith("[import-stub]") for l in o), o)
    else:
        print("SKIP: no patched loader or no C compiler: the probe is not run")

    # the other patched files: what the diff adds
    if "patch-wine-msgbox-timeout.py" in patched:
        m = (patched["patch-wine-msgbox-timeout.py"] / "dlls/user32/msgbox.c").read_text()
        check("msgbox: the timer id, the WM_TIMER branch ending the dialog with 32000, no timer for 0 or INFINITE, MessageBoxIndirect* pass 0, the FIXME is gone",
              "#define MADEIRA_MSGBOX_TIMER    0x4d42" in m and "EndDialog(hwnd, MADEIRA_MB_TIMEDOUT)" in m and "NtUserKillTimer(hwnd, MADEIRA_MSGBOX_TIMER)" in m and "#define MADEIRA_MB_TIMEDOUT     32000" in m
              and "if (timeout && timeout != INFINITE && timeout <= 0x7fffffff)" in m and "return msgbox_indirect_w( msgbox, 0 );" in m
              and "return msgbox_indirect_a( msgbox, 0 );" in m and "timeout not supported" not in m)
    if "patch-wine-wow64-token-null-sid.py" in patched:
        m = (patched["patch-wine-wow64-token-null-sid.py"] / "dlls/wow64/security.c").read_text()
        i = m.index("if (!sid)   /* madeira-bcd: NULL SID")
        check("wow64: a NULL SID stores Owner = 0 and answers with *retlen = 4, STATUS_BUFFER_TOO_SMALL below 4 bytes, before the SubAuthorityCount read",
              "if (len >= sizeof(*owner32)) owner32->Owner = 0;" in m[i:i + 600] and "*retlen = sizeof(*owner32);" in m[i:i + 600]
              and i < m.index("sid_len = offsetof( SID, SubAuthority[sid->SubAuthorityCount] );", i))
    if "patch-wine-wintrust-diag.py" in patched:
        a = (patched["patch-wine-wintrust-diag.py"] / "dlls/wintrust/wintrust_main.c").read_text()
        b = (patched["patch-wine-wintrust-diag.py"] / "dlls/wintrust/softpub.c").read_text()
        check("wintrust: three failure-path logs, each capped (8, 3 chains, 8), no result changed",
              "InterlockedIncrement(&told) <= 8" in a and "InterlockedIncrement(&told) > 3" in b and b.count("InterlockedIncrement(&told) <= 8") == 1
              and "if (err) SetLastError(err);" in a and a.count("return err;") == pinned("dlls/wintrust/wintrust_main.c").count("return err;"))
    if "patch-wine-ntdll-rtcs-area.py" in patched:
        m = (patched["patch-wine-ntdll-rtcs-area.py"] / "dlls/ntdll/signal_arm64ec.c").read_text()
        check("rtcs: the CPU area is read only when there is one", "get_arm64ec_cpu_area() ? get_arm64ec_cpu_area()->InSimulation : 0 );" in m
              and m.count("get_arm64ec_cpu_area()->InSimulation") == pinned("dlls/ntdll/signal_arm64ec.c").count("get_arm64ec_cpu_area()->InSimulation") )

wf = (root / ".github/workflows/build-ipa.yml").read_text()
check("the workflow applies the msgbox and wintrust scripts tree-wide and the loader one for the i386 farm only (put back right after it); it runs this check",
      "python3 tools/patch-wine-msgbox-timeout.py wine" in wf and "python3 tools/patch-wine-wintrust-diag.py wine" in wf
      and "python3 tools/patch-wine-loader-import-diag.py wine" in wf and "git -C wine checkout -- dlls/ntdll/loader.c" in wf
      and wf.index("patch-wine-loader-import-diag.py wine") < wf.index("Put back the files patched for the i386 farm only")
      and "python3 tools/patch-wine-loader-import-appdir.py wine" in wf
      and wf.index("patch-wine-loader-import-diag.py wine") < wf.index("patch-wine-loader-import-appdir.py wine") < wf.index("Put back the files patched for the i386 farm only")
      and "python3 tests/host/check-wine-pe-patches.py" in wf)
check("no workflow step applies the arm64ec-only or aarch64-only scripts to the shared tree (the extra-DLL builds compare an unpatched ntdll with the committed one)",
      "python3 tools/patch-wine-ntdll-rtcs-area.py" not in wf and "python3 tools/patch-wine-wow64-token-null-sid.py" not in wf)
print("PASS: wine PE patches" if ok else "FAILED: wine PE patches")
sys.exit(0 if ok else 1)

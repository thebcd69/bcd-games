#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""tools/patch-wine-zero-mitigation-diag.py: kernelbase-optin.dll says what GetProcessMitigationPolicy did and exports SetProcessValidCallTargets
(night round 2026-11-11, G5 S7; Glyphica 8YHNJ0: ChakraCore failed fast with the key set AND the fork's kernelbase in force).

Checks:
  - tools/build-wine-extra-dlls.sh applies the patch inside the kernelbase-optin block only (after the "kernelbase left as the pin has it" test,
    before the make), builds without it when it does not apply, puts the two files back afterwards, and the block keeps the structure
    tests/host/check-kernelbase-optin.py holds it to;
  - tools/verify-kernelbase-optin.py accepts exactly the shipped kernelbase's exports plus SetProcessValidCallTargets and nothing else;
  - with a Wine tree (wine/ of this tree, WINE_SRC or ~/madeira-bcd/wine; skipped without one): the patch applies once to copies of process.c and
    kernelbase.spec, a second run changes nothing, a moved place refuses it and leaves both files alone (no half patch); the real functions, extracted
    from the patched file and run on the host with stubs: key off: the buffer is untouched and the line says enabled=0 zeroed=0; key 1: a buffer of
    1 to 16 bytes is zeroed and the line says enabled=1 zeroed=1 first_dword=00000000; a NULL buffer is left alone; 8 lines a process at most;
    SetProcessValidCallTargets answers TRUE; the patched process.c compiles for arm64ec with the build's own flags with no warning (the Mac).
"""
from pathlib import Path
import importlib.util
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
    print(("ok   " if cond else "FAIL ") + what + (("\n     " + str(detail)[:1500]) if (detail and not cond) else ""))
    ok = ok and bool(cond)


spec = importlib.util.spec_from_file_location("zmdiag", root / "tools/patch-wine-zero-mitigation-diag.py")
P = importlib.util.module_from_spec(spec)
spec.loader.exec_module(P)

sh = (root / "tools/build-wine-extra-dlls.sh").read_text()
blk = sh[sh.index("# madeira-bcd: kernelbase-optin.dll (see ZMP above)"):]
blk = blk[:blk.index("\nfi\n") + 4]
check("the build block applies the patch after the pin test and before the make, builds without it when it does not apply, and puts the two files back",
      blk.index('if git -C "$R/wine" diff --quiet -- dlls/kernelbase; then') < blk.index('python3 "$R/tools/patch-wine-zero-mitigation-diag.py" "$R/wine"')
      < blk.index('make -C "$B"') < blk.index("git -C \"$R/wine\" checkout -- dlls/kernelbase/process.c dlls/kernelbase/kernelbase.spec")
      and "|| echo \"::warning::kernelbase zero-mitigation diagnostic patch did not apply" in blk and "ZMPDIAG=0" in blk and 'if [ "$ZMPDIAG" = 1 ]; then git -C' in blk)
check("nothing else patches process.c or kernelbase.spec outside that block (the i386 farm and every other build see the pinned files)",
      sh.count("patch-wine-zero-mitigation-diag.py") == 2 and "python3 tools/patch-wine-zero-mitigation-diag.py" not in (root / ".github/workflows/build-ipa.yml").read_text())

vspec = importlib.util.spec_from_file_location("verifykb", root / "tools/verify-kernelbase-optin.py")
V = importlib.util.module_from_spec(vspec)
vspec.loader.exec_module(V)
shipped_names = {"CreateFileW", "GetProcessMitigationPolicy", "SetProcessWorkingSetSizeEx"}
marker = V.MARKER.encode("utf-16-le")


def verdict(built_names, built_machine=0x8664, data=marker, shipped=shipped_names):
    answers = {"built": (data, built_machine, set(built_names)), "shipped": (b"", 0x8664, set(shipped))}
    V._kb.pe_exports = lambda path: answers[path]
    return V.problems("built", "shipped")[0]


check("the verifier accepts the shipped exports", verdict(shipped_names) == [])
check("...accepts them plus SetProcessValidCallTargets", verdict(shipped_names | {"SetProcessValidCallTargets"}) == [])
check("...refuses any other extra export", len(verdict(shipped_names | {"SetProcessValidCallTargets", "Foo"})) == 1)
check("...refuses a missing export even with the extra one", len(verdict((shipped_names - {"CreateFileW"}) | {"SetProcessValidCallTargets"})) == 1)
check("...still refuses a DLL without the key's name and one for another machine",
      len(verdict(shipped_names, data=b"")) == 1 and len(verdict(shipped_names, built_machine=0xaa64)) == 1)

cands = [root / "wine", Path(os.environ["WINE_SRC"]) if os.environ.get("WINE_SRC") else None, Path.home() / "madeira-bcd/wine"]
wine = next((c for c in cands if c and (c / P.PROCESS).is_file() and (c / P.SPEC).is_file()), None)
if not wine:
    print("skip: no Wine tree here (wine/, WINE_SRC or ~/madeira-bcd/wine)")
    print("PASS: kernelbase-optin diagnostic" if ok else "FAILED: kernelbase-optin diagnostic")
    sys.exit(0 if ok else 1)

process_src = (wine / P.PROCESS).read_text()
spec_src = (wine / P.SPEC).read_text()
if P.MARKER in process_src:
    print("note: the Wine tree is already patched; only the patched files are checked")
    patched_p, patched_s = process_src, spec_src
else:
    patched_p = None
with tempfile.TemporaryDirectory(prefix="madeira-zmdiag-") as tmp:
    tmp = Path(tmp)
    script = str(root / "tools/patch-wine-zero-mitigation-diag.py")
    if patched_p is None:
        tree = tmp / "w"
        (tree / "dlls/kernelbase").mkdir(parents=True)
        (tree / P.PROCESS).write_text(process_src)
        (tree / P.SPEC).write_text(spec_src)
        r1 = subprocess.run([sys.executable, script, str(tree)], capture_output=True, text=True)
        patched_p, patched_s = (tree / P.PROCESS).read_text(), (tree / P.SPEC).read_text()
        r2 = subprocess.run([sys.executable, script, str(tree)], capture_output=True, text=True)
        check("it applies once to process.c and kernelbase.spec; a second run says so and changes nothing",
              r1.returncode == 0 and r2.returncode == 0 and "already patched" in r2.stdout and (tree / P.PROCESS).read_text() == patched_p
              and (tree / P.SPEC).read_text() == patched_s and P.MARKER in patched_p, r1.stdout + r1.stderr + r2.stdout + r2.stderr)
        check("kernelbase.spec: the commented stub becomes an export and nothing else changes",
              patched_s == spec_src.replace(P.SPEC_OLD, P.SPEC_NEW, 1))
        for what, a, b in (("the policy function moved", P.PROCESS,
                            process_src.replace("FIXME( \"(%p, %u, %p, %Iu): stub\\n\", process, policy, buffer, length );",
                                                "FIXME( \"(%p, %u, %p, %Iu): stub\\n\", process, policy, buffer, length ); ", 1)),
                           ("the spec stub is gone", P.SPEC, spec_src.replace(P.SPEC_OLD, "", 1))):
            (tree / P.PROCESS).write_text(process_src)
            (tree / P.SPEC).write_text(spec_src)
            (tree / a).write_text(b)
            r3 = subprocess.run([sys.executable, script, str(tree)], capture_output=True, text=True)
            check("%s: the patch refuses by name and leaves both files alone (no half patch)" % what,
                  r3.returncode != 0 and "patch-wine-zero-mitigation-diag:" in r3.stderr and P.MARKER not in (tree / P.PROCESS).read_text()
                  and (tree / P.SPEC).read_text() == (b if a == P.SPEC else spec_src), r3.stderr)

    cc = shutil.which("cc") or shutil.which("clang")
    if not cc:
        print("skip: no C compiler here")
    else:
        def grab(src, start):
            i = src.index(start)
            return src[i:src.index("\n}\n", i) + 3]

        enabled_fn = grab(patched_p, "static BOOL zero_mitigation_policy_enabled(void)")
        policy_fn = patched_p[patched_p.index("BOOL WINAPI /* DECLSPEC_HOTPATCH */ GetProcessMitigationPolicy("):]
        policy_fn = policy_fn[:policy_fn.index("\n}\n") + 3]
        valid_fn = grab(patched_p, "BOOL WINAPI SetProcessValidCallTargets(")
        harness = r'''
#include <stdio.h>
#include <string.h>
#include <stdlib.h>
typedef int BOOL; typedef unsigned int DWORD; typedef int LONG; typedef unsigned int ULONG; typedef unsigned short WCHAR;
typedef void *HANDLE; typedef unsigned long SIZE_T; typedef int PROCESS_MITIGATION_POLICY;
#define WINAPI
#define TRUE 1
#define FALSE 0
#define ARRAY_SIZE(a) (sizeof(a) / sizeof((a)[0]))
#define InterlockedIncrement(p) (++*(p))
static void fixme(const char *fmt, ...);
#define FIXME(...) fixme(__VA_ARGS__)
#include <stdarg.h>
static void fixme(const char *fmt, ...)
{
    char f[512]; char *o = f; const char *p = fmt; va_list ap;
    while (*p && o < f + sizeof(f) - 8)
    {
        if (!strncmp(p, "%Iu", 3)) { memcpy(o, "%zu", 3); o += 3; p += 3; }
        else if (!strncmp(p, "%08lx", 5)) { memcpy(o, "%08x", 4); o += 4; p += 5; }
        else if (!strncmp(p, "%lu", 3)) { memcpy(o, "%u", 2); o += 2; p += 3; }
        else *o++ = *p++;
    }
    *o = 0;
    va_start(ap, fmt); vprintf(f, ap); va_end(ap);
}
static DWORD last_error = 5;
static const char *env_value;
static DWORD GetLastError(void) { return last_error; }
static void SetLastError(DWORD e) { last_error = e; }
static DWORD GetEnvironmentVariableW(const WCHAR *name, WCHAR *buf, DWORD size)
{
    size_t i, n;
    if (!env_value) { last_error = 203; return 0; }
    n = strlen(env_value);
    if (n + 1 > size) return (DWORD)(n + 1);
    for (i = 0; i < n; i++) buf[i] = (unsigned char)env_value[i];
    buf[n] = 0;
    return (DWORD)n;
}
@ENABLED@
@POLICY@
@VALID@
int main(int argc, char **argv)
{
    unsigned char buf[40];
    size_t length = (size_t)atoi(argv[2]);
    int mode = atoi(argv[3]), i, calls = mode == 2 ? 12 : 1, r = 0;
    env_value = strcmp(argv[1], "UNSET") ? argv[1] : NULL;
    memset(buf, 0xA5, sizeof(buf));
    for (i = 0; i < calls; i++) r = GetProcessMitigationPolicy((HANDLE)-1, 7, mode == 1 ? NULL : buf, length);
    printf("ret=%d buf0=%02x\n", r, buf[0]);
    if (mode == 3) printf("valid=%d\n", SetProcessValidCallTargets((HANDLE)-1, (void *)0x1000, 0x1000, 1, NULL));
    return 0;
}
'''.replace("@ENABLED@", enabled_fn).replace("@POLICY@", policy_fn).replace("@VALID@", valid_fn)
        (tmp / "h.c").write_text(harness)
        b = subprocess.run([cc, "-std=gnu11", "-Wall", "-Wno-unused-function", "-fshort-wchar", "-o", str(tmp / "h"), str(tmp / "h.c")], capture_output=True, text=True)
        check("the patched functions compile on the host with stubs", b.returncode == 0, b.stderr[-1500:])
        if b.returncode == 0:
            def run(env, length, mode=0):
                p = subprocess.run([str(tmp / "h"), env, str(length), str(mode)], capture_output=True, text=True)
                return p.stdout.splitlines()
            out = run("1", 4)
            check("key 1, 4 bytes: zeroed, the line says so with first_dword=00000000",
                  out[-1] == "ret=1 buf0=00" and "ZERO_MITIGATION_POLICY enabled=1 zeroed=1 policy=7 length=4 first_dword=00000000" in "\n".join(out), out)
            out = run("UNSET", 4)
            check("key unset: nothing zeroed, the line says enabled=0 zeroed=0 and the stack garbage it saw",
                  out[-1] == "ret=1 buf0=a5" and "enabled=0 zeroed=0 policy=7 length=4 first_dword=a5a5a5a5" in "\n".join(out), out)
            out = run("0", 4)
            check("key 0 or any other value: as unset", out[-1] == "ret=1 buf0=a5" and "enabled=0 zeroed=0" in "\n".join(out), out)
            out = run("1", 17)
            check("key 1, a length above 16: left alone, zeroed=0", out[-1] == "ret=1 buf0=a5" and "enabled=1 zeroed=0 policy=7 length=17" in "\n".join(out), out)
            out = run("1", 4, 1)
            check("a NULL buffer: left alone, first_dword=0, no crash", out[-1].startswith("ret=1") and "first_dword=00000000" in "\n".join(out), out)
            out = run("1", 4, 2)
            check("12 calls: the original line each time, the new one 8 times at most",
                  len([l for l in out if "ZERO_MITIGATION_POLICY enabled" in l]) == 8 and len([l for l in out if l.endswith("stub")]) == 12, len(out))
            out = run("1", 4, 3)
            check("SetProcessValidCallTargets answers TRUE", out[-1] == "valid=1", out)

    # the PE compile for the optin DLL's own target (arm64ec) with the build's flags
    mingw = Path.home() / "madeira-bcd/toolchains/llvm-mingw-20260421-ucrt-macos-universal/bin"
    bd = Path.home() / "madeira-bcd/wine/build-arm64ec"
    if not (mingw / "arm64ec-w64-mingw32-clang").is_file() or not (bd / "include").is_dir():
        print("skip: the arm64ec compile needs llvm-mingw and the full tree's wine/build-arm64ec (the Mac)")
    else:
        f = tmp / "process.c"
        f.write_text(patched_p)
        env = dict(os.environ, PATH=str(mingw) + os.pathsep + os.environ["PATH"])
        cmd = ["arm64ec-w64-mingw32-clang", "-D__STDC__", "-c", "-o", str(tmp / "p.o"), str(f), "-I" + str(bd / "dlls/kernelbase"), "-I" + str(wine / "dlls/kernelbase"),
               "-I" + str(bd / "include"), "-I" + str(wine / "include"), "-I" + str(wine / "include/msvcrt"), "-D_MSVCR_VER=0", "-D__WINESRC__", "-DWINBASEAPI=",
               "-DWINADVAPI=", "-DWINUSERAPI=", "-DWINSHLWAPI=", "-D__WINE_PE_BUILD", "-Wall", "-target", "arm64ec-windows", "-fuse-ld=lld", "--no-default-config",
               "-fno-strict-aliasing", "-Wdeclaration-after-statement", "-Wempty-body", "-Wignored-qualifiers", "-Winit-self", "-Wno-microsoft-enum-forward-reference",
               "-Wstrict-prototypes", "-Wtype-limits", "-Wunused-but-set-parameter", "-Wvla", "-Wwrite-strings", "-Wpointer-arith", "-Wabsolute-value",
               "-ffunction-sections", "-fasync-exceptions", "-ffp-exception-behavior=maytrap", "-O2"]
        r = subprocess.run(cmd, capture_output=True, text=True, env=env)
        check("the patched process.c compiles for arm64ec with the build's flags (no error, no warning)", r.returncode == 0 and not r.stderr.strip(), r.stderr[-800:])

print("PASS: kernelbase-optin diagnostic" if ok else "FAILED: kernelbase-optin diagnostic")
sys.exit(0 if ok else 1)

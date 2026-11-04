#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""tools/patch-wine-xaudio2-callbacks.py: xaudio2 engine callbacks published before their count, opt-in (round 630, agent O3 S5).

Skyrim (93EMSH): RegisterForCallbacks raised the count before the array existed; FAudio's audio thread read the count with a NULL array.

Checks:
  - Wine source (wine/ of this tree, WINE_SRC, or ~/madeira-bcd/wine; that part is skipped without it): on a copy of
    dlls/xaudio2_7/xaudio_dll.c the patch adds the key helper and the snapshot once before the engine callbacks, puts a keyed
    branch in front of each of the three callback loops (the old loop stays as it was, after it) and in front of RegisterForCallbacks'
    count/realloc/store (which stays as it was); in the keyed branch the array is published before the count with a barrier between
    them, the old array is not freed, and the passes read the count, a barrier, then the array; a second run changes nothing; a moved
    anchor refuses it and leaves the file alone; with the llvm-mingw toolchain of the full tree, the patched file compiles for i386
    (xaudio2_6, as Skyrim loads it) with no warning;
  - a model (C, built on the host) of the two publish orders with the audio thread's pass run between every pair of the writer's
    stores: the old order lets a pass see a count of 1 with no array (the crash), the keyed order never does, and every pass that sees
    the new count calls the new callback;
  - the key is in the settings catalog, off by default; the auto-tune rule proposes it for a 32-bit NULL fault inside xaudio2 and
    stops once "[xaudio2-cb]" is in the log; CI applies the patch before the i386 farm and runs this check.
"""
from pathlib import Path
import importlib.util, os, re, shutil, subprocess, sys, tempfile

root = Path(__file__).resolve().parents[2]
ok = True


def check(what, cond, detail=""):
    global ok
    print(("ok   " if cond else "FAIL ") + what + (("\n     " + str(detail)[:1500]) if (detail and not cond) else ""))
    ok = ok and bool(cond)


spec = importlib.util.spec_from_file_location("xa2patch", root / "tools/patch-wine-xaudio2-callbacks.py")
P = importlib.util.module_from_spec(spec)
spec.loader.exec_module(P)

wf = (root / ".github/workflows/build-ipa.yml").read_text()
step = wf.index("      - name: Patch wine xaudio2 engine callbacks (opt-in)\n")
check("CI runs this check and applies the patch before the i386 farm is built",
      "python3 tests/host/check-xaudio2-callbacks.py\n          python3 tools/patch-wine-xaudio2-callbacks.py wine\n" in wf[step:step + 900]
      and step < wf.index("      - name: Build i386 farm (WoW64)\n"))

cands = [root / "wine", Path(os.environ["WINE_SRC"]) if os.environ.get("WINE_SRC") else None, Path.home() / "madeira-bcd/wine"]
wine = next((c for c in cands if c and (c / P.FILE).is_file()), None)
if not wine:
    print("skip: no Wine tree here (wine/, WINE_SRC or ~/madeira-bcd/wine); the CI and the Mac run the source part")
else:
    src = (wine / P.FILE).read_text()
    if P.MARKER in src:
        print("note: the Wine tree is already patched; the checks run on it as it is")
    with tempfile.TemporaryDirectory(prefix="madeira-xa2-") as tmp:
        tree = Path(tmp)
        (tree / "dlls/xaudio2_7").mkdir(parents=True)
        t = tree / P.FILE
        t.write_text(src)
        r1 = subprocess.run([sys.executable, str(root / "tools/patch-wine-xaudio2-callbacks.py"), str(tree)], capture_output=True, text=True)
        once = t.read_text()
        r2 = subprocess.run([sys.executable, str(root / "tools/patch-wine-xaudio2-callbacks.py"), str(tree)], capture_output=True, text=True)
        check("it applies once; a second run says so and changes nothing",
              r1.returncode == 0 and r2.returncode == 0 and "already in" in r2.stdout and t.read_text() == once and P.MARKER in once,
              r1.stdout + r1.stderr + r2.stdout + r2.stderr)
        pristine = once if P.MARKER not in src else None
        check("the helpers sit once, right before the engine callbacks",
              once.count("static BOOL xa2_callback_order(void)") == 1 and once.count(P.HELPERS + P.HELPERS_ANCHOR) == 1)
        for old, call in P.LOOPS:
            name = call.split("_")[1].split("(")[0]
            fn = once[once.index("static void FAUDIOCALL XA2ECB_" + name + "("):]
            fn = fn[:fn.index("\n}\n")]
            check(f"XA2ECB_{name}: the keyed branch reads a snapshot and returns; the old loop follows unchanged",
                  fn.count("if (xa2_callback_order())") == 1 and "xa2_callbacks_snapshot(This, &cbs);" in fn
                  and fn.index("return;") < fn.index(old.rstrip("\n")) and fn.endswith(old.rstrip("\n")))
        reg = once[once.index("static HRESULT WINAPI IXAudio2Impl_RegisterForCallbacks("):]
        reg = reg[:reg.index("\n}\n")]
        kb = reg[reg.index("if (xa2_callback_order())"):reg.index(P.WRITER_OLD)]
        check("RegisterForCallbacks: the keyed branch fills a new array, publishes it, then the count, with a barrier between",
              kb.index("cbs[i] = pCallback;") < kb.index("MemoryBarrier();") < kb.index("&This->cbs = cbs;")
              < kb.rindex("MemoryBarrier();") < kb.index("&This->ncbs = i + 1;") < kb.index("return S_OK;")
              and "realloc" not in kb and "free(" not in kb)
        check("the old count/realloc/store stays as it was, after the keyed branch", reg.count(P.WRITER_OLD) == 1)
        snap = once[once.index("static UINT32 xa2_callbacks_snapshot("):]
        snap = snap[:snap.index("\n}\n")]
        check("a pass reads the count, a barrier, then the array; a missing array is no callbacks",
              snap.index("&This->ncbs;") < snap.index("MemoryBarrier();") < snap.index("&This->cbs;") and "return *cbs ? n : 0;" in snap)
        key = once[once.index("static BOOL xa2_callback_order(void)"):]
        key = key[:key.index("\n}\n")]
        check('the key is read once, only the exact value "1" turns it on, the last error is kept, one [xaudio2-cb] line',
              'GetEnvironmentVariableW( L"MADEIRA_XAUDIO2_CALLBACK_ORDER", value, ARRAY_SIZE(value) );' in key
              and "(len == 1 && value[0] == '1')" in key and "SetLastError( err );" in key
              and "InterlockedCompareExchange( &enabled, on, -1 ) == -1 && on" in key and key.count("[xaudio2-cb]") == 1)
        if pristine:
            t.write_text(src.replace("    This->ncbs++;\n    This->cbs = realloc(", "    This->ncbs += 1;\n    This->cbs = realloc(", 1))
            r3 = subprocess.run([sys.executable, str(root / "tools/patch-wine-xaudio2-callbacks.py"), str(tree)], capture_output=True, text=True)
            check("a moved anchor refuses the patch and leaves the file alone",
                  r3.returncode != 0 and "RegisterForCallbacks" in r3.stderr and P.MARKER not in t.read_text(), r3.stderr)

        tc = Path(os.environ.get("MINGW_BIN", Path.home() / "madeira-bcd/toolchains/llvm-mingw-20260421-ucrt-macos-universal/bin"))
        cc = tc / "i686-w64-mingw32-gcc"
        if not cc.exists():
            print("skip: no llvm-mingw toolchain here for the i386 compile")
        else:
            (tree / "patched.c").write_text(once)
            args = [str(cc), "-D__STDC__", "-c", "-o", str(tree / "x.o"), str(tree / "patched.c"),
                    "-I" + str(wine / "dlls/xaudio2_6"), "-I" + str(wine / "dlls/xaudio2_7"), "-I" + str(wine / "include"),
                    "-I" + str(wine / "include/msvcrt"), "-I" + str(wine / "libs/faudio/include"), "-D_UCRT", "-D__WINESRC__",
                    "-DXAUDIO2_VER=6", "-D__WINE_PE_BUILD", "-Wall", "-target", "i686-windows", "--no-default-config",
                    "-fno-strict-aliasing", "-Wdeclaration-after-statement", "-Wempty-body", "-Wstrict-prototypes", "-Wtype-limits",
                    "-Wpointer-arith", "-fno-omit-frame-pointer", "-mlong-double-64", "-O2"]
            built = [d for d in (wine / "build-i386/dlls/xaudio2_6", wine / "build-i386/include") if d.is_dir()]
            args[7:7] = ["-I" + str(d) for d in built]
            r = subprocess.run(args, capture_output=True, text=True)
            check("the patched file compiles for i386 (xaudio2_6) with no warning", r.returncode == 0 and "warning" not in r.stderr,
                  r.stderr[-1200:])

# ---- the publish orders, as the audio thread can see them ---------------------------------------------------------
model = r'''
#include <stdio.h>
#include <stdlib.h>
typedef struct { int ncbs; void **cbs; } Engine;
static void *CALLBACK_A = (void *)0x1111;
static int bad, seen_new, called_new;
static void pass(Engine *e, int keyed)
{
    if (keyed) {
        int n = e->ncbs; void **cbs = e->cbs;           /* the count, then the array */
        if (!cbs) n = 0;
        for (int i = 0; i < n && cbs[i]; ++i) if (cbs[i] == CALLBACK_A) called_new++;
        if (n == 1) seen_new++;
    } else {
        for (int i = 0; i < e->ncbs; ++i) {             /* the old loop: This->cbs[i] with the count it reads */
            if (!e->cbs) { bad++; return; }              /* the NULL read of xaudio2_6.dll+0x6773 */
            if (!e->cbs[i]) return;
            if (e->cbs[i] == CALLBACK_A) called_new++;
        }
        if (e->ncbs == 1) seen_new++;
    }
}
/* the writer's stores, with the pass run before each of them and at the end */
static void register_old(Engine *e)
{
    pass(e, 0); e->ncbs++;
    pass(e, 0); e->cbs = calloc(e->ncbs, sizeof(void *));   /* realloc of NULL; calloc keeps the model's slot readable */
    pass(e, 0); e->cbs[0] = CALLBACK_A;
    pass(e, 0);
}
static void register_keyed(Engine *e)
{
    void **cbs = malloc((e->ncbs + 1) * sizeof(void *));
    pass(e, 1); cbs[0] = CALLBACK_A;
    pass(e, 1); e->cbs = cbs;
    pass(e, 1); e->ncbs = 1;
    pass(e, 1);
}
int main(void)
{
    Engine a = {0, NULL}, b = {0, NULL};
    register_old(&a);
    printf("old bad=%d\n", bad);
    bad = seen_new = called_new = 0;
    register_keyed(&b);
    printf("keyed bad=%d seen=%d called=%d\n", bad, seen_new, called_new);
    return 0;
}
'''
cc = shutil.which("cc") or shutil.which("clang")
if not cc:
    print("skip: no C compiler for the model")
else:
    with tempfile.TemporaryDirectory(prefix="madeira-xa2-model-") as tmp:
        c, exe = Path(tmp) / "m.c", Path(tmp) / "m"
        c.write_text(model)
        b = subprocess.run([cc, "-std=c11", "-Wall", "-Werror", str(c), "-o", str(exe)], capture_output=True, text=True)
        check("the model builds: " + b.stderr[-400:], b.returncode == 0)
        if b.returncode == 0:
            out = subprocess.run([str(exe)], capture_output=True, text=True).stdout
            check("the old order lets a pass see the count with no array (the crash)", "old bad=1\n" in out, out)
            check("the keyed order never does, and every pass that sees the new count calls the new callback",
                  "keyed bad=0 seen=1 called=1\n" in out, out)

# ---- the key in the catalog and in auto-tune ----------------------------------------------------------------------
cat_py = (root / "build/tools/gen-config-catalog.py").read_text()
check("the settings catalog knows the key, off by default, from the patch",
      re.search(r'"env\.MADEIRA_XAUDIO2_CALLBACK_ORDER": \{[^}]*"kind": "bool", "default": "0"[^}]*'
                r'"sources": \["tools/patch-wine-xaudio2-callbacks\.py"\]', cat_py, re.S) is not None)
rules = (root / "build/tools/autotune_rules.py").read_text()
rb = rules[rules.index('"id": "xaudio2-callbacks-null"'):]
rb = rb[:rb.index("\n    },\n")]
check("the auto-tune rule proposes the key for a 32-bit NULL fault inside xaudio2 and stops once [xaudio2-cb] is in the log",
      '"lines": ["env.MADEIRA_XAUDIO2_CALLBACK_ORDER = 1"]' in rb and 's("[xaudio2-cb] MADEIRA_XAUDIO2_CALLBACK_ORDER=1")' in rb
      and '"[wow-null32]", "registers that point into a module:", "(xaudio2_"' in rb
      and (root / "tests/fixtures/autotune/skyrim-xaudio2-callbacks-null-2026-11-03.expect").is_file())

print("PASS: xaudio2 engine callbacks published before their count (opt-in)" if ok else "FAILED: xaudio2 engine callbacks")
sys.exit(0 if ok else 1)

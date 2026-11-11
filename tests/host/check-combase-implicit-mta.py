#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""env.MADEIRA_OPTIN_DLLS = combase: a thread with no COM state uses an MTA created on first need; no Wine runs (round 2026-11-11, G7 S1).

Unreal Engine 5's FAsyncLoadingThread creates the audio device enumerator on a thread that never started COM; no thread of the process had
made an MTA, Wine's combase answered CO_E_NOTINITIALIZED, the game did not check and called through NULL (Meccha Chameleon F4WD9B, Hubert
2CGJ07). tools/patch-wine-combase-implicit-mta.py changes apartment_get_current_or_mta() only; the patched combase ships as combase-optin.dll
next to the shipped combase.dll and a game gets it with env.MADEIRA_OPTIN_DLLS = combase.

Checks:
  1. the patch applies to the pinned Wine's dlls/combase/apartment.c, a second run changes nothing, a drifted file is refused, the only line
     of Wine's file that goes is `return apartment_get_mta();` inside apartment_get_current_or_mta, the marker is defined once, and the
     file says it was modified;
  2. the patched function, compiled on the host with stubs for the apartment table, gives: a thread with an apartment gets it (no MTA made),
     a thread without one gets the existing MTA (no second usage), with neither the MTA is created once and every later call finds it,
     and an allocation failure gives NULL as before (without the patch the "neither" case returns NULL: the check fails there);
  3. --verify accepts a built DLL with the marker, the machine and the exports of the shipped one, and refuses one without the marker,
     of another machine, with other exports, or when the shipped file already carries the marker;
  4. the build script builds it after the other patches are undone, verifies it before it ships and restores the tree; CI verifies the
     shipped combase-optin.dll and runs this check; a downloaded list may name combase; the catalog says so.
NOT covered: building the DLL (CI's extra-DLL step), Wine's own COM code and a device run.
"""
from pathlib import Path
import difflib, os, shutil, subprocess, sys, tempfile

root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from pe_synth import build_pe   # noqa: E402

ok = True


def check(what, cond, detail=""):
    global ok
    print(("ok   " if cond else "FAIL ") + what + (("\n     " + str(detail)[:1500]) if detail and not cond else ""))
    ok = ok and bool(cond)


patch_py = root / "tools/patch-wine-combase-implicit-mta.py"
wine = Path(os.environ.get("WINE_SRC") or (root / "wine"))
marker = "combase-implicit-mta-v1 madeira-bcd"
src = wine / "dlls/combase/apartment.c"
patched = None

# 1. the patch on Wine's file
if src.exists():
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp) / "dlls/combase"
        d.mkdir(parents=True)
        orig = src.read_text()
        (d / "apartment.c").write_text(orig)
        r1 = subprocess.run([sys.executable, str(patch_py), tmp], capture_output=True, text=True)
        patched = (d / "apartment.c").read_text()
        r2 = subprocess.run([sys.executable, str(patch_py), tmp], capture_output=True, text=True)
        check("the patch applies to Wine's apartment.c and a second run changes nothing",
              r1.returncode == 0 and "patched" in r1.stdout and r2.returncode == 0 and "already patched" in r2.stdout
              and (d / "apartment.c").read_text() == patched, r1.stdout + r1.stderr + r2.stdout + r2.stderr)
        gone = [l for l in difflib.ndiff(orig.splitlines(), patched.splitlines()) if l.startswith("- ")]
        check("the only line of Wine's file that goes is the MTA lookup's return", gone == ["-     return apartment_get_mta();"], gone)
        check("the marker is defined once, kept by the linker",
              patched.count('static const char madeira_implicit_mta_tag[] __attribute__((used)) = "%s";' % marker) == 1)
        check("the file says it was modified, under its own license", "Modified by BCD" in patched
              and patched.index("Modified by BCD") < patched.index("#include <stdarg.h>"))
        moved = Path(tmp) / "moved"
        (moved / "dlls/combase").mkdir(parents=True)
        (moved / "dlls/combase/apartment.c").write_text(orig.replace("return apartment_get_mta();", "return apartment_get_mta_x();", 1))
        r3 = subprocess.run([sys.executable, str(patch_py), str(moved)], capture_output=True, text=True)
        check("a drifted Wine file is refused by name", r3.returncode != 0 and "anchor found 0 times" in (r3.stdout + r3.stderr), r3.stdout + r3.stderr)
        check("the helper uses the function behind CoIncrementMTAUsage (declared in combase_private.h)",
              "apartment_increment_mta_usage" in (wine / "dlls/combase/combase_private.h").read_text()
              and "apartment_increment_mta_usage(&cookie)" in patched)
else:
    check("the pinned Wine tree has dlls/combase/apartment.c (WINE_SRC)", False, str(src))

# 2. the patched function on the host, with stubs for the apartment table
cc = shutil.which("cc") or shutil.which("clang") or shutil.which("gcc")
if patched and cc:
    start = patched.index("static const char madeira_implicit_mta_tag")
    end = patched.index("\n}\n", patched.index("struct apartment * apartment_get_current_or_mta(void)")) + 3
    func = patched[start:end]
    harness = r'''
#include <stdio.h>
#include <string.h>
#include <stdint.h>
typedef long LONG; typedef unsigned long DWORD; typedef long HRESULT; typedef void *CO_MTA_USAGE_COOKIE;
#define FAILED(hr) ((hr) < 0)
#define S_OK 0
#define E_OUTOFMEMORY ((HRESULT)0x8007000eL)
struct apartment { int refs; };
static struct apartment g_mta_store, g_thread_apt_store;
static struct apartment *g_mta, *g_thread_apt;
static int increments, addrefs, errs, fail_alloc;
static struct apartment *com_get_current_apt(void) { return g_thread_apt; }
static void apartment_addref(struct apartment *a) { a->refs++; addrefs++; }
static struct apartment *apartment_get_mta(void) { if (g_mta) g_mta->refs++; return g_mta; }
static HRESULT apartment_increment_mta_usage(CO_MTA_USAGE_COOKIE *c)
{
    *c = NULL;
    if (fail_alloc) return E_OUTOFMEMORY;
    increments++;
    if (!g_mta) { g_mta = &g_mta_store; g_mta->refs = 1; }
    else g_mta->refs++;
    return S_OK;
}
static LONG InterlockedExchange(LONG volatile *p, LONG v) { LONG o = *p; *p = v; return o; }
static DWORD GetCurrentThreadId(void) { return 0x164; }
#define ERR(...) do { errs++; if (getenv("SHOW")) printf(__VA_ARGS__); } while (0)
#include <stdlib.h>
''' + func + r'''
#define EXPECT(c, what) do { if (!(c)) { printf("FAIL %s\n", what); bad++; } } while (0)
int main(void)
{
    int bad = 0;
    struct apartment *a;
    /* a thread with an apartment of its own */
    g_thread_apt = &g_thread_apt_store;
    a = apartment_get_current_or_mta();
    EXPECT(a == &g_thread_apt_store && increments == 0 && g_mta == NULL, "a thread with an apartment gets it and no MTA is made");
    g_thread_apt = NULL;
    /* no apartment, an MTA exists */
    g_mta = &g_mta_store; g_mta->refs = 1;
    a = apartment_get_current_or_mta();
    EXPECT(a == g_mta && increments == 0 && errs == 0, "a thread without an apartment gets the existing MTA, no second usage, no log line");
    g_mta = NULL;
    /* allocation failure: nothing is made */
    fail_alloc = 1;
    a = apartment_get_current_or_mta();
    EXPECT(a == NULL && g_mta == NULL, "an allocation failure still gives NULL, as before");
    fail_alloc = 0;
    /* neither: the MTA is created once */
    a = apartment_get_current_or_mta();
    EXPECT(a == &g_mta_store && increments == 1 && errs == 1, "with neither, the MTA is created (one usage) and said once");
    a = apartment_get_current_or_mta();
    EXPECT(a == &g_mta_store && increments == 1 && errs == 1, "every later call finds it: no second usage, no second line");
    printf(bad ? "FAILED\n" : "OK\n");
    return bad != 0;
}
'''
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "t.c").write_text(harness)
        b = subprocess.run([cc, "-w", "-o", str(Path(tmp) / "t"), str(Path(tmp) / "t.c")], capture_output=True, text=True)
        check("the patched function compiles on the host", b.returncode == 0, b.stderr)
        if b.returncode == 0:
            r = subprocess.run([str(Path(tmp) / "t")], capture_output=True, text=True)
            check("a thread with an apartment, with an MTA, with neither and with a failing allocation behave as designed",
                  r.returncode == 0 and r.stdout.strip() == "OK", r.stdout + r.stderr)
        # the original function, the same harness: with neither, it gives NULL (the failure the patch removes)
        orig_func = orig[orig.index("struct apartment * apartment_get_current_or_mta(void)"):]
        orig_func = orig_func[:orig_func.index("\n}\n") + 3]
        h0 = harness.replace(func, orig_func)
        (Path(tmp) / "o.c").write_text(h0)
        b0 = subprocess.run([cc, "-w", "-o", str(Path(tmp) / "o"), str(Path(tmp) / "o.c")], capture_output=True, text=True)
        if b0.returncode == 0:
            r0 = subprocess.run([str(Path(tmp) / "o")], capture_output=True, text=True)
            check("without the patch the same cases fail (a thread with neither gets NULL: CO_E_NOTINITIALIZED)",
                  r0.returncode != 0 and "with neither" in r0.stdout, r0.stdout + r0.stderr)
        else:
            check("the unpatched function compiles in the same harness", False, b0.stderr)
else:
    print("skip the host run: no patched file or no C compiler")

# 3. --verify
with tempfile.TemporaryDirectory() as tmp:
    exports = ("CoCreateInstance", "CoGetClassObject", "CoInitializeEx", "CoIncrementMTAUsage")

    def verify(name, shipped_blob=b"combase", **kw):
        p, sh = Path(tmp) / name, Path(tmp) / ("shipped-" + name)
        sh.write_bytes(build_pe(dll_name="combase.dll", exports=exports, blob=shipped_blob))
        kw.setdefault("exports", exports)
        kw.setdefault("blob", marker.encode() + b"\0")
        p.write_bytes(build_pe(dll_name="combase.dll", **kw))
        return subprocess.run([sys.executable, str(patch_py), "--verify", str(p), str(sh)], capture_output=True, text=True).returncode
    check("--verify accepts an image with the marker, the shipped machine and exports", verify("a.dll") == 0)
    check("--verify refuses an image without the marker", verify("b.dll", blob=b"combase") != 0)
    check("--verify refuses another machine type", verify("c.dll", machine=0xaa64) != 0)
    check("--verify refuses other exports", verify("d.dll", exports=exports[:3]) != 0)
    check("--verify refuses a shipped file that carries the marker", verify("e.dll", shipped_blob=marker.encode() + b"\0") != 0)

# 4. the build, CI, the list policy and the catalog
build = (root / "tools/build-wine-extra-dlls.sh").read_text()
cmb = build[build.index('if [ -n "$CMB" ]; then'):]
cmb = cmb[:cmb.index("\nfi\n") + 4]
dco_at, cmb_at = build.index('if [ -n "$DCO" ]; then'), build.index('if [ -n "$CMB" ]; then')
check("the build builds it only from a tree with the patch's file and next to a shipped combase.dll; MADEIRA_COMBASE_BUILD=0 skips it",
      '"${MADEIRA_COMBASE_BUILD:-1}" != 0' in build and '[ -f "$R/wine/dlls/combase/apartment.c" ] && [ -f "$SHIP/combase.dll" ]' in build
      and "$DCO$CMB\" ]" in build)
check("it patches, makes, strips, pads, verifies against the shipped file, ships as combase-optin.dll, restores the tree and the objects",
      cmb.index("patch-wine-combase-implicit-mta.py\" \"$R/wine\"") < cmb.index("make -C \"$B\"") < cmb.index("--strip-debug \"$cb_tmp\"")
      < cmb.index("+ 0x10000") < cmb.index("--verify \"$cb_tmp\" \"$SHIP/combase.dll\"") < cmb.index('mv "$cb_tmp" "$SHIP/combase-optin.dll"')
      and 'git -C "$R/wine" checkout -- dlls/combase/apartment.c' in cmb and dco_at < cmb_at)
check("it never replaces the shipped combase.dll", 'mv "$cb_tmp" "$SHIP/combase.dll"' not in build and 'cp "$cb" "$SHIP/combase.dll' not in build)
wf = (root / ".github/workflows/build-ipa.yml").read_text()
check("CI verifies the shipped combase-optin.dll against combase.dll and runs this check",
      "python3 tools/patch-wine-combase-implicit-mta.py --verify app/Madeira/arm64ec-windows/combase-optin.dll" in wf
      and "python3 tests/host/check-combase-implicit-mta.py" in wf)
pol = (root / "app/Madeira/GameRecommendationsList.swift").read_text()
case = pol[pol.index('case "MADEIRA_OPTIN_DLLS":'):]
case = case[:case.index("\n            case ")]
check("a downloaded list may name combase", '"coremessaging", "combase"].contains($0)' in case and "coremessaging, combase" in case)
gen = (root / "build/tools/gen-config-catalog.py").read_text()
cat = (root / "app/Madeira/ConfigCatalog.generated.swift").read_text()
check("the catalog says that combase is a second name shipped for every game, with the implicit MTA",
      "combase is the second name BCD ships for every game" in gen and "combase is the second name BCD ships for every game" in cat
      and "tools/patch-wine-combase-implicit-mta.py" in gen)
rules = (root / "build/tools/autotune_rules.py").read_text()
r = rules[rules.index('"id": "com-no-apartment-null-call"'):]
r = r[:r.index("\n    },\n")]
check("auto-tune adds combase after the NOTINITIALIZED call through NULL, not again once linked",
      '"lines": ["env.MADEIRA_OPTIN_DLLS = combase"]' in r and 'err:ole:com_get_class_object apartment not initialised' in r
      and '[x86_live]", "RAX=0x800401f0"' in r and 'MADEIRA_OPTIN_DLLS: combase.dll -> combase-optin.dll' in r and '"noRetry": True' in r)
fx = root / "tests/fixtures/autotune/meccha-chameleon-com-mta-2026-11-08"
check("its fixture proves the rule", fx.with_suffix(".expect").exists() and '"com-no-apartment-null-call"' in fx.with_suffix(".expect").read_text())
print("PASS: combase implicit MTA" if ok else "FAILED: combase implicit MTA")
sys.exit(0 if ok else 1)

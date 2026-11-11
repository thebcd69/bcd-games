#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright 2026 Russell Barton
"""env.MADEIRA_OPTIN_DLLS = dxdiagn,ddraw: Wine's DxDiag provider for a 64-bit program; no Wine runs.

The prefix registers CLSID_DxDiagProvider at system32\\dxdiagn.dll, but only the 32-bit set had the DLL, so a 64-bit program's
CoCreateInstance failed (Far Cry 4's hardware detection, which then read a null result). dxdiagn ships as an opt-in DLL like
ddraw, which it imports, and is built with tools/patch-wine-dxdiagn-directshow.py: without devenum.dll, which the 64-bit set
does not ship, IDxDiagProvider::Initialize failed on its DirectShow section.

Checks:
  1. the patch applies to the pinned Wine's dlls/dxdiagn/provider.c, a second run changes nothing, and in the patched
     DirectShow section a missing device enumerator or a failed enumeration returns S_OK with one ERR line each; the only
     line of Wine's file that goes is the early `return hr;`;
  2. --verify accepts an ARM64EC/AMD64 image with the marker and refuses one without it or of another machine;
  3. the build lists dxdiagn in OPTIN_WANT, patches the tree before make, restores it after, and drops a DLL that fails
     --verify before it is copied; CI verifies the shipped dxdiagn-optin.dll;
  4. a downloaded list may name dxdiagn; the catalog says to list it with ddraw; the farm baseline accepts its ddraw.dll
     import (resolved through the opt-in list); CI runs this check.
NOT covered: building the DLL (the optional extra-DLL build), DxDiag's other sections, and a device run.
"""
from pathlib import Path
import difflib, os, subprocess, sys, tempfile

root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from pe_synth import build_pe   # noqa: E402

ok = True


def check(what, cond, detail=""):
    global ok
    print(("PASS: " if cond else "FAIL: ") + what + (("\n      " + str(detail)[:1500]) if detail and not cond else ""))
    ok = ok and bool(cond)


patch_py = root / "tools/patch-wine-dxdiagn-directshow.py"
wine = Path(os.environ.get("WINE_SRC") or (root / "wine"))
marker = "dxdiagn-directshow-v1 madeira-bcd"

# 1. the patch on Wine's file
src = wine / "dlls/dxdiagn/provider.c"
if src.exists():
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp) / "dlls/dxdiagn"
        d.mkdir(parents=True)
        orig = src.read_text()
        (d / "provider.c").write_text(orig)
        r1 = subprocess.run([sys.executable, str(patch_py), tmp], capture_output=True, text=True)
        patched = (d / "provider.c").read_text()
        r2 = subprocess.run([sys.executable, str(patch_py), tmp], capture_output=True, text=True)
        check("the patch applies to Wine's provider.c and a second run changes nothing",
              r1.returncode == 0 and "patched" in r1.stdout and r2.returncode == 0 and "already patched" in r2.stdout
              and (d / "provider.c").read_text() == patched, r1.stdout + r1.stderr + r2.stdout + r2.stderr)
        if r1.returncode == 0:
            at = patched.index("static HRESULT build_directshowfilters_tree(IDxDiagContainerImpl_Container *node)")
            fn = patched[at:patched.index("\n}\n", at)]
            create = fn[fn.index("CoCreateInstance(&CLSID_SystemDeviceEnum"):]
            create = create[:create.index("\n    }\n")]
            tail = fn[fn.index("cleanup:"):]
            check("no device enumerator: one ERR line with the marker, then S_OK",
                  create.count('ERR("[dxdiagn] madeira-bcd: no DirectShow device enumerator') == 1
                  and "madeira_dxdiagn_marker" in create and create.rstrip().endswith("return S_OK;"))
            check("a failed enumeration keeps what it found: one ERR line, then S_OK; a success still returns hr",
                  tail.count('ERR("[dxdiagn] madeira-bcd: DirectShow filter enumeration failed') == 1
                  and "if (FAILED(hr))" in tail and "return S_OK;" in tail and tail.rstrip().endswith("return hr;"))
            check("the marker is defined once, kept by the linker", patched.count(
                'static const char madeira_dxdiagn_marker[] __attribute__((used)) = "%s";' % marker) == 1)
            gone = [l for l in difflib.ndiff(orig.splitlines(), patched.splitlines()) if l.startswith("- ")]
            check("the only line of Wine's file that goes is the early return", gone == ["-         return hr;"], gone)
            check("the file says it was modified, under its own license", "Modified by BCD" in patched
                  and patched.index("Modified by BCD") < patched.index('#include "dxdiag_private.h"'))
else:
    check("the pinned Wine tree has dlls/dxdiagn/provider.c (WINE_SRC)", False, str(src))

# 2. --verify
with tempfile.TemporaryDirectory() as tmp:
    def verify(name, **kw):
        p = Path(tmp) / name
        p.write_bytes(build_pe(dll_name="dxdiagn.dll", exports=("DllGetClassObject",), **kw))
        return subprocess.run([sys.executable, str(patch_py), "--verify", str(p)], capture_output=True, text=True).returncode
    check("--verify accepts an AMD64-machine image with the marker", verify("a.dll", blob=marker.encode() + b"\0") == 0)
    check("--verify accepts an ARM64-machine image with the marker", verify("b.dll", machine=0xaa64, blob=marker.encode() + b"\0") == 0)
    check("--verify refuses an image without the marker", verify("c.dll", blob=b"dxdiagn") != 0)
    check("--verify refuses a 32-bit image", verify("d.dll", machine=0x14c, pe32=True, blob=marker.encode() + b"\0") != 0)

# 3. the build and CI
build = (root / "tools/build-wine-extra-dlls.sh").read_text()
want = build[build.index('OPTIN_WANT="'):]
want = want[:want.index('"\n', 12)].split('"')[1].split()
patch_at = build.find('python3 "$R/tools/patch-wine-dxdiagn-directshow.py" "$R/wine"')
make_at = build.find('[ -n "$targets" ] && make -C "$B"')
restore_at = build.find('[ -n "$DXD" ] && git -C "$R/wine" checkout -- dlls/dxdiagn/provider.c')
verify_at = build.find('python3 "$R/tools/patch-wine-dxdiagn-directshow.py" --verify "$f"')
copy_at = build.find('for d in $OPT; do\n    f="$B/dlls/$d/arm64ec-windows/$d.dll"')
check("OPTIN_WANT lists dxdiagn and ddraw", "dxdiagn" in want and "ddraw" in want, want)
check("the build patches the tree before make, restores it after, and checks the DLL before it is copied",
      -1 not in (patch_at, make_at, restore_at, verify_at, copy_at) and patch_at < make_at < restore_at < verify_at < copy_at)
blk = build[patch_at:make_at]
check("a patch that does not apply drops dxdiagn from the build", 'OPT="${OPT/ dxdiagn/}"' in blk
      and 'targets="${targets/ dlls\\/dxdiagn\\/arm64ec-windows\\/dxdiagn.dll/}"' in blk)
wf = (root / ".github/workflows/build-ipa.yml").read_text()
check("CI verifies the shipped dxdiagn-optin.dll and runs this check",
      "python3 tools/patch-wine-dxdiagn-directshow.py --verify app/Madeira/arm64ec-windows/dxdiagn-optin.dll" in wf
      and "python3 tests/host/check-dxdiagn-optin.py" in wf)

# 4. the list policy, the catalog and the farm baseline
pol = (root / "app/Madeira/GameRecommendationsList.swift").read_text()
case = pol[pol.index('case "MADEIRA_OPTIN_DLLS":'):]
case = case[:case.index("\n            case ")]
check("a downloaded list may name dxdiagn", '|| $0 == "dxdiagn"' in case and "sensapi, ddraw, dxdiagn, or msi" in case)
gen = (root / "build/tools/gen-config-catalog.py").read_text()
cat = (root / "app/Madeira/ConfigCatalog.generated.swift").read_text()
check("the catalog says to list dxdiagn with ddraw", "dxdiagn is Wine's DxDiag provider" in gen
      and "List it with ddraw, which it imports." in gen and "List it with ddraw, which it imports." in cat)
import json   # noqa: E402
base = json.loads((root / "tools/farm-baseline.json").read_text())
check("the farm baseline accepts dxdiagn-optin.dll's ddraw.dll import",
      base["farms"]["arm64ec-windows"]["staticGaps"].get("dxdiagn-optin.dll") == ["ddraw.dll"])
print("PASS: dxdiagn opt-in" if ok else "FAILED: dxdiagn opt-in")
sys.exit(0 if ok else 1)

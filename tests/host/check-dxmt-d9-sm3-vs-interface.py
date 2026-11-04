#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""tools/patch-dxmt-d9-sm3-vs-interface.py: vs_3_0 outputs reach the pixel shader under the names it reads (round 630, agent O1 S3).

Alan Wake (A6232G): 67 pipelines failed with "Fragment input(s) `user(TANGENT0),user(BINORMAL0),user(NORMAL0)` ... not written by
vertex shader": the SM3 vertex-output walk dropped every usage but Color, Texcoord and Fog.

Checks (DXMT tree: DXMT_SRC, ./dxmt or ~/madeira-bcd/dxmt; the source part is skipped without one): the patch applies to a copy of the
pinned dxso_compile.cpp (git show HEAD: when the tree is a checkout), a second run changes nothing, a moved anchor refuses it; the six
usages are named in the vertex walk exactly as the pixel side names its inputs and stubs; the walk still emits only declared outputs
(no stub for the new names on the vertex side); nothing else in the file changes; CI applies it before the i386 farm key with this
check first. Whether a pipeline links is the device's to show (Alan Wake, first chapter).
"""
from pathlib import Path
import difflib, os, re, subprocess, sys, tempfile

root = Path(__file__).resolve().parents[2]
patch = root / "tools/patch-dxmt-d9-sm3-vs-interface.py"
ok = True


def check(what, cond, detail=""):
    global ok
    print(("ok   " if cond else "FAIL ") + what + (("\n     " + str(detail)[:1500]) if (detail and not cond) else ""))
    ok = ok and bool(cond)


wf = (root / ".github/workflows/build-ipa.yml").read_text()
check("CI applies it with this check first, before the i386 farm key",
      "python3 tests/host/check-dxmt-d9-sm3-vs-interface.py" in wf and "python3 tools/patch-dxmt-d9-sm3-vs-interface.py" in wf
      # after the D3D9/D3D11 query chain (index range, query park, texture formats, stuck queries: each step directly follows the one
      # before, check-gpu-time-wow64.py, check-d9-texfmt.py, check-d9-query-stuck.py), before the farm key
      and wf.index("python3 tools/patch-dxmt-d11-query-stuck.py") < wf.index("python3 tests/host/check-dxmt-d9-sm3-vs-interface.py")
      < wf.index("python3 tools/patch-dxmt-d9-sm3-vs-interface.py") < wf.index("name: i386 farm revision"))

cands = [Path(os.environ["DXMT_SRC"]) if os.environ.get("DXMT_SRC") else None, root / "dxmt", Path.home() / "madeira-bcd/dxmt"]
tree = next((c for c in cands if c and (c / "src/airconv/dxso_compile.cpp").exists()), None)
if tree is None:
    print("skip: no DXMT tree here (CI and the Mac run the source part)")
else:
    shown = subprocess.run(["git", "-C", str(tree), "show", "HEAD:src/airconv/dxso_compile.cpp"], capture_output=True, text=True)
    pinned = shown.stdout if shown.returncode == 0 and shown.stdout else (tree / "src/airconv/dxso_compile.cpp").read_text()
    with tempfile.TemporaryDirectory(prefix="madeira-d9-sm3-") as tmp:
        f = Path(tmp) / "dxso_compile.cpp"
        f.write_text(pinned)
        r1 = subprocess.run([sys.executable, str(patch), str(f)], capture_output=True, text=True)
        once = f.read_text()
        r2 = subprocess.run([sys.executable, str(patch), str(f)], capture_output=True, text=True)
        check("it applies once; a second run says so and changes nothing",
              r1.returncode == 0 and r2.returncode == 0 and "already in" in r2.stdout and f.read_text() == once and once != pinned,
              r1.stdout + r1.stderr + r2.stdout + r2.stderr)
        walk = once[once.index("bool vs3_sem_color[2] = {};"):]
        walk = walk[:walk.index("// Stub the legacy interpolants the shader does not declare")]
        names = {"BlendWeight": "BLENDWEIGHT", "BlendIndices": "BLENDINDICES", "Normal": "NORMAL", "Tangent": "TANGENT",
                 "Binormal": "BINORMAL", "Depth": "DEPTH"}
        check("the vertex walk names the six usages",
              all(("case DxsoUsage::%s:\n          base = \"%s\";" % (u, n)) in walk for u, n in names.items()), walk[-1600:])
        stubs = once[once.index("auto define_stub = [&](const std::string &user) {"):once.index("// PS point-sprite [[point_coord]] input.")]
        check("the pixel side reads and stubs the same names (NORMAL0 ... DEPTH0)",
              all(('define_stub("%s0");' % n) in stubs for n in names.values()))
        check("the walk still defines an output only for a declared usage (base set, then DefineOutput with usage_index)",
              "if (!base)\n          continue;" in walk and ".user = std::string(base) + std::to_string(d.dcl.usage_index)," in walk)
        a, b = pinned.splitlines(), once.splitlines()
        ops = [o for o in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes() if o[0] != "equal"]
        check("nothing else changes: the six cases come in before the default branch, whose TODO comment goes",
              len(ops) == 2 and ops[0][0] == "insert" and b[ops[0][4]] == "        default:"
              and ops[1][0] == "replace" and a[ops[1][1]:ops[1][2]] == ["          break; // Depth / etc; TODO"]
              and b[ops[1][3]:ops[1][4]] == ["          break;"], ops)
        f.write_text(pinned.replace('          break; // Depth / etc; TODO', '          break; // depth etc.'))
        r3 = subprocess.run([sys.executable, str(patch), str(f)], capture_output=True, text=True)
        check("a moved anchor refuses the patch", r3.returncode != 0 and "not found exactly once" in r3.stderr)

print("PASS: vs_3_0 outputs reach the pixel shader under the names it reads" if ok else "FAILED: DXMT D3D9 SM3 vertex interface")
sys.exit(0 if ok else 1)

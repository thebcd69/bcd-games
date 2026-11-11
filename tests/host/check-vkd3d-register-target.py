#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""tools/patch-wine-vkd3d-register-target.py: Wine's HLSL compiler honours register(ps, c0) (night round 2026-11-11, Discord S4).

S.T.A.L.K.E.R. Clear Sky (N8E3WW and six more logs, build 630): "E5017: Aborting due to not yet implemented feature: Reservation shader
target ps." after the `point` fix. Checks:
  - CI runs this check and applies the patch next to the `point` patch, before the i386 farm is built;
  - the auto-tune rule names the error (fixed in 652) and its fixture is there;
  - with a Wine tree: the patch applies once, a second run changes nothing, a moved rule refuses it and leaves the file alone;
    no "Reservation shader target" fixme is left; the helper keeps a reservation for the profile's own type, drops it for another type
    (memset of the reservation) and warns for an unknown target; bison (when installed, >= 3) generates the patched grammar.
"""
import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

root = Path(__file__).resolve().parents[2]
ok = True


def check(what, cond, detail=""):
    global ok
    print(("ok   " if cond else "FAIL ") + what + (("\n     " + str(detail)[:1500]) if (detail and not cond) else ""))
    ok = ok and bool(cond)


spec = importlib.util.spec_from_file_location("regtarget", root / "tools/patch-wine-vkd3d-register-target.py")
P = importlib.util.module_from_spec(spec)
spec.loader.exec_module(P)

wf = (root / ".github/workflows/build-ipa.yml").read_text()
step = wf.index("      - name: Patch wine vkd3d-shader to honour register(ps, c0)\n")
check("CI runs this check and applies the patch before the i386 farm is built",
      "python3 tests/host/check-vkd3d-register-target.py\n          python3 tools/patch-wine-vkd3d-register-target.py wine\n" in wf[step:step + 400]
      and step < wf.index("      - name: Build i386 farm (WoW64)\n"))
rules = (root / "build/tools/autotune_rules.py").read_text()
rb = rules[rules.index('"id": "hlsl-register-shader-target"'):]
rb = rb[:rb.index("\n    },\n")]
check("the auto-tune rule names the error (fixed in 652) and its fixture is there",
      '"fixedIn": 652' in rb and 's("Reservation shader target")' in rb
      and (root / "tests/fixtures/autotune/stalker-clear-sky-register-target-2026-11-05.expect").is_file())

cands = [root / "wine", Path(os.environ["WINE_SRC"]) if os.environ.get("WINE_SRC") else None, Path.home() / "madeira-bcd/wine"]
wine = next((c for c in cands if c and (c / P.FILE).is_file()), None)
if not wine:
    print("skip: no Wine tree here (wine/, WINE_SRC or ~/madeira-bcd/wine); the CI and the Mac run the source part")
    print("PASS: register(ps, c0) is honoured" if ok else "FAILED: vkd3d register-target")
    sys.exit(0 if ok else 1)

src = (wine / P.FILE).read_text()
if P.MARKER in src:
    print("note: the Wine tree is already patched; only the static checks of the patched file run")
    check("the patched tree has no reservation fixme left and the helper once", P.FIXME not in src and src.count(P.HELPER) == 1)
    print("PASS: register(ps, c0) is honoured" if ok else "FAILED: vkd3d register-target")
    sys.exit(0 if ok else 1)
check("the unpatched grammar has the four fixmes the patch replaces", src.count('hlsl_fixme(ctx, &@4, "Reservation shader target %s.", $4);') == 4)
with tempfile.TemporaryDirectory(prefix="madeira-vkd3d-regtarget-") as tmp:
    tmp = Path(tmp)
    tree = tmp / "w"
    (tree / Path(P.FILE).parent).mkdir(parents=True)
    t = tree / P.FILE
    t.write_text(src)
    script = str(root / "tools/patch-wine-vkd3d-register-target.py")
    r1 = subprocess.run([sys.executable, script, str(tree)], capture_output=True, text=True)
    patched = t.read_text()
    r2 = subprocess.run([sys.executable, script, str(tree)], capture_output=True, text=True)
    check("it applies once; a second run says so and changes nothing",
          r1.returncode == 0 and r2.returncode == 0 and "already patched" in r2.stdout and t.read_text() == patched and P.MARKER in patched,
          r1.stdout + r1.stderr + r2.stdout + r2.stderr)
    check("no 'Reservation shader target' fixme is left; four calls of the helper",
          "Reservation shader target" not in patched.replace("Unknown shader target", "")
          and patched.count("madeira_reservation_target_applies(ctx, $4, &@4)") == 4)
    helper = patched[patched.index("static bool madeira_reservation_target_applies("):]
    helper = helper[:helper.index("\n}\n")]
    check("the helper: the profile's own type keeps it, another type drops it, an unknown target warns and keeps it",
          "return ctx->profile->type == types[i].type;" in helper and 'len == 2 && !ascii_strncasecmp(target, types[i].name, 2)' in helper
          and "hlsl_warning(ctx, loc, VKD3D_SHADER_WARNING_HLSL_UNKNOWN_ATTRIBUTE," in helper and helper.rstrip().endswith("return true;")
          and all('{"%s", VKD3D_SHADER_TYPE_%s}' % p in helper for p in
                  (("ps", "PIXEL"), ("vs", "VERTEX"), ("gs", "GEOMETRY"), ("hs", "HULL"), ("ds", "DOMAIN"), ("cs", "COMPUTE"))))
    check("a dropped reservation is cleared (each rule drops $$ when the target does not apply)",
          patched.count("if (!madeira_applies)\n                memset(&$$, 0, sizeof($$));") == 3
          and "if (!madeira_reservation_target_applies(ctx, $4, &@4))\n                    memset(&$$, 0, sizeof($$));" in patched)
    t.write_text(src.replace(P.R1_OLD, P.R1_OLD.replace("if (parse_reservation_index(ctx, $6, 0, &$$))",
                                                        "if (parse_reservation_index(ctx, $6, 0, &($$)))"), 1))
    r3 = subprocess.run([sys.executable, script, str(tree)], capture_output=True, text=True)
    check("a moved rule refuses the patch and leaves the file alone",
          r3.returncode != 0 and "register(target, reg) rule" in r3.stderr and P.MARKER not in t.read_text(), r3.stderr)
    bison = next((b for b in ("/usr/local/opt/bison/bin/bison", "/opt/homebrew/opt/bison/bin/bison", shutil.which("bison") or "")
                  if b and Path(b).is_file()), None)
    if not bison:
        print("skip: no bison here")
    else:
        (tmp / "p.y").write_text(patched)
        g = subprocess.run([bison, "-d", "-o", str(tmp / "p.tab.c"), str(tmp / "p.y")], capture_output=True, text=True, stdin=subprocess.DEVNULL)
        if g.returncode != 0 and "require" in g.stderr:
            print("skip: this bison is too old for vkd3d's grammar")
        else:
            check("bison generates the patched grammar", g.returncode == 0 and (tmp / "p.tab.c").is_file(), g.stderr[-800:])

print("PASS: register(ps, c0) is honoured" if ok else "FAILED: vkd3d register-target")
sys.exit(0 if ok else 1)

#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""Direct3D 9 luminance render targets, opt-in (dxmt e82c8646 + 3d62913b by 125hz, willfaust/dxmt#55, Madeira #575;
BCD: off unless env.MADEIRA_D3D9_LUMINANCE_RT = 1); DXMT's Direct3D 9 frontend source, no device.

An anti-aliasing mod in a 32-bit game (Send Log CZ4R49, build 634, a copy started from the library) asks for D3DFMT_L8 render
targets, DXMT refuses them ("CreateTexture: format 50 not render-target-capable") and the game's threads park for good. The storage of
L8 / A8L8 / L16 (R8Unorm / RG8Unorm / R16Unorm) is colour-renderable on every Metal GPU, so isColorRTFormat() may accept
them. isColorRTFormat() also decides CheckDeviceFormat's RENDERTARGET and AUTOGENMIPMAP answers, CheckDepthStencilMatch
and the three create gates, so upstream's default-on would change what every Direct3D 9 game that probes L8 is told: BCD
makes it opt-in.

1. Source: the luminance case is the only change of isColorRTFormat(); it reads MADEIRA_D3D9_LUMINANCE_RT once and is on
   only for exactly "1"; CheckDeviceFormatConversion keeps the three formats out on both sides (3d62913b).
2. isColorRTFormat() is cut out and compiled on the host next to the same function without the luminance case (the code
   before the change): for every D3DFORMAT value 0..255 and every FOURCC the frontend knows, the answers are identical for
   MADEIRA_D3D9_LUMINANCE_RT unset, "", "0", "true", "2" and " 1"; with "1" only L8, A8L8 and L16 change, to TRUE.
3. The conversion probe (CheckDeviceFormatConversionProbe's body) is cut out and compiled the same way, next to its form
   before 3d62913b: for every pair of those formats it answers the same as before, with the switch on and off.
4. The format layer still maps the three formats to R8Unorm / RG8Unorm / R16Unorm for a render target.
5. Every DXMT patch script that edits d3d9_format.cpp or d3d9_interface.cpp still applies to this tree (a copy).
6. The catalog, the list policy, the session end and the CI step; the auto-tune rule that names the line on the refusal texts (noFix,
   evaluateWhenRan: CZ4R49 is scored "ran") with its fixture; no rule's lines and no list row set the switch.
SKIP of the compiled parts (exit 0) without the dxmt sources (dxmt/ or $DXMT_SRC_DIR), the Direct3D headers or a C++ compiler.
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
dxmt = Path(os.environ.get("DXMT_SRC_DIR", root / "dxmt"))
d9 = dxmt / "src/d3d9"
inc_dx = dxmt / "include/native/directx"
inc_win = dxmt / "include/native/windows"
failures = []


def check(what, cond, detail=""):
    print(("PASS: " if cond else "FAIL: ") + what + (("\n      " + str(detail)[:2000]) if detail and not cond else ""))
    if not cond:
        failures.append(what)


def cut(text, a, b):
    i = text.index(a)
    return text[i:text.index(b, i) + len(b)]


wf = (root / ".github/workflows/build-ipa.yml").read_text()
check("CI runs this check", "python3 tests/host/check-dxmt-d9-luminance-rt.py" in wf)
gen = (root / "build/tools/gen-config-catalog.py").read_text()
ent = gen[gen.index('"env.MADEIRA_D3D9_LUMINANCE_RT"'):] if '"env.MADEIRA_D3D9_LUMINANCE_RT"' in gen else ""
ent = ent[:ent.index("},") + 2] if ent else ""
check("the catalog entry: Direct3D 9 category, a switch, default off, names the source",
      '"category": "Direct3D 9/10/11 (DXMT)"' in ent and '"kind": "bool"' in ent and '"default": "0"' in ent
      and "dxmt/src/d3d9/d3d9_format.cpp" in ent, ent[:600])
pol = (root / "app/Madeira/GameRecommendationsList.swift").read_text()
check("a list may set it (allowedEnvNames), only to 0 or 1",
      '"MADEIRA_D3D9_LUMINANCE_RT",' in pol and 'case "MADEIRA_D3D9_LUMINANCE_RT":' in pol
      and 'throw RecommendationsError.policy("\\(path): MADEIRA_D3D9_LUMINANCE_RT is 0 or 1")' in pol)
bridge = (root / "app/Madeira/WineProcessBridge.m").read_text()
end = bridge[bridge.index("ml1184: these belong to the launch that just ended"):] if "ml1184: these belong to the launch that just ended" in bridge else ""
check("the session end drops it, so a later game of the same app run does not inherit it",
      'unsetenv("MADEIRA_D3D9_LUMINANCE_RT");' in end[:6000])
spec = importlib.util.spec_from_file_location("autotune_rules", root / "build/tools/autotune_rules.py")
rules_mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rules_mod)
# The rule only names the line (noFix + evaluateWhenRan): CZ4R49 was sent while the frozen game still ran, so auto-tune scores it "ran"
# and never changes such a session; the player adds the line. The Steam copy of that game makes no L8 target (82MRGQ), so no list row
# carries it either.
rule = next((r for r in rules_mod.RULES if r["id"] == "d3d9-luminance-rt-refused"), {})
texts = sorted(" ".join(x["has"]) for x in rule.get("any", []))
want = sorted("d3d9: %s: format %s not render-target-capable" % (a, f) for a in ("CreateTexture", "CreateCubeTexture") for f in ("50", "51", "81"))
check("the auto-tune rule names the line on the six refusal texts without changing anything (noFix, evaluateWhenRan, no lines)",
      rule.get("noFix") is True and rule.get("evaluateWhenRan") is True and rule.get("lines") == [] and texts == want
      and "env.MADEIRA_D3D9_LUMINANCE_RT = 1" in rule.get("reason", "")
      and (root / "tests/fixtures/autotune/d3d9-luminance-rt-refused-2026-11-09.expect").is_file(), rule)
check("no auto-tune rule's lines and no list row set the switch",
      not any("MADEIRA_D3D9_LUMINANCE_RT" in " ".join(r.get("lines", [])) for r in rules_mod.RULES)
      and "MADEIRA_D3D9_LUMINANCE_RT" not in (root / "recommended/recommendations.json").read_text())
fixture = root / "tests/fixtures/autotune/d3d9-luminance-rt-refused-2026-11-09.expect"
if fixture.is_file():
    import json
    exp = json.loads(fixture.read_text())
    check("the fixture (CZ4R49) is scored ran and this rule speaks for it", exp.get("outcome") == "ran"
          and "d3d9-luminance-rt-refused" in exp.get("matched", []) and exp.get("next") == [], exp)

if not (d9 / "d3d9_format.cpp").is_file() or not (inc_dx / "d3d9.h").is_file():
    print("SKIP: no dxmt checkout with the Direct3D headers (dxmt/ or DXMT_SRC_DIR)")
    print("FAILED: D3D9 luminance render targets" if failures else "PASS: D3D9 luminance render targets (source part)")
    sys.exit(1 if failures else 0)

fmt = (d9 / "d3d9_format.cpp").read_text()
itf = (d9 / "d3d9_interface.cpp").read_text()

# ---- 1. source
rt = cut(fmt, "bool\nisColorRTFormat(D3DFORMAT format) {", "\n}\n")
lum = re.search(r"\n(  // Luminance formats as render targets:.*?\n  case D3DFMT_L8:\n  case D3DFMT_A8L8:\n  case D3DFMT_L16: \{\n.*?\n  \}\n)", rt, re.S)
check("isColorRTFormat() has the L8 / A8L8 / L16 case (dxmt e82c8646)", lum is not None)
if lum is None:
    print("FAILED: D3D9 luminance render targets: pin the dxmt fork at the commits that carry it")
    sys.exit(1)
case = lum.group(1)
check("... on only for exactly \"1\", read once",
      'static const bool luminanceRT = env::getEnvVar("MADEIRA_D3D9_LUMINANCE_RT") == "1";\n    return luminanceRT;' in case
      and fmt.count('"MADEIRA_D3D9_LUMINANCE_RT"') == 1 and "!= \"0\"" not in case)
check("d3d9_format.cpp includes util_env.hpp for the switch", '#include "util_env.hpp"' in fmt)
check("the conversion probe keeps the three formats out on both sides (dxmt 3d62913b)",
      "return isColorRTFormat(f) && f != D3DFMT_L8 && f != D3DFMT_A8L8 && f != D3DFMT_L16;" in itf
      and "(simple_rgb(SourceFormat) && !IsFloatColorFormat(SourceFormat));" in itf
      and "if (!src_blittable || !simple_rgb(TargetFormat))" in itf)

# ---- 4. the format layer maps them for a render target
for name, metal in (("D3DFMT_L8", "WMTPixelFormatR8Unorm"), ("D3DFMT_A8L8", "WMTPixelFormatRG8Unorm"), ("D3DFMT_L16", "WMTPixelFormatR16Unorm")):
    mm = re.search(r"case %s:.*?return usage == D3D9FormatUsage::DepthStencil \? WMTPixelFormatInvalid : (\w+);" % name, fmt, re.S)
    check(f"{name} maps to {metal} for every usage but depth", mm is not None and mm.group(1) == metal)

# ---- 2 + 3. the compiled harness
cxx = os.environ.get("CXX") or shutil.which("c++") or shutil.which("clang++") or shutil.which("g++")
before_rt = rt.replace(case, "\n", 1).replace("bool\nisColorRTFormat(", "bool\nisColorRTFormatBefore(", 1)
float_fn = cut(fmt, "bool\nIsFloatColorFormat(D3DFORMAT format) {", "\n}\n")
probe = cut(itf, "MTLD3D9Interface::CheckDeviceFormatConversionProbe(", "\n}\n")
body = probe[probe.index("  // wined3d short-circuits the same-format case"):]
lam = ("  const auto simple_rgb = [](D3DFORMAT f) {\n"
       "    return isColorRTFormat(f) && f != D3DFMT_L8 && f != D3DFMT_A8L8 && f != D3DFMT_L16;\n  };\n")
check("the probe's body has the lambda once", body.count(lam) == 1)
before_body = body.replace(lam, "", 1).replace("simple_rgb(", "isColorRTFormatBefore(")
known = sorted(set(int(m) for m in re.findall(r"D3DFMT_\w+\s*=\s*(\d+),", (inc_dx / "d3d9types.h").read_text())) |
               set(range(256)))
fourcc = re.findall(r"D3DFMT_(\w+)\s*=\s*MAKEFOURCC\('(.)', '(.)', '(.)', '(.)'\)", (inc_dx / "d3d9types.h").read_text())
fourcc_vals = [ord(a) | ord(b) << 8 | ord(c) << 16 | ord(d) << 24 for _, a, b, c, d in fourcc]
check("the Direct3D 9 header lists the FOURCC formats the probe set takes", len(fourcc_vals) >= 10, len(fourcc_vals))
formats = known + fourcc_vals
if not cxx:
    print("SKIP: no C++ compiler for the compiled part")
else:
    harness = r'''
#include "d3d9.h"
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
namespace env { static std::string getEnvVar(const char *n) { const char *v = getenv(n); return v ? std::string(v) : std::string(); } }
namespace dxmt {
''' + rt + "\n" + before_rt + "\n" + float_fn + "\n" + \
        "static HRESULT probeNow(D3DFORMAT SourceFormat, D3DFORMAT TargetFormat) {\n" + body + "\n" + \
        "static HRESULT probeBefore(D3DFORMAT SourceFormat, D3DFORMAT TargetFormat) {\n" + before_body + "\n" + r'''
} // namespace dxmt
using namespace dxmt;
static const unsigned formats[] = { ''' + ", ".join("%#xu" % f for f in formats) + r''' };
int main(int argc, char **argv) {
  /* rt: one line per format "<fmt> <now> <before>"; conv: the pairs where the probe now differs from before */
  if (argc > 1 && !strcmp(argv[1], "rt")) {
    for (unsigned f : formats) printf("%u %d %d\n", f, isColorRTFormat((D3DFORMAT)f) ? 1 : 0, isColorRTFormatBefore((D3DFORMAT)f) ? 1 : 0);
    return 0;
  }
  unsigned diff = 0, pairs = 0;
  for (unsigned s : formats) for (unsigned t : formats) {
    pairs++;
    if (probeNow((D3DFORMAT)s, (D3DFORMAT)t) != probeBefore((D3DFORMAT)s, (D3DFORMAT)t)) { if (diff++ < 8) printf("differs %u -> %u\n", s, t); }
  }
  printf("pairs %u differ %u\n", pairs, diff);
  return diff ? 1 : 0;
}
'''
    with tempfile.TemporaryDirectory() as t:
        t = Path(t)
        (t / "h.cpp").write_text(harness)
        exe = t / "h"
        flags = ["-std=c++20", "-O1", "-w", f"-I{inc_dx}", f"-I{inc_win}", f"-I{dxmt / 'include'}"]
        b = subprocess.run([cxx, *flags, "-o", str(exe), str(t / "h.cpp")], capture_output=True, text=True)
        check("the cut-out format gate and conversion probe compile on the host", b.returncode == 0, b.stderr[-2500:])
        if b.returncode == 0:
            lumset = {50, 51, 81}   # D3DFMT_L8, D3DFMT_A8L8, D3DFMT_L16

            def run(value, *args):
                envv = {k: v for k, v in os.environ.items() if k != "MADEIRA_D3D9_LUMINANCE_RT"}
                if value is not None:
                    envv["MADEIRA_D3D9_LUMINANCE_RT"] = value
                return subprocess.run([str(exe), *args], capture_output=True, text=True, env=envv, timeout=120)

            for value in (None, "", "0", "true", "2", " 1", "1"):
                r = run(value, "rt")
                rows = [tuple(int(x) for x in line.split()) for line in r.stdout.split("\n") if line.strip()]
                changed = {f for f, now, before in rows if now != before}
                want = lumset if value == "1" else set()
                label = "(unset)" if value is None else repr(value)
                check(f"MADEIRA_D3D9_LUMINANCE_RT={label}: isColorRTFormat() differs from before for {sorted(want) or 'no format'} "
                      f"({len(rows)} formats)",
                      r.returncode == 0 and len(rows) == len(formats) and changed == want
                      and all(now == 1 for f, now, before in rows if f in want), (sorted(changed), r.stderr[-400:]))
            for value in (None, "1"):
                r = run(value, "conv")
                label = "(unset)" if value is None else repr(value)
                check(f"MADEIRA_D3D9_LUMINANCE_RT={label}: CheckDeviceFormatConversion answers every pair as before", r.returncode == 0,
                      r.stdout[-800:] + r.stderr[-400:])

# ---- 5. the DXMT patch scripts that edit these two files still apply
scripts = sorted(p for p in (root / "tools").glob("patch-dxmt-*.py")
                 if "d3d9_format.cpp" in p.read_text() or "d3d9_interface.cpp" in p.read_text())
check("the scripts that edit d3d9_format.cpp / d3d9_interface.cpp are found", len(scripts) >= 2, [p.name for p in scripts])
with tempfile.TemporaryDirectory() as t5:
    t5 = Path(t5)
    shutil.copytree(d9, t5 / "dxmt/src/d3d9")
    for sc in scripts:
        r5 = subprocess.run([sys.executable, str(sc)], cwd=t5, capture_output=True, text=True)
        check(f"{sc.name} applies to this tree", r5.returncode == 0, r5.stdout[-600:] + r5.stderr[-600:])
    after = (t5 / "dxmt/src/d3d9/d3d9_format.cpp").read_text()
    check("the luminance case is still there after them", case in after)

print("FAILED: D3D9 luminance render targets" if failures else "PASS: D3D9 luminance render targets")
sys.exit(1 if failures else 0)

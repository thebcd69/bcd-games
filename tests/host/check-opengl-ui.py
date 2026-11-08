#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""OpenGL in BCD's own interface and data (OpenGL, build 632; docs/OPENGL.md): where the choice is made and what turns it on.

The choice is a game's: `gl-backend` in the game's own file (Game details), empty = off. This check holds the places that show or set it
and the data that sets it for a game that cannot start without OpenGL:

  1. the game page (BCDGamePageLive.swift): an "OpenGL" row in the Direct3D group, after the Direct3D 12 rows and before the GPU-identity rows,
     a picker built like its neighbours (Self.line), written to the game's own file by setLine; its words, choices and note come from
     BCDOpenGL.swift, which the preview's game page sample (app/BCDPreview/BCDGamePageSample.swift) shows too;
  2. Settings > Graphics says once, in an info group, that OpenGL is a game's choice and where it is made; no app-wide switch exists;
  3. BCDOpenGL.swift compiled on the host (swiftc) with its harness;
  4. the settings catalog: gl-backend in category Graphics (not the app-level one, so lists and auto-tune may set it) with the two words;
     the list policy takes only zink or gles for it; the catalog's other OpenGL entries;
  5. auto-tune: the rule opengl-only-no-context (and the GameMaker runner's) now carry `gl-backend = zink` instead of noFix, without a retry; the
     Godot Vulkan box stays a known failure; each rule's key is one the catalog knows;
  6. the recommended list: the games with a rule carry the line from build 632 only (minBuild), the new rules match the games' app ids, folders and
     programs the logs show, and the line is one the policy takes;
  7. the Xcode project and the notices: BCDOpenGL.swift builds, the About list titles the new license texts, the three notices name Mesa,
     MoltenVK and LuaJIT.
"""
import argparse
import importlib.util
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

R = Path(__file__).resolve().parents[2]
ap = argparse.ArgumentParser()
ap.add_argument("--require-swift", action="store_true")
args = ap.parse_args()
ok = True


def check(what, cond, detail=""):
    global ok
    print(("ok   " if cond else "FAIL ") + what + (("\n     " + str(detail)[-1800:]) if detail and not cond else ""))
    ok = ok and bool(cond)


live = (R / "app/Madeira/BCDGamePageLive.swift").read_text()
sample = (R / "app/BCDPreview/BCDGamePageSample.swift").read_text()
sect = (R / "app/Madeira/BCDSettingsSections.swift").read_text()
core = (R / "app/Madeira/BCDOpenGL.swift").read_text()
proj = (R / "app/Madeira.xcodeproj/project.pbxproj").read_text()
policy = (R / "app/Madeira/GameRecommendationsList.swift").read_text()

# ---- 1. the game page ----------------------------------------------------------------------------------------------------------------
d3d = live[live.index("private func direct3D("):]
d3d = d3d[:d3d.index("// MARK: Compatibility")]
row = 'Self.line(BCDOpenGL.key, "OpenGL", profile, BCDOpenGL.choices,\n                      note: BCDOpenGL.pageNote),'
check("the game page's Direct3D group has the OpenGL row, built with Self.line like its neighbours, after Direct3D 12 GPU sync and before the NVIDIA row",
      row in d3d and d3d.index('Self.line("fence-chain"') < d3d.index(row) < d3d.index('Self.toggle("nvidia"') < d3d.index("Self.line(BCDGPUCards.key"), d3d[:400])
check("... the group keeps its id and title (other texts point to Game details > Direct3D)",
      'BCDGameSettingGroup(id: "direct3d", title: "Direct3D", symbol: "cube.transparent", settings: [' in d3d)
check("the row is written to the game's own file by the same setter as the other Direct3D choices",
      "        case BCDOpenGL.key:\n            // OpenGL (docs/OPENGL.md): a plain line in the game's own file, like the Direct3D choices above.\n            setLine(game, setting, value)" in live
      and "BCDGPUCards.key, BCDGPUCards.memoryKey:\n            setLine(game, setting, value)" in live)
check("the preview's sample page shows the same row from the same words",
      'choice(BCDOpenGL.key, "OpenGL", "", BCDOpenGL.choices, note: BCDOpenGL.pageNote),' in sample
      and sample.index('choice("fence-chain"') < sample.index("choice(BCDOpenGL.key") < sample.index('toggle("nvidia"'))
check("BCDOpenGL.swift is Foundation only, plain values (the preview builds it; the engine side is WineProcessBridge.m)",
      re.findall(r"^import (\w+)$", core, re.M) == ["Foundation"] and "enum BCDOpenGL {" in core and core.startswith("// SPDX-License-Identifier: GPL-3.0-only"))

# ---- 2. Settings ----------------------------------------------------------------------------------------------------------------------
g = sect[sect.index("struct BCDGraphicsSettings<Backend: BCDGraphicsBackend>: View {"):]
g = g[:g.index("private var resolutionChoices")]
grp = 'BCDSettingsGroup(title: "OpenGL") {\n                BCDSettingsInfoRow(title: BCDOpenGL.settingsTitle, note: BCDOpenGL.settingsNote, value: BCDOpenGL.settingsValue)\n            }'
check("Settings > Graphics has an info group naming where OpenGL is chosen (a fact, not a control), before \"More graphics options\"",
      grp in g and g.index(grp) < g.index('title: "More graphics options"') and g.index("if backend.restartNeeded") < g.index(grp))
check("... and no app-wide OpenGL switch exists in Settings (the choice is a game's)",
      "gl-backend" not in sect and "setOpenGL" not in sect and "BCDSettingsValueRow(id: BCDSettingsID.control(.graphics, \"opengl\"" not in sect)

# ---- 3. BCDOpenGL.swift compiled -----------------------------------------------------------------------------------------------------
if shutil.which("swiftc"):
    with tempfile.TemporaryDirectory() as t:
        t = Path(t)
        shutil.copy(R / "tests/host/bcd_opengl_checks.swift", t / "main.swift")
        shutil.copy(R / "app/Madeira/BCDOpenGL.swift", t / "BCDOpenGL.swift")
        exe = t / "bcdopengl"
        cc = subprocess.run(["swiftc", "-o", str(exe), str(t / "main.swift"), str(t / "BCDOpenGL.swift")], capture_output=True, text=True)
        if cc.returncode != 0:
            check("BCDOpenGL.swift and its harness compile", False, cc.stderr)
        else:
            r = subprocess.run([str(exe)], capture_output=True, text=True)
            fails = [l for l in r.stdout.splitlines() if l.startswith("FAIL")]
            check(f"BCDOpenGL.swift: {r.stdout.count('PASS:') - 1} harness checks pass", r.returncode == 0 and not fails, r.stdout + r.stderr)
elif args.require_swift:
    check("swiftc is available", False)
else:
    print("SKIP: no swiftc; BCDOpenGL.swift was not compiled")

# ---- 4. the catalog and the list policy ----------------------------------------------------------------------------------------------
spec = importlib.util.spec_from_file_location("gencat", R / "build/tools/gen-config-catalog.py")
gen = importlib.util.module_from_spec(spec)
sys.path.insert(0, str(R / "build/tools"))
spec.loader.exec_module(gen)
ov = gen.OVERLAY
gb = ov.get("gl-backend", {})
check("the catalog overlay has gl-backend in Graphics (not \"App & front end\"), a choice with the two words and an empty off",
      gb.get("category") == "Graphics" and gb.get("kind") == "choice" and [c[0] for c in gb.get("choices", [])] == ["", "zink", "gles"]
      and gb.get("default") == "", gb)
for key, kind, dflt in (("env.MADEIRA_GL_BACKEND", "text", ""), ("env.MADEIRA_GL_DIR", "text", ""), ("env.MADEIRA_GL_PERF", "bool", "1"),
                        ("env.MADEIRA_GL_FPS_CAP", "bool", "1"), ("env.MADEIRA_GL_COMPILE_TIMEOUT", "int", "20"), ("env.MADEIRA_GL_SHADER_CACHE", "bool", "1"),
                        ("env.MADEIRA_GL_EXTENSIONS_MAX", "int", "3072"), ("env.MADEIRA_GL_QUERY_BUFFER_OFFSETS", "bool", "1")):
    e = ov.get(key, {})
    check(f"the catalog describes {key} ({kind}, default {dflt or 'none'}, Graphics)", e.get("category") == "Graphics" and e.get("kind") == kind and e.get("default") == dflt and len(e.get("note", "")) > 60, e)
check("the catalog notes say BCD, not Madeira", all("Madeira" not in v.get("note", "") for k, v in ov.items() if "GL" in k or k == "gl-backend"))
gen_swift = (R / "app/Madeira/ConfigCatalog.generated.swift").read_text()
if 'key: "gl-backend"' in gen_swift:
    check("the generated catalog is current for the OpenGL keys (regenerated)", all(f'key: "{k}"' in gen_swift for k in ["gl-backend", "env.MADEIRA_GL_BACKEND", "env.MADEIRA_GL_PERF"]))
else:
    print("note: ConfigCatalog.generated.swift is not regenerated in this tree yet (build/tools/gen-config-catalog.py needs the full tree: wine, dxmt, FEX); the lead regenerates it")
check("the list policy takes gl-backend only as zink or gles", 'if key == "gl-backend" {' in policy and 'guard value == "zink" || value == "gles" else {' in policy)
check("... gl-backend is not an environment name lists may set (it is a plain key)", '"MADEIRA_GL_BACKEND"' not in policy.split("static let allowedEnvNames")[1].split("]\n")[0])

# ---- 5. auto-tune ---------------------------------------------------------------------------------------------------------------------
spec = importlib.util.spec_from_file_location("atr", R / "build/tools/autotune_rules.py")
atr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(atr)
rules = {r["id"]: r for r in atr.RULES}
for rid, prio in (("opengl-only-no-context", 2), ("gamemaker-opengl-runner", 1)):
    r = rules[rid]
    check(f"{rid}: the attempt is `gl-backend = zink` at once (noRetry), priority {prio}, no longer a noFix rule",
          r["lines"] == ["gl-backend = zink"] and r.get("noRetry") is True and not r.get("noFix") and r["priority"] == prio and not r.get("fixedIn"), r)
r = rules["opengl-only-no-context"]
check("... the stub line is still required (once the real tables are bound the log says \"opengl32 (winios WGL)\" and the rule cannot match twice)",
      {"has": ["[unixlib] opengl32 (GL-absent stub table)"]} in r["all"])
check("... a Godot 4 game that starts its Vulkan renderer first stays the known failure of its own rule",
      {"has": ["[win-name] #", "Unable to initialize Vulkan video driver"]} in r["none"] and rules["godot-vulkan-or-opengl-only"].get("noFix") is True
      and rules["godot-vulkan-or-opengl-only"]["lines"] == [])
check("luajit-no-low-memory: the attempt is `gl-backend = gles` (the GC64 LuaJIT substitution needs the key; LOVE's hint selects OpenGL ES), no retry, no longer noFix",
      rules["luajit-no-low-memory"]["lines"] == ["gl-backend = gles"] and rules["luajit-no-low-memory"].get("noRetry") is True and not rules["luajit-no-low-memory"].get("noFix"))
check("... the reasons say what the line does (and no longer that BCD has no OpenGL yet)",
      "BCD has no OpenGL yet" not in r["reason"] and "turns on BCD's OpenGL" in r["reason"]
      and "BCD has no OpenGL yet" not in rules["gamemaker-opengl-runner"]["reason"])
check("the keys the rules name are in the catalog overlay", all(l.split(" = ")[0] in ov for rid in ("opengl-only-no-context", "gamemaker-opengl-runner", "luajit-no-low-memory") for l in rules[rid]["lines"]))
fixtures = sorted((R / "tests/fixtures/autotune").glob("*opengl*.expect"))
check("the OpenGL fixtures have .expect files that name the rule (blessed after this change: status trying, not known)", len(fixtures) >= 10, len(fixtures))

# ---- 6. the recommended list -------------------------------------------------------------------------------------------------------------
lst = json.loads((R / "recommended/recommendations.json").read_text())
by = {x["id"]: x for x in lst["rules"]}
want = {  # id: (app id, folder, program, word)
    "the-binding-of-isaac-rebirth": (250900, "the binding of isaac rebirth", "isaac-ng.exe", "zink"),
    "mewgenics": (686060, "mewgenics", "mewgenics.exe", "zink"),
    "doom-3-bfg-edition": (208200, "doom 3 bfg edition", "doom3bfg.exe", "zink"),
    "darkest-dungeon": (262060, "darkestdungeon", "darkest.exe", "zink"),
    "battle-brothers": (365360, "battle brothers", "battlebrothers.exe", "zink"),
    "balatro": (2379780, "balatro", "balatro.exe", "gles"),
}
for rid, (app, folder, prog, word) in want.items():
    r = by.get(rid)
    lines = [c for c in (r or {}).get("config", []) if isinstance(c, dict)]
    check(f"list rule {rid}: Steam {app}, folder {folder!r}, program {prog}, `gl-backend = {word}` from build 632 only",
          r is not None and r["match"] == {"steamAppIDs": [app], "steamFolders": [folder], "programs": [{"name": prog}]}
          and lines == [{"line": f"gl-backend = {word}", "minBuild": 632}] and r["version"] == 1 and any("Not tried on a device yet" in n for n in r["notes"]), r)
for rid in ("jetboard-joust", "stardew-valley"):
    lines = [c for c in by[rid]["config"] if isinstance(c, dict) and c["line"].startswith("gl-backend")]
    check(f"list rule {rid}: carries `gl-backend = zink` from build 632 only, its version raised", lines == [{"line": "gl-backend = zink", "minBuild": 632}], by[rid]["config"])
check("no other list rule turns OpenGL on, and none turns it on without minBuild 632",
      sorted(r["id"] for r in lst["rules"] if any("gl-backend" in (c["line"] if isinstance(c, dict) else c) for c in r["config"]))
      == sorted(list(want) + ["jetboard-joust", "stardew-valley"])
      and all(c.get("minBuild") == 632 for r in lst["rules"] for c in r["config"] if isinstance(c, dict) and c["line"].startswith("gl-backend")))
check("the standard rule is still last, and the list is v32 or later (the lead raised and signed it for round 632)",
      lst["rules"][-1]["id"] == "standard" and lst["version"] >= 32)
ids = [r["id"] for r in lst["rules"]]
check("rule ids are unique", len(ids) == len(set(ids)))

# ---- 7. project and notices ---------------------------------------------------------------------------------------------------------
check("the Xcode project builds BCDOpenGL.swift and WiniosGL.m, links IOSurface and (weakly) OpenGLES, bundles gl/ and compat/, and signs gl/*.dylib",
      "A1004801 /* BCDOpenGL.swift in Sources */" in proj and "A1000701 /* Winios/WiniosGL.m in Sources */" in proj
      and "A1000703 /* IOSurface.framework in Frameworks */" in proj and "settings = {ATTRIBUTES = (Weak, ); }" in proj
      and "A1000705 /* gl in Resources */" in proj and "A1000704 /* compat in Resources */" in proj and 'CODESIGNING_FOLDER_PATH\\"/gl/*.dylib' in proj)
live_s = (R / "app/Madeira/BCDSettingsLive.swift").read_text()
check("About lists the new license texts by name", all(f'"{n}"' in live_s for n in ("MIT-Mesa.txt", "Mesa-license.rst", "MoltenVK-Apache-2.0.txt", "SPIRV-Cross-Apache-2.0.txt",
                                                                                   "SPIRV-Tools-Apache-2.0.txt", "cereal-BSD-3-Clause.txt", "LuaJIT-MIT.txt")))
notices = {p: (R / p).read_text() for p in ("THIRD-PARTY-NOTICES.md", "app/Madeira/legal/THIRD-PARTY-NOTICES.md", "app/Madeira/licenses/THIRD-PARTY-NOTICES.txt")}
check("the three notices name Mesa 25.0.7, MoltenVK 1.4.2 and LuaJIT", all("Mesa" in v and "25.0.7" in v and "MoltenVK" in v and "1.4.2" in v and "LuaJIT" in v for v in notices.values()))
check("the Mesa license text is in LICENSES/ and the bundled copy", (R / "LICENSES/MIT-Mesa.txt").read_text() == (R / "app/Madeira/legal/LICENSES/MIT-Mesa.txt").read_text()
      and "Mesa 3-D graphics library 25.0.7" in (R / "LICENSES/MIT-Mesa.txt").read_text() and "MIT License" in (R / "LICENSES/MIT-Mesa.txt").read_text())
check("the notices keep the credit: Madeira's code is named in the file headers (opengl_ios.c, WiniosGL.m, luajit_compat.c)",
      "Connor Gow" in (R / "build/wineserver/luajit_compat.c").read_text() and (R / "build/win32u-unix/opengl_ios.c").read_text().startswith("/* LGPL-2.1-or-later")
      and (R / "app/Madeira/Winios/WiniosGL.m").read_text().startswith("/* GPL-3.0-or-later WITH the Madeira Converter Exception"))
wf = (R / ".github/workflows/build-ipa.yml").read_text()
check("CI runs this check", "python3 tests/host/check-opengl-ui.py --require-swift" in wf)

print("PASS: OpenGL interface and data" if ok else "FAIL: OpenGL interface and data")
sys.exit(0 if ok else 1)

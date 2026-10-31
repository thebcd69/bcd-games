#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""DXMT's Direct3D 9 for 64-bit games (env.MADEIRA_D3D9_X64 = dxmt): the wiring and the opt-in gates; no Wine runs.

A 64-bit Direct3D 9 game loads Wine's own d3d9.dll from sysx64, which draws through wined3d's OpenGL backend; BCD has no
OpenGL, so Direct3DCreate9 fails ("wined3d_adapter_gl_init Failed to get a GL context", "d3d9_init FAILED") and Darksiders II
Deathinitive Edition blames its resolution. The 32-bit games run DXMT's Direct3D 9 frontend (d3d9-emulated.dll); the same
sources are compiled for arm64ec by tools/build-d3d9-x64-dll.sh and shipped as arm64ec-windows/d3d9-dxmt.dll, which the
bridge links as d3d9.dll only for a game whose file says env.MADEIRA_D3D9_X64 = dxmt.

The owner's rule is that nothing that works today changes, so most of this file proves that:
  - the build script writes into its work directory and d3d9-dxmt.dll, and nowhere else (read in the source, and, with the
    toolchain, run against a scratch directory whose other files are compared before and after);
  - the workflow builds it outside the i386 farm's cache key, after the DXMT patch steps and the i386 farm, never fails the
    run, and hashes the shipped d3d9 / d3d11 / dxgi / d3d10core / winemetal DLLs of both farms before and after;
  - the bridge's block is the only code that names the key or d3d9-dxmt.dll, runs only for the value "dxmt", links only
    d3d9.dll in sysx64 (and system32 in an ARM64EC session), never the i386 farm, and the opt-in DLL loop ignores the file;
  - the key is in the catalog (off by default), the list policy takes only "dxmt", only Darksiders II's list carries it
    (from build 595), and auto-tune's two rules know it (the line, then the noFix text once the line has run).
Set MINGW to the llvm-mingw bin directory (the default: toolchains/ in the repository) for the compile and link.
"""
from pathlib import Path
import hashlib
import json
import os
import re
import struct
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[2]
dxmt = Path(os.environ.get("DXMT_SRC", root / "dxmt"))
ok = True


def check(what, cond):
    global ok
    print(("ok   " if cond else "FAIL ") + what)
    ok &= bool(cond)


sh_path = root / "tools/build-d3d9-x64-dll.sh"
sh = sh_path.read_text()

# --- the build script ---------------------------------------------------------------------------------------------
check("the script compiles for arm64ec with llvm-mingw and links with the DXMT recipe (-O3 -DNDEBUG, C++20, -static, 4096 alignment)",
      "arm64ec-w64-mingw32-clang++" in sh and "-O3 -DNDEBUG" in sh and "-std=c++20" in sh and "-static -Wl,--file-alignment=4096" in sh)
check("it builds in its own work directory (build/dxmt-d3d9-x64) and ships only d3d9-dxmt.dll",
      'OUT="${D3D9X64_OUT:-$R/build/dxmt-d3d9-x64}"' in sh and 'DEST="$SHIP/d3d9-dxmt.dll"' in sh)
writes = re.findall(r'^\s*(?:cp|mv)\b[^\n]*', sh, re.M)
check("the only copy into the bundle is d3d9-dxmt.dll (through a temporary name)",
      len(writes) == 1 and 'cp "$OUT/d3d9.dll" "$DEST.tmp" && mv -f "$DEST.tmp" "$DEST"' in writes[0])
check("it never writes d3d9.dll, the i386 farm, the d3d9 shim, d3d9-emulated.dll, d3d11, dxgi or winemetal",
      not re.search(r'(>|cp |mv |rm |strip |ln )[^\n]*(\$SHIP/(d3d9|d3d11|dxgi|d3d10core|winemetal)\.dll|i386-windows|d3d9shim|d3d9-emulated)', sh))
rms = re.findall(r'^\s*rm\s[^\n]*', sh, re.M)
check("every rm removes something inside its own work directory ($OUT)", rms and all(re.fullmatch(r'rm -(?:rf|f) "\$OUT(/[a-z.]+)?"', r.strip()) for r in rms))
check("it reads the DXMT tree and never edits it (no patch, sed -i or git write on it)",
      not re.search(r'sed -i|patch-dxmt|git -C "\$D" (checkout|apply|reset|stash|commit)', sh) and 'cp -R' not in sh)
check("every failure is a warning that leaves the bundle alone: fail() exits 1 and comes before the copy",
      '::warning::d3d9-dxmt.dll NOT built' in sh and sh.index("fail()") < sh.index('cp "$OUT/d3d9.dll" "$DEST.tmp"'))
check("it checks the result: ARM64EC machine, exports = d3d9.def, imports winemetal.dll and no other graphics DLL",
      "IMAGE_FILE_MACHINE_ARM64EC" in sh and 'diff "$OUT/exports.expected" "$OUT/exports.built"' in sh
      and 'grep -qix "winemetal.dll"' in sh and "dxgi|d3d11|d3d10core|d3d12|opengl32|wined3d" in sh)
check("it refuses a metallib (taken out of the committed d3d11.dll) that lacks a function the pin's dxmt_command.metal defines",
      "dxmt_command.metal changed since it was built" in sh and "[[(?:vertex|fragment|kernel)]]" in sh.replace("\\[\\[", "[[").replace("\\]\\]", "]]"))
check("it pads the image like the other shipped DLLs (SizeOfImage + 0x10000)", "+ 0x10000" in sh and "padding the image failed" in sh)
check("the headless wsi files an aarch64 Windows host gets (src/util/meson.build) are the ones it compiles",
      all(f in sh for f in ("wsi_monitor_headless.cpp", "wsi_window_headless.cpp", "wsi_platform_win32.cpp"))
      and "wsi_window_win32.cpp" not in sh and "madeira" not in sh.split("UTIL_SRC=(")[1].split(")")[0])
check("DXMT_MADEIRA is not defined (the frontend that ships as d3d9-emulated.dll is the one built, not the native ARM64 one)",
      "-DDXMT_MADEIRA" not in sh and "-DDXMT_NATIVE" not in sh)
if (dxmt / "src/d3d9/meson.build").exists():
    mb = (dxmt / "src/d3d9/meson.build").read_text()
    listed = sorted(re.findall(r"'([a-z0-9_]+\.cpp)'", mb[mb.index("d3d9_src = ["):mb.index("\n]")]))
    mine = sorted(s + ".cpp" for s in sh.split("D3D9_SRC=(")[1].split(")")[0].split())
    check("its source list is src/d3d9/meson.build's d3d9_src (%d files)" % len(listed), listed == mine)
    core = re.findall(r"'(dxmt_[a-z_]+\.cpp)'", (dxmt / "src/dxmt/meson.build").read_text().split("dxmt_src = [")[1].split("]")[0])
    mine = sorted(s + ".cpp" for s in sh.split("DXMT_SRC=(")[1].split(")")[0].split())
    check("its DXMT core list is src/dxmt/meson.build's dxmt_src (%d files)" % len(core), sorted(core) == mine)
else:
    print("note: %s not checked out (set DXMT_SRC); the source lists are not compared" % dxmt)

# --- the workflow -------------------------------------------------------------------------------------------------
wf = (root / ".github/workflows/build-ipa.yml").read_text()
steps = re.findall(r"\n      - name: (.+)", wf)
idx = {n: i for i, n in enumerate(steps)}
build_name = next((n for n in steps if n.startswith("Build d3d9-dxmt.dll")), None)
check_name = next((n for n in steps if n.startswith("Check the opt-in 64-bit Direct3D 9")), None)
check("the workflow has the build step and the check step", build_name is not None and check_name is not None)
if build_name and check_name:
    block = wf[wf.index("- name: " + build_name):]
    block = block[:block.index("\n      - name:", 10)]
    check("the build step runs after the DXMT patch steps, the i386 farm (built, cache saved) and its put-back, before the IPA is packaged",
          idx["Patch winemetal pause while inactive"] < idx[build_name] and idx["Save i386 farm"] < idx[build_name]
          and idx["Put back the files patched for the i386 farm only (x64 machine answer)"] < idx[build_name]
          and idx[build_name] < idx["Verify committed PE DLLs"])
    check("it never fails the run (continue-on-error) and runs the script", "continue-on-error: true" in block and "bash tools/build-d3d9-x64-dll.sh" in block)
    check("it hashes the d3d9, d3d11, dxgi, d3d10core and winemetal DLLs of both farms before and after, and fails when one differs",
          'shipped > "$T/before"' in block and 'shipped > "$T/after"' in block and 'cmp -s "$T/before" "$T/after"' in block
          and "app/Madeira/arm64ec-windows/{d3d9,d3d11,dxgi,d3d10core,winemetal}.dll" in block
          and "app/Madeira/i386-windows/{d3d9,d3d9shim,d3d9-emulated,d3d11,dxgi,d3d10core,winemetal}.dll" in block and "exit 1" in block)
    check("the check step runs with the toolchain, after llvm-mingw is installed",
          idx["Install ninja/meson and fetch llvm-mingw"] < idx[check_name] and "check-d3d9-x64.py" in wf)
key = re.search(r'echo "key=(i386-farm-[^"]*)" >> "\$GITHUB_OUTPUT"', wf)
check("the i386 farm's cache key is unchanged: wine revision, dxmt revision, and the hash of build.sh, patch-dxmt-*.py and patch-wine-*.py",
      key is not None and key.group(1) == "i386-farm-$(git -C wine rev-parse HEAD)-$(git -C dxmt rev-parse HEAD)-${{ hashFiles('build/wine-i386/build.sh', 'tools/patch-dxmt-*.py', 'tools/patch-wine-*.py') }}")
# Build 623's d3d9.customDriver (tools/patch-dxmt-d3d9-custom-driver.py, Fallout 3 / New Vegas) is a separate change that must reach
# the i386 farm (both games are 32-bit); it is checked by tests/host/check-dxmt-d3d9-custom-driver.py.
_d3d9_patches = sorted(p.name for p in (root / "tools").glob("patch-dxmt-d3d9*")) + sorted(p.name for p in (root / "tools").glob("patch-wine-d3d9*"))
check("no tools/patch-dxmt-*.py or tools/patch-wine-*.py file belongs to this change (the key hashes them: the i386 farm would be rebuilt)",
      not (root / "tools/patch-dxmt-d3d9-x64.py").exists()
      # round 624 (agent D2 S16): the Direct3D 9 query park measurement is a separate change of its own (check-gpu-time-wow64.py)
      and _d3d9_patches == ["patch-dxmt-d3d9-custom-driver.py", "patch-dxmt-d3d9-query-park.py"])
i386 = (root / "build/wine-i386/build.sh").read_text()
check("the i386 build still installs the shim as d3d9.dll and the emulated frontend as d3d9-emulated.dll, from its own build-pe-i386 tree",
      '"$STRIP" -o "$DEST/d3d9.dll" build-pe-i386/src/d3d9shim/d3d9shim.dll' in i386
      and '"$STRIP" -o "$DEST/d3d9-emulated.dll" build-pe-i386/src/d3d9/d3d9.dll' in i386 and "d3d9-dxmt" not in i386)
gi = (root / ".gitignore").read_text()
check(".gitignore keeps the built DLL and the work directory out of git",
      "app/Madeira/arm64ec-windows/d3d9-dxmt.dll" in gi and "build/dxmt-d3d9-x64/" in gi)

# --- the bridge ---------------------------------------------------------------------------------------------------
m = (root / "app/Madeira/WineProcessBridge.m").read_text()
start = m.index('const char *d3d9X64 = getenv("MADEIRA_D3D9_X64");')
blk = m[start - 40:]
blk = blk[:blk.index("Wine's d3d9.dll stays\\n\");") + 40]
check("the bridge's block runs only for the value dxmt of the game's own line",
      'if (d3d9X64 && strcmp(d3d9X64, "dxmt") == 0) {' in blk and m.count('getenv("MADEIRA_D3D9_X64")') == 1)
check("it links d3d9-dxmt.dll as d3d9.dll in sysx64, and in system32 only in an ARM64EC session",
      '@"d3d9-dxmt.dll"' in blk and 'stringByAppendingPathComponent:@"d3d9.dll"' in blk and 'drive_c/windows/sysx64' in blk
      and "if (use_arm64ec) [dirs addObject:sys32Dir];" in blk and "withDestinationPath:dxmtDll" in blk)
check("it never touches the i386 farm (syswow64) or the other farms' DLLs", "syswow64" not in blk and "i386" not in blk
      and "d3d11" not in blk and "dxgi" not in blk and "winemetal" not in blk)
first = m.index("/* madeira-bcd (2026-10-23): env.MADEIRA_D3D9_X64 = dxmt")
last = m.index("Wine's d3d9.dll stays\\n\");", start) + 40
whole = m[first:last]
check("its block (comment and code) is the only place in the file that names d3d9.dll, d3d9-dxmt or the key",
      m.count('@"d3d9.dll"') == 1 and m[:first].count("d3d9-dxmt") == 0 and m[last:].count("d3d9-dxmt") == 0
      and m[:first].count("MADEIRA_D3D9_X64") == 0 and m[last:].count("MADEIRA_D3D9_X64") == 0 and whole.count("MADEIRA_D3D9_X64") >= 3)
check("it comes after the farms are relinked from the bundle (the line is dropped, Wine's d3d9.dll is back at the next start)",
      m.index('{ "sysx64",  "arm64ec-windows" }') < start < m.index('getenv("MADEIRA_UCRT_X64_MATH")'))
check("it logs what it linked, in the line auto-tune's rules read",
      '"[WineProc] MADEIRA_D3D9_X64=dxmt: d3d9.dll -> d3d9-dxmt.dll (' in blk)
check("a missing DLL leaves Wine's d3d9.dll in place and says so", "this build has no d3d9-dxmt.dll -- Wine's d3d9.dll stays" in blk)
opt = m[m.index('const char *optList = getenv("MADEIRA_OPTIN_DLLS");'):]
opt = opt[:opt.index("\n            }\n")]
check("the opt-in DLL loop (hasSuffix -optin.dll) ignores d3d9-dxmt.dll", 'hasSuffix:@"-optin.dll"' in opt and "d3d9-dxmt.dll".endswith("-optin.dll") is False)

# --- the key: catalog, policy, list --------------------------------------------------------------------------------
cat = (root / "app/Madeira/ConfigCatalog.generated.swift").read_text()
# Round 602 (ajan41): the empty choice is "automatic" (BCDGraphicsDefaults.swift picks DXMT from the game's folder, tests/host/check-d3d9-x64-default.py)
# and "wined3d" is the game's way to keep Wine's d3d9.dll.
check("the catalog lists env.MADEIRA_D3D9_X64 as a choice, empty (automatic) by default, with dxmt and wined3d as the explicit words",
      re.search(r'key: "env.MADEIRA_D3D9_X64".*kind: \.choice, defaultValue: ""', cat) is not None
      and re.search(r'key: "env.MADEIRA_D3D9_X64".*choices: \[\("", "[^"]*"\), \("dxmt", "[^"]*"\), \("wined3d", "[^"]*"\)\]', cat) is not None)
gen = (root / "build/tools/gen-config-catalog.py").read_text()
check("the catalog generator's overlay documents it", '"env.MADEIRA_D3D9_X64": {' in gen)
pol = (root / "app/Madeira/GameRecommendationsList.swift").read_text()
check("a downloaded list may set it, with the value dxmt or wined3d and nothing else",
      '"MADEIRA_D3D9_X64"' in pol and 'case "MADEIRA_D3D9_X64":' in pol and 'guard value == "dxmt" || value == "wined3d"' in pol)
recs = json.loads((root / "recommended/recommendations.json").read_text())
carry = [r["id"] for r in recs["rules"] if "MADEIRA_D3D9_X64" in json.dumps(r["config"])]
# 2026-10-24 (ajan23/loglar): MudRunner and Indivisible crash on the NULL result of the failed Direct3DCreate9 as well; their lists carry the line from build 598.
# Round 602 (ajan41): PICO PARK, Don't Starve Together and A Hat in Time (their own programs fail the same probe) carry the line too, from build 602.
# Round 624 (agent C): No More Room in Hell's 64-bit Source engine (8E0JH6, Z6VXH7, ZSY2WM) carries the line from build 598, as Counter-Strike: Source.
check("only Darksiders II Deathinitive Edition's, MudRunner's, Indivisible's, Counter-Strike: Source's, (round 602) PICO PARK's, Don't Starve Together's and A Hat in Time's and (round 624) No More Room in Hell's lists carry the line",
      carry == ["darksiders-ii-deathinitive", "mudrunner", "indivisible", "counter-strike-source", "pico-park", "dont-starve-together", "a-hat-in-time",
                "no-more-room-in-hell", "outlast"])
_nm = next(r for r in recs["rules"] if r["id"] == "no-more-room-in-hell")
check("no-more-room-in-hell: the line is from build 598 only, after the standard line",
      _nm["config"][2:] == ["env.MADEIRA_EC_HOOK_TRACE = 0", {"line": "env.MADEIRA_D3D9_X64 = dxmt", "minBuild": 598}] and _nm["version"] == 1)
for _id in ("pico-park", "dont-starve-together", "a-hat-in-time"):
    r_ = next(r for r in recs["rules"] if r["id"] == _id)
    check(f"{_id}: the line is from build 602 only, after the standard line", r_["config"][2:] == ["env.MADEIRA_EC_HOOK_TRACE = 0", {"line": "env.MADEIRA_D3D9_X64 = dxmt", "minBuild": 602}] and r_["version"] == 1)
for _id in ("mudrunner", "indivisible"):
    r_ = next(r for r in recs["rules"] if r["id"] == _id)
    check(f"{_id}: the line is from build 598 only, after the standard line", r_["config"][2:] == ["env.MADEIRA_EC_HOOK_TRACE = 0", {"line": "env.MADEIRA_D3D9_X64 = dxmt", "minBuild": 598}])
css = next(r for r in recs["rules"] if r["id"] == "counter-strike-source")
check("Counter-Strike: Source (round 2026-10-24): Steam 240, the folder and cstrike_win64.exe, the line from build 598 only, in version 1",
      css["match"] == {"steamAppIDs": [240], "steamFolders": ["counter-strike source"], "programs": [{"name": "cstrike_win64.exe"}]}
      and css["config"] == ["# BCD's recommended settings for Counter-Strike: Source.",
                            "# Reset to Recommended, at the bottom of the game's settings, brings them back.",
                            "env.MADEIRA_EC_HOOK_TRACE = 0", {"line": "env.MADEIRA_D3D9_X64 = dxmt", "minBuild": 598}]
      and css["version"] == 1)
ds = next(r for r in recs["rules"] if r["id"] == "darksiders-ii-deathinitive")
check("its match is Steam 388410, the folder and darksiders2.exe; the line is from build 595 only",
      ds["match"] == {"steamAppIDs": [388410], "steamFolders": ["darksiders ii deathinitive edition"], "programs": [{"name": "darksiders2.exe"}]}
      and {"line": "env.MADEIRA_D3D9_X64 = dxmt", "minBuild": 595} in ds["config"]
      and ds["config"][:3] == ["# BCD's recommended settings for Darksiders II Deathinitive Edition.",
                               "# Reset to Recommended, at the bottom of the game's settings, brings them back.",
                               "env.MADEIRA_EC_HOOK_TRACE = 0"])
# build 598: Fallout: New Vegas carries the D3D9 last-calls ring (a diagnostic), Resident Evil 6 / Revelations 2 the standard 32-bit frontend
# (env.MADEIRA_D3D9 = emulated, the shim's own default), MudRunner and Indivisible their own lines; none of them names the 64-bit variable.
others = [r["id"] for r in recs["rules"] if r["id"] not in ("darksiders-ii-deathinitive", "fallout-new-vegas", "resident-evil-6", "resident-evil-revelations-2", "mudrunner", "indivisible", "counter-strike-source",
                                                           "pico-park", "dont-starve-together", "a-hat-in-time",
                                                           "grand-theft-auto-iii", "postal-2", "hotline-miami", "max-payne-2",   # these four and red-faction: env.MADEIRA_D3D8 = d3d9
                                                           "red-faction", "no-more-room-in-hell", "outlast",
                                                           "fallout-3")   # round 624: Fallout 3 carries New Vegas's D3D9 last-calls ring
          and "d3d9" in json.dumps(r["config"]).lower()]
check("no other list names d3d9 (the Direct3D 9 default of build 602 comes from the game's folder, BCDGraphicsDefaults.swift, not from a list)", others == [])
print("note: the list's own version is not bumped here (the release does it); the rule's lines carry minBuild 595, so older builds skip it")

# --- auto-tune ----------------------------------------------------------------------------------------------------
rules = (root / "build/tools/autotune_rules.py").read_text()
line_rule = rules[rules.index('"id": "d3d9-64bit-dxmt"'):]
line_rule = line_rule[:line_rule.index("\n    },")]
nofix = rules[rules.index('"id": "d3d9-64bit-needs-opengl"'):]
nofix = nofix[:nofix.index("\n    },")]
field = lambda block, name: re.search(r'^\s*"%s": (\[.*\]),\s*$' % name, block, re.M).group(1)
check("auto-tune adds the line after Wine's d3d9 failed on a missing GL context, never again once the bridge logged the link",
      '"lines": ["env.MADEIRA_D3D9_X64 = dxmt"]' in line_rule and 's("wined3d_adapter_gl_init Failed to get a GL context")' in field(line_rule, "all")
      and 's("Direct3DCreate9 called -> d3d9_init FAILED")' in field(line_rule, "all") and '"noRetry": True' in line_rule
      and 's("MADEIRA_D3D9_X64=dxmt: d3d9.dll -> d3d9-dxmt.dll")' in field(line_rule, "none"))
check("it also needs the game's own graphics error box, and is off for a 32-bit session ([wow-window], wow64cpu)",
      's("[win-name] #", "graphics subsystem")' in field(line_rule, "any")
      and 's("[wow-window]")' in field(line_rule, "none") and 's("wow64cpu")' in field(line_rule, "none"))
check("no other session type is named in the rule (the library and Dock sessions both log the signals)", '"session"' not in line_rule and '"api"' not in line_rule)
check("the noFix text needs the bridge's link line too: it shows only when the line already ran",
      '"noFix": True' in nofix and 's("MADEIRA_D3D9_X64=dxmt: d3d9.dll -> d3d9-dxmt.dll")' in nofix and '"lines": []' in nofix
      and rules.index('"id": "d3d9-64bit-dxmt"') < rules.index('"id": "d3d9-64bit-needs-opengl"'))
fx = root / "tests/fixtures/autotune"

def signal_hits(text, sig):
    lines = text.split("\n")
    n = sum(1 for l in lines if all(h in l for h in sig["has"]))
    return n >= sig.get("count", 1)


def rule_matches(rule, text):
    allsig = all(signal_hits(text, x) for x in rule.get("all", []))
    anysig = not rule.get("any") or any(signal_hits(text, x) for x in rule["any"])
    nonesig = not any(signal_hits(text, x) for x in rule.get("none", []))
    return allsig and anysig and nonesig


# The rule table as data, to try the line rule on variants of Send Log 3F3TYX (the same matching the engine does: a signal is a line
# holding all its strings, "count" of them at least).
import importlib.util
spec = importlib.util.spec_from_file_location("autotune_rules", root / "build/tools/autotune_rules.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
table = {r["id"]: r for r in mod.RULES} if hasattr(mod, "RULES") else {}
if "d3d9-64bit-dxmt" in table:
    base = (fx / "darksiders-2-d3d9-64bit-2026-10-23.log").read_text()
    rule = table["d3d9-64bit-dxmt"]
    check("the line rule matches Send Log 3F3TYX's fixture", rule_matches(rule, base))
    check("not with a 32-bit session's marker added ([wow-window])", not rule_matches(rule, base + "\n[wow-window] main-process reserve FAILED 0x1\n"))
    check("not with wow64cpu loaded", not rule_matches(rule, base + "\nD AC Load module wow64cpu.dll (wow64cpu.dll-1): 71F2680000\n"))
    check("not without the game's graphics error box", not rule_matches(rule, base.replace("Could not initialize the graphics subsystem", "Could not start")))
    check("not when the probe did not fail (no wined3d GL error)", not rule_matches(rule, base.replace("wined3d_adapter_gl_init Failed to get a GL context", "ok")))
    check("not once the bridge logged the link", not rule_matches(rule, base + "\n[WineProc] MADEIRA_D3D9_X64=dxmt: d3d9.dll -> d3d9-dxmt.dll (x)\n"))
    check("the noFix rule matches only with the link line", not rule_matches(table["d3d9-64bit-needs-opengl"], base)
          and rule_matches(table["d3d9-64bit-needs-opengl"], base + "\n[WineProc] MADEIRA_D3D9_X64=dxmt: d3d9.dll -> d3d9-dxmt.dll (x)\n"))
else:
    print("note: the rule table has no RULES list to simulate")

prio = lambda b: int(re.search(r'"priority": (\d+)', b).group(1))
check("the line rule is tried before the noFix rule", prio(line_rule) < prio(nofix))


def expect(name):
    return json.loads((fx / (name + ".expect")).read_text())


a = expect("darksiders-2-d3d9-64bit-2026-10-23")
b = expect("darksiders-2-d3d9-64bit-line-ran-2026-10-23")
check("Send Log 3F3TYX (build 592) now proposes the line", a["matched"] == ["d3d9-64bit-dxmt"] and a["next"] == ["env.MADEIRA_D3D9_X64 = dxmt"] and a["status"] == "trying")
check("the same log with the line in force gets the noFix text", b["matched"] == ["d3d9-64bit-needs-opengl"] and b["next"] == [] and b["status"] == "known")
fixture_logs = {p.name[:-7]: p.read_text(errors="replace") for p in fx.glob("*.expect")}
fires = sorted(n for n in fixture_logs if any(r in expect(n).get("matched", []) for r in ("d3d9-64bit-dxmt", "d3d9-64bit-needs-opengl")))
check("no other fixture (the 32-bit D3D9 games among them) matches either rule: %s" % fires,
      fires == ["counter-strike-source-d3d9-64bit-2026-10-23", "darksiders-2-d3d9-64bit-2026-10-23", "darksiders-2-d3d9-64bit-line-ran-2026-10-23"])
c = expect("counter-strike-source-d3d9-64bit-2026-10-23")
check("Send Log 38S4C1 (build 592): the engine's OutputDebugString wording gets the line, no error box needed",
      c["matched"] == ["d3d9-64bit-dxmt"] and c["next"] == ["env.MADEIRA_D3D9_X64 = dxmt"] and c["status"] == "trying" and c["dialogs"] == [])
if "d3d9-64bit-dxmt" in table:
    cs = (fx / "counter-strike-source-d3d9-64bit-2026-10-23.log").read_text()
    check("...and not without that wording", not rule_matches(table["d3d9-64bit-dxmt"], cs.replace("Failed to create D3D9!.", "ok")))
    check("...and not for a 32-bit session's marker", not rule_matches(table["d3d9-64bit-dxmt"], cs + "\n[wow-window] main-process reserve FAILED 0x1\n"))
d32 = [n for n in fixture_logs if (fx / (n + ".log")).exists() and "d3d9-emulated" in (fx / (n + ".log")).read_text(errors="replace")]
check("%d fixtures of 32-bit Direct3D 9 sessions (d3d9-emulated.dll) are among them" % len(d32), len(d32) >= 3)

# --- compile, link, ship into a scratch directory --------------------------------------------------------------------
mingw = Path(os.environ.get("MINGW") or root / "toolchains/llvm-mingw-20260421-ucrt-macos-universal/bin")
shipped = root / "app/Madeira/arm64ec-windows"
if (mingw / "arm64ec-w64-mingw32-clang++").exists() and (dxmt / "src/d3d9/d3d9_device.cpp").exists() \
        and (shipped / "d3d11.dll").exists() and (shipped / "winemetal.dll").exists():
    sha = lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest()
    with tempfile.TemporaryDirectory(prefix="d3d9x64-") as t:
        ship = Path(t) / "ship"
        ship.mkdir()
        for f in ("d3d11.dll", "winemetal.dll"):
            (ship / f).symlink_to(shipped / f)
        before = {f: sha(shipped / f) for f in ("d3d11.dll", "winemetal.dll")}
        tracked = {p.name: sha(p) for p in (shipped / n for n in ("d3d9.dll", "dxgi.dll", "d3d10core.dll"))}
        env = dict(os.environ, MINGW=str(mingw), SHIP_DIR=str(ship), D3D9X64_OUT=str(Path(t) / "out"), DXMT_DIR=str(dxmt))
        r = subprocess.run(["bash", str(sh_path)], cwd=root, env=env, capture_output=True, text=True)
        check("the script compiles and links the arm64ec DLL and ships it (exit %d)" % r.returncode, r.returncode == 0)
        if r.returncode != 0:
            print((r.stdout + r.stderr)[-3000:])
        out = ship / "d3d9-dxmt.dll"
        check("the scratch bundle holds exactly the two inputs and d3d9-dxmt.dll, no other file, no d3d9.dll",
              sorted(p.name for p in ship.iterdir()) == ["d3d11.dll", "d3d9-dxmt.dll", "winemetal.dll"] and out.is_file() and not out.is_symlink())
        check("the inputs and the DLLs that ship today are byte for byte unchanged",
              all(sha(shipped / f) == v for f, v in before.items()) and all(sha(shipped / f) == v for f, v in tracked.items()))
        check("no temporary file is left behind", not list(ship.glob("*.tmp")))
        if out.is_file():
            d = out.read_bytes()
            pe = struct.unpack_from("<I", d, 0x3C)[0]
            machine = struct.unpack_from("<H", d, pe + 4)[0]
            soi = struct.unpack_from("<I", d, pe + 24 + 56)[0]
            hdr = subprocess.run([str(mingw / "llvm-readobj"), "--file-headers", str(out)], capture_output=True, text=True).stdout
            shipped_machine = struct.unpack_from("<H", (shipped / "d3d11.dll").read_bytes(), struct.unpack_from("<I", (shipped / "d3d11.dll").read_bytes(), 0x3C)[0] + 4)[0]
            check("it is an ARM64EC image (the machine field of the shipped d3d11.dll, the hybrid metadata llvm-readobj names ARM64EC), at least SizeOfImage + 0x10000 long",
                  d[pe:pe + 4] == b"PE\0\0" and machine == shipped_machine and "IMAGE_FILE_MACHINE_ARM64EC" in hdr and len(d) >= soi + 0x10000)
            ex = subprocess.run([str(mingw / "llvm-readobj"), "--coff-exports", str(out)], capture_output=True, text=True).stdout
            names = sorted(set(re.findall(r"Name: (\S+)", ex)))
            want = sorted(re.findall(r"^\s+(\w+) @\d+", (dxmt / "src/d3d9/d3d9.def").read_text(), re.M))
            check("it exports what d3d9.def names (%d functions, Direct3DCreate9 among them)" % len(want), names == want and "Direct3DCreate9" in names)
            im = subprocess.run([str(mingw / "llvm-readobj"), "--coff-imports", str(out)], capture_output=True, text=True).stdout
            dlls = {x.lower() for x in re.findall(r"Name: (\S+\.dll)", im)}
            check("it imports winemetal.dll and no other graphics DLL", "winemetal.dll" in dlls and not dlls & {"dxgi.dll", "d3d11.dll", "d3d10core.dll", "d3d12.dll", "opengl32.dll", "wined3d.dll"})
else:
    print("note: no arm64ec llvm-mingw (set MINGW), dxmt/src/d3d9 or the shipped d3d11.dll / winemetal.dll: the compile and ship test is skipped")

print("PASS" if ok else "FAILED")
sys.exit(0 if ok else 1)

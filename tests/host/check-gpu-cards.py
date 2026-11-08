#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
""""Look like this GPU" (round 603): the graphics card a game is told it has, chosen per game.

BCD reports Apple's GPU (106B:0001) to every game, or a GeForce RTX 3060 (10DE:2544) when the game's NVIDIA switch is on.
A game's line `gpu-card` now picks another: NVIDIA GeForce RTX 3060, AMD Radeon RX 6700 XT or RX 580, Intel UHD Graphics 630
or Arc A770, or Apple's own on purpose. The same name, maker and model reach DXGI (Direct3D 10, 11 and 12) and Direct3D 9
through DXMT's own dxgi.custom* and d3d9.custom* options, and Windows' own adapter (EnumDisplayDevices, the registry, SetupAPI,
D3DKMT) through MADEIRA_GPU_VENDOR, MADEIRA_GPU_DEVICE and MADEIRA_GPU_NAME, which build/win32u-unix/sysparams_ios.c reads.
Video memory is the game's `vram-mb` line (a row of its own on the game page).

Source checks (every host):
  1. BCD's licence header on the new files, the harnesses and this check.
  2. Layers: BCDGPUCards.swift is Foundation only; BCDGPUCardsLive.swift is the engine side and out of the preview build;
     the Xcode project builds both.
  3. One table: the app's cards, win32u's list of identities it may find in a saved prefix and the catalog's choices agree.
  4. Nothing changes without a choice: the launch hook runs after every other DXMT option is composed and before DXMT_CONFIG
     is set, reads the game's line then bcd.cfg's, and returns before touching an option or the environment when there is
     none; win32u reads nothing unless both ids are set, and keeps "BCD Display" and 4096 MB for a game that picked none.
  5. The game page has the two rows and writes the game's own file; CI runs this check.
Behaviour (every host with swiftc and clang; --require-swift makes their absence a failure):
  - tests/host/bcd_gpu_cards_checks.swift: the cards, the options, the environment, how they join a launch's options;
  - win32u's identity functions, cut out of sysparams_ios.c and compiled on the host: the default identity, the NVIDIA switch's,
    a picked card's, and every malformed value ignored.

  check-gpu-cards.py [--require-swift]
"""
import argparse
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

R = Path(__file__).resolve().parents[2]
APP = R / "app/Madeira"
ap = argparse.ArgumentParser()
ap.add_argument("--require-swift", action="store_true")
args = ap.parse_args()
failures = 0


def check(cond, what, detail=""):
    global failures
    print(("PASS: " if cond else "FAIL: ") + what + (("\n      " + str(detail)[-2500:]) if detail and not cond else ""))
    if not cond:
        failures += 1


def code(text):
    out = []
    for line in text.splitlines():
        if line.strip().startswith("//"):
            continue
        out.append(re.sub(r'\s//[^"]*$', "", line))
    return "\n".join(out)


HEADER = ("// SPDX-License-Identifier: GPL-3.0-only\n// Copyright 2026 thebcd69 (BCD)\n"
          "// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.\n")
cards_src = (APP / "BCDGPUCards.swift").read_text()
live_src = (APP / "BCDGPUCardsLive.swift").read_text()
harness = (R / "tests/host/bcd_gpu_cards_checks.swift").read_text()
sysparams = (R / "build/win32u-unix/sysparams_ios.c").read_text()
content = (APP / "ContentView.swift").read_text()
page = (APP / "BCDGamePageLive.swift").read_text()
project = (R / "app/Madeira.xcodeproj/project.pbxproj").read_text()
tool = (R / "tools/ui-preview.py").read_text()
gen = (R / "build/tools/gen-config-catalog.py").read_text()

# ---- 1. licence ---------------------------------------------------------------------------------
# BCDGPUCards.swift and BCDGPUCardsLive.swift hold Madeira code (adec653): BCD's copyright, no "BCD Material" line
# (LICENSE-BCD.md section 1), and the "Portions copyright" credit (owner 2026-11-06).
MIXED = HEADER.split("// BCD Material")[0] + "// Portions copyright 2026 ft7t8drgwf-wq"
for name, text in (("BCDGPUCards.swift", cards_src), ("BCDGPUCardsLive.swift", live_src)):
    check(text.startswith(MIXED), f"{name}: BCD's copyright and the Madeira credit, no BCD Material line")
check(harness.startswith(HEADER), "bcd_gpu_cards_checks.swift: BCD's licence header")
check(Path(__file__).read_text().startswith("#!/usr/bin/env python3\n# SPDX-License-Identifier: GPL-3.0-only\n# Copyright 2026 thebcd69 (BCD)\n"),
      "this check: BCD's licence header")

# ---- 2. layers ----------------------------------------------------------------------------------
check(re.findall(r"^import (\w+)", code(cards_src), re.M) == ["Foundation"], "BCDGPUCards.swift imports Foundation only")
named = sorted({w for w in ("MadeiraConfig", "LogStore", "LibraryModel", "GameProfile", "setenv", "unsetenv", "getenv", "UIKit", "SwiftUI")
                if w in code(cards_src)})
check(not named, "BCDGPUCards.swift names no engine or UI type and touches no environment", ", ".join(named))
check(re.search(r'ENGINE_BOUND\s*\|=\s*\{[^}]*"BCDGPUCardsLive\.swift"', tool) is not None, "BCDGPUCardsLive.swift is in ui-preview.py's ENGINE_BOUND")
check(not re.search(r'ENGINE_BOUND[^\n]*"BCDGPUCards\.swift"', tool), "BCDGPUCards.swift stays in the preview build")
for name in ("BCDGPUCards.swift", "BCDGPUCardsLive.swift"):
    check(project.count(f"/* {name} in Sources */") == 2 and project.count(f"path = {name};") == 1, f"{name} is built by the Xcode project")

# ---- 3. one table ----------------------------------------------------------------------------------
VENDORS = {"apple": 0x106B, "nvidia": 0x10DE, "amd": 0x1002, "intel": 0x8086}
# Build 623: a card may name Direct3D 9's driver file too (the GeForce 7900 GS: nv4_disp.dll).
swift_cards = re.findall(r'\.init\(id: "([\w-]+)", title: "([^"]+)", vendor: (\w+), device: (0x[0-9A-Fa-f]+), name: "([^"]+)"(?:, driver: "[^"]+")?\)', cards_src)
check(len(swift_cards) == 8, "the app has eight cards (build 623: the GeForce 7900 GS; build 632: the GTX 1060 6GB)", len(swift_cards))
swift_pairs = {(VENDORS[v], int(d, 16)) for _, _, v, d, _ in swift_cards}
block = re.search(r"static const struct \{ UINT16 vendor, device; \} ios_known_gpus\[\] =\s*\{(.*?)\};", sysparams, re.S)
check(block is not None, "win32u lists the identities it may find in a prefix")
c_pairs = {(int(v, 16), int(d, 16)) for v, d in re.findall(r"\{\s*(0x[0-9a-fA-F]+),\s*(0x[0-9a-fA-F]+)\s*\}", block.group(1))} if block else set()
check(c_pairs == swift_pairs, "... and it is the app's table, pair for pair", f"C {sorted(map(hex, (a << 16 | b for a, b in c_pairs)))} app {sorted(map(hex, (a << 16 | b for a, b in swift_pairs)))}")
check((0x106B, 0x0001) in c_pairs and (0x10DE, 0x2544) in c_pairs, "... including the two identities BCD had before")
check('if (nv && nv[0] == \'1\') { *vendor = 0x10de; *device = 0x2544; }' in sysparams
      and 'else { *vendor = 0x106b; *device = 0x0001; }' in sysparams, "the NVIDIA switch's identity and the default are as they were")
cat = re.search(r'"gpu-card": \{.*?"choices": \[(.*?)\]\},', gen, re.S)
check(cat is not None, "the catalog has gpu-card")
if cat:
    choices = re.findall(r'\("([\w-]*)", "([^"]*)"\)', cat.group(1))
    expected = [("", "As BCD does by default")] + [(i, t) for i, t, _, _, _ in swift_cards]
    check(choices == expected, "... with the app's choices, in the app's order", f"{choices} vs {expected}")
check('"env.MADEIRA_GPU_VENDOR"' in gen and '"env.MADEIRA_GPU_DEVICE"' in gen and '"env.MADEIRA_GPU_NAME"' in gen,
      "the three environment names the app sets say so in the catalog")

# ---- 4. nothing changes without a choice -------------------------------------------------------------------
hook = "BCDGPUCardsLive.apply(to: &dxmtOptions)"
check(content.count(hook) == 1, "ContentView calls the hook once")
i = content.index(hook)
check(content.rfind('if let c = getenv("MADEIRA_DXMT_EXTRA")', 0, i) > 0 and content.rfind("dxgi.customDeviceId=2544", 0, i) > 0
      and content.index('setenv("DXMT_CONFIG"', i) - i < 400, "... after the NVIDIA switch's and the game's options, right before DXMT_CONFIG is set")
live = code(live_src)
check('MadeiraConfig.gameValue("gpu-card") ?? MadeiraConfig.get("gpu-card")' in live, "the game's line, then bcd.cfg's")
apply_body = live[live.index("static func apply("):]
guard = apply_body.index("guard let card else")
check(guard < apply_body.index("setenv(") and guard < apply_body.index("unsetenv(") and guard < apply_body.index("options = merged.options"),
      "with no card it returns before it touches an option or the environment")
check(apply_body[guard:guard + 400].count("return") >= 1, "... and says so only when the line names a card this build does not know")
check("level: .error" not in live, "an unknown card is a note, not an error (auto-tune reads errors)")
c_head = sysparams[sysparams.index('"Look like this GPU" (app/Madeira/BCDGPUCards.swift)'):]
check('getenv( "MADEIRA_GPU_VENDOR" ), *d = getenv( "MADEIRA_GPU_DEVICE" )' in c_head and "if (!v || !d || !*v || !*d) return 0;" in c_head,
      "win32u reads nothing unless both ids are set")
check("kmt || ios_gpu_pick" in sysparams and "ULONGLONG memory = (ULONGLONG)4096 * 1024 * 1024;" in sysparams,
      "the registry keeps 4096 MB unless the D3DKMT adapter is on or a card was picked")
check('(ios_gpu_pick_name() ? ios_gpu_pick_name() : "BCD Display")' in sysparams, "EnumDisplayDevices says \"BCD Display\" unless a card was picked")
check("ios_forget_virtual_gpu( ios_known_gpus[i].vendor, ios_known_gpus[i].device )" in sysparams
      and "!= pci.vendor || ios_known_gpus[i].device != pci.device" in sysparams,
      "every identity this launch does not report is removed from the saved prefix, never the one it reports")

# ---- 5. the game page ------------------------------------------------------------------------------------
check("Self.line(BCDGPUCards.key, \"Look like this GPU\", profile, BCDGPUCards.choices," in page, "the game page has the card row")
check("Self.line(BCDGPUCards.memoryKey, \"Video memory\", profile, BCDGPUCards.memoryChoices(RuntimeMemorySyncSettings.vramChoices)," in page,
      "... and the video memory row, with Settings' sizes")
check("GamepadInput.padModeKey, BCDGPUCards.key, BCDGPUCards.memoryKey:\n            setLine(game, setting, value)" in page,
      "both are written to the game's own file")
ci = R / ".github/workflows/build-ipa.yml"
if ci.exists():
    check("python3 tests/host/check-gpu-cards.py" in ci.read_text(), "CI runs this check")

# ---- behaviour ---------------------------------------------------------------------------------------------
SWIFTC = shutil.which("swiftc")
if not SWIFTC:
    check(not args.require_swift, "swiftc for the behaviour checks", "not found")
else:
    with tempfile.TemporaryDirectory(prefix="bcd-gpu-cards-") as tmp:
        main = Path(tmp) / "main.swift"
        main.write_text(harness)
        exe = Path(tmp) / "check"
        r = subprocess.run([SWIFTC, "-O", str(APP / "BCDGPUCards.swift"), str(main), "-o", str(exe)], capture_output=True, text=True)
        check(r.returncode == 0, "the cards' behaviour checks compile on the host", r.stderr or r.stdout)
        if r.returncode == 0:
            r = subprocess.run([str(exe)], capture_output=True, text=True, timeout=300)
            for line in r.stdout.splitlines():
                if line.startswith("FAIL") or line.startswith("ALL"):
                    print("  " + line)
            check(r.returncode == 0, "the cards behave as described", r.stdout + r.stderr)

CC = shutil.which("clang") or shutil.which("cc")
if not CC:
    check(not args.require_swift, "a C compiler for win32u's identity functions", "not found")
else:
    start = sysparams.index('/* madeira-bcd (2026-10-25, round 603): "Look like this GPU"')
    end = sysparams.index("/* Every game shares one prefix")
    cut = sysparams[start:end]
    program = ('#include <stdio.h>\n#include <stdlib.h>\n#include <string.h>\ntypedef unsigned short UINT16;\n'
               'static const char *gpu_device_name( UINT16 vendor, UINT16 device, const char *default_name ) { (void)vendor; (void)device; return default_name; }\n'
               + cut +
               '\nint main(void)\n{\n    UINT16 v = 0, d = 0;\n    const char *picked;\n    ios_virtual_gpu_ids( &v, &d );\n'
               '    picked = ios_gpu_pick_name();\n'
               '    printf( "%04x:%04x name=%s picked=%s\\n", v, d, ios_virtual_gpu_name( v, d ), picked ? picked : "(none)" );\n    return 0;\n}\n')
    with tempfile.TemporaryDirectory(prefix="bcd-gpu-c-") as tmp:
        src = Path(tmp) / "pick.c"
        src.write_text(program)
        exe = Path(tmp) / "pick"
        r = subprocess.run([CC, "-Wall", "-Werror", "-Wno-unused-function", "-Wno-unused-const-variable", str(src), "-o", str(exe)], capture_output=True, text=True)
        check(r.returncode == 0, "win32u's identity functions compile on the host", r.stderr or r.stdout)
        if r.returncode == 0:
            def run(env):
                full = {"PATH": "/usr/bin:/bin"}
                full.update(env)
                return subprocess.run([str(exe)], capture_output=True, text=True, env=full).stdout.strip()
            default = "106b:0001 name=BCD Display picked=(none)"
            check(run({}) == default, "no card: Apple's identity and \"BCD Display\", as before", run({}))
            check(run({"DXMT_ENABLE_NVEXT": "1"}) == "10de:2544 name=BCD Display picked=(none)",
                  "the NVIDIA switch: 10DE:2544 (the name comes from the table in the real file)", run({"DXMT_ENABLE_NVEXT": "1"}))
            amd = {"MADEIRA_GPU_VENDOR": "1002", "MADEIRA_GPU_DEVICE": "73df", "MADEIRA_GPU_NAME": "AMD Radeon RX 6700 XT"}
            check(run(amd) == "1002:73df name=AMD Radeon RX 6700 XT picked=AMD Radeon RX 6700 XT", "a picked card: its ids and its name", run(amd))
            check(run({**amd, "DXMT_ENABLE_NVEXT": "1"}).startswith("1002:73df"), "a picked card wins over the NVIDIA switch")
            nameless = {"MADEIRA_GPU_VENDOR": "8086", "MADEIRA_GPU_DEVICE": "3e92"}
            check(run(nameless) == "8086:3e92 name=BCD Display picked=(none)", "a card without a name takes the table's (here the default)", run(nameless))
            for bad in ({"MADEIRA_GPU_VENDOR": "1002"}, {"MADEIRA_GPU_DEVICE": "73df"}, {"MADEIRA_GPU_VENDOR": "zz", "MADEIRA_GPU_DEVICE": "73df"},
                        {"MADEIRA_GPU_VENDOR": "1002", "MADEIRA_GPU_DEVICE": "73dfx"}, {"MADEIRA_GPU_VENDOR": "0", "MADEIRA_GPU_DEVICE": "1"},
                        {"MADEIRA_GPU_VENDOR": "10002", "MADEIRA_GPU_DEVICE": "1"}, {"MADEIRA_GPU_VENDOR": "1002", "MADEIRA_GPU_DEVICE": "10000"},
                        {"MADEIRA_GPU_VENDOR": "", "MADEIRA_GPU_DEVICE": ""}):
                check(run({**bad, "MADEIRA_GPU_NAME": "Evil"}) == default, f"a half or malformed pick is ignored: {bad}", run(bad))

if failures:
    print(f"{failures} FAILED")
    sys.exit(1)
print("PASS: Look like this GPU")

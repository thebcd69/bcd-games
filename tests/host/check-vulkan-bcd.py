#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""Windows Vulkan, BCD's own parts (build 646; the route itself is Madeira's #353, #400 and #462, checked by check-vulkan-*.py).

A game whose file has `vulkan = moltenvk` gets Wine's winevulkan on MoltenVK (WineProcessBridge.m, build/win32u-unix/vulkan_ios.c); every other
session is what it was. Checks:
  - the session key: only moltenvk (and a bundled kosmickrisp) turn it on, it runs after the farm passes, and the session's end clears it;
  - the list policy accepts `vulkan` only as moltenvk; the settings catalog has the key (default off) and its two environment names;
  - the game page has a per-game switch for it (owner 2026-11-09), which writes or removes `vulkan = moltenvk` in the game's own file;
  - Vulkan presents count as frames for BCD's front end (WiniosGL.m), counting only, never waiting;
  - the bundle folder is git-ignored but its README is tracked, and the Xcode project carries the folder;
  - CI: Madeira's Wine change is written before win32u is built, the DLLs are built and verified, the tests run, the unix table is checked;
  - tools/patch-wine-vulkan-suboptimal.py on a copy of Wine's win32u/vulkan.c (when the wine tree is there): applies once, then only says so.
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

root = Path(__file__).resolve().parents[2]
ok = True


def check(name, cond, detail=""):
    global ok
    print(("ok   " if cond else "FAIL ") + name)
    if not cond:
        ok = False
        if detail:
            print("     " + str(detail)[:1500])


def body(text, start, end="\n}\n"):
    i = text.find(start)
    if i < 0:
        return ""
    return text[i:text.find(end, i) + len(end)]


bridge = (root / "app/Madeira/WineProcessBridge.m").read_text()
setup = body(bridge, "static void bcd_vulkan_session_setup(")
check("the session key turns Vulkan on only for moltenvk (and kosmickrisp when bundled); anything else leaves it off and unexported",
      'if (!strcmp(want, "moltenvk")) lib = [bundlePath stringByAppendingPathComponent:@"gl/libMoltenVK.dylib"];' in setup
      and 'else if (!strcmp(want, "kosmickrisp"))' in setup
      and setup.count('unsetenv("MADEIRA_VULKAN_LIBRARY");') == 2
      and 'setenv("MADEIRA_VULKAN_LIBRARY", lib.UTF8String, 1);' in setup, setup[:1500])
check("...stays off when the library or a DLL is missing, and removes only links into the bundle's vulkan folder",
      'access([stack stringByAppendingPathComponent:@"winevulkan.dll"].fileSystemRepresentation, R_OK) != 0' in setup
      and '[dest rangeOfString:@"/vulkan/arm64ec-windows/"].location != NSNotFound' in setup, setup[:1500])
call = bridge.find("bcd_vulkan_session_setup(fm, prefix, bundlePath, use_arm64ec);")
check("...is decided after the OpenGL key (after the farm passes) and cleared at the session's end",
      0 < bridge.find("bcd_gl_session_setup(bundlePath);") < call
      and 'unsetenv("MADEIRA_VULKAN_LIBRARY");   /* madeira-bcd (Windows Vulkan): likewise' in body(bridge, "static void wine_session_end(void)\n{"))

policy = (root / "app/Madeira/GameRecommendationsList.swift").read_text()
check("a downloaded list may set vulkan only to moltenvk",
      'if key == "vulkan" {' in policy and 'guard value == "moltenvk" else {' in policy
      and 'RecommendationsError.policy("\\(path): \\(key) is moltenvk")' in policy)

gen = (root / "build/tools/gen-config-catalog.py").read_text()
catalog = (root / "app/Madeira/ConfigCatalog.generated.swift").read_text()
check("the settings catalog has the vulkan key, off by default, with moltenvk as its one choice",
      '"vulkan": {"category": "Graphics", "title": "Vulkan for this game (x64)", "kind": "choice", "default": "",' in gen
      and '("moltenvk", "Vulkan on Metal (MoltenVK)")' in gen and 'ConfigOption(key: "vulkan"' in catalog)
check("...and its two environment names (the library the app exports, Madeira's present switch), both curated",
      '"env.MADEIRA_VULKAN_LIBRARY": {' in gen and '"env.MADEIRA_VK_HOST_SUBOPTIMAL": {' in gen
      and 'ConfigOption(key: "env.MADEIRA_VULKAN_LIBRARY"' in catalog and 'ConfigOption(key: "env.MADEIRA_VK_HOST_SUBOPTIMAL"' in catalog)

page = (root / "app/Madeira/BCDGamePageLive.swift").read_text()
d3d = page[page.index("private func direct3D("):page.index("// MARK: Compatibility")]
check("the game page's Direct3D group has a Vulkan switch, on only when the game's file says moltenvk, after the OpenGL row and before NVIDIA",
      'Self.toggle("vulkan", "Vulkan (experimental)", profile.get("vulkan") == "moltenvk",' in d3d
      and d3d.index("Self.line(BCDOpenGL.key") < d3d.index('Self.toggle("vulkan"') < d3d.index('Self.toggle("nvidia"'), d3d[:600])
check("...the switch writes `vulkan = moltenvk` into the game's own file and takes it out when off",
      '        case "vulkan":\n            // Windows Vulkan (docs/VULKAN.md): the switch is the line `vulkan = moltenvk` in the game\'s own file.\n'
      '            setLine(game, "vulkan", on ? "moltenvk" : "")' in page)
sample = (root / "app/BCDPreview/BCDGamePageSample.swift").read_text()
check("...and the preview's sample page shows it in the same place",
      'toggle("vulkan", "Vulkan (experimental)",' in sample
      and sample.index("choice(BCDOpenGL.key") < sample.index('toggle("vulkan"') < sample.index('toggle("nvidia"'))

gl = (root / "app/Madeira/Winios/WiniosGL.m").read_text()
surface = (root / "build/win32u-unix/vulkan_surface_ios.c").read_text()
note = body(gl, "void madeira_vulkan_present_note(void) {")
check("a Vulkan present counts as a frame for the front end: one atomic add, nothing that can wait (win32u holds surfaces_lock)",
      note.strip().count("\n") == 2 and "atomic_fetch_add_explicit(&vulkan_present_count, 1, memory_order_relaxed);" in note
      and "atomic_load_explicit(&vulkan_present_count, memory_order_relaxed)" in body(gl, "uint64_t madeira_gl_present_count(void) {")
      and "madeira_vulkan_present_note();" in body(surface, "static void ios_vulkan_surface_present("), note)

ignore = (root / ".gitignore").read_text()
pbx = (root / "app/Madeira.xcodeproj/project.pbxproj").read_text()
check("the DLL folder is ignored, the folder's README is tracked, and the app bundles the vulkan folder",
      "app/Madeira/vulkan/arm64ec-windows/" in ignore and (root / "app/Madeira/vulkan/README.md").is_file()
      and "A1000707 /* vulkan in Resources */" in pbx and 'A2000707 /* vulkan */ = {isa = PBXFileReference; lastKnownFileType = folder; path = vulkan;' in pbx)

ci = (root / ".github/workflows/build-ipa.yml").read_text()
patch_step = ci.find("python3 tools/patch-wine-vulkan-suboptimal.py")
check("CI writes Madeira's Wine present change before win32u is built, and checks it is there",
      0 < patch_step < ci.find("      - name: Build win32u-unix\n")
      and "grep -q MADEIRA_VK_HOST_SUBOPTIMAL wine/dlls/win32u/vulkan.c" in ci)
check("CI builds the two DLLs after the extra Wine DLLs, verifies the pieces after OpenGL's, and runs the Vulkan tests",
      ci.find('run: bash tools/build-wine-extra-dlls.sh "$GITHUB_WORKSPACE/wine/build-native"') < ci.find("run: bash tools/build-vulkan-dlls.sh")
      and ci.find("run: python3 tools/check-opengl-shipped.py") < ci.find("run: python3 tools/check-vulkan-shipped.py")
      and all(("python3 tests/host/%s" % t) in ci for t in ("check-vulkan-opt-in.py", "check-vulkan-surface.py", "check-vk-surface-query.py",
                                                           "check-vulkan-layer-geometry.py", "check-vulkan-bcd.py", "check-unixlib-binder.py")))
check("CI checks libntdll_unix.a defines winevulkan's unix table and that its objects are not empty",
      "opengl32_unix_call_funcs opengl32_unix_call_wow64_funcs winevulkan_unix_call_funcs; do" in ci
      and "opengl32_wgl opengl32_thunks winevulkan_vulkan winevulkan_thunks; do" in ci)

# the Wine patch, on a copy (never on the tree)
wine = Path(os.environ.get("WINE_SRC", root / "wine"))
src = wine / "dlls/win32u/vulkan.c"
if src.is_file():
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        (work / "wine/dlls/win32u").mkdir(parents=True)
        text = src.read_text()
        if "ignore_host_suboptimal" in text:
            print("note: the wine tree is patched already; the patch is checked on the text without it is not possible here")
            check("the patched tree carries the switch", "MADEIRA_VK_HOST_SUBOPTIMAL" in text)
        else:
            (work / "wine/dlls/win32u/vulkan.c").write_text(text)
            r1 = subprocess.run([sys.executable, str(root / "tools/patch-wine-vulkan-suboptimal.py")], cwd=work, capture_output=True, text=True)
            out = (work / "wine/dlls/win32u/vulkan.c").read_text()
            r2 = subprocess.run([sys.executable, str(root / "tools/patch-wine-vulkan-suboptimal.py")], cwd=work, capture_output=True, text=True)
            check("the Wine patch applies to this tree's win32u/vulkan.c and adds the switch",
                  r1.returncode == 0 and "MADEIRA_VK_HOST_SUBOPTIMAL" in out and out.count("ignore_host_suboptimal()") == 1, r1.stdout + r1.stderr)
            check("...a second run changes nothing and only says so",
                  r2.returncode == 0 and "already there" in r2.stdout and (work / "wine/dlls/win32u/vulkan.c").read_text() == out, r2.stdout)
            check("...and the host result still passes through for a real resize (the client rect check stays)",
                  "else if (!extents_equals( &swapchain->extents, &client_rect ))" in out and "suboptimal_kept = TRUE;" in out)
else:
    print("skip the Wine patch check: no wine tree (set WINE_SRC)")

print("PASS: Windows Vulkan (BCD's parts)" if ok else "FAILED: Windows Vulkan (BCD's parts)")
sys.exit(0 if ok else 1)

#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""Lossless Scaling frame generation, Direct3D 11 (tools/patch-dxmt-lsfg.py, tools/patch-winemetal-lsfg-pacing.py, the app side).
No file of Lossless Scaling is used or needed; no Metal device, no Wine, no iPhone.

1. tools/patch-dxmt-lsfg.py applies to dxmt/src/d3d11 in the three states a build has it in: the pinned sources, the copy
   tools/build-d3d11-dll.sh patches for d3d11-src.dll (its BCD_PATCHES before this one), and the tree after the workflow's whole chain
   before it. Each time: idempotent, the swap chain changed by exactly the script's hunks, d3d11_lsfg.hpp written as the script holds
   it. A tree a build already patched is recognised and checked by undoing the hunks.
2. Nothing of Lossless Scaling is in BCD, and the upscaler is not taken: the header holds no shader, only the ids of the frame
   generation (the same 48 the app extracts), the notices of Porthole and of lsfg-vk 1.0.0 (MIT) are in it, and none of the LS1 code.
3. Off path, on the source: LsfgBeforePresent returns at its first line without MADEIRA_LSFG_CTL (or MADEIRA_LSFG=1), and a frame
   without generated pictures takes its present state where it always did.
4. The patched swap chain compiles for arm64ec with the flags of tools/build-d3d11-dll.sh when the llvm-mingw toolchain is there.
5. d3d11-src.dll is built with it (BCD_PATCHES and the IPA's marker list), and the workflow patches before the i386 farm key.
6. The pacing in winemetal: applies in the family's order, exact, compiles for iOS; with the number at 1 the mode is the game's own;
   the mapping itself is run on the host.
7. The app: tests/host/bcd_lossless_checks.swift (the receipt, the sealed file, reading only the frame generation's shaders out of a
   made-up DLL, the control file, the menu), and the wiring: a game without the `lossless-scaling` line exports nothing.
SKIP (exit 0) without the dxmt sources.
"""
from pathlib import Path
import importlib.util
import os
import re
import shutil
import subprocess
import sys
import tempfile

R = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import winemetal_fx_kit as kit   # noqa: E402

if not kit.have_sources():
    print(f"SKIP: no dxmt sources at {kit.SRC} (check out the submodule or set DXMT_SRC_DIR)")
    sys.exit(0)
failures = []


def check(what, cond):
    print(("PASS: " if cond else "FAIL: ") + what)
    if not cond:
        failures.append(what)
    return cond


SCRIPT = "tools/patch-dxmt-lsfg.py"
mod = kit.load(SCRIPT)
wf = (R / ".github/workflows/build-ipa.yml").read_text()
sh = (R / "tools/build-d3d11-dll.sh").read_text()
chain = kit.chain()
SRC = kit.SRC


def run(script, cwd, *args):
    return subprocess.run([sys.executable, str(R / script), *args], cwd=cwd, capture_output=True, text=True)


def undo(text):
    for _, anchor, new in reversed(mod.PAIRS):
        if text.count(new) != 1:
            return None
        text = text.replace(new, anchor, 1)
    return text


mingw = next((p for p in (Path(os.environ.get("MINGW", "/nonexistent")), R / "toolchains/llvm-mingw-20260421-ucrt-macos-universal/bin",
                          Path.home() / "madeira-bcd/toolchains/llvm-mingw-20260421-ucrt-macos-universal/bin")
              if (p / "arm64ec-w64-mingw32-clang++").exists()), None)


def compiles(d3d11, core):
    """The swap chain of this tree, syntax and types, for arm64ec (the flags of tools/build-d3d11-dll.sh)."""
    d = R / "dxmt"
    with tempfile.TemporaryDirectory() as gen:
        (Path(gen) / "version.h").write_text('#define DXMT_VERSION "check"\n')
        c = subprocess.run([str(mingw / "arm64ec-w64-mingw32-clang++"), "-std=c++20", "-O3", "-DNDEBUG", "-Wimplicit-fallthrough",
                            "-Wno-missing-field-initializers", "-Wno-unused-parameter", "-Wno-cast-function-type", "-Wno-unused-private-field",
                            "-Wno-microsoft-exception-spec", "-Wno-extern-c-compat", "-Wno-unused-const-variable", "-Wno-missing-braces", "-fblocks",
                            "-DNOMINMAX", "-D_WIN32_WINNT=0xa00", "-DDXMT_IOS=1", "-DDXMT_PAGE_SIZE=4096",
                            f"-I{d3d11}", f"-I{d}/src/dxgi", f"-I{core}", f"-I{d}/src/airconv", f"-I{d}/src/winemetal", f"-I{d}/src/util",
                            f"-I{d}/include", f"-I{d}/libs", f"-I{gen}", "-fsyntax-only", str(Path(d3d11) / "d3d11_swapchain.cpp")],
                           capture_output=True, text=True)
    return c


check("the workflow runs the script before the i386 farm key, and this check",
      SCRIPT in chain and wf.index("python3 tools/patch-dxmt-lsfg.py") < wf.index("name: i386 farm revision")
      and "run: python3 tests/host/check-lsfg.py" in wf)
check("d3d11-src.dll is built with it and an IPA without it is refused (BCD_PATCHES, the workflow's marker list)",
      '"patch-dxmt-lsfg.py" "[lsfg] madeira-bcd ")' in sh and '"[view-ref] madeira-bcd #" "[lsfg] madeira-bcd "; do' in wf
      and "[lsfg] madeira-bcd {} {} get frame generation" in mod.HEADER)

came_patched = mod.MARKER in (SRC / "d3d11/d3d11_swapchain.cpp").read_text()
states = {}
with tempfile.TemporaryDirectory() as tmp:
    t = Path(tmp)
    # ---- 1. the three states
    for name in ("pinned", "d3d11-src", "chain"):
        root = t / name
        if name == "chain" and not came_patched:   # the whole tree: the chain's scripts patch all of it
            shutil.copytree(SRC, root / "dxmt/src")
            (root / "build").mkdir()
            shutil.copy(R / "build/madeira_cfg.h", root / "build/madeira_cfg.h")
            shutil.copytree(R / "research/remote-metal", root / "research/remote-metal")
        else:
            for part in ("d3d11", "d3d10", "dxmt"):
                shutil.copytree(SRC / part, root / "dxmt/src" / part)
        swap = root / "dxmt/src/d3d11/d3d11_swapchain.cpp"
        problems = []
        if came_patched:
            pass   # a build's tree: every state is this tree; the hunks are undone below
        elif name == "d3d11-src":
            p = run("tools/patch-dxmt-context-state-swap.py", root, str(root / "dxmt/src/d3d11"))
            if p.returncode:
                problems.append("context-state-swap: " + (p.stdout + p.stderr).strip()[:200])
            listed = re.findall(r'"(patch-dxmt-[a-z0-9-]+\.py)" "', sh)
            for s in listed[:listed.index("patch-dxmt-lsfg.py")]:
                p = run("tools/" + s, root)
                if p.returncode:
                    problems.append(s + ": " + (p.stdout + p.stderr).strip()[:200])
        elif name == "chain":
            for s in chain[:chain.index(SCRIPT)]:
                if s.endswith("patch-dxmt-metal-atomics.py"):
                    continue   # it patches the repository's own dxmt whatever the working directory, and only a shader this check does not read
                p = run(s, root)
                if p.returncode:
                    problems.append(s + ": " + (p.stdout + p.stderr).strip()[:200])
        before = swap.read_text()
        p1 = run(SCRIPT, root)
        after = swap.read_text()
        p2 = run(SCRIPT, root)
        hdr = root / "dxmt/src/d3d11/d3d11_lsfg.hpp"
        for problem in problems:
            print(f"  {name}: {problem}")
        ok = (not problems and p1.returncode == 0 and mod.MARKER in after and hdr.is_file() and hdr.read_text() == mod.HEADER
              and p2.returncode == 0 and swap.read_text() == after and "already patched" in p2.stdout)
        undone = undo(after)
        exact = undone is not None and mod.MARKER not in undone and (came_patched or undone == before)
        check(f"{name}: the script applies, a second run changes nothing, the header is the script's", ok)
        check(f"{name}: the swap chain is changed by the script's hunks and nothing else", exact)
        if not ok:
            print((p1.stdout + p1.stderr)[-600:])
        states[name] = root
        if came_patched:
            break
    swapchain = (next(iter(states.values())) / "dxmt/src/d3d11/d3d11_swapchain.cpp").read_text()

    # ---- 4. compiles for arm64ec
    if not mingw or not (R / "dxmt/include").is_dir():
        print("SKIP: no llvm-mingw toolchain (MINGW, toolchains/ or the build clone's) for the arm64ec compile part")
    else:
        for name, root in states.items():
            c = compiles(root / "dxmt/src/d3d11", root / "dxmt/src/dxmt")
            if not check(f"{name}: the patched swap chain compiles for arm64ec (C++20, the flags of tools/build-d3d11-dll.sh)", c.returncode == 0):
                print("\n".join(l for l in c.stderr.splitlines() if "error" in l)[:3000])
            check(f"{name}: no warning in it", "warning:" not in c.stderr)

# ---- 2. what is in the header, and what is not
H = mod.HEADER
check("the header keeps Porthole's copyright and licence and says what BCD changed",
      H.startswith("// SPDX-License-Identifier: GPL-3.0-or-later\n// Copyright (C) 2026 The Porthole authors. This file was taken from "
                   "https://github.com/tylerkaska112/Porthole (commit 99ec315:") and "Modified by BCD (thebcd69), November 2026" in H)
mit = (R / "LICENSES/MIT-lsfg-vk.txt").read_text() if (R / "LICENSES/MIT-lsfg-vk.txt").is_file() else ""
squash = lambda s: re.sub(r"[\s/]+", " ", s)
check("lsfg-vk 1.0.0's MIT notice is in the header and in LICENSES/MIT-lsfg-vk.txt, copyright line and permission notice",
      "Copyright (c) 2025 lsfg-vk" in H and "Copyright (c) 2025 lsfg-vk" in mit
      and "The above copyright notice and this permission notice shall be included in all copies or substantial portions of the Software." in squash(H)
      and "The above copyright notice and this permission notice shall be included in all copies or substantial portions of the Software." in squash(mit)
      and 'THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND' in squash(H) and "tag v1.0.0" in H
      and "Nothing of lsfg-vk later than 1.0.0 is used" in H)
ids = set()
for table in re.findall(r"inline constexpr Ids k\w+\{([^;]+)\};", H):
    ids |= {int(n) for n in re.findall(r"\d+", table)}
swift = (R / "app/Madeira/BCDLossless.swift").read_text()
_req = swift.index("static let required: [Int] = [") + len("static let required: [Int] = [")
required = {int(n) for n in re.findall(r"\d+", swift[_req:swift.index("]", _req)])}
check("the engine's two layouts name the 48 shader ids 255 to 302, the same the app extracts", ids == set(range(255, 303)) and required == ids)
check("no shader is in the script: nothing that looks like compiled shader bytes (DXBC only as the file's magic test is absent here)",
      "DXBC" not in H and not re.search(r"(0x[0-9a-fA-F]{2},\s*){16}", (R / SCRIPT).read_text()))
whole = (R / SCRIPT).read_text() + swift + (R / "app/Madeira/BCDLosslessLive.swift").read_text() + (R / "madeira-d3d12/src/pe/madeira_d3d12.c").read_text()
check("the LS1 upscaler is not taken: none of its code, files or ids anywhere in the port",
      not re.search(r"Ls1Upscaler|d3d11_lsfg_upscale|lsfg_upscale\.hpp|upscale\.ctl|LS1_P\b|kLs1|ls1p\b|mad_up_before|DXMTLsUp", whole)
      and "NOT taken from Porthole, on purpose: the LS1 / LS1 performance upscaler" in (R / SCRIPT).read_text())
check("the shader file's place is BCD's folder in the prefix, the same in the engine and the app",
      'shaderFile = "C:\\\\ProgramData\\\\BCD\\\\lsfg.bin";' in H and 'static let windowsFolder = #"C:\\ProgramData\\BCD"#' in swift)

# ---- 3. off path
body = kit.body
check("LsfgBeforePresent returns at its first line unless the app set the control file (or MADEIRA_LSFG=1)",
      "    auto &ctl = lsfg::control();\n    if (!ctl.available || !lsfg::processAllowed() || desc_.Width < 640 || desc_.Height < 360)\n      return;\n" in swapchain
      and "available = envOn_ || !ctlPath.empty();" in H and 'ctlPath = getenvStr("MADEIRA_LSFG_CTL");' in H and 'envOn_ = env == "1";' in H)
check("a frame without generated pictures takes its present state where it always did; only one with them takes it earlier",
      "    std::optional<Presenter::PresentState> lsfg_state;\n    if (!lsfg_frames.empty()) {" in swapchain
      and swapchain.count("state = lsfg_state ? std::move(*lsfg_state) : presenter->synchronizeLayerProperties()") == 2
      and swapchain.count("presenter->synchronizeLayerProperties()") == 3
      and swapchain.index("lsfg_state.emplace(presenter->synchronizeLayerProperties());") < swapchain.index("    auto chunk = cmd_queue.CurrentChunk();\n    chunk->signal_frame_latency_fence_"))
check("the gamma ramp is set before the state is taken in both places, as before",
      swapchain.index("presenter->changeGammaRamp(static_cast<MTLDXGIOutput *>(target_.ptr())->GetGammaRamp());") < swapchain.index("lsfg_state.emplace(")
      and "      presenter->changeGammaRamp(output->GetGammaRamp());\n" in swapchain)
check("launchers and helper programs get no generated frames (decided once per process by the program's name)",
      '"launcher"' in H and '"steam.exe"' in H and '"dockhost"' in H and 'getenvStr("MADEIRA_LSFG_ANY_PROCESS") == "1"' in H)

# ---- 6. pacing in winemetal
PACE = "tools/patch-winemetal-lsfg-pacing.py"
check("the workflow runs the pacing script after present-fx", PACE in chain and chain.index(PACE) > chain.index("tools/patch-winemetal-present-fx.py"))
stage = kit.Stage(PACE)
for problem in stage.problems:
    print("  " + problem)
check("pacing: the chain before it applies on the copy (or the copy came patched)", not stage.problems)
check("pacing: the script applies and a second run changes nothing", stage.first.returncode == 0 and stage.mod.MARKER in stage.after and stage.idempotent)
check("pacing: winemetal_unix.c is changed by the script's hunks and nothing else", stage.exact())
src = stage.after
present = body(src, "_MTLCommandBuffer_presentDrawable(void *obj) {")
check("pacing: the present call reads the game's own mode through one function, which returns it unchanged unless frames are added",
      "int mode = madeira_lsfg_present_mode(g_madeira_vsync_mode, atomic_load_explicit(&g_lsfg_shown, memory_order_relaxed)," in present
      and "static _Atomic int g_lsfg_shown = 1;" in src and "  if (shown <= 1) return mode;" in src
      and src.count("&g_lsfg_shown") == 2 and "atomic_exchange_explicit(&g_lsfg_shown, n, memory_order_relaxed)" in body(src, "void madeira_lsfg_set_shown("))
cc = os.environ.get("CC") or shutil.which("cc") or shutil.which("clang") or shutil.which("gcc")
if cc:
    with tempfile.TemporaryDirectory() as tmp:
        t = Path(tmp)
        pure = src[src.index("/* lsfgp-pure-begin"):src.index("/* lsfgp-pure-end */")]
        (t / "p.c").write_text("#include <stdio.h>\n" + pure + r'''
static int fails;
static void expect(int c, const char *m) { printf("%s %s\n", c ? "ok  " : "FAIL", m); if (!c) fails++; }
int main(void) {
  int m, s, ok = 1;
  for (m = 0; m <= 4; m++) for (s = -1; s <= 1; s++) ok &= madeira_lsfg_present_mode(m, s, 120) == m && madeira_lsfg_present_mode(m, s, 0) == m;
  expect(ok, "no frames added (1 or less): every mode is the game's own");
  expect(madeira_lsfg_present_mode(3, 2, 120) == 1, "30 with one frame added runs as the 60 limit");
  expect(madeira_lsfg_present_mode(1, 2, 120) == 0, "60 with one added on a 120 Hz screen: the screen's top rate");
  expect(madeira_lsfg_present_mode(3, 4, 120) == 0, "30 with three added on a 120 Hz screen: the screen's top rate");
  expect(madeira_lsfg_present_mode(4, 2, 120) == 0 && madeira_lsfg_present_mode(3, 3, 120) == 0, "80 and 90 have no limit of their own: the screen's top rate");
  expect(madeira_lsfg_present_mode(3, 2, 60) == 0, "30 with one added on a 60 Hz screen: the screen's top rate (60)");
  expect(madeira_lsfg_present_mode(3, 2, 0) == 1 && madeira_lsfg_present_mode(1, 2, 0) == 0, "screen rate not known: 60 as a limit, anything above as the top rate");
  expect(madeira_lsfg_present_mode(0, 3, 120) == 0 && madeira_lsfg_present_mode(2, 3, 120) == 2, "the screen's top rate and no limit stay what they are");
  return fails ? 1 : 0;
}
''')
        b = subprocess.run([cc, "-std=gnu11", "-O1", "-Wall", "-o", str(t / "p"), str(t / "p.c")], capture_output=True, text=True)
        if check("pacing: the mapping compiles on the host", b.returncode == 0):
            r = subprocess.run([str(t / "p")], capture_output=True, text=True)
            print(r.stdout.rstrip())
            check("pacing: the mapping", r.returncode == 0)
ran, ok_c, err = stage.compile_ios()
if ran:
    if not check("pacing: the patched winemetal_unix.c compiles for iOS arm64", ok_c):
        print("\n".join(l for l in err.splitlines() if "error" in l)[:3000])
else:
    print("SKIP: no iOS SDK (xcrun) or no dxmt/include for the compile part")
stage.close()

# ---- 7. the app
APP = R / "app/Madeira"
live = (APP / "BCDLosslessLive.swift").read_text()
content = (APP / "ContentView.swift").read_text()
launch = live[live.index("    nonisolated static func launch() -> String? {"):live.index("    private func sessionStarted(")]
check("a session's start: without the game's line nothing is exported (the files an earlier session left are removed, the variables unset)",
      launch.index("BCDLosslessFiles.removeSessionFiles()") < launch.index('unsetenv("MADEIRA_LSFG_CTL")')
      < launch.index("guard let generated = BCDLossless.generated(MadeiraConfig.gameValue(BCDLossless.key) ?? MadeiraConfig.get(BCDLossless.key)) else { return nil }")
      < launch.index('        setenv("MADEIRA_LSFG_CTL"') and len(re.findall(r"(?<!un)setenv\(", launch)) == 3
      and "if let line = BCDLosslessLive.launch() { logStore.log(line) }" in content)
check("with the line: only when the purchase check is valid and the sealed file opens; the game then runs on d3d11-src.dll and MetalFX's generator is off",
      launch.index("guard BCDLosslessFiles.prepareForGame() else {") < launch.index('        setenv("MADEIRA_LSFG_CTL"')
      and 'setenv("MADEIRA_D3D11_SRC", "1", 1)' in launch and "_ = madeira_fgl_set(0)" in launch and "madeira_lsfg_set_shown(Int32(generated + 1))" in launch
      and "case .valid? = status(r)" in live and "BCDLosslessLicence.open(sealed, deviceSecret: deviceSecret(), account: account)" in live)
check("the file comes from Steam with the account's own licence and from nowhere else (no import, no picker, no other download)",
      "SteamOwnedLibrary.shared.licenceNow(appID: UInt32(BCDLosslessLicence.appID))" in live
      and "SteamOwnedLibrary.shared.downloadForApp(appID: UInt32(BCDLosslessLicence.appID), steamApps: work)" in live
      and live.count("sealed.write(to:") == 1 and not re.search(r"UIDocumentPicker|fileImporter|URLSession|importFile", live + swift)
      and live.index("guard await verify(), let account = BCDLosslessFiles.account else { return }") < live.index("downloadForApp("))
check("the shaders stay in the prefix only for the session; the DLL is never written in the clear",
      "BCDLosslessFiles.removeSessionFiles()" in live[live.index("    func sessionEnded() {"):] and "BCDLosslessLive.shared.sessionEnded()" in (APP / "BCDMenuMore.swift").read_text()
      and live.count("box.write(to: shaderFileURL") == 1
      # every file this code writes: the receipt, the sealed DLL, the shader container, the control file
      and sorted(re.findall(r"(\w+(?:\.text\(hz: hz\))?)\.write\(to: (\w+(?:\.\w+)?)", live))
          == [("box", "shaderFileURL"), ("control.text(hz: hz)", "controlFileURL"), ("data", "receiptURL"), ("sealed", "BCDLosslessFiles.sealedURL")])
steam = (APP / "SteamOwnedLibrary.swift").read_text()
check("Steam: the licence is read from the account's list, and the download is refused while a game runs or another download is active",
      "fetcher.forgetOwnedApps()\n        return try await fetcher.ownedAppIDs().contains(appID)" in steam
      and steam.count('guard !inSession else { throw SteamFileError.invalid("A game is running. Try again when it has ended.") }') == 2
      and "guard active == nil else { throw SteamFileError.invalid(" in steam)
menu = (APP / "BCDMenuMore.swift").read_text()
check("the in-game menu: the section comes from BCDLosslessMenu (nil when it is neither set up, asked for nor running); one generator per session",
      "return BCDLosslessMenu.section(live: lossless.live, ready: lossless.usable, wanted: wanted, control: lossless.control," in menu
      and "if BCDLosslessLive.shared.live { return nil }   // madeira-bcd: one frame generator per session" in menu
      and "guard live else { return }" in live[live.index("    func apply(_ new: BCDLossless.Control) {"):])
page = (APP / "BCDGamePageLive.swift").read_text()
check("a game's page has the choice and writes the game's own line; Settings has the setup",
      "Self.line(BCDLossless.key, BCDLossless.title, profile, BCDLossless.choices, note: BCDLossless.note)," in page
      and "        case BCDLossless.key:\n            // madeira-bcd: Lossless Scaling frame generation, a plain line in the game's own file like the choices above.\n"
          "            setLine(game, setting, value)" in page
      and 'BCDSettingsID.control(.graphics, "lossless-download")' in (APP / "BCDSettingsSections.swift").read_text())
catalog = (APP / "ConfigCatalog.generated.swift").read_text()
check("the catalog has the game's keys (off by default) and the app's two variables",
      re.search(r'key: "lossless-scaling", title: "[^"]*", kind: \.choice, defaultValue: ""', catalog) is not None
      and all(f'ConfigOption(key: "{k}"' in catalog for k in ("lossless-flow", "lossless-quality", "env.MADEIRA_LSFG_CTL", "env.MADEIRA_LSFG_SHADERS",
                                                                "env.MADEIRA_LSFG_ANY_PROCESS")))
hdr = (APP / "Madeira-Bridging-Header.h").read_text()
pbx = (R / "app/Madeira.xcodeproj/project.pbxproj").read_text()
check("the bridging header, the Xcode project and the preview's list know the new pieces",
      "void madeira_lsfg_set_shown(int frames_per_game_frame);" in hdr
      and all(pbx.count(f"/* {n} in Sources */") == 2 for n in ("BCDLossless.swift", "BCDLosslessLive.swift"))
      and '"BCDLosslessLive.swift"' in (R / "tools/ui-preview.py").read_text())
if not shutil.which("xcrun"):
    check("xcrun swiftc is here for the Swift part", "--require-swift" not in sys.argv)
else:
    with tempfile.TemporaryDirectory() as tmp:
        t = Path(tmp)
        (t / "stubs.swift").write_text("""import Foundation
enum BCDSessionTone: Equatable { case normal, soft, good, warm, hot }
struct BCDChoice: Identifiable, Equatable { var id: String; var title: String }
struct BCDMenuRow: Identifiable, Equatable {
    enum Kind: Equatable { case toggle(Bool); case choice([BCDChoice], selected: String); case note(BCDSessionTone) }
    var id: String; var title: String; var detail: String? = nil; var symbol: String? = nil; var kind: Kind
}
struct BCDMenuSection: Identifiable, Equatable { var id: String; var title: String? = nil; var rows: [BCDMenuRow] }
""")
        shutil.copy(R / "tests/host/bcd_lossless_checks.swift", t / "main.swift")
        r = subprocess.run(["xcrun", "swiftc", "-swift-version", "5", "-o", str(t / "run"), str(t / "main.swift"), str(t / "stubs.swift"),
                            str(APP / "BCDLossless.swift")], capture_output=True, text=True)
        if check("BCDLossless.swift and its harness compile on the host", r.returncode == 0):
            r = subprocess.run([str(t / "run")], capture_output=True, text=True)
            for line in r.stdout.splitlines():
                if line.startswith("FAIL"):
                    print(line)
            check(f"the Swift harness: {sum(1 for l in r.stdout.splitlines() if l.startswith('PASS'))} checks, none failing",
                  r.returncode == 0 and "ALL PASS" in r.stdout)
        else:
            print((r.stdout + r.stderr)[-2500:])

print("FAILED: Lossless Scaling frame generation" if failures else "PASS: Lossless Scaling frame generation")
sys.exit(1 if failures else 0)

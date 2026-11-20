#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""KosmicKrisp in BCD: the third word of a game's `vulkan` line (docs/VULKAN.md).

`vulkan = kosmickrisp` runs Wine's Vulkan on Mesa's KosmicKrisp (gl/libvulkan_kosmickrisp.dylib, Vulkan on Metal 4) instead of MoltenVK.
The route is Madeira's (pull request #462) and the build profile is the Playport authors' (Madeira's branch playport/kosmickrisp);
check-playport-mesa-profile.py is Madeira's own check of the two build routes. This file checks BCD's parts:

  - nothing changes without the word: the session function still has its one "off" path, MoltenVK's line is untouched, and a library
    this device cannot load (below iOS 26, or no Metal 4 GPU) or that the build does not bundle leaves Vulkan off with a log line;
    MoltenVK is never taken in its place;
  - the shader cache variables are set only for a KosmicKrisp session, never over an existing value or in a session with BCD's OpenGL on,
    and the session's end removes exactly what was set;
  - the game page offers the driver choice only where the library is bundled and the device can run it; everywhere else the switch of
    build 646 is what it was; the settings catalog lists the word; a downloaded list still may choose only moltenvk;
  - the build: the wrapper's pins (the Playport profile's Mesa commit is the one fetch-playport.py checks by SHA-256, the release tarball's
    SHA-256 is Madeira's), 19 + 6 patches, iOS 26.0 as the library's minimum, the OSMesa build's patch folder (its cache key) untouched,
    CI's three steps with a failure that only warns, the packaging check, the licence text's place and .gitignore.
With a built library at app/Madeira/gl/ (a build tree), the packaging rules are run on it too.
"""
import json
import re
import subprocess
import sys
from pathlib import Path

R = Path(__file__).resolve().parents[2]
failures = 0


def check(what, ok, detail=""):
    global failures
    print(("ok   " if ok else "FAIL ") + what + (("\n     " + str(detail)[:1200]) if detail and not ok else ""))
    if not ok:
        failures += 1


def body(text, start, end="\n}\n"):
    i = text.find(start)
    return "" if i < 0 else text[i:text.find(end, i) + len(end)]


bridge = (R / "app/Madeira/WineProcessBridge.m").read_text()
setup = body(bridge, "static void bcd_vulkan_session_setup(")
gate = body(bridge, "static const char *bcd_kosmickrisp_unusable(void)")
check("the device question: iOS 26 and a GPU of the Metal 4 family, else a reason for the log",
      "if (@available(iOS 26.0, *)) {" in gate and "[device supportsFamily:MTLGPUFamilyMetal4]" in gate
      and gate.count("return NULL;") == 1 and gate.count('return "') == 2 and "#import <Metal/Metal.h>" in bridge, gate)
i_word = setup.find('else if (!strcmp(want, "kosmickrisp")) lib = [bundlePath stringByAppendingPathComponent:@"gl/libvulkan_kosmickrisp.dylib"];')
i_gate = setup.find('if (lib && !strcmp(want, "kosmickrisp") && access(lib.fileSystemRepresentation, R_OK) == 0) {')
i_off = setup.find("    if (!lib) {\n        unsetenv(\"MADEIRA_VULKAN_LIBRARY\");\n        return;\n    }")
i_missing = setup.find("if (access(lib.fileSystemRepresentation, R_OK) != 0 ||")
i_on = setup.find('setenv("MADEIRA_VULKAN_LIBRARY", lib.UTF8String, 1);')
check("a bundled KosmicKrisp this device cannot load turns the session's Vulkan off before anything is exported, with the reason in the log",
      0 < i_word < i_gate < i_off < i_missing < i_on
      and "const char *why = bcd_kosmickrisp_unusable();" in setup[i_gate:i_off]
      and 'dprintf(STDERR_FILENO, "[WineProc] vulkan=kosmickrisp: Vulkan stays off, %s\\n", why);\n            lib = nil;' in setup, setup[:2500])
check("...and nothing else of the session function changed its shape: two off paths, MoltenVK's line as it was, no fallback to MoltenVK",
      setup.count('unsetenv("MADEIRA_VULKAN_LIBRARY");') == 2
      and 'if (!strcmp(want, "moltenvk")) lib = [bundlePath stringByAppendingPathComponent:@"gl/libMoltenVK.dylib"];' in setup
      and setup.count("libMoltenVK.dylib") == 1)
cache = setup[i_on:]
check("the shader cache: only for kosmickrisp, in Library/Caches/KosmicKrisp, never over a value that is there, never with BCD's OpenGL on",
      cache.find('if (!strcmp(want, "kosmickrisp")) {') < cache.find('setenv("MESA_SHADER_CACHE_DIR"')
      and '!getenv("MESA_SHADER_CACHE_DIR") && !getenv("MADEIRA_GL_BACKEND")' in cache
      and 'stringByAppendingPathComponent:@"KosmicKrisp"]' in cache and "NSCachesDirectory" in cache
      and 'if (!getenv("MESA_SHADER_CACHE_MAX_SIZE")) {\n                setenv("MESA_SHADER_CACHE_MAX_SIZE", "512M", 1);' in cache
      and len(re.findall(r'(?<!un)setenv\("MESA_SHADER_CACHE_DIR"', bridge)) == 1
      and len(re.findall(r'(?<!un)setenv\("MESA_SHADER_CACHE_MAX_SIZE"', bridge)) == 1, cache[:1800])
end = body(bridge, "static void wine_session_end(void)\n{")
check("...and the session's end removes exactly the variables this file set",
      'if (g_kk_cache_dir_set) { unsetenv("MESA_SHADER_CACHE_DIR"); g_kk_cache_dir_set = 0; }' in end
      and 'if (g_kk_cache_size_set) { unsetenv("MESA_SHADER_CACHE_MAX_SIZE"); g_kk_cache_size_set = 0; }' in end
      and bridge.count('unsetenv("MESA_SHADER_CACHE_DIR")') == 1 and bridge.count('unsetenv("MESA_SHADER_CACHE_MAX_SIZE")') == 1)
gl = (R / "build/win32u-unix/opengl_ios.c").read_text()
check("(the reason for that care: the OpenGL driver takes an existing MESA_SHADER_CACHE_DIR as it is)",
      "An existing MESA_SHADER_CACHE_DIR" in gl and 'getenv( "MESA_SHADER_CACHE_DIR" )' in gl)

page = (R / "app/Madeira/BCDGamePageLive.swift").read_text()
d3d = page[page.index("private func direct3D("):page.index("// MARK: Compatibility")]
usable = body(page, "    static let kosmicKrispUsable: Bool = {", "\n    }()\n")
check("the game page asks the same question once: the bundled file, iOS 26, a Metal 4 GPU",
      'appendingPathComponent("libvulkan_kosmickrisp.dylib")' in usable and "if #available(iOS 26.0, *) { return MTLCreateSystemDefaultDevice()?.supportsFamily(.metal4) ?? false }" in usable
      and usable.rstrip().endswith("return false\n    }()") and "import Metal\n" in page, usable)
check("...the driver choice is offered only there; everywhere else the Direct3D group keeps build 646's switch, in its place",
      'Self.kosmicKrispUsable ? Self.vulkanChoice(profile) :\n            Self.toggle("vulkan", "Vulkan (experimental)", profile.get("vulkan") == "moltenvk" || profile.get("vulkan") == "moltenvk-patched",' in d3d
      and d3d.index("Self.line(BCDOpenGL.key") < d3d.index("Self.kosmicKrispUsable ?") < d3d.index('Self.toggle("nvidia"'))
choice = body(page, "    private static func vulkanChoice(_ profile: GameProfile) -> BCDGameSetting {", "\n    }\n")
check("...the choice is Off, MoltenVK, KosmicKrisp (and, from build 669, MoltenVK (patched) where the build has it) on the game's own `vulkan` line, with an id of its own",
      'BCDGameChoice(value: "", label: "Off"), BCDGameChoice(value: "moltenvk", label: "MoltenVK")' in choice
      and 'BCDGameChoice(value: "kosmickrisp", label: "KosmicKrisp")' in choice and 'BCDGameSetting(id: "vulkan.driver"' in choice
      and '        case "vulkan.driver":' in page
      and 'setLine(game, "vulkan", value == "moltenvk" || value == "moltenvk-patched" || value == "kosmickrisp" ? value : "")' in page
      and 'setLine(game, "vulkan", on ? "moltenvk" : "")' in page, choice)

gen = (R / "build/tools/gen-config-catalog.py").read_text()
check("the settings catalog lists the word and what it needs",
      '("kosmickrisp", "Vulkan on Metal 4 (Mesa KosmicKrisp; iOS 26)")' in gen and "it needs iOS 26 " in gen
      and '("moltenvk", "Vulkan on Metal (MoltenVK)")' in gen)
policy = (R / "app/Madeira/GameRecommendationsList.swift").read_text()
# Round 668 (G2 S1; Enshrouded 7Q88YK, PNJPSK): a list may also choose kosmickrisp, for a game MoltenVK cannot run (no drawIndirectCount);
# its line carries minBuild 668, so an older build, whose policy refuses the word, never reads it.
check("a downloaded list may set vulkan to moltenvk, to kosmickrisp from build 668 (a game MoltenVK cannot run) and to moltenvk-patched from build 669",
      'if key == "vulkan" {' in policy and 'guard value == "moltenvk" || value == "moltenvk-patched" || value == "kosmickrisp" else {' in policy
      and all((c.get("minBuild", 0) if isinstance(c, dict) else 0) >= 668
              for r in json.loads((R / "recommended/recommendations.json").read_text())["rules"] for c in r["config"]
              if "vulkan = kosmickrisp" in (c["line"] if isinstance(c, dict) else c)))

mesa = R / "build/mesa-ios"
wrapper = (mesa / "build-kosmickrisp-bcd.sh").read_text()
fetch = (mesa / "fetch-playport.py").read_text()
recipe = (mesa / "build-kosmickrisp.sh").read_text()
playport = (mesa / "build-playport-kosmickrisp.sh").read_text()
pin = re.search(r'^PIN = "([0-9a-f]{40})"$', fetch, re.M)
check("the Playport profile's Mesa is one commit, fetched by SHA-256, and the wrapper and the recipe name the same commit",
      pin is not None and re.search(r'^SHA256 = "[0-9a-f]{64}"$', fetch, re.M) is not None
      and f"MESA_PLAYPORT={pin.group(1)}" in wrapper and f"mesa-{pin.group(1)}" in playport
      and "    return hashlib.sha256(path.read_bytes()).hexdigest()" in fetch
      and fetch.count('raise SystemExit("Mesa source archive hash mismatch")') == 2)
check("the release profile's tarball is Mesa 26.2.4 by the SHA-256 Madeira's recipe states",
      "MESA_RELEASE=26.2.4" in wrapper and "MESA_RELEASE_SHA256=bce5f7fbebb934373b86c999a064d52fb5065878dc57f287f95346648ec832e9" in wrapper
      and "bce5f7fbebb934373b86c999a064d52fb5065878dc57f287f95346648ec832e9" in recipe
      and '[ "$got" = "$MESA_RELEASE_SHA256" ] ||' in wrapper)
check("the host libraries of Mesa's kernel compiler are pinned commits, one translator per LLVM major version, and an unknown LLVM is refused",
      re.search(r"^SPIRV_TOOLS=[0-9a-f]{40}\b", wrapper, re.M) is not None and re.search(r"^SPIRV_HEADERS=[0-9a-f]{40}\b", wrapper, re.M) is not None
      and re.search(r"^  21\) TRANSLATOR=[0-9a-f]{40} ;;", wrapper, re.M) is not None and re.search(r"^  22\) TRANSLATOR=[0-9a-f]{40} ;;", wrapper, re.M) is not None
      and 'BCD pins a SPIRV-LLVM-Translator for LLVM 21 and 22 only" >&2; exit 1 ;;' in wrapper
      and '[ "$(git -C "$dest" rev-parse HEAD)" = "$commit" ] ||' in wrapper)
check("the wrapper's default profile is playport, any other word but release is refused, and the result is checked (arm64, iOS 26.0, the two entry points)",
      'PROFILE="${KK_PROFILE:-playport}"' in wrapper and "playport|release) ;;" in wrapper
      and '[ "$archs" = arm64 ] ||' in wrapper and '[ "$minos" = 26.0 ] ||' in wrapper
      and "for symbol in _vkGetInstanceProcAddr _vkGetDeviceProcAddr; do" in wrapper
      and 'cp "$SRC/docs/license.rst" "$R/app/Madeira/licenses/KosmicKrisp-Mesa-license.rst"' in wrapper)

# The fetch paths themselves (U6 counter-review H2), run here on local stand-ins: nothing is fetched from the network.
import os
import tempfile
fn = wrapper[wrapper.index("checkout() {"):]
fn = fn[:fn.index("\n}\n") + 3]
with tempfile.TemporaryDirectory(prefix="bcd-kk-fetch-") as tmp:
    t = Path(tmp)
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_CONFIG")}
    env.update(GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
    def git(*a, cwd=None):
        return subprocess.run(["git", *a], cwd=cwd, env=env, capture_output=True, text=True)
    up = t / "upstream"
    git("init", "-q", str(up))
    git("config", "uploadpack.allowAnySHA1InWant", "true", cwd=up)
    commits = []
    for n in ("one", "two"):
        (up / "f").write_text(n)
        git("add", "f", cwd=up)
        git("commit", "-q", "-m", n, cwd=up)
        commits.append(git("rev-parse", "HEAD", cwd=up).stdout.strip())
    def run_checkout(dest, commit):
        return subprocess.run(["bash", "-c", "set -eu\n" + fn + 'checkout "$1" "$2" "$3"', "x", str(dest), str(up), commit],
                              env=env, capture_output=True, text=True)
    fresh = run_checkout(t / "fresh", commits[1])
    empty = t / "empty"
    git("init", "-q", str(empty))                     # what a failed fetch left: an empty repository
    after_empty = run_checkout(empty, commits[1])
    other = t / "other"
    run_checkout(other, commits[0])                   # a folder at another commit
    after_other = run_checkout(other, commits[1])
    plain = t / "plain"
    plain.mkdir()
    (plain / "keep-me").write_text("data")
    refused = run_checkout(plain, commits[1])
    check("the wrapper's checkout: a fresh folder is fetched; one an earlier failed fetch left (empty, or at another commit) is fetched again; "
          "a folder that is not a checkout is refused and kept",
          fresh.returncode == 0 and git("rev-parse", "HEAD", cwd=t / "fresh").stdout.strip() == commits[1]
          and after_empty.returncode == 0 and git("rev-parse", "HEAD", cwd=empty).stdout.strip() == commits[1]
          and after_other.returncode == 0 and git("rev-parse", "HEAD", cwd=other).stdout.strip() == commits[1]
          and (other / "f").read_text() == "two"
          and refused.returncode != 0 and (plain / "keep-me").read_text() == "data",
          [fresh.stderr, after_empty.stderr, after_other.stderr, refused.stderr])
    # fetch-playport.py with the download replaced: a download that is not the pinned archive leaves neither the archive's name nor
    # its .part, so the next run fetches again instead of failing on a file an earlier run left.
    work = t / "playport"
    stub = ("import runpy, sys, urllib.request\n"
            "urllib.request.urlretrieve = lambda url, path: open(path, 'wb').write(b'not the pinned archive')\n"
            "sys.argv = [sys.argv[1], sys.argv[2]]\n"
            "runpy.run_path(sys.argv[0], run_name='__main__')\n")
    bad = [subprocess.run([sys.executable, "-I", "-c", stub, str(mesa / "fetch-playport.py"), str(work)], capture_output=True, text=True)
           for _ in range(2)]
    check("fetch-playport.py: a wrong download is refused and leaves no archive and no .part, the second time as the first",
          all(r.returncode != 0 and "hash mismatch" in r.stderr for r in bad) and sorted(x.name for x in work.iterdir()) == [],
          [r.stderr[-300:] for r in bad] + [sorted(x.name for x in work.iterdir())])
check("the wrapper uses at most 4 jobs by default (an 8-core build machine runs it inside the whole build) and gives Mesa's ninja the same",
      'JOBS="${JOBS:-$(n=$(sysctl -n hw.ncpu 2>/dev/null || echo 4); [ "$n" -lt 4 ] && echo "$n" || echo 4)}"\nexport JOBS' in wrapper
      and recipe.count('ninja ${JOBS:+-j "$JOBS"} -C') == 2)

patches = sorted(p.name for p in (mesa / "playport-patches").glob("*.patch"))
release = sorted(p.name for p in (mesa / "kosmickrisp-patches").glob("*.patch"))
check("31 Playport patches (0001 to 0019 from 412de5f8; 0020 to 0031, round 669, from 06e83ca5: each by the Playport authors), BCD's 0032 "
      "(round 669, X-Plane 12's opaque fds, tests/host/check-kk-opaque-fd.py), 0033 (build 673, X-Plane 12's SNORM blending, "
      "tests/host/check-kk-snorm-blend.py), 0034 (build 674, the refused MSL logged, tests/host/check-kk-msl-fail.py) and 0035 last "
      "(build 675, stores to multisampled storage images left out with a key, tests/host/check-kk-ms-store-drop.py) "
      "and the six release patches",
      [p[:4] for p in patches] == [f"{n:04d}" for n in range(1, 36)]
      and all("From: The Playport authors <dev@playport.dev>" in (mesa / "playport-patches" / p).read_text().split("\n---\n")[0] for p in patches[:31])
      and patches[31] == "0032-kk-in-process-opaque-fd-memory-and-semaphores.patch"
      and patches[32] == "0033-kk-snorm-color-attachments-report-blending.patch"
      and patches[33] == "0034-kk-log-the-msl-source-metal-refused.patch"
      and patches[34] == "0035-kk-drop-stores-to-multisampled-storage-images.patch"
      and all((mesa / "playport-patches" / q).read_text().startswith("From: BCD <190844990+thebcd69@users.noreply.github.com>") for q in patches[31:35])
      and len(release) == 6 and all(p.startswith("kosmickrisp-ios-000") for p in release), (patches, release))
check("...the recipe reads the six from their own folder, so the OSMesa build's patch folder (its cache key) holds only its numbered patches",
      'set -- "$HERE"/kosmickrisp-patches/kosmickrisp-ios-*.patch' in recipe
      and all(re.match(r"\d{4}-", p.name) for p in (mesa / "patches").glob("*.patch"))
      and 'for p in "$HERE"/patches/[0-9]*.patch; do' in (mesa / "build.sh").read_text())
cross = (mesa / "kosmickrisp-ios-arm64.cross").read_text()
check("the library is compiled for iOS 26.0 and later (Metal 4)", cross.count("-miphoneos-version-min=26.0") == 6 and "version-min=1" not in cross)

ci = (R / ".github/workflows/build-ipa.yml").read_text()
step = ci[ci.index("      - name: Restore KosmicKrisp (Mesa's Vulkan on Metal 4)"):ci.index("      # A game's own *.log lines (errors, GPU / driver / display lines) in the")]
key = "kosmickrisp-v1-${{ hashFiles('build/mesa-ios/build-kosmickrisp-bcd.sh', 'build/mesa-ios/build-kosmickrisp.sh', 'build/mesa-ios/build-playport-kosmickrisp.sh', 'build/mesa-ios/fetch-playport.py', 'build/mesa-ios/kk-stale-source.sh', 'build/mesa-ios/kosmickrisp-ios-arm64.cross', 'build/mesa-ios/kosmickrisp-patches/*.patch', 'build/mesa-ios/playport-patches/*.patch') }}"
check("CI: restore, build, save on one key of the scripts and the patches; three named files, no globs (the local runner tests each path)",
      step.count(key) == 2 and step.count("app/Madeira/gl/libvulkan_kosmickrisp.dylib\n") == 2 and "*" not in "".join(l for l in step.splitlines() if l.strip().startswith("app/Madeira/")), step[:1500])
check("...a failed build only warns, saves nothing and leaves no older library in the package",
      "        id: kkbuild\n        if: steps.kkcache.outputs.cache-hit != 'true'\n        continue-on-error: true\n        run: bash build/mesa-ios/build-kosmickrisp-bcd.sh\n" in step
      and "if: steps.kkcache.outputs.cache-hit != 'true' && steps.kkbuild.outcome == 'success'\n        uses: actions/cache/save@v4" in step
      and "if: steps.kkcache.outputs.cache-hit != 'true' && steps.kkbuild.outcome != 'success'\n        run: |\n          rm -f app/Madeira/gl/libvulkan_kosmickrisp.dylib" in step)
check("...the OpenGL libraries' cache key is what it was, and the build routes and this file run in CI",
      "key: opengl-libs-v1-${{ hashFiles('build/mesa-ios/build.sh', 'build/mesa-ios/ios-arm64.cross', 'build/mesa-ios/patches/*.patch', 'build/moltenvk-ios/build.sh') }}" in ci
      and "run: python3 tests/host/check-playport-mesa-profile.py" in ci and "run: python3 tests/host/check-kosmickrisp.py" in ci)
shipped = (R / "tools/check-vulkan-shipped.py").read_text()
check("the packaging check takes a bundle without the library, and refuses one whose library is not whole",
      'kk_note = "KosmicKrisp is not in this package (vulkan = kosmickrisp stays off)"' in shipped and 'if minos != "26.0":' in shipped
      and "KosmicKrisp-Mesa-license.rst is missing" in shipped and '"_vkGetInstanceProcAddr", "_vkGetDeviceProcAddr"' in shipped)
mit = (R / "app/Madeira/legal/LICENSES/MIT-Mesa-KosmicKrisp.txt").read_text()
check("the MIT text and KosmicKrisp's copyright holders are in LICENSES/ and the bundle's copy, and the short notice points at them",
      (R / "LICENSES/MIT-Mesa-KosmicKrisp.txt").read_text() == mit and "LunarG, Inc. and Google LLC" in mit and "Permission is hereby granted, free of charge" in mit and "MIT-Mesa-KosmicKrisp.txt" in shipped
      and "legal/LICENSES/MIT-Mesa-KosmicKrisp.txt" in (R / "app/Madeira/licenses/THIRD-PARTY-NOTICES.txt").read_text())
about = (R / "app/Madeira/BCDSettingsLive.swift").read_text()
check("About names the two licence texts", '"MIT-Mesa-KosmicKrisp.txt": "Mesa KosmicKrisp · MIT license"' in about
      and '"KosmicKrisp-Mesa-license.rst": "Mesa KosmicKrisp · the licenses of its parts"' in about)
ignore = (R / ".gitignore").read_text()
check("the built library and its licence text are never committed",
      "app/Madeira/gl/*.dylib" in ignore and "app/Madeira/gl/*.version" in ignore and "app/Madeira/licenses/KosmicKrisp-Mesa-license.rst" in ignore
      and subprocess.run(["git", "-C", str(R), "ls-files", "app/Madeira/gl/libvulkan_kosmickrisp.dylib"], capture_output=True, text=True).stdout.strip() == "")

lib = R / "app/Madeira/gl/libvulkan_kosmickrisp.dylib"
if lib.is_file():
    kk = subprocess.run([sys.executable, str(R / "tools/check-vulkan-shipped.py")], capture_output=True, text=True).stdout
    check("the library in this tree passes the packaging rules (arm64, iOS 26.0, entry points, version file, licence text)",
          "libvulkan_kosmickrisp" not in "\n".join(l for l in kk.splitlines() if l.startswith("FAIL")), kk)
else:
    print("skip the built library is not in this tree (CI's build step makes it)")

print("FAIL" if failures else "PASS")
sys.exit(1 if failures else 0)

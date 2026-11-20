#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""The patched MoltenVK as a second, opt-in Vulkan library (round 669; bcd-games pull request #57 by RageMC; docs/VULKAN.md "Yamalı MoltenVK").

`vulkan = moltenvk-patched` runs Wine's Vulkan on gl/libMoltenVK_patched.dylib (MoltenVK v1.4.2 plus later MoltenVK work, nivogli's Enshrouded changes and RageMC's
fixes, SPIRV-Cross plus one patch). Everything else stays what it was: `vulkan = moltenvk`, `vulkan = kosmickrisp` and Zink load the plain, unmodified
libMoltenVK.dylib, and no session loads two MoltenVKs. This file checks:

  1. the sources: the build script (own folders, pins, stamps, a library written under its own name and install name, nothing of the plain build touched), the two
     patches byte for byte as received, and that they fit the pins (the SPIRV-Cross revision the patch moves to is the one the script builds);
  2. CI: restore / build / save / drop-stale steps with a cache key of their own, a failure that only warns, and the plain library's key unchanged;
  3. the app: the word in the session setup, the missing-library case (Vulkan off, a log line, never the other MoltenVK), Zink taking the same library in such a
     session, the list's policy and minBuild, the settings catalog, the game page's choice, the auto-tune guards, the packaging check;
  4. the credits: the notices (both copies and the bundled text), the headers, the README line, the document;
  5. run on this Mac (macOS with clang / Xcode): the session code against temporary folders, Zink's choice of library, the packaging check against made-up packages,
     and the build script itself in a scratch tree against the local mirrors (git and patch logic, stamps, recovery; `make` is a stand-in, xcodebuild must not run).
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

R = Path(__file__).resolve().parents[2]
ok = True


def check(what, cond, detail=""):
    global ok
    print(("ok   " if cond else "FAIL ") + what)
    if not cond:
        ok = False
        if detail:
            print("     " + str(detail)[:2500])
    sys.stdout.flush()


def function(source, start):
    a = source.index(start)
    b = source.index("{", a)
    depth, c = 1, b + 1
    while depth:
        depth += (source[c] == "{") - (source[c] == "}")
        c += 1
    return source[a:c]


MVK_COMMIT = "db66022459ffb663aa2b50f6b018bc2e124f5edf"
SPC_OLD = "6c09849fe88c48eaed08413aa022aaa136a3a057"
SPC_NEW = "aa217aeb6c9f0ace7a0ab233b28807edf45eb165"
PATCH_MVK = R / "build/moltenvk-ios/patches/0001-moltenvk-enshrouded.patch"
PATCH_SPC = R / "build/moltenvk-ios/patches/spirv-cross-0001-instance-relative-patch-id.patch"

# ===================================================================================================================== 1. the sources
script = (R / "build/moltenvk-ios/build-patched.sh").read_text()
plain = (R / "build/moltenvk-ios/build.sh").read_text()
check("the script: the credits of both sources (Connor Gow's build.sh, RageMC's pull request) and the licence line, in its header",
      script.startswith("#!/bin/bash\n# Portions copyright 2026 Connor Gow: code taken from https://github.com/willfaust/Madeira (pull request #462)\n")
      and "# Portions copyright 2026 RageMC: code taken from https://github.com/thebcd69/bcd-games (pull request #57 by RageMC)\n" in script
      and "# GPL-3.0-or-later WITH the Madeira Converter Exception, version 1.\n" in script and "See THIRD-PARTY-NOTICES.md." in script)
check("the script: MoltenVK v1.4.2 by its commit (the plain script's), in a folder of its own, never the plain build's checkout",
      f"MVK_COMMIT={MVK_COMMIT}" in script and f"MVK_COMMIT={MVK_COMMIT}" in plain
      and 'SRC="${MOLTENVK_PATCHED_SRC:-$R/toolchains/MoltenVK-patched}"' in script
      and 'SPC="${MOLTENVK_PATCHED_SPIRV_CROSS_SRC:-$R/toolchains/MoltenVK-patched-SPIRV-Cross}"' in script
      and 'toolchains/MoltenVK"' not in script and "toolchains/MoltenVK}" not in script
      and 'git clone -q -b v1.4.2 https://github.com/KhronosGroup/MoltenVK "$SRC"' in script
      and 'git clone -q https://github.com/KhronosGroup/SPIRV-Cross "$SPC"' in script)
check("the script: the library under its own name and install name, written to a temporary file first and removed again if anything fails; the plain name never",
      'LIB=libMoltenVK_patched.dylib' in script and "trap 'rm -f \"$OUT/$LIB.tmp\"' EXIT" in script
      and 'cp "$BIN" "$OUT/$LIB.tmp"' in script and 'install_name_tool -id "@rpath/$LIB" "$OUT/$LIB.tmp"' in script
      and 'mv "$OUT/$LIB.tmp" "$OUT/$LIB"' in script and "libMoltenVK_patched.version" in script
      and "libMoltenVK.dylib" not in re.sub(r"(?m)^#.*$", "", script) and "licenses" not in re.sub(r"(?m)^#.*$", "", script))
check("the script: the patch goes on the working tree and the SPIRV-Cross patch is committed (git am, BCD noreply), each guarded by a stamp; dependencies are fetched "
      "once per set of pins, through --spirv-cross-root, and the pin the patch moves to is checked",
      'git apply "$MVK_PATCH"' in script and "git reset -q --hard \"$MVK_COMMIT\"" in script and "git clean -fdq" in script
      and "bcd-patched-tree" in script and "bcd-patch-sha" in script and '-c user.name=BCD -c user.email=noreply@users.noreply.github.com am -q "$SPC_PATCH"' in script
      and './fetchDependencies --ios --spirv-cross-root "$SPC"' in script and "External/.bcd-patched-deps" in script
      and f"SPC_COMMIT={SPC_NEW}" in script and "LC_ALL=C sort" in script)
check("the script: MOLTENVK_JOBS as in the plain build, and `make ios`", 'if [ -n "${MOLTENVK_JOBS:-}" ]; then' in script and "\nmake ios\n" in script)
check("the plain build.sh is as it was: no patch, no second library, its output name",
      "patch" not in plain.lower() and 'cp "$BIN" "$OUT/libMoltenVK.dylib"' in plain
      and 'install_name_tool -id @rpath/libMoltenVK.dylib "$OUT/libMoltenVK.dylib"' in plain and "$R/toolchains/MoltenVK}" in plain)

sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
check("the two patches are the files of pull request #57, byte for byte (a changed patch needs this line, the notices and a new device test)",
      sha(PATCH_MVK) == "35ed1a218cbb55e866c0ec3f3fa4308f919e82f4b3fffd18d71f6729f5008462"
      and sha(PATCH_SPC) == "80aed5e669d856d674735eeb2204a0b18c79a1d059e60f3fb1c0f650cb272172", (sha(PATCH_MVK), sha(PATCH_SPC)))
pm = PATCH_MVK.read_text(errors="replace")
touched = re.findall(r"^diff --git a/(\S+) b/", pm, re.M)
check("the MoltenVK patch changes MoltenVK's own files only, and moves SPIRV-Cross from v1.4.2's pin to the one the script builds",
      len(touched) == 53 and not any(t.startswith(("build/", "app/", "tools/", "toolchains/", "External/")) for t in touched)
      and f"-{SPC_OLD}\n+{SPC_NEW}\n" in pm and "ExternalRevisions/SPIRV-Cross_repo_revision" in touched
      and "ExternalRevisions/SPIRV-Tools_repo_revision" not in touched and "ExternalRevisions/cereal_repo_revision" not in touched, (len(touched), touched[:5]))
ps = PATCH_SPC.read_text()
check("the SPIRV-Cross patch is a mailbox patch of spirv_msl.cpp / spirv_msl.hpp adding instance_relative_patch_id",
      ps.startswith("From 0000000000000000000000000000000000000000 ") and "Subject: [PATCH] local: instance_relative_patch_id" in ps
      and re.findall(r"^diff --git a/(\S+) b/", ps, re.M) == ["spirv_msl.cpp", "spirv_msl.hpp"] and "bool instance_relative_patch_id = false;" in ps)

# ===================================================================================================================== 2. CI
ci = (R / ".github/workflows/build-ipa.yml").read_text()
key_plain = "key: opengl-libs-v1-${{ hashFiles('build/mesa-ios/build.sh', 'build/mesa-ios/ios-arm64.cross', 'build/mesa-ios/patches/*.patch', 'build/moltenvk-ios/build.sh') }}\n"
check("CI: the plain OpenGL library set keeps its cache key (restore and save), so that the patched build never rebuilds Mesa or MoltenVK",
      ci.count(key_plain) == 2 and "run: bash build/moltenvk-ios/build.sh\n" in ci)
key_p = "key: moltenvk-patched-v1-${{ hashFiles('build/moltenvk-ios/build-patched.sh', 'build/moltenvk-ios/patches/*.patch') }}\n"
paths = "          path: |\n            app/Madeira/gl/libMoltenVK_patched.dylib\n            app/Madeira/gl/libMoltenVK_patched.version\n"
steps = {
    "restore": "      - name: Restore patched MoltenVK (second Vulkan library)\n        id: mvkpcache\n        uses: actions/cache/restore@v4\n        with:\n" + paths,
    "build": "      - name: Build patched MoltenVK for iOS\n        id: mvkpbuild\n        if: steps.mvkpcache.outputs.cache-hit != 'true'\n"
             "        continue-on-error: true\n        run: bash build/moltenvk-ios/build-patched.sh\n",
    "save": "      - name: Save patched MoltenVK\n        if: steps.mvkpcache.outputs.cache-hit != 'true' && steps.mvkpbuild.outcome == 'success'\n"
            "        uses: actions/cache/save@v4\n        with:\n" + paths,
    "drop": "      - name: Drop a stale patched MoltenVK after a failed build\n        if: steps.mvkpcache.outputs.cache-hit != 'true' && steps.mvkpbuild.outcome != 'success'\n"
            "        run: |\n          rm -f app/Madeira/gl/libMoltenVK_patched.dylib app/Madeira/gl/libMoltenVK_patched.dylib.tmp app/Madeira/gl/libMoltenVK_patched.version\n",
}
pos = {k: ci.find(v) for k, v in steps.items()}
check("CI: restore, build (cache miss only, a failure only warns), save (only a built library) and drop-stale steps, in this order, with a cache key of their own",
      all(p > 0 for p in pos.values()) and pos["restore"] < pos["build"] < pos["save"] < pos["drop"] and ci.count(key_p) == 2, pos)
kk = ci.find("      - name: Put the kk-retry shim in front of KosmicKrisp\n")
check("CI: after the KosmicKrisp and kk-retry steps, so that no cached set of the OpenGL or KosmicKrisp steps depends on the patched build",
      0 < ci.find("      - name: Save KosmicKrisp\n") < kk < pos["restore"] and ci.index("      - name: Save OpenGL libraries\n") < kk)
check("CI: runs this check next to the kk-retry check, and the packaging check is still run",
      "      - name: Check the patched MoltenVK\n        run: python3 tests/host/check-moltenvk-patched.py\n" in ci
      and ci.index("python3 tests/host/check-kk-retry.py") < ci.index("python3 tests/host/check-moltenvk-patched.py")
      and "run: python3 tools/check-vulkan-shipped.py" in ci)
try:
    import yaml
    wf = yaml.safe_load(ci)
    names = {s.get("name"): s for s in wf["jobs"]["build"]["steps"]}
    b = names["Build patched MoltenVK for iOS"]
    check("CI (parsed): the build step continues on error; save needs a successful build; nothing else of the job depends on the step's outcome",
          b.get("continue-on-error") is True and "mvkpbuild.outcome == 'success'" in names["Save patched MoltenVK"]["if"]
          and ci.count("mvkpbuild") == 3)
except ImportError:
    print("skip the parsed workflow check (PyYAML is not here)")

# ===================================================================================================================== 3. the app
bridge = (R / "app/Madeira/WineProcessBridge.m").read_text()
setup = function(bridge, "static void bcd_vulkan_session_setup(")
check("the session setup: the word loads gl/libMoltenVK_patched.dylib, the plain word the plain one, KosmicKrisp its own; any other word is refused with the three names in the log",
      'if (!strcmp(want, "moltenvk")) lib = [bundlePath stringByAppendingPathComponent:@"gl/libMoltenVK.dylib"];' in setup
      and 'else if (!strcmp(want, "moltenvk-patched")) lib = [bundlePath stringByAppendingPathComponent:@"gl/libMoltenVK_patched.dylib"];' in setup
      and 'else if (!strcmp(want, "kosmickrisp")) lib = [bundlePath stringByAppendingPathComponent:@"gl/libvulkan_kosmickrisp.dylib"];' in setup
      and "is not moltenvk, moltenvk-patched or kosmickrisp: Vulkan stays off" in setup)
i_gate = setup.find('if (lib && !strcmp(want, "moltenvk-patched") && access(lib.fileSystemRepresentation, R_OK) != 0) {')
i_off = setup.find("    if (!lib) {\n        unsetenv(\"MADEIRA_VULKAN_LIBRARY\");\n        return;\n    }")
i_kk = setup.find('if (lib && !strcmp(want, "kosmickrisp") && access(lib.fileSystemRepresentation, R_OK) == 0) {')
check("the session setup: a build without the patched library turns Vulkan off before anything is exported, says so, and never takes the other MoltenVK instead",
      0 < i_kk < i_gate < i_off and "this build does not have the patched MoltenVK" in setup[i_gate:i_off] and "lib = nil;" in setup[i_gate:i_off]
      and "libMoltenVK.dylib" not in setup[i_gate:i_off] and setup.count('unsetenv("MADEIRA_VULKAN_LIBRARY");') == 2)
gl = (R / "build/win32u-unix/opengl_ios.c").read_text()
zink = function(gl, "static BOOL zink_init(void)")
block = function(zink, "    {\n        const char *zink_vk = getenv( \"MADEIRA_ZINK_VULKAN\" ), *session_vk = getenv( \"MADEIRA_VULKAN_LIBRARY\" );")
check("Zink: loads libMoltenVK_patched.dylib only in a session whose exported Vulkan library is that one (after the KosmicKrisp branch of MADEIRA_ZINK_VULKAN, before the "
      "plain one); every other session keeps the plain line and its log lines as they were",
      'static const char patched[] = "/libMoltenVK_patched.dylib";' in block
      and block.index('strstr( session_vk, "/libvulkan_kosmickrisp.dylib" )') < block.index("session_len >= sizeof(patched) - 1")
      < block.index('snprintf( path, sizeof(path), "%s/libMoltenVK.dylib", dir );')
      and 'snprintf( path, sizeof(path), "%s/libMoltenVK_patched.dylib", dir );' in block
      and 'ERR( "[winios-gl] zink Vulkan library: libMoltenVK.dylib\\n" );' in block and zink.count("ZINK_VULKAN_LIBRARY") == 1
      and zink.index("static const char patched[]") < zink.index('setenv( "ZINK_VULKAN_LIBRARY", path, 1 );'))
vk = (R / "build/win32u-unix/vulkan_ios.c").read_text()
check("win32u's Vulkan layer loads whatever MADEIRA_VULKAN_LIBRARY names, by full path (so Zink and winevulkan share one image), and the 32-bit path asks only whether it is set",
      'const char *path = getenv( "MADEIRA_VULKAN_LIBRARY" );' in vk and "ios_vulkan_open( path, flags )" in vk
      and re.sub(r"\s+", " ", function((R / "build/ntdll-unix/virtual_ios.c").read_text(), "static int ios_vk_session_on( void )")).endswith(
          '{ const char *e = getenv( "MADEIRA_VULKAN_LIBRARY" ); return e && *e; }'))

policy = (R / "app/Madeira/GameRecommendationsList.swift").read_text()
check("the list policy: moltenvk-patched is allowed beside moltenvk and kosmickrisp, nothing else",
      'guard value == "moltenvk" || value == "moltenvk-patched" || value == "kosmickrisp" else {' in policy
      and 'is moltenvk, moltenvk-patched or kosmickrisp' in policy)
rules = json.loads((R / "recommended/recommendations.json").read_text())["rules"]
lines = [(r["id"], c) for r in rules for c in r["config"] if "vulkan = moltenvk-patched" in (c["line"] if isinstance(c, dict) else c)]
check("the lists: a row that chooses moltenvk-patched has the line for build 669 on (an older build, whose policy refuses the word, never reads it); the plain word's "
      "line and the enshrouded row's kosmickrisp line are not made later than they were",
      all(isinstance(c, dict) and c.get("minBuild", 0) >= 669 for _, c in lines)
      and all((c.get("minBuild") if isinstance(c, dict) else None) == 668 for r in rules if r["id"] == "enshrouded" for c in r["config"]
              if "vulkan = kosmickrisp" in (c["line"] if isinstance(c, dict) else c)), lines)
gen = (R / "build/tools/gen-config-catalog.py").read_text()
catalog = (R / "app/Madeira/ConfigCatalog.generated.swift").read_text()
check("the settings catalog: the choice and its note, in the generator and in the generated file; the library variable's note names the second library",
      '("moltenvk-patched", "Vulkan on Metal (patched MoltenVK; build 669)")' in gen and '("moltenvk-patched", "Vulkan on Metal (patched MoltenVK; build 669)")' in catalog
      and "moltenvk-patched (from build 669)" in gen and "moltenvk-patched (from build 669)" in catalog
      and 'moltenvk-patched: its "\n                        "libMoltenVK_patched.dylib' in gen and "moltenvk-patched: its libMoltenVK_patched.dylib" in catalog)
page = (R / "app/Madeira/BCDGamePageLive.swift").read_text()
choice = function(page, "    private static func vulkanChoice(_ profile: GameProfile) -> BCDGameSetting {")
check("the game page: the choice offers MoltenVK (patched) only where this build has the library, keeps a typed value listed, and writes the word; the old switch shows it as on",
      'BCDGameChoice(value: "moltenvk-patched", label: "MoltenVK (patched)")' in choice and "if patchedMoltenVKBundled {" in choice
      and 'appendingPathComponent("libMoltenVK_patched.dylib")' in function(page, "    static let patchedMoltenVKBundled: Bool = {")
      and 'setLine(game, "vulkan", value == "moltenvk" || value == "moltenvk-patched" || value == "kosmickrisp" ? value : "")' in page
      and 'profile.get("vulkan") == "moltenvk" || profile.get("vulkan") == "moltenvk-patched",' in page)
at = (R / "build/tools/autotune_rules.py").read_text()
guard = 's("vulkan=moltenvk: Vulkan is on for this session"), s("vulkan=moltenvk-patched: Vulkan is on for this session")'
rule_ids = ["vulkan-required-box", "godot-vulkan-display-driver", "vulkan-32bit-no-loader-box", "needs-opengl-or-vulkan"]   # round 669 (C NMS-1): the fourth
bodies = {}
for rid in rule_ids:
    i = at.index(f'"id": "{rid}"')
    bodies[rid] = at[i:at.index('"reason"', i)]
check("auto-tune: the four rules that propose `vulkan = moltenvk` also treat a moltenvk-patched session as one that already had Vulkan on (and so does the generated file)",
      all(guard in b for b in bodies.values()) and (R / "app/Madeira/AutoTuneRules.generated.swift").read_text().count("vulkan=moltenvk-patched: Vulkan is on for this session") == 4,
      {k: v[-200:] for k, v in bodies.items()})
shipped = (R / "tools/check-vulkan-shipped.py").read_text()
check("the packaging check: the patched library is optional, and whole when there (name, arm64, install name, entry points, not the plain library's bytes, its version file, no .tmp)",
      "libMoltenVK_patched.dylib" in shipped and "@rpath/libMoltenVK_patched.dylib" in shipped and '"_vkGetInstanceProcAddr", "_vkGetDeviceProcAddr"' in shipped
      and "is the plain libMoltenVK.dylib under another name" in shipped and "libMoltenVK_patched.dylib.tmp" in shipped
      and "the patched MoltenVK is not in this package (vulkan = moltenvk-patched stays off)" in shipped
      and 'if not (APP / "gl" / "libMoltenVK.dylib").is_file():' in shipped)
ignore = (R / ".gitignore").read_text()
check(".gitignore: the library, its version file and the temporary file are ignored", "app/Madeira/gl/*.dylib\n" in ignore and "app/Madeira/gl/*.version\n" in ignore
      and "app/Madeira/gl/*.dylib.tmp\n" in ignore)

# ===================================================================================================================== 4. the credits
notices = (R / "THIRD-PARTY-NOTICES.md").read_text()
check("THIRD-PARTY-NOTICES.md and its bundled copy are the same file", notices == (R / "app/Madeira/legal/THIRD-PARTY-NOTICES.md").read_text())
need = ["**MoltenVK, patched**", "bcd-games pull request #57 by **RageMC**", "github.com/nivogli/MoltenVK", "Apache-2.0 section 4(b)", "### The patched MoltenVK, file by file",
        "#57 by RageMC)", "| #57, 2026-11-16 | RageMC |", "#2860 jamesdolan", "#2873 MaxLikesCode", "#2887 imellag", "#2889 prosperkartik", "Isabella Basso", "Anshul Noori", "utzcoz",
        "dttdrv", "osy", "hopesuffers", "hstdt", "Courtney Andrew Richardson", "squidbus", "David Grinberg", "Alexander Devaikin", "Denis Korobov", "Axel Gneiting",
        "Francisco Ramirez de Anton", "M4rc68", "Bill Hollings", "KhronosWebservices", "`build/moltenvk-ios/build-patched.sh`", "instance_relative_patch_id"]
check("the notices name the library, the pull request, the fork, the licence, the open pull requests and every author of the commits found in the patch",
      all(n in notices for n in need), [n for n in need if n not in notices])
table = re.findall(r"^\| `(MoltenVK[^`]*|CONTRIBUTING\.md|CMakeLists\.txt|Docs/[^`]*|ExternalRevisions/[^`]*)` \| (?:portions|whole file) \| (\d+) \|", notices, re.M)
check("...and give every file of the MoltenVK patch a row (53 files, 1631 lines)",
      len(table) == 53 and sum(int(n) for _, n in table) == 1631 and {f for f, _ in table} == set(touched), (len(table), sum(int(n) for _, n in table)))
bundled = (R / "app/Madeira/licenses/THIRD-PARTY-NOTICES.txt").read_text()
check("the bundled plain-text notice names it too, with the licence, the pull request and the people",
      "MoltenVK, patched (bcd-games pull request #57 by RageMC)" in bundled and "Apache-2.0 like MoltenVK" in bundled and "nivogli (github.com/nivogli/MoltenVK)" in bundled
      and "gl/libMoltenVK_patched.dylib" in bundled and "Isabella Basso" in bundled)
check("the README names the contributor, the settings page lists no new licence text (the Apache texts of MoltenVK and SPIRV-Cross are the ones it already lists)",
      "[RageMC](https://github.com/RageMC)" in (R / "README-BCD.md").read_text()
      and '"MoltenVK-Apache-2.0.txt": "MoltenVK' in (R / "app/Madeira/BCDSettingsLive.swift").read_text())
doc = (R / "docs/VULKAN.md").read_text()
check("docs/VULKAN.md has the section, and the test is named in its list", "## Yamalı MoltenVK (build 669): `vulkan = moltenvk-patched`" in doc and "check-moltenvk-patched.py" in doc)
check("app/Madeira/gl/README.md names the script", "build/moltenvk-ios/build-patched.sh" in (R / "app/Madeira/gl/README.md").read_text())

# ===================================================================================================================== 5. runs on this Mac
mac = sys.platform == "darwin" and shutil.which("clang") and shutil.which("xcrun")
if not mac:
    print("skip the runs (they need macOS with clang and Xcode's tools)")
    print("PASS: patched MoltenVK (static parts)" if ok else "FAILED: patched MoltenVK")
    sys.exit(0 if ok else 1)

ios = subprocess.run(["xcrun", "-sdk", "iphoneos", "--show-sdk-path"], capture_output=True, text=True).returncode == 0

# ---- 5a. the session code, compiled from WineProcessBridge.m
with tempfile.TemporaryDirectory(prefix="bcd-mvkp-session-") as tmp:
    tmp = Path(tmp)
    (tmp / "session.m").write_text('''
#import <Foundation/Foundation.h>
#include <sys/stat.h>
#include <unistd.h>
#include <stdio.h>
#include <string.h>
#include <stdlib.h>
static int madeira_cfg_get(const char *key, char *buf, size_t size)
{
    const char *v = getenv("TEST_CFG_VULKAN");
    if (strcmp(key, "vulkan") || !v) return 0;
    snprintf(buf, size, "%s", v);
    return 1;
}
static const char *bcd_kosmickrisp_unusable(void) { return getenv("TEST_KK_UNUSABLE"); }
static int g_kk_cache_dir_set, g_kk_cache_size_set;
''' + function(bridge, "static void bcd_vulkan32_session_links(") + "\n" + setup + '''
int main(int argc, char **argv)
{
    @autoreleasepool {
        bcd_vulkan_session_setup([NSFileManager defaultManager], @(argv[1]), @(argv[2]), 0);
        const char *lib = getenv("MADEIRA_VULKAN_LIBRARY");
        printf("LIB=%s\\n", lib ? lib : "(unset)");
    }
    return 0;
}
''')
    r = subprocess.run(["clang", "-fobjc-arc", "-framework", "Foundation", "-Wno-unused-function", str(tmp / "session.m"), "-o", str(tmp / "session")],
                       capture_output=True, text=True)
    check("the session code of WineProcessBridge.m compiles on its own for the host", r.returncode == 0, r.stderr)
    if r.returncode == 0:
        def world(name, plain_lib=True, patched=False, kk=False):
            w = tmp / name
            for d in ("system32", "sysx64", "syswow64"):
                (w / "prefix/drive_c/windows" / d).mkdir(parents=True)
            (w / "bundle/gl").mkdir(parents=True)
            for want, f in ((plain_lib, "libMoltenVK.dylib"), (patched, "libMoltenVK_patched.dylib"), (kk, "libvulkan_kosmickrisp.dylib")):
                if want:
                    (w / "bundle/gl" / f).write_text("x")
            for arch in ("arm64ec-windows", "i386-windows"):
                (w / "bundle/vulkan" / arch).mkdir(parents=True)
                for f in ("winevulkan.dll", "vulkan-1.dll"):
                    (w / "bundle/vulkan" / arch / f).write_text(arch)
            return w

        def run(w, key):
            env = dict(os.environ)
            for n in ("MADEIRA_VULKAN_LIBRARY", "TEST_CFG_VULKAN", "TEST_KK_UNUSABLE", "MADEIRA_GL_BACKEND"):
                env.pop(n, None)
            env["MESA_SHADER_CACHE_DIR"] = str(tmp / "shader-cache-of-the-test")
            if key is not None:
                env["TEST_CFG_VULKAN"] = key
            r = subprocess.run([str(tmp / "session"), str(w / "prefix"), str(w / "bundle")], capture_output=True, text=True, env=env)
            return r.stdout + r.stderr

        def links(w):
            out = {}
            for d in ("system32", "sysx64", "syswow64"):
                for f in sorted((w / "prefix/drive_c/windows" / d).iterdir()):
                    out[f"{d}/{f.name}"] = os.readlink(f) if f.is_symlink() else "FILE"
            return out

        w = world("both", patched=True)
        log = run(w, "moltenvk")
        check("key moltenvk with both libraries in the bundle: the plain library, as before",
              "LIB=" + str(w / "bundle/gl/libMoltenVK.dylib") in log and "vulkan=moltenvk: Vulkan is on for this session" in log and "patched" not in log.lower(), log)
        log = run(w, "moltenvk-patched")
        got = links(w)
        check("key moltenvk-patched: MADEIRA_VULKAN_LIBRARY is the patched library, system32, sysx64 and syswow64 get their links, the log says the word",
              "LIB=" + str(w / "bundle/gl/libMoltenVK_patched.dylib") in log and "vulkan=moltenvk-patched: Vulkan is on for this session (MADEIRA_VULKAN_LIBRARY=" in log
              and len(got) == 6 and all(v.startswith(str(w / "bundle/vulkan/")) for v in got.values())
              and "vulkan=moltenvk-patched: Vulkan for 32-bit programs is on for this session" in log, (got, log))
        check("...in any case", "LIB=" + str(w / "bundle/gl/libMoltenVK_patched.dylib") in run(w, "MoltenVK-Patched"))
        log = run(w, None)
        check("...and a session without the key takes the links away again and exports nothing", links(w) == {} and "LIB=(unset)" in log, (links(w), log))
        w = world("no-patched")
        log = run(w, "moltenvk-patched")
        check("a build without the patched library (the plain one is there): Vulkan stays off, nothing is linked, the library is not exported, the log says why and the other "
              "MoltenVK is not taken instead",
              links(w) == {} and "LIB=(unset)" in log and "vulkan=moltenvk-patched: Vulkan stays off, this build does not have the patched MoltenVK" in log
              and "libMoltenVK.dylib" not in log and "32-bit" not in log, (links(w), log))
        run(w, "moltenvk")
        log = run(w, "moltenvk-patched")
        check("...also when the session before had links (they go)", links(w) == {} and "LIB=(unset)" in log, (links(w), log))
        w = world("typo", patched=True)
        log = run(w, "moltenvk-patch")
        check("any other word: Vulkan stays off, and the log names the three that count",
              links(w) == {} and "LIB=(unset)" in log and "vulkan=moltenvk-patch is not moltenvk, moltenvk-patched or kosmickrisp: Vulkan stays off" in log, log)
        w = world("kk", patched=True, kk=True)
        log = run(w, "kosmickrisp")
        check("key kosmickrisp with the patched library in the bundle: KosmicKrisp, as before",
              "LIB=" + str(w / "bundle/gl/libvulkan_kosmickrisp.dylib") in log and "patched" not in log.lower(), log)

# ---- 5b. Zink's choice of library: the block of zink_init, compiled and run
with tempfile.TemporaryDirectory(prefix="bcd-mvkp-zink-") as tmp:
    tmp = Path(tmp)
    (tmp / "zink.c").write_text('''
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#define ERR(...) printf(__VA_ARGS__)
int main(void)
{
    char path[4096];
    const char *dir = "/bundle/gl";
''' + block + '''
    printf("PATH=%s\\n", path);
    return 0;
}
''')
    r = subprocess.run(["clang", "-Wall", "-Werror", str(tmp / "zink.c"), "-o", str(tmp / "zink")], capture_output=True, text=True)
    check("the library choice of zink_init compiles on its own for the host", r.returncode == 0, r.stderr)
    if r.returncode == 0:
        def zink_run(value, zink_vk=None):
            env = {k: v for k, v in os.environ.items() if k not in ("MADEIRA_VULKAN_LIBRARY", "MADEIRA_ZINK_VULKAN")}
            if value is not None:
                env["MADEIRA_VULKAN_LIBRARY"] = value
            if zink_vk is not None:
                env["MADEIRA_ZINK_VULKAN"] = zink_vk
            return subprocess.run([str(tmp / "zink")], capture_output=True, text=True, env=env).stdout

        plain_out = "[winios-gl] zink Vulkan library: libMoltenVK.dylib\nPATH=/bundle/gl/libMoltenVK.dylib\n"
        check("Zink: no Vulkan library exported, an empty one, the plain one, KosmicKrisp's, or a name that only ends alike: the plain library and the old log line, byte for byte",
              all(zink_run(v) == plain_out for v in (None, "", "/app/gl/libMoltenVK.dylib", "/app/gl/libvulkan_kosmickrisp.dylib", "/app/gl/xlibMoltenVK_patched.dylib",
                                                      "libMoltenVK_patched.dylib")), [zink_run(v) for v in (None, "", "libMoltenVK_patched.dylib")])
        out = zink_run("/private/var/containers/Bundle/Application/X/BCD.app/gl/libMoltenVK_patched.dylib")
        check("Zink: with the patched library exported (the session's `vulkan = moltenvk-patched`): the patched library of the bundle's gl folder, and a log line that says so",
              out == "[winios-gl] zink Vulkan library: libMoltenVK_patched.dylib (the session's vulkan = moltenvk-patched: one MoltenVK in the process)\n"
                     "PATH=/bundle/gl/libMoltenVK_patched.dylib\n", out)
        kk = "/private/var/containers/Bundle/Application/X/BCD.app/gl/libvulkan_kosmickrisp.dylib"
        out = zink_run("/app/gl/libMoltenVK_patched.dylib", zink_vk="kosmickrisp")
        check("Zink: MADEIRA_ZINK_VULKAN=kosmickrisp in a patched session cannot move Zink off the session's MoltenVK: the patched library, and the log says what the key needs",
              out == "[winios-gl] zink Vulkan library: libMoltenVK_patched.dylib (the session's vulkan = moltenvk-patched: one MoltenVK in the process; "
                     "MADEIRA_ZINK_VULKAN=kosmickrisp needs vulkan = kosmickrisp on in this session)\nPATH=/bundle/gl/libMoltenVK_patched.dylib\n", out)
        out = zink_run(kk, zink_vk="kosmickrisp")
        check("Zink: the X-Plane 12 route (MADEIRA_ZINK_VULKAN=kosmickrisp in a kosmickrisp session) is what it was: Zink on the session's KosmicKrisp",
              out == "[winios-gl] zink Vulkan library: libvulkan_kosmickrisp.dylib (MADEIRA_ZINK_VULKAN=kosmickrisp, the session's Vulkan)\nPATH=" + kk + "\n", out)
        out = zink_run(kk)
        check("Zink: a kosmickrisp session without MADEIRA_ZINK_VULKAN, and MADEIRA_ZINK_VULKAN without a Vulkan session, stay on the plain MoltenVK as before",
              out == plain_out and zink_run(None, zink_vk="kosmickrisp")
              == "[winios-gl] zink Vulkan library: libMoltenVK.dylib (MADEIRA_ZINK_VULKAN=kosmickrisp needs vulkan = kosmickrisp on in this session)\n"
                 "PATH=/bundle/gl/libMoltenVK.dylib\n", (out, zink_run(None, zink_vk="kosmickrisp")))

# ---- 5c. the packaging check, against made-up packages
if ios:
    with tempfile.TemporaryDirectory(prefix="bcd-mvkp-pkg-") as tmp:
        tmp = Path(tmp)
        (tmp / "tools").mkdir()
        shutil.copy(R / "tools/check-vulkan-shipped.py", tmp / "tools/check-vulkan-shipped.py")
        gl_dir = tmp / "app/Madeira/gl"
        gl_dir.mkdir(parents=True)

        def dylib(path, install, exports=("vkGetInstanceProcAddr", "vkGetDeviceProcAddr"), pad=1_200_000, arch="arm64"):
            src = tmp / "x.c"
            src.write_text("".join(f"void *{e}(void *a, const char *b) {{ return 0; }}\n" for e in exports)
                           + f"const char bcd_pad[{pad}] = {{1}};\n")
            r = subprocess.run(["xcrun", "-sdk", "iphoneos", "clang", "-arch", arch, "-miphoneos-version-min=15.0", "-dynamiclib", str(src), "-install_name", install,
                                "-o", str(path)], capture_output=True, text=True)
            assert r.returncode == 0, r.stderr

        def lines():
            r = subprocess.run([sys.executable, str(tmp / "tools/check-vulkan-shipped.py")], capture_output=True, text=True)
            return [l for l in r.stdout.splitlines() if "libMoltenVK_patched" in l or "patched MoltenVK" in l]

        dylib(gl_dir / "libMoltenVK.dylib", "@rpath/libMoltenVK.dylib", pad=1_000_000)
        none = lines()
        dylib(gl_dir / "libMoltenVK_patched.dylib", "@rpath/libMoltenVK_patched.dylib")
        (gl_dir / "libMoltenVK_patched.version").write_text("v1.4.2+bcd-patched.0123456789ab spirv-cross=aa217aeb\n")
        good = lines()
        (gl_dir / "libMoltenVK_patched.version").unlink()
        no_version = lines()
        (gl_dir / "libMoltenVK_patched.version").write_text("v\n")
        shutil.copy(gl_dir / "libMoltenVK.dylib", gl_dir / "libMoltenVK_patched.dylib")
        same = lines()
        dylib(gl_dir / "libMoltenVK_patched.dylib", "@rpath/libMoltenVK.dylib")
        wrong_id = lines()
        dylib(gl_dir / "libMoltenVK_patched.dylib", "@rpath/libMoltenVK_patched.dylib", exports=("vkGetInstanceProcAddr",))
        no_entry = lines()
        dylib(gl_dir / "libMoltenVK_patched.dylib", "@rpath/libMoltenVK_patched.dylib", pad=1000)
        small = lines()
        dylib(gl_dir / "libMoltenVK_patched.dylib", "@rpath/libMoltenVK_patched.dylib")
        (gl_dir / "libMoltenVK_patched.dylib.tmp").write_text("half")
        tmp_left = lines()
        (gl_dir / "libMoltenVK_patched.dylib.tmp").unlink()
        (gl_dir / "libMoltenVK_patched.dylib").unlink()
        (gl_dir / "libMoltenVK_patched.version").unlink()
        absent = lines()
        check("the packaging check: no patched library is fine; a whole one is fine; a missing version file, the plain library's bytes, a wrong install name, a missing entry "
              "point, a tiny file and a leftover .tmp are each refused by their own line",
              none == [] and absent == [] and good == []
              and len(no_version) == 1 and "libMoltenVK_patched.version is missing" in no_version[0]
              and any("is the plain libMoltenVK.dylib under another name" in l for l in same)
              and len(wrong_id) == 1 and "install name @rpath/libMoltenVK.dylib, want @rpath/libMoltenVK_patched.dylib" in wrong_id[0]
              and len(no_entry) == 1 and "does not export _vkGetDeviceProcAddr" in no_entry[0]
              and len(small) == 1 and "is only" in small[0]
              and len(tmp_left) == 1 and ".tmp is in the package" in tmp_left[0],
              (none, good, no_version, same, wrong_id, no_entry, small, tmp_left, absent))
else:
    print("skip the packaging runs (no iOS SDK)")

# ---- 5d. the build script, in a scratch tree against the local mirrors (this Mac only): git and patch logic, stamps, recovery
K = Path.home() / "madeira-local/kaynak/ucuncu"
YERELGIT = Path.home() / "madeira-local/yerel-git.sh"
if (K / "KhronosGroup--MoltenVK.git").is_dir() and (K / "KhronosGroup--SPIRV-Cross.git").is_dir() and YERELGIT.is_file():
    with tempfile.TemporaryDirectory(prefix="bcd-mvkp-build-") as tmp:
        tmp = Path(tmp)
        (tmp / "build/moltenvk-ios/patches").mkdir(parents=True)
        shutil.copy(R / "build/moltenvk-ios/build-patched.sh", tmp / "build/moltenvk-ios/build-patched.sh")
        for p in (PATCH_MVK, PATCH_SPC):
            shutil.copy(p, tmp / "build/moltenvk-ios/patches" / p.name)
        shim = tmp / "shim"
        shim.mkdir()
        (shim / "make").write_text('#!/bin/sh\n[ "$1" = ios ] || exit 2\n[ -z "$BCD_TEST_MAKE_FAILS" ] || exit 1\n'
                                   'd=Package/Release/MoltenVK/dynamic/MoltenVK.xcframework/ios-arm64/MoltenVK.framework\nmkdir -p "$d"\ncp "$BCD_TEST_STANDIN" "$d/MoltenVK"\n'
                                   'echo make >> "$BCD_TEST_LOG"\n')
        (shim / "xcodebuild").write_text('#!/bin/sh\necho "xcodebuild must not run in this test (the dependency stamp did not match)" >&2\nexit 99\n')
        for f in shim.iterdir():
            f.chmod(0o755)
        (tmp / "stand-in.c").write_text("void *vkGetInstanceProcAddr(void *i, const char *n) { return 0; }\nvoid *vkGetDeviceProcAddr(void *d, const char *n) { return 0; }\n")
        subprocess.run(["clang", "-dynamiclib", str(tmp / "stand-in.c"), "-install_name", "@rpath/MoltenVK.framework/MoltenVK", "-o", str(tmp / "stand-in.dylib")], check=True)
        env = dict(os.environ, PATH=f"{shim}:{os.environ['PATH']}", BCD_TEST_STANDIN=str(tmp / "stand-in.dylib"), BCD_TEST_LOG=str(tmp / "make.log"),
                   LANG="en_US.UTF-8", LC_ALL="en_US.UTF-8")
        env.pop("BCD_TEST_MAKE_FAILS", None)
        for k in [k for k in env if k.startswith("GIT_CONFIG")]:
            env.pop(k)

        def sh(cmd, **kw):
            e = dict(env)
            e.update(kw.pop("env", {}))
            return subprocess.run(["bash", "-c", f'. "{YERELGIT}" && {cmd}'], capture_output=True, text=True, env=e, cwd=kw.pop("cwd", tmp))

        def build(**kw):
            return subprocess.run(["bash", "-c", f'. "{YERELGIT}" && bash "{tmp}/build/moltenvk-ios/build-patched.sh"'],
                                  capture_output=True, text=True, env={**env, **kw.get("env", {})}, cwd=tmp)

        src, spc, out = tmp / "toolchains/MoltenVK-patched", tmp / "toolchains/MoltenVK-patched-SPIRV-Cross", tmp / "app/Madeira/gl"
        # the pins' dependency stamp, as the script makes it, so that fetchDependencies (the network, xcodebuild) is not run
        r = sh(f'mkdir -p toolchains && git clone -q -b v1.4.2 https://github.com/KhronosGroup/MoltenVK toolchains/MoltenVK-patched && cd toolchains/MoltenVK-patched && git apply "{PATCH_MVK}"')
        check("the scratch tree: MoltenVK v1.4.2 comes from the local mirror at the pinned commit, and the patch applies to it",
              r.returncode == 0 and subprocess.run(["git", "-C", str(src), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip() == MVK_COMMIT
              and (src / "MoltenVK/MoltenVK/GPUObjects/MVKPerfICount.h").is_file()
              and (src / "ExternalRevisions/SPIRV-Cross_repo_revision").read_text().strip() == SPC_NEW, r.stderr[-1200:])
        if r.returncode == 0:
            data = b"".join((src / "ExternalRevisions" / n).read_bytes() for n in sorted(p.name for p in (src / "ExternalRevisions").glob("*_repo_revision")))
            deps = hashlib.sha256(data + PATCH_SPC.read_bytes()).hexdigest()[:16]
            (src / "External/build").mkdir(parents=True)
            (src / "External/.bcd-patched-deps").write_text(deps + "\n")
            r1 = build()
            tree_stamp = (src / ".git/bcd-patched-tree").read_text().split() if (src / ".git/bcd-patched-tree").is_file() else []
            lib = out / "libMoltenVK_patched.dylib"
            ident = subprocess.run(["otool", "-D", str(lib)], capture_output=True, text=True).stdout.split() if lib.is_file() else []
            check("the script, first run: the patch is on the pinned MoltenVK, SPIRV-Cross is at its pin with the patch committed on top, the dependencies are left alone, the "
                  "library is staged under its own name and install name with its version file, nothing else (no plain library, no .tmp, no licence file)",
                  r1.returncode == 0 and len(tree_stamp) == 2 and tree_stamp[0] == sha(PATCH_MVK)[:16]
                  and subprocess.run(["git", "-C", str(spc), "rev-parse", "HEAD^"], capture_output=True, text=True).stdout.strip() == SPC_NEW
                  and "instance_relative_patch_id" in subprocess.run(["git", "-C", str(spc), "log", "-1", "--format=%s"], capture_output=True, text=True).stdout
                  and "instance_relative_patch_id" in (spc / "spirv_msl.hpp").read_text()
                  and (src / "External/.bcd-patched-deps").read_text().strip() == deps
                  and "@rpath/libMoltenVK_patched.dylib" in ident
                  and re.fullmatch(r"v1\.4\.2\+bcd-patched\.[0-9a-f]{12} spirv-cross=aa217aeb\n", (out / "libMoltenVK_patched.version").read_text() if lib.is_file() else "")
                  and sorted(p.name for p in out.iterdir()) == ["libMoltenVK_patched.dylib", "libMoltenVK_patched.version"] and not (tmp / "app/Madeira/licenses").exists(),
                  (r1.returncode, r1.stdout[-800:], r1.stderr[-1500:]))
            if r1.returncode == 0:
                touched_file = src / "MoltenVK/MoltenVK/GPUObjects/MVKPerfICount.h"
                m1 = touched_file.stat().st_mtime_ns
                r2 = build()
                check("a second run changes nothing in the sources (the stamp matches, so the patch is not applied again) and makes the same library",
                      r2.returncode == 0 and touched_file.stat().st_mtime_ns == m1 and (out / "libMoltenVK_patched.dylib").is_file(), (r2.stdout[-500:], r2.stderr[-800:]))
                # a folder somebody edited, a stray file, an edited SPIRV-Cross: the next run puts all of it back
                (src / "README.md").write_text((src / "README.md").read_text() + "junk\n")
                (src / "stray-untracked.txt").write_text("x")
                (spc / "README.md").write_text((spc / "README.md").read_text() + "junk\n")
                r3 = build()
                status_mvk = subprocess.run(["git", "-C", str(src), "status", "--porcelain"], capture_output=True, text=True).stdout.splitlines()
                check("a folder somebody edited is put back: the edit and the stray file are gone, the patch is on again, SPIRV-Cross is clean on its patched commit",
                      r3.returncode == 0 and "junk" not in (src / "README.md").read_text() and not (src / "stray-untracked.txt").exists()
                      and "junk" not in (spc / "README.md").read_text() and not subprocess.run(["git", "-C", str(spc), "status", "--porcelain", "--untracked-files=no"],
                                                                                              capture_output=True, text=True).stdout.strip()
                      and subprocess.run(["git", "-C", str(spc), "rev-parse", "HEAD^"], capture_output=True, text=True).stdout.strip() == SPC_NEW
                      and (src / "MoltenVK/MoltenVK/GPUObjects/MVKPerfTess.h").is_file() and any("MVKCmdDraw.mm" in l for l in status_mvk),
                      (r3.returncode, r3.stderr[-1000:], status_mvk[:3]))
                (out / "libMoltenVK_patched.dylib").unlink()
                (out / "libMoltenVK_patched.version").unlink()
                r4 = build(env={"BCD_TEST_MAKE_FAILS": "1"})
                check("a failing MoltenVK build: the script fails, leaves no library, no version file and no .tmp (the CI step then only warns and drops what it finds)",
                      r4.returncode != 0 and list(out.iterdir()) == [], (r4.returncode, [p.name for p in out.iterdir()]))
                r5 = build()
                check("...and the next run makes the library again", r5.returncode == 0 and (out / "libMoltenVK_patched.dylib").is_file())
                (tmp / "build/moltenvk-ios/patches" / PATCH_SPC.name).write_text(PATCH_SPC.read_text().replace("instance_relative_patch_id = false", "instance_relative_patch_id = false "))
                r6 = build()
                check("a changed patch file is noticed: the script no longer trusts its stamps (the dependencies are to be built again, which this test does not allow: xcodebuild "
                      "refuses)", r6.returncode != 0 and "xcodebuild must not run in this test" in (r6.stdout + r6.stderr), (r6.returncode, r6.stderr[-600:]))
                shutil.copy(PATCH_SPC, tmp / "build/moltenvk-ios/patches" / PATCH_SPC.name)
                outer = tmp / "outer"
                (outer / "sub").mkdir(parents=True)
                (outer / "sub/fetchDependencies").write_text("not MoltenVK\n")
                (outer / "work.txt").write_text("committed\n")
                subprocess.run(["git", "-C", str(outer), "init", "-q"], check=True)
                subprocess.run(["git", "-C", str(outer), "add", "-A"], check=True)
                subprocess.run(["git", "-C", str(outer), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "x"], check=True)
                (outer / "work.txt").write_text("changed by somebody, not committed\n")
                r8 = build(env={"MOLTENVK_PATCHED_SRC": str(outer / "sub")})
                check("a source folder that is not a repository of its own (one inside another) is refused before any reset or clean can touch the repository above it",
                      r8.returncode != 0 and "is not a git repository of its own" in r8.stderr and (outer / "work.txt").read_text().startswith("changed by somebody"),
                      (r8.returncode, r8.stderr[-400:]))
                (tmp / "build/moltenvk-ios/patches" / PATCH_MVK.name).unlink()
                r7 = build()
                check("a missing patch file stops the script before anything is touched", r7.returncode != 0 and "patches/0001-moltenvk-enshrouded.patch is missing" in r7.stderr, r7.stderr[-400:])
else:
    print("skip the build script run (the local mirrors of MoltenVK and SPIRV-Cross, or yerel-git.sh, are not on this machine)")

print("PASS: patched MoltenVK" if ok else "FAILED: patched MoltenVK")
sys.exit(0 if ok else 1)

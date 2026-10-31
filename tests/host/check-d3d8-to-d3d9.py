#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""Direct3D 8 on DXMT's Direct3D 9 (crosire's d3d8to9) and DirectDraw through cnc-ddraw, both opt-in per game (round 602); no Wine runs.

Wine's d3d8.dll and ddraw.dll draw through wined3d, whose only 3D backend is OpenGL, and a phone has none. Two third-party DLLs, built
from their projects' own sources in third-party/, take their place for a 32-bit game whose file asks (env.MADEIRA_D3D8 = d3d9,
env.MADEIRA_DDRAW = cnc). The owner's rule is that nothing that works today changes, so most of this file proves that:
  - the vendored sources are the projects' files byte for byte (SHA256SUMS), with their licences, and carry no Microsoft SDK header;
  - the build scripts write into their work directory and one DLL of the i386 farm, and nowhere else; a failure is a warning that leaves
    Wine's DLLs alone; with the toolchain they are run into scratch directories and the DLLs checked (i386, exports, imports);
  - the workflow builds them after the i386 farm's cache was saved, never fails the run and hashes the farm's graphics DLLs before and after;
  - the link helper (BCDGraphicsRoutes.c, compiled and run on temporary folders) links only for the one value of its variable, replaces the
    farm's link atomically, and leaves Wine's DLL in place when anything fails;
  - the bridge calls the helper once, for a session that has the i386 farm, after the farm was relinked; no other code names the DLLs;
  - the keys are in the catalog (off by default), the list policy takes one value each, and only the games verified in the logs carry the
    lines (from build 602) and have auto-tune rules.
Set MINGW to the llvm-mingw bin directory (the default: toolchains/ in the repository) for the compile and link.
"""
from pathlib import Path
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[2]
ok = True


def check(what, cond):
    global ok
    print(("ok   " if cond else "FAIL ") + what)
    ok &= bool(cond)


# --- the vendored sources -----------------------------------------------------------------------------------------------
def sums_match(folder, skip_dirs=()):
    want = {}
    for line in (folder / "SHA256SUMS").read_text().splitlines():
        digest, name = line.split(None, 1)
        want[name.strip()] = digest
    have = {}
    for p in sorted(folder.rglob("*")):
        rel = "./" + str(p.relative_to(folder))
        if p.is_file() and p.name not in ("SHA256SUMS", "BCD-SOURCE.txt") and not rel.startswith("./bcd/"):
            have[rel] = hashlib.sha256(p.read_bytes()).hexdigest()
    return want == have, len(have)


d8 = root / "third-party/d3d8to9"
good, n = sums_match(d8)
check("third-party/d3d8to9: every file is byte for byte the project's (SHA256SUMS, %d files), nothing else is there" % n, good and n == 22)
lic = (d8 / "LICENSE.md").read_text()
check("its BSD 2-clause licence and Patrick Mours' copyright are kept", "Copyright (C) 2015 Patrick Mours" in lic and "Redistributions of source code must retain" in lic
      and (root / "LICENSES/BSD-2-d3d8to9.txt").read_text() == lic and (root / "app/Madeira/legal/LICENSES/BSD-2-d3d8to9.txt").read_text() == lic)
note = (d8 / "BCD-SOURCE.txt").read_text()
check("BCD-SOURCE.txt names the project, the tag v1.16.0, the commit and the archive's SHA-256",
      "github.com/crosire/d3d8to9" in note and "v1.16.0" in note and "255338f698c8270b537f0a91a13f795f4f988250" in note
      and "5f7c840620246b103a871bdf676a1a35b9ca76ba5e000e7f99c9901c45156316" in note and "Changes by BCD to these files: none" in note)
check("every source file keeps its copyright header (Patrick Mours) - no BCD edit",
      all("Copyright (C) 2015 Patrick Mours" in p.read_text() for p in (d8 / "source").glob("*.cpp")))

cnc = root / "third-party/cnc-ddraw"
good, n = sums_match(cnc)
check("third-party/cnc-ddraw: every file is byte for byte the project's (SHA256SUMS, %d files), nothing else is there" % n, good and n == 82)
lic = (cnc / "LICENSE").read_text()
check("its MIT licence and FunkyFr3sh's copyright are kept", "Copyright (c) 2022 github.com/FunkyFr3sh" in lic and (root / "LICENSES/MIT-cnc-ddraw.txt").read_text() == lic
      and (root / "app/Madeira/legal/LICENSES/MIT-cnc-ddraw.txt").read_text() == lic)
note = (cnc / "BCD-SOURCE.txt").read_text()
check("BCD-SOURCE.txt names the project, the tag v7.1.0.0, the commit and the tarball's SHA-256",
      "github.com/FunkyFr3sh/cnc-ddraw" in note and "v7.1.0.0" in note and "541b5de218ec3fbd6ea91e606ebfadc07c1786b0" in note
      and "25f910ddee138f3fbed7584ddd88239d1c9bec326cb5a993df3b7a96aa8b57b1" in note)
check("the two Microsoft DirectX SDK headers (Copyright (C) Microsoft Corporation, no licence grant) are not copied, nor is Detours or the Borland settings program",
      not (cnc / "inc/ddraw.h").exists() and not (cnc / "inc/d3dcaps.h").exists() and not (cnc / "src/detours").exists() and not (cnc / "config").exists()
      and not any("Copyright (C) Microsoft Corporation" in p.read_text(errors="replace") for p in (cnc / "inc").glob("*.h")))
check("the notes say why, and the bundled licence texts of what it contains are there (LodePNG zlib, Detours MIT excerpt)",
      "Microsoft DirectX SDK headers" in note and (root / "LICENSES/zlib-lodepng.txt").read_text().count("Lode Vandevenne") == 1
      and "Copyright (c) Microsoft Corporation" in (root / "LICENSES/MIT-Detours.txt").read_text()
      and all((root / "app/Madeira/legal/LICENSES" / n_).read_bytes() == (root / "LICENSES" / n_).read_bytes() for n_ in ("zlib-lodepng.txt", "MIT-Detours.txt")))
compat = (cnc / "bcd/ddraw_dx1.h").read_text()
check("bcd/ddraw_dx1.h is BCD's file (SPDX and BCD Material headers) and holds the DDCAPS_DX1 structure with a size check",
      compat.startswith("// SPDX-License-Identifier: GPL-3.0-only") and "BCD Material" in compat and "} DDCAPS_DX1, *LPDDCAPS_DX1;" in compat and "== 172" in compat)
tp = (root / "THIRD-PARTY-NOTICES.md").read_text()
check("THIRD-PARTY-NOTICES.md (and its bundled copy) list both, with licence, source and the opt-in key",
      "| **d3d8to9** v1.16.0 (crosire) | BSD-2-Clause |" in tp and "| **cnc-ddraw** v7.1.0.0 (FunkyFr3sh) | MIT;" in tp
      and "env.MADEIRA_D3D8 = d3d9" in tp and "env.MADEIRA_DDRAW = cnc" in tp and tp == (root / "app/Madeira/legal/THIRD-PARTY-NOTICES.md").read_text())
short = (root / "app/Madeira/licenses/THIRD-PARTY-NOTICES.txt").read_text()
check("the notice shipped in the package names both, with their licences", "d3d8to9 v1.16.0" in short and "BSD-2-Clause" in short and "cnc-ddraw v7.1.0.0" in short and "LodePNG" in short)

# --- the build scripts ----------------------------------------------------------------------------------------------------
for name, dll, outvar, shipvar, work in (("build-d3d8to9-dll.sh", "d3d8to9.dll", "D3D8TO9_OUT", "D3D8TO9_NO_SHIP", "build/d3d8to9"),
                                         ("build-cnc-ddraw-dll.sh", "cnc-ddraw.dll", "CNCDDRAW_OUT", "CNCDDRAW_NO_SHIP", "build/cnc-ddraw")):
    sh = (root / "tools" / name).read_text()
    check(f"{name}: builds in its own work directory ({work}) and ships only {dll}", f'OUT="${{{outvar}:-$R/{work}}}"' in sh and f'DEST="$SHIP/{dll}"' in sh)
    writes = re.findall(r'^\s*(?:cp|mv)\b[^\n]*', sh, re.M)
    check(f"{name}: the only copy into the bundle is {dll} (through a temporary name); the other copies go into the work directory",
          sum('"$DEST.tmp"' in w for w in writes) == 1 and all('"$DEST' in w or '"$OUT' in w for w in writes))
    check(f"{name}: it never writes d3d8.dll, ddraw.dll, wined3d.dll, d3d9, d3d11, dxgi or winemetal",
          not re.search(r'(>|cp |mv |rm |strip |ln )[^\n]*\$SHIP/(d3d8|ddraw|wined3d|d3d9|d3d11|dxgi|winemetal)', sh))
    rms = re.findall(r'^\s*rm\s[^\n]*', sh, re.M)
    check(f"{name}: every rm removes something inside its own work directory ($OUT)", rms and all(re.fullmatch(r'rm -rf "\$OUT"', r.strip()) for r in rms))
    check(f"{name}: a failure is a warning that leaves the bundle alone (fail() exits 1 before the copy)",
          f"::warning::{dll} NOT built" in sh and sh.index("fail()") < sh.index('cp "$OUT/' + dll + '" "$DEST.tmp"') and f"{shipvar}" in sh)
    check(f"{name}: it checks the vendored files against SHA256SUMS first and the result's machine, exports and imports last",
          "differs from SHA256SUMS" in sh and "IMAGE_FILE_MACHINE_I386" in sh and "exports.expected" in sh and "imports.built" in sh)
    check(f"{name}: it builds for i686 with the tracked llvm-mingw and never touches the sources", "i686-w64-mingw32-clang" in sh and not re.search(r'sed -i|patch ', sh))
sh8 = (root / "tools/build-d3d8to9-dll.sh").read_text()
check("d3d8to9: compiled as the project's Release configuration (-DD3D8TO9NOLOG: no log file, no message box on a missing log), with the project's link (stdcall fixup, d3d9, gdi32)",
      "-DD3D8TO9NOLOG" in sh8 and "--enable-stdcall-fixup" in sh8 and "-ld3d9 -lgdi32" in sh8 and "-std=c++17" in sh8)
shc = (root / "tools/build-cnc-ddraw-dll.sh").read_text()
check("cnc-ddraw: compiled as the project's Makefile does (-O2 -std=c99, -static -shared, stdcall fixup) against llvm-mingw's ddraw.h with bcd/ddraw_dx1.h and d3dtypes.h for the Direct3D 7 files",
      "-O2 -Wall -std=c99" in shc and "-static -shared" in shc and "--enable-stdcall-fixup" in shc and "bcd/ddraw_dx1.h" in shc and "-include d3dtypes.h" in shc and "IDirect3D" in shc)
check("cnc-ddraw: the build writes inc/git.h into its COPY of the sources, never into third-party/", 'printf \'#define GIT_COMMIT "v7.1.0.0"' in shc and '> "$OUT/inc/git.h"' in shc)

mingw = Path(os.environ.get("MINGW", root / "toolchains/llvm-mingw-20260421-ucrt-macos-universal/bin"))
if not (mingw / "i686-w64-mingw32-clang").exists():
    alt = Path.home() / "madeira-bcd/toolchains/llvm-mingw-20260421-ucrt-macos-universal/bin"
    mingw = alt if (alt / "i686-w64-mingw32-clang").exists() else mingw
if (mingw / "i686-w64-mingw32-clang").exists():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        ship = tmp / "ship"
        ship.mkdir()
        (ship / "marker.txt").write_text("untouched")
        for name, dll, outvar, shipvar, builder in (("d3d8to9", "d3d8to9.dll", "D3D8TO9_OUT", "D3D8TO9_NO_SHIP", "tools/build-d3d8to9-dll.sh"),
                                                    ("cnc-ddraw", "cnc-ddraw.dll", "CNCDDRAW_OUT", "CNCDDRAW_NO_SHIP", "tools/build-cnc-ddraw-dll.sh")):
            env = dict(os.environ, MINGW=str(mingw), SHIP_DIR=str(ship), JOBS="3", **{outvar: str(tmp / ("out-" + name)), shipvar: "1"})
            r = subprocess.run(["nice", "-n", "10", "bash", str(root / builder)], cwd=root, env=env, capture_output=True, text=True)
            built = tmp / ("out-" + name) / dll
            check(f"{name}: the script builds {dll} with the toolchain and checks it (i386, exports, imports) - {built.stat().st_size if built.exists() else 0} bytes",
                  r.returncode == 0 and built.exists() and built.stat().st_size > 100000)
            if r.returncode:
                print(r.stdout[-1500:], r.stderr[-1500:])
        check("...into the scratch work directories only: the ship directory holds the one file it had", sorted(p.name for p in ship.iterdir()) == ["marker.txt"])
        check("...and the vendored sources are unchanged by the build (no inc/git.h, no object file under third-party/)",
              not (cnc / "inc/git.h").exists() and not list((root / "third-party").rglob("*.o")))
else:
    print("note: no i686 llvm-mingw (set MINGW): the build scripts were not run")

# --- the link helper -----------------------------------------------------------------------------------------------------
test_c = r'''
#include "BCDGraphicsRoutes.h"
#include <assert.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>
static void touch(const char *dir, const char *name) { char p[1024]; snprintf(p, sizeof p, "%s/%s", dir, name); FILE *f = fopen(p, "w"); fputs(name, f); fclose(f); }
static void link_to(const char *dir, const char *name, const char *src) { char p[1024], s[1024]; snprintf(p, sizeof p, "%s/%s", dir, name); snprintf(s, sizeof s, "%s/%s", src, name); unlink(p); assert(symlink(s, p) == 0); }
static void target(const char *dir, const char *name, char *out) { char p[1024]; snprintf(p, sizeof p, "%s/%s", dir, name); ssize_t n = readlink(p, out, 1023); if (n < 0) { strcpy(out, "(none)"); return; } out[n] = 0; }
int main(int argc, char **argv) {
    const char *i386 = argv[1], *wow = argv[2];
    char line[512], t[1024], want[1024];
    touch(i386, "d3d8.dll"); touch(i386, "ddraw.dll"); touch(i386, "d3d8to9.dll");   /* no cnc-ddraw.dll in this bundle */
    link_to(wow, "d3d8.dll", i386); link_to(wow, "ddraw.dll", i386);
    assert(bcd_wow_route_count == 2);
    const BCDWowRoute *d8 = &bcd_wow_routes[0], *dd = &bcd_wow_routes[1];
    assert(!strcmp(d8->env, "MADEIRA_D3D8") && !strcmp(d8->value, "d3d9") && !strcmp(d8->target, "d3d8.dll") && !strcmp(d8->source, "d3d8to9.dll"));
    assert(!strcmp(dd->env, "MADEIRA_DDRAW") && !strcmp(dd->value, "cnc") && !strcmp(dd->target, "ddraw.dll") && !strcmp(dd->source, "cnc-ddraw.dll"));
    /* not asked: unset, empty, another value */
    assert(bcd_wow_route_apply(d8, NULL, wow, i386, line, sizeof line) == 0 && !line[0]);
    assert(bcd_wow_route_apply(d8, "", wow, i386, line, sizeof line) == 0);
    assert(bcd_wow_route_apply(d8, "wined3d", wow, i386, line, sizeof line) == 0);
    assert(bcd_wow_route_apply(d8, "D3D9", wow, i386, line, sizeof line) == 0);   /* exact value only */
    assert(bcd_wow_route_apply(dd, "d3d9", wow, i386, line, sizeof line) == 0);   /* another route's value */
    snprintf(want, sizeof want, "%s/d3d8.dll", i386); target(wow, "d3d8.dll", t); assert(!strcmp(t, want));   /* Wine's link untouched */
    /* asked: relinked, the line says so, no temporary file is left */
    assert(bcd_wow_route_apply(d8, "d3d9", wow, i386, line, sizeof line) == 1);
    assert(strstr(line, "[WineProc] MADEIRA_D3D8=d3d9: d3d8.dll -> d3d8to9.dll (") == line && strstr(line, "syswow64") && line[strlen(line) - 1] == '\n');
    snprintf(want, sizeof want, "%s/d3d8to9.dll", i386); target(wow, "d3d8.dll", t); assert(!strcmp(t, want));
    char tmpn[1100]; snprintf(tmpn, sizeof tmpn, "%s/d3d8.dll.bcd-new", wow); assert(access(tmpn, F_OK) != 0);
    /* applying it again is the same (a session start twice) */
    assert(bcd_wow_route_apply(d8, "d3d9", wow, i386, line, sizeof line) == 1);
    /* the DLL is not in this bundle: Wine's stays, the line says so */
    assert(bcd_wow_route_apply(dd, "cnc", wow, i386, line, sizeof line) == -1);
    assert(strstr(line, "[WineProc] MADEIRA_DDRAW=cnc but this build has no cnc-ddraw.dll -- Wine's ddraw.dll stays\n") == line);
    snprintf(want, sizeof want, "%s/ddraw.dll", i386); target(wow, "ddraw.dll", t); assert(!strcmp(t, want));
    /* the link cannot be made (no such folder): -1, nothing is created */
    assert(bcd_wow_route_apply(d8, "d3d9", "/nonexistent-bcd-folder", i386, line, sizeof line) == -1 && strstr(line, "stays"));
    /* the whole table from the environment: only d3d8 is set */
    link_to(wow, "d3d8.dll", i386);
    setenv("MADEIRA_D3D8", "d3d9", 1); unsetenv("MADEIRA_DDRAW");
    int fds[2]; assert(pipe(fds) == 0);
    assert(bcd_wow_routes_apply(wow, i386, fds[1]) == 1);
    close(fds[1]); char buf[2048]; ssize_t n = read(fds[0], buf, sizeof buf - 1); buf[n > 0 ? n : 0] = 0;
    assert(strstr(buf, "MADEIRA_D3D8=d3d9: d3d8.dll -> d3d8to9.dll") && !strstr(buf, "DDRAW"));
    unsetenv("MADEIRA_D3D8");
    link_to(wow, "d3d8.dll", i386);
    assert(pipe(fds) == 0);
    assert(bcd_wow_routes_apply(wow, i386, fds[1]) == 0);
    close(fds[1]); n = read(fds[0], buf, sizeof buf - 1); assert(n == 0);
    target(wow, "d3d8.dll", t); snprintf(want, sizeof want, "%s/d3d8.dll", i386); assert(!strcmp(t, want));
    puts("routes ok");
    return 0;
}
'''
with tempfile.TemporaryDirectory() as tmp:
    tmp = Path(tmp)
    (tmp / "t.c").write_text(test_c)
    (tmp / "i386").mkdir()
    (tmp / "wow").mkdir()
    cc = subprocess.run(["cc", "-std=c11", "-Wall", "-Werror", "-I", str(root / "app/Madeira"), "-o", str(tmp / "t"), str(tmp / "t.c"), str(root / "app/Madeira/BCDGraphicsRoutes.c")],
                        capture_output=True, text=True)
    check("BCDGraphicsRoutes.c compiles without warnings", cc.returncode == 0)
    if cc.returncode:
        print(cc.stderr[-2000:])
    else:
        run = subprocess.run([str(tmp / "t"), str(tmp / "i386"), str(tmp / "wow")], capture_output=True, text=True)
        check("the link helper: only the exact value links, atomically (no .bcd-new left), a missing DLL or a failed link leaves Wine's link, the table reads the environment",
              run.returncode == 0 and "routes ok" in run.stdout)
        if run.returncode:
            print(run.stdout, run.stderr)

# --- the bridge ----------------------------------------------------------------------------------------------------------
m = (root / "app/Madeira/WineProcessBridge.m").read_text()
check("the bridge includes the helper and calls it once", m.count('#include "BCDGraphicsRoutes.h"') == 1 and m.count("bcd_wow_routes_apply(") == 1)
call = m.index("bcd_wow_routes_apply(")
guard = m.rfind("if (has_i386_set) {", 0, call)
check("...only for a session whose bundle has the i386 farm, after the farm was relinked from the bundle (madeira_link_syswow64) and after the 64-bit Direct3D 9 block",
      guard > m.index("madeira_link_syswow64(fm, prefix, bundlePath);") and guard > m.index('getenv("MADEIRA_D3D9_X64")') and call - guard < 120)
check("...with the syswow64 folder of the prefix and the bundle's i386-windows folder",
      'drive_c/windows/syswow64' in m[call - 220:call + 260] and '@"i386-windows"' in m[call:call + 260])
hook_start = m.index("/* madeira-bcd (round 602): the two 32-bit replacements")
hook_comment = m[hook_start:m.index("*/", hook_start)]
check("the bridge names d3d8to9 and cnc-ddraw only in the hook's comment (the helper owns the table of DLL names)",
      m.replace(hook_comment, "").count("d3d8to9") == 0 and m.replace(hook_comment, "").count("cnc-ddraw") == 0)
named = [l for l in m.splitlines() if "MADEIRA_D3D8" in l or "MADEIRA_DDRAW" in l]
check("...the two keys appear only in the comment and the include line of the hook (%d lines)" % len(named), all(("round 602" in l or "env.MADEIRA_D3D8" in l or 'setenv("MADEIRA_D3D8", "d3d9", 0);' in l or "env.MADEIRA_DDRAW" in l or "BCDGraphicsRoutes" in l) for l in named) and len(named) <= 6)
proj = (root / "app/Madeira.xcodeproj/project.pbxproj").read_text()
check("the Xcode project builds BCDGraphicsRoutes.c, BCDGraphicsScan.swift and BCDGraphicsDefaults.swift",
      all(f"{i} /* {n_} in Sources */" in proj for i, n_ in (("A1004101", "BCDGraphicsScan.swift"), ("A1004102", "BCDGraphicsDefaults.swift"), ("A1004103", "BCDGraphicsRoutes.c")))
      and "A2004104 /* BCDGraphicsRoutes.h */" in proj)
h = (root / "app/Madeira/BCDGraphicsRoutes.h").read_text()
c = (root / "app/Madeira/BCDGraphicsRoutes.c").read_text()
check("the new files carry the SPDX and BCD Material headers", all(t.startswith("// SPDX-License-Identifier: GPL-3.0-only") and "BCD Material" in t[:300] for t in (h, c)))

# --- the workflow -------------------------------------------------------------------------------------------------------
wf = (root / ".github/workflows/build-ipa.yml").read_text()
steps = re.findall(r"\n      - name: (.+)", wf)
idx = {n_: i for i, n_ in enumerate(steps)}
b8 = next((n_ for n_ in steps if n_.startswith("Build d3d8to9.dll")), None)
bc = next((n_ for n_ in steps if n_.startswith("Build cnc-ddraw.dll")), None)
k8 = next((n_ for n_ in steps if n_.startswith("Check the Direct3D 8 and DirectDraw routes")), None)
kd = next((n_ for n_ in steps if n_.startswith("Check the 64-bit Direct3D 9 default")), None)
check("the workflow has both build steps and both check steps", None not in (b8, bc, k8, kd))
if None not in (b8, bc, k8, kd):
    for name, dll, script in ((b8, "d3d8to9", "tools/build-d3d8to9-dll.sh"), (bc, "cnc-ddraw", "tools/build-cnc-ddraw-dll.sh")):
        block = wf[wf.index("- name: " + name):]
        block = block[:block.index("\n      - name:", 10)]
        check(f"{dll}: built after the i386 farm's cache was saved and put-back, after d3d9-dxmt.dll, before the IPA is packaged; never fails the run; hashes the farm's graphics DLLs before and after",
              idx["Save i386 farm"] < idx[name] and idx["Put back the files patched for the i386 farm only (x64 machine answer)"] < idx[name]
              and idx[next(n_ for n_ in steps if n_.startswith("Build d3d9-dxmt.dll"))] < idx[name] < idx["Verify committed PE DLLs"]
              and "continue-on-error: true" in block and f"bash {script}" in block and 'cmp -s "$T/before" "$T/after"' in block and "exit 1" in block
              and "app/Madeira/i386-windows/{d3d8,d3d9,d3d9shim,d3d9-emulated,d3d11,dxgi,d3d10core,winemetal,ddraw,wined3d,d3dx9_43}.dll" in block)
    check("the checks run with the toolchain installed (the route check) and on a runner with swiftc (the scan check)",
          idx["Install ninja/meson and fetch llvm-mingw"] < idx[k8] and "check-d3d8-to-d3d9.py" in wf and "check-d3d9-x64-default.py" in wf)
key = re.search(r'echo "key=(i386-farm-[^"]*)" >> "\$GITHUB_OUTPUT"', wf)
check("the i386 farm's cache key is unchanged (the new scripts are outside it)", key is not None
      and key.group(1) == "i386-farm-$(git -C wine rev-parse HEAD)-$(git -C dxmt rev-parse HEAD)-${{ hashFiles('build/wine-i386/build.sh', 'tools/patch-dxmt-*.py', 'tools/patch-wine-*.py') }}")
gi = (root / ".gitignore").read_text()
check(".gitignore keeps the work directories out of git (the DLLs go into the ignored i386 farm)", "build/d3d8to9/" in gi and "build/cnc-ddraw/" in gi and "app/Madeira/i386-windows/*" in gi)
i386 = (root / "build/wine-i386/build.sh").read_text()
check("the i386 farm's build is not touched: it still installs Wine's d3d8.dll and ddraw.dll and names neither new DLL", "d3d8to9" not in i386 and "cnc-ddraw" not in i386)

# --- catalog, policy ---------------------------------------------------------------------------------------------------
cat = (root / "app/Madeira/ConfigCatalog.generated.swift").read_text()
check("the catalog lists env.MADEIRA_D3D8 (d3d9) and env.MADEIRA_DDRAW (cnc) as choices, empty by default",
      re.search(r'key: "env.MADEIRA_D3D8".*kind: \.choice, defaultValue: ""', cat) is not None and re.search(r'key: "env.MADEIRA_D3D8".*\("d3d9", "[^"]*"\)\]', cat) is not None
      and re.search(r'key: "env.MADEIRA_DDRAW".*kind: \.choice, defaultValue: ""', cat) is not None and re.search(r'key: "env.MADEIRA_DDRAW".*\("cnc", "[^"]*"\)\]', cat) is not None)
pol = (root / "app/Madeira/GameRecommendationsList.swift").read_text()
check("a downloaded list may set them, with the one value each and nothing else",
      '"MADEIRA_D3D8", "MADEIRA_DDRAW"' in pol and 'case "MADEIRA_D3D8":' in pol and 'guard value == "d3d9"' in pol and 'case "MADEIRA_DDRAW":' in pol and 'guard value == "cnc"' in pol)

# --- the lists and auto-tune ---------------------------------------------------------------------------------------------
recs = json.loads((root / "recommended/recommendations.json").read_text())
with_d8 = [r["id"] for r in recs["rules"] if "MADEIRA_D3D8" in json.dumps(r["config"])]
# round 624 (agent D2): Red Faction's launcher (8R8V3Z) is the fifth.
check("only the five Direct3D 8 games of the logs carry env.MADEIRA_D3D8 = d3d9, each from build 602 only, after the standard line",
      with_d8 == ["grand-theft-auto-iii", "postal-2", "hotline-miami", "max-payne-2", "red-faction"]
      and all(next(r for r in recs["rules"] if r["id"] == i)["config"][2:] == ["env.MADEIRA_EC_HOOK_TRACE = 0", {"line": "env.MADEIRA_D3D8 = d3d9", "minBuild": 602}] for i in with_d8))
matches = {r["id"]: r["match"] for r in recs["rules"] if r["id"] in with_d8}
check("their matches are the games' Steam App IDs (12100, 223470, 219150, 12150, 20530), install folders and programs",
      matches == {"grand-theft-auto-iii": {"steamAppIDs": [12100], "steamFolders": ["grand theft auto 3"], "programs": [{"name": "gta3.exe"}]},
                  "postal-2": {"steamAppIDs": [223470], "steamFolders": ["postal2complete"], "programs": [{"name": "postal2.exe"}]},
                  "hotline-miami": {"steamAppIDs": [219150], "steamFolders": ["hotline_miami"], "programs": [{"name": "hotlinemiami_original.exe"}]},
                  "max-payne-2": {"steamAppIDs": [12150], "steamFolders": ["max payne 2 the fall of max payne"], "programs": [{"name": "maxpayne2.exe"}]},
                  "red-faction": {"steamAppIDs": [20530], "steamFolders": ["red faction"], "programs": [{"name": "redfaction.exe"}]}})
check("no list sets env.MADEIRA_DDRAW (no log shows a 2D DirectDraw game that fails: the ddraw failures in the logs are probes of D3D9 and D3D11 games)",
      not any("MADEIRA_DDRAW" in json.dumps(r["config"]) for r in recs["rules"]))
working = {"rdr2-steam", "rdr2-other", "gta5e-steam", "gta5e-other", "gta5l-steam", "gta5l-other", "ghost-of-tsushima", "god-of-war", "horizon-zero-dawn",
           "horizon-forbidden-west", "ratchet-clank-rift-apart", "crysis-remastered", "crysis-2-remastered", "crysis-3-remastered", "msfs-2024"}
check("none of the proven lists (GoT, GoW, HZD, HFW, Rift Apart, RDR2, GTA V, Crysis, MSFS) names the new keys", not any(
    k in json.dumps(r["config"]) for r in recs["rules"] if r["id"] in working for k in ("MADEIRA_D3D8", "MADEIRA_DDRAW", "BCD_D3D9_X64_AUTO")))
spec = __import__("importlib.util").util.spec_from_file_location("autotune_rules", root / "build/tools/autotune_rules.py")
mod = __import__("importlib.util").util.module_from_spec(spec)
spec.loader.exec_module(mod)
rule = next(r for r in mod.RULES if r["id"] == "d3d8-to-d3d9")
# round 624 (agent D2): Red Faction's launcher (8R8V3Z) joins with its three lines, 16 in all.
check("auto-tune's rule adds the one line, needs the GL failure and a loaded d3d8.dll, is the five games' alone and stops once the bridge linked (or lacked) the DLL",
      rule["lines"] == ["env.MADEIRA_D3D8 = d3d9"] and rule.get("noRetry") and len(rule["all"]) == 2 and len(rule["any"]) == 16
      and {"has": ["(d3d8.dll-", "Load module"]} in rule["all"] and {"has": ["wined3d_adapter_gl_init Failed to get a GL context"]} in rule["all"]
      and {"has": ["MADEIRA_D3D8=d3d9: d3d8.dll -> d3d8to9.dll"]} in rule["none"] and "noFix" not in rule and "fixedIn" not in rule)
fx = root / "tests/fixtures/autotune"


def signal_hits(text, sig):
    n = sum(1 for l in text.split("\n") if all(h in l for h in sig["has"]))
    return n >= sig.get("count", 1)


def rule_matches(r, text):
    return (all(signal_hits(text, x) for x in r.get("all", [])) and (not r.get("any") or any(signal_hits(text, x) for x in r["any"]))
            and not any(signal_hits(text, x) for x in r.get("none", [])))


for name, want_outcome in (("postal-2-d3d8-2026-10-24", "crashed"), ("hotline-miami-d3d8-2026-10-21", "exited-early"), ("gta-iii-d3d8-2026-10-24", "exited-early"),
                           ("red-faction-d3d8-2026-10-29", "exited-early")):
    expect = json.loads((fx / (name + ".expect")).read_text())
    log = (fx / (name + ".log")).read_text()
    check(f"{name}: the fixture proposes the line ({expect['outcome']}, stage {expect['stage']})",
          expect["matched"] == ["d3d8-to-d3d9"] and expect["next"] == ["env.MADEIRA_D3D8 = d3d9"] and expect["outcome"] == want_outcome and expect["status"] == "trying")
    check(f"{name}: not once the bridge logged the link, not when the DLL was missing, not without the GL failure or the d3d8.dll load, not for another game",
          rule_matches(rule, log)
          and not rule_matches(rule, log + "\n[WineProc] MADEIRA_D3D8=d3d9: d3d8.dll -> d3d8to9.dll (x; syswow64)\n")
          and not rule_matches(rule, log + "\n[WineProc] MADEIRA_D3D8=d3d9 but this build has no d3d8to9.dll -- Wine's d3d8.dll stays\n")
          and not rule_matches(rule, log.replace("wined3d_adapter_gl_init Failed to get a GL context", "ok"))
          and not rule_matches(rule, log.replace("(d3d8.dll-", "(d3d9.dll-"))
          and not rule_matches(rule, re.sub(r"library launch [^:\n]*:|\[session-log\] Steam game [^:\n]*:|Session log: logs/[^ \n]*", "library launch Some Other Game:", log)))
    check(f"{name}: scrubbed (no container path, user name, e-mail, address)",
          not re.search(r"/var/mobile/Containers|/Users/(?!<user>)|[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[a-z]{2,}|\b(?:\d{1,3}\.){3}\d{1,3}\b", log))
others = [p.name[:-7] for p in fx.glob("*.expect") if "d3d8-to-d3d9" in json.loads(p.read_text()).get("matched", [])]
check("no other fixture matches it (Vice City, Hitman, Peggle, the D3D9 and D3D11 games): %s" % others,
      sorted(others) == ["gta-iii-d3d8-2026-10-24", "hotline-miami-d3d8-2026-10-21", "postal-2-d3d8-2026-10-24", "red-faction-d3d8-2026-10-29"])
catalog_ok = '"env.MADEIRA_D3D8"' in (root / "build/tools/gen-config-catalog.py").read_text()
check("the rule's key is in the settings catalog's overlay", catalog_ok)

# Round 602 verification build 603: the CI runs with LC_ALL=en_US.UTF-8, where sort orders the vendored file names differently from the
# C order SHA256SUMS was written in; both build scripts must sort in the C locale.
for _script in ("tools/build-d3d8to9-dll.sh", "tools/build-cnc-ddraw-dll.sh"):
    _t = (ROOT / _script).read_text() if "ROOT" in globals() else open(_script).read()
    check(f"{_script} compares SHA256SUMS in the C sort order", "| LC_ALL=C sort | xargs shasum -a 256 | cmp -s -" in _t)

print("PASS: Direct3D 8 on Direct3D 9 and DirectDraw through cnc-ddraw, opt-in" if ok else "FAILED")
sys.exit(0 if ok else 1)

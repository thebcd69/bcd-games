#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""The kk-retry shim in front of KosmicKrisp (round 668; bcd-games pull request #57 by RageMC; build/mesa-ios/kk-retry).

Under memory pressure iOS ends the Metal compiler service; Metal 4's MTL4Compiler then fails with XPC_ERROR_CONNECTION_INTERRUPTED, KosmicKrisp
hands "Failed to create MTLLibrary" to the game and Enshrouded ends itself. The shim is a small library in front of the real one
(libvulkan_kosmickrisp_real.dylib, re-exported whole): it retries such a compile with a fresh compiler, runs at most four at once, compiles
libraries at the size optimisation level and raises the shader cache cap. Only a game with vulkan = kosmickrisp loads it.

  1. the sources: the contributor's MIT header and BCD's changes marked; the marker the build step reads; the slow-compile dump capped;
     the compile census (a "[kk-retry] compiles" line every 10 s with activity; a compile past 60 s named while it runs);
  2. CI: its own step after the KosmicKrisp cache steps (the cached library stays the real one), a failed run only warns, a failed
     KosmicKrisp build also removes a _real left over; the packaging check knows the shim; the notices credit it;
  3. on a Mac with Xcode: build.sh against a stand-in KosmicKrisp (an arm64 iOS 26 dylib with the two entry points) in a scratch tree:
     the shim re-exports @rpath/libvulkan_kosmickrisp_real.dylib with @loader_path, a second run leaves the same real library, a fresh real
     library replaces an older _real, and a tree without KosmicKrisp is left alone.
"""
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

R = Path(__file__).resolve().parents[2]
K = R / "build/mesa-ios/kk-retry"
ok = True


def check(what, cond, detail=""):
    global ok
    print(("ok   " if cond else "FAIL ") + what + ("" if cond or not detail else "\n     " + str(detail)[:1500]))
    ok = ok and bool(cond)


m = (K / "kk_retry.m").read_text()
b = (K / "build.sh").read_text()
check("kk_retry.m: the contributor's MIT header and pull request, and BCD's changes marked",
      m.startswith("// SPDX-License-Identifier: MIT\n// Copyright 2026 RageMC\n// From bcd-games pull request #57") and "// Modified by BCD (thebcd69), 2026-11-16:" in m)
check("kk_retry.m: the marker the build step reads, kept in the binary",
      '__attribute__((used)) static const char kkr_marker[] = "kk-retry: shim in front of KosmicKrisp";' in m)
check("kk_retry.m: at most eight slow-compile sources saved a run",
      "if (__atomic_fetch_add(&kkr_dumps, 1, __ATOMIC_RELAXED) >= 8) {" in m and m.index("kkr_dumps, 1") < m.index("createDirectoryAtPath"))
check("kk_retry.m: retries only a lost compiler connection, with a fresh compiler, and passes every other error back",
      "XPC_ERROR_CONNECTION_INTERRUPTED" in m and "if (r || !kkr_is_xpc(err) || i == KKR_TRIES) break;" in m and "id fresh = kkr_fresh(self);" in m)
check("kk_retry.m: a shader cache cap the player set is kept (only BCD's default 512M is raised)", 'if (!cs || !strcmp(cs, "512M"))' in m)
check("kk_retry.m: the compile census (2026-11-20, X-Plane 12): BCD's change marked; every compile timed inside the gate; a line every 10 s"
      " only when compiles finished, failed or run; a compile past 60 s named while it runs and its MSL saved under the same cap, not twice",
      "// Modified by BCD (thebcd69), 2026-11-20: a compile census (log only, nothing else changes)" in m
      and "int _slot = kkr_begin(KIND, DESC); KKR_TEST_DELAY id _r = (X);" in m
      and m.index("dispatch_semaphore_wait(kkr_sem, DISPATCH_TIME_FOREVER)") < m.index("int _slot = kkr_begin(KIND, DESC)") < m.index("if (kkr_sem) dispatch_semaphore_signal(kkr_sem); _r; })")
      and "static const double kkr_overdue_s = 60;" in m and "if (any) {" in m
      and "dispatch_source_set_timer(kkr_timer, dispatch_time(DISPATCH_TIME_NOW, 10 * NSEC_PER_SEC), 10 * NSEC_PER_SEC, NSEC_PER_SEC);" in m
      and "kkr_dump(d, late[i].secs, YES);" in m and "if (dt > 10 && !named && [desc isKindOfClass:[MTL4LibraryDescriptor class]]) kkr_dump(desc, dt, NO);" in m
      and 'KKR_BODY(o_rps(self, s, desc, opt, &err), "render pipeline", KKR_RPS)' in m and 'KKR_BODY(o_cps(self, s, desc, opt, &err), "compute pipeline", KKR_CPS)' in m)
t = (K / "test_mac.m").read_text()
check("test_mac.m: the census cases (quiet when idle, a running compile named, the finished one counted); its dumps go to the temporary folder",
      'printf("census when idle' in t and "in flight 1 (oldest " in t and 'strstr(line, "library 1 (avg ")' in t
      and "NSTemporaryDirectory()" in m)
check("build.sh: the contributor's header and BCD's changes; the shim told by its marker; made in a temporary folder, moved in only when linked",
      b.startswith("#!/bin/bash\n# SPDX-License-Identifier: MIT\n# Copyright 2026 RageMC\n") and "# Modified by BCD (thebcd69), 2026-11-16:" in b
      and 'MARK="kk-retry: shim in front of KosmicKrisp"' in b and 'if grep -q "$MARK" "$LIB"; then' in b
      and b.index('-o libvulkan_kosmickrisp.dylib') < b.index('mv "$REAL.tmp" "$REAL"') < b.index('mv "$LIB.tmp" "$LIB"'))
ci = (R / ".github/workflows/build-ipa.yml").read_text()
step = "      - name: Put the kk-retry shim in front of KosmicKrisp\n        continue-on-error: true\n        run: bash build/mesa-ios/kk-retry/build.sh\n"
check("CI: the shim's own step comes after the KosmicKrisp cache save and the stale-library drop, and only warns when it fails",
      step in ci and ci.index("      - name: Save KosmicKrisp\n") < ci.index("      - name: Drop a stale KosmicKrisp after a failed build\n") < ci.index(step))
check("CI: a failed KosmicKrisp build also drops a _real library an earlier run left", "          rm -f app/Madeira/gl/libvulkan_kosmickrisp_real.dylib\n" in ci)
check("CI runs this check", "run: python3 tests/host/check-kk-retry.py" in ci)
shipped = (R / "tools/check-vulkan-shipped.py").read_text()
check("the packaging check: the shim needs its _real library and has to re-export it; a _real without the shim is refused",
      "is the kk-retry shim, but libvulkan_kosmickrisp_real.dylib is missing" in shipped and "is not the shim that loads it" in shipped
      and "LC_REEXPORT_DYLIB" in shipped and "path @loader_path" in shipped)
notices = (R / "THIRD-PARTY-NOTICES.md").read_text()
bundled = (R / "app/Madeira/licenses/THIRD-PARTY-NOTICES.txt").read_text()
check("THIRD-PARTY-NOTICES names the three files, the contributor and the licence; the bundled notice too",
      "build/mesa-ios/kk-retry" in notices and "RageMC" in notices and "RageMC" in bundled and "kk-retry" in bundled)

if sys.platform == "darwin" and shutil.which("xcrun") and subprocess.run(["xcrun", "-sdk", "iphoneos", "--show-sdk-path"], capture_output=True).returncode == 0:
    with tempfile.TemporaryDirectory(prefix="bcd-kkr-") as t:
        t = Path(t)
        (t / "app/Madeira/gl").mkdir(parents=True)
        (t / "build/mesa-ios").mkdir(parents=True)
        shutil.copytree(K, t / "build/mesa-ios/kk-retry")
        gl = t / "app/Madeira/gl"

        def stand_in(tag):
            src = t / "kk.c"
            src.write_text('const char *bcd_tag = "%s";\nvoid *vkGetInstanceProcAddr(void *i, const char *n) { return 0; }\n'
                           'void *vkGetDeviceProcAddr(void *d, const char *n) { return 0; }\n' % tag)
            r = subprocess.run(["xcrun", "-sdk", "iphoneos", "clang", "-arch", "arm64", "-mios-version-min=26.0", "-dynamiclib", str(src),
                                "-install_name", "@rpath/libvulkan_kosmickrisp.dylib", "-o", str(gl / "libvulkan_kosmickrisp.dylib")],
                               capture_output=True, text=True)
            return r.returncode == 0, r.stderr

        def run():
            return subprocess.run(["bash", str(t / "build/mesa-ios/kk-retry/build.sh")], capture_output=True, text=True)

        built, err = stand_in("first")
        check("a stand-in KosmicKrisp builds (arm64, iOS 26)", built, err)
        if built:
            r = run()
            lib, real = gl / "libvulkan_kosmickrisp.dylib", gl / "libvulkan_kosmickrisp_real.dylib"
            check("build.sh puts the shim in front of it", r.returncode == 0 and real.is_file()
                  and b"kk-retry: shim in front of KosmicKrisp" in lib.read_bytes() and b"first" in real.read_bytes(), r.stdout + r.stderr)
            cmds = subprocess.run(["xcrun", "otool", "-l", str(lib)], capture_output=True, text=True).stdout
            idreal = subprocess.run(["xcrun", "otool", "-l", str(real)], capture_output=True, text=True).stdout
            check("the shim re-exports @rpath/libvulkan_kosmickrisp_real.dylib, finds it with @loader_path and keeps the library's own name",
                  "LC_REEXPORT_DYLIB" in cmds and "name @rpath/libvulkan_kosmickrisp_real.dylib" in cmds and "path @loader_path" in cmds
                  and "name @rpath/libvulkan_kosmickrisp.dylib" in cmds and "name @rpath/libvulkan_kosmickrisp_real.dylib" in idreal)
            ver = subprocess.run(["xcrun", "vtool", "-show-build", str(lib)], capture_output=True, text=True).stdout.split()
            check("the shim is for iOS 26.0, like the library", "minos" in ver and ver[ver.index("minos") + 1] == "26.0")
            before = real.read_bytes()
            r = run()
            check("a second run (a kept tree) remakes only the shim over the same real library", r.returncode == 0 and real.read_bytes() == before
                  and b"kk-retry: shim in front of KosmicKrisp" in lib.read_bytes(), r.stdout + r.stderr)
            built, err = stand_in("second")
            r = run()
            check("a freshly built KosmicKrisp replaces the older _real", built and r.returncode == 0 and b"second" in real.read_bytes()
                  and b"first" not in real.read_bytes(), r.stdout + r.stderr)
            lib.unlink()
            r = run()
            check("a tree without KosmicKrisp is left alone (and a stray _real is removed)", r.returncode == 0 and not lib.exists() and not real.exists(),
                  r.stdout + r.stderr)
else:
    print("skip the build runs need macOS with Xcode's iOS SDK")

print("PASS: kk-retry shim" if ok else "FAILED: kk-retry shim")
sys.exit(0 if ok else 1)

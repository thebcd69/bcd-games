#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""0.1.682's launch crash on iOS 26 and older (2026-11-20): pipe2() from the iOS 27 SDK; 0.1.712's on an iOS 27.2 beta.

The crash reports (iPadOS 26.6.2, iPad Air M1, SideStore): SIGSEGV at address 0 in server_pipe <- init_thread_pipe <-
server_init_process <- __wine_main, on every game launch. Wine's configure, run against the iOS 27 SDK, defined HAVE_PIPE2; pipe2()
exists only from iOS 27, so the app imported it weakly and called NULL on older systems.

  1. CI undefines HAVE_PIPE2 in the iOS config.h (with the iOS SDK fixups), and both server_ios.c and process_ios.c only call
     pipe2() under HAVE_PIPE2;
  2. tools/check-weak-libsystem.py: the dyld_info parser on fixture lines (pipe2 caught, the known names and Swift/UIKit weak
     imports let through); CI runs it on the archived app before packaging;
  3. on a Mac with the release IPAs at hand (~/madeira-local/state/release): 0.1.665 passes, 0.1.682 fails on pipe2.

0.1.712 crashed at launch on iOS 27.2 beta 24B5089g (2026-11-24): the back-deploy thunk of Foundation's Date <= asks for iOS 27.2,
the beta reports 27.2 without the symbol, and the weak import is NULL (pc=0 in SteamSignIn.usable for a signed-in Steam account).
  4. no Date <= or >= between two Dates in the app sources that 0.1.712's thunks came from; they compare
     timeIntervalSinceReferenceDate, which is the thunk's own fallback;
  5. the tool's nm parser on fixture lines (Twb thunks only, not the TwB fallbacks), with only ThrowingTaskGroup.next known;
  6. on this Mac with 0.1.712 at hand: fails on the two Date thunks, and only on them.
"""
import importlib.util
import os
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

R = Path(__file__).resolve().parents[2]
ok = True


def check(what, cond, detail=""):
    global ok
    print(("ok   " if cond else "FAIL ") + what + ("" if cond or not detail else "\n     " + str(detail)[:1500]))
    ok = ok and bool(cond)


ci = (R / ".github/workflows/build-ipa.yml").read_text()
fix = ci.index('echo "/* iOS SDK fixups appended by CI */"')
check("CI: HAVE_PIPE2 undefined among the iOS config.h fixups, with the reason",
      'echo "#undef HAVE_PIPE2"' in ci and fix < ci.index('echo "#undef HAVE_PIPE2"') < ci.index('} >> "$CFG"', fix)
      and "every game" in ci[fix:ci.index('echo "#undef HAVE_PIPE2"')])
for f in ("build/ntdll-unix/server_ios.c", "build/ntdll-unix/process_ios.c"):
    src = (R / f).read_text()
    calls = [i for i in range(len(src)) if src.startswith("pipe2(", i)]
    guarded = all(src.rfind("#ifdef HAVE_PIPE2", 0, i) > src.rfind("#endif", 0, i) for i in calls)
    check(f"{f}: every pipe2() call sits under #ifdef HAVE_PIPE2", calls and guarded)
step = ("      - name: Check the app for weak C imports newer than its oldest iOS\n"
        "        run: python3 tools/check-weak-libsystem.py build-out/Madeira.xcarchive/Products/Applications/Madeira.app\n")
check("CI: the weak-import check runs on the archived app before packaging",
      step in ci and ci.index(step) < ci.index("      - name: Package Madeira and Madeira2 unsigned IPAs"))
check("CI runs this check", "run: python3 tests/host/check-weak-libsystem.py" in ci)

spec = importlib.util.spec_from_file_location("cwl", R / "tools/check-weak-libsystem.py")
cwl = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cwl)
fixture = """/x/Madeira [arm64]:
    -imports:
      0x0601  _pipe2 [weak-import] (from libSystem)
      0x0602  __availability_version_check [weak-import] (from libSystem)
      0x0603  _os_sync_wake_by_address_any [weak-import] (from libSystem)
      0x0604  _$s10Foundation4DateV2geoiySbAC_ACtFZ [weak-import] (from Foundation)
      0x0605  _OBJC_CLASS_$_UIHingeInteraction [weak-import] (from UIKit)
      0x0606  _pipe (from libSystem)
      0x0607  __ZNSt3__1newthing [weak-import] (from libc++)
            _pthread_mutexattr_init [weak-import] (from libSystem)
"""
got = cwl.weak_c_imports(fixture)
names = [s for s, _ in got]
check("the parser: weak C imports from libSystem and libc++ only, with or without an ordinal column",
      names == ["_pipe2", "__availability_version_check", "_os_sync_wake_by_address_any", "__ZNSt3__1newthing",
                "_pthread_mutexattr_init"], got)
check("the known list lets the compiler's check, os_sync and Mesa's pthread names through, not pipe2",
      [n for n in names if n not in cwl.KNOWN] == ["_pipe2", "__ZNSt3__1newthing"])

sites = {
    "app/Madeira/SwiftSteam/SteamSignIn.swift": "expiry.timeIntervalSinceReferenceDate <= now.timeIntervalSinceReferenceDate",
    "app/Madeira/BCDSessionCore.swift": "t >= from.timeIntervalSinceReferenceDate && t <= logEnd.addingTimeInterval(slack).timeIntervalSinceReferenceDate",
    "app/Madeira/BCDEpic/BCDEpicAuth.swift": "now.addingTimeInterval(600).timeIntervalSinceReferenceDate >= accessExpires.timeIntervalSinceReferenceDate",
    "app/Madeira/BCDGOG/BCDGOGAuth.swift": "now.addingTimeInterval(300).timeIntervalSinceReferenceDate >= expires.timeIntervalSinceReferenceDate",
    "app/Madeira/StikJITHelper.swift": "Date().timeIntervalSinceReferenceDate >= deadline.timeIntervalSinceReferenceDate",
    "app/Madeira/GameRecommendationsSource.swift": "now.timeIntervalSinceReferenceDate >= lastChecked.timeIntervalSinceReferenceDate",
    "app/Madeira/SwiftItch/ItchInstall.swift": "made.timeIntervalSinceReferenceDate >= since.addingTimeInterval(-60).timeIntervalSinceReferenceDate",
}
for f, line in sites.items():
    check(f"{f}: compares the Dates' timeIntervalSinceReferenceDate, not Date <= or >=", line in (R / f).read_text())
nm = """0000000100945300 t _$sScg4next9isolationxSgScA_pSgYi_tYaKFTwb
0000000100945400 t _$sScg4next9isolationxSgScA_pSgYi_tYaKFTwB
000000010097aea8 t _$s10Foundation4DateV2geoiySbAC_ACtFZTwb
0000000100001000 T _main
"""
th = cwl.backdeploy_thunks(nm)
check("the nm parser: Twb thunks only", th == ["_$sScg4next9isolationxSgScA_pSgYi_tYaKFTwb", "_$s10Foundation4DateV2geoiySbAC_ACtFZTwb"], th)
check("the known list lets ThrowingTaskGroup.next through, not Date >=",
      [n for n in th if n not in cwl.KNOWN_BACKDEPLOY] == ["_$s10Foundation4DateV2geoiySbAC_ACtFZTwb"])

rel = Path.home() / "madeira-local/state/release"
def run_on(ipa):
    with tempfile.TemporaryDirectory(prefix="bcd-weak-") as t:
        with zipfile.ZipFile(ipa) as z:
            members = [n for n in z.namelist() if not n.endswith("/") and (n.count("/") == 2 or "/gl/" in n or "/Frameworks/" in n
                                                                          or ".appex/" in n)]
            z.extractall(t, members)
        app = next(Path(t, "Payload").glob("*.app"))
        return subprocess.run([sys.executable, str(R / "tools/check-weak-libsystem.py"), str(app)], capture_output=True, text=True)


if sys.platform == "darwin" and (rel / "BCD-0.1.665.ipa").is_file() and (rel / "BCD-0.1.682.ipa").is_file():
    a = run_on(rel / "BCD-0.1.665.ipa")
    check("on the released 0.1.665 (iOS 26.2 SDK): passes", a.returncode == 0, a.stdout + a.stderr)
    b = run_on(rel / "BCD-0.1.682.ipa")
    check("on the released 0.1.682 (iOS 27.2 SDK): fails on pipe2 in Madeira, and only on it", b.returncode == 1
          and b.stdout.count("::error::weak C import") == 1 and "Madeira: _pipe2 (from libSystem)" in b.stdout, b.stdout + b.stderr)
else:
    print("skip the release IPAs are not on this machine")
if sys.platform == "darwin" and (rel / "BCD-0.1.712.ipa").is_file():
    c = run_on(rel / "BCD-0.1.712.ipa")
    check("on the released 0.1.712: fails on the Date <= and >= thunks in Madeira, and only on them", c.returncode == 1
          and c.stdout.count("::error::") == 2 and "Madeira: _$s10Foundation4DateV2leoiySbAC_ACtFZTwb" in c.stdout
          and "Madeira: _$s10Foundation4DateV2geoiySbAC_ACtFZTwb" in c.stdout, c.stdout + c.stderr)
else:
    print("skip 0.1.712 is not on this machine")

print("PASS: weak C imports" if ok else "FAILED: weak C imports")
sys.exit(0 if ok else 1)

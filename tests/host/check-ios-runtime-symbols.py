#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""The app against older iOS runtimes (2026-11-20; after 0.1.682's pipe2 crash on iOS 26).

tools/check-ios-runtime-symbols.py checks every system import of the app's Mach-O files against the dyld shared caches of older
iOS simulator runtimes, read by tools/dyld_cache_exports.py.

  1. CI: the step runs on the archived app after the weak C import check, before packaging, with --auto;
  2. the cache reader: the export trie walk on a hand-made trie (names at every terminal node, nested edges);
  3. the install-path names dyld_info prints ("from libSystem", "from SwiftUI");
  4. on a Mac with an older iOS runtime installed and the release IPAs at hand: 0.1.665 and 0.1.682 have no error (the simulator
     exports pipe2, so 0.1.682's crash is caught by the two other checks, not this one).
"""
import importlib.util
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

R = Path(__file__).resolve().parents[2]
ok = True


def check(what, cond, detail=""):
    global ok
    print(("ok   " if cond else "FAIL ") + what + ("" if cond or not detail else "\n     " + str(detail)[:2000]))
    ok = ok and bool(cond)


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(path.parent))
    spec.loader.exec_module(m)
    return m


ci = (R / ".github/workflows/build-ipa.yml").read_text()
step = ("      - name: Check the app against older iOS runtimes\n"
        "        run: python3 tools/check-ios-runtime-symbols.py build-out/Madeira.xcarchive/Products/Applications/Madeira.app --auto\n")
check("CI: the runtime check runs on the archived app after the weak C import check, before packaging",
      step in ci and ci.index("      - name: Check the app for weak C imports newer than its oldest iOS\n") < ci.index(step)
      < ci.index("      - name: Package Madeira and Madeira2 unsigned IPAs"))
check("CI runs this check", "run: python3 tests/host/check-ios-runtime-symbols.py" in ci)

dce = load("dyld_cache_exports", R / "tools/dyld_cache_exports.py")


# A trie with _a, _b and _bc: root(0 terminal, 2 edges) -> "_a" node A (terminal), "_b" node B (terminal, 1 edge "c" -> node C).
root = b"\x00\x02" + b"_a\x00" + b"\x0a" + b"_b\x00" + b"\x0e"     # 10 bytes: nodes start at 10
node_a = b"\x02\x00\x00\x00"                                          # at 10: terminal size 2 (flags 0, address 0), no children
node_b = b"\x02\x00\x00\x01" + b"c\x00" + b"\x15"                     # at 14: terminal, 1 edge "c" -> 21
node_c = b"\x02\x00\x00\x00"                                          # at 21
trie = root + node_a + node_b + node_c
assert len(root) == 10 and len(root + node_a) == 14 and len(root + node_a + node_b) == 21
src = (R / "tools/dyld_cache_exports.py").read_text()
start = src.index("            stack = [(0, b\"\")]")
loop = src[start:src.index("        self._cache[install] = (frozenset(names), tuple(reexports))")]
ns = {"uleb": dce.uleb, "data": trie, "names": set()}
exec("def _f(data, names):\n" + "\n".join(l[8:] for l in loop.splitlines()) + "\n    return names\n", ns)
got = ns["_f"](trie, set())
check("the cache reader's trie walk finds every terminal name, through nested edges", got == {"_a", "_b", "_bc"}, got)

crs = load("crs", R / "tools/check-ios-runtime-symbols.py")
check("install paths map to the names dyld_info prints",
      [crs.leaf(p) for p in ("/usr/lib/libSystem.B.dylib", "/usr/lib/libc++.1.dylib", "/usr/lib/libobjc.A.dylib",
                             "/System/Library/Frameworks/SwiftUI.framework/SwiftUI", "/usr/lib/swift/libswift_Concurrency.dylib",
                             "/System/Library/Frameworks/IOKit.framework/Versions/A/IOKit")]
      == ["libSystem", "libc++", "libobjc", "SwiftUI", "libswift_Concurrency", "IOKit"])

rel = Path.home() / "madeira-local/state/release"
older = [d for v, d in crs.installed_runtimes() if crs.sdk_version() and v < crs.sdk_version()] if sys.platform == "darwin" else []
if older and (rel / "BCD-0.1.665.ipa").is_file() and (rel / "BCD-0.1.682.ipa").is_file():
    def run_on(ipa):
        with tempfile.TemporaryDirectory(prefix="bcd-rts-") as t:
            with zipfile.ZipFile(ipa) as z:
                z.extractall(t, [n for n in z.namelist() if not n.endswith("/") and not n.lower().endswith((".dll", ".exe", ".png"))])
            app = next(Path(t, "Payload").glob("*.app"))
            return subprocess.run([sys.executable, str(R / "tools/check-ios-runtime-symbols.py"), str(app), "--auto"],
                                  capture_output=True, text=True)
    a = run_on(rel / "BCD-0.1.665.ipa")
    check("0.1.665 against the older runtimes: no error", a.returncode == 0 and "::error::" not in a.stdout, a.stdout + a.stderr)
    b = run_on(rel / "BCD-0.1.682.ipa")
    # The simulator's libsystem_kernel has pipe2 (a device on iPadOS 26.6.2 does not): this check cannot see 0.1.682's crash, and
    # says so in its header; check-weak-libsystem.py and check-availability-warnings.py catch it.
    check("0.1.682 against the older runtimes: no strong or framework error either (the pipe2 crash is outside what a simulator shows)",
          b.returncode == 0 and "::error::" not in b.stdout, b.stdout + b.stderr)
else:
    print("skip no older iOS simulator runtime or no release IPAs on this machine")

print("PASS: older iOS runtimes" if ok else "FAILED: older iOS runtimes")
sys.exit(0 if ok else 1)

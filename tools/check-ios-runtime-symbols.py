#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""Does the built app find every system symbol it imports on an older iOS? (2026-11-20)

BCD 0.1.682, the first build with the iOS 27 SDK, imported pipe2() (iOS 27 only) and crashed every game launch on iOS 26. This
checks every Mach-O of an app against the system libraries of older iOS versions, read from the dyld shared caches of their
simulator runtimes (tools/dyld_cache_exports.py; ~/madeira-local/ios-runtimes/caches.txt lists the cache folders):

  check-ios-runtime-symbols.py <Madeira.app> <baseline cache dir> <older cache dir> [<older cache dir> ...]
  check-ios-runtime-symbols.py <Madeira.app> --auto     the installed iOS simulator runtimes: the SDK's own as the baseline, every
                                                       older one as a target (a warning and exit 0 when no older one is installed)

The baseline is the runtime of the SDK the app was built with (iOS 27.2): an import the baseline simulator lacks too is a
simulator/device difference, not a version one, and is left out. For each older cache, an import the baseline has and it lacks:

  strong import             error: dyld refuses to load the binary (the app at launch, a dylib at dlopen)
  weak C import             error, unless KNOWN_GUARDED: libSystem/libc++/libobjc resolve to NULL and C callers do not check
  weak Swift/ObjC import    note only: the compiler guards those (availability checks, @backDeployed)
  a system library missing  error when linked strongly
Flat-namespace imports (some Mesa/Vulkan dylibs) are looked up in all the system libraries that binary links.

Limit: the simulator's lowest layer is not the device's. The iOS 26.4 simulator's libsystem_kernel exports pipe2, an iPadOS 26.6.2
device does not, so this check does NOT catch 0.1.682's crash; tools/check-weak-libsystem.py and tools/check-availability-warnings.py
do. This one covers frameworks and libraries a newer SDK links or uses (a whole framework, Swift and ObjC symbols, strong imports).
"""
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from dyld_cache_exports import DyldCache   # noqa: E402

C_LIBS = ("libSystem", "libc++", "libobjc")
KNOWN_GUARDED = {"__availability_version_check"}   # the compiler's own __builtin_available helper
IMPORT = re.compile(r"^\s*(?:0x[0-9A-Fa-f]+\s+)?(\S+)(\s+\[weak-import\])?\s+\(from ([^)]+)\)")


def run(*cmd):
    return subprocess.run(cmd, capture_output=True, text=True).stdout


def is_macho(p):
    try:
        with open(p, "rb") as f:
            return f.read(4) in (b"\xcf\xfa\xed\xfe", b"\xca\xfe\xba\xbe")
    except OSError:
        return False


def leaf(install):
    """The name dyld_info prints after "from" for an install path."""
    base = install.rsplit("/", 1)[-1]
    return base.split(".")[0] if base.startswith("lib") else base


def linked(binary):
    """[(install path, weak)] of one Mach-O."""
    out = []
    for line in run("xcrun", "dyld_info", "-no_validate", "-linked_dylibs", str(binary)).splitlines()[1:]:
        parts = line.split()
        if parts and parts[-1].startswith(("/", "@")):
            out.append((parts[-1], "weak" in parts[:-1]))
    return out


def imports(binary):
    """[(symbol, weak, from-leaf)] of one Mach-O."""
    out = []
    for line in run("xcrun", "dyld_info", "-no_validate", "-imports", str(binary)).splitlines():
        m = IMPORT.match(line)
        if m:
            out.append((m.group(1), bool(m.group(2)), m.group(3)))
    return out


def flat(path):
    return re.sub(r"/Versions/[^/]+/", "/", path)   # iOS frameworks are flat


def lib_exports(cache, path):
    e = cache.exports(path)
    return e if e is not None else cache.exports(flat(path))


def check(app, base, older):
    errors, notes, skipped = [], [], 0
    files = sorted(p for p in Path(app).rglob("*") if p.is_file() and not p.is_symlink() and is_macho(p))
    for f in files:
        rel = f.relative_to(app)
        libs = {}
        for path, weak in linked(f):
            if path.startswith(("/usr/lib/", "/System/")):
                libs[leaf(path)] = (path, weak)
                if lib_exports(base, path) is not None and lib_exports(older, path) is None and not weak:
                    errors.append(f"{rel}: links {path}, which this iOS does not have")
        all_b = set().union(*[lib_exports(base, p) or set() for p, _ in libs.values()]) if libs else set()
        all_o = set().union(*[lib_exports(older, p) or set() for p, _ in libs.values()]) if libs else set()
        for sym, weak, frm in imports(f):
            if frm == "<flat-namespace>":
                b, o, lib_weak, cname = all_b, all_o, False, "flat namespace"
            elif frm in libs:
                path, lib_weak = libs[frm]
                b, o, cname = lib_exports(base, path), lib_exports(older, path), frm
            else:
                continue          # a library inside the app (@rpath) or a coalesced weak definition
            if b is None or sym not in b:
                skipped += 1      # not in the simulator's library at all: a device-only symbol, not a version difference
                continue
            if o is not None and sym in o:
                continue
            what = f"{rel}: {sym} (from {cname})"
            if not weak and not lib_weak:
                errors.append(what + " is a strong import: the binary does not load")
            elif cname in C_LIBS and sym not in KNOWN_GUARDED:
                errors.append(what + " is a weak C import: NULL here, and a call crashes")
            else:
                notes.append(what)
    return files, errors, notes, skipped


def installed_runtimes():
    """[(version tuple, cache dir)] of the ready iOS simulator runtimes."""
    import json
    try:
        data = json.loads(run("xcrun", "simctl", "runtime", "list", "-j") or "{}")
    except ValueError:
        return []
    out = []
    for v in data.values():
        b = v.get("runtimeBundlePath")
        if v.get("platformIdentifier") != "com.apple.platform.iphonesimulator" or v.get("state") != "Ready" or not b:
            continue
        d = Path(b) / "Contents/Resources/RuntimeRoot/System/Library/Caches/com.apple.dyld"
        if not d.is_dir():
            # Runtimes without a shipped cache: CoreSimulator builds it on the Mac, per host OS build and runtime identifier.
            ident = f'{v.get("runtimeIdentifier", "")}.{v.get("build", "")}'
            found = sorted(Path("/Library/Developer/CoreSimulator/Caches/dyld").glob(f"*/{ident}")) if v.get("runtimeIdentifier") else []
            found = [f for f in found if (f / "dyld_sim_shared_cache_arm64").is_file()]
            d = found[-1] if found else None
        if d:
            out.append((tuple(int(x) for x in v["version"].split(".")), str(d)))
    return sorted(set(out))


def sdk_version():
    v = run("xcrun", "--sdk", "iphoneos", "--show-sdk-version").strip()
    return tuple(int(x) for x in v.split(".")) if v else None


def main():
    if len(sys.argv) == 3 and sys.argv[2] == "--auto":
        rts, sdk = installed_runtimes(), sdk_version()
        base = [d for v, d in rts if v == sdk] or [d for v, d in rts if sdk and v[:1] == sdk[:1]]
        older = [d for v, d in rts if sdk and v < sdk]
        if not base or not older:
            print(f"::warning::check-ios-runtime-symbols: no {'baseline' if not base else 'older'} iOS simulator runtime installed "
                  f"(SDK {sdk}; installed {[v for v, _ in rts]}); nothing checked")
            return 0
        argv = [sys.argv[1], base[-1]] + older
    elif len(sys.argv) >= 4:
        argv = sys.argv[1:]
    else:
        sys.exit(__doc__)
    app, base = Path(argv[0]), DyldCache(argv[1])
    bad = 0
    for d in argv[2:]:
        name = next((p for p in Path(d).parts if p.endswith(".simruntime")), d)
        files, errors, notes, skipped = check(app, base, DyldCache(d))
        print(f"== {name}: {len(files)} Mach-O files, {len(errors)} error(s), {len(notes)} guarded weak import(s) missing there, "
              f"{skipped} import(s) not in the baseline simulator (left out)")
        for e in errors:
            print(f"::error::{name}: {e}")
        for n in notes[:40]:
            print(f"   note {n}")
        if len(notes) > 40:
            print(f"   note ... {len(notes) - 40} more")
        bad += len(errors)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())

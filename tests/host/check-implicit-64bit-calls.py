#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""A function that returns 64 bits must be declared where the unix side calls it.

build/ntdll-unix/build.sh compiles with -Wno-implicit-function-declaration (Wine's own files and the iPhone SDK need it), so a call to a
function no header declared compiles as a call that returns int: on arm64 the compiler keeps the low 32 bits and sign-extends them
(`bl _mach_absolute_time; sxtw x0, w0`). For the Mach calls that return kern_return_t that is the right answer. For mach_absolute_time
it is not: build/ntdll-unix/signal_arm64_ios.c called it without a declaration (engine review 2026-11-17), so the Mach exception thread's
tick counts were the low 32 bits, sign-extended. Bit 31 of the count flips every 89.5 s at the 24 MHz timebase: every 89.5 s one
[fault-cost] sample was dropped as implausible and the [perf-hint] rate (three reports in a row at 20,000 emulated stores a second) saw
the time jump and started counting again.

Checks:
  - signal_arm64_ios.c declares mach_absolute_time at file scope before its first call;
  - on the host: an undeclared call really returns the low 32 bits sign-extended, a declared one the whole count (skipped when there is no
    C compiler);
  - with a configured Wine tree and the iPhone SDK (the workflow has both by the time ntdll-unix is built; --require-wine makes their
    absence a failure): every replacement file of build/ntdll-unix compiles as build.sh compiles it, and every function it calls without
    a declaration is one of the known ones that return int or nothing (ALLOWED below). A new name fails the check: declare it, or, when
    it does return int or void, add it to the list.
"""
from pathlib import Path
import os
import re
import shutil
import subprocess
import sys
import tempfile

R = Path(__file__).resolve().parents[2]
ok = True


def check(what, cond, detail=""):
    global ok
    print(("ok   " if cond else "FAIL ") + what + (("\n       " + detail) if detail and not cond else ""))
    ok = ok and bool(cond)


# Called without a declaration today; each returns int (kern_return_t, NTSTATUS, Boolean) or nothing, or its result is not used.
ALLOWED = {
    "mach_vm_region_recurse", "mach_vm_deallocate", "mach_vm_map", "mach_vm_allocate", "mach_vm_remap", "mach_vm_purgable_control",
    "mach_vm_read_overwrite", "bootstrap_look_up",                      # kern_return_t
    "sys_icache_invalidate", "unix_init_startup_info",                  # void
    "ios_tail_carve_lookup_trylock",                                    # int (virtual_ios.c)
    "NtWaitForSingleObject", "NtClose",                                 # NTSTATUS
    "MPTaskIsPreemptive", "MPCurrentTaskID", "CFNotificationCenterGetDistributedCenter",   # Wine's own main-thread code; results unused
}
FILES = ["virtual_ios.c", "signal_arm64_ios.c", "thread_ios.c", "server_ios.c", "loader_ios.c", "process_ios.c", "env_ios.c",
         "audio_null_ios.c"]

# ------------------------------------------------------------------ 1. the declaration
src = (R / "build/ntdll-unix/signal_arm64_ios.c").read_text()
decl = src.find("\nextern uint64_t mach_absolute_time( void );\n")
first_call = re.search(r"=\s*mach_absolute_time\(\)", src)
check("signal_arm64_ios.c declares mach_absolute_time at file scope before its first call",
      decl >= 0 and first_call is not None and decl < first_call.start())
check("no other declaration of it in the file (a second one with another type would not compile)",
      len(re.findall(r"\bmach_absolute_time\s*\(\s*void\s*\)", src)) == 1)

# ------------------------------------------------------------------ 2. what an undeclared call returns
cc = shutil.which("cc") or shutil.which("clang") or shutil.which("gcc")
if not cc:
    print("SKIP the host demonstration: no C compiler")
else:
    with tempfile.TemporaryDirectory() as tmp:
        a, b, exe = os.path.join(tmp, "a.c"), os.path.join(tmp, "b.c"), os.path.join(tmp, "t")
        # a stand-in with a fixed 64-bit answer, so the demonstration does not depend on the machine's clock
        open(a, "w").write("unsigned long long undeclared( void ) { return ticks64(); }\n")
        open(b, "w").write("#include <stdio.h>\n#include <stdint.h>\n"
                           "uint64_t ticks64( void ) { return 0x00001ce288ae455eull; }\n"
                           "unsigned long long undeclared( void );\n"
                           "int main( void ) { printf( \"%llx %llx\\n\", (unsigned long long)ticks64(), undeclared() ); return 0; }\n")
        r = subprocess.run([cc, "-std=gnu99", "-O2", "-Wno-implicit-function-declaration", "-Wno-error", a, b, "-o", exe],
                           capture_output=True, text=True)
        if r.returncode:
            print("SKIP the host demonstration: this compiler refuses an implicit declaration outright")
        else:
            out = subprocess.run([exe], capture_output=True, text=True).stdout.split()
            check("an undeclared call returns the low 32 bits sign-extended (1ce288ae455e -> ffffffff88ae455e), a declared one the whole value",
                  out == ["1ce288ae455e", "ffffffff88ae455e"], " ".join(out))

# ------------------------------------------------------------------ 3. every undeclared call of the unix side is a known int/void one
require = "--require-wine" in sys.argv
wine = None
for cand in (os.environ.get("WINE_SRC"), str(R / "wine"), str(Path.home() / "madeira-bcd/wine")):
    if cand and (Path(cand) / "build-macos/include/config.h").exists():
        wine = Path(cand)
        break
sdk = subprocess.run(["xcrun", "--sdk", "iphoneos", "--show-sdk-path"], capture_output=True, text=True)
if not wine or sdk.returncode:
    if require:
        check("a configured Wine tree (WINE_SRC with build-macos/include/config.h) and the iPhone SDK are there (--require-wine)", False)
    else:
        print("SKIP the compile: no configured Wine tree or no iPhone SDK (the workflow runs it with --require-wine)")
else:
    b = R / "build/ntdll-unix"
    wb = wine / "build-macos"
    base = ["xcrun", "-sdk", "iphoneos", "clang", "-arch", "arm64", "-isysroot", sdk.stdout.strip(), "-miphoneos-version-min=17.0",
            "-O2", "-fPIC", "-fvisibility=hidden", "-fno-stack-protector", "-fno-strict-aliasing",
            "-Wno-int-conversion", "-Wimplicit-function-declaration", "-Wno-error=implicit-function-declaration",
            "-include", str(wb / "include/config.h"), "-include", str(b / "shims/wine_ios_exit.h"),
            "-I" + str(b / "shims"), "-I" + str(R / "build/madsync"), "-DHAVE_LINUX_NTSYNC_H=1",
            "-I" + str(wb / "dlls/ntdll"), "-I" + str(wine / "dlls/ntdll"), "-I" + str(wine / "dlls/ntdll/unix"),
            "-I" + str(wb / "include"), "-I" + str(wine / "include"),
            "-D__WINESRC__", "-DLTC_NO_PROTOTYPES", "-DLTC_SOURCE", "-D_NTSYSTEM_", "-D_ACRTIMP=", "-DWINBASEAPI=",
            '-DBINDIR="/usr/local/bin"', '-DLIBDIR="/usr/local/lib"', '-DDATADIR="/usr/local/share"', '-DSYSTEMDLLPATH=""',
            "-DWINE_UNIX_LIB", "-DWINE_IOS=1", "-Dget_thread_context=ntdll_get_thread_context",
            "-Dset_thread_context=ntdll_set_thread_context", "-fsyntax-only"]
    seen = set()
    for name in FILES:
        r = subprocess.run(base + [str(b / name)], capture_output=True, text=True)
        errors = [l for l in r.stderr.splitlines() if " error: " in l]
        undeclared = set(re.findall(r"call to undeclared function '([A-Za-z0-9_]+)'", r.stderr))
        seen |= undeclared
        new = sorted(undeclared - ALLOWED)
        check(f"{name}: compiles, and calls nothing undeclared beyond the known int/void functions", not errors and not new,
              "; ".join(errors[:2]) or "undeclared: " + ", ".join(new))
    check("mach_absolute_time is declared wherever the unix side calls it", "mach_absolute_time" not in seen)

print("PASS: implicit 64-bit calls" if ok else "FAILED: implicit 64-bit calls")
sys.exit(0 if ok else 1)

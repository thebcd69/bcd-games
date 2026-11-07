#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""env.MADEIRA_THIN_RESERVE: thin reservations for 1-2 GB read-write MEM_RESERVE (virtual_ios.c, tests/x64/thinreserve-x64.c).

Some programs reserve address space in 1 GB blocks (MEM_RESERVE, PAGE_READWRITE, no address) and commit a few MB of each. A
device without the increased-memory-limit entitlement maps about 64 GB, every reservation takes its full size, and the 43rd
of one game's 1 GB blocks failed with STATUS_NO_MEMORY. After the first MADEIRA_THIN_RESERVE_AFTER_GB (8) GB of such
reservations the next ones get a real 63 MB head and a 1 MB guard in a dedicated arena, and the caller is told it got the full
size; a commit that grows past the head hits the guard and fails ([thin] OVERFLOW).

A program that commits more than a head into each of those blocks would meet the guard, which a device with a large address
space never asks of it today, so in BCD this is OFF unless env.MADEIRA_THIN_RESERVE = 1 in the game's own file.

  1. ios_thin_config, cut out of virtual_ios.c and run on the host: unset, empty, 0, anything but 1: off, nothing logged, the
     settings of the head are not even read into effect; 1: on, a 63 MB head by default (MADEIRA_THIN_RESERVE_MB 16..512),
     8 GB first (MADEIRA_THIN_RESERVE_AFTER_GB), one log line, read once;
  2. the source: the only entry is ios_thin_reserve (called for a NULL-base MEM_RESERVE with no limits), every other thin hook
     is guarded by ios_thin_live_n (0 with the switch off), the thin arena type is initialised at every place a reserved area
     is made, and generic placement never searches a thin arena;
  3. the device program tests/x64/thinreserve-x64.c is there.
Needs python3 and a C compiler.
"""
from pathlib import Path
import os
import re
import shutil
import subprocess
import sys
import tempfile

R = Path(__file__).resolve().parents[2]
src = (R / "build/ntdll-unix/virtual_ios.c").read_text()
ok = True


def check(what, cond, detail=""):
    global ok
    print(("ok   " if cond else "FAIL ") + what + (("  " + str(detail)) if detail and not cond else ""))
    ok = ok and bool(cond)


a = src.index("static int ios_thin_config(void)")
fn = src[a:src.index("\n}\n", a) + 3]
cc = shutil.which("cc") or shutil.which("clang") or shutil.which("gcc")
if not cc:
    print("SKIP host run: no C compiler")
else:
    harness = r'''
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
typedef uintptr_t ULONG_PTR;
#define IOS_THIN_GUARD 0x100000ULL
static int lines;
#define dprintf(fd, ...) (lines++)
static int ios_thin_mode = -1;
static ULONG_PTR ios_thin_stride, ios_thin_head, ios_thin_after;
''' + fn + r'''
int main(void)
{
    int a = ios_thin_config(), b = ios_thin_config();
    printf("%d %d %d %lu %lu\n", a, b, lines, (unsigned long)(ios_thin_head >> 20), (unsigned long)(ios_thin_after >> 30));
    return 0;
}
'''
    with tempfile.TemporaryDirectory() as tmp:
        c = os.path.join(tmp, "t.c")
        exe = os.path.join(tmp, "t")
        open(c, "w").write(harness)
        b = subprocess.run([cc, "-std=gnu99", "-Wall", "-Werror", "-o", exe, c], capture_output=True, text=True)
        check("ios_thin_config builds on the host", b.returncode == 0, b.stderr[-1500:])
        if b.returncode == 0:
            def run(**env_extra):
                env = {k: v for k, v in os.environ.items() if not k.startswith("MADEIRA_THIN")}
                env.update(env_extra)
                return subprocess.run([exe], capture_output=True, text=True, env=env).stdout.strip()
            for label, kw in (("unset", {}), ("empty", {"MADEIRA_THIN_RESERVE": ""}), ("0", {"MADEIRA_THIN_RESERVE": "0"}),
                              ("2", {"MADEIRA_THIN_RESERVE": "2"}), ("on", {"MADEIRA_THIN_RESERVE": "on"})):
                out = run(**kw)
                check("MADEIRA_THIN_RESERVE %s: off, no log line" % label, out.split()[:3] == ["0", "0", "0"], out)
            check("MADEIRA_THIN_RESERVE=1: on, a 63 MB head, 8 GB first, one log line, read once",
                  run(MADEIRA_THIN_RESERVE="1") == "1 1 1 63 8", run(MADEIRA_THIN_RESERVE="1"))
            check("  the slot stride and the first GB are tunable and clamped (16..512 MB)",
                  run(MADEIRA_THIN_RESERVE="1", MADEIRA_THIN_RESERVE_MB="128", MADEIRA_THIN_RESERVE_AFTER_GB="4") == "1 1 1 127 4"
                  and run(MADEIRA_THIN_RESERVE="1", MADEIRA_THIN_RESERVE_MB="2").split()[3] == "15"
                  and run(MADEIRA_THIN_RESERVE="1", MADEIRA_THIN_RESERVE_MB="9999").split()[3] == "511")

# ---- the source
check("the switch is read once and only '1' turns it on", src.count('getenv( "MADEIRA_THIN_RESERVE" )') == 1
      and "ios_thin_mode = (e && e[0] == '1');" in fn)
check("the only entry is ios_thin_reserve, for a NULL-base MEM_RESERVE with no limits",
      "if (!*ret && type == MEM_RESERVE && !limit_low && !limit_high && !align && !attributes)" in src
      and src.count("ios_thin_reserve( ret, size_ptr, protect, &thin_status )") == 1)
body = src[src.index("static BOOL ios_thin_reserve( void **ret, SIZE_T *size_ptr, ULONG protect, NTSTATUS *status )"):]
body = body[:body.index("\n}\n")]
check("ios_thin_reserve asks the switch before anything else", body.index("if (!ios_thin_config()) return FALSE;") < body.index("ios_thin_seen"))
check("the commit, decommit, release and query hooks run only with a thin reservation live",
      "&& ios_thin_live_n)\n        ios_thin_note_commit( *ret, *size_ptr );" in src
      and "if (ios_thin_live_n && type == MEM_DECOMMIT && base && ios_thin_decommit_fixup( base, &size ))" in src
      and "if ((type & MEM_RELEASE) && ios_thin_live_n) ios_thin_released( base );" in src
      and "if (ios_thin_live_n) ios_thin_fix_query( addr, info );" in src)
check("every reserved area is made with thin_only set (0 for generic, FEX and x64 hook-zone areas)",
      len(re.findall(r"area->thin_only = 0;", src)) == 3 and "new_area->thin_only = area->thin_only;" in src
      and src.count("area->thin_only = 1;") == 1)
check("generic placement never searches a thin arena", src.count("if (area->thin_only) continue;") == 2)
check("the device program is there", (R / "tests/x64/thinreserve-x64.c").is_file())
check("no recommended list and no auto-tune rule sets it", "MADEIRA_THIN_RESERVE" not in (R / "recommended/recommendations.json").read_text()
      and "MADEIRA_THIN_RESERVE" not in (R / "build/tools/autotune_rules.py").read_text())

print("PASS: thin reserve" if ok else "FAIL: thin reserve")
sys.exit(0 if ok else 1)

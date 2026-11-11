#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""env.MADEIRA_CAGE_RELEASE = 2 (build 651; build/ntdll-unix/virtual_ios.c ios_cage_boot_skip, virtual_init).

The Last of Us Part I (build 650, the owner's start of 2026-11-10 09:05 with env.MADEIRA_CAGE_RELEASE = 1 by hand): the band ran out
while the first level loaded, mode 1 handed the 8 GB V8 cage holdback to Wine at 1:10, and three seconds later the host's allocator had
no room ("NSMallocException ... AGXG18PFamilyBuffer"). Mode 2 never holds the 8 GB, so the kernel shares it between Wine's views and the
host from the start. Checks:
  - the key: exactly "2" (not "1", "21", "", unset) skips the holdback; the host harness runs the function itself;
  - virtual_init: with 2 only the first host page is held (a 32-bit window's borrowed guard), ios_cage_holdback_live stays 0 (no cage
    grant, no carve, no tail), the line says so; otherwise the 8 GB holdback exactly as before;
  - mode 1 (release on exhaustion) is unchanged: it still asks for "1";
  - virtual_init calls no ios_wow_ helper of its own (check-lazy-windows' invariant);
  - CI runs this check.
"""
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

root = Path(__file__).resolve().parents[2]
ok = True


def check(name, cond, detail=""):
    global ok
    print(("ok   " if cond else "FAIL ") + name)
    if not cond:
        ok = False
        if detail:
            print("     " + str(detail)[:1500])


src = (root / "build/ntdll-unix/virtual_ios.c").read_text()
fn = src[src.index("static int ios_cage_boot_skip( void )\n{"):]
fn = fn[:fn.index("\n}\n") + 3]
check("the key reads MADEIRA_CAGE_RELEASE and takes exactly \"2\"",
      'const char *e = getenv( "MADEIRA_CAGE_RELEASE" );' in fn and "return e && e[0] == '2' && !e[1];" in fn, fn)

init = src[src.index("void virtual_init(void)\n{"):]
init = init[:init.index("\n}\n")]
blk = init[init.index("if (ios_cage_boot_skip())"):init.index('else dprintf( 2, "[cage] holdback reserve FAILED')]
check("virtual_init: with 2 only the first host page is held, and the line says the 8 GB is free",
      "anon_mmap_fixed( (void *)(uintptr_t)IOS_CAGE_BASE, host_page_size ? (size_t)host_page_size : 0x4000," in blk
      and "[cage] holdback NOT reserved (MADEIRA_CAGE_RELEASE=2)" in blk
      and blk.index("[cage] holdback NOT reserved") < blk.index("else if (anon_mmap_fixed( (void *)(uintptr_t)IOS_CAGE_BASE, IOS_CAGE_REAL_SIZE, PROT_NONE, 0 ) != MAP_FAILED)"))
skip_part = blk[:blk.index("else if (anon_mmap_fixed(")]
check("...ios_cage_holdback_live stays 0 with 2 (no cage grant, carve or tail); the 8 GB branch sets it as before",
      "ios_cage_holdback_live" not in skip_part and "ios_cage_window_tail_live" not in skip_part
      and "ios_cage_holdback_live = 1;" in blk[blk.index("else if (anon_mmap_fixed("):])
check("virtual_init calls no ios_wow_ helper itself (only ios_wow_session_start)",
      "ios_wow_guard_size(" not in init)

rel = src[src.index("static int ios_cage_release_on_exhaustion( void *start, void *end, size_t want )"):]
rel = rel[:rel.index("\n}\n")]
check("mode 1 is unchanged: the hand-over still asks for \"1\"",
      "if (!ios_cage_holdback_live || !e || *e != '1') return 0;" in rel)

cc = shutil.which("cc") or shutil.which("clang")
if not cc:
    print("note: no host C compiler; the key was not run")
else:
    with tempfile.TemporaryDirectory() as tmp:
        t = Path(tmp)
        (t / "k.c").write_text("#include <stdlib.h>\n#include <stdio.h>\n" + fn + """
int main(int argc, char **argv) { (void)argc; (void)argv; printf("%d\\n", ios_cage_boot_skip()); return 0; }
""")
        b = subprocess.run([cc, "-O1", "-Wall", "-Werror", str(t / "k.c"), "-o", str(t / "k")], capture_output=True, text=True)
        check("the key builds on the host", b.returncode == 0, b.stderr)
        if b.returncode == 0:
            got = {}
            for v in (None, "", "0", "1", "2", "21", " 2", "2 "):
                env = {k: x for k, x in os.environ.items() if k != "MADEIRA_CAGE_RELEASE"}
                if v is not None:
                    env["MADEIRA_CAGE_RELEASE"] = v
                got[v] = subprocess.run([str(t / "k")], capture_output=True, text=True, env=env).stdout.strip()
            want = {None: "0", "": "0", "0": "0", "1": "0", "2": "1", "21": "0", " 2": "0", "2 ": "0"}
            check("...only exactly \"2\" skips the holdback", got == want, got)

ci = (root / ".github/workflows/build-ipa.yml").read_text()
check("CI runs this check", "python3 tests/host/check-cage-boot-skip.py" in ci)

print("PASS: cage-boot-skip" if ok else "FAILED: cage-boot-skip")
sys.exit(0 if ok else 1)

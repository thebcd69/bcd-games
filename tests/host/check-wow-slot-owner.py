#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""A 32-bit guest window is found by its reserving thread's Darwin id, not its pthread_t (round 624).

Battle.net installer session Y0HFHB (build 614) and seven Garry's Mod / Battle.net logs: a 32-bit process killed by
TerminateProcess never ran the exit tail that releases its window; its slot kept the dead boot thread's pthread_t as `owner`.
A pthread_t is the address of the thread's structure and can be handed to the next pthread_create, so a later boot thread could
compare equal in ios_wow_slot_current(), take that window through the silent "already have one" return of
ios_wow_window_reserve_once(), map its ntdll32 elsewhere and overwrite the session-wide LdrSystemDllInitBlock ([wow-initblock]).

Checks:
  - the slot keeps owner_tid, set from pthread_threadid_np where `owner` is set;
  - ios_wow_slot_current() compares owner_tid with the caller's id (no pthread_equal on `owner` any more);
  - the "already have one" branch logs how the window was found, at most 32 times, and still returns STATUS_SUCCESS;
  - a host model: a recycled pthread_t with a different Darwin id is not the owner; the same thread is;
  - virtual_ios.c still compiles for iOS (syntax only, pinned Wine headers) when the Wine tree is there.
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

root = Path(__file__).resolve().parents[2]
src = (root / "build/ntdll-unix/virtual_ios.c").read_text(errors="replace")
ok = True


def check(name, cond, detail=""):
    global ok
    print(("ok   " if cond else "FAIL ") + name)
    if not cond:
        ok = False
        if detail:
            print("     " + str(detail)[:2000])


st = src[src.find("struct ios_wow_window\n{"):]
st = st[:st.find("};")]
check("the slot keeps owner_tid", "uint64_t   owner_tid;" in st)
cur = src[src.find("static struct ios_wow_window *ios_wow_slot_current(void)"):]
cur = cur[:cur.find("\n}\n") + 3]
check("ios_wow_slot_current compares owner_tid with the caller's Darwin id",
      "me = ios_thread_uid();" in cur and "ios_wow_windows[i].owner_tid == me" in cur, cur)
check("...and no longer pthread_equal on owner", "pthread_equal" not in cur, cur)
uid = src[src.find("static uint64_t ios_thread_uid( void )"):]
uid = uid[:uid.find("\n}\n") + 3]
check("ios_thread_uid reads pthread_threadid_np", "pthread_threadid_np( NULL, &t );" in uid, uid)
res = src[src.find("static NTSTATUS ios_wow_window_reserve_once(void)\n{"):]
res = res[:res.find("\n}\n") + 3]
check("owner_tid is set where owner is set", "slot->owner         = pthread_self();\n    slot->owner_tid     = ios_thread_uid();" in res)
br = res[res.find("if ((slot = ios_wow_slot_current()))"):res.find("return STATUS_SUCCESS;   /* already have one */")]
check("the already-have-one branch logs by=, owner_tid, me_tid, live_child, dead, at most 32 times",
      "[wow-window] already have one:" in br and "already_n++ < 32" in br and "by=%s" in br and "owner_tid=%llu" in br
      and "me_tid=%llu" in br and "live_child=%d dead=%d" in br, br)
check("...unlocks before logging and still returns success",
      br.find("pthread_mutex_unlock( &ios_wow_mutex );") < br.find("dprintf") and
      "return STATUS_SUCCESS;   /* already have one */" in res)

# ---------------------------------------------------------------- host model of the lookup
model = r'''
#include <stdio.h>
#include <stdint.h>
#include <string.h>
struct slot { uintptr_t base; unsigned leaked, dead; uint64_t owner_tid; void *peb; };
static struct slot w[8]; static unsigned n;
static uint64_t cur_tid;
static uint64_t ios_thread_uid(void) { return cur_tid; }
static struct slot *lookup(void)
{
    unsigned i; uint64_t me = ios_thread_uid();
    for (i = 0; i < n; i++)
        if (w[i].base && !w[i].leaked && !w[i].dead && me && w[i].owner_tid == me) return &w[i];
    return NULL;
}
int main(void)
{
    int fails = 0;
    n = 1; w[0].base = 0x10000000; w[0].owner_tid = 101; w[0].peb = (void *)0x48ff84000;   /* killed process's window */
    cur_tid = 101; if (lookup() != &w[0]) { puts("FAIL the reserving thread finds its window"); fails++; }
    cur_tid = 202; if (lookup() != NULL) { puts("FAIL a new thread that reuses the dead one's pthread_t is not the owner"); fails++; }
    w[0].dead = 1; cur_tid = 101; if (lookup() != NULL) { puts("FAIL a dead slot resolves to nobody"); fails++; }
    cur_tid = 0; w[0].dead = 0; if (lookup() != NULL) { puts("FAIL an unknown id matches nothing"); fails++; }
    if (!fails) puts("model-ok");
    return fails != 0;
}
'''
cc = shutil.which("cc") or shutil.which("clang")
if cc:
    with tempfile.TemporaryDirectory() as t:
        c = Path(t) / "m.c"
        c.write_text(model)
        b = subprocess.run([cc, "-Wall", "-Werror", "-o", str(Path(t) / "m"), str(c)], capture_output=True, text=True)
        check("the lookup model compiles", b.returncode == 0, b.stderr)
        if b.returncode == 0:
            r = subprocess.run([str(Path(t) / "m")], capture_output=True, text=True)
            check("model: the owner by id, never a thread that reuses a dead owner's pthread_t", r.returncode == 0 and "model-ok" in r.stdout, r.stdout)

# ---------------------------------------------------------------- the real file still compiles for iOS
wine = Path(os.environ.get("WINE_SRC", root / "wine"))
config = wine / "build-macos/include/config.h"
if shutil.which("xcrun") and config.exists():
    sdk = subprocess.run(["xcrun", "--sdk", "iphoneos", "--show-sdk-path"], capture_output=True, text=True).stdout.strip()
    if sdk:
        build = root / "build/ntdll-unix"
        flags = ["-fsyntax-only", "-arch", "arm64", "-isysroot", sdk, "-miphoneos-version-min=17.0", "-O2", "-fPIC",
                 "-fno-strict-aliasing", "-Wno-implicit-function-declaration", "-Wno-int-conversion",
                 "-include", str(config), "-include", str(build / "shims/wine_ios_exit.h"), f"-I{build / 'shims'}",
                 f"-I{wine / 'build-macos/dlls/ntdll'}", f"-I{wine / 'dlls/ntdll'}", f"-I{wine / 'dlls/ntdll/unix'}",
                 f"-I{wine / 'build-macos/include'}", f"-I{wine / 'include'}", "-D__WINESRC__", "-DLTC_NO_PROTOTYPES",
                 "-DLTC_SOURCE", "-D_NTSYSTEM_", "-D_ACRTIMP=", "-DWINBASEAPI=", '-DBINDIR="/usr/local/bin"',
                 '-DLIBDIR="/usr/local/lib"', '-DDATADIR="/usr/local/share"', '-DSYSTEMDLLPATH=""', "-DWINE_UNIX_LIB",
                 "-DWINE_IOS=1"]
        r = subprocess.run(["xcrun", "-sdk", "iphoneos", "clang", *flags, str(root / "build/ntdll-unix/virtual_ios.c")],
                           capture_output=True, text=True)
        check("virtual_ios.c compiles for iOS (syntax only, pinned wine headers)", r.returncode == 0, r.stderr[-2000:])
        check("...with no warning from the new code", "owner_tid" not in r.stderr and "ios_thread_uid" not in r.stderr, r.stderr[-2000:])
else:
    print("skip the iOS syntax check: no wine headers (set WINE_SRC)")

print("PASS: wow slot owner" if ok else "FAILED: wow slot owner")
sys.exit(0 if ok else 1)

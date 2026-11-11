#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""[hid-pad] ml2101 minute: what the game reads from the HID controller and what the app fed it (night round 2026-11-11, T3 S12, T1 S17; log only).

Resonance (A82ECP: HID DualSense mode without a controller, a pointer on a letterboxed picture) and Devil May Cry 5 (7VHWKC, C6MW97: "freeze" comments
with a healthy log) left no trace of what the game did with the pad. build/wineserver/hidpad_ios.c now counts the game's ReadFile calls and the time of the
last one, and once a minute (30 lines at most, only after the game opened the device) logs `[hid-pad] ml2101 minute #N: opens=.. read-requests=.. (+..)
reports-delivered=.. (+..) last-read-request=never|N ms ago pad=connected|none packet=.. (+..)`. Checks: CI runs this check; the read counter and its time stamp
are the first statements of the read handler; the timer starts when the device is created (HID mode only: the XInput wineserver is exactly as before) and
re-arms itself; the real function, cut from hidpad_ios.c and run on the host with stubs: a device nobody opened prints nothing; after opens the line has the
exact numbers and the per-minute deltas; `pad=none` when the app has no controller connected; the thirty-first minute prints nothing; the timer is re-armed
every minute; with the iOS SDK (the Mac) the file passes `clang -fsyntax-only` with the wineserver build's flags and no warning.
(The XInput half of T1 S17, `[xinput] minute: GetState calls`, lives in the app's HardwareInput and is not part of this file.)
"""
from pathlib import Path
import os
import re
import shutil
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[2]
ok = True


def check(what, cond, detail=""):
    global ok
    print(("ok   " if cond else "FAIL ") + what + (("\n     " + str(detail)[:1500]) if (detail and not cond) else ""))
    ok = ok and bool(cond)


hp = (root / "build/wineserver/hidpad_ios.c").read_text()
wf = (root / ".github/workflows/build-ipa.yml").read_text()
check("CI runs this check", "python3 tests/host/check-hidpad-minute.py" in wf)
rd = hp[hp.index("static void hidpad_file_read( struct fd *fd, struct async *async, file_pos_t pos )\n{"):]
rd = rd[:rd.index("\n}\n")]
check("the read handler counts the request and stamps its time before it queues the read",
      rd.index("device->read_requests++;") < rd.index("device->last_read_request = monotonic_time;") < rd.index("queue_async( &file->read_q, async );"))
init = hp[hp.index("void madeira_hidpad_init( void )\n{"):]
check("the timer starts when the device exists (after it is linked), not in XInput mode (init returns first when MADEIRA_HIDPAD is unset)",
      init.index("if (!id) return;") < init.index("device->stats_timer = add_timeout_user( -60 * TICKS_PER_SEC, hidpad_stats, device );")
      and init.index("\\\\??\\\\%s\\n\", id->env") < init.index("device->stats_timer = add_timeout_user("))
fn = hp[hp.index("static void hidpad_stats( void *private )"):]
fn = fn[:fn.index("\n}\n") + 3]
harness = r'''
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
typedef int64_t timeout_t;
#define TICKS_PER_SEC 10000000
struct timeout_user;
struct hidpad_device
{
    unsigned int opens, reads;
    unsigned int read_requests, stats_lines, stats_requests, stats_reads, stats_packet;
    timeout_t last_read_request;
    struct timeout_user *stats_timer;
};
struct winios_hidpad { uint32_t packet; uint8_t connected; };
static timeout_t monotonic_time;
static int rearms;
static int pad_connected = 1;
static unsigned int pad_packet;
static struct timeout_user *add_timeout_user(timeout_t when, void (*func)(void *), void *private) { rearms++; if (when != -60 * (timeout_t)TICKS_PER_SEC) printf("BAD TIMEOUT\n"); return (struct timeout_user *)1; }
static int winios_hidpad_get_state(struct winios_hidpad *out) { out->packet = pad_packet; out->connected = pad_connected; return pad_connected; }
@FN@
int main(int argc, char **argv)
{
    struct hidpad_device d;
    int mode = atoi(argv[1]), i;
    memset(&d, 0, sizeof(d));
    switch (mode)
    {
    case 0: hidpad_stats(&d); printf("rearms=%d\n", rearms); break;                                    /* never opened */
    case 1:
        d.opens = 1; d.read_requests = 14976; d.reads = 14975; d.last_read_request = 100 * (timeout_t)TICKS_PER_SEC; monotonic_time = d.last_read_request + 30000;
        pad_packet = 5120;
        hidpad_stats(&d);
        d.read_requests += 100; d.reads += 90; monotonic_time += 600000000; pad_packet += 7;
        hidpad_stats(&d);
        break;
    case 2: d.opens = 2; pad_connected = 0; pad_packet = 3; hidpad_stats(&d); break;                    /* no reads at all, no pad */
    case 3: d.opens = 1; for (i = 0; i < 35; i++) hidpad_stats(&d); printf("rearms=%d lines=%u\n", rearms, d.stats_lines); break;
    }
    return 0;
}
'''.replace("@FN@", fn)
with tempfile.TemporaryDirectory() as tmp:
    t = Path(tmp)
    (t / "h.c").write_text(harness)
    b = subprocess.run([shutil.which("cc"), "-std=gnu11", "-Wall", "-Wno-unused-function", "-o", str(t / "h"), str(t / "h.c")], capture_output=True, text=True)
    check("the real function compiles on the host with stubs", b.returncode == 0, b.stderr[-1500:])
    if b.returncode == 0:
        def run(mode):
            p = subprocess.run([str(t / "h"), str(mode)], capture_output=True, text=True)
            return p.stdout.splitlines(), p.stderr.splitlines()
        o, e = run(0)
        check("a device nobody opened prints nothing and the timer is re-armed", e == [] and o == ["rearms=1"], (o, e))
        o, e = run(1)
        check("after opens: the exact numbers (cumulative and per minute), the age of the last read request, the pad state and packet",
              e[0] == "[hid-pad] ml2101 minute #1: opens=1 read-requests=14976 (+14976) reports-delivered=14975 (+14975) last-read-request=3 ms ago pad=connected packet=5120 (+5120)"
              and e[1] == "[hid-pad] ml2101 minute #2: opens=1 read-requests=15076 (+100) reports-delivered=15065 (+90) last-read-request=60003 ms ago pad=connected packet=5127 (+7)", e)
        o, e = run(2)
        check("a device opened but never read, with no controller connected: last-read-request=never, pad=none",
              e == ["[hid-pad] ml2101 minute #1: opens=2 read-requests=0 (+0) reports-delivered=0 (+0) last-read-request=never pad=none packet=3 (+3)"], e)
        o, e = run(3)
        check("30 lines at most (the 31st minute prints nothing); the timer is re-armed every minute", len(e) == 30 and o == ["rearms=35 lines=30"], (len(e), o))

    xcrun = shutil.which("xcrun")
    wine = next((c for c in (root / "wine", Path(os.environ.get("WINE_SRC", "/nonexistent")), Path.home() / "madeira-bcd/wine")
                 if (c / "server/object.c").is_file() and (c / "build-macos/include/config.h").is_file()), None)
    if not xcrun or not wine:
        print("SKIP: the iOS-SDK syntax check needs Xcode and the full tree's wine/build-macos/include (the Mac)")
    else:
        sdk = subprocess.run(["xcrun", "--sdk", "iphoneos", "--show-sdk-path"], capture_output=True, text=True).stdout.strip()
        bdir = root / "build/wineserver"
        cmd = ["xcrun", "-sdk", "iphoneos", "clang", "-fsyntax-only", "-arch", "arm64", "-isysroot", sdk, "-miphoneos-version-min=17.0", "-O2",
               "-I" + str(wine / "include"), "-I" + str(wine / "include/wine"), "-I" + str(wine / "build-macos/include"), "-I" + str(bdir),
               "-I" + str(wine / "server"), "-I" + str(root / "build/ntdll-unix/shims"), "-I" + str(root / "build/madsync"), "-DHAVE_LINUX_NTSYNC_H=1",
               "-include", str(bdir / "config_ios.h"), "-include", "stdarg.h", "-include", str(bdir / "unicode_fix.h"),
               "-include", str(bdir / "wineserver_ios_kill.h"), '-DBINDIR="/usr/local/bin"', '-DDATADIR="/usr/local/share"', "-D__WINESRC__",
               "-DWINE_IOS=1", "-Dmain=wineserver_main", "-Wno-implicit-function-declaration", "-Wall", str(bdir / "hidpad_ios.c")]
        r = subprocess.run(cmd, capture_output=True, text=True)
        check("hidpad_ios.c passes clang -fsyntax-only with the wineserver build's flags (no error, no warning)", r.returncode == 0 and not r.stderr.strip(), r.stderr[-800:])

print("PASS: hid-pad minute" if ok else "FAIL: hid-pad minute")
sys.exit(0 if ok else 1)

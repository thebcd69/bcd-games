#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""Wine's combase: a thread with no COM state joins an implicit MTA even when no thread of the process made one (dlls/combase/apartment.c).

Unreal Engine 5's FAsyncLoadingThread (a plain worker thread that never called CoInitializeEx) creates the audio device enumerator
(CoCreateInstance(CLSID_MMDeviceEnumerator)) and does not check the result. Wine's combase answers CO_E_NOTINITIALIZED
(err:ole:com_get_class_object apartment not initialised) when the thread has no apartment and the process has no MTA, the
game then calls through the NULL object it did not get (mov rax,[rcx]; call [rax+0x20] with rcx = 0), and the process ends:
Meccha Chameleon (F4WD9B, build 634) and Hubert (2CGJ07, build 614), nowhere else in the log archive.

The class lookup, the in-process server (mmdevapi's enumerator is threading(both)) and everything after it work for a thread that
does have an apartment (every other Unreal 5 session creates the same object). What differs is only apartment_get_current_or_mta():

    struct apartment *apt = com_get_current_apt();
    if (apt) { apartment_addref(apt); return apt; }
    return apartment_get_mta();          /* NULL when no thread of the process ever created an MTA */

A Windows game process practically always holds an MTA (some component of the process, such as the audio engine's or Media
Foundation's own threads, initialises one), and a thread with no COM state then uses it implicitly, which Wine's combase also does
(dlls/ole32/tests/compobj.c test_implicit_mta). Which component supplies it on Windows is not proved; BCD's process has none of
those components' threads in these two sessions.

The patch gives such a thread the MTA the way CoIncrementMTAUsage does: when there is no current apartment and no MTA, the process's
MTA is created with a usage cookie that is never released (the cookie is the MTA's own reference, so the apartment lives as long as
the process, as the one Media Foundation's work queue takes does). Only the path that returns NULL today changes; a call that finds
an apartment or an MTA is as it was. A later CoInitializeEx on any thread joins the same MTA (STA threads still get their own
apartment: nothing is attached to the calling thread). One ERR line per process says it happened ("[com-mta] ...").

The patched combase ships as arm64ec-windows/combase-optin.dll NEXT TO the untouched combase.dll; only a game whose settings have
env.MADEIRA_OPTIN_DLLS = combase gets it (app/Madeira/WineProcessBridge.m links it as combase.dll in sysx64, and the next
session's farm relink restores the shipped one). Every other game keeps the shipped file, byte for byte.

Wine's combase is LGPL 2.1 or later; the patched file carries a "Modified by BCD" notice, and this script is the source of the change.

Usage:
  patch-wine-combase-implicit-mta.py WINE_SRC               patch the tree (idempotent: a second run changes nothing)
  patch-wine-combase-implicit-mta.py --verify DLL SHIPPED   check a built DLL: it carries the marker, is the same machine type,
                                                            and exports exactly the shipped combase.dll's names
tools/build-wine-extra-dlls.sh applies it around its combase build and restores the tree afterwards.
"""
import os
import struct
import sys

MARKER = "combase-implicit-mta-v1 madeira-bcd"
FILE = "dlls/combase/apartment.c"

NOTICE_ANCHOR = "#include <stdarg.h>\n#include <assert.h>\n"
NOTICE = '''/*
 * Modified by BCD, 2026-11-11 (tools/patch-wine-combase-implicit-mta.py): a thread with no COM state uses an MTA that is
 * created on first need when no thread of the process made one (apartment_get_current_or_mta). Everything else in this file
 * is as Wine's; this file stays under the license above.
 */

'''

OLD = '''/* Return the current apartment if it exists, or, failing that, the MTA. Caller
 * must free the returned apartment in either case. */
struct apartment * apartment_get_current_or_mta(void)
{
    struct apartment *apt = com_get_current_apt();
    if (apt)
    {
        apartment_addref(apt);
        return apt;
    }
    return apartment_get_mta();
}
'''

NEW = '''static const char madeira_implicit_mta_tag[] __attribute__((used)) = "''' + MARKER + '''";

/* Modified by BCD: a thread that never initialised COM, in a process where no thread made an MTA, got CO_E_NOTINITIALIZED
 * from every COM call that accepts an implicit MTA (Unreal Engine 5's FAsyncLoadingThread creating the audio device enumerator:
 * the game does not check the result). The process's MTA is created here, the way CoIncrementMTAUsage does, with a usage
 * cookie that is never released; nothing is attached to the calling thread. */
static struct apartment *madeira_implicit_mta(void)
{
    CO_MTA_USAGE_COOKIE cookie;
    static LONG logged;

    if (FAILED(apartment_increment_mta_usage(&cookie)))
        return NULL;
    if (!InterlockedExchange(&logged, 1))
        ERR("[com-mta] no thread of this process made an MTA: created it for thread %04lx (%s)\\n",
            GetCurrentThreadId(), madeira_implicit_mta_tag);
    return apartment_get_mta();
}

/* Return the current apartment if it exists, or, failing that, the MTA. Caller
 * must free the returned apartment in either case. */
struct apartment * apartment_get_current_or_mta(void)
{
    struct apartment *apt = com_get_current_apt();
    if (apt)
    {
        apartment_addref(apt);
        return apt;
    }
    if ((apt = apartment_get_mta()))
        return apt;
    return madeira_implicit_mta();
}
'''

EDITS = (
    (NOTICE_ANCHOR, NOTICE + NOTICE_ANCHOR),
    (OLD, NEW),
)


def patch(wine):
    path = os.path.join(wine, FILE)
    if not os.path.isfile(path):
        sys.exit("patch-wine-combase-implicit-mta: no " + path)
    text = open(path).read()
    if MARKER in text:
        print("already patched")
        return
    for old, new in EDITS:
        if text.count(old) != 1:
            sys.exit("patch-wine-combase-implicit-mta: anchor found %d times in %s:\n%s"
                     % (text.count(old), FILE, old[:400]))
        text = text.replace(old, new)
    open(path, "w").write(text)
    print("patched %s (%d edits)" % (path, len(EDITS)))


def pe_exports(path):
    d = open(path, "rb").read()
    if d[:2] != b"MZ":
        sys.exit("verify: %s is not a PE image" % path)
    pe = struct.unpack_from("<I", d, 0x3c)[0]
    machine, nsec = struct.unpack_from("<HH", d, pe + 4)
    optsz = struct.unpack_from("<H", d, pe + 20)[0]
    opt = pe + 24
    secs = []
    for i in range(nsec):
        s = opt + optsz + 40 * i
        vsz, va, rsz, raw = struct.unpack_from("<IIII", d, s + 8)
        secs.append((va, max(vsz, rsz), raw))

    def off(rva):
        for va, size, raw in secs:
            if va <= rva < va + size:
                return rva - va + raw
        raise ValueError("rva %#x outside the sections" % rva)

    exp_rva = struct.unpack_from("<I", d, opt + 112)[0]
    e = off(exp_rva)
    nnames, anames = struct.unpack_from("<I", d, e + 24)[0], struct.unpack_from("<I", d, e + 32)[0]
    names = set()
    for i in range(nnames):
        no = off(struct.unpack_from("<I", d, off(anames) + 4 * i)[0])
        names.add(d[no:d.index(b"\0", no)].decode("latin-1"))
    return d, machine, names


def verify(path, shipped):
    d, machine, names = pe_exports(path)
    _, smachine, snames = pe_exports(shipped)
    bad = []
    if MARKER.encode() not in d:
        bad.append("no '%s' string: not built from the patched tree" % MARKER)
    if MARKER.encode() in open(shipped, "rb").read():
        bad.append("the shipped combase.dll carries the marker: it was built from a patched tree")
    if machine != smachine:
        bad.append("machine %#x, the shipped combase.dll's is %#x" % (machine, smachine))
    if names != snames:
        bad.append("exports differ from the shipped combase.dll: only here %s, only there %s"
                   % (sorted(names - snames)[:8], sorted(snames - names)[:8]))
    if bad:
        sys.exit("verify %s:\n  %s" % (path, "\n  ".join(bad)))
    print("verify %s: implicit MTA present, the %d exports of %s" % (path, len(names), shipped))


if __name__ == "__main__":
    if len(sys.argv) == 4 and sys.argv[1] == "--verify":
        verify(sys.argv[2], sys.argv[3])
    elif len(sys.argv) == 2:
        patch(sys.argv[1])
    else:
        sys.exit(__doc__)

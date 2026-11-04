#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""Wine xaudio2, opt-in: engine callbacks are published before their count (round 630, agent O3 S5).

The Elder Scrolls V: Skyrim (32-bit TESV.exe, Send Log 93EMSH, build 626): IXAudio2::RegisterForCallbacks raised the callback count,
then grew the array with realloc, then stored the callback, all under the engine's lock; FAudio's audio thread runs the engine-callback
passes (XA2ECB_OnProcessingPassStart/End, OnCriticalError) without that lock, read the new count with the array still NULL and faulted
on the NULL read (xaudio2_6.dll+0x6773, "FAudio_AudioClientThread", 29 s after the start). The order is a race on any machine; a second
start usually wins it (the same player, 4 minutes later). It is the only crash inside xaudio2 in about 3,000 logs.

With env.MADEIRA_XAUDIO2_CALLBACK_ORDER = 1 (exactly "1"): RegisterForCallbacks fills a new array, publishes it, and only then the count
(a memory barrier between them); the passes read the count, then the array, and skip a missing one. The old array is not freed, because
a pass on the audio thread may still be reading it (a few bytes per registration beyond the largest count; Release frees the current
one). One "[xaudio2-cb]" line says it is on. Unset or any other value: the old code runs, unchanged.

Every xaudio2_0..9 module builds this one file (PARENTSRC = ../xaudio2_7). The i386 farm (build/wine-i386/build.sh) is built from the
patched tree; the arm64ec copies stay the shipped ones (tools/build-wine-extra-dlls.sh never replaces a shipped DLL), so the key acts in
32-bit programs. Idempotent; fails by name when an anchor moves.
Usage: patch-wine-xaudio2-callbacks.py WINE-DIR
"""
from pathlib import Path
import sys

MARKER = "madeira-bcd: xaudio2 engine callbacks"
FILE = "dlls/xaudio2_7/xaudio_dll.c"

HELPERS_ANCHOR = "/* Engine Callbacks */\n"
HELPERS = (
    "/* " + MARKER + " (tools/patch-wine-xaudio2-callbacks.py, round 630, Skyrim 93EMSH): with env\n"
    " * MADEIRA_XAUDIO2_CALLBACK_ORDER=1 (only the exact value 1) RegisterForCallbacks publishes the array before the count and the\n"
    " * engine-callback passes, which FAudio's audio thread runs without the engine's lock, read the count before the array. The old\n"
    " * order (count, realloc, store) let a pass see the new count with a NULL array. Unset, 0 or anything else: unchanged. */\n"
    "static BOOL xa2_callback_order(void)\n"
    "{\n"
    "    static LONG enabled = -1;\n"
    "\n"
    "    if (enabled < 0)\n"
    "    {\n"
    "        WCHAR value[4];\n"
    "        DWORD err = GetLastError();\n"
    "        DWORD len = GetEnvironmentVariableW( L\"MADEIRA_XAUDIO2_CALLBACK_ORDER\", value, ARRAY_SIZE(value) );\n"
    "        LONG on = (len == 1 && value[0] == '1');\n"
    "        SetLastError( err );\n"
    "        if (InterlockedCompareExchange( &enabled, on, -1 ) == -1 && on)\n"
    "            ERR( \"[xaudio2-cb] MADEIRA_XAUDIO2_CALLBACK_ORDER=1: callbacks are published before their count\\n\" );\n"
    "    }\n"
    "    return enabled > 0;\n"
    "}\n"
    "\n"
    "/* the count first, then the array (RegisterForCallbacks publishes them in the opposite order) */\n"
    "static UINT32 xa2_callbacks_snapshot(IXAudio2Impl *This, IXAudio2EngineCallback ***cbs)\n"
    "{\n"
    "    UINT32 n = *(volatile UINT32 *)&This->ncbs;\n"
    "    MemoryBarrier();\n"
    "    *cbs = *(IXAudio2EngineCallback ** volatile *)&This->cbs;\n"
    "    return *cbs ? n : 0;\n"
    "}\n"
    "\n"
)

LOOPS = [
    ("    for(i = 0; i < This->ncbs && This->cbs[i]; ++i)\n"
     "        IXAudio2EngineCallback_OnProcessingPassStart(This->cbs[i]);\n",
     "IXAudio2EngineCallback_OnProcessingPassStart(cbs[i]);"),
    ("    for(i = 0; i < This->ncbs && This->cbs[i]; ++i)\n"
     "        IXAudio2EngineCallback_OnProcessingPassEnd(This->cbs[i]);\n",
     "IXAudio2EngineCallback_OnProcessingPassEnd(cbs[i]);"),
    ("    for(i = 0; i < This->ncbs && This->cbs[i]; ++i)\n"
     "        IXAudio2EngineCallback_OnCriticalError(This->cbs[i], error);\n",
     "IXAudio2EngineCallback_OnCriticalError(cbs[i], error);"),
]


def loop_new(old, call):
    return ("    if (xa2_callback_order())   /* " + MARKER + " */\n"
            "    {\n"
            "        IXAudio2EngineCallback **cbs;\n"
            "        UINT32 n = xa2_callbacks_snapshot(This, &cbs);\n"
            "        for(i = 0; i < n && cbs[i]; ++i)\n"
            "            " + call + "\n"
            "        return;\n"
            "    }\n" + old)


WRITER_OLD = ("    This->ncbs++;\n"
              "    This->cbs = realloc(This->cbs, This->ncbs * sizeof(*This->cbs));\n"
              "\n"
              "    This->cbs[i] = pCallback;\n")
WRITER_NEW = (
    "    if (xa2_callback_order())   /* " + MARKER + " */\n"
    "    {\n"
    "        /* a new array, filled, then published, then the count (i == This->ncbs here). The old array stays allocated: a pass\n"
    "         * on the audio thread may still be reading it. */\n"
    "        IXAudio2EngineCallback **cbs = malloc((This->ncbs + 1) * sizeof(*cbs));\n"
    "        UINT32 k;\n"
    "\n"
    "        if (!cbs)\n"
    "        {\n"
    "            LeaveCriticalSection(&This->lock);\n"
    "            return E_OUTOFMEMORY;\n"
    "        }\n"
    "        for (k = 0; k < This->ncbs; ++k)\n"
    "            cbs[k] = This->cbs[k];\n"
    "        cbs[i] = pCallback;\n"
    "        MemoryBarrier();\n"
    "        *(IXAudio2EngineCallback ** volatile *)&This->cbs = cbs;\n"
    "        MemoryBarrier();\n"
    "        *(volatile UINT32 *)&This->ncbs = i + 1;\n"
    "        LeaveCriticalSection(&This->lock);\n"
    "        return S_OK;\n"
    "    }\n"
    "\n" + WRITER_OLD)


def patch(src):
    if MARKER in src:
        if (src.count(HELPERS + HELPERS_ANCHOR) != 1 or src.count(WRITER_NEW) != 1
                or any(src.count(loop_new(old, call)) != 1 for old, call in LOOPS)):
            raise ValueError("partial or changed xaudio2 callback patch")
        return src
    if src.count(HELPERS_ANCHOR) != 1:
        raise ValueError("the engine-callback section of xaudio_dll.c not found once (Wine changed)")
    for old, _ in LOOPS:
        if src.count(old) != 1:
            raise ValueError("an engine-callback loop of xaudio_dll.c not found once (Wine changed)")
    if src.count(WRITER_OLD) != 1:
        raise ValueError("RegisterForCallbacks' count/realloc/store not found once (Wine changed)")
    reg = src.index("static HRESULT WINAPI IXAudio2Impl_RegisterForCallbacks(")
    if not reg < src.index(WRITER_OLD) < src.index("\n}\n", reg):
        raise ValueError("the count/realloc/store is not in RegisterForCallbacks (Wine changed)")
    out = src.replace(HELPERS_ANCHOR, HELPERS + HELPERS_ANCHOR, 1)
    for old, call in LOOPS:
        out = out.replace(old, loop_new(old, call), 1)
    return out.replace(WRITER_OLD, WRITER_NEW, 1)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__.strip().splitlines()[-1])
    path = Path(sys.argv[1]) / FILE
    source = path.read_text()
    try:
        result = patch(source)
    except ValueError as error:
        sys.exit("patch-wine-xaudio2-callbacks: " + str(error))
    if result == source:
        print("patch-wine-xaudio2-callbacks: already in")
    else:
        path.write_text(result)
        print("wine xaudio2: engine callbacks are published before their count with MADEIRA_XAUDIO2_CALLBACK_ORDER=1 (unchanged otherwise)")

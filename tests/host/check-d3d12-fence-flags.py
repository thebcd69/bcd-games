#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""ID3D12Device::CreateFence accepts the fence flags the API defines, opt-in (bcd.cfg d3d12-fence-flags = 1; build 652; madeira-d3d12; night round 2026-11-11, G8 S12).

skate2-pc.exe (a wgpu / Rust Windows port with a Direct3D 12 backend; Send Log 0XWQCV, build 632, iPhone14,7) creates its fence with
D3D12_FENCE_FLAG_SHARED. madeira-d3d12 refused every flag but NONE ("[madeira-d3d12] FAILED CreateFence flags -> 0x80004001 (1, 0)"), wgpu
dropped its device and the game stopped on "Parent device is lost". The three flags the API defines (SHARED 1, SHARED_CROSS_ADAPTER 2,
NON_MONITORED 4) concern handles shared across processes or adapters and the OS watching the fence; an in-process fence works the same
whatever they say.

KEYED, default off: 14 other archived logs (all Red Dead Redemption 2 sessions, started directly or through its Launcher.exe, builds 626-634) show the
same refusal with flag 1, in pairs, and those sessions play on (RDR2 N1MBVQ: 35.8 FPS average over 339 s on a 16 GB iPad). What such a game does with a fence it is told
was made (CreateSharedHandle is still not implemented) is not known, so only the key changes the answer. Off: every flag but NONE is refused
with the same HRESULT and MAD_FAIL site as before. On: the fence is made as for NONE and remembers the flags (ID3D12Fence1::GetCreationFlags
answers them); a bit outside the three is still refused.

Checks:
  1. the source: the refusal tests the flags against NONE (key off) or the three flags (key on), keeps its MAD_FAIL name and value and comes before
     the allocation; the key is read once, default 0, with a log line only when on; the fence stores the flags; GetCreationFlags returns what was
     stored; the field is appended last;
  2. the real mad_fence_flags_on, device_CreateFence, fence_GetCreationFlags and struct mad_fence are cut out of madeira_d3d12.c and compiled on the
     host with fakes for the object plumbing, and run with the key off (unset, 0) and on (1): off, flags 0 makes a fence and EVERY other value (1..7,
     8, 0x10, 0x80000001) is refused with E_NOTIMPL, a MAD_FAIL line naming it and no object; on, flags 0..7 make a fence whose GetCreationFlags is
     what was given and 8, 0x10 and 0x80000001 are still refused; the key logs one line when on, none when off; the first four flagged creations
     log one line each;
  3. the auto-tune rule (line d3d12-fence-flags = 1, only for a session with wgpu's "Parent device is lost") and its fixture are there; CI runs this check.
Needs python3 and a C compiler (SKIP of part 2 without one).
"""
from pathlib import Path
import os
import shutil
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[2]
src = (root / "madeira-d3d12/src/pe/madeira_d3d12.c").read_text()
ok = True


def check(what, cond, detail=""):
    global ok
    print(("ok   " if cond else "FAIL ") + what)
    if not cond:
        ok = False
        if detail:
            print("     " + str(detail)[:1500])


def cut(text, start, end):
    a = text.index(start)
    return text[a:text.index(end, a) + len(end)]


struct = cut(src, "#define MAD_FENCE_WAITERS 16\nstruct mad_fence {", "\n};\n")
getflags = cut(src, "static D3D12_FENCE_FLAGS STDMETHODCALLTYPE fence_GetCreationFlags(", "\n")
create = cut(src, "static HRESULT STDMETHODCALLTYPE device_CreateFence(", "\n}\n")
macro = cut(src, "#define MAD_FAIL(what, hr, a, b)", "while (0)\n")
keyfn = cut(src, "static int g_fence_flags = -1;\nstatic int mad_fence_flags_on(void) {", "\n}\n")

# ---- 1. the source
check("the refusal tests the flags against NONE (key off) or the three flags (key on), keeps its site name and value",
      "if (flags & ~(mad_fence_flags_on() ? (D3D12_FENCE_FLAG_SHARED | D3D12_FENCE_FLAG_SHARED_CROSS_ADAPTER | D3D12_FENCE_FLAG_NON_MONITORED)\n"
      "                                       : D3D12_FENCE_FLAG_NONE))\n"
      '        MAD_FAIL("CreateFence flags", E_NOTIMPL, flags, 0);' in create
      and "if (flags != D3D12_FENCE_FLAG_NONE)" not in create)
check("the refusal comes before the allocation (a refused call makes no object) and the flags are stored on the fence",
      create.index("MAD_FAIL(") < create.index("calloc(1, sizeof *f)") < create.index("f->flags = flags;"))
check("the key d3d12-fence-flags is read once through mad_cfg_int_pe, default 0, one line when on; the name is read nowhere else",
      'g_fence_flags = mad_cfg_int_pe("d3d12-fence-flags", 0) ? 1 : 0;' in keyfn and "if (g_fence_flags)" in keyfn and "return g_fence_flags > 0;" in keyfn
      and src.count('"d3d12-fence-flags"') == 1)
check("GetCreationFlags answers the stored flags; the field is appended last (nothing else of the object moves)",
      "return ((struct mad_fence *)This)->flags; }" in getflags
      and struct.rstrip().splitlines()[-2].strip().startswith("D3D12_FENCE_FLAGS flags;"))
check("the vtable slot still points at fence_GetCreationFlags", "g_fence_vtbl1.GetCreationFlags      = fence_GetCreationFlags;" in src)

# ---- 2. the real code on the host
harness = r'''
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
typedef long HRESULT; typedef long LONG; typedef unsigned UINT; typedef uint64_t UINT64; typedef long long LONG64, LONGLONG; typedef void *HANDLE;
typedef const void *REFIID; typedef struct { int dummy; } CRITICAL_SECTION, CONDITION_VARIABLE; typedef struct ID3D12FenceVtbl { int dummy; } ID3D12FenceVtbl; typedef unsigned long ULONG;
typedef struct { int dummy; } IID; struct mad_queue;
#define STDMETHODCALLTYPE
#define S_OK ((HRESULT)0)
#define E_INVALIDARG ((HRESULT)0x80070057L)
#define E_OUTOFMEMORY ((HRESULT)0x8007000EL)
#define E_NOTIMPL ((HRESULT)0x80004001L)
typedef enum D3D12_FENCE_FLAGS { D3D12_FENCE_FLAG_NONE = 0x0, D3D12_FENCE_FLAG_SHARED = 0x1, D3D12_FENCE_FLAG_SHARED_CROSS_ADAPTER = 0x2,
                                 D3D12_FENCE_FLAG_NON_MONITORED = 0x4 } D3D12_FENCE_FLAGS;
typedef struct ID3D12Device { int dummy; } ID3D12Device; typedef struct ID3D12Fence { int dummy; } ID3D12Fence; typedef struct ID3D12Fence1 { int dummy; } ID3D12Fence1;
#define InterlockedIncrement(p) (__sync_add_and_fetch((p), 1))
static int nlog; static char lastlog[512], faillog[512];
static void d3d12_log(const char *fmt, ...) { va_list ap; va_start(ap, fmt); vsnprintf(lastlog, sizeof lastlog, fmt, ap); va_end(ap); nlog++; }
static void mad_fail_log(const char *what, HRESULT hr, unsigned a, unsigned b)
{ snprintf(faillog, sizeof faillog, "%s -> 0x%08lx (%u, %u)", what, (unsigned long)hr, a, b); }
static long long mad_cfg_int_pe(const char *k, long long d)
{ const char *v = strcmp(k, "d3d12-fence-flags") ? 0 : getenv("FENCEFLAGS_KEY"); return v ? atoll(v) : d; }
static void InitializeCriticalSection(CRITICAL_SECTION *c) { (void)c; }
static void InitializeConditionVariable(CONDITION_VARIABLE *c) { (void)c; }
static const IID IID_ID3D12Fence; static ID3D12FenceVtbl g_fence_vtbl;
''' + macro + struct + "\n" + keyfn + r'''
static struct mad_fence *made;
static HRESULT STDMETHODCALLTYPE fence_QI(ID3D12Fence *This, REFIID riid, void **out) { (void)riid; made = (struct mad_fence *)This; *out = This; return S_OK; }
static ULONG STDMETHODCALLTYPE fence_Release(ID3D12Fence *This) { (void)This; return 1; }   /* the harness frees what it made */
''' + getflags + "\n" + create + r'''
int main(void)
{
    static const unsigned flags[] = { 0, 1, 2, 3, 4, 5, 6, 7, 8, 0x10, 0x80000001u };
    unsigned i;
    for (i = 0; i < sizeof flags / sizeof flags[0]; i++) {
        void *out = 0; HRESULT hr; unsigned got = 99; int logged = nlog;
        made = 0; faillog[0] = 0;
        hr = device_CreateFence((ID3D12Device *)0, 5, (D3D12_FENCE_FLAGS)flags[i], (REFIID)0, &out);
        if (made) got = (unsigned)fence_GetCreationFlags((ID3D12Fence1 *)made);
        printf("flags=%#x hr=0x%08lx made=%d out=%d value=%llu got=%u log=%d fail=[%s]\n", flags[i], (unsigned long)hr, made != 0, out != 0,
               made ? (unsigned long long)made->value : 0ull, got, nlog - logged, faillog);
        if (made) free(made);
    }
    {   /* the log is limited to four flagged creations */
        int n0 = nlog, k;
        for (k = 0; k < 10; k++) { void *out = 0; made = 0; device_CreateFence((ID3D12Device *)0, 0, D3D12_FENCE_FLAG_SHARED, (REFIID)0, &out); if (made) free(made); }
        printf("ten more flagged creations logged %d lines\n", nlog - n0);
    }
    printf("last log: %s", nlog ? lastlog : "(none)\n");
    return 0;
}
'''
cc = os.environ.get("CC") or shutil.which("cc") or shutil.which("clang") or shutil.which("gcc")
if not cc:
    print("SKIP: no C compiler for the CreateFence harness")
else:
    with tempfile.TemporaryDirectory() as tmp:
        c = Path(tmp) / "fence.c"
        c.write_text(harness)
        exe = Path(tmp) / "fence"
        b = subprocess.run([cc, "-std=gnu11", "-Wall", "-Wno-unused-function", "-Wno-unused-variable", "-o", str(exe), str(c)], capture_output=True, text=True)
        check("the real key reader, CreateFence, GetCreationFlags and struct mad_fence compile against stubs (-Wall)", b.returncode == 0 and "warning" not in b.stderr, b.stderr[-2500:])
        if b.returncode == 0:
            def run(key):
                env = {k: v for k, v in os.environ.items() if k != "FENCEFLAGS_KEY"}
                if key is not None:
                    env["FENCEFLAGS_KEY"] = key
                out = subprocess.run([str(exe)], capture_output=True, text=True, timeout=60, env=env).stdout
                rows = {}
                for ln in out.splitlines():
                    if ln.startswith("flags="):
                        kv = dict(p.split("=", 1) for p in ln.split(" ") if "=" in p and not p.startswith("fail"))
                        rows[int(kv["flags"], 16)] = (kv, ln)
                return out, rows
            for key in (None, "0"):
                out, rows = run(key)
                tag = "key unset" if key is None else f"key {key!r}"
                kv, ln = rows.get(0, ({}, ""))
                check(f"{tag}: flags 0 makes a fence as before (S_OK, GetCreationFlags 0, initial value 5)",
                      kv.get("hr") == "0x00000000" and kv.get("made") == "1" and kv.get("got") == "0" and kv.get("value") == "5", ln)
                check(f"{tag}: every other flag value (1..7, 8, 0x10, 0x80000001) is refused as before: E_NOTIMPL, no object, the MAD_FAIL line names the site and the value",
                      all(rows[f][0].get("hr") == "0x80004001" and rows[f][0].get("made") == "0" and rows[f][0].get("out") == "0"
                          and f"fail=[CreateFence flags -> 0x80004001 ({f}, 0)]" in rows[f][1] for f in (1, 2, 3, 4, 5, 6, 7, 8, 0x10, 0x80000001)), out[:900])
                check(f"{tag}: nothing is logged by the key or by a created fence", "d3d12-fence-flags = 1" not in out and "last log: (none)" in out, out[-300:])
            out, rows = run("1")
            for f in (0, 1, 2, 3, 4, 5, 6, 7):
                kv, ln = rows.get(f, ({}, ""))
                check(f"key 1: flags {f}: a fence is made (S_OK), it remembers the flags and GetCreationFlags answers {f}, its value is the initial one",
                      kv.get("hr") == "0x00000000" and kv.get("made") == "1" and kv.get("out") == "1" and kv.get("got") == str(f) and kv.get("value") == "5", ln)
            for f in (8, 0x10, 0x80000001):
                kv, ln = rows.get(f, ({}, ""))
                check(f"key 1: flags {f:#x} are still refused with E_NOTIMPL, no object, the MAD_FAIL line names the site",
                      kv.get("hr") == "0x80004001" and kv.get("made") == "0" and kv.get("out") == "0"
                      and f"fail=[CreateFence flags -> 0x80004001 ({f}, 0)]" in ln, ln)
            check("key 1: the key logs one line (at the first creation); flags 0 log nothing else; the first four flagged creations (flags 1 to 4) log one line each, "
                  "the fifth and later none (also not in ten more)",
                  rows[0][0]["log"] == "1" and all(rows[f][0]["log"] == "1" for f in range(1, 5)) and all(rows[f][0]["log"] == "0" for f in range(5, 8))
                  and "ten more flagged creations logged 0 lines" in out, out[:900])
            check("key 1: the line says what was given and that sharing is not supported",
                  "last log: [madeira-d3d12] fence created with flags 4 (NON_MONITORED): it works inside this process; sharing it with another process or adapter is not supported" in out, out[-400:])

# ---- 3. rule, fixture, CI
rules = (root / "build/tools/autotune_rules.py").read_text()
r0 = rules.find('"id": "d3d12-fence-shared-flag"')
rb = rules[r0:rules.index("\n    },\n", r0)] if r0 >= 0 else ""
check("the auto-tune rule adds d3d12-fence-flags = 1 (no fixedIn, no noFix), only for a session that also shows wgpu's 'Parent device is lost', and its fixture is there",
      '"lines": ["d3d12-fence-flags = 1"]' in rb and '"fixedIn"' not in rb and '"noFix"' not in rb and 's("Parent device is lost")' in rb
      and (root / "tests/fixtures/autotune/skate2-fence-shared-flag-2026-11-06.expect").is_file())
ci = (root / ".github/workflows/build-ipa.yml").read_text()
check("CI runs this check", "python3 tests/host/check-d3d12-fence-flags.py" in ci)

print("PASS: d3d12 fence flags" if ok else "FAILED: d3d12 fence flags")
sys.exit(0 if ok else 1)

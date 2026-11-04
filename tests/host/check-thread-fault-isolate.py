#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""env.MADEIRA_THREAD_FAULT_ISOLATE: a listed DLL's own thread that dies unhandled ends only itself (round 630, agent G3 S4).

TEKKEN 7 (37CPHE, FQEKA1, 1TQHPW, HFT0B5): NVIDIA's GfeSDK.dll starts a worker thread in NVGSDK_Create; Create fails without GeForce
Experience, the object is freed, and the leftover thread enters the freed critical section about 2 s later: the unhandled c0000008 ended
the game, which played at 57 FPS.

Checks:
  - the key's parser and the name match, cut from thread_ios.c and built on the host: 1-4 names ending .dll, case-insensitive, any bad
    name turns the key off; unset or empty: off;
  - NtCreateThreadEx records a thread's entry point by its TEB only with the key, right after the TEB is made; the table has fixed size;
  - NtTerminateProcess ends only the calling thread when, and only when: the handle is the current process, the status is an NTSTATUS
    error, the key is on, the thread's recorded entry point lies in a module whose name is listed; one [thread-isolate] line; the branch
    comes before everything else of the function;
  - catalog (text, default empty), list policy (the value rule), the TEKKEN 7 row (from build 630), auto-tune's rule and its fixture, CI.
"""
from pathlib import Path
import json, re, shutil, subprocess, sys, tempfile

root = Path(__file__).resolve().parents[2]
thread = (root / "build/ntdll-unix/thread_ios.c").read_text()
process = (root / "build/ntdll-unix/process_ios.c").read_text()
ok = True


def check(what, cond, detail=""):
    global ok
    print(("ok   " if cond else "FAIL ") + what + (("\n     " + str(detail)[:1800]) if (detail and not cond) else ""))
    ok = ok and bool(cond)


block = thread[thread.index("/* thread-isolate-test:begin"):thread.index("/* thread-isolate-test:end */")]
harness = r'''
#include <stdio.h>
#include <string.h>
#include <stdint.h>
''' + block + r'''
int main( void )
{
    static const char *values[] = { "GfeSDK.dll", "GfeSDK.dll,nvapi64.DLL", "a.dll,b.dll,c.dll,d.dll", "a.dll,b.dll,c.dll,d.dll,e.dll",
                                     "", "GfeSDK", ".dll", "Gfe SDK.dll", "GfeSDK.dll,", ",GfeSDK.dll", "x.exe",
                                     "@A60@.dll", "@A61@.dll" };
    char list[4][65];
    unsigned int i;
    for (i = 0; i < sizeof(values) / sizeof(values[0]); i++) printf( "%d ", ios_thr_isolate_parse( values[i], list, 4 ) );
    printf( "%d\n", ios_thr_isolate_parse( NULL, list, 4 ) );
    ios_thr_isolate_parse( "GfeSDK.dll,nvapi64.DLL", list, 4 );
    printf( "%d %d %d %d %d %d\n", ios_thr_isolate_match( "GfeSDK.dll", list, 2 ), ios_thr_isolate_match( "gfesdk.DLL", list, 2 ),
            ios_thr_isolate_match( "NVAPI64.dll", list, 2 ), ios_thr_isolate_match( "GfeSDK2.dll", list, 2 ),
            ios_thr_isolate_match( "", list, 2 ), ios_thr_isolate_match( NULL, list, 2 ) );
    return 0;
}
'''
harness = harness.replace("@A60@", "a" * 60).replace("@A61@", "a" * 61)   # names of 64 and 65 characters
cc = shutil.which("cc") or shutil.which("clang")
if not cc:
    print("skip the compiled part: no C compiler")
else:
    with tempfile.TemporaryDirectory(prefix="madeira-thr-isolate-") as d:
        d = Path(d)
        (d / "t.c").write_text(harness)
        b = subprocess.run([cc, "-std=gnu11", "-Wall", "-Wextra", "-Werror", "-Wno-unused-function", "-Wno-unused-variable", str(d / "t.c"),
                            "-o", str(d / "t")], capture_output=True, text=True)
        check("the parser and the match build on the host (warnings are errors)", b.returncode == 0, b.stderr[-1500:])
        if b.returncode == 0:
            out = subprocess.run([str(d / "t")], capture_output=True, text=True).stdout.splitlines()
            check("1-4 names ending .dll count; 5 names, a bad name, an empty name, a name over 64 characters or none turn the key off",
                  out[0] == "1 2 4 0 0 0 0 0 0 0 0 1 0 0", out)
            check("the match is by file name, any case, exact", out[1] == "1 1 1 0 0 0", out)

create = thread[thread.index("NTSTATUS WINAPI NtCreateThreadEx( HANDLE *handle, ACCESS_MASK access, OBJECT_ATTRIBUTES *attr,"):]
create = create[:create.index("\n}\n")]
check("NtCreateThreadEx records the entry point only with the key, right after the TEB is made",
      create.count("if (ios_thr_isolate_on()) ios_thr_entry_note( (uintptr_t)teb, (uintptr_t)start );") == 1
      and create.index("if ((status = virtual_alloc_teb( &teb )))") < create.index("ios_thr_entry_note(")
      < create.index("init_thread_stack( teb,"))
check("the table has a fixed size (one slot a TEB, overwritten by a new thread on it)",
      "#define IOS_THR_ENTRY_SLOTS 1024" in thread and "static struct { uintptr_t teb, start; } ios_thr_entry[IOS_THR_ENTRY_SLOTS];" in thread
      and "% IOS_THR_ENTRY_SLOTS" in thread)
term = process[process.index("NTSTATUS WINAPI NtTerminateProcess( HANDLE handle, LONG exit_code )\n{"):]
term = term[:term.index("\n}\n")]
branch = term[term.index("    if (handle == NtCurrentProcess() && ((unsigned int)exit_code & 0xc0000000u) == 0xc0000000u && ios_thr_isolate_on())"):]
branch = branch[:branch.index("\n    }\n") + 7]
check("NtTerminateProcess: only the current process, an NTSTATUS error, the key on, a recorded entry point in a listed module; right after the "
      "NULL handle's exit record (another handle), before the [term-stack] dump",
      "uintptr_t start = ios_thr_entry_start( (uintptr_t)NtCurrentTeb() );" in branch
      and "unsigned long long msz = 0, mbase = start ? ios_jit_module_base_for_va( start, &msz ) : 0;" in branch
      and "if (name && ios_thr_isolate_listed( name ))" in branch
      and "return NtTerminateThread( NtCurrentThread(), exit_code );" in branch and branch.count("dprintf( 2,") == 1
      and "[thread-isolate] r630 tid=%04x start=%s+0x%llx: unhandled %08x ends this thread only" in branch
      and term.index("if (!handle) ios_exiting_record(") < term.index("ios_thr_isolate_on())") < term.index("static int term_log_count = 0;"))

cat = (root / "build/tools/gen-config-catalog.py").read_text()
check("the catalog lists env.MADEIRA_THREAD_FAULT_ISOLATE (text, default empty)",
      re.search(r'"env\.MADEIRA_THREAD_FAULT_ISOLATE": \{[^}]*"kind": "text"[^}]*"default": ""', cat, re.S) is not None)
pol = (root / "app/Madeira/GameRecommendationsList.swift").read_text()
check("the list policy allows the key with 1-4 DLL names", '"MADEIRA_THREAD_FAULT_ISOLATE",' in pol and 'case "MADEIRA_THREAD_FAULT_ISOLATE":' in pol
      and "MADEIRA_THREAD_FAULT_ISOLATE is 1 to 4 DLL names" in pol)
recs = json.loads((root / "recommended/recommendations.json").read_text())
tekken = next((r for r in recs["rules"] if r["id"] == "tekken-7"), None)
check("TEKKEN 7's row carries the key for GfeSDK.dll from build 630",
      tekken is not None and {"line": "env.MADEIRA_THREAD_FAULT_ISOLATE = GfeSDK.dll", "minBuild": 630} in tekken["config"], tekken)
rules = (root / "build/tools/autotune_rules.py").read_text()
rule = rules[rules.index('"id": "gfesdk-worker-thread-fault"'):]
rule = rule[:rule.index("\n    },\n")]
check("auto-tune's gfesdk-worker-thread-fault offers the line after exactly that chain, once",
      '"lines": ["env.MADEIRA_THREAD_FAULT_ISOLATE = GfeSDK.dll"]' in rule and '"noRetry": True' in rule
      and 'Error creating message bus interface' in rule and 'status=c0000008' in rule)
fx = root / "tests/fixtures/autotune/tekken-7-gfesdk-worker-fault-2026-11-02.expect"
check("its fixture (37CPHE) proposes the line", fx.is_file() and json.loads(fx.read_text())["next"] == ["env.MADEIRA_THREAD_FAULT_ISOLATE = GfeSDK.dll"])
wf = (root / ".github/workflows/build-ipa.yml").read_text()
check("CI runs this check", "python3 tests/host/check-thread-fault-isolate.py" in wf)
print("PASS: thread fault isolation (MADEIRA_THREAD_FAULT_ISOLATE)" if ok else "FAILED: thread fault isolation")
sys.exit(0 if ok else 1)

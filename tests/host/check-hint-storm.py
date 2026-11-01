#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""env.MADEIRA_HINT_STORM_N (opt-in, round 626, agent U2 S5; Spelunky 2 ZR4NTC, 9BHTK8 build 623 and 8 archive logs, all iPad16,3).

From fmod.dll on, one thread of Spelunky 2 asks again and again for a 32 MB reservation hinted at a random address in the top TiB of an
8 TiB space; BCD's address map ends at 0x8000000000, so every request is refused (c0000018, up to 225 million times) and the main
thread never leaves its loop. With the key (0 = off, the default; 256..65536) NtAllocateVirtualMemory serves such a request without its
hint after N consecutive refusals on one thread of reservations of the same size hinted above the map's end.

Checks: the wrapper calls the counter only with the key, for this process, and counts only reserves hinted at or above
host_addr_space_limit that were refused with STATUS_CONFLICTING_ADDRESSES; the retry asks without a hint and only its success replaces
the answer; the parser and the counter, cut from build/ntdll-unix/virtual_ios.c and run on the host: unset, "", "0", "255", "65537",
"12x" -> off; "256", "4096", "65536" -> on; N-1 refusals do not serve, the N-th does and the following ones too; a success of the
thread's own resets; a new size starts again; a refusal of another kind is not counted and does not reset; the list policy, catalog,
Spelunky 2's row, auto-tune's rule and its fixture; CI.
"""
from pathlib import Path
import json, re, shutil, subprocess, sys, tempfile

root = Path(__file__).resolve().parents[2]
SRC = (root / "build/ntdll-unix/virtual_ios.c").read_text()
ok = True


def check(what, cond, detail=""):
    global ok
    print(("ok   " if cond else "FAIL ") + what + (("\n     " + str(detail)[:1500]) if (detail and not cond) else ""))
    ok = ok and bool(cond)


region = SRC[SRC.index("/* hint-storm-test:begin"):SRC.index("/* hint-storm-test:end */")]
w = SRC[SRC.index("NTSTATUS WINAPI NtAllocateVirtualMemory( HANDLE process, PVOID *ret, ULONG_PTR zero_bits,"):]
w = w[:w.index("\n}\n")]
check("the key is read once (MADEIRA_HINT_STORM_N)", 'v = (int)ios_hint_storm_parse( getenv( "MADEIRA_HINT_STORM_N" ) );' in SRC)
check("the wrapper counts only with the key, for this process, and only reserves hinted above the map's end that were refused",
      "if (hs_limit && process == NtCurrentProcess() && ret && size_ptr)" in w
      and "int refused = status == STATUS_CONFLICTING_ADDRESSES && hint && (type & MEM_RESERVE) &&" in w
      and "(ULONG_PTR)hint >= (ULONG_PTR)host_addr_space_limit;" in w)
check("the retry asks without a hint and only its success replaces the answer",
      "PVOID again = NULL;" in w and "ios_nt_impl_NtAllocateVirtualMemory( process, &again, 0, &again_size, type, protect );" in w
      and "if (!NT_ERROR( st2 ))" in w and "*ret = again;" in w)
check("the old failure log stays for every failure", "if (NT_ERROR( status ))\n        ios_ntfail_alloc( \"NtAllocateVirtualMemory\"" in w)
HARNESS = r'''
#include <stdio.h>
#include <stdlib.h>
''' + region + r'''
int main( void )
{
    int fails = 0; unsigned i; struct ios_hint_storm hs = { 0, 0 };
#define T(c) do { if (!(c)) { printf( "FAIL line %d: %s\n", __LINE__, #c ); fails++; } } while (0)
    T( !ios_hint_storm_parse( NULL ) ); T( !ios_hint_storm_parse( "" ) ); T( !ios_hint_storm_parse( "0" ) );
    T( !ios_hint_storm_parse( "255" ) ); T( !ios_hint_storm_parse( "65537" ) ); T( !ios_hint_storm_parse( "12x" ) );
    T( ios_hint_storm_parse( "256" ) == 256 ); T( ios_hint_storm_parse( "4096" ) == 4096 ); T( ios_hint_storm_parse( "65536" ) == 65536 );
    for (i = 1; i < 4096; i++) T( !ios_hint_storm_note( &hs, 1, 0, 0x2000000, 4096 ) );   /* N-1 refusals: not served */
    T( ios_hint_storm_note( &hs, 1, 0, 0x2000000, 4096 ) );                                /* the N-th is served */
    T( ios_hint_storm_note( &hs, 1, 0, 0x2000000, 4096 ) );                                /* and the next ones (a served one does not reset) */
    T( !ios_hint_storm_note( &hs, 0, 0, 0, 4096 ) && hs.n >= 4096 );                         /* another failure: not counted, no reset */
    T( !ios_hint_storm_note( &hs, 1, 0, 0x2010000, 4096 ) && hs.n == 1 );                    /* a new size starts again */
    T( !ios_hint_storm_note( &hs, 0, 1, 0x1000, 4096 ) && hs.n == 0 );                       /* the thread's own success resets */
    puts( fails ? "FAILED" : "ok: off unless 256..65536; served from the N-th refusal on; a success resets" );
    return fails != 0;
}
'''
cc = shutil.which("cc") or shutil.which("clang")
if cc:
    with tempfile.TemporaryDirectory() as tmp:
        c = Path(tmp) / "t.c"
        c.write_text(HARNESS)
        exe = Path(tmp) / "t"
        r = subprocess.run([cc, "-O1", "-Wall", "-Werror", "-Wno-unused-function", "-o", str(exe), str(c)], capture_output=True, text=True)
        check("the parser and the counter compile on the host", r.returncode == 0, r.stderr[-2000:])
        if r.returncode == 0:
            run = subprocess.run([str(exe)], capture_output=True, text=True)
            print("     " + run.stdout.strip().replace("\n", "\n     "))
            check("the parser and the counter behave as documented", run.returncode == 0, run.stdout)
else:
    print("note: no host C compiler; the counter was not run")

pol = (root / "app/Madeira/GameRecommendationsList.swift").read_text()
check("a list may set the key, 0 or 256..65536", '"MADEIRA_HINT_STORM_N",' in pol and 'case "MADEIRA_HINT_STORM_N":' in pol)
cat = (root / "app/Madeira/ConfigCatalog.generated.swift").read_text()
check("the catalog documents it, off by default", re.search(r'key: "env\.MADEIRA_HINT_STORM_N"[^\n]*kind: \.int, defaultValue: "0"', cat) is not None)
recs = json.loads((root / "recommended/recommendations.json").read_text())
carriers = sorted(r["id"] for r in recs["rules"] if "MADEIRA_HINT_STORM_N" in json.dumps(r["config"]))
sp = next((r for r in recs["rules"] if r["id"] == "spelunky-2"), None)
check("only Spelunky 2's row sets it (4096, from build 626)", carriers == ["spelunky-2"] and sp is not None
      and {"line": "env.MADEIRA_HINT_STORM_N = 4096", "minBuild": 626} in sp["config"], carriers)
rules = (root / "build/tools/autotune_rules.py").read_text()
r = rules[rules.index('"id": "hinted-reserve-storm"'):] if '"id": "hinted-reserve-storm"' in rules else ""
r = r[:r.index("\n    },")] if r else ""
check("auto-tune adds the line after the refusal storm", '"lines": ["env.MADEIRA_HINT_STORM_N = 4096"]' in r and "count=200" in r
      and '[hint-storm] ' in r, r[:400])
exp = root / "tests/fixtures/autotune/spelunky-2-hinted-reserve-storm-2026-10-30.expect"
check("the fixture is there and expects the line", exp.is_file() and json.loads(exp.read_text()).get("next") == ["env.MADEIRA_HINT_STORM_N = 4096"])
wf = (root / ".github/workflows/build-ipa.yml").read_text()
check("CI runs this check", "python3 tests/host/check-hint-storm.py" in wf)
print("PASS: hinted reservation storm" if ok else "FAILED: hinted reservation storm")
sys.exit(0 if ok else 1)

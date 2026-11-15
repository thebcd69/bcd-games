#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""cs-concurrent = 1 (off unless set): the compute encoder a dispatch opens is concurrent between barriers
(madeira-d3d12/src/pe/madeira_d3d12.c; from upstream Madeira's codex/d3d12-consolidation, 984ed082 and 826a9678, opt-in there too).

D3D12 orders dispatches only at a UAV or transition barrier, and every barrier ends the compute encoder here; a serial Metal
encoder still makes each dispatch wait for the one before it. With the switch the encoder exec_dispatch opens is created with
MTLDispatchTypeConcurrent, except in fence-chain mode 6, which keeps the encoder open across barriers. A game that relies on the
order of two dispatches with no barrier between them would render wrongly, so it stays off unless set.

Checks:
  - the source: the switch is read once through mad_cfg_int_pe with default 0, published with a compare-exchange (two threads
    replaying at once agree, one line), and it is the only thing that can make a compute encoder concurrent: every other
    encoder the runtime opens passes `false`, as before; the barrier still ends the compute encoder in every mode but 6;
  - on the host: the statements of exec_dispatch that read the switch, cut from the source, for the switch unset, 0, 1, 2, -1 and every fence mode 0..6: false whenever
    the switch is unset or 0, true for a set switch except in mode 6; read once; one log line only when on; two racing first
    readers get the same answer;
  - the backend honours the flag (when the dxmt tree is present);
  - no recommended list or built-in rule sets the key (The Last of Us Part I and every other game run with it off);
  - the catalog entry (default 0), the credit, and CI runs this check; the whole runtime compiles in the full tree.
"""
import json
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

R = pathlib.Path(__file__).resolve().parents[2]
ok = True


def check(name, cond, detail=""):
    global ok
    print(("ok   " if cond else "FAIL ") + name)
    if not cond:
        ok = False
        if detail:
            print("     " + str(detail)[:2000])


SRC = (R / "madeira-d3d12/src/pe/madeira_d3d12.c").read_text()
disp = SRC[SRC.index("static void exec_dispatch(struct mad_exec *e, const struct mad_cmd *c) {"):]
disp = disp[:disp.index("\n}\n")]
a = disp.index("        LONG cs_concurrent = InterlockedCompareExchange(&g_cs_concurrent, 0, 0);")
CALL = "        e->cenc = MTLCommandBuffer_computeCommandEncoder(e->cb, cs_concurrent > 0 && e->mode != 6); if (e->cenc) g_enc_seq++;\n"
fn = disp[a:disp.index(CALL)]   # the statements that read the switch, as they stand in exec_dispatch

# --- the source ---------------------------------------------------------------
check("the switch is read through mad_cfg_int_pe, default 0, any other value is 1",
      'mad_cfg_int_pe("cs-concurrent", 0) ? 1 : 0;' in fn and SRC.count('"cs-concurrent"') == 1)
check("...once, published with a compare-exchange from -1; the line is printed by the thread that published it, only when on",
      "static volatile LONG g_cs_concurrent = -1;" in SRC and SRC.count("g_cs_concurrent") == 3
      and "InterlockedCompareExchange(&g_cs_concurrent, configured, -1);" in fn and "cs_concurrent = previous < 0 ? configured : previous;" in fn
      and "if (previous < 0 && configured)" in fn)
code = "\n".join(ln for ln in SRC.splitlines() if not ln.lstrip().startswith(("*", "/*")))
calls = re.findall(r"MTLCommandBuffer_computeCommandEncoder\(([^;]*?)\);", code)
calls = [c for c in calls if not c.startswith("cb_")]   # the wrapper macro itself
args = sorted(c.split(",", 1)[1].strip() for c in calls)
check("exec_dispatch's encoder is the only one that can be concurrent; the other four pass false, as before",
      args == ["cs_concurrent > 0 && e->mode != 6", "false", "false", "false", "false"], args)
check("...and it is in exec_dispatch, where the encoder is opened after the open pass or encoder was ended",
      disp.count(CALL) == 1 and disp.index("exec_end(e);") < disp.index("LONG cs_concurrent = ") < disp.index(CALL))
bar = SRC[SRC.index("        case MC_BARRIER:"):]
bar = bar[:bar.index("        case MC_QUERY_BEGIN:")]
check("a ResourceBarrier still ends an open compute encoder in every mode but 6 (what makes the dispatches of one encoder independent)",
      "if (e.cenc || e.benc || (g_barrier_render && e.renc)) { if (e.renc) InterlockedIncrement(&g_barrier_renc_closed); exec_end(&e); }" in bar
      and bar.index("if (e.mode == 6) {") < bar.index("if (e.cenc || e.benc || (g_barrier_render && e.renc))"))
wrap = SRC[SRC.index("static obj_handle_t mad_es_cenc(obj_handle_t cb, bool concurrent) {"):]
check("the encoder wrapper passes the flag on", "(MTLCommandBuffer_computeCommandEncoder)(cb, concurrent);" in wrap[:wrap.index("\n}\n")])
unix = R / "dxmt/src/winemetal/unix/winemetal_unix.c"
if unix.exists():
    check("the backend opens a concurrent encoder only when the flag is set",
          "computeCommandEncoderWithDispatchType:params->arg ? MTLDispatchTypeConcurrent : MTLDispatchTypeSerial]" in unix.read_text())
else:
    print("note: dxmt is not in this tree; the backend's use of the flag is checked in the full tree")

# --- on the host ----------------------------------------------------------------
HARNESS = r'''
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdarg.h>
typedef int LONG;
static int g_key = -1, g_reads, g_logs, g_race;
static LONG InterlockedCompareExchange(volatile LONG *p, LONG x, LONG c);
static long long mad_cfg_int_pe(const char *key, long long dflt) {
    g_reads++;
    if (strcmp(key, "cs-concurrent")) { puts("wrong key"); exit(1); }
    return g_key == -1000 ? dflt : g_key;
}
static void d3d12_log(const char *fmt, ...) { (void)fmt; g_logs++; }
static volatile LONG g_cs_concurrent = -1;
struct mad_exec { int mode; };
/* the statements of exec_dispatch that decide the encoder's kind, and the second argument it passes */
static int encoder_is_concurrent(struct mad_exec *e) {
''' + fn + r'''
    return cs_concurrent > 0 && e->mode != 6;
}
static int mad_cs_concurrent(void) { struct mad_exec e = { 0 }; return encoder_is_concurrent(&e); }
/* the second argument of the compare-exchange that publishes: a racing thread may have published first */
static LONG InterlockedCompareExchange(volatile LONG *p, LONG x, LONG c) {
    LONG o;
    if (g_race && c == -1 && *p == -1) { g_race = 0; *p = 1; }   /* another thread read the same file and won */
    o = *p; if (o == c) *p = x; return o;
}
int main(int argc, char **argv) {
    int mode, expect_on, fails = 0, i;
    g_key = argc > 1 ? atoi(argv[1]) : -1000;
    expect_on = argc > 1 && atoi(argv[1]) != 0;
    if (argc > 2) {   /* the race: this thread reads the switch as set while another publishes 1 first */
        g_race = 1;
        if (mad_cs_concurrent() != 1 || g_logs != 0 || g_reads != 1) { puts("FAILED race"); return 1; }
        puts("race: the loser takes the published value and prints nothing");
        return 0;
    }
    for (i = 0; i < 3; i++)
        for (mode = 0; mode <= 6; mode++) {
            struct mad_exec ex = { mode };
            int concurrent = encoder_is_concurrent(&ex);
            if (concurrent != (expect_on && mode != 6)) { printf("FAILED mode %d\n", mode); fails++; }
        }
    if (g_reads != 1) { printf("FAILED: %d reads\n", g_reads); fails++; }
    if (g_logs != (expect_on ? 1 : 0)) { printf("FAILED: %d log lines\n", g_logs); fails++; }
    printf(fails ? "FAILED\n" : expect_on ? "on: concurrent in modes 0-5, serial in mode 6, read once, one line\n" : "off: serial in every mode, read once, no line\n");
    return fails ? 1 : 0;
}
'''
cc = shutil.which("cc") or shutil.which("clang") or shutil.which("gcc")
if cc:
    with tempfile.TemporaryDirectory() as tmp:
        c = pathlib.Path(tmp) / "t.c"
        c.write_text(HARNESS)
        exe = pathlib.Path(tmp) / "t"
        r = subprocess.run([cc, "-O1", "-Wall", "-Werror", "-Wno-unused-function", "-o", str(exe), str(c)], capture_output=True, text=True)
        check("the switch's function compiles on the host", r.returncode == 0, r.stderr[-3000:])
        if r.returncode == 0:
            for argv, label, want in (([], "unset", "off:"), (["0"], "0", "off:"), (["1"], "1", "on:"), (["2"], "2", "on:"), (["-1"], "-1", "on:")):
                run = subprocess.run([str(exe)] + argv, capture_output=True, text=True)
                print("     [key %s] %s" % (label, run.stdout.strip().splitlines()[-1] if run.stdout.strip() else run.stderr.strip()))
                check("switch %s: %s" % (label, "concurrent except in mode 6" if want == "on:" else "serial encoders, as before"),
                      run.returncode == 0 and run.stdout.strip().startswith(want))
            run = subprocess.run([str(exe), "1", "race"], capture_output=True, text=True)
            check("two first readers at once agree, and only the one that published prints", run.returncode == 0, run.stdout)
else:
    print("note: no host C compiler; the function was not run")

# --- nobody sets it ---------------------------------------------------------------
rules = json.loads((R / "recommended/recommendations.json").read_text())["rules"]
setters = [rule["id"] for rule in rules for item in rule.get("config", [])
           if (item["line"] if isinstance(item, dict) else item).split("=")[0].strip() == "cs-concurrent"]
check("no recommended list sets cs-concurrent (The Last of Us Part I and every other game run with it off)", not setters, setters)
for gen in ("app/Madeira/GameRecommendations.swift", "app/Madeira/AutoTuneRules.generated.swift"):
    p = R / gen
    if p.exists():
        check("no line of %s sets it" % gen, "cs-concurrent = " not in p.read_text())

# --- catalog, credit, CI -------------------------------------------------------------
cat = (R / "build/tools/gen-config-catalog.py").read_text()
entry = cat[cat.index('"cs-concurrent": {'):] if '"cs-concurrent": {' in cat else ""
entry = entry[:entry.index('"sources"')] if entry else ""
check("the settings catalog lists it: Direct3D 12, a bool, off by default", '"category": "Direct3D 12"' in entry and '"kind": "bool"' in entry
      and '"default": "0"' in entry, entry[:300])
notices = (R / "THIRD-PARTY-NOTICES.md").read_text()
check("the credit: THIRD-PARTY-NOTICES names the upstream commits and their authors",
      "`984ed08`" in notices and "`826a967`" in notices and "| Shitiz Garg | " in notices and "intraducine" in notices)
wf = (R / ".github/workflows/build-ipa.yml").read_text()
check("CI runs this check", "python3 tests/host/check-d3d12-cs-concurrent.py" in wf)

mingw = R / "toolchains/llvm-mingw-20260421-ucrt-macos-universal/bin/arm64ec-w64-mingw32-clang"
winemetal = R / "dxmt/src/winemetal"
if mingw.exists() and winemetal.exists():
    r = subprocess.run([str(mingw), "-fsyntax-only", "-O2", "-Wall", '-DMAD_SC_CONVERTER_ID="0000000000000000"',
                        "-I" + str(R / "madeira-d3d12/src/pe"), "-I" + str(R / "madeira-d3d12/src"), "-I" + str(winemetal),
                        str(R / "madeira-d3d12/src/pe/madeira_d3d12.c")], capture_output=True, text=True)
    check("madeira_d3d12.c compiles (arm64ec, -fsyntax-only)", r.returncode == 0, r.stderr[-3000:])
else:
    print("note: llvm-mingw / dxmt not in this tree; the syntax compile runs in the full tree")

sys.exit(0 if ok else 1)

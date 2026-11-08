#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""env.MADEIRA_FEX_KEEP_DEAD_LOADS: FEX keeps an x86 load whose value nothing reads (round 632, Ixion's 2934fa452; opt-in in BCD).

FEX's dead-code pass (DeadFlagCalculationEliminination::EliminateDeadCode) removed LoadMem / LoadMemTSO once nothing read their value,
but an x86 load can fault and programs depend on it: HotSpot stops compiled loops at a safepoint with `test eax,[polling_page]`, whose
only result is flags, then makes the page unreadable; without the load a loop never stops and every collection waits for it (Project
Zomboid, Slay the Spire). Keeping the loads changes the code every program gets (and a stack probe such as MinGW's `test rcx,[rcx]` in
___chkstk_ms would touch its page again), and no BCD log shows the symptom, so in BCD it is opt-in: env.MADEIRA_FEX_KEEP_DEAD_LOADS
is exactly "1", read once at the first dead load the pass meets, one "[fex-keep-loads]" line says it is on. Unset or any other value the pass
removes what it removed before, byte for byte. Windows modules only (_WIN32); the iOS static library is not touched.

Checks (FEX tree: $FEX_SRC, FEX/ of this checkout, ~/madeira-bcd/FEX; skipped without it):
  - the source: the helper and the condition are inside the one _WIN32 block, the gate is read through getenv once (a function-local
    static), the key is compared with exactly "1", the load test is the only addition and comes after the "used or side effects" test;
  - EliminateDeadCode and the helper, cut out of the file and run on the host with stub IR types (the _WIN32 branch under the name
    HARNESS_WIN32): for every combination of operation (LoadMem, LoadMemTSO, an add), uses 0/1, side effects 0/1 and the switch unset, "",
    "0", "1", "11", "true", "2", " 1":
      * key off (anything but "1"): the node is removed exactly when it has no uses and no side effects, loads included (the reference);
      * "1": a load with no uses and no side effects is kept, everything else as in the reference;
      * the log line is printed once with "1" and never otherwise;
      * built without the Windows branch (the iOS static library) the load is removed whatever the switch says;
  - the settings catalog entry (default off), and CI runs this check.
The host run is skipped when no C++ compiler is found.
"""
from pathlib import Path
import itertools, os, re, shutil, subprocess, sys, tempfile

root = Path(__file__).resolve().parents[2]
ok = True


def check(what, cond, detail=""):
    global ok
    print(("ok   " if cond else "FAIL ") + what + (("\n     " + str(detail)[:1500]) if (detail and not cond) else ""))
    ok = ok and bool(cond)


wf = (root / ".github/workflows/build-ipa.yml").read_text()
check("CI runs this check", "python3 tests/host/check-fex-keep-dead-loads.py" in wf)
cat = (root / "build/tools/gen-config-catalog.py").read_text()
entry = cat[cat.index('"env.MADEIRA_FEX_KEEP_DEAD_LOADS"'):] if '"env.MADEIRA_FEX_KEEP_DEAD_LOADS"' in cat else ""
entry = entry[:entry.index("\n    #") if "\n    #" in entry else entry.index("},\n") + 3] if entry else ""
check("the settings catalog lists it, off by default", '"kind": "bool"' in entry and '"default": "0"' in entry, entry[:300])

cands = ([Path(os.environ["FEX_SRC"]).expanduser()] if os.environ.get("FEX_SRC") else []) + [root / "FEX", Path("~/madeira-bcd/FEX").expanduser()]
rel = "FEXCore/Source/Interface/IR/Passes/RedundantFlagCalculationElimination.cpp"
fex = next((c for c in cands if (c / rel).is_file()), None)
if fex is None:
    print("skip the FEX part: no FEX tree (set FEX_SRC)")
    print("PASS: FEX keep dead loads" if ok else "FAILED: FEX keep dead loads")
    sys.exit(0 if ok else 1)

src = (fex / rel).read_text()
check("<cstdlib> is included for getenv", "#include <cstdlib>" in src)
a = src.index("#if defined(_WIN32)\n// madeira-bcd (round 632): keeping an x86 load")
b_head = "bool DeadFlagCalculationEliminination::EliminateDeadCode(IREmitter* IREmit, Ref CodeNode, IROp_Header* IROp) {\n"
check("EliminateDeadCode is there once", src.count(b_head) == 1)
fn_start = src.index(b_head)
fn_end = src.index("\n}\n", fn_start) + 3
cut = src[a:fn_end]
helper = cut[:cut.index("// General purpose dead code elimination")]
fn = src[fn_start:fn_end]

check("the helper is one function-local static read through getenv", helper.count("static const bool On = [] {") == 1 and helper.count('getenv("MADEIRA_FEX_KEEP_DEAD_LOADS")') == 1)
check("only exactly \"1\" turns it on", "Env && Env[0] == '1' && Env[1] == '\\0'" in helper)
check("one log line, only when it is on", helper.count("[fex-keep-loads]") == 1 and "if (Value) {" in helper)
check("the helper sits in a _WIN32 block", helper.startswith("#if defined(_WIN32)\n") and helper.rstrip().endswith("#endif"))
first_test = fn.index("if (CodeNode->GetUses() > 0 || IR::HasSideEffects(IROp->Op)) {")
check("the load test comes after the used-or-side-effects test, inside _WIN32, and needs the switch",
      first_test < fn.index("#if defined(_WIN32)") < fn.index("(IROp->Op == OP_LOADMEM || IROp->Op == OP_LOADMEMTSO) && KeepDeadLoads()")
      < fn.index("#endif") < fn.index("IREmit->Remove(CodeNode);"))
check("nothing else in the function changed: it removes the node and answers true",
      fn.rstrip().endswith("IREmit->Remove(CodeNode);\n  return true;\n}"))

cxx = shutil.which("clang++") or shutil.which("c++") or shutil.which("g++")
if not cxx:
    print("skip the host run: no C++ compiler")
    print("PASS: FEX keep dead loads" if ok else "FAILED: FEX keep dead loads")
    sys.exit(0 if ok else 1)

HARNESS = r'''
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>

enum : int { OP_ADD = 1, OP_LOADMEM = 2, OP_LOADMEMTSO = 3 };
struct Node { int Uses; int GetUses() const { return Uses; } };
using Ref = Node*;
struct IROp_Header { int Op; };
struct IREmitter { int Removed = 0; void Remove(Ref) { Removed++; } };
static int SideEffects = 0;
static int LogLines = 0;
namespace LogMan::Msg { template <typename... A> void IFmt(const char*, A...) { LogLines++; } }
namespace FEXCore::IR {
inline bool HasSideEffects(int) { return SideEffects != 0; }
class DeadFlagCalculationEliminination {
public:
  bool EliminateDeadCode(IREmitter* IREmit, Ref CodeNode, IROp_Header* IROp);
};
@CUT@
} // namespace FEXCore::IR

int main() {
  using namespace FEXCore::IR;
  DeadFlagCalculationEliminination P;
  const int Ops[] = {OP_ADD, OP_LOADMEM, OP_LOADMEMTSO};
  for (int Op : Ops)
    for (int Uses = 0; Uses < 2; Uses++)
      for (int Se = 0; Se < 2; Se++) {
        SideEffects = Se;
        IREmitter E;
        Node N {Uses};
        IROp_Header H {Op};
        const bool Ret = P.EliminateDeadCode(&E, &N, &H);
        std::printf("op=%d uses=%d se=%d removed=%d ret=%d\n", Op, Uses, Se, E.Removed, Ret ? 1 : 0);
      }
  std::printf("log=%d\n", LogLines);
  return 0;
}
'''

values = [None, "", "0", "1", "11", "true", "2", " 1"]
with tempfile.TemporaryDirectory(prefix="madeira-keepdead-") as tmp:
    t = Path(tmp)
    (t / "win.cpp").write_text(HARNESS.replace("@CUT@", cut.replace("#if defined(_WIN32)", "#if defined(HARNESS_WIN32)")))
    for label, flags in (("win", ["-DHARNESS_WIN32"]), ("nowin", [])):
        r = subprocess.run([cxx, "-std=c++20", "-O1", "-Wall", "-Wno-unused-function", "-Wno-unused-parameter"] + flags
                           + [str(t / "win.cpp"), "-o", str(t / label)], capture_output=True, text=True)
        check(f"the {label} build of the pass compiles on the host", r.returncode == 0, r.stderr)
        if r.returncode:
            continue
        results = {}
        for v in values:
            env = {k: x for k, x in os.environ.items() if k != "MADEIRA_FEX_KEEP_DEAD_LOADS"}
            if v is not None:
                env["MADEIRA_FEX_KEEP_DEAD_LOADS"] = v
            run = subprocess.run([str(t / label)], capture_output=True, text=True, env=env)
            rows = [dict(kv.split("=") for kv in line.split()) for line in run.stdout.splitlines() if line.startswith("op=")]
            log = int(re.search(r"log=(\d+)", run.stdout).group(1))
            results[v] = (rows, log)
        # the reference: a node is removed exactly when it has no uses and no side effects
        for v, (rows, log) in results.items():
            name = "unset" if v is None else repr(v)
            on = (label == "win" and v == "1")
            bad = []
            for r in rows:
                op, uses, se = int(r["op"]), int(r["uses"]), int(r["se"])
                want = (uses == 0 and se == 0) and not (on and op in (2, 3))
                if int(r["removed"]) != (1 if want else 0) or int(r["ret"]) != int(r["removed"]):
                    bad.append(r)
            check(f"{label}, switch {name}: {len(rows)} combinations behave as the reference ({'loads kept' if on else 'loads removed like any dead node'})",
                  len(rows) == 12 and not bad, bad[:3])
            check(f"{label}, switch {name}: the log line is printed {'once' if on else 'never'}", log == (1 if on else 0), log)

print("PASS: FEX keep dead loads" if ok else "FAILED: FEX keep dead loads")
sys.exit(0 if ok else 1)

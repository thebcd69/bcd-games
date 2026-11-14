#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""FEX (ARM64EC module): no suspend check at the point a guest CALL returns to, opt-in (tools/patch-fex-ios-callret-lean.py).

Needs the FEX submodule and a host C++ compiler; no Wine, FEX build or game.
1. The overlay applies to the pin's three files as they are and after every overlay tools/build-xtajit64.sh applies before it, a
   second run changes nothing, a moved anchor and a partial overlay are refused without writing, and undoing its hunks gives the
   files back byte for byte.
2. The switch, compiled on the host from the patched text: only MADEIRA_FEX_CALLRET_LEAN = exactly "1" is on, and only in the iOS
   ARM64EC module (without its two defines the answer is a constant false).
3. The entry point code, cut from FEX's file before and after the overlay and compiled with a recording stand-in for the emitter,
   for every state a block's entry point is emitted in (a branch falling into it, the call it is the return block of just emitted,
   a call returning elsewhere, none; with and without the trap flag test; no, few and many spill slots):
   - switch off: the patched code emits exactly what FEX's own code emits, in every state;
   - switch on: the same, except that the entry point directly after its own call has no suspend check; every other state keeps it.
4. What the script's reasoning rests on is still true of the tree: a callee's entry point is emitted by the same EmitEntryPoint
   call (with its check), ExitFunctionEC reads the doorbell, an unconditional backward branch keeps its check.
5. Build wiring: applied by the ARM64EC build only, after its other overlays, a refusal is a warning, the three files are restored;
   the catalog entry (off by default); no downloaded list may set it; the session end clears it; the credit; CI runs this check.
NOT covered: the JIT's bytes on a device, and a suspend arriving while a game runs (the device A/B is the owner's).
SKIP (exit 0) without the FEX sources.
"""
from pathlib import Path
import importlib.util
import os
import re
import shutil
import subprocess
import sys
import tempfile

R = Path(__file__).resolve().parents[2]
FEX = Path(os.environ.get("FEX_SRC_DIR", R / "FEX"))
failures = []


def check(what, cond, detail=""):
    print(("PASS: " if cond else "FAIL: ") + what)
    if not cond:
        failures.append(what)
        if detail:
            print("      " + str(detail)[:1500])
    return cond


spec = importlib.util.spec_from_file_location("lean", R / "tools/patch-fex-ios-callret-lean.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
SCRIPT = R / "tools/patch-fex-ios-callret-lean.py"
if not all((FEX / f).is_file() for f in mod.FILES):
    print(f"SKIP: no FEX sources at {FEX} (check out the submodule or set FEX_SRC_DIR)")
    sys.exit(0)


def run(root):
    return subprocess.run([sys.executable, str(SCRIPT), str(root)], capture_output=True, text=True)


def copy_three(dst, src=FEX):
    for f in mod.FILES:
        (dst / f).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(src / f, dst / f)


pristine = {f: (FEX / f).read_text() for f in mod.FILES}
came_patched = mod.MARKER in pristine[mod.JIT]
if came_patched:   # a tree a build left patched (the build restores these files; a build that was cut short does not)
    for f, _, old, new in reversed(mod.HUNKS):
        pristine[f] = pristine[f].replace(new, old, 1)

# ---- 1. the overlay
with tempfile.TemporaryDirectory() as tmp:
    t = Path(tmp)
    a = t / "pin"
    for f in mod.FILES:
        (a / f).parent.mkdir(parents=True, exist_ok=True)
        (a / f).write_text(pristine[f])
    p1 = run(a)
    after = {f: (a / f).read_text() for f in mod.FILES}
    p2 = run(a)
    check("the overlay applies to the pin's files, and a second run changes nothing",
          p1.returncode == 0 and p2.returncode == 0 and "already in" in p2.stdout and after == {f: (a / f).read_text() for f in mod.FILES}
          and all(mod.MARKER in after[f] for f in mod.FILES), p1.stdout + p1.stderr)
    undone = dict(after)
    for f, _, old, new in reversed(mod.HUNKS):
        undone[f] = undone[f].replace(new, old, 1)
    check("undoing its hunks gives the three files back byte for byte (nothing else was changed)", undone == pristine)
    for f, what, old, new in mod.HUNKS[2:3] + mod.HUNKS[-1:]:
        b = t / ("moved-" + Path(f).name)
        for g in mod.FILES:
            (b / g).parent.mkdir(parents=True, exist_ok=True)
            (b / g).write_text(pristine[g].replace(old, old.replace("EmitEntryPoint", "EmitEntry_Point").replace("callret-elide-v1", "callret-elide-v2"), 1) if g == f else pristine[g])
        before = {g: (b / g).read_text() for g in mod.FILES}
        p = run(b)
        check(f"a moved anchor ({what}) is refused by name and nothing is written",
              p.returncode != 0 and "not found once" in (p.stdout + p.stderr) and before == {g: (b / g).read_text() for g in mod.FILES})
    c = t / "partial"
    for g in mod.FILES:
        (c / g).parent.mkdir(parents=True, exist_ok=True)
        (c / g).write_text(after[g] if g == mod.JIT_H else pristine[g])
    before = {g: (c / g).read_text() for g in mod.FILES}
    p = run(c)
    check("a partial overlay (one file patched, the others not) is refused and nothing is written",
          p.returncode != 0 and "partial" in (p.stdout + p.stderr) and before == {g: (c / g).read_text() for g in mod.FILES})

    # after the overlays the build applies before it
    sh = (R / "tools/build-xtajit64.sh").read_text()
    chain = t / "chain"
    for d in ("FEXCore/Source", "FEXCore/include", "Source/Windows"):
        shutil.copytree(FEX / d, chain / d)
    if came_patched:
        for f in mod.FILES:
            (chain / f).write_text(pristine[f])
    have_rpmalloc = (FEX / "External/rpmalloc/rpmalloc/rpmalloc.c").is_file()
    if have_rpmalloc:
        (chain / "External/rpmalloc/rpmalloc").mkdir(parents=True)
        shutil.copy(FEX / "External/rpmalloc/rpmalloc/rpmalloc.c", chain / "External/rpmalloc/rpmalloc/rpmalloc.c")
    carries = "ec-ret-direct-v1 madeira-bcd" in pristine[mod.MODULE]
    block = sh[sh.index("if grep -q \"ec-ret-direct-v1 madeira-bcd\""):sh.rindex("\nbuild\n")]
    first, rest = block.split("\nelse\n", 1)
    older, tail = rest.split("\nfi\n", 1)
    lines = [l for l in ((first if carries else older) + "\n" + tail).splitlines() if l.startswith("python3 ") or l.startswith("    python3 ")]
    problems, applied = [], 0
    for line in lines:
        m = re.match(r'\s*python3 "\$R/(tools/[\w.-]+\.py)" "\$R/FEX(/[^"]*)?"(.*?)(?: \|\| echo .*)?$', line)
        if not m:
            problems.append("not understood: " + line[:80])
            continue
        script, sub, extra = m.group(1), (m.group(2) or "").replace("/$CPUF", "/Source/Windows/Common/CPUFeatures.cpp"), m.group(3).split()
        if script.endswith("patch-fex-ios-callret-lean.py"):
            continue
        if "rpmalloc" in script and not have_rpmalloc:
            continue
        q = subprocess.run([sys.executable, str(R / script), str(chain) + sub] + extra, capture_output=True, text=True)
        applied += 1
        if q.returncode:
            problems.append(script + ": " + (q.stdout + q.stderr).strip()[-200:])
    if not have_rpmalloc:
        print("note: FEX/External/rpmalloc is not checked out; its overlay (another file) is left out of the chain")
    check(f"the {applied} overlays the build applies before it apply to the pin's tree", not problems and applied >= 10, problems)
    pc = run(chain)
    pc2 = run(chain)
    check("after them the overlay applies, and a second run changes nothing", pc.returncode == 0 and "already in" in pc2.stdout, pc.stdout + pc.stderr)

    # ---- 2 and 3. on the host
    cxx = os.environ.get("CXX") or shutil.which("c++") or shutil.which("clang++") or shutil.which("g++")
    if not cxx:
        print("SKIP: no C++ compiler for the behaviour parts")
    else:
        jit = after[mod.JIT]
        helper = jit[jit.index("// callret-lean-test:begin"):jit.index("// callret-lean-test:end")]
        (t / "sw.cpp").write_text("#include <cstdlib>\n#include <cstdio>\n" + helper + "\nint main() { printf(\"%d\\n\", CallRetLeanEnabled() ? 1 : 0); return 0; }\n")
        on_build = subprocess.run([cxx, "-std=c++20", "-DFEX_IOS_HOST=1", "-DARCHITECTURE_arm64ec=1", "-Wall", "-o", str(t / "sw-ec"), str(t / "sw.cpp")], capture_output=True, text=True)
        off_build = subprocess.run([cxx, "-std=c++20", "-Wall", "-o", str(t / "sw-other"), str(t / "sw.cpp")], capture_output=True, text=True)
        if check("the switch compiles on the host, with and without the iOS ARM64EC defines", on_build.returncode == 0 and off_build.returncode == 0, on_build.stderr + off_build.stderr):
            def ask(exe, value):
                env = {k: v for k, v in os.environ.items() if k != "MADEIRA_FEX_CALLRET_LEAN"}
                if value is not None:
                    env["MADEIRA_FEX_CALLRET_LEAN"] = value
                return subprocess.run([str(t / exe)], capture_output=True, text=True, env=env).stdout.strip()
            values = [None, "", "0", "1", "11", "1 ", "on", "true", "2"]
            check('only exactly "1" switches it on (unset, empty, 0, 11, "1 ", on, true, 2 are off)',
                  [ask("sw-ec", v) for v in values] == ["0", "0", "0", "1", "0", "0", "0", "0", "0"])
            check("outside the iOS ARM64EC module the answer is false whatever the variable says", [ask("sw-other", v) for v in values] == ["0"] * len(values))

        def entry_code(text):
            fn = text[text.index("void Arm64JITCore::EmitEntryPoint("):]
            fn = fn[:fn.index("\n}\n") + 3]
            blk = text[text.index("      if (BlockIROp->EntryPoint) {"):text.index("      if (PendingCallReturnTargetLabel) {\n        // If there is still a pending call return target")]
            return fn, blk
        harness = r'''
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstddef>
#include <map>
#include <string>
#include <vector>
namespace ARMEmitter {
enum class Size { i64Bit };
enum class ExtendedType { LSL_64 };
struct Reg { static constexpr int rsp = 31; };
struct XReg { static constexpr int rsp = 31; };
struct BackwardLabel {};
static bool IsImmAddSub(uint64_t v) { return v < 4096; }
}
namespace FEXCore::Core { struct CPUState { uint64_t InlineJITBlockHeader; }; }
struct Lbl { int id; };
static const int TMP1 = 10, STATE = 28;
static const uint64_t MaxSpillSlotSize = 32;
static bool g_lean;
@HELPER@
struct Arm64JITCore {
  std::vector<std::string> ops;
  Lbl* PendingTargetLabel = nullptr;
  Lbl* PendingCallReturnTargetLabel = nullptr;
  std::map<int, Lbl> CallReturnTargets;
  struct { std::map<uint64_t, uint8_t*> EntryPoints; uint8_t* BlockBegin; } CodeData;
  struct DD { std::vector<std::pair<uint64_t, long>> GuestOpcodes; } dd, *DebugData = &dd;
  uint8_t buf[16];
  uint64_t SpillSlots = 0;
  template <class T> T GetCursorAddress() { return (T)(buf + ops.size()); }
  void b_OrRestart(Lbl* l) { ops.push_back("b L" + std::to_string(l->id)); }
  void BindOrRestart(Lbl* l) { ops.push_back("bind L" + std::to_string(l->id)); }
  template <class... A> void adr_OrRestart(A&&...) { ops.push_back("adr header"); }
  template <class... A> void str(A&&...) { ops.push_back("str header"); }
  template <class... A> void sub(A&&... a) { ops.push_back(sizeof...(a) == 4 ? "sub sp, imm" : "sub sp, reg"); }
  template <class... A> void LoadConstant(A&&...) { ops.push_back("mov const"); }
  void EmitTFCheck() { ops.push_back("tf-check"); }
  void EmitSuspendInterruptCheck() { ops.push_back("suspend-check"); }
#if PATCHED
  void EmitEntryPoint(ARMEmitter::BackwardLabel& HeaderLabel, bool CheckTF, bool SkipSuspendCheck = false);
#else
  void EmitEntryPoint(ARMEmitter::BackwardLabel& HeaderLabel, bool CheckTF);
#endif
  void Block(int Node, Lbl* Target, uint64_t Entry, bool CheckTF);
};
@FN@
void Arm64JITCore::Block(int Node, Lbl* Target, uint64_t Entry, bool CheckTF) {
  struct { bool EntryPoint; uint64_t GuestEntryOffset; } blk = { true, 0x40 }, *BlockIROp = &blk;
  ARMEmitter::BackwardLabel JITCodeHeaderLabel;
  CodeData.BlockBegin = buf;
@BLOCK@
}
int main() {
  // every state: who is pending x trap flag test x spill slots
  for (int pending = 0; pending < 4; pending++)
    for (int tf = 0; tf < 2; tf++)
      for (uint64_t slots : {0ull, 2ull, 4096ull}) {
        Arm64JITCore j;
        Lbl target { 1 }, other { 9 };
        j.SpillSlots = slots;
        int node = 7;
        j.CallReturnTargets.try_emplace(8, Lbl { 8 });
        if (pending == 1) j.PendingTargetLabel = &target;                                       // a branch falls into this block
        if (pending == 2) j.PendingCallReturnTargetLabel = &j.CallReturnTargets.try_emplace(node, Lbl { 7 }).first->second;   // the call it is the return block of
        if (pending == 3) j.PendingCallReturnTargetLabel = &j.CallReturnTargets.find(8)->second;   // a call that returns elsewhere
        if (pending != 2) j.CallReturnTargets.try_emplace(node, Lbl { 7 });
        j.Block(node, &target, 0x140000000ull, tf != 0);
        printf("pending=%d tf=%d slots=%llu:", pending, tf, (unsigned long long)slots);
        for (auto& o : j.ops) printf(" %s;", o.c_str());
        printf(" | entry=%d pcr=%s\n", (int)j.CodeData.EntryPoints.size(), j.PendingCallReturnTargetLabel ? "set" : "null");
      }
  return 0;
}
'''
        outs = {}
        ok_build = True
        for name, text, patched in (("fex", pristine[mod.JIT], 0), ("lean", after[mod.JIT], 1)):
            fn, blk = entry_code(text)
            src = harness.replace("@FN@", fn).replace("@BLOCK@", blk)
            # the harness's switch: the patched text's own function is replaced by one the test sets (its parsing is checked above)
            src = src.replace("@HELPER@", "static bool CallRetLeanEnabled() { return g_lean; }" if patched else "")
            src = src.replace("int main() {", "int main(int argc, char** argv) {\n  g_lean = argc > 1 && argv[1][0] == '1';", 1)
            (t / f"e-{name}.cpp").write_text(src)
            b = subprocess.run([cxx, "-std=c++20", f"-DPATCHED={patched}", "-Wall", "-Wno-unused-variable", "-Wno-unused-parameter", "-o", str(t / f"e-{name}"), str(t / f"e-{name}.cpp")],
                               capture_output=True, text=True)
            if b.returncode:
                ok_build = False
                print("\n".join(l for l in b.stderr.splitlines() if "error" in l)[:2500])
        if check("FEX's entry point code, before and after the overlay, compiles on the host with a recording emitter", ok_build):
            base = subprocess.run([str(t / "e-fex")], capture_output=True, text=True).stdout.splitlines()
            off = subprocess.run([str(t / "e-lean"), "0"], capture_output=True, text=True).stdout.splitlines()
            on = subprocess.run([str(t / "e-lean"), "1"], capture_output=True, text=True).stdout.splitlines()
            check("24 states are covered, and FEX's own code tests the doorbell in every one of them",
                  len(base) == 24 and all(" suspend-check;" in l for l in base) and len(set(base)) >= 12)
            check("switch off: the patched code emits exactly what FEX's own code emits, in all 24 states", off == base,
                  [x for x in zip(base, off) if x[0] != x[1]][:2])
            lean_lines = [i for i, l in enumerate(base) if l.startswith("pending=2 ")]
            expected = [l.replace(" suspend-check;", "") if i in lean_lines else l for i, l in enumerate(base)]
            check("switch on: only the entry point directly after its own call loses the test (6 states); a branch falling in, a call "
                  "returning elsewhere and a plain entry point keep it, and everything else in the header stays",
                  on == expected and len(lean_lines) == 6 and all(" suspend-check;" in on[i] for i in range(24) if i not in lean_lines),
                  [x for x in zip(expected, on) if x[0] != x[1]][:2])
            check("the header's other parts are still emitted there (the block header's address, the trap flag test, the spill slots)",
                  all("adr header; str header;" in on[i] for i in lean_lines) and any("tf-check;" in on[i] for i in lean_lines)
                  and any("sub sp, imm;" in on[i] for i in lean_lines) and any("mov const; sub sp, reg;" in on[i] for i in lean_lines))

# ---- 4. what the reasoning rests on
jit = pristine[mod.JIT]
branch = (FEX / "FEXCore/Source/Interface/Core/JIT/BranchOps.cpp").read_text()
asm = (FEX / "Source/Windows/ARM64EC/Module.S").read_text()
check("there is one EmitEntryPoint call: a callee's entry point is emitted by the same line, where the new argument is false unless a call falls into it",
      jit.count("EmitEntryPoint(JITCodeHeaderLabel, CheckTF);") == 1 and jit.count("EmitEntryPoint(") == 2)
check("an unconditional backward branch keeps its own doorbell test (two places), which the overlay does not touch",
      jit.count("if (PendingTargetLabel->Backward.Location) {\n          EmitSuspendInterruptCheck();") == 1
      and jit.count("if (PendingTargetLabel->Backward.Location) {\n      EmitSuspendInterruptCheck();") == 1
      and not any("PendingTargetLabel->Backward" in new for _, _, _, new in mod.HUNKS))
check("a call into ARM64EC code leaves through ExitFunctionEC, which reads the doorbell after it has left the simulation",
      re.search(r"strb wzr, \[x17, #0x0\] // ChpeV2CpuAreaInfo->InSimulation\n\s*ldr w17, \[x28, #IOS_FRAME_SUSPEND_DOORBELL\]", asm) is not None
      and "brk #0xCAFE" in asm)
disp = (FEX / "FEXCore/Source/Interface/Core/OpcodeDispatcher.cpp").read_text()
call_op = disp[disp.index("void OpDispatchBuilder::CALLOp(OpcodeArgs) {"):]
call_op = call_op[:call_op.index("\n}\n")]
check("a call to the next instruction is no call for the JIT (a push and the end of the block): the point after it is an ordinary entry point with its test",
      "if (TargetOffset != 0) {" in call_op and call_op.index("ExitRelocatedPC(Op, TargetOffset, BranchHint::Call") < call_op.index("} else {\n    NeedsBlockEnd = true;"))
check("the pending call return is set only by a guest CALL (BranchOps.cpp), to the call's own return block",
      branch.count("PendingCallReturnTargetLabel = &CallReturnTargets.try_emplace(Op->CallReturnBlock.ID()).first->second;") == 4
      and "PendingCallReturnTargetLabel = " not in jit.replace("PendingCallReturnTargetLabel = nullptr;", ""))

# ---- 5. the wiring
sh = (R / "tools/build-xtajit64.sh").read_text()
wow = (R / "tools/build-xtajit-wow64.sh").read_text()
line = 'python3 "$R/tools/patch-fex-ios-callret-lean.py" "$R/FEX" || echo "::warning::the lean call return was not applied'
restore = sh[sh.rindex("\nbuild\n"):]
check("the ARM64EC build applies it last of its overlays (a refusal is a warning) and restores the three files; the WOW64 build does not apply it",
      line in sh and sh.index('patch-fex-ios-repne-bit-ops.py" "$R/FEX"') < sh.index(line) < sh.rindex("\nbuild\n")
      and all(f in restore for f in mod.FILES) and "callret-lean" not in wow)
gen = (R / "build/tools/gen-config-catalog.py").read_text()
cat = (R / "app/Madeira/ConfigCatalog.generated.swift").read_text()
check("the catalog documents the switch, off by default",
      re.search(r'"env\.MADEIRA_FEX_CALLRET_LEAN": \{[^}]*"default": "0"', gen, re.S) is not None
      and re.search(r'key: "env\.MADEIRA_FEX_CALLRET_LEAN", title: "[^"]*", kind: \.bool, defaultValue: "0"', cat) is not None)
policy = (R / "app/Madeira/GameRecommendationsList.swift").read_text()
check("no downloaded list may set it (an A/B switch for a person at the device), and the session end clears it",
      "MADEIRA_FEX_CALLRET_LEAN" not in policy and 'unsetenv("MADEIRA_FEX_CALLRET_LEAN");' in (R / "app/Madeira/WineProcessBridge.m").read_text())
notices = (R / "THIRD-PARTY-NOTICES.md").read_text()
head = SCRIPT.read_text()[:700]
check("the credit: Porthole's patch 0143 in the script's header and in the notices",
      "patches/0143-fex-lean-callret.patch" in head and "Portions copyright 2026 The Porthole authors" in head and "BCD Material" not in head
      and "| `tools/patch-fex-ios-callret-lean.py` |" in notices)
check("CI runs this check", "run: python3 tests/host/check-fex-callret-lean.py" in (R / ".github/workflows/build-ipa.yml").read_text())

print("FAILED: fex callret lean" if failures else "PASS: fex callret lean")
sys.exit(1 if failures else 0)

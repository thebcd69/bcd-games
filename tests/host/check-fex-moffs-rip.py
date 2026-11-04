#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""tools/patch-fex-ios-moffs-rip.py: MOV moffs records its guest RIP with MADEIRA_FEX_MOFFS_RIP=1 (round 630, agent O3 S9).

Unreal Gold (Y1G0A7, D1D3P2, 43QKTT, EQ6DMS, X3HYP5): FEX's inline self-modifying-code handler resumed in front of each MOV [moffs],EAX store
of the fire texture code and ran the register-only instructions before it a second time.

Checks:
  - the WOW64 build applies it without failing the build, after the invalidation-walk guard (both edit Core.cpp) and restores Core.cpp;
    the ARM64EC build does not apply it; CI runs this check;
  - FEX tree (FEX_SRC, default ~/madeira-bcd/FEX; skipped without it): on a copy of the pinned Core.cpp, after the build's earlier Core.cpp
    patch, it adds the helper once right before GenerateIR and widens the one guest-opcode condition, nothing else; a second run changes
    nothing; a moved anchor refuses it and leaves the file alone; the premise still holds: FLAGS_MEM_OFFSET is on exactly the eight A0-A3
    rows, none of them has FLAGS_DEBUG_MEM_ACCESS, and CanHaveSideEffects still counts only GPR-direct/indirect, RIP-relative and SIB
    operands (a literal address is not among them);
  - the helper, cut from the patched file and built on the host with stand-ins: unset, "0", "10" or "" mark nothing; "1" marks the MOV
    moffs rows only (not another row, not a missing one) and says so once;
  - a model of the handler (a block entry and every marked instruction have a RIP entry; a store into the running page resumes at the last
    entry and steps one instruction; the next block starts after it) run on the routine of Fire.dll+0x15DCE as the log shows it (offsets
    and kinds only, no code bytes) gives exactly the sixteen events of Y1G0A7 without the patch, and with it one event per store at the
    store itself and the values a single pass gives;
  - the key: allowed in lists (0 or 1), in the settings catalog off by default, carried by the unreal-gold row only (from build 630); the
    auto-tune rule proposes it and stops once "[fex-moffs]" is in the log.
"""
from pathlib import Path
import json, os, re, shutil, subprocess, sys, tempfile

root = Path(__file__).resolve().parents[2]
patch = root / "tools/patch-fex-ios-moffs-rip.py"
inval = root / "tools/patch-fex-ios-inval-null-thread.py"
ok = True


def check(what, cond, detail=""):
    global ok
    print(("ok   " if cond else "FAIL ") + what + (("\n     " + str(detail)[:1500]) if (detail and not cond) else ""))
    ok = ok and bool(cond)


w64 = (root / "tools/build-xtajit-wow64.sh").read_text()
ec = (root / "tools/build-xtajit64.sh").read_text()
line = 'python3 "$R/tools/patch-fex-ios-moffs-rip.py" "$R/FEX/FEXCore/Source/Interface/Core/Core.cpp" || echo "::warning::'
check("the WOW64 build applies it without failing the build, after the invalidation-walk guard, and restores Core.cpp",
      line in w64 and w64.index("patch-fex-ios-inval-null-thread.py") < w64.index(line) < w64.index("restore() {")
      and "FEXCore/Source/Interface/Core/Core.cpp" in w64[w64.index("restore() {"):w64.index("trap restore EXIT")])
check("the ARM64EC build does not apply it", "patch-fex-ios-moffs-rip" not in ec)
wf = (root / ".github/workflows/build-ipa.yml").read_text()
check("CI runs this check", "python3 tests/host/check-fex-moffs-rip.py" in wf)

fex = Path(os.environ.get("FEX_SRC", Path.home() / "madeira-bcd/FEX"))
core = fex / "FEXCore/Source/Interface/Core/Core.cpp"
helper = None
if not core.is_file():
    print("skip: no FEX tree here (FEX_SRC or ~/madeira-bcd/FEX); the CI and the Mac run the source part")
else:
    with tempfile.TemporaryDirectory(prefix="madeira-fex-moffs-") as tmp:
        tree = Path(tmp)
        (tree / "FEXCore/Source/Interface/Core").mkdir(parents=True)
        t = tree / "FEXCore/Source/Interface/Core/Core.cpp"
        shutil.copy(core, t)
        ri = subprocess.run([sys.executable, str(inval), str(tree)], capture_output=True, text=True)
        check("the invalidation-walk guard applies first, as in the build", ri.returncode == 0, ri.stdout + ri.stderr)
        before = t.read_text()
        r1 = subprocess.run([sys.executable, str(patch), str(t)], capture_output=True, text=True)
        once = t.read_text()
        r2 = subprocess.run([sys.executable, str(patch), str(t)], capture_output=True, text=True)
        check("it applies once; a second run says so and changes nothing",
              r1.returncode == 0 and r2.returncode == 0 and "already in" in r2.stdout and t.read_text() == once and once != before,
              r1.stdout + r1.stderr + r2.stdout + r2.stderr)
        head = "ContextImpl::GenerateIRResult\nContextImpl::GenerateIR(FEXCore::Core::InternalThreadState* Thread, uint64_t GuestRIP"
        hs = once.index("// moffs-rip-v1 madeira-bcd")
        check("the helper is at file scope right before GenerateIR, once",
              once.count("static bool MadeiraMoffsRip(") == 1 and once.index(head) > hs
              and once[hs:once.index(head)].rstrip().endswith("}"))
        old = "        if (ExtendedDebugInfo || Thread->OpDispatcher->CanHaveSideEffects(TableInfo, DecodedInfo)) {\n"
        new = ("        if (ExtendedDebugInfo || Thread->OpDispatcher->CanHaveSideEffects(TableInfo, DecodedInfo) ||\n"
               "            MadeiraMoffsRip(TableInfo)) { // madeira-bcd: MADEIRA_FEX_MOFFS_RIP=1 (tools/patch-fex-ios-moffs-rip.py)\n")
        rest = once[:hs] + once[once.index(head):]
        check("nothing else changes: the condition is widened by the helper's call only",
              old not in once and once.count(new) == 1 and rest.replace(new, old, 1) == before)
        t.write_text(before.replace(old, old.replace("(TableInfo, DecodedInfo)", "(TableInfo, DecodedInfo, 0)"), 1))
        r3 = subprocess.run([sys.executable, str(patch), str(t)], capture_output=True, text=True)
        check("a moved anchor refuses the patch and leaves the file alone",
              r3.returncode != 0 and "condition" in r3.stderr and "moffs-rip" not in t.read_text(), r3.stderr)
        helper = once[once.index("static bool MadeiraMoffsRip("):once.index(head)]

    tables = (fex / "FEXCore/Source/Interface/Core/X86Tables/BaseTables.cpp").read_text()
    rows = [l for l in tables.splitlines() if "FLAGS_MEM_OFFSET" in l]
    others = []
    for f in (fex / "FEXCore/Source/Interface/Core/X86Tables").glob("*.cpp"):
        if f.name != "BaseTables.cpp" and "FLAGS_MEM_OFFSET" in f.read_text():
            others.append(f.name)
    check("FLAGS_MEM_OFFSET is on exactly the eight MOV moffs rows (A0-A3, 32- and 64-bit slots), in no other table",
          len(rows) == 8 and all('"MOV"' in l and "MOVOffsetOp" in l for l in rows) and not others, rows + others)
    check("none of those rows has FLAGS_DEBUG_MEM_ACCESS (the gap)", not any("FLAGS_DEBUG_MEM_ACCESS" in l for l in rows))
    disp = (fex / "FEXCore/Source/Interface/Core/OpcodeDispatcher.h").read_text()
    fn = disp[disp.index("static bool CanHaveSideEffects("):]
    fn = fn[:fn.index("\n  }\n")]
    check("CanHaveSideEffects still counts only GPR-direct/indirect, RIP-relative and SIB operands (a literal address is not one)",
          "return Operand.IsGPRDirect() || Operand.IsGPRIndirect() || Operand.IsRIPRelative() || Operand.IsSIB();" in fn
          and "Literal" not in fn and "FLAGS_MEM_OFFSET" not in fn, fn[-600:])
    front = (fex / "FEXCore/Source/Interface/Core/Frontend.cpp").read_text()
    check("the moffs address is decoded as a literal operand",
          "DecodeInst->Src[CurrentSrc].Data.Literal.Size = Bytes;" in front
          and "Thread->OpDispatcher->_GuestOpcode(InstAddress - GuestRIP);" in core.read_text())

if helper:
    harness = r'''
#include <cstdint>
#include <cstdio>
#include <cstdlib>
namespace FEXCore { namespace X86Tables {
namespace InstFlags { constexpr uint64_t FLAGS_MEM_OFFSET = (1ULL << 9); constexpr uint64_t FLAGS_DEBUG_MEM_ACCESS = (1ULL << 2); }
struct X86InstInfo { const char* Name; uint64_t Flags; };
} }
namespace LogMan { namespace Msg { template <typename... A> void IFmt(const char* f, A...) { std::printf("LOG %s\n", f); } } }
''' + helper + r'''
int main() {
  using namespace FEXCore::X86Tables;
  X86InstInfo moffs {"MOV", InstFlags::FLAGS_MEM_OFFSET | (1ULL << 11)};
  X86InstInfo other {"ADD", InstFlags::FLAGS_DEBUG_MEM_ACCESS};
  int a = MadeiraMoffsRip(&moffs), b = MadeiraMoffsRip(&other), c = MadeiraMoffsRip(nullptr), d = MadeiraMoffsRip(&moffs);
  std::printf("moffs=%d other=%d null=%d again=%d\n", a, b, c, d);
  return 0;
}
'''
    cxx = shutil.which("c++") or shutil.which("clang++")
    if not cxx:
        print("skip: no C++ compiler for the helper part")
    else:
        with tempfile.TemporaryDirectory(prefix="madeira-fex-moffs-h-") as tmp:
            src, exe = Path(tmp) / "t.cpp", Path(tmp) / "t"
            src.write_text(harness)
            b = subprocess.run([cxx, "-std=c++20", "-Wall", "-Werror", str(src), "-o", str(exe)], capture_output=True, text=True)
            check("the helper builds on the host: " + b.stderr[-600:], b.returncode == 0)
            if b.returncode == 0:
                outs = {}
                for value in (None, "0", "10", "", "1"):
                    env = {k: v for k, v in os.environ.items() if k != "MADEIRA_FEX_MOFFS_RIP"}
                    if value is not None:
                        env["MADEIRA_FEX_MOFFS_RIP"] = value
                    outs[value] = subprocess.run([str(exe)], capture_output=True, text=True, env=env).stdout
                check("unset, 0, 10 or empty: nothing is marked and nothing is said",
                      all(outs[v] == "moffs=0 other=0 null=0 again=0\n" for v in (None, "0", "10", "")), outs)
                check('"1": the MOV moffs rows only, and one "[fex-moffs]" line',
                      outs["1"] == "LOG [fex-moffs] MADEIRA_FEX_MOFFS_RIP=1: MOV moffs instructions record their guest RIP\n"
                                   "moffs=1 other=0 null=0 again=1\n", outs["1"])

# ---- the handler model on the routine as Y1G0A7 shows it -------------------------------------------------------
# (offset from Fire.dll+0x15DCE, kind, operation); kinds: "reg" no RIP entry, "mem" a marked memory instruction, "moffs" MOV [address],EAX
# (marked only with the patch), "ret" ends the routine. Operations: ("mov", dst, src), ("add", reg, n), ("addk", reg, sign: K = [0x5CA8]),
# ("st", target, reg). Offsets and kinds come from the log's events and the instruction lengths; no code bytes of the game are here.
ROUTINE = [
    (0x00, "reg", ("mov", "ecx", "eax")), (0x02, "moffs", ("st", "E6A", "eax")), (0x07, "reg", ("add", "eax", 1)),
    (0x08, "moffs", ("st", "E72", "eax")), (0x0D, "moffs", ("st", "EA5", "eax")), (0x12, "reg", ("mov", "ebx", "eax")),
    (0x14, "reg", ("add", "ebx", 2)), (0x17, "mem", ("st", "EDB", "ebx")), (0x1D, "reg", ("add", "eax", 1)),
    (0x1E, "moffs", ("st", "ECB", "eax")), (0x23, "reg", ("add", "eax", -2)), (0x26, "moffs", ("st", "E92", "eax")),
    (0x2B, "reg", ("mov", "eax", "ecx")), (0x2D, "mem", ("addk", "eax", 1)), (0x33, "reg", ("add", "eax", -1)),
    (0x34, "moffs", ("st", "E64", "eax")), (0x39, "reg", ("mov", "eax", "ecx")), (0x3B, "mem", ("addk", "eax", -1)),
    (0x41, "reg", ("add", "eax", 0)), (0x44, "moffs", ("st", "F0B", "eax")), (0x49, "ret", ("ret",)),
]
Y1G0A7_EVENTS = [0x5DCE, 0x5DD0, 0x5DD5, 0x5DD6, 0x5DDB, 0x5DE5, 0x5DEB, 0x5DEC, 0x5DF1, 0x5DF4, 0x5DFB, 0x5E01, 0x5E02, 0x5E09, 0x5E0F,
                 0x5E12]   # "Handled inline self-modifying code ... rip: 78D5xxx", in log order, for this routine's stores


def run_model(marked_moffs, eax, k):
    regs, mem, events = {"eax": eax, "ebx": 0, "ecx": 0}, {}, []

    def step(i):
        op = ROUTINE[i][2]
        if op[0] == "mov":
            regs[op[1]] = regs[op[2]]
        elif op[0] == "add":
            regs[op[1]] = (regs[op[1]] + op[2]) & 0xFFFFFFFF
        elif op[0] == "addk":
            regs[op[1]] = (regs[op[1]] + op[2] * k) & 0xFFFFFFFF
        elif op[0] == "st":
            mem[op[1]] = regs[op[2]]

    def has_entry(j, block):
        return j == block or ROUTINE[j][1] == "mem" or (marked_moffs and ROUTINE[j][1] == "moffs")

    i = block = 0
    while ROUTINE[i][1] != "ret":
        if ROUTINE[i][2][0] == "st":
            # the store hits the running page: the state is rebuilt at the last RIP entry of this block, one instruction is
            # stepped from there, and the next block starts after it
            r = max(j for j in range(block, i + 1) if has_entry(j, block))
            events.append(0x5DCE + ROUTINE[r][0])
            step(r)
            i = block = r + 1
            continue
        step(i)
        i += 1
    return events, mem


ev_old, mem_old = run_model(False, 0x100, 0x100)
ev_new, mem_new = run_model(True, 0x100, 0x100)
check("the model without the patch gives exactly the sixteen events of Y1G0A7", ev_old == Y1G0A7_EVENTS, [hex(e) for e in ev_old])
stores = [0x5DCE + off for off, kind, op in ROUTINE if op[0] == "st"]
check("with the patch every store is one event at the store itself", ev_new == stores, [hex(e) for e in ev_new])
single = {"E6A": 0x100, "E72": 0x101, "EA5": 0x101, "EDB": 0x103, "ECB": 0x102, "E92": 0x100, "E64": 0x1FF, "F0B": 0x0}
check("with the patch the stored values are those of a single pass (eax = 0x100, [0x5CA8] = 0x100)", mem_new == single,
      {k: hex(v) for k, v in mem_new.items()})
check("without it the INC/SUB/ADD in front of a store ran twice", mem_old["E72"] == 0x102 and mem_old["E64"] == 0x2FD and mem_old != single,
      {k: hex(v) for k, v in mem_old.items()})

# ---- the key in lists, the catalog and auto-tune ---------------------------------------------------------------
pol = (root / "app/Madeira/GameRecommendationsList.swift").read_text()
check("a list may set the key, 0 or 1", '"MADEIRA_FEX_MOFFS_RIP",' in pol
      and re.search(r'case "MADEIRA_FEX_MOFFS_RIP":\n[^\n]*\n\s*guard value == "0" \|\| value == "1"', pol) is not None)
cat_py = (root / "build/tools/gen-config-catalog.py").read_text()
check("the settings catalog knows it, off by default, from the patch",
      re.search(r'"env\.MADEIRA_FEX_MOFFS_RIP": \{[^}]*"kind": "bool", "default": "0"[^}]*"sources": \["tools/patch-fex-ios-moffs-rip\.py"\]',
                cat_py, re.S) is not None)
cat = root / "app/Madeira/ConfigCatalog.generated.swift"
if cat.is_file() and 'key: "env.MADEIRA_FEX_MOFFS_RIP"' in cat.read_text():
    check("the generated catalog has it off by default",
          re.search(r'key: "env\.MADEIRA_FEX_MOFFS_RIP"[^\n]*kind: \.bool, defaultValue: "0"', cat.read_text()) is not None)
else:
    print("note: app/Madeira/ConfigCatalog.generated.swift is regenerated in the full tree (build/tools/gen-config-catalog.py)")
recs = json.loads((root / "recommended/recommendations.json").read_text())
carriers = sorted(r["id"] for r in recs["rules"]
                  if any("MADEIRA_FEX_MOFFS_RIP" in (c["line"] if isinstance(c, dict) else c) for c in r["config"]))
ug = next((r for r in recs["rules"] if r["id"] == "unreal-gold"), None)
check("only the unreal-gold row carries it, from build 630",
      carriers == ["unreal-gold"] and {"line": "env.MADEIRA_FEX_MOFFS_RIP = 1", "minBuild": 630} in ug["config"], carriers)
rules = (root / "build/tools/autotune_rules.py").read_text()
rb = rules[rules.index('"id": "unreal-fire-smc-moffs"'):]
rb = rb[:rb.index("\n    },\n")]
check("the auto-tune rule proposes the line and stops once [fex-moffs] is in the log",
      '"lines": ["env.MADEIRA_FEX_MOFFS_RIP = 1"]' in rb and 's("[fex-moffs] MADEIRA_FEX_MOFFS_RIP=1")' in rb
      and (root / "tests/fixtures/autotune/unreal-gold-fire-smc-moffs-2026-11-01.expect").is_file())

print("PASS: MOV moffs records its guest RIP with MADEIRA_FEX_MOFFS_RIP=1" if ok else "FAILED: FEX moffs RIP")
sys.exit(0 if ok else 1)

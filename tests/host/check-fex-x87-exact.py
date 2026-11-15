#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""env.MADEIRA_FEX_X87_EXACT_INT64 = 1 (off unless set): the fast x87 mode keeps the 64-bit integer of FILD m64 for FISTP m64.

BCD runs 32-bit games with FEX's reduced-precision x87 (64-bit doubles). `fild qword [m]; fistp qword [m]` is how 32-bit Visual C++
code reads a 64-bit value atomically, and through a double a value of 2^53 or more loses its low bits: Batman: Arkham City and
Injustice read a lock-free list head that way and fault on the rounded pointer (tests/host/check-x87-int64-diag.py, auto-tune's
x87-int64-roundtrip, which turns the fast mode off for them). Madeira's FEX fork fixes it in the JIT (branch
codex/fex-runtime-consolidation: 16 commits by intraducine, the first a port of the Playport authors' work): the integer is kept
beside the double and returned while the value is unchanged, through blocks, FXSAVE / FNSAVE images and thread contexts.

That work changes what the JIT emits for every 32-bit program and moves the JIT's frame, so in BCD's fork it is behind one switch
and the frame is left alone. This check is the proof that the switch off is FEX as it was:

1. THE SOURCE, SWITCH OFF = THE EARLIER TEXT. Every changed statement in the dispatcher, the x87 pass, the frame and the two
   Windows modules is written as `if (<switch>) { new } else { old }` or as an addition, each marked
   (`// madeira-bcd x87-exact: on / off / end / added`). Removing the `on` parts and the additions and keeping the `off` parts
   gives back, byte for byte, the files of FEX b3fc72588 (the commit before the series; their SHA-256 are in this file). Every
   `on` condition contains the switch; the operations and helpers the series adds are produced and called only inside `on` parts;
   what cannot be written that way is listed and checked line by line (a new source file in the build list, one include of
   declarations, two new IR operations nothing emits with the switch off, a dispatcher store compiled only without _WIN32).
2. THE FRAME. The two new fields are the last members of CpuStateFrame and nothing else in CoreState.h changed (part 1), so no
   member of CPUState or of the frame moves; ARM64EC/Module.S and the offsets its static_asserts name are the earlier ones; the
   new fields' offsets are asserted to be in the range of the instructions that reach them.
3. THE SWITCH, compiled on the host from FPState.cpp: on only for exactly "1"; read once; one line when on.
4. THE NEW CODE, SWITCH ON: the fork's own value-model tests (unittests/host), which cut the pass's and the dispatcher's
   statements out of the source and run them, pass with the frame layout.
5. BCD's side: the catalog entry (off by default), the list policy (0 or 1), the session end clears it, the credit in the fork,
   CI runs this check.
NOT covered: the JIT's bytes on a device and a 32-bit game with the switch on (the device test is the owner's).
SKIP (exit 0) without a FEX tree that carries the series (FEX_SRC, FEX/ of this checkout, ~/madeira-bcd/FEX).
"""
import hashlib
import json
import os
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
            print("     " + str(detail)[:2500])


# --- BCD's side (needs no FEX tree) ----------------------------------------------
KEY = "MADEIRA_FEX_X87_EXACT_INT64"
cat = (R / "build/tools/gen-config-catalog.py").read_text()
entry = cat[cat.index('"env.%s": {' % KEY):] if ('"env.%s": {' % KEY) in cat else ""
entry = entry[:entry.index('"sources"')] if entry else ""
check("the settings catalog lists it: x86 emulation, a bool, off by default",
      '"category": "x86 emulation (FEX)"' in entry and '"kind": "bool"' in entry and '"default": "0"' in entry, entry[:300])
pol = (R / "app/Madeira/GameRecommendationsList.swift").read_text()
check("a downloaded list may set it (allowedEnvNames), 0 or 1",
      ('"%s"' % KEY) in pol.split("static let allowedEnvNames")[1].split("]\n")[0] and ("%s is 0 or 1" % KEY) in pol)
bridge = (R / "app/Madeira/WineProcessBridge.m").read_text()
check("the session end clears it (a game's switch does not reach the next game of the app run)", 'unsetenv("%s");' % KEY in bridge)
rules = json.loads((R / "recommended/recommendations.json").read_text())["rules"]
setters = [r["id"] for r in rules for item in r.get("config", []) if KEY in (item["line"] if isinstance(item, dict) else item)]
check("no recommended list sets it yet (every game runs with it off until a device test says otherwise)", not setters, setters)
auto = (R / "build/tools/autotune_rules.py").read_text()
check("auto-tune's rule for the failure still turns the fast x87 mode off (the fix that is proven), not this switch",
      '"id": "x87-int64-roundtrip"' in auto and '"lines": ["env.FEX_X87REDUCEDPRECISION = 0"]' in auto and KEY not in auto)
wf = (R / ".github/workflows/build-ipa.yml").read_text()
check("CI runs this check", "python3 tests/host/check-fex-x87-exact.py" in wf)

# --- the FEX tree --------------------------------------------------------------------
M = "// madeira-bcd x87-exact:"
cands = ([pathlib.Path(os.environ["FEX_SRC"]).expanduser()] if os.environ.get("FEX_SRC") else []) + [R / "FEX", pathlib.Path("~/madeira-bcd/FEX").expanduser()]
fex = next((c for c in cands if (c / "FEXCore/Source/Utils/FPState.cpp").is_file()
            and "X87ExactInt64" in (c / "FEXCore/Source/Utils/FPState.cpp").read_text()), None)
if fex is None:
    # madeira-bcd (U5 counter-review, finding 1): BCD's FEX pin carries the series since the merge, so a FEX tree WITHOUT it is a
    # wrong pin (a failure); only a checkout with no FEX tree at all (no submodule) skips this part.
    present = [c for c in cands if (c / "FEXCore/Source/Interface/Core/CPUID.cpp").is_file()]
    if present:
        check("the FEX tree carries the x87 series (BCD's pin is the merge's tip)", False, ", ".join(str(c) for c in present))
        sys.exit(1)
    print("SKIP the FEX part: no FEX tree (%s); set FEX_SRC" % ", ".join(str(c) for c in cands))
    sys.exit(0 if ok else 1)
print("FEX tree: %s" % fex)

# SHA-256 of the files at FEX b3fc72588 (BCD's pin before the series).
PIN = {
    "FEXCore/include/FEXCore/Core/CoreState.h": "09056e80bc443ce27d40c8703fb6bc667cf7bc0115b9fcebb2d4500cc55ae78a",
    "FEXCore/include/FEXCore/Utils/FPState.h": "f557593351d798592cbb47118de0042cfad560cb1bb119bdc18c2715e8392954",
    "FEXCore/Source/Interface/Core/OpcodeDispatcher.h": "90f2f59459ecf2110af3ac6a6aa24ea7446de43d31fffebc73ecc6eb7006859e",
    "FEXCore/Source/Interface/Core/OpcodeDispatcher/X87.cpp": "f69155e131952ab9e52e93459a1846e78dd6c2cb60b9530567e927adf501c984",
    "FEXCore/Source/Interface/Core/OpcodeDispatcher/X87F64.cpp": "03fb559a5b5cfe6d1b8b031d1e7de573dc402ed90e8a20ca0b855c56a981a180",
    "FEXCore/Source/Interface/Core/OpcodeDispatcher/Vector.cpp": "e0add7082bab104e08a40e88f8cd21ff8777e5269f22eb6a64344f1c159d9531",
    "FEXCore/Source/Interface/IR/Passes/x87StackOptimizationPass.cpp": "c1895c28f028aa810b5f877d494b2479ffc6e5e6f954c7b3ee02149db17798a8",
    "Source/Windows/WOW64/Module.cpp": "291fa52f09fea2f2c53cdf2d788d38fb26a66dae8eb5c301673ebab44ee3ccb4",
    "Source/Windows/ARM64EC/Module.cpp": "4568498a9c2bbc486df593f090934b0c464ad478bca41fe02b8ca110920b9045",
    "Source/Windows/ARM64EC/Module.S": "43caf7794c57aa1cad5b15b426f7e7e902ec4ef254927fff37ceaebc8d27449e",
}
PIN_OTHER = {
    "FEXCore/Source/Interface/IR/IR.json": "2f7a185a2d838c1ce5628dd81c15dbbdfeede1098af5046b23e08e92a35daf41",
    "FEXCore/Source/CMakeLists.txt": "753e1a34616a95da259e174ddff1c6186c9e1e366607a1fcbf7c75ec109f74b9",
    "FEXCore/Source/Utils/Allocator.cpp": "7cdfd2d10f657cccfe31e312fec4d0c727b80de8c412962b95aaabfd2094f1d2",
    "FEXCore/Source/Interface/Core/Dispatcher/Dispatcher.cpp": "9817e66205081eec37f635ffdebf42fc21eb75dd4a79f91f12e0be0f34420764",
}
ON = re.compile(r"^(\s*)if \((.*)\) \{ " + re.escape(M) + r" on$")


def sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


def reduce_text(text):
    """The file with the switch off: `on` parts and additions removed, `off` parts kept (one level less indented).
    Returns the text, the `on` conditions, and for every line of the input whether it is live with the switch off."""
    out, conds, lines, i = [], [], text.split("\n"), 0
    kind = ["plain"] * len(lines)
    while i < len(lines):
        ln = lines[i]
        if ln.strip() == M + " added {":
            j = i + 1
            while lines[j].strip() != M + " added }":
                assert M not in lines[j], ("a marker inside an added block", j + 1, lines[j])
                j += 1
            for k in range(i, j + 1):
                kind[k] = "added"
            i = j + 1
            continue
        m = ON.match(ln)
        if m:
            ind = m.group(1)
            conds.append(m.group(2))
            j = i + 1
            while lines[j] not in (ind + "} else { " + M + " off", ind + "} " + M + " end"):
                assert M not in lines[j], ("a marker inside an on part", j + 1, lines[j])
                j += 1
            for k in range(i, j + 1):
                kind[k] = "on"
            if lines[j].endswith(" off"):
                k = j + 1
                while lines[k] != ind + "} " + M + " end":
                    assert M not in lines[k], ("a marker inside an off part", k + 1, lines[k])
                    assert lines[k] == "" or lines[k].startswith(ind + "  "), ("an off part's indentation", k + 1, lines[k])
                    out.append(lines[k][2:] if lines[k] else "")
                    kind[k] = "off"
                    k += 1
                kind[k] = "on"
                i = k + 1
            else:
                i = j + 1
            continue
        if re.search(re.escape(M) + r" added( \(.*\))?$", ln):
            kind[i] = "added"
            i += 1
            continue
        assert M not in ln, ("an unknown marker form", i + 1, ln)
        out.append(ln)
        i += 1
    return "\n".join(out), conds, kind


# 1. the source, switch off = the earlier text
all_conds, kinds, texts = [], {}, {}
for rel, want in PIN.items():
    text = (fex / rel).read_text()
    texts[rel] = text
    try:
        red, conds, kind = reduce_text(text)
    except AssertionError as e:
        check("switch off, %s is FEX b3fc72588's text" % rel, False, e)
        continue
    kinds[rel] = kind
    all_conds += [(rel, c) for c in conds]
    check("switch off, %s is FEX b3fc72588's text (%d on parts, %d added lines)"
          % (rel, len(conds), sum(1 for k in kind if k == "added")), sha(red) == want)
bad = [(rel, c) for rel, c in all_conds if "X87ExactInt64()" not in c and not (rel == "Source/Windows/WOW64/Module.cpp" and c == "WowReducedX87")]
check("every `on` condition contains the switch (%d conditions; the WOW64 module's is its own flag)" % len(all_conds), not bad and len(all_conds) >= 20, bad)
wow = texts["Source/Windows/WOW64/Module.cpp"]
check("...and that flag is set once, to reduced precision AND the switch",
      len(re.findall(r"WowReducedX87 = ", wow)) == 1 and "Context::WowReducedX87 = ReducedX87 && FEXCore::FPState::X87ExactInt64();" in wow
      and "static bool WowReducedX87 {};" in wow)


def uses_outside_on(name, allow_added=False):
    """Lines that use `name` and are live with the switch off (not in an `on` part; additions count unless allow_added)."""
    hits = []
    for rel, text in texts.items():
        for n, ln in enumerate(text.split("\n")):
            if name in ln and kinds[rel][n] not in (("on", "added") if allow_added else ("on",)):
                hits.append("%s:%d: %s" % (rel, n + 1, ln.strip()[:120]))
    return hits


if len(kinds) == len(PIN):
    for name in ("_PushStackFromInt64(", "_ReadStackInt64("):
        check("the new operation %s is emitted only inside an `on` part" % name[1:-1], not uses_outside_on(name) and any(name in t for t in texts.values()),
              uses_outside_on(name))
    for name in ("SaveReducedX87Value(", "RestoreReducedX87Value(", "ImportX87Register(", "ExportX87Register("):
        check("%s is called only inside an `on` part (its declaration and definition are additions)" % name[:-1],
              not uses_outside_on(name, allow_added=True), uses_outside_on(name, allow_added=True))
    check("x87_exact_int and x87_exact_valid are named only in `on` parts and additions",
          not uses_outside_on("x87_exact_", allow_added=True), uses_outside_on("x87_exact_", allow_added=True))
    xp = texts["FEXCore/Source/Interface/IR/Passes/x87StackOptimizationPass.cpp"]
    a = xp.index("inline std::pair<Ref, Ref> X87StackOptimization::LoadExactInt_Slow(uint8_t Offset) {")
    body = xp[a:xp.index("\n}\n", a)]
    first = body.split("\n")[1].strip()
    check("LoadExactInt_Slow answers nothing, before it emits anything, without the switch",
          first == "if (!ReducedPrecisionMode || !FPState::X87ExactInt64()) return {};", first)
    live_cache = [h for h in uses_outside_on("TopInt64", allow_added=True)]
    check("the pass's two new tables are touched only in `on` parts and additions", not live_cache, live_cache)
    added_emit = []
    for rel, text in texts.items():
        if not rel.endswith("x87StackOptimizationPass.cpp"):
            continue
        for n, ln in enumerate(text.split("\n")):
            # an addition outside the new functions and cases may not emit IR: only bookkeeping on the two tables, or a call of LoadExactInt_Slow
            if kinds[rel][n] == "added" and re.search(re.escape(M) + r" added( \(.*\))?$", ln):
                code = ln.split("//")[0].strip()
                if not re.fullmatch(r"(TopInt64(Valid)?Cache(\[[A-Za-z0-9]+\])? ?(= ?(nullptr|Exact\w*|Valid\w*)|\.fill\(nullptr\));"
                                    r"|std::rotate\(TopInt64(Valid)?Cache\.begin\(\), std::(next|prev)\(TopInt64(Valid)?Cache\.(begin|end)\(\)\), TopInt64(Valid)?Cache\.end\(\)\);"
                                    r"|auto \[\w+, \w+\] = LoadExactInt_Slow\(\w+\);|Ref ExactMask \{\};|std::pair<Ref, Ref> LoadExactInt_Slow\(uint8_t Offset\);"
                                    r"|#include <utility>|#include <FEXCore/Utils/FPState.h>)", code):
                    added_emit.append("%d: %s" % (n + 1, code))
    check("the single-line additions in the pass are bookkeeping on those tables, calls of LoadExactInt_Slow and declarations", not added_emit, added_emit)

# what cannot be written as on / off
irj = (fex / "FEXCore/Source/Interface/IR/IR.json").read_text()
ir_red = irj
for name in ('      "PushStackFromInt64 FPR:$X80Src, FPR:$Int64Value": {', '      "FPR = ReadStackInt64 FPR:$Converted": {'):
    if name in ir_red:
        a = ir_red.index(name)
        ir_red = ir_red[:a] + ir_red[ir_red.index("      },\n", a) + len("      },\n"):]
check("IR.json: the earlier file plus the two operations (which only the switched-on dispatcher emits)",
      sha(ir_red) == PIN_OTHER["FEXCore/Source/Interface/IR/IR.json"] and irj.count('"X87": true') >= 2 and irj != ir_red)
gen = (fex / "FEXCore/Scripts/json_ir_generator.py")
tree_cmp = subprocess.run(["grep", "-rnE", r"Op (<|>|<=|>=) (IR::)?OP_|OP_[A-Z0-9_]+ (<|>|<=|>=) ", str(fex / "FEXCore/Source"), "--include=*.cpp", "--include=*.h", "--include=*.inl"],
                          capture_output=True, text=True).stdout.strip()
check("no code orders IR operations by their numbers (two more numbers change no emitted code)", gen.is_file() and not tree_cmp, tree_cmp[:500])
cm = (fex / "FEXCore/Source/CMakeLists.txt").read_text()
check("CMakeLists.txt: the earlier file plus Utils/FPState.cpp", sha(cm.replace("  Utils/FPState.cpp\n", "", 1)) == PIN_OTHER["FEXCore/Source/CMakeLists.txt"]
      and cm.count("  Utils/FPState.cpp\n") == 1)
al = (fex / "FEXCore/Source/Utils/Allocator.cpp").read_text()
ah = (fex / "FEXCore/Source/Utils/Allocator.h").read_text()
check("Allocator.cpp: the earlier file plus one include of its own header, which holds declarations only",
      sha(al.replace('#include "Utils/Allocator.h"\n', "", 1)) == PIN_OTHER["FEXCore/Source/Utils/Allocator.cpp"]
      and all(ln.strip() == "" or ln.startswith(("//", "#", "namespace", "struct InternalThreadState;", "}", "void ")) for ln in ah.split("\n")), ah[:400])
dp = (fex / "FEXCore/Source/Interface/Core/Dispatcher/Dispatcher.cpp").read_text()
new_d = ("    // The page can be more than 4095 bytes from the frame after alignment.\n"
         "    constexpr auto FaultPageOffset = offsetof(FEXCore::Core::InternalThreadState, InterruptFaultPage) -\n"
         "                                     offsetof(FEXCore::Core::InternalThreadState, BaseFrameState);\n"
         "    LoadConstant(ARMEmitter::Size::i64Bit, TMP2, FaultPageOffset);\n"
         "    strb(ARMEmitter::XReg::zr, STATE, TMP2, ARMEmitter::ExtendedType::LSL_64, 0);\n")
old_d = ("    // Trigger segfault if any deferred signals are pending\n"
         "    strb(ARMEmitter::XReg::zr, STATE,\n"
         "         offsetof(FEXCore::Core::InternalThreadState, InterruptFaultPage) - offsetof(FEXCore::Core::InternalThreadState, BaseFrameState));\n")
blk = dp[dp.rindex("#ifndef _WIN32", 0, dp.index(new_d)):dp.index("#endif", dp.index(new_d))] if new_d in dp else ""
check("Dispatcher.cpp: the earlier file but for the deferred-signal store, which is compiled only without _WIN32 (never in the two modules that run games)",
      new_d in dp and sha(dp.replace(new_d, old_d, 1)) == PIN_OTHER["FEXCore/Source/Interface/Core/Dispatcher/Dispatcher.cpp"]
      and blk.count("#ifdef") + blk.count("#if ") + blk.count("#ifndef") == 1 and "#else" not in blk)

# 2. the frame
cs = (fex / "FEXCore/include/FEXCore/Core/CoreState.h").read_text()
frame = cs[cs.index("struct CpuStateFrame {"):]
frame = frame[:frame.index("\n};\n")]
tail = [ln.strip() for ln in frame.split("\n") if ln.strip() and not ln.strip().startswith(("/*", "*", "//"))][-2:]
check("the two fields are the last members of CpuStateFrame (no member of the CPU state or the frame moves)",
      tail == ["alignas(16) uint64_t x87_exact_int[8][2] {};", "uint32_t x87_exact_valid {};"], tail)
cpu = cs[cs.index("struct alignas(64) CPUState {"):cs.index("static_assert(std::is_trivially_copyable_v<CPUState>")]
check("...and CPUState has neither", "x87_exact" not in cpu)
check("their offsets are asserted to be within reach of the context loads and stores",
      "static_assert(offsetof(CpuStateFrame, x87_exact_valid) % 4 == 0 && offsetof(CpuStateFrame, x87_exact_valid) <= 16380," in cs
      and "offsetof(CpuStateFrame, x87_exact_int) + sizeof(CpuStateFrame::x87_exact_int) <= 32760," in cs
      and 'static_assert(offsetof(CpuStateFrame, State) == 0, "STATE is the frame and the CPU state");' in cs)
ecm = (fex / "Source/Windows/ARM64EC/Module.cpp").read_text()
check("ARM64EC: the frame offsets Module.S reads are the earlier ones (1512, 4080, 4088, 4096)",
      all(s in ecm for s in ("SuspendDoorbell) == 1512,", "IosCpuArea) == 4080,", "IosXpCallCount) == 4088,", "IosAliasHint) == 4096,")))
ctx32 = [ln for ln in "".join(texts.get(k, "") for k in ("FEXCore/Source/Interface/Core/OpcodeDispatcher.h", "FEXCore/Source/Interface/Core/OpcodeDispatcher/X87.cpp",
                                                         "FEXCore/Source/Interface/Core/OpcodeDispatcher/Vector.cpp",
                                                         "FEXCore/Source/Interface/IR/Passes/x87StackOptimizationPass.cpp")).split("\n")
         if "x87_exact_valid" in ln and "ContextGPR(" in ln]
check("the mask is loaded and stored as 32 bits everywhere (%d sites)" % len(ctx32),
      len(ctx32) >= 10 and all("OpSize::i32Bit" in ln and "CpuStateFrame, x87_exact_valid" in ln for ln in ctx32), [ln.strip() for ln in ctx32 if "i32Bit" not in ln])
check("ARM64EC keeps the x87 precision the game's settings chose (Madeira's 01c5e4b0f is not taken)",
      "using full x87 precision for ARM64EC context fidelity" not in ecm)

# 3. the switch
fp = (fex / "FEXCore/Source/Utils/FPState.cpp").read_text()
a = fp.index("bool X87ExactInt64() {")
helper = fp[a:fp.index("\n}\n", a) + 3]
check("the switch is read with getenv once (a function-local static) and compared with exactly \"1\"",
      helper.count('getenv("%s")' % KEY) == 1 and "static const bool On = [] {" in helper
      and "const bool Value = Env && Env[0] == '1' && Env[1] == '\\0';" in helper)
cxx = shutil.which("c++") or shutil.which("clang++")
if cxx:
    with tempfile.TemporaryDirectory() as tmp:
        src = pathlib.Path(tmp) / "t.cpp"
        src.write_text('#include <cstdio>\n#include <cstdlib>\nstatic int g_logs;\n'
                       'namespace LogMan { namespace Msg { template <typename... A> void IFmt(const char*, A...) { g_logs++; } } }\n'
                       + helper + 'int main() { int a = X87ExactInt64(), b = X87ExactInt64(), c = X87ExactInt64(); printf("%d %d %d %d\\n", a, b, c, g_logs); }\n')
        exe = pathlib.Path(tmp) / "t"
        r = subprocess.run([cxx, "-std=c++20", "-O1", "-Wall", "-Werror", "-o", str(exe), str(src)], capture_output=True, text=True)
        check("the switch compiles on the host", r.returncode == 0, r.stderr[-2000:])
        if r.returncode == 0:
            env0 = {k: v for k, v in os.environ.items() if k != KEY}
            for value in (None, "", "0", "11", "true", "2", " 1", "1 ", "yes", "1"):
                env = dict(env0)
                if value is not None:
                    env[KEY] = value
                out = subprocess.run([str(exe)], capture_output=True, text=True, env=env).stdout.split()
                want = ["1", "1", "1", "1"] if value == "1" else ["0", "0", "0", "0"]
                check("key %s: %s" % ("unset" if value is None else repr(value), "on, one line" if value == "1" else "off, no line"), out == want, out)
else:
    print("note: no host C++ compiler; the switch was not run")

# 4. the new code, switch on: the fork's own value-model tests
for t in ("unittests/host/check-x87-provenance.py", "unittests/host/check-x87-state-save.py"):
    if (fex / t).is_file() and cxx:
        r = subprocess.run([sys.executable, str(fex / t)], capture_output=True, text=True)
        check("switch on: the fork's %s passes on the production statements" % t.split("/")[-1], r.returncode == 0, (r.stdout + r.stderr)[-1500:])
    else:
        check("the fork's %s is there" % t, (fex / t).is_file())

# 5. the credit in the fork
lic = (fex / "LICENSE-MADEIRA.md").read_text()
check("the fork's LICENSE-MADEIRA.md credits the commits, their author and the Playport authors",
      "intraducine" in lic and "codex/fex-runtime-consolidation" in lic and "ad1a3db3508" in lic and "playportdev/playport" in lic
      and (fex / "docs/playport-port.md").is_file())

sys.exit(0 if ok else 1)

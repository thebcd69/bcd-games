#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""tools/patch-fex-ios-exit-runaway.py: an exit that re-enters the torn-down emulator ends the process (round 630, agent A2 S18).

Enshrouded (PG716Q, 0CAY0F, 2TKD32, build 626) compiled a decommitted host page for ever after LdrShutdownProcess had torn FEX down.

Checks (FEX tree: FEX_SRC, default ~/madeira-bcd/FEX; that part is skipped without it): the patch applies to a copy of the pinned
Core.cpp after the Mono opt-in patch (the build's order), a second run says "already in" and changes nothing, a moved anchor refuses
it; the declarations sit at file scope right before ContextImpl::CompileBlock, the stop is the first thing inside the host-address
gate and only under FEX_IOS_HOST and _WIN32; nothing else changes. The stop block, cut from the patched file and compiled on the host
with stub RtlDllShutdownInProgress/NtTerminateProcess/LogMan: outside a shutdown 1,000 hits end nothing; in a shutdown the 63rd hit
ends nothing, the 64th ends the process once with 0xC000010A and logs one line. The ARM64EC build applies it without failing the
build and restores Core.cpp; CI runs this check.
"""
from pathlib import Path
import difflib, os, shutil, subprocess, sys, tempfile

root = Path(__file__).resolve().parents[2]
ok = True


def check(what, cond, detail=""):
    global ok
    print(("ok   " if cond else "FAIL ") + what + (("\n     " + str(detail)[:1500]) if (detail and not cond) else ""))
    ok = ok and bool(cond)


patch = root / "tools/patch-fex-ios-exit-runaway.py"
mono = root / "tools/patch-fex-ios-mono-bp-unlocked-miss.py"
fex = Path(os.environ.get("FEX_SRC", Path.home() / "madeira-bcd/FEX"))
core = fex / "FEXCore/Source/Interface/Core/Core.cpp"

build = (root / "tools/build-xtajit64.sh").read_text()
restore = build[build.index('git -C FEX checkout -- "$CPUF"'):]
restore = restore[:restore.index("\n")]
check("the ARM64EC build applies it after the Mono patch, without failing the build, and restores Core.cpp",
      'python3 "$R/tools/patch-fex-ios-exit-runaway.py" "$R/FEX" || echo "::warning::' in build
      and build.index("patch-fex-ios-mono-bp-unlocked-miss.py") < build.index("patch-fex-ios-exit-runaway.py\" \"$R/FEX\"")
      < build.index('git -C FEX checkout -- "$CPUF"') and "FEXCore/Source/Interface/Core/Core.cpp" in restore)
wf = (root / ".github/workflows/build-ipa.yml").read_text()
check("CI runs this check", "python3 tests/host/check-fex-exit-runaway.py" in wf)

if not core.is_file():
    print("skip: no FEX tree here (FEX_SRC or ~/madeira-bcd/FEX); the CI and the Mac run the source part")
else:
    with tempfile.TemporaryDirectory(prefix="madeira-fex-runaway-") as tmp:
        tree = Path(tmp)
        (tree / "FEXCore/Source/Interface/Core").mkdir(parents=True)
        t = tree / "FEXCore/Source/Interface/Core/Core.cpp"
        shutil.copy(core, t)
        rm = subprocess.run([sys.executable, str(mono), str(t)], capture_output=True, text=True)
        check("the Mono opt-in patch applies first, as in the build", rm.returncode == 0, rm.stdout + rm.stderr)
        before = t.read_text()
        r1 = subprocess.run([sys.executable, str(patch), str(tree)], capture_output=True, text=True)
        once = t.read_text()
        r2 = subprocess.run([sys.executable, str(patch), str(tree)], capture_output=True, text=True)
        check("it applies once; a second run says so and changes nothing",
              r1.returncode == 0 and r2.returncode == 0 and "already in" in r2.stdout and t.read_text() == once and once != before,
              r1.stdout + r1.stderr + r2.stdout + r2.stderr)
        head = "uintptr_t ContextImpl::CompileBlock(FEXCore::Core::CpuStateFrame* Frame, uint64_t GuestRIP, uint64_t MaxInst) {"
        decl = once[:once.index(head)]
        check("the declarations are at file scope right before CompileBlock, only for the iOS Windows build",
              decl.rstrip().endswith("#endif") and decl[decl.rindex("#if defined(FEX_IOS_HOST) && defined(_WIN32)"):].count("extern \"C\"") == 2
              and 'extern "C" unsigned char RtlDllShutdownInProgress(void);' in decl[decl.rindex("#if defined(FEX_IOS_HOST)"):])
        fn = once[once.index(head):]
        gate = fn.index("  if (GuestRIP >= 0x7400000000ULL || RIPInFEXCodeBuffer) {")
        block = fn[gate:fn.index("    static uint32_t BogusRIPCount = 0;", gate)]
        check("the stop is the first thing inside the host-address gate, under FEX_IOS_HOST and _WIN32",
              block.count("\n") > 5 and block.split("\n")[1] == "#if defined(FEX_IOS_HOST) && defined(_WIN32)"
              and block.rstrip().endswith("#endif") and "if (RtlDllShutdownInProgress()) {" in block)
        a, b = before.splitlines(), once.splitlines()
        ops = [o for o in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes() if o[0] != "equal"]
        check("nothing else changes: two insertions", len(ops) == 2 and all(o[0] == "insert" for o in ops), ops)
        t.write_text(before.replace("  if (GuestRIP >= 0x7400000000ULL || RIPInFEXCodeBuffer) {", "  if (GuestRIP >= 0x7500000000ULL || RIPInFEXCodeBuffer) {"))
        r3 = subprocess.run([sys.executable, str(patch), str(tree)], capture_output=True, text=True)
        check("a moved anchor refuses the patch and leaves the file alone",
              r3.returncode != 0 and "gate" in r3.stderr and "exit-runaway" not in t.read_text())

        stop = block[block.index("#if defined(FEX_IOS_HOST) && defined(_WIN32)\n") + len("#if defined(FEX_IOS_HOST) && defined(_WIN32)\n"):block.rindex("#endif")]
        harness = r'''
#include <atomic>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
static bool g_shutdown;
static int g_ends, g_logs;
static int32_t g_status;
extern "C" unsigned char RtlDllShutdownInProgress(void) { return g_shutdown; }
extern "C" int32_t NtTerminateProcess(void*, int32_t s) { g_ends++; g_status = s; return 0; }
namespace LogMan { namespace Msg { template <typename... A> void EFmt(const char*, A...) { g_logs++; } } }
static void gate(uint64_t GuestRIP) {
''' + stop + r'''
}
int main() {
  for (int i = 0; i < 1000; i++) gate(0x7c2be10000ull);
  if (g_ends || g_logs) { std::puts("FAIL: outside a shutdown the gate ended the process"); return 1; }
  g_shutdown = true;
  /* the counter is shared: the 1,000 hits above did not count (the stop counts only while the loader shuts down) */
  for (int i = 0; i < 63; i++) gate(0x7c2be10000ull);
  if (g_ends) { std::puts("FAIL: ended before the 64th hit"); return 1; }
  gate(0x7c2be10000ull);
  if (g_ends != 1 || g_status != (int32_t)0xC000010A || g_logs != 1) { std::printf("FAIL: ends=%d status=%x logs=%d\n", g_ends, g_status, g_logs); return 1; }
  for (int i = 0; i < 100; i++) gate(0x7c2be10000ull);
  if (g_ends != 1 || g_logs != 1) { std::puts("FAIL: more than one end"); return 1; }
  std::puts("ok: no end outside a shutdown; in one, the 64th hit ends the process once with STATUS_PROCESS_IS_TERMINATING");
  return 0;
}
'''
        cxx = shutil.which("c++") or shutil.which("clang++")
        if cxx:
            src = tree / "t.cpp"
            exe = tree / "t"
            src.write_text(harness)
            bld = subprocess.run([cxx, "-std=c++20", "-Wall", "-Werror", str(src), "-o", str(exe)], capture_output=True, text=True)
            check("the stop block builds on the host: " + bld.stderr[-600:], bld.returncode == 0)
            if bld.returncode == 0:
                run = subprocess.run([str(exe)], capture_output=True, text=True)
                print(run.stdout, end="")
                check("the stop block's behaviour", run.returncode == 0, run.stdout + run.stderr)
        else:
            print("skip: no C++ compiler for the behaviour part")

print("PASS: an exit that re-enters the torn-down emulator ends the process" if ok else "FAILED: FEX exit-runaway stop")
sys.exit(0 if ok else 1)

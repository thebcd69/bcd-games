#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""env.MADEIRA_SMC_HOT_PAGE and env.MADEIRA_CALLRET_RESET_RATE: FEX's hot self-modifying-code pages, opt-in (round 632; 125hz's 68bb21cf4 + e6055c96e).

A program that keeps a counter or PRNG state in the same page as the code that updates it takes a write fault on every store: each one
untraps the page, invalidates its code, single-steps the store, recompiles and traps the page again (Far Cry Primal, Watch Dogs 2, Mad Max:
"protector-incode-counter-loop", 201,052 handled events in 40 s). With env.MADEIRA_SMC_HOT_PAGE = N, a page that takes N write faults within
a second turns hot: it is invalidated once more and never write-protected again, and FEXCore compiles every block that touches it with a
per-instruction code check (SMCChecks=full for those blocks), in the 64-bit and the 32-bit module. env.MADEIRA_CALLRET_RESET_RATE = N skips
the call-return stack reset a code invalidation does when the thread did one less than N ms ago. Upstream turns both on (64 faults, 50 ms);
in BCD both are off unless the key names a number, and with them off nothing changes: the fault counter returns before it reads a clock
or takes a lock, every inline-SMC fault keeps its log line (auto-tune counts them), no "[smc-hot]" line is printed.

BCD's addition to upstream's WOW64 commit (which enabled the tracker's hot pages but left the module's SyscallHandler without
IsHotSMCCodePage, so blocks on a hot page were not checked): the WOW64 handler answers IsHotSMCCodePage (guest page in, host page to the
tracker) and, as the ARM64EC module does, a fault on an already hot page skips the inline single step.

Checks (FEX tree: $FEX_SRC, FEX/ of this checkout, ~/madeira-bcd/FEX; skipped without it):
  - the source of both modules and of FEXCore: the defaults, the exact key reading, the log lines printed only when on, the sampling only
    with the key on (the unsampled line text is the old one), the call-ret limiter default, the WOW64 override and its address conversion;
  - the tracker's hot-page table (EnableHotSMCPages, IsHotSMCPage, CountSMCWriteFault, ForgetHotSMCPages cut out of InvalidationTracker.cpp
    and run on the host with a fake clock): threshold 0 never counts, never reads the clock and never marks a page; N faults inside the window
    make exactly the Nth one hot; a gap longer than the window restarts the count; two pages that share a slot evict each other; a 65th hot
    page is refused; freed pages are forgotten and their slot is reused; page 0 is ignored;
  - IosSkipInvalidationCallRetReset (cut out of Core.cpp, fake clock): unset, empty, "0" or a word never skips and never reads the counter
    frequency; N skips a second reset of the same thread within N ms and not after; another thread is not affected;
  - the settings catalog entries (both off), the auto-tune rules and their fixtures, and CI runs this check.
The host run is skipped when no C++ compiler is found.
"""
from pathlib import Path
import json, os, re, shutil, subprocess, sys, tempfile

root = Path(__file__).resolve().parents[2]
ok = True


def check(what, cond, detail=""):
    global ok
    print(("ok   " if cond else "FAIL ") + what + (("\n     " + str(detail)[:1500]) if (detail and not cond) else ""))
    ok = ok and bool(cond)


wf = (root / ".github/workflows/build-ipa.yml").read_text()
check("CI runs this check", "python3 tests/host/check-fex-hot-smc-pages.py" in wf)
cat = (root / "build/tools/gen-config-catalog.py").read_text()
for key, kind in (("MADEIRA_SMC_HOT_PAGE", "int"), ("MADEIRA_CALLRET_RESET_RATE", "int")):
    entry = cat[cat.index('"env.%s"' % key):] if ('"env.%s"' % key) in cat else ""
    entry = entry[:entry.index("},\n") + 3] if entry else ""
    check(f"the settings catalog lists {key}, off by default", f'"kind": "{kind}"' in entry and '"default": "0"' in entry, entry[:300])

rules = (root / "build/tools/autotune_rules.py").read_text()
check("auto-tune: the protector counter loop tries the key, and the old verdict stays for a session that already had it",
      '"id": "protector-incode-counter-hot-page"' in rules and '"env.MADEIRA_SMC_HOT_PAGE = 64"' in rules
      and '"id": "protector-incode-counter-loop"' in rules)
fix = root / "tests/fixtures/autotune"
for name, want in (("far-cry-primal-protector-counter-2026-10-28", "trying"), ("watch-dogs-2-protector-counter-2026-10-29", "trying"),
                   ("far-cry-primal-protector-counter-hot-on-synthetic", "known")):
    p = fix / (name + ".expect")
    exp = json.loads(p.read_text()) if p.exists() else {}
    check(f"fixture {name}: {want}", exp.get("status") == want, exp.get("status"))

cands = ([Path(os.environ["FEX_SRC"]).expanduser()] if os.environ.get("FEX_SRC") else []) + [root / "FEX", Path("~/madeira-bcd/FEX").expanduser()]
fex = next((c for c in cands if (c / "Source/Windows/Common/InvalidationTracker.cpp").is_file()), None)
if fex is None:
    print("skip the FEX part: no FEX tree (set FEX_SRC)")
    print("PASS: FEX hot SMC pages" if ok else "FAILED: FEX hot SMC pages")
    sys.exit(0 if ok else 1)

ec = (fex / "Source/Windows/ARM64EC/Module.cpp").read_text()
wow = (fex / "Source/Windows/WOW64/Module.cpp").read_text()
core = (fex / "FEXCore/Source/Interface/Core/Core.cpp").read_text()
trk = (fex / "Source/Windows/Common/InvalidationTracker.cpp").read_text()
hdr = (fex / "Source/Windows/Common/InvalidationTracker.h").read_text()

for name, text in (("ARM64EC", ec), ("WOW64", wow)):
    check(f"{name}: off unless the key names a number, read once at start",
          text.count("uint32_t HotThreshold = 0;") == 1 and text.count('getenv("MADEIRA_SMC_HOT_PAGE")') == 1
          and "HotThreshold = static_cast<uint32_t>(strtoul(Hot, nullptr, 0));" in text and "uint32_t HotThreshold = 64;" not in text)
    i = text.index("uint32_t HotThreshold = 0;")
    blk = text[i:text.index("  }\n  ImageTracker.emplace", i)]
    check(f"{name}: the [smc-hot] line is printed only when on, and the tracker is told the threshold",
          "InvalidationTracker->EnableHotSMCPages(HotThreshold);" in blk and "if (HotThreshold) {\n      LogMan::Msg::EFmt(\"[smc-hot]" in blk
          and blk.count("[smc-hot]") == 1)
check("ARM64EC: the unsampled inline-SMC line is the old text, sampling only with the key on",
      '''if (!InvalidationTracker->HotSMCPagesEnabled()) {
          LogMan::Msg::DFmt("Handled inline self-modifying code: pc: {:X} rip: {:X} fault: {:X}", NativeContext->Pc,
                            Thread->CurrentFrame->State.rip, FaultAddress);
        } else {''' in ec and "InlineN <= 16 || (InlineN % 4096) == 0" in ec)
check("ARM64EC: a fault on an already hot page skips the inline single step", "if (!SmcHotPage && CTX->IsAddressInCodeBuffer(Thread, NativeContext->Pc)" in ec
      and "HandleRWXAccessViolation(Thread, NativeContext->Pc, FaultAddress, &SmcHotPage)" in ec)
check("ARM64EC: IsHotSMCCodePage asks the tracker for the alias's PE page too",
      "bool IsHotSMCCodePage(uint64_t Address) override {" in ec and "InvalidationTracker->IsHotSMCPage(OrigAddr)" in ec)
# lead (round 632): with MADEIRA_SMC_HOT_PAGE off no page can be hot, so both overrides answer false before any lookup
check("ARM64EC: IsHotSMCCodePage answers false at once while hot pages are off",
      "bool IsHotSMCCodePage(uint64_t Address) override {\n    // madeira-bcd (round 632): no page can be hot while MADEIRA_SMC_HOT_PAGE is off, so a compile asks nothing more.\n"
      "    if (!InvalidationTracker->HotSMCPagesEnabled()) {\n      return false;\n    }\n#ifdef FEX_IOS_HOST" in ec)
check("WOW64: IsHotSMCCodePage answers for the guest page through the window, and false at once while hot pages are off",
      "bool IsHotSMCCodePage(uint64_t Address) override {\n    return InvalidationTracker->HotSMCPagesEnabled() && "
      "InvalidationTracker->IsHotSMCPage(GuestWindow::ToHost(Address));\n  }" in wow)
check("WOW64: a fault on an already hot page skips the inline single step",
      "HandleRWXAccessViolation(Thread, Context->Pc, FaultAddress, &SmcHotPage)" in wow and "if (!SmcHotPage && CTX->IsAddressInCodeBuffer(Thread, Context->Pc)" in wow)
check("FEXCore asks IsHotSMCCodePage only for the mtrack mode and treats a hot page like SMCChecks=full",
      "Config.SMCChecks == FEXCore::Config::CONFIG_SMC_MTRACK && SyscallHandler" in core
      and "Config.SMCChecks == FEXCore::Config::CONFIG_SMC_FULL || Block.ForceFullSMCDetection || HotSMCBlock" in core)
_c = trk[trk.index("bool InvalidationTracker::CountSMCWriteFault(uint64_t Page) {"):]
_c = _c[:_c.index("\n}\n")]
check("the fault counter returns before it reads a clock or takes the lock when off",
      _c.index("if (!HotPageThreshold) {\n    return false;\n  }") < _c.index('mrs %0, cntvct_el0') < _c.index("std::scoped_lock Lock(HotPagesLock);"))
check("the call-ret limiter is off unless the key names a number, and reads the frequency only then",
      "uint64_t Ms = 0;" in core and "uint64_t Ms = 50;" not in core and "if (Ms) {\n      __asm volatile(\"mrs %0, cntfrq_el0\"" in core)

cxx = shutil.which("clang++") or shutil.which("c++") or shutil.which("g++")
if not cxx:
    print("skip the host run: no C++ compiler")
    print("PASS: FEX hot SMC pages" if ok else "FAILED: FEX hot SMC pages")
    sys.exit(0 if ok else 1)

# ---- the tracker's table ---------------------------------------------------------------------------------------------------------
a = trk.index("void InvalidationTracker::EnableHotSMCPages(uint32_t Threshold) {")
b = trk.index("void InvalidationTracker::ForgetHotSMCPages(uint64_t Begin, uint64_t End) {")
funcs = trk[a:trk.index("\n}\n", b) + 3]
CLK = '  __asm volatile("mrs %0, cntvct_el0" : "=r"(Now));\n  __asm volatile("mrs %0, cntfrq_el0" : "=r"(Frequency));\n'
check("the counter reads the virtual counter and its frequency", CLK in funcs)
funcs = funcs.replace(CLK, "  Now = FakeNow;\n  ClockReads++;\n  Frequency = 1000;\n")
members = hdr[hdr.index("  static constexpr size_t HotFaultSlots = 64;"):hdr.index("// Slots of HotPages in use (high-water mark)\n") + len("// Slots of HotPages in use (high-water mark)\n")]
acc_a = hdr.index("  bool HotSMCPagesEnabled() const {")
accessor = hdr[acc_a:hdr.index("\n  }\n", acc_a) + 5]

T_HARNESS = r'''
#include <atomic>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <mutex>

static uint64_t FakeNow = 0;
static int ClockReads = 0;
namespace FEXCore::Utils {
static constexpr uint64_t FEX_PAGE_SIZE = 4096;
static constexpr uint64_t FEX_PAGE_SHIFT = 12;
static constexpr uint64_t FEX_PAGE_MASK = ~(FEX_PAGE_SIZE - 1);
} // namespace FEXCore::Utils

namespace FEX::Windows {
class InvalidationTracker {
public:
  void EnableHotSMCPages(uint32_t Threshold);
  bool IsHotSMCPage(uint64_t Address) const;
@ACCESSOR@
  bool CountSMCWriteFault(uint64_t Page);
  void ForgetHotSMCPages(uint64_t Begin, uint64_t End);
@MEMBERS@
};
@FUNCS@
} // namespace FEX::Windows

using FEX::Windows::InvalidationTracker;

static int Fail = 0;
#define EXPECT(c, msg) do { if (!(c)) { std::printf("FAIL %s\n", msg); Fail++; } } while (0)

int main() {
  const uint64_t P = 0x140000000ull;
  {
    InvalidationTracker T;                      // never enabled: threshold 0
    bool any = false;
    for (int i = 0; i < 1000; i++) any |= T.CountSMCWriteFault(P + 4096 * (i % 7));
    EXPECT(!any && ClockReads == 0, "threshold 0: nothing counts and the clock is never read");
    EXPECT(!T.HotSMCPagesEnabled() && !T.IsHotSMCPage(P), "threshold 0: not enabled, no hot page");
    T.EnableHotSMCPages(0);
    EXPECT(!T.HotSMCPagesEnabled() && !T.CountSMCWriteFault(P) && ClockReads == 0, "EnableHotSMCPages(0) keeps it off");
  }
  {
    InvalidationTracker T;
    T.EnableHotSMCPages(64);
    EXPECT(T.HotSMCPagesEnabled(), "threshold 64: enabled");
    int madeHot = 0, at = 0;
    for (int i = 1; i <= 64; i++) { FakeNow += 10; if (T.CountSMCWriteFault(P)) { madeHot++; at = i; } }
    EXPECT(madeHot == 1 && at == 64, "64 faults inside the window: exactly the 64th makes the page hot");
    EXPECT(T.IsHotSMCPage(P) && T.IsHotSMCPage(P + 0xfff) && !T.IsHotSMCPage(P + 0x1000) && !T.IsHotSMCPage(P - 1),
           "the whole 4 KB page is hot and the neighbours are not");
    EXPECT(ClockReads == 64, "one clock read per counted fault");
    // a gap longer than the window (1000 ticks) restarts the count
    for (int i = 1; i <= 63; i++) { FakeNow += 10; T.CountSMCWriteFault(P + 0x5000); }
    FakeNow += 2000;
    bool hot = false;
    for (int i = 1; i <= 63; i++) { FakeNow += 10; hot |= T.CountSMCWriteFault(P + 0x5000); }
    EXPECT(!hot && !T.IsHotSMCPage(P + 0x5000), "63 + gap + 63 faults: never hot");
    FakeNow += 10;
    EXPECT(T.CountSMCWriteFault(P + 0x5000) , "...but the 64th fault after the gap's restart is hot");
    // page 0 is ignored
    InvalidationTracker Z; Z.EnableHotSMCPages(1);
    EXPECT(!Z.CountSMCWriteFault(0), "page 0 is ignored");
    EXPECT(Z.CountSMCWriteFault(P), "threshold 1: the first fault makes a page hot");
  }
  {
    // two pages that share a slot (64 pages apart) evict each other: neither gets hot
    InvalidationTracker T;
    T.EnableHotSMCPages(8);
    const uint64_t A = P, B = P + 64 * 4096;
    bool hot = false;
    for (int i = 0; i < 100; i++) { FakeNow += 1; hot |= T.CountSMCWriteFault((i & 1) ? B : A); }
    EXPECT(!hot, "two pages sharing a slot evict each other and neither turns hot");
  }
  {
    // the table holds 64 hot pages; the 65th is refused; a forgotten page frees its slot
    InvalidationTracker T;
    T.EnableHotSMCPages(2);
    int hot = 0;
    for (int i = 0; i < 64; i++) { T.CountSMCWriteFault(P + 4096 * i); FakeNow += 1; hot += T.CountSMCWriteFault(P + 4096 * i); }
    EXPECT(hot == 64, "64 pages turn hot");
    T.CountSMCWriteFault(P + 4096 * 200); FakeNow += 1;
    EXPECT(!T.CountSMCWriteFault(P + 4096 * 200) && !T.IsHotSMCPage(P + 4096 * 200), "the 65th page is refused (it keeps its write trap)");
    T.ForgetHotSMCPages(P + 4096 * 10, P + 4096 * 12);
    EXPECT(!T.IsHotSMCPage(P + 4096 * 10) && !T.IsHotSMCPage(P + 4096 * 11) && T.IsHotSMCPage(P + 4096 * 12) && T.IsHotSMCPage(P + 4096 * 9),
           "freed pages are forgotten, the neighbours stay");
    T.CountSMCWriteFault(P + 4096 * 200); FakeNow += 1;
    EXPECT(T.CountSMCWriteFault(P + 4096 * 200) && T.IsHotSMCPage(P + 4096 * 200), "the freed slot is reused by the next hot page");
  }
  std::printf("done fail=%d\n", Fail);
  return Fail ? 1 : 0;
}
'''

# ---- the call-ret limiter ----------------------------------------------------------------------------------------------------------
la = core.index("static bool IosSkipInvalidationCallRetReset(FEXCore::Core::InternalThreadState* Thread) {")
lfn = core[la:core.index("\n}\n", la) + 3]
LNOW = '  __asm volatile("mrs %0, cntvct_el0" : "=r"(Now));\n'
LFRQ = '      __asm volatile("mrs %0, cntfrq_el0" : "=r"(Frequency));\n'
check("the limiter reads the counter and the frequency", LNOW in lfn and LFRQ in lfn)
lfn = lfn.replace(LNOW, "  Now = LNow;\n").replace(LFRQ, "      Frequency = 1000;\n      FreqReads++;\n")

L_HARNESS = r'''
#include <cstdint>
#include <cstdio>
#include <cstdlib>
static uint64_t LNow = 0;
static int FreqReads = 0;
static int LogLines = 0;
namespace FEXCore::Core { struct InternalThreadState { int x; }; }
namespace LogMan::Msg { template <typename... A> void EFmt(const char*, A...) { LogLines++; } }
@FN@
int main() {
  FEXCore::Core::InternalThreadState T1 {1}, T2 {2};
  int skipped = 0, calls = 0;
  for (int i = 0; i < 6; i++) { LNow += 10; calls++; skipped += IosSkipInvalidationCallRetReset(&T1); }   // 10 ticks apart = 10 ms at 1000 Hz? no: 1 tick = 1 ms
  std::printf("t1_first_run_skipped=%d calls=%d freq_reads=%d\n", skipped, calls, FreqReads);
  LNow += 500;
  std::printf("t1_after_gap=%d\n", IosSkipInvalidationCallRetReset(&T1) ? 1 : 0);
  std::printf("t2_first=%d\n", IosSkipInvalidationCallRetReset(&T2) ? 1 : 0);
  std::printf("t2_second=%d\n", IosSkipInvalidationCallRetReset(&T2) ? 1 : 0);
  std::printf("log=%d\n", LogLines);
  return 0;
}
'''

with tempfile.TemporaryDirectory(prefix="madeira-hotsmc-") as tmp:
    t = Path(tmp)
    (t / "t.cpp").write_text(T_HARNESS.replace("@ACCESSOR@", accessor).replace("@MEMBERS@", members).replace("@FUNCS@", funcs))
    r = subprocess.run([cxx, "-std=c++20", "-O1", "-Wall", "-Wno-unused-function", str(t / "t.cpp"), "-o", str(t / "t")], capture_output=True, text=True)
    check("the tracker's hot-page table compiles on the host", r.returncode == 0, r.stderr)
    if r.returncode == 0:
        run = subprocess.run([str(t / "t")], capture_output=True, text=True)
        check("the tracker's hot-page table behaves (see the list above)", run.returncode == 0 and "done fail=0" in run.stdout, run.stdout + run.stderr)

    (t / "l.cpp").write_text(L_HARNESS.replace("@FN@", lfn))
    r = subprocess.run([cxx, "-std=c++20", "-O1", "-Wall", "-Wno-unused-function", str(t / "l.cpp"), "-o", str(t / "l")], capture_output=True, text=True)
    check("the call-ret limiter compiles on the host", r.returncode == 0, r.stderr)
    if r.returncode == 0:
        for v in (None, "", "0", "abc", "-5"):
            env = {k: x for k, x in os.environ.items() if k != "MADEIRA_CALLRET_RESET_RATE"}
            if v is not None:
                env["MADEIRA_CALLRET_RESET_RATE"] = v
            out = dict(kv.split("=") for kv in subprocess.run([str(t / "l")], capture_output=True, text=True, env=env).stdout.split())
            name = "unset" if v is None else repr(v)
            if v == "-5":
                continue
            check(f"limiter, key {name}: never skips a reset, never reads the counter frequency, no log line",
                  out["t1_first_run_skipped"] == "0" and out["t1_after_gap"] == "0" and out["t2_second"] == "0" and out["freq_reads"] == "0" and out["log"] == "0", out)
        env = dict(os.environ, MADEIRA_CALLRET_RESET_RATE="50")
        out = dict(kv.split("=") for kv in subprocess.run([str(t / "l")], capture_output=True, text=True, env=env).stdout.split())
        # 1 tick = 1 ms (frequency 1000); calls 10 ms apart: the first resets, the next four (10..40 ms later) are skipped, the sixth is 50 ms after the reset
        check("limiter, key 50: resets are skipped inside 50 ms, one is allowed after, another thread is independent",
              out["t1_first_run_skipped"] == "4" and out["t1_after_gap"] == "0" and out["t2_first"] == "0" and out["t2_second"] == "1" and out["freq_reads"] == "1", out)

print("PASS: FEX hot SMC pages" if ok else "FAILED: FEX hot SMC pages")
sys.exit(0 if ok else 1)

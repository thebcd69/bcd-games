#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""BCD fork diagnostic: recheck executable mapped guest memory on a FEX NoExec miss.

Evidence: Turing Complete on BCD, guest RIP in a live 16 KiB SEC_COMMIT
anonymous JIT mapping, Wine VirtualQuery(MEM_COMMIT, PAGE_EXECUTE_READWRITE,
MEM_MAPPED), while FEX's XIntervals reports a miss.

Opt-in only: env.MADEIRA_FEX_EXEC_RECHECK = 1. Default behavior is unchanged.
Only the *requested 4 KiB page* can be inserted, only when Windows confirms
that it is committed, executable, unguarded MEM_MAPPED memory. No wild-branch
or ordinary data memory is made executable. No full-range speculative promotion.

The initial implementation intentionally checks on the query miss rather than
modifying the Wine -> FEX notification path: it distinguishes "missing interval"
from a bad instruction/alias even when the earlier map notification is absent.

Applied to the temporary FEX build tree by tools/build-xtajit64.sh.
"""
from pathlib import Path
import sys

MARKER = "turing-exec-recheck-v1"
ANCHOR = """#else
FEXCore::HLE::ExecutableRangeInfo InvalidationTracker::QueryExecutableRange(uint64_t Address) {
  std::shared_lock Lock(IntervalsLock);
#endif
  const auto XResult = XIntervals.Query(Address);"""

REPLACEMENT = r"""#else
FEXCore::HLE::ExecutableRangeInfo InvalidationTracker::QueryExecutableRange(uint64_t Address) {
#ifdef FEX_IOS_HOST
  // turing-exec-recheck-v1: diagnostic opt-in for a genuine executable
  // mapped view missing from FEX's intervals; never promote arbitrary data.
  static const bool RecheckEnabled = [] {
    const char* Value = std::getenv("MADEIRA_FEX_EXEC_RECHECK");
    return Value && Value[0] == '1' && Value[1] == '\0';
  }();
  if (RecheckEnabled) {
    bool Missing;
    {
      std::shared_lock Lock(IntervalsLock);
      Missing = !XIntervals.Query(Address).Enclosed;
    }
    if (Missing) {
      MEMORY_BASIC_INFORMATION Mem {};
      if (VirtualQuery(reinterpret_cast<LPCVOID>(Address), &Mem, sizeof(Mem)) &&
          Mem.State == MEM_COMMIT && Mem.Type == MEM_MAPPED &&
          !(Mem.Protect & (PAGE_GUARD | PAGE_NOACCESS)) &&
          ProtHasExec(Mem.Protect)) {
        // Only the missing guest page: this is not permission to execute an
        // entire region or to overrule a genuine non-executable mapping.
        const uint64_t Page = Address & FEXCore::Utils::FEX_PAGE_MASK;
        const uint64_t End = Page + FEXCore::Utils::FEX_PAGE_SIZE;
        bool Inserted = false;
        {
          std::unique_lock Lock(IntervalsLock);
          if (!XIntervals.Query(Address).Enclosed) {
            const FEXCore::IntervalList<uint64_t>::Interval Span {Page, End};
            XIntervals.Insert(Span);
            if (ProtIsWritable(Mem.Protect)) {
              RWXIntervals.Insert(Span);
            }
            Inserted = true;
          }
        }
        // Logging outside IntervalsLock avoids nested allocation callbacks.
        static std::atomic<uint32_t> RecheckLogs {0};
        if (Inserted && RecheckLogs.fetch_add(1, std::memory_order_relaxed) < 32) {
          LogMan::Msg::EFmt("[tc-exec-recheck] restored {:#x}-{:#x} type={:#x} prot={:#x}",
                            Page, End, Mem.Type, Mem.Protect);
        }
      }
    }
  }
#endif
  std::shared_lock Lock(IntervalsLock);
#endif
  const auto XResult = XIntervals.Query(Address);"""


def patch(source: str) -> str:
    if MARKER in source:
        if source.count(REPLACEMENT) != 1:
            raise ValueError("partial or changed exec-recheck patch")
        return source
    if source.count(ANCHOR) != 1:
        raise ValueError(f"expected one ARM64EC QueryExecutableRange anchor, found {source.count(ANCHOR)}")
    return source.replace(ANCHOR, REPLACEMENT, 1)


if __name__ == "__main__":
    target = Path(sys.argv[1])
    original = target.read_text()
    try:
        updated = patch(original)
    except ValueError as error:
        sys.exit(f"patch-fex-ios-exec-recheck: {error}")
    if updated != original:
        target.write_text(updated)
    print("FEX ARM64EC: Turing Complete executable-view recheck (opt-in only)")

#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Host source checks for the opt-in Turing Complete executable-view recheck.

Requires the FEX submodule; runs without a phone and never changes the checkout.
A successful host check is not evidence of an on-device fix.
"""
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
patcher_path = ROOT / "tools" / "patch-fex-ios-exec-recheck.py"
source_path = ROOT / "FEX" / "Source" / "Windows" / "Common" / "InvalidationTracker.cpp"

spec = importlib.util.spec_from_file_location("tc_recheck", patcher_path)
patcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(patcher)

before = source_path.read_text()
after = patcher.patch(before)
assert after != before, "the recheck overlay was not applied"
assert patcher.patch(after) == after, "the overlay is not idempotent"
assert after.count(patcher.MARKER) == 1
assert 'std::getenv("MADEIRA_FEX_EXEC_RECHECK")' in after
assert "Mem.State == MEM_COMMIT && Mem.Type == MEM_MAPPED" in after
assert "!(Mem.Protect & (PAGE_GUARD | PAGE_NOACCESS))" in after
assert "ProtHasExec(Mem.Protect)" in after
assert "Address & FEXCore::Utils::FEX_PAGE_MASK" in after
assert "Page + FEXCore::Utils::FEX_PAGE_SIZE" in after
assert "std::unique_lock Lock(IntervalsLock)" in after
assert "RWXIntervals.Insert(Span)" in after
assert "[tc-exec-recheck]" in after
assert after.replace(patcher.REPLACEMENT, patcher.ANCHOR, 1) == before, "changed code outside the intended anchor"

# Reject an unexpected upstream change rather than patching an unknown routine.
try:
    patcher.patch(before.replace(patcher.ANCHOR, "unexpected source", 1))
except ValueError:
    pass
else:
    raise AssertionError("patch accepted an unknown FEX source")

print("PASS: opt-in ARM64EC mapped-executable recheck; host source guard")

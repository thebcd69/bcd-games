#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""A 32-bit DXMT game's [gpu-perf] line, with MADEIRA_FRAME_STATS (round 624, agent D2 S16; Far Cry 2, Send Log Z7CADQ).

DXMT's [gpu-perf] summary (ml1254: GPU busy share, readback Map waits, not-ready polls, draws a frame, ...) is printed by
CommandQueue::AccountGpuTime (dxmt/src/dxmt/dxmt_command_queue.cpp) only after MadeiraCtl op 3 returned a completed command
buffer's GPUStartTime/GPUEndTime. For a 32-bit caller the call arrives through _madeira_ctl_wow64, which forwards op 7 alone and
answers STATUS_NOT_IMPLEMENTED for op 3 ("the other ops carry guest pointers in ptr"), so no 32-bit game has ever printed the line
(0 lines in Z7CADQ, PA7PQB, EHSWQZ, 3XQYKD, 6DDH3K, B1MR64; 27-109 in 64-bit Counter-Strike: Source logs).

With MADEIRA_FRAME_STATS on (madeira_frame_hooks_on(), the [frame] instrument's own switch) op 3 is forwarded with its guest
pointer converted by UInt32ToPtr, as every other wow64 thunk of this file does: { uint64 cb; double start, end; } has the same
24 bytes and offsets on i386. Without the switch nothing changes (STATUS_NOT_IMPLEMENTED, as before) for any game.

Native only (the winemetal unix side), not in the i386 farm key. Idempotent (marker "ctl-gpu-time-wow64-v1"); exits 1 naming the
anchor when it moved, the file unchanged. Run from the repository root.
"""
import pathlib
import sys

PATH = pathlib.Path("dxmt/src/winemetal/unix/winemetal_unix.c")
MARKER = "ctl-gpu-time-wow64-v1"

OLD = """static NTSTATUS _madeira_ctl_wow64(void *args) {
  struct madeira_ctl_args *a = args;
  if (a && a->op == 7) return _madeira_ctl(args);
  return STATUS_NOT_IMPLEMENTED;
}
"""

NEW = """static NTSTATUS _madeira_ctl_wow64(void *args) {
  struct madeira_ctl_args *a = args;
  if (a && a->op == 7) return _madeira_ctl(args);
  /* madeira-bcd ctl-gpu-time-wow64-v1 (round 624, Far Cry 2 Z7CADQ; tools/patch-winemetal-ctl-gpu-time-wow64.py): op 3 for a
   * 32-bit caller while the [frame] instrument is on (MADEIRA_FRAME_STATS), its guest pointer converted; then DXMT's [gpu-perf]
   * line appears for 32-bit games too. Without the switch: not implemented, as before. */
  if (a && a->op == 3 && madeira_frame_hooks_on()) {
    struct madeira_ctl_args host = *a;
    host.ptr = (uint64_t)(uintptr_t)UInt32ToPtr((uint32_t)a->ptr);
    NTSTATUS status = _madeira_ctl(&host);
    a->ret = host.ret;
    return status;
  }
  return STATUS_NOT_IMPLEMENTED;
}
"""


def main():
    s = PATH.read_text()
    if MARKER in s:
        print("winemetal_unix.c: ctl-gpu-time-wow64 already patched")
        return 0
    if s.count(OLD) != 1:
        print(f"::error::patch-winemetal-ctl-gpu-time-wow64: _madeira_ctl_wow64 found {s.count(OLD)} times (want 1) in {PATH}")
        return 1
    if "UInt32ToPtr(uint32_t v)" not in s or s.index("UInt32ToPtr(uint32_t v)") > s.index(OLD) \
            or "madeira_frame_hooks_on(void)" not in s:
        print(f"::error::patch-winemetal-ctl-gpu-time-wow64: UInt32ToPtr or madeira_frame_hooks_on is not defined before _madeira_ctl_wow64 in {PATH}")
        return 1
    PATH.write_text(s.replace(OLD, NEW, 1))
    print("winemetal_unix.c: ctl-gpu-time-wow64 patched (op 3 for 32-bit callers with MADEIRA_FRAME_STATS)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

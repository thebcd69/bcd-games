#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""D3D9 dynamic buffer refresh with no room for a new generation (DXMT); no Wine, Metal or guest runs.

GTA IV (log JA13M8, build 578, iPad16,5): the 32-bit process's address space was full (largest free
range 1.4 MB), a dynamic vertex or index buffer's refresh could not mint a 1.5 MB generation, and the
draw read the previous one ("d3d9: out of video memory refreshing a dynamic buffer; the draw reads
stale contents"): the main menu showed broken geometry. Now the refresh first waits for the GPU
(MTLD3D9Device::drainForDynamicRename) and tries again; with nothing to recycle the store goes into the
current generation, which no unfinished draw reads after the wait. These are source invariants: the wait
runs only after a failed rename, DXMT_D9_RENAME_WAIT=0 keeps the old behaviour, and the drain has the
shape of the readback drains (submit, CPU fence, GPU wait, rings trimmed). Needs the dxmt submodule.
"""
from pathlib import Path
import re
import sys

root = Path(__file__).resolve().parents[2]
d9 = root / "dxmt/src/d3d9"
if not (d9 / "d3d9_buffer.cpp").is_file():
    print("SKIP: no dxmt checkout (run in the full tree)")
    sys.exit(0)
buf = (d9 / "d3d9_buffer.cpp").read_text()
dev = (d9 / "d3d9_device.cpp").read_text()
hdr = (d9 / "d3d9_device.hpp").read_text()
failures = 0


def check(ok, label):
    global failures
    print(("PASS: " if ok else "FAIL: ") + label)
    failures += 0 if ok else 1


def body(text, signature):
    start = text.index(signature)
    open_at = text.index("{", start)
    depth = 0
    for i in range(open_at, len(text)):
        depth += {"{": 1, "}": -1}.get(text[i], 0)
        if depth == 0:
            return text[open_at:i + 1]
    raise ValueError(signature)


for cls in ("MTLD3D9VertexBuffer", "MTLD3D9IndexBuffer"):
    b = body(buf, cls + "::refreshWholeMirror() {")
    first = b.index("renameDynamicBuffer(")
    drain = b.find("m_device->drainForDynamicRename()")
    retry = b.find("renameDynamicBuffer(", first + 1)
    in_place = b.find("m_device->noteRenameInPlace()")
    stale = b.find("the draw reads stale contents")
    store = b.find("updateContents(0, m_hostPtr, m_size")
    check(first < drain < retry < in_place < store,
          f"{cls}: a failed rename waits for the GPU, renames again, else stores in place, then the store runs")
    else_at = b.rfind("} else {", 0, stale)
    after = b[first:]
    check(drain < else_at < stale and after.count("return;") == 1 and after.index("return;") > stale - first,
          f"{cls}: the stale draw is left only when the wait is refused (DXMT_D9_RENAME_WAIT=0 or a dead device)")
    check(b.count("m_device->drainForDynamicRename()") == 1, f"{cls}: one wait per refresh")

# madeira-bcd (round 624): in CI this runs after tools/patch-dxmt-d9-ib-range.py, which adds the index buffer's range refresh
# (env DXMT_D9_IB_RANGE=1): a third failed-rename path with the same wait, retry, in-place store and stale-draw fallback.
ranged = "MTLD3D9IndexBuffer::refreshRange(uint32_t lo, uint32_t hi) {" in buf
if ranged:
    r = body(buf, "MTLD3D9IndexBuffer::refreshRange(uint32_t lo, uint32_t hi) {")
    first = r.index("renameDynamicBuffer(")
    order = [first, r.find("m_device->drainForDynamicRename()"), r.find("renameDynamicBuffer(", first + 1),
             r.find("m_device->noteRenameInPlace()"), r.find("the draw reads stale contents"), r.find("updateContents(")]
    check(all(x >= 0 for x in order) and order == sorted(order) and r.count("m_device->drainForDynamicRename()") == 1,
          "MTLD3D9IndexBuffer::refreshRange (DXMT_D9_IB_RANGE): the same wait, retry and in-place store before its range store")
callers = re.findall(r"drainForDynamicRename\(\)", buf + dev)
n_paths = 3 if ranged else 2
check(len(re.findall(r"m_device->drainForDynamicRename\(\)", buf)) == n_paths and len(callers) == n_paths + 1,
      "the wait is called only from the failed-rename paths (the two whole refreshes, the range refresh when patched) and defined once")

d = body(dev, "MTLD3D9Device::drainForDynamicRename() {")
check('env::getEnvVar("DXMT_D9_RENAME_WAIT") != "0"' in d and "if (!enabled)\n    return false;" in d,
      "DXMT_D9_RENAME_WAIT=0 turns it off; unset is on")
order = [d.find(s) for s in ("FlushDrawBatch();", "flushOpenWork();", "emitCmdbufTailSignal();",
                             "const uint64_t signal = m_currentCmdSeq - 1;", "CurrentSeqId()",
                             "commitCurrentChunkTimed(4);", "WaitCPUFence(seq);", "waitForGpuOrDeviceError(signal)",
                             "m_constRing.free_blocks(signalled);", "m_uploadRing.free_blocks(signalled);",
                             "m_constRingResolve.free_blocks(signalled);")]
check(all(x >= 0 for x in order) and order == sorted(order),
      "the drain submits, waits for the CPU fence and the GPU, then trims every ring against the new progress")
check("if (!waitForGpuOrDeviceError(signal))\n    return false;" in d,
      "an abandoned wait (device error) falls back to the old behaviour")
check("bool drainForDynamicRename();" in hdr and "void noteRenameInPlace();" in hdr, "declared on the device")
check("[d9-rename-wait]" in d, "the log says how often it waited and how long")

if failures:
    print(f"check-d9-rename-wait: {failures} FAILED")
    sys.exit(1)
print("check-d9-rename-wait: all passed")

#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""UpdateSubresource(1) on a STAGING buffer: drop the call instead of crashing the encode thread (night round 2026-11-11, G4 S12).

FIFA 23 (FIFA23.exe, 64-bit; Send Logs HPM5D7 and KAXT96, build 626, one session) died 22 s in on DXMT's encode thread:
"AV READ of 8" at d3d11.dll+0x98818, `ldr x19,[x8,#8]` with x8 = 0, inside the lambda of the buffer branch of
UpdateSubresource1. The lambda captures `dst = bindable->buffer()`; for a D3D11_USAGE_STAGING buffer that is an empty Rc
(StagingBuffer::buffer() returns {}, d3d11_resource_staging.cpp), and `enc.access(dst, ...)` dereferences it. Direct3D 11
ignores an update of a resource the GPU cannot reach (Wine's wined3d: "Resource %p is not GPU accessible", dropped); DXMT
queued a command that died later on another thread. Every other buffer class (D3D11Buffer) has a non-empty buffer().

This patch takes the buffer before the staging allocation and the blit encoder switch and returns when it is empty, with one
log line per call ([update-subresource-staging] in the log: the first 8 calls, then powers of two). Only a call that crashed the
process changes: with a Metal buffer behind the destination the encoded command is the same as before. The texture side needs
nothing (UpdateTexture already ends a staging destination in UNIMPLEMENTED, it does not dereference). Idempotent; fails by name
if an anchor moved. Run from the repository root (the CI workflow does; tools/build-d3d11-dll.sh runs it on its copy).
"""
import pathlib
import sys

ROOT = pathlib.Path("dxmt/src")
MARKER = "madeira-bcd: UpdateSubresource on a staging buffer"
REL = "d3d11/d3d11_context_impl.cpp"

OLD = """        auto [staging_buffer, offset] = AllocateStagingBuffer(copy_len, 16);
        staging_buffer.updateContents(offset, pSrcData, copy_len);
        SwitchToBlitEncoder(CommandBufferState::UpdateBlitEncoderActive);
        EmitOP([staging_buffer, offset, dst = bindable->buffer(), copy_offset, copy_len](ArgumentEncodingContext &enc) {
"""
NEW = """        /* madeira-bcd: UpdateSubresource on a staging buffer (night round 2026-11-11, FIFA 23: HPM5D7, KAXT96). Direct3D 11 drops an
         * update of a resource the GPU cannot reach (Wine's wined3d: "Resource %p is not GPU accessible"). StagingBuffer::buffer() is an
         * empty Rc (d3d11_resource_staging.cpp); the encoder lambda below dereferenced it on the encode thread (d3d11.dll+0x98818,
         * `ldr x19,[x8,#8]` with x8 = 0) and the process died with c0000005. A call with a Metal buffer behind it is unchanged. */
        auto dst_rc = bindable->buffer();
        if (!dst_rc) {
          static std::atomic<unsigned> skipped{0};
          unsigned n = ++skipped;
          if (n <= 8 || !(n & (n - 1)))
            ERR("[update-subresource-staging] skipped #", n, ": the destination buffer has no Metal buffer (a STAGING buffer)");
          return;
        }
        auto [staging_buffer, offset] = AllocateStagingBuffer(copy_len, 16);
        staging_buffer.updateContents(offset, pSrcData, copy_len);
        SwitchToBlitEncoder(CommandBufferState::UpdateBlitEncoderActive);
        EmitOP([staging_buffer, offset, dst = std::move(dst_rc), copy_offset, copy_len](ArgumentEncodingContext &enc) {
"""


def main():
    path = ROOT / REL
    if not path.exists():
        sys.exit(f"patch-dxmt-update-subresource-staging: {path} not found (run from the repository root)")
    s = path.read_text()
    if MARKER in s:
        print(f"{REL}: already patched")
        return
    if s.count(OLD) != 1:
        sys.exit(f"patch-dxmt-update-subresource-staging: anchor not found once in {REL} (UpdateSubresource1's buffer branch moved)")
    s = s.replace(OLD, NEW, 1)
    if "#include <atomic>" not in s:
        first = s.index("#include")
        s = s[:first] + "#include <atomic>\n" + s[first:]
    path.write_text(s)
    print(f"{REL}: patched")


main()

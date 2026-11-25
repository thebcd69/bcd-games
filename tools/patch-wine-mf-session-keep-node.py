#!/usr/bin/env python3
# SPDX-License-Identifier: LGPL-2.1-or-later
# Portions copyright 2026 wenze-allen: the change to dlls/mf/session.c, taken from https://github.com/willfaust/wine (pull request #78, commit
# 18689a0897) under LGPL-2.1-or-later and modified for BCD in November 2026 (written as a build-time script). See THIRD-PARTY-NOTICES.md.
# Copyright 2026 thebcd69 (BCD): the script and its check; BCD's section 7 terms (LICENSE-BCD.md) apply to those, not to the Wine lines it writes.
"""A media session keeps an output node whose video sample allocator cannot be initialized (Wine pull request #78 by wenze-allen).

Media Foundation's session (dlls/mf/session.c, session_append_node) asks an output node's renderer for a video sample allocator and
initializes it with the stream's media type. When that fails (the EVR's allocator, when the device cannot create surfaces of the type's
format: NV12 on a Direct3D 9 device that has no YUV surface format), Wine kept the HRESULT of the failed call and failed the whole node.
session_collect_nodes() then stopped at that node, and the session ran without the renderer's node and every node after it: it still sent
MESessionStarted, but no sample was ever requested and the movie never played or ended (the pull request's case: an EVR on DXMT).

With this the node stays: the allocator is released and the node has none, the upstream node delivers system memory samples (every use of
the allocator in session.c is guarded: the node's release, AllocateSample and the re-initialization at a media type change), and the
failure is an ERR line instead of a WARN. A node whose allocator initializes is handled exactly as before (the callback is set as before).

WHERE THIS REACHES: the 32-bit mf.dll through the i386 farm and, through tools/build-wine-fresh-dlls.sh, the 64-bit one of the rebuilt set
(env.MADEIRA_WIN64_FRESH = 1); the committed 64-bit mf.dll is a frozen binary.
Idempotent (marker "madeira-bcd: mf-session-keep-node-v1"); an anchor that moved is refused by name and the file is left alone.
Usage: patch-wine-mf-session-keep-node.py WINE-DIR | path/to/wine/dlls/mf/session.c
"""
from pathlib import Path
import sys

FILE = "dlls/mf/session.c"
MARKER = "madeira-bcd: mf-session-keep-node-v1"

OLD = """                        if (FAILED(hr = IMFVideoSampleAllocator_InitializeSampleAllocator(topo_node->u.sink.allocator,
                                2, media_type)))
                        {
                            WARN("Failed to initialize sample allocator for the stream, hr %#lx.\\n", hr);
                        }
                        IMFVideoSampleAllocator_QueryInterface(topo_node->u.sink.allocator,
                                &IID_IMFVideoSampleAllocatorCallback, (void **)&topo_node->u.sink.allocator_cb);
                        IMFVideoSampleAllocatorCallback_SetCallback(topo_node->u.sink.allocator_cb,
                                &topo_node->u.sink.notify_cb);
"""
NEW = """                        if (FAILED(hr = IMFVideoSampleAllocator_InitializeSampleAllocator(topo_node->u.sink.allocator,
                                2, media_type)))
                        {
                            /* """ + MARKER + """ (tools/patch-wine-mf-session-keep-node.py; Wine pull request #78 by
                             * wenze-allen). Keep the node and give the upstream node system memory samples instead.
                             * Failing here dropped this node and every node after it, and the session then started
                             * without ever requesting a sample. */
                            ERR("Failed to initialize sample allocator for the stream, hr %#lx.\\n", hr);
                            IMFVideoSampleAllocator_Release(topo_node->u.sink.allocator);
                            topo_node->u.sink.allocator = NULL;
                            hr = S_OK;
                        }
                        else
                        {
                            IMFVideoSampleAllocator_QueryInterface(topo_node->u.sink.allocator,
                                    &IID_IMFVideoSampleAllocatorCallback, (void **)&topo_node->u.sink.allocator_cb);
                            IMFVideoSampleAllocatorCallback_SetCallback(topo_node->u.sink.allocator_cb,
                                    &topo_node->u.sink.notify_cb);
                        }
"""


def main():
    arg = Path(sys.argv[1] if len(sys.argv) > 1 else "wine")
    path = arg if arg.suffix == ".c" else arg / FILE
    text = path.read_text()
    if MARKER in text:
        print("mf session keep node: already patched")
        return
    if text.count(OLD) != 1:
        sys.exit("patch-wine-mf-session-keep-node: the allocator initialization of session_append_node is not where it was in %s "
                 "(anchor not found exactly once); nothing written" % path)
    path.write_text(text.replace(OLD, NEW))
    print("mf session keep node: patched")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""The app's frame-rate limit holds a Windows Vulkan game's presents (2026-11-22, build 703).

BCD's limit (the in-game menu's 60 / 40 / 30 FPS, the vsync mode madeira_get_vsync_locked() reports) is applied in winemetal's
present, so it holds DirectX games only. A Windows Vulkan game presents through Wine's win32u Vulkan layer to MoltenVK or
KosmicKrisp and was never held: X-Plane 12 drew its main menu at the screen's 120 with the limit at 60 (owner, build 702).

This patch makes win32u_vkQueuePresentKHR call madeira_vk_fps_cap_pace() (build/win32u-unix/vulkan_ios.c, the file that compiles
Wine's vulkan.c) right before the host's present: with the limit on, the presenting thread waits for the limit's next deadline;
with no limit (vsync mode 0 or 2) it returns at once. A game slower than the limit never waits. MADEIRA_VK_FPS_CAP=0 turns it off.

Applied to the checked-out wine tree at build time like the other win32u Vulkan changes, so the Wine pin (and the caches keyed on
it) does not change. Its anchor is the host's present line, which it keeps and inserts before; tools/patch-wine-vulkan-frame-ring.py
inserts after the same line, and the two other scripts that change win32u/vulkan.c do not touch it, so the order does not matter.
Idempotent (a second run only says so); fails by name when the anchor moved. Run from the repository root before
build/win32u-unix/build.sh.
"""
import pathlib
import sys

PATH = pathlib.Path("wine/dlls/win32u/vulkan.c")
MARKER = "madeira-bcd fps cap (Vulkan)"

OLD = """    res = device->p_vkQueuePresentKHR( queue->host.queue, present_info );
"""
NEW = """    madeira_vk_fps_cap_pace();   /* madeira-bcd fps cap (Vulkan) (tools/patch-wine-vulkan-fps-cap.py): the app's frame-rate limit */
    res = device->p_vkQueuePresentKHR( queue->host.queue, present_info );
"""


def patch_text(s):
    """The patched text, or None when it is patched already. Raises ValueError when the anchor is not there once."""
    if MARKER in s:
        return None
    if s.count(OLD) != 1:
        raise ValueError(f"anchor found {s.count(OLD)} times (want 1):\n{OLD}")
    return s.replace(OLD, NEW)


def main():
    path = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else PATH
    try:
        out = patch_text(path.read_text())
    except ValueError as e:
        sys.exit(f"patch-wine-vulkan-fps-cap: {path}: {e}")
    if out is None:
        print(f"{path.name}: Vulkan frame-rate limit already patched")
        return 0
    path.write_text(out)
    print(f"{path.name}: Vulkan frame-rate limit patched")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""A Windows Vulkan game's presents go into the frame-time ring, for the in-game HUD and the session summary (2026-11-22, build 702).

bcd_frame_ring_push() (app/Madeira/BCDFrameRing.c) is what the HUD's FPS, its frame-time graph and the [session] line read. DXMT's
winemetal calls it once per Present (tools/patch-winemetal-frame-ring.py) and the winios OpenGL presenter once per SwapBuffers
(app/Madeira/Winios/WiniosGL.m). A Windows Vulkan game presents through neither: its vkQueuePresentKHR goes through Wine's win32u
Vulkan layer to the host library (MoltenVK or KosmicKrisp). So every Vulkan game showed fps=0 (X-Plane 12 on build 701: "başından
beri bu oyunda FPS sayacımız çalışmıyor") and its [session] line said frames=0.

This patch makes win32u_vkQueuePresentKHR call bcd_frame_ring_push() once per present call that the host accepted (VK_SUCCESS or
VK_SUBOPTIMAL_KHR; not per swapchain, not for an error). The call is a weak import, so a link without the app's ring still links and
the call is skipped. It reads the clock and does two relaxed atomics, without a lock, an allocation or a log line. While the in-game
menu has paused the game the push waits in BCDPauseGate, as a DXMT or OpenGL present does, so Pause now holds a Vulkan game too.

Why here and not in madeira_vulkan_present_note() (app/Madeira/Winios/WiniosGL.m, called by build/win32u-unix/vulkan_surface_ios.c's
present callback): win32u calls that callback from client_surface_present() with its surfaces_lock held, and the Pause gate must not wait
there (docs/VULKAN.md). This block runs right after the host's present returned and before that loop: win32u_vkQueuePresentKHR takes no
lock of its own. The note stays as it is: it already adds Vulkan presents to madeira_get_present_count() (the launch checks that wait for
a first frame, the Dock's starting screen, the engine's census), which this patch does not change.

Applied to the checked-out wine tree at build time, like tools/patch-wine-vulkan-suboptimal.py, so the Wine pin (and the caches keyed on
it) does not change. Its anchor is a line neither of the other two scripts that change win32u/vulkan.c touches, so the order does not
matter. Idempotent (a second run only says so); fails by name when the anchor moved. Run from the repository root before
build/win32u-unix/build.sh.
"""
import pathlib
import sys

PATH = pathlib.Path("wine/dlls/win32u/vulkan.c")
MARKER = "madeira-bcd frame ring (Vulkan)"

OLD = """    res = device->p_vkQueuePresentKHR( queue->host.queue, present_info );
"""
NEW = """    res = device->p_vkQueuePresentKHR( queue->host.queue, present_info );
    if (res >= VK_SUCCESS)
    {   /* madeira-bcd frame ring (Vulkan) (tools/patch-wine-vulkan-frame-ring.py): when this present happened, for the HUD and the session summary */
        extern void bcd_frame_ring_push(void) __attribute__((weak_import));
        if (bcd_frame_ring_push)
            bcd_frame_ring_push();
    }
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
        sys.exit(f"patch-wine-vulkan-frame-ring: {path}: {e}")
    if out is None:
        print(f"{path.name}: Vulkan frame ring already patched")
        return 0
    path.write_text(out)
    print(f"{path.name}: Vulkan frame ring patched")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
# Portions copyright 2026 125hz (signed off by intraducine): the C change it writes is Madeira's Wine commit df1c5ea5
# (https://github.com/willfaust/Madeira, pull request #353), LGPL-2.1-or-later like Wine. See THIRD-PARTY-NOTICES.md.
"""Wine's win32u Vulkan layer reports a host VK_SUBOPTIMAL_KHR as success when the swapchain matches its client rect.

This is the change of Madeira's Wine commit df1c5ea5 ("win32u: report a host VK_SUBOPTIMAL_KHR as success when the swapchain matches its client rect
(ml1390)"; author 125hz, signed off by intraducine, upstream Madeira #353/#462; LGPL-2.1-or-later like Wine), applied to the checked-out wine tree at build time
instead of as a commit of the Wine fork, so the Wine pin (and the caches keyed on it) does not change. An iOS CAMetalLayer measures its bounds in points, so
MoltenVK calls every present suboptimal although the swapchain has the window's client-rect size; an application that recreates the swapchain on
VK_SUBOPTIMAL_KHR then does so on every frame. With the change such a result is VK_SUCCESS when the swapchain extent equals the client rect, and a real resize is
still reported. MADEIRA_VK_HOST_SUBOPTIMAL=1 passes the host result through again. The code runs only in a session that loaded a Vulkan library.

Idempotent (a second run only says so); fails by name when an anchor moved. Run from the repository root before build/win32u-unix/build.sh.
"""
import pathlib
import sys

PATH = pathlib.Path("wine/dlls/win32u/vulkan.c")
MARKER = "ignore_host_suboptimal"

EDITS = [
    ('''#include <dlfcn.h>
#include <pthread.h>
#include <unistd.h>
''', '''#include <dlfcn.h>
#include <pthread.h>
#include <stdlib.h>
#include <unistd.h>
'''),
    ('''static VkResult win32u_vkQueuePresentKHR( VkQueue client_queue, const VkPresentInfoKHR *client_present_info )
{
''', '''/* The host surface can measure its size in other units than the client rect (an iOS CAMetalLayer
 * reports points), so the host may call a swapchain suboptimal although it matches the window's
 * client rect. The application cannot fix that: a new swapchain gets the same size and the same
 * result, and recreating it on every frame costs the whole frame. When the swapchain matches the
 * client rect, such a host VK_SUBOPTIMAL_KHR is reported as VK_SUCCESS. The client rect check
 * below still reports real resizes. MADEIRA_VK_HOST_SUBOPTIMAL=1 passes it through again. */
static BOOL ignore_host_suboptimal(void)
{
    static int ignore = -1;
    if (ignore < 0)
    {
        const char *env = getenv( "MADEIRA_VK_HOST_SUBOPTIMAL" );
        ignore = !(env && *env == '1');
        ERR( "[winios-vk] ml1390 host VK_SUBOPTIMAL_KHR for a swapchain that matches its client rect is %s"
             " (MADEIRA_VK_HOST_SUBOPTIMAL=1 passes it through)\\n", ignore ? "reported as VK_SUCCESS" : "passed through" );
    }
    return ignore;
}

static VkResult win32u_vkQueuePresentKHR( VkQueue client_queue, const VkPresentInfoKHR *client_present_info )
{
'''),
    ('''    const VkSwapchainKHR *client_swapchains;
    VkResult res;

    TRACE( "queue %p, present_info %p\\n", queue, present_info );
''', '''    const VkSwapchainKHR *client_swapchains;
    BOOL host_suboptimal_ignored = FALSE, suboptimal_kept = FALSE;
    VkResult res;

    TRACE( "queue %p, present_info %p\\n", queue, present_info );
'''),
    ('''        else if (swapchain_res)
            WARN( "Present returned status %d for swapchain %p\\n", swapchain_res, swapchain );
        else if (!extents_equals( &swapchain->extents, &client_rect ))
        {
            WARN( "Swapchain size %dx%d does not match client rect %s, returning VK_SUBOPTIMAL_KHR\\n",
                  swapchain->extents.width, swapchain->extents.height, wine_dbgstr_rect( &client_rect ) );
            if (present_info->pResults) present_info->pResults[i] = VK_SUBOPTIMAL_KHR;
            if (!res) res = VK_SUBOPTIMAL_KHR;
        }
    }

    if (swapchains != swapchains_buffer) free( swapchains );
''', '''        else if (swapchain_res == VK_SUBOPTIMAL_KHR && extents_equals( &swapchain->extents, &client_rect ) &&
                 ignore_host_suboptimal())
        {
            if (present_info->pResults) present_info->pResults[i] = VK_SUCCESS;
            host_suboptimal_ignored = TRUE;
        }
        else if (swapchain_res)
        {
            WARN( "Present returned status %d for swapchain %p\\n", swapchain_res, swapchain );
            if (swapchain_res == VK_SUBOPTIMAL_KHR) suboptimal_kept = TRUE;
        }
        else if (!extents_equals( &swapchain->extents, &client_rect ))
        {
            WARN( "Swapchain size %dx%d does not match client rect %s, returning VK_SUBOPTIMAL_KHR\\n",
                  swapchain->extents.width, swapchain->extents.height, wine_dbgstr_rect( &client_rect ) );
            if (present_info->pResults) present_info->pResults[i] = VK_SUBOPTIMAL_KHR;
            if (!res) res = VK_SUBOPTIMAL_KHR;
            suboptimal_kept = TRUE;
        }
    }
    if (res == VK_SUBOPTIMAL_KHR && host_suboptimal_ignored && !suboptimal_kept) res = VK_SUCCESS;

    if (swapchains != swapchains_buffer) free( swapchains );
'''),
]


def main():
    s = PATH.read_text()
    if MARKER in s:
        print("win32u/vulkan.c: the host VK_SUBOPTIMAL_KHR change is already there")
        return 0
    for n, (old, new) in enumerate(EDITS, 1):
        if s.count(old) != 1:
            sys.exit(f"patch-wine-vulkan-suboptimal: edit {n} anchor found {s.count(old)} times (want 1) in {PATH}:\n{old}")
        s = s.replace(old, new)
    PATH.write_text(s)
    print("win32u/vulkan.c: a host VK_SUBOPTIMAL_KHR that matches the client rect is reported as success")
    return 0


if __name__ == "__main__":
    sys.exit(main())

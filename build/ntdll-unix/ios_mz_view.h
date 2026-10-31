/* Does the Wine view that holds the nearest MZ page reach an executable request? (round 624)
 *
 * SPDX-License-Identifier: GPL-3.0-only
 * Copyright 2026 thebcd69 (BCD)
 * BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
 *
 * WHY THIS EXISTS
 * ---------------
 * mprotect_exec() (virtual_ios.c) attributes an executable request to the nearest MZ/PE page below it when the request lies
 * below that header's image_base + SizeOfImage. Mono (Unity games: R.E.P.O., Raft, Risk of Rain 2, Mycopunk, Skate Story,
 * Kindergarten, ...) maps its .NET assemblies as PLAIN FILES: the Wine view ends where the file ends, while the PE header's
 * SizeOfImage reaches further. R.E.P.O. AJ7RNE (build 623): the view of an assembly is 0x7059480000+0x20000, its header says
 * SizeOfImage 0x24000, and Mono's next 64 KB RWX code chunk starts right behind the file at 0x70594a0000. The header alone
 * made the chunk's first 16 KB the "image's tail": copied into the pool as image, no anonymous alias, and every store Mono
 * made into it faulted ("[store-noalias] #1 ... NO pool/anon alias"), after which MonoBackpatcherWrite left FEX's
 * CodeInvalidationMutex held and the game never drew a frame.
 *
 * A real SEC_IMAGE view spans [image_base, image_base + SizeOfImage), so for a genuine image the request always lies inside
 * the view and nothing changes. With MADEIRA_MZ_VIEW_CHECK=1 a request outside the view of its nearest MZ is handled as
 * anonymous RWX, like every other code chunk of the same runtime.
 */
#ifndef IOS_MZ_VIEW_H
#define IOS_MZ_VIEW_H

#include <stdint.h>

/* 1 when `base` lies inside the view [view_base, view_base + view_size). */
static inline int ios_mz_view_reaches( uintptr_t view_base, uintptr_t view_size, uintptr_t base )
{
    return base >= view_base && base - view_base < view_size;
}

#endif /* IOS_MZ_VIEW_H */

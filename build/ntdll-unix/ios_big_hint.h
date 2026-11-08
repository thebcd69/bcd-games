/* Reservations this address space can never hold, answered with their hint (round 634; env.MADEIRA_BIG_HINT_EXACT, default off)
 *
 * SPDX-License-Identifier: GPL-3.0-only
 * Copyright 2026 thebcd69 (BCD)
 * BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
 *
 * WHY THIS EXISTS
 * ---------------
 * The Last of Us Part I (tlou-i.exe, builds 576 to 633; Send Logs 24TC8Y, W5M7YN, S0M36P, 8GRXB9 and four starts of build 633) asks
 * Windows sixteen times for a 512 GB MEM_RESERVE at the hints 0x1000000000 + n * 0x8000000000 and, when it has no answer it accepts,
 * stops itself: "HALT: Coudln't find any available virtual memory start addresses after 16 tries" (Memory::FindAvailableVirtualMemory-
 * StartAddress, corelib/memory/memory-area.cpp:65, then int3). Windows answers a hinted reserve with exactly that address or fails;
 * BCD answers with a soft grant at another base (virtual_ios.c, ios_soft) or c0000018, and the game takes neither: it never freed the
 * soft grant of [jumbo#1] and went on to hint 2.
 *
 * No such block can exist here: every one of the sixteen ranges ends above the map (0x8000000000), the first starts exactly at the floor
 * of the GPU carveout [64 GB, 448 GB) that the CPU cannot touch ([window] run#0 0xfc0000000+394 GB, maxprot 0), and fixed mappings below
 * it are refused too ([va-profile] kr=3 at 32 to 63 GB). This experiment answers with the hint itself, backed by
 * nothing, to learn two things in one device run: whether the game accepts an exact answer, and what it asks for next (every commit,
 * query and release inside the block is logged; a commit is refused with STATUS_NO_MEMORY, because no memory can be put at those
 * addresses). It is not a fix: the game can only go on if it never needs real memory inside the block.
 *
 * The first block overlaps the top of the map on paper ([0x7000000000, 0x8000000000) is where BCD's own memory lives), so a range is
 * only answered at addresses nothing real can be at (ios_big_hint_virtual), and only for the process that reserved it.
 *
 * The table logic lives here, free of Wine types, so tests/host/check-big-hint.py compiles and runs it on the host.
 */
#ifndef IOS_BIG_HINT_H
#define IOS_BIG_HINT_H

#include <stdint.h>

#define IOS_BIG_HINT_MAX      8
#define IOS_BIG_HINT_MIN_SIZE 0x4000000000ULL   /* 256 GB */
#define IOS_BIG_HINT_MIN_HINT 0x1000000000ULL   /* 64 GB: the floor of the GPU carveout; nothing below it is answered */

struct ios_big_hint
{
    uint64_t base, size;
    uint32_t prot;      /* the protection the program asked for, reported back by a query */
    uintptr_t owner;    /* the pseudo-process (its PEB) that reserved it: no other process sees the range */
};

/* The virtual part of an answered range: the addresses nothing real can be at. The part of the map BCD works in, [real_lo, space_limit)
 * (the JIT pool's alias and everything above it: the FEX arena, the guest's views, soft pools), is real memory the range only overlaps on
 * paper; commits and queries there go the way they always went. */
static inline int ios_big_hint_virtual( uint64_t addr, uint64_t space_limit, uint64_t real_lo )
{
    return addr < real_lo || addr >= space_limit;
}

/* 1 when a MEM_RESERVE of `size` bytes at `hint` is one this address space can never hold: at least 256 GB, an explicit hint on the 64 KB
 * allocation granularity and not below 64 GB, a range that ends above `space_limit` (a request that fits is left to the placement code),
 * and a hint that is itself in the virtual part (not in [real_lo, space_limit), where the base would be real memory). */
static inline int ios_big_hint_wanted( uint64_t hint, uint64_t size, uint64_t space_limit, uint64_t real_lo )
{
    if (size < IOS_BIG_HINT_MIN_SIZE || hint < IOS_BIG_HINT_MIN_HINT) return 0;
    if ((hint & 0xffff) || (size & 0xfff)) return 0;
    if (hint > UINT64_MAX - size) return 0;   /* the range wraps: not a request a program makes */
    if (hint + size <= space_limit) return 0;
    return ios_big_hint_virtual( hint, space_limit, real_lo );
}

/* 0 when the range was added; -1 when the table is full or the range overlaps one that is in it. */
static inline int ios_big_hint_add( struct ios_big_hint *t, unsigned *n, uint64_t base, uint64_t size, uint32_t prot, uintptr_t owner )
{
    unsigned i;

    if (*n >= IOS_BIG_HINT_MAX || !size || base > UINT64_MAX - size) return -1;
    for (i = 0; i < *n; i++)
        if (base < t[i].base + t[i].size && t[i].base < base + size) return -1;
    t[*n].base = base;
    t[*n].size = size;
    t[*n].prot = prot;
    t[*n].owner = owner;
    (*n)++;
    return 0;
}

/* the index of the range of `owner` whose virtual part holds `addr`, or -1 */
static inline int ios_big_hint_find( const struct ios_big_hint *t, unsigned n, uint64_t addr, uintptr_t owner,
                                     uint64_t space_limit, uint64_t real_lo )
{
    unsigned i;

    if (!ios_big_hint_virtual( addr, space_limit, real_lo )) return -1;
    for (i = 0; i < n; i++)
        if (t[i].owner == owner && addr >= t[i].base && addr - t[i].base < t[i].size) return (int)i;
    return -1;
}

/* A MEM_RELEASE of the exact base of one of `owner`'s ranges forgets it; 1 and the range's size in *size when it did, 0 for any other address. */
static inline int ios_big_hint_forget( struct ios_big_hint *t, unsigned *n, uint64_t base, uintptr_t owner, uint64_t *size )
{
    unsigned i;

    for (i = 0; i < *n; i++)
    {
        if (t[i].base != base || t[i].owner != owner) continue;
        *size = t[i].size;
        for (; i + 1 < *n; i++) t[i] = t[i + 1];
        (*n)--;
        return 1;
    }
    return 0;
}

#endif /* IOS_BIG_HINT_H */

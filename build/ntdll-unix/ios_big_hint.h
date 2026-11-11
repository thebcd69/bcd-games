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
    uint64_t real;      /* build 635 (mode 2): the first `real` bytes are a real reservation of the process (a Wine view), 0 otherwise */
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
    t[*n].real = 0;
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

/* Build 635 (env.MADEIRA_BIG_HINT_EXACT = 2; the owner's build-634 logs of 2026-11-06 19:10, tlou-i-l.exe and its launcher): with the hint
 * granted, the game committed 0x142400000 bytes (5.04 GB) at the block's very start and stopped on its own assertion "expectedAddr ==
 * m_startAddr: Expected 0x0000001000000000, got 0x0000000000000000, error 8" (Memory::Area::AllocateMemory, memory-area-win.cpp:84): it needs
 * REAL memory at the base, and iOS maps nothing at 64 GB on any device. The [jumbo-code] dump of that log shows the reserve loop
 * (Memory::FindAvailableVirtualMemoryStartAddress, tlou-i.exe build 4169145):
 *
 *     xor edi, edi
 *   loop: mov ebx, edi; movabs rdx, 0x8000000000; shl rbx, 0x27; mov r9d, 4; add rbx, [rip + d]  <- the base, a global (0x1000000000)
 *         mov r8d, 0x2000; mov rcx, rbx; call [rip + e] (VirtualAlloc); cmp rax, rbx; jne next   <- the answer must be the hint itself
 *         mov [rip + d'], rax   ... next: inc edi; cmp edi, 0x10; jb loop; jmp HALT
 *
 * The global is read again for every try, so the base can move between two tries: BCD answers try 0 with a refusal after writing
 * B - 0x8000000000 into the global, try 1 then asks for B, a place in BCD's own part of the map where a real reservation already waits.
 * This recognises the loop from the 0x2a bytes before the call's return address and the 4 after it, and returns the global's address. */
#define IOS_BIG_HINT_TLOU_BEFORE 0x2a
#define IOS_BIG_HINT_TLOU_LEN    (IOS_BIG_HINT_TLOU_BEFORE + 4)
static inline int ios_big_hint_tlou_loop( const unsigned char *code, uint64_t ret, uint64_t *global )
{
    static const unsigned char head[] = { 0x48, 0xba, 0x00, 0x00, 0x00, 0x00, 0x80, 0x00, 0x00, 0x00,   /* movabs rdx, 0x8000000000 */
                                          0x48, 0xc1, 0xe3, 0x27,                                       /* shl rbx, 0x27 */
                                          0x41, 0xb9, 0x04, 0x00, 0x00, 0x00,                           /* mov r9d, 4 */
                                          0x48, 0x03, 0x1d };                                           /* add rbx, [rip + d] */
    static const unsigned char mid[] = { 0x41, 0xb8, 0x00, 0x20, 0x00, 0x00,                            /* mov r8d, 0x2000 (MEM_RESERVE) */
                                         0x48, 0x8b, 0xcb,                                              /* mov rcx, rbx */
                                         0xff, 0x15 };                                                  /* call [rip + e] */
    static const unsigned char tail[] = { 0x48, 0x3b, 0xc3, 0x75 };                                     /* cmp rax, rbx; jne */
    unsigned i;
    int32_t d;
    uint64_t start = ret - IOS_BIG_HINT_TLOU_BEFORE;

    if (ret < IOS_BIG_HINT_TLOU_BEFORE) return 0;
    for (i = 0; i < sizeof(head); i++) if (code[i] != head[i]) return 0;
    for (i = 0; i < sizeof(mid); i++) if (code[27 + i] != mid[i]) return 0;
    for (i = 0; i < sizeof(tail); i++) if (code[IOS_BIG_HINT_TLOU_BEFORE + i] != tail[i]) return 0;
    d = (int32_t)((uint32_t)code[23] | (uint32_t)code[24] << 8 | (uint32_t)code[25] << 16 | (uint32_t)code[26] << 24);
    *global = start + 27 + (uint64_t)(int64_t)d;
    return 1;
}

/* The value that makes try 1 of the loop (n = 1) ask for `base`: the loop adds n * 0x8000000000 to the global (two's complement wraps) */
static inline uint64_t ios_big_hint_tlou_seed( uint64_t base )
{
    return base - 0x8000000000ULL;
}

/* Build 644 (the owner's build-643 log of 2026-11-08 12:17, tlou-i-l.exe; the game's code read from its exe, build 4169145): the game's
 * 512 GB area is a pool of 512 slots of 1 GB. Every Memory::Area takes whole slots, the first free run from slot 0 (0x1403b58b8: count =
 * ((size - 1) >> 30) + 1, address = base + (slot << 30); the release at 0x1403df8b0 insists on a 1 GB-aligned address), so a 10 MB heap
 * costs 1 GB of address space. By the first loading screen it held slots 0 to 15 (the 5 GB CPU area alone takes six) and asked for slot 16:
 * with a 16 GB real block that is 0x7c00000000, the first page of the emulator's arena. The commit failed, the game did not check, and its
 * stores rewrote the emulator's allocator (rpmalloc's heap at 0x7c00010000: "[rpm-poison] heap-head ... badascii", then five threads
 * spinning forever on its lock: the coin froze). The slot of an address inside the area, and a 512-bit "seen" set for the log. */
#define IOS_BIG_HINT_TLOU_SLOT_SHIFT 30
#define IOS_BIG_HINT_TLOU_SLOTS      512

/* the slot of `addr` inside the area [base, base + 512 slots of 1 << shift), or -1 */
static inline int ios_big_hint_tlou_slot( uint64_t base, uint64_t addr, unsigned shift )
{
    if (addr < base || addr - base >= ((uint64_t)IOS_BIG_HINT_TLOU_SLOTS << shift)) return -1;
    return (int)((addr - base) >> shift);
}

/* Mode 3 (env.MADEIRA_BIG_HINT_EXACT = 3, an experiment): mode 2, and the game's slots made 64 MB instead of 1 GB, so its whole bitmap of
 * 512 slots is exactly the 32 GB real block and a 10 MB area costs 64 MB of address space. 256 MB slots (the first form of mode 3, the owner's
 * start of 2026-11-08 19:59) were not enough: starting a new game the game opened some 70 small areas and asked for slot 128, the end of a
 * 32 GB block of 256 MB slots ([big-hint] r644 OUT OF ROOM). With 64 MB slots the game's own limit (512 slots; it stops itself with its
 * assertion when no run is free, 0x1403b5911) comes before the end of the real block. Read from tlou-i-l.exe 1.1.5 (build 4169145): every
 * use of the slot size is one of these six instructions (all code that reads the area base 0x143649c40 or the slot bitmap 0x145faaa10):
 *   0x1403b58db  shr rbp, 0x1e                 the allocator's slot count, ((size - 1) >> 30) + 1
 *   0x1403b59c3  shl rbx, 0x1e                 the allocator's address, base + (slot << 30)
 *   0x1403df8c4  test rcx, 0x3fffffff          the release's assertion that the address starts a slot
 *   0x1403df94a  shr rsi, 0x1e                 the release's first slot
 *   0x1403df94e  shr rax, 0x1e                 the release's slot count
 *   0x141e6dfb0  shr rbx, 0x1e                 the "is this address in a used slot" lookup
 * Inside an area the game works in megabytes from its start and asserts only 1 MB alignment (Memory::Area: "IsSizeAligned(addrSize,
 * kAlign1M)", "(addr + sizeMB * ONE_MEGA_BYTE) <= (m_startAddr + m_addrSizeMB * ONE_MEGA_BYTE)"), and the bitmap search walks all eight
 * 64-bit words (a run across words is counted on: an empty word adds 64, 0x1403b5a98; marking, checking and clearing a run handle
 * several words, 0x1411abb38, 0x1411b7fe2, 0x1411b7fa4), so the 81-slot run of the 5 GB CPU area works as its 6-slot run of 1 GB did.
 * The six are patched only together, only when the loop's return
 * address is at its rva of this exe and every byte below matches (any other exe or version: nothing is written). */
#define IOS_BIG_HINT_TLOU_SMALL_SHIFT 26   /* 64 MB */
#define IOS_BIG_HINT_TLOU_LOOP_RET    0x3b3d89u   /* rva of the reserve loop's return address in tlou-i-l.exe 1.1.5 */
struct ios_big_hint_site
{
    uint32_t rva;           /* the instruction */
    uint8_t len;            /* its length */
    uint8_t old[7];         /* its bytes in the exe */
    uint8_t at;             /* the byte that changes */
    uint8_t new_byte;       /* its value for 64 MB slots */
};
static const struct ios_big_hint_site ios_big_hint_tlou_sites[6] =
{
    { 0x3b58db, 4, { 0x48, 0xc1, 0xed, 0x1e }, 3, 0x1a },
    { 0x3b59c3, 4, { 0x48, 0xc1, 0xe3, 0x1e }, 3, 0x1a },
    { 0x3df8c4, 7, { 0x48, 0xf7, 0xc1, 0xff, 0xff, 0xff, 0x3f }, 6, 0x03 },
    { 0x3df94a, 4, { 0x48, 0xc1, 0xee, 0x1e }, 3, 0x1a },
    { 0x3df94e, 4, { 0x48, 0xc1, 0xe8, 0x1e }, 3, 0x1a },
    { 0x1e6dfb0, 4, { 0x48, 0xc1, 0xeb, 0x1e }, 3, 0x1a },
};

/* 1 when the bytes at site `i` (read from the image) are the exe's; `got` holds site.len bytes */
static inline int ios_big_hint_site_matches( unsigned i, const uint8_t *got )
{
    unsigned k;

    if (i >= 6) return 0;
    for (k = 0; k < ios_big_hint_tlou_sites[i].len; k++)
        if (got[k] != ios_big_hint_tlou_sites[i].old[k]) return 0;
    return 1;
}

/* 1 the first time `slot` is marked in `seen`, 0 after that, -1 for a slot outside the area */
static inline int ios_big_hint_slot_first( uint64_t seen[IOS_BIG_HINT_TLOU_SLOTS / 64], int slot )
{
    uint64_t bit;

    if (slot < 0 || slot >= IOS_BIG_HINT_TLOU_SLOTS) return -1;
    bit = 1ULL << (slot & 63);
    if (seen[slot >> 6] & bit) return 0;
    seen[slot >> 6] |= bit;
    return 1;
}

#endif /* IOS_BIG_HINT_H */

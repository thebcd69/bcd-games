/* SPDX-License-Identifier: GPL-3.0-only
 * Copyright 2026 thebcd69 (BCD)
 * BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
 *
 * madeira-bcd [unity-va] (build 669, env.MADEIRA_UNITY_SMALL_VA = 1, default off): the decisions of virtual_ios.c's "Unity windows",
 * as pure functions so tests/host/check-unity-small-va.py can compile this header on the host and run them.
 *
 * WHY (docs: tur668 G1 S14, round 2026-11-17 K2): a Unity player's start-up allocator reserves pieces of 0x1ffff000 bytes
 * (MEM_RESERVE, PAGE_NOACCESS, no address; 12 to 34 of them, median 15 before the first frame and 18 in a session) and commits some
 * MB of each. 0x1ffff000 is 2 x 256 MB - 4 KB: it asks for one 256 MB block aligned to 256 MB and over-reserves to be sure to find
 * it. In 6,246 pieces of 406 working logs the first commit was at ceil256(base) in 5,375, and all commits but ten stayed inside
 * [ceil256(base), +256 MB): the other half of the piece is never touched. On an install whose task map leaves only 4 to 5 GB of
 * free address space (17 GB maps, 454 GB maps with the GPU carve-out) the first piece fits, the second does not.
 *
 * WHAT: with the key, a 4 GB-class FEX arena (the emulator's own address-space band, a reserved area only the emulator may
 * use) gets, when it is created and before the emulator allocates anything, one small real HEAD view (PAGE_NOACCESS, default 64 MB)
 * at every 256 MB-aligned address inside it. A piece request is answered with the highest unused head (base aligned to 256 MB,
 * size as asked): the program gets an aligned block whose first 64 MB exist; the rest of the 256 MB window is address space
 * that nobody holds. A commit past the head, inside its own window, is served by a new view at that address when the address
 * space there is free (the emulator fills the arena from the bottom, the first pieces get the top), and refused cleanly, with a
 * log line, when it is not. Nothing is ever handed to two owners knowingly; the one case that cannot be told apart is counted.
 * The windows are disjoint (aligned), so no window ever overlaps another window's head. The unused upper half of a piece (the next
 * window's address space) is not protected: a program that touches it (ten of 6,246 pieces did, a different allocator) lands in the
 * next window.
 *
 * ABSOLUTE LIMIT: one window per 256 MB-aligned slot, so a 4 GB arena serves at most 16 pieces (15 where its base is not aligned);
 * 22 % of the games in the archive ask for 16 or fewer pieces in a whole session. The arena also has to hold the emulator
 * (2.6 GB median, 3.0 GB at the upper end with 72-90 threads; 0.5 GB less with env.MADEIRA_FEX_CALLRET_RESERVE_KB = 64).
 */
#ifndef IOS_UNITY_VA_H
#define IOS_UNITY_VA_H

#include <stdint.h>
#include <stddef.h>

#define IOS_USVA_PIECE        0x1ffff000ULL   /* Unity's reserve: 2 x 256 MB - 4 KB */
#define IOS_USVA_WINDOW       0x10000000ULL   /* 256 MB: the aligned block Unity carves out of the piece */
#define IOS_USVA_MAX          32              /* slots (a 4.5 GB arena has at most 17) */
#define IOS_USVA_HEAD_DEFAULT 64              /* MB */
#define IOS_USVA_HEAD_MIN     16
#define IOS_USVA_HEAD_MAX     128
#define IOS_USVA_ARENA_MIN    0xc0000000ULL   /* 3 GB: below it the emulator alone (2.6 GB median) does not fit */
#define IOS_USVA_ARENA_MAX    0x120000000ULL  /* 4.5 GB: an 8 or 16 GB arena has no use for windows */

enum { IOS_USVA_NONE = 0, IOS_USVA_HELD = 1, IOS_USVA_USED = 2, IOS_USVA_GONE = 3 };

struct ios_usva_slot
{
    uint64_t      base;    /* the aligned window start = the head view's base */
    uint64_t      hw;      /* highest committed end seen in this window, as an offset from base */
    unsigned char state;   /* IOS_USVA_* */
};

/* how a commit request relates to the views already in its range (the caller counts them) */
enum { IOS_USVA_COVER_NONE = 0,       /* no view overlaps the range */
       IOS_USVA_COVER_OURS = 1,       /* one view of ours (a head or a growth view) holds the whole range */
       IOS_USVA_COVER_OURS_PART = 2,  /* views of ours overlap it, or one holds only part of it */
       IOS_USVA_COVER_FOREIGN = 3,    /* one foreign view holds the whole range */
       IOS_USVA_COVER_FOREIGN_PART = 4 };

/* the views that overlap a commit range, as the engine collects them (at most IOS_USVA_IV_MAX; `overflow` when there were more) */
#define IOS_USVA_IV_MAX 16
struct ios_usva_iv
{
    uint64_t base, end;   /* the view */
    int      ours;        /* a head or a growth view of a window */
};

/* IOS_USVA_COVER_* for the range [lo, hi) given the views that overlap it. A view that holds the whole range leaves no room for another
 * (views never overlap each other). With more views than were collected the range is not whole and not ours alone: FOREIGN_PART. */
static inline int ios_usva_cover_of( uint64_t lo, uint64_t hi, const struct ios_usva_iv *v, unsigned n, int overflow )
{
    unsigned i;
    int ours = 0, foreign = overflow != 0, whole = 0;

    for (i = 0; i < n; i++)
    {
        if (v[i].end <= lo || v[i].base >= hi) continue;
        if (v[i].ours) ours = 1; else foreign = 1;
        if (v[i].base <= lo && v[i].end >= hi) whole = 1;
    }
    if (!ours && !foreign) return IOS_USVA_COVER_NONE;
    if (whole && !overflow) return foreign ? IOS_USVA_COVER_FOREIGN : IOS_USVA_COVER_OURS;
    return foreign ? IOS_USVA_COVER_FOREIGN_PART : IOS_USVA_COVER_OURS_PART;
}

/* what to do with a commit request that lies in a live window */
enum { IOS_USVA_PASS = 0,             /* the ordinary commit (inside our own view) */
       IOS_USVA_GROW = 1,             /* make a view for the range first (free address space), then the ordinary commit */
       IOS_USVA_REFUSE_STRADDLE = 2,  /* starts or ends outside a view of ours and overlaps one: STATUS_NO_MEMORY */
       IOS_USVA_REFUSE_CONFLICT = 3,  /* a foreign view holds part of the range: STATUS_NO_MEMORY */
       IOS_USVA_REFUSE_HW = 4,        /* the next contiguous step of the window lies wholly inside a foreign view: STATUS_NO_MEMORY */
       IOS_USVA_PASS_AMBIGUOUS = 5,   /* wholly inside a foreign view, not at the growth point: its owner's own commit, passes, counted */
       IOS_USVA_NOT_OURS = 6 };       /* the range leaves the window: not the window's business */

/* env.MADEIRA_UNITY_SMALL_VA: only exactly "1" turns it on */
static inline int ios_usva_key_on( const char *e )
{
    return e && e[0] == '1' && !e[1];
}

/* env.MADEIRA_UNITY_SMALL_VA_HEAD_MB: a number, clamped to 16..128 and rounded down to a multiple of 16 (Unity's commit steps are 16, 4, 2
 * and 1 MB, so a head of that size holds whole steps); unset, empty or not a number: 64. */
static inline unsigned ios_usva_head_mb( const char *e )
{
    unsigned long v = 0;
    const char *p;

    if (!e || !*e) return IOS_USVA_HEAD_DEFAULT;
    for (p = e; *p; p++)
    {
        if (*p < '0' || *p > '9' || v > 100000) return IOS_USVA_HEAD_DEFAULT;
        v = v * 10 + (unsigned long)(*p - '0');
    }
    if (v < IOS_USVA_HEAD_MIN) v = IOS_USVA_HEAD_MIN;
    if (v > IOS_USVA_HEAD_MAX) v = IOS_USVA_HEAD_MAX;
    return (unsigned)(v & ~15UL);
}

/* the arena is one the windows are meant for: 3 GB to 4.5 GB (the 4 GB step of the arena plan; an 8 or 16 GB arena has room for
 * Unity's pieces as they are, a 2 GB arena does not hold the emulator itself) */
static inline int ios_usva_arena_ok( uint64_t lo, uint64_t hi )
{
    return hi > lo && hi - lo >= IOS_USVA_ARENA_MIN && hi - lo <= IOS_USVA_ARENA_MAX;
}

/* the 256 MB-aligned windows that lie wholly inside [lo, hi), lowest first; returns how many (at most max) */
static inline unsigned ios_usva_aligned_slots( uint64_t lo, uint64_t hi, uint64_t *out, unsigned max )
{
    uint64_t a = (lo + IOS_USVA_WINDOW - 1) & ~(IOS_USVA_WINDOW - 1);
    unsigned n = 0;

    while (n < max && a + IOS_USVA_WINDOW <= hi && a + IOS_USVA_WINDOW > a)
    {
        out[n++] = a;
        a += IOS_USVA_WINDOW;
    }
    return n;
}

/* the slot a piece request gets: the highest one whose head is held and unused (the emulator fills the arena from the bottom), or -1 */
static inline int ios_usva_pick( const struct ios_usva_slot *s, unsigned n )
{
    int i;

    for (i = (int)n - 1; i >= 0; i--)
        if (s[i].state == IOS_USVA_HELD) return i;
    return -1;
}

/* the slot in use whose window holds addr, or -1 */
static inline int ios_usva_find( const struct ios_usva_slot *s, unsigned n, uint64_t addr )
{
    unsigned i;

    for (i = 0; i < n; i++)
        if (s[i].state == IOS_USVA_USED && addr >= s[i].base && addr - s[i].base < IOS_USVA_WINDOW) return (int)i;
    return -1;
}

/* A commit request [x, x + len) (page rounded; x inside a window of base a): what to do.
 * x_off = x - a; hw = the window's highest committed end so far. `cover` says what the views in [x, x + len) are. */
static inline int ios_usva_commit_class( uint64_t x_off, uint64_t len, uint64_t hw, int cover )
{
    if (len == 0 || x_off + len > IOS_USVA_WINDOW || x_off + len < x_off) return IOS_USVA_NOT_OURS;
    switch (cover)
    {
    case IOS_USVA_COVER_OURS:         return IOS_USVA_PASS;
    case IOS_USVA_COVER_NONE:         return IOS_USVA_GROW;
    case IOS_USVA_COVER_OURS_PART:    return IOS_USVA_REFUSE_STRADDLE;
    case IOS_USVA_COVER_FOREIGN_PART: return IOS_USVA_REFUSE_CONFLICT;
    case IOS_USVA_COVER_FOREIGN:      return x_off == hw ? IOS_USVA_REFUSE_HW : IOS_USVA_PASS_AMBIGUOUS;
    }
    return IOS_USVA_NOT_OURS;
}

#endif /* IOS_UNITY_VA_H */

/* ios_reloc_walk.h: bounds for the walks over a PE base-relocation directory in a JIT pool copy.
 *
 * SPDX-License-Identifier: GPL-3.0-only
 * Copyright 2026 thebcd69 (BCD)
 * BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
 *
 * WHY THIS EXISTS (night round 2026-11-11, reports G8 S7 and G10 S5)
 * ---------------------------------------------------------------
 * virtual_ios.c re-applies a program's DIR64 relocations to its copy in the JIT pool in three places (the first copy, the IAT
 * sync, and ios_jit_copy_module_for_child) and reads the directory the way a linker writes it: it trusts the directory's RVA and
 * size, every block's SizeOfBlock and every block's VirtualAddress. A program whose directory is not what its header says (a
 * packed or patched program: relocations stripped but a directory left in the header; a directory outside the copy) made the
 * walk read past the copy or store through a wild address:
 *   - 4EN8TR (Roommate demo, build 634): `[early-fault] sig=11 ... phase=relocations`, a block VirtualAddress of 0xFFFF0xxx
 *     in a 0x2c000-byte image, the store faults and BCD itself ends ("wineserver crashed").
 *   - V88DKA (The Edge Of - Definitive Edition, build 634): the IAT-sync loop read a SizeOfBlock of about 100 MB at a directory
 *     that lies beyond the 0xa000-byte copy, ran off the mapping inside virtual_mutex, and the Mach handler's best-effort
 *     delivery then waited for the same mutex: 57-76 s of silence, no exit.
 * A well-formed directory passes every test here unchanged, so the walks behave exactly as before for it.
 *
 * What is in this header (pure functions, no state except the log counter; the host check compiles it):
 *   ios_reloc_dir_ok():   the directory [rva, rva+size) lies inside the copy of `image_size` bytes.
 *   ios_reloc_block_ok(): a block header is readable (8 bytes left) and its SizeOfBlock is at least 8 and fits what is left of the copy.
 *   ios_reloc_entry_ok(): the 8 bytes a DIR64 entry names lie inside the copy.
 *   ios_reloc_note():     one `[reloc-bad]` line per walk problem, at most IOS_RELOC_NOTES per run (log only).
 */
#ifndef IOS_RELOC_WALK_H
#define IOS_RELOC_WALK_H

#define IOS_RELOC_NOTES 8

/* 1 when a directory of `size` bytes at `rva` lies inside an image of `image_size` bytes. An empty or absent directory is the
 * caller's test (rva == 0 || size == 0 never reaches the walk). */
static int ios_reloc_dir_ok( size_t image_size, unsigned int rva, unsigned int size )
{
    return (size_t)rva < image_size && (size_t)size <= image_size - (size_t)rva;
}

/* 1 when the block at `block` may be read and walked: `left` is the number of bytes from `block` to the END OF THE COPY (not of
 * the directory: Windows' own loader and Wine's process every block that starts inside the directory even when the directory's
 * Size field cuts the last one short, and a program that relies on that must keep working). The header is eight bytes;
 * SizeOfBlock covers the header and the 16-bit entries, so it is at least eight and at most what is left of the copy. */
static int ios_reloc_block_ok( unsigned int block_size, size_t left )
{
    return left >= 8 && block_size >= 8 && (size_t)block_size <= left;
}

/* 1 when a DIR64 entry at `fixup_rva` (the block's VirtualAddress plus the entry's 12-bit offset, added as size_t so a garbage
 * VirtualAddress near 4 GB cannot wrap) names eight bytes inside the copy of `image_size` bytes. */
static int ios_reloc_entry_ok( size_t image_size, size_t fixup_rva )
{
    return image_size >= 8 && fixup_rva <= image_size - 8;
}

/* The log line. `where` says which walk ("first-copy", "iat-sync", "child-copy"), `why` the failed test ("directory", "block",
 * "entry"). Printed up to IOS_RELOC_NOTES times in a run, then one line says the rest is not printed. The keep bitmap of the IAT
 * sync (which only reads the directory to find the words that are relocation targets) does not print: it skips a directory that
 * lies outside the copy, which leaves it on the old rule (every changed word is translated), and the walk right after it logs. */
static void ios_reloc_note( const char *where, const char *module, const char *why, unsigned int rva, unsigned int size,
                            unsigned int block_rva, unsigned int block_size, size_t image_size )
{
    static volatile int lines;
    int n = __sync_add_and_fetch( &lines, 1 );

    if (n > IOS_RELOC_NOTES + 1) return;
    if (n == IOS_RELOC_NOTES + 1)
    {
        dprintf( STDERR_FILENO, "[reloc-bad] %d lines printed; the rest of this run's relocation-directory problems are not\n",
                 IOS_RELOC_NOTES );
        return;
    }
    dprintf( STDERR_FILENO,
             "[reloc-bad] r652 %s walk of %s: %s is outside the copy or malformed (directory rva=0x%x size=0x%x, block rva=0x%x "
             "size=0x%x, copy size=0x%lx): the walk stops here and the rest of the directory is not applied\n",
             where, module ? module : "?", why, rva, size, block_rva, block_size, (unsigned long)image_size );
}

#endif

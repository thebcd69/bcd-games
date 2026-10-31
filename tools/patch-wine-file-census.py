#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""[file-census]: where a game's file lookups spend their time, ten seconds at a time (round 624, agent D2 S19; log only).

Counter-Strike: Source (QJKJZJ, RQ8CSF) stalls after a menu click in 450-1,900 CreateFileA calls a second on the main thread, 14 of 15
thread samples inside Wine's host file calls (fstatat from find_file_in_dir, openat, getdirentries64), and the log could not say whether
the lookups hit, miss, scan directories or simply cost that much per call. This counts, in Wine's lookup code
(dlls/ntdll/unix/file.c), per ten-second window:

  full-path tries / hits    lookup_unix_name's whole-path fstatat shortcut
  component lookups         find_file_in_dir calls (one path component that the shortcut did not find)
  shortcut hits / misses    a component found by its exact name / not found at all
  scans / entries           directory scans (openat + readdir) and the entries they read
  us-in-lookup              time inside find_file_in_dir, all threads together, and its share of the window
  slowest                   the slowest component lookup of the window (the end of its path, milliseconds)
  case-sensitive            what get_dir_case_sensitivity answered last (a case-insensitive volume never scans)

and prints one "[file-census]" line for a window with 200 component lookups or more, 60 lines a session at most. Nothing else changes:
every lookup returns what it returned before. Two time stamps and a few relaxed atomic adds per component lookup; env MADEIRA_FILE_CENSUS
= 0 turns it off (read once).

Applied to the Wine checkout after the file-trace patch (same file); the submodule is not committed to. Idempotent; exits non-zero when an
anchor is missing. Usage: patch-wine-file-census.py [path/to/wine/dlls/ntdll/unix/file.c]
"""
import sys

MARKER = "madeira-bcd: [file-census]"

FIND_OLD = """static NTSTATUS find_file_in_dir( int root_fd, char *unix_name, int pos, const WCHAR *name, int length,
                                  BOOLEAN check_case )
{
    WCHAR buffer[MAX_DIR_ENTRY_LEN];"""

HELPER = r"""#ifdef WINE_IOS
/* madeira-bcd: [file-census] (tools/patch-wine-file-census.py, round 624, agent D2 S19: Counter-Strike: Source's CreateFileA storm,
 * QJKJZJ / RQ8CSF): component lookups, shortcut hits, misses, directory scans and the time spent, per ten-second window. Log only. */
#include <mach/mach_time.h>
static struct
{
    long long full_tries, full_hits, lookups, shortcut_hits, misses, scans, entries;
    unsigned long long ticks, slow_ticks, window;
} madeira_fc;
static char madeira_fc_slow_path[64];
static int madeira_fc_case = -1, madeira_fc_lines;

static int madeira_fc_on( void )
{
    static int on = -1;
    if (on < 0)
    {
        const char *e = getenv( "MADEIRA_FILE_CENSUS" );
        on = !(e && e[0] == '0');
    }
    return on;
}

static unsigned long long madeira_fc_us( unsigned long long ticks )
{
    static mach_timebase_info_data_t tb;
    if (!tb.denom) mach_timebase_info( &tb );
    return ticks * tb.numer / tb.denom / 1000;
}

static void madeira_fc_note( NTSTATUS status, unsigned long long t0, const char *unix_name )
{
    unsigned long long now = mach_absolute_time(), dt = now - t0, start, slow;
    long long lookups;

    __atomic_add_fetch( &madeira_fc.lookups, 1, __ATOMIC_RELAXED );
    if (status) __atomic_add_fetch( &madeira_fc.misses, 1, __ATOMIC_RELAXED );
    __atomic_add_fetch( &madeira_fc.ticks, dt, __ATOMIC_RELAXED );
    if (dt > __atomic_load_n( &madeira_fc.slow_ticks, __ATOMIC_RELAXED ))
    {
        size_t len = strlen( unix_name );
        __atomic_store_n( &madeira_fc.slow_ticks, dt, __ATOMIC_RELAXED );
        snprintf( madeira_fc_slow_path, sizeof(madeira_fc_slow_path), "%s", unix_name + (len > 60 ? len - 60 : 0) );
    }
    start = __atomic_load_n( &madeira_fc.window, __ATOMIC_RELAXED );
    if (!start)
    {
        __atomic_compare_exchange_n( &madeira_fc.window, &start, now, 0, __ATOMIC_RELAXED, __ATOMIC_RELAXED );
        return;
    }
    if (madeira_fc_us( now - start ) < 10000000) return;
    if (!__atomic_compare_exchange_n( &madeira_fc.window, &start, now, 0, __ATOMIC_RELAXED, __ATOMIC_RELAXED )) return;
    lookups = __atomic_exchange_n( &madeira_fc.lookups, 0, __ATOMIC_RELAXED );
    {
        long long full_tries = __atomic_exchange_n( &madeira_fc.full_tries, 0, __ATOMIC_RELAXED );
        long long full_hits = __atomic_exchange_n( &madeira_fc.full_hits, 0, __ATOMIC_RELAXED );
        long long hits = __atomic_exchange_n( &madeira_fc.shortcut_hits, 0, __ATOMIC_RELAXED );
        long long misses = __atomic_exchange_n( &madeira_fc.misses, 0, __ATOMIC_RELAXED );
        long long scans = __atomic_exchange_n( &madeira_fc.scans, 0, __ATOMIC_RELAXED );
        long long entries = __atomic_exchange_n( &madeira_fc.entries, 0, __ATOMIC_RELAXED );
        unsigned long long us = madeira_fc_us( __atomic_exchange_n( &madeira_fc.ticks, 0, __ATOMIC_RELAXED ) );
        unsigned long long wall = madeira_fc_us( now - start );

        slow = madeira_fc_us( __atomic_exchange_n( &madeira_fc.slow_ticks, 0, __ATOMIC_RELAXED ) );
        if (lookups < 200 || __atomic_add_fetch( &madeira_fc_lines, 1, __ATOMIC_RELAXED ) > 60) return;
        fprintf( stderr, "[file-census] r624 %llus: case-sensitive=%d full-path-tries=%lld full-path-hits=%lld component-lookups=%lld "
                 "shortcut-hits=%lld misses=%lld scans=%lld entries-scanned=%lld us-in-lookup=%llu (%llu%% of the window) "
                 "slowest=%llu.%03llu ms ...%s\n", wall / 1000000, madeira_fc_case, full_tries, full_hits, lookups, hits, misses,
                 scans, entries, us, wall ? us * 100 / wall : 0, slow / 1000, slow % 1000, madeira_fc_slow_path );
    }
}

static NTSTATUS madeira_fc_find_inner( int root_fd, char *unix_name, int pos, const WCHAR *name, int length,
                                       BOOLEAN check_case );

static NTSTATUS find_file_in_dir( int root_fd, char *unix_name, int pos, const WCHAR *name, int length,
                                  BOOLEAN check_case )
{
    unsigned long long t0;
    NTSTATUS status;

    if (!madeira_fc_on()) return madeira_fc_find_inner( root_fd, unix_name, pos, name, length, check_case );
    t0 = mach_absolute_time();
    status = madeira_fc_find_inner( root_fd, unix_name, pos, name, length, check_case );
    madeira_fc_note( status, t0, unix_name );
    return status;
}
#define find_file_in_dir madeira_fc_find_inner
#endif

"""

FIND_NEW = HELPER + FIND_OLD + "\n#ifdef WINE_IOS\n#undef find_file_in_dir\n#endif"

SHORTCUT_OLD = """        unix_name[pos + ret] = 0;
        if (!fstatat( root_fd, unix_name, &st, 0 )) return STATUS_SUCCESS;
    }
    if (check_case) goto not_found;  /* we want an exact match */"""
SHORTCUT_NEW = """        unix_name[pos + ret] = 0;
        if (!fstatat( root_fd, unix_name, &st, 0 ))
        {
#ifdef WINE_IOS
            __atomic_add_fetch( &madeira_fc.shortcut_hits, 1, __ATOMIC_RELAXED );   /* madeira-bcd: [file-census] */
#endif
            return STATUS_SUCCESS;
        }
    }
    if (check_case) goto not_found;  /* we want an exact match */"""

CASE_OLD = """    if (!is_name_8_dot_3 && !get_dir_case_sensitivity( root_fd, unix_name )) goto not_found;"""
CASE_NEW = """#ifdef WINE_IOS
    if (!is_name_8_dot_3 && !(madeira_fc_case = get_dir_case_sensitivity( root_fd, unix_name ))) goto not_found;   /* madeira-bcd: [file-census] */
#else
    if (!is_name_8_dot_3 && !get_dir_case_sensitivity( root_fd, unix_name )) goto not_found;
#endif"""

SCAN_OLD = """    if ((fd = openat( root_fd, unix_name, O_RDONLY )) == -1) return errno_to_status( errno );
    if (!(dir = fdopendir( fd )))"""
SCAN_NEW = """#ifdef WINE_IOS
    __atomic_add_fetch( &madeira_fc.scans, 1, __ATOMIC_RELAXED );   /* madeira-bcd: [file-census] */
#endif
    if ((fd = openat( root_fd, unix_name, O_RDONLY )) == -1) return errno_to_status( errno );
    if (!(dir = fdopendir( fd )))"""

ENTRY_OLD = """    unix_name[pos - 1] = '/';
    while ((de = readdir( dir )))
    {
        ret = ntdll_umbstowcs( de->d_name, strlen(de->d_name), buffer, MAX_DIR_ENTRY_LEN );"""
ENTRY_NEW = """    unix_name[pos - 1] = '/';
    while ((de = readdir( dir )))
    {
#ifdef WINE_IOS
        __atomic_add_fetch( &madeira_fc.entries, 1, __ATOMIC_RELAXED );   /* madeira-bcd: [file-census] */
#endif
        ret = ntdll_umbstowcs( de->d_name, strlen(de->d_name), buffer, MAX_DIR_ENTRY_LEN );"""

FULL_OLD = """        for (p = unix_name + pos ; *p; p++) if (*p == '\\\\') *p = '/';
        if (!fstatat( root_fd, unix_name, &st, 0 ))
        {
            if (disposition == FILE_CREATE) return STATUS_OBJECT_NAME_COLLISION;"""
FULL_NEW = """        for (p = unix_name + pos ; *p; p++) if (*p == '\\\\') *p = '/';
#ifdef WINE_IOS
        __atomic_add_fetch( &madeira_fc.full_tries, 1, __ATOMIC_RELAXED );   /* madeira-bcd: [file-census] */
#endif
        if (!fstatat( root_fd, unix_name, &st, 0 ))
        {
#ifdef WINE_IOS
            __atomic_add_fetch( &madeira_fc.full_hits, 1, __ATOMIC_RELAXED );   /* madeira-bcd: [file-census] */
#endif
            if (disposition == FILE_CREATE) return STATUS_OBJECT_NAME_COLLISION;"""


def once(text, old, new, what, path):
    if text.count(old) != 1:
        sys.exit("patch-wine-file-census: anchor '%s' not found exactly once in %s" % (what, path))
    return text.replace(old, new)


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else "wine/dlls/ntdll/unix/file.c"
    text = open(path).read()
    if MARKER in text:
        print("patch-wine-file-census: already patched")
        return
    text = once(text, FIND_OLD, FIND_NEW, "find_file_in_dir", path)
    text = once(text, SHORTCUT_OLD, SHORTCUT_NEW, "find_file_in_dir shortcut", path)
    text = once(text, CASE_OLD, CASE_NEW, "get_dir_case_sensitivity", path)
    text = once(text, SCAN_OLD, SCAN_NEW, "openat scan", path)
    text = once(text, ENTRY_OLD, ENTRY_NEW, "readdir loop", path)
    text = once(text, FULL_OLD, FULL_NEW, "lookup_unix_name shortcut", path)
    open(path, "w").write(text)
    print("patch-wine-file-census: patched")


if __name__ == "__main__":
    main()

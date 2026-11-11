#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""[dir-slow] / [dir-storm]: where a process spends its time listing directories (night round 2026-11-11, A2 S4; log only).

Spec Ops: The Line and XCOM: Enemy Unknown in BCD Dock (Send Logs 0TF6NN, 82Y3VH, 7NVV4P; build 634): Valve's CEG job never finishes in 95 s and
the only fact the log has is that the Steam Client Service's thread "ClientModuleManagerPool:0" sits at 95 % CPU in NtQueryDirectoryFile ->
get_file_info (stat + listxattr of one directory entry after another, `[thread-stacks]` / `[thread-sample]`); WHICH directory it lists, how many
entries it has and how long a listing takes is in no log (`[dir-read]` names the directory only with MADEIRA_FILE_TRACE = 1, which a service
does not get, and [file-census] counts lookups, not listings). With this, in every Wine process (services included), per ten-second window:

  [dir-storm] r652 10s: calls=412 ms-in-calls=3977 (39% of the window) bytes=1507328 slowest=812 ms names=31422 dir=c:/windows/system32
  [dir-slow] r652 812 ms status=0x00000000 bytes=8160 names=31422 pos=1180 mask=* dir=c:/windows/system32

a [dir-storm] line for a window in which listings took 2 s or more (24 lines a session at most): the number of NtQueryDirectoryFile calls, the time
inside them (all threads together; the calls are serialised by the directory mutex), the bytes they returned, the slowest call of the window, the
size of the directory that call listed and its path (relative to the Windows drive when it is below one); and a [dir-slow] line for one listing that
took 500 ms or more (12 lines a session at most). Nothing else changes: every listing returns what it returned before. Two time stamps a call;
env MADEIRA_DIR_STORM = 0 turns it off (read once).

Applied to the Wine checkout after the file-census patch (same file; this code sits before it). Idempotent (marker "madeira-bcd: [dir-storm]");
every anchor is checked before the file is written; fails by name when a function moved.
Usage: patch-wine-dir-storm.py [WINE-DIR | path/to/wine/dlls/ntdll/unix/file.c]
"""
from pathlib import Path
import sys

MARKER = "madeira-bcd: [dir-storm]"
FILE = "dlls/ntdll/unix/file.c"

ANCHOR = ("/******************************************************************************\n"
          " *              NtQueryDirectoryFile   (NTDLL.@)\n"
          " */\n"
          "NTSTATUS WINAPI NtQueryDirectoryFile( HANDLE handle, HANDLE event, PIO_APC_ROUTINE apc_routine,\n")
HELPER = r'''#ifdef WINE_IOS
/* ''' + MARKER + r''' (tools/patch-wine-dir-storm.py, night round 2026-11-11, A2 S4: Spec Ops: The Line 0TF6NN, the Steam Client Service's
 * "ClientModuleManagerPool:0" at 95 % CPU in NtQueryDirectoryFile): time inside directory listings, per ten-second window ([dir-storm], a window
 * of 2 s or more, 24 lines at most) and a single listing of 500 ms or more ([dir-slow], 12 lines at most). Log only. NtQueryDirectoryFile holds
 * dir_mutex while it works, so the counters need no atomics. */
#include <mach/mach_time.h>
static struct
{
    unsigned long long window, ticks, slow_ticks, bytes;
    unsigned int calls, slow_names, storm_lines, slow_lines;
    char slow_dir[96];
} madeira_ds;

static int madeira_ds_on( void )
{
    static int on = -1;
    if (on < 0)
    {
        const char *e = getenv( "MADEIRA_DIR_STORM" );
        on = !(e && e[0] == '0');
    }
    return on;
}

static unsigned long long madeira_ds_ms( unsigned long long ticks )
{
    static mach_timebase_info_data_t tb;
    if (!tb.denom) mach_timebase_info( &tb );
    return ticks * tb.numer / tb.denom / 1000000;
}

/* the directory of fd as the Windows drive sees it when it is below one, else the end of the unix path */
static void madeira_ds_dir( int fd, char *out, size_t size )
{
    char path[PATH_MAX];
    const char *p, *drive;

    out[0] = 0;
    if (fcntl( fd, F_GETPATH, path ) == -1) { snprintf( out, size, "(unknown)" ); return; }
    if ((drive = strstr( path, "/dosdevices/" ))) p = drive + strlen( "/dosdevices/" );
    else p = path + (strlen( path ) > size - 1 ? strlen( path ) - (size - 1) : 0);
    snprintf( out, size, "%s", p );
}

static void madeira_ds_note( unsigned long long t0, int fd, unsigned int status, const IO_STATUS_BLOCK *io, const struct dir_data *data,
                             const UNICODE_STRING *mask )
{
    unsigned long long now = mach_absolute_time(), dt = now - t0, ms = madeira_ds_ms( dt );
    unsigned int names = data ? data->count : 0, i;
    char dir[96], mask_a[32];

    madeira_ds.calls++;
    madeira_ds.ticks += dt;
    madeira_ds.bytes += io->Information;
    if (ms >= 500 || dt > madeira_ds.slow_ticks)
    {
        madeira_ds_dir( fd, dir, sizeof(dir) );
        if (dt > madeira_ds.slow_ticks)
        {
            madeira_ds.slow_ticks = dt;
            madeira_ds.slow_names = names;
            memcpy( madeira_ds.slow_dir, dir, sizeof(dir) );
        }
        if (ms >= 500 && madeira_ds.slow_lines < 12)
        {
            for (i = 0; mask && i < mask->Length / sizeof(WCHAR) && i < sizeof(mask_a) - 1; i++)
                mask_a[i] = (mask->Buffer[i] >= 32 && mask->Buffer[i] < 127) ? (char)mask->Buffer[i] : '?';
            mask_a[mask ? i : 0] = 0;
            madeira_ds.slow_lines++;
            fprintf( stderr, "[dir-slow] r652 %llu ms status=0x%08x bytes=%lu names=%u pos=%u mask=%s dir=%s\n", ms, status,
                     (unsigned long)io->Information, names, data ? data->pos : 0, mask_a, dir );
        }
    }
    if (!madeira_ds.window)
    {
        madeira_ds.window = now;
        return;
    }
    if (madeira_ds_ms( now - madeira_ds.window ) < 10000) return;
    {
        unsigned long long wall = madeira_ds_ms( now - madeira_ds.window ), in = madeira_ds_ms( madeira_ds.ticks );

        if (in >= 2000 && madeira_ds.storm_lines < 24)
        {
            madeira_ds.storm_lines++;
            fprintf( stderr, "[dir-storm] r652 %llus: calls=%u ms-in-calls=%llu (%llu%% of the window) bytes=%llu slowest=%llu ms names=%u dir=%s\n",
                     wall / 1000, madeira_ds.calls, in, wall ? in * 100 / wall : 0, madeira_ds.bytes, madeira_ds_ms( madeira_ds.slow_ticks ),
                     madeira_ds.slow_names, madeira_ds.slow_dir );
        }
        madeira_ds.window = now;
        madeira_ds.calls = 0;
        madeira_ds.ticks = madeira_ds.slow_ticks = madeira_ds.bytes = 0;
        madeira_ds.slow_names = 0;
        madeira_ds.slow_dir[0] = 0;
    }
}
#endif

'''
LOCALS_OLD = "    unsigned int status;\n    BOOL by_path = FALSE;\n\n    TRACE(\"(%p %p %p %p %p %p 0x%08x 0x%08x 0x%08x %s 0x%08x\\n\",\n"
LOCALS_NEW = ("    unsigned int status;\n    BOOL by_path = FALSE;\n#ifdef WINE_IOS\n    unsigned long long madeira_ds_t0 = 0;   /* " + MARKER + " */\n#endif\n\n"
              "    TRACE(\"(%p %p %p %p %p %p 0x%08x 0x%08x 0x%08x %s 0x%08x\\n\",\n")
START_OLD = "    mutex_lock( &dir_mutex );\n\n#ifdef WINE_IOS\n    by_path = ios_dir_read_by_path( fd );\n#endif\n"
START_NEW = ("    mutex_lock( &dir_mutex );\n\n#ifdef WINE_IOS\n    if (madeira_ds_on()) madeira_ds_t0 = mach_absolute_time();   /* " + MARKER + " */\n"
             "    by_path = ios_dir_read_by_path( fd );\n#endif\n")
END_OLD = "#ifdef WINE_IOS\n    ios_dir_read_trace( status, io, data );\n    ios_dir_path[0] = 0;\n#endif\n\n    mutex_unlock( &dir_mutex );\n"
END_NEW = ("#ifdef WINE_IOS\n    if (madeira_ds_t0) madeira_ds_note( madeira_ds_t0, fd, status, io, data, mask );   /* " + MARKER + " */\n"
           "    ios_dir_read_trace( status, io, data );\n    ios_dir_path[0] = 0;\n#endif\n\n    mutex_unlock( &dir_mutex );\n")


def patch(src):
    if MARKER in src:
        if src.count(HELPER) != 1 or src.count(LOCALS_NEW) != 1 or src.count(START_NEW) != 1 or src.count(END_NEW) != 1:
            raise ValueError("partial or changed directory storm patch")
        return src
    for what, old in (("the NtQueryDirectoryFile header", ANCHOR), ("its local declarations", LOCALS_OLD),
                      ("the start of its directory read", START_OLD), ("the end of its directory read", END_OLD)):
        if src.count(old) != 1:
            raise ValueError("%s of dlls/ntdll/unix/file.c not found once (Wine changed)" % what)
    start = src.index(ANCHOR)
    if not start < src.index(LOCALS_OLD) < src.index(START_OLD) < src.index(END_OLD) < start + 6000:
        raise ValueError("NtQueryDirectoryFile is no longer in the expected shape (Wine changed)")
    src = src.replace(ANCHOR, HELPER + ANCHOR, 1)
    for old, new in ((LOCALS_OLD, LOCALS_NEW), (START_OLD, START_NEW), (END_OLD, END_NEW)):
        src = src.replace(old, new, 1)
    return src


def main():
    if len(sys.argv) != 2:
        sys.exit("usage: patch-wine-dir-storm.py [WINE-DIR | path/to/wine/dlls/ntdll/unix/file.c]")
    arg = Path(sys.argv[1])
    path = arg if arg.suffix == ".c" else arg / FILE
    source = path.read_text()
    try:
        result = patch(source)
    except ValueError as error:
        sys.exit("patch-wine-dir-storm: " + str(error))
    if result == source:
        print("patch-wine-dir-storm: already patched")
    else:
        path.write_text(result)
        print("patch-wine-dir-storm: patched")


if __name__ == "__main__":
    main()

/* env.MADEIRA_CEF_POOL_IMAGES: a program's own Chromium runs in ONE process and its PartitionAlloc pools get the address band's four slots
 * (OPT-IN, off unless set; needs env.MADEIRA_SC_PA_POOLS = 2).
 *
 * SPDX-License-Identifier: GPL-3.0-only
 * Copyright 2026 thebcd69 (BCD)
 * BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
 *
 * WHY THIS EXISTS
 * ---------------
 * X-Plane 12 (Steam 2014780, build 670, iPhone18,2, send log MX3R0X): X-Plane.exe loads libcef.dll (200 MB) and chrome_elf.dll into its own
 * process and starts Chromium from its main thread ("CrBrowserMain"). PartitionAlloc runs in two copies (chrome_elf.dll's and libcef.dll's) and
 * each asks for a plain 16 GB reserve on a 16 GB boundary (the regular pool) and a 16 GB + 64 KB one whose base is a boundary minus 64 KB (the
 * BackupRefPtr pool and its forbidden zone): [jumbo#1] plain, #2 guard, #3 plain, #4 guard. The address band holds four such slots and a session
 * without layout 2 has two of them free (the JIT pool's alias and the guest furniture are in the first, the FEX arena in the last), so #3 and #4
 * were soft grants on the views of #2 and #1: two live pools on the same pages, a commit of size 0 at 0x780020d000 (c000000d) and PartitionAlloc's
 * own out-of-memory int3 in libcef.dll. cefclient.exe (--type=gpu-process, X-Plane's Chromium helper) then found no slot for ITS four pools
 * ([jumbo#5-9], c0000018 / c0000017). This is the EA app's failure word for word (build 598, EADesktop.exe), which build 599 solved for the
 * EA app only: its image names were in the code (virtual_ios.c "the EA app's Chromium", process_ios.c ea_cef_kind).
 *
 * WHAT IT DOES
 * ------------
 * env.MADEIRA_CEF_POOL_IMAGES = x-plane.exe,cefclient.exe (1 to 4 program file names, ".exe" at the end, separated by commas): the FIRST name is the
 * program that hosts Chromium (the browser), every other name is one of its Chromium helpers. With the key, and with env.MADEIRA_SC_PA_POOLS = 2
 * beside it, the browser gets what the EA app's browser gets, and nothing else changes:
 *   - virtual_ios.c: its four PartitionAlloc pools take the four slots of layout 2 (ios_ea_route), a helper's big reserve gets no soft grant on
 *     a slot that holds a view;
 *   - process_ios.c: the browser starts --single-process (renderer and GPU become its threads) with PartitionAllocBackupRefPtr off, V8 --jitless and
 *     no proxy server, plus env.MADEIRA_CEF_FLAGS verbatim, and a --type= child of any listed program is refused (there is no slot for its pools).
 * Names are compared with the file name of the process image (any folder, ASCII case ignored). The EA app's own two programs are never taken from
 * the list: env.MADEIRA_EA_CEF has them. A list that is not valid (an empty entry, a name that is not a plain .exe name, more than four names) lists
 * nothing; process_ios.c says so once. Without the variable none of this runs: one getenv per started program, one per large reserve.
 *
 * The string work is here, free of Wine's types but for WCHAR, so tests/host/check-cef-pool-images.py compiles and runs it on the host; the callers are
 * virtual_ios.c (ios_ea_cef_app) and process_ios.c (cef_one_kind).
 */
#ifndef IOS_CEF_IMAGES_H
#define IOS_CEF_IMAGES_H

#include <stddef.h>
#include <string.h>

#define IOS_CEF_IMAGES_MAX 4
#define IOS_CEF_NAME_MAX   64
#define IOS_CEF_LIST_MAX   511   /* characters of the whole list, blanks included: virtual_ios.c keeps a copy of it */

enum { IOS_CEF_NOT = 0, IOS_CEF_BROWSER = 1, IOS_CEF_HELPER = 2 };

static inline unsigned char ios_cef_lower( unsigned char c )
{
    return (c >= 'A' && c <= 'Z') ? (unsigned char)(c + ('a' - 'A')) : c;
}

/* A program file name of the list: 5 to 64 ASCII letters, digits, '.', '_' and '-', ".exe" at the end (any case). */
static inline int ios_cef_name_valid( const char *s, size_t len )
{
    size_t i;

    if (len < 5 || len > IOS_CEF_NAME_MAX) return 0;
    for (i = 0; i < len; i++)
    {
        unsigned char c = (unsigned char)s[i];
        if (!((c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9') || c == '.' || c == '_' || c == '-')) return 0;
    }
    return s[len - 4] == '.' && ios_cef_lower( (unsigned char)s[len - 3] ) == 'e' &&
           ios_cef_lower( (unsigned char)s[len - 2] ) == 'x' && ios_cef_lower( (unsigned char)s[len - 1] ) == 'e';
}

/* The names of `list`, blanks around a name ignored: their number (1 to IOS_CEF_IMAGES_MAX) with each name's start and length, or 0 when the list
 * is empty, longer than IOS_CEF_LIST_MAX characters, has an entry that is not a valid name, or has more than IOS_CEF_IMAGES_MAX names. */
static inline int ios_cef_list_parse( const char *list, const char *start[IOS_CEF_IMAGES_MAX], size_t len[IOS_CEF_IMAGES_MAX] )
{
    int n = 0;

    if (!list || strlen( list ) > IOS_CEF_LIST_MAX) return 0;
    for (;;)
    {
        const char *p = list, *end;

        while (*p == ' ' || *p == '\t') p++;
        end = p;
        while (*end && *end != ',') end++;
        list = end;                                  /* at the comma or the NUL */
        while (end > p && (end[-1] == ' ' || end[-1] == '\t')) end--;
        if (n >= IOS_CEF_IMAGES_MAX || !ios_cef_name_valid( p, (size_t)(end - p) )) return 0;
        start[n] = p;
        len[n] = (size_t)(end - p);
        n++;
        if (!*list) return n;
        list++;                                      /* past the comma */
    }
}

/* 1 when the last component of `path` (after '\' or '/'), `path_len` characters, is the ASCII name `name` of `n` characters, any case. */
static inline int ios_cef_leaf_is( const WCHAR *path, size_t path_len, const char *name, size_t n )
{
    size_t base = 0, k;

    if (!path) return 0;
    for (k = 0; k < path_len; k++) if (path[k] == '\\' || path[k] == '/') base = k + 1;
    if (path_len - base != n) return 0;
    for (k = 0; k < n; k++)
    {
        WCHAR c = path[base + k];
        if (c >= 'A' && c <= 'Z') c += 'a' - 'A';
        if (c != (WCHAR)ios_cef_lower( (unsigned char)name[k] )) return 0;
    }
    return 1;
}

/* IOS_CEF_BROWSER when the program image `path` is the first name of `list`, IOS_CEF_HELPER when it is another, IOS_CEF_NOT otherwise: for an
 * image the list does not name, for a list that is not valid, and for the EA app's two programs, which env.MADEIRA_EA_CEF decides about. */
static inline int ios_cef_listed_kind( const char *list, const WCHAR *path, size_t path_len )
{
    const char *start[IOS_CEF_IMAGES_MAX];
    size_t len[IOS_CEF_IMAGES_MAX];
    int n, i;

    if (!path || !path_len || (n = ios_cef_list_parse( list, start, len )) <= 0) return IOS_CEF_NOT;
    if (ios_cef_leaf_is( path, path_len, "eadesktop.exe", 13 ) || ios_cef_leaf_is( path, path_len, "eacefsubprocess.exe", 19 )) return IOS_CEF_NOT;
    for (i = 0; i < n; i++)
        if (ios_cef_leaf_is( path, path_len, start[i], len[i] )) return i ? IOS_CEF_HELPER : IOS_CEF_BROWSER;
    return IOS_CEF_NOT;
}

/* The first name of a valid `list`, as written, copied to `out` (`cap` bytes, at least IOS_CEF_NAME_MAX + 1): 1 when it was, else 0 with "?" in `out`. */
static inline int ios_cef_browser_name( const char *list, char *out, size_t cap )
{
    const char *start[IOS_CEF_IMAGES_MAX];
    size_t len[IOS_CEF_IMAGES_MAX];

    if (cap < 2) return 0;
    if (ios_cef_list_parse( list, start, len ) <= 0 || len[0] + 1 > cap)
    {
        out[0] = '?';
        out[1] = 0;
        return 0;
    }
    memcpy( out, start[0], len[0] );
    out[len[0]] = 0;
    return 1;
}

#endif /* IOS_CEF_IMAGES_H */

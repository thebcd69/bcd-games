// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
//
// The rules of BCD's winebrowser.exe (round 625, the owner's item 23), kept free of Windows so that bcd-openurl/core_test.c checks them
// on the host. openurl.c is the Windows program around them.
//
// Wine's registry opens a web page with "C:\windows\system32\winebrowser.exe" "<url>" (wine.inf: http, https, ftp, mailto and the
// html, pdf and xml file types), Unreal Engine's FPlatformProcess::LaunchURL reads the same command, and ShellExecute follows it. BCD's
// 64-bit farms had no winebrowser.exe and Wine's 32-bit one can start no browser on iOS, so every such request failed (Satisfactory's
// Epic sign-in: "CreateProc failed: File not found", SAZFJQ). This program takes the request as BCD's: an http or https address of at
// most 2048 printable ASCII characters is written to C:\ProgramData\BCD\OpenURL\<ticks>-<process id>.url, where the app asks the player
// whether to open it (Library.swift watchOpenURL, BCDOpenURL.swift). Anything else (a file, mailto:, ftp:) fails as it did before.
#ifndef BCD_OPENURL_CORE_H
#define BCD_OPENURL_CORE_H

#include <stddef.h>
#include <stdio.h>
#include <string.h>

#define OU_MAX_URL 2048

/* The address in a command line (UTF-8, the program's own name first, as GetCommandLine gives it): the first argument after the
 * program's name, past Wine's "-nohome", without its quotes. Copied into `out` (at least OU_MAX_URL + 1 bytes); 0 when there is none
 * or it is too long. */
static int ou_argument(const char *line, char *out, size_t cap)
{
    const char *p = line, *start, *end;
    size_t n;
    if (!line || !out || cap < 2) return 0;
    out[0] = 0;
    /* the program's own name: quoted, or up to the first space */
    while (*p == ' ' || *p == '\t') p++;
    if (*p == '"') { p++; while (*p && *p != '"') p++; if (*p == '"') p++; }
    else while (*p && *p != ' ' && *p != '\t') p++;
    for (;;) {
        while (*p == ' ' || *p == '\t') p++;
        if (!*p) return 0;
        if (*p == '"') { start = ++p; while (*p && *p != '"') p++; end = p; if (*p == '"') p++; }
        else { start = p; while (*p && *p != ' ' && *p != '\t') p++; end = p; }
        n = (size_t)(end - start);
        if (n == 7 && !strncmp(start, "-nohome", 7)) continue;
        if (!n) continue;
        if (n >= cap || n > OU_MAX_URL) return 0;
        memcpy(out, start, n);
        out[n] = 0;
        return 1;
    }
}

static int ou_lower(int c) { return c >= 'A' && c <= 'Z' ? c + 32 : c; }

static int ou_starts(const char *s, const char *prefix)
{
    for (; *prefix; s++, prefix++)
        if (ou_lower((unsigned char)*s) != *prefix) return 0;
    return 1;
}

/* 1 for an address the app may offer: http:// or https://, a host, no user name or password before it, printable ASCII only (no
 * space, no control character, nothing above 0x7e), at most OU_MAX_URL characters. The app checks the same again. */
static int ou_is_page(const char *url)
{
    size_t n, i, skip;
    const char *host, *stop;
    if (!url) return 0;
    n = strlen(url);
    if (!n || n > OU_MAX_URL) return 0;
    for (i = 0; i < n; i++)
        if ((unsigned char)url[i] < 0x21 || (unsigned char)url[i] > 0x7e) return 0;
    if (ou_starts(url, "https://")) skip = 8;
    else if (ou_starts(url, "http://")) skip = 7;
    else return 0;
    host = url + skip;
    stop = host + strcspn(host, "/?#");
    if (stop == host) return 0;                          /* no host */
    for (i = 0; host + i < stop; i++)
        if (host[i] == '@') return 0;                    /* a user name or password before the host */
    if (host[0] == ':' || host[0] == '.') return 0;
    return 1;
}

/* The host of a page (between "//" and the first of ":/?#"), for the log: the rest of an address can carry a sign-in code. */
static void ou_host(const char *url, char *out, size_t cap)
{
    const char *h = strstr(url, "//"), *e;
    size_t n;
    if (!cap) return;
    out[0] = 0;
    if (!h) return;
    h += 2;
    e = h + strcspn(h, ":/?#");
    n = (size_t)(e - h);
    if (n >= cap) n = cap - 1;
    memcpy(out, h, n);
    out[n] = 0;
}

/* The request's file name in the folder: "<ticks>-<pid>.url", so the app takes them oldest first. */
static int ou_file_name(char *out, size_t cap, unsigned long long ticks, unsigned long pid)
{
    int n = snprintf(out, cap, "%020llu-%lu.url", ticks, pid);
    return n > 0 && (size_t)n < cap;
}

/* 1 when the environment value turns the bridge off ("0"); unset or anything else leaves it on (the catalog's default). */
static int ou_off(const char *value) { return value && value[0] == '0' && !value[1]; }

#endif

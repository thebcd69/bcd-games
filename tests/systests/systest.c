// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
//
// systest.c: BCD's system tests, one Windows program told what to do by its first argument. The Wine desktop's Start menu
// (app/Madeira/BCDStartMenu.c) has one entry for each:
//
//   bcd-systest-x64.exe ram <GB>    takes memory in steps and reports how far it got, never beyond what iOS would allow
//   bcd-systest-x86.exe ram32       how much of its 4 GB address space a 32-bit program gets, and how much of it is RAM
//   bcd-systest-x64.exe jit         the JIT pool of this session: size, layout, how full it is
//   bcd-systest-x64.exe cpu         the processor features a game sees and a short speed test
//   bcd-systest-x64.exe sysinfo     BCD's dxdiag: Windows, CPU, memory, GPU, Direct3D 11 and 12 levels
//   bcd-systest-x64.exe vcrt        Visual C++ runtimes (2005 to 2022, x64 and x86) and .NET or Mono pieces a game would find
//   bcd-systest-x64.exe disk        write and read speed of C: and of C:\External when there is one
//   bcd-systest-x64.exe net         DNS and HTTPS to Steam, Epic, GOG, Battle.net and EA
//   bcd-systest-x64.exe all         the quick ones in a row, one report
//
// Where the output goes. On screen: a window with the text, which stays until it is closed. In the session log: stderr is the
// session log (the app points file descriptors 1 and 2 at bcd-log.txt, and a program's standard error handle is the same
// file), so every line is written to it as "[systest] ..." in one WriteFile, as the DirectX tests do ("[dx-test] ..."). The
// same text goes to C:\ProgramData\BCD\bcd-systest.log (the last run); the engine's [guest-log] mirror copies lines with
// "fail" or "error" from a *.log file into the session log too, so a failure reaches the log even when stderr does not.
// Every test ends with one line "RESULT <test>: PASS|LIMIT|WARN|FAIL|SKIP - <numbers>".
//
// What the app and the engine tell it: C:\ProgramData\BCD\host-info.txt (once, at the session's start) and host-live.txt
// (about every second; every quarter of a second once the app holds more than 2.4 GB), written by app/Madeira/BCDHostStatus.c
// and build/ntdll-unix/ios_host_status.h only in the Windows desktop session. Without them the tests still run and say so.
// The pool's layout is in the session's environment (WINE_IOS_JIT_*). core.h holds the logic that needs no Windows.

#define WIN32_LEAN_AND_MEAN
#define COBJMACROS
#define CINTERFACE
#ifndef _WIN32_WINNT
#define _WIN32_WINNT 0x0A00
#endif
#include <winsock2.h>
#include <ws2tcpip.h>
#include <windows.h>
#include <winhttp.h>
#include <dxgi1_4.h>
#include <d3d11.h>
#include <d3d12.h>
#include <cpuid.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "core.h"

#define APP_TITLE "BCD system test"
#define HOST_DIR  "C:\\ProgramData\\BCD"
#define HOST_INFO HOST_DIR "\\host-info.txt"
#define HOST_LIVE HOST_DIR "\\host-live.txt"
#define LOG_FILE  HOST_DIR "\\bcd-systest.log"

#ifdef _WIN64
#define BITS 64
#else
#define BITS 32
#endif

/* ================================================================== output */

static HWND g_wnd, g_edit;
static CRITICAL_SECTION g_lock;
static char *g_text;                       /* the window's text, lines ended by \r\n */
static size_t g_len, g_cap;
static volatile LONG g_dirty;
static volatile LONG g_abort;              /* the window was closed while a test runs */
static HANDLE g_logfile = INVALID_HANDLE_VALUE;
static int g_headless;                     /* no window (the window could not be made): log only */
#define WM_REFRESH (WM_APP + 1)
#define WM_DONE    (WM_APP + 2)
static volatile u64 g_sink;                /* keeps the speed loops from being optimised away */
static volatile double g_sink_d;

/* One line (no newline in it) to the session log in one WriteFile, to the file and to the window. */
static void emit(const char *line)
{
    char buf[1100];
    DWORD written = 0;
    size_t n = strlen(line);
    HANDLE err = GetStdHandle(STD_ERROR_HANDLE);

    if (n > 1000) n = 1000;
    memcpy(buf, "[systest] ", 10);
    memcpy(buf + 10, line, n);
    buf[10 + n] = '\n';
    if (err && err != INVALID_HANDLE_VALUE) WriteFile(err, buf, (DWORD)(11 + n), &written, NULL);
    if (g_logfile != INVALID_HANDLE_VALUE) WriteFile(g_logfile, buf + 10, (DWORD)(n + 1), &written, NULL);

    EnterCriticalSection(&g_lock);
    if (g_len + n + 3 > g_cap) {
        size_t cap = g_cap ? g_cap * 2 : 16384;
        char *bigger;
        while (cap < g_len + n + 3) cap *= 2;
        bigger = (char *)realloc(g_text, cap);
        if (bigger) { g_text = bigger; g_cap = cap; }
    }
    if (g_text && g_len + n + 3 <= g_cap) {
        memcpy(g_text + g_len, buf + 10, n);
        g_len += n;
        g_text[g_len++] = '\r';
        g_text[g_len++] = '\n';
        g_text[g_len] = 0;
    }
    LeaveCriticalSection(&g_lock);
    if (g_wnd && !InterlockedExchange(&g_dirty, 1)) PostMessageA(g_wnd, WM_REFRESH, 0, 0);
}

/* A formatted text; several lines in it are written one by one. */
static void say(const char *fmt, ...)
{
    char text[2400], *p, *eol;
    va_list ap;

    va_start(ap, fmt);
    vsnprintf(text, sizeof text, fmt, ap);
    va_end(ap);
    for (p = text;; p = eol + 1) {
        eol = strchr(p, '\n');
        if (!eol) { emit(p); break; }
        *eol = 0;
        emit(p);
    }
}

/* ================================================================== helpers */

static double now_ms(void)
{
    LARGE_INTEGER f, t;
    QueryPerformanceFrequency(&f);
    QueryPerformanceCounter(&t);
    return (double)t.QuadPart * 1000.0 / (double)f.QuadPart;
}

static u64 xs_next(u64 *s)
{
    u64 x = *s;
    x ^= x << 13;
    x ^= x >> 7;
    x ^= x << 17;
    *s = x;
    return x;
}

/* Data that no compressor shrinks (iOS compresses idle memory, and zeros would cost almost nothing). */
static void fill_random(void *p, size_t bytes, u64 seed)
{
    u64 *q = (u64 *)p, s = seed | 1;
    size_t i, n = bytes / 8;
    for (i = 0; i < n; i++) q[i] = xs_next(&s);
}

static size_t check_random(const void *p, size_t bytes, u64 seed)
{
    const u64 *q = (const u64 *)p;
    u64 s = seed | 1;
    size_t i, n = bytes / 8, bad = 0;
    for (i = 0; i < n; i++) if (q[i] != xs_next(&s)) bad++;
    return bad;
}

static int read_text_file(const char *path, char *buf, size_t cap)
{
    HANDLE h = CreateFileA(path, GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE, NULL, OPEN_EXISTING,
                           FILE_ATTRIBUTE_NORMAL, NULL);
    DWORD got = 0;
    if (h == INVALID_HANDLE_VALUE) return -1;
    if (!ReadFile(h, buf, (DWORD)(cap - 1), &got, NULL)) got = 0;
    CloseHandle(h);
    buf[got] = 0;
    return (int)got;
}

static int path_exists(const char *path) { return GetFileAttributesA(path) != INVALID_FILE_ATTRIBUTES; }
static int dir_exists(const char *path)
{
    DWORD a = GetFileAttributesA(path);
    return a != INVALID_FILE_ATTRIBUTES && (a & FILE_ATTRIBUTE_DIRECTORY);
}

static void wide_to_ascii(const WCHAR *w, char *out, size_t cap)
{
    size_t i = 0;
    for (; w[i] && i + 1 < cap; i++) out[i] = w[i] < 0x80 ? (char)w[i] : '?';
    out[i] = 0;
}

static int env_get(const char *name, char *out, size_t cap)
{
    DWORD n = GetEnvironmentVariableA(name, out, (DWORD)cap);
    if (n == 0 || n >= cap) { out[0] = 0; return 0; }
    return 1;
}

/* The mapped result of a finished test, for the table of "all". */
typedef struct { char name[48]; int verdict; char summary[220]; } result_t;
static result_t g_results[24];
static int g_nresults;

static int finish(const char *name, int verdict, const char *fmt, ...)
{
    char summary[220];
    va_list ap;

    va_start(ap, fmt);
    vsnprintf(summary, sizeof summary, fmt, ap);
    va_end(ap);
    say("RESULT %s: %s - %s", name, verdict_name(verdict), summary);
    if (g_nresults < (int)(sizeof g_results / sizeof g_results[0])) {
        result_t *r = &g_results[g_nresults++];
        snprintf(r->name, sizeof r->name, "%s", name);
        r->verdict = verdict;
        snprintf(r->summary, sizeof r->summary, "%s", summary);
    }
    return verdict;
}

static void header(const char *title)
{
    say("");
    say("==== %s ====", title);
}

/* ================================================================== what the app and the engine say */

static host_status g_host;

/* The app sets MADEIRA_HOST_STATUS (to the folder) in the plain Windows desktop session only. A session without it has no host numbers
 * even if an earlier session left the files in the prefix: those are not this session's and are not read. "0" is the switch. */
static int host_enabled(void)
{
    char v[8];
    DWORD n = GetEnvironmentVariableA("MADEIRA_HOST_STATUS", v, sizeof v);   /* longer than the buffer: it is a folder's path */
    return n > 0 && !(n == 1 && v[0] == '0');
}

static void host_read_info(void)
{
    char text[4096];
    host_status_clear(&g_host);
    if (host_enabled() && read_text_file(HOST_INFO, text, sizeof text) > 0) host_parse_info(text, &g_host);
}

/* Reads host-live.txt into g_host; 1 when it was there. */
static int host_read_live(void)
{
    char text[4096];
    host_status h = g_host;

    h.have_live = 0;
    if (!host_enabled() || read_text_file(HOST_LIVE, text, sizeof text) <= 0) return 0;
    host_parse_live(text, &h);
    if (!h.have_live) return 0;
    g_host = h;
    return 1;
}

/* Waits (at most `timeout_ms`) until the engine has written a sample whose sequence number is `after` or higher. */
static int host_wait_seq(u64 after, DWORD timeout_ms)
{
    DWORD t0 = GetTickCount();
    for (;;) {
        if (host_read_live() && g_host.seq >= after) return 1;
        if (g_abort || GetTickCount() - t0 >= timeout_ms) return 0;
        Sleep(40);
    }
}

static void host_lines(void)
{
    char a[32], b[32];

    if (!g_host.have_info) {
        say("Host numbers: not available (the app writes them only in the Windows desktop session)");
        return;
    }
    fmt_mb(a, sizeof a, g_host.ram_mb);
    fmt_mb(b, sizeof b, g_host.limit_mb);
    say("Device: %s, %s, %s RAM, %llu cores", g_host.model[0] ? g_host.model : "?", g_host.os[0] ? g_host.os : "?", a, g_host.cores);
    say("BCD: build %s%s%s; memory limit iOS gives this app: %s%s", g_host.build[0] ? g_host.build : "?",
        g_host.version[0] ? ", version " : "", g_host.version, g_host.limit_mb ? b : "unknown",
        g_host.increased_memory == 1 ? " (increased-memory entitlement: yes)" :
        g_host.increased_memory == 0 ? " (increased-memory entitlement: NO, iOS closes large games early)" : "");
    if (g_host.extended_va >= 0 || g_host.address_map_gb)
        say("Address space: %llu GB map, extended-virtual-addressing %s", g_host.address_map_gb,
            g_host.extended_va == 1 ? "yes" : g_host.extended_va == 0 ? "no" : "unknown");
    if (g_host.swap_mb) say("Swap tier: on, up to %llu MB of disk backs game memory (it does not count against the limit)", g_host.swap_mb);
}

/* ================================================================== the processor */

static void cpu_collect(cpu_info *c)
{
    unsigned a, b, cc, d, i;

    memset(c, 0, sizeof *c);
    __cpuid_count(0, 0, a, b, cc, d);
    c->max_leaf = a;
    memcpy(c->vendor, &b, 4);
    memcpy(c->vendor + 4, &d, 4);
    memcpy(c->vendor + 8, &cc, 4);
    c->vendor[12] = 0;
    if (c->max_leaf >= 1) {
        __cpuid_count(1, 0, c->l1.eax, c->l1.ebx, c->l1.ecx, c->l1.edx);
    }
    if (c->max_leaf >= 7) {
        __cpuid_count(7, 0, c->l7.eax, c->l7.ebx, c->l7.ecx, c->l7.edx);
        if (c->l7.eax >= 1) __cpuid_count(7, 1, c->l7_1.eax, c->l7_1.ebx, c->l7_1.ecx, c->l7_1.edx);
    }
    __cpuid_count(0x80000000u, 0, a, b, cc, d);
    c->max_ext = a;
    if (c->max_ext >= 0x80000001u) __cpuid_count(0x80000001u, 0, c->e1.eax, c->e1.ebx, c->e1.ecx, c->e1.edx);
    if (c->max_ext >= 0x80000004u) {
        for (i = 0; i < 3; i++) {
            unsigned r[4];
            __cpuid_count(0x80000002u + i, 0, r[0], r[1], r[2], r[3]);
            memcpy(c->brand + 16 * i, r, 16);
        }
        c->brand[48] = 0;
    }
    if (cpu_has(c, "OSXSAVE")) {
        unsigned lo, hi;
        __asm__ volatile("xgetbv" : "=a"(lo), "=d"(hi) : "c"(0));
        c->xcr0 = ((u64)hi << 32) | lo;
    }
}

/* ---- the speed test: three short loops, one thread and all threads ---- */
typedef struct { volatile LONG go; double ms; u64 ops; u64 sink; } spin_job;

static u64 spin_int(double ms, u64 *ops_out)
{
    u64 x = 88172645463325252ull, acc = 0, ops = 0;
    double t0 = now_ms();
    do {
        unsigned i;
        for (i = 0; i < 20000; i++) {
            x ^= x << 13; x ^= x >> 7; x ^= x << 17;
            acc += x * 0x9E3779B97F4A7C15ull + (acc >> 3);
        }
        ops += 20000;
    } while (now_ms() - t0 < ms);
    *ops_out = ops;
    return acc;
}

static double spin_float(double ms, u64 *ops_out)
{
    double a = 1.0000001, b = 0.9999999, c = 0.5;
    u64 ops = 0;
    double t0 = now_ms();
    do {
        unsigned i;
        for (i = 0; i < 20000; i++) {
            c = c * a + b;
            a = a * 1.0000000001 + 1e-12;
            b = b * 0.9999999999 + 1e-12;
        }
        ops += 20000;
    } while (now_ms() - t0 < ms);
    *ops_out = ops;
    return c + a + b;
}

static DWORD WINAPI spin_thread(LPVOID p)
{
    spin_job *j = (spin_job *)p;
    u64 ops = 0;
    while (!j->go) Sleep(1);
    j->sink = spin_int(j->ms, &ops);
    j->ops = ops;
    return 0;
}

static int test_cpu(int quick)
{
    cpu_info c;
    char feat[900];
    SYSTEM_INFO si;
    double ms = quick ? 250.0 : 600.0, t0, single, multi;
    u64 ops;
    int level, threads, i, started = 0;
    spin_job jobs[16];
    HANDLE hs[16];
    DWORD tid;

    header("CPU test");
    cpu_collect(&c);
    GetSystemInfo(&si);
    say("Vendor: %s", c.vendor);
    say("Name:   %s", c.brand[0] ? c.brand : "(the CPU gives no name)");
    say("Cores seen by Windows: %lu, build: %d-bit", (unsigned long)si.dwNumberOfProcessors, BITS);
    cpu_feature_text(&c, feat, sizeof feat, 2, 74);
    say("Features a game sees:\n%s", feat);
    say("AVX usable by programs: %s; AVX-512 usable: %s", cpu_avx_usable(&c) ? "yes" : "no", cpu_avx512_usable(&c) ? "yes" : "no");
    level = cpu_level(&c);
    say("x86-64 level: v%d (%s)", level,
        level >= 4 ? "AVX-512 class" : level == 3 ? "AVX2 class, what most 2020s games ask for" :
        level == 2 ? "SSE4.2 class; a game that needs AVX2 will not start" :
        level == 1 ? "baseline SSE2 only; most 2015+ games need SSE4.2" : "below x86-64");

    g_sink = spin_int(ms, &ops);
    single = (double)ops / (ms * 1000.0);
    say("Integer, 1 thread:   %8.1f million loops/s", single);
    t0 = now_ms();
    {
        u64 fops;
        g_sink_d = spin_float(ms, &fops);
        say("Floating, 1 thread:  %8.1f million loops/s", (double)fops / ((now_ms() - t0) * 1000.0));
    }
    threads = (int)si.dwNumberOfProcessors;
    if (threads > 16) threads = 16;
    if (threads < 1) threads = 1;
    for (i = 0; i < threads; i++) {
        memset(&jobs[i], 0, sizeof jobs[i]);
        jobs[i].ms = ms;
        hs[i] = CreateThread(NULL, 0, spin_thread, &jobs[i], 0, &tid);
        if (hs[i]) started++;
    }
    for (i = 0; i < threads; i++) jobs[i].go = 1;
    multi = 0;
    for (i = 0; i < threads; i++) {
        if (!hs[i]) continue;
        WaitForSingleObject(hs[i], 20000);
        CloseHandle(hs[i]);
        multi += (double)jobs[i].ops / (ms * 1000.0);
        g_sink ^= jobs[i].sink;
    }
    say("Integer, %d threads:  %8.1f million loops/s (%.1fx one thread)", started, multi, single > 0 ? multi / single : 0.0);
    if (level < 1) return finish("cpu", V_FAIL, "below x86-64 baseline (no SSE2)");
    if (level < 2) return finish("cpu", V_FAIL, "x86-64 v%d only: no SSE4.2/POPCNT, many games will not start", level);
    if (started < threads) return finish("cpu", V_WARN, "v%d, %.0f M loops/s; only %d of %d speed threads started", level, single, started, threads);
    return finish("cpu", V_PASS, "x86-64 v%d, AVX %s, %.0f M loops/s on 1 thread, %.0f on %d", level, cpu_avx_usable(&c) ? "yes" : "no",
                  single, multi, started);
}

/* ================================================================== memory */

static void windows_memory_lines(const char *when)
{
    MEMORYSTATUSEX m;
    char t[32], a[32], pt[32], pa[32], vt[32], va[32];

    memset(&m, 0, sizeof m);
    m.dwLength = sizeof m;
    if (!GlobalMemoryStatusEx(&m)) { say("Windows' view %s: GlobalMemoryStatusEx failed (%lu)", when, GetLastError()); return; }
    fmt_mb(t, sizeof t, m.ullTotalPhys >> 20);
    fmt_mb(a, sizeof a, m.ullAvailPhys >> 20);
    fmt_mb(pt, sizeof pt, m.ullTotalPageFile >> 20);
    fmt_mb(pa, sizeof pa, m.ullAvailPageFile >> 20);
    fmt_mb(vt, sizeof vt, m.ullTotalVirtual >> 20);
    fmt_mb(va, sizeof va, m.ullAvailVirtual >> 20);
    say("Windows' view %s: RAM %s (free %s), commit limit %s (free %s), address space %s (free %s), load %lu%%",
        when, t, a, pt, pa, vt, va, (unsigned long)m.dwMemoryLoad);
}

static void sample_line(const char *tag)
{
    char fp[32], av[32];
    if (!g_host.have_live) return;
    fmt_mb(fp, sizeof fp, g_host.footprint_mb);
    fmt_mb(av, sizeof av, g_host.available_mb);
    say("  %s: iOS charges this app %s, says %s left%s", tag, fp, g_host.available_mb ? av : "(no number)",
        g_host.swap_used_mb ? " (the swap file holds more)" : "");
}

#define MAX_CHUNKS 512

typedef struct {
    void *chunks[MAX_CHUNKS];
    u64 seeds[MAX_CHUNKS];
    int n;
    u64 chunk_mb, last_seq, next_report, limit_mb;
    double t0;
    DWORD err;
    ram_state *st;
} ram_world;

static int ram_take(void *ctx, u64 chunk_mb)
{
    ram_world *w = (ram_world *)ctx;
    void *p;

    if (w->n >= MAX_CHUNKS) return 0;
    p = VirtualAlloc(NULL, (SIZE_T)chunk_mb << 20, MEM_RESERVE | MEM_COMMIT, PAGE_READWRITE);
    if (!p) { w->err = GetLastError(); return 0; }
    w->chunks[w->n] = p;
    w->seeds[w->n] = 0x9E3779B97F4A7C15ull * (u64)(w->n + 1);
    fill_random(p, (size_t)chunk_mb << 20, w->seeds[w->n]);
    w->n++;
    return 1;
}

/* Two newer samples: the first may have been taken before the last piece was touched. The engine writes one every 250 ms once the
 * app holds more than 2.4 GB and every two seconds (plus its own work, up to half a second) below, so nine seconds is a timeout. */
static int ram_sample(void *ctx, u64 *avail)
{
    ram_world *w = (ram_world *)ctx;
    if (!host_wait_seq(w->last_seq + 2, 9000)) return 0;
    w->last_seq = g_host.seq;
    *avail = ram_base_avail(g_host.limit_mb, g_host.footprint_mb, g_host.available_mb);
    return 1;
}

static int ram_aborted(void *ctx) { (void)ctx; return g_abort != 0; }

static void ram_progress(void *ctx, u64 got_mb)
{
    ram_world *w = (ram_world *)ctx;

    if (got_mb < w->next_report) return;
    w->next_report += 512;
    if (g_host.have_live) {
        char fp[32], av[32];
        host_read_live();
        fmt_mb(fp, sizeof fp, g_host.footprint_mb);
        fmt_mb(av, sizeof av, g_host.available_mb);
        say("  %5llu MB taken after %.1f s (iOS charges %s%s%s)", got_mb, (now_ms() - w->t0) / 1000.0, fp,
            g_host.available_mb ? ", left " : "", g_host.available_mb ? av : "");
    } else
        say("  %5llu MB taken after %.1f s", got_mb, (now_ms() - w->t0) / 1000.0);
}

/* The RAM test. `target_mb` is what was asked for. Returns the verdict. */
static int ram_run(const char *name, u64 target_mb)
{
    static ram_world w;                  /* 4 KB of pointers and seeds: not on the stack of a thread */
    MEMORYSTATUSEX m;
    ram_state st;
    ram_io io;
    int i, end;
    u64 got_mb, head = 0, mismatches = 0;
    double t_alloc, t_verify = 0;
    char b1[32], b2[32], b3[32], swapnote[96] = "";
    int blind;

    memset(&w, 0, sizeof w);
    host_read_info();
    host_lines();
    windows_memory_lines("before");
    memset(&m, 0, sizeof m);
    m.dwLength = sizeof m;
    GlobalMemoryStatusEx(&m);

    memset(&st, 0, sizeof st);
    st.target_mb = target_mb;
    st.chunk_mb = BITS == 64 ? 64 : 32;
    w.limit_mb = g_host.limit_mb ? g_host.limit_mb : (m.ullTotalPhys >> 20);
    st.margin_mb = ram_margin_mb(w.limit_mb);
    st.blind_cap_mb = (m.ullTotalPhys >> 20) / 4;          /* without a number from the app: a quarter of what Windows thinks the RAM is */
    if (st.blind_cap_mb > 2048) st.blind_cap_mb = 2048;
    if (st.blind_cap_mb > target_mb) st.blind_cap_mb = target_mb;
    w.next_report = 512;
    w.st = &st;

    if (host_read_live()) {
        st.base_avail_mb = ram_base_avail(g_host.limit_mb, g_host.footprint_mb, g_host.available_mb);
        st.fresh = 1;
        w.last_seq = g_host.seq;
        sample_line("at the start");
    }
    blind = st.base_avail_mb == 0;
    fmt_mb(b1, sizeof b1, target_mb);
    fmt_mb(b2, sizeof b2, st.margin_mb);
    if (blind)
        say("No usable numbers from the app: staying under %llu MB to be safe (%s asked). Start this test from the Windows desktop "
            "session to get the real figure.", st.blind_cap_mb, b1);
    else
        say("Plan: take %s in %llu MB steps; stop %s before iOS's limit (the app needs that much itself).", b1, st.chunk_mb, b2);

    memset(&io, 0, sizeof io);
    io.ctx = &w;
    io.take = ram_take;
    io.sample = ram_sample;
    io.aborted = ram_aborted;
    io.progress = ram_progress;
    w.t0 = now_ms();
    end = ram_drive(&st, &io, &head);
    t_alloc = now_ms() - w.t0;
    got_mb = st.got_mb;
    if (end == RAM_END_STALE) say("  The app's numbers stopped updating; stopping here to be safe.");

    if (w.n && end != RAM_END_ABORT) {                     /* the memory must hold what was written */
        double v0 = now_ms();
        for (i = 0; i < w.n; i++) mismatches += check_random(w.chunks[i], (size_t)st.chunk_mb << 20, w.seeds[i]);
        t_verify = now_ms() - v0;
    }
    if (g_host.have_live) {
        host_wait_seq(w.last_seq + 1, 4500);
        sample_line("at the end");
        if (g_host.swap_used_mb > 0)
            snprintf(swapnote, sizeof swapnote, "; %llu MB of it sit in the swap file on disk, not in RAM", g_host.swap_used_mb);
    }
    windows_memory_lines("with the memory held");
    for (i = 0; i < w.n; i++) VirtualFree(w.chunks[i], 0, MEM_RELEASE);
    if (g_host.have_live && w.n) {
        host_wait_seq(g_host.seq + 2, 9000);
        sample_line("after giving it back");
    }

    fmt_mb(b1, sizeof b1, got_mb);
    fmt_mb(b2, sizeof b2, target_mb);
    say("Took %s of %s in %.1f s (%.0f MB/s writing), read back in %.1f s (%.0f MB/s), %llu words wrong.", b1, b2, t_alloc / 1000.0,
        mb_per_s(got_mb << 20, t_alloc), t_verify / 1000.0, mb_per_s(got_mb << 20, t_verify), mismatches);

    if (end == RAM_END_ABORT) return finish(name, V_SKIP, "closed while running; %s taken", b1);
    if (mismatches) return finish(name, V_FAIL, "%s taken but %llu words read back wrong: the memory does not hold its content", b1, mismatches);
    if (end == RAM_END_ALLOC_FAIL) {
        fmt_mb(b3, sizeof b3, head);
        return finish(name, V_FAIL, "Windows refused memory at %s (error %lu) although iOS had %s of headroom left", b1, (unsigned long)w.err, b3);
    }
    if (end == RAM_END_LIMIT || end == RAM_END_STALE) {
        char lim[32];
        fmt_mb(lim, sizeof lim, g_host.limit_mb);
        return finish(name, V_LIMIT, "your device gave %s of the %s asked; stopped on purpose before iOS would close BCD%s%s%s%s", b1, b2,
                      g_host.limit_mb ? " (iOS limit about " : "", g_host.limit_mb ? lim : "", g_host.limit_mb ? ")" : "", swapnote);
    }
    return finish(name, V_PASS, "%s taken and read back correctly in %.1f s%s", b1, (t_alloc + t_verify) / 1000.0, swapnote);
}

static int test_ram(u64 gb)
{
    char title[64], name[48];
    u64 mb = gb * 1024;

    if (gb == 0 || gb > 32) { say("RAM test: the size must be 1 to 32 (GB)"); return V_FAIL; }   /* MAX_CHUNKS pieces of 64 MB */
    snprintf(title, sizeof title, "RAM test %llu GB", gb);
    header(title);
    snprintf(name, sizeof name, "ram %llu GB", gb);
    return ram_run(name, mb);
}

/* A quick RAM test for "all": 1 GB. */
static int test_ram_quick(void)
{
    header("RAM test 1 GB (quick)");
    return ram_run("ram 1 GB", 1024);
}

/* The 32-bit window: reserves address space until Windows says no, then takes real memory inside it as far as is safe. */
static int test_ram32(void)
{
#ifdef _WIN64
    header("RAM test (32-bit)");
    say("This is the 64-bit build. The 32-bit window needs bcd-systest-x86.exe (Start > Programs > BCD Tests > RAM test (32-bit)).");
    return finish("ram32", V_SKIP, "64-bit build; start bcd-systest-x86.exe");
#else
    MEMORYSTATUSEX m;
    void *res[128];
    int nres = 0, i;
    u64 reserved_mb = 0, chunk_mb = 32, largest_mb = 0;
    BOOL wow = FALSE;
    char t[32], a[32];
    int verdict;

    header("RAM test (32-bit)");
    host_read_info();
    host_lines();
    {
        typedef BOOL (WINAPI *w64_fn)(HANDLE, BOOL *);
        w64_fn f = (w64_fn)GetProcAddress(GetModuleHandleA("kernel32.dll"), "IsWow64Process");
        if (f) f(GetCurrentProcess(), &wow);
    }
    memset(&m, 0, sizeof m);
    m.dwLength = sizeof m;
    GlobalMemoryStatusEx(&m);
    say("This program is a 32-bit process%s.", wow ? " running under WoW64 (like a 32-bit game)" : "");
    windows_memory_lines("before");
    /* the largest single block, found by halving */
    for (largest_mb = 3584; largest_mb >= 16; largest_mb /= 2) {
        void *p = VirtualAlloc(NULL, (SIZE_T)largest_mb << 20, MEM_RESERVE, PAGE_NOACCESS);
        if (p) { VirtualFree(p, 0, MEM_RELEASE); break; }
    }
    if (largest_mb < 16) largest_mb = 0;
    /* reserve 32 MB at a time, keeping 160 MB of the window for this program itself */
    for (;;) {
        MEMORYSTATUSEX now;
        if (nres >= 128) break;
        memset(&now, 0, sizeof now);
        now.dwLength = sizeof now;
        GlobalMemoryStatusEx(&now);
        if ((now.ullAvailVirtual >> 20) < 160 + chunk_mb) break;
        res[nres] = VirtualAlloc(NULL, (SIZE_T)chunk_mb << 20, MEM_RESERVE, PAGE_READWRITE);
        if (!res[nres]) break;
        nres++;
        reserved_mb += chunk_mb;
    }
    fmt_mb(t, sizeof t, reserved_mb);
    fmt_mb(a, sizeof a, m.ullTotalVirtual >> 20);
    say("Address space: Windows says %s in all; %s could be reserved in %llu MB pieces (the rest is kept for this program).", a, t, chunk_mb);
    say("A single block of %llu MB or more could be reserved before the pieces (large-address-aware program).", largest_mb);
    for (i = 0; i < nres; i++) VirtualFree(res[i], 0, MEM_RELEASE);

    /* real memory inside the window, as far as the planner allows */
    {
        u64 take = reserved_mb > 256 ? reserved_mb - 128 : reserved_mb;
        int v2;
        say("Now taking real memory inside the window (up to %llu MB), the same way as the RAM test.", take);
        v2 = ram_run("ram32 memory", take);
        verdict = v2 == V_PASS || v2 == V_LIMIT ? V_PASS : v2;
    }
    if (verdict == V_PASS && reserved_mb < 3000)
        return finish("ram32", V_WARN, "only %s of address space for a 32-bit program (a full window is about 3.8 GB)", t);
    if (verdict != V_PASS) return finish("ram32", verdict, "the memory inside the 32-bit window failed (see above)");
    return finish("ram32", V_PASS, "%s of address space for a 32-bit program, largest block %llu MB", t, largest_mb);
#endif
}

/* ================================================================== the JIT pool */

static int parse_hex_pair(const char *s, u64 *a, u64 *b)
{
    char *end;
    *a = strtoull(s, &end, 16);
    if (*end != ':') return 0;
    *b = strtoull(end + 1, &end, 16);
    return 1;
}

static int test_jit(void)
{
    char env[128], x[32], y[32], z[32];
    u64 span = 0, h1o = 0, h1e = 0, h2o = 0, h2e = 0, c_rx = 0, c_size = 0, rx = 0;
    u64 region_mb[3] = { 0, 0, 0 };
    int regions, have_hole1 = 0, have_hole2 = 0;
    u64 usable_kb;

    header("JIT pool test");
    host_read_info();
    host_read_live();
    host_lines();
    if (!env_get("WINE_IOS_JIT_SIZE", env, sizeof env)) {
        say("This session has no JIT pool in its environment (WINE_IOS_JIT_SIZE is not set).");
        return finish("jit", V_FAIL, "no JIT pool is visible to Windows programs in this session");
    }
    span = strtoull(env, NULL, 16);
    if (env_get("WINE_IOS_JIT_RX", env, sizeof env)) rx = strtoull(env, NULL, 16);
    if (env_get("WINE_IOS_JIT_HOLE", env, sizeof env)) have_hole1 = parse_hex_pair(env, &h1o, &h1e);
    if (env_get("WINE_IOS_JIT_HOLE2", env, sizeof env)) have_hole2 = parse_hex_pair(env, &h2o, &h2e);
    if (env_get("WINE_IOS_JIT_TAIL_REGION", env, sizeof env)) parse_hex_pair(env, &c_rx, &c_size);

    if (have_hole1 && h1e > h1o && h1e <= span) {
        region_mb[0] = h1o >> 20;
        if (have_hole2 && h2e > h2o && h2o >= h1e && h2e <= span) {
            region_mb[1] = (h2o - h1e) >> 20;
            region_mb[2] = (span - h2e) >> 20;
            regions = 3;
        } else {
            region_mb[1] = (span - h1e) >> 20;
            regions = 2;
        }
    } else {
        regions = 1;
        region_mb[0] = span >> 20;
    }
    fmt_mb(x, sizeof x, span >> 20);
    say("Pool span: %s at 0x%llx", x, rx);
    if (regions == 1) say("Layout: one region of %llu MB", region_mb[0]);
    else if (regions == 2) say("Layout: 2 regions of %llu MB and %llu MB with a hole of %llu MB between them that is not pool memory",
                               region_mb[0], region_mb[1], (h1e - h1o) >> 20);
    else say("Layout: 3 regions of %llu, %llu and %llu MB; holes of %llu MB and %llu MB between them are not pool memory",
             region_mb[0], region_mb[1], region_mb[2], (h1e - h1o) >> 20, (h2e - h2o) >> 20);
    if (c_size) {
        fmt_mb(y, sizeof y, c_size >> 20);
        say("Region C: %s below the pool for the emulator's code buffers (pool-low)", y);
    }
    if (g_host.pool_cfg_mb) {
        fmt_mb(z, sizeof z, g_host.pool_cfg_mb);
        say("Asked for in the settings (pool): %s", z);
    }
    say("Why the pool differs from one session to the next: it is cut out of the free address space when the session starts, and its size\n"
        "follows where the app's own stack and libraries landed this time. When no single hole fits the size asked for, the pool is made\n"
        "of smaller pieces (above) or is smaller. It does not shrink while a session runs; what runs down is the part that is still free.");

    if (!g_host.have_pool) {
        say("How full: the app gives no live numbers in this session, so only the size is shown.");
        return finish("jit", V_WARN, "pool span %s, no live fill figures (started outside the Windows desktop session?)", x);
    }
    usable_kb = g_host.pool_total_kb > g_host.pool_hole_kb ? g_host.pool_total_kb - g_host.pool_hole_kb : g_host.pool_total_kb;
    {
        u64 images = g_host.pool_head_kb > g_host.pool_free_kb ? g_host.pool_head_kb - g_host.pool_free_kb : 0;
        u64 free_total = g_host.pool_virgin_kb + g_host.pool_free_kb;
        u64 free_pct = usable_kb ? free_total * 100 / usable_kb : 0;
        char a1[32], a2[32], a3[32], a4[32], a5[32];

        fmt_kb(a1, sizeof a1, usable_kb);
        fmt_kb(a2, sizeof a2, images);
        fmt_kb(a3, sizeof a3, g_host.pool_tail_kb);
        fmt_kb(a4, sizeof a4, g_host.pool_virgin_kb);
        fmt_kb(a5, sizeof a5, g_host.pool_free_kb);
        say("Usable pool memory: %s", a1);
        say("  program and DLL copies: %s", a2);
        say("  emulator code buffers:  %s", a3);
        say("  never used:             %s", a4);
        say("  given back, reusable:   %s", a5);
        if (g_host.poolc_total_kb) {
            fmt_kb(a1, sizeof a1, g_host.poolc_used_kb);
            fmt_kb(a2, sizeof a2, g_host.poolc_total_kb);
            say("  region C: %s of %s in use", a1, a2);
        }
        say("The pool is %llu%% full (%llu%% free).", 100 - free_pct, free_pct);
        fmt_kb(a1, sizeof a1, free_total);
        if (free_pct < 5) return finish("jit", V_FAIL, "pool almost full: %s of %s left (%llu%%); a program that loads more DLLs will fail with EXHAUSTED", a1, x, free_pct);
        if (free_pct < 15) return finish("jit", V_WARN, "pool getting full: %s left (%llu%%) of a %s span", a1, free_pct, x);
        return finish("jit", V_PASS, "pool span %s, %s left (%llu%% free)", x, a1, free_pct);
    }
}

/* ================================================================== system information */

static const char *machine_name(WORD m)
{
    switch (m) {
    case 0x8664: return "x86-64";
    case 0xAA64: return "ARM64";
    case 0x01c4: return "ARM";
    case 0x014c: return "x86 (32-bit)";
    case 0: return "unknown";
    default: return "other";
    }
}

static int reg_string(HKEY root, const char *sub, const char *name, char *out, size_t cap, REGSAM view)
{
    HKEY k;
    DWORD type = 0, size = (DWORD)cap - 1;
    LONG r = RegOpenKeyExA(root, sub, 0, KEY_READ | view, &k);
    out[0] = 0;
    if (r != ERROR_SUCCESS) return 0;
    r = RegQueryValueExA(k, name, NULL, &type, (BYTE *)out, &size);
    RegCloseKey(k);
    if (r != ERROR_SUCCESS || (type != REG_SZ && type != REG_EXPAND_SZ)) { out[0] = 0; return 0; }
    out[size < cap ? size : cap - 1] = 0;
    return 1;
}

static int reg_dword(HKEY root, const char *sub, const char *name, DWORD *out, REGSAM view)
{
    HKEY k;
    DWORD type = 0, size = sizeof *out;
    LONG r = RegOpenKeyExA(root, sub, 0, KEY_READ | view, &k);
    if (r != ERROR_SUCCESS) return 0;
    r = RegQueryValueExA(k, name, NULL, &type, (BYTE *)out, &size);
    RegCloseKey(k);
    return r == ERROR_SUCCESS && type == REG_DWORD;
}

static void windows_lines(void)
{
    typedef LONG (WINAPI *rtlver_fn)(OSVERSIONINFOEXW *);
    typedef const char *(CDECL *winever_fn)(void);
    typedef BOOL (WINAPI *w64_2_fn)(HANDLE, USHORT *, USHORT *);
    HMODULE nt = GetModuleHandleA("ntdll.dll");
    rtlver_fn rv = nt ? (rtlver_fn)GetProcAddress(nt, "RtlGetVersion") : NULL;
    winever_fn wv = nt ? (winever_fn)GetProcAddress(nt, "wine_get_version") : NULL;
    w64_2_fn w2 = (w64_2_fn)GetProcAddress(GetModuleHandleA("kernel32.dll"), "IsWow64Process2");
    OSVERSIONINFOEXW os;
    char prod[96], build[32], disp[32];
    SYSTEM_INFO si;
    USHORT pm = 0, nm = 0;

    memset(&os, 0, sizeof os);
    os.dwOSVersionInfoSize = sizeof os;
    if (rv && rv(&os) == 0)
        say("Windows as a game sees it: %lu.%lu build %lu%s", (unsigned long)os.dwMajorVersion, (unsigned long)os.dwMinorVersion,
            (unsigned long)os.dwBuildNumber, os.wProductType == VER_NT_WORKSTATION ? " (workstation)" : "");
    prod[0] = build[0] = disp[0] = 0;
    reg_string(HKEY_LOCAL_MACHINE, "SOFTWARE\\Microsoft\\Windows NT\\CurrentVersion", "ProductName", prod, sizeof prod, 0);
    reg_string(HKEY_LOCAL_MACHINE, "SOFTWARE\\Microsoft\\Windows NT\\CurrentVersion", "CurrentBuild", build, sizeof build, 0);
    reg_string(HKEY_LOCAL_MACHINE, "SOFTWARE\\Microsoft\\Windows NT\\CurrentVersion", "DisplayVersion", disp, sizeof disp, 0);
    if (prod[0]) say("Registry: %s, build %s%s%s", prod, build[0] ? build : "?", disp[0] ? ", " : "", disp);
    if (wv) say("Wine: %s", wv());
    memset(&si, 0, sizeof si);
    GetNativeSystemInfo(&si);
    say("Native processor: %s, %lu logical processors, page size %lu, allocation granularity %lu",
        si.wProcessorArchitecture == PROCESSOR_ARCHITECTURE_AMD64 ? "x86-64" :
        si.wProcessorArchitecture == PROCESSOR_ARCHITECTURE_ARM64 ? "ARM64" :
        si.wProcessorArchitecture == PROCESSOR_ARCHITECTURE_INTEL ? "x86" : "other",
        (unsigned long)si.dwNumberOfProcessors, (unsigned long)si.dwPageSize, (unsigned long)si.dwAllocationGranularity);
    if (w2 && w2(GetCurrentProcess(), &pm, &nm))
        say("This program: %s on a %s system (%d-bit build)", pm ? machine_name(pm) : machine_name(nm), machine_name(nm), BITS);
}

static void display_lines(void)
{
    DEVMODEA dm;
    memset(&dm, 0, sizeof dm);
    dm.dmSize = sizeof dm;
    if (EnumDisplaySettingsA(NULL, ENUM_CURRENT_SETTINGS, &dm))
        say("Display: %lux%lu, %lu bits, %lu Hz; virtual screen %dx%d", (unsigned long)dm.dmPelsWidth, (unsigned long)dm.dmPelsHeight,
            (unsigned long)dm.dmBitsPerPel, (unsigned long)dm.dmDisplayFrequency, GetSystemMetrics(SM_CXSCREEN), GetSystemMetrics(SM_CYSCREEN));
    else
        say("Display: EnumDisplaySettings failed (%lu)", GetLastError());
}

static const char *fl_name(D3D_FEATURE_LEVEL f)
{
    switch ((int)f) {
    case 0xc200: return "12_2";
    case 0xc100: return "12_1";
    case 0xc000: return "12_0";
    case 0xb100: return "11_1";
    case 0xb000: return "11_0";
    case 0xa100: return "10_1";
    case 0xa000: return "10_0";
    case 0x9300: return "9_3";
    case 0x9200: return "9_2";
    case 0x9100: return "9_1";
    default: return "?";
    }
}

static const char *sm_name(UINT sm)
{
    static char buf[16];
    snprintf(buf, sizeof buf, "%u.%u", (sm >> 4) & 15u, sm & 15u);
    return buf;
}

/* DXGI, Direct3D 11 and 12. Returns the number of problems (0 = all fine). Each step is logged BEFORE it is tried. */
static int gpu_lines(void)
{
    typedef HRESULT (WINAPI *mkfactory_fn)(REFIID, void **);
    typedef HRESULT (WINAPI *d3d11_fn)(IDXGIAdapter *, D3D_DRIVER_TYPE, HMODULE, UINT, const D3D_FEATURE_LEVEL *, UINT, UINT,
                                       ID3D11Device **, D3D_FEATURE_LEVEL *, ID3D11DeviceContext **);
    typedef HRESULT (WINAPI *d3d12_fn)(IUnknown *, D3D_FEATURE_LEVEL, REFIID, void **);
    static const D3D_FEATURE_LEVEL levels11[] = { D3D_FEATURE_LEVEL_12_1, D3D_FEATURE_LEVEL_12_0, D3D_FEATURE_LEVEL_11_1,
                                                  D3D_FEATURE_LEVEL_11_0, D3D_FEATURE_LEVEL_10_1, D3D_FEATURE_LEVEL_10_0 };
    static const D3D_FEATURE_LEVEL levels12[] = { D3D_FEATURE_LEVEL_12_2, D3D_FEATURE_LEVEL_12_1, D3D_FEATURE_LEVEL_12_0,
                                                  D3D_FEATURE_LEVEL_11_1, D3D_FEATURE_LEVEL_11_0 };
    int problems = 0;
    HMODULE dxgi, d11, d12;
    IDXGIFactory1 *factory = NULL;
    IDXGIAdapter1 *adapter = NULL;
    DXGI_ADAPTER_DESC1 desc;
    char name[160], v1[32], v2[32], v3[32];
    HRESULT hr;

    say("Graphics: loading dxgi.dll");
    dxgi = LoadLibraryA("dxgi.dll");
    mkfactory_fn mk = dxgi ? (mkfactory_fn)GetProcAddress(dxgi, "CreateDXGIFactory1") : NULL;
    if (!mk) { say("DXGI: dxgi.dll or CreateDXGIFactory1 is missing"); return 1; }
    say("DXGI: CreateDXGIFactory1");
    hr = mk(&IID_IDXGIFactory1, (void **)&factory);
    if (FAILED(hr) || !factory) { say("DXGI: no factory (0x%08lx)", (unsigned long)hr); return 1; }
    say("DXGI: EnumAdapters1");
    hr = IDXGIFactory1_EnumAdapters1(factory, 0, &adapter);
    if (FAILED(hr) || !adapter) { say("DXGI: no graphics adapter (0x%08lx)", (unsigned long)hr); IDXGIFactory1_Release(factory); return 1; }
    memset(&desc, 0, sizeof desc);
    IDXGIAdapter1_GetDesc1(adapter, &desc);
    wide_to_ascii(desc.Description, name, sizeof name);
    fmt_mb(v1, sizeof v1, desc.DedicatedVideoMemory >> 20);
    fmt_mb(v2, sizeof v2, desc.SharedSystemMemory >> 20);
    say("GPU: %s", name);
    say("  vendor 0x%04x device 0x%04x, dedicated video memory %s, shared system memory %s", desc.VendorId, desc.DeviceId, v1, v2);
    {
        IDXGIAdapter3 *a3 = NULL;
        say("DXGI: QueryVideoMemoryInfo");
        if (SUCCEEDED(IDXGIAdapter1_QueryInterface(adapter, &IID_IDXGIAdapter3, (void **)&a3)) && a3) {
            DXGI_QUERY_VIDEO_MEMORY_INFO li, ni;
            memset(&li, 0, sizeof li);
            memset(&ni, 0, sizeof ni);
            if (SUCCEEDED(IDXGIAdapter3_QueryVideoMemoryInfo(a3, 0, DXGI_MEMORY_SEGMENT_GROUP_LOCAL, &li))) {
                fmt_mb(v1, sizeof v1, li.Budget >> 20);
                fmt_mb(v2, sizeof v2, li.CurrentUsage >> 20);
                fmt_mb(v3, sizeof v3, li.AvailableForReservation >> 20);
                say("  video memory budget (local): %s, in use %s, available for reservation %s", v1, v2, v3);
            } else { say("  QueryVideoMemoryInfo (local) failed"); problems++; }
            if (SUCCEEDED(IDXGIAdapter3_QueryVideoMemoryInfo(a3, 0, DXGI_MEMORY_SEGMENT_GROUP_NON_LOCAL, &ni))) {
                fmt_mb(v1, sizeof v1, ni.Budget >> 20);
                fmt_mb(v2, sizeof v2, ni.CurrentUsage >> 20);
                say("  video memory budget (non-local): %s, in use %s", v1, v2);
            }
            IDXGIAdapter3_Release(a3);
        } else say("  IDXGIAdapter3 (QueryVideoMemoryInfo) is not offered by this DXGI");
    }

    d11 = LoadLibraryA("d3d11.dll");
    d3d11_fn mk11 = d11 ? (d3d11_fn)GetProcAddress(d11, "D3D11CreateDevice") : NULL;
    if (!mk11) { say("Direct3D 11: d3d11.dll is missing"); problems++; }
    else {
        ID3D11Device *dev = NULL;
        ID3D11DeviceContext *ctx = NULL;
        D3D_FEATURE_LEVEL got = 0;
        say("Direct3D 11: D3D11CreateDevice");
        hr = mk11((IDXGIAdapter *)adapter, D3D_DRIVER_TYPE_UNKNOWN, NULL, 0, levels11, (UINT)(sizeof levels11 / sizeof levels11[0]),
                  D3D11_SDK_VERSION, &dev, &got, &ctx);
        if (hr == E_INVALIDARG)            /* a runtime without 11_1 and 12_x levels */
            hr = mk11((IDXGIAdapter *)adapter, D3D_DRIVER_TYPE_UNKNOWN, NULL, 0, levels11 + 3, 3, D3D11_SDK_VERSION, &dev, &got, &ctx);
        if (FAILED(hr) || !dev) { say("Direct3D 11: no device (0x%08lx)", (unsigned long)hr); problems++; }
        else {
            say("Direct3D 11: highest feature level %s (0x%04x)", fl_name(got), (unsigned)got);
            {
                D3D11_FEATURE_DATA_THREADING th;
                memset(&th, 0, sizeof th);
                if (SUCCEEDED(ID3D11Device_CheckFeatureSupport(dev, D3D11_FEATURE_THREADING, &th, sizeof th)))
                    say("  threading: concurrent creates %s, command lists %s", th.DriverConcurrentCreates ? "yes" : "no",
                        th.DriverCommandLists ? "yes" : "no");
            }
            if (ctx) ID3D11DeviceContext_Release(ctx);
            ID3D11Device_Release(dev);
        }
    }

    d12 = LoadLibraryA("d3d12.dll");
    d3d12_fn mk12 = d12 ? (d3d12_fn)GetProcAddress(d12, "D3D12CreateDevice") : NULL;
    if (!mk12) { say("Direct3D 12: d3d12.dll is missing"); problems++; }
    else {
        ID3D12Device *dev = NULL;
        say("Direct3D 12: D3D12CreateDevice (minimum level 11_0)");
        hr = mk12((IUnknown *)adapter, D3D_FEATURE_LEVEL_11_0, &IID_ID3D12Device, (void **)&dev);
        if (FAILED(hr) || !dev) { say("Direct3D 12: no device (0x%08lx)", (unsigned long)hr); problems++; }
        else {
            D3D12_FEATURE_DATA_FEATURE_LEVELS fl;
            D3D12_FEATURE_DATA_SHADER_MODEL sm;
            D3D12_FEATURE_DATA_D3D12_OPTIONS o;
            D3D12_FEATURE_DATA_D3D12_OPTIONS5 o5;
            UINT want;

            memset(&fl, 0, sizeof fl);
            fl.NumFeatureLevels = (UINT)(sizeof levels12 / sizeof levels12[0]);
            fl.pFeatureLevelsRequested = levels12;
            say("Direct3D 12: CheckFeatureSupport (feature levels)");
            hr = ID3D12Device_CheckFeatureSupport(dev, D3D12_FEATURE_FEATURE_LEVELS, &fl, sizeof fl);
            if (SUCCEEDED(hr)) say("Direct3D 12: feature level %s (0x%04x)", fl_name(fl.MaxSupportedFeatureLevel), (unsigned)fl.MaxSupportedFeatureLevel);
            else say("Direct3D 12: feature levels not answered (0x%08lx)", (unsigned long)hr);

            say("Direct3D 12: CheckFeatureSupport (shader model)");
            for (want = 0x67; want >= 0x60; want--) {
                memset(&sm, 0, sizeof sm);
                sm.HighestShaderModel = (D3D_SHADER_MODEL)want;
                hr = ID3D12Device_CheckFeatureSupport(dev, D3D12_FEATURE_SHADER_MODEL, &sm, sizeof sm);
                if (SUCCEEDED(hr)) { say("Direct3D 12: highest shader model %s", sm_name((UINT)sm.HighestShaderModel)); break; }
                if (hr != E_INVALIDARG) break;
            }
            if (FAILED(hr)) say("Direct3D 12: shader model not answered (0x%08lx)", (unsigned long)hr);

            say("Direct3D 12: CheckFeatureSupport (options)");
            memset(&o, 0, sizeof o);
            if (SUCCEEDED(ID3D12Device_CheckFeatureSupport(dev, D3D12_FEATURE_D3D12_OPTIONS, &o, sizeof o)))
                say("  resource binding tier %d, tiled resources tier %d, heap tier %d, conservative rasterization tier %d",
                    (int)o.ResourceBindingTier, (int)o.TiledResourcesTier, (int)o.ResourceHeapTier, (int)o.ConservativeRasterizationTier);
            memset(&o5, 0, sizeof o5);
            if (SUCCEEDED(ID3D12Device_CheckFeatureSupport(dev, D3D12_FEATURE_D3D12_OPTIONS5, &o5, sizeof o5)))
                say("  ray tracing tier %d", (int)o5.RaytracingTier);
            ID3D12Device_Release(dev);
        }
    }
    IDXGIAdapter1_Release(adapter);
    IDXGIFactory1_Release(factory);
    return problems;
}

static int test_sysinfo(int with_gpu)
{
    MEMORYSTATUSEX m;
    cpu_info c;
    char a[32];
    int problems = 0;

    header("System info");
    host_read_info();
    host_read_live();
    host_lines();
    windows_lines();
    cpu_collect(&c);
    say("CPU: %s (%s)", c.brand[0] ? c.brand : "no name", c.vendor);
    memset(&m, 0, sizeof m);
    m.dwLength = sizeof m;
    if (GlobalMemoryStatusEx(&m)) {
        fmt_mb(a, sizeof a, m.ullTotalPhys >> 20);
        say("RAM as Windows reports it: %s", a);
    }
    windows_memory_lines("now");
    display_lines();
    if (!with_gpu) return finish("sysinfo", V_PASS, "system, memory and display shown (graphics skipped)");
    problems = gpu_lines();
    if (problems) return finish("sysinfo", V_FAIL, "%d graphics problem(s), see above", problems);
    return finish("sysinfo", V_PASS, "GPU, video memory budget, Direct3D 11 and 12 levels read");
}

/* ================================================================== Visual C++ and .NET */

/* "major.minor.build.rev" and the company of a file's version resource; 0 when it has none. */
static int file_version(const char *path, char *ver, size_t vcap, char *company, size_t ccap)
{
    typedef DWORD (WINAPI *size_fn)(LPCSTR, LPDWORD);
    typedef BOOL (WINAPI *get_fn)(LPCSTR, DWORD, DWORD, LPVOID);
    typedef BOOL (WINAPI *query_fn)(LPCVOID, LPCSTR, LPVOID *, PUINT);
    static HMODULE vdll;
    static size_fn fsize;
    static get_fn fget;
    static query_fn fquery;
    DWORD dummy = 0, size;
    BYTE *data;
    VS_FIXEDFILEINFO *ffi = NULL;
    UINT len = 0;
    int ok = 0;

    ver[0] = company[0] = 0;
    if (!vdll) {
        vdll = LoadLibraryA("version.dll");
        if (vdll) {
            fsize = (size_fn)GetProcAddress(vdll, "GetFileVersionInfoSizeA");
            fget = (get_fn)GetProcAddress(vdll, "GetFileVersionInfoA");
            fquery = (query_fn)GetProcAddress(vdll, "VerQueryValueA");
        }
    }
    if (!fsize || !fget || !fquery) return 0;
    size = fsize(path, &dummy);
    if (!size || size > (1u << 20)) return 0;
    data = (BYTE *)malloc(size);
    if (!data) return 0;
    if (fget(path, 0, size, data) && fquery(data, "\\", (LPVOID *)&ffi, &len) && ffi && len >= sizeof *ffi) {
        char *co = NULL;
        UINT clen = 0;
        snprintf(ver, vcap, "%u.%u.%u.%u", (unsigned)HIWORD(ffi->dwFileVersionMS), (unsigned)LOWORD(ffi->dwFileVersionMS),
                 (unsigned)HIWORD(ffi->dwFileVersionLS), (unsigned)LOWORD(ffi->dwFileVersionLS));
        ok = 1;
        if (fquery(data, "\\StringFileInfo\\040904b0\\CompanyName", (LPVOID *)&co, &clen) && co) snprintf(company, ccap, "%s", co);
        else if (fquery(data, "\\StringFileInfo\\000004b0\\CompanyName", (LPVOID *)&co, &clen) && co) snprintf(company, ccap, "%s", co);
    }
    free(data);
    return ok;
}

/* Wine's own DLLs carry "Wine builtin DLL" in the DOS header's stub. */
static int is_wine_builtin(const char *path)
{
    char head[512];
    int n = read_text_file(path, head, sizeof head), i;
    for (i = 0; i + 16 < n; i++)
        if (!memcmp(head + i, "Wine builtin DLL", 16) || !memcmp(head + i, "Wine placeholder", 16)) return 1;
    return 0;
}

typedef struct { int found; int builtin; char version[32]; char company[64]; } dll_info;

/* Looks for `dll` in `dir`; fills `out`. */
static void dll_probe(const char *dir, const char *dll, dll_info *out)
{
    char path[MAX_PATH];
    memset(out, 0, sizeof *out);
    snprintf(path, sizeof path, "%s\\%s", dir, dll);
    if (!path_exists(path)) return;
    out->found = 1;
    file_version(path, out->version, sizeof out->version, out->company, sizeof out->company);
    out->builtin = is_wine_builtin(path);
}

static const char *dll_origin(const dll_info *d)
{
    if (!d->found) return "missing";
    if (strstr(d->company, "Microsoft")) return "Microsoft's";
    if (d->builtin) return "Wine built-in";
    return "found";
}

static void dll_text(char *out, size_t cap, const dll_info *d, const char *expected_year)
{
    unsigned v[4];
    const char *year = NULL;

    if (!d->found) { snprintf(out, cap, "missing"); return; }
    if (version_parse(d->version, v) >= 1) year = vcrt_year_of_version(v[0]);
    snprintf(out, cap, "%s%s%s%s%s%s", dll_origin(d), d->version[0] ? " " : "", d->version,
             year && strcmp(year, expected_year) ? " (this is the " : "", year && strcmp(year, expected_year) ? year : "",
             year && strcmp(year, expected_year) ? " one)" : "");
}

/* Lists the sub-folders of `dir` into out (comma separated); returns how many. */
static int list_dirs(const char *dir, char *out, size_t cap)
{
    WIN32_FIND_DATAA fd;
    char pat[MAX_PATH];
    HANDLE h;
    int n = 0;

    out[0] = 0;
    snprintf(pat, sizeof pat, "%s\\*", dir);
    h = FindFirstFileA(pat, &fd);
    if (h == INVALID_HANDLE_VALUE) return 0;
    do {
        if (!(fd.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) || fd.cFileName[0] == '.') continue;
        if (strlen(out) + strlen(fd.cFileName) + 3 < cap) {
            if (out[0]) strcat(out, ", ");
            strcat(out, fd.cFileName);
        }
        n++;
    } while (FindNextFileA(h, &fd));
    FindClose(h);
    return n;
}

static int test_vcrt(void)
{
    char sys64[MAX_PATH], sys32[MAX_PATH], t1[96], t2[96], missing[512], dirs[300];
    size_t r;
    int arch, have_2015_x64 = 0, have_2015_x86 = 0, miss_count = 0, nnet = 0;
    int syswow = 0;
    char win[MAX_PATH];
    DWORD dv;
    char sv[96];

    header("VC++ and .NET check");
    GetWindowsDirectoryA(win, sizeof win);
    snprintf(sys64, sizeof sys64, "%s\\system32", win);
    snprintf(sys32, sizeof sys32, "%s\\syswow64", win);
    syswow = dir_exists(sys32);
    missing[0] = 0;
    say("What a game finds: DLLs in %s (64-bit programs)%s, and the registry entries the Redistributable installers set.", sys64,
        syswow ? " and syswow64 (32-bit programs)" : "; there is no syswow64, so 32-bit programs have no set at all");
    for (arch = 0; arch < 2; arch++) {
        const char *dir = arch == 0 ? sys64 : sys32;
        say("");
        say("Visual C++ runtimes, %s:", arch == 0 ? "x64" : "x86");
        if (arch == 1 && !syswow) { say("  (no 32-bit set in this build)"); continue; }
        for (r = 0; r < VCRT_ROW_COUNT; r++) {
            const vcrt_row *row = &vcrt_rows[r];
            dll_info crt, cpp;
            const char *reg = arch == 0 ? row->regkey_x64 : row->regkey_x86;
            char key[200];
            DWORD installed = 0;
            int has_reg = 0;

            dll_probe(dir, row->dll_crt, &crt);
            if (row->dll_cpp) dll_probe(dir, row->dll_cpp, &cpp); else memset(&cpp, 0, sizeof cpp);
            dll_text(t1, sizeof t1, &crt, row->year);
            dll_text(t2, sizeof t2, &cpp, row->year);
            sv[0] = 0;
            if (reg) {
                snprintf(key, sizeof key, "SOFTWARE\\Microsoft\\%s", reg);
                has_reg = reg_dword(HKEY_LOCAL_MACHINE, key, "Installed", &installed, arch == 0 ? KEY_WOW64_64KEY : KEY_WOW64_32KEY) && installed;
                reg_string(HKEY_LOCAL_MACHINE, key, "Version", sv, sizeof sv, arch == 0 ? KEY_WOW64_64KEY : KEY_WOW64_32KEY);
                (void)dv;
            }
            say("  %-9s %s %s | %s %s | registry %s%s%s", row->year, row->dll_crt, t1, row->dll_cpp ? row->dll_cpp : "-", row->dll_cpp ? t2 : "",
                reg ? (has_reg ? "installed " : "not set") : "n/a", has_reg ? sv : "", "");
            if (!strcmp(row->year, "2015-2022")) {
                int ok = crt.found && cpp.found;
                if (arch == 0) have_2015_x64 = ok; else have_2015_x86 = ok;
            }
            if (!crt.found || (row->dll_cpp && !cpp.found)) {
                miss_count++;
                if (strlen(missing) + 24 < sizeof missing)
                    snprintf(missing + strlen(missing), sizeof missing - strlen(missing), "%s%s %s", missing[0] ? ", " : "", row->year,
                             arch == 0 ? "x64" : "x86");
            }
        }
    }
    {
        char ver[32], co[64], mono[MAX_PATH], p[MAX_PATH];
        int has_mscoree64, has_mscoree32, has_mono;
        DWORD rel = 0;
        char nv[64];

        say("");
        say(".NET and Mono:");
        snprintf(p, sizeof p, "%s\\mscoree.dll", sys64);
        has_mscoree64 = path_exists(p);
        snprintf(p, sizeof p, "%s\\mscoree.dll", sys32);
        has_mscoree32 = syswow && path_exists(p);
        say("  mscoree.dll (the .NET loader): x64 %s, x86 %s", has_mscoree64 ? "yes" : "NO", syswow ? (has_mscoree32 ? "yes" : "NO") : "n/a");
        snprintf(mono, sizeof mono, "%s\\mono\\mono-2.0", win);
        has_mono = dir_exists(mono);
        if (has_mono) {
            snprintf(p, sizeof p, "%s\\lib\\mono\\4.5\\mscorlib.dll", mono);
            if (path_exists(p)) {
                file_version(p, ver, sizeof ver, co, sizeof co);
                say("  Wine Mono: installed, mscorlib %s", ver[0] ? ver : "(no version)");
            } else say("  Wine Mono: folder %s exists but has no lib\\mono\\4.5\\mscorlib.dll", mono);
        } else say("  Wine Mono: not installed (%s is missing); .NET Framework games need it or Microsoft's .NET", mono);
        nv[0] = 0;
        if (reg_dword(HKEY_LOCAL_MACHINE, "SOFTWARE\\Microsoft\\NET Framework Setup\\NDP\\v4\\Full", "Release", &rel, KEY_WOW64_64KEY))
            snprintf(nv, sizeof nv, "release %lu", (unsigned long)rel);
        say("  .NET Framework 4.x registry entry: %s", nv[0] ? nv : "none");
        {
            DWORD i35 = 0, i20 = 0;
            int a = reg_dword(HKEY_LOCAL_MACHINE, "SOFTWARE\\Microsoft\\NET Framework Setup\\NDP\\v3.5", "Install", &i35, KEY_WOW64_64KEY);
            int b = reg_dword(HKEY_LOCAL_MACHINE, "SOFTWARE\\Microsoft\\NET Framework Setup\\NDP\\v2.0.50727", "Install", &i20, KEY_WOW64_64KEY);
            say("  .NET Framework 3.5: %s, 2.0: %s", a && i35 ? "yes" : "no", b && i20 ? "yes" : "no");
        }
        snprintf(p, sizeof p, "C:\\Program Files\\dotnet\\shared\\Microsoft.NETCore.App");
        nnet = list_dirs(p, dirs, sizeof dirs);
        if (path_exists("C:\\Program Files\\dotnet\\dotnet.exe") || nnet)
            say("  .NET (Core) runtimes in Program Files\\dotnet: %s", nnet ? dirs : "dotnet.exe only");
        else say("  .NET 5 and newer (Program Files\\dotnet): not installed");
        if (!has_mscoree64) { miss_count++; if (strlen(missing) + 24 < sizeof missing) snprintf(missing + strlen(missing), sizeof missing - strlen(missing), "%smscoree.dll", missing[0] ? ", " : ""); }
    }
    say("");
    if (miss_count) say("Missing: %s. A game that installs its own Redistributable (most do) brings these in.", missing);
    else say("Nothing missing.");
    if (!have_2015_x64) return finish("vcrt", V_FAIL, "Visual C++ 2015-2022 x64 (vcruntime140.dll, msvcp140.dll) is missing: most 64-bit games need it");
    if (syswow && !have_2015_x86) return finish("vcrt", V_WARN, "x64 2015-2022 is there, x86 2015-2022 is missing; missing overall: %s", missing);
    if (miss_count) return finish("vcrt", V_PASS, "2015-2022 is there for the programs of this build; older years missing: %s", missing);
    return finish("vcrt", V_PASS, "all Visual C++ runtimes and the .NET loader are there");
}

/* ================================================================== disk */

typedef struct { double write_mbs, read_mbs, small_files_per_s; int ok; char why[120]; } disk_result;

static void disk_one(const char *dir, u64 size_mb, disk_result *out)
{
    char path[MAX_PATH], sub[MAX_PATH];
    HANDLE h;
    BYTE *buf;
    size_t chunk = 1u << 20, i;
    u64 total = size_mb << 20, done = 0, sum_w = 0, sum_r = 0, seed = 0x5851F42D4C957F2Dull;
    double t0, tw = 0, tr = 0;
    ULARGE_INTEGER freeb;
    int serial;
    DWORD got;

    memset(out, 0, sizeof *out);
    if (!GetDiskFreeSpaceExA(dir, &freeb, NULL, NULL)) { snprintf(out->why, sizeof out->why, "cannot read the free space of %s", dir); return; }
    if (freeb.QuadPart < total * 3 + (256ull << 20)) {
        snprintf(out->why, sizeof out->why, "only %llu MB free on %s; a %llu MB test needs about %llu MB", (u64)(freeb.QuadPart >> 20), dir, size_mb,
                 size_mb * 3 + 256);
        return;
    }
    buf = (BYTE *)VirtualAlloc(NULL, chunk, MEM_RESERVE | MEM_COMMIT, PAGE_READWRITE);
    if (!buf) { snprintf(out->why, sizeof out->why, "no memory for the buffer"); return; }
    fill_random(buf, chunk, seed);

    h = INVALID_HANDLE_VALUE;
    for (serial = 0; serial < 100 && h == INVALID_HANDLE_VALUE; serial++) {
        snprintf(path, sizeof path, "%s\\bcd-disktest-%lu-%d.tmp", dir, (unsigned long)GetCurrentProcessId(), serial);
        h = CreateFileA(path, GENERIC_WRITE, 0, NULL, CREATE_NEW, FILE_ATTRIBUTE_NORMAL | FILE_FLAG_SEQUENTIAL_SCAN, NULL);   /* never an existing file */
        if (h == INVALID_HANDLE_VALUE && GetLastError() != ERROR_FILE_EXISTS && GetLastError() != ERROR_ALREADY_EXISTS) break;
    }
    if (h == INVALID_HANDLE_VALUE) { snprintf(out->why, sizeof out->why, "cannot create a test file in %s (error %lu)", dir, GetLastError()); VirtualFree(buf, 0, MEM_RELEASE); return; }

    t0 = now_ms();
    while (done < total && !g_abort) {
        *(u64 *)buf = done;
        if (!WriteFile(h, buf, (DWORD)chunk, &got, NULL) || got != chunk) { snprintf(out->why, sizeof out->why, "write failed at %llu MB (error %lu)", done >> 20, GetLastError()); break; }
        done += chunk;
        sum_w += *(u64 *)buf;
    }
    FlushFileBuffers(h);
    tw = now_ms() - t0;
    CloseHandle(h);
    if (g_abort && !out->why[0]) snprintf(out->why, sizeof out->why, "closed while running");
    if (done == total && !out->why[0]) {
        h = CreateFileA(path, GENERIC_READ, FILE_SHARE_READ, NULL, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL | FILE_FLAG_SEQUENTIAL_SCAN, NULL);
        if (h == INVALID_HANDLE_VALUE) snprintf(out->why, sizeof out->why, "cannot reopen the test file (error %lu)", GetLastError());
        else {
            done = 0;
            t0 = now_ms();
            while (done < total && !g_abort) {
                if (!ReadFile(h, buf, (DWORD)chunk, &got, NULL) || got != chunk) { snprintf(out->why, sizeof out->why, "read failed at %llu MB (error %lu)", done >> 20, GetLastError()); break; }
                sum_r += *(u64 *)buf;
                done += chunk;
            }
            tr = now_ms() - t0;
            CloseHandle(h);
            if (g_abort && !out->why[0]) snprintf(out->why, sizeof out->why, "closed while running");
            if (!out->why[0] && sum_r != sum_w) snprintf(out->why, sizeof out->why, "the file read back differently from what was written (block markers)");
        }
    }
    if (!DeleteFileA(path) && path_exists(path))                 /* the test's own file goes at once */
        say("  warning: %s could not be removed (error %lu)", path, GetLastError());

    if (!out->why[0]) {
        out->write_mbs = mb_per_s(total, tw);
        out->read_mbs = mb_per_s(total, tr);
        /* many small files: how an installer or a game's loose assets behave */
        snprintf(sub, sizeof sub, "%s\\bcd-disktest-dir-%lu", dir, (unsigned long)GetCurrentProcessId());
        if (CreateDirectoryA(sub, NULL)) {
            int made = 0;
            t0 = now_ms();
            for (i = 0; i < 200 && !g_abort; i++) {
                HANDLE f;
                snprintf(path, sizeof path, "%s\\f%03u.tmp", sub, (unsigned)i);
                f = CreateFileA(path, GENERIC_WRITE, 0, NULL, CREATE_NEW, FILE_ATTRIBUTE_NORMAL, NULL);
                if (f == INVALID_HANDLE_VALUE) break;
                WriteFile(f, buf, 4096, &got, NULL);
                CloseHandle(f);
                made++;
            }
            for (i = 0; i < (size_t)made; i++) {
                snprintf(path, sizeof path, "%s\\f%03u.tmp", sub, (unsigned)i);
                DeleteFileA(path);
            }
            tw = now_ms() - t0;
            RemoveDirectoryA(sub);
            out->small_files_per_s = tw > 0 ? made / (tw / 1000.0) : 0;
        }
        out->ok = 1;
    }
    VirtualFree(buf, 0, MEM_RELEASE);
}

/* `fatal`: a drive that cannot be measured is a failure (C:) or a warning (the external drive, which may be read-only or slow by design). */
static int disk_target(const char *label, const char *dir, u64 size_mb, int *verdict_io, int fatal)
{
    disk_result r;
    say("%s (%s), %llu MB test file:", label, dir, size_mb);
    disk_one(dir, size_mb, &r);
    if (!r.ok) {
        say("  not measured: %s", r.why);
        if (*verdict_io < (fatal ? V_FAIL : V_WARN)) *verdict_io = fatal ? V_FAIL : V_WARN;
        return 0;
    }
    say("  write %.0f MB/s, read %.0f MB/s, small files (4 KB create+delete) %.0f per second", r.write_mbs, r.read_mbs, r.small_files_per_s);
    say("  (the read may come from the system's cache for a file this small)");
    if ((r.write_mbs < 5 || r.read_mbs < 5) && *verdict_io < V_FAIL) *verdict_io = fatal ? V_FAIL : V_WARN;
    else if ((r.write_mbs < 20 || r.read_mbs < 20) && *verdict_io < V_WARN) *verdict_io = V_WARN;
    return 1;
}

static int test_disk(u64 size_mb)
{
    int verdict = V_PASS, measured = 0;
    char c_dir[MAX_PATH];
    DWORD a;

    header("Disk speed test");
    GetTempPathA(sizeof c_dir, c_dir);
    {
        size_t n = strlen(c_dir);
        while (n > 3 && (c_dir[n - 1] == '\\' || c_dir[n - 1] == '/')) c_dir[--n] = 0;
    }
    if (!dir_exists(c_dir)) snprintf(c_dir, sizeof c_dir, "C:");
    measured += disk_target("C: (the game folder's drive)", c_dir, size_mb, &verdict, 1);
    /* External drives (Settings > Storage > External drives): each connected drive is C:\\External\\<name>. */
    a = GetFileAttributesA("C:\\External");
    if (a != INVALID_FILE_ATTRIBUTES && (a & FILE_ATTRIBUTE_DIRECTORY)) {
        WIN32_FIND_DATAA fd;
        HANDLE h = FindFirstFileA("C:\\External\\*", &fd);
        int drives = 0;
        if (h != INVALID_HANDLE_VALUE) {
            do {
                char dir[MAX_PATH], label[MAX_PATH + 32];
                if (!(fd.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) || fd.cFileName[0] == '.') continue;
                if (drives >= 4) break;
                snprintf(dir, sizeof dir, "C:\\External\\%s", fd.cFileName);
                snprintf(label, sizeof label, "External drive %s", fd.cFileName);
                measured += disk_target(label, dir, size_mb, &verdict, 0);
                drives++;
            } while (FindNextFileA(h, &fd));
            FindClose(h);
        }
        if (!drives) say("External drive: none connected in BCD (C:\\External has no drive)");
    } else
        say("External drive: none connected in BCD (no C:\\External)");
    if (!measured) return finish("disk", V_FAIL, "nothing could be measured");
    if (verdict == V_FAIL) return finish("disk", V_FAIL, "a drive is very slow or failed (see above)");
    if (verdict == V_WARN) return finish("disk", V_WARN, "a drive is slow (under 20 MB/s) or could not be measured (see above)");
    return finish("disk", V_PASS, "%d drive(s) measured, none under 20 MB/s", measured);
}

/* ================================================================== network */

typedef struct { const char *service; const char *hosts[2]; } net_service;

static const net_service net_services[] = {
    { "Steam",      { "store.steampowered.com", "api.steampowered.com" } },
    { "Epic",       { "www.epicgames.com", "launcher-public-service-prod06.ol.epicgames.com" } },
    { "GOG",        { "www.gog.com", "api.gog.com" } },
    { "Battle.net", { "us.battle.net", "account.battle.net" } },
    { "EA",         { "www.ea.com", "accounts.ea.com" } },
};

static const char *winhttp_error(DWORD e)
{
    switch (e) {
    case 12002: return "timed out";
    case 12005: return "invalid URL";
    case 12007: return "name not resolved";
    case 12029: return "cannot connect";
    case 12030: return "connection aborted";
    case 12038: return "certificate name does not match";
    case 12044: return "client certificate wanted";
    case 12152: return "invalid server response";
    case 12157: return "secure channel error";
    case 12175: return "TLS/certificate failure";
    case 12178: return "auto proxy failed";
    default: return "";
    }
}

typedef struct {
    HMODULE ws, wh;
    int (WSAAPI *startup)(WORD, LPWSADATA);
    int (WSAAPI *cleanup)(void);
    int (WSAAPI *getaddr)(PCSTR, PCSTR, const ADDRINFOA *, PADDRINFOA *);
    void (WSAAPI *freeaddr)(PADDRINFOA);
    int (WSAAPI *lasterr)(void);
    HINTERNET (WINAPI *open)(LPCWSTR, DWORD, LPCWSTR, LPCWSTR, DWORD);
    HINTERNET (WINAPI *connect)(HINTERNET, LPCWSTR, INTERNET_PORT, DWORD);
    HINTERNET (WINAPI *openreq)(HINTERNET, LPCWSTR, LPCWSTR, LPCWSTR, LPCWSTR, LPCWSTR *, DWORD);
    BOOL (WINAPI *send)(HINTERNET, LPCWSTR, DWORD, LPVOID, DWORD, DWORD, DWORD_PTR);
    BOOL (WINAPI *recv)(HINTERNET, LPVOID);
    BOOL (WINAPI *query)(HINTERNET, DWORD, LPCWSTR, LPVOID, LPDWORD, LPDWORD);
    BOOL (WINAPI *close)(HINTERNET);
    BOOL (WINAPI *timeouts)(HINTERNET, int, int, int, int);
} net_api;

static int net_load(net_api *a)
{
    memset(a, 0, sizeof *a);
    a->ws = LoadLibraryA("ws2_32.dll");
    a->wh = LoadLibraryA("winhttp.dll");
    if (!a->ws || !a->wh) return 0;
#define GP(m, dll, n) *(FARPROC *)&a->m = GetProcAddress(a->dll, n)
    GP(startup, ws, "WSAStartup"); GP(cleanup, ws, "WSACleanup"); GP(getaddr, ws, "getaddrinfo"); GP(freeaddr, ws, "freeaddrinfo");
    GP(lasterr, ws, "WSAGetLastError");
    GP(open, wh, "WinHttpOpen"); GP(connect, wh, "WinHttpConnect"); GP(openreq, wh, "WinHttpOpenRequest"); GP(send, wh, "WinHttpSendRequest");
    GP(recv, wh, "WinHttpReceiveResponse"); GP(query, wh, "WinHttpQueryHeaders"); GP(close, wh, "WinHttpCloseHandle");
    GP(timeouts, wh, "WinHttpSetTimeouts");
#undef GP
    return a->startup && a->getaddr && a->freeaddr && a->open && a->connect && a->openreq && a->send && a->recv && a->query && a->close;
}

/* One host: DNS and an HTTPS HEAD. Returns 1 when the HTTPS answer came (any status), 0 otherwise. */
static int net_host(net_api *a, const char *host, char *line, size_t cap)
{
    ADDRINFOA hints, *res = NULL;
    double t0;
    double dns_ms = 0, https_ms = 0;
    int dns_ok = 0, naddr = 0, werr = 0, https_ok = 0;
    DWORD status = 0, serr = 0, size = sizeof status;
    WCHAR whost[128];
    HINTERNET s, c, r;
    ADDRINFOA *p;
    char first[64] = "";

    memset(&hints, 0, sizeof hints);
    hints.ai_family = AF_UNSPEC;
    hints.ai_socktype = SOCK_STREAM;
    t0 = now_ms();
    if (a->getaddr(host, "443", &hints, &res) == 0 && res) {
        dns_ok = 1;
        for (p = res; p; p = p->ai_next) {
            if (!naddr && p->ai_family == AF_INET) {
                unsigned char *b = (unsigned char *)&((struct sockaddr_in *)p->ai_addr)->sin_addr;
                snprintf(first, sizeof first, "%u.%u.%u.%u", b[0], b[1], b[2], b[3]);
            }
            naddr++;
        }
        a->freeaddr(res);
    } else werr = a->lasterr ? a->lasterr() : 0;
    dns_ms = now_ms() - t0;
    if (!dns_ok) {
        snprintf(line, cap, "%-46s DNS FAILED (Winsock error %d), %.0f ms", host, werr, dns_ms);
        return 0;
    }
    MultiByteToWideChar(CP_ACP, 0, host, -1, whost, 128);
    t0 = now_ms();
    s = a->open(L"BCD-systest/1", WINHTTP_ACCESS_TYPE_DEFAULT_PROXY, NULL, NULL, 0);
    if (s) {
        if (a->timeouts) a->timeouts(s, 6000, 8000, 8000, 8000);
        c = a->connect(s, whost, INTERNET_DEFAULT_HTTPS_PORT, 0);
        if (c) {
            r = a->openreq(c, L"HEAD", L"/", NULL, NULL, NULL, WINHTTP_FLAG_SECURE);
            if (r) {
                if (a->send(r, NULL, 0, NULL, 0, 0, 0) && a->recv(r, NULL)) {
                    if (a->query(r, WINHTTP_QUERY_STATUS_CODE | WINHTTP_QUERY_FLAG_NUMBER, NULL, &status, &size, NULL)) https_ok = 1;
                }
                if (!https_ok) serr = GetLastError();
                a->close(r);
            } else serr = GetLastError();
            a->close(c);
        } else serr = GetLastError();
        a->close(s);
    } else serr = GetLastError();
    https_ms = now_ms() - t0;
    if (https_ok)
        snprintf(line, cap, "%-46s DNS %.0f ms (%d addr, %s) | HTTPS %.0f ms, status %lu", host, dns_ms, naddr, first[0] ? first : "IPv6", https_ms,
                 (unsigned long)status);
    else
        snprintf(line, cap, "%-46s DNS %.0f ms (%d addr) | HTTPS FAILED after %.0f ms: error %lu %s", host, dns_ms, naddr, https_ms,
                 (unsigned long)serr, winhttp_error(serr));
    return https_ok;
}

static int test_net(int quick)
{
    net_api api;
    WSADATA wsa;
    size_t i, k, hosts_per = quick ? 1 : 2;
    int services_ok = 0, services = 0, any_dns_fail = 0;
    char line[300];

    header("Network test");
    if (!net_load(&api)) {
        say("ws2_32.dll or winhttp.dll could not be loaded");
        return finish("net", V_FAIL, "Windows networking is not available");
    }
    if (api.startup(MAKEWORD(2, 2), &wsa) != 0) return finish("net", V_FAIL, "WSAStartup failed");
    for (i = 0; i < sizeof net_services / sizeof net_services[0]; i++) {
        int ok = 0;
        services++;
        say("%s:", net_services[i].service);
        for (k = 0; k < hosts_per && net_services[i].hosts[k] && !g_abort; k++) {
            int r = net_host(&api, net_services[i].hosts[k], line, sizeof line);
            say("  %s", line);
            if (strstr(line, "DNS FAILED")) any_dns_fail = 1;
            ok |= r;
        }
        if (ok) services_ok++;
        else say("  -> %s is not reachable from here", net_services[i].service);
    }
    if (api.cleanup) api.cleanup();
    if (g_abort) return finish("net", V_SKIP, "closed while running");
    if (services_ok == services) return finish("net", V_PASS, "all %d services answered over HTTPS", services);
    if (services_ok == 0) return finish("net", V_FAIL, "nothing answered%s: no network, or TLS/DNS does not work in this build", any_dns_fail ? " (DNS fails)" : "");
    return finish("net", V_WARN, "%d of %d services answered", services_ok, services);
}

/* ================================================================== all */

/* One line per test and the totals; returns the worst verdict (a FAIL beats a WARN beats a LIMIT) and the totals in `summary`. */
static int results_table(char *summary, size_t cap)
{
    int i, pass = 0, fail = 0, warn = 0, limit = 0, skip = 0, worst = V_PASS;

    say("");
    say("==== Report ====");
    for (i = 0; i < g_nresults; i++) {
        say("  %-6s %-14s %s", verdict_name(g_results[i].verdict), g_results[i].name, g_results[i].summary);
        switch (g_results[i].verdict) {
        case V_PASS: pass++; break;
        case V_FAIL: fail++; break;
        case V_WARN: warn++; break;
        case V_LIMIT: limit++; break;
        default: skip++; break;
        }
    }
    if (fail) worst = V_FAIL;
    else if (warn) worst = V_WARN;
    else if (limit) worst = V_LIMIT;
    snprintf(summary, cap, "%d passed, %d limited by the device, %d warnings, %d failed, %d skipped", pass, limit, warn, fail, skip);
    say("  %s", summary);
    return worst;
}

static int test_all(void)
{
    char summary[160];
    int worst;

    say("BCD system tests (%d-bit program)", BITS);
    host_read_info();
    host_lines();
    /* the graphics probes last: if a driver call ever ends the process, everything before it is already in the log */
    test_cpu(1);
    test_ram_quick();
    test_jit();
    test_vcrt();
    test_disk(64);
    test_net(1);
    test_sysinfo(1);
    worst = results_table(summary, sizeof summary);
    say("");
    finish("all", worst, "%s", summary);
    say("");
    say("All done. Now press Send Log in BCD, so this report reaches the developer.");
    return worst;
}

/* ================================================================== window */

static const char *g_job;
static u64 g_job_arg;
static int g_exit_code;
static HANDLE g_worker;

static int run_job(void)
{
    if (!strcmp(g_job, "all")) return test_all();
    if (!strcmp(g_job, "cpu")) return test_cpu(0);
    if (!strcmp(g_job, "ram")) return test_ram(g_job_arg);
    if (!strcmp(g_job, "ram32")) return test_ram32();
    if (!strcmp(g_job, "jit")) return test_jit();
    if (!strcmp(g_job, "sysinfo")) return test_sysinfo(1);
    if (!strcmp(g_job, "vcrt")) return test_vcrt();
    if (!strcmp(g_job, "disk")) return test_disk(256);
    if (!strcmp(g_job, "net")) return test_net(0);
    say("Unknown test \"%s\". Tests: all, cpu, ram <GB>, ram32, jit, sysinfo, vcrt, disk, net.", g_job);
    return V_FAIL;
}

static DWORD WINAPI worker_main(LPVOID unused)
{
    int v;
    (void)unused;
    say("Start: %s%s (%d-bit program)", g_job, g_job_arg ? " with a size argument" : "", BITS);
    v = run_job();
    g_exit_code = v == V_FAIL ? 1 : 0;
    say("");
    if (strcmp(g_job, "all")) say("The result is in the session log. Send Log in BCD carries it.");
    if (g_wnd) PostMessageA(g_wnd, WM_DONE, 0, 0);
    return 0;
}

static LRESULT CALLBACK wndproc(HWND h, UINT m, WPARAM w, LPARAM l)
{
    switch (m) {
    case WM_SIZE:
        if (g_edit) MoveWindow(g_edit, 8, 8, LOWORD(l) - 16, HIWORD(l) - 52, TRUE);
        {
            HWND b = GetDlgItem(h, 1001);
            if (b) MoveWindow(b, LOWORD(l) - 108, HIWORD(l) - 38, 100, 30, TRUE);
        }
        return 0;
    case WM_REFRESH:
        InterlockedExchange(&g_dirty, 0);
        EnterCriticalSection(&g_lock);
        if (g_text) {
            SetWindowTextA(g_edit, g_text);
            SendMessageA(g_edit, EM_SETSEL, (WPARAM)g_len, (LPARAM)g_len);
            SendMessageA(g_edit, EM_SCROLLCARET, 0, 0);
        }
        LeaveCriticalSection(&g_lock);
        return 0;
    case WM_DONE: {                                         /* the test is over; the window stays for reading */
        char t[160];
        GetWindowTextA(h, t, sizeof t - 12);
        strcat(t, " (done)");
        SetWindowTextA(h, t);
        return 0;
    }
    case WM_COMMAND:
        if (LOWORD(w) == 1001) PostMessageA(h, WM_CLOSE, 0, 0);
        return 0;
    case WM_KEYDOWN:
        if (w == VK_ESCAPE) PostMessageA(h, WM_CLOSE, 0, 0);
        return 0;
    case WM_CLOSE:
        InterlockedExchange(&g_abort, 1);
        DestroyWindow(h);
        return 0;
    case WM_DESTROY:
        PostQuitMessage(0);
        return 0;
    }
    return DefWindowProcA(h, m, w, l);
}

static int make_window(HINSTANCE inst, const char *title)
{
    WNDCLASSA wc;
    HFONT font;
    HWND btn;
    int sw = GetSystemMetrics(SM_CXSCREEN), sh = GetSystemMetrics(SM_CYSCREEN), cw = 760, ch = 520;
    RECT r;
    const DWORD style = WS_OVERLAPPEDWINDOW;

    /* a small Wine desktop (down to 640x360) must still hold the whole window */
    if (sw > 0 && cw > sw - 60) cw = sw - 60 > 320 ? sw - 60 : 320;
    if (sh > 0 && ch > sh - 110) ch = sh - 110 > 220 ? sh - 110 : 220;
    r.left = r.top = 0;
    r.right = cw;
    r.bottom = ch;

    memset(&wc, 0, sizeof wc);
    wc.lpfnWndProc = wndproc;
    wc.hInstance = inst;
    wc.hCursor = LoadCursorA(NULL, (LPCSTR)IDC_ARROW);
    wc.hbrBackground = (HBRUSH)(COLOR_BTNFACE + 1);
    wc.lpszClassName = "BCDSystemTest";
    if (!RegisterClassA(&wc)) return 0;
    AdjustWindowRect(&r, style, FALSE);
    g_wnd = CreateWindowExA(0, wc.lpszClassName, title, style | WS_VISIBLE, CW_USEDEFAULT, CW_USEDEFAULT, r.right - r.left, r.bottom - r.top,
                            NULL, NULL, inst, NULL);
    if (!g_wnd) return 0;
    g_edit = CreateWindowExA(WS_EX_CLIENTEDGE, "EDIT", "", WS_CHILD | WS_VISIBLE | WS_VSCROLL | WS_HSCROLL | ES_MULTILINE | ES_READONLY |
                             ES_AUTOVSCROLL | ES_AUTOHSCROLL, 8, 8, cw - 16, ch - 52, g_wnd, NULL, inst, NULL);
    btn = CreateWindowExA(0, "BUTTON", "Close", WS_CHILD | WS_VISIBLE | BS_PUSHBUTTON, cw - 108, ch - 38, 100, 30, g_wnd, (HMENU)1001, inst, NULL);
    font = CreateFontA(-14, 0, 0, 0, FW_NORMAL, 0, 0, 0, DEFAULT_CHARSET, 0, 0, DEFAULT_QUALITY, FIXED_PITCH | FF_MODERN, "Courier New");
    if (font && g_edit) SendMessageA(g_edit, WM_SETFONT, (WPARAM)font, TRUE);
    if (g_edit) SendMessageA(g_edit, EM_LIMITTEXT, 1 << 20, 0);
    (void)btn;
    UpdateWindow(g_wnd);
    return g_edit != NULL;
}

int WINAPI WinMain(HINSTANCE inst, HINSTANCE prev, LPSTR cmd, int show)
{
    static char job[32], arg[32];           /* static: the worker thread reads g_job */
    char args[256], title[96];
    MSG msg;
    DWORD tid;
    CreateDirectoryA("C:\\ProgramData", NULL);
    CreateDirectoryA(HOST_DIR, NULL);

    (void)prev; (void)show;
    InitializeCriticalSection(&g_lock);
    snprintf(args, sizeof args, "%s", cmd ? cmd : "");
    if (sscanf(args, "%31s %31s", job, arg) < 1) snprintf(job, sizeof job, "help");
    g_job = job;
    g_job_arg = (u64)strtoull(arg, NULL, 10);

    g_logfile = CreateFileA(LOG_FILE, GENERIC_WRITE, FILE_SHARE_READ, NULL, CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);   /* the last run only */
    snprintf(title, sizeof title, APP_TITLE " - %s%s%s", job, arg[0] ? " " : "", arg);
    if (!make_window(inst, title)) {
        g_headless = 1;
        say("The window could not be created; the test runs with the log only.");
    }
    if (!strcmp(job, "help")) {
        say("BCD system tests. Start one from Start > Programs > BCD Tests:");
        say("  Run all tests, System info, CPU test, RAM test 2/4/6/8/12 GB, RAM test (32-bit), JIT pool test,");
        say("  VC++ and .NET check, Disk speed test, Network test.");
        say("Command line: bcd-systest-x64.exe all | cpu | ram <GB> | jit | sysinfo | vcrt | disk | net   (bcd-systest-x86.exe ram32)");
        if (g_headless) return 0;
    } else {
        g_worker = CreateThread(NULL, 0, worker_main, NULL, 0, &tid);
        if (!g_worker) { say("The test thread could not be started (error %lu)", GetLastError()); return 1; }
        if (g_headless) {
            WaitForSingleObject(g_worker, INFINITE);
            return g_exit_code;
        }
    }
    while (GetMessageA(&msg, NULL, 0, 0) > 0) {
        TranslateMessage(&msg);
        DispatchMessageA(&msg);
    }
    if (g_worker) {
        InterlockedExchange(&g_abort, 1);
        WaitForSingleObject(g_worker, 8000);               /* the memory is given back before the process ends */
    }
    if (g_logfile != INVALID_HANDLE_VALUE) CloseHandle(g_logfile);
    return g_exit_code;
}

// WineProcessBridge.m - Initialize Wine's ntdll Unix-side on iOS
// This calls __wine_main() to bootstrap the Wine process, connecting
// to the already-running wineserver thread.

#import <Foundation/Foundation.h>
#import <os/log.h>
#import <pthread.h>
#import "WineProcessBridge.h"
#import "BCDHDR.h"
/* AVFoundation: AVAudioSession activation for the Tier-2 audio driver
 * (audio_null_ios.c RemoteIO backend). AudioToolbox: pulls the framework
 * in via autolink — the static-lib driver code can't autolink itself. */
#import <AVFoundation/AVFoundation.h>
#import <AudioToolbox/AudioToolbox.h>
/* madeira-bcd: the app's activity notifications (MADEIRA_AUDIO_RESUME, MADEIRA_PAUSE_IN_BACKGROUND). */
#import <UIKit/UIKit.h>
#include <stdatomic.h>
#include <sys/time.h>
#include <time.h>
#include <unistd.h>
#include <pwd.h>
#include <fcntl.h>
#include <sys/socket.h>
#include <setjmp.h>
#include <stdlib.h>
#include <errno.h>
#include <dirent.h>
#include "../../build/madeira_cfg.h"   /* ml1095: one config file */
#include "../../build/bcd_epic_mask.h"   /* madeira-bcd (2026-10-22): Epic's sign-in arguments masked in the argv lines */
#include "BCDGraphicsRoutes.h"   /* madeira-bcd (round 602): env.MADEIRA_D3D8 = d3d9 and env.MADEIRA_DDRAW = cnc replace Wine's d3d8.dll and ddraw.dll in syswow64 */
#include <sys/stat.h>
#include <pwd.h>
#include <limits.h>
#include <string.h>
#include <stdio.h>
#include <stdint.h>
#include <sys/sysctl.h>
#include <dlfcn.h>
#include <mach-o/loader.h>

#include "WineProcessBridge.h"
#include "WineServerBridge.h"
#include "PrefixExtractor.h"
#include "BCDStartMenu.h"   /* madeira-bcd: the Start menu entries of the Wine desktop */
#include "BCDHostStatus.h"   /* madeira-bcd: what the system tests (tests/systests) may know about the device and the app */
#include "FEXBridge.h"  // fex_get_jit_write_offset()

/* Write end of the pipe on fd 0, or -1 when stdin was left alone. The app's
 * host writes the guest's keystrokes here. */
int madeira_stdin_master = -1;
/* madeira-stdout-pipe: read end of the guest's stdout, or -1 when the pipe is
 * not enabled and stdout still goes to the shared log. */
int madeira_stdout_master = -1;

int madeira_stdout_is_open(void) { return madeira_stdout_master >= 0; }

long madeira_stdout_read(void *buf, unsigned long len) {
    if (madeira_stdout_master < 0 || !buf || !len) return -1;
    return read(madeira_stdout_master, buf, (size_t)len);
}

long madeira_stdin_write(const void *buf, unsigned long len) {
    if (madeira_stdin_master < 0) return -1;
    return (long)write(madeira_stdin_master, buf, (size_t)len);
}

/* Whether the pipe exists. An accessor rather than letting callers read the
 * global: it is written on the Wine thread and read from the UI thread, and
 * Swift 6 refuses to import a mutable C global as concurrency-safe. */
int madeira_stdin_is_open(void) {
    return madeira_stdin_master >= 0;
}

/* Close the write end so the guest's stdin reaches EOF.
 *
 * Without this, anything that reads to EOF -- an interpreter fed a script,
 * sort, the type/more family -- blocks forever after consuming what it was
 * given. Combined with one-program-per-launch that costs the whole app launch,
 * so the terminal must be able to signal "no more input". */
void madeira_stdin_close(void) {
    if (madeira_stdin_master < 0) return;
    close(madeira_stdin_master);
    madeira_stdin_master = -1;
    dprintf(STDERR_FILENO, "[stdin-pipe] write end closed; guest stdin is at EOF\n");
}

// Thread-local globals for wine_ios_exit longjmp (used by wine_ios_exit.h shim in ntdll)
// Each Wine "process" thread has its own jmpbuf so child processes can exit independently.
_Thread_local jmp_buf wine_ios_exit_jmpbuf;
_Thread_local volatile int wine_ios_exit_code = 0;

/* Non-thread-local copy of the above, published when the Wine thread unwinds.
 * Valid once wine_process_is_running() returns 0. */
/* Not volatile: the atomics below provide the ordering, and volatile would
 * only obscure that they are doing so. */
static int g_wine_exit_code = 0;
_Thread_local pthread_t wine_ios_main_thread;
_Thread_local int wine_ios_exit_initialized = 0;


static os_log_t wine_proc_log(void) {
    static os_log_t log;
    static dispatch_once_t once;
    dispatch_once(&once, ^{ log = os_log_create("com.madeira.emulator", "wine-proc"); });
    return log;
}

#define LOG(fmt, ...) os_log(wine_proc_log(), "[WineProc] " fmt, ##__VA_ARGS__)

/* ---- ml581: undo the hand-made AppData skeleton ------------------------
 *
 * While chasing the Steam login window I hand-created
 * drive_c/users/mobile/AppData/{Roaming,LocalLow}/... on the device with
 * devicectl, each leaf holding a placeholder ".keep" file (devicectl cannot
 * copy an empty directory). That was a mistake: Wine populates the profile
 * itself, and it decides per-directory by EXISTENCE. Pre-creating Roaming
 * made every one of those checks pass, so the population that builds
 * Start Menu\Programs never ran -- the taskbar lost its Start button and
 * the virtual desktop stopped booting properly, four runs running.
 *
 * devicectl has no delete verb, so the undo has to live in the app. This
 * deletes ONLY files literally named ".keep", then removes directories that
 * are empty as a result, walking bottom-up and stopping at AppData itself.
 * A directory holding anything real is left completely alone, so this can
 * never destroy user or Steam data -- it only restores the "absent" state
 * Wine's population is gated on. Idempotent: after the first clean boot
 * repopulates the tree, there are no .keep files left and it does nothing. */
static int madeira_prune_keep_tree(const char *dir, int depth)
{
    DIR *d = opendir( dir );
    if (!d) return 0;                       /* absent/unreadable => nothing to do */

    int survivors = 0;
    struct dirent *ent;
    while ((ent = readdir( d )))
    {
        if (!strcmp( ent->d_name, "." ) || !strcmp( ent->d_name, ".." )) continue;

        char path[PATH_MAX];
        if (snprintf( path, sizeof(path), "%s/%s", dir, ent->d_name ) >= (int)sizeof(path))
        {
            survivors++;                    /* can't address it => treat as real */
            continue;
        }

        struct stat st;
        if (lstat( path, &st ) != 0) { survivors++; continue; }

        if (S_ISDIR( st.st_mode ) && depth > 0)
        {
            if (madeira_prune_keep_tree( path, depth - 1 ) > 0) survivors++;
            else if (rmdir( path ) != 0) survivors++;   /* non-empty or denied */
            else LOG( "keep-prune: rmdir %{public}s", path );
        }
        else if (S_ISREG( st.st_mode ) && !strcmp( ent->d_name, ".keep" ))
        {
            if (unlink( path ) != 0) survivors++;
            else LOG( "keep-prune: unlink %{public}s", path );
        }
        else survivors++;                   /* anything real keeps the dir alive */
    }
    closedir( d );
    return survivors;
}

/* ---- ml666: PROFILE REPAIR — the "usersmadeira" escaping bug -------------
 *
 * The shipped .reg files wrote  "C:\\users\madeira\\AppData\\Roaming"  with a
 * SINGLE backslash before `madeira`. In .reg syntax `\\` is a literal backslash
 * and a lone `\` starts an escape; `\m` is not a valid escape, so the backslash
 * was dropped and every shell folder resolved to  C:\usersmadeira\...  -- a
 * directory that never existed. 57 sites across user.reg/userdef.reg plus 3
 * already-collapsed in system.reg.
 *
 * It degraded silently for months: %TEMP% pointed there too, so Wine happily
 * CREATED C:\usersmadeira\AppData\Local\Temp and filled it (683 files and a CEF
 * cache on the dev device). Only paths whose parents are NOT auto-created broke
 * -- notably LocalLow, where Unity's log CreateDirectory failed, which left
 * stdout closed at _file=-1 and fast-failed the CRT inside _isatty.
 *
 * The template is fixed, but an existing prefix keeps the collapsed strings in
 * its own user.reg (Wine rewrote them after parsing). So repair on disk, before
 * __wine_main, once:
 *   1. rewrite  C:\\usersmadeira  ->  C:\\users\\madeira  in the three .reg files
 *   2. MOVE (never delete) drive_c/usersmadeira/* into drive_c/users/madeira/*
 *   3. ensure the AppData skeleton exists
 * Idempotent and marker-gated. Step 2 merges and refuses to clobber: if a
 * destination already exists the source is left in place for manual review,
 * because that tree holds real user data. */

static int ios_reg_unmangle(const char *path)
{
    FILE *f = fopen( path, "rb" );
    if (!f) return 0;
    fseek( f, 0, SEEK_END ); long n = ftell( f ); fseek( f, 0, SEEK_SET );
    if (n <= 0 || n > (64 << 20)) { fclose( f ); return 0; }
    char *buf = malloc( (size_t)n + 1 );
    if (!buf) { fclose( f ); return 0; }
    size_t got = fread( buf, 1, (size_t)n, f );
    fclose( f );
    if (got != (size_t)n) { free( buf ); return 0; }
    buf[n] = 0;

    /* ml667: anchored on "C:" originally, which MISSED the one value that has
     * no drive letter -- HOMEPATH = "\\usersmadeira". HOMEDRIVE+HOMEPATH is a
     * standard way to reach the profile, so that single miss left the default
     * path broken while everything else looked repaired. Match the collapsed
     * token itself; it reconstructs correctly with or without a drive prefix. */
    static const char BAD[]  = "usersmadeira";
    static const char GOOD[] = "users\\\\madeira";
    const size_t bl = sizeof(BAD) - 1, gl = sizeof(GOOD) - 1;
    size_t hits = 0;
    for (char *q = buf; (q = strstr( q, BAD )); q += bl) hits++;
    if (!hits) { free( buf ); return 0; }

    char *out = malloc( (size_t)n + hits * (gl - bl) + 1 ), *w;
    if (!out) { free( buf ); return 0; }
    w = out;
    for (const char *r = buf; *r; )
    {
        if (!strncmp( r, BAD, bl )) { memcpy( w, GOOD, gl ); w += gl; r += bl; }
        else *w++ = *r++;
    }
    *w = 0;

    /* write via temp + rename so a kill mid-write cannot truncate the registry */
    char tmp[PATH_MAX];
    snprintf( tmp, sizeof(tmp), "%s.ml666", path );
    FILE *o = fopen( tmp, "wb" );
    int ok = 0;
    if (o)
    {
        ok = fwrite( out, 1, (size_t)(w - out), o ) == (size_t)(w - out);
        if (fclose( o ) != 0) ok = 0;
        if (ok && rename( tmp, path ) != 0) ok = 0;
        if (!ok) unlink( tmp );
    }
    LOG( "profile-repair: %{public}s %zu path(s) %{public}s", path, hits, ok ? "rewritten" : "FAILED" );
    free( buf ); free( out );
    return ok ? (int)hits : 0;
}

/* Move src into dst, merging. Existing destinations are never overwritten. */
static void ios_merge_move(const char *src, const char *dst, int depth)
{
    DIR *d;
    struct dirent *ent;
    if (depth <= 0) return;
    if (rename( src, dst ) == 0) { LOG( "profile-repair: moved %{public}s", src ); return; }
    if (errno != ENOTEMPTY && errno != EEXIST && errno != ENOTDIR) return;
    if (!(d = opendir( src ))) return;
    while ((ent = readdir( d )))
    {
        char sp[PATH_MAX], dp[PATH_MAX];
        struct stat st;
        if (!strcmp( ent->d_name, "." ) || !strcmp( ent->d_name, ".." )) continue;
        if (snprintf( sp, sizeof(sp), "%s/%s", src, ent->d_name ) >= (int)sizeof(sp)) continue;
        if (snprintf( dp, sizeof(dp), "%s/%s", dst, ent->d_name ) >= (int)sizeof(dp)) continue;
        if (lstat( dp, &st ) != 0) { if (rename( sp, dp ) == 0) continue; }
        if (lstat( sp, &st ) == 0 && S_ISDIR( st.st_mode ))
        {
            mkdir( dp, 0755 );
            ios_merge_move( sp, dp, depth - 1 );
        }
        /* a colliding FILE is left alone -- never clobber real user data */
    }
    closedir( d );
    rmdir( src );                       /* only succeeds once genuinely empty */
}

static void madeira_repair_profile(NSString *prefix)
{
    NSFileManager *fm = [NSFileManager defaultManager];
    NSString *marker = [prefix stringByAppendingPathComponent:@".madeira-profile-repaired-ml667"];
    if ([fm fileExistsAtPath:marker]) return;

    int fixed = 0;
    for (NSString *reg in @[ @"user.reg", @"userdef.reg", @"system.reg" ])
        fixed += ios_reg_unmangle( [prefix stringByAppendingPathComponent:reg].fileSystemRepresentation );

    NSString *bad  = [prefix stringByAppendingPathComponent:@"drive_c/usersmadeira"];
    NSString *good = [prefix stringByAppendingPathComponent:@"drive_c/users/madeira"];
    if ([fm fileExistsAtPath:bad])
    {
        [fm createDirectoryAtPath:good withIntermediateDirectories:YES attributes:nil error:nil];
        ios_merge_move( bad.fileSystemRepresentation, good.fileSystemRepresentation, 12 );
    }

    /* The skeleton Wine's existence checks gate on. Creating it is safe here --
     * unlike the ml581 mistake, these are the REGISTERED profile paths. */
    for (NSString *leaf in @[ @"AppData/Roaming", @"AppData/Local", @"AppData/LocalLow",
                              @"AppData/Roaming/Microsoft/Windows/Start Menu/Programs" ])
        [fm createDirectoryAtPath:[good stringByAppendingPathComponent:leaf]
      withIntermediateDirectories:YES attributes:nil error:nil];

    /* ml667: only claim completion once the collapsed tree is actually gone.
     * ios_merge_move refuses to clobber, so a colliding file leaves the source
     * alive -- marking done there would strand that data forever. */
    if (![fm fileExistsAtPath:bad])
        [@"ml667" writeToFile:marker atomically:YES encoding:NSUTF8StringEncoding error:nil];
    else
        LOG( "profile-repair: %{public}s still present -- will retry next launch", bad.UTF8String );
    LOG( "profile-repair: complete (%d registry path(s) rewritten)", fixed );
}

static void madeira_undo_appdata_skeleton(NSString *prefix)
{
    /* ml666: SCOPED DOWN. As written this walked EVERY user and removed ANY
     * empty tree, which made it far more destructive than its own comment
     * claimed. Two consequences, both observed:
     *
     *   - It deleted the legitimate, registered users/madeira AppData skeleton
     *     that prefix-template.tar.gz ships -- the very directories Wine's
     *     population and Unity's log path depend on.
     *   - Given a freshly created empty Roaming/LocalLow it removed those too,
     *     and nothing recreates them, so the profile stayed permanently absent.
     *
     * It also never actually worked on the artifacts it was written for: the
     * ml581 devicectl push left those directories owned by uid 0, so unlink()
     * inside them always failed. It has been a silent no-op since it shipped.
     *
     * Now: users/mobile ONLY (the sole path the ml581 experiment touched), the
     * three leaf roots are never themselves removed, and the whole thing is
     * marker-gated so it runs once instead of on every launch. */
    NSFileManager *fm = [NSFileManager defaultManager];
    NSString *marker = [prefix stringByAppendingPathComponent:@".madeira-keepprune-done-ml666"];
    if ([fm fileExistsAtPath:marker]) return;

    NSString *appdata = [prefix stringByAppendingPathComponent:@"drive_c/users/mobile/AppData"];
    for (NSString *leaf in @[ @"Roaming", @"LocalLow", @"Local" ])
    {
        /* depth 6 covers Roaming/<Vendor>/<Product>/<...> comfortably; the
         * recursion is bounded so a symlink loop can't run away. Only the
         * .keep placeholders and the empty dirs they propped up are removed --
         * the leaf root itself always stays. */
        NSString *path = [appdata stringByAppendingPathComponent:leaf];
        madeira_prune_keep_tree( path.fileSystemRepresentation, 6 );
    }
    [@"ml666" writeToFile:marker atomically:YES encoding:NSUTF8StringEncoding error:nil];
}


// Wine's main entry point (from ntdll unix loader.c, statically linked)
extern void __wine_main(int argc, char *argv[]);

// File-based logging (from server_ios.c)
extern void wine_log_set_file(const char *path);

static pthread_t g_wine_thread;
static volatile int g_wine_running = 0;
/* madeira-bcd: env.MADEIRA_SRV_FGETPATH of the launch in progress, read by the in-app
 * wineserver (build/wineserver/fd_ios.c), which is running before a game's env lines
 * are set. Set after they are applied, cleared when the session ends. */
volatile int g_madeira_srv_fgetpath = 0;
/* madeira-bcd: the same for the server's other per-launch switches (fd_ios.c, request_ios.c;
 * ntdll's server_ios.c reads g_madeira_srv_direct too). All off by default. */
volatile int g_madeira_srv_eco_follow = 0;   /* env.MADEIRA_SRV_ECO_FOLLOW = 1 */
volatile int g_madeira_srv_direct = 0;       /* env.MADEIRA_SRV_DIRECT = 1 or 2 */
volatile int g_madeira_srv_idle_us = 0;      /* env.MADEIRA_SRV_IDLE_US = 100..50000, 0 = 1000 */
volatile int g_madeira_srv_log_all = 0;      /* env.MADEIRA_SRV_LOG_ALL = 1 */

/* Session exit report for the library front end. The app marks one process as
 * its own: the program it hands to __wine_main below, which is the session's
 * initial process. ntdll's common exit wrapper (build/ntdll-unix/server_ios.c)
 * calls wine_launched_process_did_exit() for that process only, on whichever
 * thread ends it. Only the status is kept: no names, no allocation, no
 * logging. g_launch_exit holds (1 << 32) | status when the program ended with
 * an NTSTATUS error (0xC...), else 0.
 * madeira-bcd (round 577, GTA IV freeze): the hook also hands the end to
 * wine_session_note_launch_exit() ("Session end" below): which thread it ran on,
 * and the app's own program name back. */
void wine_session_note_launch_exit(int status);
static uint64_t g_launch_exit = 0;
void wine_launched_process_did_exit(int status) {
    if ((uint32_t)status >= 0xC0000000u)
        __atomic_store_n(&g_launch_exit, (UINT64_C(1) << 32) | (uint32_t)status, __ATOMIC_RELEASE);
    wine_session_note_launch_exit(status);
}
void wine_exit_status_reset(void) {
    __atomic_store_n(&g_launch_exit, 0, __ATOMIC_RELEASE);
}
int wine_crash_exit_status(uint32_t *status) {
    uint64_t value = __atomic_load_n(&g_launch_exit, __ATOMIC_ACQUIRE);
    if (!(value >> 32)) return 0;
    if (status) *status = (uint32_t)value;
    return 1;
}
static char *g_prefix_path = NULL;

/* ---- Session end (madeira-bcd, round 577: GTA IV "the game freezes") ----------------------------------
 *
 * What ends a session: wine_process_thread (below) runs __wine_main on its own thread, the boot thread, and
 * clears g_wine_running and stops the wineserver when __wine_main comes back. It comes back when the program's
 * exit reaches the shim in shims/wine_ios_exit.h on THAT thread: exit() long-jumps to the setjmp above it. When
 * the program ends on any other thread (GTA IV 2026-10-21 10:18:42: the game thread 00b8 died of an uncaught C++
 * exception and called ExitProcess, the boot thread was 0024; Stranglehold RVBE4K: abort_process on 0070) the
 * shim can only pthread_exit that thread, the server kills the boot thread with the rest of the process,
 * __wine_main never returns, g_wine_running stays 1 and the library keeps the last frame for good. Of the logs
 * that show the server's line for the initial process's death ("[srv-kill] kill_process pid=0020", builds
 * 574-576), the two whose program ended off the boot thread (GTA IV 00b8, Stranglehold 0070) never printed
 * "[WineProc] stopping wineserver"; those that ended on 0024 did (or went on to a launcher's child wait).
 *
 * wine_session_note_launch_exit() (the hook above) notes that the initial process ended: with what code, when,
 * and whether on the boot thread (then the normal path follows). LibraryModel.poll asks
 * wine_launched_exit_stuck(): "ended off the boot thread, __wine_main has not returned". When the library
 * decides to leave (SessionEndRules in Library.swift: none of the program's own processes is left, a few
 * seconds passed) it calls wine_session_abandon(), which does what the boot thread would have done. That work,
 * wine_session_end(), is shared with the boot thread's own tail and runs once per session.
 *
 * Program name: setprogname() keeps the POINTER it is given (Darwin's libc does not copy). Wine's
 * set_process_name (build/ntdll-unix/env_ios.c) hands it the program's file name, which lies inside exe_path[],
 * a local of wine_process_thread: on the boot thread's stack. When that thread is gone its stack is unmapped
 * and the next getprogname() in the app (ICU's date formatting on BCD's game page, XPC's name for the network
 * state in AssistantServices, ...) faults on a native thread: [mach-deliver] NATIVE-THREAD DECLINE in
 * _platform_strlen / strcmp / strncasecmp_l, at an address 0x1270-0x1670 below the boot thread's TSD base (GTA IV
 * 0x16d621a87 = exe_path + 23, where the file name starts in the program's full path; Dead Space DVXWGA, Tomb Raider
 * TXPQM1, PES 2013 B39EXN, NFS Most Wanted XM2BC1 the same shape, Dead Space after a session that ended cleanly).
 * The hook gives the app's own name back as soon as the initial process ends, and wine_session_end() again. */
static volatile int g_wine_main_returned = 0;   /* __wine_main came back (or long-jumped back) on the boot thread */
static int g_launch_exit_claim = 0;             /* the first end of the initial process is taken */
static volatile int g_launch_exit_seen = 0;     /* ... and its code and time are stored */
static volatile int g_launch_exit_boot = 0;     /* an end of it ran on the boot thread: the normal path follows */
static volatile int g_launch_exit_code = 0;
static volatile uint64_t g_launch_exit_ns = 0;
static int g_session_end_claim = 0;             /* wine_session_end() has run for this session */
static char *g_app_progname = NULL;             /* the app's own getprogname(), taken before the first session */

static uint64_t wine_session_now_ns(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (uint64_t)ts.tv_sec * 1000000000ull + (uint64_t)ts.tv_nsec;
}

static void wine_progname_restore(void) {
    const char *name = __atomic_load_n(&g_app_progname, __ATOMIC_ACQUIRE);
    if (name) setprogname(name);
}

void wine_session_note_launch_exit(int status) {
    int boot = g_wine_thread && pthread_equal(pthread_self(), g_wine_thread);
    int expected = 0;
    if (boot) __atomic_store_n(&g_launch_exit_boot, 1, __ATOMIC_RELEASE);
    if (__atomic_compare_exchange_n(&g_launch_exit_claim, &expected, 1, 0, __ATOMIC_ACQ_REL, __ATOMIC_ACQUIRE)) {
        g_launch_exit_code = status;
        g_launch_exit_ns = wine_session_now_ns();
        __atomic_store_n(&g_launch_exit_seen, 1, __ATOMIC_RELEASE);
    }
    wine_progname_restore();
}

/* 1: the program ended off the boot thread and __wine_main has not come back (and the session's end work has
 * not run). *code: its exit status; *seconds: how long ago. */
int wine_launched_exit_stuck(int32_t *code, double *seconds) {
    if (!__atomic_load_n(&g_launch_exit_seen, __ATOMIC_ACQUIRE)) return 0;
    if (__atomic_load_n(&g_launch_exit_boot, __ATOMIC_ACQUIRE) || __atomic_load_n(&g_wine_main_returned, __ATOMIC_ACQUIRE)) return 0;
    if (!g_wine_running || __atomic_load_n(&g_session_end_claim, __ATOMIC_ACQUIRE)) return 0;
    if (code) *code = (int32_t)g_launch_exit_code;
    if (seconds) {
        uint64_t now = wine_session_now_ns(), then = g_launch_exit_ns;
        *seconds = now > then ? (double)(now - then) / 1e9 : 0.0;
    }
    return 1;
}

static void wine_session_end(void);   /* defined after wine_process_thread: the boot thread's tail and wine_session_abandon call it */

/* Export MADEIRA_DOCS_DIR before main(), while HOME is still the app container.
 * The in-app wineserver thread sets HOME to the Wine prefix before it creates its
 * first object, and madsync reads bcd.cfg inproc-sync right there (then keeps
 * the answer for the whole app run); with MADEIRA_DOCS_DIR exported only when the
 * guest starts (wine_process_thread below), that read looked in
 * Documents/wine/Documents and missed the user's bcd.cfg. Kill switch:
 * MADEIRA_CFG_EARLY_DOCS=0 in the process environment or
 * env.MADEIRA_CFG_EARLY_DOCS = 0 in bcd.cfg. Pure C, host-tested
 * (tests/host/check-cfg-early-docs.py). */
static const char *g_madeira_docs_early = "not-run";
/* madeira-env-locked: MADEIRA_ENV_LOCKED is a comma-separated list of names
 * the host set on purpose. Without it a madeira.cfg line -- or a library
 * game's own config -- could replace the program the front end was asked to
 * launch, and the user taps one thing while another starts, with nothing on
 * screen to say so. The match is on the whole name between commas, so a lock
 * on MADEIRA_EXE does not also catch MADEIRA_EXEC_FOO. Nothing is locked
 * unless a host asks for it. */
static int madeira_env_is_locked(const char *name)
{
    const char *locked = getenv("MADEIRA_ENV_LOCKED");
    if (!locked || !*locked || !name || !*name) return 0;
    size_t n = strlen(name);
    for (const char *p = locked; *p; ) {
        while (*p == ' ' || *p == ',') p++;
        const char *start = p;
        while (*p && *p != ',') p++;
        const char *end = p;
        while (end > start && end[-1] == ' ') end--;
        if ((size_t)(end - start) == n && !strncmp(start, name, n)) return 1;
    }
    return 0;
}

static int madeira_cfg_off_word(const char *v)
{
    return v && (!strcmp(v, "0") || !strcmp(v, "off") || !strcmp(v, "no"));
}
static const char *madeira_docs_dir_early(void)
{
    char docs[1024], v[16];
    const char *home = getenv("HOME"), *have = getenv("MADEIRA_DOCS_DIR");
    if (madeira_cfg_off_word(getenv("MADEIRA_CFG_EARLY_DOCS"))) return "off-env";
    if (have && *have) return "already-set";
    if (!home || !*home || strlen(home) + 11 >= sizeof(docs)) return "no-home";
    snprintf(docs, sizeof(docs), "%s/Documents", home);
    setenv("MADEIRA_DOCS_DIR", docs, 0);
    if (madeira_cfg_get("env.MADEIRA_CFG_EARLY_DOCS", v, sizeof(v)) && madeira_cfg_off_word(v)) {
        unsetenv("MADEIRA_DOCS_DIR");
        setenv("MADEIRA_CFG_EARLY_DOCS", "0", 1);   /* also turns off madeira_cfg.h's container fallback */
        return "off-cfg";
    }
    return "set";
}
__attribute__((constructor)) static void madeira_docs_dir_ctor(void)
{
    g_madeira_docs_early = madeira_docs_dir_early();
}

/* The profile's AppData\LocalLow folder.
 *
 * The shell-folder registry maps FOLDERID_LocalAppDataLow to
 * %USERPROFILE%\AppData\LocalLow, and the profile is named after the unix user
 * (ntdll's set_home_dir: "mobile" on a device). The template ships the folders
 * of the user it was built as, so the running user's LocalLow does not exist,
 * and nothing creates it: SHGetKnownFolderPath without KF_FLAG_CREATE fails
 * with ERROR_PATH_NOT_FOUND ("Failed to get LocalAppDataLow path, hr
 * 0x80070003"). A program that keeps its log there then opens a relative path
 * that does not exist either, is left with a closed stdout, and its C runtime
 * fast-fails (0xc0000409) before the first frame.
 *
 * Only LocalLow is created. Roaming is left alone: Wine populates it itself and
 * decides by existence (see madeira_undo_appdata_skeleton). Local already
 * exists on every start (the TEMP directory is in it). */
/* The PE shader cache's build identity (madeira_d3d12.c, mad_sc_init) includes the
 * LC_UUID of the image this bridge is in: the converter's unix half is linked into it
 * too, so the UUID changes whenever the converter could have, and a cache keyed on
 * the DLL's own build would otherwise keep serving an older converter's output. */
static void madeira_export_image_uuid(void)
{
    Dl_info di;
    const struct mach_header_64 *mh;
    const struct load_command *lc;
    uint32_t i;

    if (!dladdr((const void *)&madeira_export_image_uuid, &di) || !(mh = di.dli_fbase)) return;
    lc = (const struct load_command *)(mh + 1);
    for (i = 0; i < mh->ncmds; i++, lc = (const struct load_command *)((const char *)lc + lc->cmdsize))
    {
        if (lc->cmd != LC_UUID) continue;
        const uint8_t *u = ((const struct uuid_command *)lc)->uuid;
        char hex[33];
        for (int k = 0; k < 16; k++) snprintf(hex + 2 * k, 3, "%02x", u[k]);
        setenv("MADEIRA_APP_IMAGE_UUID", hex, 1);
        return;
    }
}

static void madeira_ensure_locallow(NSString *prefix)
{
    /* 0 leaves the profile's AppData\LocalLow folder missing, as before. */
    const char *off = getenv( "MADEIRA_PROFILE_LOCALLOW" );
    /* ml1236: this runs from wineserver_start, before wine_process_thread exports
     * bcd.cfg's env.* lines, so the cfg line is read here directly. */
    char cfg_off[16];
    if (!off && madeira_cfg_get( "env.MADEIRA_PROFILE_LOCALLOW", cfg_off, sizeof(cfg_off) )) off = cfg_off;
    if (off && off[0] == '0') return;

    const char *name = getenv( "USER" );
    if (!name)
    {
        struct passwd *pwd = getpwuid( getuid() );
        name = pwd && pwd->pw_name ? pwd->pw_name : "wine";
    }
    const char *slash = strrchr( name, '/' );
    if (slash) name = slash + 1;
    if ((slash = strrchr( name, '\\' ))) name = slash + 1;   /* ml1236: as ntdll's set_home_dir */
    if (!name[0]) return;

    NSString *path = [prefix stringByAppendingPathComponent:
        [NSString stringWithFormat:@"drive_c/users/%s/AppData/LocalLow", name]];
    NSFileManager *fm = [NSFileManager defaultManager];
    if ([fm fileExistsAtPath:path]) return;
    BOOL made = [fm createDirectoryAtPath:path withIntermediateDirectories:YES attributes:nil error:nil];
    extern void ws_log(const char *fmt, ...);
    ws_log( "[profile] users/%s/AppData/LocalLow %s", name, made ? "created" : "could NOT be created" );
}

static void madeira_seed_fonts_and_vc80_amd64(NSFileManager *fm, NSString *prefix)
{
    NSString *fontsDir = [prefix stringByAppendingPathComponent:@"drive_c/windows/Fonts"];
    [fm createDirectoryAtPath:fontsDir withIntermediateDirectories:YES attributes:nil error:nil];
    struct { const char *dst; const char *srcs[4]; } const fontMap[] = {
        { "msgothic.ttc", { "/System/Library/Fonts/Core/HiraginoKakuGothic.ttc",
                            "/System/Library/Fonts/Core/Hiragino Sans.ttc",
                            "/System/Library/Fonts/Hiragino Sans GB.ttc",
                            "/System/Library/Fonts/Core/PingFang.ttc" } },
        { "msmincho.ttc", { "/System/Library/Fonts/AppFonts/HiraginoMincho.ttc",
                            "/System/Library/Fonts/Core/HiraginoKakuGothic.ttc",
                            "/System/Library/Fonts/Core/Hiragino Sans.ttc", NULL } },
        { "arial.ttf",    { "/System/Library/Fonts/CoreAddition/Arial.ttf",
                            "/System/Library/Fonts/Supplemental/Arial.ttf", NULL, NULL } },
        { "arialbd.ttf",  { "/System/Library/Fonts/CoreAddition/ArialBold.ttf",
                            "/System/Library/Fonts/Supplemental/Arial Bold.ttf", NULL, NULL } },
        { "verdana.ttf",  { "/System/Library/Fonts/CoreAddition/Verdana.ttf",
                            "/System/Library/Fonts/Supplemental/Verdana.ttf", NULL, NULL } },
        { "georgia.ttf",  { "/System/Library/Fonts/CoreAddition/Georgia.ttf",
                            "/System/Library/Fonts/Supplemental/Georgia.ttf", NULL, NULL } },
        { "cour.ttf",     { "/System/Library/Fonts/Core/CourierNew.ttf",
                            "/System/Library/Fonts/Supplemental/Courier New.ttf", NULL, NULL } },
        { "times.ttf",    { "/System/Library/Fonts/Core/TimesNewRoman.ttf",
                            "/System/Library/Fonts/Supplemental/Times New Roman.ttf", NULL, NULL } },
        { "trebuc.ttf",   { "/System/Library/Fonts/CoreUI/TrebuchetMS.ttf",
                            "/System/Library/Fonts/Supplemental/Trebuchet MS.ttf", NULL, NULL } },
    };
    for (size_t i = 0; i < sizeof(fontMap) / sizeof(fontMap[0]); i++) {
        NSString *dst = [fontsDir stringByAppendingPathComponent:[NSString stringWithUTF8String:fontMap[i].dst]];
        if ([fm fileExistsAtPath:dst]) continue;
        for (size_t j = 0; j < 4 && fontMap[i].srcs[j]; j++) {
            NSString *src = [NSString stringWithUTF8String:fontMap[i].srcs[j]];
            if ([fm fileExistsAtPath:src]) {
                [fm removeItemAtPath:dst error:nil];
                [fm createSymbolicLinkAtPath:dst withDestinationPath:src error:nil];
                break;
            }
        }
    }

    /* Seed 64-bit Microsoft.VC80.CRT side-by-side assembly from arm64ec-windows if present. */
    NSString *bundle = [NSBundle mainBundle].bundlePath;
    NSString *ecDir = [bundle stringByAppendingPathComponent:@"arm64ec-windows"];
    NSString *firstVc80 = [ecDir stringByAppendingPathComponent:@"msvcr80.dll"];
    if ([fm fileExistsAtPath:firstVc80]) {
        NSString *winsxs = [prefix stringByAppendingPathComponent:@"drive_c/windows/winsxs"];
        NSString *manifests = [winsxs stringByAppendingPathComponent:@"manifests"];
        NSString *dirName = @"amd64_microsoft.vc80.crt_1fc8b3b9a1e18e3b_8.0.50727.9672_none_deadbeef";
        NSString *asmDir = [winsxs stringByAppendingPathComponent:dirName];
        NSString *manifest = [manifests stringByAppendingPathComponent:[dirName stringByAppendingString:@".manifest"]];
        [fm createDirectoryAtPath:manifests withIntermediateDirectories:YES attributes:nil error:nil];
        [fm createDirectoryAtPath:asmDir withIntermediateDirectories:YES attributes:nil error:nil];
        NSMutableString *text = [NSMutableString stringWithString:
            @"<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>\n"
            @"<assembly xmlns=\"urn:schemas-microsoft-com:asm.v1\" manifestVersion=\"1.0\">\n"
            @"  <assemblyIdentity type=\"win32\" name=\"Microsoft.VC80.CRT\" version=\"8.0.50727.9672\" "
            @"processorArchitecture=\"amd64\" publicKeyToken=\"1fc8b3b9a1e18e3b\"/>\n"];
        const char *const files[] = { "msvcr80.dll", "msvcp80.dll", "msvcm80.dll" };
        for (size_t f = 0; f < 3; f++) {
            NSString *fn = [NSString stringWithUTF8String:files[f]];
            NSString *src = [ecDir stringByAppendingPathComponent:fn];
            NSString *link = [asmDir stringByAppendingPathComponent:fn];
            [fm removeItemAtPath:link error:nil];
            if (![fm fileExistsAtPath:src]) continue;
            if ([fm createSymbolicLinkAtPath:link withDestinationPath:src error:nil])
                [text appendFormat:@"  <file name=\"%s\"/>\n", files[f]];
        }
        [text appendString:@"</assembly>\n"];
        [[text dataUsingEncoding:NSUTF8StringEncoding] writeToFile:manifest atomically:YES];
    }
}

/***********************************************************************
 *           madeira_seed_prefix_if_needed
 *
 * Extract the bundled prefix template on first launch and (re)create the
 * dosdevices links. Idempotent: the .update-timestamp probe makes every call
 * after the first a single stat().
 *
 * ml588 — MUST RUN BEFORE THE WINESERVER STARTS. This used to live inside
 * wine_process_thread(), which starts ~2s AFTER wineserver_start(). On a fresh
 * prefix that ordering silently destroyed the shipped registry: wineserver's
 * init_registry() (server/main.c:268) found no system.reg, built an EMPTY
 * registry, and its first save then overwrote the 3.7MB / 17,479-key file the
 * template had just written -- ml587's device prefix was left with 24 keys.
 * Everything registry-backed broke on a fresh install while a hand-maintained
 * dev prefix kept working, which is why this hid for so long: no WinRT
 * ActivatableClassId (Thumper aborts on RoGetActivationFactory for
 * Windows.Gaming.Input.Gamepad), and no Fonts keys (the #61/#70 dwrite fix).
 */
void madeira_seed_prefix_if_needed(const char *prefix_path) {
    @autoreleasepool {
        if (!prefix_path) return;
        NSString *prefix = [NSString stringWithUTF8String:prefix_path];
        NSString *stamp = [prefix stringByAppendingPathComponent:@".update-timestamp"];
        NSFileManager *fm = [NSFileManager defaultManager];

        [fm createDirectoryAtPath:prefix withIntermediateDirectories:YES attributes:nil error:nil];

        if (![fm fileExistsAtPath:stamp]) {
            NSString *tgz = [[NSBundle mainBundle] pathForResource:@"prefix-template" ofType:@"tar.gz"];
            if (!tgz) {
                LOG("prefix-template.tar.gz missing from bundle!");
            } else {
                LOG("Seeding prefix from %{public}s", tgz.UTF8String);
                if (madeira_extract_prefix_tgz(tgz.UTF8String, prefix_path) != 0) {
                    LOG("prefix extraction FAILED");
                } else {
                    LOG("prefix seeded to %{public}s", prefix_path);
                }
            }
        }

        // (Re)create dosdevices/c: -> ../drive_c. The tarball omits
        // dosdevices because Mac's z: -> / is wrong here.
        NSString *dosdev = [prefix stringByAppendingPathComponent:@"dosdevices"];
        [fm createDirectoryAtPath:dosdev withIntermediateDirectories:YES attributes:nil error:nil];
        NSString *cLink = [dosdev stringByAppendingPathComponent:@"c:"];
        [fm removeItemAtPath:cLink error:nil];
        [fm createSymbolicLinkAtPath:cLink withDestinationPath:@"../drive_c" error:nil];

        /* ml666: repair the usersmadeira escaping damage BEFORE anything reads
         * the registry, then the (now scoped) ml581 legacy cleanup. */
        madeira_repair_profile( prefix );
        /* ml581: see madeira_undo_appdata_skeleton() above. */
        madeira_undo_appdata_skeleton( prefix );
        madeira_ensure_locallow( prefix );
        madeira_seed_fonts_and_vc80_amd64( fm, prefix );
    }
}

/* ===========================================================================
 * WoW64: 32-bit (i386) targets. See docs/WOW64.md.
 *
 * Everything below is used only when the bundle carries the i386 Wine set
 * (app/Madeira/i386-windows, built by build/wine-i386/build.sh) or when the
 * target exe is an i386 PE. A bundle without i386-windows and a 64-bit
 * target take exactly the code path they took before.
 * ========================================================================= */
#define MADEIRA_IMAGE_FILE_MACHINE_I386 0x014c

/* build/ntdll-unix/virtual_ios.c: nonzero when this session's MAIN image is
 * 32-bit. The unix side reserves the process's guest window before its first
 * TEB when it is set; the image's machine is not known there until later. */
extern int ios_main_image_i386;

/* IMAGE_FILE_HEADER.Machine, read off disk (MZ -> e_lfanew -> "PE\0\0").
 * 0 on any read or format failure. */
static uint16_t madeira_pe_machine(const char *unix_path)
{
    unsigned char dos[64], pe[6];
    uint16_t machine = 0;
    FILE *f;

    if (!unix_path || !*unix_path || !(f = fopen(unix_path, "rb"))) return 0;
    if (fread(dos, 1, sizeof(dos), f) == sizeof(dos) && dos[0] == 'M' && dos[1] == 'Z')
    {
        uint32_t lfanew = (uint32_t)dos[0x3c] | ((uint32_t)dos[0x3d] << 8) |
                          ((uint32_t)dos[0x3e] << 16) | ((uint32_t)dos[0x3f] << 24);
        if (lfanew <= (16u << 20) && fseek(f, (long)lfanew, SEEK_SET) == 0 &&
            fread(pe, 1, sizeof(pe), f) == sizeof(pe) &&
            pe[0] == 'P' && pe[1] == 'E' && !pe[2] && !pe[3])
            machine = (uint16_t)(pe[4] | (pe[5] << 8));
    }
    fclose(f);
    return machine;
}

/* The machine of the file the launch below will run, or 0 when it is not
 * known (the caller then treats the target as 64-bit, as before).
 *  - "C:\...\app.exe": the file under the prefix's drive_c.
 *  - a bare name: the launch resolves it as C:\windows\system32\<name>, which
 *    links the 64-bit farms, so a name present in a 64-bit farm is not probed
 *    any further. Otherwise it is looked up in the bundle's i386-windows. To
 *    launch the 32-bit build of a name both sets carry (explorer.exe, cmd.exe),
 *    give its full path, C:\windows\syswow64\<name>.
 *  - any other form (another drive, a relative Win32 path) is not probed. */
static uint16_t madeira_target_machine(const char *exe, const char *prefix, NSString *bundle)
{
    static const char * const farms64[] = { "aarch64-windows", "arm64ec-windows" };
    char probe[PATH_MAX];
    size_t skip;

    if ((exe[0] == 'C' || exe[0] == 'c') && exe[1] == ':' && exe[2] == '\\')
    {
        skip = strlen(prefix) + strlen("/drive_c/");
        if (snprintf(probe, sizeof(probe), "%s/drive_c/%s", prefix, exe + 3) >= (int)sizeof(probe))
            return 0;
        for (char *p = probe + skip; *p; p++) if (*p == '\\') *p = '/';
    }
    else if (strchr(exe, '\\') || (exe[0] && exe[1] == ':'))
        return 0;
    else
    {
        for (size_t i = 0; i < sizeof(farms64) / sizeof(farms64[0]); i++)
        {
            snprintf(probe, sizeof(probe), "%s/%s/%s", bundle.fileSystemRepresentation, farms64[i], exe);
            if (access(probe, R_OK) == 0) return 0;
        }
        if (snprintf(probe, sizeof(probe), "%s/i386-windows/%s", bundle.fileSystemRepresentation, exe)
                >= (int)sizeof(probe))
            return 0;
    }
    return madeira_pe_machine(probe);
}

/* The bundle carries the i386 Wine set. */
static BOOL madeira_bundle_has_i386(NSString *bundle)
{
    NSString *ntdll = [bundle stringByAppendingPathComponent:@"i386-windows/ntdll.dll"];
    return access(ntdll.fileSystemRepresentation, R_OK) == 0;
}

/* madeira-bcd cmd-bundle (round 579): the Windows command interpreter is the one
 * program whose session core cannot be left to the name heuristic. Library.swift
 * starts every .bat/.cmd entry as "C:\windows\system32\cmd.exe /c <file>" (a
 * backslash path, so the heuristic picks arm64ec-windows), but cmd.exe is shipped
 * only as a native aarch64 program (aarch64-windows and i386-windows; arm64ec-windows
 * has none) and a native ARM64 process cannot load the ARM64EC kernel32: "could not
 * load kernel32.dll, status c000007b" after 0.5 s (VSWVB0, JFFRDP, H579EM, ZSRKW7).
 * For cmd.exe, and only for it, the core is aarch64-windows, the one the Wine desktop
 * (explorer.exe /desktop=... cmd /c x.bat) already runs it in. The name is matched
 * exactly, bare or in system32: a program of the user's own called cmd.exe lives in
 * another folder and keeps the heuristic's answer. A pure function, compiled by
 * tests/host/check-cmd-bundle.py. */
static BOOL madeira_target_is_cmd(const char *exe)
{
    return exe && (strcasecmp(exe, "cmd.exe") == 0 ||
                   strcasecmp(exe, "C:\\windows\\system32\\cmd.exe") == 0);
}

/* C:\windows\syswow64: the i386 farm, the Windows name for this pattern. A
 * 32-bit process's system32 is redirected here, and the unix loader's
 * machine -> directory mapping looks here for the 32-bit ntdll, kernel32 and
 * the rest. Linked for every session once the bundle has the i386 set, so a
 * 64-bit launcher can start a 32-bit child. */
static void madeira_link_syswow64(NSFileManager *fm, NSString *prefix, NSString *bundle)
{
    NSString *farmDir = [prefix stringByAppendingPathComponent:@"drive_c/windows/syswow64"];
    NSString *source = [bundle stringByAppendingPathComponent:@"i386-windows"];
    int linked = 0;

    [fm createDirectoryAtPath:farmDir withIntermediateDirectories:YES attributes:nil error:nil];
    for (NSString *f in [fm contentsOfDirectoryAtPath:source error:nil])
    {
        if ([f hasPrefix:@"."]) continue;
        NSString *dst = [farmDir stringByAppendingPathComponent:f];
        [fm removeItemAtPath:dst error:nil];  /* self-heal stale links on reinstall */
        if ([fm createSymbolicLinkAtPath:dst
                     withDestinationPath:[source stringByAppendingPathComponent:f] error:nil])
            linked++;
    }
    dprintf(STDERR_FILENO, "[WineProc] Farm syswow64: %d links -> i386-windows\n", linked);
}

/* <system_dir>\wbem: syswow64\wbem from the i386 farm for 32-bit targets,
 * system32\wbem from the session's farm for 64-bit ones. The farms are flat,
 * but WMI's registered InprocServer32 paths are C:\windows\system32\wbem\<name>
 * (wine.inf installs these modules there), so CoCreateInstance(CLSID_WbemLocator)
 * -- for example dxdiagn asking WMI about the display adapter, or a game's
 * GPU requirement check -- fails with c0000135 when the subdirectory is
 * empty. The list is wine.inf's. */
static void madeira_link_wbem(NSFileManager *fm, NSString *prefix, NSString *system_dir, NSString *source)
{
    static const char * const wbem[] = { "wbemprox.dll", "wbemdisp.dll", "wmiutils.dll",
                                         "wmic.exe", "mofcomp.exe" };
    NSString *dir = [[prefix stringByAppendingPathComponent:@"drive_c/windows"]
                        stringByAppendingPathComponent:[system_dir stringByAppendingPathComponent:@"wbem"]];
    int linked = 0;

    [fm createDirectoryAtPath:dir withIntermediateDirectories:YES attributes:nil error:nil];
    for (size_t i = 0; i < sizeof(wbem) / sizeof(wbem[0]); i++)
    {
        NSString *n = [NSString stringWithUTF8String:wbem[i]];
        NSString *src = [source stringByAppendingPathComponent:n];
        NSString *dst = [dir stringByAppendingPathComponent:n];
        [fm removeItemAtPath:dst error:nil];
        if (![fm fileExistsAtPath:src]) continue;
        if ([fm createSymbolicLinkAtPath:dst withDestinationPath:src error:nil]) linked++;
    }
    dprintf(STDERR_FILENO, "[WineProc] %s\\wbem: %d/%zu links\n", system_dir.UTF8String,
            linked, sizeof(wbem) / sizeof(wbem[0]));
}

static void madeira_link_syswow64_wbem(NSFileManager *fm, NSString *prefix, NSString *bundle)
{
    madeira_link_wbem(fm, prefix, @"syswow64", [bundle stringByAppendingPathComponent:@"i386-windows"]);
}

/* ml1275: C:\windows\mono\mono-2.0 -> the bundle's Wine Mono, when the build
 * carries one (the "Bundle Wine Mono" build phase); otherwise the copy Madeira
 * downloaded from WineHQ (WineMono.swift, Library/Application Support/WineMono/
 * wine-mono), as release builds carry none. mscoree looks there first
 * (wine/dlls/mscoree/metahost.c get_mono_path_local) for bin\libmono-2.0-x86.dll
 * in a 32-bit process and bin\libmono-2.0-x86_64.dll in a 64-bit one; without
 * it every .NET Framework program dies with "Wine Mono is not installed".
 * The link names the bundle path, which changes on every reinstall, so it is
 * renewed for every session. A real directory there (Mono installed by hand
 * or by its MSI) is the prefix's own and is left alone. */
static void madeira_link_wine_mono(NSFileManager *fm, NSString *prefix, NSString *bundle)
{
    NSString *source = [bundle stringByAppendingPathComponent:@"wine-mono"];
    NSString *monoDir = [prefix stringByAppendingPathComponent:@"drive_c/windows/mono"];
    if (access([source stringByAppendingPathComponent:@"bin/libmono-2.0-x86.dll"].fileSystemRepresentation, R_OK) != 0)
    {
        NSString *support = NSSearchPathForDirectoriesInDomains(NSApplicationSupportDirectory, NSUserDomainMask, YES).firstObject;
        NSString *downloaded = [support stringByAppendingPathComponent:@"WineMono/wine-mono"];
        if (access([downloaded stringByAppendingPathComponent:@"bin/libmono-2.0-x86.dll"].fileSystemRepresentation, R_OK) == 0)
            source = downloaded;
    }
    NSString *dst = [monoDir stringByAppendingPathComponent:@"mono-2.0"];
    struct stat st;

    if (access([source stringByAppendingPathComponent:@"bin/libmono-2.0-x86.dll"].fileSystemRepresentation, R_OK) != 0)
    {
        /* A link an earlier build with Mono made points into a bundle that is gone. */
        if (lstat(dst.fileSystemRepresentation, &st) == 0 && S_ISLNK(st.st_mode))
            [fm removeItemAtPath:dst error:nil];
        dprintf(STDERR_FILENO, "[wine-mono] ml1275 neither this build nor a download has Wine Mono -- a .NET Framework game then exits 0xffffffff with \"Wine Mono is not installed\"; Settings > .NET Framework downloads it\n");
        return;
    }
    if (lstat(dst.fileSystemRepresentation, &st) == 0 && !S_ISLNK(st.st_mode))
    {
        dprintf(STDERR_FILENO, "[wine-mono] ml1275 C:\\windows\\mono\\mono-2.0 is the prefix's own copy; the bundled one is not linked\n");
        return;
    }
    [fm createDirectoryAtPath:monoDir withIntermediateDirectories:YES attributes:nil error:nil];
    [fm removeItemAtPath:dst error:nil];  /* self-heal a stale link on reinstall */
    if ([fm createSymbolicLinkAtPath:dst withDestinationPath:source error:nil])
        dprintf(STDERR_FILENO, "[wine-mono] ml1275 C:\\windows\\mono\\mono-2.0 -> %s wine-mono\n",
                [source hasPrefix:bundle] ? "bundle" : "downloaded");
    else
        dprintf(STDERR_FILENO, "[wine-mono] ml1275 FAILED to link C:\\windows\\mono\\mono-2.0 errno=%d\n", errno);
}

/* BCD: the folder Microsoft's x64 VC++ runtime DLLs are linked from this
 * session. The bundle's x86_64-vcruntime when it holds them (the owner's
 * builds, which fetch them at build time: unchanged); else the copy the app
 * downloaded from Microsoft (VCRuntimeDownload.swift) into
 * Library/Application Support/VCRuntime/x86_64; else the bundle's folder as
 * before, with nothing to link. vcruntime140.dll marks a folder that holds
 * them (VCRuntimeInstaller.marker). `support` is the Application Support
 * directory. */
static NSString *madeira_vcruntime_dir(NSString *bundle, NSString *support)
{
    NSString *bundled = [bundle stringByAppendingPathComponent:@"x86_64-vcruntime"];
    NSString *downloaded = support ? [support stringByAppendingPathComponent:@"VCRuntime/x86_64"] : nil;
    const char *source = "none";
    NSString *dir = bundled;
    if (access([bundled stringByAppendingPathComponent:@"vcruntime140.dll"].fileSystemRepresentation, R_OK) == 0) {
        source = "bundled";
    } else if (downloaded &&
               access([downloaded stringByAppendingPathComponent:@"vcruntime140.dll"].fileSystemRepresentation, R_OK) == 0) {
        source = "downloaded";
        dir = downloaded;
    }
    dprintf(STDERR_FILENO, "[WineProc] VC++ runtime: %s\n", source);
    return dir;
}

/* BCD (2026-10-25, ported from Madeira #359): the DLLs the VC++ overlay links, as a list of names plus name -> file. Two places:
 * Documents/vcruntime, where a person can put their own copies of Microsoft's fourteen runtime DLLs with the Files app (the
 * bundle is sealed by the code signature and the download is the app's own), looked at first so a file there shadows the
 * build's; then `dir`, madeira_vcruntime_dir's folder. Only .dll files count, from either place (a placeholder or marker
 * file in the folder used to be linked into system32 and counted as a DLL), and from Documents only the fourteen names of
 * Microsoft's package, so a stray kernel32.dll there cannot replace a Wine builtin. No Documents/vcruntime: exactly the
 * build's folder, as before. `user` is Documents/vcruntime (nil: none); *fromDocs counts the names taken from it. */
static NSArray<NSString *> *madeira_vcruntime_dlls(NSFileManager *fm, NSString *dir, NSString *user,
                                                   NSMutableDictionary<NSString *, NSString *> *home, NSUInteger *fromDocs)
{
    static NSSet<NSString *> *known;
    static dispatch_once_t once;
    dispatch_once(&once, ^{
        known = [NSSet setWithArray:@[ @"concrt140.dll", @"msvcp140.dll", @"msvcp140_1.dll", @"msvcp140_2.dll",
                                       @"msvcp140_atomic_wait.dll", @"msvcp140_codecvt_ids.dll", @"vcamp140.dll",
                                       @"vccorlib140.dll", @"vcomp140.dll", @"vcruntime140.dll", @"vcruntime140_1.dll",
                                       @"vcruntime140_threads.dll", @"mfc140.dll", @"mfc140u.dll" ]];
    });
    NSMutableArray<NSString *> *names = [NSMutableArray array];
    for (NSString *src in (user.length ? @[ user, dir ] : @[ dir ])) {
        BOOL fromUser = user && [src isEqualToString:user];
        for (NSString *f in [fm contentsOfDirectoryAtPath:src error:nil]) {
            NSString *key = f.lowercaseString;
            if (![key hasSuffix:@".dll"]) continue;
            if (fromUser && ![known containsObject:key]) continue;
            if (home[key]) continue;   /* Documents is looked at first */
            home[key] = [src stringByAppendingPathComponent:f];
            [names addObject:f];
            if (fromUser && fromDocs) (*fromDocs)++;
        }
    }
    return names;
}

/* BCD (2026-10-23, bcd-games issue #13): env.MADEIRA_VCRT_ARM64_KEY = 1 writes the ARM64 VC++ "Installed" marker
 * (server_ios.c, ios_vcrt_arm64_key_publish) with a fixed version, 14.44.35211.0. Unreal's bootstrap programs compare
 * that version with the one they were built against, so a newer game wants a newer number (Dead as Disco refused
 * 14.42.34433.0 and started with 14.44.35211.0). When the VC++ runtime this session uses (the bundle's, else the
 * download) is newer than the fixed version, the marker takes the runtime's own file version instead: the value "1"
 * is replaced by that version for this session (the switch is cleared at the end of every session). Any other value
 * (an explicit version, 0, unset) is left as it is. The version is VS_FIXEDFILEINFO's file version of vcruntime140.dll,
 * found by its signature; a file that has none, or not a 14.x one, keeps the fixed version. Logs one line. */
static void madeira_vcrt_arm64_version(NSString *bundle, NSString *support)
{
    const char *sw = getenv("MADEIRA_VCRT_ARM64_KEY");
    if (!sw || strcmp(sw, "1")) return;
    NSString *dll = [madeira_vcruntime_dir(bundle, support) stringByAppendingPathComponent:@"vcruntime140.dll"];
    NSData *data = [NSData dataWithContentsOfFile:dll options:NSDataReadingMappedIfSafe error:nil];
    const uint8_t *p = data.bytes;
    NSUInteger n = data.length;
    unsigned v[4] = { 0, 0, 0, 0 };
    BOOL found = NO;
    for (NSUInteger i = 0; p && i + 16 <= n && n <= 16u * 1024 * 1024; i += 4) {
        uint32_t sig, ms, ls;
        memcpy(&sig, p + i, 4);
        if (sig != 0xFEEF04BDu) continue;            /* VS_FIXEDFILEINFO.dwSignature */
        memcpy(&ms, p + i + 8, 4);                   /* dwFileVersionMS, dwFileVersionLS */
        memcpy(&ls, p + i + 12, 4);
        v[0] = ms >> 16; v[1] = ms & 0xffff; v[2] = ls >> 16; v[3] = ls & 0xffff;
        found = (v[0] == 14 && v[1] < 100);
        break;
    }
    static const unsigned fixed[4] = { 14, 44, 35211, 0 };   /* server_ios.c's version for "1" */
    BOOL newer = NO;
    if (found)
        for (int k = 0; k < 4; k++)
            if (v[k] != fixed[k]) { newer = v[k] > fixed[k]; break; }
    if (newer) {
        char version[32];
        snprintf(version, sizeof(version), "%u.%u.%u.%u", v[0], v[1], v[2], v[3]);
        setenv("MADEIRA_VCRT_ARM64_KEY", version, 1);
        dprintf(STDERR_FILENO, "[vcrt-arm64] MADEIRA_VCRT_ARM64_KEY=1: the marker takes this session's VC++ runtime version %s\n", version);
    } else if (found) {
        dprintf(STDERR_FILENO, "[vcrt-arm64] MADEIRA_VCRT_ARM64_KEY=1: VC++ runtime %u.%u.%u.%u is not newer than 14.44.35211.0: the marker keeps 14.44.35211.0\n",
                v[0], v[1], v[2], v[3]);
    } else {
        dprintf(STDERR_FILENO, "[vcrt-arm64] MADEIRA_VCRT_ARM64_KEY=1: no VC++ runtime version found (%s): the marker keeps 14.44.35211.0\n",
                data ? "no 14.x file version in vcruntime140.dll" : "vcruntime140.dll not there");
    }
}

/* madeira-bcd (round 591): env.MADEIRA_MFC_DOCK = 1 puts Microsoft's MFC (mfc140.dll, mfc140u.dll) where a BCD Dock
 * session's 64-bit game looks for it. A Dock session runs Valve's client in an aarch64 session (explorer.exe is the target),
 * so the VC++ overlay in the session setup, which links MFC into system32, never runs for it. The game Valve's client starts
 * is an x64 program with its own ARM64EC ntdll: it searches system32 (the aarch64 farm, no MFC in it) and, for a name it does
 * not find there, C:\windows\sysx64 (ntdll loader.c, "mixed-arch fallback"). So the two MFC DLLs are linked into sysx64, from
 * the folder the overlay uses (the bundle's, else the app's download), and nothing else of the VC++ runtime: the rest stays
 * Wine's builtins, as in a Dock session today. Horizon Zero Dawn and Age of Empires II HD stopped at once with c0000135
 * ("import_dll Library mfc140.dll ... not found") without them. Off by default: with the switch unset nothing is linked and the
 * VC++ folder is not even looked at. An ARM64EC session (a direct start) changes nothing either: its system32 already has MFC.
 * env.MADEIRA_MFC = 0 keeps MFC out here too. A link an earlier session made for this switch is removed when the session does
 * not ask for it, so one game's line never leaves MFC in the next game's sysx64 (the prefix is shared). Logs one line. */
static void madeira_mfc_dock(NSFileManager *fm, NSString *sysx64, NSString *bundle, NSString *support, BOOL ec_session)
{
    NSString *const names[] = { @"mfc140.dll", @"mfc140u.dll" };
    const char *dockEnv = getenv("MADEIRA_MFC_DOCK");
    const char *allEnv = getenv("MADEIRA_MFC");   /* the system32 switch; 0 keeps MFC out */
    const BOOL asked = dockEnv && dockEnv[0] == '1';
    const BOOL mfcOff = allEnv && allEnv[0] == '0';
    const BOOL link = asked && !ec_session && !mfcOff;
    NSString *dir = link ? madeira_vcruntime_dir(bundle, support) : nil;
    NSMutableArray<NSString *> *linked = [NSMutableArray array];
    for (size_t i = 0; i < sizeof(names) / sizeof(names[0]); i++) {
        NSString *dst = [sysx64 stringByAppendingPathComponent:names[i]];
        NSString *src = dir ? [dir stringByAppendingPathComponent:names[i]] : nil;
        NSString *type = [[fm attributesOfItemAtPath:dst error:nil] fileType];   /* the link itself, not its target */
        if (src && access(src.fileSystemRepresentation, R_OK) == 0) {
            if (type && ![type isEqualToString:NSFileTypeSymbolicLink]) {
                dprintf(STDERR_FILENO, "[WineProc] MADEIRA_MFC_DOCK=1: %s in sysx64 is a file, not a link: left as it is\n", names[i].UTF8String);
                continue;
            }
            [fm removeItemAtPath:dst error:nil];   /* self-heal a stale link on reinstall */
            if ([fm createSymbolicLinkAtPath:dst withDestinationPath:src error:nil]) [linked addObject:names[i]];
            continue;
        }
        /* Not asked for, or not there: only a link into the VC++ folders (what this switch makes) goes. */
        NSString *target = [type isEqualToString:NSFileTypeSymbolicLink] ? [fm destinationOfSymbolicLinkAtPath:dst error:nil] : nil;
        if (target && ([target containsString:@"/VCRuntime/x86_64/"] || [target containsString:@"/x86_64-vcruntime/"])) {
            [fm removeItemAtPath:dst error:nil];
            dprintf(STDERR_FILENO, "[WineProc] MADEIRA_MFC_DOCK: %s removed from sysx64 (a link of an earlier session; this one does not ask for it)\n",
                    names[i].UTF8String);
        }
    }
    if (!asked) return;
    if (ec_session)
        dprintf(STDERR_FILENO, "[WineProc] MADEIRA_MFC_DOCK=1: an ARM64EC session already links MFC into system32: nothing linked into sysx64\n");
    else if (mfcOff)
        dprintf(STDERR_FILENO, "[WineProc] MADEIRA_MFC_DOCK=1 but MADEIRA_MFC=0: MFC stays out\n");
    else if (linked.count)
        dprintf(STDERR_FILENO, "[WineProc] MADEIRA_MFC_DOCK=1: %s linked into sysx64 (from the %s VC++ runtime)\n",
                [linked componentsJoinedByString:@" "].UTF8String, [dir hasPrefix:bundle] ? "bundled" : "downloaded");
    else
        dprintf(STDERR_FILENO, "[WineProc] MADEIRA_MFC_DOCK=1: no mfc140.dll / mfc140u.dll in the VC++ runtime folder (not downloaded): nothing linked\n");
}

/* C:\windows\winsxs for 32-bit processes: the x86 side-by-side assemblies Wine
 * ships. Re-seeded every session, because the links name the bundle path.
 *
 * The prefix has no winsxs directory: the template does not carry one and
 * this port never runs wineboot's fake-DLL install, which is what builds it on
 * a normal Wine prefix. Without it no 32-bit program gets a Common-Controls
 * 6.0 activation context (comdlg32 gives up in DllMain with 14001), and a
 * program built with Visual Studio 2005/2008, which carries its CRT as a
 * side-by-side dependency ("Microsoft.VC80.CRT"), fails the activation
 * context for every DLL with the same dependency.
 *
 * This writes what dlls/setupapi/fakedll.c writes for each WINE_MANIFEST
 * assembly in the tree:
 *   windows\winsxs\manifests\<DIR>.manifest
 *   windows\winsxs\<DIR>\<file>          (linked from the i386 farm)
 *   <DIR> = x86_<lower-case name>_<publicKeyToken>_<version>_none_deadbeef
 * with the architecture filled into processorArchitecture, because actctx.c
 * checks the identity in the file against the one in its name. actctx.c pins
 * only major.minor, and accepts any build/revision >= the one requested, so
 * one assembly per major.minor serves every service pack of it.
 *
 * Only the x86 architecture is written: ntdll looks for "x86_" assemblies in
 * a 32-bit process and for "arm64_"/"amd64_" ones in 64-bit processes, so
 * 64-bit processes in the same prefix see no difference. An assembly whose
 * first file is missing from the i386 farm is skipped: a manifest without its
 * DLL would redirect that DLL's loads into an empty directory. */
static void madeira_seed_winsxs_x86(NSFileManager *fm, NSString *prefix, NSString *bundle)
{
    struct sxs_file { const char *in_assembly; const char *in_farm; };
    struct sxs_assembly { const char *name, *lname, *key, *version; struct sxs_file files[4]; };
    static const char *comctl32_body =
        "    <windowClass>Button</windowClass>\n"
        "    <windowClass>ButtonListBox</windowClass>\n"
        "    <windowClass>ComboBoxEx32</windowClass>\n"
        "    <windowClass>ComboLBox</windowClass>\n"
        "    <windowClass>ComboBox</windowClass>\n"
        "    <windowClass>Edit</windowClass>\n"
        "    <windowClass>ListBox</windowClass>\n"
        "    <windowClass>NativeFontCtl</windowClass>\n"
        "    <windowClass>ReBarWindow32</windowClass>\n"
        "    <windowClass>ScrollBar</windowClass>\n"
        "    <windowClass>Static</windowClass>\n"
        "    <windowClass>SysAnimate32</windowClass>\n"
        "    <windowClass>SysDateTimePick32</windowClass>\n"
        "    <windowClass>SysHeader32</windowClass>\n"
        "    <windowClass>SysIPAddress32</windowClass>\n"
        "    <windowClass>SysLink</windowClass>\n"
        "    <windowClass>SysListView32</windowClass>\n"
        "    <windowClass>SysMonthCal32</windowClass>\n"
        "    <windowClass>SysPager</windowClass>\n"
        "    <windowClass>SysTabControl32</windowClass>\n"
        "    <windowClass>SysTreeView32</windowClass>\n"
        "    <windowClass>ToolbarWindow32</windowClass>\n"
        "    <windowClass>msctls_hotkey32</windowClass>\n"
        "    <windowClass>msctls_progress32</windowClass>\n"
        "    <windowClass>msctls_statusbar32</windowClass>\n"
        "    <windowClass>msctls_trackbar32</windowClass>\n"
        "    <windowClass>msctls_updown32</windowClass>\n"
        "    <windowClass>tooltips_class32</windowClass>\n";
    /* One entry per WINE_MANIFEST resource in the Wine tree (the file the
     * values come from is named on each entry). */
    static const struct sxs_assembly asms[] = {
        /* dlls/comctl32_v6/comctl32.manifest (index 0: gets comctl32_body) */
        { "Microsoft.Windows.Common-Controls", "microsoft.windows.common-controls",
          "6595b64144ccf1df", "6.0.2600.2982", { { "comctl32.dll", "comctl32_v6.dll" } } },
        /* dlls/msvcr80/msvcr80.manifest */
        { "Microsoft.VC80.CRT", "microsoft.vc80.crt", "1fc8b3b9a1e18e3b", "8.0.50727.9672",
          { { "msvcr80.dll", "msvcr80.dll" }, { "msvcp80.dll", "msvcp80.dll" },
            { "msvcm80.dll", "msvcm80.dll" } } },
        /* dlls/msvcr90/msvcr90.manifest */
        { "Microsoft.VC90.CRT", "microsoft.vc90.crt", "1fc8b3b9a1e18e3b", "9.0.30729.6161",
          { { "msvcr90.dll", "msvcr90.dll" }, { "msvcp90.dll", "msvcp90.dll" },
            { "msvcm90.dll", "msvcm90.dll" } } },
        /* dlls/atl80/atl80.manifest */
        { "Microsoft.VC80.ATL", "microsoft.vc80.atl", "1fc8b3b9a1e18e3b", "8.0.50727.4053",
          { { "atl80.dll", "atl80.dll" } } },
        /* dlls/atl90/atl90.manifest */
        { "Microsoft.VC90.ATL", "microsoft.vc90.atl", "1fc8b3b9a1e18e3b", "9.0.30729.6161",
          { { "atl90.dll", "atl90.dll" } } },
        /* dlls/gdiplus/gdiplus.manifest and gdiplus11.manifest: one DLL */
        { "Microsoft.Windows.GdiPlus", "microsoft.windows.gdiplus", "6595b64144ccf1df",
          "1.0.6000.16386", { { "gdiplus.dll", "gdiplus.dll" } } },
        { "Microsoft.Windows.GdiPlus", "microsoft.windows.gdiplus", "6595b64144ccf1df",
          "1.1.7601.23038", { { "gdiplus.dll", "gdiplus.dll" } } },
        /* dlls/msxml3, msxml4 and msxml6 manifests */
        { "Microsoft-Windows-MSXML30", "microsoft-windows-msxml30", "31bf3856ad364e35",
          "6.0.6000.16386", { { "msxml3.dll", "msxml3.dll" } } },
        { "Microsoft.MSXML2", "microsoft.msxml2", "6bd6b9abf345378f", "4.1.0.0",
          { { "msxml4.dll", "msxml4.dll" } } },
        { "Microsoft-Windows-MSXML60", "microsoft-windows-msxml60", "31bf3856ad364e35",
          "6.0.6000.16386", { { "msxml6.dll", "msxml6.dll" } } },
    };
    NSString *winsxs = [prefix stringByAppendingPathComponent:@"drive_c/windows/winsxs"];
    NSString *manifests = [winsxs stringByAppendingPathComponent:@"manifests"];
    NSString *source = [bundle stringByAppendingPathComponent:@"i386-windows"];
    const size_t count = sizeof(asms) / sizeof(asms[0]);
    int seeded = 0, skipped = 0;

    [fm createDirectoryAtPath:manifests withIntermediateDirectories:YES attributes:nil error:nil];
    for (size_t a = 0; a < count; a++)
    {
        const struct sxs_assembly *def = &asms[a];
        const char *body = a == 0 ? comctl32_body : NULL;
        NSString *first = [source stringByAppendingPathComponent:
                           [NSString stringWithUTF8String:def->files[0].in_farm]];
        if (![fm fileExistsAtPath:first])
        {
            dprintf(STDERR_FILENO, "[WineProc] winsxs: x86 %s skipped, i386-windows has no %s\n",
                    def->name, def->files[0].in_farm);
            skipped++;
            continue;
        }
        NSString *dirName = [NSString stringWithFormat:@"x86_%s_%s_%s_none_deadbeef",
                             def->lname, def->key, def->version];
        NSString *asmDir = [winsxs stringByAppendingPathComponent:dirName];
        NSString *manifest = [manifests stringByAppendingPathComponent:
                              [dirName stringByAppendingString:@".manifest"]];
        [fm createDirectoryAtPath:asmDir withIntermediateDirectories:YES attributes:nil error:nil];

        /* Build the manifest and the directory together so the <file> list
         * and the directory cannot disagree: a file missing from the farm is
         * left out of both. UTF-8, LF, no BOM. */
        NSMutableString *text = [NSMutableString stringWithString:
            @"<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>\n"
            @"<assembly xmlns=\"urn:schemas-microsoft-com:asm.v1\" manifestVersion=\"1.0\">\n"];
        [text appendFormat:@"  <assemblyIdentity type=\"win32\" name=\"%s\" version=\"%s\" "
                           @"processorArchitecture=\"x86\" publicKeyToken=\"%s\"/>\n",
                           def->name, def->version, def->key];
        BOOL ok = YES;
        for (size_t f = 0; f < sizeof(def->files) / sizeof(def->files[0]) && def->files[f].in_assembly; f++)
        {
            NSString *src = [source stringByAppendingPathComponent:
                             [NSString stringWithUTF8String:def->files[f].in_farm]];
            NSString *link = [asmDir stringByAppendingPathComponent:
                              [NSString stringWithUTF8String:def->files[f].in_assembly]];
            [fm removeItemAtPath:link error:nil];  /* the bundle path changes on reinstall */
            if (![fm fileExistsAtPath:src]) continue;
            if (![fm createSymbolicLinkAtPath:link withDestinationPath:src error:nil]) { ok = NO; break; }
            if (body)
                [text appendFormat:@"  <file name=\"%s\">\n%s  </file>\n", def->files[f].in_assembly, body];
            else
                [text appendFormat:@"  <file name=\"%s\"/>\n", def->files[f].in_assembly];
        }
        [text appendString:@"</assembly>\n"];
        if (!ok || ![[text dataUsingEncoding:NSUTF8StringEncoding] writeToFile:manifest atomically:YES])
        {
            dprintf(STDERR_FILENO, "[WineProc] winsxs: x86 %s FAILED\n", def->name);
            skipped++;
            continue;
        }
        seeded++;
    }
    dprintf(STDERR_FILENO, "[WineProc] winsxs: %d/%zu x86 assemblies seeded, %d skipped\n",
            seeded, count, skipped);
}

/* FEX's WOW64 module cannot call sysctl, and without an answer it assumes the
 * newest cores' feature set. A wrong "present" is silent corruption, not a
 * crash (FEAT_AFP claimed on a core without it leaves FPCR.NEP RES0, so every
 * scalar SSE operation zeroes the upper lanes of its destination), so the app
 * asks and passes the answers in FEX_MADEIRA_HOSTPROBE, for every session.
 * Both FEX modules read it (FEX Source/Windows/Common/CPUFeatures.cpp): an
 * explicit 0 turns a feature off. The 64-bit module ignoring it made A12/A13
 * devices, which lack FlagM/FlagM2, stop 64-bit games with 0xC000001D. The
 * ARM64EC module also takes LRCPC2 and AFP from it (ml1231). "?" means the
 * sysctl does not exist and keeps FEX's assumption. */
static void madeira_publish_host_probe(void)
{
    static const struct { const char *key, *sysctl; } probes[] = {
        { "AFP",     "hw.optional.arm.FEAT_AFP" },
        { "FLAGM",   "hw.optional.arm.FEAT_FlagM" },
        { "FLAGM2",  "hw.optional.arm.FEAT_FlagM2" },
        { "FCMA",    "hw.optional.arm.FEAT_FCMA" },
        { "RCPC",    "hw.optional.arm.FEAT_LRCPC" },
        { "LRCPC2",  "hw.optional.arm.FEAT_LRCPC2" },   /* ml1231: the ARM64EC module needs an explicit 1 */
        { "AES",     "hw.optional.arm.FEAT_AES" },
        { "PMULL",   "hw.optional.arm.FEAT_PMULL" },
        { "SHA",     "hw.optional.arm.FEAT_SHA256" },
        { "CRC",     "hw.optional.armv8_crc32" },
        { "ATOMICS", "hw.optional.arm.FEAT_LSE" },
        /* madeira-bcd: reported, not yet used by either module: which of FEX's other
         * host features this device has (FRINTTS, CSSC, RPRES, MOPS, ECV, WFXT), and
         * LSE2/LRCPC3 for its TSO code (which unaligned atomics trap; vector imm9). */
        { "LSE2",    "hw.optional.arm.FEAT_LSE2" },
        { "LRCPC3",  "hw.optional.arm.FEAT_LRCPC3" },
        { "FRINTTS", "hw.optional.arm.FEAT_FRINTTS" },
        { "RPRES",   "hw.optional.arm.FEAT_RPRES" },
        { "CSSC",    "hw.optional.arm.FEAT_CSSC" },
        { "MOPS",    "hw.optional.arm.FEAT_MOPS" },
        { "ECV",     "hw.optional.arm.FEAT_ECV" },
        { "WFXT",    "hw.optional.arm.FEAT_WFxT" },
    };
    char buf[256];
    size_t len = 0;

    buf[0] = 0;
    for (size_t i = 0; i < sizeof(probes) / sizeof(probes[0]); i++)
    {
        int32_t v = 0;
        size_t sz = sizeof(v);
        const char *val = sysctlbyname(probes[i].sysctl, &v, &sz, NULL, 0) == 0 ? (v ? "1" : "0") : "?";
        len += snprintf(buf + len, sizeof(buf) - len, "%s%s=%s", i ? "," : "", probes[i].key, val);
        if (len >= sizeof(buf)) return;
    }
    setenv("FEX_MADEIRA_HOSTPROBE", buf, 1);
    dprintf(STDERR_FILENO, "[WineProc] FEX host feature probe: %s\n", buf);
}

/* ---- madeira-bcd: the audio session and the app's activity, for the engine ----
 * (2026-10-18, docs/research/ios18-2026-10-18/gameplay.md sections 2.1 and 2.2)
 *
 * env.MADEIRA_AUDIO_RESUME = 1 (opt-in, off by default): game audio comes back
 * after an interruption. The session is set up once (Playback, active), and iOS
 * stops it for a phone call, Siri, an alarm, another app's sound, or when it
 * suspends the app; nothing started the output again, so the sound stayed off
 * until the game was restarted and an audio-clocked game could stall. These
 * observers report the events to the audio driver (audio_null_ios.c,
 * madeira_audio_session_event), which keeps its clients' clock running while
 * the output is stopped and starts the output again:
 *   - an interruption began: the output stops; the clock runs on;
 *   - the interruption ended, or the app is active again: the session is
 *     activated and the output started where a client plays;
 *   - the route changed: an output that stopped behind our back starts again;
 *   - media services were reset: category, session and a new output unit.
 * Log: "[audio] interruption began", "[audio] interruption ended, resumed=1"
 * (0: the output did not start; the next event tries again).
 *
 * env.MADEIRA_PAUSE_IN_BACKGROUND = 1 (opt-in, off by default): Metal work waits
 * while the app is not active (madeira_app_wait_active, called by winemetal
 * before a commit and a present, tools/patch-winemetal-pause-inactive.py). iOS
 * refuses GPU work from the background, and a game runs on for some seconds
 * after Home: what it submitted then was lost (a streamed texture could stay
 * black). Log: "[gpu-pause] ...".
 *
 * Neither switch set: no observer is registered and nothing here runs. */
extern int madeira_audio_session_event(int what);   /* build/ntdll-unix/audio_null_ios.c */

static int madeira_audio_resume_switch(void)
{
    const char *v = getenv("MADEIRA_AUDIO_RESUME");   /* 1: the app reports the audio session's interruptions to the audio driver, which restarts the output (audio_null_ios.c) */
    return v && v[0] == '1' && !v[1];
}

static int madeira_pause_switch(void)
{
    const char *v = getenv("MADEIRA_PAUSE_IN_BACKGROUND");   /* 1: Metal command buffers and presents wait while the app is not active, so iOS never refuses the game's GPU work from the background; unset/0: as before */
    return v && v[0] == '1' && !v[1];
}

static pthread_mutex_t g_madeira_active_lock = PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t g_madeira_active_cond = PTHREAD_COND_INITIALIZER;
static _Atomic int g_madeira_inactive;   /* 1 from resign-active to become-active, only with MADEIRA_PAUSE_IN_BACKGROUND */

static void madeira_set_inactive(int inactive)
{
    pthread_mutex_lock(&g_madeira_active_lock);
    atomic_store_explicit(&g_madeira_inactive, inactive, memory_order_release);
    if (!inactive) pthread_cond_broadcast(&g_madeira_active_cond);
    pthread_mutex_unlock(&g_madeira_active_lock);
}

/* winemetal calls this before it commits a command buffer and before a present
 * (tools/patch-winemetal-pause-inactive.py). While the app is active, and always
 * without the switch (the flag is then never set), it returns at once. Otherwise
 * it waits until the app is active again: never on the main thread, whose run
 * loop delivers the notification that ends the wait, and 10 minutes at most. */
int madeira_app_wait_active(const char *where)
{
    static _Atomic int said;
    struct timeval tv, t1;
    struct timespec until;
    int timed_out = 0;

    if (!atomic_load_explicit(&g_madeira_inactive, memory_order_acquire) || pthread_main_np()) return 0;
    gettimeofday(&tv, NULL);
    until.tv_sec = tv.tv_sec + 600;
    until.tv_nsec = (long)tv.tv_usec * 1000;
    pthread_mutex_lock(&g_madeira_active_lock);
    while (atomic_load_explicit(&g_madeira_inactive, memory_order_acquire) && !timed_out)
        timed_out = pthread_cond_timedwait(&g_madeira_active_cond, &g_madeira_active_lock, &until) == ETIMEDOUT;
    pthread_mutex_unlock(&g_madeira_active_lock);
    gettimeofday(&t1, NULL);
    if (atomic_fetch_add_explicit(&said, 1, memory_order_relaxed) < 24)
        dprintf(STDERR_FILENO, "[gpu-pause] %s waited %.1f s while the app was not active%s\n", where ? where : "?",
                (double)(t1.tv_sec - tv.tv_sec) + (double)(t1.tv_usec - tv.tv_usec) / 1e6,
                timed_out ? " (10 minutes: going on)" : "");
    return 1;
}

/* Activate the session and ask the driver to start the output again. */
static void madeira_audio_resume(const char *why, int always_log)
{
    NSError *err = nil;
    BOOL active = [[AVAudioSession sharedInstance] setActive:YES error:&err];
    int r = active ? madeira_audio_session_event(1) : 0;
    if (always_log || r == 0 || r == 2)
        dprintf(STDERR_FILENO, "[audio] %s, resumed=%d (%s%s)\n", why, r >= 1 ? 1 : 0,
                !active ? "the audio session could not be activated: " :
                r == 2 ? "output started again" : r == 1 ? "output running" : r == 3 ? "no stream playing" :
                r < 0 ? "switch off in the driver" : "output still stopped",
                !active ? err.localizedDescription.UTF8String : "");
}

/* madeira-bcd (ported from Madeira #349): env.MADEIRA_AUDIO_IO_MS = N (OFF unless set; upstream asks for 5 ms in every session)
 * asks the audio session for an N ms IO buffer, for a shared-mode client that keeps only one 10 ms engine period queued
 * (IAudioClient3 low latency, a Godot 4 game): iOS pulls ~1024 frames (21 ms) per render callback by default, every callback
 * empties such a queue and plays silence for the rest. The granted duration is published as MADEIRA_AUDIO_IO_US, from which
 * the driver (audio_null_ios.c, ios_engine_period) reports an engine period of at least two slices. Unset or 0: the system's
 * slice, nothing published, the 10 ms period as before. Called once per session after the env.* lines are exported. */
static void madeira_audio_io_slice(void)
{
    const char *io = getenv("MADEIRA_AUDIO_IO_MS");
    double ms = io && *io ? atof(io) : 0;
    if (ms <= 0 || ms > 100) return;
    NSError *err = nil;
    AVAudioSession *session = [AVAudioSession sharedInstance];
    if (![session setPreferredIOBufferDuration:ms / 1000.0 error:&err])
        dprintf(STDERR_FILENO, "[audio] setPreferredIOBufferDuration(%.1f ms) failed: %s\n", ms, err.localizedDescription.UTF8String);
    err = nil;
    [session setActive:YES error:&err];   /* the session is already active: this applies the new duration */
    char io_us[32];
    snprintf(io_us, sizeof io_us, "%lld", (long long)(session.IOBufferDuration * 1e6));
    setenv("MADEIRA_AUDIO_IO_US", io_us, 1);
    dprintf(STDERR_FILENO, "[audio] io buffer asked %.1f ms, granted %.2f ms (env.MADEIRA_AUDIO_IO_MS)\n", ms, session.IOBufferDuration * 1000.0);
}

/* Once per app run, when a session with either switch starts; each observer
 * checks its switch again when it fires (a later session may have it off). */
static void madeira_engine_observers_install(void)
{
    static dispatch_once_t once;
    int audio = madeira_audio_resume_switch(), pause = madeira_pause_switch();

    if (audio) dprintf(STDERR_FILENO, "[audio] interruption handling on (env.MADEIRA_AUDIO_RESUME = 1)\n");
    if (pause) dprintf(STDERR_FILENO, "[gpu-pause] Metal work waits while the app is not active "
                       "(env.MADEIRA_PAUSE_IN_BACKGROUND = 1)\n");
    if (!audio && !pause) return;
    dispatch_once(&once, ^{
        NSNotificationCenter *nc = [NSNotificationCenter defaultCenter];
        AVAudioSession *session = [AVAudioSession sharedInstance];
        /* setActive can block for a while: the audio events run on their own serial queue */
        NSOperationQueue *q = [[NSOperationQueue alloc] init];
        q.maxConcurrentOperationCount = 1;
        q.qualityOfService = NSQualityOfServiceUserInitiated;
        q.name = @"madeira.audio-session";
        [nc addObserverForName:AVAudioSessionInterruptionNotification object:session queue:q
                    usingBlock:^(NSNotification *n) {
            if (!madeira_audio_resume_switch()) return;
            if ([n.userInfo[AVAudioSessionInterruptionTypeKey] unsignedIntegerValue] == AVAudioSessionInterruptionTypeBegan) {
                madeira_audio_session_event(0);
                dprintf(STDERR_FILENO, "[audio] interruption began (reason %lu)\n",
                        (unsigned long)[n.userInfo[AVAudioSessionInterruptionReasonKey] unsignedIntegerValue]);
            } else {
                madeira_audio_resume("interruption ended", 1);
            }
        }];
        [nc addObserverForName:AVAudioSessionRouteChangeNotification object:session queue:q
                    usingBlock:^(NSNotification *n) {
            if (!madeira_audio_resume_switch()) return;
            if (madeira_audio_session_event(3) == 2)
                dprintf(STDERR_FILENO, "[audio] route changed (reason %lu), resumed=1 (output started again)\n",
                        (unsigned long)[n.userInfo[AVAudioSessionRouteChangeReasonKey] unsignedIntegerValue]);
        }];
        [nc addObserverForName:AVAudioSessionMediaServicesWereResetNotification object:session queue:q
                    usingBlock:^(NSNotification *n) {
            (void)n;
            if (!madeira_audio_resume_switch()) return;
            NSError *err = nil;
            [[AVAudioSession sharedInstance] setCategory:AVAudioSessionCategoryPlayback error:&err];
            BOOL active = [[AVAudioSession sharedInstance] setActive:YES error:&err];
            int r = madeira_audio_session_event(2);
            dprintf(STDERR_FILENO, "[audio] media services were reset, resumed=%d (session %s)\n",
                    r >= 1 ? 1 : 0, active ? "active" : "not active");
        }];
        [nc addObserverForName:UIApplicationWillResignActiveNotification object:nil queue:nil
                    usingBlock:^(NSNotification *n) {
            (void)n;
            if (madeira_pause_switch()) madeira_set_inactive(1);
        }];
        [nc addObserverForName:UIApplicationDidBecomeActiveNotification object:nil queue:nil
                    usingBlock:^(NSNotification *n) {
            (void)n;
            madeira_set_inactive(0);   /* always: the switch may have changed since */
            if (madeira_audio_resume_switch())
                [q addOperationWithBlock:^{ madeira_audio_resume("app active again", 0); }];
        }];
    });
}

static void *wine_process_thread(void *arg) {
    @autoreleasepool {
        /* Perf: the guest main thread runs ON this pthread. Promote to
         * USER_INTERACTIVE so it schedules on P-cores with minimal kernel
         * timer coalescing (same rationale as start_thread in
         * thread_ios.c — default QoS costs tens of ms of sleep leeway). */
        {
            int qrc = pthread_set_qos_class_self_np(QOS_CLASS_USER_INTERACTIVE, 0);
            qos_class_t q = QOS_CLASS_UNSPECIFIED; int rel = 0;
            pthread_get_qos_class_np(pthread_self(), &q, &rel);
            LOG("Wine process thread started");
            dprintf(STDERR_FILENO, "[main-qos] madeira-bcd guest main thread: set USER_INTERACTIVE rc=%d, class now 0x%x\n",
                    qrc, (unsigned)q);
        }

        /* ml588: seeding itself now happens in wineserver_start(), BEFORE the
         * server loads the registry. Kept here as a safety net for any path
         * that reaches Wine without going through wineserver_start() — the
         * stamp probe makes it a no-op stat once the prefix exists. */
        madeira_seed_prefix_if_needed(g_prefix_path);

        // Set environment for Wine
        setenv("WINEPREFIX", g_prefix_path, 1);
        setenv("HOME", g_prefix_path, 1);

        // Skip check_command_line / reexec_loader
        setenv("WINELOADERNOEXEC", "1", 1);

        // Set DLL search path to app bundle (contains aarch64-windows/ with PE DLLs)
        {
            NSString *bundlePath = [[NSBundle mainBundle] bundlePath];
            setenv("WINEDLLPATH", bundlePath.UTF8String, 1);
            LOG("WINEDLLPATH=%{public}s", bundlePath.UTF8String);
        }

        /* Wine trace channels.
         *
         * 2026-05-19 perf pivot: the verbose default (err+all, fixme+all,
         * warn+module, warn+file, trace+process, trace+module, trace+loaddll,
         * trace+loadorder, trace+win, trace+user32, trace+syscall, trace+file)
         * was generating ~220 KB/sec of log writes — the dominant source of
         * the 1.35s-per-frame menu rendering. trace+syscall + trace+file alone
         * are likely 90%+ of the volume (every Nt* call writes 3-5 log lines).
         *
         * Default is now PERF: only err+all (so we still see real failures).
         * For debugging, set MADEIRA_DEBUG_VERBOSE=1 in the environment to
         * restore the full trace channel set. */
        {
            const char *verbose = getenv("MADEIRA_DEBUG_VERBOSE");
            if (verbose && *verbose && *verbose != '0') {
                setenv("WINEDEBUG", "err+all,fixme+all,warn+module,warn+file,trace+process,trace+module,trace+loaddll,trace+loadorder,trace+win,trace+user32,trace+syscall,trace+file", 1);
                LOG("WINEDEBUG = verbose (MADEIRA_DEBUG_VERBOSE set)");
            } else {
                /* err+all keeps real failure messages, but subtract err+virtual
                 * because our iOS virtual_ios.c uses ERR() for informational
                 * traces ("iOS vm_protect RW+COPY OK", "iOS JIT: pool size",
                 * "iOS JIT: copied image"). Those produce thousands of lines
                 * per boot. Real failures in virtual_ios.c use distinctive
                 * FATAL/FAIL prefixes our app surfaces via other paths. */
                /* ml740: warn+seh removed again now the tracing it existed for is
                 * done. It routes every OutputDebugStringA through an exception
                 * dispatch, which is real overhead in hot paths; re-add it only
                 * alongside MADEIRA_TF_TRACE. */
                /* fixme-d3dcompiler: Wine's shader reflection prints one
                 * skip_u32_unknown line per unknown RDEF dword. Metro 2033
                 * Redux reflects every shader at load: ~90,000 of a
                 * 105,000-line log in six seconds, all of it parsed by LogStore. */
                setenv("WINEDEBUG", "err+all,err-virtual,fixme-d3dcompiler", 1);
                LOG("WINEDEBUG = err+all,err-virtual,fixme-d3dcompiler (perf default — set MADEIRA_DEBUG_VERBOSE=1 for full trace)");
            }
        }

        // Phase 3D investigation: re-enabled. Investigation C concluded
        // wineserver dispatch is fine; the `ws_log drops at high rate`
        // artifact was the prior false signal. Now chasing a real bug:
        // get_desktop_window's returned HWND fails get_user_object lookup
        // when create_window receives it as req->parent.
        setenv("MADEIRA_WIN32U", "1", 1);
        madeira_export_image_uuid();

        /* iOS-Madeira ml711: default FNA to its D3D11 backend.
         *
         * FNA3D picks OpenGL by default, and there is no GL on iOS -- our graphics stack
         * is DXMT (D3D11 -> Metal). Marvel Cosmic Invasion loaded FNA3D.dll, immediately
         * pulled in OPENGL32.DLL, created SDL's hidden 10x10 pixel-format probe window,
         * and stopped there: d3d11.dll and dxgi.dll never loaded at all. FNA3D.dll ships
         * the D3D11 backend (D3D11Driver plus the MOJOSHADER_d3d11* set are present in the
         * shipped binary), so it only needs to be selected.
         *
         * This is a platform policy rather than a per-title override: FNA's GL backend
         * cannot work through this stack for ANY title, while D3D11 routes into DXMT.
         *
         * overwrite=0 on purpose -- D3D11 becomes the iOS default while an explicit
         * developer or user setting still wins. Wine copies this verbatim into the Windows
         * environment (get_initial_environment ignores only NIXPKGS_/QT_/VK_ and the SDL
         * audio+video driver names), and env_ios.c logs an [iOS env] INCLUDED line for it
         * so the next log proves it arrived rather than leaving us to infer it. */
        setenv("FNA3D_FORCE_DRIVER", "D3D11", 0);

        /* ml720: make Mono report unhandled exceptions and assembly-load failures.
         *
         * DIAGNOSTIC — revisit before shipping; this is chatty and costs startup time.
         *
         * Marvel Cosmic Invasion now reaches its own managed catch block (exit code went
         * 0 -> 1 once /gldevice: and -AllowMultiInstance cleared the two early returns in
         * Main), so there IS a real exception -- but the game cannot tell us what it is:
         * NLog's file target never gets written and NBug leaves no artifact, both because
         * the failure happens before logging is usable.
         *
         * Mono itself will say. asm+dll masks also surface a missing or mismatched
         * assembly, a common startup failure that would otherwise look like an opaque
         * managed exception.
         *
         * overwrite=0 so an explicit setting still wins; MONO_ is already in env_ios.c's
         * [iOS env] beacon list, so the next log proves whether these arrived. */
        /* ml733: was "debug"/"asm,dll", which existed to diagnose DLL
         * resolution. That work is finished, and it now emits ~62,000 identical
         * assembly-load lines in a single run -- most of a 100k-line log, plus
         * the I/O cost of writing them, on a title we are trying to time.
         * "warning" keeps genuine failures and drops the chatter. */
        setenv("MONO_LOG_LEVEL", "warning", 0);

        /* 2026-07-05 quiet/release mode: disables the heavyweight
         * diagnostics — the PROF sampler (thread_suspends the game thread
         * ~500x/s), per-present log lines (100+/s at RAW rates), winios
         * poll heartbeat. Counters (present count for the FPS overlay,
         * machexc, srvw) keep ticking; ERR-level and boot logging are
         * untouched. Worth a few %% of frame time and, more importantly,
         * HEAT — thermals are what cap ProMotion at 60. COMMENT THIS OUT
         * for diagnostic/profiling sessions. */
        setenv("MADEIRA_QUIET", "1", 1);

        /* task #34 share/purge-probe experiments CONCLUDED 2026-07-14
         * (remap-sharing dead; pool not purgeable; ml76 wall = mismatched
         * MADV_FREE/MADV_FREE_REUSE pair). Probe machinery stays in
         * ntdll-unix, gated on MADEIRA_SHARE_PROBE — set it here to re-run. */

        /* 2026-07-05 audio: activate the AVAudioSession before Wine boots
         * so the RemoteIO unit in the mmdevapi driver can start. Playback
         * category = ignores silent switch (it's a game). */
        {
            NSError *aerr = nil;
            AVAudioSession *session = [AVAudioSession sharedInstance];
            [session setCategory:AVAudioSessionCategoryPlayback error:&aerr];
            if (aerr) LOG("AVAudioSession setCategory failed: %{public}s",
                          aerr.localizedDescription.UTF8String);
            aerr = nil;
            [session setActive:YES error:&aerr];
            if (aerr) LOG("AVAudioSession setActive failed: %{public}s",
                          aerr.localizedDescription.UTF8String);
            else LOG("AVAudioSession active: rate=%.0f latency=%.1fms",
                     session.sampleRate, session.outputLatency * 1000.0);
        }

        /* 2026-07-04 BISECT RESULT: arm A (this env set, all handler fixes
         * on) booted to menu at 17-18 FPS with the x18-access emulator
         * firing 135K+ times cleanly — handler fixes EXONERATED. The
         * libsystem_malloc death is specific to UNIXCALL-DIRECT. Env
         * removed; next crash run carries an fp-walk backtrace + malloc
         * prologue dump to name the Metal call handing free() a garbage
         * pointer. */

        /* 2026-07-04: MADEIRA_HEAL retried with XLATE-HOOK-REV in place and
         * STILL fatal — same C000001D libplatform (os_unfair_lock abort)
         * seconds after healing the ntdll dispatch-thunk VA at boot. One of
         * the rewritten slots has a consumer doing identity/offset math on
         * the PE VA, which no unwinder fix helps. Blanket healing is dead;
         * the fault-latency attack needs slot-level forensics (which slot
         * is the pure branch-feeder) or a writer-side fix. Healer stays
         * opt-in-off. */

        /* Steam game vars. One title reads SteamAppPath as its asset base path and
         * queries it dozens of times during init, so it must be present before that
         * title starts.
         *
         * KNOWN DEFECT, deliberately left in place for now: this publishes ONE title's
         * identity to EVERY guest, with overwrite=1. A different title that links a Steam
         * wrapper therefore sees the wrong app ID. Removing it outright was tested and is
         * NOT the fix -- it regresses the title that needs the path, and it did not change
         * the behaviour of the title that was mis-identified, so the mismatch is real but
         * was not the failure being chased.
         *
         * The durable design belongs in the title-launch layer: publish nothing by
         * default, take the ID from explicit title metadata or the game's own
         * steam_appid.txt, set SteamAppPath to that game's directory, and give each child
         * its own environment rather than mutating one process-global set shared by every
         * pseudo-process. This path usually launches explorer.exe and cannot know which
         * title the desktop will start later, so a conditional here cannot work.
         *
         * A Madeira Dock session is the one launch that can know: it runs Valve's
         * client inside the host process, and the client gives every game it
         * starts that game's own identity. The fixed identity above reached the
         * client and the game (both inherit this environment), so a Dock launch
         * publishes none of the three. ContentView sets MADEIRA_DOCK_SESSION=1 for
         * a Dock launch only and clears it for every other launch, which keeps
         * the fixed identity exactly as before.
         *
         * A Steam game the library starts as its own program ("Start with: The
         * game", LibraryEntry.configureLaunch) knows its title too. It passes that
         * game's App ID and install folder in MADEIRA_STEAM_APPID / MADEIRA_STEAM_APPPATH,
         * and this launch publishes the game's own identity instead of the fixed one. Both
         * are cleared here, so no later launch inherits them. */
        const char *dock_session = getenv("MADEIRA_DOCK_SESSION");
        const char *direct_app = getenv("MADEIRA_STEAM_APPID");    /* set by the library for one direct Steam start (Start with: The game); not a setting */
        const char *direct_path = getenv("MADEIRA_STEAM_APPPATH"); /* that game's install folder, with MADEIRA_STEAM_APPID; not a setting */
        /* madeira-bcd (lead, 2026-10-24): env.MADEIRA_STEAM_IDENTITY = none in a game's file publishes no Steam identity at all.
         * The fixed one above made Rockstar's launcher of the Epic GTA V and RDR2 think it was the Steam version: every Epic start
         * of the launcher in the testers' logs (8 of 9; YZZFYA, DJAMF2, VX8ZZ0, ...) carried SteamAppId=356400 and ended in
         * "LAUNCHER_ERR_STEAM_FAILED" (113, "Steam failed to initialize"). Opt-in (the app adds it after that error in an Epic
         * session); unset or any other value keeps the branches below exactly as they were. */
        const char *steam_identity = getenv("MADEIRA_STEAM_IDENTITY");
        if (steam_identity && strcmp(steam_identity, "none") == 0) {
            unsetenv("SteamAppPath");
            unsetenv("SteamGameId");
            unsetenv("SteamAppId");
            dprintf(STDERR_FILENO, "[steam-env] MADEIRA_STEAM_IDENTITY=none: no Steam game identity published\n");
        } else if (dock_session && dock_session[0] == '1') {
            unsetenv("SteamAppPath");
            unsetenv("SteamGameId");
            unsetenv("SteamAppId");
            dprintf(STDERR_FILENO, "[steam-env] BCD Dock session: no fixed Steam game identity published\n");
        } else if (direct_app && direct_app[0] && strlen(direct_app) <= 10 &&
            strspn(direct_app, "0123456789") == strlen(direct_app) &&
            direct_path && (direct_path[0] == 'C' || direct_path[0] == 'c') && direct_path[1] == ':' &&
            direct_path[2] == '\\' && strlen(direct_path) < 1024 && !strstr(direct_path, "..")) {
            setenv("SteamAppPath", direct_path, 1);
            setenv("SteamGameId", direct_app, 1);
            setenv("SteamAppId",  direct_app, 1);
            dprintf(STDERR_FILENO, "[steam-start] direct start: the game's own Steam identity (app %s) published\n", direct_app);
        } else {
            setenv("SteamAppPath", "C:\\Program Files\\Thumper", 1);
            setenv("SteamGameId", "356400", 1);
            setenv("SteamAppId",  "356400", 1);
        }
        unsetenv("MADEIRA_STEAM_APPID");
        unsetenv("MADEIRA_STEAM_APPPATH");

        /* iOS-Madeira 2026-07-02: publish the TRUE JIT-pool RX->RW offset to
         * xtajit64.dll (its own FEXCore copy reads this via getenv in
         * ProcessInit). Set HERE — beside SteamAppPath, the point where
         * Wine snapshots the environment — so it forwards reliably; setting
         * it in FEXBridge.mm::jit_pool_init was too early and did not reach
         * Wine's GetEnvironmentVariableW. jit_pool_init has already run by
         * now (fex_initialize is a prerequisite for launching the guest),
         * so the offset is available. */
        {
            int64_t jit_off = fex_get_jit_write_offset();
            if (jit_off != 0) {
                char off_str[32];
                snprintf(off_str, sizeof(off_str), "0x%llx", (unsigned long long)jit_off);
                setenv("MADEIRA_JIT_WRITE_OFFSET", off_str, 1);
                LOG("setenv MADEIRA_JIT_WRITE_OFFSET=%{public}s", off_str);
            } else {
                LOG("WARNING: fex_get_jit_write_offset() returned 0 — JIT pool not initialized?");
            }
        }

        /* iOS-Madeira: TSO stays ENABLED (default). The unaligned LDAR/LDAPR/
         * STLR backpatch is now in signal_arm64_ios.c's Mach handler, which
         * replicates FEX's HandleUnalignedAccess (Arm64.cpp:2072) so iOS
         * EXC_BAD_ACCESS faults get the same in-place LDAR→LDR+DMB_LD
         * recovery FEX does for Windows EXCEPTION_DATATYPE_MISALIGNMENT. */

        /* iOS-Madeira: a tiny stub steamclient64.dll is shipped in the game
         * directory (built from /tmp/steamclient_stub/stub.c). It exports
         * just VR_InitInternal (returns NULL) — the only function another
         * module in the game directory imports from steamclient64. The
         * original steamclient64.dll (packed, RWX self-modifying, unwind
         * info v5) was blowing up Wine's loader; the stub lets that import
         * bind and the process proceed without OpenVR support. Note: no
         * WINEDLLOVERRIDES needed — we just shipped a different file at the
         * same path. */

        LOG("WINEPREFIX=%{public}s", g_prefix_path);

        // Set up file-based logging for Wine C code
        {
            NSString *docs = NSSearchPathForDirectoriesInDomains(NSDocumentDirectory, NSUserDomainMask, YES).firstObject;
            NSString *logPath = [docs stringByAppendingPathComponent:@"bcd-log.txt"];
            wine_log_set_file(logPath.UTF8String);
            /* ml519: start the freeze detector as soon as logging works, so
             * every launch (Thumper as well as Steam) yields a measurement. */
            { extern void winios_freeze_watch_start(void); winios_freeze_watch_start(); }
            /* a game session starts with no game-mode overlay windows and
             * no known Metal windows (Winios.m) */
            { extern void winios_session_reset(void); winios_session_reset(); }
            LOG("Wine log file: %{public}s", logPath.UTF8String);
            /* Expose the app Documents dir to Wine code (e.g. for fex-jit-dump.bin) */
            setenv("MADEIRA_DOCS_DIR", docs.UTF8String, 1);
            dprintf(STDERR_FILENO, "[config-dir] early MADEIRA_DOCS_DIR=%s; native bcd.cfg readers that run "
                    "before this point (wineserver start: madsync inproc-sync) use it (MADEIRA_CFG_EARLY_DOCS=0 disables)\n",
                    g_madeira_docs_early);

            /* ml1076: file-backed memory canary (Astra's memory-backing-canary.c,
             * run in-app on the phone, gated by bcd.cfg swap-canary = 1).
             * Question: do dirty pages of a MAP_SHARED mapping of a private temp file
             * stay OUT of phys_footprint on iOS the way they do on macOS? If yes, a
             * file-backed tier for large guest commits is a real capacity lever. */
            if (madeira_cfg_bool("swap-canary", 0)) {   /* ml1095: bcd.cfg swap-canary = 1 */
                extern void madeira_memory_canary(const char *tmpdir);
                madeira_memory_canary(NSTemporaryDirectory().UTF8String);
            }

            /* ml1077: file-backed guest data tier. bcd.cfg swap-mb = cap
             * in MB; the sparse backing file lives in tmp with NO file protection so
             * the mapping survives the screen locking. See virtual_ios.c ml1077. */
            {
                long capMB = (long)madeira_cfg_int("swap-mb", 0);   /* ml1095: bcd.cfg swap-mb = N */
                if (capMB >= 64) {
                    NSString *swapPath = [NSTemporaryDirectory() stringByAppendingPathComponent:@"madeira-swap.bin"];
                    [[NSFileManager defaultManager] removeItemAtPath:swapPath error:nil];
                    if ([[NSFileManager defaultManager] createFileAtPath:swapPath contents:nil attributes:@{NSFileProtectionKey: NSFileProtectionNone}]) {
                        setenv("MADEIRA_SWAP_FILE", swapPath.UTF8String, 1);
                        setenv("MADEIRA_SWAP_MB", [NSString stringWithFormat:@"%ld", capMB].UTF8String, 1);
                        /* madeira-bcd: swap-min-kb (bcd.cfg or the game's config) lowers
                         * the tier's 8 MB eligibility floor. Upstream's virtual_ios.c reads
                         * MADEIRA_SWAP_MIN_KB only in its "blocks" coverage, so swap-min-kb
                         * selects that; an env.MADEIRA_SWAP_COVERAGE line, exported below,
                         * still wins. */
                        long minKB = (long)madeira_cfg_int("swap-min-kb", 0);
                        if (minKB > 0) {
                            setenv("MADEIRA_SWAP_MIN_KB", [NSString stringWithFormat:@"%ld", minKB].UTF8String, 1);
                            setenv("MADEIRA_SWAP_COVERAGE", "blocks", 1);
                            fprintf(stderr, "[swap] app: commits from %ld KB are eligible (swap-min-kb, coverage=blocks)\n", minKB);
                        } else {
                            unsetenv("MADEIRA_SWAP_MIN_KB");
                            unsetenv("MADEIRA_SWAP_COVERAGE");
                        }
                        LOG("ml1077 swap tier armed: %{public}s, %ld MB", swapPath.UTF8String, capMB);
                        fprintf(stderr, "[swap] ml1077 app: backing file %s, cap %ld MB\n", swapPath.UTF8String, capMB);
                        /* ml1150: memory pool, the tier's first stage (MemoryPool.m). */
                        long poolMB = (long)madeira_cfg_int("mempool-mb", 0);   /* bcd.cfg mempool-mb = N */
                        if (poolMB >= 512) {
                            extern long MadeiraMemoryPoolStart(long megabytes);
                            long got = MadeiraMemoryPoolStart(poolMB);
                            LOG("ml1150 memory pool: %ld of %ld MB", got, poolMB);
                        }
                    }
                }
            }

            /* ml1062: environment exports -- the "env.NAME = value" lines of bcd.cfg,
             * exported before Wine starts. FEX reads its whole configuration from FEX_*
             * environment variables (EnvLoader over the process environment, which
             * Wine builds from ours), so this turns every FEX option -- TSO emulation,
             * multiblock, SMC checks, x87 precision -- into a file edit instead of a
             * rebuild. Lines starting with # are comments. Logged, so a run's log
             * always says what it ran with. */
            {
                /* ml1095: collected as KEY=VALUE lines; none without bcd.cfg. */
                NSString *text = nil;
                if (madeira_cfg_present()) {
                    NSMutableString *acc = [NSMutableString string];
                    NSString *cfg = [NSString stringWithContentsOfFile:[docs stringByAppendingPathComponent:@MADEIRA_CFG_FILE] encoding:NSUTF8StringEncoding error:nil];
                    for (NSString *raw in [cfg componentsSeparatedByCharactersInSet:[NSCharacterSet newlineCharacterSet]]) {
                        NSString *line = [raw stringByTrimmingCharactersInSet:[NSCharacterSet whitespaceCharacterSet]];
                        NSRange eq = [line rangeOfString:@"="];
                        if (![line hasPrefix:@"env."] || eq.location == NSNotFound) continue;
                        NSString *k = [[line substringWithRange:NSMakeRange(4, eq.location - 4)] stringByTrimmingCharactersInSet:[NSCharacterSet whitespaceCharacterSet]];
                        NSString *v = [[line substringFromIndex:eq.location + 1] stringByTrimmingCharactersInSet:[NSCharacterSet whitespaceCharacterSet]];
                        if (k.length) [acc appendFormat:@"%@=%@\n", k, v];
                    }
                    text = acc;
                }
                /* iOS-Madeira ml1184: a library game's own settings (LibraryEntry.applyEnvironment,
                 * which ran before this on the launch worker) win over the same key in
                 * bcd.cfg, as its details page says; the cfg line used to replace them
                 * (env.MADEIRA_FASTSYNC = auto undid "Fast synchronization: off"). Which of
                 * them the game set is noted before the first cfg line is exported: bcd.cfg
                 * is last-line-wins, and a later line for the same key must still replace an
                 * earlier one instead of being taken for the game's own setting. Every key
                 * applyEnvironment exports belongs here (check-frontend). The game's own
                 * config lines ($MADEIRA_CFG_GAME, below) come after and win over both. */
                static const char *const per_launch[] = {
                    "MADEIRA_FASTSYNC", "MADEIRA_FASTSYNC_SEM", "MADEIRA_CPU_COUNT", "DXMT_D9_ANISO_LIMIT",
                    "FEX_X87REDUCEDPRECISION",   /* ml1184: the game's "Reduced-precision x87" */
                    "MADEIRA_DINPUT_PAD",        /* ml1240: the game's "XInput and DirectInput" */
                    "MADEIRA_FEX_AVX",           /* ml1184: the game's "AVX and AVX2" */
                    "MADEIRA_FRAMEGEN",          /* ml1184: the game's "Frame generation" */
                    "DXMT_ENABLE_NVEXT", "DXMT_WSI_MONITOR_IDENTITY", "DXMT_WSI_MODE_TABLE",   /* the game's "Report an NVIDIA GPU" */
                };
                enum { per_launch_count = sizeof(per_launch) / sizeof(per_launch[0]) };
                BOOL game_set[per_launch_count];
                for (size_t i = 0; i < per_launch_count; i++) game_set[i] = getenv(per_launch[i]) != NULL;
                for (NSString *raw in [text componentsSeparatedByCharactersInSet:[NSCharacterSet newlineCharacterSet]]) {
                    NSString *line = [raw stringByTrimmingCharactersInSet:[NSCharacterSet whitespaceCharacterSet]];
                    NSRange eq = [line rangeOfString:@"="];
                    if (!line.length || [line hasPrefix:@"#"] || eq.location == NSNotFound || eq.location == 0) continue;
                    NSString *k = [line substringToIndex:eq.location], *v = [line substringFromIndex:eq.location + 1];
                    BOOL kept = NO;
                    for (size_t i = 0; i < per_launch_count; i++)
                        if (game_set[i] && !strcmp(k.UTF8String, per_launch[i])) kept = YES;
                    if (kept) {
                        fprintf(stderr, "[madeira-env] ml1184 %s=%s kept (the game's own setting); bcd.cfg's %s ignored\n",
                                k.UTF8String, getenv(k.UTF8String), v.UTF8String);
                        continue;
                    }
                    if (madeira_env_is_locked(k.UTF8String)) {
                        fprintf(stderr, "[madeira-env] %s is set by the app; "
                                "the config value is ignored\n", k.UTF8String);
                        continue;
                    }
                    setenv(k.UTF8String, v.UTF8String, 1);
                    LOG("bcd.cfg env: %{public}s=%{public}s", k.UTF8String, v.UTF8String);
                    fprintf(stderr, "[madeira-env] ml1062 %s=%s\n", k.UTF8String, v.UTF8String);
                }
                /* The game's own lines ($MADEIRA_CFG_GAME): madeira-bcd's per-game file
                 * (GameProfiles.swift, Application Support/GameConfigs), or the
                 * library entry's lines LibraryEntry.applyEnvironment writes
                 * (MadeiraConfig.applyGame merges the two). Its env.NAME lines come
                 * after bcd.cfg's and win, the rule madeira_cfg_get applies to keys. */
                const char *gameCfg = getenv("MADEIRA_CFG_GAME");
                NSString *gtext = (gameCfg && *gameCfg)
                    ? [NSString stringWithContentsOfFile:[NSString stringWithUTF8String:gameCfg] encoding:NSUTF8StringEncoding error:nil]
                    : nil;
                /* madeira-bcd: print every setting line of the game's file, so a log
                 * always says which per-game keys (dxmt, swap-*, metalfx...) it ran with. */
                if (gameCfg && *gameCfg)
                    fprintf(stderr, "[game-cfg] %s (%s)\n", gameCfg, gtext ? "read" : "MISSING");
                for (NSString *raw in [gtext componentsSeparatedByCharactersInSet:[NSCharacterSet newlineCharacterSet]]) {
                    NSString *t = [raw stringByTrimmingCharactersInSet:[NSCharacterSet whitespaceCharacterSet]];
                    if (t.length && ![t hasPrefix:@"#"] && ![t hasPrefix:@"env."])
                        fprintf(stderr, "[game-cfg]   %s\n", t.UTF8String);
                }
                for (NSString *raw in [gtext componentsSeparatedByCharactersInSet:[NSCharacterSet newlineCharacterSet]]) {
                    NSString *line = [raw stringByTrimmingCharactersInSet:[NSCharacterSet whitespaceCharacterSet]];
                    NSRange eq = [line rangeOfString:@"="];
                    if (![line hasPrefix:@"env."] || eq.location == NSNotFound) continue;
                    NSString *k = [[line substringWithRange:NSMakeRange(4, eq.location - 4)] stringByTrimmingCharactersInSet:[NSCharacterSet whitespaceCharacterSet]];
                    NSString *v = [[line substringFromIndex:eq.location + 1] stringByTrimmingCharactersInSet:[NSCharacterSet whitespaceCharacterSet]];
                    if (!k.length) continue;
                    if (madeira_env_is_locked(k.UTF8String)) {
                        fprintf(stderr, "[madeira-env] %s is set by the app; "
                                "the config value is ignored\n", k.UTF8String);
                        continue;
                    }
                    setenv(k.UTF8String, v.UTF8String, 1);
                    LOG("game config env: %{public}s=%{public}s", k.UTF8String, v.UTF8String);
                    fprintf(stderr, "[madeira-env] game %s=%s\n", k.UTF8String, v.UTF8String);
                }
                {   /* madeira-bcd (round 604): the in-game menu's Wine debug channels, for THIS start only (BCDDebugLive.swift, BCDDebugChannels.swift).
                     * The menu writes the text of the WINEDEBUG it wants into a file in the temporary folder; it is exported here, after the game's own
                     * lines, and the file is removed, so the start after this one runs as always. Anything but plain channel text is ignored. */
                    NSString *once = [NSTemporaryDirectory() stringByAppendingPathComponent:@"bcd-debug-once.txt"];
                    NSString *chan = [[NSString stringWithContentsOfFile:once encoding:NSUTF8StringEncoding error:nil]
                                      stringByTrimmingCharactersInSet:[NSCharacterSet whitespaceAndNewlineCharacterSet]];
                    if (chan) {
                        [[NSFileManager defaultManager] removeItemAtPath:once error:nil];
                        NSCharacterSet *bad = [[NSCharacterSet characterSetWithCharactersInString:
                            @"abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789,+-_."] invertedSet];
                        if (chan.length > 0 && chan.length <= 400 && [chan rangeOfCharacterFromSet:bad].location == NSNotFound) {
                            setenv("WINEDEBUG", chan.UTF8String, 1);
                            LOG("WINEDEBUG = %{public}s (the in-game menu's debug channels, this start only)", chan.UTF8String);
                            fprintf(stderr, "[debug-channels] this start runs with WINEDEBUG=%s (chosen in the in-game menu; the next start runs as always)\n", chan.UTF8String);
                        } else {
                            fprintf(stderr, "[debug-channels] the one-shot file was not plain channel text; ignored\n");
                        }
                    }
                }
                {   /* madeira-bcd: for the wineserver (g_madeira_srv_fgetpath) */
                    const char *fp = getenv("MADEIRA_SRV_FGETPATH");
                    g_madeira_srv_fgetpath = (fp && fp[0] == '1') ? 1 : 0;
                    if (g_madeira_srv_fgetpath)
                        fprintf(stderr, "[madeira-env] MADEIRA_SRV_FGETPATH=1: the server names the files it opens with F_GETPATH\n");
                }
                {   /* madeira-bcd: the server's other per-launch switches (fd_ios.c, request_ios.c) */
                    const char *ef = getenv("MADEIRA_SRV_ECO_FOLLOW");   /* 1: the server thread runs at utility QoS while ECO is on */
                    const char *dr = getenv("MADEIRA_SRV_DIRECT");       /* 1 or 2: listed requests run on the calling thread */
                    const char *iu = getenv("MADEIRA_SRV_IDLE_US");      /* the server loop's longest sleep, us (default 1000) */
                    const char *la = getenv("MADEIRA_SRV_LOG_ALL");      /* 1: every per-descriptor server log line, as before */
                    int idle = iu && *iu ? atoi(iu) : 0;
                    g_madeira_srv_idle_us = idle > 0 ? idle : 0;
                    g_madeira_srv_log_all = (la && la[0] == '1') ? 1 : 0;
                    g_madeira_srv_eco_follow = (ef && ef[0] == '1') ? 1 : 0;
                    g_madeira_srv_direct = (dr && (dr[0] == '1' || dr[0] == '2') && !dr[1]) ? dr[0] - '0' : 0;
                    if (g_madeira_srv_eco_follow || g_madeira_srv_direct || g_madeira_srv_idle_us || g_madeira_srv_log_all)
                        fprintf(stderr, "[madeira-env] server switches: MADEIRA_SRV_ECO_FOLLOW=%d MADEIRA_SRV_DIRECT=%d "
                                "MADEIRA_SRV_IDLE_US=%d MADEIRA_SRV_LOG_ALL=%d\n", g_madeira_srv_eco_follow,
                                g_madeira_srv_direct, g_madeira_srv_idle_us, g_madeira_srv_log_all);
                }
                /* Fastsync is the default sync engine: with neither inproc-sync nor
                 * env.MADEIRA_FASTSYNC in bcd.cfg, Wine gets MADEIRA_FASTSYNC=auto,
                 * the value Settings > Sync engine > Fastsync writes. Never overrides a
                 * value already set (a game's own fastsync switch sets 0). */
                if (madeira_cfg_sync_engine() == MADEIRA_SYNC_FASTSYNC && !getenv("MADEIRA_FASTSYNC")) {
                    setenv("MADEIRA_FASTSYNC", "auto", 0);
                    fprintf(stderr, "[madeira-env] sync engine: fastsync (default), MADEIRA_FASTSYNC=auto\n");
                }
            }
        }

        // Steam S0: root CA trust. iOS has no API to enumerate system
        // roots, so crypt32's unix rootstore (crypt32_unixlib_ios.c)
        // reads the bundled Mozilla CA set from this path instead.
        {
            NSString *caPath = [[NSBundle mainBundle] pathForResource:@"cacert" ofType:@"pem"];
            if (caPath) {
                setenv("MADEIRA_CA_BUNDLE", caPath.UTF8String, 1);
                LOG("CA bundle: %{public}s", caPath.UTF8String);
            } else {
                LOG("WARNING: cacert.pem missing from bundle — HTTPS cert verification will fail");
            }
        }

        bcd_hdr_begin(); // session environment is applied; keep the HUD's probe request

        // Redirect stderr AND stdout to log file so Wine debug output (WINEDEBUG)
        // and the guest program's printf are both captured.
        {
            NSString *docs = NSSearchPathForDirectoriesInDomains(NSDocumentDirectory, NSUserDomainMask, YES).firstObject;
            NSString *logPath2 = [docs stringByAppendingPathComponent:@"bcd-log.txt"];
            int logfd = open(logPath2.UTF8String, O_WRONLY | O_CREAT | O_APPEND, 0644);
            if (logfd >= 0) {
                dup2(logfd, STDERR_FILENO);
                dup2(logfd, STDOUT_FILENO);
                close(logfd);
            }
        }

        madeira_engine_observers_install();   /* madeira-bcd: env.MADEIRA_AUDIO_RESUME / env.MADEIRA_PAUSE_IN_BACKGROUND; neither set: nothing */
        madeira_audio_io_slice();             /* madeira-bcd: env.MADEIRA_AUDIO_IO_MS (#349); unset: nothing */
        /* madeira-stdout-pipe: hand the guest's stdout to a pipe, and leave
         * stderr on the log.
         *
         * The split is the one the code already has: Wine's err:/warn: channels
         * and every dprintf(STDERR_FILENO, ...) go to stderr, the guest's printf
         * goes to stdout. Separating the file descriptors separates program
         * output from runtime narration by the kernel rather than by matching
         * line prefixes after the fact -- which is the only way a terminal can
         * interpret an escape sequence that a telemetry line would otherwise
         * land in the middle of.
         *
         * Opt-in, because an unread pipe fills at 64 KB and blocks the guest
         * forever on its next write. The reader creates the flag file. */
        {
            NSString *docs3 = NSSearchPathForDirectoriesInDomains(
                NSDocumentDirectory, NSUserDomainMask, YES).firstObject;
            /* env.MADEIRA_STDOUT_PIPE = 1 moves the guest's stdout onto its
             * own pipe, leaving fd 2 on madeira-log.txt. That is the seam the
             * code already had: Wine's err:/warn: channels and every
             * dprintf(STDERR_FILENO, ...) are stderr, the guest's printf is
             * stdout. Off by default for the same reason as the stdin half --
             * an unread pipe fills at 64 KB and blocks the guest -- so only a
             * front end that drains it should ask. Documents/bcd-stdout.txt
             * still works. */
            NSString *flag = [docs3 stringByAppendingPathComponent:@"bcd-stdout.txt"];
            const char *stdout_opt = getenv( "MADEIRA_STDOUT_PIPE" );
            if ([[NSFileManager defaultManager] fileExistsAtPath:flag] ||
                (stdout_opt && stdout_opt[0] == '1')) {
                int fds[2];
                /* madeira-bcd: a second session in one app run: the earlier read end is closed, not leaked */
                if (madeira_stdout_master >= 0) { close(madeira_stdout_master); madeira_stdout_master = -1; }
                if (pipe(fds) != 0) {
                    dprintf(STDERR_FILENO, "[stdout-pipe] pipe() failed errno=%d\n", errno);
                } else {
                    dup2(fds[1], STDOUT_FILENO);
                    if (fds[1] != STDOUT_FILENO) close(fds[1]);
                    /* Non-blocking: the reader polls and must not stall while
                     * the guest is quiet. */
                    fcntl(fds[0], F_SETFL, fcntl(fds[0], F_GETFL, 0) | O_NONBLOCK);
                    madeira_stdout_master = fds[0];
                    dprintf(STDERR_FILENO,
                            "[stdout-pipe] guest stdout is a pipe, read end=%d\n", fds[0]);
                }
            }
        }

        // ---- Optional pipe on fd 0, and optional forced console ----
        // Both opt-in via Documents files; absent, nothing here runs
        // and fd 0 stays whatever iOS gave us (which the 2026-09-16 probe
        // showed reads as immediate EOF).
        //
        // A pipe, not a pty: posix_openpt is EPERM under the iOS sandbox. It
        // turns out not to matter -- Wine's is_tty_handle() is a pointer-tag
        // test, not a tty test, so a tagged pipe reaches conhost the same way.
        {
            /* env.MADEIRA_STDIN_PIPE = 1 puts a pipe on fd 0 so a guest can
             * read stdin at all; without it fd 0 is whatever iOS gave us,
             * which reads as immediate EOF. Off by default: an unread pipe
             * fills at 64 KB and blocks the guest on its next write, so only a
             * front end that drains it should ask for one.
             * Documents/bcd-stdin.txt still works and additionally seeds
             * the pipe with its contents, which is how a probe supplies canned
             * input with no front end at all. */
            NSString *docs2 = NSSearchPathForDirectoriesInDomains(NSDocumentDirectory, NSUserDomainMask, YES).firstObject;
            NSData *seed = [NSData dataWithContentsOfFile:
                            [docs2 stringByAppendingPathComponent:@"bcd-stdin.txt"]];
            const char *stdin_opt = getenv( "MADEIRA_STDIN_PIPE" );
            if (seed || (stdin_opt && stdin_opt[0] == '1')) {
                int fds[2];
                /* madeira-bcd: a second session in one app run: the earlier write end is closed, not leaked */
                if (madeira_stdin_master >= 0) { close(madeira_stdin_master); madeira_stdin_master = -1; }
                if (pipe(fds) != 0) {
                    dprintf(STDERR_FILENO, "[stdin-pipe] pipe() failed errno=%d\n", errno);
                } else {
                    dup2(fds[0], STDIN_FILENO);
                    if (fds[0] != STDIN_FILENO) close(fds[0]);
                    madeira_stdin_master = fds[1];
                    dprintf(STDERR_FILENO,
                            "[stdin-pipe] fd 0 is a pipe, isatty=%d, write end=%d\n",
                            isatty(STDIN_FILENO), fds[1]);
                    // Non-blocking write end. XNU pipes start at 16KB and only
                    // grow toward 64KB opportunistically, and nothing bounds the
                    // size of bcd-stdin.txt -- a blocking write with no
                    // reader yet would hang the Wine thread before __wine_main,
                    // with the last log line being the one above.
                    int fl = fcntl(fds[1], F_GETFL, 0);
                    if (fl != -1) fcntl(fds[1], F_SETFL, fl | O_NONBLOCK);
                    if (seed && seed.length) {
                        ssize_t w = write(fds[1], seed.bytes, seed.length);
                        if (w < 0) {
                            dprintf(STDERR_FILENO, "[stdin-pipe] seed write failed errno=%d\n", errno);
                        } else if ((unsigned long)w < seed.length) {
                            dprintf(STDERR_FILENO,
                                    "[stdin-pipe] WARNING seed TRUNCATED: %zd of %lu bytes -- "
                                    "the pipe buffer is full and nothing has read yet\n",
                                    w, (unsigned long)seed.length);
                        } else {
                            dprintf(STDERR_FILENO, "[stdin-pipe] seeded %zd bytes\n", w);
                        }
                    }
                }
            }

            // Separate knob: ask the guest to build a console anyway. Read here
            // rather than in ContentView because this runs in the same process
            // before __wine_main, so setenv is visible to env_ios.c.
            if ([[NSFileManager defaultManager] fileExistsAtPath:
                 [docs2 stringByAppendingPathComponent:@"bcd-console.txt"]]) {
                setenv("MADEIRA_FORCE_CONSOLE", "1", 1);
                dprintf(STDERR_FILENO,
                        "[force-console] MADEIRA_FORCE_CONSOLE=1 via bcd-console.txt\n");
            }
        }

        // Pick which exe to run (env var override, default = cube.exe).
        // Set MADEIRA_EXE=hello-x64.exe in env to launch the ARM64EC test path.
        const char *madeira_exe = getenv("MADEIRA_EXE");
        if (!madeira_exe || !*madeira_exe) madeira_exe = "cube.exe";
        // Heuristic: x86_64 guest exes (cube-x64, hello-x64, real games like
        // Thumper) need the arm64ec-windows bundle (ARM64EC hybrid system
        // DLLs that interop with FEX-translated x86_64 code). ARM64-native
        // tests (cube.exe) use the aarch64-windows bundle.
        // MADEIRA_USE_ARM64EC=1 forces the arm64ec path explicitly.
        // Otherwise: detect "x64" in the exe name (cube-x64, fib-x64, etc.)
        // OR a Win32 full path (real game launches typically need ARM64EC).
        const char *force_ec = getenv("MADEIRA_USE_ARM64EC");
        BOOL use_arm64ec = (force_ec && *force_ec == '1') ||
                           (strstr(madeira_exe, "x64") != NULL) ||
                           (strchr(madeira_exe, '\\') != NULL);

        /* WoW64: a 32-bit (i386) target, from the PE header on disk rather
         * than the name. Its 64-bit half runs on the plain aarch64 core (the
         * unix loader resolves aarch64-windows for a process whose main image
         * is i386), whatever the heuristic above chose. Any other target
         * keeps the heuristic's answer unchanged. */
        NSString *bundleForProbe = [[NSBundle mainBundle] bundlePath];
        const BOOL has_i386_set = madeira_bundle_has_i386(bundleForProbe);
        const uint16_t target_machine = madeira_target_machine(madeira_exe, g_prefix_path, bundleForProbe);
        const BOOL is_i386_target = has_i386_set && target_machine == MADEIRA_IMAGE_FILE_MACHINE_I386;
        dprintf(STDERR_FILENO, "[WineProc] PE probe: machine=0x%x%s\n", target_machine,
                is_i386_target ? " (i386: WoW64)" :
                target_machine == MADEIRA_IMAGE_FILE_MACHINE_I386 ? " (i386, but the bundle has no i386-windows)" : "");
        if (is_i386_target) use_arm64ec = NO;
        if (madeira_target_is_cmd(madeira_exe)) {   /* madeira-bcd cmd-bundle: cmd.exe is aarch64-only, see madeira_target_is_cmd */
            if (use_arm64ec)
                dprintf(STDERR_FILENO, "[WineProc] cmd.exe exists only in aarch64-windows: that core runs it (the name heuristic's arm64ec-windows ends in c000007b)\n");
            use_arm64ec = NO;
        }
        const char *bundle_subdir = use_arm64ec ? "arm64ec-windows" : "aarch64-windows";
        LOG("Target exe: %{public}s (bundle=%{public}s)", madeira_exe, bundle_subdir);
        dprintf(STDERR_FILENO, "[WineProc] Target exe: %s (bundle=%s)\n", madeira_exe, bundle_subdir);

        // Ensure Wine prefix has system32 directory with DLLs from bundle
        {
            NSString *bundlePath = [[NSBundle mainBundle] bundlePath];
            NSString *dllSource = [bundlePath stringByAppendingPathComponent:[NSString stringWithUTF8String:bundle_subdir]];
            NSString *prefix = [NSString stringWithUTF8String:g_prefix_path];
            NSString *sys32Dir = [prefix stringByAppendingPathComponent:@"drive_c/windows/system32"];
            NSFileManager *fm = [NSFileManager defaultManager];

            [fm createDirectoryAtPath:sys32Dir withIntermediateDirectories:YES attributes:nil error:nil];

            NSArray *dlls = [fm contentsOfDirectoryAtPath:dllSource error:nil];
            int linked = 0;
            for (NSString *dll in dlls) {
                NSString *src = [dllSource stringByAppendingPathComponent:dll];
                NSString *dst = [sys32Dir stringByAppendingPathComponent:dll];
                // Remove stale symlinks and re-create (bundle path changes on reinstall)
                [fm removeItemAtPath:dst error:nil];
                if ([fm createSymbolicLinkAtPath:dst withDestinationPath:src error:nil])
                    linked++;
            }
            LOG("Symlinked %d DLLs from %{public}s to %{public}s", linked, bundle_subdir, sys32Dir.UTF8String);
            madeira_link_wbem(fm, prefix, @"system32", dllSource);   /* madeira-bcd: WMI for 64-bit targets */
            dprintf(STDERR_FILENO, "[WineProc] Symlinked %d DLLs from %s -> sys32\n", linked, bundle_subdir);

            // X3 mixed-mode: also link NON-COLLIDING files from the other
            // bundle arch so cross-arch child exes resolve by Win32 path
            // (e.g. proc-test-x64.exe in an aarch64 desktop session).
            // Canonical DLL names (ntdll.dll, ...) already link to the
            // session's set above and are skipped here; children load their
            // system DLLs arch-correctly via WINEDLLPATH + pe_dir probing.
            {
                const char *other_subdir = use_arm64ec ? "aarch64-windows" : "arm64ec-windows";
                NSString *otherSource = [bundlePath stringByAppendingPathComponent:[NSString stringWithUTF8String:other_subdir]];
                NSArray *others = [fm contentsOfDirectoryAtPath:otherSource error:nil];
                int crossLinked = 0;
                for (NSString *f in others) {
                    NSString *dst = [sys32Dir stringByAppendingPathComponent:f];
                    // fileExistsAtPath FOLLOWS symlinks: YES means the session
                    // (main) pass already linked this name to a resolvable
                    // file — that arch wins, leave it.
                    if ([fm fileExistsAtPath:dst]) continue;
                    // NO means absent OR a stale/dangling symlink left by a
                    // previous install (bundle UUID changed on reinstall).
                    // createSymbolicLink fails with EEXIST on a dangling link
                    // that still occupies the path — which silently left the
                    // -x64 files pointing at a dead bundle, so they vanished
                    // from Wine's dir enumeration. Clear then recreate, like
                    // the main pass does.
                    [fm removeItemAtPath:dst error:nil];
                    NSString *src = [otherSource stringByAppendingPathComponent:f];
                    if ([fm createSymbolicLinkAtPath:dst withDestinationPath:src error:nil])
                        crossLinked++;
                }
                dprintf(STDERR_FILENO, "[WineProc] Cross-linked %d non-colliding files from %s -> sys32\n",
                        crossLinked, other_subdir);
            }

            // X3c mixed-mode: full per-arch DLL farms. A cross-arch child's
            // private ntdll retries C:\windows\sysx64 (SysWOW64-style) when a
            // system32 name resolves to the session arch's binary — colliding
            // names (ucrtbase, kernel32, ...) always do. sysaa64 is the
            // mirror for the future inverse case (aarch64 child in an EC
            // session, e.g. rpcss under Steam).
            {
                struct { const char *farm; const char *arch; } farms[] = {
                    { "sysx64",  "arm64ec-windows" },
                    { "sysaa64", "aarch64-windows" },
                };
                for (int i = 0; i < 2; i++) {
                    NSString *farmDir = [prefix stringByAppendingPathComponent:
                        [NSString stringWithFormat:@"drive_c/windows/%s", farms[i].farm]];
                    NSString *archSource = [bundlePath stringByAppendingPathComponent:
                        [NSString stringWithUTF8String:farms[i].arch]];
                    [fm createDirectoryAtPath:farmDir withIntermediateDirectories:YES attributes:nil error:nil];
                    NSArray *files = [fm contentsOfDirectoryAtPath:archSource error:nil];
                    int farmLinked = 0;
                    for (NSString *f in files) {
                        NSString *dst = [farmDir stringByAppendingPathComponent:f];
                        [fm removeItemAtPath:dst error:nil];  // self-heal stale links on reinstall
                        NSString *src = [archSource stringByAppendingPathComponent:f];
                        if ([fm createSymbolicLinkAtPath:dst withDestinationPath:src error:nil])
                            farmLinked++;
                    }
                    dprintf(STDERR_FILENO, "[WineProc] Farm %s: %d links -> %s\n",
                            farms[i].farm, farmLinked, farms[i].arch);
                }
            }

            /* WoW64: the i386 farm, syswow64\wbem and the x86 side-by-side
             * store for every session once the bundle has it (docs/WOW64.md).
             * The store was seeded for a 32-bit target only, but a 64-bit
             * target (a launcher, the Dock host) starts 32-bit children too,
             * and their activation contexts redirect comctl32 and the VC80/
             * VC90 CRT into the store. Its links name the bundle path, which
             * changes on every reinstall: a session with a 64-bit target after
             * a reinstall left them dangling, and such a child died in the
             * loader with c0000135 for DLLs syswow64 still had. */
            if (has_i386_set) {
                madeira_link_syswow64(fm, prefix, bundlePath);
                madeira_link_syswow64_wbem(fm, prefix, bundlePath);
                madeira_seed_winsxs_x86(fm, prefix, bundlePath);
            }
            madeira_link_wine_mono(fm, prefix, bundlePath);

            /* madeira-bcd (2026-10-22): MADEIRA_MSIEXEC_SYSTEM32=1 (set for an Epic game's session by ContentView, or by a
             * game's own env line) links the bundle's msiexec.exe, which exists only as i386, into the 64-bit system32 for
             * this session: a 64-bit program that runs "msiexec" from PATH then finds it, and Wine starts it as WoW64
             * (Paradox's launcher stub, build 585: "exec: msiexec: executable file not found in %PATH%"). Without the
             * variable a link BCD made earlier is removed. A real file or any other link named msiexec.exe is left alone. */
            {
                NSString *msiDst = [sys32Dir stringByAppendingPathComponent:@"msiexec.exe"];
                NSString *msiSrc = [[bundlePath stringByAppendingPathComponent:@"i386-windows"] stringByAppendingPathComponent:@"msiexec.exe"];
                const char *msiWant = getenv("MADEIRA_MSIEXEC_SYSTEM32");
                struct stat msiSt;
                const BOOL msiThere = lstat(msiDst.fileSystemRepresentation, &msiSt) == 0;
                NSString *msiCur = (msiThere && S_ISLNK(msiSt.st_mode)) ? [fm destinationOfSymbolicLinkAtPath:msiDst error:nil] : nil;
                const BOOL msiOurs = msiCur != nil && [msiCur hasSuffix:@"/i386-windows/msiexec.exe"];
                if (msiWant && strcmp(msiWant, "1") == 0 && has_i386_set && [fm fileExistsAtPath:msiSrc]) {
                    if (!msiThere || msiOurs) {
                        [fm removeItemAtPath:msiDst error:nil];
                        const BOOL ok = [fm createSymbolicLinkAtPath:msiDst withDestinationPath:msiSrc error:nil];
                        dprintf(STDERR_FILENO, "[msiexec64] system32\\msiexec.exe -> i386 msiexec: %s\n", ok ? "linked" : "link FAILED");
                    } else {
                        dprintf(STDERR_FILENO, "[msiexec64] system32\\msiexec.exe exists and is not BCD's: left alone\n");
                    }
                } else if (msiOurs) {
                    [fm removeItemAtPath:msiDst error:nil];
                    dprintf(STDERR_FILENO, "[msiexec64] removed BCD's system32\\msiexec.exe link (not asked for this session)\n");
                }
            }

            /* madeira-bcd: the AVX opt-in lives in a separately built FEX module
             * (tools/build-xtajit64.sh ships it as xtajit64-avx.dll). Only a
             * game whose settings ask for AVX gets it; everything else keeps
             * upstream's xtajit64.dll. Wine loads the emulator as
             * C:\windows\system32\xtajit64.dll, one of the links made above.
             * BCD (2026-10-23): the value 2 (the game's own line env.MADEIRA_FEX_AVX = 2) takes the same module;
             * the module then reports AVX without AVX2 (FEX CPUID, HideAVX2). 1 and unset are unchanged. */
            {
                const char *avxEnv = getenv("MADEIRA_FEX_AVX");
                if (use_arm64ec && avxEnv && (avxEnv[0] == '1' || (avxEnv[0] == '2' && avxEnv[1] == '\0'))) {
                    const char *avxMode = avxEnv[0] == '2' ? "2" : "1";
                    NSString *avxDll = [[bundlePath stringByAppendingPathComponent:@"arm64ec-windows"]
                                        stringByAppendingPathComponent:@"xtajit64-avx.dll"];
                    if ([fm fileExistsAtPath:avxDll]) {
                        for (NSString *dir in @[ sys32Dir, [prefix stringByAppendingPathComponent:@"drive_c/windows/sysx64"] ]) {
                            NSString *dst = [dir stringByAppendingPathComponent:@"xtajit64.dll"];
                            [fm removeItemAtPath:dst error:nil];
                            [fm createSymbolicLinkAtPath:dst withDestinationPath:avxDll error:nil];
                        }
                        dprintf(STDERR_FILENO, "[WineProc] MADEIRA_FEX_AVX=%s: xtajit64.dll -> xtajit64-avx.dll%s\n", avxMode,
                                avxEnv[0] == '2' ? " (AVX reported, AVX2 not)" : "");
                    } else {
                        dprintf(STDERR_FILENO, "[WineProc] MADEIRA_FEX_AVX=%s but this build has no xtajit64-avx.dll -- AVX stays off\n", avxMode);
                    }
                }
            }

            /* madeira-bcd: env.MADEIRA_DXGI_SRC = 1 (the game's own file) runs the
             * 64-bit dxgi.dll built from the dxmt submodule source
             * (tools/build-dxgi-dll.sh ships it as dxgi-src.dll): upstream's committed
             * DXMT dxgi.dll -- the CI checks the unpatched source build reproduces
             * it -- plus IDXGIFactory7 and EnumAdapterByLuid. GTA V Enhanced asks
             * for IDXGIFactory7 and stops with ERR_GFX_D3D_NOD3D12 without it. Off
             * by default: every other game keeps upstream's binary, and dropping
             * the line restores it at the next start (the farms above are relinked
             * from the bundle every session). sysx64 is relinked in any session, an
             * x64 child of an aarch64 session loads from there. */
            {
                const char *dxgiSrc = getenv("MADEIRA_DXGI_SRC");
                if (dxgiSrc && dxgiSrc[0] == '1') {
                    NSString *ecDir = [bundlePath stringByAppendingPathComponent:@"arm64ec-windows"];
                    NSString *srcDll = [ecDir stringByAppendingPathComponent:@"dxgi-src.dll"];
                    if ([fm fileExistsAtPath:srcDll]) {
                        NSMutableArray *dirs = [NSMutableArray arrayWithObject:
                            [prefix stringByAppendingPathComponent:@"drive_c/windows/sysx64"]];
                        if (use_arm64ec) [dirs addObject:sys32Dir];
                        for (NSString *dir in dirs) {
                            NSString *dst = [dir stringByAppendingPathComponent:@"dxgi.dll"];
                            [fm removeItemAtPath:dst error:nil];
                            [fm createSymbolicLinkAtPath:dst withDestinationPath:srcDll error:nil];
                        }
                        dprintf(STDERR_FILENO, "[WineProc] MADEIRA_DXGI_SRC=1: dxgi.dll -> dxgi-src.dll (DXMT source build with IDXGIFactory7; %s)\n",
                                use_arm64ec ? "system32 + sysx64" : "sysx64 only");
                    } else {
                        dprintf(STDERR_FILENO, "[WineProc] MADEIRA_DXGI_SRC=1 but this build has no dxgi-src.dll -- upstream's dxgi.dll stays\n");
                    }
                }
            }

            /* madeira-bcd: env.MADEIRA_D3D11_SRC = 1 (the game's own file) runs the
             * 64-bit d3d11.dll built from the dxmt submodule source
             * (tools/build-d3d11-dll.sh ships it as d3d11-src.dll): upstream's
             * committed DXMT d3d11.dll -- the CI checks the unpatched source build
             * against it -- plus ID3D11DeviceContext1::SwapDeviceContextState,
             * which upstream's aborts in. Wine's d2d1 calls it around every draw:
             * the Rockstar Games Launcher exited with code 3 there. Off by default,
             * linked like dxgi-src.dll above (system32 + sysx64, relinked from the
             * bundle every session, so dropping the line restores upstream's). */
            {
                const char *d3d11Src = getenv("MADEIRA_D3D11_SRC");
                const char *hdr = getenv("DXMT_AUTO_HDR");
                BOOL autoHDR = hdr && !strcmp(hdr, "1");
                // The Display toggle selects its matching source presenter automatically.
                if ((d3d11Src && d3d11Src[0] == '1') || autoHDR) {
                    NSString *ecDir = [bundlePath stringByAppendingPathComponent:@"arm64ec-windows"];
                    NSString *srcDll = [ecDir stringByAppendingPathComponent:@"d3d11-src.dll"];
                    if ([fm fileExistsAtPath:srcDll]) {
                        NSMutableArray *dirs = [NSMutableArray arrayWithObject:
                            [prefix stringByAppendingPathComponent:@"drive_c/windows/sysx64"]];
                        if (use_arm64ec) [dirs addObject:sys32Dir];
                        for (NSString *dir in dirs) {
                            NSString *dst = [dir stringByAppendingPathComponent:@"d3d11.dll"];
                            [fm removeItemAtPath:dst error:nil];
                            [fm createSymbolicLinkAtPath:dst withDestinationPath:srcDll error:nil];
                        }
                        dprintf(STDERR_FILENO, "[WineProc] %s: d3d11.dll -> d3d11-src.dll (DXMT source build with SwapDeviceContextState; %s)\n",
                                autoHDR ? "DXMT_AUTO_HDR=1" : "MADEIRA_D3D11_SRC=1", use_arm64ec ? "system32 + sysx64" : "sysx64 only");
                    } else {
                        dprintf(STDERR_FILENO, "[WineProc] %s but this build has no d3d11-src.dll -- upstream's d3d11.dll stays\n",
                                autoHDR ? "DXMT_AUTO_HDR=1" : "MADEIRA_D3D11_SRC=1");
                    }
                }
            }

            /* madeira-bcd (2026-10-23): env.MADEIRA_D3D9_X64 = dxmt (the game's own file) runs the 64-bit
             * d3d9.dll built from the dxmt submodule (tools/build-d3d9-x64-dll.sh ships it as d3d9-dxmt.dll):
             * DXMT's Direct3D 9 frontend, the one the 32-bit games already run as d3d9-emulated.dll,
             * compiled for arm64ec. A 64-bit Direct3D 9 game loads Wine's own d3d9.dll, which draws through
             * wined3d's OpenGL backend, and BCD has none ("Direct3DCreate9 called -> d3d9_init FAILED":
             * Darksiders II Deathinitive Edition, PICO PARK). Off by default: every other game keeps Wine's
             * d3d9.dll byte for byte, and dropping the line restores it at the next start (the farms above
             * are relinked from the bundle every session). Linked like dxgi-src.dll: sysx64 in any session
             * (an x64 child of an aarch64 session loads from there), system32 too in an ARM64EC one. Only
             * the value "dxmt" counts; the 32-bit d3d9.dll (the shim, in syswow64) is not touched. */
            /* madeira-bcd (2026-10-25, owner: the Start menu's "DirectX 9 test (64-bit)" failed): in the plain Windows desktop
             * session (not Dock, explorer.exe; the host-status rule) 64-bit Direct3D 9 defaults to DXMT, since Wine's d3d9.dll can
             * never draw on iOS. A player's own value (env.MADEIRA_D3D9_X64 = wined3d in the desktop's file) still wins (setenv 0). */
            if (bcd_host_status_wanted(getenv("MADEIRA_DESKTOP"), getenv("MADEIRA_DOCK_SESSION"), getenv("MADEIRA_EXE"), NULL)) {
                setenv("MADEIRA_D3D9_X64", "dxmt", 0);
                /* 2026-10-25 (owner: the Start menu's 64-bit video test failed): Media Foundation video for 64-bit programs in the plain
                 * desktop session too (winegstreamer 64-bit, the ASF source, WMV and WMA decoders); never over the player's own values. */
                setenv("MADEIRA_WG_64BIT", "1", 0);
                setenv("MADEIRA_OPTIN_DLLS", "mfasfsrcsnk,wmvdecod,wmadmod", 0);
            }
            {
                const char *d3d9X64 = getenv("MADEIRA_D3D9_X64");
                if (d3d9X64 && strcmp(d3d9X64, "dxmt") == 0) {
                    NSString *ecDir = [bundlePath stringByAppendingPathComponent:@"arm64ec-windows"];
                    NSString *dxmtDll = [ecDir stringByAppendingPathComponent:@"d3d9-dxmt.dll"];
                    if ([fm fileExistsAtPath:dxmtDll]) {
                        NSMutableArray *dirs = [NSMutableArray arrayWithObject:
                            [prefix stringByAppendingPathComponent:@"drive_c/windows/sysx64"]];
                        if (use_arm64ec) [dirs addObject:sys32Dir];
                        for (NSString *dir in dirs) {
                            NSString *dst = [dir stringByAppendingPathComponent:@"d3d9.dll"];
                            [fm removeItemAtPath:dst error:nil];
                            [fm createSymbolicLinkAtPath:dst withDestinationPath:dxmtDll error:nil];
                        }
                        dprintf(STDERR_FILENO, "[WineProc] MADEIRA_D3D9_X64=dxmt: d3d9.dll -> d3d9-dxmt.dll (DXMT's Direct3D 9 for 64-bit programs; %s)\n",
                                use_arm64ec ? "system32 + sysx64" : "sysx64 only");
                    } else {
                        dprintf(STDERR_FILENO, "[WineProc] MADEIRA_D3D9_X64=dxmt but this build has no d3d9-dxmt.dll -- Wine's d3d9.dll stays\n");
                    }
                }
            }

            /* madeira-bcd (round 602): the two 32-bit replacements for the old Direct3D and DirectDraw, env.MADEIRA_D3D8 = d3d9 (crosire's d3d8to9,
             * Direct3D 8 on DXMT's Direct3D 9) and env.MADEIRA_DDRAW = cnc (cnc-ddraw), both off by default and linked over the farm's Wine DLLs
             * by BCDGraphicsRoutes.c; the farm above was relinked from the bundle a moment ago, so a session without the line has Wine's. */
            /* madeira-bcd (2026-10-25, owner: the Start menu's DirectX 8 test failed): the plain Windows desktop session routes Direct3D 8
             * to Direct3D 9 by default (Wine's d3d8 needs OpenGL); a player's own env.MADEIRA_D3D8 still wins (setenv 0). */
            if (bcd_host_status_wanted(getenv("MADEIRA_DESKTOP"), getenv("MADEIRA_DOCK_SESSION"), getenv("MADEIRA_EXE"), NULL))
                setenv("MADEIRA_D3D8", "d3d9", 0);
            if (has_i386_set) {
                bcd_wow_routes_apply([[prefix stringByAppendingPathComponent:@"drive_c/windows/syswow64"] fileSystemRepresentation],
                                     [[bundlePath stringByAppendingPathComponent:@"i386-windows"] fileSystemRepresentation], STDERR_FILENO);
            }

            /* madeira-bcd: env.MADEIRA_UCRT_X64_MATH = 1 (the game's own file) runs
             * ucrtbase-x64math.dll as ucrtbase.dll: Wine's ucrtbase built from the
             * submodule with tools/patch-wine-ucrtbase-x64-math.py, whose floor and
             * sqrt exports are x64 code, so an x64 caller stays in the emulator
             * instead of crossing to the native body and back. Horizon Zero Dawn
             * (build 492) makes 2-4 million such calls a second, about half of all
             * its x64->ARM64EC calls. Off by default: every other game keeps the
             * shipped ucrtbase.dll, and dropping the line restores it at the next
             * start (the farms above are relinked from the bundle every session).
             * ARM64EC sessions only, linked like xtajit64-avx.dll. */
            {
                const char *ucrtX64 = getenv("MADEIRA_UCRT_X64_MATH");
                if (use_arm64ec && ucrtX64 && ucrtX64[0] == '1') {
                    NSString *mathDll = [[bundlePath stringByAppendingPathComponent:@"arm64ec-windows"]
                                         stringByAppendingPathComponent:@"ucrtbase-x64math.dll"];
                    if ([fm fileExistsAtPath:mathDll]) {
                        for (NSString *dir in @[ sys32Dir, [prefix stringByAppendingPathComponent:@"drive_c/windows/sysx64"] ]) {
                            NSString *dst = [dir stringByAppendingPathComponent:@"ucrtbase.dll"];
                            [fm removeItemAtPath:dst error:nil];
                            [fm createSymbolicLinkAtPath:dst withDestinationPath:mathDll error:nil];
                        }
                        dprintf(STDERR_FILENO, "[WineProc] MADEIRA_UCRT_X64_MATH=1: ucrtbase.dll -> ucrtbase-x64math.dll (floor and sqrt as x64 code for x64 callers)\n");
                    } else {
                        dprintf(STDERR_FILENO, "[WineProc] MADEIRA_UCRT_X64_MATH=1 but this build has no ucrtbase-x64math.dll -- the shipped ucrtbase.dll stays\n");
                    }
                }
            }

            /* madeira-bcd: env.MADEIRA_FIBER_TRACE = 1 (the game's own file) runs
             * kernelbase-fibertrace.dll as kernelbase.dll: Wine's kernelbase built from
             * the submodule with tools/patch-wine-kernelbase-fibertrace.py, which logs
             * the fiber calls as [fiber-trace] lines (creation, conversion, deletion, a
             * summary every 2 s, fibers parked for 3 s or more with the x64 calls on
             * their stacks, wrong switches, the table at an unhandled exception).
             * Microsoft Flight Simulator 2024 stops with "FiberError" once its job
             * system has run out of free fibers (build 540). A diagnostic, off by
             * default: every other game keeps the shipped kernelbase.dll, and dropping
             * the line restores it at the next start. Linked like dxgi-src.dll:
             * sysx64 in any session, system32 too in an ARM64EC one. A Steam game
             * runs in the Dock's aarch64 session and its x64 process loads
             * kernelbase.dll from sysx64; with system32 alone (ARM64EC sessions
             * only, until build 545) the line did nothing there (MSFS 2024, build
             * 543: no [WineProc] line, no [fiber-trace] lines). */
            {
                const char *fiberTrace = getenv("MADEIRA_FIBER_TRACE");
                if (fiberTrace && fiberTrace[0] == '1') {
                    NSString *traceDll = [[bundlePath stringByAppendingPathComponent:@"arm64ec-windows"]
                                          stringByAppendingPathComponent:@"kernelbase-fibertrace.dll"];
                    if ([fm fileExistsAtPath:traceDll]) {
                        NSMutableArray *dirs = [NSMutableArray arrayWithObject:
                            [prefix stringByAppendingPathComponent:@"drive_c/windows/sysx64"]];
                        if (use_arm64ec) [dirs addObject:sys32Dir];
                        for (NSString *dir in dirs) {
                            NSString *dst = [dir stringByAppendingPathComponent:@"kernelbase.dll"];
                            [fm removeItemAtPath:dst error:nil];
                            [fm createSymbolicLinkAtPath:dst withDestinationPath:traceDll error:nil];
                        }
                        dprintf(STDERR_FILENO, "[WineProc] MADEIRA_FIBER_TRACE=1: kernelbase.dll -> kernelbase-fibertrace.dll (fiber calls logged as [fiber-trace] lines; %s)\n",
                                use_arm64ec ? "system32 + sysx64" : "sysx64 only");
                    } else {
                        dprintf(STDERR_FILENO, "[WineProc] MADEIRA_FIBER_TRACE=1 but this build has no kernelbase-fibertrace.dll -- the shipped kernelbase.dll stays\n");
                    }
                }
            }

            /* madeira-bcd: env.MADEIRA_WER = 1 (the game's own file) runs wer-optin.dll as
             * wer.dll: Wine's own wer.dll, which tools/build-wine-extra-dlls.sh ships under
             * that name and never as wer.dll. Hello Neighbor's UE4 executable imports
             * wer.dll and exited at once with 0xc0000135 (a tester's log, build 573).
             * Wine's wer.dll has 68 unimplemented exports, so a program that loads it
             * optionally today could be aborted by one of them: only a game with the line
             * gets it (the line is added after a "Library wer.dll ... not found" failure).
             * wer.dll is no part of the bundle, so the farms above never relink or remove
             * it: without the line a link of ours left by an earlier session is removed,
             * so the game that needed it is the only one that has it. Linked like
             * kernelbase-fibertrace.dll: sysx64 in any session, system32 too in an
             * ARM64EC one. */
            {
                const char *werOptIn = getenv("MADEIRA_WER");
                NSMutableArray *werDirs = [NSMutableArray arrayWithObject:
                    [prefix stringByAppendingPathComponent:@"drive_c/windows/sysx64"]];
                if (use_arm64ec) [werDirs addObject:sys32Dir];
                NSString *werDll = [[bundlePath stringByAppendingPathComponent:@"arm64ec-windows"]
                                    stringByAppendingPathComponent:@"wer-optin.dll"];
                if (werOptIn && werOptIn[0] == '1') {
                    if ([fm fileExistsAtPath:werDll]) {
                        for (NSString *dir in werDirs) {
                            NSString *dst = [dir stringByAppendingPathComponent:@"wer.dll"];
                            [fm removeItemAtPath:dst error:nil];
                            [fm createSymbolicLinkAtPath:dst withDestinationPath:werDll error:nil];
                        }
                        dprintf(STDERR_FILENO, "[WineProc] MADEIRA_WER=1: wer.dll -> wer-optin.dll (Wine's wer.dll for this game; %s)\n",
                                use_arm64ec ? "system32 + sysx64" : "sysx64 only");
                    } else {
                        dprintf(STDERR_FILENO, "[WineProc] MADEIRA_WER=1 but this build has no wer-optin.dll -- the game gets no wer.dll\n");
                    }
                } else {
                    for (NSString *dir in @[ [prefix stringByAppendingPathComponent:@"drive_c/windows/sysx64"], sys32Dir ]) {
                        NSString *dst = [dir stringByAppendingPathComponent:@"wer.dll"];
                        NSString *to = [fm destinationOfSymbolicLinkAtPath:dst error:nil];
                        if (to && [to.lastPathComponent isEqualToString:@"wer-optin.dll"]) [fm removeItemAtPath:dst error:nil];
                    }
                }
            }

            /* madeira-bcd: env.MADEIRA_WINTYPES = 1 runs wintypes-optin.dll as wintypes.dll, as
             * MADEIRA_WER above. Unity IL2CPP's GameAssembly.dll imports
             * api-ms-win-core-winrt-robuffer-l1-1-0.dll, which Wine's apiset maps to wintypes.dll,
             * and the 64-bit set ships none: Megabonk, No, I'm not a Human and Over the Hill ended
             * at once with c0000135 (testers' logs, build 574). Chromium (Steam's web helper)
             * loads wintypes.dll optionally and works without it, so only a game with the line
             * gets it; without the line a link of ours left by an earlier session is removed. */
            {
                const char *wtyOptIn = getenv("MADEIRA_WINTYPES");
                NSMutableArray *wtyDirs = [NSMutableArray arrayWithObject:
                    [prefix stringByAppendingPathComponent:@"drive_c/windows/sysx64"]];
                if (use_arm64ec) [wtyDirs addObject:sys32Dir];
                NSString *wtyDll = [[bundlePath stringByAppendingPathComponent:@"arm64ec-windows"]
                                    stringByAppendingPathComponent:@"wintypes-optin.dll"];
                if (wtyOptIn && wtyOptIn[0] == '1') {
                    if ([fm fileExistsAtPath:wtyDll]) {
                        for (NSString *dir in wtyDirs) {
                            NSString *dst = [dir stringByAppendingPathComponent:@"wintypes.dll"];
                            [fm removeItemAtPath:dst error:nil];
                            [fm createSymbolicLinkAtPath:dst withDestinationPath:wtyDll error:nil];
                        }
                        dprintf(STDERR_FILENO, "[WineProc] MADEIRA_WINTYPES=1: wintypes.dll -> wintypes-optin.dll (Wine's wintypes.dll for this game; %s)\n",
                                use_arm64ec ? "system32 + sysx64" : "sysx64 only");
                    } else {
                        dprintf(STDERR_FILENO, "[WineProc] MADEIRA_WINTYPES=1 but this build has no wintypes-optin.dll -- the game gets no wintypes.dll\n");
                    }
                } else {
                    for (NSString *dir in @[ [prefix stringByAppendingPathComponent:@"drive_c/windows/sysx64"], sys32Dir ]) {
                        NSString *dst = [dir stringByAppendingPathComponent:@"wintypes.dll"];
                        NSString *to = [fm destinationOfSymbolicLinkAtPath:dst error:nil];
                        if (to && [to.lastPathComponent isEqualToString:@"wintypes-optin.dll"]) [fm removeItemAtPath:dst error:nil];
                    }
                }
            }

            /* madeira-bcd: env.MADEIRA_OPTIN_DLLS = dxva2,evr (comma or semicolon separated) runs each
             * listed <name>-optin.dll that this build ships (tools/build-wine-extra-dlls.sh, OPTIN_WANT)
             * as <name>.dll, as MADEIRA_WER and MADEIRA_WINTYPES above. Fort Solis and DRAGON BALL
             * Sparking! ZERO import dxva2.dll and ended at once with c0000135 (testers' logs, build
             * 574). Every other shipped *-optin.dll that is not listed has a link of ours removed, so
             * only the game that lists one has it. wer and wintypes keep their own switches.
             * setupapi is the one name that IS shipped (setupapi.dll, linked into both farms above):
             * setupapi-optin.dll is the same DLL with a lock round its device node table
             * (tools/patch-wine-setupapi-devinst-lock.py; Dying Light's controller library and main
             * thread raced in it, build 576). The loop below links it over the farm's link for a game
             * that lists setupapi; the farms are relinked from the bundle at the start of every session,
             * so the next session without the line has the shipped file again, and the removal below
             * only ever removes a link to the -optin file, never the shipped setupapi.dll. */
            {
                const char *optList = getenv("MADEIRA_OPTIN_DLLS");
                NSMutableSet<NSString *> *wanted = [NSMutableSet set];
                if (optList) {
                    for (NSString *part in [[NSString stringWithUTF8String:optList]
                                               componentsSeparatedByCharactersInSet:[NSCharacterSet characterSetWithCharactersInString:@",; "]]) {
                        NSString *n = part.lowercaseString;
                        if ([n hasSuffix:@".dll"]) n = [n substringToIndex:n.length - 4];
                        if (n.length) [wanted addObject:n];
                    }
                }
                if ([wanted containsObject:@"evr"]) [wanted addObject:@"dxva2"];
                if ([wanted containsObject:@"sfc"]) [wanted addObject:@"sfc_os"];
                if (([wanted containsObject:@"wmadmod"] || [wanted containsObject:@"wmvdecod"] || [wanted containsObject:@"mfasfsrcsnk"]) && !getenv("MADEIRA_WG_64BIT")) setenv("MADEIRA_WG_64BIT", "1", 0);
                NSString *ecDir = [bundlePath stringByAppendingPathComponent:@"arm64ec-windows"];
                NSArray *optDirs = use_arm64ec
                    ? @[ [prefix stringByAppendingPathComponent:@"drive_c/windows/sysx64"], sys32Dir ]
                    : @[ [prefix stringByAppendingPathComponent:@"drive_c/windows/sysx64"] ];
                for (NSString *file in [fm contentsOfDirectoryAtPath:ecDir error:nil]) {
                    if (![file hasSuffix:@"-optin.dll"]) continue;
                    NSString *name = [file substringToIndex:file.length - 10];
                    if ([name isEqualToString:@"wer"] || [name isEqualToString:@"wintypes"]) continue;
                    NSString *src = [ecDir stringByAppendingPathComponent:file];
                    NSString *dll = [name stringByAppendingString:@".dll"];
                    if ([wanted containsObject:name]) {
                        for (NSString *dir in optDirs) {
                            NSString *dst = [dir stringByAppendingPathComponent:dll];
                            [fm removeItemAtPath:dst error:nil];
                            [fm createSymbolicLinkAtPath:dst withDestinationPath:src error:nil];
                        }
                        [wanted removeObject:name];
                        dprintf(STDERR_FILENO, "[WineProc] MADEIRA_OPTIN_DLLS: %s -> %s (Wine's %s for this game)\n",
                                dll.UTF8String, file.UTF8String, dll.UTF8String);
                    } else {
                        for (NSString *dir in @[ [prefix stringByAppendingPathComponent:@"drive_c/windows/sysx64"], sys32Dir ]) {
                            NSString *dst = [dir stringByAppendingPathComponent:dll];
                            NSString *to = [fm destinationOfSymbolicLinkAtPath:dst error:nil];
                            if (to && [to.lastPathComponent isEqualToString:file]) [fm removeItemAtPath:dst error:nil];
                        }
                    }
                }
                for (NSString *name in wanted)
                    dprintf(STDERR_FILENO, "[WineProc] MADEIRA_OPTIN_DLLS lists %s, which this build does not ship as %s-optin.dll\n",
                            name.UTF8String, name.UTF8String);
            }

            /* madeira-bcd: ntdll-sync.dll (tools/patch-wine-ntdll-sync.py, built by
             * tools/build-wine-extra-dlls.sh next to the shipped ntdll.dll) is the
             * ARM64EC ntdll the unix loader maps when env.MADEIRA_NTDLL_SYNC = 1 or
             * native (build/ntdll-unix/loader_ios.c, ios_ec_ntdll_name): the same
             * SRW locks and critical sections, cheaper. Off by default since build
             * 499 (on 498 every 64-bit game died at start; see loader_ios.c). Wine
             * names it C:\windows\system32\ntdll.dll, so that link (ARM64EC sessions)
             * and sysx64's (x64 children) name the same file: a program reading
             * ntdll.dll from disk sees what runs. Unset or 0 keeps the shipped
             * ntdll.dll for both; the farms above are relinked from the bundle
             * every session. */
            {
                const char *ntSync = getenv("MADEIRA_NTDLL_SYNC");
                BOOL ntSyncOn = ntSync && ((ntSync[0] == '1' && !ntSync[1]) || !strcmp(ntSync, "native"));
                NSString *syncDll = [[bundlePath stringByAppendingPathComponent:@"arm64ec-windows"]
                                     stringByAppendingPathComponent:@"ntdll-sync.dll"];
                if (ntSyncOn && [fm fileExistsAtPath:syncDll]) {
                    NSMutableArray *dirs = [NSMutableArray arrayWithObject:
                        [prefix stringByAppendingPathComponent:@"drive_c/windows/sysx64"]];
                    if (use_arm64ec) [dirs addObject:sys32Dir];
                    for (NSString *dir in dirs) {
                        NSString *dst = [dir stringByAppendingPathComponent:@"ntdll.dll"];
                        [fm removeItemAtPath:dst error:nil];
                        [fm createSymbolicLinkAtPath:dst withDestinationPath:syncDll error:nil];
                    }
                    dprintf(STDERR_FILENO, "[WineProc] ntdll.dll -> ntdll-sync.dll (opt-in MADEIRA_NTDLL_SYNC=%s; %s)\n",
                            ntSync, use_arm64ec ? "system32 + sysx64" : "sysx64 only");
                } else if (ntSyncOn) {
                    dprintf(STDERR_FILENO, "[WineProc] MADEIRA_NTDLL_SYNC=%s but this build has no ntdll-sync.dll: the shipped ntdll.dll\n", ntSync);
                }
            }

            /* madeira-bcd, from thebcd69/bcd-games#4 and thebcd69/bcd-wine#1 by mckenna654 (modified 2026-10-24): ntdll-xsec.dll
             * (tools/patch-wine-ntdll-xsec.py, built by tools/build-wine-extra-dlls.sh next to the shipped ntdll.dll) is the
             * ARM64EC ntdll the unix loader maps when env.MADEIRA_NTDLL_XSEC = 1 (build/ntdll-unix/loader_ios.c,
             * ios_ec_ntdll_name; it wins over ntdll-sync.dll, so this block comes after that one). Linked the same way: system32
             * (ARM64EC sessions) and sysx64 (x64 children) name the file the session runs. Unset or 0: nothing here, and the
             * links above are what they were. */
            {
                const char *xsec = getenv("MADEIRA_NTDLL_XSEC");
                BOOL xsecOn = xsec && xsec[0] == '1' && !xsec[1];
                NSString *xsecDll = [[bundlePath stringByAppendingPathComponent:@"arm64ec-windows"]
                                     stringByAppendingPathComponent:@"ntdll-xsec.dll"];
                if (xsecOn && [fm fileExistsAtPath:xsecDll]) {
                    NSMutableArray *dirs = [NSMutableArray arrayWithObject:
                        [prefix stringByAppendingPathComponent:@"drive_c/windows/sysx64"]];
                    if (use_arm64ec) [dirs addObject:sys32Dir];
                    for (NSString *dir in dirs) {
                        NSString *dst = [dir stringByAppendingPathComponent:@"ntdll.dll"];
                        [fm removeItemAtPath:dst error:nil];
                        [fm createSymbolicLinkAtPath:dst withDestinationPath:xsecDll error:nil];
                    }
                    dprintf(STDERR_FILENO, "[WineProc] ntdll.dll -> ntdll-xsec.dll (opt-in MADEIRA_NTDLL_XSEC=1; %s)\n",
                            use_arm64ec ? "system32 + sysx64" : "sysx64 only");
                } else if (xsecOn) {
                    dprintf(STDERR_FILENO, "[WineProc] MADEIRA_NTDLL_XSEC=1 but this build has no ntdll-xsec.dll: the shipped ntdll.dll\n");
                }
            }

            /* ml719: REPAIR THE SHELL FOLDERS. They ship as symlinks to the BUILD
             * MACHINE's home directory.
             *
             * prefix-template.tar.gz contains six absolute links --
             *   drive_c/users/madeira/Documents -> /Users/willfaust/Documents
             * and the same for Desktop, Downloads, Music, Pictures, Videos. That path
             * exists on no device, so every one of them is dangling everywhere the app has
             * ever been installed, including testers' phones. Anything resolving a Windows
             * shell folder silently fails: Marvel Cosmic Invasion's NLog target is
             * ${specialfolder:MyDocuments}/Tribute Games/... which is why no game log was
             * ever produced, and it is a live candidate for why the game exits at startup
             * (a title that cannot write its settings or save directory quitting cleanly is
             * ordinary behaviour).
             *
             * Regenerating the archive is necessary but NOT sufficient: the template is
             * extracted once, so existing prefixes keep the broken links forever. Hence
             * this runtime migration.
             *
             * Deliberately conservative -- lstat so a dangling link is still seen, and only
             * a symlink whose target is absent is touched. A real directory, or a link the
             * user made themselves that resolves, is left completely alone. Ordinary
             * directories rather than container-absolute symlinks: the container UUID
             * changes across reinstalls, so an absolute link would rot the same way. */
            {
                static const char *shell_dirs[] = {
                    "Documents", "Desktop", "Downloads", "Music", "Pictures", "Videos"
                };
                int repaired = 0, already = 0;
                for (int i = 0; i < 6; i++) {
                    NSString *sp = [prefix stringByAppendingPathComponent:
                        [NSString stringWithFormat:@"drive_c/users/madeira/%s", shell_dirs[i]]];
                    const char *cp = sp.fileSystemRepresentation;
                    struct stat lst;
                    if (lstat(cp, &lst) != 0) {          /* nothing there at all */
                        if (mkdir(cp, 0755) == 0) repaired++;
                        continue;
                    }
                    if (!S_ISLNK(lst.st_mode)) { already++; continue; }   /* real dir: leave */
                    struct stat tgt;
                    if (stat(cp, &tgt) == 0) { already++; continue; }     /* link resolves: leave */
                    char buf[1024]; ssize_t n = readlink(cp, buf, sizeof(buf) - 1);
                    if (n > 0) buf[n] = 0; else buf[0] = 0;
                    if (unlink(cp) == 0 && mkdir(cp, 0755) == 0) {
                        repaired++;
                        dprintf(STDERR_FILENO, "[shell-dir] ml719 repaired %s (was dangling -> %s)\n",
                                shell_dirs[i], buf);
                    } else {
                        dprintf(STDERR_FILENO, "[shell-dir] ml719 FAILED to repair %s (was -> %s) errno=%d\n",
                                shell_dirs[i], buf, errno);
                    }
                }
                dprintf(STDERR_FILENO, "[shell-dir] ml719 %d repaired, %d already good\n",
                        repaired, already);
            }

            /* madeira-bcd: the same folders for the user Wine actually runs as.
             * ml719 covers C:\users\madeira (the template's user), but Wine
             * names the profile after the unix user -- "mobile" on iOS -- so
             * %USERPROFILE% is C:\users\mobile, where Documents may be
             * missing or a dangling link. Ghost of Tsushima then stops with
             * "Unable to create the game's save folder" before touching the
             * filesystem. Make every folder games write to a real directory
             * (Saved Games and AppData too), for both names.
             *
             * madeira-bcd (2026-10-24, build 598): the user's own Start Menu\Programs too. The template ships it
             * for its own user only (users/mythic); the registry's User Shell Folders map "Start Menu" to
             * %USERPROFILE%\AppData\Roaming\Microsoft\Windows\Start Menu, which did not exist for "mobile". Wine's
             * explorer (the desktop of every session) then had no folder for the user's half of the Start menu,
             * and once the public half had entries (BCD's own, an installer's) pressing the Start button read
             * address 0 (explorer.exe 0x1400192cc) and the whole session ended: Battle.net, build 597, at its
             * sign-in. Every Windows profile has this folder; an existing one is left alone. */
            {
                NSMutableOrderedSet *names = [NSMutableOrderedSet orderedSetWithObject:@"madeira"];
                struct passwd *pw = getpwuid(getuid());
                if (pw && pw->pw_name && pw->pw_name[0]) [names addObject:@(pw->pw_name)];
                const char *envUser = getenv("USER");
                if (envUser && envUser[0]) [names addObject:@(envUser)];
                NSArray *rels = @[ @"Documents", @"Desktop", @"Downloads", @"Music", @"Pictures",
                                   @"Videos", @"Saved Games", @"AppData/Local", @"AppData/LocalLow",
                                   @"AppData/Roaming", @"AppData/Roaming/Microsoft/Windows/Start Menu/Programs" ];
                int made = 0;
                for (NSString *name in names) {
                    NSString *home = [prefix stringByAppendingPathComponent:
                        [@"drive_c/users" stringByAppendingPathComponent:name]];
                    for (NSString *rel in rels) {
                        NSString *path = [home stringByAppendingPathComponent:rel];
                        const char *cp = path.fileSystemRepresentation;
                        struct stat lst, tgt;
                        if (lstat(cp, &lst) == 0) {
                            if (!S_ISLNK(lst.st_mode) || stat(cp, &tgt) == 0) continue;
                            unlink(cp);                       /* dangling link */
                        }
                        if ([fm createDirectoryAtPath:path withIntermediateDirectories:YES
                                           attributes:nil error:nil]) made++;
                    }
                }
                dprintf(STDERR_FILENO, "[shell-dir] user folders for %s: %d created (Start Menu included)\n",
                        [[names.array componentsJoinedByString:@", "] UTF8String], made);
            }

            /* madeira-bcd (2026-10-23): the Wine desktop's Start menu gets an Explorer entry and one test program per
             * Direct3D 9, 10, 11 and 12 (BCDStartMenu.c): shortcut files in the prefix's public Start Menu folder, which
             * Wine's explorer lists when the Start button is pressed. Only a desktop session (the Windows desktop, a
             * program started in a Wine desktop) has such a menu, so only those sessions get the files; every other
             * launch, a BCD Dock session included, does not touch the folder. A file is written when missing or when
             * it is BCD's own and outdated, never over a file of a person's own. env.MADEIRA_START_MENU = 0 leaves
             * them out. */
            {
                const char *desk = getenv("MADEIRA_DESKTOP");
                const char *dockSession = getenv("MADEIRA_DOCK_SESSION");
                const char *menuOff = getenv("MADEIRA_START_MENU");
                if (desk && desk[0] == '1' && !(dockSession && dockSession[0] == '1') && !(menuOff && menuOff[0] == '0')) {
                    bcd_start_result sm = bcd_startmenu_install(prefix.fileSystemRepresentation, bundlePath.fileSystemRepresentation);
                    dprintf(STDERR_FILENO, "[start-menu] BCD entries: %d written, %d current, %d left alone, %d without a program, %d failed, "
                            "%d start a 32-bit program, %d of an older build removed%s (MADEIRA_START_MENU=0 leaves them out)\n",
                            sm.written, sm.current, sm.foreign, sm.missing, sm.failed, sm.x86, sm.retired,
                            sm.no_menu ? "; the prefix has no public Start Menu folder" : "");
                }
            }

            /* madeira-bcd (round 602): the system tests in the Start menu (BCD Tests) read the device and the app's facts from
             * C:\ProgramData\BCD\host-info.txt, and the engine's live numbers (build/ntdll-unix/ios_host_status.h) from
             * host-live.txt in the same folder. Only the plain Windows desktop session gets them (never a game, never a BCD
             * Dock session); env.MADEIRA_HOST_STATUS = 0 leaves them out. BCDHostStatus.c has the rule. */
            if (bcd_host_status_wanted(getenv("MADEIRA_DESKTOP"), getenv("MADEIRA_DOCK_SESSION"), getenv("MADEIRA_EXE"),
                                       getenv("MADEIRA_HOST_STATUS"))) {
                NSDictionary *info = NSBundle.mainBundle.infoDictionary;
                NSString *hsBuild = info[@"CFBundleVersion"], *hsVersion = info[@"CFBundleShortVersionString"];
                long long hsPool = madeira_cfg_int("pool", 0), hsSwap = madeira_cfg_int("swap-mb", 0);
                bcd_host_facts hf = { hsBuild.UTF8String, hsVersion.UTF8String, hsPool > 0 ? (unsigned long long)hsPool : 0,
                                      hsSwap > 0 ? (unsigned long long)hsSwap : 0 };
                int hsOn = bcd_host_status_prepare(prefix.fileSystemRepresentation, &hf);
                /* The Start menu starts its programs without BCD's stderr (shell32 gives them a new console), so the tests' result
                 * lines reach the session log only through the [guest-log] mirror of their own .log files: every line of it, up to
                 * 800, in this plain desktop session only, and never over a value the player set (setenv's last argument 0). */
                setenv("MADEIRA_GUEST_LOG", "all", 0);
                setenv("MADEIRA_GUEST_LOG_LIMIT", "800", 0);
                dprintf(STDERR_FILENO, "[host-status] %s (MADEIRA_HOST_STATUS=0 leaves it out)\n",
                        hsOn ? "host-info.txt written; the engine writes host-live.txt for the system tests"
                             : "host-info.txt could not be written; the system tests run without the app's numbers");
            }

            // Layer Microsoft's real VC++ Runtime DLLs ON TOP of the ARM64EC
            // bundle (only for x86_64 guests). These overwrite Wine's stub
            // builtins — Wine then loads the real MS x86_64 implementation
            // (via FEX) instead of its partial ARM64EC reimplementation.
            //
            // Same pattern Proton/Winlator use: drop in the real concrt140 /
            // msvcp140 / vcruntime140 binaries from VC_redist.x64.exe so games
            // that exercise the full C++ runtime (parallel_for, atomic_wait,
            // <filesystem>, etc.) don't trip __wine_unimplemented stubs.
            /* madeira-bcd: MADEIRA_WINE_VCRT=1 (a per-game switch) keeps
             * Wine's ARM64EC builtins for every VC++ runtime DLL. Ghost of
             * Tsushima dies right after loading the MS x86_64 concrt140,
             * in the x64<->EC transition the two exemptions below describe. */
            const char *wineVcrt = getenv("MADEIRA_WINE_VCRT");
            NSString *vcrtDir = use_arm64ec ? madeira_vcruntime_dir(bundlePath,   /* BCD: the bundle's DLLs, else the download */
                NSSearchPathForDirectoriesInDomains(NSApplicationSupportDirectory, NSUserDomainMask, YES).firstObject) : nil;
            NSMutableDictionary<NSString *, NSString *> *vcrtHome = [NSMutableDictionary dictionary];   /* madeira_vcruntime_dlls */
            NSUInteger vcrtFromDocs = 0;
            NSArray<NSString *> *vcrtDlls = use_arm64ec ? madeira_vcruntime_dlls(fm, vcrtDir,
                [NSSearchPathForDirectoriesInDomains(NSDocumentDirectory, NSUserDomainMask, YES).firstObject
                    stringByAppendingPathComponent:@"vcruntime"], vcrtHome, &vcrtFromDocs) : nil;
            if (vcrtFromDocs)
                dprintf(STDERR_FILENO, "[WineProc] VC++ runtime: %lu DLL(s) from Documents/vcruntime shadow the build's\n",
                        (unsigned long)vcrtFromDocs);
            if (use_arm64ec && wineVcrt && wineVcrt[0] == '1') {
                for (NSString *dll in vcrtDlls) {
                    NSString *dst = [sys32Dir stringByAppendingPathComponent:dll];
                    NSString *builtin = [[bundlePath stringByAppendingPathComponent:@"arm64ec-windows"] stringByAppendingPathComponent:dll];
                    if ([fm fileExistsAtPath:builtin]) {
                        [fm removeItemAtPath:dst error:nil];
                        [fm createSymbolicLinkAtPath:dst withDestinationPath:builtin error:nil];
                    }
                }
                LOG("MADEIRA_WINE_VCRT=1: Wine's ARM64EC VC++ runtime builtins kept, no MS overlay");
                dprintf(STDERR_FILENO, "[WineProc] MADEIRA_WINE_VCRT=1: MS VC++ runtime overlay skipped\n");
            } else
            if (use_arm64ec) {
                int vcrtLinked = 0, vcrtSkipped = 0;
                /* madeira-bcd: MFC (mfc140.dll, mfc140u.dll) comes from the same
                 * VC_redist.x64.exe since 2026-10-09: Horizon Zero Dawn imports
                 * mfc140.dll and stopped at once with c0000135 without it (log
                 * 12:51:47, build 466). Wine has no MFC of its own, so nothing
                 * else changes. env.MADEIRA_MFC = 0 leaves it out of system32. */
                const char *mfcEnv = getenv("MADEIRA_MFC");
                BOOL mfcOff = mfcEnv && mfcEnv[0] == '0';
                for (NSString *dll in vcrtDlls) {
                    if ([[dll lowercaseString] hasPrefix:@"mfc"]) {
                        NSString *dst = [sys32Dir stringByAppendingPathComponent:dll];
                        if (mfcOff) {
                            [fm removeItemAtPath:dst error:nil];   /* a link from an earlier session */
                            vcrtSkipped++;
                            dprintf(STDERR_FILENO, "[WineProc] %s left out (MADEIRA_MFC=0)\n", dll.UTF8String);
                            continue;
                        }
                        dprintf(STDERR_FILENO, "[WineProc] MFC %s linked (MADEIRA_MFC=0 leaves it out)\n", dll.UTF8String);
                    }
                    /* NOTE 2026-07-03 (late): retried lifting BOTH exemptions
                     * below after the fast-write bisect, hoping trap-mode had
                     * fixed the corruption class (and to keep hot CRT calls
                     * like memcpy inside the JIT — they cost a full x64→EC
                     * round trip as ARM64EC builtins, a large share of the
                     * 57ms menu frame). Result: guest RIP jumped to junk
                     * (0x600000010xx, lr=0xa59696ff...) right after
                     * MSVCP140/VCRUNTIME140 loaded x86_64, before present #1.
                     * So the x86→EC SEH/transition corruption is NOT the
                     * fast-write bug — it's still unfixed, and these
                     * exemptions must stay until it is. */
                    /* Keep vcruntime140.dll as the ARM64EC builtin: its
                     * __C_specific_handler is invoked by Wine's SEH dispatch,
                     * and routing that through FEX corrupts x86 RSP (SEH
                     * dispatcher's exit-thunk arg setup is broken). With the
                     * native arm64ec vcruntime140, Wine calls the handler
                     * directly in ARM64 — no FEX bridging on the exception
                     * path. Other vcruntime/msvcp/concrt DLLs still overlay. */
                    if ([[dll lowercaseString] isEqualToString:@"vcruntime140.dll"]) {
                        vcrtSkipped++;
                        continue;
                    }
                    /* msvcp140.dll: same exemption as vcruntime140, found
                     * 2026-07-03. The MS x86_64 msvcp140 throws a C++
                     * exception during its own DllMain; the x86 throw-record
                     * builder calls RtlPcToFileHeader cross-arch and the
                     * exception-path exit thunk corrupts guest RSP — the
                     * returned module base lands in the return-address slot
                     * and RIP jumps to the MZ header (NoExec loop, no
                     * splash). Keep the ARM64EC builtin so msvcp140's EH
                     * runs natively, like vcruntime140. */
                    if ([[dll lowercaseString] isEqualToString:@"msvcp140.dll"]) {
                        vcrtSkipped++;
                        continue;
                    }
                    NSString *src = vcrtHome[[dll lowercaseString]];   /* Documents/vcruntime's copy, else the build's */
                    NSString *dst = [sys32Dir stringByAppendingPathComponent:dll];
                    [fm removeItemAtPath:dst error:nil];
                    if ([fm createSymbolicLinkAtPath:dst withDestinationPath:src error:nil])
                        vcrtLinked++;
                }
                LOG("Symlinked %d MS VC++ Runtime DLLs (x86_64 native) over arm64ec builtins, skipped %d", vcrtLinked, vcrtSkipped);
                dprintf(STDERR_FILENO, "[WineProc] Symlinked %d MS VC++ Runtime DLLs over arm64ec builtins (skipped %d for native EC SEH)\n", vcrtLinked, vcrtSkipped);
            }

            /* madeira-bcd (round 591): env.MADEIRA_MFC_DOCK = 1 (the game's own line; the game's file was exported above, a Dock
             * start from the library included) links MFC into sysx64 for a session that is not ARM64EC, see madeira_mfc_dock. */
            madeira_mfc_dock(fm, [prefix stringByAppendingPathComponent:@"drive_c/windows/sysx64"], bundlePath,
                             NSSearchPathForDirectoriesInDomains(NSApplicationSupportDirectory, NSUserDomainMask, YES).firstObject,
                             use_arm64ec);

            /* BCD (issue #13): env.MADEIRA_VCRT_ARM64_KEY = 1 takes the session's VC++ runtime version when it is newer than
             * the marker's fixed one; the marker itself is written by the first Wine process (loader_ios.c). */
            madeira_vcrt_arm64_version(bundlePath,
                             NSSearchPathForDirectoriesInDomains(NSApplicationSupportDirectory, NSUserDomainMask, YES).firstObject);
        }

        // Build the launch path for Wine's PE loader.
        // If MADEIRA_EXE contains a backslash or starts with a drive letter
        // (e.g. "C:\\Program Files\\Thumper\\THUMPER_win10.exe"), use it
        // as-is. Otherwise treat it as a bare exe name in system32 (legacy
        // path used by cube/fib/hello tests).
        char exe_path[512];
        if (strchr(madeira_exe, '\\') || (madeira_exe[0] && madeira_exe[1] == ':')) {
            snprintf(exe_path, sizeof(exe_path), "%s", madeira_exe);
        } else if (is_i386_target) {
            /* WoW64: a bare i386 name lives in the syswow64 farm */
            snprintf(exe_path, sizeof(exe_path), "C:\\windows\\syswow64\\%s", madeira_exe);
        } else {
            snprintf(exe_path, sizeof(exe_path), "C:\\windows\\system32\\%s", madeira_exe);
        }

        // Optional MADEIRA_ARGS env var: args appended to argv, split in place.
        // ml1163: double quotes group a token, so a game path such as
        // "C:\Program Files\Game\game.exe" (a library game started in the Wine
        // desktop hands explorer one) stays ONE argument. The quotes themselves are
        // dropped: Wine re-quotes any argv entry with a space when it builds the
        // command line. Up to 64 extra tokens (was 16, split on spaces only).
        static char args_buf[4096];
        char *extra_argv[64] = {0};
        int extra_argc = 0;
        const char *madeira_args = getenv("MADEIRA_ARGS");
        if (madeira_args && *madeira_args) {
            strncpy(args_buf, madeira_args, sizeof(args_buf) - 1);
            args_buf[sizeof(args_buf) - 1] = 0;
            char *r = args_buf, *w = args_buf;   /* w never passes r: tokens only shrink */
            while (*r && extra_argc < 64) {
                while (*r == ' ' || *r == '\t') r++;
                if (!*r) break;
                extra_argv[extra_argc++] = w;
                int quoted = 0;
                while (*r && (quoted || (*r != ' ' && *r != '\t'))) {
                    if (*r == '"') { quoted = !quoted; r++; continue; }
                    *w++ = *r++;
                }
                if (*r) r++;
                *w++ = 0;
            }
        }

        char *argv[72];
        int argc = 0;
        argv[argc++] = "wine";
        argv[argc++] = exe_path;
        for (int i = 0; i < extra_argc; i++) argv[argc++] = extra_argv[i];
        argv[argc] = NULL;
        dprintf(STDERR_FILENO, "[WineProc] argv[1] = %s\n", exe_path);
        for (int i = 0; i < extra_argc; i++) {
            /* madeira-bcd (2026-10-22, Epic Games milestone 2): Epic's sign-in code, account ID and name print as "*" (build/bcd_epic_mask.h);
             * a signed-in start passes none of them here (BCD Host reads them from a one-use file), so this is a second line of defence. */
            char masked[sizeof(args_buf) + 1];
            long masked_len = bcd_epic_mask_a(extra_argv[i], strlen(extra_argv[i]), masked);
            if (masked_len >= 0) masked[masked_len] = 0;
            dprintf(STDERR_FILENO, "[WineProc] argv[%d] = %s\n", 2 + i, masked_len >= 0 ? masked : extra_argv[i]);
        }

        /* iOS-Madeira: chdir to the unix path that maps to the exe's Wine
         * directory BEFORE __wine_main. Wine inherits the iOS app sandbox
         * cwd, which becomes a `unix\private\var\mobile\...\Documents\wine\`
         * Wine path — and Thumper's relative cache opens (e.g.,
         * "cache/721e72f7.pc") then resolve to doubled paths that don't
         * exist. Per GPT diagnosis 2026-05-12. Only chdir for full-path EXE
         * launches; bare-name launches (cube, hello-x64) use C:\windows\system32.
         *
         * A launch may carry its working folder in MADEIRA_WORKDIR (a C:\ folder of
         * the prefix, for this launch only; cleared here), used instead of the exe's
         * own: Steam's launch configuration for "Start with: The game", and ml1163's
         * library working folder (LibraryEntry.launchDirectory: a chosen folder, or
         * the program's own when explorer (desktop mode) or cmd.exe (a .bat, the
         * services batch) is what starts). */
        const char *launch_workdir = getenv("MADEIRA_WORKDIR");   /* set by the library for one launch: the working folder (Steam's, or the entry's); not a setting */
        char workdir[512] = "";
        if (launch_workdir && (launch_workdir[0] == 'C' || launch_workdir[0] == 'c') && launch_workdir[1] == ':' &&
            launch_workdir[2] == '\\' && launch_workdir[3] && !strstr(launch_workdir, "..") &&
            strlen(launch_workdir) < sizeof(workdir) - 2)
            snprintf(workdir, sizeof(workdir), "%s", launch_workdir);
        unsetenv("MADEIRA_WORKDIR");
        /* ml1163: a typed folder may end in '\\'; MADEIRA_INITIAL_CWD gets exactly one. */
        for (size_t n = strlen(workdir); n > 3 && workdir[n - 1] == '\\'; n--) workdir[n - 1] = 0;
        if (workdir[0]) {
            char unix_dir[1024], windir[512], wine_cwd[520];
            snprintf(windir, sizeof(windir), "%s", workdir + 3);
            for (char *p = windir; *p; p++) if (*p == '\\') *p = '/';
            snprintf(unix_dir, sizeof(unix_dir), "%s/drive_c/%s", g_prefix_path, windir);
            int rc = chdir(unix_dir);
            setenv("PWD", unix_dir, 1);
            snprintf(wine_cwd, sizeof(wine_cwd), "%s\\", workdir);
            setenv("MADEIRA_INITIAL_CWD", wine_cwd, 1);
            dprintf(STDERR_FILENO, "[WineProc] working folder from the launch: chdir(%s) = %d errno=%d, MADEIRA_INITIAL_CWD=%s\n",
                    unix_dir, rc, rc ? errno : 0, wine_cwd);
        } else if (strchr(madeira_exe, '\\') || (madeira_exe[0] && madeira_exe[1] == ':')) {
            /* Convert "C:\Program Files\Thumper\X.exe" → unix path */
            char unix_dir[1024];
            const char *drive_c = "drive_c";
            const char *after_drive = madeira_exe + 3; /* skip "C:\" */
            char *last_sep = strrchr(madeira_exe, '\\');
            if (last_sep && last_sep > madeira_exe + 3) {
                /* Get "Program Files\Thumper" from "C:\Program Files\Thumper\X.exe" */
                size_t dir_len = (size_t)(last_sep - after_drive);
                char windir[512];
                memcpy(windir, after_drive, dir_len);
                windir[dir_len] = 0;
                /* Translate backslashes to forward slashes */
                for (char *p = windir; *p; p++) if (*p == '\\') *p = '/';
                snprintf(unix_dir, sizeof(unix_dir), "%s/%s/%s",
                         g_prefix_path, drive_c, windir);
                int rc = chdir(unix_dir);
                setenv("PWD", unix_dir, 1);
                /* Also set the iOS-specific override so env_ios.c's
                 * get_initial_directory bypasses unix_to_nt_file_name (which
                 * fails to resolve drive_c via dosdevices on iOS). */
                char wine_cwd[768];
                /* Strip trailing exe name from madeira_exe to get the dir part */
                {
                    const char *exe = madeira_exe;
                    size_t dir_len = (size_t)(last_sep - exe);
                    if (dir_len < sizeof(wine_cwd) - 2) {
                        memcpy(wine_cwd, exe, dir_len);
                        wine_cwd[dir_len] = '\\';
                        wine_cwd[dir_len + 1] = 0;
                        setenv("MADEIRA_INITIAL_CWD", wine_cwd, 1);
                    }
                }
                dprintf(STDERR_FILENO, "[WineProc] chdir(%s) = %d errno=%d, PWD + MADEIRA_INITIAL_CWD=%s\n",
                        unix_dir, rc, rc ? errno : 0, wine_cwd);
            }
        }

        // Record this thread so wine_ios_exit knows where to longjmp
        wine_ios_main_thread = pthread_self();
        wine_ios_exit_initialized = 1;

        LOG("Calling __wine_main...");

        /* WoW64: publish the main image's machine so the unix side reserves
         * this process's guest window before its first TEB, and hand FEX's
         * WOW64 module the host features it cannot query itself. */
        ios_main_image_i386 = is_i386_target ? 1 : 0;
        madeira_publish_host_probe();   /* both FEX modules read it (A12/A13: FlagM, FlagM2) */

        if (setjmp(wine_ios_exit_jmpbuf) == 0) {
            __wine_main(argc, argv);
            dprintf(STDERR_FILENO, "[WineProc] __wine_main returned normally\n");
        } else {
            dprintf(STDERR_FILENO, "[WineProc] Wine exited with code %d (caught by longjmp)\n", wine_ios_exit_code);
        }
        __atomic_store_n(&g_wine_main_returned, 1, __ATOMIC_RELEASE);   /* madeira-bcd (round 577): the normal path; wine_launched_exit_stuck() stays 0 */

        /* wine_ios_exit_code is _Thread_local, so copy it somewhere the app
         * thread can read. RELEASE so a reader that sees g_wine_running == 0
         * is guaranteed to see this value too -- volatile alone does not order
         * these on arm64. */
        __atomic_store_n(&g_wine_exit_code, (int)wine_ios_exit_code,
                         __ATOMIC_RELEASE); /* madeira-exit-code */

        /* A launcher stub that starts the game and exits at once (GTA V
         * Enhanced: PlayGTAV.exe -> GTA5_Enhanced.exe) must not end the
         * session -- stopping the wineserver here killed the game while it
         * loaded. If a child process that is not a crash reporter / helper was
         * started in the last 60 s and still runs, the session goes on until
         * no such child is left (process_ios.c, madeira_live_game_children).
         * A game that exits normally long after starting its helpers is not
         * affected. MADEIRA_WAIT_CHILDREN=0 ends the session with the main
         * process, as before. */
        {
            extern int madeira_live_game_children(char *buf, int len, double max_age);
            const char *wc = getenv("MADEIRA_WAIT_CHILDREN");
            char names[256];
            int n = madeira_live_game_children(names, sizeof names, 60.0);
            if (n > 0 && !(wc && wc[0] == '0')) {
                dprintf(STDERR_FILENO, "[WineProc] madeira-bcd: the main process exited but %d child process(es) "
                        "it started still run (%s) -- a launcher started the game; the session goes on until "
                        "they exit (MADEIRA_WAIT_CHILDREN=0 ends it with the main process)\n", n, names);
                unsigned ticks = 0;
                while ((n = madeira_live_game_children(names, sizeof names, -1.0)) > 0) {
                    usleep(200 * 1000);
                    if ((++ticks % 300) == 0)
                        dprintf(STDERR_FILENO, "[WineProc] madeira-bcd: still running: %d child process(es) (%s), %u s\n",
                                n, names, ticks / 5);
                }
                dprintf(STDERR_FILENO, "[WineProc] madeira-bcd: the last child process exited after %u s\n", ticks / 5);
            }
        }

        /* madeira-bcd (round 577): the session's end work, shared with wine_session_abandon
         * (the "Session end" section above): g_wine_running = 0, the per-launch switches
         * unset, the wineserver stopped. Once per session. */
        wine_session_end();

        dprintf(STDERR_FILENO, "[WineProc] Wine process thread finished cleanly\n");

        // Steam S0: this thread's TEB was mirrored into pthread TSD slot
        // 275 (FEX's hardcoded 0x898) which we don't own via
        // pthread_key_create. Returning from a pthread runs foreign key
        // destructors on whatever's in the slot -> objc_release(TEB)
        // crash wedged the app after every net-test run. Clear it, same
        // as ntdll's pthread_exit_wrapper does for Wine worker threads.
        {
            uintptr_t tsd_base;
            __asm__ volatile("mrs %0, TPIDRRO_EL0" : "=r"(tsd_base));
            tsd_base &= ~7ULL;
            *(void **)(tsd_base + 275 * 8) = NULL;
        }
    }
    return NULL;
}

/* What the boot thread does after __wine_main: the session is over, stop the wineserver. Once per session,
 * whoever gets here first (the boot thread, or wine_session_abandon). */
static void wine_session_end(void)
{
    if (__atomic_exchange_n(&g_session_end_claim, 1, __ATOMIC_ACQ_REL)) return;
    bcd_hdr_reset();
    g_wine_running = 0;
    wine_progname_restore();
    /* ml1184: these belong to the launch that just ended; a later session in this app
     * run gets its own from its game, or bcd.cfg's. */
    unsetenv("MADEIRA_FASTSYNC"); unsetenv("MADEIRA_FASTSYNC_SEM");
    unsetenv("MADEIRA_CPU_COUNT"); unsetenv("DXMT_D9_ANISO_LIMIT");
    unsetenv("FEX_X87REDUCEDPRECISION");   /* ml1184 */
    unsetenv("MADEIRA_DINPUT_PAD");        /* ml1240 */
    unsetenv("MADEIRA_FEX_AVX"); unsetenv("MADEIRA_FRAMEGEN");   /* ml1184 */
    unsetenv("DXMT_AUTO_HDR"); unsetenv("DXMT_AUTO_HDR_PEAK"); unsetenv("DXMT_AUTO_HDR_PATTERN"); /* Auto HDR belongs to this session. */
    unsetenv("DXMT_ENABLE_NVEXT"); unsetenv("DXMT_WSI_MONITOR_IDENTITY"); unsetenv("DXMT_WSI_MODE_TABLE");
    /* madeira-bcd: Horizon Zero Dawn's per-game switches must not reach a later game
     * of this app run (GTA V Enhanced runs without them). */
    unsetenv("MADEIRA_UCRT_X64_MATH"); unsetenv("MADEIRA_SRV_FGETPATH");
    unsetenv("MADEIRA_NTDLL_SYNC");   /* madeira-bcd: a game's switch is its own */
    unsetenv("MADEIRA_ANON_LDADDAL32"); /* build 608: opt-in belongs only to the game that enabled it */
    /* madeira-bcd: Diablo II: Resurrected's executable-section switches (thebcd69/bcd-games#4), likewise */
    unsetenv("MADEIRA_NTDLL_XSEC"); unsetenv("MADEIRA_SHARED_SECTION_EXEC"); unsetenv("MADEIRA_NTDLL_DATA_SYNC_RANGE");
    unsetenv("MADEIRA_FAULT_LOOP_GUEST_PROGRESS"); unsetenv("MADEIRA_D2R_PROBE_RECOVERY");
    unsetenv("MADEIRA_FIBER_TRACE");  /* madeira-bcd: likewise */
    unsetenv("MADEIRA_WER");          /* madeira-bcd: likewise */
    unsetenv("MADEIRA_WINTYPES");     /* madeira-bcd: likewise */
    unsetenv("MADEIRA_X64_MACHINE");  /* madeira-bcd: likewise (BeamNG.drive's launcher) */
    unsetenv("MADEIRA_TINY_WINDOW_FIX"); /* madeira-bcd: likewise (American Truck Simulator's tiny window) */
    unsetenv("MADEIRA_OPTIN_DLLS");   /* madeira-bcd: likewise */
    unsetenv("MADEIRA_NLS_EXTRA");    /* madeira-bcd: likewise (env_ios.c reads it at every code page request) */
    unsetenv("MADEIRA_WG_64BIT");     /* madeira-bcd: likewise (Resident Evil 2's decoders; a later game keeps the stub table) */
    unsetenv("MADEIRA_VCRT_ARM64_KEY"); /* madeira-bcd: likewise (the Runtimes\\arm64 marker is per game) */
    unsetenv("MADEIRA_TAIL_CARVE_EXEC_CHECK"); /* madeira-bcd: likewise */
    unsetenv("MADEIRA_POOL_CLAMP_SECTIONS"); /* madeira-bcd: likewise */
    unsetenv("MADEIRA_LUM_PROBE");      /* madeira-bcd: likewise */
    unsetenv("MADEIRA_FEX_BAND_KEEP_MB"); /* madeira-bcd: likewise */
    g_madeira_srv_fgetpath = 0;
    /* madeira-bcd: the server's per-launch switches end with the session as well */
    unsetenv("MADEIRA_SRV_ECO_FOLLOW"); unsetenv("MADEIRA_SRV_DIRECT");
    unsetenv("MADEIRA_SRV_IDLE_US"); unsetenv("MADEIRA_SRV_LOG_ALL");
    g_madeira_srv_eco_follow = 0; g_madeira_srv_direct = 0;
    g_madeira_srv_idle_us = 0; g_madeira_srv_log_all = 0;

    // Stop wineserver to prevent CPU spin (iOS kills for excessive CPU)
    dprintf(STDERR_FILENO, "[WineProc] stopping wineserver...\n");
    wineserver_stop();
}

/* The library's call (a background queue) once SessionEndRules says the session is over although __wine_main
 * never came back. The thread that ran it is gone with the program. */
void wine_session_abandon(void)
{
    int32_t code = 0;
    double seconds = 0;
    if (!wine_launched_exit_stuck(&code, &seconds)) return;
    dprintf(STDERR_FILENO, "[WineProc] madeira-bcd: the program exited (status %d) on a thread other than the boot thread "
            "%.1f s ago and __wine_main never returned; ending the session here\n", (int)code, seconds);
    __atomic_store_n(&g_wine_exit_code, (int)code, __ATOMIC_RELEASE);   /* madeira-bcd: wine_process_exit_code() for a session ended off the boot thread */
    wine_session_end();
}

/* madeira-bcd (round 579, BCD Dock): a Dock session's initial program is Wine's desktop (explorer), which never ends by
 * itself on iOS, so a game that died (Dying Light 32FEAT, The Binding of Isaac VV5NHZ: c0000005, the host reported the game
 * over and finished) left the session on its dead desktop for good. The library's watch (LibraryModel.watchDockGameEnd,
 * DockGameEnd.swift) calls this once it has seen the game's last process end with an error status, nothing of the game
 * left, the grace time passed and the host finished. The status becomes the session's exit status
 * (wine_crash_exit_status: the library's exit report and its crash status), then the end work runs, as
 * wine_session_abandon does. Wine's own programs are idle and are not waited for. Returns 1 when it ended the session. */
int wine_session_dock_game_ended(int32_t status)
{
    uint64_t none = 0;
    if (!g_wine_running || __atomic_load_n(&g_session_end_claim, __ATOMIC_ACQUIRE)) return 0;
    __atomic_compare_exchange_n(&g_launch_exit, &none, (UINT64_C(1) << 32) | (uint32_t)status, 0, __ATOMIC_ACQ_REL, __ATOMIC_ACQUIRE);
    dprintf(STDERR_FILENO, "[WineProc] madeira-bcd: the Dock game's last process ended with status 0x%x and nothing of it is left; "
            "ending the session here\n", (unsigned)status);
    __atomic_store_n(&g_wine_exit_code, (int)status, __ATOMIC_RELEASE);   /* madeira-bcd: wine_process_exit_code() for a session ended off the boot thread */
    wine_session_end();
    return 1;
}

int wine_process_start(const char *prefix_path) {
    if (g_wine_running) {
        LOG("Wine process already running");
        return 0;
    }

    if (g_prefix_path) free(g_prefix_path);
    g_prefix_path = strdup(prefix_path);

    LOG("Starting Wine process with prefix: %{public}s", prefix_path);

    /* madeira-bcd (round 577, "Session end" above): this session's own bookkeeping, and the app's own program
     * name, taken once before any session has handed setprogname() a pointer of its own. */
    if (!g_app_progname) {
        const char *own = getprogname();
        if (own && *own) __atomic_store_n(&g_app_progname, strdup(own), __ATOMIC_RELEASE);
    }
    g_wine_main_returned = 0; g_launch_exit_claim = 0; g_launch_exit_seen = 0; g_launch_exit_boot = 0;
    g_launch_exit_code = 0; g_launch_exit_ns = 0; g_session_end_claim = 0;

    g_wine_running = 1;

    // Create socketpair to bypass broken iOS UDS accept()
    // pair[0] = wineserver side (injected as client fd)
    // pair[1] = ntdll side (used as fd_socket)
    int pair[2];
    if (socketpair(AF_UNIX, SOCK_STREAM, 0, pair) == -1) {
        LOG("socketpair failed: %{public}s", strerror(errno));
        g_wine_running = 0;
        return -1;
    }
    LOG("socketpair created: server_fd=%d, client_fd=%d", pair[0], pair[1]);

    // Set env var for ntdll to pick up instead of server_connect()
    // Must use WINESERVERSOCKET — that's what Wine's server_init_process() checks
    char fd_str[16];
    snprintf(fd_str, sizeof(fd_str), "%d", pair[1]);
    setenv("WINESERVERSOCKET", fd_str, 1);

    // Inject wineserver side — the event loop will pick this up
    wineserver_inject_client_fd(pair[0]);

    // madeira-bcd: no explicit sched priority on the guest main thread. A thread
    // created with pthread_attr_setschedparam has a fixed priority, and Darwin
    // then refuses pthread_set_qos_class_self_np (EPERM) -- so the promotion to
    // USER_INTERACTIVE in wine_process_thread, and the ECO switch, never took,
    // and the game's main thread ran at priority 20 on the efficiency cores for
    // the whole session (32-bit Crysis, [xp-t] 0024: 0 ms P / ~100 % E, logs
    // 2026-09-30 13:19-15:46). Ask for the QoS class through the attribute.
    pthread_attr_t attr;
    pthread_attr_init(&attr);
    pthread_attr_set_qos_class_np(&attr, QOS_CLASS_USER_INTERACTIVE, 0);

    int ret = pthread_create(&g_wine_thread, &attr, wine_process_thread, NULL);
    pthread_attr_destroy(&attr);
    if (ret != 0) {
        LOG("Failed to create Wine process thread: %d", ret);
        close(pair[0]);
        close(pair[1]);
        g_wine_running = 0;
        return -1;
    }

    pthread_detach(g_wine_thread);
    LOG("Wine process thread created");
    return 0;
}

int wine_process_is_running(void) {
    return g_wine_running;
}

int wine_process_exit_code(void) {
    return __atomic_load_n(&g_wine_exit_code, __ATOMIC_ACQUIRE);
}

int madeira_write_continue_flag(void) {
    if (!g_prefix_path) return -1;
    char path[1024];
    snprintf(path, sizeof(path), "%s/drive_c/madeira-continue.flag", g_prefix_path);
    int fd = open(path, O_WRONLY | O_CREAT | O_TRUNC, 0644);
    if (fd < 0) {
        LOG("continue flag write FAILED: %{public}s errno=%d", path, errno);
        return -1;
    }
    close(fd);
    LOG("continue flag written: %{public}s", path);
    return 0;
}


/* ---- ml1076: in-app memory-backing canary --------------------------------- */
#include <sys/mman.h>
#include <os/proc.h>
#include <errno.h>
#include <fcntl.h>
#include <mach/mach.h>
static void mc_sample(const char *mode, const char *phase, uint64_t *fp_out) {
    task_vm_info_data_t v; mach_msg_type_number_t n = TASK_VM_INFO_COUNT;
    memset(&v, 0, sizeof v);
    if (task_info(mach_task_self(), TASK_VM_INFO, (task_info_t)&v, &n) != KERN_SUCCESS) return;
    if (fp_out) *fp_out = v.phys_footprint;
    fprintf(stderr, "[mem-canary] ml1076 %s %s: footprint=%llu MB resident=%llu MB internal=%llu MB external=%llu MB compressed=%llu MB available=%lld MB\n",
            mode, phase, (unsigned long long)v.phys_footprint >> 20, (unsigned long long)v.resident_size >> 20,
            (unsigned long long)v.internal >> 20, (unsigned long long)v.external >> 20,
            (unsigned long long)v.compressed >> 20, (long long)os_proc_available_memory() >> 20);
}
static uint64_t mc_next(uint64_t *s) { *s ^= *s << 13; *s ^= *s >> 7; *s ^= *s << 17; return *s; }
static void mc_run(const char *mode, size_t bytes, int flags, const char *tmpdir, int punch) {
    char path[1024]; int fd = -1; uint64_t before = 0, after = 0, seed = 0x192834756abcdefULL;
    snprintf(path, sizeof path, "%s/madeira-memory-probe-XXXXXX", tmpdir);
    if (!(flags & MAP_ANON)) {
        fd = mkstemp(path);
        if (fd < 0) { fprintf(stderr, "[mem-canary] %s: mkstemp failed errno=%d\n", mode, errno); return; }
        /* the mapping must survive the device locking: no file protection */
        [[NSFileManager defaultManager] setAttributes:@{NSFileProtectionKey: NSFileProtectionNone} ofItemAtPath:[NSString stringWithUTF8String:path] error:nil];
        unlink(path);
        if (ftruncate(fd, (off_t)bytes)) { fprintf(stderr, "[mem-canary] %s: ftruncate failed errno=%d\n", mode, errno); close(fd); return; }
    }
    mc_sample(mode, "before", &before);
    uint64_t *p = mmap(NULL, bytes, PROT_READ | PROT_WRITE, flags, fd, 0);
    if (p == MAP_FAILED) { fprintf(stderr, "[mem-canary] %s: mmap failed errno=%d\n", mode, errno); if (fd >= 0) close(fd); return; }
    for (size_t i = 0; i < bytes / 8; i++) p[i] = mc_next(&seed);
    mc_sample(mode, "written", &after);
    fprintf(stderr, "[mem-canary] ml1076 %s: %zu MB written -> footprint +%lld MB\n", mode, bytes >> 20, (long long)(after - before) >> 20);
    if (fd >= 0) {
        if (msync(p, bytes, MS_SYNC)) fprintf(stderr, "[mem-canary] %s: msync errno=%d\n", mode, errno);
        mc_sample(mode, "synced", NULL);
        if (flags & MAP_SHARED) {
            /* ask the kernel to drop the pages; a re-read must come back from the file */
            if (madvise(p, bytes, MADV_DONTNEED)) fprintf(stderr, "[mem-canary] %s: madvise errno=%d\n", mode, errno);
            mc_sample(mode, "advised", NULL);
        }
    }
    seed = 0x192834756abcdefULL;
    { size_t bad = 0; for (size_t i = 0; i < bytes / 8; i++) if (p[i] != mc_next(&seed)) { bad++; if (bad == 1) fprintf(stderr, "[mem-canary] %s: DATA MISMATCH at byte %zu\n", mode, i * 8); }
      fprintf(stderr, "[mem-canary] ml1076 %s: verify %s (%zu bad words)\n", mode, bad ? "FAILED" : "ok", bad); }
    mc_sample(mode, "verified", NULL);
    if (punch && fd >= 0) {
        /* decommit semantics: punch a hole under the first half; it must read as zero and cost nothing */
        struct fpunchhole ph; memset(&ph, 0, sizeof ph); ph.fp_offset = 0; ph.fp_length = (off_t)(bytes / 2);
        int r = fcntl(fd, F_PUNCHHOLE, &ph);
        fprintf(stderr, "[mem-canary] ml1076 %s: F_PUNCHHOLE first half -> %d (errno %d); word0 now %llx, word at half %llx\n",
                mode, r, r ? errno : 0, (unsigned long long)p[0], (unsigned long long)p[bytes / 16]);
        mc_sample(mode, "punched", NULL);
    }
    munmap(p, bytes);
    if (fd >= 0) close(fd);
    mc_sample(mode, "released", NULL);
}
void madeira_memory_canary(const char *tmpdir) {
    fprintf(stderr, "[mem-canary] ml1076 start (page %ld, tmp %s)\n", sysconf(_SC_PAGESIZE), tmpdir);
    mc_run("anonymous", 64u << 20, MAP_PRIVATE | MAP_ANON, tmpdir, 0);
    mc_run("file-private-COW", 64u << 20, MAP_PRIVATE, tmpdir, 0);
    mc_run("file-shared-64MB", 64u << 20, MAP_SHARED, tmpdir, 1);
    mc_run("file-shared-512MB", 512u << 20, MAP_SHARED, tmpdir, 0);
    /* ml1079: CONTENTION. ph-rdr46 stalled with a thread blocked in a first-touch
     * page fault on a fresh 32 MB extent while ~886 MB of earlier extents in the
     * SAME file were dirty (presumably being written back). Does a fault on a
     * sparse region block behind writeback of the same vnode? And does a separate
     * file avoid it? Dirty 512 MB, kick writeback, then time 64 first touches on
     * a fresh region of the same file and of a second file, several times. */
    {
        char pa[1024], pb[1024]; int fa, fb; size_t big = 512u << 20, probe = 64u << 20; unsigned round;
        snprintf(pa, sizeof pa, "%s/madeira-memory-probe-A-XXXXXX", tmpdir); snprintf(pb, sizeof pb, "%s/madeira-memory-probe-B-XXXXXX", tmpdir);
        fa = mkstemp(pa); fb = mkstemp(pb);
        if (fa >= 0 && fb >= 0) {
            [[NSFileManager defaultManager] setAttributes:@{NSFileProtectionKey: NSFileProtectionNone} ofItemAtPath:[NSString stringWithUTF8String:pa] error:nil];
            [[NSFileManager defaultManager] setAttributes:@{NSFileProtectionKey: NSFileProtectionNone} ofItemAtPath:[NSString stringWithUTF8String:pb] error:nil];
            unlink(pa); unlink(pb);
            ftruncate(fa, (off_t)(big + 8 * probe)); ftruncate(fb, (off_t)(8 * probe));
            uint64_t *dirty = mmap(NULL, big, PROT_READ | PROT_WRITE, MAP_SHARED, fa, 0);
            if (dirty != MAP_FAILED) {
                uint64_t seed = 1;
                for (size_t i = 0; i < big / 8; i++) dirty[i] = mc_next(&seed);
                msync(dirty, big, MS_ASYNC);
                for (round = 0; round < 6; round++) {
                    struct timeval t0, t1, t2; unsigned k; volatile char sink = 0;
                    char *ra = mmap(NULL, probe, PROT_READ | PROT_WRITE, MAP_SHARED, fa, (off_t)(big + round * probe));
                    char *rb = mmap(NULL, probe, PROT_READ | PROT_WRITE, MAP_SHARED, fb, (off_t)(round * probe));
                    if (ra == MAP_FAILED || rb == MAP_FAILED) break;
                    gettimeofday(&t0, NULL);
                    for (k = 0; k < 64; k++) sink += ra[(probe / 64) * k];
                    gettimeofday(&t1, NULL);
                    for (k = 0; k < 64; k++) sink += rb[(probe / 64) * k];
                    gettimeofday(&t2, NULL);
                    fprintf(stderr, "[mem-canary] ml1079 round %u (%s): same-file first-touch %ld us/64 pages, other-file %ld us/64 pages\n", round,
                            round == 0 ? "right after dirtying 512 MB" : "later",
                            (long)((t1.tv_sec - t0.tv_sec) * 1000000 + (t1.tv_usec - t0.tv_usec)),
                            (long)((t2.tv_sec - t1.tv_sec) * 1000000 + (t2.tv_usec - t1.tv_usec)));
                    (void)sink;
                    munmap(ra, probe); munmap(rb, probe);
                    usleep(500000);
                }
                mc_sample("contention", "after", NULL);
                munmap(dirty, big);
            }
        }
        if (fa >= 0) close(fa); if (fb >= 0) close(fb);
    }
    /* ml1080: SUSTAINED DIRTYING THROUGHPUT. Hypothesis for the ph-rdr46 stall:
     * xnu throttles producers of dirty file-backed pages once the dirty backlog
     * passes a threshold, pacing them to the (wear-limited) writeback rate. Write
     * 1.5 GB through one shared mapping in 128 MB chunks and time each chunk; a
     * cliff after N chunks is the threshold, and the slow rate is the ceiling any
     * file-backed tier would impose on the game's loading writes. */
    {
        char pc[1024]; int fc; size_t total = 1536u << 20, chunk = 128u << 20;
        snprintf(pc, sizeof pc, "%s/madeira-memory-probe-C-XXXXXX", tmpdir);
        fc = mkstemp(pc);
        if (fc >= 0) {
            [[NSFileManager defaultManager] setAttributes:@{NSFileProtectionKey: NSFileProtectionNone} ofItemAtPath:[NSString stringWithUTF8String:pc] error:nil];
            unlink(pc); ftruncate(fc, (off_t)total);
            uint64_t *m = mmap(NULL, total, PROT_READ | PROT_WRITE, MAP_SHARED, fc, 0);
            if (m != MAP_FAILED) {
                size_t c; struct timeval t0, t1;
                for (c = 0; c < total / chunk; c++) {
                    uint64_t *q = m + (c * chunk) / 8; size_t i;
                    gettimeofday(&t0, NULL);
                    for (i = 0; i < chunk / 8; i += 2048) q[i] = (uint64_t)i ^ c;   /* one word per 16 KB page: dirty every page, minimal CPU */
                    gettimeofday(&t1, NULL);
                    long us = (t1.tv_sec - t0.tv_sec) * 1000000 + (t1.tv_usec - t0.tv_usec);
                    task_vm_info_data_t v; mach_msg_type_number_t n = TASK_VM_INFO_COUNT; memset(&v, 0, sizeof v);
                    task_info(mach_task_self(), TASK_VM_INFO, (task_info_t)&v, &n);
                    fprintf(stderr, "[mem-canary] ml1080 chunk %zu: dirtied 128 MB in %ld us (%ld MB/s); footprint=%llu MB external=%llu MB\n",
                            c, us, us > 0 ? (long)(128000000L / us) : -1L, (unsigned long long)v.phys_footprint >> 20, (unsigned long long)v.external >> 20);
                }
                {   /* and a re-read of the first chunk after the rest was written: still cheap? */
                    volatile uint64_t sink = 0; size_t i; gettimeofday(&t0, NULL);
                    for (i = 0; i < chunk / 8; i += 2048) sink += m[i];
                    gettimeofday(&t1, NULL);
                    fprintf(stderr, "[mem-canary] ml1080 re-read of chunk 0: %ld us\n", (long)((t1.tv_sec - t0.tv_sec) * 1000000 + (t1.tv_usec - t0.tv_usec)));
                    (void)sink;
                }
                munmap(m, total);
            }
            close(fc);
        }
        mc_sample("throughput", "after", NULL);
    }
    fprintf(stderr, "[mem-canary] ml1076 done\n");
}

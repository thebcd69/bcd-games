// SPDX-License-Identifier: MIT
// Copyright 2026 RageMC
// From bcd-games pull request #57 (RageMC, 2026-11-16, "Enshrouded on iPad: patched MoltenVK, kk-retry shim for KosmicKrisp").
// Modified by BCD (thebcd69), 2026-11-16: a marker string the build step recognises the shim by, and the slow-compile dump capped at
// eight files a run (a game with many slow compiles would otherwise fill the device's storage with its shader sources).
// Modified by BCD (thebcd69), 2026-11-20: a compile census (log only, nothing else changes): every 10 s with compile activity one
// "[kk-retry] compiles" line (finished per kind with average and longest time, failed, in flight with the oldest, waiting for a slot),
// and a compile still running after 60 s is named at once and its MSL saved under the same cap (X-Plane 12's loading screen froze on
// one: the old dump only wrote after a compile ended).
// Modified by BCD (thebcd69), 2026-11-22: a library request without MSL (its source nil or empty) is answered with an error here and
// never reaches Metal's compiler, and the first ones are logged with their caller. X-Plane 12 (build 695, iPhone18,2) froze on one:
// after about 700 compiles of 0.1 to 2 s, a library compile with 0 bytes of MSL was still running 96 s later and the game never drew
// again. On a Mac, Metal's own compiler asserts on a nil source ("source must not be nil"). A library without source holds no function,
// so the caller fails either way; every request with MSL goes on exactly as before.
// kk-retry: shim in front of BCD's KosmicKrisp (libvulkan_kosmickrisp_real.dylib, re-exported whole).
// Why: on iPad, under memory pressure iOS kills the Metal compiler service mid-game; Metal 4's
// MTL4Compiler then fails with XPC_ERROR_CONNECTION_INTERRUPTED ("Failed to create MTLLibrary"),
// KosmicKrisp returns the error to the game and Enshrouded aborts (BCD 0.1.665 logs, 2026-11-16).
// Fix: when a compile fails with that error, wait (the service is relaunched), make a fresh
// MTL4Compiler from the same device and compile again, with backoff. Other errors pass through.
// Also (same cause, the "Compiling shaders 0%" screen): at most KKR_MAX_PARALLEL (env, default 4) compiles
// run at once, so the compiler service needs less memory, and BCD's 512M Mesa shader cache cap is raised
// to 4G (env KKR_CACHE_SIZE overrides) so a game whose shaders do not fit stops recompiling every launch.
// Also: libraries compile with MTLLibraryOptimizationLevelSize (env KKR_OPT=default turns it off) because
// one Enshrouded library compile ran 12+ minutes with the default level; a compile over 10 s is logged and
// its MSL saved to Documents/kk-retry-slow/ for the developers.
// MIT (matches KosmicKrisp/Mesa). 2026-11-16.
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#import <objc/runtime.h>
#include <stdio.h>
#include <unistd.h>
#include <stdlib.h>
#include <string.h>
#include <dispatch/dispatch.h>
#include <os/lock.h>

static BOOL kkr_is_xpc(NSError *e) {
    if (!e) return NO;
    NSString *s = [e description];
    return [s containsString:@"XPC_ERROR_CONNECTION_INTERRUPTED"] || [s containsString:@"interrupted connection"]
        || [s containsString:@"XPC_ERROR_CONNECTION_INVALID"];
}
#ifdef KKR_TEST
int kkr_fake_fails = 0;   // test hook: fail this many calls with a fake XPC error
#endif
#ifndef KKR_WAITS
#define KKR_WAITS {250000, 500000, 1000000, 2000000, 4000000, 8000000}
#endif
static const useconds_t kkr_wait[] = KKR_WAITS;
enum { KKR_TRIES = sizeof kkr_wait / sizeof kkr_wait[0] };

typedef id (*imp2)(id, SEL, id, NSError **);
typedef id (*imp3)(id, SEL, id, id, NSError **);
static imp2 o_lib; static imp3 o_cps, o_rps;
static dispatch_semaphore_t kkr_sem;

/* BCD (2026-11-20): the compile census. A slot per compile in flight (the descriptor retained while it runs), counts since the last
 * report, under one lock; kkr_report() runs every 10 s and writes only when something happened. */
enum { KKR_LIB, KKR_RPS, KKR_CPS, KKR_KINDS, KKR_SLOTS = 64 };
static const char *const kkr_kind_name[KKR_KINDS] = { "library", "render pipeline", "compute pipeline" };
static os_unfair_lock kkr_lock = OS_UNFAIR_LOCK_INIT;
static struct kkr_slot { CFAbsoluteTime t0; CFTypeRef desc; unsigned long bytes; int kind, used, named; } kkr_slots[KKR_SLOTS];
static long kkr_done[KKR_KINDS], kkr_failed[KKR_KINDS], kkr_waiting, kkr_untracked, kkr_refused;
static double kkr_ms_sum[KKR_KINDS], kkr_ms_max[KKR_KINDS];
#ifdef KKR_TEST
double kkr_overdue_s = 60;     // test hook: when a running compile is named
useconds_t kkr_test_delay;     // test hook: each compile takes at least this long
#else
static const double kkr_overdue_s = 60;
#endif
static void kkr_dump(MTL4LibraryDescriptor *d, double secs, BOOL running);

static int kkr_begin(int kind, id desc) {
    unsigned long bytes = 0;
    if (kind == KKR_LIB && [desc isKindOfClass:[MTL4LibraryDescriptor class]]) bytes = ((MTL4LibraryDescriptor *)desc).source.length;
    int slot = -1;
    os_unfair_lock_lock(&kkr_lock);
    for (int i = 0; i < KKR_SLOTS; i++)
        if (!kkr_slots[i].used) { slot = i; break; }
    if (slot >= 0)
        kkr_slots[slot] = (struct kkr_slot){ CFAbsoluteTimeGetCurrent(), desc ? CFBridgingRetain(desc) : NULL, bytes, kind, 1, 0 };
    else
        kkr_untracked++;
    os_unfair_lock_unlock(&kkr_lock);
    return slot;
}

/* returns 1 when kkr_report already named this compile (and saved its source) while it ran */
static int kkr_end(int slot, int kind, BOOL ok) {
    CFTypeRef desc = NULL; int named = 0;
    os_unfair_lock_lock(&kkr_lock);
    if (slot >= 0 && kkr_slots[slot].used) {
        double ms = (CFAbsoluteTimeGetCurrent() - kkr_slots[slot].t0) * 1000.0;
        desc = kkr_slots[slot].desc; named = kkr_slots[slot].named;
        kkr_slots[slot] = (struct kkr_slot){ 0 };
        kkr_ms_sum[kind] += ms;
        if (ms > kkr_ms_max[kind]) kkr_ms_max[kind] = ms;
    }
    if (ok) kkr_done[kind]++; else kkr_failed[kind]++;
    os_unfair_lock_unlock(&kkr_lock);
    if (desc) CFRelease(desc);
    return named;
}

#ifdef KKR_TEST
#define KKR_TEST_DELAY if (kkr_test_delay) usleep(kkr_test_delay);
#else
#define KKR_TEST_DELAY
#endif
#define KKR_GATED(X, KIND, DESC, NAMED) ({                                                                     \
        if (kkr_sem) { __atomic_add_fetch(&kkr_waiting, 1, __ATOMIC_RELAXED);                                    \
                       dispatch_semaphore_wait(kkr_sem, DISPATCH_TIME_FOREVER);                                    \
                       __atomic_sub_fetch(&kkr_waiting, 1, __ATOMIC_RELAXED); }                                    \
        int _slot = kkr_begin(KIND, DESC); KKR_TEST_DELAY id _r = (X);                                            \
        if (kkr_end(_slot, KIND, _r != nil) && (NAMED)) *(NAMED) = 1;                                            \
        if (kkr_sem) dispatch_semaphore_signal(kkr_sem); _r; })

/* One line when compiles finished, failed or are running; a compile past kkr_overdue_s is named once, a library's MSL saved. Writes the
 * census line into out (empty when there was nothing to say) and to stderr. */
void kkr_report(char *out, size_t cap) {
    long done[KKR_KINDS], failed[KKR_KINDS], waiting, untracked; double sum[KKR_KINDS], mx[KKR_KINDS];
    int running = 0, oldest = -1; double oldest_s = 0; CFAbsoluteTime now = CFAbsoluteTimeGetCurrent();
    struct { CFTypeRef desc; double secs; unsigned long bytes; int kind; } late[4]; int nlate = 0;
    struct kkr_slot old = { 0 };
    os_unfair_lock_lock(&kkr_lock);
    for (int k = 0; k < KKR_KINDS; k++) {
        done[k] = kkr_done[k]; failed[k] = kkr_failed[k]; sum[k] = kkr_ms_sum[k]; mx[k] = kkr_ms_max[k];
        kkr_done[k] = kkr_failed[k] = 0; kkr_ms_sum[k] = kkr_ms_max[k] = 0;
    }
    untracked = kkr_untracked; kkr_untracked = 0;
    long refused = kkr_refused; kkr_refused = 0;
    for (int i = 0; i < KKR_SLOTS; i++) {
        struct kkr_slot *s = &kkr_slots[i];
        if (!s->used) continue;
        double secs = now - s->t0;
        running++;
        if (oldest < 0 || secs > oldest_s) { oldest = i; oldest_s = secs; old = *s; }
        if (!s->named && secs >= kkr_overdue_s && nlate < 4) {
            s->named = 1;
            late[nlate].desc = s->desc ? CFRetain(s->desc) : NULL; late[nlate].secs = secs; late[nlate].bytes = s->bytes;
            late[nlate].kind = s->kind; nlate++;
        }
    }
    os_unfair_lock_unlock(&kkr_lock);
    waiting = __atomic_load_n(&kkr_waiting, __ATOMIC_RELAXED);
    if (out && cap) out[0] = 0;
    long any = running + waiting + untracked + refused;
    for (int k = 0; k < KKR_KINDS; k++) any += done[k] + failed[k];
    if (any) {
        char line[600]; int n = snprintf(line, sizeof line, "[kk-retry] compiles in the last 10 s:");
        for (int k = 0; k < KKR_KINDS; k++)
            n += snprintf(line + n, sizeof line - n, " %s %ld (avg %.0f ms, max %.0f ms)%s", kkr_kind_name[k], done[k],
                          done[k] + failed[k] ? sum[k] / (done[k] + failed[k]) : 0.0, mx[k], k + 1 < KKR_KINDS ? "," : ";");
        n += snprintf(line + n, sizeof line - n, " failed %ld; in flight %d", failed[0] + failed[1] + failed[2], running);
        if (running)
            n += snprintf(line + n, sizeof line - n, " (oldest %.1f s, a %s compile%s)", oldest_s, kkr_kind_name[old.kind],
                          old.kind == KKR_LIB ? [[NSString stringWithFormat:@", %lu bytes of MSL", old.bytes] UTF8String] : "");
        n += snprintf(line + n, sizeof line - n, "; waiting for a slot %ld", waiting);
        if (untracked) n += snprintf(line + n, sizeof line - n, "; %ld not tracked (all %d slots busy)", untracked, KKR_SLOTS);
        if (refused) n += snprintf(line + n, sizeof line - n, "; %ld without MSL refused", refused);
        fprintf(stderr, "%s\n", line);
        if (out && cap) snprintf(out, cap, "%s", line);
    }
    for (int i = 0; i < nlate; i++) {
        fprintf(stderr, "[kk-retry] a %s compile is still running after %.1f s%s\n", kkr_kind_name[late[i].kind], late[i].secs,
                late[i].kind == KKR_LIB ? [[NSString stringWithFormat:@" (%lu bytes of MSL)", late[i].bytes] UTF8String] : "");
        if (late[i].desc) {
            id d = (__bridge id)late[i].desc;
            if (late[i].kind == KKR_LIB && [d isKindOfClass:[MTL4LibraryDescriptor class]]) kkr_dump(d, late[i].secs, YES);
            CFRelease(late[i].desc);
        }
    }
}

static id kkr_fresh(id compiler) {
    id<MTLDevice> dev = [compiler device];
    MTL4CompilerDescriptor *d = [MTL4CompilerDescriptor new];
    NSError *e = nil;
    id c = [dev newCompilerWithDescriptor:d error:&e];
    return c;
}

#define KKR_BODY(CALL, WHAT, KIND)                                                                      \
    NSError *err = nil; id r = nil;                                                               \
    for (int i = 0;; i++) {                                                                       \
        err = nil; r = KKR_GATED(CALL, KIND, desc, (int *)NULL);                                                                      \
        KKR_FAKE                                                                                  \
        if (r || !kkr_is_xpc(err) || i == KKR_TRIES) break;                                       \
        fprintf(stderr, "[kk-retry] %s: Metal compiler connection lost; retry %d/%d in %u ms\n",  \
                WHAT, i + 1, KKR_TRIES, kkr_wait[i] / 1000);                                      \
        usleep(kkr_wait[i]);                                                                      \
        id fresh = kkr_fresh(self); if (fresh) self = fresh;                                      \
    }                                                                                             \
    if (!r && error) *error = err;                                                                   \
    return r;

#define KKR_BODY_EXPR(CALL, WHAT, KIND, NAMED) \
    NSError *err = nil; id r = nil; \
    for (int i = 0;; i++) { err = nil; r = KKR_GATED(CALL, KIND, desc, NAMED); KKR_FAKE \
        if (r || !kkr_is_xpc(err) || i == KKR_TRIES) break; \
        fprintf(stderr, "[kk-retry] %s: Metal compiler connection lost; retry %d/%d in %u ms\n", WHAT, i + 1, KKR_TRIES, kkr_wait[i] / 1000); \
        usleep(kkr_wait[i]); id fresh = kkr_fresh(self); if (fresh) self = fresh; } \
    if (!r && error) *error = err; r;
#ifdef KKR_TEST
#define KKR_FAKE if (kkr_fake_fails > 0) { kkr_fake_fails--; r = nil; err = [NSError errorWithDomain:@"MTLLibraryErrorDomain" code:3 userInfo:@{NSLocalizedDescriptionKey: @"Compilation failed due to an interrupted connection: XPC_ERROR_CONNECTION_INTERRUPTED."}]; }
#else
#define KKR_FAKE
#endif

static int kkr_opt_size = 1;   /* env KKR_OPT=default turns it off */
/* BCD: build/mesa-ios/kk-retry/build.sh tells the shim from the real library by this text. */
__attribute__((used)) static const char kkr_marker[] = "kk-retry: shim in front of KosmicKrisp";
static int kkr_dumps;   /* BCD: at most eight slow-compile sources a run */
static void kkr_dump(MTL4LibraryDescriptor *d, double secs, BOOL running) {
    NSString *src = d.source; if (!src) return;
    if (__atomic_fetch_add(&kkr_dumps, 1, __ATOMIC_RELAXED) >= 8) {
        fprintf(stderr, "[kk-retry] slow library compile: %.1f s%s, %lu bytes of MSL (not saved: eight saved this run)\n", secs,
                running ? " so far" : "", (unsigned long)src.length);
        return;
    }
#ifdef KKR_TEST
    NSString *dir = [NSTemporaryDirectory() stringByAppendingPathComponent:@"kk-retry-slow"];   /* the Mac self-test keeps ~/Documents clean */
#else
    NSString *dir = [NSSearchPathForDirectoriesInDomains(NSDocumentDirectory, NSUserDomainMask, YES).firstObject
                     stringByAppendingPathComponent:@"kk-retry-slow"];
#endif
    [[NSFileManager defaultManager] createDirectoryAtPath:dir withIntermediateDirectories:YES attributes:nil error:nil];
    NSString *f = [dir stringByAppendingPathComponent:[NSString stringWithFormat:@"slow-%.0fs-%lx.metal", secs, (unsigned long)src.hash]];
    [src writeToFile:f atomically:YES encoding:NSUTF8StringEncoding error:nil];
    fprintf(stderr, "[kk-retry] slow library compile: %.1f s%s, %lu bytes of MSL, saved to Documents/kk-retry-slow/%s\n",
            secs, running ? " so far" : "", (unsigned long)src.length, f.lastPathComponent.UTF8String);
}
/* BCD (2026-11-22): the caller of a request worth naming, a few frames on one line (frames 0 to 2 are this shim). */
static NSString *kkr_caller(void) {
    NSArray<NSString *> *frames = [NSThread callStackSymbols];
    NSMutableArray<NSString *> *out = [NSMutableArray array];
    for (NSUInteger i = 3; i < frames.count && out.count < 10; i++) {
        NSString *f = [frames[i] stringByReplacingOccurrencesOfString:@"\\s+" withString:@" " options:NSRegularExpressionSearch range:NSMakeRange(0, frames[i].length)];
        [out addObject:f];
    }
    return [out componentsJoinedByString:@" | "];
}

/* BCD (2026-11-22): a library request without MSL. Counted for the census; the first four are named with their caller. */
static long kkr_refusals;
static void kkr_no_source(MTL4LibraryDescriptor *d) {
    os_unfair_lock_lock(&kkr_lock);
    kkr_refused++;
    os_unfair_lock_unlock(&kkr_lock);
    long n = __atomic_add_fetch(&kkr_refusals, 1, __ATOMIC_RELAXED);
    if (n > 4) return;
    fprintf(stderr, "[kk-retry] a library compile without MSL was refused (#%ld; source %s, name %s): Metal's compiler may never return "
            "from one; called from: %s\n", n, d.source ? "empty" : "nil", d.name.length ? d.name.UTF8String : "-", kkr_caller().UTF8String);
}

/* BCD (2026-11-22): a library request of another descriptor class goes on as before; the first of each of four classes is named. */
static void kkr_other_class(id desc) {
    static NSMutableSet<NSString *> *seen; static os_unfair_lock seen_lock = OS_UNFAIR_LOCK_INIT;
    NSString *name = NSStringFromClass([desc class]) ?: @"nil";
    os_unfair_lock_lock(&seen_lock);
    if (!seen) seen = [NSMutableSet set];
    BOOL first = seen.count < 4 && ![seen containsObject:name];
    if (first) [seen addObject:name];
    os_unfair_lock_unlock(&seen_lock);
    if (first)
        fprintf(stderr, "[kk-retry] a library compile with a %s descriptor (not MTL4LibraryDescriptor; its MSL is not counted) goes on; "
                "called from: %s\n", name.UTF8String, kkr_caller().UTF8String);
}

static id w_lib(id self, SEL s, id desc, NSError **error) {
    if ([desc isKindOfClass:[MTL4LibraryDescriptor class]] && ((MTL4LibraryDescriptor *)desc).source.length == 0) {
        kkr_no_source(desc);
        if (error) *error = [NSError errorWithDomain:MTLLibraryErrorDomain code:MTLLibraryErrorCompileFailure
                                            userInfo:@{NSLocalizedDescriptionKey: @"kk-retry: a library without MSL was not compiled"}];
        return nil;
    }
    if (![desc isKindOfClass:[MTL4LibraryDescriptor class]]) kkr_other_class(desc);
    if (kkr_opt_size && [desc isKindOfClass:[MTL4LibraryDescriptor class]]) {
        MTL4LibraryDescriptor *d = desc;
        MTLCompileOptions *o = d.options ? [d.options copy] : [MTLCompileOptions new];
        if (o.optimizationLevel != MTLLibraryOptimizationLevelSize) { o.optimizationLevel = MTLLibraryOptimizationLevelSize; d.options = o; }
    }
    CFAbsoluteTime t0 = CFAbsoluteTimeGetCurrent();
    int named = 0;
    id r = ({ KKR_BODY_EXPR(o_lib(self, s, desc, &err), "library", KKR_LIB, &named) });
    double dt = CFAbsoluteTimeGetCurrent() - t0;
    if (dt > 10 && !named && [desc isKindOfClass:[MTL4LibraryDescriptor class]]) kkr_dump(desc, dt, NO);
    return r;
}
static id w_cps(id self, SEL s, id desc, id opt, NSError **error) { KKR_BODY(o_cps(self, s, desc, opt, &err), "compute pipeline", KKR_CPS) }
static id w_rps(id self, SEL s, id desc, id opt, NSError **error) { KKR_BODY(o_rps(self, s, desc, opt, &err), "render pipeline", KKR_RPS) }

static void kkr_hook(Class c, SEL s, IMP w, IMP *orig) {
    Method m = class_getInstanceMethod(c, s);
    if (!m) { fprintf(stderr, "[kk-retry] %s missing on %s\n", sel_getName(s), class_getName(c)); return; }
    *orig = method_setImplementation(m, w);
}

__attribute__((constructor)) static void kkr_init(void) {
    @autoreleasepool {
        const char *cs = getenv("MESA_SHADER_CACHE_MAX_SIZE");
        if (!cs || !strcmp(cs, "512M")) {   /* BCD's default; a value the player set is kept */
            const char *want = getenv("KKR_CACHE_SIZE"); if (!want || !*want) want = "4G";
            setenv("MESA_SHADER_CACHE_MAX_SIZE", want, 1);
            fprintf(stderr, "[kk-retry] shader cache cap %s -> %s\n", cs ? cs : "(unset)", want);
        }
        const char *op = getenv("KKR_OPT"); if (op && !strcmp(op, "default")) kkr_opt_size = 0;
        fprintf(stderr, "[kk-retry] library compiles: %s\n", kkr_opt_size ? "size optimization (faster compiles)" : "Metal default");
        const char *mp = getenv("KKR_MAX_PARALLEL"); long n = mp ? strtol(mp, NULL, 10) : 4;
        if (n > 0) kkr_sem = dispatch_semaphore_create(n);
        fprintf(stderr, "[kk-retry] parallel Metal compiles: %s\n", n > 0 ? [[NSString stringWithFormat:@"at most %ld", n] UTF8String] : "no limit");
        id<MTLDevice> dev = MTLCreateSystemDefaultDevice();
        if (!dev || ![dev respondsToSelector:@selector(newCompilerWithDescriptor:error:)]) {
            fprintf(stderr, "[kk-retry] no Metal 4 compiler; not installed\n"); return;
        }
        id c = [dev newCompilerWithDescriptor:[MTL4CompilerDescriptor new] error:nil];
        if (!c) { fprintf(stderr, "[kk-retry] could not make a compiler; not installed\n"); return; }
        Class k = object_getClass(c);
        kkr_hook(k, @selector(newLibraryWithDescriptor:error:), (IMP)w_lib, (IMP *)&o_lib);
        kkr_hook(k, @selector(newComputePipelineStateWithDescriptor:compilerTaskOptions:error:), (IMP)w_cps, (IMP *)&o_cps);
        kkr_hook(k, @selector(newRenderPipelineStateWithDescriptor:compilerTaskOptions:error:), (IMP)w_rps, (IMP *)&o_rps);
        fprintf(stderr, "[kk-retry] installed on %s (retries on Metal compiler connection loss)\n", class_getName(k));
        /* BCD (2026-11-20): the compile census, every 10 s (a line only when compiles finished, failed or are running) */
        static dispatch_source_t kkr_timer;
        kkr_timer = dispatch_source_create(DISPATCH_SOURCE_TYPE_TIMER, 0, 0, dispatch_get_global_queue(QOS_CLASS_UTILITY, 0));
        if (kkr_timer) {
            dispatch_source_set_timer(kkr_timer, dispatch_time(DISPATCH_TIME_NOW, 10 * NSEC_PER_SEC), 10 * NSEC_PER_SEC, NSEC_PER_SEC);
            dispatch_source_set_event_handler(kkr_timer, ^{ @autoreleasepool { kkr_report(NULL, 0); } });
            dispatch_resume(kkr_timer);
            fprintf(stderr, "[kk-retry] compile census every 10 s; a compile running %.0f s or more is named\n", kkr_overdue_s);
        }
    }
}

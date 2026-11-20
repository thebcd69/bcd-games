// SPDX-License-Identifier: MIT
// Copyright 2026 RageMC
// From bcd-games pull request #57 (RageMC, 2026-11-16, "Enshrouded on iPad: patched MoltenVK, kk-retry shim for KosmicKrisp").
// Modified by BCD (thebcd69), 2026-11-16: a marker string the build step recognises the shim by, and the slow-compile dump capped at
// eight files a run (a game with many slow compiles would otherwise fill the device's storage with its shader sources).
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
#define KKR_GATED(X) ({ if (kkr_sem) dispatch_semaphore_wait(kkr_sem, DISPATCH_TIME_FOREVER); id _r = (X); \
                        if (kkr_sem) dispatch_semaphore_signal(kkr_sem); _r; })

static id kkr_fresh(id compiler) {
    id<MTLDevice> dev = [compiler device];
    MTL4CompilerDescriptor *d = [MTL4CompilerDescriptor new];
    NSError *e = nil;
    id c = [dev newCompilerWithDescriptor:d error:&e];
    return c;
}

#define KKR_BODY(CALL, WHAT)                                                                      \
    NSError *err = nil; id r = nil;                                                               \
    for (int i = 0;; i++) {                                                                       \
        err = nil; r = KKR_GATED(CALL);                                                                      \
        KKR_FAKE                                                                                  \
        if (r || !kkr_is_xpc(err) || i == KKR_TRIES) break;                                       \
        fprintf(stderr, "[kk-retry] %s: Metal compiler connection lost; retry %d/%d in %u ms\n",  \
                WHAT, i + 1, KKR_TRIES, kkr_wait[i] / 1000);                                      \
        usleep(kkr_wait[i]);                                                                      \
        id fresh = kkr_fresh(self); if (fresh) self = fresh;                                      \
    }                                                                                             \
    if (!r && error) *error = err;                                                                   \
    return r;

#define KKR_BODY_EXPR(CALL, WHAT) \
    NSError *err = nil; id r = nil; \
    for (int i = 0;; i++) { err = nil; r = KKR_GATED(CALL); KKR_FAKE \
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
static void kkr_dump(MTL4LibraryDescriptor *d, double secs) {
    NSString *src = d.source; if (!src) return;
    if (__atomic_fetch_add(&kkr_dumps, 1, __ATOMIC_RELAXED) >= 8) {
        fprintf(stderr, "[kk-retry] slow library compile: %.1f s, %lu bytes of MSL (not saved: eight saved this run)\n", secs, (unsigned long)src.length);
        return;
    }
    NSString *dir = [NSSearchPathForDirectoriesInDomains(NSDocumentDirectory, NSUserDomainMask, YES).firstObject
                     stringByAppendingPathComponent:@"kk-retry-slow"];
    [[NSFileManager defaultManager] createDirectoryAtPath:dir withIntermediateDirectories:YES attributes:nil error:nil];
    NSString *f = [dir stringByAppendingPathComponent:[NSString stringWithFormat:@"slow-%.0fs-%lx.metal", secs, (unsigned long)src.hash]];
    [src writeToFile:f atomically:YES encoding:NSUTF8StringEncoding error:nil];
    fprintf(stderr, "[kk-retry] slow library compile: %.1f s, %lu bytes of MSL, saved to Documents/kk-retry-slow/%s\n",
            secs, (unsigned long)src.length, f.lastPathComponent.UTF8String);
}
static id w_lib(id self, SEL s, id desc, NSError **error) {
    if (kkr_opt_size && [desc isKindOfClass:[MTL4LibraryDescriptor class]]) {
        MTL4LibraryDescriptor *d = desc;
        MTLCompileOptions *o = d.options ? [d.options copy] : [MTLCompileOptions new];
        if (o.optimizationLevel != MTLLibraryOptimizationLevelSize) { o.optimizationLevel = MTLLibraryOptimizationLevelSize; d.options = o; }
    }
    CFAbsoluteTime t0 = CFAbsoluteTimeGetCurrent();
    id r = ({ KKR_BODY_EXPR(o_lib(self, s, desc, &err), "library") });
    double dt = CFAbsoluteTimeGetCurrent() - t0;
    if (dt > 10 && [desc isKindOfClass:[MTL4LibraryDescriptor class]]) kkr_dump(desc, dt);
    return r;
}
static id w_cps(id self, SEL s, id desc, id opt, NSError **error) { KKR_BODY(o_cps(self, s, desc, opt, &err), "compute pipeline") }
static id w_rps(id self, SEL s, id desc, id opt, NSError **error) { KKR_BODY(o_rps(self, s, desc, opt, &err), "render pipeline") }

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
    }
}

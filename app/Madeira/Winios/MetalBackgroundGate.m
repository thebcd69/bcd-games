/* GPL-3.0-or-later WITH the Madeira Converter Exception, version 1. */
/* Copyright 2026 ft7t8drgwf-wq */
// MetalBackgroundGate.m -- no Metal command buffer is committed while BCD
// is not the active app (BackgroundRun.swift installs it, the first time a game is
// kept running in the background; bcd.cfg background-gpu-gate = 0 leaves it out).
// madeira-bcd: from Madeira #498. Not installed at every start; hold() added; see mbg_wrap.
//
// iOS refuses GPU work from an app in the background: the command buffer
// fails ("Insufficient Permission (to submit GPU work from background)") and
// MoltenVK then marks its VkDevice lost, so a Zink OpenGL game (Project
// Zomboid) never draws again. While Madeira kept running in the background
// only for its few seconds of grace, little could reach the GPU; now that it
// keeps running (BackgroundRun.swift), a game that loads in the background
// uploads textures, and Zink submits them without presenting -- past the
// present gates (madeira_gl_wait_active in WiniosGL.m, winemetal's
// background-pause).
//
// So every -[MTLCommandBuffer commit] made off the main thread waits here from
// WillResignActive until DidBecomeActive: that thread stands still, the
// others (CPU loading) carry on. The main thread never waits: it delivers the
// notification that ends the wait, and Madeira's own UI commits there. The
// concrete command buffer classes the device hands out are found once, at
// install, and their -commit is wrapped. madeira-bcd (round 668): so are a
// Metal 4 queue's -commit:count: and -commit:count:options: (KosmicKrisp).

#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#import <objc/runtime.h>
#import <objc/message.h>

#include <pthread.h>
#include <stdio.h>
#include <time.h>

#define MBG_CLASSES 4

static pthread_mutex_t mbg_lock = PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t mbg_cond = PTHREAD_COND_INITIALIZER;
static int mbg_inactive;
static unsigned mbg_logged;

static Class mbg_class[MBG_CLASSES];
static IMP mbg_orig[MBG_CLASSES];
static int mbg_count;

static void mbg_set(int inactive) {
    pthread_mutex_lock(&mbg_lock);
    mbg_inactive = inactive;
    pthread_cond_broadcast(&mbg_cond);
    pthread_mutex_unlock(&mbg_lock);
}

static void mbg_wait(void) {
    pthread_mutex_lock(&mbg_lock);
    if (mbg_inactive) {
        struct timespec t0, t1;
        int log = mbg_logged < 32;
        if (log) mbg_logged++;
        clock_gettime(CLOCK_MONOTONIC, &t0);
        if (log) fprintf(stderr, "[metal-bg] app inactive: a command buffer waits until BCD is active again\n");
        while (mbg_inactive) pthread_cond_wait(&mbg_cond, &mbg_lock);
        clock_gettime(CLOCK_MONOTONIC, &t1);
        if (log) fprintf(stderr, "[metal-bg] app active: committed after %.1f s\n",
                         (t1.tv_sec - t0.tv_sec) + (t1.tv_nsec - t0.tv_nsec) / 1e9);
    }
    pthread_mutex_unlock(&mbg_lock);
}

/* The wrapped -commit: the original of the nearest wrapped class. */
static void mbg_commit(id self, SEL cmd) {
    IMP orig = NULL;
    int count = __atomic_load_n(&mbg_count, __ATOMIC_ACQUIRE);
    for (Class c = object_getClass(self); c && !orig; c = class_getSuperclass(c))
        for (int i = 0; i < count; i++)
            if (mbg_class[i] == c) { orig = mbg_orig[i]; break; }
    if (![NSThread isMainThread]) mbg_wait();
    if (orig) ((void (*)(id, SEL))orig)(self, cmd);
}

static void mbg_wrap(Class cls) {
    if (!cls || mbg_count >= MBG_CLASSES) return;
    for (int i = 0; i < mbg_count; i++)
        if (mbg_class[i] == cls) return;
    Method m = class_getInstanceMethod(cls, @selector(commit));
    if (!m) return;
    IMP orig = method_getImplementation(m);
    if (orig == (IMP)mbg_commit) return;
    /* madeira-bcd: the table entry is published BEFORE the class can dispatch to mbg_commit. Upstream installs the gate
     * at app start, when no game commits anything; BCD makes it the first time a game is kept running, with the game's
     * threads committing, and a commit that found no original would be dropped. */
    mbg_class[mbg_count] = cls;
    mbg_orig[mbg_count] = orig;
    __atomic_store_n(&mbg_count, mbg_count + 1, __ATOMIC_RELEASE);
    /* An inherited -commit gets an override on this class only; its own is replaced. */
    if (!class_addMethod(cls, @selector(commit), (IMP)mbg_commit, method_getTypeEncoding(m)))
        method_setImplementation(m, (IMP)mbg_commit);
    fprintf(stderr, "[metal-bg] command buffers of %s wait while BCD is in the background\n",
            class_getName(cls));
}

/* madeira-bcd (round 668, agent G1 S16, merge N113; Enshrouded FNFHZP = WF49CA with vulkan = kosmickrisp, build 665): KosmicKrisp does not
 * commit Metal command buffers but Metal 4 ones, through -[MTL4CommandQueue commit:count:] and -commit:count:options:, which the wrap
 * above never saw. The first commit after the app went to the background failed ("[kk-queue] commit done error=1 ...
 * MTL4CommandQueueErrorDomain error 1", a timeout), KosmicKrisp marked the device lost and the game ended itself 1.9 s later. The same
 * rule for both: off the main thread a commit waits while BCD is not the active app. The queue's concrete class is found once, at
 * install, from a queue the device makes and drops (runtime calls, so a device without Metal 4 or a host test's fake device is fine). */
#define MBG4_SLOTS 4
static Class mbg4_class[MBG4_SLOTS];
static SEL mbg4_sel[MBG4_SLOTS];
static IMP mbg4_orig[MBG4_SLOTS];
static int mbg4_count;

static IMP mbg4_original(id self, SEL cmd) {
    int count = __atomic_load_n(&mbg4_count, __ATOMIC_ACQUIRE);
    for (Class c = object_getClass(self); c; c = class_getSuperclass(c))
        for (int i = 0; i < count; i++)
            if (mbg4_class[i] == c && sel_isEqual(mbg4_sel[i], cmd)) return mbg4_orig[i];
    return NULL;
}

static void mbg4_commit(id self, SEL cmd, const void *buffers, NSUInteger count) {
    IMP orig = mbg4_original(self, cmd);
    if (![NSThread isMainThread]) mbg_wait();
    if (orig) ((void (*)(id, SEL, const void *, NSUInteger))orig)(self, cmd, buffers, count);
}

static void mbg4_commit_options(id self, SEL cmd, const void *buffers, NSUInteger count, id options) {
    IMP orig = mbg4_original(self, cmd);
    if (![NSThread isMainThread]) mbg_wait();
    if (orig) ((void (*)(id, SEL, const void *, NSUInteger, id))orig)(self, cmd, buffers, count, options);
}

/* As mbg_wrap: the original is published before the class can dispatch to the wrapper. */
static void mbg4_wrap(Class cls, SEL sel, IMP wrapper) {
    if (!cls || mbg4_count >= MBG4_SLOTS) return;
    for (int i = 0; i < mbg4_count; i++)
        if (mbg4_class[i] == cls && sel_isEqual(mbg4_sel[i], sel)) return;
    Method m = class_getInstanceMethod(cls, sel);
    if (!m) return;
    IMP orig = method_getImplementation(m);
    if (orig == wrapper) return;
    mbg4_class[mbg4_count] = cls;
    mbg4_sel[mbg4_count] = sel;
    mbg4_orig[mbg4_count] = orig;
    __atomic_store_n(&mbg4_count, mbg4_count + 1, __ATOMIC_RELEASE);
    if (!class_addMethod(cls, sel, wrapper, method_getTypeEncoding(m)))
        method_setImplementation(m, wrapper);
    fprintf(stderr, "[metal-bg] Metal 4 queue of %s (%s) waits while BCD is in the background\n", class_getName(cls), sel_getName(sel));
}

typedef id (*mbg4_new_fn)(id, SEL) __attribute__((ns_returns_retained));

/* The Metal 4 queue class of `device`, wrapped; nothing on a device without Metal 4. */
static void mbg4_install(id device) {
    SEL make = sel_registerName("newMTL4CommandQueue");
    if (!device || ![device respondsToSelector:make]) return;
    id q4 = ((mbg4_new_fn)objc_msgSend)(device, make);
    if (!q4) return;
    Class cls = object_getClass(q4);
    mbg4_wrap(cls, sel_registerName("commit:count:"), (IMP)mbg4_commit);
    mbg4_wrap(cls, sel_registerName("commit:count:options:"), (IMP)mbg4_commit_options);
}

/* The number of Metal 4 queue methods wrapped (the host test reads it). */
int madeira_metal_gate_metal4_count(void) {
    return __atomic_load_n(&mbg4_count, __ATOMIC_ACQUIRE);
}

/* madeira-bcd: hold (1) or release (0) the gate by hand. BCD makes the gate when a game is first kept running, at
 * WillResignActive, so the notification that should have held it has already gone out. */
void madeira_metal_gate_hold(int inactive) {
    mbg_set(inactive ? 1 : 0);
}

/* Called once on the main thread. Returns the number of classes wrapped. (madeira-bcd: upstream says "before a game
 * starts"; BCD calls it the first time a game is kept running in the background, with the game's threads running: see
 * mbg_wrap.) */
int madeira_metal_gate_install(void) {
    static dispatch_once_t once;
    dispatch_once(&once, ^{
        @autoreleasepool {
            id<MTLDevice> device = MTLCreateSystemDefaultDevice();
            id<MTLCommandQueue> queue = [device newCommandQueue];
            if (!queue) return;
            /* the kinds DXMT, MoltenVK and Mesa ask for; none is committed */
            mbg_wrap(object_getClass([queue commandBuffer]));
            mbg_wrap(object_getClass([queue commandBufferWithUnretainedReferences]));
            MTLCommandBufferDescriptor *d = [MTLCommandBufferDescriptor new];
            d.errorOptions = MTLCommandBufferErrorOptionEncoderExecutionStatus;
            mbg_wrap(object_getClass([queue commandBufferWithDescriptor:d]));
            mbg4_install(device);   /* round 668: KosmicKrisp's Metal 4 queues */
            NSNotificationCenter *nc = [NSNotificationCenter defaultCenter];
            [nc addObserverForName:@"UIApplicationWillResignActiveNotification" object:nil queue:nil
                        usingBlock:^(NSNotification *n) { (void)n; mbg_set(1); }];
            [nc addObserverForName:@"UIApplicationDidBecomeActiveNotification" object:nil queue:nil
                        usingBlock:^(NSNotification *n) { (void)n; mbg_set(0); }];
        }
    });
    return mbg_count;
}

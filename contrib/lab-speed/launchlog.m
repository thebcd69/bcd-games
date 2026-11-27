// BCD launch logger (lab build): loaded first via LC_LOAD_DYLIB. Writes Documents/BCD-launch-log.txt (visible in Files > On My iPad > BCD).
// Captures stdout/stderr (NSLog), app lifecycle notifications, uncaught exceptions, fatal signals with backtraces, and a
// main-thread watchdog that dumps the main thread's stack whenever it is stuck for 2 s.
#import <Foundation/Foundation.h>
#import <UIKit/UIKit.h>
#include <execinfo.h>
#include <signal.h>
#include <pthread.h>
#include <unistd.h>
#include <fcntl.h>
#include <mach-o/dyld.h>
#include <sys/sysctl.h>
#include <mach/mach.h>
#include <dlfcn.h>

static int gfd = -1;
static pthread_t gmain;
static thread_t gmainport;
static volatile uint64_t gbeat;
static double t0;

static double now(void) { return [NSDate timeIntervalSinceReferenceDate]; }
static void L(const char *fmt, ...) {
    if (gfd < 0) return;
    char buf[2048]; int n = snprintf(buf, sizeof buf, "[%8.3f] ", now() - t0);
    va_list ap; va_start(ap, fmt); n += vsnprintf(buf + n, sizeof buf - n, fmt, ap); va_end(ap);
    if (n > (int)sizeof buf - 2) n = sizeof buf - 2;
    buf[n++] = '\n'; write(gfd, buf, n);
}
static void bt(const char *why) {
    void *f[128]; int n = backtrace(f, 128);
    L("---- backtrace (%s) ----", why);
    backtrace_symbols_fd(f, n, gfd);
}
static void onsig(int s, siginfo_t *si, void *uc) {
    (void)uc; L("FATAL SIGNAL %d (%s) addr=%p", s, strsignal(s), si ? si->si_addr : 0); bt("signal");
    signal(s, SIG_DFL); raise(s);
}
static void onusr2(int s) { (void)s; bt("main thread, watchdog"); }
static void sym(int i, uintptr_t pc) {
    Dl_info d; if (dladdr((void *)pc, &d) && d.dli_fname) {
        const char *f = strrchr(d.dli_fname, '/'); f = f ? f + 1 : d.dli_fname;
        L("  #%02d %s %s + %lu", i, f, d.dli_sname ? d.dli_sname : "?", (unsigned long)(pc - (uintptr_t)(d.dli_sname ? d.dli_saddr : d.dli_fbase)));
    } else L("  #%02d 0x%lx", i, (unsigned long)pc);
}
static void mainstack(void) {  // suspend the main thread and walk its frame-pointer chain from here
    if (thread_suspend(gmainport) != KERN_SUCCESS) { L("cannot suspend main thread"); return; }
    arm_thread_state64_t st; mach_msg_type_number_t c = ARM_THREAD_STATE64_COUNT;
    if (thread_get_state(gmainport, ARM_THREAD_STATE64, (thread_state_t)&st, &c) == KERN_SUCCESS) {
        L("---- main thread stack ----");
        sym(0, (uintptr_t)arm_thread_state64_get_pc(st));
        sym(1, (uintptr_t)arm_thread_state64_get_lr(st));
        uintptr_t fp = (uintptr_t)arm_thread_state64_get_fp(st);
        for (int i = 2; i < 64 && fp && !(fp & 7); i++) {
            uintptr_t *fr = (uintptr_t *)fp; uintptr_t next = fr[0], ret = fr[1] & 0x0000000fffffffffULL;
            if (!ret) break; sym(i, ret);
            if (next <= fp) break; fp = next;
        }
    }
    thread_resume(gmainport);
}
static void onexc(NSException *e) {
    L("UNCAUGHT EXCEPTION %s: %s", e.name.UTF8String, e.reason.UTF8String);
    for (NSString *x in e.callStackSymbols) L("  %s", x.UTF8String);
}
#include "speeddefaults.h"
static void *watchdog(void *a) {
    (void)a; uint64_t last = gbeat; int stuck = 0;
    for (;;) {
        sleep(1);
        // Stack samples suspend the main thread, so they are taken only in the first 2 minutes (launch problems);
        // later stalls are only noted, to keep the game's main thread undisturbed.
        if (gbeat == last) { if (++stuck == 2 || stuck % 10 == 0) { L("main thread not responding for %d s", stuck); if (now() - t0 < 120) mainstack(); } }
        else { if (stuck >= 2) L("main thread responsive again after %d s", stuck); stuck = 0; last = gbeat; }
    }
    return 0;
}
static void beat(void) {
    gbeat++;
    dispatch_after(dispatch_time(DISPATCH_TIME_NOW, 250 * NSEC_PER_MSEC), dispatch_get_main_queue(), ^{ beat(); });
}

__attribute__((constructor)) static void init(void) {
    t0 = now(); gmain = pthread_self(); gmainport = mach_thread_self();
    NSString *dir = NSSearchPathForDirectoriesInDomains(NSDocumentDirectory, NSUserDomainMask, YES).firstObject;
    NSString *path = [dir stringByAppendingPathComponent:@"BCD-launch-log.txt"];
    NSString *prev = [dir stringByAppendingPathComponent:@"BCD-launch-log-previous.txt"];
    [NSFileManager.defaultManager removeItemAtPath:prev error:nil];
    [NSFileManager.defaultManager moveItemAtPath:path toPath:prev error:nil];
    gfd = open(path.fileSystemRepresentation, O_WRONLY | O_CREAT | O_TRUNC | O_APPEND, 0644);
    NSDictionary *info = NSBundle.mainBundle.infoDictionary;
    char model[64] = {0}; size_t ms = sizeof model; sysctlbyname("hw.machine", model, &ms, 0, 0);
    L("BCD launch log: %s %s (build %s), device %s, iOS %s, pid %d", [info[@"CFBundleDisplayName"] UTF8String],
      [info[@"CFBundleShortVersionString"] UTF8String], [info[@"CFBundleVersion"] UTF8String], model,
      NSProcessInfo.processInfo.operatingSystemVersionString.UTF8String, getpid());
    L("bundle %s", NSBundle.mainBundle.bundlePath.UTF8String);
    L("loaded images at start: %u", _dyld_image_count());
    // bcd.cfg lives where BCD's C reader looks first before main(): $CFFIXED_USER_HOME/Documents (madeira_cfg.h)
    const char *home = getenv("CFFIXED_USER_HOME");
    NSString *cfgDir = (home && *home) ? [@(home) stringByAppendingPathComponent:@"Documents"] : dir;
    @try { speedDefaults(cfgDir); } @catch (NSException *e) { L("speed defaults: %s", e.reason.UTF8String); }
    // everything the app prints (NSLog, printf, Swift print) goes to the log too
    dup2(gfd, STDERR_FILENO); dup2(gfd, STDOUT_FILENO); setvbuf(stdout, NULL, _IOLBF, 0);
    struct sigaction sa = {0}; sa.sa_sigaction = onsig; sa.sa_flags = SA_SIGINFO;
    int sigs[] = {SIGSEGV, SIGBUS, SIGILL, SIGABRT, SIGFPE, SIGTRAP, SIGSYS};
    for (unsigned i = 0; i < sizeof sigs / sizeof *sigs; i++) sigaction(sigs[i], &sa, NULL);
    signal(SIGUSR2, onusr2);
    NSSetUncaughtExceptionHandler(onexc);
    for (NSString *n in @[UIApplicationDidFinishLaunchingNotification, UIApplicationDidBecomeActiveNotification,
                          UIApplicationWillResignActiveNotification, UIApplicationDidEnterBackgroundNotification,
                          UIApplicationWillEnterForegroundNotification, UIApplicationWillTerminateNotification,
                          UIApplicationDidReceiveMemoryWarningNotification, UISceneWillConnectNotification,
                          UISceneDidActivateNotification, UISceneDidDisconnectNotification, UIWindowDidBecomeKeyNotification,
                          UIWindowDidBecomeVisibleNotification])
        [NSNotificationCenter.defaultCenter addObserverForName:n object:nil queue:nil usingBlock:^(NSNotification *x) {
            L("event %s (%s)", n.UTF8String, NSStringFromClass([x.object class]).UTF8String);
            if ([n isEqual:UIApplicationDidBecomeActiveNotification]) {
                for (UIScene *sc in UIApplication.sharedApplication.connectedScenes) L("  scene %s state %ld", NSStringFromClass(sc.class).UTF8String, (long)sc.activationState);
                for (UIWindow *w in ((UIWindowScene *)UIApplication.sharedApplication.connectedScenes.anyObject).windows)
                    L("  window %s hidden=%d root=%s frame=%s", NSStringFromClass(w.class).UTF8String, w.hidden, NSStringFromClass(w.rootViewController.class).UTF8String, NSStringFromCGRect(w.frame).UTF8String);
            }
        }];
    dispatch_async(dispatch_get_main_queue(), ^{ L("main run loop started"); beat(); });
    pthread_t t; pthread_create(&t, NULL, watchdog, NULL); pthread_detach(t);
    L("logger ready");
}

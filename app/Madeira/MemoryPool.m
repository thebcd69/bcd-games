// ml1150: fills the swap tier's memory pool (virtual_ios.c) before Wine starts.
//
// The MadeiraMemoryHost extension creates owned memory regions without touching
// them, hands their memory entries over and exits. Memory owned by an exited
// task is billed to no process's footprint (runs 2-6 of MemoryHostTest.m), so
// the tier can map guest data from it at RAM speed. A dispatch memory-pressure
// source feeds the tier's guard, which stops taking pool memory at the system's
// critical level.

#import <Foundation/Foundation.h>
#import "MemoryHostProtocol.h"

int madeira_pool_add(unsigned int entry, unsigned long long size);
void madeira_pool_set_pressure(int level);

@protocol MPExtensionClass
+ (id)extensionWithIdentifier:(NSString *)identifier error:(NSError **)error;
@end
@protocol MPExtension
- (void)beginExtensionRequestWithInputItems:(NSArray *)items completion:(void (^)(NSUUID *identifier))completion;
- (void)cancelExtensionRequestWithIdentifier:(NSUUID *)identifier;
- (void)setRequestInterruptionBlock:(void (^)(NSUUID *identifier))block;
@end

@interface MPListener : NSObject <NSXPCListenerDelegate, MHMemoryClient>
@property (strong) NSXPCConnection *connection;
@property (strong) dispatch_semaphore_t connected;
@end
@implementation MPListener
- (BOOL)listener:(NSXPCListener *)listener shouldAcceptNewConnection:(NSXPCConnection *)connection {
    connection.remoteObjectInterface = MHMemoryServerInterface();
    connection.exportedInterface = [NSXPCInterface interfaceWithProtocol:@protocol(MHMemoryClient)];
    connection.exportedObject = self;
    [connection resume];
    self.connection = connection;
    dispatch_semaphore_signal(self.connected);
    return YES;
}
- (void)extensionReady:(int)pid available:(uint64_t)available {}
@end

// <dispatch/private.h> (WebKit asks for them too): BCD's own limit, at its warning mark and past it.
#define MP_MEMORYPRESSURE_PROC_LIMIT_WARN     0x10
#define MP_MEMORYPRESSURE_PROC_LIMIT_CRITICAL 0x20

// madeira-bcd (round 625, the owner's decision 38 b): env.MADEIRA_POOL_MORE = 1 (bcd.cfg or the game's
// config; WineProcessBridge.m sets this before MadeiraMemoryPoolStart) takes the changes below; without it
// the source is the one of build 624 and before, for every player who uses helper memory today.
int MadeiraMemoryPoolMore;

// madeira-bcd: a source that asks for the system's levels only is sent BCD's own
// limit warning as DISPATCH_MEMORYPRESSURE_CRITICAL (xnu memorystatus_warn_process),
// and no "normal" after it. Persona 3 Reload, build 622: "CRITICAL" at 5.3 GB, 80%
// of BCD's 6.7 GB, and the pool took nothing more until iOS closed BCD. Asking for
// the process-limit events too makes the kernel send those under their own bits;
// they are logged and never close the guard, which is there for the SYSTEM's RAM.
static void MPStartPressureSource(void) {
    static dispatch_source_t source;
    static BOOL split;
    const unsigned long system = DISPATCH_MEMORYPRESSURE_NORMAL | DISPATCH_MEMORYPRESSURE_WARN | DISPATCH_MEMORYPRESSURE_CRITICAL;
    dispatch_queue_t queue = dispatch_get_global_queue(QOS_CLASS_USER_INITIATED, 0);
    if (!MadeiraMemoryPoolMore) {   /* as before round 625: the system's levels, and any of them closes the guard */
        source = dispatch_source_create(DISPATCH_SOURCE_TYPE_MEMORYPRESSURE, 0, system, queue);
        dispatch_source_set_event_handler(source, ^{
            unsigned long level = dispatch_source_get_data(source);
            madeira_pool_set_pressure(level & DISPATCH_MEMORYPRESSURE_CRITICAL ? 2 : level & DISPATCH_MEMORYPRESSURE_WARN ? 1 : 0);
        });
        dispatch_resume(source);
        return;
    }
    source = dispatch_source_create(DISPATCH_SOURCE_TYPE_MEMORYPRESSURE, 0,
                                    system | MP_MEMORYPRESSURE_PROC_LIMIT_WARN | MP_MEMORYPRESSURE_PROC_LIMIT_CRITICAL, queue);
    split = source != nil;
    if (!source) source = dispatch_source_create(DISPATCH_SOURCE_TYPE_MEMORYPRESSURE, 0, system, queue);
    if (!source) { fprintf(stderr, "[swap] ml1150 pool: no memory-pressure source; the guard measures free memory only\n"); return; }
    fprintf(stderr, "[swap] ml1150 pool: memory-pressure source %s\n",
            split ? "with BCD's own limit events apart" : "for the system's levels only (a CRITICAL may be BCD's own limit: logged as a warning)");
    dispatch_source_set_event_handler(source, ^{
        unsigned long level = dispatch_source_get_data(source);
        if (level & (MP_MEMORYPRESSURE_PROC_LIMIT_WARN | MP_MEMORYPRESSURE_PROC_LIMIT_CRITICAL)) {
            fprintf(stderr, "[swap] ml1150 pool: BCD is at its own limit's %s mark; the pool goes on taking\n",
                    level & MP_MEMORYPRESSURE_PROC_LIMIT_CRITICAL ? "critical" : "warning");
            return;
        }
        int system_level = level & DISPATCH_MEMORYPRESSURE_CRITICAL ? 2 : level & DISPATCH_MEMORYPRESSURE_WARN ? 1 : 0;
        madeira_pool_set_pressure(!split && system_level == 2 ? 1 : system_level);
    });
    dispatch_resume(source);
}

/// Blocks for at most ~15 s. Returns the MB registered with the tier.
long MadeiraMemoryPoolStart(long megabytes) {
    const uint64_t region = 512ull << 20;
    const int64_t timeout = 10 * NSEC_PER_SEC;
    long regions = megabytes / 512, added = 0;
    if (regions <= 0) return 0;
    NSString *identifier = [NSBundle.mainBundle.bundleIdentifier stringByAppendingString:@".MemoryHost"];
    Class<MPExtensionClass> cls = (Class<MPExtensionClass>)NSClassFromString(@"NSExtension");
    id<MPExtension> extension = [cls extensionWithIdentifier:identifier error:nil];
    if (!extension) { fprintf(stderr, "[swap] ml1150 pool: no extension %s\n", identifier.UTF8String); return 0; }
    dispatch_semaphore_t exited = dispatch_semaphore_create(0);
    [extension setRequestInterruptionBlock:^(NSUUID *uuid) { dispatch_semaphore_signal(exited); }];

    static NSXPCListener *listener;
    static MPListener *delegate;
    delegate = [MPListener new];
    delegate.connected = dispatch_semaphore_create(0);
    listener = [NSXPCListener anonymousListener];
    listener.delegate = delegate;
    [listener resume];
    NSExtensionItem *item = [NSExtensionItem new];
    item.userInfo = @{ @"endpoint": listener.endpoint };
    dispatch_semaphore_t started = dispatch_semaphore_create(0);
    __block NSUUID *request = nil;
    [extension beginExtensionRequestWithInputItems:@[item] completion:^(NSUUID *uuid) { request = uuid; dispatch_semaphore_signal(started); }];
    if (dispatch_semaphore_wait(started, dispatch_time(DISPATCH_TIME_NOW, timeout)) || !request ||
        dispatch_semaphore_wait(delegate.connected, dispatch_time(DISPATCH_TIME_NOW, timeout))) {
        fprintf(stderr, "[swap] ml1150 pool: the extension did not start or connect; no pool\n");
        if (request) [extension cancelExtensionRequestWithIdentifier:request];
        return 0;
    }
    NSXPCConnection *connection = delegate.connection;
    for (long i = 0; i < regions; i++) {
        dispatch_semaphore_t reply = dispatch_semaphore_create(0);
        __block mach_port_t entry = MACH_PORT_NULL;
        id<MHMemoryServer> proxy = [connection remoteObjectProxyWithErrorHandler:^(NSError *e) { dispatch_semaphore_signal(reply); }];
        [proxy createRegion:region kind:MHRegionLedgerTagged touch:NO reply:^(xpc_object_t handle, int kr, uint64_t fp, uint64_t av) {
            if (kr == KERN_SUCCESS && handle) entry = xpc_dictionary_copy_mach_send(handle, "entry");
            dispatch_semaphore_signal(reply);
        }];
        if (dispatch_semaphore_wait(reply, dispatch_time(DISPATCH_TIME_NOW, timeout)) || entry == MACH_PORT_NULL) break;
        if (madeira_pool_add(entry, region) != 0) { mach_port_deallocate(mach_task_self(), entry); break; }
        added++;
    }
    // The extension exits; from then on its regions are billed to nobody.
    [(id<MHMemoryServer>)[connection remoteObjectProxyWithErrorHandler:^(NSError *e) {}] exitWithReply:^{}];
    BOOL gone = dispatch_semaphore_wait(exited, dispatch_time(DISPATCH_TIME_NOW, timeout)) == 0;
    [connection invalidate];
    fprintf(stderr, "[swap] ml1150 pool: %ld x 512 MB registered, extension %s\n", added, gone ? "exited" : "did NOT report exiting");
    if (added) MPStartPressureSource();
    return added * 512;
}

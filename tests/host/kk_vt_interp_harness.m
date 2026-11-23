// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
//
// tests/host/check-kk-vt-interp.py builds this with KosmicKrisp's wsi_common_metal_vti.m (taken out of
// build/mesa-ios/playport-patches/0036-*.patch) and runs it on this Mac: a real Metal device, a CAMetalLayer, a Metal 4
// queue for the event and residency, and VideoToolbox's own processor. It stands in for KosmicKrisp's WSI and a Vulkan
// game: every frame it takes a stand-in from wsi_metal_vti_acquire (or a drawable where the module gives none), draws a
// white bar on black a step further right into its texture and hands it to wsi_metal_vti_present, as
// wsi_common_metal.c does. With `ahead` = 2 it holds two images at once, as a game with a frame in flight does (the case
// that stalled X-Plane 12 at 3.6 FPS on build 703, when the worker took drawables beside the game). With `gpu-ms` the event
// the device's queue signals after a frame comes that late, as for a game whose GPU has several frames queued (X-Plane 12
// in flight on build 704: 130-155 ms, when a worker that sat waiting for each frame gave one frame in five its picture).
// It prints what it saw as key=value lines.
//
//   kk_vt_interp_harness <frames> <frame-ms> [off-at] [ahead] [gpu-ms]   off-at: the frame at which the menu's switch goes off
//
// With MADEIRA_LSFG_HOST_CTL and MADEIRA_LSFG_HOST_MSL the module makes Lossless Scaling's frames instead (BCD's
// wsi_common_metal_lsfg.m); KKH_LSFG_OFF_AT=<frame> rewrites that control file with enabled=0 there, as the app does when
// the in-game menu switches it off. KKH_SRGB=1: an sRGB swapchain (X-Plane 12's). KKH_STEP: how far the bar moves a frame
// (24 pixels; more than the bar is wide tells motion from a blend of two frames).
#import <Metal/Metal.h>
#import <QuartzCore/CAMetalLayer.h>
#include <stdio.h>
#include <stdlib.h>
#include <time.h>
#include <unistd.h>
#import <objc/message.h>

bool wsi_metal_vti_wanted(void);
void *wsi_metal_vti_acquire(void *mtl4_queue, const void *metal_layer, unsigned width, unsigned height, int *status);
bool wsi_metal_vti_is_stand_in(void *drawable);
void wsi_metal_vti_present(void *mtl4_queue, void *drawable, void (*handler)(void *, uint64_t), void *info, uint64_t present_id);
void wsi_metal_vti_unpresented(void *drawable);
int madeira_kk_vti_set(int on);
int madeira_kk_vti_status(int *width, int *height, double *game_fps, double *generated_fps, double *pair_ms);
int madeira_kk_vti_why(char *out, int cap);
void madeira_kk_vti_session_ended(void);

static int g_presented_waits;
// The app's ring of frames made, as the driver finds it in the process (dlsym): here it only counts.
static unsigned g_made_pushed;
__attribute__((visibility("default"), used)) void bcd_frame_made_push_n(uint32_t count);
void bcd_frame_made_push_n(uint32_t count) { __atomic_add_fetch(&g_made_pushed, count, __ATOMIC_RELAXED); }
static void on_presented(void *info, uint64_t id) { (void)info; (void)id; __atomic_add_fetch(&g_presented_waits, 1, __ATOMIC_RELAXED); }

// The device's Metal 4 queue of a game whose GPU is behind: the module's event is signalled gpu-ms after the present
// asked for it (in order), and the stand-ins' residency set goes to the real queue.
@interface LateQueue : NSObject
@property(nonatomic, strong) id real;
@property(nonatomic) int ms;
@property(nonatomic, strong) dispatch_queue_t gpu;
@end
@implementation LateQueue
- (void)signalEvent:(id<MTLSharedEvent>)event value:(uint64_t)value {
    dispatch_after(dispatch_time(DISPATCH_TIME_NOW, (int64_t)self.ms * NSEC_PER_MSEC), self.gpu, ^{ event.signaledValue = value; });
}
- (void)addResidencySet:(id)set { ((void (*)(id, SEL, id))objc_msgSend)(self.real, @selector(addResidencySet:), set); }
@end

static double now_ms(void) { struct timespec t; clock_gettime(CLOCK_MONOTONIC, &t); return t.tv_sec * 1e3 + t.tv_nsec / 1e6; }

static unsigned g_step = 24;   // KKH_STEP: pixels the bar moves a frame
static void draw_bar(id<MTLCommandQueue> q, id<MTLTexture> staging, id<MTLTexture> target, unsigned k) {
    unsigned w = (unsigned)staging.width, h = (unsigned)staging.height, bar = 64, x0 = 32 + (k * g_step) % (w - bar - 64);
    uint32_t *px = malloc((size_t)w * h * 4);
    for (unsigned y = 0; y < h; y++)
        for (unsigned x = 0; x < w; x++) px[(size_t)y * w + x] = (x >= x0 && x < x0 + bar) ? 0xffffffffu : 0xff000000u;
    [staging replaceRegion:MTLRegionMake2D(0, 0, w, h) mipmapLevel:0 withBytes:px bytesPerRow:w * 4];
    free(px);
    id<MTLCommandBuffer> cb = [q commandBuffer];
    id<MTLBlitCommandEncoder> b = [cb blitCommandEncoder];
    [b copyFromTexture:staging toTexture:target];
    [b endEncoding];
    [cb commit];
    [cb waitUntilCompleted];
}

int main(int argc, char **argv) {
    @autoreleasepool {
        int frames = argc > 1 ? atoi(argv[1]) : 120, frame_ms = argc > 2 ? atoi(argv[2]) : 33, off_at = argc > 3 ? atoi(argv[3]) : -1;
        int ahead = argc > 4 ? atoi(argv[4]) : 1, gpu_ms = argc > 5 ? atoi(argv[5]) : 0;
        int lsfg_off_at = getenv("KKH_LSFG_OFF_AT") ? atoi(getenv("KKH_LSFG_OFF_AT")) : -1;
        if (getenv("KKH_STEP") && atoi(getenv("KKH_STEP")) > 0) g_step = (unsigned)atoi(getenv("KKH_STEP"));
        id<MTLDevice> dev = MTLCreateSystemDefaultDevice();
        if (!dev) { printf("skip=no Metal device\n"); return 0; }
        printf("wanted=%d\n", wsi_metal_vti_wanted() ? 1 : 0);
        CAMetalLayer *layer = [CAMetalLayer layer];
        layer.device = dev;
        MTLPixelFormat fmt = getenv("KKH_SRGB") ? MTLPixelFormatBGRA8Unorm_sRGB : MTLPixelFormatBGRA8Unorm;   // KKH_SRGB: an sRGB swapchain, as X-Plane 12's
        layer.pixelFormat = fmt;
        layer.framebufferOnly = !wsi_metal_vti_wanted();   // as KosmicKrisp's WSI makes it
        layer.drawableSize = CGSizeMake(1280, 720);
        layer.maximumDrawableCount = 3;
        layer.allowsNextDrawableTimeout = YES;
        id<MTLCommandQueue> q = [dev newCommandQueue];
        id q4 = nil;
        if (@available(macOS 26.0, *)) q4 = [dev newMTL4CommandQueue];   // KosmicKrisp's queue kind: the module signals its event on it
        printf("mtl4=%d\n", q4 ? 1 : 0);
        if (q4 && gpu_ms > 0) {
            LateQueue *late = [LateQueue new];
            late.real = q4; late.ms = gpu_ms;
            late.gpu = dispatch_queue_create("harness.gpu", DISPATCH_QUEUE_SERIAL);
            q4 = late;
        }
        MTLTextureDescriptor *td = [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:fmt width:1280 height:720 mipmapped:NO];
        td.storageMode = MTLStorageModeShared;
        id<MTLTexture> staging = [dev newTextureWithDescriptor:td];
        int stand_ins = 0, drawables = 0, timeouts = 0, status = 0, last_status = -1, made_seen = 0, presents = 0;
        double gen_max = 0, game_fps = 0, slowest_acquire = 0, t_start = now_ms(), gen_last = 0, game_last = 0;
        void *held[4] = {0};
        int held_n = 0;
        for (int k = 0; k < frames; k++) {
            @autoreleasepool {
                if (k == off_at) printf("switch_off_returns=%d\n", madeira_kk_vti_set(0));
                if (k == lsfg_off_at && getenv("MADEIRA_LSFG_HOST_CTL")) {
                    FILE *c = fopen(getenv("MADEIRA_LSFG_HOST_CTL"), "w");
                    if (c) { fputs("enabled=0\n", c); fclose(c); }
                    printf("lsfg_off_at=%d\n", k);
                }
                double a0 = now_ms();
                int st = 0;
                void *img = wsi_metal_vti_acquire((__bridge void *)q4, (__bridge void *)layer, 1280, 720, &st);
                if (!img && st == -1) { timeouts++; continue; }
                if (!img) img = (__bridge_retained void *)[layer nextDrawable];
                double waited = now_ms() - a0;
                if (k > 5 && waited > slowest_acquire) slowest_acquire = waited;
                if (!img) { timeouts++; continue; }
                if (wsi_metal_vti_is_stand_in(img)) stand_ins++; else drawables++;
                draw_bar(q, staging, [(__bridge id)img texture], (unsigned)k);
                held[held_n++] = img;
                if (held_n < ahead && k + 1 < frames) { usleep(frame_ms * 1000); continue; }   // a frame in flight
                void *p = held[0];
                for (int i = 1; i < held_n; i++) held[i - 1] = held[i];
                held_n--;
                if (wsi_metal_vti_is_stand_in(p)) wsi_metal_vti_present((__bridge void *)q4, p, on_presented, NULL, (uint64_t)k + 1);
                else [(__bridge id<CAMetalDrawable>)p present];
                CFRelease(p);   // the WSI's own reference
                presents++;
                int w = 0, h = 0; double g = 0, gen = 0, pair = 0;
                status = madeira_kk_vti_status(&w, &h, &g, &gen, &pair);
                if (status == 3) made_seen = 1;
                if (gen > gen_max) gen_max = gen;
                if (g > game_fps) game_fps = g;
                gen_last = gen; game_last = g;
                if (status != last_status) { printf("status_at_%d=%d size=%dx%d\n", k, status, w, h); last_status = status; }
            }
            usleep(frame_ms * 1000);
        }
        for (int i = 0; i < held_n; i++) { wsi_metal_vti_unpresented(held[i]); CFRelease(held[i]); }
        double secs = (now_ms() - t_start) / 1000.0;
        usleep(600 * 1000);   // the worker presents what it still has
        char why[240] = "";
        madeira_kk_vti_why(why, sizeof why);
        printf("stand_ins=%d\ndrawables=%d\ntimeouts=%d\npresents=%d\nloop_fps=%.1f\nslowest_acquire_ms=%.1f\npresented_waits=%d\n"
               "final_status=%d\nmade_seen=%d\ngenerated_fps_max=%.1f\ngame_fps_max=%.1f\ngenerated_fps_last=%.1f\ngame_fps_last=%.1f\nwhy=%s\n"
               "made_pushed=%u\n",
               stand_ins, drawables, timeouts, presents, presents / secs, slowest_acquire, __atomic_load_n(&g_presented_waits, __ATOMIC_RELAXED),
               status, made_seen, gen_max, game_fps, gen_last, game_last, why, __atomic_load_n(&g_made_pushed, __ATOMIC_RELAXED));
        madeira_kk_vti_session_ended();
        printf("after_end_status=%d\n", madeira_kk_vti_status(NULL, NULL, NULL, NULL, NULL));
        usleep(300 * 1000);
    }
    return 0;
}

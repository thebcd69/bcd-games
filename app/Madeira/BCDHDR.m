// SPDX-License-Identifier: GPL-3.0-or-later
// Additional permission: Madeira Converter Exception, version 1; see LICENSE-EXCEPTION.md.
#import "BCDHDR.h"
#import <Metal/Metal.h>
#import <QuartzCore/CAMetalLayer.h>
#import <TargetConditionals.h>
#if TARGET_OS_IOS
#import <UIKit/UIKit.h>
#else
#import <AppKit/AppKit.h>
#endif
#import <objc/runtime.h>
#include <stdatomic.h>
#include <pthread.h>
#include <time.h>
#include <math.h>

NSNotificationName const BCDHDRChangedNotification = @"BCDHDRChanged";
static _Atomic uint32_t g_flags = BCD_HDR_ENABLED, g_revision = 1;
// One coherent, lock-free control reply per present.
static _Atomic uint64_t g_controls = (1ull << 32) | BCD_HDR_ENABLED;
static _Atomic int g_busy;
static pthread_mutex_t g_lock = PTHREAD_MUTEX_INITIALIZER;
static BCDHDRSnapshot g_reading;
static uint32_t g_requested, g_pattern_seconds, g_generation = 1;
static uint32_t g_preparations, g_allocations, g_refreshes;
static uint64_t g_owner, g_owner_area, g_seen, g_last_probe, g_last_log, g_pattern_deadline;
static char g_tag_key, g_display_key;

@interface BCDHDRTag : NSObject
@property(nonatomic) uint32_t revision, flags;
@property(nonatomic) float peak;
@end
@implementation BCDHDRTag
@end

@interface BCDHDRDisplay : NSObject {
@public
    float current, potential;
    uint32_t generation;
    uint64_t sampled;
    BOOL pending;
}
@end
@implementation BCDHDRDisplay
@end

@interface BCDHDRResources : NSObject {
@public
    id<MTLDevice> device;
    id<MTLComputePipelineState> pipeline;
    id<MTLBuffer> data;
    uint32_t state; // 1 preparing, 2 ready, 3 failed; published under g_lock
}
@end
@implementation BCDHDRResources
@end
static BCDHDRResources *g_resources;
static void hdr_prepare(id<MTLDevice> device);

static uint64_t hdr_now(void) {
    struct timespec t; clock_gettime(CLOCK_MONOTONIC, &t);
    return (uint64_t)t.tv_sec * 1000000000ull + t.tv_nsec;
}
static void hdr_notify(void) {
    dispatch_async(dispatch_get_main_queue(), ^{
        [[NSNotificationCenter defaultCenter] postNotificationName:BCDHDRChangedNotification object:nil];
    });
}
static void hdr_publish(void) { // g_lock held by the writer
    atomic_store_explicit(&g_controls, ((uint64_t)atomic_load(&g_revision) << 32) | atomic_load(&g_flags), memory_order_release);
}
void bcd_hdr_read(BCDHDRSnapshot *out) {
    if (!out) return;
    pthread_mutex_lock(&g_lock); *out = g_reading;
    out->flags = atomic_load(&g_flags); out->revision = atomic_load(&g_revision);
    out->requested = g_requested;
    out->age_ms = out->valid && out->sample_ns ? (uint32_t)fmin((hdr_now() - out->sample_ns) / 1000000ull, UINT32_MAX) : 0;
    out->probe_state = g_resources ? g_resources->state : 0;
    out->probe_preparations = g_preparations; out->probe_allocations = g_allocations; out->headroom_refreshes = g_refreshes;
    pthread_mutex_unlock(&g_lock);
}
void bcd_hdr_reset(void) {
    pthread_mutex_lock(&g_lock);
    atomic_fetch_add(&g_revision, 1); atomic_store(&g_flags, BCD_HDR_ENABLED); hdr_publish();
    g_generation++; g_requested = g_pattern_seconds = 0; g_pattern_deadline = 0;
    g_reading = (BCDHDRSnapshot){0}; g_owner = g_owner_area = g_seen = g_last_probe = g_last_log = 0;
    // Retain the prepared pipeline/buffer across sessions. An old in-flight GPU
    // sample owns its resources until completion and its revision is rejected.
    pthread_mutex_unlock(&g_lock); hdr_notify();
}
void bcd_hdr_begin(void) {
    const char *request = getenv("DXMT_AUTO_HDR"), *pattern = getenv("DXMT_AUTO_HDR_PATTERN");
    uint32_t seconds = pattern && (!strcmp(pattern, "10") || !strcmp(pattern, "30")) ? (uint32_t)atoi(pattern) : 0;
    pthread_mutex_lock(&g_lock);
    g_requested = request && !strcmp(request, "1");
    g_pattern_seconds = g_requested ? seconds : 0; g_pattern_deadline = 0;
    uint32_t flags = BCD_HDR_ENABLED | (atomic_load(&g_flags) & BCD_HDR_PROBE) | (g_pattern_seconds ? BCD_HDR_PATTERN : 0);
    atomic_store(&g_flags, flags); atomic_fetch_add(&g_revision, 1); hdr_publish();
    BOOL prepare = g_requested && (flags & BCD_HDR_PROBE);
    pthread_mutex_unlock(&g_lock);
    if (prepare) hdr_prepare(nil);
    hdr_notify();
}
static void hdr_set(uint32_t flag, int on) {
    pthread_mutex_lock(&g_lock);
    uint32_t old = atomic_load(&g_flags), next = on ? old | flag : old & ~flag;
    if (old == next) { pthread_mutex_unlock(&g_lock); return; }
    atomic_store(&g_flags, next); atomic_fetch_add(&g_revision, 1); hdr_publish();
    g_reading.valid = 0; g_reading.error = 0; g_last_probe = g_last_log = 0;
    BOOL prepare = g_requested && flag == BCD_HDR_PROBE && on;
    if (prepare && g_resources && g_resources->state == 3) g_resources = nil;
    pthread_mutex_unlock(&g_lock);
    if (prepare) hdr_prepare(nil);
    hdr_notify();
}
void bcd_hdr_set_enabled(int on) { hdr_set(BCD_HDR_ENABLED, on); }
void bcd_hdr_set_pattern(int on) { hdr_set(BCD_HDR_PATTERN, on); }
void bcd_hdr_set_probe(int on) { hdr_set(BCD_HDR_PROBE, on); }
uint32_t bcd_hdr_control_get(uint32_t *revision) {
    uint64_t control = atomic_load_explicit(&g_controls, memory_order_acquire);
    *revision = (uint32_t)(control >> 32);
    return 0x48445200u | (uint32_t)control;
}

void bcd_hdr_tag_drawable(uint64_t handle, uint32_t revision, uint32_t flags, float peak) {
    id<CAMetalDrawable> drawable = (__bridge id<CAMetalDrawable>)(void *)(uintptr_t)handle;
    if (!drawable || revision != atomic_load(&g_revision)) return;
    uint64_t layer = (uint64_t)(uintptr_t)(__bridge void *)drawable.layer;
    uint64_t area = (uint64_t)drawable.texture.width * drawable.texture.height, now = hdr_now();
    pthread_mutex_lock(&g_lock);
    if (revision != atomic_load(&g_revision)) { pthread_mutex_unlock(&g_lock); return; }
    if (g_owner && layer != g_owner && area < g_owner_area && now - g_seen < 2000000000ull) {
        pthread_mutex_unlock(&g_lock); return;
    }
    if ((flags & BCD_HDR_ELIGIBLE) && (flags & BCD_HDR_PATTERN) && g_pattern_seconds) {
        if (!g_pattern_deadline) g_pattern_deadline = now + (uint64_t)g_pattern_seconds * 1000000000ull;
        if (now >= g_pattern_deadline) {
            atomic_fetch_and(&g_flags, ~BCD_HDR_PATTERN); atomic_fetch_add(&g_revision, 1); hdr_publish();
            g_reading.valid = 0; g_pattern_seconds = 0;
            pthread_mutex_unlock(&g_lock); hdr_notify(); return;
        }
    }
    int notify = g_reading.prepared != !!(flags & BCD_HDR_ELIGIBLE) || layer != g_owner;
    if (notify) { g_reading.valid = 0; g_reading.error = 0; }
    g_owner = layer; g_owner_area = area; g_seen = now;
    g_reading.prepared = !!(flags & BCD_HDR_ELIGIBLE);
    pthread_mutex_unlock(&g_lock);
    if (notify) hdr_notify();
    if (atomic_load(&g_flags) & BCD_HDR_PROBE) {
        BCDHDRTag *tag = [BCDHDRTag new]; tag.revision = revision; tag.flags = flags; tag.peak = peak;
        objc_setAssociatedObject(drawable, &g_tag_key, tag, OBJC_ASSOCIATION_RETAIN_NONATOMIC);
    }
}

// The render path reads a bounded-age cache. UIKit and the layer ancestry are
// inspected asynchronously on main, at most 2 Hz per layer; never dispatch_sync.
// Detached layers, a delayed main queue and old sessions return SDR headroom.
void bcd_hdr_get_headroom(uint64_t handle, float *current, float *potential) {
    *current = *potential = 1;
    CAMetalLayer *layer = (__bridge CAMetalLayer *)(void *)(uintptr_t)handle;
    if (!layer) return;
    uint64_t now = hdr_now();
    pthread_mutex_lock(&g_lock);
    if (!g_requested) { pthread_mutex_unlock(&g_lock); return; }
    BCDHDRDisplay *cache = objc_getAssociatedObject(layer, &g_display_key);
    if (!cache) { cache = [BCDHDRDisplay new]; objc_setAssociatedObject(layer, &g_display_key, cache, OBJC_ASSOCIATION_RETAIN_NONATOMIC); }
    if (cache->generation != g_generation) {
        cache->generation = g_generation; cache->current = cache->potential = 1; cache->sampled = 0; cache->pending = NO;
    }
    if (cache->sampled && now - cache->sampled <= 1500000000ull) {
        *current = cache->current; *potential = cache->potential;
    }
    BOOL refresh = !cache->pending && (!cache->sampled || now - cache->sampled >= 500000000ull);
    uint32_t generation = g_generation;
    if (refresh) cache->pending = YES;
    pthread_mutex_unlock(&g_lock);
    if (!refresh) return;
    __weak CAMetalLayer *weakLayer = layer;
    dispatch_async(dispatch_get_main_queue(), ^{
        float c = 1, p = 1;
        for (CALayer *ancestor = weakLayer; ancestor; ancestor = ancestor.superlayer) {
#if TARGET_OS_IOS
            if ([ancestor.delegate isKindOfClass:UIView.class]) {
                UIScreen *screen = ((UIView *)ancestor.delegate).window.windowScene.screen;
                if (screen) { c = (float)screen.currentEDRHeadroom; p = (float)screen.potentialEDRHeadroom; break; }
            }
#else
            if ([ancestor.delegate isKindOfClass:NSView.class]) {
                NSScreen *screen = ((NSView *)ancestor.delegate).window.screen;
                if (screen) { c = (float)screen.maximumExtendedDynamicRangeColorComponentValue; p = (float)screen.maximumPotentialExtendedDynamicRangeColorComponentValue; break; }
            }
#endif
        }
        pthread_mutex_lock(&g_lock);
        if (generation == g_generation && cache->generation == generation && g_requested) {
            cache->current = isfinite(c) ? fmaxf(1,c) : 1; cache->potential = isfinite(p) ? fmaxf(1,p) : 1;
            cache->sampled = hdr_now(); cache->pending = NO; g_refreshes++;
        }
        pthread_mutex_unlock(&g_lock);
    });
}

static NSString *const hdr_msl = @"#include <metal_stdlib>\n"
"using namespace metal;\n"
"struct Stats { atomic_uint peak, above, finite, invalid; };\n"
"kernel void hdr_sample(texture2d<float, access::read> src [[texture(0)]],\n"
" device Stats &out [[buffer(0)]], uint2 gid [[thread_position_in_grid]]) {\n"
" uint2 p = min(uint2(src.get_width()-1, src.get_height()-1),\n"
"   uint2((float2(gid)+0.5f)*float2(src.get_width(),src.get_height())/float2(64,36)));\n"
" float3 c = src.read(p).rgb;\n"
" if (!all(isfinite(c))) { atomic_fetch_add_explicit(&out.invalid,1u,memory_order_relaxed); return; }\n"
" float peak = max(0.0f,max(c.r,max(c.g,c.b)));\n"
" atomic_fetch_max_explicit(&out.peak,as_type<uint>(peak),memory_order_relaxed);\n"
" atomic_fetch_add_explicit(&out.finite,1u,memory_order_relaxed);\n"
" if (peak>1.001f) atomic_fetch_add_explicit(&out.above,1u,memory_order_relaxed);\n"
"}\n";

// Preparation runs on a utility queue. Present only reads ready resources and
// skips sampling while they are being built; no GPU/CPU wait or shader compile.
static void hdr_prepare(id<MTLDevice> requestedDevice) {
    pthread_mutex_lock(&g_lock);
    if (g_resources && (!requestedDevice || g_resources->device == requestedDevice ||
                        (g_resources->state == 1 && !g_resources->device))) {
        pthread_mutex_unlock(&g_lock); return;
    }
    BCDHDRResources *resources = [BCDHDRResources new]; resources->state = 1; resources->device = requestedDevice;
    g_resources = resources; g_preparations++;
    pthread_mutex_unlock(&g_lock);
    dispatch_async(dispatch_get_global_queue(QOS_CLASS_UTILITY, 0), ^{
        id<MTLDevice> device = requestedDevice ?: MTLCreateSystemDefaultDevice();
        NSError *error = nil;
        MTLCompileOptions *options = [MTLCompileOptions new]; options.languageVersion = MTLLanguageVersion3_1;
        id<MTLLibrary> library = [device newLibraryWithSource:hdr_msl options:options error:&error];
        id<MTLComputePipelineState> pipeline = library ? [device newComputePipelineStateWithFunction:[library newFunctionWithName:@"hdr_sample"] error:&error] : nil;
        id<MTLBuffer> data = pipeline ? [device newBufferWithLength:16 options:MTLResourceStorageModeShared] : nil;
        pthread_mutex_lock(&g_lock);
        resources->device = device; resources->pipeline = pipeline; resources->data = data;
        resources->state = data ? 2 : 3;
        if (data) g_allocations++;
        BOOL active = g_resources == resources && g_requested && (atomic_load(&g_flags) & BCD_HDR_PROBE);
        if (active) g_reading.error = data ? 0 : 2;
        pthread_mutex_unlock(&g_lock);
        if (!data) fprintf(stderr, "[hdr-signal] probe preparation failed: %s\n", error ? error.localizedDescription.UTF8String : "no Metal device or buffer");
        if (active) hdr_notify();
    });
}

void bcd_hdr_probe_encode(uint64_t cb_handle, uint64_t drawable_handle) {
    if (!(atomic_load(&g_flags) & BCD_HDR_PROBE)) return;
    id<CAMetalDrawable> drawable = (__bridge id<CAMetalDrawable>)(void *)(uintptr_t)drawable_handle;
    id<MTLCommandBuffer> cb = (__bridge id<MTLCommandBuffer>)(void *)(uintptr_t)cb_handle;
    BCDHDRTag *tag = objc_getAssociatedObject(drawable, &g_tag_key);
    if (!tag || !(tag.flags & BCD_HDR_ELIGIBLE) || tag.revision != atomic_load(&g_revision)) return;
    CAMetalLayer *layer = drawable.layer;
    uint64_t owner = (uint64_t)(uintptr_t)(__bridge void *)layer, now = hdr_now();
    pthread_mutex_lock(&g_lock);
    BOOL due = owner == g_owner && (g_last_probe == 0 || now - g_last_probe >= 500000000ull);
    BCDHDRResources *resources = g_resources;
    BOOL ready = resources && resources->state == 2;
    pthread_mutex_unlock(&g_lock);
    if (!due) return;
    if (!ready || resources->device != drawable.texture.device) { hdr_prepare(drawable.texture.device); return; }
    int idle = 0;
    if (!atomic_compare_exchange_strong(&g_busy, &idle, 1)) return;
    pthread_mutex_lock(&g_lock); g_last_probe = now; pthread_mutex_unlock(&g_lock);
    id<MTLTexture> texture = drawable.texture;
    if (layer.framebufferOnly || texture.pixelFormat != MTLPixelFormatRGBA16Float) {
        pthread_mutex_lock(&g_lock); g_reading.valid = 0; g_reading.error = 1; pthread_mutex_unlock(&g_lock);
        atomic_store(&g_busy, 0); hdr_notify(); return;
    }
    // This buffer has a single GPU owner; reuse only after its completion handler.
    id<MTLBuffer> data = resources->data;
    memset(data.contents, 0, 16);
    float headroom, potential;
    bcd_hdr_get_headroom(owner, &headroom, &potential);
    CFStringRef name = layer.colorspace ? CGColorSpaceCopyName(layer.colorspace) : NULL;
    BOOL linear = name && CFEqual(name, kCGColorSpaceExtendedLinearSRGB);
    if (name) CFRelease(name);
    BCDHDRSnapshot snapshot = {0};
    snapshot.applied_flags = tag.flags; snapshot.applied_peak = tag.peak;
    snapshot.revision = tag.revision; snapshot.headroom = headroom;
    snapshot.edr = layer.wantsExtendedDynamicRangeContent; snapshot.linear = linear;
    snapshot.pixel_format = (uint32_t)texture.pixelFormat;
    snapshot.width = (uint32_t)texture.width; snapshot.height = (uint32_t)texture.height;
    id<MTLComputeCommandEncoder> encoder = [cb computeCommandEncoder];
    if (!encoder) { atomic_store(&g_busy, 0); return; }
    encoder.label = @"BCD HDR signal sample";
    [encoder setComputePipelineState:resources->pipeline]; [encoder setTexture:texture atIndex:0]; [encoder setBuffer:data offset:0 atIndex:0];
    [encoder dispatchThreads:MTLSizeMake(64,36,1) threadsPerThreadgroup:MTLSizeMake(8,8,1)];
    [encoder endEncoding];
    [cb addCompletedHandler:^(id<MTLCommandBuffer> finished) {
        BCDHDRSnapshot result = snapshot;
        if (finished.status == MTLCommandBufferStatusCompleted) {
            uint32_t *values = data.contents;
            memcpy(&result.peak, &values[0], sizeof(float));
            result.above_sdr = values[1]; result.samples = values[2]; result.invalid = values[3];
            result.valid = 1; result.sample_ns = hdr_now();
        } else result.error = 3;
        pthread_mutex_lock(&g_lock);
        BOOL current = tag.revision == atomic_load(&g_revision) && owner == g_owner;
        BOOL log = current && result.valid && (g_last_log == 0 || result.sample_ns - g_last_log >= 5000000000ull);
        if (current) { result.prepared = g_reading.prepared; g_reading = result; }
        if (log) g_last_log = result.sample_ns;
        pthread_mutex_unlock(&g_lock);
        if (log) fprintf(stderr, "[hdr-signal] sample_ns=%llu revision=%u view=%s pattern=%u edr=%u linear=%u format=%u headroom=%.3f sampled_peak=%.3f above=%u/%u invalid=%u size=%ux%u\n",
            (unsigned long long)result.sample_ns, tag.revision, tag.flags & BCD_HDR_ENABLED ? "HDR" : "SDR", !!(tag.flags & BCD_HDR_PATTERN),
            result.edr, result.linear, result.pixel_format, result.headroom, result.peak, result.above_sdr, result.samples, result.invalid, result.width, result.height);
        atomic_store(&g_busy, 0);
        if (current) hdr_notify();
    }];
}

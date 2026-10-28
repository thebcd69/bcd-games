// SPDX-License-Identifier: GPL-3.0-or-later
// Additional permission: Madeira Converter Exception, version 1; see LICENSE-EXCEPTION.md.
// Exercise the production sampler on real floating-point CAMetalDrawables.
#import "BCDHDR.h"
#import <Metal/Metal.h>
#import <QuartzCore/CAMetalLayer.h>
#import <assert.h>
#import <math.h>

static BCDHDRSnapshot sample(id<MTLCommandQueue> queue, CAMetalLayer *layer,
                             double value, BOOL stale) {
    bcd_hdr_set_probe(0); bcd_hdr_set_probe(1); // new revision, no rate-limit wait
    // Preparation is asynchronous; run main's deferred cache refreshes too.
    NSDate *deadline = [NSDate dateWithTimeIntervalSinceNow:10];
    BCDHDRSnapshot ready;
    do {
        bcd_hdr_read(&ready);
        if (ready.probe_state == 2) break;
        assert(ready.probe_state != 3);
        [[NSRunLoop currentRunLoop] runUntilDate:[NSDate dateWithTimeIntervalSinceNow:0.005]];
    } while ([deadline timeIntervalSinceNow] > 0);
    assert(ready.probe_state == 2);
    uint32_t revision;
    bcd_hdr_control_get(&revision);
    id<CAMetalDrawable> drawable = [layer nextDrawable]; assert(drawable);
    id<MTLCommandBuffer> cb = [queue commandBuffer];
    MTLRenderPassDescriptor *pass = [MTLRenderPassDescriptor renderPassDescriptor];
    pass.colorAttachments[0].texture = drawable.texture;
    pass.colorAttachments[0].loadAction = MTLLoadActionClear;
    pass.colorAttachments[0].storeAction = MTLStoreActionStore;
    pass.colorAttachments[0].clearColor = MTLClearColorMake(value, value / 2, 0, 1);
    [[cb renderCommandEncoderWithDescriptor:pass] endEncoding];
    bcd_hdr_tag_drawable((uint64_t)(__bridge void *)drawable, revision, 9, 2);
    bcd_hdr_probe_encode((uint64_t)(__bridge void *)cb, (uint64_t)(__bridge void *)drawable);
    if (stale) bcd_hdr_set_enabled(0);
    dispatch_semaphore_t done = dispatch_semaphore_create(0);
    [cb addCompletedHandler:^(id<MTLCommandBuffer> finished) { dispatch_semaphore_signal(done); }];
    [cb commit];
    assert(dispatch_semaphore_wait(done, dispatch_time(DISPATCH_TIME_NOW, 10*NSEC_PER_SEC)) == 0);
    assert(cb.status == MTLCommandBufferStatusCompleted);
    BCDHDRSnapshot result; bcd_hdr_read(&result); return result;
}
int main(void) { @autoreleasepool {
    id<MTLDevice> device = MTLCreateSystemDefaultDevice();
    if (!device) { puts("SKIP: native HDR probe needs a Metal GPU"); return 0; }
    id<MTLCommandQueue> queue = [device newCommandQueue];
    CAMetalLayer *layer = [CAMetalLayer layer]; layer.device = device;
    layer.pixelFormat = MTLPixelFormatRGBA16Float; layer.framebufferOnly = NO;
    layer.drawableSize = CGSizeMake(128, 72); layer.wantsExtendedDynamicRangeContent = YES;
    CGColorSpaceRef cs = CGColorSpaceCreateWithName(kCGColorSpaceExtendedLinearSRGB);
    layer.colorspace = cs; CGColorSpaceRelease(cs);
    bcd_hdr_reset(); setenv("DXMT_AUTO_HDR", "1", 1); bcd_hdr_begin();
    dispatch_semaphore_t cacheRead = dispatch_semaphore_create(0);
    dispatch_async(dispatch_get_global_queue(QOS_CLASS_USER_INITIATED, 0), ^{
        float current, potential;
        bcd_hdr_get_headroom((uint64_t)(__bridge void *)layer, &current, &potential);
        assert(current == 1 && potential == 1); // conservative while main is unavailable
        dispatch_semaphore_signal(cacheRead);
    });
    // This would time out if the render-side query dispatch_sync'd to main.
    assert(dispatch_semaphore_wait(cacheRead, dispatch_time(DISPATCH_TIME_NOW, NSEC_PER_SEC)) == 0);
    BCDHDRSnapshot hdr = sample(queue, layer, 1.5, NO);
    assert(hdr.requested && hdr.valid && hdr.prepared && hdr.samples == 2304 && hdr.above_sdr == 2304);
    assert(fabsf(hdr.peak - 1.5f) < 0.001 && hdr.invalid == 0 && hdr.error == 0);
    assert(hdr.edr && hdr.linear && hdr.pixel_format == MTLPixelFormatRGBA16Float);
    assert(hdr.headroom == 1); // detached layer must not claim a physical HDR display
    BCDHDRSnapshot sdr = sample(queue, layer, 1, NO);
    assert(sdr.valid && sdr.samples == 2304 && sdr.above_sdr == 0 && sdr.peak == 1);
    BCDHDRSnapshot stale = sample(queue, layer, 2, YES);
    assert(!stale.valid && !(stale.flags & BCD_HDR_ENABLED));
    BCDHDRSnapshot resources; bcd_hdr_read(&resources);
    assert(resources.probe_preparations == 1 && resources.probe_allocations == 1);
    assert(resources.headroom_refreshes >= 1);
    // Startup calibration expires without an in-game calibration switch.
    setenv("DXMT_AUTO_HDR_PATTERN", "10", 1); bcd_hdr_begin();
    uint32_t rev; uint32_t controls = bcd_hdr_control_get(&rev);
    assert(controls & BCD_HDR_PATTERN);
    id<CAMetalDrawable> reference = [layer nextDrawable]; assert(reference);
    bcd_hdr_tag_drawable((uint64_t)(__bridge void *)reference, rev, 11, 2);
    [NSThread sleepForTimeInterval:10.01];
    bcd_hdr_tag_drawable((uint64_t)(__bridge void *)reference, rev, 11, 2);
    assert(!(bcd_hdr_control_get(&rev) & BCD_HDR_PATTERN));
    unsetenv("DXMT_AUTO_HDR_PATTERN");
    bcd_hdr_reset(); BCDHDRSnapshot reset; bcd_hdr_read(&reset);
    assert(!reset.valid && !reset.prepared && reset.flags == BCD_HDR_ENABLED);
    puts("PASS: production GPU probe: actual HDR/SDR pixels, asynchronous headroom, one prepared/reused buffer, stale-frame rejection, timed calibration, session reset");
} }

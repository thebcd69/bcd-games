// SPDX-License-Identifier: MIT
// Copyright 2026 RageMC
// From bcd-games pull request #57 (RageMC, 2026-11-16). The shim's self-test on a Mac with a Metal 4 GPU: build.sh --test.
// Modified by BCD (thebcd69), 2026-11-20: the compile census cases (quiet when idle, a running compile named, the finished one counted).
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
extern int kkr_fake_fails;
extern double kkr_overdue_s; extern useconds_t kkr_test_delay;
void kkr_report(char *out, size_t cap);
int main(void) { @autoreleasepool {
    id<MTLDevice> dev = MTLCreateSystemDefaultDevice();
    id<MTL4Compiler> c = [dev newCompilerWithDescriptor:[MTL4CompilerDescriptor new] error:nil];
    MTL4LibraryDescriptor *ld = [MTL4LibraryDescriptor new];
    ld.source = @"kernel void k(device float *a [[buffer(0)]], uint i [[thread_position_in_grid]]) { a[i] *= 2; }";
    int ok = 1;
    kkr_fake_fails = 2; NSError *e = nil;
    id lib = [c newLibraryWithDescriptor:ld error:&e];
    printf("retry case: lib=%s err=%s\n", lib ? "ok" : "nil", e ? e.localizedDescription.UTF8String : "none"); ok &= lib != nil;
    kkr_fake_fails = 99; e = nil;
    lib = [c newLibraryWithDescriptor:ld error:&e];
    printf("give-up case: lib=%s err=%s\n", lib ? "ok" : "nil", e ? "xpc error passed back" : "none"); ok &= lib == nil && e != nil;
    kkr_fake_fails = 0; ld.source = @"this is not metal"; e = nil;
    lib = [c newLibraryWithDescriptor:ld error:&e];
    printf("real error case: lib=%s err=%s\n", lib ? "ok" : "nil", e ? "compile error passed back, no retry" : "none"); ok &= lib == nil && e != nil;
    ld.source = @"kernel void k(device float *a [[buffer(0)]], uint i [[thread_position_in_grid]]) { a[i] *= 2; }";
    __block int good = 0; dispatch_group_t g = dispatch_group_create();
    for (int i = 0; i < 16; i++) dispatch_group_async(g, dispatch_get_global_queue(0,0), ^{
        MTL4LibraryDescriptor *d2 = [MTL4LibraryDescriptor new];
        d2.source = [NSString stringWithFormat:@"kernel void k%d(device float *a [[buffer(0)]], uint i [[thread_position_in_grid]]) { a[i] += %d; }", i, i];
        if ([c newLibraryWithDescriptor:d2 error:nil]) __sync_fetch_and_add(&good, 1); });
    dispatch_group_wait(g, DISPATCH_TIME_FOREVER);
    printf("parallel case: %d/16 compiled through the gate\n", good); ok &= good == 16;
    printf("cache env: %s\n", getenv("MESA_SHADER_CACHE_MAX_SIZE")); ok &= !strcmp(getenv("MESA_SHADER_CACHE_MAX_SIZE"), "4G");
    MTL4LibraryDescriptor *d3 = [MTL4LibraryDescriptor new];
    d3.source = @"kernel void z(device float *a [[buffer(0)]], uint i [[thread_position_in_grid]]) { a[i] = 1; }";
    id l3 = [c newLibraryWithDescriptor:d3 error:nil];
    printf("size-opt case: lib=%s level=%ld\n", l3 ? "ok" : "nil", (long)d3.options.optimizationLevel);
    ok &= l3 != nil && d3.options.optimizationLevel == MTLLibraryOptimizationLevelSize;
    char line[700];
    kkr_report(line, sizeof line);   /* the cases above */
    printf("census after the cases above: %s\n", line); ok &= strstr(line, "[kk-retry] compiles in the last 10 s: library ") != NULL && strstr(line, "in flight 0") != NULL;
    kkr_report(line, sizeof line);
    printf("census when idle: \"%s\"\n", line); ok &= line[0] == 0;
    kkr_overdue_s = 0.3; kkr_test_delay = 1200000;
    MTL4LibraryDescriptor *d4 = [MTL4LibraryDescriptor new];
    d4.source = @"kernel void slow(device float *a [[buffer(0)]], uint i [[thread_position_in_grid]]) { a[i] = 3; }";
    dispatch_group_t g2 = dispatch_group_create(); __block id l4 = nil;
    dispatch_group_async(g2, dispatch_get_global_queue(0, 0), ^{ l4 = [c newLibraryWithDescriptor:d4 error:nil]; });
    usleep(600000);
    kkr_report(line, sizeof line);
    printf("census while one runs: %s\n", line);
    ok &= strstr(line, "in flight 1 (oldest ") != NULL && strstr(line, "a library compile, ") != NULL && strstr(line, [[NSString stringWithFormat:@"%lu bytes of MSL)", (unsigned long)d4.source.length] UTF8String]) != NULL;
    dispatch_group_wait(g2, DISPATCH_TIME_FOREVER); kkr_test_delay = 0;
    kkr_report(line, sizeof line);
    printf("census after it ended: %s\n", line);
    ok &= l4 != nil && strstr(line, "library 1 (avg ") != NULL && strstr(line, "in flight 0") != NULL;
    printf(ok ? "PASS\n" : "FAIL\n"); return !ok;
}}

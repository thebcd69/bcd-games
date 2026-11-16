#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
extern int kkr_fake_fails;
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
    printf(ok ? "PASS\n" : "FAIL\n"); return !ok;
}}

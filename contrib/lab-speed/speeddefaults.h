// Included by launchlog.m (needs L() and Foundation).
// BCD Lab speed defaults: a block at the TOP of Documents/bcd.cfg. bcd.cfg's last line wins and a game's own
// config wins over bcd.cfg, so anything the player or a game sets beats these. "bcd-lab-speed = 0" removes the block.
static const char *kSpeedBegin = "# >>> BCD Lab speed defaults";
static const char *kSpeedEnd = "# <<< BCD Lab speed defaults";
static const char *kSpeed[][2] = {
    {"env.MADEIRA_EC_HOOK_TRACE", "0"},    // logging only; BCD's own lists set 0 for 411 of 418 games
    {"env.MADEIRA_LEAN_DIAG", "1"},        // no thread-suspending samplers every 20 s, fewer address-space walks
    {"env.MADEIRA_FEX_EC_EXIT_POOL", "1"}, // x64->ARM64EC calls skip a table walk (42-52% of D3D11 render threads), same results
    {"env.MADEIRA_FEX_SPLIT_LOCK", "1"},   // split-lock atomics inline instead of a 25-30 us trap each, same results
    {"env.MADEIRA_DIRCACHE", "1"},         // cached directory listings for case-insensitive lookups (1 s trust window)
    {"env.MADEIRA_GL_PERF", "0"},          // OpenGL perf/stall log lines off
    {"quiet-log", "1"},                    // D3D12 log lines skip OutputDebugString (~0.5 ms each), no 1-2 s census freezes
    {"ind-probe", "0"},                    // no ExecuteIndirect readback probe that ends the render pass every 3 s
    {"perf-log", "0"},                     // no frame-split log every 5 s
    {"sched-hints", "1"},                  // ask iOS for the latency-critical, high-throughput tiers while a game runs
};
// Max-speed level (default; bcd-lab-speed = 1 keeps only the list above). Each of these BCD ships off because it can break
// some games; the user asked for every speedup regardless. A game's own file still wins over all of them.
static const char *kMax[][2] = {
    {"env.FEX_TSOENABLED", "0"},               // no x86 memory-ordering barriers on loads/stores: the biggest emulator cost
    {"env.FEX_X87REDUCEDPRECISION", "1"},      // 64-bit instead of 80-bit x87 math in 64-bit games too
    {"env.FEX_DISABLEL2CACHE", "0"},           // FEX's second-level JIT lookup cache on: fewer misses into the dispatcher
    {"env.FEX_DYNAMICL1CACHE", "0"},           // full-size first-level JIT cache from the start (no shrink/grow)
    {"env.MADEIRA_FEX_CALLRET_LEAN", "1"},     // 2 instructions fewer at every return point in 64-bit code (RDR2's tested setting)
    {"wx", "1"},                               // W^X: game JIT pages flip instead of trapping every write (ml695, experimental)
    {"env.MADEIRA_SRV_DIRECT", "2"},           // Wine server requests (files, handles, events, waits) run on the asking thread, no pipe round trip
    {"env.MADEIRA_FASTSYNC", "1"},             // fast wake path armed from the start instead of after heavy traffic
    {"env.MADEIRA_FASTSYNC_SEM", "1"},         // semaphores on the fast path too
    {"yield-mode", "switch"},                  // a yielding thread keeps its priority instead of dropping to the bottom for up to 10 ms
    {"env.MADEIRA_THREAD_STACK_SPILL", "1"},   // new threads skip ~300 refused address tries
    {"env.MADEIRA_POOL_CARVE_MERGE", "1"},     // JIT code buffers merge and split, so big requests are not refused
    {"env.MADEIRA_DIR_STORM", "0"},            // directory-timing log off
    {"pso-async", "1"},                        // D3D12: new pipelines compile on 2 background threads, no 35-50 ms frame hitches
    {"pso-archive", "1"},                      // D3D12: compiled pipelines kept on disk, second start skips compiles
    {"rp-carry", "1"},                         // D3D12: render pass stays open across command lists (0.3-0.6 GB/frame less memory traffic)
    {"rp-discard", "1"},                       // D3D12: discarded targets are not loaded/stored
    {"typed-view-unlocked", "1"},              // D3D12: view creation off the device lock
    {"env.MADEIRA_D3D9_DIRECT_MAP", "1"},      // 64-bit D3D9: dynamic buffers mapped in place (TF2 42.8 -> 83.7 fps)
    {"env.DXMT_D9_LATE_READBACK", "1"},        // D3D9: tiny per-frame readbacks no longer stall on the GPU
    // second pass over all 994 catalog options (2026-11-25); several are the player-tested RDR2 build-665 set
    {"async-submit", "1"},                     // D3D12: ExecuteCommandLists hands the batch to a worker and returns
    {"async-present-ahead", "2"},              // D3D12: the game may run 2 frames ahead of the present worker (1 by default)
    {"fence-early", "2"},                      // D3D12: fences advance as soon as the GPU reaches them; retire on a second thread
    {"fence-chain", "6"},                      // D3D12: fence chain 6 (RDR2 tested), needed by f6-rar
    {"f6-rar", "1"},                           // D3D12: read-to-read barriers no longer stall the GPU or end render passes
    {"heap-untracked", "1"},                   // D3D12: untracked Metal heaps, no automatic hazard waits
    {"indirect-fast", "1"},                    // D3D12: ExecuteIndirect records encoded 64 at a time
    {"msc-bounds-check", "0"},                 // shaders converted without robust-buffer bounds checks
    {"msc-strict-nan", "0"},                   // shaders without strict NaN handling
    {"msc-position-invariance", "0"},          // shaders without position invariance
    {"msc-fail-memo", "1"},                    // a shader the converter refused is not converted again
    {"pso-warm", "2"},                         // 2 background compiles of each lazy pipeline at creation
    {"ring-share", "1"},                       // argument-buffer chunks reused as soon as the GPU is done
    {"static-cbv", "1"},                       // single CBV ranges resolved at record time, not read by the GPU per draw
    {"d3d12-shader-pack", "1"},                // shader cache in 2 append-only files instead of one file per shader
    {"gpu-fault-info", "0"},                   // no per-encoder execution status on every command buffer
    {"env.MADEIRA_LUM_PROBE", "0"},            // no readback log lines
    {"env.MADEIRA_PIN_GRAPHICS_DLLS", "1"},    // D3D12/DXGI DLLs stay loaded (no unload/reload copies)
    {"env.DXMT_CMDLIST_KEEP_BLOCK", "1"},      // D3D11 deferred contexts keep their 2 MB block
    {"env.DXMT_DYN_KEEP", "1024"},             // dynamic buffers keep more retired names (fewer waits)
    {"env.DXMT_D9_VB_RANGE", "1"},             // D3D9: non-indexed draws copy only the vertex bytes they read
    {"env.DXMT_WAIT_ON_ADDRESS", "1"},         // DXMT waits on addresses instead of spinning (64-bit too)
    {"env.DXMT_RING_OVERSIZE_REUSE", "1"},     // oversize ring blocks reused (64-bit too)
    {"env.DXMT_CLEAR_DISCARD_STORE", "1"},     // full clears discard instead of store (64-bit too)
    {"env.MADEIRA_UCRT_X64_MATH", "1"},        // floor/sqrt stay in the emulator (HZD: 2-4 million crossings a second saved)
    {"env.MADEIRA_SRV_FGETPATH", "1"},         // Wine server gets an opened file's path from its descriptor (~0.5 ms each saved)
    {"env.MADEIRA_FIRST_PROC_OWN_EXIT_FLAG", "1"}, // after the launcher exits, its locks are no longer taken
    {"env.MADEIRA_PROBES", "0"},               // all always-on profilers off
    {"env.MADEIRA_ECO_NATIVE", "1"},           // JIT-pool warmer and healer on efficiency cores, P-cores left to the game
    {"env.MADEIRA_BG_QOS_PROCS", "steam.exe;steamwebhelper.exe;SocialClubHelper.exe;RockstarService.exe;upc.exe;UplayWebCore.exe;EADesktop.exe;EpicWebHelper.exe;EpicGamesLauncher.exe"},
    {"env.MADEIRA_BG_QOS_DELAY_S", "1"},       // launchers/web helpers demoted 1 s after the game presents (RDR2 tested)
    {"env.MADEIRA_X64_RWX_PLAIN", "1"},        // 64-bit .NET code heaps as plain memory, stores not trapped
    {"env.MADEIRA_WOW_RWX_PLAIN", "1"},        // same for 32-bit programs
    {"env.MADEIRA_WOW_UNITY_PLAIN", "1"},      // same for 32-bit Unity's Mono
    {"env.DOTNET_EnableWriteXorExecute", "0"}, // .NET 7+ games: no W^X double mapping
    {"env.MADEIRA_REPORT_HZ", "1"},            // games see 120 Hz on ProMotion, so they can run past 60
    {"env.WINEDEBUG", "-all"},
    {"env.MADEIRA_DXMT_FRESH", "1"},           // 64-bit D3D10/11 games use the source-built DXMT (newer fixes; this IPA ships it M4-tuned)                 // no Wine debug channels at all
    {"env.MVK_CONFIG_SYNCHRONOUS_QUEUE_SUBMITS", "0"}, // Vulkan: queue submits return at once
    {"env.MVK_CONFIG_FAST_MATH_ENABLED", "1"}, // Vulkan: Metal fast math for every shader
    {"env.MVK_CONFIG_USE_MTLHEAP", "1"},       // Vulkan: memory from Metal heaps (cheaper allocations)
    {"env.MVK_CONFIG_PERFORMANCE_TRACKING", "0"},
    {"env.MVK_CONFIG_LOG_LEVEL", "1"},         // Vulkan: errors only
};
static NSString *cfgKey(NSString *line) {
    NSString *t = [line stringByTrimmingCharactersInSet:NSCharacterSet.whitespaceCharacterSet];
    if (!t.length || [t hasPrefix:@"#"]) return nil;
    NSRange eq = [t rangeOfString:@"="];
    if (eq.location == NSNotFound) return nil;
    return [[t substringToIndex:eq.location] stringByTrimmingCharactersInSet:NSCharacterSet.whitespaceCharacterSet];
}
static void speedDefaults(NSString *dir) {
    NSString *path = [dir stringByAppendingPathComponent:@"bcd.cfg"];
    NSString *old = [NSString stringWithContentsOfFile:path encoding:NSUTF8StringEncoding error:nil] ?: @"";
    NSMutableArray *rest = [NSMutableArray array];
    NSMutableDictionary *set = [NSMutableDictionary dictionary];
    BOOL inBlock = NO;
    for (NSString *line in [old componentsSeparatedByCharactersInSet:NSCharacterSet.newlineCharacterSet]) {
        if ([line hasPrefix:@(kSpeedBegin)]) { inBlock = YES; continue; }
        if (inBlock) { if ([line hasPrefix:@(kSpeedEnd)]) inBlock = NO; continue; }
        [rest addObject:line];
        NSString *k = cfgKey(line);
        if (k) { NSRange eq = [line rangeOfString:@"="]; set[k] = [[line substringFromIndex:eq.location + 1] stringByTrimmingCharactersInSet:NSCharacterSet.whitespaceCharacterSet]; }
    }
    while (rest.count && [rest.lastObject length] == 0) [rest removeLastObject];
    NSMutableString *out = [NSMutableString string];
    BOOL off = [set[@"bcd-lab-speed"] isEqual:@"0"];
    if (!off) {
        [out appendFormat:@"%s (BCD Lab rewrites this block at each start; your own lines below win)\n", kSpeedBegin];
        [out appendString:@"# To turn the whole block off, add a line: bcd-lab-speed = 0 (1 = safe switches only)\n"];
        int n = 0;
        BOOL safeOnly = [set[@"bcd-lab-speed"] isEqual:@"1"];
        if (!safeOnly) [out appendString:@"# Max speed: also the risky switches. bcd-lab-speed = 1 keeps only the safe ones.\n"];
        for (unsigned i = 0; i < sizeof kSpeed / sizeof *kSpeed; i++) {
            if (set[@(kSpeed[i][0])]) continue;   // the player already chose a value
            [out appendFormat:@"%s = %s\n", kSpeed[i][0], kSpeed[i][1]]; n++;
        }
        for (unsigned i = 0; !safeOnly && i < sizeof kMax / sizeof *kMax; i++) {
            if (set[@(kMax[i][0])]) continue;
            [out appendFormat:@"%s = %s\n", kMax[i][0], kMax[i][1]]; n++;
        }
        [out appendFormat:@"%s\n", kSpeedEnd];
        L("speed defaults: %d lines written at the top of bcd.cfg (%s)", n, safeOnly ? "safe only" : "max speed");
    } else L("speed defaults: off (bcd-lab-speed = 0)");
    if (rest.count) { [out appendString:[rest componentsJoinedByString:@"\n"]]; [out appendString:@"\n"]; }
    if ([out isEqualToString:old]) return;
    if (!old.length && off) return;
    NSError *err = nil;
    if (![out writeToFile:path atomically:YES encoding:NSUTF8StringEncoding error:&err]) L("speed defaults: write failed: %s", err.description.UTF8String);
}

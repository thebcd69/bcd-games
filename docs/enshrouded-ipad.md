# Enshrouded on iPad

Enshrouded (Steam 1203620) was tested on BCD 0.1.665. The device was an iPad16,6 (M4, 16 GB) on iPadOS 27.2, using LiveContainer with StikDebug JIT, on 2026-11-16.

**Result:** with the changes below, the game creates its Vulkan device and the shader compile runs to completion. Before them it sat at "Compiling shaders 0%" and then crashed. In-game play is not yet confirmed.

## What went wrong

1. **The Metal compiler service was killed.** With KosmicKrisp and `mempool-mb = 6144`, the game's ~10.4 GB footprint plus the 6 GB of helper memory left iOS no room. iOS killed the Metal compiler service, and `MTL4Compiler` returned `XPC_ERROR_CONNECTION_INTERRUPTED`. KosmicKrisp then logged "Failed to create MTLLibrary".
2. **The game aborted.** After the failed compile, the game aborted on purpose (`*(int*)NULL = 42`, c0000005).
3. **dockhost.exe crashed during teardown.** It wrote to a freed steamclient object. This is a side effect of step 2, not a separate bug.
4. **One compile stalled the 0% screen.** With less helper memory the compiler survived, but the 0% screen was a single `newLibraryWithDescriptor:error:` call that ran for 12+ minutes on 4–8 MB of MSL that KosmicKrisp generated.
5. **Stock MoltenVK v1.4.2 failed.** On the MoltenVK route, Enshrouded reported `Graphics_DriverTooOld` / "incompatible graphics card".

## Changes in this PR

### MoltenVK patches (`build/moltenvk-ios/patches`)

These patches are what made the shader compile run to completion.

`build.sh` applies them by default. `BCD_MVK_PATCHES=0` builds plain v1.4.2.

`0001-moltenvk-enshrouded.patch` is v1.4.2 plus three sets of changes:

- **From [nivogli/MoltenVK](https://github.com/nivogli/MoltenVK), work aimed at Enshrouded:**
  - barrier handling
  - concurrent compute dispatch
  - adaptive indirect-count draw caps
  - tessellation scratch sizing
  - faster MSL math
- **From MoltenVK main:**
  - argument-buffer dirty tracking
  - residency-set bitsets
  - partial descriptor update sizes
  - 2D-array views of 3D images
  - instance-relative `gl_PrimitiveID` in tessellation
- **Local fixes:**
  - `vkGetDescriptorSetLayoutSupport` checks update-after-bind bindings against the `maxDescriptorSetUpdateAfterBind*` limits
  - indirect-count caps are kept per draw site
  - compressed and multisample image/buffer copy sizes
  - debug-report handles

The patch also moves SPIRV-Cross to `aa217aeb`. `spirv-cross-0001-instance-relative-patch-id.patch` adds the `instance_relative_patch_id` option that the tessellation fix needs. `build.sh` builds it from `toolchains/SPIRV-Cross-bcd` with `fetchDependencies --spirv-cross-root`.

Zink also loads `gl/libMoltenVK.dylib`, so OpenGL games get these changes too.

### kk-retry shim for KosmicKrisp (`build/mesa-ios/kk-retry`)

`build-kosmickrisp-bcd.sh` runs it last. `BCD_KK_RETRY=0` skips it.

The real library becomes `libvulkan_kosmickrisp_real.dylib`, and a small shim re-exports it, so `WineProcessBridge.m` is unchanged. The shim hooks the `MTL4Compiler` class:

- **Retries.** When a library, compute-pipeline or render-pipeline compile fails with an XPC connection error, it retries with a fresh compiler: 6 tries, with backoff from 0.25 s to 8 s. Other errors pass through at once.
- **Parallel limit.** At most 4 compiles run at once (`KKR_MAX_PARALLEL`).
- **Size optimisation.** Libraries compile with `MTLLibraryOptimizationLevelSize`. `KKR_OPT=default` turns this off.
- **Bigger shader cache.** The default `MESA_SHADER_CACHE_MAX_SIZE` goes from 512M to 4G (`KKR_CACHE_SIZE`).
- **Slow-compile log.** It logs compiles over 10 s and saves their MSL to `Documents/kk-retry-slow/`.

With the shim, the device had no more compiler-loss crashes. Compiles finished, but took 16–97 s each. MoltenVK is still the faster route for this game.

To run the shim's self-test on a Mac: `build/mesa-ios/kk-retry/build.sh --test`.

### Recommendation `enshrouded-steam` (list version 75)

| Setting | Value |
|---|---|
| `vulkan` | `moltenvk` |
| `mempool-mb` | `2048` |
| `env.MADEIRA_POOL_MARGIN_MB` | `3072` |
| `swap-mb` | `4096` |
| `swap-mode` | `2` |
| `vram-mb` | `4096` |
| `env.MADEIRA_EC_HOOK_TRACE` | `0` |

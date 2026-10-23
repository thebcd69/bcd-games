# iOS path resolution, exception guards and JIT pool placement

These changes correct shared iOS runtime paths that can affect games which
use GLOBALROOT-wrapped DOS paths, recover from guest instruction exceptions,
or need split executable-pool allocation. They apply when those conditions
occur. The implementation uses no game-name checks, executable hashes or
game-specific addresses. Minecraft Dungeons II is the device-tested example
documented below; compatibility with other titles remains to be tested.

## Runtime corrections and applicability

- **GLOBALROOT-wrapped DOS paths:** Wine's iOS DOS-path resolver recognises
  complete GLOBALROOT wrappers and resolves the inner DOS path. This supports
  programs that open files through a path such as
  `\??\GLOBALROOT\??\C:\...\data.dat`. Original string offsets, options and
  native-device fallback are retained. Ordinary drive resolution continues
  through the existing Wine code.
- **Guest progress at a shared instruction trap:** FEX can deliver different
  guest instruction exceptions through one shared host HLT thunk. The Mach
  exception repeat guard includes the saved guest RIP when a bounded,
  fault-safe probe identifies that thunk. Different guest instructions no
  longer count as one stationary fault. Unknown and native faults retain
  their existing identity. The 2,000-repeat limit remains in force.
- **Memory-only page-fault guard:** the page-fault loop guard applies to
  EXC_BAD_ACCESS. Instruction traps no longer count towards its memory-fault
  limits. Real memory-fault termination and the existing rate-based guard
  option remain intact.
- **Split-pool placement races:** with the existing `pool-split = 1`, a
  misplaced second JIT allocation is released and retried up to three times.
  Each attempt rechecks current free ranges. Bounds, the executable window,
  temporary reservations and release failures are checked. The existing
  single-region fallback is retained if safe placement cannot be obtained.

These correctness changes are scoped to iOS code paths. They do not add
global compatibility switches. Pool placement retries apply only when the
game already requests split-pool allocation. Per-game settings should follow
the game's observed failures; the example configuration below is specific
to the tested title.

## Optional instruction-recovery log sampling

`env.MADEIRA_RECOVERY_LOG_SAMPLE = 1` enables recovery-log sampling. The
default is off. Configuration is read before guest threads start; signal
handlers only read the cached value. The filter keeps recognised routine
instruction-recovery records at bounded intervals. Access violations, loader
errors, unknown faults and mixed or partial writes are retained. Sampling
changes logging only. It does not suppress exception delivery or relax
guards. The existing RTCS cap is independent.

The investigation-only SRW-history and socket-metadata probes are excluded.
No FEX source, submodule pin or compiled Windows runtime DLL is changed.
The GLOBALROOT correction is applied as an idempotent native Wine build
overlay.

## Validation

The host checks compile the actual C helpers with AddressSanitizer and
UndefinedBehaviorSanitizer. Fixtures reproduce the old false terminations,
verify guest progress and native/unreadable fallbacks, and preserve real
memory-fault limits. A Swift fixture runs the production placement prefix
through races, bounds, release failures and cleanup. Logging checks cover
normal errors, context collisions, mixed writes and the opt-in/default paths.

Run the focused checks from the repository root:

```sh
python3 tests/host/check-globalroot.py
python3 tests/host/check-guest-fault-progress.py
python3 tests/host/check-page-fault-guard.py
python3 tests/host/check-pool-placement-retry.py
python3 tests/host/check-recovery-log.py
python3 tests/host/check-fault-guard-rate.py
python3 tests/host/check-rtcs-cap.py
python3 tests/host/check-x64-image-nocopy.py
python3 tests/host/check-config-catalog.py
```

Initialise the pinned source submodules before running the path and catalog
checks. The native build applies the GLOBALROOT overlay; the path regression
also tests it directly against the pinned source. The changed native loader
and signal files and patched Wine resolver compiled for iOS ARM64 against
the pinned headers.

A configuration follow-up also ran `check-vcrt-arm64-key.py`. Its source
checks and the fixture with ASan/UBSan passed. The unsanitised fixture
aborted on the host with a stack-buffer-overflow report; that separate
failure remains unresolved. No VCRT runtime code is changed here.

## Tested example: Minecraft Dungeons II

On BCD 0.1.592, this game exposed the GLOBALROOT file-open failure, false
exception-loop decisions and executable-pool capacity problems. Investigation
builds containing the corrections reached the main menu and later entered
online gameplay. This is evidence for the shared failure paths above;
other titles have not received device validation as part of this work.

### Launch procedure

1. Use a BCD build containing these runtime corrections. The unmodified
   0.1.592 release does not include them.
2. Add the following settings to this game's own configuration.
3. Fully end the running BCD session and restart it before launching the
   game. The environment settings are read during session setup.

```ini
env.MADEIRA_X64_IMAGE_NOCOPY = 1
pool-split = 1
quiet-log = 1
env.MADEIRA_RECOVERY_LOG_SAMPLE = 1
env.MADEIRA_EC_HOOK_TRACE = 0
env.MADEIRA_LOCK_ORPHAN = 0
env.MADEIRA_VCRT_ARM64_KEY = 14.44.35211.0
env.MADEIRA_OPTIN_DLLS = twinapi.appcore
```

The block records the phone-tested compatibility settings. Keep copy
omission, split-pool allocation, orphan-lock mode `0`, the ARM64 VC runtime
marker and the `twinapi.appcore` opt-in when reproducing that launch. The
source fixes alone do not enable them. We did not test removing each setting
after launch succeeded, so their individual necessity on every device is
not established.

Current upstream also supports `env.MADEIRA_VCRT_ARM64_KEY = 1`. When it
creates the marker, it uses at least `14.44.35211.0` and takes the session's
VC++ runtime version when that is newer. An explicit version remains
supported, and existing registry keys are preserved. The automatic value
was checked against upstream source and the sanitised existing host test,
without a new phone test.

Copy omission avoids the redundant pure-x64 image copies that exhausted the
pool in earlier runs. Quiet logging avoids the observed ten-second debug
output waits. Recovery-log sampling only reduces log volume and is optional
for launch. MetalFX scaling is a display preference and is omitted here.

The historical Test 5 build enabled sampling unconditionally. The new
default-off option enables the same sampling logic and has host coverage,
including its disabled path. The cleaned PR build and this configuration
gate have not been tested on the phone; no cleaned IPA was built.

### Remaining issues in this example

Reliable online sessions are not established. Dedicated-session error 0015
persists with both proxied and direct gameplay UDP. A later null-PC access
violation during return to the menu also remains unresolved. The messaging
connection stayed proxied during the direct-UDP test, so the evidence does
not clear either the remaining network paths or the runtime as possible
causes. These issues are outside the fixes described above.

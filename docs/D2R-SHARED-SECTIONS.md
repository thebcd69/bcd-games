# Shared executable sections for x64 guests

Diablo II: Resurrected creates two views of a pagefile section, writes through one
and executes through the other. BCD 573's device log shows the executable view
copied into the JIT pool while zero-filled, and both emulator notifications
skipped because the mapping has no loader filename. FEX later reports NoExec and
D2R_loader.dll initialization terminates with c0000005. See issue #3.

The native memory change keeps eligible x64 section views on their original shared
backing. Wine retains logical execute permission, while the host treats the guest
instruction bytes as readable data. ARM64EC native code, system mappings, images,
ordinary file mappings and 32-bit guest windows keep their existing paths.

The Wine companion change notifies the emulator about actual committed-region
protections for non-image sections before the loader-filename gate. It uses the
existing syscall guard and protection callbacks, skips reserved holes, and serves
both section-map variants. Non-image sections never reach the PE image parser.
Both changes are required: byte sharing and emulator executable-range tracking
are independent requirements.

## Build

Rebuild both the native library and ARM64EC PE ntdll. Updating the Wine revision
without updating the bundled ntdll.dll leaves the notification fix out of the IPA.
The PE runtime can be built with build/wine-pe/build-ntdll.sh in a configured Wine
ARM64EC build tree. Its PE-only make target avoids linking the unrelated desktop
ntdll.so. The dependency build lists include Wine sfc, sfc_os and tbs, which the
D2R loader imports.

## Validation

Host tests: python3 tests/host/check-guest-section.py and the existing
check-x64-image-nocopy.py. The section test compiles extracted production helpers
with address/undefined-behavior sanitizers; it does not emulate FEX.

Compile tests/x64/shared-section-x64.c with x86_64-w64-mingw32-clang -O2 -Wall
-Wextra -Werror. Launch the resulting executable in a fresh BCD session. Expect
four PASS lines and shared-section: PASS, then exit code zero. The cases cover
initial shared write/execution, rewrite plus instruction-cache flush, protection
transitions, and unmap/remap. Run D2R in a separate fresh BCD session through Dock
and capture the per-game log. Also smoke-test a working 64-bit and 32-bit game.

The same narrow test passed on this iPhone using an earlier Madeira build. This
BCD port requires its own device validation. D2R progressed further in that
Madeira build but later hung; this change does not establish full compatibility.
The test explicitly flushes the executable address after rewriting its sibling;
automatic invalidation for arbitrary cross-alias writes is not established.

## Follow-up: ntdll data protection changes

The 20 October device test confirms this BCD build reaches the loader's later
unmap/remap stage. It then fails with a null `ResetToConsistentState` callback:
Wine passes an ARM host PC through as the x64 exception PC, followed by access
violations and another `NoExec`. D2R is still not playable in that build.

Immediately before the first exception, D2R protects the eight-byte
`pWow64PrepareForException` slot. In the tested ntdll, that slot is at RVA
`0xd24f0`, and the recovery callback is at `0xd2ec8`, on the same 4KB page.
The existing NtProtect IAT sync copies the entire rounded page from the original
PE data into the live pool data. ARM64EC initialization writes the callback in the
pool; the original PE copy retains zero. The page copy can therefore erase it.

For writable, non-executable ntdll `.data` requests, sync only the original
requested byte range. Preserve bytes outside it, including neighbouring runtime
callbacks. Translate only complete aligned pointer slots in the copied range,
using the existing process-owner lookup and x64 code-pointer exclusion. Other
images and code sections retain the existing path. The native sync logs
`[ntdll-data-sync]` when this path runs. This addresses the overwrite rather than
retrying a missing callback during exception handling.

Run `python3 tests/host/check-ntdll-data-sync.py` for a sanitizer-enabled model
that demonstrates the old overwrite and checks the production fix. It also
covers byte patches, pointer rebinding and rejected ranges. The existing
`check-iat-sync-owner.py`, section and x64 image checks should continue to pass.

After rebuilding ARM64EC ntdll, run
`python3 tools/build-ntdll-data-regression.py`. It derives the probe slot from the
unstripped compiler symbols and validates it against the stripped bundled image;
it refuses to build if the probe and callback no longer share a page. Launch
`ntdll-data-exception-x64.exe` from the bundled farm in a fresh BCD session.
Expect successful x64 exception address/register recovery and continuation
before and after eight-byte and one-byte data protection changes, followed by
the same checks in a child process and `ntdll-data-exception: PASS`. The program
does not write the hook slot or suppress unexpected exceptions. Run D2R through
Dock in a separate fresh session and capture its per-game log.

Host checks and compilation do not prove the device path or D2R gameplay. This
follow-up still requires device validation, including another working game.
No FEX source change or experimental exception-callback retry is included.

## Follow-up: translated breakpoint progress

The subsequent D2R session confirms the data-sync fix preserves the recovery
callback and guest exception addresses/register state are reconstructed. Startup
then stops at `[fault-stuck] BREAKING LOOP`: the native detector aborts its main
thread after repeated host traps at one FEX dispatcher stub. The logged guest
RIPs keep changing, so the fixed host PC does not establish a stuck guest.

The detector now recognises progress only for Mach breakpoint traps with a
nonzero guest RIP and an x28 frame matching the thread's registered FEX frame.
Changed guest RIP resets the repeat counter without suppressing exception
delivery. Identical guest traps, unknown/native frames, NULL accesses and the
existing advancing-data-address storm ceiling retain their abort thresholds.
The key includes the registered TEB and exception type to avoid accumulating
unrelated faults together. No emulator source is changed.

Run `python3 tests/host/check-fault-loop-progress.py` for the sanitized counter
checks. The guest exception regression now also handles 96 distinct intentional
`int3` sites, directly and in a child; all must complete. Its handler resumes
only after traps in that test function. D2R still requires a new device run.

## Follow-up: inaccessible x64 thread entry points

The next device session (20 October, 18:17) confirms that the previous detector
change lets startup progress through hundreds of guest breakpoints. The new
failure is a native read in `arm64x_check_call`: it inspects D2R's entry point
while the remapped page has no access. The read faults in native ntdll. The guest
hook then receives that native context, and the next fault resumes two bytes
into the ARM64 instruction. BlizzardError.exe opens after the unhandled access
violation. The logs do not establish why the loader made the entry page
inaccessible, so this change does not relax that protection.

The Wine companion adds a fallback for the optional x64 code probe. On iOS,
a read access violation inside assembly-labelled probe bounds, outside emulator
execution, completes the existing exit-thunk selection and resumes the native
caller with the native NtContinue syscall. The emulator then attempts the guest
address, where normal guest fault handling can apply. Bitmap lookups, unrelated
native PCs, writes, instruction-fetch faults, guard faults and pre-initialisation
paths retain their exception handling. A failed native continuation leaves the
original exception context intact. Readable targets keep the existing fast path.
No FEX source is modified.

Run `python3 tests/host/check-ec-probe-fallback.py --runtime` after rebuilding
ntdll. Sanitized checks cover the recovered register state and exclusions; the
runtime check derives probe bounds from compiler symbols and verifies that the
bundled probe and exit instructions match that contract. It does not execute
the iOS bridge.

The guest regression now also starts a normal readable x64 thread, then creates
a thread at a deliberately PAGE_NOACCESS code page. Its handler requires the
exception address and RIP to name the guest entry, restores executable access,
and retries without changing RIP. The thread must return the expected value,
with exactly one handled fault. All tests repeat in a child process. An
unexpected native context fails the test instead of skipping an instruction.
This candidate still needs the iPhone regression and D2R launch test; successful
host checks and IPA compilation alone do not establish D2R compatibility.

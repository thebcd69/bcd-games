#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""A failed Metal command buffer is described with the facts that tell a culprit from a victim (diagnostic, log only; night round 2026-11-11, T3 S14).

Sekiro: Shadows Die Twice (FB6729, ZA4PSP, build 630, iPhone17,2 / iPhone18,3) plays at 38-44 FPS, then stalls for 10 s and 32.7 s while
every command buffer fails with "Discarded (victim of GPU error/recovery) (00000005:kIOGPUCommandBufferCallbackErrorInnocentVictim)"
(IOGPUCommandQueueErrorDomain code 5; "GPU busy 6 %" in the window, the performance cores parked, thermal serious). A victim is
innocent by definition: the buffer that CAUSED the recovery is not in any of the logs (it was never reported to DXMT: another queue of
the process, another process, or the system's own reset), so the stall has no named cause.

winemetal's _MTLCommandBuffer_error (DXMT calls it once for each buffer that finished in the error state) now also prints one line
with what the process can know at that moment: whether this error is a victim or something else, the error and underlying error codes,
the command buffer's label (if any) and its GPU/kernel start and end times (0 = it never ran), the system's thermal state and low-power
mode, how many errors the process has seen and how long ago the first one was:
  [gpu-error] madeira-bcd #N (victim|other): code=C domain=D underlying=U label=L gpu=[S,E] kernel=[S,E] thermal=T low-power=P
              errors=E victims=V since-first=Xs
The first 8 errors are printed, then every 64th. A run whose FIRST error is not a victim names the culprit buffer by its code (the
existing "Device error" line already prints its text); a run that only ever sees victims says so in numbers, and the thermal field and
the time since the first error show whether the recovery began with the thermal change. Native only: every game and every API that goes
through winemetal (Direct3D 9, 11, 12), 32- and 64-bit, with no list line and no switch; it only reads, after the error was fetched.
Idempotent; fails by name when the anchor moved. Run from the repository root.
"""
import pathlib
import sys

PATH = pathlib.Path("dxmt/src/winemetal/unix/winemetal_unix.c")
MARKER = "madeira-bcd: gpu-error info"

OLD = """static NTSTATUS
_MTLCommandBuffer_error(void *obj) {
  struct unixcall_generic_obj_obj_ret *params = obj;
  params->ret = (obj_handle_t)[(id<MTLCommandBuffer>)params->handle error];
  return STATUS_SUCCESS;
}
"""
NEW = """/* """ + MARKER + """ (night round 2026-11-11, T3 S14: Sekiro's 10-33 s GPU recovery stalls, every buffer an InnocentVictim and the culprit in no log):
 * one line for the first 8 failed command buffers (then every 64th) with what tells a victim from a culprit. Reads only. */
static void
madeira_gpu_error_note(id<MTLCommandBuffer> cb, NSError *err) {
  static unsigned total, victims;
  static uint64_t first_ns;
  unsigned n = __atomic_add_fetch(&total, 1, __ATOMIC_RELAXED);
  uint64_t now = clock_gettime_nsec_np(CLOCK_UPTIME_RAW);
  if (n == 1)
    first_ns = now;
  @autoreleasepool {
    NSString *text = [err localizedDescription];
    NSError *under = [[err userInfo] objectForKey:NSUnderlyingErrorKey];
    int victim = [text rangeOfString:@"InnocentVictim"].location != NSNotFound;
    unsigned v = victim ? __atomic_add_fetch(&victims, 1, __ATOMIC_RELAXED) : __atomic_load_n(&victims, __ATOMIC_RELAXED);
    if (n > 8 && (n & 63) != 0)
      return;
    NSProcessInfoThermalState th = [[NSProcessInfo processInfo] thermalState];
    const char *ths = th == NSProcessInfoThermalStateNominal ? "nominal" : th == NSProcessInfoThermalStateFair ? "fair"
                      : th == NSProcessInfoThermalStateSerious ? "serious" : "critical";
    NSString *label = [cb label];
    fprintf(stderr, "[gpu-error] madeira-bcd #%u (%s): code=%ld domain=%s underlying=%ld label=%s gpu=[%.3f,%.3f] kernel=[%.3f,%.3f] "
            "thermal=%s low-power=%d errors=%u victims=%u since-first=%.1fs\\n",
            n, victim ? "victim" : "other", (long)[err code], [[err domain] UTF8String] ? [[err domain] UTF8String] : "?",
            under ? (long)[under code] : -1L, label && [label length] ? [label UTF8String] : "(none)",
            [cb GPUStartTime], [cb GPUEndTime], [cb kernelStartTime], [cb kernelEndTime], ths,
            (int)[[NSProcessInfo processInfo] isLowPowerModeEnabled], n, v, (double)(now - first_ns) / 1e9);
  }
}

static NTSTATUS
_MTLCommandBuffer_error(void *obj) {
  struct unixcall_generic_obj_obj_ret *params = obj;
  NSError *err = [(id<MTLCommandBuffer>)params->handle error];
  params->ret = (obj_handle_t)err;
  if (err)
    madeira_gpu_error_note((id<MTLCommandBuffer>)params->handle, err);
  return STATUS_SUCCESS;
}
"""


def main():
    if not PATH.exists():
        sys.exit(f"patch-winemetal-gpu-error-info: {PATH} not found (run from the repository root)")
    s = PATH.read_text()
    if MARKER in s:
        print("winemetal_unix.c: already patched")
        return 0
    if s.count(OLD) != 1:
        sys.exit("patch-winemetal-gpu-error-info: the _MTLCommandBuffer_error anchor was not found exactly once in winemetal_unix.c (dxmt changed)")
    PATH.write_text(s.replace(OLD, NEW, 1))
    print("winemetal_unix.c: _MTLCommandBuffer_error describes a failed command buffer ([gpu-error])")
    return 0


if __name__ == "__main__":
    sys.exit(main())

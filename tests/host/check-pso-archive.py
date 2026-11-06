#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""pso-archive: compiled pipelines kept in Metal binary archives of our own (opt-in, default off;
upstream Madeira #516 with dxmt d8894f4, a828d75, 19ea691); no GPU, no Wine.

Metal's own cache of compiled pipelines can be emptied by iOS between sessions (upstream log: 6,904 of 6,986
pipelines found, then 19 of 500, then minutes of compiles at 420-550 ms each). With `pso-archive = 1` (bcd.cfg
or the game's file) madeira_d3d12 names a folder (LOCALAPPDATA\\BCD\\PipelineArchive\\<switch set>-<executable>)
to winemetal (MadeiraCtl op 11); winemetal then looks every render and compute pipeline up in the saved chunk
files first (fail on a miss: a lookup, not a compile), compiles a miss once through one of four open archives
(or queues it), saves a chunk every 128 pipelines and 10 s after the last add, keeps the folder under
MADEIRA_PSO_ARCHIVE_MB (1024) by deleting the oldest chunks at load, and deletes chunks Metal cannot load.

1. Source, madeira_d3d12.c: the key is read first, default 0, and mad_pso_archive_init returns before it
   touches anything when it is off; it is called once, from mad_sc_init, only when the shader cache is on;
   the folder is outside the shader cache and says BCD.
2. Source, winemetal: op 11 exists once and takes a name only when no remote host is used; the lookup list is
   nil until a folder was named (so a session without the key creates every pipeline with the same call as before);
   a pipeline that already carries lookup archives of its own is left alone.
3. The archive code of winemetal_unix.c is cut out and compiled on the host as Objective-C with a fake Metal device
   and fake archives that write and read real files: naming (same folder, another folder, empty, too long);
   load (valid chunks join the list, an unreadable chunk and an unfinished save are deleted, a stray file is left,
   the next chunk number is past every chunk); the cap (the oldest chunks go down to three quarters); the queue
   add (a chunk after 128 pipelines, one more 10 s after the last add, the lookup list grows, nothing is added once
   the folder is at its cap); the inline add (a miss is created from a slot's archive, all four slots busy or
   MADEIRA_PSO_ARCHIVE_INLINE=0 returns 0 so the caller creates it, a failed add returns 0, a full slot is saved
   as a chunk after 128).
4. CI runs this check.
SKIP (exit 0) off macOS or without clang and the Metal / Foundation SDK.
"""
from pathlib import Path
import os
import re
import shutil
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[2]
wm_path = root / "dxmt/src/winemetal/unix/winemetal_unix.c"
d12 = (root / "madeira-d3d12/src/pe/madeira_d3d12.c").read_text()
wf = (root / ".github/workflows/build-ipa.yml").read_text()
failures = []


def check(what, cond):
    print(("PASS: " if cond else "FAIL: ") + what)
    if not cond:
        failures.append(what)


# ---- 1. madeira_d3d12.c
fn = d12[d12.index("static void mad_pso_archive_init(void) {"):]
fn = fn[:fn.index("\n}\n")]
check("the key is read first, default 0, and nothing is touched before it",
      fn.index('if (mad_cfg_int_pe("pso-archive", 0) <= 0) return;') < fn.index("GetModuleFileNameW")
      and fn.index('if (mad_cfg_int_pe("pso-archive", 0) <= 0) return;') < fn.index("CreateDirectoryW")
      and d12.count('"pso-archive"') == 1)
check("it runs once, from mad_sc_init, only with the shader cache on",
      d12.count("mad_pso_archive_init();") == 1 and "if (on) { mad_scn_init(); mad_pso_archive_init(); MemoryBarrier(); }" in d12)
check("the folder is BCD's, outside the shader cache, one per switch set and executable",
      "L\"%ls\\\\BCD\\\\PipelineArchive\\\\%ls-%016llx\"" in fn and "ShaderCache" not in fn)
check("it asks winemetal with MadeiraCtl op 11 and a unix path, and says what happened",
      "a.op = 11; a.ptr = (UINT64)(ULONG_PTR)udir;" in fn and fn.count("d3d12_log(") == 3)
check("op 11 is not used by another call in madeira_d3d12.c (op 10 is the warm-up)", len(re.findall(r"a\.op = 11;", d12)) == 1)

# ---- 2. winemetal
if not wm_path.is_file():
    print("SKIP: no dxmt sources (the winemetal part needs the submodule)")
    print("FAILED: pso-archive" if failures else "PASS: pso-archive (madeira_d3d12 part only)")
    sys.exit(1 if failures else 0)
wm = wm_path.read_text()
if "madeira_arch_lookup" not in wm:
    check("winemetal_unix.c has the pipeline archive (dxmt d8894f4, a828d75, 19ea691): pin the dxmt fork at the commits that carry it", False)
    print("FAILED: pso-archive")
    sys.exit(1)
check("op 11 exists once, takes a name only without a remote host",
      wm.count("  case 11:") == 1 and "if (a->ptr && !wmtr_enabled()) a->ret = madeira_arch_name((const char *)(uintptr_t)a->ptr);" in wm)
check("the lookup list is nil until a folder was named",
      "if (atomic_load_explicit(&madeira_arch_named, memory_order_acquire) != 2 || !device) return nil;" in wm)
for what, name in (("render", "_MTLDevice_newRenderPipelineState(void *obj) {"), ("render with a vertex descriptor", "_MTLDevice_newRenderPipelineStateVD(void *obj) {"),
                   ("compute", "_MTLDevice_newComputePipelineState(void *obj) {")):
    body = wm[wm.index(name):]
    body = body[:body.index("\n}\n")]
    check(f"{what}: a pipeline that carries lookup archives of its own is left alone, and with no folder the creation call is the old one",
          "descriptor.binaryArchives ? nil : madeira_arch_lookup(" in body and "if (!params->ret_pso) {\n    err = NULL;" in body
          and "if (!params->ret_pso && madeira_arch)" in body)

# ---- 3. the harness
sdk = shutil.which("xcrun") and subprocess.run(["xcrun", "--sdk", "macosx", "--show-sdk-path"], capture_output=True, text=True).stdout.strip()
if sys.platform != "darwin" or not sdk:
    print("SKIP: no macOS SDK for the behaviour part (macOS CI and the Mac run it)")
else:
    start = wm.index("/* PIPELINE ARCHIVE (madeira.cfg pso-archive = 1")
    end = wm.index("static NTSTATUS\n_MTLDevice_newComputePipelineState(void *obj) {")
    block = wm[start:end]
    harness = r'''
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#include <stdatomic.h>
#include <os/lock.h>
#include <dispatch/dispatch.h>
#include <sys/stat.h>
#include <unistd.h>
#include <time.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
typedef uint64_t obj_handle_t;

static int g_add_fail, g_ser_fail;
static _Atomic int g_archives_made, g_pso_made;
@interface FakeArchive : NSObject { @public int n; }
@end
@implementation FakeArchive
- (BOOL)serializeToURL:(NSURL *)u error:(NSError **)e {
  if (g_ser_fail) { if (e) *e = [NSError errorWithDomain:@"fake" code:1 userInfo:nil]; return NO; }
  NSMutableData *d = [NSMutableData dataWithBytes:"FAKEARCH" length:8];
  [d setLength:8 + (n ? n : 1) * 512];
  return [d writeToURL:u atomically:NO];
}
- (BOOL)addRenderPipelineFunctionsWithDescriptor:(id)d error:(NSError **)e {
  if (g_add_fail) { if (e) *e = [NSError errorWithDomain:@"fake" code:2 userInfo:nil]; return NO; }
  n++; return YES;
}
- (BOOL)addComputePipelineFunctionsWithDescriptor:(id)d error:(NSError **)e { return [self addRenderPipelineFunctionsWithDescriptor:d error:e]; }
@end
@interface FakeDesc : NSObject <NSCopying> { @public id archives; }
- (void)setBinaryArchives:(id)a;
@end
@implementation FakeDesc
- (id)copyWithZone:(NSZone *)z { return [[FakeDesc alloc] init]; }
- (void)setBinaryArchives:(id)a { archives = a; }
@end
@interface FakePSO : NSObject @end
@implementation FakePSO @end
@interface FakeDevice : NSObject @end
@implementation FakeDevice
- (id)newBinaryArchiveWithDescriptor:(MTLBinaryArchiveDescriptor *)d error:(NSError **)e {
  if (d.url) {
    NSData *data = [NSData dataWithContentsOfURL:d.url];
    if (!data || data.length < 8 || memcmp(data.bytes, "FAKEARCH", 8)) { if (e) *e = [NSError errorWithDomain:@"fake" code:3 userInfo:nil]; return nil; }
    FakeArchive *a = [[FakeArchive alloc] init]; a->n = (int)(data.length - 8) / 512; return a;
  }
  atomic_fetch_add(&g_archives_made, 1);
  return [[FakeArchive alloc] init];
}
- (id)newRenderPipelineStateWithDescriptor:(id)d options:(MTLPipelineOption)o reflection:(id)r error:(NSError **)e {
  atomic_fetch_add(&g_pso_made, 1); return [[FakePSO alloc] init];
}
- (id)newComputePipelineStateWithDescriptor:(id)d options:(MTLPipelineOption)o reflection:(id)r error:(NSError **)e {
  atomic_fetch_add(&g_pso_made, 1); return [[FakePSO alloc] init];
}
@end
''' + block + r'''
static int fails = 0;
static void expect(BOOL c, const char *m) { printf("%s %s\n", c ? "ok  " : "FAIL", m); if (!c) fails++; }
static void write_chunk(const char *dir, const char *name, size_t size, BOOL valid) {
  char p[1300]; snprintf(p, sizeof p, "%s/%s", dir, name);
  FILE *f = fopen(p, "wb"); if (!f) { perror(p); exit(3); }
  char *buf = calloc(1, size); if (valid) memcpy(buf, "FAKEARCH", 8); else memcpy(buf, "GARBAGE!", 8);
  fwrite(buf, 1, size, f); fclose(f); free(buf);
}
static BOOL exists(const char *dir, const char *name) { char p[1300]; snprintf(p, sizeof p, "%s/%s", dir, name); return access(p, F_OK) == 0; }
static void drain(void) { dispatch_sync(madeira_arch_q, ^{}); }
static NSUInteger chunk_count(void) { os_unfair_lock_lock(&madeira_arch_lock); NSUInteger n = madeira_arch_list.count; os_unfair_lock_unlock(&madeira_arch_lock); return n; }
int main(int argc, char **argv) {
  @autoreleasepool {
    int ph = atoi(argv[1]); const char *dir = argv[2];
    FakeDevice *dev = [[FakeDevice alloc] init];
    id<MTLDevice> mdev = (id<MTLDevice>)dev;
    if (ph == 0) {
      expect(madeira_arch_lookup(mdev) == nil, "lookup before a folder is named: nil (a session without the key is as before)");
      char sub[1300]; snprintf(sub, sizeof sub, "%s/a", dir);
      expect(madeira_arch_name(sub) == 1 && access(sub, F_OK) == 0, "naming a folder creates it");
      expect(madeira_arch_name(sub) == 1, "the same folder again is fine");
      expect(madeira_arch_name("/tmp/other-folder-not-created") == 0 && access("/tmp/other-folder-not-created", F_OK) != 0, "another folder is refused and not created");
      expect(madeira_arch_name("") == 0 && madeira_arch_name(NULL) == 0, "an empty or missing name is refused");
      char longname[1500]; memset(longname, 'x', sizeof longname - 1); longname[sizeof longname - 1] = 0;
      expect(madeira_arch_name(longname) == 0, "a name longer than the buffer is refused");
      return fails;
    }
    if (ph == 1) {   /* load */
      write_chunk(dir, "chunk-0.metalar", 100000, YES);
      write_chunk(dir, "chunk-1.metalar", 200000, YES);
      write_chunk(dir, "chunk-2.metalar", 5000, NO);
      write_chunk(dir, "chunk-3.tmp", 1000, YES);
      write_chunk(dir, "notes.txt", 10, YES);
      expect(madeira_arch_name(dir) == 1, "the folder is named");
      NSArray *list = madeira_arch_lookup(mdev);
      expect(list != nil && list.count == 2, "two valid chunks join the lookup list");
      expect(exists(dir, "chunk-0.metalar") && exists(dir, "chunk-1.metalar"), "the valid chunks are kept");
      expect(!exists(dir, "chunk-2.metalar"), "a chunk Metal cannot load is deleted");
      expect(!exists(dir, "chunk-3.tmp"), "an unfinished save is deleted");
      expect(exists(dir, "notes.txt"), "a stray file is left alone");
      expect(atomic_load(&madeira_arch_bytes) == 300000, "the byte count is the valid chunks'");
      expect(madeira_arch_next >= 3, "the next chunk number is past every chunk that was there");
      [list release];
      return fails;
    }
    if (ph == 2) {   /* cap: MADEIRA_PSO_ARCHIVE_MB=1 */
      for (int k = 0; k < 4; k++) { char n[40]; snprintf(n, sizeof n, "chunk-%d.metalar", k); write_chunk(dir, n, 400000, YES); }
      expect(madeira_arch_name(dir) == 1, "the folder is named");
      NSArray *list = madeira_arch_lookup(mdev);
      expect(!exists(dir, "chunk-0.metalar") && !exists(dir, "chunk-1.metalar") && !exists(dir, "chunk-2.metalar") && exists(dir, "chunk-3.metalar"),
             "over the cap (1.6 MB of 1 MB): the oldest go, down to three quarters, the newest stays");
      expect(list.count == 1 && atomic_load(&madeira_arch_bytes) == 400000, "only the newest chunk is on the list");
      [list release];
      expect(!madeira_arch_full(), "400 KB is under the 1 MB cap: adds are still taken");
      FakeDesc *d = [[FakeDesc alloc] init];
      for (int i = 0; i < 128; i++) madeira_arch_add(d, 0);
      drain();
      expect(chunk_count() >= 2 && atomic_load(&madeira_arch_bytes) >= 400000, "a chunk was saved after 128 pipelines");
      return fails;
    }
    if (ph == 3) {   /* queue add: chunk after 128, one more 10 s after the last add */
      expect(madeira_arch_name(dir) == 1, "the folder is named");
      NSArray *l0 = madeira_arch_lookup(mdev); [l0 release];
      expect(chunk_count() == 0 && madeira_arch_next == 0, "an empty folder: no chunks");
      FakeDesc *d = [[FakeDesc alloc] init];
      for (int i = 0; i < 130; i++) madeira_arch_add(d, i & 1);
      drain();
      expect(exists(dir, "chunk-0.metalar") && !exists(dir, "chunk-1.metalar"), "after 128 adds chunk 0 is saved (the other two wait)");
      expect(chunk_count() == 1, "the saved chunk joined the lookup list at once");
      expect(atomic_load(&madeira_arch_added) == 130 && atomic_load(&madeira_arch_misses) == 130, "130 adds counted");
      sleep(12);
      drain();
      expect(exists(dir, "chunk-1.metalar") && chunk_count() == 2, "10 s after the last add the two that were left are saved as chunk 1");
      expect(!exists(dir, "chunk-0.tmp") && !exists(dir, "chunk-1.tmp"), "no unfinished save is left");
      g_add_fail = 1;
      madeira_arch_add(d, 0); drain();
      expect(atomic_load(&madeira_arch_add_failed) >= 1, "a failed add is counted, nothing is saved from it");
      g_add_fail = 0;
      return fails;
    }
    if (ph == 4) {   /* inline add */
      expect(madeira_arch_name(dir) == 1, "the folder is named");
      NSArray *l0 = madeira_arch_lookup(mdev); [l0 release];
      FakeDesc *d = [[FakeDesc alloc] init];
      NSError *err = nil;
      obj_handle_t p = madeira_arch_create(mdev, d, 0, MTLPipelineOptionNone, &err);
      expect(p != 0 && atomic_load(&madeira_arch_inline) == 1 && g_pso_made == 1, "a miss is created from a slot's archive (added, then created)");
      expect(d->archives == nil, "the descriptor's lookup list is cleared again afterwards");
      /* all four slots busy */
      for (int i = 0; i < MADEIRA_ARCH_SLOTS; i++) os_unfair_lock_lock(&madeira_arch_slots[i].lock);
      int made = g_pso_made;
      expect(madeira_arch_create(mdev, d, 0, MTLPipelineOptionNone, &err) == 0 && g_pso_made == made, "every slot busy: 0, nothing created (the caller creates and queues)");
      for (int i = 0; i < MADEIRA_ARCH_SLOTS; i++) os_unfair_lock_unlock(&madeira_arch_slots[i].lock);
      g_add_fail = 1;
      expect(madeira_arch_create(mdev, d, 1, MTLPipelineOptionNone, &err) == 0 && g_pso_made == made, "a refused add: 0, nothing created");
      g_add_fail = 0;
      /* 128 inline creates fill a slot, which is saved as a chunk on the queue */
      for (int i = 0; i < 300; i++) madeira_arch_create(mdev, d, i & 1, MTLPipelineOptionNone, &err);
      drain();
      expect(exists(dir, "chunk-0.metalar"), "a full slot is saved as a chunk");
      expect(atomic_load(&madeira_arch_inline_slow) == 0, "nothing was counted as compiled again");
      return fails;
    }
    if (ph == 5) {   /* MADEIRA_PSO_ARCHIVE_INLINE=0 */
      expect(madeira_arch_name(dir) == 1, "the folder is named");
      NSArray *l0 = madeira_arch_lookup(mdev); [l0 release];
      FakeDesc *d = [[FakeDesc alloc] init]; NSError *err = nil;
      expect(madeira_arch_create(mdev, d, 0, MTLPipelineOptionNone, &err) == 0 && g_pso_made == 0, "MADEIRA_PSO_ARCHIVE_INLINE=0: no inline add, the caller creates and queues");
      return fails;
    }
  }
  return 2;
}
'''
    with tempfile.TemporaryDirectory() as t:
        t = Path(t)
        (t / "h.m").write_text(harness)
        b = subprocess.run(["xcrun", "clang", "-x", "objective-c", "-fno-objc-arc", "-O1", "-g", "-Wall", "-Wno-unused-function",
                            "-Wno-unused-variable", "-Wno-deprecated-declarations", "-Wno-incomplete-implementation", "-Wno-unused-but-set-variable",
                            "-Wno-format", "-framework", "Foundation", "-framework", "Metal", "-o", str(t / "h"), str(t / "h.m")],
                           capture_output=True, text=True)
        check("the archive code of winemetal_unix.c compiles on the host with a fake Metal device", b.returncode == 0)
        if b.returncode:
            print(b.stderr[-3500:])
        else:
            cases = ((0, {}, "naming"), (1, {}, "load"), (2, {"MADEIRA_PSO_ARCHIVE_MB": "1"}, "the cap and the queue add"), (3, {}, "the queue add and the 10 s save"),
                     (4, {}, "the inline add"), (5, {"MADEIRA_PSO_ARCHIVE_INLINE": "0"}, "inline add off"))
            for ph, extra, what in cases:
                d = t / f"dir{ph}"
                d.mkdir()
                env = {k: v for k, v in os.environ.items() if not k.startswith("MADEIRA_PSO_ARCHIVE")}
                env.update(extra)
                r = subprocess.run([str(t / "h"), str(ph), str(d)], capture_output=True, text=True, env=env, timeout=240)
                print(r.stdout.rstrip())
                if r.returncode:
                    print(r.stderr[-1500:])
                check(f"harness case {ph} ({what})", r.returncode == 0)

check("this check runs in the workflow", "tests/host/check-pso-archive.py" in wf)
print("FAILED: pso-archive" if failures else "PASS: pso-archive")
sys.exit(1 if failures else 0)

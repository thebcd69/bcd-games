#!/usr/bin/env python3
"""The pipeline cache warm-up (pso-warm) hands lazy pipelines to winemetal and
winemetal compiles copies of them on its queues; no Metal, no Wine.

madeira-d3d12 (bcd.cfg / a game's config pso-warm = N) sends every lazy
pipeline to winemetal at creation (MadeiraCtl op 10), and
tools/patch-winemetal-pso-warm.py makes winemetal build it with DXMT's own
builder on one of N utility queues and release it at once, so only Metal's
cached compile stays (first draws: 35-47 ms new, 0.3-0.5 ms cached; GTA V
Enhanced, builds 447-451). Checks:
  - the patch applies once to winemetal_unix.c after the GPU fault patch (ops
    8 and 9; build 452 failed on a second case 8), puts op 10 in _madeira_ctl
    with no case value twice, and the i386 entry still forwards op 7 only;
  - the patch's own code, compiled against a fake GCD that runs blocks at once:
    off (pso-warm unset) answers 0 and builds nothing; with pso-warm = 2 a
    render, a render + vertex descriptor and a compute request each answer 1,
    reach the matching DXMT builder with a COPY of the info (and the vertex
    descriptor right after it), alternate between the two queues, release the
    built pipeline, and leave every retain balanced; bad requests answer 0;
    the [pso-warm] line splits compiled ones from cache hits;
  - madeira_d3d12.c sends both lazy kinds (render at its lazy point, compute
    with the descriptor mad_cpso_realize builds) and stops when op 10 answers 0;
  - build-ipa.yml runs the patch.
Needs python3 and a C compiler with -fblocks (clang).
"""
from pathlib import Path
import os
import re
import shutil
import subprocess
import tempfile

root = Path(__file__).resolve().parents[2]
patch = root / 'tools/patch-winemetal-pso-warm.py'
fault_patch = root / 'tools/patch-dxmt-gpu-fault-info.py'   # runs earlier in build-ipa.yml
wm_src = root / 'dxmt/src/winemetal/unix/winemetal_unix.c'
d3d12 = (root / 'madeira-d3d12/src/pe/madeira_d3d12.c').read_text()
workflow = (root / '.github/workflows/build-ipa.yml').read_text()

# --- the patch on a copy of winemetal_unix.c --------------------------------
with tempfile.TemporaryDirectory(prefix='madeira-pso-warm-') as directory:
    tree = Path(directory)
    target = tree / 'dxmt/src/winemetal/unix/winemetal_unix.c'
    target.parent.mkdir(parents=True)
    shutil.copy(wm_src, target)
    fault = subprocess.run(['python3', str(fault_patch)], cwd=tree, capture_output=True, text=True)
    assert fault.returncode == 0, fault.stdout + fault.stderr
    first = subprocess.run(['python3', str(patch)], cwd=tree, capture_output=True, text=True)
    assert first.returncode == 0, first.stdout + first.stderr
    second = subprocess.run(['python3', str(patch)], cwd=tree, capture_output=True, text=True)
    assert second.returncode == 0 and 'already applied' in second.stdout, second.stdout + second.stderr
    patched = target.read_text()

ctl = patched[patched.index('static NTSTATUS _madeira_ctl(void *args) {'):]
ctl = ctl[:ctl.index('\n}\n') + 3]
assert '  case 10: {' in ctl and 'madeira_pso_warm_submit(' in ctl, 'op 10 is not in _madeira_ctl'
cases = re.findall(r'^\s*case (\d+):', ctl, re.M)
assert len(cases) == len(set(cases)), f'_madeira_ctl has a case value twice: {sorted(cases, key=int)}'
wow = patched[patched.index('static NTSTATUS _madeira_ctl_wow64(void *args) {'):]
wow = wow[:wow.index('\n}\n') + 3]
assert 'a->op == 7' in wow and 'op == 10' not in wow, 'the i386 entry must not forward op 10'
print('PASS: the patch applies once after the GPU fault patch, op 10 lives in _madeira_ctl, no case twice, the i386 entry forwards op 7 only')

# --- the patch's code, run --------------------------------------------------
helpers = patched[patched.index('/* madeira-bcd: pipeline cache warm-up'):patched.index('static NTSTATUS _madeira_ctl(void *args) {')]
# Other patches (sc-native, rp-store, ...) put their own blocks between this one and _madeira_ctl: stop at the next patch's block.
_next = re.search(r'/\* madeira-bcd: [^\n]*tools/patch-(?!winemetal-pso-warm)', helpers[1:])
if _next:
    helpers = helpers[:_next.start() + 1]
case = ctl[ctl.index('  case 10: {'):]
case = case[:case.index('    break;\n  }\n') + len('    break;\n  }\n')]
# Objective-C -> C for the host: retains and releases are counted, the pool is a block scope.
c_code = helpers
c_code = re.sub(r'\[\(id\)(\w+(?:\[\w+\])?) retain\]', r'fake_retain((uintptr_t)(\1))', c_code)
c_code = re.sub(r'\[\(id\)(\w+(?:\[\w+\])?) release\]', r'fake_release((uintptr_t)(\1))', c_code)
c_code = c_code.replace('@autoreleasepool {', '{')
assert '[' not in re.sub(r'(?:\w+|\))\[[^\]]*\]', '', c_code.replace('[pso-warm]', '')), 'an Objective-C message was left untranslated'

harness = r'''
#define _GNU_SOURCE
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdatomic.h>
#include <assert.h>
typedef int NTSTATUS;
#define STATUS_SUCCESS 0
typedef uint64_t obj_handle_t;
void *_NSConcreteStackBlock[32], *_NSConcreteGlobalBlock[32];
struct WMTConstMemoryPointer { const void *ptr; };
struct WMTRenderPipelineInfo { unsigned char colors[200]; obj_handle_t vertex_function, fragment_function, binary_archive_for_serialization;
                               struct WMTConstMemoryPointer binary_archives_for_lookup; uint8_t num_binary_archives_for_lookup; uint8_t padding[6]; };
struct WMTComputePipelineInfo { obj_handle_t compute_function; struct WMTConstMemoryPointer binary_archives_for_lookup;
                                obj_handle_t binary_archive_for_serialization; uint8_t num_binary_archives_for_lookup; uint8_t padding; };
struct WMTVertexDescriptorInfo { unsigned char attrs[800]; };
struct unixcall_mtldevice_newcomputepso { obj_handle_t device; struct WMTConstMemoryPointer info; obj_handle_t ret_error; obj_handle_t ret_pso; };
struct unixcall_mtldevice_newrenderpso { obj_handle_t device; struct WMTConstMemoryPointer info; obj_handle_t ret_error; obj_handle_t ret_pso; };
struct unixcall_mtldevice_newrenderpso_vd { obj_handle_t device; struct WMTConstMemoryPointer info; struct WMTConstMemoryPointer vd; obj_handle_t ret_error; obj_handle_t ret_pso; };
struct madeira_ctl_args { uint32_t op; uint32_t ret; uint64_t ptr; uint64_t len; char name[160]; };
/* fake GCD: blocks run at once on the submitting thread */
typedef struct fake_queue { int id; } *dispatch_queue_t;
typedef void *dispatch_queue_attr_t;
typedef long dispatch_once_t;
static int queues_made, last_queue;
static void dispatch_once(dispatch_once_t *o, void (^b)(void)) { if (!*o) { *o = 1; b(); } }
static dispatch_queue_attr_t dispatch_queue_attr_make_with_qos_class(dispatch_queue_attr_t a, int qos, int prio) { (void)a; assert(qos == 0x11 && prio == 0); return (void *)1; }
static dispatch_queue_t dispatch_queue_create(const char *label, dispatch_queue_attr_t a) {
    struct fake_queue *q = calloc(1, sizeof *q); (void)label; assert(a); q->id = ++queues_made; return q; }
static void dispatch_async(dispatch_queue_t q, void (^b)(void)) { last_queue = q->id; b(); }
#define DISPATCH_QUEUE_SERIAL ((dispatch_queue_attr_t)0)
#define QOS_CLASS_UTILITY 0x11
#define CLOCK_UPTIME_RAW 8
static uint64_t clock_ns = 100000000000ull, step_ns;
static uint64_t clock_gettime_nsec_np(int c) { (void)c; clock_ns += step_ns; return clock_ns; }
static long long cfg_warm;
static long long madeira_cfg_int(const char *key, long long dflt) { return !strcmp(key, "pso-warm") ? cfg_warm : dflt; }
static int wmtr_enabled(void) { return 0; }
/* retain counts by handle */
static int retains[64], releases[64];
static obj_handle_t archs_r[2] = { 21, 22 }, archs_c[1];   /* the caller's lookup lists: render has two archives, compute none */
static void fake_retain(uintptr_t h) { assert(h < 64); retains[h]++; }
static void fake_release(uintptr_t h) { assert(h < 64); releases[h]++; }
/* DXMT's builders: check the copy, "build" handle 40+kind */
static const void *orig_info, *orig_vd;
static int built[3];
/* The copy keeps the caller's info byte for byte, except lookup.ptr: the copy owns a list of its own (the caller's may be released). */
static void same_info(const void *copy, const void *orig, size_t size, const void *orig_list, unsigned n) {
    unsigned char c[1024]; const struct WMTRenderPipelineInfo *r = copy; const obj_handle_t *list;
    assert(size <= sizeof c);
    memcpy(c, copy, size);
    if (size == sizeof(struct WMTRenderPipelineInfo)) {
        list = r->binary_archives_for_lookup.ptr;
        ((struct WMTRenderPipelineInfo *)c)->binary_archives_for_lookup.ptr = orig_list;
    } else {
        list = ((const struct WMTComputePipelineInfo *)copy)->binary_archives_for_lookup.ptr;
        ((struct WMTComputePipelineInfo *)c)->binary_archives_for_lookup.ptr = orig_list;
    }
    assert(!memcmp(c, orig, size));
    if (n) { assert(list != orig_list && !memcmp(list, orig_list, n * sizeof(obj_handle_t))); } else assert(list == orig_list);
}
static NTSTATUS _MTLDevice_newRenderPipelineState(void *obj) {
    struct unixcall_mtldevice_newrenderpso *p = obj;
    assert(p->device == 7 && p->info.ptr && p->info.ptr != orig_info);
    same_info(p->info.ptr, orig_info, sizeof(struct WMTRenderPipelineInfo), archs_r, 2);
    built[0]++; p->ret_pso = 40; return 0; }
static NTSTATUS _MTLDevice_newRenderPipelineStateVD(void *obj) {
    struct unixcall_mtldevice_newrenderpso_vd *p = obj;
    assert(p->device == 7 && p->info.ptr != orig_info && p->vd.ptr != orig_vd);
    assert((const unsigned char *)p->vd.ptr == (const unsigned char *)p->info.ptr + sizeof(struct WMTRenderPipelineInfo));
    same_info(p->info.ptr, orig_info, sizeof(struct WMTRenderPipelineInfo), archs_r, 2);
    assert(!memcmp(p->vd.ptr, orig_vd, sizeof(struct WMTVertexDescriptorInfo)));
    built[1]++; p->ret_pso = 41; return 0; }
static NTSTATUS _MTLDevice_newComputePipelineState(void *obj) {
    struct unixcall_mtldevice_newcomputepso *p = obj;
    assert(p->device == 7 && p->info.ptr != orig_info);
    same_info(p->info.ptr, orig_info, sizeof(struct WMTComputePipelineInfo), archs_c, 0);
    built[2]++; p->ret_pso = 0; /* a failed build */ return 0; }
''' + c_code + r'''
static uint32_t op8(unsigned kind, const void *info, const void *vd) {
    struct madeira_ctl_args args; struct madeira_ctl_args *a = &args;
    uint64_t r[3] = { 7, (uint64_t)(uintptr_t)info, (uint64_t)(uintptr_t)vd };
    memset(a, 0, sizeof *a); a->op = 10; a->len = kind; a->ptr = (uint64_t)(uintptr_t)r;
    switch (a->op) {
''' + case + r'''
    default: break;
    }
    return a->ret;
}
int main(int argc, char **argv) {
    struct WMTRenderPipelineInfo rp; struct WMTVertexDescriptorInfo vd; struct WMTComputePipelineInfo cp;
    int i, q1, q2;
    (void)argc;
    memset(&rp, 0x5a, sizeof rp); rp.vertex_function = 11; rp.fragment_function = 12;
    rp.binary_archives_for_lookup.ptr = archs_r; rp.num_binary_archives_for_lookup = 2; rp.binary_archive_for_serialization = 23;
    memset(&vd, 0x33, sizeof vd);
    memset(&cp, 0, sizeof cp); cp.compute_function = 13;
    cp.binary_archives_for_lookup.ptr = archs_c;
    cfg_warm = atoll(argv[1]);
    if (!cfg_warm) {
        orig_info = &rp;
        assert(op8(0, &rp, NULL) == 0 && !built[0] && !queues_made);
        printf("OFF\n");
        return 0;
    }
    step_ns = 40000000;   /* 40 ms: a real compile */
    orig_info = &rp; assert(op8(0, &rp, NULL) == 1); q1 = last_queue;
    step_ns = 400000;     /* 0.4 ms: a cache hit */
    orig_vd = &vd; assert(op8(1, &rp, &vd) == 1); q2 = last_queue;
    assert(queues_made == 2 && q1 != q2);
    clock_ns += 11000000000ull;   /* past the 10 s quiet period: the drained backlog is reported again */
    orig_info = &cp; assert(op8(2, &cp, NULL) == 1);
    assert(op8(3, &rp, NULL) == 0);        /* no such kind */
    assert(op8(1, &rp, NULL) == 0);        /* a vertex descriptor is missing */
    assert(built[0] == 1 && built[1] == 1 && built[2] == 1);
    assert(retains[11] == 2 && releases[11] == 2 && retains[12] == 2 && releases[12] == 2);
    assert(retains[13] == 1 && releases[13] == 1);
    assert(retains[21] == 2 && releases[21] == 2 && retains[22] == 2 && releases[22] == 2 && retains[23] == 2 && releases[23] == 2);
    assert(retains[7] == 3 && releases[7] == 3);
    assert(releases[40] == 1 && releases[41] == 1);
    for (i = 0; i < 64; i++) if (i != 40 && i != 41) assert(retains[i] == releases[i]);
    printf("ON %ld %ld %ld %ld\n", (long)madeira_pso_warm_sent, (long)madeira_pso_warm_done,
           (long)madeira_pso_warm_hits, (long)madeira_pso_warm_failed);
    return 0;
}
'''

with tempfile.TemporaryDirectory(prefix='madeira-pso-warm-c-') as directory:
    folder = Path(directory)
    (folder / 'warm.c').write_text(harness)
    exe = folder / 'warm'
    cc = os.environ.get('CC', 'clang')
    flags = [cc, '-std=gnu11', '-fblocks', '-Wall', '-Wextra', '-Wno-unused-parameter', '-Wno-unused-function',
             '-Werror', '-g', str(folder / 'warm.c'), '-o', str(exe)]
    sanitize = ['-fsanitize=address,undefined', '-fno-sanitize-recover=all']
    if subprocess.run(flags[:1] + sanitize + flags[1:], capture_output=True).returncode != 0:
        r = subprocess.run(flags, capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
    off = subprocess.run([str(exe), '0'], capture_output=True, text=True)
    assert off.returncode == 0 and off.stdout.strip() == 'OFF', off.stdout + off.stderr
    print('PASS: pso-warm unset: op 10 answers 0, no queue is made, nothing is built')
    on = subprocess.run([str(exe), '2'], capture_output=True, text=True)
    assert on.returncode == 0, on.stdout + on.stderr
    sent, done, hits, failed = map(int, on.stdout.split()[1:])
    assert (sent, done, hits, failed) == (3, 3, 1, 1), on.stdout
    assert 'pipeline cache warm-up on: 2 utility queue(s)' in on.stderr, on.stderr
    line = [l for l in on.stderr.splitlines() if 'pipelines warmed' in l]
    assert line and '1 compiled (avg 40.0 ms), 1 already in Metal\'s cache (avg 0.40 ms), 1 failed' in line[-1], on.stderr
    print('PASS: pso-warm = 2: render, render + vertex descriptor and compute reach their DXMT builder with copies, '
          'on alternating queues; built pipelines released, retains balanced, bad requests refused; '
          f'stats: {line[-1].split("madeira-bcd ")[1]}')

# --- madeira_d3d12.c --------------------------------------------------------
warm = d3d12[d3d12.index('static void mad_pso_warm('):]
warm = warm[:warm.index('\n}\n') + 3]
assert 'mad_cfg_int_pe("pso-warm", 0)' in warm and 'a.op = 10' in warm
assert re.search(r'if \(!a\.ret\) \{\s*on = 0;', warm), 'the warm-up must stop when op 10 answers 0'
assert 'mad_pso_warm(p->device_handle, &p->rp, p->has_vd ? &p->vd : NULL, p->has_vd ? 1 : 0);' in d3d12
lazy_cs = d3d12[d3d12.index('p->lazy_cs = 1; p->device_handle = d->mtl_device;'):]
lazy_cs = lazy_cs[:lazy_cs.index('return hr;')]
assert 'ci.compute_function = p->vs_fn;' in lazy_cs and 'mad_pso_warm(p->device_handle, &ci, NULL, 2);' in lazy_cs
realize_cs = d3d12[d3d12.index('static obj_handle_t mad_cpso_realize('):]
realize_cs = realize_cs[:realize_cs.index('\n}\n')]
assert 'memset(&ci, 0, sizeof ci);' in realize_cs and 'ci.compute_function = p->vs_fn;' in realize_cs
print('PASS: madeira_d3d12.c sends render pipelines at their lazy point and compute ones with mad_cpso_realize\'s '
      'descriptor, and stops when op 10 answers 0')

steps = re.findall(r'python3 (tools/patch-winemetal-[\w.-]+\.py)', workflow)
assert 'tools/patch-winemetal-pso-warm.py' in steps, 'build-ipa.yml does not run the patch'
print('PASS: build-ipa.yml runs tools/patch-winemetal-pso-warm.py')

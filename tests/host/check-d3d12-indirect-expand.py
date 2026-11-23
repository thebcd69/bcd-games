#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""ExecuteIndirect signatures with several arguments per record, drawn from the CPU's records (madeira-bcd indirect-expand,
build 687, opt-in per game); no GPU, no Wine.

Teardown (Steam 1167630, Direct3D 12) makes eleven command signatures whose records change a root constant buffer view, and
some a vertex and an index buffer too, before their draw. BCD's runtime replayed single-argument signatures only and skipped
the rest ("signature with 2 argument descs is not implemented; skipped"), so half of the scene was missing (a tester's six
sessions on build 686, 2026-11-21). With indirect-expand = 1 and an UPLOAD-heap argument buffer the records are read as the
call is recorded (a snapshot: hence opt-in) and each becomes plain calls: root views and constants, vertex and index buffers,
then the draw.

1. Source: the key indirect-expand is read once, default 0; the expansion is tried before the skip, and the skip line names why.
2. mad_indirect_expand and its helpers are cut out and compiled on the host with fakes that record every call:
   - Teardown's constant buffer view + indexed draw (stride 32): a root view then a draw per record, a zero-count record draws nothing;
   - constant buffer view + vertex buffer + index buffer + indexed draw (stride 64): the bindings precede the draw, in order;
   - a DEFAULT or CUSTOM heap argument buffer, a stride shorter than its arguments, two draws in one record, a draw that is
     not the last argument, a root constant with 0 values, an unknown argument type and the switch off are refused with a
     reason and record nothing;
   - an UPLOAD count buffer limits the records (never past MaxCommandCount); any other, or one whose offset does not fit
     (including an offset near 2^64), is ignored with one log line; the buffer's size clamps the records; more than 8192 are
     capped with a log line;
   - a root constant + dispatch signature goes through the compute setters.
3. CI runs this check.
"""
from pathlib import Path
import os
import shutil
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[2]
src = (root / "madeira-d3d12/src/pe/madeira_d3d12.c").read_text()
wf = (root / ".github/workflows/build-ipa.yml").read_text()
failures = []


def check(what, cond):
    print(("PASS: " if cond else "FAIL: ") + what)
    if not cond:
        failures.append(what)


def cut(text, a, b):
    i = text.index(a)
    return text[i:text.index(b, i)]


expand = cut(src, "static int g_indirect_expand = -1;", "/* ml889: ExecuteIndirect.")
cpu_written = cut(src, "static int mad_ug_cpu_written(const struct mad_resource *r) {", "\n}\n") + "\n}\n"
hook = cut(src, "static void STDMETHODCALLTYPE list_ExecuteIndirect(", "    switch (cs->args[0].Type) {")
check("the key is read once, default 0 (opt-in), inside the expansion",
      'g_indirect_expand = mad_cfg_int_pe("indirect-expand", 0) ? 1 : 0;' in expand and src.count('"indirect-expand"') == 1)
check("the expansion is tried before the skip, which names the reason",
      hook.index("mad_indirect_expand(This, cs, max_count, args, args_off, count_buf, count_off, &why)")
      < hook.index('is not implemented (%s); skipped') and "(madeira-bcd indirect-expand)" in hook)
check("this check runs in the workflow", "tests/host/check-d3d12-indirect-expand.py" in wf)

cc = os.environ.get("CC") or shutil.which("cc") or shutil.which("clang") or shutil.which("gcc")
if not cc:
    print("SKIP: no C compiler for the behaviour part")
else:
    fakes = r'''
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
typedef uint32_t UINT, UINT32; typedef uint64_t UINT64; typedef int32_t INT;
typedef struct ID3D12GraphicsCommandList ID3D12GraphicsCommandList; typedef struct ID3D12Resource ID3D12Resource;
typedef enum { D3D12_HEAP_TYPE_DEFAULT = 1, D3D12_HEAP_TYPE_UPLOAD = 2, D3D12_HEAP_TYPE_READBACK = 3, D3D12_HEAP_TYPE_CUSTOM = 4 } D3D12_HEAP_TYPE;
typedef enum { D3D12_INDIRECT_ARGUMENT_TYPE_DRAW = 0, D3D12_INDIRECT_ARGUMENT_TYPE_DRAW_INDEXED, D3D12_INDIRECT_ARGUMENT_TYPE_DISPATCH,
    D3D12_INDIRECT_ARGUMENT_TYPE_VERTEX_BUFFER_VIEW, D3D12_INDIRECT_ARGUMENT_TYPE_INDEX_BUFFER_VIEW, D3D12_INDIRECT_ARGUMENT_TYPE_CONSTANT,
    D3D12_INDIRECT_ARGUMENT_TYPE_CONSTANT_BUFFER_VIEW, D3D12_INDIRECT_ARGUMENT_TYPE_SHADER_RESOURCE_VIEW,
    D3D12_INDIRECT_ARGUMENT_TYPE_UNORDERED_ACCESS_VIEW, D3D12_INDIRECT_ARGUMENT_TYPE_DISPATCH_RAYS, D3D12_INDIRECT_ARGUMENT_TYPE_DISPATCH_MESH } D3D12_INDIRECT_ARGUMENT_TYPE;   /* as llvm-mingw's d3d12.h: 9 and 10 */
typedef struct { D3D12_INDIRECT_ARGUMENT_TYPE Type; union { struct { UINT Slot; } VertexBuffer;
    struct { UINT RootParameterIndex, DestOffsetIn32BitValues, Num32BitValuesToSet; } Constant;
    struct { UINT RootParameterIndex; } ConstantBufferView; struct { UINT RootParameterIndex; } ShaderResourceView;
    struct { UINT RootParameterIndex; } UnorderedAccessView; }; } D3D12_INDIRECT_ARGUMENT_DESC;
typedef struct { UINT ByteStride, NumArgumentDescs; const D3D12_INDIRECT_ARGUMENT_DESC *pArgumentDescs; UINT NodeMask; } D3D12_COMMAND_SIGNATURE_DESC;
typedef struct { UINT64 BufferLocation; UINT SizeInBytes, StrideInBytes; } D3D12_VERTEX_BUFFER_VIEW;
typedef struct { UINT64 BufferLocation; UINT SizeInBytes, Format; } D3D12_INDEX_BUFFER_VIEW;
struct mad_list { int x; };
struct mad_resource { void *cpu; UINT64 size; D3D12_HEAP_TYPE heap; };
struct mad_cmdsig { D3D12_COMMAND_SIGNATURE_DESC desc; D3D12_INDIRECT_ARGUMENT_DESC args[16]; };
static long long cfg_indirect_expand = 1;   /* the cases turn it on, as a game's file would; "off" leaves the default */
static long long mad_cfg_int_pe(const char *k, long long d) { return strcmp(k, "indirect-expand") == 0 ? cfg_indirect_expand : d; }
static char calls[8192]; static int ncalls;
static size_t ncalls_len;
static void add(const char *s) {   /* the whole text while it fits; past that only the count goes on */
    size_t n = strlen(s);
    ncalls++;
    if (ncalls_len + n + 2 > sizeof calls) return;
    memcpy(calls + ncalls_len, s, n); ncalls_len += n; calls[ncalls_len++] = ';'; calls[ncalls_len] = 0;
}
static void d3d12_log(const char *f, ...) { (void)f; add("log"); }
static void list_DrawInstanced(ID3D12GraphicsCommandList *l, UINT a, UINT b, UINT c, UINT d) { char s[96]; (void)l; snprintf(s, sizeof s, "draw(%u,%u,%u,%u)", a, b, c, d); add(s); }
static void list_DrawIndexedInstanced(ID3D12GraphicsCommandList *l, UINT a, UINT b, UINT c, INT d, UINT e) { char s[96]; (void)l; snprintf(s, sizeof s, "drawi(%u,%u,%u,%d,%u)", a, b, c, d, e); add(s); }
static void list_Dispatch(ID3D12GraphicsCommandList *l, UINT a, UINT b, UINT c) { char s[64]; (void)l; snprintf(s, sizeof s, "dispatch(%u,%u,%u)", a, b, c); add(s); }
static void list_IASetVertexBuffers(ID3D12GraphicsCommandList *l, UINT slot, UINT n, const D3D12_VERTEX_BUFFER_VIEW *v) { char s[96]; (void)l; snprintf(s, sizeof s, "vb(%u,%u,%llx,%u,%u)", slot, n, (unsigned long long)v->BufferLocation, v->SizeInBytes, v->StrideInBytes); add(s); }
static void list_IASetIndexBuffer(ID3D12GraphicsCommandList *l, const D3D12_INDEX_BUFFER_VIEW *v) { char s[96]; (void)l; snprintf(s, sizeof s, "ib(%llx,%u,%u)", (unsigned long long)v->BufferLocation, v->SizeInBytes, v->Format); add(s); }
static void list_SetGraphicsRoot32BitConstants(ID3D12GraphicsCommandList *l, UINT i, UINT n, const void *d, UINT o) { char s[96]; (void)l; snprintf(s, sizeof s, "gconst(%u,%u,%u,%u)", i, n, *(const UINT32 *)d, o); add(s); }
static void list_SetComputeRoot32BitConstants(ID3D12GraphicsCommandList *l, UINT i, UINT n, const void *d, UINT o) { char s[96]; (void)l; snprintf(s, sizeof s, "cconst(%u,%u,%u,%u)", i, n, *(const UINT32 *)d, o); add(s); }
static void mad_record_root(struct mad_list *l, UINT i, UINT64 v, int r) { char s[96]; (void)l; snprintf(s, sizeof s, "root(%u,%llx,%d)", i, (unsigned long long)v, r); add(s); }
static void mad_record_croot(struct mad_list *l, UINT i, UINT64 v, int r) { char s[96]; (void)l; snprintf(s, sizeof s, "croot(%u,%llx,%d)", i, (unsigned long long)v, r); add(s); }
'''
    harness = r'''
static void sig(struct mad_cmdsig *cs, UINT stride, int n, ...) {
    va_list ap; int i; memset(cs, 0, sizeof *cs); cs->desc.ByteStride = stride; cs->desc.NumArgumentDescs = n; cs->desc.pArgumentDescs = cs->args;
    va_start(ap, n); for (i = 0; i < n; i++) cs->args[i].Type = va_arg(ap, int); va_end(ap);
}
static void put32(unsigned char *p, UINT off, UINT32 v) { memcpy(p + off, &v, 4); }
static void put64(unsigned char *p, UINT off, UINT64 v) { memcpy(p + off, &v, 8); }
static int run(const char *mode) {
    struct mad_cmdsig cs; struct mad_resource ar = {0}, cr = {0}; static unsigned char buf[8300 * 24]; unsigned char cnt[16]; const char *why = ""; int r;
    struct mad_list l = {0}; ID3D12GraphicsCommandList *This = (ID3D12GraphicsCommandList *)&l;
    memset(buf, 0, sizeof buf); memset(cnt, 0, sizeof cnt); calls[0] = 0; ncalls = 0; ncalls_len = 0;
    ar.cpu = buf; ar.size = sizeof buf; ar.heap = D3D12_HEAP_TYPE_UPLOAD;
    if (!strcmp(mode, "teardown-cbv-drawi")) {
        /* record: CBV (8) + DRAW_INDEXED (20) in a 32-byte stride; the middle record has a zero index count */
        sig(&cs, 32, 2, D3D12_INDIRECT_ARGUMENT_TYPE_CONSTANT_BUFFER_VIEW, D3D12_INDIRECT_ARGUMENT_TYPE_DRAW_INDEXED); cs.args[0].ConstantBufferView.RootParameterIndex = 1;
        put64(buf, 0, 0xA000); put32(buf, 8, 36); put32(buf, 12, 1); put32(buf, 16, 0); put32(buf, 20, (UINT32)-4); put32(buf, 24, 0);
        put64(buf, 32, 0xB000); put32(buf, 40, 0); put32(buf, 44, 1);
        put64(buf, 64, 0xC000); put32(buf, 72, 6); put32(buf, 76, 2); put32(buf, 80, 12); put32(buf, 84, 3); put32(buf, 88, 7);
        r = mad_indirect_expand(This, &cs, 3, (ID3D12Resource *)&ar, 0, NULL, 0, &why);
        printf("%d|%s|%s\n", r, why, calls); return 0;
    }
    if (!strcmp(mode, "cbv-vb-ib-drawi")) {
        sig(&cs, 64, 4, D3D12_INDIRECT_ARGUMENT_TYPE_CONSTANT_BUFFER_VIEW, D3D12_INDIRECT_ARGUMENT_TYPE_VERTEX_BUFFER_VIEW,
            D3D12_INDIRECT_ARGUMENT_TYPE_INDEX_BUFFER_VIEW, D3D12_INDIRECT_ARGUMENT_TYPE_DRAW_INDEXED);
        cs.args[0].ConstantBufferView.RootParameterIndex = 0; cs.args[1].VertexBuffer.Slot = 2;
        put64(buf, 100, 0xD000); put64(buf, 108, 0xE000); put32(buf, 116, 480); put32(buf, 120, 24); put64(buf, 124, 0xF000); put32(buf, 132, 60); put32(buf, 136, 42);
        put32(buf, 140, 30); put32(buf, 144, 1); put32(buf, 148, 0); put32(buf, 152, 0); put32(buf, 156, 0);
        r = mad_indirect_expand(This, &cs, 1, (ID3D12Resource *)&ar, 100, NULL, 0, &why);
        printf("%d|%s|%s\n", r, why, calls); return 0;
    }
    if (!strcmp(mode, "default-heap") || !strcmp(mode, "custom-heap")) {
        sig(&cs, 32, 2, D3D12_INDIRECT_ARGUMENT_TYPE_CONSTANT_BUFFER_VIEW, D3D12_INDIRECT_ARGUMENT_TYPE_DRAW_INDEXED);
        if (!strcmp(mode, "default-heap")) { ar.heap = D3D12_HEAP_TYPE_DEFAULT; ar.cpu = NULL; } else ar.heap = D3D12_HEAP_TYPE_CUSTOM;
        r = mad_indirect_expand(This, &cs, 3, (ID3D12Resource *)&ar, 0, NULL, 0, &why);
        printf("%d|%s|%s\n", r, why, calls); return 0;
    }
    if (!strcmp(mode, "draw-not-last")) {
        sig(&cs, 28, 2, D3D12_INDIRECT_ARGUMENT_TYPE_DRAW_INDEXED, D3D12_INDIRECT_ARGUMENT_TYPE_CONSTANT_BUFFER_VIEW);
        r = mad_indirect_expand(This, &cs, 3, (ID3D12Resource *)&ar, 0, NULL, 0, &why);
        printf("%d|%s|%s\n", r, why, calls); return 0;
    }
    if (!strcmp(mode, "const-zero")) {
        sig(&cs, 16, 2, D3D12_INDIRECT_ARGUMENT_TYPE_CONSTANT, D3D12_INDIRECT_ARGUMENT_TYPE_DRAW); cs.args[0].Constant.Num32BitValuesToSet = 0;
        r = mad_indirect_expand(This, &cs, 3, (ID3D12Resource *)&ar, 0, NULL, 0, &why);
        printf("%d|%s|%s\n", r, why, calls); return 0;
    }
    if (!strcmp(mode, "cap")) {
        int i, draws = 0, logs = 0; const char *q;
        sig(&cs, 24, 2, D3D12_INDIRECT_ARGUMENT_TYPE_CONSTANT_BUFFER_VIEW, D3D12_INDIRECT_ARGUMENT_TYPE_DRAW);
        for (i = 0; i < 8300; i++) { put64(buf, i * 24, 0x1000); put32(buf, i * 24 + 8, 3); put32(buf, i * 24 + 12, 1); }
        r = mad_indirect_expand(This, &cs, 8300, (ID3D12Resource *)&ar, 0, NULL, 0, &why);
        (void)q; draws = (ncalls - 1) / 2;   /* calls[] is too short for 8192 records: count them (one root + one draw each, after one log) */
        logs = strncmp(calls, "log;", 4) == 0 && ncalls % 2 == 1;
        printf("%d|%s|draws=%d logs=%d\n", r, why, draws, logs); return 0;
    }
    if (!strcmp(mode, "short-stride")) {
        sig(&cs, 24, 2, D3D12_INDIRECT_ARGUMENT_TYPE_CONSTANT_BUFFER_VIEW, D3D12_INDIRECT_ARGUMENT_TYPE_DRAW_INDEXED);
        r = mad_indirect_expand(This, &cs, 3, (ID3D12Resource *)&ar, 0, NULL, 0, &why);
        printf("%d|%s|%s\n", r, why, calls); return 0;
    }
    if (!strcmp(mode, "two-draws")) {
        sig(&cs, 36, 2, D3D12_INDIRECT_ARGUMENT_TYPE_DRAW, D3D12_INDIRECT_ARGUMENT_TYPE_DRAW_INDEXED);
        r = mad_indirect_expand(This, &cs, 3, (ID3D12Resource *)&ar, 0, NULL, 0, &why);
        printf("%d|%s|%s\n", r, why, calls); return 0;
    }
    if (!strcmp(mode, "unknown-arg")) {
        sig(&cs, 24, 2, D3D12_INDIRECT_ARGUMENT_TYPE_DISPATCH_RAYS, D3D12_INDIRECT_ARGUMENT_TYPE_DRAW);
        r = mad_indirect_expand(This, &cs, 3, (ID3D12Resource *)&ar, 0, NULL, 0, &why);
        printf("%d|%s|%s\n", r, why, calls); return 0;
    }
    if (!strcmp(mode, "off")) {
        cfg_indirect_expand = 0;
        sig(&cs, 32, 2, D3D12_INDIRECT_ARGUMENT_TYPE_CONSTANT_BUFFER_VIEW, D3D12_INDIRECT_ARGUMENT_TYPE_DRAW_INDEXED);
        r = mad_indirect_expand(This, &cs, 3, (ID3D12Resource *)&ar, 0, NULL, 0, &why);
        printf("%d|%s|%s\n", r, why, calls); return 0;
    }
    if (!strcmp(mode, "count-cpu") || !strcmp(mode, "count-gpu") || !strcmp(mode, "count-big") || !strcmp(mode, "count-wrap")) {
        int i; UINT64 coff = 8; sig(&cs, 24, 2, D3D12_INDIRECT_ARGUMENT_TYPE_CONSTANT_BUFFER_VIEW, D3D12_INDIRECT_ARGUMENT_TYPE_DRAW);
        for (i = 0; i < 4; i++) { put64(buf, i * 24, 0x1000 + i); put32(buf, i * 24 + 8, 3); put32(buf, i * 24 + 12, 1); }
        cr.cpu = cnt; cr.size = sizeof cnt; cr.heap = !strcmp(mode, "count-gpu") ? D3D12_HEAP_TYPE_DEFAULT : D3D12_HEAP_TYPE_UPLOAD;
        put32(cnt, 8, !strcmp(mode, "count-big") ? 9 : 2);
        if (!strcmp(mode, "count-wrap")) coff = ~(UINT64)0 - 1;
        if (cr.heap == D3D12_HEAP_TYPE_DEFAULT) cr.cpu = NULL;
        r = mad_indirect_expand(This, &cs, 4, (ID3D12Resource *)&ar, 0, (ID3D12Resource *)&cr, coff, &why);
        printf("%d|%s|%s\n", r, why, calls); return 0;
    }
    if (!strcmp(mode, "clamp")) {
        int i; sig(&cs, 24, 2, D3D12_INDIRECT_ARGUMENT_TYPE_CONSTANT_BUFFER_VIEW, D3D12_INDIRECT_ARGUMENT_TYPE_DRAW);
        for (i = 0; i < 4; i++) { put64(buf, i * 24, 0x1000 + i); put32(buf, i * 24 + 8, 3); put32(buf, i * 24 + 12, 1); }
        ar.size = 24 * 2 + 10;   /* room for two whole records */
        r = mad_indirect_expand(This, &cs, 100, (ID3D12Resource *)&ar, 0, NULL, 0, &why);
        printf("%d|%s|%s\n", r, why, calls); return 0;
    }
    if (!strcmp(mode, "const-dispatch")) {
        sig(&cs, 20, 2, D3D12_INDIRECT_ARGUMENT_TYPE_CONSTANT, D3D12_INDIRECT_ARGUMENT_TYPE_DISPATCH);
        cs.args[0].Constant.RootParameterIndex = 3; cs.args[0].Constant.DestOffsetIn32BitValues = 1; cs.args[0].Constant.Num32BitValuesToSet = 2;
        put32(buf, 0, 77); put32(buf, 4, 78); put32(buf, 8, 4); put32(buf, 12, 5); put32(buf, 16, 6);
        r = mad_indirect_expand(This, &cs, 1, (ID3D12Resource *)&ar, 0, NULL, 0, &why);
        printf("%d|%s|%s\n", r, why, calls); return 0;
    }
    return 2;
}
int main(int argc, char **argv) { return argc > 1 ? run(argv[1]) : 2; }
'''
    with tempfile.TemporaryDirectory() as d:
        t = Path(d)
        (t / "h.c").write_text("#include <stdarg.h>\n" + fakes + cpu_written + expand + harness)
        # No sanitizers: on macOS 26.4 (Xcode 26.2) a binary with -fsanitize=address never leaves dyld's initializers.
        b = subprocess.run([cc, "-std=c11", "-O1", "-Wall", "-Wextra", "-Werror", "-Wno-unused-parameter", "-Wno-unused-function",
                            "-fstack-protector-all", "-o", str(t / "h"), str(t / "h.c")], capture_output=True, text=True)
        check("the expansion compiles on the host with fakes" + ("" if not b.returncode else ": " + b.stderr[-1500:]), b.returncode == 0)
        if b.returncode == 0:
            def out(mode):
                r = subprocess.run([str(t / "h"), mode], capture_output=True, text=True, timeout=60)
                return r.stdout.strip()
            check("Teardown's constant buffer view + indexed draw: a root view then a draw per record, a zero-count record draws nothing",
                  out("teardown-cbv-drawi") == "1||root(1,a000,1);drawi(36,1,0,-4,0);root(1,b000,1);root(1,c000,1);drawi(6,2,12,3,7);")
            check("constant buffer view, vertex buffer, index buffer, indexed draw: bindings before the draw, in record order",
                  out("cbv-vb-ib-drawi") == "1||root(0,d000,1);vb(2,1,e000,480,24);ib(f000,60,42);drawi(30,1,0,0,0);")
            check("a DEFAULT heap argument buffer is refused with its reason and records nothing",
                  out("default-heap") == "0|an argument buffer that is not an UPLOAD heap|")
            check("a CUSTOM heap argument buffer (the GPU can write one) is refused too",
                  out("custom-heap") == "0|an argument buffer that is not an UPLOAD heap|")
            check("a draw that is not the last argument is refused", out("draw-not-last") == "0|a draw or dispatch that is not the last argument|")
            check("a root constant with 0 values is refused with its own reason",
                  out("const-zero") == "0|a root constant argument with 0 or more than 64 values|")
            check("more than 8192 records are capped at 8192 with one log line", out("cap") == "1||draws=8192 logs=1")
            check("a count buffer value above MaxCommandCount never issues more than MaxCommandCount",
                  out("count-big") == "1||root(0,1000,1);draw(3,1,0,0);root(0,1001,1);draw(3,1,0,0);root(0,1002,1);draw(3,1,0,0);root(0,1003,1);draw(3,1,0,0);")
            check("a stride shorter than its arguments is refused", out("short-stride") == "0|a stride shorter than its arguments|")
            check("two draws in one record are refused (the first is not the last argument)", out("two-draws") == "0|a draw or dispatch that is not the last argument|")
            check("an argument type this build does not expand is refused", out("unknown-arg") == "0|an argument type this build does not expand|")
            check("with the switch off (the default) nothing is expanded", out("off") == "0|indirect-expand is off|")
            check("a CPU-written count buffer limits the records",
                  out("count-cpu") == "1||root(0,1000,1);draw(3,1,0,0);root(0,1001,1);draw(3,1,0,0);")
            all4 = "1||log;root(0,1000,1);draw(3,1,0,0);root(0,1001,1);draw(3,1,0,0);root(0,1002,1);draw(3,1,0,0);root(0,1003,1);draw(3,1,0,0);"
            check("a count buffer that is not an UPLOAD heap is ignored: every record, with one log line", out("count-gpu") == all4)
            check("a count offset near 2^64 does not wrap the bound: ignored like an unreadable one", out("count-wrap") == all4)
            check("the argument buffer's size clamps the records", out("clamp") == "1||root(0,1000,1);draw(3,1,0,0);root(0,1001,1);draw(3,1,0,0);")
            check("a root constant + dispatch signature goes through the compute setters",
                  out("const-dispatch") == "1||cconst(3,2,77,1);dispatch(4,5,6);")

if failures:
    print(f"\n{len(failures)} check(s) failed")
    sys.exit(1)
print("\nall checks passed")

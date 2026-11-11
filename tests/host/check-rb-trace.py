#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""trace-buffer-size and trace-dispatch (build 649; madeira-d3d12/src/pe/madeira_d3d12.c): diagnostics, log only, off unless set.

The Last of Us Part I's ray-cast results reach the CPU with valid hit data but unwritten guard words (the owner's logs of 2026-11-09).
trace-buffer-size follows the buffers of the listed sizes (creation, Map, copies, clears) and, when the command buffer that ran a
traced copy has finished, logs what the destination holds at +0, +428, +444 and +872 as the CPU sees it; trace-dispatch logs what
the named compute pipelines are bound to. Checks the source: both read their keys once, change nothing when unset, every hook is in
place (copy recorded and run, command buffer finished, creation, Map, UAV clear, dispatch), a probe holds references to both
resources until it is read, and the counts are capped. CI runs this check.
"""
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[2]
ok = True


def check(name, cond, detail=""):
    global ok
    print(("ok   " if cond else "FAIL ") + name)
    if not cond:
        ok = False
        if detail:
            print("     " + str(detail)[:1200])


src = (root / "madeira-d3d12/src/pe/madeira_d3d12.c").read_text()
mod = src[src.index("/* madeira-bcd (build 649): READBACK TRACE and DISPATCH TRACE"):src.index("static void mad_fault_report(obj_handle_t cb) {")]
check("the keys are read once; unset, the state is 0 and every hook returns at once",
      'mad_cfg_str_pe("trace-buffer-size", v, sizeof v);' in mod and '__atomic_store_n(&g_rbt_state, n ? 1 : 0, __ATOMIC_RELEASE);' in mod
      and 'mad_cfg_str_pe("trace-dispatch", g_tdisp, sizeof g_tdisp);' in mod
      and '__atomic_store_n(&g_tdisp_state, g_tdisp[0] ? 1 : 0, __ATOMIC_RELEASE);' in mod)
check("a buffer is followed by its creation size",
      "return r && r->buffer && mad_rbt_size(r->desc.Width);" in mod)
check("a probe takes a reference on both resources and gives them back once read; at most 64 open, 400 in all",
      "InterlockedIncrement(&dst->refs); InterlockedIncrement(&src->refs);" in mod
      and "dst->vtbl->Release((ID3D12Resource2 *)dst); src->vtbl->Release((ID3D12Resource2 *)src);" in mod and "if (i < 64 && g_rbt_seq < 400) {" in mod)
check("...and reads the destination only through its CPU pointer, inside the copied range",
      "if (!dst->cpu) snprintf(line + w, sizeof line - w, \" destination is GPU-only (the CPU cannot read it)\");" in mod
      and "if (at[k] + 16 > len) break;" in mod)
check("the dispatch trace fills the same record the GPU fault report uses; every Nth matching dispatch, up to the cap (default 1 and 6)",
      "mad_frec_fill(&r, e, c);" in mod
      and "if ((m - 1) % g_tdisp_every || (n = InterlockedIncrement(&said)) > g_tdisp_max) return;" in mod
      and "static LONG g_tdisp_max = 6, g_tdisp_every = 1;" in mod
      and 'g_tdisp_max = mad_cfg_int_pe("trace-dispatch-max", 6);' in mod and 'g_tdisp_every = mad_cfg_int_pe("trace-dispatch-every", 1);' in mod
      and "if (g_tdisp_max < 1) g_tdisp_max = 1; if (g_tdisp_max > 64) g_tdisp_max = 64;" in mod and "if (g_tdisp_every < 1) g_tdisp_every = 1;" in mod
      and "static void mad_fault_record_dispatch(struct mad_exec *e, const struct mad_cmd *c) {\n"
          "    mad_frec_fill(&g_frec[(InterlockedIncrement(&g_frec_pos) - 1) % MAD_FREC_N], e, c);\n}" in src)
snap = mod[mod.index("static void mad_tdisp_snap("):mod.index("static void mad_tdsnap_retire(")]
ret = mod[mod.index("static void mad_tdsnap_retire("):mod.index("static void mad_tdisp(struct mad_exec *e, const struct mad_cmd *c) {")]
check("build 651: a GPU-only buffer of a traced dispatch is copied before it runs (CPU-readable ones are printed as they are)",
      "if (!res || !res->buffer || res->cpu || !e->cb) return;" in snap and "bi.length = 64; bi.options = WMTResourceStorageModeShared;" in snap
      and "k.src = res->buffer; k.src_offset = off; k.dst = stage; k.dst_offset = 0; k.copy_length = len;" in snap
      and "if (off + len > res->size) len = res->size - off;" in snap and "if (vlen && vlen < len) len = vlen;" in snap)
check("...a failed staging buffer or blit gives its slot back; at most 32 open",
      "if (stage) NSObject_release(stage);" in snap and "AcquireSRWLockExclusive(&g_tdsnap_lock); g_tdsnap[i].cb = 0; ReleaseSRWLockExclusive(&g_tdsnap_lock);" in snap
      and "#define MAD_TDSNAP_N 32" in mod)
check("...logged and released when its command buffer has finished",
      "NSObject_release(stage);" in ret and "if (g_tdsnap[i].cb != cb) { ReleaseSRWLockExclusive(&g_tdsnap_lock); continue; }" in ret
      and "    mad_rbt_retire(cb);   /* madeira-bcd trace-buffer-size: what this buffer's traced copies left for the CPU */\n"
          "    mad_tdsnap_retire(cb);" in src)
disp = src[src.index("    if (g_census_on) exec_capture_cs(e, c);   /* ml920 */"):]
disp = disp[:disp.index("    if (!e->cenc) {")]
check("...the trace runs before the dispatch's compute encoder exists (a blit there would end it: ml930)",
      "if (__atomic_load_n(&g_tdisp_state, __ATOMIC_ACQUIRE) != 0) mad_tdisp(e, c);" in disp
      and src.count("mad_tdisp(e, c);") == 1)
check("...CPU-readable buffers: 64 bytes (a 56-byte constant buffer whole)",
      "for (h = 0; h < 64 && off + h + 32 <= res->size; h += 32) {" in mod)
for name, needle in (("copy recorded", "mad_rbt_copy(d, dst_off, s, src_off, len);"),
                     ("copy run", "mad_rbt_probe(e->cb, c->u.bb.dst, c->u.bb.doff, c->u.bb.src, c->u.bb.soff, c->u.bb.len);"),
                     ("command buffer finished", "    MTLCommandBuffer_waitUntilCompleted(cb);\n    mad_rbt_retire(cb);"),
                     ("creation", 'mad_rbt_note(ph ? "created (placed)" : "created (committed)", r);'),
                     ("Map", 'mad_rbt_note("Map", r);'),
                     ("UAV clear", "if (mad_rbt_res(r)) {   /* madeira-bcd trace-buffer-size */"),
                     ("dispatch", "if (__atomic_load_n(&g_tdisp_state, __ATOMIC_ACQUIRE) != 0) mad_tdisp(e, c);")):
    check("hook: %s" % name, needle in src)
ci = (root / ".github/workflows/build-ipa.yml").read_text()
check("CI runs this check", "python3 tests/host/check-rb-trace.py" in ci)
print("PASS: readback and dispatch traces" if ok else "FAILED: readback and dispatch traces")
sys.exit(0 if ok else 1)

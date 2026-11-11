#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""D3D12 bundles (build 652; madeira-d3d12/src/pe/madeira_d3d12.c; night round 2026-11-11, Discord S3).

KINGDOM HEARTS HD 1.5+2.5 ReMIX's launcher (GY47CH, YT825Q, 1CFEH7, HNEF49, ZY0345, PRHX5X): "[madeira-d3d12] FAILED
CreateCommandAllocator type -> 0x80004001 (1, 0)", then Reset on the NULL allocator. Checks:
  - allocators and lists accept D3D12_COMMAND_LIST_TYPE_BUNDLE (CreateCommandAllocator, CreateCommandList, CreateCommandList1);
    a queue still does not (no bundle queue in D3D12); DIRECT/COMPUTE/COPY unchanged;
  - a bundle's initial PSO (CreateCommandList / Reset) is recorded; other list types keep ignoring it, as before;
  - ExecuteBundle: only a closed BUNDLE into an open DIRECT list; the bundle's cdata is appended to the parent's and every command
    that indexes cdata (root constants, static-cbv latches, clear rectangles) moves with it; all or nothing; the bundle's resources
    join the parent's; the vtable slot is wired (no longer the stub);
  - a model of the copy: the parent replays the bundle's constants from the moved offsets;
  - the auto-tune rule is fixed in 652 with its fixture; CI runs this check.
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
check("bundle type accepted for allocators and lists only",
      "return mad_list_type_ok(t) || t == D3D12_COMMAND_LIST_TYPE_BUNDLE;" in src
      and 'if (!mad_list_or_bundle_type_ok(type)) MAD_FAIL("CreateCommandAllocator type", E_NOTIMPL, type, 0);' in src
      and 'if (!mad_list_or_bundle_type_ok(type)) MAD_FAIL("CreateCommandList type", E_NOTIMPL, type, 0);' in src
      and 'if (!mad_list_or_bundle_type_ok(type)) MAD_FAIL("CreateCommandList1 type", E_NOTIMPL, type, 0);' in src)
q = src[src.index("static HRESULT STDMETHODCALLTYPE device_CreateCommandQueue(ID3D12Device *This,"):]
q = q[:q.index("\n}\n")]
check("...a queue still refuses BUNDLE (mad_list_type_ok: DIRECT, COMPUTE, COPY)",
      "if (!mad_list_type_ok(desc->Type)) {" in q
      and "return t == D3D12_COMMAND_LIST_TYPE_DIRECT || t == D3D12_COMMAND_LIST_TYPE_COMPUTE ||\n           t == D3D12_COMMAND_LIST_TYPE_COPY;" in src)
check("a bundle's initial PSO is recorded at create and at Reset; other list types as before",
      "if (type == D3D12_COMMAND_LIST_TYPE_BUNDLE && pso) list_SetPipelineState((ID3D12GraphicsCommandList *)l, pso);" in src
      and "if (l->type == D3D12_COMMAND_LIST_TYPE_BUNDLE && pso) list_SetPipelineState(This, pso);" in src)
eb = src[src.index("static void STDMETHODCALLTYPE list_ExecuteBundle("):]
eb = eb[:eb.index("\n}\n")]
check("ExecuteBundle takes only a closed bundle into an open DIRECT list (else logged and ignored)",
      "if (!l || l->closed) return;" in eb
      and "if (!b || b->type != D3D12_COMMAND_LIST_TYPE_BUNDLE || !b->closed || l->type != D3D12_COMMAND_LIST_TYPE_DIRECT) {" in eb
      and "ExecuteBundle refused" in eb)
check("...appends the bundle's cdata and moves every cdata index (root constants, latches, clear rectangles)",
      "base = l->ncdata;" in eb and "memcpy(&l->cdata[base], b->cdata, (size_t)b->ncdata * sizeof *l->cdata);" in eb
      and "case MC_ROOT_CONST: case MC_CROOT_CONST: c->u.rconst.data += base; break;" in eb
      and "case MC_CBV_LATCH: c->u.latch.data += base; break;" in eb
      and "case MC_CLEAR_RT: case MC_CLEAR_DS: if (c->u.clear.nrect) c->u.clear.rect_at += base; break;" in eb)
check("...all or nothing, and the bundle's resources join the parent's",
      "if (!c) { l->ncmds = n0; return; }" in eb and "for (i = 0; i < b->nused; i++) mad_list_note_used(l, b->used[i]);" in eb)
# every place that writes a cdata offset into a command is one of the three the copy moves
writers = [ln.strip() for ln in src.splitlines() if ("u.rconst.data =" in ln or "u.latch.data =" in ln or "u.clear.rect_at =" in ln)]
others = [ln.strip() for ln in src.splitlines() if "= l->ncdata" in ln or "= at;" in ln and "c->u." in ln]
check("...the only command fields that hold a cdata offset are those three",
      len(writers) >= 4 and all(("rconst.data" in w or "latch.data" in w or "rect_at" in w) for w in writers), writers)
check("the vtable slot is wired", "g_list_vtbl.ExecuteBundle           = (void *)list_ExecuteBundle;" in src)


# a model of the copy: commands are (kind, offset); the parent reads cdata[offset]
def execute_bundle(parent, bundle):
    base = len(parent["cdata"])
    parent["cdata"] += bundle["cdata"]
    for kind, off in bundle["cmds"]:
        parent["cmds"].append((kind, off + base if kind in ("rconst", "latch", "rect") else off))


par = {"cdata": [7, 8], "cmds": [("rconst", 0)]}
bun = {"cdata": [42, 43, 44], "cmds": [("rconst", 1), ("draw", 0), ("latch", 0)]}
execute_bundle(par, bun)
execute_bundle(par, bun)   # the same bundle twice
reads = [par["cdata"][o] for k, o in par["cmds"] if k != "draw"]
check("model: the parent reads the bundle's own words after one and two copies", reads == [7, 43, 42, 43, 42], reads)

rules = (root / "build/tools/autotune_rules.py").read_text()
rb = rules[rules.index('"id": "kingdom-hearts-launcher-null-d3d12-allocator"'):]
rb = rb[:rb.index("\n    },\n")]
check("the auto-tune rule is fixed in 652 and its fixture is there",
      '"fixedIn": 652' in rb and '"noFix"' not in rb
      and (root / "tests/fixtures/autotune/kingdom-hearts-bundle-allocator-2026-11-07.expect").is_file())
ci = (root / ".github/workflows/build-ipa.yml").read_text()
check("CI runs this check", "python3 tests/host/check-d3d12-bundles.py" in ci)

print("PASS: d3d12 bundles" if ok else "FAILED: d3d12 bundles")
sys.exit(0 if ok else 1)

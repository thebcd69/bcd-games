#!/usr/bin/env python3
"""The DXMT / airconv / winemetal patch scripts apply in the workflow's order,
on an unpatched dxmt/src, and are idempotent.

build-ipa.yml runs every tools/patch-(dxmt|airconv|winemetal)-*.py in a fixed
order on the pinned dxmt submodule; a new script that only works on its own,
or an anchor that an earlier script already moved, fails the CI build. This
copies dxmt/src (or $DXMT_SRC_DIR, a directory holding an unpatched `src`),
build/madeira_cfg.h and research/remote-metal into a temporary tree, runs the
whole chain twice and checks that the second round only says "already
patched". It also checks the God of War darkening switches (2026-10-01,
docs/gow-darkening.md) landed: every converter experiment salts the shader
cache and the GPU trace hooks are in place.

SKIP (exit 0) when no dxmt sources are available (the submodule is not
checked out)."""
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

R = pathlib.Path(__file__).resolve().parents[2]
src = pathlib.Path(os.environ.get("DXMT_SRC_DIR", R / "dxmt")) / "src"
if not (src / "airconv").is_dir() or not (src / "winemetal" / "unix").is_dir():
    print(f"SKIP: no dxmt sources at {src} (check out the submodule or set DXMT_SRC_DIR)")
    sys.exit(0)

wf = (R / ".github/workflows/build-ipa.yml").read_text()
scripts = re.findall(r"python3 (tools/patch-(?:dxmt|airconv|winemetal)-[\w.-]+\.py)", wf)
ok = True
with tempfile.TemporaryDirectory() as tmp:
    t = pathlib.Path(tmp)
    shutil.copytree(src, t / "dxmt/src")
    (t / "build").mkdir()
    shutil.copy(R / "build/madeira_cfg.h", t / "build/madeira_cfg.h")
    shutil.copytree(R / "research/remote-metal", t / "research/remote-metal")
    for rnd in (1, 2):
        for s in scripts:
            p = subprocess.run([sys.executable, str(R / s)], cwd=t, capture_output=True, text=True)
            out = (p.stdout + p.stderr).strip()
            if p.returncode != 0:
                print(f"FAIL round {rnd} {s}:\n{out}")
                ok = False
            elif rnd == 2 and re.search(r"(?<!already )patched", out.replace("already patched", "")):
                print(f"FAIL round 2 {s} patched again (not idempotent): {out}")
                ok = False
    if ok:
        wm = (t / "dxmt/src/winemetal/unix/winemetal_unix.c").read_text()
        header = (t / "dxmt/src/dxmt/dxmt_presenter.hpp").read_text()
        cache = (t / "dxmt/src/winemetal/unix/cache.c").read_text()
        conv = (t / "dxmt/src/airconv/nt/dxbc_converter_base.cpp").read_text()
        ctx = (t / "dxmt/src/airconv/airconv_context.cpp").read_text()
        dxc = (t / "dxmt/src/airconv/dxbc_converter.cpp").read_text()
        ctl = wm[wm.index("static NTSTATUS _madeira_ctl(void *args) {"):]
        ctl_ops = re.findall(r"^\s*case (\d+):", ctl[:ctl.index("\n}\n")], re.M)
        for what, cond in [
            ("HDR metadata and presenter members are declared once across both patch rounds",
             all(header.count(field) == 1 for field in ("float auto_hdr_peak;", "uint32_t auto_hdr_flags;",
                    "uint32_t auto_hdr_revision;", "bool auto_hdr_requested_ = false;", "float auto_hdr_peak_ = 2.0f;"))),
            ("cache salt for every converter experiment",
             all(n in cache for n in ("MADEIRA_TGSM_SYNC", "MADEIRA_SAMPLE_L_BIAS", "MADEIRA_PRECISE_MATH",
                                      "MADEIRA_BOUNDS_EXTRA", "MADEIRA_PS_CLAMP"))
             # dxmt db546ee made ld bounds unconditional and dropped its switch; a
             # switch that still exists in the converter must salt the cache.
             and ("MADEIRA_LD_BOUNDS" in cache or "MADEIRA_LD_BOUNDS" not in conv + ctx + dxc)),
            ("simdgroup barrier pass registered behind MADEIRA_TGSM_SYNC",
             "MadeiraSimdgroupImplicitMemBarrierPass()" in ctx and "madeira_gow::tgsm_sync()" in ctx),
            ("sample_l adds the sampler bias behind MADEIRA_SAMPLE_L_BIAS",
             "LOD = ir.CreateFAdd(LOD, Sampler->Bias);" in conv),
            ("typed UAV atomics guarded behind MADEIRA_BOUNDS_EXTRA", conv.count("madeira_guarded(air, ir, MadeiraOk") == 2),
            ("DXBC dump + registry for the frame trace",
             "madeira_airconv_dump_function" in dxc and "madeira_shader_dump(pBytecode" in dxc and "madeira_reg_remove(" in dxc),
            ("GPU trace hooks: present, commit, encoders, textures, PSOs",
             all(h in wm for h in ("madeira_gt_note_present(params->handle)", "madeira_gt_after_commit(params->handle)",
                                   "madeira_gt_walk(0,", "madeira_gt_walk(1,", "madeira_gt_walk(2,",
                                   "madeira_gt_note_texture(ret)", "madeira_gt_end_encoder(params->handle)"))
             and wm.count("madeira_gt_note_pso(params->ret_pso") == 3),
            ("MADEIRA_RP_LOAD and MADEIRA_BORDER experiments", "madeira_rp_load_on()" in wm and "madeira_note_border_sampler(info, sampler_desc)" in wm),
            # build 452: two scripts each added a case 8 to the MadeiraCtl switch
            ("every MadeiraCtl op (_madeira_ctl case value) is used once", len(ctl_ops) == len(set(ctl_ops))),
        ]:
            print(("ok   " if cond else "FAIL ") + what)
            ok &= cond
print(f"{len(scripts)} patch scripts in workflow order, twice")
print("PASS" if ok else "FAILED")
sys.exit(0 if ok else 1)

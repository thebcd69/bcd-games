#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""Four one-line fixes of upstream DXMT in BCD's fork (upstream merge 2026-11-14), each a line that was wrong as written.

  32c30a88  Direct3D 10 DrawIndexedInstanced handed the context StartIndexLocation where StartInstanceLocation belongs: a draw with
            a start index and instance 0 drew from the wrong instance.
  e4f868dc  Direct3D 10 IAGetIndexBuffer, with no buffer bound, assigned null to its own parameter and left the caller's pointer
            as it was (uninitialised memory for a caller that did not clear it).
  aeb5fa67  The restore of a context's encoding state bound the OUTPUT MERGER's UAVs to the compute stage and never the compute
            stage's own.
  6251cffd  An anisotropic sampler passed MaxAnisotropy to Metal as given; Metal takes 1 to 16.
None has a switch: nothing that is right today takes another path (the start instance is passed through, a null is written where the
caller reads, the compute loop walks the compute list, a value inside 1..16 is unchanged). This reads dxmt/src (or $DXMT_SRC_DIR),
pristine or after the workflow's patches. They reach the 32-bit d3d11.dll / d3d10 and the 64-bit d3d11-src.dll.
SKIP (exit 0) without the dxmt sources."""
import os
import pathlib
import re
import sys

R = pathlib.Path(__file__).resolve().parents[2]
S = pathlib.Path(os.environ.get("DXMT_SRC_DIR", R / "dxmt")) / "src"
if not (S / "d3d10/d3d10_device.cpp").exists():
    print(f"SKIP: no dxmt sources at {S}")
    sys.exit(0)
ok = True


def check(name, cond):
    global ok
    print(("ok   " if cond else "FAIL ") + name)
    ok &= bool(cond)


check("CI runs this check", "python3 tests/host/check-dxmt-upstream-fixes.py" in (R / ".github/workflows/build-ipa.yml").read_text())
d10 = (S / "d3d10/d3d10_device.cpp").read_text()
def cut(text, start, end, what):
    a = text.find(start)
    b = text.find(end, a + len(start)) if a >= 0 else -1
    if a < 0 or b < 0:
        check(f"{what}: the code is where this check expects it", False)
        return ""
    return text[a:b]


def in_order(text, *needles):
    pos = -1
    for n in needles:
        pos = text.find(n, pos + 1)
        if pos < 0:
            return False
    return True


fn = cut(d10, "MTLD3D10Device::DrawIndexedInstanced(", "\n}\n", "Direct3D 10 DrawIndexedInstanced")
check("DrawIndexedInstanced passes its five parameters in order, the start instance last",
      re.search(r"d3d11_context_->DrawIndexedInstanced\(\s*IndexCountPerInstance, InstanceCount, StartIndexLocation, BaseVertexLocation, "
                r"StartInstanceLocation\s*\);", fn) is not None)
fn = cut(d10, "MTLD3D10Device::IAGetIndexBuffer(", "\n}\n", "Direct3D 10 IAGetIndexBuffer")
check("IAGetIndexBuffer writes the null through the pointer, inside the test that the pointer is there",
      "      *pIndexBuffer = nullptr;" in fn and "      pIndexBuffer = nullptr;" not in fn
      and in_order(fn, "if (pIndexBuffer) {", "*pIndexBuffer = nullptr;"))
ci = (S / "d3d11/d3d11_context_impl.cpp").read_text()
om = "for (const auto &[slot, entry] : state_.OutputMerger.UAVs) {"
cs = "for (const auto &[slot, entry] : state_.ComputeStageUAV.UAVs) {"
region = cut(ci, "RestoreEncodingContextStageState<PipelineStage::Compute>();", "\n  }\n", "the state restore")
check("the state restore binds the output merger's UAVs to the pixel stage and the compute stage's to the compute stage",
      region.count(om) == 1 and region.count(cs) == 1 and in_order(region, om, "bindOutputBuffer<PipelineStage::Pixel>", cs,
                                                                     "bindOutputBuffer<PipelineStage::Compute>")
      and "PipelineStage::Compute>" not in region[region.find(om):region.find(cs)])
so = (S / "d3d11/d3d11_state_object.cpp").read_text()
check("an anisotropic sampler's MaxAnisotropy is clamped to 1..16, the only place the description's value is used",
      so.count("info.max_anisotroy = std::clamp(desc.MaxAnisotropy, 1u, 16u);") == 1 and "info.max_anisotroy = desc.MaxAnisotropy;" not in so)
if not ok:
    print("FAILED: dxmt-upstream-fixes")
    sys.exit(1)
print("PASS: the four upstream DXMT one-line fixes are in place")

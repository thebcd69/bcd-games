#!/usr/bin/env python3
"""heap-untracked (opt-in, bcd.cfg / a game's config, default 0):
tools/patch-winemetal-heap-untracked.py makes winemetal's
_MTLDevice_newPlacementHeap create madeira-d3d12's placement heaps with
MTLHazardTrackingModeUntracked when the key is set; Tracked otherwise.

Checks, on a copy of dxmt's winemetal_unix.c (the patch is applied there when
the file does not carry it yet; SKIP without the submodule):
  - the patch lands inside _MTLDevice_newPlacementHeap, replaces the one
    hard-coded Tracked line, and is idempotent;
  - the key is read once with default 0; off keeps Tracked; on picks
    Untracked; the [heap-untracked] lines only print when on;
  - madeira-d3d12's two placement-heap sites still ask for private heaps;
  - CI runs the patch among the winemetal patches and this check; the catalog
    documents the key.
"""
import os, pathlib, re, shutil, subprocess, sys, tempfile

R = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = R / "tools/patch-winemetal-heap-untracked.py"
src_dir = pathlib.Path(os.environ.get("DXMT_SRC_DIR", R / "dxmt")) / "src"
wm = src_dir / "winemetal/unix/winemetal_unix.c"
ok = True


def check(what, cond):
    global ok
    print(("ok   " if cond else "FAIL ") + what)
    ok &= bool(cond)


wf = (R / ".github/workflows/build-ipa.yml").read_text()
steps = re.findall(r"python3 (tools/patch-(?:dxmt|airconv|winemetal)-[\w.-]+\.py)", wf)
check("CI applies the patch with the winemetal patches, after rp-store",
      "tools/patch-winemetal-heap-untracked.py" in steps and
      steps.index("tools/patch-winemetal-heap-untracked.py") > steps.index("tools/patch-winemetal-rp-store.py"))
check("CI runs this check", "python3 tests/host/check-heap-untracked.py" in wf)
cat = (R / "app/Madeira/ConfigCatalog.generated.swift").read_text()
check("catalog: heap-untracked, bool, default 0",
      'ConfigOption(key: "heap-untracked", title: "Placement heaps without Metal hazard tracking", kind: .bool, defaultValue: "0"' in cat)
d3d12 = (R / "madeira-d3d12/src/pe/madeira_d3d12.c").read_text()
check("madeira-d3d12's two placement-heap sites, both private",
      d3d12.count("MTLDevice_newPlacementHeap(") == 2 and
      d3d12.count("MTLDevice_newPlacementHeap(d->mtl_device, hs, WMTResourceStorageModePrivate)") == 1 and
      d3d12.count("MTLDevice_newPlacementHeap(hd->mtl_device, want, WMTResourceStorageModePrivate)") == 1)   # round 626: want = SizeInBytes + d3d12-heap-va-align's boundary (0 when off)

if not wm.is_file():
    print(f"SKIP: no winemetal sources at {wm}")
    sys.exit(0 if ok else 1)
with tempfile.TemporaryDirectory() as tmp:
    t = pathlib.Path(tmp)
    (t / "dxmt/src/winemetal/unix").mkdir(parents=True)
    shutil.copy(wm, t / "dxmt/src/winemetal/unix/winemetal_unix.c")
    runs = [subprocess.run([sys.executable, str(SCRIPT)], cwd=t, capture_output=True, text=True) for _ in range(2)]
    check("applies (or finds itself applied), then says already patched",
          runs[0].returncode == 0 and runs[1].returncode == 0 and "already patched" in runs[1].stdout)
    out = (t / "dxmt/src/winemetal/unix/winemetal_unix.c").read_text()
    fn = out[out.index("_MTLDevice_newPlacementHeap(void *obj) {"):]
    fn = fn[:fn.index("\n}\n")]
    check("inside _MTLDevice_newPlacementHeap: no hard-coded Tracked line left",
          "hd.hazardTrackingMode = MTLHazardTrackingModeTracked;" not in fn and "madeira-bcd: heap-untracked" in fn)
    check("key read once, default 0; off = Tracked, on = Untracked",
          'if (untracked < 0) untracked = madeira_cfg_int("heap-untracked", 0) > 0;' in fn and
          "hd.hazardTrackingMode = untracked ? MTLHazardTrackingModeUntracked : MTLHazardTrackingModeTracked;" in fn)
    check("the line prints only when on", "if (untracked && said++ < 3)" in fn)
    check("the heap is still made from the descriptor right after",
          fn.index("hd.hazardTrackingMode = untracked ?") < fn.index("newHeapWithDescriptor:hd]"))
    # a broken anchor fails by name
    (t / "dxmt/src/winemetal/unix/winemetal_unix.c").write_text("int nothing_here;\n")
    r = subprocess.run([sys.executable, str(SCRIPT)], cwd=t, capture_output=True, text=True)
    check("a moved anchor fails by name", r.returncode != 0 and "anchor not found" in (r.stdout + r.stderr))
sys.exit(0 if ok else 1)

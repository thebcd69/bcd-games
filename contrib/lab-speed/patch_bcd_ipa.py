#!/usr/bin/env python3
"""Add the Mac lab's findings and the KosmicKrisp compile-retry shim (kk-retry/out) to a BCD IPA as built-in recommendation rules (keeps the list version,
so BCD's next newer online list replaces it). Usage: patch_bcd_ipa.py IN.ipa OUT.ipa"""
import json, sys, zipfile, os
RULES = [{
  "id": "dragons-dogma-2", "title": "Dragon's Dogma 2 (Steam)", "version": 1,
  "match": {"steamAppIDs": [2054970], "steamFolders": ["dragons dogma 2"], "programs": [{"name": "dd2.exe", "steam": True}]},
  "config": [
    "# Starting point, not yet tested on a device: the lines BCD's own RE Engine rules share",
    "# (Resident Evil Requiem, PRAGMATA, Onimusha: Way of the Sword) plus the memory settings that kept",
    "# the Metal compiler service alive for Enshrouded on a 16 GB iPad.",
    "env.MADEIRA_EC_HOOK_TRACE = 0",
    "exe-window-mb = 640",
    "env.MADEIRA_X64_IMAGE_NOCOPY = 1",
    "d3d12-pso-cached-blob = 1",
    "env.MADEIRA_KUSER_KEEP_REGS = 1",
    "# Report an AMD card: under Wine on Linux DD2 crashes in NVIDIA's low-latency (Reflex,",
    "# VK_NV_low_latency2) path; the known workaround hides the NVIDIA GPU.",
    "gpu-card = amd",
    "mempool-mb = 2048",
    "env.MADEIRA_POOL_MARGIN_MB = 3072",
    "swap-mb = 4096",
    "swap-mode = 2",
    "# Mods: REFramework (dinput8.dll) hung DD2 on the Mac; remove it if the game hangs at start.",
  ],
  "notes": ["Mac lab 2026-11-09..11: DD2 runs on CrossOver/D3DMetal; those settings do not apply to BCD."],
}, {
  "id": "enshrouded-steam", "title": "Enshrouded (Steam)", "version": 1,
  "match": {"steamAppIDs": [1203620], "steamFolders": ["enshrouded"], "programs": [{"name": "enshrouded.exe", "steam": True}]},
  "config": [
    "# Enshrouded (Vulkan, KosmicKrisp). From BCD 0.1.665 logs on a 16 GB iPad (2026-11-16): with 6 GB of helper",
    "# memory on top of the game's ~10.4 GB, iOS starves and kills the Metal compiler service, so shader",
    "# compiling sits at 0% and the game then aborts on a failed compile. Keep RAM free for the compiler.",
    "mempool-mb = 2048",
    "env.MADEIRA_POOL_MARGIN_MB = 3072",
    "swap-mb = 4096",
    "swap-mode = 2",
    "vram-mb = 4096",
    "# The kk-retry shim (gl/libvulkan_kosmickrisp.dylib) retries compiles the service drops, runs at most",
    "# 4 at once and raises the shader cache to 4G. Let the first full shader compile finish in front.",
  ],
  "notes": ["BCD-Enshrouded-crash-fix.txt"],
}, {
  "id": "vortex-mods", "title": "Vortex (Nexus Mods)", "version": 1,
  "match": {"programs": [{"name": "vortex.exe", "steam": False}]},
  "config": [
    "# Settings from the Mac lab (Vortex 2.10.1 fully working on NotProton and CrossOver Preview, 2026-11-12).",
    "# Electron needs software rendering under Wine; GPU paths crash or draw nothing.",
    "env.MADEIRA_CHILD_ARGS = Vortex.exe --no-sandbox --disable-gpu --disable-gpu-compositing --in-process-gpu --use-angle=swiftshader --disable-software-rasterizer",
    "# Use Hardlink Deployment, never Symlink: Wine fakes symlinks as 0-byte files the game cannot read.",
    "# Put the staging folder on the same drive as the game, or Vortex hides Hardlink Deployment.",
  ],
  "notes": ["Mac lab, 2026-11-12. Nexus sign-in is done by the user."],
}]
src = sys.argv[1]
import plistlib, datetime
with zipfile.ZipFile(src) as _z:
    _p = next(n for n in _z.namelist() if n.endswith(".app/Info.plist") and n.count("/") == 2)
    _v = plistlib.loads(_z.read(_p)).get("CFBundleShortVersionString", "x")
if len(sys.argv) > 2:
    dst = sys.argv[2]
else:
    dst = os.path.join(os.path.dirname(os.path.abspath(src)), f"BCD-{_v}-lab-max-{datetime.date.today():%Y%m%d}.ipa")
import copy, os
KK = os.path.join(os.path.dirname(os.path.abspath(__file__)), "kk-retry/out")
KK_LIB = ".app/gl/libvulkan_kosmickrisp.dylib"
MVK_OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mvk-out/libMoltenVK.dylib")  # patched (nivogli) MoltenVK for iOS
import subprocess, tempfile
def old_sdk(data, name):
    """BCD builds after 0.1.665 link the iOS 27 SDK but have no UIScene manifest: iOS 27 then leaves the app on a black
    screen (Apple requires the UIScene lifecycle for apps built with the iOS 27 SDK). Recording SDK 26.2 in LC_BUILD_VERSION
    keeps the legacy lifecycle that 0.1.665 (built with 26.2) ran with."""
    with tempfile.TemporaryDirectory() as t:
        src_, out = os.path.join(t, "in"), os.path.join(t, "out")
        open(src_, "wb").write(data)
        info = subprocess.run(["otool", "-l", src_], capture_output=True, text=True).stdout
        if "sdk 27" not in info and "sdk 28" not in info:
            return data
        subprocess.run(["vtool", "-set-build-version", "ios", "18.0", "26.2", "-replace", "-output", out, src_], check=True)
        print("patched", name, "-> LC_BUILD_VERSION sdk 26.2 (legacy app lifecycle)")
        return open(out, "rb").read()
def no_backdeploy(data, name):
    """Swift back-deployment thunks (symbols ending in Twb) call the OS's own API when _stdlib_isOSVersionAtLeast says the
    OS is new enough. iPadOS 27.2 beta 24B5089g reports 27.2 but lacks Foundation's Date.<= (weak-linked to 0), so
    0.1.712 jumped to address 0 while checking the Steam sign-in, the attached JIT debugger held the fault, and the app
    stayed a black screen. NOP each thunk's `tbnz w0, #0` so it always uses its own fallback code."""
    import struct
    with tempfile.TemporaryDirectory() as t:
        f = os.path.join(t, "bin"); open(f, "wb").write(data)
        addr = {}
        for l in subprocess.run(["nm", f], capture_output=True, text=True).stdout.splitlines():
            q = l.split()
            if len(q) == 3: addr[q[2]] = int(q[0], 16)
    chk = addr.get("_$ss31_stdlib_isOSVersionAtLeast_AEICyBi1_Bw_BwBwtF")
    if not chk: return data
    data = bytearray(data); n = 0
    for sym, a in addr.items():
        if not sym.endswith("Twb"): continue
        off = a - 0x100000000; prev = False
        for i in range(0, 200, 4):
            ins = struct.unpack_from("<I", data, off + i)[0]
            if (ins >> 26) == 0x25:
                imm = ins & 0x3FFFFFF; imm = imm - (1 << 26) if imm & (1 << 25) else imm
                prev = (a + i + imm * 4) == chk; continue
            if prev and (ins & 0xFFF8001F) == 0x37000000:
                struct.pack_into("<I", data, off + i, 0xD503201F); n += 1; break
            prev = False
    print("patched", name, f"-> {n} back-deployment thunks use their fallback")
    return bytes(data)
FASTDIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fast")
FAST = {   # path inside the .app -> file in fast/
    "arm64ec-windows/xtajit64.dll": "xtajit64.dll", "arm64ec-windows/xtajit64-avx.dll": "xtajit64.dll",
    "aarch64-windows/xtajit.dll": "xtajit.dll",
    "arm64ec-windows/madeira_d3d12.dll": "madeira_d3d12.dll", "arm64ec-windows/d3d12.dll": "madeira_d3d12.dll",
    "arm64ec-windows/d3d11-src.dll": "d3d11-src.dll", "arm64ec-windows/dxgi-src.dll": "dxgi-src.dll",
    "gl/libMoltenVK.dylib": "libMoltenVK.dylib", "gl/libMoltenVK_patched.dylib": "libMoltenVK_patched.dylib",
}
LOGLIB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "launchlog/libbcdlaunchlog.dylib")
def add_logger(data, name):
    """Lab builds load a launch logger first: Documents/BCD-launch-log.txt (Files > On My iPad > BCD)."""
    sys.path.insert(0, os.path.dirname(LOGLIB)); import insert_dylib
    print("patched", name, "-> loads @executable_path/Frameworks/libbcdlaunchlog.dylib")
    return insert_dylib.insert(data, "@executable_path/Frameworks/libbcdlaunchlog.dylib")
with zipfile.ZipFile(src) as zi:
    names = zi.namelist()
# BCD 0.1.668+ ships kk-retry inside its own KosmicKrisp and 0.1.712 ships the nivogli MoltenVK as gl/libMoltenVK_patched.dylib
# (`vulkan = moltenvk-patched`). Swapping in the 0.1.665-era binaries then breaks the newer build (ABI mismatch), so only the
# rules are patched there; Enshrouded uses the patched MoltenVK route, which is the one that ran on the user's iPad.
UPSTREAM_FIXES = any(n.endswith(".app/gl/libMoltenVK_patched.dylib") for n in names)
if UPSTREAM_FIXES:
    RULES = [r for r in RULES if r["id"] != "enshrouded-steam"]
def fix_enshrouded(rules):
    for r in rules:
        if r["id"] == "enshrouded":
            out = []
            for c in r["config"]:
                line = c["line"] if isinstance(c, dict) else c
                if line.startswith("vulkan ="):
                    c = {"line": "vulkan = moltenvk-patched", "minBuild": 712} if isinstance(c, dict) else "vulkan = moltenvk-patched"
                out.append(c)
            if not any((c["line"] if isinstance(c, dict) else c).startswith("vulkan =") for c in out):
                out.append({"line": "vulkan = moltenvk-patched", "minBuild": 712})
            r["config"] = out
            r["version"] = r.get("version", 1) + 100   # stay ahead of the online list's copy
            r.setdefault("notes", []).append("Mac lab patch (2026-11-24): the user's iPad (M4 16 GB) ran Enshrouded on the patched (nivogli) MoltenVK; KosmicKrisp stops after 3-8 min (exit 42).")
# Speed build: the recommended lists' diagnostic-only lines (each documented "log only" / "changes nothing else" in BCD's
# config catalog or patch notes) cost CPU and I/O on every start; drop them. Wine debug channels keep err/fixme filters only.
DIAG_KEYS = {"d3d12-caps-log", "dxil-dump", "view-census", "trace-buffer-size", "trace-dispatch", "trace-dispatch-cb-offset",
             "trace-dispatch-every", "trace-dispatch-max", "replay-split",
             "env.DXMT_DISPLAY_MODE_STATS", "env.DXMT_DUMP_D9_SHADER", "env.MADEIRA_BAND_CENSUS", "env.MADEIRA_DEVICE_STATS",
             "env.MADEIRA_DIALOG_CODE_DUMP", "env.MADEIRA_FILE_TRACE", "env.MADEIRA_FILE_TRACE_LIMIT", "env.MADEIRA_FRAME_STATS",
             "env.MADEIRA_GPU_TRACE", "env.MADEIRA_GUEST_LOG", "env.MADEIRA_GUEST_LOG_LIMIT", "env.MADEIRA_HLSL_FAIL_DUMP",
             "env.MADEIRA_JUMBO_CODE_DUMP", "env.MADEIRA_REG_TRACE", "env.MADEIRA_X86_FAULT_DUMP", "env.MADEIRA_SPIN_DUMP_REGS"}
def speed_rules(rules):
    dropped = changed = 0
    def fix(lst):
        nonlocal dropped
        out = []
        for c in lst:
            line = c["line"] if isinstance(c, dict) else c
            k = line.split("=", 1)[0].strip() if "=" in line and not line.lstrip().startswith("#") else None
            if k in DIAG_KEYS: dropped += 1; continue
            if k == "env.WINEDEBUG":
                v = ",".join(t for t in line.split("=", 1)[1].strip().split(",") if not (t.startswith("+") or t.startswith("trace+") or t.startswith("warn+") or t.startswith("fixme+")))
                line2 = f"env.WINEDEBUG = {v}" if v else None
                if line2 != line.strip():
                    dropped += 1
                    if not line2: continue
                    c = dict(c, line=line2) if isinstance(c, dict) else line2
            out.append(c)
        return out
    for r in rules:
        before = dropped
        r["config"] = fix(r["config"])
        if "smallRAMConfig" in r: r["smallRAMConfig"] = fix(r["smallRAMConfig"])
        if dropped != before:
            changed += 1
            r["version"] = r.get("version", 1) + 100   # Reset to Recommended picks the lean list up
            r.setdefault("notes", []).append("BCD Lab Speed: diagnostic-only log lines removed from this list.")
    print(f"speed: {dropped} diagnostic lines dropped from {changed} rules")
with zipfile.ZipFile(src) as zi, zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as zo:
    for info in zi.infolist():
        data = zi.read(info.filename)
        if info.filename.endswith(".app/recommendations.json") and info.filename.count("/") == 2:
            d = json.loads(data)
            ids = {r["id"] for r in RULES}
            d["rules"] = [r for r in d["rules"] if r["id"] not in ids] + RULES
            if UPSTREAM_FIXES: fix_enshrouded(d["rules"])
            speed_rules(d["rules"])
            data = json.dumps(d, indent=1, ensure_ascii=False).encode()
            print("patched", info.filename, "rules:", len(d["rules"]), "version:", d["version"])
        if info.filename.endswith(".app/gl/libMoltenVK.dylib") and info.filename.count("/") == 3 and os.path.exists(MVK_OUT) and not UPSTREAM_FIXES:
            data = open(MVK_OUT, "rb").read(); print("patched", info.filename, "-> patched MoltenVK")
        if info.filename.endswith(".app/gl/libMoltenVK.version") and os.path.exists(MVK_OUT) and not UPSTREAM_FIXES:
            data = b"nivogli enshrouded fork + upstream fixes (patched for Enshrouded, 2026-11-16)\n"
        if info.filename.endswith(KK_LIB) and info.filename.count("/") == 3 and not UPSTREAM_FIXES:
            real = copy.copy(info); real.filename = info.filename.replace(".dylib", "_real.dylib")
            zo.writestr(real, open(os.path.join(KK, "libvulkan_kosmickrisp_real.dylib"), "rb").read(), compress_type=zipfile.ZIP_DEFLATED)
            data = open(os.path.join(KK, "libvulkan_kosmickrisp.dylib"), "rb").read()
            print("patched", info.filename, "-> kk-retry shim + _real")
        # Speed rebuilds (fast/): BCD's own build scripts and recipes at 0.1.712, with -mcpu=apple-m4 (and -O3 where it was -O2);
        # only for the release they were built from, since a newer BCD ships newer code in these files.
        rel = info.filename.split(".app/", 1)[-1]
        if _v == "0.1.712" and rel in FAST and os.path.exists(os.path.join(FASTDIR, FAST[rel])):
            data = open(os.path.join(FASTDIR, FAST[rel]), "rb").read(); print("patched", rel, "-> M4-tuned rebuild")
        if info.filename.endswith(".app/Info.plist") and info.filename.count("/") == 2:
            import plistlib, datetime
            pl = plistlib.loads(data)
            pl["CFBundleDisplayName"] = f"BCD Lab Max {pl.get('CFBundleVersion', '')}".strip()   # home-screen name marks the modified build
            data = plistlib.dumps(pl, fmt=plistlib.FMT_BINARY)
            print("patched", info.filename, "-> display name", pl["CFBundleDisplayName"])
        if info.filename.endswith(".app/Madeira") and info.filename.count("/") == 2:
            data = no_backdeploy(data, info.filename)
            data = old_sdk(data, info.filename)
            if os.path.exists(LOGLIB):
                data = add_logger(data, info.filename)
                lib = copy.copy(info); lib.filename = info.filename.rsplit("/", 1)[0] + "/Frameworks/libbcdlaunchlog.dylib"
                lib.external_attr = 0o100755 << 16
                zo.writestr(lib, open(LOGLIB, "rb").read(), compress_type=zipfile.ZIP_DEFLATED)
        zo.writestr(info, data, compress_type=zipfile.ZIP_STORED if info.compress_type == 0 else zipfile.ZIP_DEFLATED)

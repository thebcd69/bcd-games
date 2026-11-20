#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""Every piece of BCD's Windows Vulkan is in the package before it is archived (modelled on tools/check-opengl-shipped.py).

A game whose file has `vulkan = moltenvk` runs Wine's winevulkan.dll and vulkan-1.dll (the bundle's vulkan/arm64ec-windows folder; vulkan/i386-windows
for a 32-bit program), the unix side in libntdll_unix.a (winevulkan_unix_call_funcs, winevulkan_unix_call_wow64_funcs) and libwin32u_unix.a
(madeira_ios_VulkanInit, the win32u Vulkan layer), and MoltenVK (gl/libMoltenVK.dylib; the patched second MoltenVK, gl/libMoltenVK_patched.dylib, and
KosmicKrisp are optional and checked whole when they are there).
A bundle that lacks a piece is refused here instead of being found on a phone. The farm's stand-in vulkan-1.dll must stay: every other session keeps it.
Run from the repository root after the build steps (CI: "Verify the Windows Vulkan pieces").
"""
import shutil, struct, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app" / "Madeira"
errors = []
# An ARM64EC DLL carries Machine = AMD64 (0x8664) in its COFF header, and the ARM64EC metadata in its load config (every DLL of the farm does);
# llvm-readobj prints it as 0xA641. Both words are accepted; an ARM64 (0xAA64) or i386 (0x14c) build would be wrong.
ARM64EC_OK = (0x8664, 0xA641)


def machine(p):
    d = p.read_bytes()
    pe = struct.unpack_from("<I", d, 0x3C)[0]
    return struct.unpack_from("<H", d, pe + 4)[0]


for name, floor in (("winevulkan.dll", 500_000), ("vulkan-1.dll", 300_000)):
    p = APP / "vulkan" / "arm64ec-windows" / name
    if not p.is_file():
        errors.append(f"app/Madeira/vulkan/arm64ec-windows/{name} is missing: DEST=app/Madeira/vulkan/arm64ec-windows build/wine-pe/build-modules.sh winevulkan vulkan-1 did not run or failed")
    elif machine(p) not in ARM64EC_OK:
        errors.append(f"{p.relative_to(ROOT)}: machine {machine(p):#x}, want an ARM64EC image (COFF machine 0x8664)")
    elif p.stat().st_size < floor:
        errors.append(f"{p.relative_to(ROOT)} is only {p.stat().st_size} bytes (Wine's is about 0.9 MB / 0.6 MB)")

# Vulkan for 32-bit programs (build 660): the same two modules for i386, wanted whenever the package can run a 32-bit program at all
# (the i386 farm is there; its build may fail without stopping the IPA, and then nothing 32-bit runs anyway). A session with the key
# links them into syswow64; a package without them would let a 32-bit game's key do nothing but log.
if (APP / "i386-windows" / "ntdll.dll").is_file():
    for name, floor in (("winevulkan.dll", 200_000), ("vulkan-1.dll", 30_000)):
        p = APP / "vulkan" / "i386-windows" / name
        if not p.is_file():
            errors.append(f"app/Madeira/vulkan/i386-windows/{name} is missing: tools/build-vulkan-dlls.sh did not build the i386 set (wine/build-i386)")
        elif machine(p) != 0x14c:
            errors.append(f"{p.relative_to(ROOT)}: machine {machine(p):#x}, want an i386 image (0x14c)")
        elif p.stat().st_size < floor:
            errors.append(f"{p.relative_to(ROOT)} is only {p.stat().st_size} bytes (Wine's is about 380 KB / 49 KB)")
    for name in ("winevulkan.dll", "vulkan-1.dll"):
        if (APP / "i386-windows" / name).exists():
            errors.append(f"app/Madeira/i386-windows/{name} is in the 32-bit farm: every 32-bit program would find a Vulkan loader without the key "
                          "(build/wine-i386/build.sh keeps both names out)")

stand_in = APP / "arm64ec-windows" / "vulkan-1.dll"
if not stand_in.is_file() or stand_in.stat().st_size > 300_000:
    errors.append("app/Madeira/arm64ec-windows/vulkan-1.dll (the stand-in, tools/build-stub-dlls.py) is missing or is not the small stand-in: "
                  "sessions without a vulkan key would probe Wine's real loader")
if not (APP / "gl" / "libMoltenVK.dylib").is_file():
    errors.append("app/Madeira/gl/libMoltenVK.dylib is missing (build/moltenvk-ios/build.sh)")

nm = shutil.which("nm")
for lib, symbols in (("libntdll_unix.a", ("_winevulkan_unix_call_funcs", "_winevulkan_unix_call_wow64_funcs")),
                     ("libwin32u_unix.a", ("_madeira_ios_VulkanInit", "___wine_get_vulkan_driver", "_madeira_vulkan_init_on_this_thread"))):
    a = APP / lib
    if not a.is_file():
        errors.append(f"app/Madeira/{lib} is missing")
    elif nm:
        out = subprocess.run([nm, "-gU", str(a)], capture_output=True, text=True).stdout.split()
        for s in symbols:
            if s not in out:
                errors.append(f"app/Madeira/{lib} defines no {s}: the Vulkan objects were not built into it")

# KosmicKrisp (vulkan = kosmickrisp; build/mesa-ios/build-kosmickrisp-bcd.sh) is optional: a build without it keeps Vulkan off for such a game
# and says so in the log. When the library is there it must be whole: arm64 for iOS 26.0, exporting the two entry points Wine's Vulkan layer
# loads it by, with what it was built from and Mesa's licence text beside it.
kk = APP / "gl" / "libvulkan_kosmickrisp.dylib"
kk_note = "KosmicKrisp is not in this package (vulkan = kosmickrisp stays off)"
if kk.is_file():
    kk_note = "KosmicKrisp is in the package"
    if not (APP / "gl" / "libvulkan_kosmickrisp.version").is_file():
        errors.append("app/Madeira/gl/libvulkan_kosmickrisp.version is missing beside the library (build/mesa-ios/build-kosmickrisp-bcd.sh writes both)")
    else:
        kk_note += " (" + (APP / "gl" / "libvulkan_kosmickrisp.version").read_text().strip()[:120] + ")"
    if not (APP / "licenses" / "KosmicKrisp-Mesa-license.rst").is_file():
        errors.append("app/Madeira/licenses/KosmicKrisp-Mesa-license.rst is missing: the library is in the package without Mesa's licence text")
    if not (APP / "legal" / "LICENSES" / "MIT-Mesa-KosmicKrisp.txt").is_file():
        errors.append("app/Madeira/legal/LICENSES/MIT-Mesa-KosmicKrisp.txt is missing: the MIT text and KosmicKrisp's copyright holders")
    # Round 668 (bcd-games #57, build/mesa-ios/kk-retry): the library a game loads may be the kk-retry shim, which re-exports the real
    # one, libvulkan_kosmickrisp_real.dylib; then the real one carries the entry points and the shim has to point at it.
    real = APP / "gl" / "libvulkan_kosmickrisp_real.dylib"
    shim = b"kk-retry: shim in front of KosmicKrisp" in kk.read_bytes()
    if shim and not real.is_file():
        errors.append("app/Madeira/gl/libvulkan_kosmickrisp.dylib is the kk-retry shim, but libvulkan_kosmickrisp_real.dylib is missing")
    if real.is_file() and not shim:
        errors.append("app/Madeira/gl/libvulkan_kosmickrisp_real.dylib is in the package, but libvulkan_kosmickrisp.dylib is not the shim that loads it")
    if shim:
        kk_note += " behind the kk-retry shim"
    if shutil.which("xcrun"):
        for lib in [kk] + ([real] if shim and real.is_file() else []):
            name = "app/Madeira/gl/" + lib.name
            archs = subprocess.run(["xcrun", "lipo", "-archs", str(lib)], capture_output=True, text=True).stdout.split()
            if archs != ["arm64"]:
                errors.append(f"{name} is {' '.join(archs) or 'not a Mach-O'}, want arm64")
            build = subprocess.run(["xcrun", "vtool", "-show-build", str(lib)], capture_output=True, text=True).stdout.split()
            minos = build[build.index("minos") + 1] if "minos" in build else "?"
            if minos != "26.0":
                errors.append(f"{name} has minos {minos}: the app lets a game use it from iOS 26.0 on (WineProcessBridge.m)")
        entry = real if shim and real.is_file() else kk
        exported = subprocess.run(["xcrun", "nm", "-gU", str(entry)], capture_output=True, text=True).stdout.split()
        for s in ("_vkGetInstanceProcAddr", "_vkGetDeviceProcAddr"):
            if s not in exported:
                errors.append(f"app/Madeira/gl/{entry.name} does not export {s}: win32u's Vulkan layer could not use it")
        if shim:
            cmds = subprocess.run(["xcrun", "otool", "-l", str(kk)], capture_output=True, text=True).stdout
            if "name @rpath/libvulkan_kosmickrisp_real.dylib" not in cmds or "LC_REEXPORT_DYLIB" not in cmds or "path @loader_path" not in cmds:
                errors.append("app/Madeira/gl/libvulkan_kosmickrisp.dylib (the shim) does not re-export @rpath/libvulkan_kosmickrisp_real.dylib "
                              "with @loader_path in its run paths")

# The patched MoltenVK (vulkan = moltenvk-patched; round 669, build/moltenvk-ios/build-patched.sh) is optional like KosmicKrisp: its CI step only warns
# when it fails, and a build without it keeps Vulkan off for such a game and says so in the log (never the plain MoltenVK instead). When the library
# is there it must be whole: a thin arm64 dylib under its own install name (the plain MoltenVK's is another), exporting the two entry points Wine's
# Vulkan layer loads it by, not the plain library's bytes (a patch that did not apply), with the version file the script writes beside it and no
# half-written file next to it.
mp = APP / "gl" / "libMoltenVK_patched.dylib"
mp_note = "the patched MoltenVK is not in this package (vulkan = moltenvk-patched stays off)"
if (APP / "gl" / "libMoltenVK_patched.dylib.tmp").exists():
    errors.append("app/Madeira/gl/libMoltenVK_patched.dylib.tmp is in the package: a half-written library of a failed build")
if mp.is_file():
    mp_note = "the patched MoltenVK is in the package"
    if not (APP / "gl" / "libMoltenVK_patched.version").is_file():
        errors.append("app/Madeira/gl/libMoltenVK_patched.version is missing beside the library (build/moltenvk-ios/build-patched.sh writes both)")
    else:
        mp_note += " (" + (APP / "gl" / "libMoltenVK_patched.version").read_text().strip()[:120] + ")"
    if mp.stat().st_size < 1_000_000:
        errors.append(f"app/Madeira/gl/libMoltenVK_patched.dylib is only {mp.stat().st_size} bytes (MoltenVK's is about 4.8 MB)")
    plain = APP / "gl" / "libMoltenVK.dylib"
    if plain.is_file() and plain.read_bytes() == mp.read_bytes():
        errors.append("app/Madeira/gl/libMoltenVK_patched.dylib is the plain libMoltenVK.dylib under another name: the patch did not take")
    if shutil.which("xcrun"):
        archs = subprocess.run(["xcrun", "lipo", "-archs", str(mp)], capture_output=True, text=True).stdout.split()
        if archs != ["arm64"]:
            errors.append(f"app/Madeira/gl/libMoltenVK_patched.dylib is {' '.join(archs) or 'not a Mach-O'}, want arm64")
        ident = subprocess.run(["xcrun", "otool", "-D", str(mp)], capture_output=True, text=True).stdout.split()
        if "@rpath/libMoltenVK_patched.dylib" not in ident:
            errors.append(f"app/Madeira/gl/libMoltenVK_patched.dylib: install name {ident[-1] if ident else '?'}, want @rpath/libMoltenVK_patched.dylib")
        exported = subprocess.run(["xcrun", "nm", "-gU", str(mp)], capture_output=True, text=True).stdout.split()
        for sym in ("_vkGetInstanceProcAddr", "_vkGetDeviceProcAddr"):
            if sym not in exported:
                errors.append(f"app/Madeira/gl/libMoltenVK_patched.dylib does not export {sym}: win32u's Vulkan layer could not use it")

print("\n".join(("FAIL " + e) for e in errors) if errors else "ok   the Windows Vulkan pieces are all in the package; " + kk_note + "; " + mp_note)
sys.exit(1 if errors else 0)

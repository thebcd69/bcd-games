#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""Vulkan for 32-bit programs (build 660, docs/VULKAN.md "32-bit programs"): the same `vulkan = moltenvk` key, for an i386 game.

Part A needs only this repository:
  - the wiring: the unix binder gives a 32-bit winevulkan.dll its wow64 table only in a session with the key; the bridge links the
    two i386 DLLs into syswow64 only then; the i386 farm still ships neither file; the build script, the package check, the CI
    steps and their order; the page, the catalog and the document say what the code does;
  - the session code itself, compiled from WineProcessBridge.m and run against temporary folders: with the key off nothing is
    linked anywhere and MADEIRA_VULKAN_LIBRARY is not set, a link an earlier session made is removed, the user's own file is not
    touched; with the key on syswow64 gets exactly the two links, or a log line when the build has no i386 set;
  - the Wine patch's thunk transform on small samples (addresses converted, numbers kept).

Part B needs the wine submodule (WINE_SRC, default <repository>/wine) and, for the harness, the generated Wine headers of a
configured tree (WINE_GEN_INCLUDE, default <wine>/build-macos/include, which the build has by then):
  - tools/patch-wine-vulkan-wow64.py on copies: Wine 11.4's files as committed (the counts are checked), a second run, and both
    orders with tools/patch-wine-vulkan-suboptimal.py, which changes the same win32u file (pristine AND build-patched trees);
  - the generator: where a Vulkan registry is at hand (BCD_VK_XML / BCD_VK_VIDEO_XML, or toolchains/MoltenVK's Vulkan-Headers),
    the changed make_vulkan writes exactly what the transform makes of the unchanged one's output, and no other generated file
    changes (the PE side is untouched);
  - when an unstripped i386 winevulkan.dll is at hand (BCD_VK32_I386_DLL, or wine/build-i386) with llvm-dwarfdump: the argument
    blocks of the real 32-bit DLL (its debug info) against the layouts the unix thunks read, block by block;
  - the harness (tests/host/vulkan-wow64): winevulkan's unix side and win32u's Vulkan layer as BCD builds them, driven as a
    32-bit program in a guest window 4 GB-aligned above 4 GB, against a stand-in Vulkan library with MoltenVK's memory model, then
    with VK_EXT_map_memory_placed, then with neither (the refusal); and a 64-bit program in the same session, for which nothing
    may change. --control also builds it with Wine's unchanged thunks and expects the fault that proves the harness can see one.

    python3 tests/host/check-vulkan-wow64.py [--require-wine] [--control] [-v]
"""
import importlib.util
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

root = Path(__file__).resolve().parents[2]
here = root / "tests/host/vulkan-wow64"
ok = True
REQUIRE = "--require-wine" in sys.argv
CONTROL = "--control" in sys.argv
VERBOSE = "-v" in sys.argv


def check(name, cond, detail=""):
    global ok
    print(("ok   " if cond else "FAIL ") + name)
    if not cond:
        ok = False
        if detail:
            print("     " + str(detail)[:3000])
    sys.stdout.flush()


def function(source, start):
    a = source.index(start)
    b = source.index("{", a)
    depth, c = 1, b + 1
    while depth:
        depth += (source[c] == "{") - (source[c] == "}")
        c += 1
    return source[a:c]


spec = importlib.util.spec_from_file_location("patch_wine_vulkan_wow64", root / "tools/patch-wine-vulkan-wow64.py")
patch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(patch)

# ============================================================================================================== Part A: wiring
virt = (root / "build/ntdll-unix/virtual_ios.c").read_text()
chain = function(virt, "static NTSTATUS load_builtin_unixlib(")
branch = [b for b in re.split(r"\n        \} else ", chain) if 'strstr(match, "winevulkan")' in b.split("\n")[0]]
check("the unix binder has one winevulkan branch: any 64-bit caller, a 32-bit caller only in a session with the key",
      len(branch) == 1 and branch[0].split("\n")[0] == 'if (match && strstr(match, "winevulkan") && (!wow || ios_vk_session_on())) {'
      and "funcs64 = (const void *)winevulkan_unix_call_funcs;" in branch[0]
      and "funcs_wow64 = (const void *)winevulkan_unix_call_wow64_funcs;" in branch[0], branch[:1])
check("...the wow64 table is declared, and the key is asked through MADEIRA_VULKAN_LIBRARY and nothing else",
      "extern const void *winevulkan_unix_call_wow64_funcs[];" in virt
      and re.sub(r"\s+", " ", function(virt, "static int ios_vk_session_on( void )")).endswith(
          '{ const char *e = getenv( "MADEIRA_VULKAN_LIBRARY" ); return e && *e; }'))
check("...a 32-bit caller without the key still ends in the generic branch, which refuses it (no stub table for 32-bit code)",
      chain.index('strstr(match, "winevulkan")') < chain.index("funcs_wow64 = NULL;\n            if (wow)")
      and "refusing with STATUS_NOT_SUPPORTED" in chain)

bridge = (root / "app/Madeira/WineProcessBridge.m").read_text()
links = function(bridge, "static void bcd_vulkan32_session_links(")
setup = function(bridge, "static void bcd_vulkan_session_setup(")
check("the bridge links the i386 pair into syswow64 from the bundle's vulkan/i386-windows, and only removes links that point there",
      '@"drive_c/windows/syswow64"' in links and '@"vulkan/i386-windows"' in links
      and '@[@"winevulkan.dll", @"vulkan-1.dll"]' in links
      and '[dest rangeOfString:@"/vulkan/i386-windows/"].location != NSNotFound' in links
      and links.index("if (!want) return;") < links.index("createSymbolicLinkAtPath"), links[:800])
export = '\n    setenv("MADEIRA_VULKAN_LIBRARY", lib.UTF8String, 1);'   # not the unsetenv calls of the ways out
check("...an earlier session's 32-bit links go before the key is even read (so every way Vulkan stays off leaves none), and they are made only "
      "after the library is exported",
      setup.count("bcd_vulkan32_session_links(fm, prefix, bundlePath, NULL);") == 1
      and setup.count("bcd_vulkan32_session_links(fm, prefix, bundlePath, want);") == 1
      and setup.index("bcd_vulkan32_session_links(fm, prefix, bundlePath, NULL);") < setup.index('madeira_cfg_get("vulkan"')
      and setup.index(export) < setup.index("bcd_vulkan32_session_links(fm, prefix, bundlePath, want);")
      and setup.count("return;") == 2 and setup.rindex("return;") < setup.index(export))

farm = (root / "build/wine-i386/build.sh").read_text()
dlls = (root / "tools/build-vulkan-dlls.sh").read_text()
check("the i386 farm still ships neither DLL (a 32-bit program without the key finds no vulkan-1.dll, as always)",
      '"winevulkan.dll|vulkan-1.dll=' in farm)
conf = re.search(r"\(cd \"\$B\" && (\.\./configure.*?)\)\n", farm, re.S)
conf2 = re.search(r"\(cd \"\$B\" && (\.\./configure.*?)\)\n", dlls, re.S)
norm = lambda m: " ".join(m.group(1).replace("\\\n", " ").split()) if m else None
check("tools/build-vulkan-dlls.sh configures wine/build-i386 exactly as the farm's script does",
      norm(conf) is not None and norm(conf) == norm(conf2), (norm(conf), norm(conf2)))
check("...builds the two i386 modules into the bundle's vulkan/i386-windows (not the farm), keeps the arm64ec set as it was",
      'T32="dlls/winevulkan/i386-windows/winevulkan.dll dlls/vulkan-1/i386-windows/vulkan-1.dll"' in dlls
      and 'DEST32="$R/app/Madeira/vulkan/i386-windows"' in dlls
      and 'DEST="$R/app/Madeira/vulkan/arm64ec-windows" bash "$R/build/wine-pe/build-modules.sh" winevulkan vulkan-1' in dlls)
ignore = (root / ".gitignore").read_text()
check("the i386 DLL folder is ignored like the arm64ec one", "app/Madeira/vulkan/i386-windows/" in ignore)
shipped = (root / "tools/check-vulkan-shipped.py").read_text()
check("the package check wants the i386 pair (machine 0x14c) whenever the package has the i386 farm, and the wow64 table",
      '"vulkan" / "i386-windows"' in shipped and "0x14c" in shipped and '"_winevulkan_unix_call_wow64_funcs"' in shipped
      and '"i386-windows" / "ntdll.dll"' in shipped)

ci = (root / ".github/workflows/build-ipa.yml").read_text()
p_step = ci.find("python3 tools/patch-wine-vulkan-wow64.py")
check("CI writes the Wine change before the unix sides are compiled (ntdll-unix holds winevulkan's, win32u-unix the layer's)",
      0 < p_step < ci.find("      - name: Build ntdll-unix\n") < ci.find("      - name: Build win32u-unix\n")
      and "grep -q wine_vk_client_ptr wine/dlls/winevulkan/vulkan_thunks.c" in ci)
check("...runs this test with the wine tree required, after the generated headers exist and before the unix sides are built",
      ci.find("      - name: Generate cross-tree headers and patch config.h for iOS SDK\n")
      < ci.find("python3 tests/host/check-vulkan-wow64.py --require-wine") < ci.find("      - name: Build ntdll-unix\n"))
check("...and checks the wow64 table is in libntdll_unix.a",
      "opengl32_unix_call_funcs opengl32_unix_call_wow64_funcs winevulkan_unix_call_funcs winevulkan_unix_call_wow64_funcs; do" in ci)

page = (root / "app/Madeira/BCDGamePageLive.swift").read_text()
sample = (root / "app/BCDPreview/BCDGamePageSample.swift").read_text()
note = "For a game that needs Vulkan, 64-bit (Detroit: Become Human) or 32-bit: Wine's Vulkan on Metal (MoltenVK)"
check("the game page's Vulkan switch says it serves 32-bit games too (and the preview's sample page says the same)",
      note in page and note in sample and "For a 64-bit game that needs Vulkan" not in page + sample)
gen = (root / "build/tools/gen-config-catalog.py").read_text()
catalog = (root / "app/Madeira/ConfigCatalog.generated.swift").read_text()
check("the settings catalog's vulkan entry no longer says x64 only, and says what a 32-bit program pays for it",
      '"vulkan": {"category": "Graphics", "title": "Vulkan for this game", "kind": "choice", "default": "",' in gen
      and "32-bit programs get no Vulkan" not in gen and "placed inside its own" in gen
      and 'ConfigOption(key: "vulkan", title: "Vulkan for this game", kind: .choice, defaultValue: "",' in catalog)
doc = (root / "docs/VULKAN.md").read_text()
check("docs/VULKAN.md has the 32-bit section", "## 32-bit programlar" in doc and "VK_EXT_external_memory_host" in doc
      and "patch-wine-vulkan-wow64.py" in doc and "32-bit programa Vulkan yok" not in doc)
readme = (root / "app/Madeira/vulkan/README.md").read_text()
check("the bundle folder's README names both sets", "i386-windows" in readme and "arm64ec-windows" in readme)

# ---- the session code, run
if shutil.which("clang") and sys.platform == "darwin":
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        (tmp / "session.m").write_text('''
#import <Foundation/Foundation.h>
#include <sys/stat.h>
#include <unistd.h>
#include <stdio.h>
#include <string.h>
#include <stdlib.h>
static int madeira_cfg_get(const char *key, char *buf, size_t size)
{
    const char *v = getenv("TEST_CFG_VULKAN");
    if (strcmp(key, "vulkan") || !v) return 0;
    snprintf(buf, size, "%s", v);
    return 1;
}
/* KosmicKrisp's two pieces of the bridge that the setup uses (the device question needs Metal 4; the test answers it) */
static const char *bcd_kosmickrisp_unusable(void) { return getenv("TEST_KK_UNUSABLE"); }
static int g_kk_cache_dir_set, g_kk_cache_size_set;
''' + links + "\n" + setup + '''
int main(int argc, char **argv)
{
    @autoreleasepool {
        bcd_vulkan_session_setup([NSFileManager defaultManager], @(argv[1]), @(argv[2]), argc > 3 && argv[3][0] == '1');
        const char *lib = getenv("MADEIRA_VULKAN_LIBRARY");
        printf("LIB=%s\\n", lib ? lib : "(unset)");
    }
    return 0;
}
''')
        r = subprocess.run(["clang", "-fobjc-arc", "-framework", "Foundation", "-Wno-unused-function", str(tmp / "session.m"), "-o", str(tmp / "session")],
                           capture_output=True, text=True)
        check("the session code of WineProcessBridge.m compiles on its own for the host", r.returncode == 0, r.stderr)
        if r.returncode == 0:
            def world(name, i386_set=True, bundle32=True, bundle64=True, kosmickrisp=False):
                w = tmp / name
                for d in ("system32", "sysx64") + (("syswow64",) if i386_set else ()):
                    (w / "prefix/drive_c/windows" / d).mkdir(parents=True)
                (w / "bundle/gl").mkdir(parents=True)
                (w / "bundle/gl/libMoltenVK.dylib").write_text("x")
                if kosmickrisp:
                    (w / "bundle/gl/libvulkan_kosmickrisp.dylib").write_text("x")
                for arch, want in (("arm64ec-windows", bundle64), ("i386-windows", bundle32)):
                    if want:
                        (w / "bundle/vulkan" / arch).mkdir(parents=True)
                        for f in ("winevulkan.dll", "vulkan-1.dll"):
                            (w / "bundle/vulkan" / arch / f).write_text(arch)
                return w

            def run(w, key, ec="0", kk_unusable=None):
                env = dict(os.environ)
                for name in ("MADEIRA_VULKAN_LIBRARY", "TEST_CFG_VULKAN", "TEST_KK_UNUSABLE", "MADEIRA_GL_BACKEND"):
                    env.pop(name, None)
                # the KosmicKrisp branch leaves an existing shader cache folder alone: the test's run creates nothing in Library/Caches
                env["MESA_SHADER_CACHE_DIR"] = str(tmp / "shader-cache-of-the-test")
                if key is not None:
                    env["TEST_CFG_VULKAN"] = key
                if kk_unusable:
                    env["TEST_KK_UNUSABLE"] = kk_unusable
                r = subprocess.run([str(tmp / "session"), str(w / "prefix"), str(w / "bundle"), ec], capture_output=True, text=True, env=env)
                return r.stdout + r.stderr

            def listing(w):
                out = {}
                for d in ("system32", "sysx64", "syswow64"):
                    p = w / "prefix/drive_c/windows" / d
                    if p.is_dir():
                        for f in sorted(p.iterdir()):
                            out[f"{d}/{f.name}"] = os.readlink(f) if f.is_symlink() else "FILE"
                return out

            for key in (None, "", "off", "0", "dxvk"):
                w = world("off-%s" % (key if key else repr(key).strip("'")))
                log = run(w, key)
                check(f"key {key!r}: nothing is linked in system32, sysx64 or syswow64, and MADEIRA_VULKAN_LIBRARY stays unset",
                      listing(w) == {} and "LIB=(unset)" in log and "32-bit" not in log, (listing(w), log))
            w = world("on")
            log = run(w, "moltenvk")
            got = listing(w)
            check("key moltenvk: syswow64 gets exactly the two links, into the bundle's vulkan/i386-windows, and the log says so",
                  {k: v for k, v in got.items() if k.startswith("syswow64/")} ==
                  {"syswow64/" + f: str(w / "bundle/vulkan/i386-windows" / f) for f in ("winevulkan.dll", "vulkan-1.dll")}
                  and "Vulkan for 32-bit programs is on for this session (2 Vulkan DLL links in syswow64" in log
                  and "LIB=" + str(w / "bundle/gl/libMoltenVK.dylib") in log, (got, log))
            log = run(w, None)
            check("...and the next session without the key takes them away again (and the 64-bit ones, as before)",
                  listing(w) == {} and "LIB=(unset)" in log, (listing(w), log))
            (w / "prefix/drive_c/windows/syswow64/vulkan-1.dll").write_text("the user's own")
            log = run(w, "moltenvk")
            got = listing(w)
            check("a real file named vulkan-1.dll in syswow64 (the user's) is left alone, with the key on and off",
                  got.get("syswow64/vulkan-1.dll") == "FILE" and "syswow64\\vulkan-1.dll is a file, not a link: left as it is" in log
                  and (run(w, None), listing(w).get("syswow64/vulkan-1.dll"))[1] == "FILE", (got, log))
            w = world("no-bundle32", bundle32=False)
            log = run(w, "moltenvk")
            check("a build without the i386 pair: 64-bit Vulkan is on, no 32-bit link is made, and the log says which file is missing",
                  not any(k.startswith("syswow64/") for k in listing(w)) and "no Vulkan for 32-bit programs in this build" in log
                  and "vulkan/i386-windows/winevulkan.dll is missing" in log and "Vulkan is on for this session" in log, (listing(w), log))
            w = world("no-i386-set", i386_set=False)
            log = run(w, "moltenvk")
            check("a build without the 32-bit set at all: no syswow64 folder is made, no 32-bit line is logged",
                  not (w / "prefix/drive_c/windows/syswow64").exists() and "32-bit" not in log, log)
            w = world("no-bundle64", bundle64=False)
            log = run(w, "moltenvk")
            check("the 64-bit pair missing: Vulkan stays off as a whole, 32-bit included",
                  listing(w) == {} and "Vulkan stays off" in log and "LIB=(unset)" in log, (listing(w), log))
            # the key's other word (KosmicKrisp, build 660): the same links; which memory route is used is win32u's choice by the library's extensions
            w = world("kosmickrisp", kosmickrisp=True)
            log = run(w, "kosmickrisp")
            got = listing(w)
            check("key kosmickrisp on a device that can run it: the 32-bit program gets the same two links, and the log names the word",
                  {k: v for k, v in got.items() if k.startswith("syswow64/")} ==
                  {"syswow64/" + f: str(w / "bundle/vulkan/i386-windows" / f) for f in ("winevulkan.dll", "vulkan-1.dll")}
                  and "vulkan=kosmickrisp: Vulkan for 32-bit programs is on for this session (2 Vulkan DLL links in syswow64" in log
                  and "LIB=" + str(w / "bundle/gl/libvulkan_kosmickrisp.dylib") in log
                  and not (tmp / "shader-cache-of-the-test").exists(), (got, log))
            log = run(w, "kosmickrisp", kk_unusable="KosmicKrisp needs iOS 26 or later")
            check("...on a device that cannot: Vulkan stays off, and the links of the session before are gone, 32-bit ones too",
                  listing(w) == {} and "vulkan=kosmickrisp: Vulkan stays off, KosmicKrisp needs iOS 26 or later" in log
                  and "LIB=(unset)" in log and "32-bit" not in log, (listing(w), log))
            w = world("kosmickrisp-not-bundled")
            run(w, "moltenvk")
            log = run(w, "kosmickrisp")
            check("...in a build without the library: Vulkan stays off, nothing is linked (MoltenVK is not taken instead)",
                  listing(w) == {} and "Vulkan stays off" in log and "LIB=(unset)" in log and "32-bit" not in log, (listing(w), log))
else:
    print("skip the session code run (needs clang on macOS)")

# ---- the package check, run against made-up packages
with tempfile.TemporaryDirectory() as tmp:
    tmp = Path(tmp)
    (tmp / "tools").mkdir()
    shutil.copy(root / "tools/check-vulkan-shipped.py", tmp / "tools/check-vulkan-shipped.py")
    app = tmp / "app/Madeira"

    def pe(path, machine_word, size):
        path.parent.mkdir(parents=True, exist_ok=True)
        data = bytearray(size)
        data[0:2] = b"MZ"
        data[0x3C:0x40] = (0x80).to_bytes(4, "little")
        data[0x80:0x84] = b"PE\0\0"
        data[0x84:0x86] = machine_word.to_bytes(2, "little")
        path.write_bytes(data)

    def shipped():
        r = subprocess.run([sys.executable, str(tmp / "tools/check-vulkan-shipped.py")], capture_output=True, text=True)
        return [l for l in r.stdout.splitlines() if "i386" in l]

    no_farm = shipped()
    pe(app / "i386-windows/ntdll.dll", 0x14c, 4096)
    missing = shipped()
    pe(app / "vulkan/i386-windows/winevulkan.dll", 0x14c, 380_928)
    pe(app / "vulkan/i386-windows/vulkan-1.dll", 0x14c, 49_152)
    present = shipped()
    pe(app / "vulkan/i386-windows/winevulkan.dll", 0x8664, 380_928)
    wrong = shipped()
    pe(app / "vulkan/i386-windows/winevulkan.dll", 0x14c, 380_928)
    pe(app / "i386-windows/vulkan-1.dll", 0x14c, 49_152)
    in_farm = shipped()
    check("the package check: no word about i386 without the 32-bit farm; with it, a missing pair, a wrong machine and a Vulkan DLL inside the farm are each refused",
          no_farm == [] and present == [] and len(missing) == 2 and all("is missing" in l for l in missing)
          and len(wrong) == 1 and "machine 0x8664, want an i386 image" in wrong[0]
          and len(in_farm) == 1 and "is in the 32-bit farm" in in_farm[0], (no_farm, missing, present, wrong, in_farm))

# ---- the thunk transform on samples
SAMPLE = '''static NTSTATUS thunk32_vkSample(void *args)
{
    struct
    {
        PTR32 device;
        PTR32 pHandle;
        PTR32 ppData;
    } *params = args;
    HANDLE pHandle_host;
    void *ppData_host;
    VkDevice pDevice_host;

    out->hwnd = (HWND)UlongToPtr(in->hwnd);
    out->hinstance = (HINSTANCE)UlongToPtr(in->hinstance);
    out->handle = (HANDLE)UlongToPtr(in->handle);
    out->pfnUserCallback = (PFN_vkDebugUtilsMessengerCallbackEXT)UlongToPtr(in->pfnUserCallback);
    out->name = (LPCWSTR)UlongToPtr(in->name);
    out->pUserData = UlongToPtr(in->pUserData);
    out->pData = PtrToUlong(in->pData);
    for (in_header = UlongToPtr(in->pNext); in_header; in_header = UlongToPtr(in_header->pNext))
    pHandle_host = UlongToPtr(*(PTR32 *)UlongToPtr(params->pHandle));
    ppData_host = UlongToPtr(*(PTR32 *)UlongToPtr(params->ppData));
    pDevice_host = UlongToPtr(*(PTR32 *)UlongToPtr(params->pDevice));
    x = vulkan_device_from_handle((VkDevice)UlongToPtr(params->device))->p_f((HANDLE)UlongToPtr(params->handle), (const char *)UlongToPtr(params->p));
    *(PTR32 *)UlongToPtr(params->pHandle) = PtrToUlong(pHandle_host);
    *(PTR32 *)UlongToPtr(params->ppData) = PtrToUlong(ppData_host);
    *(PTR32 *)UlongToPtr(params->pDevice) = PtrToUlong(pDevice_host);
        return (uint64_t) (uintptr_t) vulkan_device_from_handle(((VkDevice) (uintptr_t) handle))->host.device;
}
'''
WANT = '''static NTSTATUS thunk32_vkSample(void *args)
{
    struct
    {
        PTR32 device;
        PTR32 pHandle;
        PTR32 ppData;
    } *params = args;
    HANDLE pHandle_host;
    void *ppData_host;
    VkDevice pDevice_host;

    out->hwnd = (HWND)UlongToPtr(in->hwnd);
    out->hinstance = (HINSTANCE)UlongToPtr(in->hinstance);
    out->handle = (HANDLE)UlongToPtr(in->handle);
    out->pfnUserCallback = (PFN_vkDebugUtilsMessengerCallbackEXT)UlongToPtr(in->pfnUserCallback);
    out->name = (LPCWSTR)ios_wow_host_ptr(in->name);
    out->pUserData = ios_wow_host_ptr(in->pUserData);
    out->pData = ios_wow_guest_ptr32(in->pData);
    for (in_header = ios_wow_host_ptr(in->pNext); in_header; in_header = ios_wow_host_ptr(in_header->pNext))
    pHandle_host = UlongToPtr(*(PTR32 *)ios_wow_host_ptr(params->pHandle));
    ppData_host = ios_wow_host_ptr(*(PTR32 *)ios_wow_host_ptr(params->ppData));
    pDevice_host = ios_wow_host_ptr(*(PTR32 *)ios_wow_host_ptr(params->pDevice));
    x = vulkan_device_from_handle((VkDevice)ios_wow_host_ptr(params->device))->p_f((HANDLE)UlongToPtr(params->handle), (const char *)ios_wow_host_ptr(params->p));
    *(PTR32 *)ios_wow_host_ptr(params->pHandle) = PtrToUlong(pHandle_host);
    *(PTR32 *)ios_wow_host_ptr(params->ppData) = ios_wow_guest_ptr32(ppData_host);
    *(PTR32 *)ios_wow_host_ptr(params->pDevice) = ios_wow_guest_ptr32(pDevice_host);
        return (uint64_t) (uintptr_t) vulkan_device_from_handle(((VkDevice) wine_vk_client_ptr(handle)))->host.device;
}
'''
got, counts = patch.transform_thunks(SAMPLE)
check("the thunk transform converts addresses (pointers, strings, dispatchable handles, mappings) and keeps numbers (HWND, HINSTANCE, HANDLE, PFN_*)",
      got == WANT and counts == {"host": 14, "guest": 3, "kept_host": 6, "kept_guest": 1, "unwrap": 1}, (counts, got))

# ============================================================================================================ Part B: the wine tree
wine = Path(os.environ.get("WINE_SRC", root / "wine"))
RELS = [rel for rel, _, _ in patch.FILES]
have_wine = all((wine / rel).is_file() for rel in RELS)
if not have_wine:
    if REQUIRE:
        check("the wine tree is there (--require-wine)", False, f"{wine} has no dlls/winevulkan: set WINE_SRC")
    else:
        print(f"skip the Wine patch, the generator and the harness: no wine tree at {wine} (set WINE_SRC)")
    print("PASS" if ok else "FAILED")
    sys.exit(0 if ok else 1)


def pristine(rel):
    """Wine's committed file (the tree itself may already be patched by the build)."""
    r = subprocess.run(["git", "-C", str(wine), "show", "HEAD:" + rel], capture_output=True, text=True)
    return r.stdout if r.returncode == 0 and r.stdout else (wine / rel).read_text()


def run_script(name, cwd, *args):
    return subprocess.run([sys.executable, str(root / "tools" / name), *args], cwd=cwd, capture_output=True, text=True)


with tempfile.TemporaryDirectory() as tmp_name:
    tmp = Path(tmp_name)

    def tree(name, source=pristine):
        t = tmp / name / "wine"
        for rel in RELS:
            (t / rel).parent.mkdir(parents=True, exist_ok=True)
            (t / rel).write_text(source(rel))
        return t.parent

    # ---- the patch on Wine's committed files
    a = tree("a")
    r1 = run_script("patch-wine-vulkan-wow64.py", a)
    after1 = {rel: (a / "wine" / rel).read_text() for rel in RELS}
    r2 = run_script("patch-wine-vulkan-wow64.py", a)
    check("the Wine patch applies to Wine's committed files (11.4: the conversion counts are the expected ones) and writes all five",
          r1.returncode == 0 and r1.stdout.count("32-bit Vulkan change written") == 5, r1.stdout + r1.stderr)
    check("...a second run changes nothing and only says so",
          r2.returncode == 0 and r2.stdout.count("already there") == 5 and after1 == {rel: (a / "wine" / rel).read_text() for rel in RELS},
          r2.stdout + r2.stderr)
    thunks = after1["dlls/winevulkan/vulkan_thunks.c"]
    kept = [l.strip() for l in thunks.split("\n") if "UlongToPtr(" in l or "PtrToUlong(" in l]
    check("...what is left of UlongToPtr / PtrToUlong in the thunks is only window, module and kernel handles and callback addresses",
          kept and all(re.search(r"\((HWND|HINSTANCE|HANDLE|PFN_\w+)\)UlongToPtr\(", l) or re.match(r"pHandle_host = UlongToPtr\(\*", l)
                       or re.search(r"= PtrToUlong\(pHandle_host\);$", l) for l in kept), kept[:20])
    win64 = lambda text: re.findall(r"#ifdef _WIN64\n.*?\n#endif /\* _WIN64 \*/", text, re.S)
    blocks = win64(thunks)
    check("...the code of 64-bit callers is byte for byte Wine's: every `#ifdef _WIN64` block (the thunk64 functions, the win64 conversions, the 64-bit table)",
          len(blocks) >= 700 and blocks == win64(pristine("dlls/winevulkan/vulkan_thunks.c"))
          and sum("thunk64_" in b.split("\n")[1] for b in blocks) > 600 and any("__wine_unix_call_funcs[]" in b for b in blocks)
          and "ios_wow_" not in "".join(blocks), len(blocks))

    # ---- with the other script that changes win32u/vulkan.c, both orders; and on the tree as it is now
    b, c = tree("b"), tree("c")
    rb = [run_script("patch-wine-vulkan-suboptimal.py", b), run_script("patch-wine-vulkan-wow64.py", b)]
    rc = [run_script("patch-wine-vulkan-wow64.py", c), run_script("patch-wine-vulkan-suboptimal.py", c)]
    check("the Wine patch and tools/patch-wine-vulkan-suboptimal.py apply in either order, with the same result",
          all(r.returncode == 0 for r in rb + rc)
          and (b / "wine/dlls/win32u/vulkan.c").read_text() == (c / "wine/dlls/win32u/vulkan.c").read_text()
          and "ignore_host_suboptimal" in (b / "wine/dlls/win32u/vulkan.c").read_text()
          and "host_alloc" in (b / "wine/dlls/win32u/vulkan.c").read_text(), "\n".join(r.stdout + r.stderr for r in rb + rc))
    d = tree("d", lambda rel: (wine / rel).read_text())
    rd = run_script("patch-wine-vulkan-wow64.py", d)
    check("...and on the tree as it is now (pristine, or already patched by the build): it applies or says it is there",
          rd.returncode == 0 and rd.stdout.count("\n") == 5
          and {rel: (d / "wine" / rel).read_text() for rel in RELS if rel != "dlls/win32u/vulkan.c"}
          == {rel: after1[rel] for rel in RELS if rel != "dlls/win32u/vulkan.c"}, rd.stdout + rd.stderr)

    # ---- the generator
    reg = None
    for cand in (os.environ.get("BCD_VK_XML"), root / "toolchains/MoltenVK/External/Vulkan-Headers/registry/vk.xml"):
        if cand and Path(cand).is_file():
            reg = Path(cand)
            break
    video = Path(os.environ.get("BCD_VK_VIDEO_XML", reg.parent / "video.xml")) if reg else None
    if reg and video and video.is_file():
        outs = {}
        for which in ("old", "new"):
            g = tmp / ("gen-" + which)
            (g / "dlls/winevulkan").mkdir(parents=True)
            (g / "dlls/vulkan-1").mkdir(parents=True)
            (g / "include/wine").mkdir(parents=True)
            src = pristine("dlls/winevulkan/make_vulkan") if which == "old" else after1["dlls/winevulkan/make_vulkan"]
            (g / "dlls/winevulkan/make_vulkan").write_text(src)
            shutil.copy(wine / "dlls/winevulkan/winevk.xml", g / "dlls/winevulkan/winevk.xml")
            r = subprocess.run([sys.executable, "make_vulkan", "-x", str(reg), "-X", str(video)], cwd=g / "dlls/winevulkan", capture_output=True, text=True)
            outs[which] = (r, {p.relative_to(g).as_posix(): p.read_text() for p in sorted(g.rglob("*")) if p.is_file() and p.name not in ("make_vulkan", "winevk.xml")})
        if outs["old"][0].returncode != 0:
            print(f"skip the generator comparison: Wine's make_vulkan cannot read the registry at {reg} ({outs['old'][0].stderr.strip().splitlines()[-1:]})")
        else:
            old, new = outs["old"][1], outs["new"][1]
            t, n = patch.transform_thunks(old.get("dlls/winevulkan/vulkan_thunks.c", ""))
            check(f"the changed make_vulkan writes exactly the transform of the unchanged one's vulkan_thunks.c ({n['host']} + {n['guest']} conversions, registry {reg.parent.parent.name})",
                  outs["new"][0].returncode == 0 and t == new.get("dlls/winevulkan/vulkan_thunks.c") and n["host"] > 3000,
                  outs["new"][0].stderr[-800:])
            check("...and no other generated file changes: the PE side (loader thunks, the spec files, vulkan.h) is untouched",
                  {k: v for k, v in old.items() if not k.endswith("vulkan_thunks.c")} == {k: v for k, v in new.items() if not k.endswith("vulkan_thunks.c")}
                  and len(old) >= 7, sorted(old))
    else:
        print("skip the generator comparison: no Vulkan registry at hand (BCD_VK_XML, or toolchains/MoltenVK/External/Vulkan-Headers/registry/vk.xml)")

    # ---- the harness
    geninc = Path(os.environ.get("WINE_GEN_INCLUDE", wine / "build-macos/include"))
    if not (geninc / "objidlbase.h").is_file() or not shutil.which("cc"):
        if REQUIRE:
            check("the generated Wine headers are there for the harness (--require-wine)", False,
                  f"{geninc} has no objidlbase.h: set WINE_GEN_INCLUDE to a configured tree's include folder (after `make include`)")
        else:
            print(f"skip the harness: no generated Wine headers at {geninc} (set WINE_GEN_INCLUDE)")
    else:
        def build(name, patched=True):
            w = tmp / name
            wv = w / "wv"
            t = w / "t"
            inc = w / "inc"
            for p in (wv, t / "build/win32u-unix", t / "wine/dlls/win32u", inc):
                p.mkdir(parents=True)
            (inc / "config.h").write_text("/* the harness's config.h: nothing of the host's configuration matters to these files */\n#define HAVE_CLOCK_GETTIME 1\n")
            src = after1 if patched else {rel: pristine(rel) for rel in RELS}
            for f in ("vulkan.c", "vulkan_thunks.c", "vulkan_private.h"):
                (wv / f).write_text(src["dlls/winevulkan/" + f])
            # win32u's file as the build has it: both scripts applied (the unpatched control keeps only the other one)
            w32 = (b / "wine/dlls/win32u/vulkan.c").read_text() if patched else pristine("dlls/win32u/vulkan.c")
            (t / "wine/dlls/win32u/vulkan.c").write_text(w32)
            for f in ("vulkan_ios.c", "vulkan_surface_ios.c", "config_ios.h"):
                shutil.copy(root / "build/win32u-unix" / f, t / "build/win32u-unix" / f)
            thunk_src = (wv / "vulkan_thunks.c").read_text()
            blocks = re.findall(r"static (?:NTSTATUS|void) thunk32_(\w+)\(void \*args\)\n\{\n    struct\n    \{\n(.*?)    \} \*params = args;", thunk_src, re.S)
            layout = "".join('    printf( "%s %u", "{0}", (unsigned)sizeof(struct p32_{0}) );\n'.format(n)
                             + "".join('    printf( " %s@%u", "{1}", (unsigned)offsetof(struct p32_{0}, {1}) );\n'.format(n, f)
                                       for f in re.findall(r"(\w+);\n", body))
                             + '    printf( "\\n" );\n' for n, body in blocks)
            (wv / "params32.h").write_text("/* each wow64 thunk's own argument block, cut out of vulkan_thunks.c */\n"
                                           + "".join(f"struct p32_{n}\n{{\n{body}}};\n\n" for n, body in blocks)
                                           + "struct p32_is_available\n{\n    UINT32 handle;\n    UINT32 name;\n};\n\n"
                                           + "/* --layout: the size of every block and the offset of every field, as this (64-bit) compiler lays them out */\n"
                                           + "static void dump_layout(void)\n{\n" + layout + "}\n")
            common = ["cc", "-O0", "-g", "-fno-strict-aliasing", "-Wno-implicit-function-declaration", "-Wno-int-conversion", "-Wno-pragma-pack",
                      "-Wno-unused-value", "-D__WINESRC__", "-D_ACRTIMP=", "-DWINBASEAPI=", "-DWINE_UNIX_LIB", "-DWINE_IOS=1"]
            wv_flags = ["-include", str(inc / "config.h"), "-D_NTSYSTEM_", f"-I{inc}", f"-I{wv}", f"-I{here}", f"-I{wine / 'dlls/winevulkan'}", f"-I{wine / 'include'}"]
            w32_flags = ["-include", str(t / "build/win32u-unix/config_ios.h"), "-D_WIN32U_", '-DSYSTEMDLLPATH=""', f"-I{inc}", f"-I{here}",
                         f"-I{t / 'build/win32u-unix'}", f"-I{wine / 'dlls/win32u'}", f"-I{wine / 'include'}", f"-I{geninc}"]
            steps = [
                common + ["-dynamiclib", "-include", str(inc / "config.h"), f"-I{inc}", f"-I{here}", f"-I{wine / 'include'}", str(here / "mock_icd.c"), "-o", str(w / "libmockvk.dylib")],
                common + wv_flags + ["-c", str(wv / "vulkan.c"), "-o", str(w / "vulkan.o")],
                common + wv_flags + ["-c", str(here / "harness.c"), "-o", str(w / "harness.o")],
                common + w32_flags + ["-c", str(t / "build/win32u-unix/vulkan_ios.c"), "-o", str(w / "win32u.o")],
                common + w32_flags + ["-c", str(here / "win32u_stubs.c"), "-o", str(w / "stubs.o")],
                ["cc", str(w / "vulkan.o"), str(w / "harness.o"), str(w / "win32u.o"), str(w / "stubs.o"), "-o", str(w / "harness")],
            ]
            for cmd in steps:
                r = subprocess.run(cmd, capture_output=True, text=True)
                if r.returncode != 0:
                    errs = [l for l in r.stderr.splitlines() if "error" in l][:12]
                    return w, "\n".join(errs) or r.stderr[-1500:]
            return w, None

        def harness(w, mode):
            env = dict(os.environ, MADEIRA_VULKAN_LIBRARY=str(w / "libmockvk.dylib"), MOCKVK_MODE=mode)
            r = subprocess.run([str(w / "harness")] + (["-v"] if VERBOSE else []), capture_output=True, text=True, env=env)
            return r.returncode, r.stdout, r.stderr

        w, err = build("h")
        check("the harness builds: winevulkan's unix side and win32u's Vulkan layer as BCD builds them, for the host", err is None, err)
        if err is None:
            for mode, what in (("host", "MoltenVK's memory model (VK_EXT_external_memory_host)"),
                               ("placed", "a library with VK_EXT_map_memory_placed"),
                               ("none", "a library that can place no mapping (the refusal)")):
                code, out, errtext = harness(w, mode)
                lines = [l for l in out.splitlines() if l.startswith(("ok", "FAIL", "     "))]
                for l in lines:
                    print("     | " + l)
                if VERBOSE and errtext:
                    print(errtext)
                check(f"the harness passes with {what}", code == 0 and lines and lines[-1].startswith("ok  ") and not any(l.startswith("FAIL") for l in lines),
                      out[-2500:] + errtext[-1500:])
        # ---- the real i386 winevulkan.dll's argument blocks against the thunks' (when an unstripped one and llvm-dwarfdump are at hand)
        dll = Path(os.environ.get("BCD_VK32_I386_DLL", wine / "build-i386/dlls/winevulkan/i386-windows/winevulkan.dll"))
        dwarfdump = shutil.which("llvm-dwarfdump") or next((str(c) for c in (Path("/usr/local/opt/llvm/bin/llvm-dwarfdump"),
                                                                             Path("/opt/homebrew/opt/llvm/bin/llvm-dwarfdump")) if c.is_file()), None)
        if err is None and dll.is_file() and dwarfdump:
            r = subprocess.run([str(w / "harness"), "--layout"], capture_output=True, text=True)
            host = {}
            for line in r.stdout.splitlines():
                name, size, *fields = line.split()
                host[name] = (int(size), [(f.split("@")[0], int(f.split("@")[1])) for f in fields])
            d = subprocess.run([dwarfdump, "--debug-info", str(dll)], capture_output=True, text=True).stdout
            pe, cur, member = {}, None, None
            for line in d.splitlines():
                m = re.search(r"DW_TAG_(\w+)", line)
                if m:
                    if m.group(1) == "structure_type":
                        cur, member = {"name": None, "size": None, "fields": []}, None
                    elif m.group(1) == "member" and cur is not None:
                        member = [None, None]
                        cur["fields"].append(member)
                    else:
                        cur = member = None
                    continue
                if cur is None:
                    continue
                m = re.search(r'DW_AT_name\s+\("([^"]+)"\)', line)
                if m:
                    if member is not None:
                        member[0] = m.group(1)
                    else:
                        cur["name"] = m.group(1)
                        key = m.group(1)[:-len("_params")]
                        if m.group(1).endswith("_params") and not pe.get(key, {}).get("fields"):   # a bare declaration never replaces the definition
                            pe[key] = cur
                m = re.search(r"DW_AT_byte_size\s+\((0x[0-9a-f]+)\)", line)
                if m and member is None and cur["size"] is None:
                    cur["size"] = int(m.group(1), 16)
                m = re.search(r"DW_AT_data_member_location\s+\((0x[0-9a-f]+)\)", line)
                if m and member is not None:
                    member[1] = int(m.group(1), 16)
            both = sorted(set(host) & set(pe))
            bad = [n for n in both if host[n] != (pe[n]["size"], [tuple(f) for f in pe[n]["fields"]])]
            check(f"the i386 winevulkan.dll's argument blocks (its debug info) are laid out exactly as the unix thunks read them: {len(both)} of {len(host)} blocks compared",
                  len(both) >= 600 and not bad, [(n, host[n], pe[n]) for n in bad[:3]] or (len(host), len(pe)))
        else:
            print("skip the i386 block layout comparison: no unstripped i386 winevulkan.dll (BCD_VK32_I386_DLL, or wine/build-i386) or no llvm-dwarfdump")

        if CONTROL:
            w, err = build("control", patched=False)
            check("control: the harness builds with Wine's unchanged files too", err is None, err)
            if err is None:
                code, out, errtext = harness(w, "host")
                fault = [l for l in out.splitlines() if l.startswith("FAIL fault")]
                print("     | " + (fault[0] if fault else out.strip().splitlines()[-1] if out.strip() else "(no output)"))
                check("control: with Wine's unchanged thunks the 32-bit program's first guest pointer faults, and the harness names it",
                      code == 2 and fault and "a guest pointer that was not converted" in fault[0], out[-1500:])

print("PASS" if ok else "FAILED")
sys.exit(0 if ok else 1)

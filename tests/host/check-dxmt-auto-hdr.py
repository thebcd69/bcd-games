#!/usr/bin/env python3
"""Check the actual Auto HDR patch and Metal shader without running Wine.

Uses an isolated copy of the pin, verifies idempotence and atomic rejection of
moved anchors, compiles the production shader for iOS 18 and macOS, runs its
actual present pass on a Metal GPU, and syntax-checks the iOS bridge/presenter.
"""
import hashlib
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
DXMT = Path(os.environ.get("DXMT_SRC_DIR", ROOT / "dxmt"))
PATCH = ROOT / "tools/patch-dxmt-auto-hdr.py"
FILES = ["dxmt/dxmt_presenter.hpp", "dxmt/dxmt_presenter.cpp", "dxmt/dxmt_command.metal",
         "winemetal/unix/winemetal_unix.c"]


def run(args, **kwargs):
    result = subprocess.run([str(a) for a in args], capture_output=True, text=True, **kwargs)
    if result.returncode:
        print(result.stdout + result.stderr)
        raise RuntimeError(f"command failed: {args}")
    return result.stdout


def digest(tree):
    return [hashlib.sha256((tree / "dxmt/src" / name).read_bytes()).digest() for name in FILES]


def function(source, signature):
    start = source.index(signature)
    return source[start:source.index("\n}", start) + 2]


def check_presenter(tree, core):
    compiler = shutil.which("c++")
    if not compiler:
        print("SKIP: C++ presenter state checks (compiler unavailable)")
        return
    cpp = (core / "dxmt_presenter.cpp").read_text()
    hpp = (core / "dxmt_presenter.hpp").read_text()
    types = re.search(r"struct DXMTPresentMetadata \{.*?\n};", hpp, re.S).group(0)
    types += "\n" + function((DXMT / "src/dxmt/dxmt_format.cpp").read_text(), "WMTPixelFormat\nForget_sRGB(")
    types += "\n" + function(cpp, "static WMTPixelFormat\nmadeira_layer_format(")
    (tree / "production_hdr_types.inc").write_text(types)
    methods = "\n".join(function(cpp, name) for name in [
        "bool\nPresenter::changeLayerProperties(", "bool\nPresenter::changeLayerColorSpace(",
        "void\nPresenter::changeHDRMetadata(", "Presenter::PresentState\nPresenter::synchronizeLayerProperties()"])
    start = cpp.index("  // madeira-bcd: auto HDR.")
    setup = cpp[start:cpp.index("  layer_.getProps", start)]
    methods += "\nvoid Presenter::setup() {\n" + setup + "}\n"
    (tree / "production_hdr_methods.inc").write_text(methods)
    exe = tree / "presenter-check"
    run([compiler, "-std=c++20", f"-I{tree}", f"-I{DXMT / 'src/winemetal'}",
         ROOT / "tests/host/auto_hdr_presenter.cpp", "-o", exe])
    print(run([exe]).strip())


def main():
    if not (DXMT / "src/dxmt/dxmt_presenter.cpp").is_file():
        print("SKIP: initialize the dxmt submodule")
        return
    with tempfile.TemporaryDirectory(prefix="bcd-auto-hdr-") as directory:
        tree = Path(directory)
        shutil.copytree(DXMT / "src", tree / "dxmt/src")
        (tree / "build").mkdir()
        shutil.copy(ROOT / "build/madeira_cfg.h", tree / "build/madeira_cfg.h")
        shutil.copytree(ROOT / "research/remote-metal", tree / "research/remote-metal")
        # Start from the pin even if the developer has already run the patches.
        for name in FILES:
            source = run(["git", "-C", DXMT, "show", f"HEAD:src/{name}"])
            (tree / "dxmt/src" / name).write_text(source)
        run([sys.executable, ROOT / "tools/patch-dxmt-layer-format.py"], cwd=tree)
        before = digest(tree)
        core = tree / "dxmt/src/dxmt"
        broken = core / "dxmt_command.metal"
        original = broken.read_text()
        broken.write_text(original.replace("[[fragment]] float4 fs_present_quad(", "[[fragment]] float4 moved("))
        moved = digest(tree)
        failure = subprocess.run([sys.executable, str(PATCH)], cwd=tree, capture_output=True)
        assert failure.returncode != 0 and digest(tree) == moved, "moved anchor partially mutated sources"
        broken.write_text(original)
        assert digest(tree) == before
        run([sys.executable, PATCH], cwd=tree)
        once = digest(tree)
        assert once != before
        run([sys.executable, PATCH], cwd=tree)
        assert digest(tree) == once, "patch is not idempotent"
        print("ok: patch applies, rejects drift without partial writes, and is idempotent")
        live = ROOT / "tools/patch-dxmt-hdr-live.py"
        run([sys.executable, live], cwd=tree)
        once = digest(tree)
        run([sys.executable, live], cwd=tree)
        assert digest(tree) == once, "live patch is not idempotent"
        print("ok: live HDR patch applies and is idempotent")
        check_presenter(tree, core)
        if not shutil.which("xcrun"):
            print("SKIP: Xcode shader/bridge checks (not on macOS)")
            return
        for sdk, target in [("iphoneos", "ios18.0"), ("macosx", "macos14.0")]:
            air = tree / f"{sdk}.air"
            run(["xcrun", "-sdk", sdk, "metal", "-std=metal3.1", f"--target=air64-apple-{target}",
                 "-c", core / "dxmt_command.metal", "-o", air])
            run(["xcrun", "-sdk", sdk, "metallib", air, "-o", tree / f"{sdk}.metallib"])
        print("ok: full production shader compiles for iOS 18 and macOS (Metal 3.1)")
        includes = [DXMT / "include", DXMT / "libs", tree / "dxmt/src/winemetal", DXMT / "src/airconv"]
        objc = ["xcrun", "-sdk", "iphoneos", "clang", "-fsyntax-only", "-x", "objective-c", "-arch", "arm64",
                "-miphoneos-version-min=18.0", "-fblocks"] + [f"-I{p}" for p in includes]
        run(objc + [tree / "dxmt/src/winemetal/unix/winemetal_unix.c"])
        native = ["xcrun", "-sdk", "iphoneos", "clang++", "-std=c++20", "-fsyntax-only", "-arch", "arm64",
                  "-miphoneos-version-min=18.0", "-fblocks", "-DDXMT_NATIVE=1", "-DDXMT_MADEIRA=1", "-DDXMT_IOS=1",
                  "-DDXMT_PAGE_SIZE=4096", "-DNOMINMAX", "-Wno-extern-c-compat"]
        includes += [DXMT / "src/nativemetal", DXMT / "src/util", core,
                     DXMT / "include/native/directx", DXMT / "include/native/windows"]
        # nativemetal's wineunixlib.h must win over the PE one.
        includes.insert(0, DXMT / "src/nativemetal")
        run(native + [f"-I{p}" for p in includes] + [core / "dxmt_presenter.cpp"])
        print("ok: production iOS Objective-C bridge and C++ presenter compile")
        exe = tree / "gpu-check"
        run(["swiftc", ROOT / "tests/host/auto_hdr_gpu.swift", "-o", exe])
        print(run([exe, tree / "macosx.metallib"]).strip())
        probe = tree / "probe-check"
        run(["xcrun", "clang", "-fobjc-arc", "-fblocks", f"-I{ROOT / 'app/Madeira'}",
             ROOT / "app/Madeira/BCDHDR.m", ROOT / "tests/host/auto_hdr_probe.m",
             "-framework", "Foundation", "-framework", "Metal", "-framework", "QuartzCore",
             "-framework", "AppKit", "-o", probe])
        print(run([probe]).strip())
        reading = (ROOT / "app/Madeira/BCDHDRMenu.swift").read_text()
        reading = reading.split("enum BCDHDRMenu")[0].replace("import SwiftUI", "import Foundation")
        source = tree / "main.swift"
        source.write_text(reading + (ROOT / "tests/host/auto_hdr_reading.swift").read_text())
        run(["swiftc", source, "-o", tree / "reading-check"])
        print(run([tree / "reading-check"]).strip())
    print("PASS: DXMT Auto HDR")


if __name__ == "__main__":
    main()

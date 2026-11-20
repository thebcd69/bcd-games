#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""Round 669 (agent D S4; The Wolf Among Us 4VHH72, build 665): tools/patch-wine-dinput-cdecl-callbacks.py routes every call of a program's
DirectInput callback through Wine's enum_callback_wrapper (a frame that restores ESP), so a cdecl callback no longer unbalances the stack of
Wine's ANSI shim. Checks: the script applies to the pinned dlls/dinput once, says "already" the second time, refuses a moved anchor and leaves
the files alone; no direct call of a program's callback is left at the 18 sites; the five-argument twin exists for both i386 compilers (MSVC
mode, which the farm uses, and GNU C); and, with llvm-mingw and the pinned tree, the patched files compile for i386 and aarch64 with no new
warning and the i386 ANSI EnumDevices callback calls the wrapper."""
import os, re, shutil, subprocess, sys, tempfile
from pathlib import Path

R = Path(__file__).resolve().parents[2]
ok = True


def check(what, cond, detail=""):
    global ok
    print(("ok   " if cond else "FAIL ") + what + ("" if cond or not detail else "\n     " + str(detail)[-1500:]))
    ok = ok and bool(cond)


script = R / "tools/patch-wine-dinput-cdecl-callbacks.py"
wine = next((c for c in (R / "wine", Path(os.environ.get("WINE_SRC", "/nonexistent")), Path.home() / "madeira-bcd/wine")
             if (c / "dlls/dinput/ansi.c").is_file() and (c / ".git").exists()), None)
FILES = ["dlls/dinput/dinput_private.h", "dlls/dinput/dinput.c", "dlls/dinput/ansi.c", "dlls/dinput/device.c", "dlls/dinput/joystick_hid.c"]
wf = (R / ".github/workflows/build-ipa.yml").read_text()
check("CI applies it for the i386 farm, after this check, and puts dlls/dinput back with the farm-only files",
      "python3 tests/host/check-dinput-cdecl-callbacks.py\n          python3 tools/patch-wine-dinput-cdecl-callbacks.py wine" in wf
      and "git -C wine checkout -- dlls/dinput" in wf
      and wf.index("patch-wine-dinput-cdecl-callbacks.py wine") < wf.index("Put back the files patched for the i386 farm only"))
if not wine:
    print("SKIP: no pinned wine checkout (wine/, WINE_SRC or ~/madeira-bcd/wine)")
else:
    def pinned(rel):
        return subprocess.run(["git", "-C", str(wine), "show", "HEAD:" + rel], capture_output=True, text=True, check=True).stdout
    with tempfile.TemporaryDirectory() as tmp:
        t = Path(tmp) / "w"
        headers = [h for h in subprocess.run(["git", "-C", str(wine), "ls-tree", "--name-only", "HEAD", "dlls/dinput/"], capture_output=True,
                                             text=True).stdout.split() if h.endswith(".h")]
        for rel in sorted(set(FILES) | set(headers)):   # every header beside them, so a header's own includes see the patched one
            (t / rel).parent.mkdir(parents=True, exist_ok=True)
            (t / rel).write_text(pinned(rel))
        r1 = subprocess.run([sys.executable, str(script), str(t)], capture_output=True, text=True)
        r2 = subprocess.run([sys.executable, str(script), str(t)], capture_output=True, text=True)
        check("applies once, then says already", r1.returncode == 0 and "applied" in r1.stdout and r2.returncode == 0 and "already" in r2.stdout,
              r1.stdout + r1.stderr + r2.stdout)
        txt = {rel: (t / rel).read_text() for rel in FILES}
        check("no program callback is called directly at the 18 sites",
              "params->callback(" not in txt["dlls/dinput/ansi.c"] and "params->callback(" not in txt["dlls/dinput/device.c"]
              and "callback( &info, context )" not in txt["dlls/dinput/device.c"]
              and "ret = callback( &object_params.instance" not in txt["dlls/dinput/dinput.c"]
              and "if (callback( &effect->" not in txt["dlls/dinput/joystick_hid.c"]
              and sum(t.count("enum_callback_wrapper") for t in txt.values()) >= 18 + 6)
        d = txt["dlls/dinput/dinput.c"]
        check("the five-argument twin exists for MSVC mode (the farm's) and GNU C, pushing 5 arguments and returning through leave",
              "__declspec(naked) BOOL enum_callback_wrapper5(" in d and "__ASM_GLOBAL_FUNC( enum_callback_wrapper5," in d
              and d.count("push [ebp+28]") == 1 and '"pushl 28(%ebp)\\n\\t"' in d)
        t2 = Path(tmp) / "m"
        for rel in FILES:
            (t2 / rel).parent.mkdir(parents=True, exist_ok=True)
            (t2 / rel).write_text(pinned(rel).replace("return params->callback( &info_a, params->ref );", "return params->callback(  &info_a, params->ref );"))
        before = {rel: (t2 / rel).read_text() for rel in FILES}
        r3 = subprocess.run([sys.executable, str(script), str(t2)], capture_output=True, text=True)
        check("a moved anchor is refused by name and nothing is written",
              r3.returncode != 0 and "patch-wine-dinput-cdecl-callbacks" in r3.stdout + r3.stderr
              and all((t2 / rel).read_text() == before[rel] for rel in FILES), r3.stdout + r3.stderr)
        tc = next(iter(sorted((Path.home() / "madeira-bcd/toolchains").glob("llvm-mingw-*/bin"))), None)
        inc = wine / "build-i386/include"
        if not tc or not inc.is_dir():
            print("SKIP: no llvm-mingw or no i386 build tree: the patched files are not compiled")
        else:
            env = dict(os.environ, PATH=str(tc) + os.pathsep + os.environ["PATH"])
            def cc(arch, src, out):
                tgt = "i686-windows" if arch == "i686" else "aarch64-windows"
                r = subprocess.run([f"{arch}-w64-mingw32-clang", "-c", str(src), "-o", str(out), f"-I{src.parent}", f"-I{inc}", f"-I{wine}/include",
                                    f"-I{wine}/include/msvcrt", "-D_UCRT", "-D__WINESRC__", "-DDIRECTINPUT_VERSION=0x0700", "-D__WINE_PE_BUILD",
                                    "-target", tgt, "--no-default-config", "-Wall", "-O2", "-fno-strict-aliasing"], capture_output=True, text=True, env=env)
                return r.returncode, r.stderr.count("warning:"), r.stderr
            for arch in ("i686", "aarch64"):
                for name in ("ansi", "device", "dinput", "joystick_hid"):
                    rp, wp, ep = cc(arch, wine / f"dlls/dinput/{name}.c", Path(tmp) / f"p-{arch}-{name}.o")
                    rn, wn, en = cc(arch, t / f"dlls/dinput/{name}.c", Path(tmp) / f"n-{arch}-{name}.o")
                    check(f"{arch} {name}.c compiles patched with no new warning", rn == 0 and wn <= wp, en[-1200:])
            dis = subprocess.run([str(tc / "llvm-objdump"), "-dr", str(Path(tmp) / "n-i686-ansi.o")], capture_output=True, text=True).stdout
            body = dis[dis.find("<_enum_devices_wtoa_callback@8>:"):]
            body = body[:body.find("\n\n")]
            check("i386: the ANSI EnumDevices callback calls the program through _enum_callback_wrapper", "_enum_callback_wrapper" in body, body[-800:])
print("PASS: dinput cdecl callbacks" if ok else "FAILED: dinput cdecl callbacks")
sys.exit(0 if ok else 1)

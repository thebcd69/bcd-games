#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""tools/check-farm-imports.py: the farm completeness guard (round 632, D17).

Synthetic farms of small PE files built here (PE32+ and PE32, static imports, delay imports by RVA and by VA, an api-set contract): the
guard passes a complete farm, and fails on a missing required file, a farm with too few files, an unreadable (truncated) module, a static import
no file resolves, and a baseline that does not know the farm; it accepts the static gaps the baseline lists; a new delay gap is a warning
with its importer, never a failure; a stale baseline line is a warning; --warn-only never fails; an api-set import resolves when the farm
ships apisetschema.dll. Then the real farms of the build clone (~/madeira-bcd/app/Madeira, or this tree's), where they are at hand: they must
pass the committed tools/farm-baseline.json. CI runs the guard on the three farms before packaging.
"""
from pathlib import Path
import json, os, struct, subprocess, sys, tempfile

root = Path(__file__).resolve().parents[2]
ok = True


def check(what, cond, detail=""):
    global ok
    print(("ok   " if cond else "FAIL ") + what + (("\n     " + str(detail)[:1500]) if (detail and not cond) else ""))
    ok = ok and bool(cond)


def make_pe(imports=(), delay=(), machine=0x8664, pe32=False, va_delay=False):
    opt_size = 224
    image_base = 0x140000000 if not pe32 else 0x400000
    names, strings = {}, bytearray()
    for n in list(imports) + list(delay):
        if n not in names:
            names[n] = len(strings)
            strings += n.encode() + b"\0"
    descriptors = len(imports) + 1
    delay_at = 20 * descriptors
    strings_at = delay_at + 32 * (len(delay) + 1)
    section = bytearray(strings_at) + strings
    for i, n in enumerate(imports):
        struct.pack_into("<IIIII", section, 20 * i, 0, 0, 0, 0x1000 + strings_at + names[n], 0)
    for i, n in enumerate(delay):
        name_rva = 0x1000 + strings_at + names[n]
        attributes, name_field = (0, (image_base + name_rva) & 0xFFFFFFFF) if va_delay else (1, name_rva)
        struct.pack_into("<IIIIIIII", section, delay_at + 32 * i, attributes, name_field, 0, 0, 0, 0, 0, 0)
    dos = b"MZ" + bytes(0x3A) + struct.pack("<I", 0x40)
    coff = struct.pack("<HHIIIHH", machine, 1, 0, 0, 0, opt_size, 0x2102)
    opt = bytearray(opt_size)
    struct.pack_into("<H", opt, 0, 0x10B if pe32 else 0x20B)
    if pe32:
        struct.pack_into("<I", opt, 28, image_base)
    else:
        struct.pack_into("<Q", opt, 24, image_base)
    struct.pack_into("<I", opt, 60, 0x400)
    struct.pack_into("<I", opt, 92 if pe32 else 108, 16)
    dirs = 96 if pe32 else 112
    if imports:
        struct.pack_into("<II", opt, dirs + 8, 0x1000, 20 * descriptors)
    if delay:
        struct.pack_into("<II", opt, dirs + 13 * 8, 0x1000 + delay_at, 32 * (len(delay) + 1))
    header = dos + b"PE\0\0" + coff + bytes(opt) + struct.pack("<8sIIIIIIHHI", b".idata", len(section), 0x1000, len(section), 0x400, 0, 0, 0, 0, 0x40000040)
    return header.ljust(0x400, b"\0") + bytes(section)


def tool(*args):
    r = subprocess.run([sys.executable, str(root / "tools/check-farm-imports.py")] + [str(a) for a in args], capture_output=True, text=True)
    return r.returncode, r.stdout + r.stderr


with tempfile.TemporaryDirectory(prefix="madeira-farm-") as td:
    t = Path(td)

    def farm(name, files):
        d = t / name
        d.mkdir(parents=True, exist_ok=True)
        for fname, data in files.items():
            (d / fname).write_bytes(data)
        return d

    baseline = {"version": 1, "farms": {"arm64ec-windows": {"minFiles": 4, "required": ["ntdll.dll", "kernel32.dll"],
                                                           "staticGaps": {"evr-optin.dll": ["dxva2.dll"]}, "delayGaps": {"a.dll": ["advpack.dll"]}},
                                        "i386-windows": {"minFiles": 2, "required": ["ntdll.dll"], "staticGaps": {}, "delayGaps": {}}}}
    (t / "baseline.json").write_text(json.dumps(baseline))
    good = {"ntdll.dll": make_pe(), "kernel32.dll": make_pe(["ntdll.dll"]), "user32.dll": make_pe(["KERNEL32.dll", "ntdll.dll"]),
            "evr-optin.dll": make_pe(["dxva2.dll", "ntdll.dll"]), "a.dll": make_pe(["ntdll.dll"], ["advpack.dll"]), "readme.txt": b"not a PE"}
    d = farm("arm64ec-windows", good)
    rc, out = tool("--baseline=" + str(t / "baseline.json"), d)
    check("a complete farm passes: names compare without case, a text file is not a module, the listed static and delay gaps are accepted",
          rc == 0 and "no unresolved static import" in out and "::error::" not in out and "::warning::" not in out, out)

    d = farm("arm64ec-windows", {})
    (d / "kernel32.dll").unlink()
    rc, out = tool("--baseline=" + str(t / "baseline.json"), d)
    check("a required file that is missing fails, by name", rc != 0 and "the required file kernel32.dll is missing" in out, out)
    (d / "user32.dll").unlink(); (d / "evr-optin.dll").unlink()
    rc, out = tool("--baseline=" + str(t / "baseline.json"), d)
    check("a farm with fewer files than its minimum fails (a farm step that wrote nothing)", rc != 0 and "has 3 files, at least 4 are expected" in out, out)
    for name in ("kernel32.dll", "user32.dll", "evr-optin.dll"):
        (d / name).write_bytes(good[name])

    (d / "bad.dll").write_bytes(make_pe(["ntdll.dll", "gone.dll"]))
    rc, out = tool("--baseline=" + str(t / "baseline.json"), d)
    check("a static import no file resolves fails and names the importer and the missing module",
          rc != 0 and "bad.dll imports gone.dll" in out, out)
    (d / "bad.dll").write_bytes(make_pe(["ntdll.dll", "api-ms-win-core-foo-l1-1-0.dll"]))
    rc, out = tool("--baseline=" + str(t / "baseline.json"), d)
    check("an api-set contract is a gap while the farm has no apisetschema.dll", rc != 0 and "api-ms-win-core-foo-l1-1-0.dll" in out, out)
    (d / "apisetschema.dll").write_bytes(make_pe())
    rc, out = tool("--baseline=" + str(t / "baseline.json"), d)
    check("and resolves once the farm ships apisetschema.dll", rc == 0, out)
    (d / "apisetschema.dll").unlink()
    (d / "bad.dll").unlink()

    (d / "d.dll").write_bytes(make_pe(["ntdll.dll"], ["newdelay.dll"]))
    (d / "e.dll").write_bytes(make_pe(["ntdll.dll"], ["newdelay32.dll"], pe32=True, va_delay=True, machine=0x14C))
    rc, out = tool("--baseline=" + str(t / "baseline.json"), d)
    check("a new delay-import gap (RVA form and the old VA form, PE32 and PE32+) is a warning with its importer and never a failure",
          rc == 0 and "::warning::arm64ec-windows: d.dll delay-imports newdelay.dll" in out and "::warning::arm64ec-windows: e.dll delay-imports newdelay32.dll" in out
          and "::error::" not in out, out)
    (d / "d.dll").unlink(); (d / "e.dll").unlink()

    (d / "half.dll").write_bytes(make_pe(["ntdll.dll"])[:0x300])
    rc, out = tool("--baseline=" + str(t / "baseline.json"), d)
    check("a truncated module fails as unreadable", rc != 0 and "not a readable PE file: half.dll" in out, out)
    (d / "half.dll").write_bytes(b"MZ")
    rc2, out2 = tool("--baseline=" + str(t / "baseline.json"), d)
    check("and so does a stub that is only a DOS header", rc2 != 0 and "half.dll" in out2, out2)
    rc3, out3 = tool("--baseline=" + str(t / "baseline.json"), "--warn-only", d)
    check("--warn-only turns every failure into a warning (exit 0)", rc3 == 0 and "::error::" not in out3 and "::warning::arm64ec-windows: not a readable" in out3, out3)
    (d / "half.dll").unlink()

    (d / "evr-optin.dll").write_bytes(make_pe(["ntdll.dll"]))
    rc, out = tool("--baseline=" + str(t / "baseline.json"), d)
    check("a gap the baseline lists but the farm no longer has is a stale-line warning, not a failure",
          rc == 0 and "which is resolved or gone now" in out and "evr-optin.dll -> dxva2.dll" in out, out)

    other = farm("aarch64-windows", {"ntdll.dll": make_pe()})
    rc, out = tool("--baseline=" + str(t / "baseline.json"), other)
    check("a farm the baseline does not know is refused", rc != 0 and "knows no farm called aarch64-windows" in out, out)
    i386 = farm("i386-windows", {"ntdll.dll": make_pe(machine=0x14C, pe32=True), "kernel32.dll": make_pe(["ntdll.dll"], machine=0x14C, pe32=True)})
    rc, out = tool("--baseline=" + str(t / "baseline.json"), i386)
    check("a 32-bit farm (PE32) is read too", rc == 0, out)
    rc, out = tool("--write-baseline", i386)
    entry = json.loads(out)["i386-windows"] if rc == 0 else {}
    check("--write-baseline prints a farm's entry (gaps found, 90 % of the files as the minimum)", entry.get("minFiles") == 1 and entry.get("staticGaps") == {}, out)

# the committed baseline and the real farms
baseline = json.loads((root / "tools/farm-baseline.json").read_text())
check("the committed baseline knows the three farms, each with required files, a minimum and gap lists",
      set(baseline["farms"]) == {"arm64ec-windows", "aarch64-windows", "i386-windows"}
      and all(f["required"] and f["minFiles"] > 50 and "staticGaps" in f and "delayGaps" in f for f in baseline["farms"].values()))
check("its accepted static gaps are the opt-in modules' (evr-optin, msi-optin) and the Bluetooth control panel's",
      set(baseline["farms"]["arm64ec-windows"]["staticGaps"]) == {"bthprops.cpl", "evr-optin.dll", "msi-optin.dll"}
      and baseline["farms"]["aarch64-windows"]["staticGaps"] == {} and baseline["farms"]["i386-windows"]["staticGaps"] == {})
wf = (root / ".github/workflows/build-ipa.yml").read_text()
step_at = wf.index("      - name: Verify the Windows farms are complete")
step = wf[step_at:wf.index("\n\n", step_at)]
# lead (build 632): the workflow runs it with --warn-only until a build of the new Wine pin has shown no finding
check("CI runs the guard (warn-only for now) on all three farms after the last farm step and before the archive, and runs this check",
      all("python3 tools/check-farm-imports.py --warn-only app/Madeira/%s" % f in step for f in ("arm64ec-windows", "aarch64-windows", "i386-windows"))
      and "python3 tests/host/check-farm-imports.py" in wf
      and wf.index("- name: Report vcruntime contents") < step_at < wf.index("- name: Verify all linked archives exist"))
for base in (root / "app/Madeira", Path.home() / "madeira-bcd/app/Madeira"):
    present = [f for f in ("arm64ec-windows", "aarch64-windows", "i386-windows") if (base / f).is_dir() and len(list((base / f).iterdir())) > 100]
    if len(present) == 3:
        for f in present:
            rc, out = tool(base / f)
            check("the real %s farm (%s) passes the committed baseline" % (f, base.parent.parent.name), rc == 0, out)
        break
else:
    print("skip: no complete farms at hand (the build clone's app/Madeira)")

print("PASS: farm completeness guard" if ok else "FAILED: farm completeness guard")
sys.exit(0 if ok else 1)

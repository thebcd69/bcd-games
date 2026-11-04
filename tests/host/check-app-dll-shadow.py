#!/usr/bin/env python3
"""env.MADEIRA_APP_DLL_SHADOW: named app-local x64 DLLs are refused like ucrtbase.dll.

Crysis 3 Remastered (build 547, log 2026-10-15 15:08:17) ships Microsoft's x64
vcruntime140.dll beside its exe but not msvcp140.dll. Wine's ARM64EC
msvcp140.dll (sysx64) then imported that x64 memcpy through a stub without an
exit thunk and branched to its own MZ header (c000001d, "MSVCP140.dll failed
to initialize"). build/ntdll-unix/virtual_ios.c's ios_ucrt_shadow_refused
gains an opt-in list: a plain x64 image named there, outside \\windows\\, is
reported as not loadable, so the loader binds the system's.

Checks:
  - ios_app_dll_listed and ios_path_in_windows, cut out of virtual_ios.c and
    compiled on the host: names match whole and in any case, with ';', ','
    or spaces between them; a prefix, a longer name or an empty list does not
    match; \\windows\\ anywhere in the path (any case, '/' too) counts, a
    folder named "windowsapps" or "mywindows" does not;
  - unset, nothing changes: the ucrtbase refusal and MADEIRA_APP_UCRT=1 work
    as before (the only early return keeps its old condition), and the new
    refusal needs the list and a path outside \\windows\\;
  - the gate before both stays: plain x64 (not hybrid, not a Wine builtin) in
    an x64 (ARM64EC) process;
  - build 580 (GTA IV Complete 13RSJQ/79ZMR7: Microsoft's x64 vcruntime140.dll in the
    prefix's C:\\windows\\system32): a NAMED dll that lies directly in
    \\windows\\system32\\ is refused too (ios_path_in_system32, compiled on the host:
    any case, '/' too; sysx64, syswow64, a folder below system32, a lookalike
    folder and a short path are not); a dll the switch does not name, or no
    switch at all, changes nothing;
  - CI runs this check; the settings catalog documents the switch.
The host compile is skipped when no C compiler is found.
"""
from pathlib import Path
import os
import shutil
import subprocess
import sys
import tempfile

R = Path(__file__).resolve().parents[2]
src = (R / "build/ntdll-unix/virtual_ios.c").read_text()
ok = True


def check(what, cond):
    global ok
    print(("ok   " if cond else "FAIL ") + what)
    ok = ok and bool(cond)


def cut(start, end_marker="\n}\n"):
    i = src.index(start)
    return src[i:src.index(end_marker, i) + len(end_marker)]


helpers = (cut("static BOOL ios_app_dll_listed(") + "\n" + cut("static BOOL ios_path_in_windows(") + "\n"
           + cut("static BOOL ios_path_in_system32("))
fn = cut("static BOOL ios_ucrt_shadow_refused(")

cc = shutil.which("cc") or shutil.which("clang") or shutil.which("gcc")
if not cc:
    print("SKIP host compile: no C compiler")
else:
    harness = r'''
#include <stdio.h>
#include <string.h>
typedef unsigned short WCHAR, USHORT;
typedef int BOOL;
#define TRUE 1
#define FALSE 0
''' + helpers + r'''
static int fails;
static void W(WCHAR *out, const char *s) { while ((*out++ = (unsigned char)*s++)); }
static void listed(const char *list, const char *name, int want) {
    WCHAR w[128]; W(w, name);
    if (ios_app_dll_listed(list, w, (USHORT)strlen(name)) != want) { printf("listed(\"%s\", %s) != %d\n", list, name, want); fails++; }
}
static void inwin(const char *path, int want) {
    WCHAR w[256]; W(w, path);
    if (ios_path_in_windows(w, (USHORT)strlen(path)) != want) { printf("in_windows(%s) != %d\n", path, want); fails++; }
}
static void insys(const char *path, int want) {
    WCHAR w[256]; W(w, path);
    const char *last = strrchr(path, '\\'); const char *l2 = strrchr(path, '/');
    if (l2 > last) last = l2;
    USHORT off = (USHORT)(last ? last - path + 1 : 0);
    if (ios_path_in_system32(w, off) != want) { printf("in_system32(%s) != %d\n", path, want); fails++; }
}
int main(void) {
    listed("vcruntime140.dll", "vcruntime140.dll", 1);
    listed("vcruntime140.dll", "VCRUNTIME140.DLL", 1);
    listed("VCRuntime140.dll", "vcruntime140.dll", 1);
    listed("msvcp140.dll;vcruntime140.dll", "vcruntime140.dll", 1);
    listed("msvcp140.dll, vcruntime140.dll", "vcruntime140.dll", 1);
    listed(" ;vcruntime140.dll; ", "vcruntime140.dll", 1);
    listed("vcruntime140.dll;vcruntime140_1.dll", "vcruntime140_1.dll", 1);
    listed("vcruntime140.dll", "vcruntime140_1.dll", 0);
    listed("vcruntime140_1.dll", "vcruntime140.dll", 0);
    listed("vcruntime140", "vcruntime140.dll", 0);
    listed("vcruntime140.dll", "vcruntime140.dl", 0);
    listed("", "vcruntime140.dll", 0);
    listed(";;", "vcruntime140.dll", 0);
    inwin("\\??\\C:\\windows\\sysx64\\vcruntime140.dll", 1);
    inwin("\\??\\C:\\WINDOWS\\System32\\vcruntime140.dll", 1);
    inwin("\\??\\unix/private/var/x/drive_c/windows/system32/a.dll", 1);
    inwin("\\??\\C:\\Program Files (x86)\\Steam\\steamapps\\common\\Crysis3Remastered\\Bin64\\vcruntime140.dll", 0);
    inwin("\\??\\C:\\Program Files\\WindowsApps\\x\\vcruntime140.dll", 0);
    inwin("\\??\\C:\\Games\\mywindows\\vcruntime140.dll", 0);
    inwin("\\windows", 0);
    /* build 580: directly in system32, and only there */
    insys("\\??\\C:\\windows\\system32\\vcruntime140.dll", 1);
    insys("\\??\\C:\\WINDOWS\\System32\\VCRUNTIME140.DLL", 1);
    insys("\\??\\unix/private/var/x/drive_c/windows/system32/vcruntime140.dll", 1);
    insys("\\windows\\system32\\a.dll", 1);
    insys("\\??\\C:\\windows\\sysx64\\vcruntime140.dll", 0);
    insys("\\??\\C:\\windows\\syswow64\\vcruntime140.dll", 0);
    insys("\\??\\C:\\windows\\system32\\drivers\\vcruntime140.dll", 0);
    insys("\\??\\C:\\windows\\system32\\wbem\\a.dll", 0);
    insys("\\??\\C:\\windows\\system\\vcruntime140.dll", 0);
    insys("\\??\\C:\\Games\\mywindows\\system32\\vcruntime140.dll", 0);
    insys("\\??\\C:\\Games\\windows\\system32x\\vcruntime140.dll", 0);
    insys("\\??\\C:\\Program Files (x86)\\Steam\\steamapps\\common\\Crysis3Remastered\\Bin64\\vcruntime140.dll", 0);
    insys("system32\\a.dll", 0);
    insys("a.dll", 0);
    printf("%s\n", fails ? "HARNESS FAILED" : "harness ok");
    return fails != 0;
}
'''
    with tempfile.TemporaryDirectory() as tmp:
        c = os.path.join(tmp, "t.c")
        exe = os.path.join(tmp, "t")
        open(c, "w").write(harness)
        b = subprocess.run([cc, "-std=c99", "-Wall", "-Werror", "-fsanitize=address,undefined", c, "-o", exe],
                           capture_output=True, text=True)
        if b.returncode:
            b = subprocess.run([cc, "-std=c99", "-Wall", "-Werror", c, "-o", exe], capture_output=True, text=True)
        check("the helpers compile on the host", b.returncode == 0)
        if b.returncode == 0:
            r = subprocess.run([exe], capture_output=True, text=True)
            print(r.stdout.strip())
            check("names match whole, in any case, with ; , or spaces between; \\windows\\ paths are recognised",
                  r.returncode == 0)
        else:
            print(b.stderr[-2000:])

gate = ('    if (pi->is_hybrid || pi->wine_builtin) return FALSE;\n'
        '    if (!wow32 && pi->machine != IMAGE_FILE_MACHINE_AMD64) return FALSE;\n'
        '    if (!wow32 && !ios_is_arm64ec_cur()) return FALSE;\n')
check("the gate: plain x64 images in an x64 (ARM64EC) process, and (round 630, O2 S1) plain i386 images",
      gate in fn and "const BOOL wow32 = (pi->machine == IMAGE_FILE_MACHINE_I386);" in fn)
check("unset, the early return keeps its old condition (MADEIRA_APP_UCRT=1 turns the ucrt refusal off); an i386 image without the "
      "key returns before any work (round 630)",
      'shadow = getenv( "MADEIRA_APP_DLL_SHADOW" );' in fn and
      "    if (shadow && !*shadow) shadow = NULL;\n    if (wow32 && !shadow) return FALSE;\n    if (opt && !shadow) return FALSE;\n" in fn and
      fn.index("if (wow32 && !shadow) return FALSE;") < fn.index("get_mapping_info( handle") and
      fn.count("return FALSE;") == 7)
check("the ucrtbase refusal is unchanged apart from its MADEIRA_APP_UCRT guard and stays x64-only",
      "    if (!wow32 && !opt && name && len == sizeof(ucrt) - 1)\n" in fn and
      '"[ucrt-shadow] %s: plain x64 ucrtbase.dll outside the system farm' in fn)
check("the line says which kind of DLL it refused", '"[app-dll-shadow] %s: plain %s DLL %s, named by "' in fn
      and 'debugstr_us(&nt_name), wow32 ? "i386" : "x64",' in fn)
blk = fn[fn.index("if (!ret && shadow"):]
check("the new refusal needs the list, a listed name and a path outside \\windows\\ (or directly in system32), and logs [app-dll-shadow]",
      blk.index("ios_app_dll_listed( shadow, name, len )") < blk.index("!ios_path_in_windows(") <
      blk.index("ios_path_in_system32( nt_name.Buffer, name_off )") <
      blk.index("ret = TRUE;") < blk.index('"[app-dll-shadow] %s:'))
check("the system32 case is part of the same listed-name condition, not a second refusal",
      blk.count("ret = TRUE;") == 1 and "(!ios_path_in_windows( nt_name.Buffer, nt_name.Length / sizeof(WCHAR) )\n"
      "            || ios_path_in_system32( nt_name.Buffer, name_off ))" in blk)
check("name_off is the offset of the file name in the NT path (taken before len is shortened)",
      "name_off = name ? (USHORT)(name - nt_name.Buffer) : 0;\n    if (name) len -= name - nt_name.Buffer;" in fn)
wf = (R / ".github/workflows/build-ipa.yml").read_text()
check("CI runs this check", "run: python3 tests/host/check-app-dll-shadow.py" in wf)
cat = (R / "app/Madeira/ConfigCatalog.generated.swift").read_text()
check("the catalog documents env.MADEIRA_APP_DLL_SHADOW (default empty)",
      'ConfigOption(key: "env.MADEIRA_APP_DLL_SHADOW", title: "Ignore these DLLs in the game\'s folder", '
      'kind: .text, defaultValue: ""' in cat)

print("PASS: app dll shadow" if ok else "FAILED: app dll shadow")
sys.exit(0 if ok else 1)

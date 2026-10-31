#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""[fault-dump]: the 64-bit guest fault report of d3d9-dxmt.dll (tools/d3d9-x64-fault-dump.{hpp,cpp}); no Wine runs.

Log: Darksiders II Deathinitive Edition (Steam 388410), build 595, iPad16,5 (Discord tester, 2026-10-23). With
env.MADEIRA_D3D9_X64 = dxmt the game gets a Direct3D 9 device (1112x832), plays its first intro video and dies right after the
second one opens: `mov rax,[rcx]` / `call [rax+0x40]` at Darksiders2.exe+0x1eb937 with RCX = [R12] = 0, R12 a slot on the main
thread's stack. The session log has the registers as numbers and 16 code bytes; it cannot say which object was NULL, which class
the function belongs to or who was meant to fill the slot. The report prints that (module+RVA, RTTI class names, the code around
the RIP as hex, the annotated stack, the call sites behind the return addresses, the stack around registers that point into it).

The owner's rule is that nothing that works today changes, so most of this file proves that the report cannot reach anything
else:
  - the glue is compiled by tools/build-d3d9-x64-dll.sh into d3d9-dxmt.dll and by nothing else (the i386 farm's build script,
    the DXMT patch scripts and every other workflow step do not name it), so the 32-bit Direct3D 9 DLLs cannot contain it;
  - it registers a handler only when MADEIRA_D3D9_LAST is 1/on/yes/true in the game's environment, the handler only observes
    (EXCEPTION_CONTINUE_SEARCH on every path), it is capped (4 reports, one per RIP, one at a time), reads memory only after
    VirtualQuery, writes nothing and takes no lock;
  - Darksiders II's list line sets it (from build 597); no other list does.
The portable core (the header) is then compiled for the host against fake memory laid out like the crash: the RIP, the 64 code
bytes the log held, an object whose vtable has MSVC RTTI, a return address behind a call, a NULL slot on the stack.
Set MINGW (llvm-mingw bin) and DXMT_SRC to also compile the glue for arm64ec; without them that step is skipped with a note.
"""
from pathlib import Path
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[2]
ok = True


def check(what, cond, detail=""):
    global ok
    print(("ok   " if cond else "FAIL ") + what + (("\n     " + detail) if (detail and not cond) else ""))
    ok &= bool(cond)


hpp = (root / "tools/d3d9-x64-fault-dump.hpp").read_text()
cpp = (root / "tools/d3d9-x64-fault-dump.cpp").read_text()
sh = (root / "tools/build-d3d9-x64-dll.sh").read_text()
wf = (root / ".github/workflows/build-ipa.yml").read_text()

# ---------------------------------------------------------------- where it is compiled
users = []
for p in list((root / "tools").glob("*")) + list((root / "build").rglob("*.sh")) + [root / ".github/workflows/build-ipa.yml"]:
    if p.is_file() and p.suffix in (".sh", ".py", ".yml", ".cpp", ".hpp", ".c", ".h", ""):
        try:
            t = p.read_text()
        except (UnicodeDecodeError, OSError):
            continue
        if "d3d9-x64-fault-dump" in t:
            users.append(str(p.relative_to(root)))
check("only the ARM64EC d3d9 build script (and its own files, this check, the workflow's check step) name the report: " + ", ".join(sorted(users)),
      set(users) <= {"tools/build-d3d9-x64-dll.sh", "tools/d3d9-x64-fault-dump.cpp", "tools/d3d9-x64-fault-dump.hpp", ".github/workflows/build-ipa.yml"})
check("the i386 farm's build script, the DXMT patch scripts and the Wine patch scripts do not name it",
      not any(("wine-i386" in u or "patch-dxmt" in u or "patch-wine" in u) for u in users))
check("the build script compiles it for the d3d9 flag set and links it after the DXMT objects (the DXMT source list and the meson.build comparison are untouched)",
      'job "$FAULT_DUMP_SRC" "$FAULT_DUMP_OBJ" d3d9' in sh and 'D3D9_OBJ+=("$FAULT_DUMP_OBJ")' in sh
      and sh.index('D3D9_OBJ+=("$FAULT_DUMP_OBJ")') > sh.index("run_jobs\n") and "D3D9_SRC=(d3d9 d3d9_buffer d3d9_census" in sh)
check("the build fails (Wine's d3d9.dll ships alone) when the report is missing from the result",
      'grep -aqF "rev=ajan17-ds2" "$OUT/d3d9.dll" || fail' in sh)
check("the workflow runs this check", "tests/host/check-d3d9-fault-dump.py" in wf)

# ---------------------------------------------------------------- the glue
check("the report arms only for MADEIRA_D3D9_LAST = 1/on/yes/true (the switch DXMT's own call ring uses)",
      'GetEnvironmentVariableA("MADEIRA_D3D9_LAST"' in cpp and 'strcmp(v, "1") && strcmp(v, "on") && strcmp(v, "yes") && strcmp(v, "true")' in cpp
      and cpp.count("AddVectoredExceptionHandler") == 1 and "return AddVectoredExceptionHandler(1, faultDumpVeh) != nullptr;" in cpp)
veh = cpp[cpp.index("LONG CALLBACK faultDumpVeh"):cpp.index("bool armFaultDump")]
returns = re.findall(r"return ([A-Z_]+);", veh)
check("the handler only observes: every return is EXCEPTION_CONTINUE_SEARCH", returns and set(returns) == {"EXCEPTION_CONTINUE_SEARCH"}, str(returns))
check("it looks at access violations, in-page errors and illegal instructions only",
      "EXCEPTION_ACCESS_VIOLATION" in veh and "EXCEPTION_IN_PAGE_ERROR" in veh and "EXCEPTION_ILLEGAL_INSTRUCTION" in veh
      and not re.search(r"EXCEPTION_(BREAKPOINT|SINGLE_STEP|INT_|FLT_|STACK_OVERFLOW)|0xE06D7363|0x406D1388", veh))
check("capped: 4 reports per process, never two for the same RIP, never two at once",
      "if (n <= 4)" in veh and "g_seen[4]" in cpp and "s.load(std::memory_order_relaxed) == rip" in veh and "g_busy.exchange(1)" in veh)
forbidden = ("WriteProcessMemory", "VirtualProtect", "VirtualAlloc", "ExitProcess", "TerminateProcess", "abort(", "exit(", "SuspendThread",
             "SetThreadContext", "fopen(", "CreateFile", "EnterCriticalSection", "GetModuleHandleEx", "GetModuleFileName", "LdrLockLoaderLock")
check("it writes, protects, stops and locks nothing (the module list is walked from the PEB without the loader lock)",
      not [f for f in forbidden if f in cpp + hpp], str([f for f in forbidden if f in cpp + hpp]))
check("memory is read only after VirtualQuery says the pages are committed and readable (one memcpy in the file)",
      cpp.count("memcpy((const void *)a") == 0 and cpp.count("memcpy(buf, (const void *)a, len)") == 1 and cpp.count("VirtualQuery(") == 1
      and cpp.index("VirtualQuery(") < cpp.index("memcpy(buf"))
check("the header has no Windows or DXMT dependency (it is compiled for the host below)",
      "windows.h" not in hpp and "log.hpp" not in hpp and "dxmt::" not in hpp)

# ---------------------------------------------------------------- the list line
rec = json.load(open(root / "recommended/recommendations.json"))
rules = rec.get("rules") or rec.get("lists") or []
ds = [r for r in rules if r.get("id") == "darksiders-ii-deathinitive"]
lines = []
if ds:
    for ln in ds[0]["config"]:
        if isinstance(ln, dict):
            lines.append((ln["line"], ln.get("minBuild")))
        elif not ln.startswith("#"):
            lines.append((ln, None))
d = dict(lines)
check("Darksiders II's list sets env.MADEIRA_D3D9_LAST = 1 from build 597", ("env.MADEIRA_D3D9_LAST = 1", 597) in lines, str(lines))
# Round 624 (agent A): Fallout 3 (seven logs, the same Gamebryo render path) carries New Vegas's lines, this one from build 598.
others = [r["id"] for r in rules if r.get("id") not in ("darksiders-ii-deathinitive", "fallout-new-vegas", "fallout-3") and any(
    "MADEIRA_D3D9_LAST" in (ln["line"] if isinstance(ln, dict) else ln) for ln in r.get("config", []))]
check("no other list sets it (Fallout: New Vegas's carries it too since build 598: four logs end in a NULL object of its own code; Fallout 3 since round 624)",
      not others, str(others))
swift = (root / "app/Madeira/GameRecommendationsList.swift").read_text()
check("the list policy knows the three names (the diagnostics) and takes 0 or 1 for the two switches, a count of 1000-100000 for the limit",
      all('"%s"' % n in swift for n in ("MADEIRA_D3D9_LAST", "MADEIRA_FILE_TRACE", "MADEIRA_FILE_TRACE_LIMIT"))
      and re.search(r'case "MADEIRA_D3D9_LAST", "MADEIRA_FILE_TRACE":\s*//[^\n]*\n\s*//[^\n]*\n\s*guard value == "0" \|\| value == "1"', swift)
      and 'case "MADEIRA_FILE_TRACE_LIMIT":' in swift and "n >= 1000, n <= 100000" in swift)

# ---------------------------------------------------------------- the core, compiled for the host against fake memory
cxx = shutil.which(os.environ.get("CXX", "c++")) or shutil.which("clang++")
TEST = r'''
#include "d3d9-x64-fault-dump.hpp"
#include <cassert>
#include <cstdlib>
#include <iostream>
#include <map>
#include <string>
#include <vector>
using namespace bcdfd;

struct Fake {
  std::map<uint64_t, unsigned char> mem; // a flat byte map: a later put overwrites an earlier one
  std::vector<Image> imgs;
  void put(uint64_t a, const void *p, size_t n) { for (size_t i = 0; i < n; i++) mem[a + i] = ((const unsigned char *)p)[i]; }
  template <class T> void put(uint64_t a, T v) { put(a, &v, sizeof v); }
  void bytes(uint64_t a, std::initializer_list<int> b) { uint64_t x = a; for (int v : b) mem[x++] = (unsigned char)v; }
  bool read(uint64_t a, void *buf, size_t len) {
    for (size_t i = 0; i < len; i++) { auto it = mem.find(a + i); if (it == mem.end()) return false; ((unsigned char *)buf)[i] = it->second; }
    return true;
  }
  bool image(uint64_t a, Image &o) { for (auto &i : imgs) if (a >= i.base && a < i.base + i.size) { o = i; return true; } return false; }
};
static int bad = 0;
#define CHECK(c, what) do { if (c) std::cout << "T ok   " << what << "\n"; else { std::cout << "T FAIL " << what << "\n"; bad++; } } while (0)

int main() {
  Fake m;
  Image exe; exe.base = 0x140000000ULL; exe.size = 0x217c000; strcpy(exe.name, "Darksiders2.exe");
  m.imgs.push_back(exe);
  Image bink; bink.base = 0x71f16b0000ULL; bink.size = 0x53000; strcpy(bink.name, "binkw64.dll");
  m.imgs.push_back(bink);

  // --- the code of the crash: the 64 bytes the session log held (32 before the RIP, 32 at it)
  m.bytes(0x1401eb917, {0x8b,0x01,0x8b,0xd6,0xff,0x10,0x48,0x8b,0x5d,0x50,0x48,0x83,0x7d,0xf0,0x00,0x74,0x1f,0xf3,0x0f,0x10,0x43,0x40,0x0f,0x2f,0x43,0x34,0x76,0x14,0x49,0x8b,0x0c,0x24,
                         0x48,0x8b,0x01,0xff,0x50,0x40,0x45,0x0f,0xb6,0xff,0x84,0xc0,0x44,0x0f,0x45,0xfe,0x80,0x7b,0x30,0x00,0x75,0x09,0x45,0x84,0xff,0x0f,0x84,0x96,0x01,0x00,0x00,0x41});
  // the rows before are readable zeros except one: a call site behind the return address 0x1401eb8d4: `call [rax+0x18]` = ff 50 18
  std::vector<unsigned char> z(0x400, 0x90);
  m.put(0x1401eb800, z.data(), z.size());
  m.bytes(0x1401eb917, {0x8b,0x01,0x8b,0xd6,0xff,0x10,0x48,0x8b,0x5d,0x50,0x48,0x83,0x7d,0xf0,0x00,0x74,0x1f,0xf3,0x0f,0x10,0x43,0x40,0x0f,0x2f,0x43,0x34,0x76,0x14,0x49,0x8b,0x0c,0x24,
                         0x48,0x8b,0x01,0xff,0x50,0x40,0x45,0x0f,0xb6,0xff,0x84,0xc0,0x44,0x0f,0x45,0xfe,0x80,0x7b,0x30,0x00,0x75,0x09,0x45,0x84,0xff,0x0f,0x84,0x96,0x01,0x00,0x00,0x41});
  m.bytes(0x1401eb8d4 - 3, {0xff,0x50,0x18});

  // --- an object with MSVC x64 RTTI: vtable 0x141471280 -> COL 0x141500000 -> type descriptor 0x141510000
  uint64_t vt = 0x141471280ULL, col = 0x141500000ULL, td = 0x141510000ULL;
  m.put<uint64_t>(vt - 8, col);
  uint32_t c[6] = {1, 0, 0, (uint32_t)(td - exe.base), 0, (uint32_t)(col - exe.base)};
  m.put(col, c, sizeof c);
  m.put<uint64_t>(td, 0x141400000ULL);
  m.put<uint64_t>(td + 8, 0);
  const char nm[] = ".?AVMovieScreen@Menu@@";
  m.put(td + 16, nm, sizeof nm);
  m.put<uint64_t>(vt, 0x1400a9f70ULL); m.put<uint64_t>(vt + 8, 0x1400a9f80ULL);
  uint64_t rbx = 0x7077b83048ULL;
  uint64_t objq[7] = {vt, 3, 0x64e5a1c839fb4d89ULL, 0, 0, 0, 1};
  m.put(rbx, objq, sizeof objq);

  // --- an object without RTTI: a vtable whose COL slot is garbage
  uint64_t vt2 = 0x141472000ULL; m.put<uint64_t>(vt2 - 8, 0x1234); m.put<uint64_t>(vt2, 0x1400b0000ULL);
  uint64_t plain = 0x70380abc10ULL; m.put<uint64_t>(plain, vt2); m.put<uint64_t>(plain + 8, 0);

  // --- the stack: rsp 0x703762ee20, base 0x7037630000; [rsp+0x18] a return address, r12 -> a slot holding 0
  uint64_t rsp = 0x703762ee20ULL, hi = 0x7037630000ULL, lo = 0x7036e30000ULL;
  std::vector<unsigned char> stk(0x1000, 0);
  m.put(rsp, stk.data(), stk.size());
  m.put<uint64_t>(rsp + 0x00, rbx);
  m.put<uint64_t>(rsp + 0x18, 0x1401eb8d4ULL);
  m.put<uint64_t>(rsp + 0x20, plain);
  m.put<uint64_t>(rsp + 0x28, 0x71f16b1234ULL);
  m.put<uint64_t>(rsp + 0x30, rsp + 0x100);
  uint64_t r12 = 0x703762eee8ULL;
  m.put<uint64_t>(r12, 0);
  char s[64] = "movies/logo_2.bik";
  m.put(0x7099000000ULL, s, sizeof s);
  m.put<uint64_t>(rsp + 0x38, 0x7099000000ULL);

  Regs g{};
  g.rax = 1; g.rcx = 0; g.rdx = plain; g.rbx = rbx; g.rsp = rsp; g.rbp = rsp + 0x40; g.rsi = 1; g.rdi = 0xffffffff;
  g.r8 = 0x1413fb608ULL; g.r12 = r12; g.r13 = 0x70416f7fa0ULL; g.r15 = 0x141ff6500ULL; g.rip = 0x1401eb937ULL;
  std::vector<std::string> out;
  auto emit = [&](const char *l) { out.push_back(l); };
  dumpFault(m, g, 1, 0xa8, 0xc0000005, 0, 0, lo, hi, emit);
  for (auto &l : out) std::cout << "L " << l << "\n";

  // --- unit checks of the pieces
  char b[128];
  CHECK(rttiName(m, rbx, b, sizeof b) && !strcmp(b, "Menu::MovieScreen"), "RTTI: .?AVMovieScreen@Menu@@ -> Menu::MovieScreen");
  CHECK(!rttiName(m, plain, b, sizeof b), "an object whose COL slot is not an RTTI locator has no class name");
  CHECK(!rttiName(m, 0, b, sizeof b) && !rttiName(m, 0x1234, b, sizeof b) && !rttiName(m, rsp + 1, b, sizeof b), "NULL, a small integer and an unaligned pointer have none");
  demangleRtti(".?AV?$Foo@H@@", b, sizeof b); CHECK(!strcmp(b, "?$Foo@H@@"), "a template name stays as the compiler wrote it, minus the prefix");
  demangleRtti(".?AUPlain@@", b, sizeof b); CHECK(!strcmp(b, "Plain"), ".?AU struct names work");
  CHECK(textAt(m, 0x7099000000ULL, b, sizeof b) && !strcmp(b, "movies/logo_2.bik"), "ASCII text is found");
  { char w[64] = {'S',0,'k',0,'i',0,'p',0}; m.put(0x70990000a0ULL, w, sizeof w);
    CHECK(textAt(m, 0x70990000a0ULL, b, sizeof b) && !strcmp(b, "Skip"), "UTF-16 text is found"); }
  CHECK(!textAt(m, rsp + 0x400, b, sizeof b), "zeros are not text");
  CHECK(isReturnAddress(m, 0x1401eb8d4ULL), "call [rax+0x18] ends at the return address (ff 50 18)");
  CHECK(isReturnAddress(m, 0x1401eb91dULL), "call [rax] (ff 10) ends at the return address");
  CHECK(!isReturnAddress(m, 0x1401eb8f0ULL), "nop padding is not a call");
  CHECK(!isReturnAddress(m, 0x100), "an address too small to have bytes before it is not one");
  Fake empty;
  std::vector<std::string> out2;
  dumpFault(empty, g, 2, 1, 0xc0000005, 1, 8, lo, hi, [&](const char *l) { out2.push_back(l); });
  bool u = false, e = false;
  for (auto &l : out2) { u |= l.find("unreadable") != std::string::npos; e |= l == "end"; }
  CHECK(u && e, "over memory that reads nothing the report still ends, with 'unreadable' rows (it cannot fault)");
  CHECK(out.size() > 40 && out.size() < 400, "the report is some tens of lines, not hundreds");
  return bad ? 1 : 0;
}
'''
if not cxx:
    check("a host C++ compiler is available for the core test", False)
else:
    with tempfile.TemporaryDirectory() as t:
        t = Path(t)
        (t / "test.cpp").write_text(TEST)
        exe = t / "test"
        r = subprocess.run([cxx, "-std=c++20", "-O1", "-Wall", "-Wextra", "-Wno-unused-parameter", "-I", str(root / "tools"), str(t / "test.cpp"), "-o", str(exe)],
                           capture_output=True, text=True)
        check("the core compiles for the host (-Wall -Wextra, no warning)", r.returncode == 0 and not r.stderr.strip(), r.stderr[-2000:])
        if r.returncode == 0:
            run = subprocess.run([str(exe)], capture_output=True, text=True)
            lines = run.stdout.splitlines()
            report = [l[2:] for l in lines if l.startswith("L ")]
            for l in lines:
                if l.startswith("T "):
                    check("unit: " + l[7:], l.startswith("T ok"))
            text = "\n".join(report)
            if os.environ.get("SHOW"):
                print(text)
            check("header: the exact RIP with module+RVA, the access and the stack bounds",
                  re.search(r"exception 0xc0000005, read of 0x0 at rip=0x1401eb937 \(Darksiders2\.exe\+0x1eb937\) rsp=0x703762ee20 stack=\[0x7036e30000,0x7037630000\)", text))
            check("rbx is named by its class from the RTTI", re.search(r"^rbx=0x7077b83048  obj<Menu::MovieScreen>$", text, re.M))
            check("rdx, an object without RTTI, is named by its vtable's first function ([v]=module+RVA)... or its vtable, not a class",
                  re.search(r"^rdx=0x70380abc10  \[v\]=Darksiders2\.exe\+0x1472000$", text, re.M))
            check("r8 (a module address) and r15 (a module address) print as module+RVA", "r8=0x1413fb608  Darksiders2.exe+0x13fb608" in text and "r15=0x141ff6500  Darksiders2.exe+0x1ff6500" in text)
            check("r12 prints as a stack address relative to rsp", "r12=0x703762eee8  rsp+0xc8" in text)
            check("rcx = 0 and the small integers are in the plain line", re.search(r"^plain registers:.* rcx=0x0 .*rsi=0x1 rdi=0xffffffff", text, re.M))
            check("the code window starts 256 bytes before the RIP and its 9th row is the faulting instruction (the bytes of the log)",
                  "code 0x1401eb837:" in text and "code 0x1401eb937: 48 8b 01 ff 50 40 45 0f b6 ff 84 c0 44 0f 45 fe 80 7b 30 00 75 09 45 84 ff 0f 84 96 01 00 00 41" in text)
            check("the row before the RIP ends with `mov rcx,[r12]` (49 8b 0c 24)", "code 0x1401eb917: 8b 01 8b d6 ff 10 48 8b 5d 50 48 83 7d f0 00 74 1f f3 0f 10 43 40 0f 2f 43 34 76 14 49 8b 0c 24" in text)
            check("the stack lists the saved rbx object, the return address (marked), the string and the binkw64 address",
                  "rsp+0x0 = 0x7077b83048  obj<Menu::MovieScreen>" in text and "rsp+0x18 = 0x1401eb8d4  Darksiders2.exe+0x1eb8d4  <== return address?" in text
                  and 'rsp+0x38 = 0x7099000000  "movies/logo_2.bik"' in text and "rsp+0x28 = 0x71f16b1234  binkw64.dll+0x1234" in text)
            check("the call site behind the return address is printed with the 96 bytes before it",
                  re.search(r"call site #1: return address 0x1401eb8d4 = Darksiders2\.exe\+0x1eb8d4, from \[rsp\+0x18\]", text) and "site 0x1401eb874:" in text)
            check("the stack around r12 is printed and shows [r12] = 0x0", re.search(r"^stack around r12=0x703762eee8 \(rsp\+0xc8\); \[r12\] = 0x0:$", text, re.M) and "r12+0x0: 0000000000000000" in text and "r12-0x60:" in text)
            check("an object with RTTI gets its first 8 qwords printed", re.search(r"^object rbx=0x7077b83048 obj<Menu::MovieScreen>, first 8 qwords:$", text, re.M))
            check("the report ends with 'end'", report and report[-1] == "end")
            check("every line is under 512 characters (the Logger takes whole lines)", all(len(l) < 512 for l in report))

# ---------------------------------------------------------------- the glue for arm64ec
mingw = Path(os.environ.get("MINGW") or root / "toolchains/llvm-mingw-20260421-ucrt-macos-universal/bin")
dxmt = Path(os.environ.get("DXMT_SRC", root / "dxmt"))
cxx64 = mingw / "arm64ec-w64-mingw32-clang++"
if cxx64.exists() and (dxmt / "src/util/log/log.hpp").exists():
    with tempfile.TemporaryDirectory() as t:
        o = Path(t) / "fd.o"
        inc = [f"-I{dxmt}/src/d3d9", f"-I{dxmt}/include", f"-I{dxmt}/libs", f"-I{dxmt}/src/dxmt", f"-I{dxmt}/src/util", f"-I{dxmt}/src/winemetal", f"-I{dxmt}/src/airconv"]
        r = subprocess.run([str(cxx64), "-std=c++20", "-O3", "-DNDEBUG", "-Wall", "-Wextra", "-Wno-unused-parameter", "-DNOMINMAX", "-D_WIN32_WINNT=0xa00",
                            "-DDXMT_IOS=1", "-DDXMT_PAGE_SIZE=4096", *inc, "-c", str(root / "tools/d3d9-x64-fault-dump.cpp"), "-o", str(o)],
                           capture_output=True, text=True)
        check("the glue compiles for arm64ec with the DXMT flags and -Wall -Wextra without a warning", r.returncode == 0 and not r.stderr.strip(), r.stderr[-2000:])
else:
    print("note: no arm64ec llvm-mingw (set MINGW) or DXMT tree (set DXMT_SRC): the glue's arm64ec compile is skipped")

print("PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)

#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""A Direct3D 11 event-class query that never completes (round 624, agent D2 S11; WRC 9 JDJ8KE = WV1P7T; tools/patch-dxmt-d11-query-stuck.py).

WRC 9's main thread called GetData 12.5 million times a second for two minutes on a TIMESTAMP_DISJOINT query that stayed pending. The
patch gives each polling thread a probe: [query-stuck] after 1.5 s of S_FALSE on one query (its event against the signalled one, the
queue, unsubmitted work; then every 10 s, 6 lines a process), and with DXMT_QUERY_TS_WATCHDOG=1 a TIMESTAMP or TIMESTAMP_DISJOINT query
pending 2 s is answered as complete until it is issued again (its data is DXMT's own anyway).

Checks: the workflow (right after the Direct3D 9 one, before the i386 farm key; CI runs this check); the whole CI patch chain applies with
it, once, then "already patched"; a moved anchor fails by name with nothing written; the probe runs after the event-state switch and before
the data is filled, for the three event-class types only; the accessor only reads; the probe cut from the patched file and compiled on the
host over a fake context and a fake clock, driven: nothing under 1.5 s, the line at 1.5 s and again 10 s later, 6 lines at most, the
watchdog off (never forced), on (a TIMESTAMP forced at 2 s and answered until re-issued; an EVENT never forced), a completed query resets
the probe; the patched d3d11_context_imm.cpp compiles for i686 and arm64ec (d3d11-src.dll) when the toolchain is there; the WRC 9 list
row and its policy entry. SKIP of the patch part (exit 0) when no dxmt sources are available.
"""
import importlib.util
import io
import json
import os
import pathlib
import re
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile

_spec = importlib.util.spec_from_file_location("dxmt_base", pathlib.Path(__file__).resolve().parent / "dxmt_base.py")
dxmt_base = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(dxmt_base)

R = pathlib.Path(__file__).resolve().parents[2]
src = pathlib.Path(os.environ.get("DXMT_SRC_DIR", R / "dxmt")) / "src"
ok = True


def check(what, cond, detail=""):
    global ok
    print(("ok   " if cond else "FAIL ") + what + (("\n     " + str(detail)[:1800]) if (detail and not cond) else ""))
    ok = ok and bool(cond)


wf = (R / ".github/workflows/build-ipa.yml").read_text()
scripts = re.findall(r"python3 (tools/patch-(?:dxmt|airconv|winemetal)-[\w.-]+\.py)", wf)
me = "tools/patch-dxmt-d11-query-stuck.py"
check("the workflow runs it right after the Direct3D 9 stuck-query patch, before the i386 farm key (which hashes tools/patch-dxmt-*.py)",
      me in scripts and scripts.index(me) == scripts.index("tools/patch-dxmt-d9-query-stuck.py") + 1
      and wf.index("python3 " + me) < wf.index("name: i386 farm revision")
      and "'tools/patch-dxmt-*.py'" in wf[wf.index("name: i386 farm revision"):][:600])
check("CI runs this check", "python3 tests/host/check-d11-query-stuck.py" in wf)
# A 64-bit game (WRC 9) runs DXMT's Direct3D 11 from d3d11-src.dll, which tools/build-d3d11-dll.sh builds from its own copy of the
# pinned sources before the workflow's patch steps: only its BCD_PATCHES reach it (build 624's first IPA lacked this one).
d11sh = (R / "tools/build-d3d11-dll.sh").read_text()
patch_text = (R / me).read_text()
check("d3d11-src.dll is built with it (build-d3d11-dll.sh's BCD_PATCHES, with the marker its code leaves in the DLL)",
      '"patch-dxmt-d11-query-stuck.py" "[query-stuck] madeira-bcd query="' in d11sh
      and 'WARN("[query-stuck] madeira-bcd query=",' in patch_text)
check("the IPA is refused when no 64-bit d3d11.dll carries it",
      any('"[query-stuck] madeira-bcd query="' in line for line in wf.splitlines()
          if line.strip().startswith('for m in "[copy-structure-count] skipped #"')))

recs = json.loads((R / "recommended/recommendations.json").read_text())
wrc = next((r for r in recs["rules"] if r["id"] == "wrc-9"), None)
check("the WRC 9 row: Steam 1267540, its folder and WRC9.exe; the source-built Direct3D 11 and the watchdog, both from build 624",
      wrc is not None and wrc["match"] == {"steamAppIDs": [1267540], "steamFolders": ["wrc 9 fia world rally championship"], "programs": [{"name": "wrc9.exe"}]}
      and {"line": "env.MADEIRA_D3D11_SRC = 1", "minBuild": 624} in wrc["config"] and {"line": "env.DXMT_QUERY_TS_WATCHDOG = 1", "minBuild": 624} in wrc["config"]
      and sorted(r["id"] for r in recs["rules"] if "DXMT_QUERY_TS_WATCHDOG" in json.dumps(r["config"])) == ["wrc-9"], wrc)
pol = (R / "app/Madeira/GameRecommendationsList.swift").read_text()
check("the list policy takes the key with the value 1 only", '"DXMT_QUERY_TS_WATCHDOG",   // build 624' in pol
      and 'case "DXMT_QUERY_TS_WATCHDOG":' in pol and 'guard value == "1" else {\n                    throw RecommendationsError.policy("\\(path): DXMT_QUERY_TS_WATCHDOG is 1")' in pol)

if not (src / "d3d11" / "d3d11_context_imm.cpp").is_file():
    print(f"SKIP: no dxmt sources at {src} (check out the submodule or set DXMT_SRC_DIR)")
    print("PASS: d3d11 stuck queries (static part)" if ok else "FAIL: d3d11 stuck queries")
    sys.exit(0 if ok else 1)

FILES = ("d3d11/d3d11_query.hpp", "d3d11/d3d11_context_imm.cpp")
with tempfile.TemporaryDirectory() as tmp:
    t = pathlib.Path(tmp)
    arc = subprocess.run(["git", "-C", str(src.parent), "archive", "--format=tar", dxmt_base.rev(src.parent), "src"], capture_output=True)
    if arc.returncode == 0 and arc.stdout:
        (t / "dxmt").mkdir()
        with tarfile.open(fileobj=io.BytesIO(arc.stdout)) as tf:
            try:
                tf.extractall(t / "dxmt", filter="data")
            except TypeError:
                tf.extractall(t / "dxmt")
    else:
        shutil.copytree(src, t / "dxmt/src")
    (t / "build").mkdir()
    shutil.copy(R / "build/madeira_cfg.h", t / "build/madeira_cfg.h")
    shutil.copytree(R / "research/remote-metal", t / "research/remote-metal")
    s_ = t / "dxmt/src"
    before, after = {}, {}
    chain_ok = True
    for s in scripts:
        if s == me:
            before = {f: (s_ / f).read_text() for f in FILES}
        p = subprocess.run([sys.executable, str(R / s)], cwd=t, capture_output=True, text=True)
        if p.returncode:
            chain_ok = False
            check(f"patch {s} applies in the chain", False, (p.stdout + p.stderr).strip())
        if s == me:
            p2 = subprocess.run([sys.executable, str(R / s)], cwd=t, capture_output=True, text=True)
            check("applies once, then says already patched",
                  p.returncode == 0 and "patched" in p.stdout and p2.returncode == 0 and "already patched" in p2.stdout, p.stdout + p2.stdout)
            after = {f: (s_ / f).read_text() for f in FILES}
    check("the whole CI patch chain applies with it", chain_ok and bool(after))

    work = t / "moved"
    shutil.copytree(t / "dxmt", work / "dxmt")
    for f, text in before.items():
        (work / "dxmt/src" / f).write_text(text)
    moved = before["d3d11/d3d11_query.hpp"].replace("  virtual EventState CheckEventState(uint64_t coherent_seq_id) = 0;\n",
                                                    "  virtual EventState CheckEventState(uint64_t coherent_seq) = 0;\n", 1)
    (work / "dxmt/src/d3d11/d3d11_query.hpp").write_text(moved)
    p3 = subprocess.run([sys.executable, str(R / me)], cwd=work, capture_output=True, text=True)
    check("a moved anchor fails by name and writes nothing",
          p3.returncode != 0 and "MTLD3D11EventQuery::CheckEventState" in p3.stdout + p3.stderr
          and (work / "dxmt/src/d3d11/d3d11_query.hpp").read_text() == moved
          and (work / "dxmt/src/d3d11/d3d11_context_imm.cpp").read_text() == before["d3d11/d3d11_context_imm.cpp"], p3.stdout + p3.stderr)

    cpp = after["d3d11/d3d11_context_imm.cpp"]
    get = cpp[cpp.index("  GetData(ID3D11Asynchronous *pAsync, void *pData, UINT DataSize, UINT GetDataFlags) override {"):]
    get = get[:get.index("\n  }\n")]
    call = "      madeiraQueryProbe(pAsync, desc.Query, hr);"
    check("the probe runs after the event-state switch and before the data is filled, for EVENT, TIMESTAMP and TIMESTAMP_DISJOINT only",
          get.count(call) == 1 and get.index("case EventState::Invalid:") < get.index(call) < get.index("*static_cast<BOOL *>(pData) = (hr == S_OK);")
          and "if (desc.Query == D3D11_QUERY_EVENT || desc.Query == D3D11_QUERY_TIMESTAMP || desc.Query == D3D11_QUERY_TIMESTAMP_DISJOINT)\n" + call in get)
    hpp = after["d3d11/d3d11_query.hpp"]
    check("ShouldBeSignaledAt only reads (a default of 0 in the interface, the event id in the query)",
          "virtual uint64_t ShouldBeSignaledAt() const { return 0; }" in hpp
          and "uint64_t ShouldBeSignaledAt() const override { return should_be_signaled_at; }" in hpp)
    check("nothing else changes in the two files",
          re.sub(r"\n    /\* madeira-bcd d11-query-stuck-v1: the probe.*?madeiraQueryProbe\(pAsync, desc\.Query, hr\);", "",
                 re.sub(r"  /\* madeira-bcd d11-query-stuck-v1 \(round 624, agent D2 S11.*?\n  \}\n\n(  HRESULT\n  STDMETHODCALLTYPE\n  GetData)", r"\1", cpp, count=1, flags=re.S),
                 count=1, flags=re.S) == before["d3d11/d3d11_context_imm.cpp"]
          and hpp.replace("  /* madeira-bcd d11-query-stuck-v1 (round 624; tools/patch-dxmt-d11-query-stuck.py): the event id the query waits for, for\n"
                          "   * [query-stuck]. Reads only. */\n  virtual uint64_t ShouldBeSignaledAt() const { return 0; }\n", "")
             .replace("  uint64_t ShouldBeSignaledAt() const override { return should_be_signaled_at; }   /* madeira-bcd d11-query-stuck-v1 */\n\n", "")
          == before["d3d11/d3d11_query.hpp"])

    probe = cpp[cpp.index("  void\n  madeiraQueryProbe(ID3D11Asynchronous *query, D3D11_QUERY type, HRESULT &hr) {"):]
    probe = probe[:probe.index("\n  }\n") + 4]
    probe = probe.replace("std::chrono::duration_cast<std::chrono::nanoseconds>(std::chrono::steady_clock::now().time_since_epoch())\n          .count()",
                          "g_fake_ns")
    harness = r'''
#include <atomic>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <sstream>
#include <string>
typedef long HRESULT;
#define S_OK 0L
#define S_FALSE 1L
enum D3D11_QUERY { D3D11_QUERY_EVENT = 0, D3D11_QUERY_OCCLUSION = 1, D3D11_QUERY_TIMESTAMP = 2, D3D11_QUERY_TIMESTAMP_DISJOINT = 3 };
struct ID3D11Asynchronous { virtual ~ID3D11Asynchronous() {} };
struct MTLD3D11EventQuery : ID3D11Asynchronous { uint64_t want = 0; uint64_t ShouldBeSignaledAt() const { return want; } };
static uint64_t g_fake_ns;
static unsigned g_lines;
template <typename... A> static void warn_line(A... a) { std::ostringstream o; (o << ... << a); printf("%s\n", o.str().c_str()); g_lines++; }
#define WARN(...) warn_line(__VA_ARGS__)
struct Queue {
  uint64_t SignaledEventSeqId() { return 8811; } uint64_t GetCurrentEventSeqId() { return 8812; }
  uint64_t CurrentSeqId() { return 7024; } uint64_t CoherentSeqId() { return 7023; }
};
struct State { bool has_dirty_op_since_last_event = false; };
struct Ctx {
  Queue cmd_queue; State ctx_state; bool promote_flush = false;
@PROBE@
};
static Ctx ctx;
static HRESULT poll(MTLD3D11EventQuery *q, D3D11_QUERY type, HRESULT hr, uint64_t step_ns) {
  g_fake_ns += step_ns;
  ctx.madeiraQueryProbe(q, type, hr);
  return hr;
}
'''
    # the driver: every scenario prints what the caller got
    harness += r'''
static int eq(const char *a, const char *b) { while (*a && *a == *b) { a++; b++; } return *a == *b; }
int main(int argc, char **argv) {
  const char *scn = argc > 1 ? argv[1] : "";
  MTLD3D11EventQuery ts, ev;
  ts.want = 8812; ev.want = 9000;
  g_fake_ns = 1000000000ull;
  const uint64_t per_poll = 80;   /* 12.5 million polls a second */
  if (eq(scn, "report")) {
    uint64_t n = 0;
    for (; n < 65536ull * 400; n++) poll(&ts, D3D11_QUERY_TIMESTAMP_DISJOINT, S_FALSE, per_poll);   /* about 2.1 s */
    printf("after-2s lines=%u\n", g_lines);
    for (; n < 65536ull * 2400; n++) poll(&ts, D3D11_QUERY_TIMESTAMP_DISJOINT, S_FALSE, per_poll);  /* about 12.6 s */
    printf("after-12s lines=%u\n", g_lines);
    for (; n < 65536ull * 16000; n++) poll(&ts, D3D11_QUERY_TIMESTAMP_DISJOINT, S_FALSE, per_poll); /* about 84 s */
    printf("after-84s lines=%u\n", g_lines);
    return 0;
  }
  if (eq(scn, "watchdog")) {
    HRESULT hr = S_FALSE;
    uint64_t n = 0;
    for (; n < 65536ull * 440 && hr == S_FALSE; n++) hr = poll(&ts, D3D11_QUERY_TIMESTAMP_DISJOINT, S_FALSE, per_poll);   /* up to 2.3 s */
    printf("ts-forced-after-ms=%llu hr=%ld\n", (unsigned long long)((g_fake_ns - 1000000000ull) / 1000000), hr);
    printf("next-poll hr=%ld\n", poll(&ts, D3D11_QUERY_TIMESTAMP_DISJOINT, S_FALSE, per_poll));
    ts.want = 8813;   /* issued again */
    printf("reissued hr=%ld\n", poll(&ts, D3D11_QUERY_TIMESTAMP_DISJOINT, S_FALSE, per_poll));
    hr = S_FALSE;
    for (n = 0; n < 65536ull * 400; n++) hr = poll(&ev, D3D11_QUERY_EVENT, S_FALSE, per_poll);
    printf("event-after-2s hr=%ld\n", hr);
    printf("completed hr=%ld\n", poll(&ev, D3D11_QUERY_EVENT, S_OK, per_poll));
    return 0;
  }
  if (eq(scn, "short")) {
    for (uint64_t n = 0; n < 65536ull * 100; n++) poll(&ts, D3D11_QUERY_TIMESTAMP, S_FALSE, per_poll);   /* about 0.5 s */
    poll(&ts, D3D11_QUERY_TIMESTAMP, S_OK, per_poll);
    for (uint64_t n = 0; n < 65536ull * 200; n++) poll(&ts, D3D11_QUERY_TIMESTAMP, S_FALSE, per_poll);   /* 1 s more: a new wait */
    printf("short lines=%u\n", g_lines);
    return 0;
  }
  return 0;
}
'''
    cxx = shutil.which("c++") or shutil.which("clang++")
    (t / "h.cpp").write_text(harness.replace("@PROBE@", probe))
    b = subprocess.run([cxx, "-std=c++20", "-O1", "-Wall", "-Wextra", "-Werror", "-Wno-unused-function", "-o", str(t / "h"), str(t / "h.cpp")],
                       capture_output=True, text=True)
    check("the probe compiles on the host (C++20, -Werror) over a fake context and clock", b.returncode == 0, b.stderr[-2000:])
    if b.returncode == 0:
        env_off = {k: v for k, v in os.environ.items() if k != "DXMT_QUERY_TS_WATCHDOG"}
        r = subprocess.run([str(t / "h"), "report"], capture_output=True, text=True, env=env_off)
        lines = r.stdout.splitlines()
        stuck = [l for l in lines if l.startswith("[query-stuck]")]
        check("the line at 1.5 s, the fields of WRC 9's question (want, signaled, cur_event, the queue, dirty, promote)",
              stuck and re.match(r"\[query-stuck\] madeira-bcd query=0x[0-9a-f]+ type=3 polls=\d+ for 15\d\d ms \| want=8812 signaled=8811 "
                                 r"cur_event=8812 cur_seq=7024 coherent=7023 \| dirty=0 promote=0$", stuck[0]) is not None, lines[:3])
        check("again 10 s later, 6 lines at most a process, and no watchdog without the key",
              "after-2s lines=1" in lines and "after-12s lines=2" in lines and "after-84s lines=6" in lines
              and not any("[query-watchdog]" in l for l in lines), lines)
        r = subprocess.run([str(t / "h"), "watchdog"], capture_output=True, text=True, env=dict(env_off, DXMT_QUERY_TS_WATCHDOG="1"))
        lines = r.stdout.splitlines()
        forced = next((l for l in lines if l.startswith("ts-forced-after-ms=")), "")
        check("with DXMT_QUERY_TS_WATCHDOG=1: said once at the start, the timestamp query answered as complete at 2 s",
              lines and lines[0].startswith("[query-watchdog] madeira-bcd on:") and forced.endswith("hr=0")
              and 2000 <= int(forced.split("=")[1].split()[0]) < 2100
              and any(l.startswith("[query-watchdog] madeira-bcd query=") and "is answered as complete" in l for l in lines), lines)
        check("...answered at once until it is issued again; a new issue waits again; an EVENT query is never forced; S_OK resets",
              "next-poll hr=0" in lines and "reissued hr=1" in lines and "event-after-2s hr=1" in lines and "completed hr=0" in lines, lines)
        r = subprocess.run([str(t / "h"), "watchdog"], capture_output=True, text=True, env=dict(env_off, DXMT_QUERY_TS_WATCHDOG="2"))
        check("only the exact value 1 turns the watchdog on", not any(l.startswith("[query-watchdog]") for l in r.stdout.splitlines()), r.stdout)
        r = subprocess.run([str(t / "h"), "short"], capture_output=True, text=True, env=env_off)
        check("a query that completes within 1.5 s says nothing, and a new wait starts the clock again", "short lines=0" in r.stdout, r.stdout)

    mingw = pathlib.Path.home() / "madeira-bcd/toolchains/llvm-mingw-20260421-ucrt-macos-universal/bin"
    cc_json = pathlib.Path.home() / "madeira-bcd/dxmt/build-pe-i386/compile_commands.json"
    orig = str(pathlib.Path.home() / "madeira-bcd/dxmt")
    if cc_json.is_file():
        entries = [e for e in json.loads(cc_json.read_text()) if e["file"].endswith("src/d3d11/d3d11_context_imm.cpp")]
        if entries:
            e = entries[0]
            args = shlex.split(e["command"]) if "command" in e else list(e["arguments"])
            new, skip = [], False
            for a in args:
                if skip:
                    skip = False
                    continue
                if a in ("-o", "-MF", "-MQ", "-MT"):
                    skip = True
                    continue
                if a in ("-c", "-MD") or a.endswith("d3d11_context_imm.cpp"):
                    continue
                if a.startswith("-I"):
                    p_ = a[2:] if os.path.isabs(a[2:]) else os.path.normpath(os.path.join(e["directory"], a[2:]))
                    if p_.startswith(orig + "/src"):
                        p_ = p_.replace(orig, str(t / "dxmt"), 1)
                    a = "-I" + p_
                new.append(a)
            c = subprocess.run(new + ["-fsyntax-only", "-fno-color-diagnostics", str(s_ / "d3d11/d3d11_context_imm.cpp")], cwd=e["directory"],
                               capture_output=True, text=True)
            own = [l for l in c.stderr.splitlines() if "warning:" in l or "error:" in l]
            check("the patched d3d11_context_imm.cpp compiles for i686 (the 32-bit d3d11.dll); its only diagnostic is the old unused kName "
                  "of d3d11_context_impl.cpp", c.returncode == 0 and all("d3d11_context_impl.cpp" in l and "kName" in l for l in own), c.stderr[-1500:])
    else:
        print("skip the i686 compile: no ~/madeira-bcd/dxmt/build-pe-i386/compile_commands.json")
    cxx64 = mingw / "arm64ec-w64-mingw32-clang++"
    if cxx64.exists():
        d, top = t / "dxmt", src.parent
        flags = ["-std=c++20", "-O3", "-DNDEBUG", "-Wimplicit-fallthrough", "-Wno-missing-field-initializers", "-Wno-unused-parameter",
                 "-Wno-cast-function-type", "-Wno-unused-private-field", "-Wno-microsoft-exception-spec", "-Wno-extern-c-compat",
                 "-Wno-unused-const-variable", "-Wno-missing-braces", "-fblocks", "-DNOMINMAX", "-D_WIN32_WINNT=0xa00", "-DDXMT_IOS=1",
                 "-DDXMT_PAGE_SIZE=4096"]   # tools/build-d3d11-dll.sh's COMMON + CXXFLAGS
        inc = [f"-I{d}/src/d3d11", f"-I{d}/src/dxgi", f"-I{d}/src/dxmt", f"-I{d}/src/airconv", f"-I{d}/src/winemetal", f"-I{d}/src/util",
               f"-I{top}/include", f"-I{top}/libs"]
        c = subprocess.run([str(cxx64)] + flags + inc + ["-fsyntax-only", "-fno-color-diagnostics", str(s_ / "d3d11/d3d11_context_imm.cpp")],
                           capture_output=True, text=True)
        check("the patched d3d11_context_imm.cpp compiles for arm64ec (d3d11-src.dll) without a diagnostic", c.returncode == 0 and not c.stderr.strip(),
              c.stderr[-1500:])
    else:
        print(f"skip the arm64ec compile: no {cxx64}")

print("PASS: d3d11 stuck queries" if ok else "FAIL: d3d11 stuck queries")
sys.exit(0 if ok else 1)

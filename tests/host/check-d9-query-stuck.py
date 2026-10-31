#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""A Direct3D 9 query that never completes (round 624, agent D2 S18; Counter-Strike: Source W69RYK; tools/patch-dxmt-d9-query-stuck.py).

Counter-Strike: Source's render thread polled MTLD3D9Query::GetData 13.8 million times a second for good and no line said which query
or in which state. The patch adds [d3d9-query-stuck] (a query still pending after 1,048,576 polls says once what it waits for: building,
the GPU behind, or a lost occlusion result; 16 lines a process) and the opt-in watchdog DXMT_D9_QUERY_LOST_MS (an occlusion query
whose work retired that many ms ago without its result is answered as visible, until the next Issue(END)).

Checks: the workflow (after the texture-format patch, before the i386 farm key that hashes it; CI runs this check); the whole CI patch
chain applies with it, once, then "already patched"; a moved anchor fails by name with nothing written; the shape of what it adds
(the watchdog only for OCCLUSION and only with the key, before the poll count; the report at the 1,048,576th poll, capped; the reset in
Issue(END); debugState reads only); the decision function cut from the patched file, compiled on the host and driven over a table (the
GPU behind, too early, just in time, the key off, never ended, no END seq, a clock that went back); the patched d3d9_query.cpp
compiles for i686 (the 32-bit DLL) and arm64ec (d3d9-dxmt.dll) when the toolchain is there. SKIP of the patch part (exit 0) when no
dxmt sources are available.
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
me = "tools/patch-dxmt-d9-query-stuck.py"
check("the workflow runs it right after the texture-format patch, before the i386 farm key (which hashes tools/patch-dxmt-*.py)",
      me in scripts and scripts.index(me) == scripts.index("tools/patch-dxmt-d9-texfmt-log.py") + 1
      and wf.index("python3 " + me) < wf.index("name: i386 farm revision")
      and "'tools/patch-dxmt-*.py'" in wf[wf.index("name: i386 farm revision"):][:600])
check("CI runs this check", "python3 tests/host/check-d9-query-stuck.py" in wf)

if not (src / "d3d9" / "d3d9_query.cpp").is_file():
    print(f"SKIP: no dxmt sources at {src} (check out the submodule or set DXMT_SRC_DIR)")
    print("PASS: d3d9 stuck queries (static part)" if ok else "FAIL: d3d9 stuck queries")
    sys.exit(0 if ok else 1)

FILES = ("dxmt/dxmt_occlusion_query.hpp", "d3d9/d3d9_query.hpp", "d3d9/d3d9_query.cpp")
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
    moved = before["d3d9/d3d9_query.cpp"].replace("    m_poll_burst = 0;\n", "    m_poll_burst = 0u;\n", 1)
    (work / "dxmt/src/d3d9/d3d9_query.cpp").write_text(moved)
    p3 = subprocess.run([sys.executable, str(R / me)], cwd=work, capture_output=True, text=True)
    check("a moved anchor fails by name and writes nothing",
          p3.returncode != 0 and "Issue(END)'s poll reset" in p3.stdout + p3.stderr
          and (work / "dxmt/src/d3d9/d3d9_query.cpp").read_text() == moved
          and (work / "dxmt/src/d3d9/d3d9_query.hpp").read_text() == before["d3d9/d3d9_query.hpp"]
          and (work / "dxmt/src/dxmt/dxmt_occlusion_query.hpp").read_text() == before["dxmt/dxmt_occlusion_query.hpp"], p3.stdout + p3.stderr)

    cpp = after["d3d9/d3d9_query.cpp"]
    get = cpp[cpp.index("MTLD3D9Query::GetData(void *pData, DWORD dwSize, DWORD dwGetDataFlags) {"):]
    get = get[:get.index("\n}\n")]
    check("the watchdog runs only for an OCCLUSION query with the key set, after getDataImpl said S_FALSE and before the poll is counted",
          get.index("if (hr != S_FALSE) {") < get.index("if (m_type == D3DQUERYTYPE_OCCLUSION && madeira_d9_query_lost_ms()) {")
          < get.index("    m_polls_since_issue++;\n"))
    check("a lost query is answered as visible (0x7fffffff samples) and keeps that answer until the next Issue(END)",
          "uint64_t visible = 0x7fffffffull;" in get and "bool lost = m_lost_answered && m_ended;" in get and "m_lost_answered = true;" in get
          and "noteCompletion();" in get[get.index("m_lost_answered = true;"):][:80] and "return D3D_OK;" in get)
    check("the report comes at the 1,048,576th pending poll, 16 lines a process, with the three verdicts",
          "if (m_polls_since_issue == 1048576u) {" in get and "stuck_said.fetch_add(1, std::memory_order_relaxed) < 16" in get
          and "building: Issue(END) never came" in get and "the GPU is behind: coherent_seq < event_seq" in get
          and "lost: the work it was issued behind has retired, the result never came" in get
          and get.index("    m_polls_since_issue++;\n") < get.index("if (m_polls_since_issue == 1048576u) {"))
    issue = cpp[cpp.index("if (dwIssueFlags & D3DISSUE_END) {"):]
    check("Issue(END) starts a new window for the watchdog too", "    m_lost_answered = false;" in issue[:issue.index("return D3D_OK;")])
    occ = after["dxmt/dxmt_occlusion_query.hpp"]
    dbg = occ[occ.index("debugState(uint64_t *begin, uint64_t *end, uint64_t *issued) const {"):]
    dbg = dbg[:dbg.index("\n  }\n")]
    check("debugState only reads (a const method, three stores to the caller's pointers)", dbg.count("= seq_id_") == 3 and "seq_id_issued =" not in dbg)
    check("nothing else changes in the three files",
          re.sub(r"  /\* madeira-bcd d9-query-stuck-v1.*?\n  \}\n\n", "", occ, count=1, flags=re.S) == before["dxmt/dxmt_occlusion_query.hpp"]
          and after["d3d9/d3d9_query.hpp"].replace("  // madeira-bcd d9-query-stuck-v1 (round 624): DXMT_D9_QUERY_LOST_MS answered this OCCLUSION query as visible; until the next\n"
                                                   "  // Issue(END) every poll gets the same answer.\n  bool m_lost_answered = false;\n", "") == before["d3d9/d3d9_query.hpp"])

    fn = cpp[cpp.index("/* d9-query-lost-test:begin */"):cpp.index("/* d9-query-lost-test:end */")]
    harness = r'''
#include <cstdint>
#include <cstdio>
@FN@
int main() {
  const uint64_t ms = 1000000ull, t0 = 5000 * ms;
  /* ended, event_seq, coherent_seq, issue_ns, now_ns, limit_ms */
  printf("behind=%d\n", madeira_d9_query_lost(true, 7021, 7020, t0, t0 + 5000 * ms, 2000));     /* the GPU has not reached it */
  printf("early=%d\n", madeira_d9_query_lost(true, 7021, 7023, t0, t0 + 1999 * ms, 2000));      /* retired, 1.999 s */
  printf("due=%d\n", madeira_d9_query_lost(true, 7021, 7021, t0, t0 + 2000 * ms, 2000));        /* retired, 2 s */
  printf("off=%d\n", madeira_d9_query_lost(true, 7021, 7030, t0, t0 + 60000 * ms, 0));          /* the key off */
  printf("building=%d\n", madeira_d9_query_lost(false, 7021, 7030, t0, t0 + 60000 * ms, 2000)); /* never ended */
  printf("noseq=%d\n", madeira_d9_query_lost(true, 0, 7030, t0, t0 + 60000 * ms, 2000));        /* no END seq */
  printf("noissue=%d\n", madeira_d9_query_lost(true, 7021, 7030, 0, t0 + 60000 * ms, 2000));    /* completed (issue time cleared) */
  printf("back=%d\n", madeira_d9_query_lost(true, 7021, 7030, t0, t0 - ms, 2000));              /* a clock that went back */
  return 0;
}
'''
    cxx = shutil.which("c++") or shutil.which("clang++")
    (t / "h.cpp").write_text(harness.replace("@FN@", fn))
    b = subprocess.run([cxx, "-std=c++20", "-Wall", "-Wextra", "-Werror", "-Wno-unused-function", "-o", str(t / "h"), str(t / "h.cpp")],
                       capture_output=True, text=True)
    check("the watchdog's decision compiles on the host (C++20, -Werror)", b.returncode == 0, b.stderr[-1500:])
    if b.returncode == 0:
        got = dict(l.split("=") for l in subprocess.run([str(t / "h")], capture_output=True, text=True).stdout.split())
        check("decided only when the query ended, its work retired and the time passed; never with the key off, before END, after completion "
              "or when the clock went back",
              got == {"behind": "0", "early": "0", "due": "1", "off": "0", "building": "0", "noseq": "0", "noissue": "0", "back": "0"}, got)

    mingw = pathlib.Path.home() / "madeira-bcd/toolchains/llvm-mingw-20260421-ucrt-macos-universal/bin"
    cc_json = pathlib.Path.home() / "madeira-bcd/dxmt/build-pe-i386/compile_commands.json"
    if cc_json.is_file():
        entries = [e for e in json.loads(cc_json.read_text()) if e["file"].endswith("src/d3d9/d3d9_query.cpp")]
        orig = str(pathlib.Path.home() / "madeira-bcd/dxmt")
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
                if a in ("-c", "-MD") or a.endswith("d3d9_query.cpp"):
                    continue
                if a.startswith("-I"):
                    p_ = a[2:] if os.path.isabs(a[2:]) else os.path.normpath(os.path.join(e["directory"], a[2:]))
                    if p_.startswith(orig + "/src"):   # the patched sources; include/ and libs/ stay the checkout's (no patch touches them)
                        p_ = p_.replace(orig, str(t / "dxmt"), 1)
                    a = "-I" + p_
                new.append(a)
            c = subprocess.run(new + ["-fsyntax-only", "-fno-color-diagnostics", str(s_ / "d3d9/d3d9_query.cpp")], cwd=e["directory"],
                               capture_output=True, text=True)
            check("the patched d3d9_query.cpp compiles for i686 (the 32-bit d3d9.dll) without a diagnostic", c.returncode == 0 and not c.stderr.strip(),
                  c.stderr[-1500:])
    else:
        print("skip the i686 compile: no ~/madeira-bcd/dxmt/build-pe-i386/compile_commands.json")
    cxx64 = mingw / "arm64ec-w64-mingw32-clang++"
    if cxx64.exists():
        d = t / "dxmt"
        flags = ["-std=c++20", "-O3", "-DNDEBUG", "-Wimplicit-fallthrough", "-Wno-missing-field-initializers", "-Wno-unused-parameter",
                 "-Wno-cast-function-type", "-Wno-unused-private-field", "-Wno-microsoft-exception-spec", "-Wno-extern-c-compat",
                 "-Wno-unused-const-variable", "-Wno-missing-braces", "-fblocks", "-DNOMINMAX", "-D_WIN32_WINNT=0xa00", "-DDXMT_IOS=1",
                 "-DDXMT_PAGE_SIZE=4096"]   # tools/build-d3d9-x64-dll.sh's COMMON + CXXFLAGS
        top = src.parent   # include/ and libs/ of the checkout: the archive holds src/ only, and no patch touches them
        inc = [f"-I{d}/src/d3d9", f"-I{top}/include", f"-I{top}/libs", f"-I{d}/src/dxmt", f"-I{d}/src/util", f"-I{d}/src/winemetal", f"-I{d}/src/airconv"]
        c = subprocess.run([str(cxx64)] + flags + inc + ["-fsyntax-only", "-fno-color-diagnostics", str(s_ / "d3d9/d3d9_query.cpp")],
                           capture_output=True, text=True)
        check("the patched d3d9_query.cpp compiles for arm64ec (d3d9-dxmt.dll) without a diagnostic", c.returncode == 0 and not c.stderr.strip(),
              c.stderr[-1500:])
    else:
        print(f"skip the arm64ec compile: no {cxx64}")

print("PASS: d3d9 stuck queries" if ok else "FAIL: d3d9 stuck queries")
sys.exit(0 if ok else 1)

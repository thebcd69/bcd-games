#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
# Portions copyright 2026 Dragooon: the idea and the shape of the parking are willfaust/dxmt pull request #58, commit 3dacbe5b (see
# tools/patch-dxmt-d11-query-park.py); this check is BCD's own.
"""tools/patch-dxmt-d11-query-park.py: a burst of GetData polls on an event query parks on the queue's event (opt-in, owner's decision 2026-11-25).

env.MADEIRA_D3D11_QUERY_PARK = 1 (exactly "1"): once the polls of one EVENT, TIMESTAMP or TIMESTAMP_DISJOINT query come in a burst, GetData
parks the caller on the command queue's shared event, at most 1 ms, then answers. BCD's logs show games polling 100,000 to 15,000,000 times a
second (42 logs, 21 titles). Without the key not one byte of behaviour changes (the [gpu-perf] line included).

Checks (dxmt sources are read from a git commit, never a work tree: the pinned commit of the `dxmt` gitlink, and the overlay base the neighbouring
dxmt checks start from, from DXMT_SRC_DIR, the dxmt submodule or ~/madeira-local/kaynak/dxmt.git; without any, the source part is skipped):
  1. Wiring: the workflow runs it right after the stuck-query patch it builds on and before the i386 farm key (which hashes tools/patch-dxmt-*.py),
     and runs this check; tools/build-d3d11-dll.sh builds d3d11-src.dll with it (BCD_PATCHES, with the marker its code leaves in the DLL); the IPA is
     refused when no 64-bit d3d11.dll carries the marker; the catalog documents the key (default 0); credit; no e-mail address.
  2. The whole CI patch chain applies with it, on both revisions, once; a second run says "already patched"; it refuses to run before the
     stuck-query patch; a moved anchor fails by name with nothing written; a half-marked tree is refused.
  3. Nothing else changes: the lines the patch removes are exactly the poll counter test, the line buffer size and the unconditional ERR(line).
  4. The code, cut from the patched files and compiled on the host over fakes (a fake clock, a fake command queue): a one-off poll and a
     second poll never park; a third poll less than 250 us after the second parks (wait on the query's event value, 1 ms) and answers S_OK when
     the value was signalled, S_FALSE otherwise; polls 300 us apart never park; a query whose chunk has not been committed never parks; the flush
     GetData would do runs once, before the park, and not again; a new Issue starts the burst again; the switch is on only for exactly 1 and
     says so once; the probe of tools/patch-dxmt-d11-query-stuck.py, driven with parked polls of 1 ms, says [query-stuck] at 1.5 s and answers the
     timestamp watchdog at 2 s (without the flag it takes 65,536 polls); its behaviour without parked polls is the old one.
  5. The patched d3d11_context_imm.cpp and dxmt_command_queue.cpp compile for i686 (the 32-bit d3d11.dll) and arm64ec (d3d11-src.dll) with the
     owner's flags, with no diagnostic that the tree without this patch does not have (skipped without the toolchain).
NOT covered: a run on a device (nothing here shows what a game does with the key on), and the Metal wait itself.
"""
import difflib
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

R = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(R / "tests/host"))
import dxmt_base  # noqa: E402

ok = True


def check(what, cond, detail=""):
    global ok
    print(("ok   " if cond else "FAIL ") + what + (("\n     " + str(detail)[:2000]) if (detail and not cond) else ""))
    ok = ok and bool(cond)


me = "tools/patch-dxmt-d11-query-park.py"
stuck = "tools/patch-dxmt-d11-query-stuck.py"
wf = (R / ".github/workflows/build-ipa.yml").read_text()
scripts = re.findall(r"python3 (tools/patch-(?:dxmt|airconv|winemetal)-[\w.-]+\.py)", wf)

# ---------------------------------------------------------------- 1. wiring
check("the workflow runs it right after the stuck-query patch it builds on, before the i386 farm key (which hashes tools/patch-dxmt-*.py)",
      me in scripts and scripts.index(me) == scripts.index(stuck) + 1 and wf.index("python3 " + me) < wf.index("name: i386 farm revision")
      and "'tools/patch-dxmt-*.py'" in wf[wf.index("name: i386 farm revision"):][:600])
check("CI runs this check", "python3 tests/host/check-d11-query-park.py" in wf)
d11sh = (R / "tools/build-d3d11-dll.sh").read_text()
patch_text = (R / me).read_text()
check("d3d11-src.dll is built with it, after the stuck-query patch (build-d3d11-dll.sh's BCD_PATCHES, with the marker its code leaves in the DLL)",
      '"patch-dxmt-d11-query-park.py" "[query-park] madeira-bcd: "' in d11sh
      and d11sh.index('"patch-dxmt-d11-query-stuck.py"') < d11sh.index('"patch-dxmt-d11-query-park.py"')
      and 'WARN("[query-park] madeira-bcd: D3D11 event-query parking on' in patch_text)
check("the IPA is refused when no 64-bit d3d11.dll carries it",
      any('"[query-park] madeira-bcd: "' in line for line in wf.splitlines() if line.strip().startswith('for m in "[copy-structure-count] skipped #"')))
_argv = sys.argv
sys.argv = ["gen-config-catalog.py"]
spec_cat = importlib.util.spec_from_file_location("catalog", R / "build/tools/gen-config-catalog.py")
cat = importlib.util.module_from_spec(spec_cat)
spec_cat.loader.exec_module(cat)
sys.argv = _argv
entry = cat.OVERLAY.get("env.MADEIRA_D3D11_QUERY_PARK", {})
check("the config catalog documents the key: a bool, default off, source the patch script, only the exact value 1",
      entry.get("kind") == "bool" and entry.get("default") == "0" and entry.get("sources") == ["tools/patch-dxmt-d11-query-park.py"]
      and "exact value 1" in entry.get("note", ""), entry)
check("the files carry no e-mail address", re.search(r"[\w.+-]+@[\w-]+\.[a-z]{2,}", patch_text + pathlib.Path(__file__).read_text()) is None)
check("the credit names Dragooon and the pull request", "Dragooon" in patch_text and "pull request #58" in patch_text)
for n in ("THIRD-PARTY-NOTICES.md", "app/Madeira/legal/THIRD-PARTY-NOTICES.md", "app/Madeira/licenses/THIRD-PARTY-NOTICES.txt"):
    t = (R / n).read_text()
    check(f"{n} credits Dragooon for this take", "Dragooon" in t and ("patch-dxmt-d11-query-park.py" in t or n.endswith(".txt")))
recs = (R / "recommended/recommendations.json").read_text()
check("no recommended-list row turns it on (opt-in per game; the owner's decision)", "MADEIRA_D3D11_QUERY_PARK" not in recs)


# ---------------------------------------------------------------- the sources, from a commit
def git(repo, *args, binary=False):
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=not binary)


def find_repo():
    p = git(R, "ls-tree", "HEAD", "dxmt")
    m = re.match(r"160000 commit ([0-9a-f]{40})\s+dxmt", p.stdout or "")
    pin = m.group(1) if m else None
    for c in (os.environ.get("DXMT_SRC_DIR"), R / "dxmt", pathlib.Path.home() / "madeira-local/kaynak/dxmt.git"):
        if not c or not pathlib.Path(c).exists() or git(c, "rev-parse", "--git-dir").returncode:
            continue
        if pin is None:
            h = git(c, "rev-parse", "HEAD")
            if h.returncode == 0:
                pin = h.stdout.strip()
        if pin and git(c, "cat-file", "-e", pin + "^{commit}").returncode == 0:
            return pathlib.Path(c), pin
    return None, None


repo, pin = find_repo()
if repo is None:
    print("skip: no dxmt repository with the pinned commit here (DXMT_SRC_DIR, the dxmt submodule or ~/madeira-local/kaynak/dxmt.git)")
    print("PASS: d3d11 query parking (static part)" if ok else "FAIL: d3d11 query parking")
    sys.exit(0 if ok else 1)
revs = [pin]
wm = git(repo, "show", f"{pin}:src/winemetal/unix/winemetal_unix.c").stdout
if dxmt_base.PIN_MARKER in wm:   # the pin carries the fork's overlay wave: the neighbouring checks start from the base the scripts were written for
    if git(repo, "cat-file", "-e", dxmt_base.OVERLAY_BASE + "^{commit}").returncode == 0:
        revs.append(dxmt_base.OVERLAY_BASE)
print(f"note: dxmt revisions {', '.join(r[:9] for r in revs)} from {repo}")

FILES = ("d3d11/d3d11_query.hpp", "d3d11/d3d11_context_imm.cpp", "dxmt/dxmt_command_queue.hpp", "dxmt/dxmt_command_queue.cpp")


def make_root(t, rev):
    """A repository root of its own in t: dxmt/src from the commit, the build/ and research/ pieces and the patch scripts the chain reads."""
    arc = git(repo, "archive", "--format=tar", rev, "src", binary=True)
    (t / "dxmt").mkdir()
    with tarfile.open(fileobj=io.BytesIO(arc.stdout)) as tf:
        try:
            tf.extractall(t / "dxmt", filter="data")
        except TypeError:
            tf.extractall(t / "dxmt")
    (t / "build").mkdir()
    shutil.copy(R / "build/madeira_cfg.h", t / "build/madeira_cfg.h")
    shutil.copytree(R / "research/remote-metal", t / "research/remote-metal")
    (t / "madeira-d3d12/src/pe").mkdir(parents=True)
    shutil.copy(R / "madeira-d3d12/src/pe/madeira_graphics_entry.h", t / "madeira-d3d12/src/pe/madeira_graphics_entry.h")
    (t / "tools").mkdir()
    for s in set(scripts):   # the two scripts that locate their files from their own place then find them here
        shutil.copy(R / s, t / s)


def run(t, s, *args):
    return subprocess.run([sys.executable, str(t / s), *args], cwd=t, capture_output=True, text=True)


def read(t):
    return {f: (t / "dxmt/src" / f).read_text() for f in FILES}


results = {}
for rev in revs:
    label = rev[:9]
    with tempfile.TemporaryDirectory(prefix="madeira-d11-park-") as tmp:
        t = pathlib.Path(tmp)
        make_root(t, rev)
        before = None
        chain_ok = True
        for s in scripts:
            if s == me:
                before = read(t)
                snap = t / "snap"
                shutil.copytree(t / "dxmt", snap)   # the tree as the chain leaves it ahead of this script
            p = run(t, s)
            if p.returncode:
                chain_ok = False
                check(f"{label}: patch {s} applies in the chain", False, (p.stdout + p.stderr).strip())
            if s == me:
                p2 = run(t, s)
                check(f"{label}: it applies once, then says already patched",
                      p.returncode == 0 and "query parking patched" in p.stdout and p2.returncode == 0 and "already patched" in p2.stdout, p.stdout + p2.stdout)
                after = read(t)
        check(f"{label}: the whole CI patch chain applies with it", chain_ok and before is not None)
        if before is None:
            continue

        # it refuses to run before the stuck-query patch, and says so
        w = t / "w"
        w.mkdir()
        for sub in ("dxmt",):
            shutil.copytree(snap, w / sub)
        for f, text in before.items():
            (w / "dxmt/src" / f).write_text(text)
        shutil.copytree(t / "tools", w / "tools")
        # take the stuck-query marker out: the state before that script
        for f in ("d3d11/d3d11_query.hpp", "d3d11/d3d11_context_imm.cpp"):
            tx = (w / "dxmt/src" / f).read_text().replace("madeira-bcd d11-query-stuck-v1", "madeira-bcd d11-query-gone")
            (w / "dxmt/src" / f).write_text(tx)
        p3 = run(w, me)
        check(f"{label}: it refuses to run before tools/patch-dxmt-d11-query-stuck.py, by name, writing nothing",
              p3.returncode != 0 and "patch-dxmt-d11-query-stuck.py has not been applied" in p3.stdout + p3.stderr
              and all((w / "dxmt/src" / f).read_text().count("d11-query-park-v1") == 0 for f in FILES), p3.stdout + p3.stderr)

        # a moved anchor fails by name and writes nothing
        w2 = t / "w2"
        w2.mkdir()
        shutil.copytree(snap, w2 / "dxmt")
        shutil.copytree(t / "tools", w2 / "tools")
        imm = w2 / "dxmt/src/d3d11/d3d11_context_imm.cpp"
        moved = imm.read_text().replace("    if (++probe.polls % 65536)\n      return;\n", "    if (++probe.polls % 32768)\n      return;\n", 1)
        imm.write_text(moved)
        keep = {f: (w2 / "dxmt/src" / f).read_text() for f in FILES}
        p4 = run(w2, me)
        check(f"{label}: a moved anchor fails by name and writes nothing",
              p4.returncode != 0 and "madeiraQueryProbe's poll counter" in p4.stdout + p4.stderr
              and all((w2 / "dxmt/src" / f).read_text() == keep[f] for f in FILES), p4.stdout + p4.stderr)
        # a half-marked tree is refused
        w3 = t / "w3"
        w3.mkdir()
        shutil.copytree(snap, w3 / "dxmt")
        shutil.copytree(t / "tools", w3 / "tools")
        h = w3 / "dxmt/src/dxmt/dxmt_command_queue.hpp"
        h.write_text(h.read_text() + "\n// madeira-bcd d11-query-park-v1\n")
        keep3 = {f: (w3 / "dxmt/src" / f).read_text() for f in FILES}
        p5 = run(w3, me)
        check(f"{label}: a half-marked tree is refused and left alone",
              p5.returncode != 0 and "only some files carry the marker" in p5.stdout + p5.stderr
              and all((w3 / "dxmt/src" / f).read_text() == keep3[f] for f in FILES), p5.stdout + p5.stderr)

        # ------------------------------------------------------------ 3. nothing else changes
        removed = {}
        for f in FILES:
            a, b = before[f].splitlines(), after[f].splitlines()
            removed[f] = [x.strip() for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes()
                          if tag in ("replace", "delete") for x in a[i1:i2]]
        check(f"{label}: the only lines that go are the poll counter test, the line buffer size and the unconditional ERR(line)",
              removed == {"d3d11/d3d11_query.hpp": [], "d3d11/d3d11_context_imm.cpp": ["if (++probe.polls % 65536)", "return;"],
                          "dxmt/dxmt_command_queue.hpp": [], "dxmt/dxmt_command_queue.cpp": ["char line[800];"]}
              or removed == {"d3d11/d3d11_query.hpp": [], "d3d11/d3d11_context_imm.cpp": ["if (++probe.polls % 65536)"],
                             "dxmt/dxmt_command_queue.hpp": [], "dxmt/dxmt_command_queue.cpp": ["char line[800];"]}, removed)
        cpp = after["d3d11/d3d11_context_imm.cpp"]
        hpp = after["d3d11/d3d11_query.hpp"]
        qcpp = after["dxmt/dxmt_command_queue.cpp"]
        get = cpp[cpp.index("  GetData(ID3D11Asynchronous *pAsync, void *pData, UINT DataSize, UINT GetDataFlags) override {"):]
        get = get[:get.index("\n  }\n")]
        park_call = "      if (hr == S_FALSE && madeiraQueryParkEnabled())"
        check(f"{label}: GetData parks only inside the event-class case, after the event-state switch and before the probe and the data fill",
              get.count("madeiraParkOnEventQuery(") == 1
              and get.index("case D3D11_QUERY_EVENT:") < get.index("case EventState::Invalid:") < get.index(park_call)
              < get.index("madeiraQueryProbe(pAsync, desc.Query, hr);") < get.index("*static_cast<BOOL *>(pData) = (hr == S_OK);"))
        end = cpp[cpp.index("  End(ID3D11Asynchronous *pAsync) override {"):]
        end = end[:end.index("case D3D11_QUERY_OCCLUSION:")]
        check(f"{label}: End() records the chunk after both ways of issuing an event query",
              end.count("->SetIssueChunk(cmd_queue.CurrentSeqId());") == 1
              and end.index("->Issue(event_id);") < end.index("->Issue(cmd_queue.GetCurrentEventSeqId());") < end.index("SetIssueChunk"))
        check(f"{label}: the interface defaults never park (SetIssueChunk, IssueChunk, NotePendingPoll)",
              "virtual void SetIssueChunk(uint64_t chunk_seq_id) { (void)chunk_seq_id; }" in hpp and "virtual uint64_t IssueChunk() const { return 0; }" in hpp
              and "virtual bool NotePendingPoll(uint64_t now_ns) { (void)now_ns; return false; }" in hpp)
        check(f"{label}: the [gpu-perf] line is only extended when a poll parked",
              "  if (parked > 0) {" in qcpp and qcpp.count("ERR(line);") == 1 and "const double parked = take(g_perf.polls_parked)" in qcpp)
        results[rev] = (before, after, t)

        # ------------------------------------------------------------ 4. the code on the host (once, on the first revision)
        if rev != revs[0]:
            continue
        probe = cpp[cpp.index("  void\n  madeiraQueryProbe(ID3D11Asynchronous *query, D3D11_QUERY type, HRESULT &hr) {"):]
        probe = probe[:probe.index("\n  }\n") + 4]
        probe = probe.replace("std::chrono::duration_cast<std::chrono::nanoseconds>(std::chrono::steady_clock::now().time_since_epoch())\n          .count()", "g_fake_ns")
        park = cpp[cpp.index("  static bool\n  madeiraQueryParkEnabled() {"):cpp.index("  HRESULT\n  STDMETHODCALLTYPE\n  GetData(")]
        park = park.replace("std::chrono::steady_clock::now()", "fake_now()")
        consts = hpp[hpp.index("constexpr size_t kEventStallThreshold = 64;"):hpp.index("struct MTLD3D11EventQuery : public ID3D11Query {")]
        iface = hpp[hpp.index("  virtual uint64_t ShouldBeSignaledAt() const { return 0; }\n"):]
        iface = iface[:iface.index("\n};\n") + 1]
        impl = hpp[hpp.index("  void Issue(uint64_t current_seq_id) override {\n    state = QueryState::Issued;"):]
        impl = impl[:impl.index("\n};\n") + 1]
        harness = r'''
#include <atomic>
#include <chrono>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <sstream>
#include <string>
typedef long HRESULT;
typedef unsigned UINT;
#define S_OK 0L
#define S_FALSE 1L
#define D3D11_ASYNC_GETDATA_DONOTFLUSH 1u
enum D3D11_QUERY { D3D11_QUERY_EVENT = 0, D3D11_QUERY_OCCLUSION = 1, D3D11_QUERY_TIMESTAMP = 2, D3D11_QUERY_TIMESTAMP_DISJOINT = 3 };
struct ID3D11Asynchronous { virtual ~ID3D11Asynchronous() {} };
enum class EventState { Pending, Stall, Signaled, Invalid };
enum class QueryState { Undefined, Issued, Signaled };
@CONSTS@
static uint64_t g_fake_ns;
static std::chrono::steady_clock::time_point fake_now() { return std::chrono::steady_clock::time_point(std::chrono::nanoseconds(g_fake_ns)); }
static unsigned g_lines;
template <typename... A> static void warn_line(A... a) { std::ostringstream o; (o << ... << a); printf("%s\n", o.str().c_str()); g_lines++; }
#define WARN(...) warn_line(__VA_ARGS__)
struct MTLD3D11EventQuery : ID3D11Asynchronous {
  virtual void Issue(uint64_t current_seq_id) = 0;
  virtual EventState CheckEventState(uint64_t coherent_seq_id) = 0;
@IFACE@
};
struct FakeQuery : MTLD3D11EventQuery {
@IMPL@
};
struct PerfCounters { std::atomic<uint64_t> polls_event{0}, polls_other{0}, polls_parked{0}, park_ns{0}; };
static PerfCounters g_perf;
struct Stats { unsigned event_stall = 0; };
static uint64_t g_signaled = 40, g_cur_seq = 100, g_cur_event = 50;
static unsigned g_waits, g_flushes;
static uint64_t g_wait_value, g_wait_ms;
static bool g_signal_on_wait;
static uint64_t g_wait_cost_ns = 700000;
struct Event { void waitUntilSignaledValue(uint64_t v, uint64_t ms) { g_waits++; g_wait_value = v; g_wait_ms = ms; g_fake_ns += g_wait_cost_ns; if (g_signal_on_wait) g_signaled = v; } };
struct Queue {
  Stats stats; Event event;
  Stats &CurrentFrameStatistics() { return stats; }
  uint64_t CurrentSeqId() { return g_cur_seq; }
  uint64_t SignaledEventSeqId() { return g_signaled; }
  uint64_t GetCurrentEventSeqId() { return g_cur_event; }
  uint64_t CoherentSeqId() { return 7023; }
};
struct State { bool has_dirty_op_since_last_event = false; };
struct Ctx {
  Queue cmd_queue; State ctx_state; bool promote_flush = false;
  void Flush() { g_flushes++; }
@PARK@
@PROBE@
};
static Ctx ctx;
static FakeQuery q;
static void new_query(uint64_t event, uint64_t chunk) { q.Issue(event); q.SetIssueChunk(chunk); }
static HRESULT poll(uint64_t step_ns, UINT flags = 0) {
  g_fake_ns += step_ns;
  HRESULT hr = S_FALSE;
  if (q.CheckEventState(ctx.cmd_queue.SignaledEventSeqId()) == EventState::Signaled) hr = S_OK;
  if (hr == S_FALSE && ctx.madeiraQueryParkEnabled()) hr = ctx.madeiraParkOnEventQuery(&q, flags);
  return hr;
}
static int eq(const char *a, const char *b) { while (*a && *a == *b) { a++; b++; } return *a == *b; }
int main(int argc, char **argv) {
  const char *scn = argc > 1 ? argv[1] : "";
  g_fake_ns = 1000000000ull;
  if (eq(scn, "burst")) {
    new_query(50, 99);   /* issued in chunk 99, the queue is at chunk 100: committed */
    poll(100000); printf("poll1 waits=%u flushes=%u\n", g_waits, g_flushes);
    poll(100000); printf("poll2 waits=%u\n", g_waits);
    HRESULT hr = poll(100000); printf("poll3 waits=%u value=%llu ms=%llu hr=%ld\n", g_waits, (unsigned long long)g_wait_value, (unsigned long long)g_wait_ms, hr);
    printf("parked-flag=%d then=%d\n", (int)ctx.madeiraParkedPollTaken(), (int)ctx.madeiraParkedPollTaken());
    printf("perf parked=%llu ns=%llu\n", (unsigned long long)g_perf.polls_parked.load(), (unsigned long long)g_perf.park_ns.load());
    hr = poll(100000); printf("poll4 waits=%u (the park took 700 us: a new burst starts)\n", g_waits);
    poll(100000); g_signal_on_wait = true;
    hr = poll(100000); printf("poll6 waits=%u hr=%ld parked-flag=%d\n", g_waits, hr, (int)ctx.madeiraParkedPollTaken());
    return 0;
  }
  if (eq(scn, "signalled")) {
    g_signal_on_wait = true; new_query(50, 99);
    poll(1000); poll(1000); HRESULT hr = poll(1000);
    printf("hr=%ld waits=%u flag=%d\n", hr, g_waits, (int)ctx.madeiraParkedPollTaken());
    return 0;
  }
  if (eq(scn, "gap")) {
    new_query(50, 99);
    for (int i = 0; i < 20; i++) poll(300000);
    printf("waits=%u\n", g_waits);
    return 0;
  }
  if (eq(scn, "uncommitted")) {
    new_query(50, 100);   /* issued in the chunk being recorded: the queue is at chunk 100 */
    for (int i = 0; i < 20; i++) poll(1000);
    printf("waits=%u\n", g_waits);
    g_cur_seq = 101;   /* the chunk is out now */
    poll(1000);
    printf("after-commit waits=%u\n", g_waits);
    return 0;
  }
  if (eq(scn, "flush")) {
    new_query(50, 99);
    poll(1000, 0); poll(1000, 0); poll(1000, 0);
    printf("no-flag flushes=%u stalls=%u\n", g_flushes, ctx.cmd_queue.stats.event_stall);
    g_flushes = 0; ctx.cmd_queue.stats.event_stall = 0;
    UINT f = D3D11_ASYNC_GETDATA_DONOTFLUSH;
    poll(1000, f); poll(1000, f); poll(1000, f);
    printf("donotflush flushes=%u stalls=%u\n", g_flushes, ctx.cmd_queue.stats.event_stall);
    UINT g = 0; g_flushes = 0;
    ctx.madeiraParkOnEventQuery(&q, g);
    printf("flag-after=%u\n", g & D3D11_ASYNC_GETDATA_DONOTFLUSH);
    return 0;
  }
  if (eq(scn, "reissue")) {
    new_query(50, 99);
    poll(1000); poll(1000);
    new_query(51, 99);          /* the game issues the query again */
    poll(1000); poll(1000);
    printf("waits-after-reissue=%u\n", g_waits);
    poll(1000);
    printf("waits=%u value=%llu\n", g_waits, (unsigned long long)g_wait_value);
    return 0;
  }
  if (eq(scn, "defaults")) {   /* a query type that does not override the park hooks never parks */
    struct Plain : MTLD3D11EventQuery { void Issue(uint64_t) override {} EventState CheckEventState(uint64_t) override { return EventState::Pending; } } p;
    p.SetIssueChunk(7);
    printf("default chunk=%llu notepoll=%d\n", (unsigned long long)p.IssueChunk(), (int)p.NotePendingPoll(1));
    return 0;
  }
  if (eq(scn, "env")) { printf("%d\n", ctx.madeiraQueryParkEnabled() ? 1 : 0); return 0; }
  /* the probe of the stuck-query patch with parked polls: 1 ms a poll */
  MTLD3D11EventQuery *iq = &q;
  (void)iq;
  FakeQuery ts, ev;
  ts.Issue(8812); ev.Issue(9000);
  auto probe_poll = [&](FakeQuery *qq, D3D11_QUERY type, bool parked, uint64_t step_ns, HRESULT hr) {
    g_fake_ns += step_ns;
    if (parked && hr == S_FALSE) ctx.madeiraParkedFlag() = true;
    ctx.madeiraQueryProbe(qq, type, hr);
    return hr;
  };
  if (eq(scn, "parked-report")) {
    uint64_t n = 0;
    for (; n < 1400; n++) probe_poll(&ts, D3D11_QUERY_TIMESTAMP_DISJOINT, true, 1000000, S_FALSE);
    printf("after-1.4s lines=%u\n", g_lines);
    for (; n < 1600; n++) probe_poll(&ts, D3D11_QUERY_TIMESTAMP_DISJOINT, true, 1000000, S_FALSE);
    printf("after-1.6s lines=%u\n", g_lines);
    return 0;
  }
  if (eq(scn, "parked-watchdog")) {
    HRESULT hr = S_FALSE; uint64_t n = 0;
    for (; n < 2600 && hr == S_FALSE; n++) hr = probe_poll(&ts, D3D11_QUERY_TIMESTAMP_DISJOINT, true, 1000000, S_FALSE);
    printf("ts-forced-after-ms=%llu hr=%ld\n", (unsigned long long)(g_fake_ns - 1000000000ull) / 1000000ull, hr);
    hr = S_FALSE;
    for (n = 0; n < 2600; n++) hr = probe_poll(&ev, D3D11_QUERY_EVENT, true, 1000000, S_FALSE);
    printf("event-after-2.6s hr=%ld\n", hr);
    return 0;
  }
  if (eq(scn, "unparked-old")) {   /* without the flag the old rule: the clock once in 65,536 polls */
    uint64_t n = 0;
    for (; n < 65535; n++) probe_poll(&ts, D3D11_QUERY_TIMESTAMP_DISJOINT, false, 1000000, S_FALSE);   /* 65 s of 1 ms polls that did not park */
    printf("before-65536 lines=%u\n", g_lines);
    probe_poll(&ts, D3D11_QUERY_TIMESTAMP_DISJOINT, false, 1000000, S_FALSE);
    printf("at-65536 lines=%u\n", g_lines);
    return 0;
  }
  return 0;
}
'''
        src = (harness.replace("@CONSTS@", consts).replace("@IFACE@", iface).replace("@IMPL@", impl)
               .replace("@PARK@", park).replace("@PROBE@", probe))
        # the park helpers are static members of the class in the patched file; the harness class holds them the same way
        cxx = shutil.which("c++") or shutil.which("clang++")
        (t / "h.cpp").write_text(src)
        b = subprocess.run([cxx, "-std=c++20", "-O1", "-Wall", "-Wextra", "-Werror", "-Wno-unused-function", "-Wno-unused-parameter", "-o", str(t / "h"), str(t / "h.cpp")],
                           capture_output=True, text=True)
        check("the parking code and the probe compile on the host (C++20, -Werror) over a fake clock and command queue", b.returncode == 0, b.stderr[-3000:])
        if b.returncode == 0:
            base_env = {k: v for k, v in os.environ.items() if k not in ("MADEIRA_D3D11_QUERY_PARK", "DXMT_QUERY_TS_WATCHDOG")}
            on = dict(base_env, MADEIRA_D3D11_QUERY_PARK="1")

            def out(scn, env=on):
                return subprocess.run([str(t / "h"), scn], capture_output=True, text=True, env=env).stdout.splitlines()
            L = out("burst")
            check("a one-off poll and a second poll never wait; the third poll of a burst waits on the query's event value for 1 ms",
                  "poll1 waits=0 flushes=1" in L and "poll2 waits=0" in L and "poll3 waits=1 value=50 ms=1 hr=1" in L
                  and L[0] == "[query-park] madeira-bcd: D3D11 event-query parking on (env.MADEIRA_D3D11_QUERY_PARK = 1)", L)
            check("a poll that parked and stayed pending leaves the flag once; the counters count the park",
                  "parked-flag=1 then=0" in L and "perf parked=1 ns=700000" in L, L)
            check("when the value was signalled during the park the answer is S_OK and no flag stays",
                  "poll4 waits=1 (the park took 700 us: a new burst starts)" in L and "poll6 waits=2 hr=0 parked-flag=0" in L, L)
            check("a query that is signalled by the park answers S_OK at the third poll too", out("signalled") == ["hr=0 waits=1 flag=0"] or
                  out("signalled")[-1] == "hr=0 waits=1 flag=0", out("signalled"))
            check("polls 300 us apart (not a burst) never wait", out("gap")[-1] == "waits=0", out("gap"))
            check("a query whose chunk has not been committed never waits; it does once the chunk is out",
                  out("uncommitted")[-2:] == ["waits=0", "after-commit waits=1"], out("uncommitted"))
            check("GetData's flush runs once per poll before the park and DONOTFLUSH is set so the tail does not flush again; with the flag set by the game none",
                  out("flush")[-3:] == ["no-flag flushes=3 stalls=3", "donotflush flushes=0 stalls=0", "flag-after=1"], out("flush"))
            check("issuing the query again starts the burst again", out("reissue")[-2:] == ["waits-after-reissue=0", "waits=1 value=51"], out("reissue"))
            check("a query type without the hooks never parks (interface defaults)", out("defaults")[-1] == "default chunk=0 notepoll=0", out("defaults"))
            res = {}
            for val in (None, "", "0", "1", "11", "true", " 1", "2"):
                env = dict(base_env)
                if val is not None:
                    env["MADEIRA_D3D11_QUERY_PARK"] = val
                res[val] = subprocess.run([str(t / "h"), "env"], capture_output=True, text=True, env=env).stdout.split("\n")[:-1]
            check("unset, empty, 0, 11, true, ' 1' and 2 leave it off and say nothing; exactly 1 turns it on and says so once",
                  all(res[v] == ["0"] for v in (None, "", "0", "11", "true", " 1", "2"))
                  and res["1"] == ["[query-park] madeira-bcd: D3D11 event-query parking on (env.MADEIRA_D3D11_QUERY_PARK = 1)", "1"], res)
            L = out("parked-report", base_env)
            stuck_lines = [x for x in L if x.startswith("[query-stuck]")]
            check("with parked polls of 1 ms the probe says [query-stuck] at 1.5 s, not after 65,536 polls",
                  "after-1.4s lines=0" in L and "after-1.6s lines=1" in L and stuck_lines and re.search(r"for 15\d\d ms", stuck_lines[0]) is not None, L)
            L = out("parked-watchdog", dict(base_env, DXMT_QUERY_TS_WATCHDOG="1"))
            forced = next((x for x in L if x.startswith("ts-forced-after-ms=")), "")
            check("with parked polls the timestamp watchdog answers a timestamp query at 2 s, an EVENT query never",
                  forced.endswith("hr=0") and 2000 <= int(forced.split("=")[1].split()[0]) < 2100 and "event-after-2.6s hr=1" in L, L)
            L = out("unparked-old", base_env)
            check("polls that did not park keep the old rule (the clock is read once in 65,536 polls)",
                  [x for x in L if x.startswith(("before-", "at-"))] == ["before-65536 lines=0", "at-65536 lines=1"]
                  and any(x.startswith("[query-stuck]") and "polls=65536" in x for x in L), L)

        # ------------------------------------------------------------ 5. compiles
        mingw = pathlib.Path.home() / "madeira-bcd/toolchains/llvm-mingw-20260421-ucrt-macos-universal/bin"
        cc_json = pathlib.Path.home() / "madeira-bcd/dxmt/build-pe-i386/compile_commands.json"
        orig = str(pathlib.Path.home() / "madeira-bcd/dxmt")

        def diag_of(tree, rel):
            e = next((x for x in json.loads(cc_json.read_text()) if x["file"].endswith("src/" + rel)), None)
            if e is None:
                return None
            args, skip = [], False
            for a in shlex.split(e["command"]) if "command" in e else list(e["arguments"]):
                if skip:
                    skip = False
                    continue
                if a in ("-o", "-MF", "-MQ", "-MT"):
                    skip = True
                    continue
                if a in ("-c", "-MD") or a.endswith(rel.split("/")[-1]) or a.startswith("-fdiagnostics-color"):
                    continue
                if a.startswith("-I"):
                    p_ = a[2:] if os.path.isabs(a[2:]) else os.path.normpath(os.path.join(e["directory"], a[2:]))
                    if p_.startswith(orig + "/src"):
                        p_ = p_.replace(orig, str(tree), 1)
                    a = "-I" + p_
                args.append(a)
            c = subprocess.run(args + ["-fsyntax-only", "-fno-color-diagnostics", str(tree / "src" / rel)], cwd=e["directory"], capture_output=True, text=True)
            return c.returncode, sorted(re.sub(r"^.*?:\d+:\d+: ", "", x) for x in c.stderr.splitlines() if " warning:" in x or " error:" in x), c.stderr[-1500:]

        if cc_json.is_file():
            for rel in ("d3d11/d3d11_context_imm.cpp", "dxmt/dxmt_command_queue.cpp"):
                with_park = diag_of(t / "dxmt", rel)
                without = diag_of(snap, rel)
                check(f"{label}: {rel} compiles for i686 (the 32-bit d3d11.dll) with no diagnostic the tree without the patch lacks",
                      with_park and without and with_park[0] == 0 and without[0] == 0 and with_park[1] == without[1], (with_park, without))
        else:
            print("skip the i686 compile: no ~/madeira-bcd/dxmt/build-pe-i386/compile_commands.json")
        cxx64 = mingw / "arm64ec-w64-mingw32-clang++"
        if cxx64.exists():
            flags = ["-std=c++20", "-O3", "-DNDEBUG", "-Wimplicit-fallthrough", "-Wno-missing-field-initializers", "-Wno-unused-parameter",
                     "-Wno-cast-function-type", "-Wno-unused-private-field", "-Wno-microsoft-exception-spec", "-Wno-extern-c-compat",
                     "-Wno-unused-const-variable", "-Wno-missing-braces", "-fblocks", "-DNOMINMAX", "-D_WIN32_WINNT=0xa00", "-DDXMT_IOS=1",
                     "-DDXMT_PAGE_SIZE=4096"]   # tools/build-d3d11-dll.sh's COMMON + CXXFLAGS
            top = pathlib.Path(orig)
            for rel in ("d3d11/d3d11_context_imm.cpp", "dxmt/dxmt_command_queue.cpp"):
                got = {}
                for which, d in (("patched", t / "dxmt"), ("without", snap)):
                    inc = [f"-I{d}/src/d3d11", f"-I{d}/src/dxgi", f"-I{d}/src/dxmt", f"-I{d}/src/airconv", f"-I{d}/src/winemetal", f"-I{d}/src/util",
                           f"-I{top}/include", f"-I{top}/libs"]
                    c = subprocess.run([str(cxx64)] + flags + inc + ["-fsyntax-only", "-fno-color-diagnostics", str(d / "src" / rel)], capture_output=True, text=True)
                    got[which] = (c.returncode, sorted(re.sub(r"^.*?:\d+:\d+: ", "", x) for x in c.stderr.splitlines() if " warning:" in x or " error:" in x), c.stderr[-1500:])
                check(f"{label}: {rel} compiles for arm64ec (d3d11-src.dll) with no diagnostic the tree without the patch lacks",
                      got["patched"][0] == 0 and got["without"][0] == 0 and got["patched"][1] == got["without"][1], got)
        else:
            print(f"skip the arm64ec compile: no {cxx64}")

print("PASS: d3d11 query parking" if ok else "FAIL: d3d11 query parking")
sys.exit(0 if ok else 1)

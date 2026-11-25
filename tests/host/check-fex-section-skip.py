#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
# Portions copyright 2026 Dragooon: the idea and the shape of the skip are willfaust/FEX pull request #40, commit 71d78809 (see
# tools/patch-fex-ios-section-skip.py); this check is BCD's own.
"""tools/patch-fex-ios-section-skip.py: an unmapped section that was never executable takes no code invalidation (opt-in, owner's decision 2026-11-25).

MADEIRA_FEX_SECTION_SKIP=1 (exactly "1") leaves out InvalidateIntervalInternal for a freed section that does not intersect XIntervals
and does not touch a sub-floor image's window under either of its names, and samples its "[iOS-xrem]" line. Without the key not one
byte of behaviour changes.

Checks. The FEX sources are read from a git commit, never a work tree: the pinned commit of the `FEX` gitlink, from FEX_SRC, the FEX
submodule or the local bare repository (~/madeira-local/kaynak/FEX.git), whichever has it; without any, the source part is skipped.
  1. The patch applies to the pinned InvalidationTracker.cpp alone and in the build's order (both modules: tools/build-xtajit64.sh and
     tools/build-xtajit-wow64.sh run it, a refusal is a warning, both restore the file; every script behind it still applies; the DEP-promotion
     fix of tools/patch-fex-ios-dep-promote-stale.py applies with it in either order); a second run says "already in" and changes nothing;
     a moved anchor, and a half-applied file, are refused without writing.
  2. Nothing else changes: the lines after the skip's branch (XIntervals, RWXIntervals, the hot SMC pages, the return) are byte for byte
     the original's; InvalidateIntervalInternal still runs unless the skip applies; the original [iOS-xrem] via=section line is kept for
     every other case; the switch is read once and only the exact value 1 turns it on.
  3. The skip's decision, cut from the patched file and compiled on the host over fake XIntervals, IntervalsLock and sub-floor windows: a
     range that never was executable is skipped; one that intersects XIntervals (inside, across either end, around it) is not; one that
     touches a sub-floor window by its low or its real name is not (ARM64EC module), while the WOW64 module has no such windows; the
     switch off means the block never runs.
  4. The patched InvalidationTracker.cpp compiles for both modules with the owner's flags (compile databases of ~/madeira-bcd/FEX; skipped
     without them), with no diagnostic the unpatched file does not have.
  5. The overlay in the config catalog (default 0), the CI step, no e-mail address in the files, the credit.
NOT covered: building xtajit64.dll / xtajit.dll, and a run on a device (nothing here shows what a game does with the key on).
"""
from pathlib import Path
import importlib.util
import io
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile

root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(root / "tests/host"))
import fex_chain  # noqa: E402

ok = True


def check(what, cond, detail=""):
    global ok
    print(("ok   " if cond else "FAIL ") + what + (("\n     " + str(detail)[:2000]) if (detail and not cond) else ""))
    ok = ok and bool(cond)


me = "patch-fex-ios-section-skip.py"
patch_path = root / "tools" / me
spec = importlib.util.spec_from_file_location("section_skip", patch_path)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

# ---------------------------------------------------------------- 5. static: wiring, credit, catalog
ec = (root / "tools/build-xtajit64.sh").read_text()
w64 = (root / "tools/build-xtajit-wow64.sh").read_text()
call = 'python3 "$R/tools/patch-fex-ios-section-skip.py" "$R/FEX" || echo "::warning::'
check("the ARM64EC build applies it after the invalidation-walk guard, without failing the build, and restores InvalidationTracker.cpp",
      call in ec and ec.index('patch-fex-ios-inval-null-thread.py" "$R/FEX"') < ec.index("patch-fex-ios-section-skip.py")
      and "Source/Windows/Common/InvalidationTracker.cpp" in ec[ec.index('git -C FEX checkout -- "$CPUF"'):])
rest = w64[w64.index("restore() {"):w64.index("trap restore EXIT")]
check("the WOW64 build applies it after the invalidation-walk guard, without failing the build, and restores InvalidationTracker.cpp",
      call in w64 and w64.index("patch-fex-ios-inval-null-thread.py") < w64.index("patch-fex-ios-section-skip.py") < w64.index("trap restore EXIT")
      and "Source/Windows/Common/InvalidationTracker.cpp" in rest)
wf = (root / ".github/workflows/build-ipa.yml").read_text()
check("CI runs this check", "python3 tests/host/check-fex-section-skip.py" in wf)
spec_cat = importlib.util.spec_from_file_location("catalog", root / "build/tools/gen-config-catalog.py")
cat = importlib.util.module_from_spec(spec_cat)
_argv = sys.argv
sys.argv = ["gen-config-catalog.py"]
spec_cat.loader.exec_module(cat)
sys.argv = _argv
entry = cat.OVERLAY.get("env.MADEIRA_FEX_SECTION_SKIP", {})
check("the config catalog documents the key: a bool, default off, source the patch script",
      entry.get("kind") == "bool" and entry.get("default") == "0" and entry.get("sources") == ["tools/patch-fex-ios-section-skip.py"]
      and "exact value 1" in entry.get("note", ""), entry)
text = patch_path.read_text() + Path(__file__).read_text()
check("the files carry no e-mail address", re.search(r"[\w.+-]+@[\w-]+\.[a-z]{2,}", text) is None)
check("the credit names Dragooon and the pull request", "Dragooon" in patch_path.read_text() and "pull request #40" in patch_path.read_text())
for n in ("THIRD-PARTY-NOTICES.md", "app/Madeira/legal/THIRD-PARTY-NOTICES.md", "app/Madeira/licenses/THIRD-PARTY-NOTICES.txt"):
    t = (root / n).read_text()
    check(f"{n} credits Dragooon for this take", "Dragooon" in t and "patch-fex-ios-section-skip.py" in t or n.endswith(".txt") and "Dragooon" in t)


# ---------------------------------------------------------------- the pinned sources, from a commit
def git(repo, *args, binary=False):
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=not binary)


def pinned_commit():
    p = git(root, "ls-tree", "HEAD", "FEX")
    m = re.match(r"160000 commit ([0-9a-f]{40})\s+FEX", p.stdout or "")
    return m.group(1) if m else None


def find_repo():
    pin = pinned_commit()
    cands = [os.environ.get("FEX_SRC"), root / "FEX", Path.home() / "madeira-local/kaynak/FEX.git"]
    for c in cands:
        if not c or not Path(c).exists():
            continue
        if git(c, "rev-parse", "--git-dir").returncode:
            continue
        rev = pin if pin and git(c, "cat-file", "-e", pin + "^{commit}").returncode == 0 else None
        if rev is None and not pin and git(c, "rev-parse", "HEAD").returncode == 0:
            rev = git(c, "rev-parse", "HEAD").stdout.strip()
        if rev:
            return Path(c), rev
    return None, None


repo, rev = find_repo()
if repo is None:
    print("skip: no FEX repository with the pinned commit here (FEX_SRC, the FEX submodule or ~/madeira-local/kaynak/FEX.git)")
    print("PASS: FEX section-unmap skip (static part)" if ok else "FAIL: FEX section-unmap skip")
    sys.exit(0 if ok else 1)
print(f"note: FEX {rev[:9]} from {repo}")


def extract(dst):
    """Source/, FEXCore/ (and External/rpmalloc when the repository has it) of the pinned commit, by git archive."""
    dst = Path(dst)
    dst.mkdir(parents=True, exist_ok=True)
    for paths in (["Source", "FEXCore"], ["External/rpmalloc"]):
        a = git(repo, "archive", "--format=tar", rev, *paths, binary=True)
        if a.returncode == 0 and a.stdout:
            with tarfile.open(fileobj=io.BytesIO(a.stdout)) as tf:
                try:
                    tf.extractall(dst, filter="data")
                except TypeError:
                    tf.extractall(dst)
    return dst


FILE = mod.FILE
orig = git(repo, "show", f"{rev}:{FILE}").stdout
check("the pinned file is there and has the section free path", "[iOS-xrem] via=section tracker=" in orig)

# ---------------------------------------------------------------- 1. applies, alone and in both chains
patched = mod.patch(orig)
check("the patch applies; a second run changes nothing", patched != orig and mod.patch(patched) == patched)
with tempfile.TemporaryDirectory(prefix="madeira-fex-section-skip-") as tmp:
    t = Path(tmp)
    (t / "a/Source/Windows/Common").mkdir(parents=True)
    f = t / "a" / FILE
    f.write_text(orig)
    r1 = subprocess.run([sys.executable, str(patch_path), str(t / "a")], capture_output=True, text=True)
    r2 = subprocess.run([sys.executable, str(patch_path), str(t / "a")], capture_output=True, text=True)
    check("the script writes it once and then says already in", r1.returncode == 0 and f.read_text() == patched and r2.returncode == 0
          and "already in" in r2.stdout and f.read_text() == patched, r1.stdout + r1.stderr + r2.stdout + r2.stderr)
    moved = orig.replace("  InvalidateIntervalInternal(SectionBase, SectionSize);\n\n  if (Free) {", "  InvalidateIntervalInternal(SectionBase, SectionSize);\n  if (Free) {", 1)
    (t / "b/Source/Windows/Common").mkdir(parents=True)
    (t / "b" / FILE).write_text(moved)
    r3 = subprocess.run([sys.executable, str(patch_path), str(t / "b")], capture_output=True, text=True)
    check("a moved anchor is refused by name and nothing is written",
          r3.returncode != 0 and "InvalidateIntervalInternal call" in r3.stderr and (t / "b" / FILE).read_text() == moved, r3.stderr)
    half = patched.replace("  if (!SkipInvalidation) {\n    InvalidateIntervalInternal(SectionBase, SectionSize);\n  }\n", "  InvalidateIntervalInternal(SectionBase, SectionSize);\n", 1)
    (t / "c/Source/Windows/Common").mkdir(parents=True)
    (t / "c" / FILE).write_text(half)
    r4 = subprocess.run([sys.executable, str(patch_path), str(t / "c")], capture_output=True, text=True)
    check("a half-applied file is refused and left alone", r4.returncode != 0 and "partial" in r4.stderr and (t / "c" / FILE).read_text() == half, r4.stderr)

    for kind, label in (("ec", "ARM64EC"), ("wow", "WOW64")):
        tree = extract(t / ("tree-" + kind))
        env_missing = not (tree / "External/rpmalloc/rpmalloc/rpmalloc.c").is_file()
        before = fex_chain.run_chain(root, kind, tree, before=me)
        if env_missing:   # the rpmalloc submodule is not in this repository: its one script cannot run here, as in every FEX check
            before = [(n, rc, o) for n, rc, o in before if n != "patch-fex-ios-rpmalloc-span8.py"]
        check(f"{label}: every script ahead of it in the build applies", not fex_chain.failures(before), fex_chain.failures(before))
        this = tree / FILE
        before_text = this.read_text()
        p = subprocess.run([sys.executable, str(patch_path), str(tree)], capture_output=True, text=True)
        check(f"{label}: it applies after them", p.returncode == 0 and this.read_text() != before_text, p.stdout + p.stderr)
        after = fex_chain.run_chain(root, kind, tree, after=me)
        check(f"{label}: every script behind it in the build still applies", not fex_chain.failures(after) and bool(after), fex_chain.failures(after))
        if kind == "wow":
            final = this.read_text()
            check("WOW64: the DEP-promotion fix (behind it) found its anchors, which the skip leaves alone",
                  "dep-promote-stale-v1 madeira-bcd" in final and final.count("DEPPromotedIntervals.Remove({SectionBase, SectionBase + SectionSize});") == 1)
            # the other order: dep-promote-stale first, then the skip
            t2 = extract(t / "tree-wow-dep")
            subprocess.run([sys.executable, str(root / "tools/patch-fex-ios-dep-promote-stale.py"), str(t2)], capture_output=True, text=True)
            q = subprocess.run([sys.executable, str(patch_path), str(t2)], capture_output=True, text=True)
            check("WOW64: the skip also applies on a file the DEP-promotion fix patched first, and the two give the same file",
                  q.returncode == 0 and (t2 / FILE).read_text() == final, q.stderr)

# ---------------------------------------------------------------- 2. nothing else changes
a = orig
b = patched
tail_old = a[a.index("    XIntervals.Remove({SectionBase, SectionBase + SectionSize});\n    RWXIntervals.Remove({SectionBase, SectionBase + SectionSize});"):]
tail_old = tail_old[:tail_old.index("  return {SectionBase, SectionSize};\n}")]
check("the bookkeeping after the skip's branch (XIntervals, RWXIntervals, hot SMC pages) is the original's, byte for byte",
      b.count(tail_old) == 1 and "ForgetHotSMCPages(SectionBase, SectionBase + SectionSize);" in tail_old)
fn = b[b.index("InvalidationTracker::InvalidateContainingSectionResult InvalidationTracker::InvalidateContainingSection("):]
fn = fn[:fn.index("\n}\n")]
check("InvalidateIntervalInternal still runs unless the skip applies, and only for a Free range with the switch on",
      fn.count("InvalidateIntervalInternal(SectionBase, SectionSize);") == 1
      and "  if (!SkipInvalidation) {\n    InvalidateIntervalInternal(SectionBase, SectionSize);\n  }\n" in fn
      and "  bool SkipInvalidation = false;\n  if (Free && SectionSkipEnabled()) {\n" in fn and fn.count("SkipInvalidation = ") == 2)
check("the original [iOS-xrem] via=section line is kept for every other case",
      '    } else {\n      LogMan::Msg::EFmt("[iOS-xrem] via=section tracker={} {:#x}-{:#x}", static_cast<void*>(this),\n' in fn)
check("the sampled line is the first 8 and then every 1024th", "if (N <= 8 || !(N & 1023)) {" in fn and "via=section-data #{}" in fn)
enable = b[b.index("static bool SectionSkipEnabled() {"):]
enable = enable[:enable.index("\n}\n") + 3]
check("the switch is read once, and only the exact value 1 turns it on",
      "static const bool Enabled = [] {" in enable and "Env && Env[0] == '1' && !Env[1]" in enable and enable.count("getenv(") == 1)
removed = [l for l in a.splitlines() if l not in b.splitlines()]
check("the only original lines that go are the call, the unconditional log line and its continuation (they are re-added inside the new branches)",
      sorted(l.strip() for l in removed) == sorted(["InvalidateIntervalInternal(SectionBase, SectionSize);",
                                                    'LogMan::Msg::EFmt("[iOS-xrem] via=section tracker={} {:#x}-{:#x}", static_cast<void*>(this),',
                                                    "SectionBase, SectionBase + SectionSize);"]), removed)

# ---------------------------------------------------------------- 3. the decision, compiled on the host
begin = b.index("    // section-skip-v1 decision begin\n") + len("    // section-skip-v1 decision begin\n")
end = b.index("    // section-skip-v1 decision end\n")
decision = b[begin:end]
harness = r'''
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <mutex>
#include <vector>
struct Interval { uint64_t Offset, End; };
struct FakeXIntervals {
  std::vector<Interval> v;
  bool Intersect(Interval i) const { for (auto& x : v) if (i.Offset < x.End && x.Offset < i.End) return true; return false; }
};
struct FakeLock { void lock() {} void unlock() {} };
struct Win { uint64_t Low, Real, Size; };
static std::vector<Win> g_windows;
extern "C" int IosSubfloorEnum(int Index, uint64_t* Low, uint64_t* Real, uint64_t* Size) {
  if (Index < 0 || (size_t)Index >= g_windows.size()) return 0;
  *Low = g_windows[Index].Low; *Real = g_windows[Index].Real; *Size = g_windows[Index].Size;
  return 1;
}
static FakeXIntervals XIntervals;
static FakeLock IntervalsLock;
static bool decide(uint64_t SectionBase, uint64_t SectionSize) {
  bool SkipInvalidation = false;
  bool Free = true;
  auto SectionSkipEnabled = [] { return true; };
  if (Free && SectionSkipEnabled()) {
@DECISION@
  }
  return SkipInvalidation;
}
int main() {
  struct { const char* name; std::vector<Interval> x; std::vector<Win> w; uint64_t base, size; int want; } t[] = {
    {"never executable, no windows", {}, {}, 0x7000000000, 0x4000, 1},
    {"never executable, other code elsewhere", {{0x1000, 0x2000}}, {}, 0x7000000000, 0x4000, 1},
    {"the whole range is executable", {{0x7000000000, 0x7000004000}}, {}, 0x7000000000, 0x4000, 0},
    {"executable inside the range", {{0x7000001000, 0x7000002000}}, {}, 0x7000000000, 0x4000, 0},
    {"executable across the start", {{0x6fffffe000, 0x7000001000}}, {}, 0x7000000000, 0x4000, 0},
    {"executable across the end", {{0x7000003000, 0x7000006000}}, {}, 0x7000000000, 0x4000, 0},
    {"executable around the range", {{0x6000000000, 0x8000000000}}, {}, 0x7000000000, 0x4000, 0},
    {"executable right behind the range (touching, not overlapping)", {{0x7000004000, 0x7000008000}}, {}, 0x7000000000, 0x4000, 1},
    {"sub-floor window by its low name", {}, {{0x400000, 0x7100000000, 0x200000}}, 0x400000, 0x1000, @LOWWANT@},
    {"sub-floor window by its real name", {}, {{0x400000, 0x7100000000, 0x200000}}, 0x7100010000, 0x1000, @REALWANT@},
    {"sub-floor window, range across its end", {}, {{0x400000, 0x7100000000, 0x200000}}, 0x7100000000 + 0x1ff000, 0x4000, @REALWANT@},
    {"a window elsewhere does not matter", {}, {{0x400000, 0x7100000000, 0x200000}}, 0x7000000000, 0x4000, 1},
    {"a window with no size is ignored", {}, {{0x7000000000, 0x7000000000, 0}}, 0x7000000000, 0x4000, 1},
    {"a second window is looked at", {}, {{0x400000, 0x7100000000, 0x1000}, {0x10000000, 0x7200000000, 0x100000}}, 0x7200001000, 0x1000, @REALWANT@},
  };
  int bad = 0;
  for (auto& c : t) {
    XIntervals.v = c.x; g_windows = c.w;
    int got = decide(c.base, c.size) ? 1 : 0;
    printf("%s %s got %d want %d\n", got == c.want ? "ok  " : "FAIL", c.name, got, c.want);
    bad += got != c.want;
  }
  return bad ? 1 : 0;
}
'''
cxx = shutil.which("c++") or shutil.which("clang++")
with tempfile.TemporaryDirectory(prefix="madeira-fex-section-skip-h-") as tmp:
    t = Path(tmp)
    for label, defs, low, real in (("ARM64EC module (sub-floor windows count)", ["-DFEX_IOS_HOST", "-DARCHITECTURE_arm64ec"], 0, 0),
                                   ("WOW64 module (no sub-floor windows)", ["-DFEX_IOS_HOST"], 1, 1)):
        src = harness.replace("@DECISION@", decision).replace("@LOWWANT@", str(low)).replace("@REALWANT@", str(real))
        (t / "h.cpp").write_text(src)
        b_ = subprocess.run([cxx, "-std=c++20", "-O1", "-Wall", "-Wextra", "-Werror", "-Wno-unused-parameter"] + defs + ["-o", str(t / "h"), str(t / "h.cpp")],
                            capture_output=True, text=True)
        check(f"{label}: the decision compiles on the host (C++20, -Werror)", b_.returncode == 0, b_.stderr[-2000:])
        if b_.returncode == 0:
            r = subprocess.run([str(t / "h")], capture_output=True, text=True)
            check(f"{label}: all {r.stdout.count(chr(10))} decision cases", r.returncode == 0 and "FAIL" not in r.stdout, r.stdout)
    # the switch: only the exact value 1
    sw = r'''
#include <cstdio>
#include <cstdlib>
namespace LogMan { namespace Msg { template <typename... A> void EFmt(const char*, A...) { std::puts("[said]"); } } }
@ENABLE@
int main() { std::printf("%d\n", SectionSkipEnabled() ? 1 : 0); }
'''
    sw = sw.replace("@ENABLE@", enable)
    (t / "s.cpp").write_text(sw)
    b_ = subprocess.run([cxx, "-std=c++20", "-O1", "-Wall", "-Wextra", "-Werror", "-o", str(t / "s"), str(t / "s.cpp")], capture_output=True, text=True)
    check("the switch reader compiles on the host", b_.returncode == 0, b_.stderr[-1500:])
    if b_.returncode == 0:
        res = {}
        for val in (None, "", "0", "1", "11", "true", " 1", "2"):
            env = {k: v for k, v in os.environ.items() if k != "MADEIRA_FEX_SECTION_SKIP"}
            if val is not None:
                env["MADEIRA_FEX_SECTION_SKIP"] = val
            res[val] = subprocess.run([str(t / "s")], capture_output=True, text=True, env=env).stdout.split()
        check("unset, empty, 0, 11, true, ' 1' and 2 leave it off and say nothing; exactly 1 turns it on and says so once",
              all(res[v] == ["0"] for v in (None, "", "0", "11", "true", " 1", "2")) and res["1"] == ["[said]", "1"], res)

# ---------------------------------------------------------------- 4. compiles for both modules
mingw = Path.home() / "madeira-bcd/toolchains/llvm-mingw-20260421-ucrt-macos-universal/bin"
with tempfile.TemporaryDirectory(prefix="madeira-fex-section-skip-cc-") as tmp:
    t = Path(tmp)
    tree = extract(t / "tree")
    for build, label in (("build-arm64ec", "ARM64EC"), ("build-wow64", "WOW64")):
        dbp = Path.home() / "madeira-bcd/FEX" / build / "compile_commands.json"
        if not dbp.is_file() or not mingw.is_dir():
            print(f"skip the {label} compile: no {dbp} or no llvm-mingw")
            continue
        entry = next((e for e in json.loads(dbp.read_text()) if e["file"].endswith("Source/Windows/Common/InvalidationTracker.cpp")), None)
        if entry is None:
            print(f"skip the {label} compile: no entry for InvalidationTracker.cpp")
            continue
        old = str(Path.home() / "madeira-bcd/FEX") + "/"
        diag = {}
        for which, text in (("unpatched", orig), ("patched", patched)):
            (tree / FILE).write_text(text)
            args, skip = [], False
            for x in shlex.split(entry["command"]):
                if skip:
                    skip = False
                    continue
                if x in ("-o", "-MF", "-MQ", "-MT"):
                    skip = True
                    continue
                if x in ("-c", "-MD") or x.startswith("-fdiagnostics-color") or x == "-fcolor-diagnostics" or x.endswith("InvalidationTracker.cpp"):
                    continue
                for d in ("Source", "FEXCore"):   # only what the archive holds moves; the build directory, External and the rest stay
                    x = x.replace(old + d, str(tree / d)) if (x.startswith("-I" + old + d) or x.startswith(old + d)) else x
                args.append(x)
            c = subprocess.run(args + ["-fsyntax-only", "-fno-color-diagnostics", str(tree / FILE)], cwd=entry["directory"], capture_output=True, text=True)
            diag[which] = (c.returncode, sorted(re.sub(r"^.*?:\d+:\d+: ", "", l) for l in c.stderr.splitlines() if " warning:" in l or " error:" in l), c.stderr[-1500:])
        check(f"{label}: the patched InvalidationTracker.cpp compiles with the owner's flags, with no diagnostic the unpatched one lacks",
              diag["patched"][0] == 0 and diag["unpatched"][0] == 0 and diag["patched"][1] == diag["unpatched"][1], diag)

print("PASS: FEX section-unmap skip" if ok else "FAIL: FEX section-unmap skip")
sys.exit(0 if ok else 1)

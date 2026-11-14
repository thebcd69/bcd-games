#!/usr/bin/env python3
"""Small staging rings in d3d11-src.dll (tools/patch-d3d11-src-small-rings.py,
env.DXMT_SMALL_RINGS); no Wine runs.

1. The selector on the host (the patch's HELPERS with stand-ins for DXMT's env
   and log helpers): unset, empty and 0 are off; a program list matches whole
   file names, case-insensitively, ';' or ',' separated, blanks trimmed; 1 and
   * match every program; the variable is read once per process, only by a
   ring larger than 4 MB, and one "[small-rings] madeira-bcd: <exe> uses 4 MB
   ..." line is logged; rings of 4 MB or less keep their size.
2. With dxmt sources (dxmt/src/dxmt, or $DXMT_SRC/src/dxmt): the patch applies
   to a copy and routes every use of the ring's BlockSize through
   ring_block_size(); a second run changes nothing; the submodule stays
   untouched; a moved anchor or a BlockSize use the script does not know is
   refused with exit 1 and the file left as it was. The patched RingBumpState
   then runs on the host (stand-ins for the Metal, log, thread and census
   headers) with a counting allocator: off, a 32 MB ring serves the sequence
   from one 32 MB block; on, from 4 MB blocks that are reused once the GPU is
   done, and an upload larger than 4 MB gets a block of its own; the 2 MB and
   4 MB rings are unchanged either way. With an arm64ec llvm-mingw ($MINGW, or
   arm64ec-w64-mingw32-clang++ on PATH) the two DXMT files that hold the 32 MB
   rings also compile from the copy.
3. tools/build-d3d11-dll.sh copies dxmt/src/dxmt, patches the copy with a
   warning-only fallback, compiles the DXMT core from both roots with each
   root's headers, links the unpatched DLL with the submodule's core and
   d3d11-src.dll with the copy's, and refuses a result without the marker; the
   workflow checks this before the build; the script name stays out of the
   workflow's DXMT patch chain and the i386 farm cache key.
4. The catalog lists env.DXMT_SMALL_RINGS (text, unset by default).
"""
from pathlib import Path
import fnmatch, importlib.util, os, re, shutil, subprocess, sys, tempfile

root = Path(__file__).resolve().parents[2]
dxmt = Path(os.environ.get("DXMT_SRC", root / "dxmt"))
core = dxmt / "src/dxmt"
header = core / "dxmt_ring_bump_allocator.hpp"
PATCH = root / "tools/patch-d3d11-src-small-rings.py"
MB = 1 << 20
ok = True


def check(what, cond):
    global ok
    print(("ok   " if cond else "FAIL ") + what)
    ok &= bool(cond)


def run(path):
    r = subprocess.run([sys.executable, str(PATCH), str(path)], capture_output=True, text=True)
    return r.returncode, r.stdout + r.stderr


def compile_and_run(cxx, src, incdir, cases, tmp, name):
    """Build src once, run it per case (env, exe); returns {case index: stdout} or None."""
    exe = tmp / name
    r = subprocess.run([cxx, "-std=c++20", "-Wall", "-Wextra", "-Wno-unused-parameter",
                        "-Wno-missing-field-initializers", "-Werror", "-I%s" % incdir, "-o", str(exe), str(src)],
                       capture_output=True, text=True)
    if r.returncode:
        print(r.stderr[-3000:])
        return None
    outs = {}
    for i, (value, prog) in enumerate(cases):
        env = {k: v for k, v in os.environ.items() if k not in ("DXMT_SMALL_RINGS", "DXMT_RING_OVERSIZE_REUSE")}
        if value is not None:
            env["DXMT_SMALL_RINGS"] = value
        env["HARNESS_EXE"] = prog
        p = subprocess.run([str(exe)], capture_output=True, text=True, env=env)
        outs[i] = p.stdout if p.returncode == 0 else "exit %d\n%s" % (p.returncode, p.stdout + p.stderr)
    return outs


spec = importlib.util.spec_from_file_location("small_rings", PATCH)
patch_mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(patch_mod)
cxx = shutil.which("clang++") or shutil.which("g++")

# (DXMT_SMALL_RINGS, program, small rings expected)
CASES = [
    (None, "Launcher.exe", False),
    ("", "Launcher.exe", False),
    ("0", "Launcher.exe", False),
    ("Launcher.exe;SocialClubHelper.exe", "SocialClubHelper.exe", True),
    ("Launcher.exe;SocialClubHelper.exe", "Launcher.exe", True),
    ("Launcher.exe;SocialClubHelper.exe", "RDR2.exe", False),
    ("Launcher.exe;SocialClubHelper.exe", "GTA5_Enhanced.exe", False),
    ("launcher.EXE", "Launcher.exe", True),
    (" Launcher.exe , SocialClubHelper.exe ", "SocialClubHelper.exe", True),
    ("Launcher.exe;", "Launcher.exe", True),
    (";;", "Launcher.exe", False),
    ("Launcher.exe", "MyLauncher.exe", False),
    ("Launcher", "Launcher.exe", False),
    ("1", "GTA5_Enhanced.exe", True),
    ("*", "dockhost.exe", True),
]

# --- 1. the selector, from the patch's own HELPERS ---
SEL = r'''
#include <cstddef>
#include <cstdio>
#include <cstdlib>
#include <map>
#include <sstream>
#include <string>
#include <vector>
namespace dxmt {
std::map<std::string, int> g_reads;
std::vector<std::string> g_log;
namespace env {
std::string getEnvVar(const char *name) { g_reads[name]++; const char *v = std::getenv(name); return v ? v : ""; }
std::string getExeName() { const char *v = std::getenv("HARNESS_EXE"); return v ? v : "harness.exe"; }
}
namespace str {
template <typename... T> std::string format(const T &...a) { std::ostringstream s; (s << ... << a); return s.str(); }
}
struct Logger { static void warn(const std::string &m) { g_log.push_back(m); } };
}
#define WARN(...) Logger::warn(str::format(__VA_ARGS__))
namespace dxmt {
@HELPERS@
} // namespace dxmt
using namespace dxmt;
int main() {
  const size_t MB = size_t(1) << 20;
  const size_t sizes[] = {2 * MB, 4 * MB, 32 * MB, 32 * MB, 5 * MB};
  const char *names[] = {"deferred", "argbuf", "staging", "upload", "odd"};
  for (int i = 0; i < 5; i++) {
    const size_t got = madeiraRingBlockSize(sizes[i]);   /* before the read count (argument order) */
    printf("%s=%zu reads=%d\n", names[i], got, g_reads["DXMT_SMALL_RINGS"]);
  }
  printf("kStagingBlockLifetime=%zu\n", kStagingBlockLifetime);
  for (auto &l : g_log)
    printf("log=%s\n", l.c_str());
  return 0;
}
'''
if not cxx:
    print("note: no host C++ compiler; selector checks skipped")
else:
    with tempfile.TemporaryDirectory() as t:
        t = Path(t)
        (t / "sel.cpp").write_text(SEL.replace("@HELPERS@", patch_mod.HELPERS))
        outs = compile_and_run(cxx, t / "sel.cpp", t, [(v, p) for v, p, _ in CASES], t, "sel")
        check("selector compiles on the host (-Wall -Wextra -Werror)", outs is not None)
        for i, (value, prog, on) in enumerate(CASES if outs else []):
            small = 4 * MB if on else 32 * MB
            want = ["deferred=%d reads=0" % (2 * MB), "argbuf=%d reads=0" % (4 * MB),
                    "staging=%d reads=1" % small, "upload=%d reads=1" % small,
                    "odd=%d reads=1" % (4 * MB if on else 5 * MB), "kStagingBlockLifetime=300"]
            if on:
                want.append("log=[small-rings] madeira-bcd: %s uses 4 MB staging and upload ring blocks "
                            "(DXMT_SMALL_RINGS)" % prog)
            check("selector DXMT_SMALL_RINGS=%r in %s: %s" % (value, prog, "4 MB blocks" if on else "as upstream"),
                  outs[i].split("\n")[:-1] == want)
            if outs[i].split("\n")[:-1] != want:
                print(outs[i])

# --- 2. the patch on a copy of dxmt/src/dxmt, and the patched ring ---
STUBS = {
    "Metal.hpp": r'''#pragma once
#include <cstddef>
#include <cstdint>
typedef uint64_t WMTResourceOptions;
struct WMTMemoryPointer { void *ptr = nullptr; void set(void *p) { ptr = p; } };
struct WMTBufferInfo { uint64_t length = 0; WMTResourceOptions options = 0; WMTMemoryPointer memory; uint64_t gpu_address = 0; };
namespace WMT {
struct Buffer {};
template <typename T> class Reference {
public:
  Reference() = default;
  Reference(std::nullptr_t) {}
  explicit Reference(T *p) : p_(p) {}
  Reference(const Reference &) = delete;
  Reference(Reference &&o) : p_(o.p_) { o.p_ = nullptr; }
  Reference &operator=(Reference &&o) { delete p_; p_ = o.p_; o.p_ = nullptr; return *this; }
  Reference &operator=(std::nullptr_t) { delete p_; p_ = nullptr; return *this; }
  ~Reference() { delete p_; }
private:
  T *p_ = nullptr;
};
struct Device {
  Reference<Buffer> newBuffer(WMTBufferInfo &info) { info.gpu_address = 0x1000; return Reference<Buffer>(new Buffer()); }
};
}
''',
    "log/log.hpp": r'''#pragma once
#include <sstream>
#include <string>
#include <vector>
namespace dxmt {
inline std::vector<std::string> &harness_log() { static std::vector<std::string> v; return v; }
namespace str {
template <typename... T> std::string format(const T &...a) { std::ostringstream s; (s << ... << a); return s.str(); }
}
struct Logger {
  static void info(const std::string &m) { harness_log().push_back(m); }
  static void warn(const std::string &m) { harness_log().push_back(m); }
};
}
#define WARN(...) Logger::warn(str::format(__VA_ARGS__))
''',
    "thread.hpp": r'''#pragma once
#include <cstdint>
#include <mutex>
namespace dxmt {
using mutex = std::mutex;
struct null_mutex { void lock() {} void unlock() {} bool try_lock() { return true; } };
namespace this_thread { inline uint32_t get_id() { return 1; } }
}
''',
    "util_math.hpp": r'''#pragma once
namespace dxmt {
template <typename T, typename U = T> constexpr T align(T what, U to) { return (what + to - 1) & ~(T(to) - 1); }
}
''',
    "util_madeira_switch.hpp": r'''#pragma once
#include <cstdlib>
#include <map>
#include <string>
namespace dxmt {
inline std::map<std::string, int> &harness_reads() { static std::map<std::string, int> m; return m; }
namespace env {
inline std::string getEnvVar(const char *name) { harness_reads()[name]++; const char *v = std::getenv(name); return v ? v : ""; }
inline std::string getExeName() { const char *v = std::getenv("HARNESS_EXE"); return v ? v : "harness.exe"; }
}
constexpr bool kMadeira32BitModule = false;
inline bool madeiraSwitch(const char *name) { const std::string v = env::getEnvVar(name); return v.empty() ? kMadeira32BitModule : v != "0"; }
}
''',
    "dxmt_mem_census.hpp": r'''#pragma once
#include <cstdint>
namespace dxmt {
enum MemOwner { MEMOWN_STAGING_RING = 3, MEMOWN_INIT_UPLOAD = 4 };
inline void mem_census_add(MemOwner, uint64_t) {}
inline void mem_census_sub(MemOwner, uint64_t) {}
}
''',
}
RING = r'''#include "dxmt_ring_bump_allocator.hpp"
#include <cstdio>
#include <string>
#include <vector>
using namespace dxmt;

struct CountingAllocator {
  std::vector<size_t> *sizes;
  class Block {
  public:
    size_t size = 0;
    Block() = default;
    Block(const Block &) = delete;
    Block(Block &&m) : size(m.size) { m.size = 0; }
  };
  Block allocate(size_t n) { sizes->push_back(n); Block b; b.size = n; return b; }
};

static std::string join(const std::vector<size_t> &v) {
  std::string s;
  for (auto n : v)
    s += (s.empty() ? "" : ",") + std::to_string(n);
  return s;
}

int main() {
  const size_t MB = size_t(1) << 20;
  {   // the command queue's staging ring / the upload heap: 32 MB blocks upstream
    std::vector<size_t> sizes;
    RingBumpState<CountingAllocator, kStagingBlockSize> ring(CountingAllocator{&sizes});
    ring.allocate(1, 0, 3 * MB, 16);
    ring.allocate(1, 0, 3 * MB, 16);   // same submission, the GPU behind
    ring.allocate(2, 1, 3 * MB, 16);   // the first block still in flight
    ring.allocate(3, 2, 3 * MB, 16);   // the first block completed: reused
    ring.allocate(3, 2, 6 * MB, 16);   // larger than a small block
    printf("seq=%s\n", join(sizes).c_str());
  }
  {
    std::vector<size_t> sizes;
    RingBumpState<CountingAllocator, kStagingBlockSize> ring(CountingAllocator{&sizes});
    ring.preallocate(2);
    ring.allocate(1, 0, 1024, 16);
    printf("pre=%s\n", join(sizes).c_str());
  }
  {
    std::vector<size_t> sizes;
    RingBumpState<CountingAllocator, kStagingBlockSizeForDeferredContext> ring(CountingAllocator{&sizes});
    ring.allocate(1, 0, 1024, 16);
    printf("deferred=%s\n", join(sizes).c_str());
  }
  {
    std::vector<size_t> sizes;
    RingBumpState<CountingAllocator, 0x400000> ring(CountingAllocator{&sizes});
    ring.allocate(1, 0, 1024, 16);
    printf("argbuf=%s\n", join(sizes).c_str());
  }
  printf("reads=%d\n", harness_reads()["DXMT_SMALL_RINGS"]);
  for (auto &l : harness_log())
    printf("log=%s\n", l.c_str());
  return 0;
}
'''

if not header.exists():
    print("note: %s not checked out (set DXMT_SRC); patch and ring checks skipped" % header)
else:
    before = header.read_text()
    with tempfile.TemporaryDirectory() as t:
        t = Path(t)
        copy = t / "dxmt"
        shutil.copytree(core, copy)
        rc, out = run(copy)
        check("patch applies to the directory given as argument", rc == 0 and "patched" in out)
        src = (copy / "dxmt_ring_bump_allocator.hpp").read_text()
        once = src
        rc, out = run(copy)
        check("second run: already patched, file unchanged",
              rc == 0 and "already patched" in out and (copy / "dxmt_ring_bump_allocator.hpp").read_text() == once)
        check("the ring asks ring_block_size() for its block size everywhere it used BlockSize",
              src.count("ring_block_size()") == 7
              and "static size_t\n  ring_block_size() {\n    return madeiraRingBlockSize(BlockSize);" in src
              and ".total_size = ring_block_size()," in src and ".block = allocator_.allocate(ring_block_size())," in src
              and "std::max(size, ring_block_size())" in src
              and "auto adhoc = front.total_size != ring_block_size() && !front.reusable_oversize;" in src
              and "if (front.total_size != ring_block_size() && !front.reusable_oversize) {" in src
              and "block_size > ring_block_size() &&" in src)
        check("only rings above 4 MB shrink, to 4 MB, and the switch is read once",
              "constexpr size_t kMadeiraSmallRingBlockSize = 0x400000;" in src
              and "return natural > kMadeiraSmallRingBlockSize && madeiraSmallRingsHere() ? kMadeiraSmallRingBlockSize : natural;" in src
              and "static const bool here = [] {" in src and 'env::getEnvVar("DXMT_SMALL_RINGS")' in src)
        check("upstream's constants are left as they were",
              "constexpr size_t kStagingBlockSize = 0x2000000; // 32MB" in src
              and "constexpr size_t kStagingBlockSizeForDeferredContext = 0x200000; // 2MB" in src
              and "static constexpr size_t block_size = BlockSize;" in src)
        check("the submodule is untouched", header.read_text() == before)

        if "kMadeiraSmallRingBlockSize" in before:
            # dxmt 128b297 (the 2026-10-15 upstream merge) carries the small rings
            # itself (willfaust/dxmt#28): the refusals need a ring without them.
            print("note: the dxmt pin carries the small rings; the moved-anchor refusals are not exercised")
        else:
            moved = t / "moved"
            shutil.copytree(core, moved)
            f = moved / "dxmt_ring_bump_allocator.hpp"
            f.write_text(before.replace("    auto adhoc = front.total_size != BlockSize", "    auto adhoc_ = front.total_size != BlockSize"))
            snap = f.read_text()
            rc, out = run(moved)
            check("moved anchor: exit 1, file untouched, anchor named",
                  rc == 1 and f.read_text() == snap and "free_blocks: ad-hoc test" in out and "nothing written" in out)
            f.write_text(before.replace("  void free_blocks(uint64_t coherent_id);\n",
                                        "  void free_blocks(uint64_t coherent_id);\n  size_t twice() { return BlockSize * 2; }\n"))
            snap = f.read_text()
            rc, out = run(moved)
            check("a BlockSize use the script does not know: exit 1, file untouched",
                  rc == 1 and f.read_text() == snap and "does not expect" in out)

        if not cxx:
            print("note: no host C++ compiler; ring checks skipped")
        else:
            inc = t / "inc"
            inc.mkdir()
            shutil.copy(copy / "dxmt_ring_bump_allocator.hpp", inc)
            for name, text in STUBS.items():
                (inc / name).parent.mkdir(parents=True, exist_ok=True)
                (inc / name).write_text(text)
            (t / "ring.cpp").write_text(RING)
            outs = compile_and_run(cxx, t / "ring.cpp", inc, [(v, p) for v, p, _ in CASES], t, "ring")
            check("the patched RingBumpState compiles on the host (-Wall -Wextra -Werror)", outs is not None)
            for i, (value, prog, on) in enumerate(CASES if outs else []):
                if on:
                    want = ["seq=%d,%d,%d,%d" % (4 * MB, 4 * MB, 4 * MB, 6 * MB), "pre=%d,%d" % (4 * MB, 4 * MB),
                            "deferred=%d" % (2 * MB), "argbuf=%d" % (4 * MB), "reads=1",
                            "log=[small-rings] madeira-bcd: %s uses 4 MB staging and upload ring blocks "
                            "(DXMT_SMALL_RINGS)" % prog,
                            "log=forced to allocate new block of size %d" % (6 * MB)]
                else:
                    want = ["seq=%d" % (32 * MB), "pre=%d,%d" % (32 * MB, 32 * MB), "deferred=%d" % (2 * MB),
                            "argbuf=%d" % (4 * MB), "reads=1"]
                got = outs[i].split("\n")[:-1]
                check("ring DXMT_SMALL_RINGS=%r in %s: %s" % (value, prog,
                      "4 MB blocks, reused when done, 6 MB upload on its own" if on else "one 32 MB block as upstream"),
                      got == want)
                if got != want:
                    print(outs[i])

        mingw = os.environ.get("MINGW")
        acxx = Path(mingw) / "arm64ec-w64-mingw32-clang++" if mingw else shutil.which("arm64ec-w64-mingw32-clang++")
        if not acxx or not Path(acxx).exists():
            print("note: no arm64ec llvm-mingw ($MINGW); compile step skipped")
        else:
            for name in ("dxmt_command_queue", "dxmt_resource_initializer"):
                r = subprocess.run([str(acxx), "-std=c++20", "-O2", "-c", "-o", str(t / (name + ".o")), str(copy / (name + ".cpp")),
                                    "-I%s" % copy, "-I%s" % (dxmt / "src/util"), "-I%s" % (dxmt / "src/airconv"),
                                    "-I%s" % (dxmt / "src/winemetal"), "-I%s" % (dxmt / "include"), "-I%s" % (dxmt / "libs"),
                                    "-DNOMINMAX", "-D_WIN32_WINNT=0xa00", "-DDXMT_IOS=1", "-DDXMT_PAGE_SIZE=4096",
                                    "-fblocks", "-Wno-microsoft-exception-spec", "-Werror=return-type"],
                                   capture_output=True, text=True)
                check("patched %s.cpp compiles for arm64ec" % name, r.returncode == 0 and (t / (name + ".o")).exists())
                if r.returncode:
                    print(r.stderr[-2000:])

# --- 3. the build script and the workflow ---
sh = (root / "tools/build-d3d11-dll.sh").read_text()
check("build script copies the DXMT core and patches the copy, never the submodule",
      'cp -R "$D/src/dxmt" "$OUT/tree/src/"' in sh
      and 'tools/patch-d3d11-src-small-rings.py" "$OUT/tree/src/dxmt"' in sh
      and sh.index('cp -R "$D/src/dxmt" "$OUT/tree/src/"') < sh.index('tools/patch-d3d11-src-small-rings.py" "$OUT/tree/src/dxmt"'))
check("build script: a patch that does not apply only warns; d3d11-src.dll is still built for every game",
      re.search(r'patch-d3d11-src-small-rings\.py" "\$OUT/tree/src/dxmt" \|\| \{\n    SMALL_RINGS=""\n'
                r'    echo "::warning::tools/patch-d3d11-src-small-rings\.py did not apply', sh) is not None)
cmp = sh[sh.index("compile_one() {"):sh.index("\n}\n", sh.index("compile_one() {"))]
check("each object includes the dxmt headers of its own root (no fixed $X in compile_one)",
      'dxmt)   inc=(-I"$root/src/dxmt" "${INC_DXMT[@]}") ;;' in cmp
      and '-I"$root/src/d3d11" -I"$G" -I"$root/src/dxmt"' in cmp and '-I"$X"' not in cmp
      and re.search(r'INC_DXMT=\([^)]*-I"\$X"', sh) is None)
check("the DXMT core is compiled from the submodule and from the copy",
      'job "$D" "$X/$s.cpp" "$o" dxmt; PLAIN_DXMT_OBJ+=("$o")' in sh
      and 'job "$OUT/tree" "$OUT/tree/src/dxmt/$s.cpp" "$o" dxmt; DXMT_OBJ+=("$o")' in sh)
check("the unpatched DLL links the submodule's core, d3d11-src.dll the copy's",
      '"$AR" csrDT "$OUT/plain/libdxmt.a" "${PLAIN_DXMT_OBJ[@]}"' in sh and '"$AR" csrDT "$OUT/libdxmt.a" "${DXMT_OBJ[@]}"' in sh
      and 'link_dll "$OUT/plain/d3d11.dll" "$OUT/plain/libdxmt.a" "$D/src/d3d11/d3d11.def" "${PLAIN_OBJ[@]}"' in sh
      and 'link_dll "$OUT/d3d11.dll" "$OUT/libdxmt.a" "$PATCHED_DEF" "${PATCHED_OBJ[@]}"' in sh   # the copy's .def: the Lossless Scaling bridge's exports
      and 'local out="$1" dxmt="$2" def="$3"; shift 3' in sh and '"$OUT/libDXBCParser.a" "$dxmt" -L"$OUT"' in sh)
# Round 620: the pin's own source has carried the small rings since the 2026-10-15 upstream merge, so the unpatched
# build is refused for the marker only when the pin's source lacks it (PIN_RINGS=0).
check("build script refuses a result that lacks the patch it applied, and an unpatched DLL that has it",
      'if [ -n "$SMALL_RINGS" ]; then\n    LC_ALL=C grep -aqF "[small-rings] madeira-bcd" "$OUT/d3d11.dll" || fail' in sh
      and 'if [ "$PIN_RINGS" = 0 ] && LC_ALL=C grep -aqF "[small-rings] madeira-bcd" "$OUT/plain/d3d11.dll"; then\n    fail' in sh
      and 'PIN_RINGS=0; grep -qF "[small-rings] madeira-bcd" "$D/src/dxmt/dxmt_ring_bump_allocator.hpp" && PIN_RINGS=1' in sh)
check("the marker the build greps is the one the patch logs",
      'WARN("[small-rings] madeira-bcd: ", exe,' in patch_mod.HELPERS)
wf = (root / ".github/workflows/build-ipa.yml").read_text()
chain = re.findall(r"python3 (tools/patch-(?:dxmt|airconv|winemetal)-[\w.-]+\.py)", wf)
check("the script is not in the workflow's in-place DXMT patch chain nor in the i386 farm cache key",
      "tools/patch-d3d11-src-small-rings.py" not in chain
      and "python3 tools/patch-d3d11-src-small-rings.py" not in wf
      and "'tools/patch-dxmt-*.py'" in wf
      and not fnmatch.fnmatch("tools/patch-d3d11-src-small-rings.py", "tools/patch-dxmt-*.py"))
steps = re.findall(r"\n      - name: (.+)", wf)
b = next((i for i, n in enumerate(steps) if n.startswith("Build d3d11-src.dll")), None)
c = next((i for i, n in enumerate(steps) if n == "Check opt-in small staging rings"), None)
check("the workflow runs this check, with llvm-mingw, before it builds d3d11-src.dll",
      b is not None and c is not None and c < b
      and "MINGW: ${{ github.workspace }}/toolchains/llvm-mingw-20260421-ucrt-macos-universal/bin\n"
          "        run: python3 tests/host/check-d3d11-small-rings.py" in wf)

# --- 4. the catalog ---
cat = (root / "app/Madeira/ConfigCatalog.generated.swift").read_text()
m = re.search(r'ConfigOption\(key: "env.DXMT_SMALL_RINGS", title: "([^"]+)", kind: \.text, defaultValue: "", '
              r'category: "Direct3D 9/10/11 \(DXMT\)", note: "([^"]+)"', cat)
check("catalog lists env.DXMT_SMALL_RINGS: a program list, unset by default, needs MADEIRA_D3D11_SRC",
      m is not None and "env.MADEIRA_D3D11_SRC = 1" in m.group(2) and "Launcher.exe;SocialClubHelper.exe" in m.group(2))

print("PASS" if ok else "FAILED")
sys.exit(0 if ok else 1)

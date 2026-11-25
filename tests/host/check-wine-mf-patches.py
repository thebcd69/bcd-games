#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""The three Wine changes taken from Madeira's Wine fork pull request #78 (wenze-allen), as BCD's build-time patch scripts:

  tools/patch-wine-mf-session-keep-node.py          dlls/mf/session.c: a session keeps an output node whose sample allocator fails to initialize
  tools/patch-wine-wmreader-compressed-streams.py   dlls/winegstreamer/wm_reader.c: compressed samples per stream; a failed reinit keeps the streams
  tools/patch-wine-mfplat-d3d9-discard-copy.py      dlls/mfplat/buffer.c: ContiguousCopyFrom of a d3d9 surface buffer locks with D3DLOCK_DISCARD

Each is applied to the file AS PINNED (read with `git show <gitlink commit>:<path>` from a Wine checkout or from the bare repository
~/madeira-local/kaynak/wine.git, never from a work tree that earlier steps patched). It must
  - write its marker once and say "patched", then "already patched" and leave the file as it is;
  - refuse a file whose anchor moved by name, with nothing written;
  - leave the rest of the file as it was: the only lines that change are the ones the pull request changed, and the text of the
    pull request's own result is in the file (the structural checks below name each);
  - compile with the module's own i386 flags (-fsyntax-only, nothing written into the build tree) with no error and no warning the
    pinned file does not have, when llvm-mingw and a configured i386 build tree exist (WINE_I386_BUILD, wine/build-i386 or
    ~/madeira-bcd/wine/build-i386); otherwise that part is skipped by name.
The workflow applies them in "Patch wine ..." steps before the i386 farm is built (its cache key hashes tools/patch-wine-*.py), and
tools/build-wine-fresh-dlls.sh applies them again for the rebuilt 64-bit set (mf.dll and mfplat.dll are in tools/wine-fresh-modules.txt).
The wm_reader one is put back before the extra-DLL builds, so the 64-bit winegstreamer.dll stays as it is today.
"""
from pathlib import Path
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[2]
ok = True


def check(what, cond, detail=""):
    global ok
    print(("ok   " if cond else "FAIL ") + what + (("\n     " + str(detail)[:1800]) if (detail and not cond) else ""))
    ok = ok and bool(cond)


SCRIPTS = {
    "patch-wine-mf-session-keep-node.py": ("dlls/mf/session.c", "madeira-bcd: mf-session-keep-node-v1",
                                           "                                2, media_type)))\n", "                                2,  media_type)))\n"),
    "patch-wine-wmreader-compressed-streams.py": ("dlls/winegstreamer/wm_reader.c", "madeira-bcd: wmreader-compressed-streams-v1",
                                                  "    reinit_stream(reader, compressed);\n", "    reinit_stream(reader,  compressed);\n"),
    "patch-wine-mfplat-d3d9-discard-copy.py": ("dlls/mfplat/buffer.c", "madeira-bcd: mfplat-d3d9-discard-copy-v1",
                                               "    d3d9_surface_buffer_Lock2DSize,\n", "    d3d9_surface_buffer_Lock2DSize ,\n"),
}

# ---------------------------------------------------------------- where the pinned source is read
gl = subprocess.run(["git", "-C", str(root), "ls-tree", "HEAD", "wine"], capture_output=True, text=True).stdout.split()
pin = gl[2] if len(gl) >= 3 and gl[0] == "160000" else "HEAD"
sources = []
for cand in (root / "wine", Path(os.environ.get("WINE_SRC", "/nonexistent")), Path.home() / "madeira-local/kaynak/wine.git",
             Path.home() / "madeira-bcd/wine"):
    if cand.exists() and subprocess.run(["git", "-C", str(cand), "cat-file", "-e", pin + ":dlls/mf/session.c"], capture_output=True).returncode == 0:
        sources.append(cand)
if not sources:
    print("SKIP: no Wine repository holds the pinned commit %s (wine/, WINE_SRC, ~/madeira-local/kaynak/wine.git, ~/madeira-bcd/wine)" % pin[:12])
    print("PASS: wine #78 patches (nothing to check)")
    sys.exit(0)
src = sources[0]
print("pinned source: %s at %s" % (src, pin[:12]))


def pinned(rel):
    r = subprocess.run(["git", "-C", str(src), "show", "%s:%s" % (pin, rel)], capture_output=True, text=True)
    return r.stdout if r.returncode == 0 else None


def run(script, target):
    return subprocess.run([sys.executable, str(root / "tools" / script), str(target)], capture_output=True, text=True)


def body(text, start, end="\n}\n"):
    a = text.index(start)
    return text[a:text.index(end, a) + len(end)]


def changed_lines(before, after):
    import difflib
    out = []
    for l in difflib.unified_diff(before.splitlines(), after.splitlines(), lineterm="", n=0):
        if l.startswith(("+++", "---", "@@")):
            continue
        out.append(l)
    return out


with tempfile.TemporaryDirectory(prefix="wine-mf-patches-") as tmp:
    tmp = Path(tmp)
    patched = {}
    for script, (rel, marker, move_old, move_new) in SCRIPTS.items():
        check("%s exists" % script, (root / "tools" / script).is_file())
        text = pinned(rel)
        check("%s is in the pinned source" % rel, text is not None)
        if text is None or not (root / "tools" / script).is_file():
            continue
        t = tmp / script
        (t / rel).parent.mkdir(parents=True, exist_ok=True)
        (t / rel).write_text(text)
        r1, r2 = run(script, t), run(script, t)
        out = (t / rel).read_text()
        check("%s: patched once (marker written once), then 'already patched'" % script,
              r1.returncode == 0 and "patched" in r1.stdout and "already" not in r1.stdout and r2.returncode == 0 and "already patched" in r2.stdout
              and out.count(marker) >= 1 and out != text, r1.stdout + r1.stderr + r2.stdout + r2.stderr)
        # the script also takes the file itself
        t1 = tmp / (script + ".file.c")
        t1.write_text(text)
        r3 = subprocess.run([sys.executable, str(root / "tools" / script), str(t1)], capture_output=True, text=True)
        check("%s: the file path works as the argument, and gives the same result" % script, r3.returncode == 0 and t1.read_text() == out, r3.stderr)
        # a moved anchor: refused by name, nothing written
        t2 = tmp / (script + ".moved")
        (t2 / rel).parent.mkdir(parents=True, exist_ok=True)
        check("%s: the anchor used for the moved-anchor check is in the pinned file once" % script, text.count(move_old) == 1, move_old)
        (t2 / rel).write_text(text.replace(move_old, move_new, 1))
        before = (t2 / rel).read_text()
        r4 = run(script, t2)
        check("%s: a file with a moved anchor is refused by name and left alone" % script,
              r4.returncode != 0 and script.replace(".py", "") in (r4.stdout + r4.stderr) and (t2 / rel).read_text() == before, r4.stdout + r4.stderr)
        patched[script] = (t, text, out)

    # ------------------------------------------------------------ what each one changes (the pull request's lines, no others)
    if "patch-wine-mf-session-keep-node.py" in patched:
        t, old, new = patched["patch-wine-mf-session-keep-node.py"]
        removed = [l for l in changed_lines(old, new) if l.startswith("-")]
        check("session.c: the only lines removed are the WARN of the failed allocator initialization and the callback set-up that moves into the else branch",
              len(removed) == 5 and any("WARN(\"Failed to initialize sample allocator" in l for l in removed)
              and sum("IMFVideoSampleAllocator" in l or "notify_cb" in l for l in removed) == 4, removed)
        check("session.c: a failed initialization releases the allocator, clears the pointer and returns S_OK for the node; the callback is set only when it worked",
              'ERR("Failed to initialize sample allocator for the stream, hr %#lx.\\n", hr);\n                            '
              "IMFVideoSampleAllocator_Release(topo_node->u.sink.allocator);\n                            "
              "topo_node->u.sink.allocator = NULL;\n                            hr = S_OK;\n                        }\n                        else\n" in new
              and new.count("IMFVideoSampleAllocator_QueryInterface(topo_node->u.sink.allocator,") == 1)
        check("session.c: every other use of the sink allocator is guarded (the node's release, AllocateSample, the media type change), so a node without one is safe",
              "            if (node->u.sink.allocator)\n                IMFVideoSampleAllocator_Release(node->u.sink.allocator);\n            if (node->u.sink.allocator_cb)" in new
              and "topo_node->type == MF_TOPOLOGY_OUTPUT_NODE && topo_node->u.sink.allocator)" in new
              and "if (SUCCEEDED(hr) && topo_node->u.sink.allocator)" in new
              and len(re.findall(r"u\.sink\.allocator\b", new)) == len(re.findall(r"u\.sink\.allocator\b", old)) + 2)

    if "patch-wine-wmreader-compressed-streams.py" in patched:
        t, old, new = patched["patch-wine-wmreader-compressed-streams.py"]
        reinit = body(new, "static HRESULT reinit_stream(")
        sss = body(new, "static HRESULT WINAPI reader_SetReadStreamSamples(")
        fs = body(new, "static void free_streams(")
        check("wm_reader.c: free_streams() resets stream_count with the array", "    reader->streams = NULL;\n    reader->stream_count = 0;" in fs)
        check("wm_reader.c: reinit_stream() takes no flag (each stream's own is used) and never frees the streams",
              "static HRESULT reinit_stream(struct wm_reader *reader)\n" in new and "free_streams(" not in reinit
              and re.search(r"(?<!>)read_compressed \? &format", reinit) is None
              and "stream->read_compressed ? &format : &stream->format" in reinit and "assert(reader->stream_count" not in reinit)
        check("wm_reader.c: a parser with another number of streams fails the call (E_FAIL) instead of asserting; a failed semaphore wait now fails too",
              "if (wg_parser_get_stream_count(reader->wg_parser) != reader->stream_count)" in reinit and reinit.count("hr = E_FAIL;") == 2)
        check("wm_reader.c: a failed reinit forgets only the handles into the destroyed parser (wg_stream = 0), keeping the streams",
              "reader->streams[i].wg_stream = 0;" in reinit and reinit.index("reader->streams[i].wg_stream = 0;") > reinit.index("out_destroy_parser:"))
        check("wm_reader.c: a second call after a failed one does not touch a parser, thread or semaphore that is gone",
              all(g in reinit for g in ("if (reader->read_sem)\n        ReleaseSemaphore", "if (reader->wg_parser)\n        wg_parser_disconnect", "if (reader->read_thread)\n    {",
                                        "if (reader->wg_parser)\n        wg_parser_destroy")))
        check("wm_reader.c: SetReadStreamSamples goes back to the previous mode and parser on failure and returns the failure, not S_OK",
              "old_compressed = stream->read_compressed;" in sss and "stream->read_compressed = old_compressed;" in sss
              and "if (FAILED(reinit_stream(reader)))" in sss and sss.rstrip().endswith("return hr;\n}") and "return S_OK;" not in sss)
        check("wm_reader.c: reinit_stream has two uses (its definition and SetReadStreamSamples) and nothing else changed in the file's other functions",
              len(re.findall(r"\breinit_stream\(", new)) == 3 and len(changed_lines(old, new)) < 140, len(changed_lines(old, new)))

    if "patch-wine-mfplat-d3d9-discard-copy.py" in patched:
        t, old, new = patched["patch-wine-mfplat-d3d9-discard-copy.py"]
        fn = body(new, "static HRESULT WINAPI d3d9_surface_buffer_ContiguousCopyFrom(")
        d3d9_vtbl = body(new, "static const IMF2DBuffer2Vtbl d3d9_surface_buffer_vtbl =", "\n};\n")
        check("mfplat buffer.c: the d3d9 surface buffer's vtable uses the new ContiguousCopyFrom; no other vtable does",
              "    d3d9_surface_buffer_ContiguousCopyFrom,\n" in d3d9_vtbl and "memory_2d_buffer_ContiguousCopyFrom" not in d3d9_vtbl
              and new.count("d3d9_surface_buffer_ContiguousCopyFrom") == 2 and new.count("    memory_2d_buffer_ContiguousCopyFrom,\n") == old.count("    memory_2d_buffer_ContiguousCopyFrom,\n") - 1)
        check("mfplat buffer.c: the new function is the generic one's bounds check, a generic fallback for a locked or linear buffer, and ONE discard lock",
              "if (src_length < buffer->_2d.plane_size)\n        return E_INVALIDARG;" in fn
              and "if (buffer->_2d.linear_buffer || buffer->_2d.locks)" in fn and "return memory_2d_buffer_ContiguousCopyFrom(iface, src_buffer, src_length);" in fn
              and fn.count("IDirect3DSurface9_LockRect(") == 1 and "NULL, D3DLOCK_DISCARD)" in fn
              and "copy_image(buffer, rect.pBits, rect.Pitch, src_buffer, buffer->_2d.width,\n                buffer->_2d.width, buffer->_2d.height);" in fn
              and fn.count("EnterCriticalSection(&buffer->cs)") == 1 and fn.count("LeaveCriticalSection(&buffer->cs)") == 2)
        check("mfplat buffer.c: the generic ContiguousCopyFrom is untouched (its text is byte for byte the pinned one)",
              body(new, "static HRESULT WINAPI memory_2d_buffer_ContiguousCopyFrom(") == body(old, "static HRESULT WINAPI memory_2d_buffer_ContiguousCopyFrom("))

    # ------------------------------------------------------------ the changes compile with the module's own i386 flags
    def tool_dir():
        for tc in sorted((Path.home() / "madeira-bcd/toolchains").glob("llvm-mingw-*/bin")) + sorted((root / "toolchains").glob("llvm-mingw-*/bin")):
            if (tc / "i686-w64-mingw32-gcc").exists() or (tc / "i686-w64-mingw32-clang").exists():
                return tc
        return None

    def build_dir():
        for cand in (Path(os.environ.get("WINE_I386_BUILD", "/nonexistent")), root / "wine/build-i386", Path.home() / "madeira-bcd/wine/build-i386"):
            if (cand / "compile_commands.json").is_file():
                return cand
        return None

    def syntax_compile(b, tc, rel, path):
        import json
        cmds = json.loads((b / "compile_commands.json").read_text())
        e = next((c for c in cmds if c["file"] == "../" + rel), None)
        if not e:
            return None
        argv, out, skip = shlex.split(e["command"]), [], 0
        for a in argv:
            if skip:
                skip -= 1
                continue
            if a in ("-o", "-MF", "-MT", "-MQ"):
                skip = 1
                continue
            if a in ("-MD", "-MMD", "-MP", "-c"):
                continue
            out.append(str(path) if a == "../" + rel else a)
        gcc = tc / "i686-w64-mingw32-gcc"
        out[0] = str(gcc if gcc.exists() else tc / "i686-w64-mingw32-clang")
        out += ["-c", "-fsyntax-only"]
        return subprocess.run(out, cwd=str(b), capture_output=True, text=True, env={**os.environ, "PATH": "%s:%s" % (tc, os.environ.get("PATH", ""))})

    tc, bd = tool_dir(), build_dir()
    if os.environ.get("WINE_PE_NO_COMPILE"):
        print("SKIP: WINE_PE_NO_COMPILE is set: the patched files are not compiled")
    elif not tc or not bd:
        print("SKIP: no llvm-mingw or no configured i386 Wine build tree (wine/build-i386, WINE_I386_BUILD, ~/madeira-bcd/wine/build-i386): the patched files are not compiled")
    else:
        warn = lambda r: sorted(set(re.findall(r"warning: [^\n]*", r.stderr)))
        for script, (rel, _m, _o, _n) in SCRIPTS.items():
            if script not in patched:
                continue
            t, old, new = patched[script]
            pre = tmp / ("pin-" + rel.replace("/", "_"))
            pre.write_text(old)
            post = tmp / ("new-" + rel.replace("/", "_"))
            post.write_text(new)
            r0, r1 = syntax_compile(bd, tc, rel, pre), syntax_compile(bd, tc, rel, post)
            if r0 is None or r1 is None:
                print("SKIP: %s has no compile command in %s" % (rel, bd))
                continue
            check("%s compiles with the i386 module's flags (no error; the same warnings as the pinned file: %d)" % (rel, len(warn(r0))),
                  r0.returncode == 0 and r1.returncode == 0 and warn(r1) == warn(r0), (r1.stderr or "")[-1500:])

# ---------------------------------------------------------------- the wiring
wf = (root / ".github/workflows/build-ipa.yml").read_text()
fresh = (root / "tools/build-wine-fresh-dlls.sh").read_text()
steps = [s for s in SCRIPTS]
check("the workflow runs each script with the wine tree, before the i386 farm is built and before 'Check the 64-bit rebuild patches'",
      all(("python3 tools/%s wine" % s) in wf for s in steps)
      and all(wf.index("python3 tools/%s wine" % s) < wf.index("- name: Check the 64-bit rebuild patches") < wf.index("- name: Build i386 farm (WoW64)") for s in steps))
check("the workflow runs this check", "python3 tests/host/check-wine-mf-patches.py" in wf)
check("the wm_reader change is put back with the i386-only files before the extra-DLL builds (the 64-bit winegstreamer.dll stays as it is)",
      "git -C wine checkout -- dlls/winegstreamer/wm_reader.c" in wf
      and wf.index("python3 tools/patch-wine-wmreader-compressed-streams.py wine") < wf.index("git -C wine checkout -- dlls/winegstreamer/wm_reader.c")
      and wf.index("git -C wine checkout -- dlls/winegstreamer/wm_reader.c") < wf.index("bash tools/build-wine-extra-dlls.sh"))
check("the mf and mfplat changes are NOT put back (mf.dll and mfplat.dll of the 64-bit sets are committed binaries no extra-DLL step builds)",
      "checkout -- dlls/mf" not in wf and "checkout -- dlls/mfplat" not in wf)
lst = re.search(r'FRESH_PATCHES="\n(.*?)"\n', fresh, re.S)
names = [l.split()[0] for l in lst.group(1).splitlines() if l.strip()] if lst else []
check("the rebuilt 64-bit set applies all three, after the wintrust scripts, once each",
      all(names.count(s) == 1 for s in steps) and names.index("patch-wine-wintrust-root-pin.py") < min(names.index(s) for s in steps))
mods = (root / "tools/wine-fresh-modules.txt").read_text()
check("mf.dll and mfplat.dll are modules of the rebuilt set (the two scripts that reach a committed 64-bit module); winegstreamer.dll is not committed there",
      "\tmf.dll\tdlls/mf/arm64ec-windows/mf.dll" in mods and "\tmfplat.dll\tdlls/mfplat/arm64ec-windows/mfplat.dll" in mods and "winegstreamer" not in mods)
notices = (root / "THIRD-PARTY-NOTICES.md").read_text()
check("the notices credit wenze-allen for the three Wine changes, and the three copies are the same",
      "wenze-allen" in notices and all(("tools/%s" % s) in notices for s in steps)
      and (root / "app/Madeira/legal/THIRD-PARTY-NOTICES.md").read_text() == notices
      and "wenze-allen" in (root / "app/Madeira/licenses/THIRD-PARTY-NOTICES.txt").read_text())
check("each script names its author and source in its header (no e-mail address anywhere)",
      all("Portions copyright 2026 wenze-allen" in (root / "tools" / s).read_text() and "@" not in "\n".join(
          l for l in (root / "tools" / s).read_text().splitlines() if "pull request" in l or "Copyright" in l) for s in steps))

print("PASS: wine #78 patches" if ok else "FAILED: wine #78 patches")
sys.exit(0 if ok else 1)

# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""Shared by the checks of the winemetal patch scripts of the Porthole port (check-framegen-live.py, check-present-fx.py, ...).

Each of those scripts is a table of (name, anchor, replacement) hunks on dxmt/src/winemetal/unix/winemetal_unix.c, and each builds on
the one before. A check has to pass on an unpatched submodule (CI's check step runs before the patch steps) and on a tree a build
already patched (a resumed build; build 652's lesson), so every check works on a copy:

  stage(script) copies dxmt/src (or $DXMT_SRC_DIR/src), brings the copy to the state right after `script`:
    - an unpatched copy gets the workflow's chain up to and including `script`;
    - a copy that already holds later scripts of this family has their hunks undone first, last script first (each hunk must be
      there exactly once), so the text checked is the same in both cases;
  and returns the directory, the file's text before and after `script`, and whether the copy came patched.
"""
import importlib.util
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

R = Path(__file__).resolve().parents[2]
SRC = Path(os.environ.get("DXMT_SRC_DIR", R / "dxmt")) / "src"
REL = "dxmt/src/winemetal/unix/winemetal_unix.c"
# The family, in the order the workflow runs it.
FAMILY = [s for s in ("tools/patch-winemetal-framegen-live.py", "tools/patch-winemetal-present-fx.py",
                       "tools/patch-winemetal-lsfg-pacing.py", "tools/patch-winemetal-vt-interp.py") if (R / s).is_file()]


def have_sources():
    return (SRC / "winemetal" / "unix" / "winemetal_unix.c").is_file()


def load(script):
    path = R / script
    spec = importlib.util.spec_from_file_location(path.stem.replace("-", "_"), path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def chain():
    wf = (R / ".github/workflows/build-ipa.yml").read_text()
    return re.findall(r"python3 (tools/patch-(?:dxmt|airconv|winemetal)-[\w.-]+\.py)", wf)


def undo(text, script):
    """`text` without the hunks of `script`; None when a hunk is not there exactly once."""
    mod = load(script)
    for _, anchor, new in reversed(mod.PAIRS):
        if text.count(new) != 1:
            return None
        text = text.replace(new, anchor, 1)
    return text


def older_tree(script, text):
    """`script` run on `text` as an older dxmt checkout has it: frame generation without the fork's one-layer change (dxmt 1f617cd), which
    this family builds on (the checks that apply the whole chain to the overlay base, tests/host/dxmt_base.py, meet such a tree).
    Returns (return code, output, whether the file was left as it was)."""
    with tempfile.TemporaryDirectory() as tmp:
        t = Path(tmp)
        (t / REL).parent.mkdir(parents=True)
        old = text.replace("madeira_fg_other_layer", "madeira_fg_some_layer")
        (t / REL).write_text(old)
        p = subprocess.run([sys.executable, str(R / script)], cwd=t, capture_output=True, text=True)
        return p.returncode, p.stdout + p.stderr, (t / REL).read_text() == old


class Stage:
    def __init__(self, script):
        self.script = script
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.problems = []
        t = self.dir
        shutil.copytree(SRC, t / "dxmt/src")
        (t / "build").mkdir()
        shutil.copy(R / "build/madeira_cfg.h", t / "build/madeira_cfg.h")
        shutil.copytree(R / "research/remote-metal", t / "research/remote-metal")
        self.file = t / REL
        text = self.file.read_text()
        mod = load(script)
        self.mod = mod
        self.came_patched = mod.MARKER in text
        order = chain()
        self.in_workflow = script in order
        # later scripts of the family that are already in the copy: undo them, last first
        later = [s for s in FAMILY[FAMILY.index(script) + 1:] if load(s).MARKER in text]
        for s in reversed(later):
            undone = undo(text, s)
            if undone is None:
                self.problems.append(f"{s}: its hunks are not all in the patched copy exactly once")
            else:
                text = undone
        self.file.write_text(text)
        if not self.came_patched and self.in_workflow:
            for s in order[:order.index(script)]:
                if s.endswith("patch-dxmt-metal-atomics.py"):
                    continue   # it patches the repository's own dxmt whatever the working directory, and only a shader these checks do not read
                p = subprocess.run([sys.executable, str(R / s)], cwd=t, capture_output=True, text=True)
                if p.returncode:
                    self.problems.append(f"{s}: {(p.stdout + p.stderr).strip()[:300]}")
        self.before = self.file.read_text() if not self.came_patched else None
        self.first = subprocess.run([sys.executable, str(R / script)], cwd=t, capture_output=True, text=True)
        self.after = self.file.read_text()
        self.second = subprocess.run([sys.executable, str(R / script)], cwd=t, capture_output=True, text=True)
        self.idempotent = self.second.returncode == 0 and self.file.read_text() == self.after
        if self.came_patched:
            self.before = undo(self.after, script)

    def exact(self):
        """The script changed the file by its own hunks and nothing else."""
        undone = undo(self.after, self.script)
        if undone is None:
            return False
        return self.mod.MARKER not in undone and (self.before is None or undone == self.before)

    def compile_ios(self):
        """(ran, ok, stderr): the staged file compiled for iOS arm64 with the flags of build/dxmt-ios/build.sh (syntax only)."""
        if not shutil.which("xcrun") or not (R / "dxmt/include").is_dir():
            return False, False, ""
        sdk = subprocess.run(["xcrun", "--sdk", "iphoneos", "--show-sdk-path"], capture_output=True, text=True)
        if sdk.returncode or not sdk.stdout.strip():
            return False, False, ""
        d = R / "dxmt"
        c = subprocess.run(["xcrun", "-sdk", "iphoneos", "clang", "-arch", "arm64", "-isysroot", sdk.stdout.strip(),
                            "-miphoneos-version-min=18.0", "-fblocks", "-O2", "-x", "objective-c", "-fsyntax-only",
                            f"-I{d}/include", f"-I{d}/libs", f"-I{self.dir}/dxmt/src/winemetal", f"-I{d}/src/airconv", str(self.file)],
                           capture_output=True, text=True)
        return True, c.returncode == 0, c.stderr

    def close(self):
        self.tmp.cleanup()


def body(text, start):
    """The function that starts with `start`, up to its closing brace in column 0."""
    a = text.index(start)
    return text[a:text.index("\n}\n", a) + 3]

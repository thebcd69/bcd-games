#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""Preview reuse must preserve invalidation, failure and parallel-check isolation.

Run the production cache with small stand-in compiler outputs. Real compilation
is still exercised by every existing preview check (one successful build per
source/toolchain key). This check does not need Xcode or download anything.
"""
from concurrent.futures import ThreadPoolExecutor
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("bcd_preview", ROOT / "tools/ui-preview.py")
preview = importlib.util.module_from_spec(spec)
spec.loader.exec_module(preview)

with tempfile.TemporaryDirectory(prefix="bcd-preview-cache-check-") as tmp:
    base = Path(tmp)
    src = base / "source"
    app = src / "app/Madeira"
    samples = src / "app/BCDPreview"
    sdk = base / "SDK"
    art = base / "art"
    for folder in (app, samples, sdk, art / "620", base / "bin"):
        folder.mkdir(parents=True)
    (app / "BCDThemes.json").write_text("[]")
    source = app / "BCDCacheCheck.swift"
    source.write_text("let value = 1\n")
    (samples / "Sample.swift").write_text("let sample = 1\n")
    (app / "BCDLiveStore.swift").write_text("excluded engine file")
    for name in ("BCDLogo.imageset", "AppIcon.appiconset"):
        folder = app / "Assets.xcassets" / name
        folder.mkdir(parents=True)
        (folder / "Contents.json").write_text("{}")
    (sdk / "SDKSettings.json").write_text('{"Version":"1"}')
    (art / "620/header.jpg").write_bytes(b"picture")
    for name in ("swiftc", "actool"):
        (base / "bin" / name).write_text(name)
    versions = {"swift": "Swift version 1", "xcode": "Xcode 1"}
    attempts = []
    behavior = {"fail": False, "mutate": False}

    def run(command, **kw):
        if command == ["xcrun", "swiftc", "--version"]:
            return versions["swift"]
        if command == ["xcodebuild", "-version"]:
            return versions["xcode"]
        if command[:2] == ["xcrun", "--find"]:
            return str(base / "bin" / command[2])
        if command == ["xcrun", "--sdk", "iphonesimulator", "--show-sdk-path"]:
            return str(sdk)
        raise AssertionError(command)

    def compile_preview(out):
        attempts.append(1)
        time.sleep(0.02)  # Let competing callers arrive while the first owns the key.
        if behavior["fail"]:
            raise SystemExit("compiler failure")
        target = out / "BCDPreview.app"
        target.mkdir(parents=True)
        (target / "BCDPreview").write_bytes(b"compiled preview")
        (target / "BCDPreview").chmod(0o755)
        (target / "Info.plist").write_bytes(b"bundle metadata")
        if behavior["mutate"]:
            behavior["mutate"] = False
            source.write_text(source.read_text() + "// edited during compilation\n")
        return target

    cache = base / "cache"
    env = {"BCD_PREVIEW_BUILD_CACHE": str(cache), "BCD_PREVIEW_NO_CACHE": "0"}
    with patch.multiple(preview, ROOT=src, APP=app, PREVIEW=samples, ART_CACHE=art,
                        run=run, fetch_art=lambda: None, _build=compile_preview), patch.dict(os.environ, env):
        # Parallel checks share the same filesystem lock.
        with ThreadPoolExecutor(8) as pool:
            outputs = list(pool.map(lambda n: preview.build(base / f"out-{n}"), range(8)))
        assert len(attempts) == 1
        assert len(set(outputs)) == 8
        (outputs[0] / "BCDPreview").write_bytes(b"caller edited its own output")
        assert (outputs[1] / "BCDPreview").read_bytes() == b"compiled preview"
        preview.build(base / "independent-output")
        assert len(attempts) == 1

        # Same timestamps do not permit stale content, new files or removed files.
        stamp = source.stat()
        source.write_text("let value = 2\n")
        os.utime(source, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
        preview.build(base / "source-changed")
        assert len(attempts) == 2
        new_source = app / "BCDNew.swift"
        new_source.write_text("let added = true")
        preview.build(base / "source-added")
        assert len(attempts) == 3
        new_source.unlink()
        preview.build(base / "source-removed")  # Exact prior successful source is reusable.
        assert len(attempts) == 3
        (app / "BCDLiveStore.swift").write_text("still excluded from preview")
        preview.build(base / "excluded-engine-change")
        assert len(attempts) == 3
        nested = app / "BCDEpic/Engine.swift"
        nested.parent.mkdir()
        nested.write_text("an engine-only file outside the top-level preview glob")
        preview.build(base / "nested-engine-file")
        assert len(attempts) == 3

        mutations = [
            lambda: (app / "BCDThemes.json").write_text('[ {"version":1} ]'),
            lambda: (samples / "Sample.swift").write_text("let sample = 2"),
            lambda: (app / "Assets.xcassets/BCDLogo.imageset/Contents.json").write_text('{"new":1}'),
            lambda: (sdk / "SDKSettings.json").write_text('{"Version":"2"}'),
            lambda: versions.update(swift="Swift version 2"),
            lambda: versions.update(xcode="Xcode 2"),
            lambda: (art / "620/header.jpg").write_bytes(b"new picture"),
        ]
        for index, mutate in enumerate(mutations, 4):
            mutate()
            preview.build(base / f"invalidated-{index}")
            assert len(attempts) == index

        # A damaged artifact must be rebuilt; preserve the bad entry for diagnosis.
        key = preview._build_key()
        (cache / key / "BCDPreview.app/BCDPreview").write_bytes(b"damaged")
        before = len(attempts)
        preview.build(base / "repaired")
        assert len(attempts) == before + 1
        assert list(cache.glob(key + ".invalid-*"))

        # Failure or changing inputs during compilation never publish success.
        source.write_text("let fail = true")
        key = preview._build_key()
        behavior["fail"] = True
        try:
            preview.build(base / "failed")
            raise AssertionError("failed compilation accepted")
        except SystemExit:
            pass
        assert not (cache / key / "complete.json").exists()
        behavior.update(fail=False, mutate=True)
        try:
            preview.build(base / "changed-during-build")
            raise AssertionError("changing inputs accepted")
        except SystemExit:
            pass
        assert not (cache / key / "complete.json").exists()
        preview.build(base / "after-failure")
        assert (base / "after-failure/BCDPreview.app/BCDPreview").exists()

        before = len(attempts)
        preview.build(base / "forced-1", use_cache=False)
        preview.build(base / "forced-2", use_cache=False)
        assert len(attempts) == before + 2

print("PASS: preview invalidation, parallel single compilation, output isolation, damage and failure handling")

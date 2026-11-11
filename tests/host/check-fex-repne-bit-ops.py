#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""tools/patch-fex-ios-repne-bit-ops.py: REPNE/REP-prefixed BSF/BSR/BTC/MOVSX/CMOVcc decode like the unprefixed forms (night round 2026-11-11, G2 S9).

Blur (43HNJX, 32-bit, build 634): the obfuscated start-up stub executes `f2 0f bc ee` (BSF with a redundant REPNE prefix, which an x86 processor ignores);
FEX's REPNE table marked 0F BB..BF invalid: `Invalid instruction in entry block: 136C70B`, `[entry-fail] ... invalid instruction (hlt thunk)`, exit code 0xc000001d.

Checks (FEX tree: FEX_SRC, default ~/madeira-bcd/FEX; that part is skipped without it): the patch applies to a copy of the pinned sources in the build's order of
BOTH modules (tests/host/fex_chain.py), a second run says "already in" and changes nothing, a moved anchor refuses it (nothing written); exactly five table
entries change and only from TYPE_INVALID to TYPE_COPY_OTHER (the logged REPNE 0F BB..BF; REP 0F BB and 0F BE..BF; CMOVcc 0F 40..4F in both prefix tables),
inside the right table; every other line of the file is unchanged; the neighbouring entries the patch must not touch stay INVALID (REPNE 0F B8 = JMPE, the
SSE holes); the table entries that were already COPY_OTHER are unchanged in number plus the new ones. Both module builds apply it without failing the build and
restore SecondaryTables.cpp; auto-tune's rule; CI runs this check.
"""
from pathlib import Path
import difflib, os, re, subprocess, sys, tempfile

root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import fex_chain

ok = True


def check(what, cond, detail=""):
    global ok
    print(("ok   " if cond else "FAIL ") + what + (("\n     " + str(detail)[:1500]) if (detail and not cond) else ""))
    ok = ok and bool(cond)


NAME = "patch-fex-ios-repne-bit-ops.py"
MARK = "repne-bit-ops-v1 madeira-bcd"
FILE = "FEXCore/Source/Interface/Core/X86Tables/SecondaryTables.cpp"
patch = root / "tools" / NAME
check("the patch script exists", patch.is_file())
for sh, label in (("tools/build-xtajit64.sh", "ARM64EC"), ("tools/build-xtajit-wow64.sh", "WOW64")):
    t = (root / sh).read_text()
    i = t.index('git -C FEX checkout -- "$CPUF"' if label == "ARM64EC" else "restore() {")
    restore = t[i:t.index("\n", i)]
    check("the " + label + " build applies it once, without failing the build, and restores SecondaryTables.cpp",
          t.count('python3 "$R/tools/' + NAME + '" "$R/FEX" || echo "::warning::') == 1 and FILE in restore)
wf = (root / ".github/workflows/build-ipa.yml").read_text()
check("CI runs this check", "python3 tests/host/check-fex-repne-bit-ops.py" in wf)
rules = (root / "build/tools/autotune_rules.py").read_text()
check("auto-tune names the fix (x86-repne-bsf-invalid, fixedIn 652)", re.search(r'"id": "x86-repne-bsf-invalid",\s*"lines": \[\],\s*"fixedIn": 652', rules) is not None)

fex = Path(os.environ.get("FEX_SRC", Path.home() / "madeira-bcd/FEX"))


def table_entries(text, start, end):
    a = text.index(start)
    b = text.index(end, a)
    out = {}
    for m in re.finditer(r'\{0x([0-9A-Fa-f]+), (\d+),\s+X86InstInfo\{"([A-Za-z0-9]*)",\s+(TYPE_[A-Z_]+)', text[a:b]):
        out[(int(m.group(1), 16), int(m.group(2)))] = (m.group(3), m.group(4))
    return out


REP = ("constexpr std::array<X86InstInfo, MAX_REP_MOD_TABLE_SIZE> RepModOps = []() consteval {", "GenerateTableWithCopy(Table.data(), RepModOpTable,")
REPNE = ("constexpr std::array<X86InstInfo, MAX_REPNE_MOD_TABLE_SIZE> RepNEModOps = []() consteval {", "GenerateTableWithCopy(Table.data(), RepNEModOpTable,")

if not (fex / FILE).is_file():
    print("skip: no FEX tree here (FEX_SRC or ~/madeira-bcd/FEX); the CI and the Mac run the source part")
else:
    for kind in ("ec", "wow"):
        with tempfile.TemporaryDirectory(prefix="madeira-fex-repne-") as tmp:
            tree = fex_chain.copy_sources(fex, Path(tmp) / "t")
            res = fex_chain.run_chain(root, kind, tree, before=NAME)
            check(kind + ": the build's scripts ahead of it apply (" + str(len(res)) + ")", not fex_chain.failures(res), fex_chain.failures(res))
            f = tree / FILE
            before = f.read_text()
            r1 = subprocess.run([sys.executable, str(patch), str(tree)], capture_output=True, text=True)
            once = f.read_text()
            r2 = subprocess.run([sys.executable, str(patch), str(tree)], capture_output=True, text=True)
            check(kind + ": it applies once; a second run says so and changes nothing",
                  r1.returncode == 0 and r2.returncode == 0 and "already in" in r2.stdout and f.read_text() == once and once != before, r1.stdout + r1.stderr + r2.stdout + r2.stderr)
            res = fex_chain.run_chain(root, kind, tree, after=NAME)
            check(kind + ": the scripts behind it still apply (" + str(len(res)) + ")", not fex_chain.failures(res), fex_chain.failures(res))
            if kind != "ec":
                continue
            a, b = before.splitlines(), once.splitlines()
            ops = [o for o in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes() if o[0] != "equal"]
            check("exactly five lines are replaced by one line each, nothing else changes", len(ops) == 5 and all(o[0] == "replace" and o[2] - o[1] == 1 and o[4] - o[3] == 1 for o in ops), ops)
            for old, new in zip((a[o[1]] for o in ops), (b[o[3]] for o in ops)):
                if new.replace("TYPE_COPY_OTHER", "TYPE_INVALID").split("   // ")[0].split() != old.split():
                    check("a changed line differs from its old form only by TYPE_INVALID -> TYPE_COPY_OTHER and the comment", False, (old, new))
                    break
            else:
                check("every changed line differs from its old form only by TYPE_INVALID -> TYPE_COPY_OTHER and the trailing comment", True)
            rb, ra = table_entries(before, *REP), table_entries(once, *REP)
            nb, na = table_entries(before, *REPNE), table_entries(once, *REPNE)
            check("REPNE table: 0F BB..BF and 0F 40..4F were INVALID and are COPY_OTHER now; 0F B8 (JMPE) stays INVALID",
                  nb[(0xBB, 5)][1] == "TYPE_INVALID" and na[(0xBB, 5)][1] == "TYPE_COPY_OTHER" and nb[(0x40, 16)][1] == "TYPE_INVALID" and na[(0x40, 16)][1] == "TYPE_COPY_OTHER"
                  and na[(0xB8, 1)][1] == "TYPE_INVALID", (nb, na))
            check("REP table: 0F BB, 0F BE..BF and 0F 40..4F were INVALID and are COPY_OTHER now; POPCNT, TZCNT and LZCNT are untouched",
                  rb[(0xBB, 1)][1] == "TYPE_INVALID" and ra[(0xBB, 1)][1] == "TYPE_COPY_OTHER" and rb[(0xBE, 2)][1] == "TYPE_INVALID" and ra[(0xBE, 2)][1] == "TYPE_COPY_OTHER"
                  and rb[(0x40, 16)][1] == "TYPE_INVALID" and ra[(0x40, 16)][1] == "TYPE_COPY_OTHER"
                  and ra[(0xB8, 1)] == ("POPCNT", "TYPE_INST") and ra[(0xBC, 1)] == ("TZCNT", "TYPE_INST") and ra[(0xBD, 1)] == ("LZCNT", "TYPE_INST"), (rb, ra))
            changed = [k for k in nb if nb[k] != na[k]] + [k for k in rb if rb[k] != ra[k]]
            check("no other entry of either table changed", len(changed) == 5, changed)

    with tempfile.TemporaryDirectory(prefix="madeira-fex-repne-") as tmp:
        tree = fex_chain.copy_sources(fex, Path(tmp) / "t")
        f = tree / FILE
        f.write_text(f.read_text().replace("{0xBB, 5,  X86InstInfo{\"\",         TYPE_INVALID,", "{0xBB, 4,  X86InstInfo{\"\",         TYPE_INVALID,", 1))
        stale = f.read_text()
        r3 = subprocess.run([sys.executable, str(patch), str(tree)], capture_output=True, text=True)
        check("a moved anchor refuses the patch and writes nothing", r3.returncode != 0 and "not one TYPE_INVALID line" in r3.stderr and f.read_text() == stale, r3.stdout + r3.stderr)

print("PASS: FEX REP/REPNE-prefixed bit operations" if ok else "FAILED: FEX REP/REPNE-prefixed bit operations")
sys.exit(0 if ok else 1)

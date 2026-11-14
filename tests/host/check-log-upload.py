#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""Send logs to BCD (madeira-bcd FORK-ONLY: never in a pull request to upstream).

Checks:
  - LogUploadCore.swift, compiled alone on the host, scrubs exactly as
    tools/autotune-fixture.py's SCRUB does, line for line, on a corpus that hits every
    rule (container paths, Windows and macOS user names, e-mail, IPv4, IPv6 and MAC
    addresses, UUIDs, 40-hex ids, Steam account folders and SteamID64s, a person's device
    name) and on lines the byte prefilter passes untouched; LOG_UPLOAD_SAMPLE=<log>
    adds a real log;
  - its output is one valid gzip stream holding those lines (a source without a final
    newline keeps it that way), and an empty log packs too;
  - the intake Worker's tests pass under node (bcd-logs/worker/worker.test.mjs), when
    node is found;
  - structure: the app sends to the deployed intake with its header, deletes the
    compressed copy when the upload ends, sends by hand only (no automatic sending, no
    session hooks: owner, 2026-10-17) and only one of the 20 newest logs, which is all
    the list shows; the Xcode project builds both files; every file is marked FORK-ONLY;
    CI runs this check.
The Swift part is skipped when swiftc is missing (--require-swift makes that a failure).
"""
import argparse
import gzip
import importlib.util
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

R = Path(__file__).resolve().parents[2]
ap = argparse.ArgumentParser()
ap.add_argument("--require-swift", action="store_true")
args = ap.parse_args()
ok = True


def check(what, cond, detail=""):
    global ok
    print(("ok   " if cond else "FAIL ") + what + (("\n     " + str(detail)) if detail and not cond else ""))
    ok = ok and bool(cond)


core = (R / "app/Madeira/LogUploadCore.swift").read_text()
app = (R / "app/Madeira/LogUpload.swift").read_text()
worker = (R / "bcd-logs/worker/worker.js").read_text()
toml = (R / "bcd-logs/worker/wrangler.toml").read_text()

spec = importlib.util.spec_from_file_location("fixture", R / "tools/autotune-fixture.py")
fx = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fx)

# ---- structure ----------------------------------------------------------------------
swift_rules = re.findall(r'\(#"(.*?)"#, "(.*?)"\)', core)
check("LogUploadCore's rules are SCRUB's, in its order",
      # \" in a Python raw string is the regex's escaped quote; Swift's raw string writes it plain.
      [(p, r.replace("$1", "\\1")) for p, r in swift_rules] == [(p.pattern.replace('\\"', '"'), r) for p, r in fx.SCRUB],
      swift_rules)
check("the app sends to the deployed intake with the intake header",
      'URL(string: "https://bcd.bcd-logs.workers.dev/v1/logs")' in app
      and 'static let intake = "madeira-bcd/2"' in app and 'export const INTAKE = "madeira-bcd/2";' in worker)
up = app[app.index("session.uploadTask(with: req, fromFile: gz)"):]
check("the compressed copy is deleted as soon as the upload ends, sent or not",
      up.index("try? FileManager.default.removeItem(at: gz)") < up.index("if let error"))
check("a log that does not pack, or packs too large, leaves no compressed copy either",
      app.count("try? FileManager.default.removeItem(at: gz)") == 3)
lib = (R / "app/Madeira/Library.swift").read_text()
# Wave 3 (2026-10-19): the classic Send Log list (LogUploadSettingsSection) and the shell's Send Log sheet are deleted;
# Send Log is BCD's Settings › Logs (BCDSettingsLive.swift) and the game page's, over the same LogUpload engine.
bcd_logs = (R / "app/Madeira/BCDSettingsLive.swift").read_text()
code = "\n".join(l for l in app.split("\n") if not l.strip().startswith("//"))
check("by hand only: no toggle, no automatic path, no session hooks in the library",
      "Toggle(" not in app and "autoSend" not in code and "wifiOnly" not in code and "sessionBegan" not in code
      and "LogUpload.session" not in lib and "LogUpload.recover" not in lib and "LogUploadSettingsSection" not in lib + app)
check("only the 20 newest logs are listed, and send refuses any other",
      "static let listed = 20" in app and "(runs + own).sorted { modified($0) > modified($1) }.prefix(listed)" in app
      and "let urls = LogUpload.recentLogs()" in bcd_logs and "guard !sending, let url = files[log.id] else { return }" in bcd_logs
      # Porthole port, item 11: the thinned log's head and per-line decision follow the trailer (nothing without the switch).
      and app.index("guard recentLogs().contains(") < app.index("LogUploadCore.pack(log, to: gz, comment: comment, trailer: trace,")
      and "return finish(.failure(UploadError.notRecent))" in app)
check("build 562's automatic setting and session marker are cleared",
      'UserDefaults.standard.removeObject(forKey: "madeira.bcd.logs.autosend")' in app and '"bcd-log-session.json"' in app
      and "static func forgetAutomatic()" in app and "LogUpload.forgetAutomatic()" in bcd_logs)
pbx = (R / "app/Madeira.xcodeproj/project.pbxproj").read_text()
for f in ("LogUpload.swift", "LogUploadCore.swift"):
    check(f"the Xcode project builds {f}", pbx.count(f"/* {f} in Sources */") == 2 and pbx.count(f"path = {f};") == 1)
for f in ("app/Madeira/LogUpload.swift", "app/Madeira/LogUploadCore.swift", "bcd-logs/worker/worker.js",
          "bcd-logs/worker/wrangler.toml", "bcd-logs/worker/worker.test.mjs", "tools/pull-logs.py"):
    check(f"{f} is marked FORK-ONLY", "FORK-ONLY" in (R / f).read_text()[:800])
check("the Worker's R2 binding is its own bucket, with an hourly trim",
      'bucket_name = "bcd-logs"' in toml and 'crons = ["17 * * * *"]' in toml and "macosvm" not in toml)
CI = R / ".github/workflows/build-ipa.yml"
if CI.exists():
    check("CI runs this check", "run: python3 tests/host/check-log-upload.py --require-swift" in CI.read_text())

# ---- the Worker under node ---------------------------------------------------------------
node = shutil.which("node") or str(Path.home() / "madeira-local/tools/node/bin/node")
if Path(node).exists():
    r = subprocess.run([node, "--test", str(R / "bcd-logs/worker/worker.test.mjs")], capture_output=True, text=True)
    check("the intake Worker's tests pass under node", r.returncode == 0, (r.stdout + r.stderr)[-2000:])
else:
    print("SKIP the Worker's node tests: no node")

# ---- scrub and gzip, Swift against Python ---------------------------------------------
corpus = [
    "[game-cfg] /var/mobile/Containers/Data/Application/1D682E79-F361-4E2F-BF98-43786458BC4B/Library/x.cfg (read)",
    "unix=/private/var/mobile/Containers/Shared/AppGroup/0D682E79-F361-4E2F-BF98-43786458BC4B/fex",
    r"name=\??\C:\users\steamuser\AppData\Local\x C:\Users\Public\y c:\USERS\mobile\z \\users\\Bob\\q",
    "path=/Users/jane/Desktop/x and /Users/<user>/y",
    "account mail someone.name+tag@example.co.uk said hi, root@localhost not",
    "[jit-loopback] reachable via utun4 10.7.1.1 and 192.168.0.12, not 1.2.3 or 1.2.3.4.5",
    "uuid 1D682E79-F361-4E2F-BF98-43786458BC4B and 1d682e79-f361-4e2f-bf98-43786458bc4b",
    "sha " + "a" * 40 + " short " + "b" * 39 + " long " + "c" * 41,
    "D 24 Handled self-modifying code: pc: 1590D5560 fault: 71016B1F99",
    "[xp] 23:25:02.028 +970.32 dt=300 cpu=43 P=17 E=26 GHz P=1.90 E=1.84 IPC=2.86",
    "self-modifying - with - four - dashes - here",
    "Ünïcödé façade 東京 user users USERS Users",
    r"C:\Program Files (x86)\Steam\userdata\123456789\config\localconfig.vdf USERDATA/42/x",
    "steamid 76561198012345678 and 765611980123456789 (18 digits) and 1234567890123456",
    "mac a4:83:e7:12:34:56 and A4-83-E7-12-34-56, not 12:53:43.615",
    "addr 2a02:1234:5678:9abc:def0:1234:5678:9abc, fe80::1c2b:3d4e%en0, fd12:3456::1, std::thread dxmt::Presenter Face::Add",
    "keyboard connected: Ali's Keyboard; GameController connected: Jane’s DualSense Wireless Controller; it's a keyboard",
    "[xp-t] 23:25:02.028 m670405:17/20:1.9/2.0:214 m685463:0/2:2.4/1.1:9",
    "epic token eg1~eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJhIn0.c2lnbmF0dXJl and egoc1~eyJhbGci.abc.def, not eg1~ alone or PROGRA~1 or veg1~x",
    '{"redirectUrl":"https://localhost/launcher/authorized?code=x","authorizationCode":"0123456789abcdef0123456789abcdef","sid":null}',
    '"authorizationCode" : "short" and "authorizationCode":null',
    # Epic Games milestone 2: a signed-in start's command line (as the runtime prints it escaped, and plain), its masks, an address with a code.
    r'NtCreateUserProcess: image=L"C:\\Games\\a.exe" cmdline=L"\"C:\\Games\\a.exe\" -AUTH_LOGIN=unused -AUTH_PASSWORD=0123456789abcdef0123456789abcdef -AUTH_TYPE=exchangecode -epicapp=Sugar -epicusername=\"Some One\" -epicuserid=fedcba9876543210fedcba9876543210 -epiclocale=en"',
    'cmd -auth_password=ABCDEF0123456789ABCDEF0123456789 -EPICUSERID=fedcba9876543210fedcba9876543210 -epicusername=Plain',
    'masked: -AUTH_PASSWORD=* -epicuserid=* -epicusername=* and -AUTH_PASSWORD=<code> -epicuserid=<id> stay',
    'page https://www.epicgames.com/activate?userCode=ABCD1234&x=1 and https://localhost/cb?code=0123456789abcdef&state=2 and exchangeCode=abcdef0123456789abcdef; not exit code=0 or barcode=1 or -AUTH_PASSWORD alone',
    'short -epicuserid=12 and -epicusername="" and -epicusername= end',
    "",
    "x" * 20000,
]
sample = os.environ.get("LOG_UPLOAD_SAMPLE")
if sample and Path(sample).exists():
    with open(sample, "rb") as f:
        corpus += [l.decode("utf-8", "replace").rstrip("\r\n") for _, l in zip(range(200000), f)]

# Send Log's comment box (the owner, 2026-10-24: "her yere ekle"): every place a person sends a log by hand asks for a comment
# first (BCDLogComment, a system alert over whatever is on screen) and sends it with the log; the feedback form has its own
# message and sends its log with none.
APPDIR = R / "app/Madeira"
comp_src = (APPDIR / "BCDComponents.swift").read_text()
check("BCDLogComment asks with one text field, Send and Cancel, over the topmost screen",
      "enum BCDLogComment" in comp_src and "alert.addTextField" in comp_src and 'UIAlertAction(title: "Cancel", style: .cancel)' in comp_src
      and 'UIAlertAction(title: "Send", style: .default)' in comp_src and "while let presented = top?.presentedViewController" in comp_src)
sites = {"AutoTuneViews.swift": "BCDLogComment.ask(for: log) { LogUpload.send(log, game: offer.title, comment: $0) }",
         "BCDSettingsSections.swift": "BCDLogComment.ask(for: URL(fileURLWithPath: log.id)) { backend.send(log, comment: $0) }",
         "BCDGamePage.swift": "guard let comment = await backend.logComment(for: log) else { return }",
         "BCDGamePageLive.swift": "BCDLogComment.ask(for: file, { continuation.resume(returning: $0) }, cancelled: { continuation.resume(returning: nil) })"}
for name, want in sites.items():
    check(f"{name}: Send Log asks for the comment first", want in (APPDIR / name).read_text())
calls = {}
for f in sorted(APPDIR.glob("*.swift")):
    for line in f.read_text().splitlines():
        if "LogUpload.send(" in line and "static func send" not in line:
            calls.setdefault(f.name, []).append(line.strip())
# the two feedback forms (Settings > Feedback, and the game page's "How did it run?", round 604) send the attached log without the comment box: the message is the comment
missing = {k: v for k, v in calls.items() for l in v if "comment:" not in l and k not in ("BCDSettingsLive.swift", "BCDGamePageLiveHelp.swift")}
live = (APPDIR / "BCDSettingsLive.swift").read_text()
check("every hand-sent log carries the comment (Settings, the game page, auto-tune); only the feedback form's log goes without",
      not missing and live.count("LogUpload.send(") == 2 and "comment: comment, progress:" in live, (missing, calls))

swiftc = shutil.which("swiftc")
if not swiftc:
    check("Swift compiler available", not args.require_swift)
else:
    with tempfile.TemporaryDirectory(prefix="madeira-logupload-") as tmp:
        t = Path(tmp)
        main = t / "main.swift"
        main.write_text(
            "import Foundation\n"
            "let a = CommandLine.arguments\n"
            "let trailer = a.count > 4 && !a[4].isEmpty ? a[4].split(separator: \"\\n\", omittingEmptySubsequences: false).map(String.init) : []\n"
            "do { let r = try LogUploadCore.pack(URL(fileURLWithPath: a[1]), to: URL(fileURLWithPath: a[2]),\n"
            "                                    comment: a.count > 3 ? a[3] : nil, trailer: trailer)\n"
            "     print(\"\\(r.raw) \\(r.packed)\") } catch { print(\"error \\(error)\"); exit(1) }\n")
        exe = t / "pack"
        b = subprocess.run([swiftc, "-O", "-o", str(exe), str(R / "app/Madeira/LogUploadCore.swift"), str(main)],
                           capture_output=True, text=True)
        check("LogUploadCore.swift builds on the host", b.returncode == 0, b.stderr[-2000:])
        if b.returncode == 0:
            for name, text in (("corpus", "\n".join(corpus) + "\n"), ("no-final-newline", "a 10.0.0.1\nb"), ("empty", "")):
                src, dst = t / (name + ".txt"), t / (name + ".gz")
                src.write_text(text)
                r = subprocess.run([str(exe), str(src), str(dst)], capture_output=True, text=True)
                got = gzip.decompress(dst.read_bytes()).decode("utf-8") if r.returncode == 0 else None
                want = "\n".join(fx.scrub(l) for l in text.split("\n"))
                check(f"{name}: one gzip stream, scrubbed line for line as SCRUB does", got == want,
                      next(((i, g, w) for i, (g, w) in enumerate(zip((got or "").split("\n"), want.split("\n"))) if g != w),
                           (r.stdout, len(got or ""), len(want))))
            # Send Log's comment box (2026-10-24): the sender's words go first, one "[user-comment] " line per line, scrubbed like
            # the log, control characters out, at most 2000 characters; no comment (or only spaces) adds nothing.
            src, dst = t / "c.txt", t / "c.gz"
            src.write_text("line one\nline two\n")
            comment = "  low FPS in the city\nfreezes when I open the map /var/mobile/Containers/Data/Application/AAAA\tx  "
            r = subprocess.run([str(exe), str(src), str(dst), comment], capture_output=True, text=True)
            got = gzip.decompress(dst.read_bytes()).decode("utf-8") if r.returncode == 0 else ""
            want_first = ["[user-comment] low FPS in the city",
                          fx.scrub("[user-comment] freezes when I open the map /var/mobile/Containers/Data/Application/AAAAx")]
            check("a comment goes first, one [user-comment] line per line, scrubbed, without control characters, then the log",
                  got.split("\n")[:4] == want_first + ["line one", "line two"], got[:400])
            for empty in ("", "   \n  "):
                r = subprocess.run([str(exe), str(src), str(dst), empty], capture_output=True, text=True)
                got = gzip.decompress(dst.read_bytes()).decode("utf-8") if r.returncode == 0 else ""
                check(f"an empty comment ({empty!r}) adds nothing", got == "line one\nline two\n", got[:200])
            r = subprocess.run([str(exe), str(src), str(dst), "x" * 5000], capture_output=True, text=True)
            got = gzip.decompress(dst.read_bytes()).decode("utf-8") if r.returncode == 0 else ""
            check("a comment is cut at 2000 characters", got.split("\n")[0] == "[user-comment] " + "x" * 2000)
            # Round 604: the performance trace goes after the log's last line (a trailer), scrubbed like the rest; no trailer adds nothing,
            # and a log that did not end with a newline gets its line ended first.
            trailer = "[trace] begin v1 rows=2\\n[trace] 0,,,,,,812,5100,0,25,0,0,\\n[trace] 1,58.1,17.2,33,41.0,22.3,1024,4890,0,40,0,0,71\\n[trace] end"
            trailer = trailer.replace("\\n", "\n")
            for name, text in (("ended", "line one\nline two\n"), ("unended", "line one\nline two")):
                src, dst = t / "t.txt", t / "t.gz"
                src.write_text(text)
                r = subprocess.run([str(exe), str(src), str(dst), "", trailer], capture_output=True, text=True)
                got = gzip.decompress(dst.read_bytes()).decode("utf-8") if r.returncode == 0 else ""
                check(f"a trailer follows the last line of a log that is {name}, one line each, with a final newline",
                      got == "line one\nline two\n" + trailer + "\n", got[:300])
            r = subprocess.run([str(exe), str(src), str(dst), "a comment", trailer], capture_output=True, text=True)
            got = gzip.decompress(dst.read_bytes()).decode("utf-8") if r.returncode == 0 else ""
            check("with a comment too: the comment first, the trace last (no line number of the log moves because of the trace)",
                  got.split("\n")[0] == "[user-comment] a comment" and got.split("\n")[1:3] == ["line one", "line two"]
                  and got.endswith("[trace] end\n"), got[:300])
            r = subprocess.run([str(exe), str(src), str(dst), "", ""], capture_output=True, text=True)
            got = gzip.decompress(dst.read_bytes()).decode("utf-8") if r.returncode == 0 else ""
            check("an empty trailer adds nothing (the log's own end is kept as it was)", got == "line one\nline two", got[:200])
            check("a line that needs no scrub passes byte for byte, and the prefilter misses nothing a rule changes",
                  all(fx.scrub(l) == l for l in corpus if "Handled self" in l or l.startswith("[xp]") or l.startswith("self-")))

print("PASS: log upload" if ok else "FAILED: log upload")
sys.exit(0 if ok else 1)

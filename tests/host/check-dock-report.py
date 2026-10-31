#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright 2026 125hz
# Madeira Converter Exception: see LICENSE-EXCEPTION.md
"""Compile Madeira Dock's production report parser; synthetic reports only.

The host (madeira-dock) writes numeric stages to C:\\madeira-dock.txt.
Only whitelisted numeric fields and a 64-hex client fingerprint may be read;
anything else, partial final lines and oversized files are ignored.
"""
from pathlib import Path
import os, shutil, subprocess, tempfile
root = Path(__file__).resolve().parents[2]
SWIFTC = os.environ.get('SWIFTC') or shutil.which('swiftc') or str(Path.home() / '.local/share/swiftly/bin/swiftc')
text = (root / 'app/Madeira/MadeiraDock.swift').read_text()
parser = text[text.index('    struct Report {'):text.index('    @MainActor private static var lastReport =')]
host = (root / 'madeira-dock/src/main.c')
checks = r'''
func parse(_ text: String) -> MadeiraDock.Report { MadeiraDock.parseReport(Data(text.utf8)) }
let fingerprint = String(repeating: "a", count: 64)
let report = parse("[steam-host] ml1820 client-sha256=\(fingerprint)\r\n[steam-host] ml1830 session-unsupported-client=1\r\n[steam-host] ml1830 probe-result=30\r\n")
assert(report.result == 30 && report.failure!.contains("client build"))
assert(report.fields["client-sha256"] == fingerprint)
let missingOption = parse("[steam-host] ml1830 launch-option-index=7\n[steam-host] ml1830 launch-option-missing=7\n[steam-host] ml2011 launch-config-wait=22\n[steam-host] ml1830 probe-result=45\n")
assert(missingOption.fields["launch-option-index"] == "7" && missingOption.failure!.contains("launch option"))
assert(parse("[steam-host] ml1830 launch-option-invalid=1\n[steam-host] ml1830 probe-result=45\n").failure!.contains("launch option"))
assert(parse("[steam-host] ml1830 probe-result=30\n").failure!.contains("set up the Steam session"))
assert(parse("[steam-host] ml1830 probe-result=35\n").failure!.contains("license"))
assert(parse("[steam-host] ml1830 probe-result=0\n").failure == nil)
assert(parse("[steam-host] ml1830 probe-result=30").result == nil)
assert(parse("[steam-host] ml1830 probe-result=30\n[steam-host] ml1830 probe-result=0\n").result == 0)
assert(parse("[steam-host] ml1860 session-client-adapter=202601\n").fields["session-client-adapter"] == "202601")
let transport = parse("[steam-host] ml1870 session-handoff-stage=1\r\n[steam-host] ml1870 session-handoff-error=3\r\n[steam-host] ml1830 probe-result=37\r\n")
assert(transport.fields["session-handoff-stage"] == "1" && transport.fields["session-handoff-error"] == "3")
assert(transport.failure!.contains("sign-in transfer") && transport.failure!.contains("hasn't checked"))
assert(parse("[steam-host] ml1830 session-native-handoff-app-mismatch=1\n[steam-host] ml1830 probe-result=37\n").failure!.contains("another launch"))
assert(parse("[steam-host] ml1990 ceg-result=10\n[steam-host] ml1830 probe-result=49\n").failure!.contains("busy"))
// Round 623 (Just Cause 2, feedback BEQD9Y): CEG result 55 keeps the old words and says it is a known stop; another code still gets the plain text.
let ceg55 = parse("[steam-host] ml1990 ceg-result=55\n[steam-host] ml1830 probe-result=49\n").failure!
assert(ceg55.contains("copy protection") && ceg55.contains("(code 55)") && ceg55.contains("known stop") && ceg55.contains("no BCD setting is known"))
let cegOther = parse("[steam-host] ml1990 ceg-result=7\n[steam-host] ml1830 probe-result=49\n").failure!
assert(cegOther.contains("(code 7)") && !cegOther.contains("known stop"))
assert(parse("[steam-host] ml1830 probe-result=49\n").failure!.contains("(code ?)"))
assert(parse("[steam-host] ml1970 launch-client-error=18\n[steam-host] ml1830 probe-result=45\n").failure!.contains("installed"))
assert(parse("[steam-host] ml2011 launch-config-wait=1\n[steam-host] ml1970 launch-client-error=22\n[steam-host] ml1830 probe-result=48\n").failure!.contains("configuration"))
let session = parse("[steam-host] ml1970 launch-client-error=35\n[steam-host] ml2015 launch-session-wait=35\n[steam-host] ml2015 launch-session-gave-up=12\n[steam-host] ml1830 probe-result=45\n")
assert(session.fields["launch-session-wait"] == "35" && session.fields["launch-session-gave-up"] == "12")
assert(session.failure!.contains("still reports") && session.failure!.contains("another session"))
assert(parse("[steam-host] ml1970 launch-client-error=35\n[steam-host] ml2015 launch-session-wait=35\n[steam-host] ml1830 probe-result=48\n").failure!.contains("still reports"))
let sessionNoWait = parse("[steam-host] ml1970 launch-client-error=35\n[steam-host] ml1830 probe-result=45\n").failure!
assert(sessionNoWait.contains("another session") && !sessionNoWait.contains("still reports"))
assert(parse("[steam-host] ml2015 launch-session-wait=35\n[steam-host] ml1970 launch-client-error=22\n[steam-host] ml1830 probe-result=45\n").failure!.contains("configuration"))
assert(parse("[steam-host] ml1970 launch-client-error=99\n[steam-host] ml1830 probe-result=45\n").failure!.contains("code 45"))
// Round 624 (agent N S13; QH4XFC, V71SS6, 9Z31S2): client error 46 (the 32-bit window refused) names the install's missing memory limit, and only then
assert(MadeiraDock.Report.launchRefusal(46, waited: false) == nil)
assert(MadeiraDock.Report.launchRefusal(46, waited: false, installLacksMemory: true)!.contains("increased memory limit"))
assert(MadeiraDock.Report.launchRefusal(5, waited: false, installLacksMemory: true)!.contains("license"))
assert(parse("[steam-host] ml1970 launch-client-error=46\n[steam-host] ml1830 probe-result=45\n").failure!.contains("code 45"))   // the stub install has the limit
let callbacks = parse((1...20).map { "[steam-host] ml1830 session-callback-id=\(100 + $0)\n" }.joined() + "[steam-host] ml1830 session-callback-id=x\n")
assert(callbacks.fields["session-callback-ids"] == (1...16).map { String(100 + $0) }.joined(separator: ","))
assert(callbacks.fields["session-callback-id"] == nil)
let entitled = parse("[steam-host] ml1830 session-requested-app-entitled=1\n[steam-host] ml1830 session-subscription-count=12\n")
assert(entitled.fields["session-requested-app-entitled"] == "1" && entitled.fields["session-subscription-count"] == "12")
// The host's own account of the launch reaches the log (a Rockstar launcher that finds no Steam, 2026-10-20).
let hostLaunch = parse("[steam-host] ml1830 launch-install-directory-verified=1\n[steam-host] ml1830 launch-client-discovery-published=1\n[steam-host] ml1830 launch-request-submitted=1\n[steam-host] ml1830 launch-game-running=1\n[steam-host] ml1830 launch-game-ended=1\n[steam-host] ml1830 launch-discovery-restored=1\n[steam-host] ml1830 launch-host-result=0\n")
for key in ["launch-install-directory-verified", "launch-client-discovery-published", "launch-request-submitted", "launch-game-running", "launch-game-ended", "launch-discovery-restored"] {
    assert(hostLaunch.fields[key] == "1")
}
assert(hostLaunch.fields["launch-host-result"] == "0" && hostLaunch.failure == nil)
assert(parse("[steam-host] ml1830 session-authenticated-online=1\n[steam-host] ml1830 probe-result=34\n").failure!.contains("license check for this game timed out"))
assert(parse("[steam-host] ml1830 probe-result=34\n").failure!.contains("sign-in didn't complete"))
// Round 620 (Half-Life 2, 6XBJKH): an expired saved sign-in (EResult 15 in callback 102/103) says so, not "check your connection".
assert(parse("[steam-host] ml1830 session-connection-result=15\n[steam-host] ml1830 probe-result=34\n").failure!.contains("no longer accepts your sign-in"))
assert(parse("[steam-host] ml1830 session-connection-result=3\n[steam-host] ml1830 probe-result=34\n").failure!.contains("sign-in didn't complete"))
assert(parse("[steam-host] ml1830 session-authenticated-online=1\n[steam-host] ml1830 session-connection-result=15\n[steam-host] ml1830 probe-result=34\n").failure!.contains("license check"))
// Offline start (ml2016): Valve's client decides; each refusal has its own words.
let offline = parse("[steam-host] ml2016 session-offline-abi=1\n[steam-host] ml2016 session-offline-requested=1\n[steam-host] ml2016 session-offline-can=2\n[steam-host] ml1830 probe-result=50\n")
assert(offline.fields["session-offline-can"] == "2" && offline.failure!.contains("no saved data yet") && offline.failure!.contains("once while online"))
assert(parse("[steam-host] ml2016 session-offline-abi=0\n[steam-host] ml1830 probe-result=50\n").failure!.contains("client build"))
assert(parse("[steam-host] ml2016 session-offline-logon-result=2\n[steam-host] ml1830 probe-result=51\n").failure!.contains("turned down the offline sign-in"))
assert(parse("[steam-host] ml2016 session-offline-logon-state=3\n[steam-host] ml1830 probe-result=52\n").failure!.contains("saved data doesn't include this game"))
assert(parse("[steam-host] ml2016 session-offline-ready=1\n").fields["session-offline-ready"] == "1")
let rejected = "[steam-host] ml1830 account=synthetic\n[steam-host] ml1830 token=synthetic\n[steam-host] ml1830 probe-result=2147483648\n[steam-host] ml1830 probe-result=secret\n[steam-host] ml1830 client-sha256=invalid\n[steam-host] unknown probe-result=0\n[steam-host] ml1830 probe-result=0 secret\n"
assert(parse(rejected).fields.isEmpty)
assert(parse(String(repeating: "x", count: 32769)).fields.isEmpty)
assert(MadeiraDock.parseReport(Data([0xff])).fields.isEmpty)
print("PASS: Dock report completion, CRLF, fingerprints, adapter version, callback IDs, failure reasons (including the session wait and the license wait) and private/malformed field rejection")
'''
with tempfile.TemporaryDirectory(prefix='madeira-dock-report-') as directory:
    source = Path(directory) / 'main.swift'; binary = Path(directory) / 'check'
    source.write_text('import Foundation\nenum MemoryRights { static let lacking = false }   // round 624: the install has the limit here\n'
                      'enum MadeiraDock {\n' + parser + '\n}\n' + checks)
    subprocess.run([SWIFTC, str(source), '-o', str(binary)], check=True)
    subprocess.run([str(binary)], check=True)

# Round 620: the Dock's expired-sign-in results are SteamError.signInExpiredCodes, as text.
import re as _re
_err = (root / "app/Madeira/SwiftSteam/Core/SteamError.swift").read_text()
_codes = set(_re.search(r"signInExpiredCodes: Set<UInt32> = \[([0-9, ]+)\]", _err).group(1).replace(" ", "").split(","))
_dock = set(_re.search(r"expiredSignInResults: Set<String> = \[([^\]]*)\]", text).group(1).replace('"', "").replace(" ", "").split(","))
assert _codes == _dock, (_codes, _dock)

# The rounds the parser accepts are the ones the pinned host writes.
if host.exists():
    import re
    written = set(re.findall(r'"(ml\d{4})"', host.read_text())) | set(re.findall(r'\[steam-host\] (ml\d{4})', host.read_text()))
    for path in (root / 'madeira-dock/src').glob('*.c'):
        written |= set(re.findall(r'"(ml\d{4})"', path.read_text()))
    accepted = set(re.findall(r'"(ml\d{4})"', text[text.index('static let reportRounds'):text.index('static func parseReport')]))
    # ml2014 tags only install-scm, emitted by the host's `--start-services` CLI mode before any
    # report file is opened (stderr only). Madeira runs that mode only as its own process in the
    # one-time-install batch (DockInstallers.swift), before the host run, so it never reaches the report.
    main_c = host.read_text()
    assert re.search(r'!strncmp\(stage, "install-scm", 11\) \? "ml2014"', main_c) and main_c.count('"ml2014"') == 1
    assert main_c.index('"--start-services"') < main_c.index('report = _wfopen(')
    uses = sorted(p.name for p in (root / 'app/Madeira').glob('*.swift') if '--start-services' in p.read_text())
    assert uses in ([], ['DockInstallers.swift']), uses
    written -= {'ml2014'}
    assert written <= accepted, f'host rounds not accepted: {sorted(written - accepted)}'
    print(f'PASS: every report round the pinned host writes is accepted ({len(written)})')
else:
    print('SKIP: madeira-dock not checked out; round cross-check not run')

# The live Dock status names the session wait only while the host reports it for refusal 35.
view = (root / 'app/Madeira/MadeiraDockModel.swift').read_text()
assert 'report.fields["launch-session-wait"] != nil, report.fields["launch-client-error"] == "35"' in view
assert 'Waiting up to 3 minutes for Steam to end it' in view
print('PASS: live Dock status shows the session wait')

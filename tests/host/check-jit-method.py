#!/usr/bin/env python3
"""Static integration checks for StikDebug and the iOS 26 built-in JIT helper."""

from pathlib import Path
import plistlib
import re

root = Path(__file__).resolve().parents[2]
app = root / "app/Madeira"
project = (root / "app/Madeira.xcodeproj/project.pbxproj").read_text()
stik = (app / "StikJITHelper.swift").read_text()
setup = (app / "JITSetup.swift").read_text()
host = (app / "JITBuiltInHost.swift").read_text()
messages = (app / "JITBuiltInMessages.swift").read_text()
helper = (root / "app/MadeiraJITHelper/MadeiraJITHelper.swift").read_text()
content = (app / "ContentView.swift").read_text()
library = (app / "Library.swift").read_text()


def require(condition, label):
    if not condition:
        raise AssertionError(label)
    print(f"PASS: {label}")


def function(source, signature):
    start = source.index(signature)
    brace = source.index("{", start)
    depth = 1
    end = brace + 1
    while depth:
        depth += (source[end] == "{") - (source[end] == "}")
        end += 1
    return source[start:end]


# StikDebug must target this process, carry the one bundled script, and wait
# for a live debugger rather than accepting the sticky CS_DEBUGGED bit alone.
enable = function(stik, "static func enableJIT(")
require('components.scheme = "stikdebug"' in enable
        and 'components.host = "enable-jit"' in enable,
        "StikDebug uses the canonical enable-jit URL")
require('URLQueryItem(name: "bundle-id", value: bundleID)' in enable
        and 'URLQueryItem(name: "pid", value: String(getpid()))' in enable
        and 'URLQueryItem(name: "script-data", value: scriptData.base64EncodedString())' in enable,
        "StikDebug request targets the current PID and carries Madeira's script")
require('Bundle.main.url(forResource: "madeira-jit", withExtension: "js")' in stik
        and "private static let scriptBase64" not in stik,
        "the bundled JavaScript file is the single script source")
wait = function(stik, "static func waitForDebugger(")
# madeira-bcd ios18-jit: each poll goes through attachPoll(ready:...), which also ends the
# wait when a debugger attached and left (tests/host/check-jit-no-debugger.py).
require("timeout: TimeInterval = 90" in wait and ("if ready {" in wait or "attachPoll(ready: ready," in wait),
        "StikDebug attach has a finite 90-second readiness timeout")
ready = function(stik, "static var ready: Bool")
# ml1235 (local): `ready` reads CS_DEBUGGED through SigningStatus.current.debugged,
# the same csops query without jit_check_debugged's log line (the library polls
# `ready` every 2 s and waitForDebugger every 0.5 s).
# madeira-bcd ios18-jit (2026-10-18): the live debugger is P_TRACED (isDebuggerTraced), as
# upstream meant it; isDebuggerAttached() is the badge's CS_DEBUGGED-first reading.
require(("jit_check_debugged()" in ready or "SigningStatus.current.debugged" in ready)
        and "isDebuggerTraced()" in ready and "isDebuggerAttached()" not in ready,
        "readiness requires CS_DEBUGGED and a live debugger")

# Automatic selection is deterministic: installed StikDebug first, otherwise
# the built-in helper. A failure never silently falls through to another method.
resolved = function(setup, "var resolvedMethod: JITMethod")
require("StikJITHelper.isAvailable ? .stikDebug : .builtIn" in resolved,
        "Automatic prefers installed StikDebug, then Built-in StikJIT")
coordinator_enable = function(setup, "func enable(completion:")
coordinator_route = function(setup, "private func enableResolved(")
require("ensureLoopback" in coordinator_enable and "self?.enableResolved" in coordinator_enable
        and "switch resolvedMethod" in coordinator_route
        and "enableBuiltIn(completion: completion)" in coordinator_route
        and setup.count("StikJITHelper.enableJIT") == 1,
        "the coordinator routes each selected JIT method once, after the loopback check")
# ContentView's on-demand JIT setup is BCD's (BCDJITSetupSheet, 2026-10-18).
# Wave 3 (2026-10-19): the classic JITSettingsSection and JITSetupView are deleted; Settings › JIT is BCD's section.
require("BCDJITSetupSheet(enableJIT: { enableJIT() })" in content
        and ".jit: { AnyView(BCDJITLiveSection(enableJIT:" in (app / "BCDSettingsLive.swift").read_text()
        and "JITSettingsSection" not in library and "JITSetupView" not in setup,
        "JIT setup is reachable from the main flow and Settings")
require('dictionary["public_key"]' in setup
        and 'dictionary["private_key"]' in setup
        and 'dictionary["identifier"]' in setup,
        "pairing import rejects ordinary lockdown plists before invoking StikJIT")

# The debugger must run in a separate extension process. The request includes
# the target app PID and helper always forces Madeira's custom script.
# The helper is a classic app extension started by the bundle ID it has in this
# installation (LiveContainer's way), so a sideloader that renames Madeira's bundle ID
# (and the helper's with it) does not lose it; an ExtensionKit extension point did.
require("extensionWithIdentifier:error:" in host and "beginExtensionRequestWithInputItems:completion:" in host
        and "setRequestCompletionBlock:" in host and "setRequestInterruptionBlock:" in host
        and 'Bundle.main.builtInPlugInsURL?.appendingPathComponent(helperFile)' in host
        and "Bundle(url: url)?.bundleIdentifier" in host,
        "the app starts the helper extension by the helper's own bundle ID in this installation")
require("ExtensionFoundation" not in helper.replace("ExtensionFoundation.framework", "")
        and "AppExtensionPoint" not in host and "EX_ENABLE_EXTENSION_POINT_GENERATION" not in project
        and "extensionkit" not in project,
        "no ExtensionKit extension point is declared or looked up (iOS registers none for a renamed install)")
require("let targetPID: Int32?" in messages and "let pairingData: Data?" in messages
        and "let scriptBase64: String?" in messages
        and "item.userInfo = [MadeiraJITRequest.itemKey: data]" in host
        and "context.completeRequest(returningItems: [item], completionHandler: nil)" in helper
        and "@objc(MadeiraJITHelperHandler)" in helper,
        "the request (PID, pairing data, script) goes in the extension request, the answer in the item it completes with")
helper_enable = helper[helper.index("try StikJIT.enableJIT("):]
require("targetPID: targetPID" in helper_enable
        and "script: .customBase64(scriptBase64)" in helper_enable
        and "forceScript: true" in helper_enable,
        "the helper attaches to Madeira and forces its custom script")
require('getenv("LC_HOME_PATH")' in host and 'LiveContainerHelper.state != .ready' in host,
        "built-in JIT under LiveContainer only through its proven Helper Process Support")

# Packaging: both targets share Codable messages; only the helper links the
# device-only StikJIT framework; the script and helper are embedded in the app.
for marker in [
    "MadeiraJITHelper.appex in Embed JIT Helper",
    "madeira-jit.js in Resources",
    "JITBuiltInMessages.swift in Helper Sources",
    "Frameworks/StikJIT.xcframework/ios-arm64",
]:
    require(marker in project, f"Xcode project contains {marker}")
require(project.count('"OTHER_LDFLAGS[sdk=iphoneos*]"') == 2
        and re.search(r"(?m)^\s*OTHER_LDFLAGS\s*=", project) is None
        and "#if targetEnvironment(simulator)" in helper,
        "StikJIT links on device only, with a simulator-safe helper stub")

with (app / "Info.plist").open("rb") as f:
    app_plist = plistlib.load(f)
require("stikdebug" in app_plist["LSApplicationQueriesSchemes"],
        "the app may detect the canonical StikDebug URL scheme")
with (root / "app/MadeiraJITHelper/Info.plist").open("rb") as f:
    helper_plist = plistlib.load(f)
extension_info = helper_plist.get("NSExtension", {})
require(helper_plist["CFBundlePackageType"] == "XPC!" and "EXAppExtensionAttributes" not in helper_plist
        and extension_info.get("NSExtensionPointIdentifier") == "com.apple.ar.viewer"
        and extension_info.get("NSExtensionPrincipalClass") == "MadeiraJITHelperHandler"
        and helper_plist["XPCService"]["_ProcessType"] == "App"
        and 'productType = "com.apple.product-type.app-extension";' in project
        and "dstSubfolderSpec = 13;" in project[project.index("/* Embed JIT Helper */ = {"):],
        "the helper is a classic app extension in PlugIns, as LiveContainer's LiveProcess")
project = (root / "app/Madeira.xcodeproj/project.pbxproj").read_text()
helper_source = (root / "app/MadeiraJITHelper/MadeiraJITHelper.swift").read_text()
require(project.count('PRODUCT_BUNDLE_IDENTIFIER = "$(MADEIRA_BUNDLE_IDENTIFIER)";') == 2
        and project.count('PRODUCT_BUNDLE_IDENTIFIER = "$(MADEIRA_BUNDLE_IDENTIFIER).JITHelper";') == 2
        and project.count("MADEIRA_BUNDLE_IDENTIFIER = com.thebcd69.bcd;") == 2   # the app is BCD (owner, 2026-10-18)
        and "AppExtensionPoint" not in helper_source,
        "one setting, MADEIRA_BUNDLE_IDENTIFIER, names the app and the helper")

problem = setup[setup.index("enum ConnectionProblem"):setup.index("var message: String")]
require(problem.index('"connectionreset"') < problem.index("self = .pairing") < problem.index('"connectionrefused"')
        < problem.index('"timedout"') < problem.index("self = .vpn"),
        "a reset connection reads as a rejected pairing; refused, timed out or unreachable as LocalDevVPN")
require(setup.count("helperFailure(response.message)") == 2,
        "Check setup and Enable JIT both explain connection problems")
# Wave 3: the library's error alert is BCD's interface's (BCDLiveFrontEnd), over the whole shell, Settings included.
front = (app / "BCDLiveFrontEnd.swift").read_text()
require("if let jitProblem { jitConnectionActions(jitProblem, retry: enableJIT) { library.error = nil } }" in front,
        "the library's JIT error offers Pair Again and LocalDevVPN (or the shortcut)")
require('.alert(jitProblem == nil ? "Library" : "Enabling JIT Failed"' in front
        and "presenting(watching(shell))" in front,
        "the library's error alert is on the whole shell, shown over Settings as well")
require('URL(string: "localdevvpn://enable?scheme=bcd")' in setup
        and "localdevvpn" in app_plist["LSApplicationQueriesSchemes"]
        and any("bcd" in t.get("CFBundleURLSchemes", []) for t in app_plist.get("CFBundleURLTypes", [])),
        "LocalDevVPN opens to connect and returns to Madeira's own URL scheme")

# Play without JIT runs Enable JIT, then starts the game, once: only with the debugger
# attached (so the start cannot ask again), and never after a failure.
gate = function(content, "private func jitReadyForLaunch(")
require(gate.index("if StikJITHelper.ready { return true }") < gate.index("if let launch {")
        < gate.index("if StikJITHelper.flaggedWithoutDebugger {")
        and "launchAfterJIT = launch" in gate and "library.startingJIT = entry" in gate
        and "if jitStatus != .testing { enableJIT() }" in gate,
        "Play without JIT enables it (a second Play while it runs only replaces the game)")
ended = function(content, "private func launchAfterJITEnded(")
require("launchAfterJIT = nil" in ended
        and "guard started, StikJITHelper.ready, library.current == nil, wine_process_is_running() == 0 else {" in ended
        and ended.index("launchAfterJIT = nil") < ended.index("library.startingJIT = nil") < ended.index("launch()"),
        "the waiting game starts once, only when JIT came on and nothing else started")
# Wave 3: the classic Play button ("Enabling JIT" with a spinner) is deleted; BCD's Play says "Turning on JIT…"
# (checked in check-bcd-launch.py) and BCD's open page closes for JIT setup.
require(".onChange(of: jit.showSetup) { _, show in if show { live.sheet = nil } }" in front
        and "library.$startingJIT" in (app / "BCDLiveStore.swift").read_text(),
        "BCD's Play says JIT is turning on meanwhile, and an open page closes for JIT setup")
enable_jit = function(content, "private func enableJIT()")
require(enable_jit.count("launchAfterJITEnded(started: false)") == 2 and enable_jit.count("launchAfterJITEnded(started: true)") == 1,
        "every way Enable JIT ends settles the waiting game")
play = function(content, "private func launchLibraryEntry(")
require("guard jitReadyForLaunch(entry: entry.id, then: { startLibraryEntry(entry) }) else { return }" in play
        and play.index("cloudClear(") < play.index("jitReadyForLaunch(")
        and "then: { startDock(game, compactPool: compactPool, profile: profile) }) else { return }" in content,
        "Play and Dock starts continue after JIT without repeating the checks already passed")

# LiveContainer's Helper Process Support (follow-up of the night round 2026-11-11, owner: "nothing may change for those who do not
# use that LiveContainer or do not turn the switch on; verify it"). Outside LiveContainer nothing new runs; inside it, without the
# switch on, nothing new is called (only LiveContainer's own LCAppInfo.plist is read) and every outcome is the old one.
look = function(host, "private static func look() -> BCDLCHelper")
body = look[look.index("{") + 1:].lstrip()
require(body.startswith('guard getenv("LC_HOME_PATH") != nil else { return .notAvailable }'),
        "LiveContainer helper: outside LiveContainer the look returns at once (no call, no log line)")
require("if !multitask, switchOn == true, let url = helperURL" in look and look.count("factory.extension(withIdentifier:") == 1
        and look.index("if !multitask, switchOn == true") < look.index("factory.extension(withIdentifier:"),
        "LiveContainer helper: the extension probe runs only with Helper Process Support switched on")
require('else if probe == "LCGuestExtension" && prepared { state = .ready }' in look and look.count("state = .ready") == 1,
        "LiveContainer helper: ready only with LiveContainer's own extension object and its install-time preparation")
reason_body = function(host, "static var unavailableReason: String?")
require('if getenv("LC_HOME_PATH") != nil, LiveContainerHelper.state != .ready {' in reason_body,
        "built-in JIT: the LiveContainer gate opens only for a proven ready helper")
pool = (app / "MemoryPool.m").read_text()
ident = function(pool, "NSString *MadeiraMemoryHostIdentifier(void)")
require('NSString *fallback = [NSBundle.mainBundle.bundleIdentifier stringByAppendingString:@".MemoryHost"];\n    if (!getenv("LC_HOME_PATH")) return fallback;' in ident
        and "NSString *identifier = MadeiraMemoryHostIdentifier();" in pool,
        "helper memory: outside LiveContainer the extension's id is the one it always was")
bridge = (app / "WineProcessBridge.m").read_text()
require('if (poolMB >= 512 && getenv("LC_HOME_PATH") && !madeira_cfg_bool("env.MADEIRA_LC_HELPER_MEMORY", 0)) {' in bridge
        and '} else if (poolMB >= 512) {\n                            if (getenv("LC_HOME_PATH"))' in bridge,
        "helper memory: inside LiveContainer only with env.MADEIRA_LC_HELPER_MEMORY = 1; outside it the old path, untouched")
catalog = (app / "ConfigCatalog.generated.swift").read_text()
require('ConfigOption(key: "env.MADEIRA_LC_HELPER_MEMORY", title: "Helper memory inside LiveContainer", kind: .bool, defaultValue: "0"' in catalog,
        "helper memory inside LiveContainer: the key is in the catalog, off by default")
# Owner, 2026-11-11: "Built-in's behaviour will never change either." The built-in helper's own path (its request, the helper
# extension, the pairing, the network, the script, the JIT coordinator) knows nothing of LiveContainer's helper state; only the
# LiveContainer gate and one LiveContainer-only log word read it.
for name in ("JITSetup.swift", "StikJITHelper.swift", "JITPairing.swift", "JITNetwork.swift"):
    require("LiveContainerHelper" not in (app / name).read_text() and "BCDLCHelper" not in (app / name).read_text(),
            "built-in JIT unchanged: %s does not read LiveContainer's helper state" % name)
helper_src = "".join(f.read_text() for f in (root / "app/MadeiraJITHelper").rglob("*.swift"))
require("LiveContainerHelper" not in helper_src and "LC_HOME_PATH" not in helper_src,
        "built-in JIT unchanged: the helper extension itself is untouched")
send_body = function(host, "static func send(_ request: MadeiraJITRequest,")
require("LiveContainerHelper" not in send_body and "lcHelper" not in send_body,
        "built-in JIT unchanged: the request is sent exactly as before")
require(host.count("LiveContainerHelper.state") == 2 and 'let host = getenv("LC_HOME_PATH") != nil ? " host-pid=\\(getpid())" : ""' in host,
        "built-in JIT unchanged outside LiveContainer: the helper state is read only by the LiveContainer gate, and the log line gains a word only there")
require("LiveContainerHelper" not in (app / "BCDSetupView.swift").read_text(),
        "first-run setup is unchanged (its Built-in choices inside LiveContainer behave as before)")

# Build 655 (owner, 2026-11-11; a tester's idea): Home's "JIT off" chip runs Enable JIT, the same action as Settings › JIT's button
# (BCDSettingsLive: BCDLiveStore.shared.enableJIT(); the Home store's actions are BCDLiveStore). "JIT on" and "JIT…" stay labels.
front = (app / "BCDFrontEnd.swift").read_text()
require(front.count("if system.jit == .off {\n                    Button { store.actions?.enableJIT() } label: { chip }") == 1
        and front.count("if store.system.jit == .off {\n            Button { store.actions?.enableJIT() } label: { label }") == 1
        and front.count("store.actions?.enableJIT()") == 2,
        "Home: the JIT off chip (classic and native interface) runs Enable JIT; nothing else on Home starts it")
require("func enableJIT() { settled { [weak self] in self?.requestJIT?() } }" in (app / "BCDLiveStore.swift").read_text()
        and "BCDJITLiveSection(enableJIT: { BCDLiveStore.shared.enableJIT() }" in (app / "BCDSettingsLive.swift").read_text(),
        "Home's chip and Settings › JIT's Enable JIT are one action")

print("check-jit-method: PASS")

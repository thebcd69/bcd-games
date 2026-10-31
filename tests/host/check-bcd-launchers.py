#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""PC launchers in BCD's Library (Battle.net, EA app) and Wine Gecko for their installers
(app/Madeira/BCDLaunchers.swift, BCDLauncherView.swift, BCDLauncherDownload.swift, BCDLaunchersLive.swift,
BCDGecko.swift, BCDGeckoSection.swift; docs/BCD-UI-ARCHITECTURE.md).

Source checks:
  - BCD's licence header on every new file; the screens and models name no engine type; the live engine is in
    tools/ui-preview.py's ENGINE_BOUND; the Xcode project builds all six app files; the preview has a sample;
  - a launcher starts as the session's first program, never through explorer.exe or a Wine desktop: the live
    engine sets no launch mode, no services batch and no explorer arguments; each launcher's session lines are
    in its table entry (BCDLauncherSpec.commonSessionConfig: the GDI table and msiexec in system32);
  - the installer's run is not added to the library (ContentView hands LibraryModel.begin `remember: false`),
    the Library has one chip per launcher and Settings has the HTML engine section;
  - ContentView exports Wine Gecko's cache folder through BCDGeckoFiles only;
  - CI runs this check.
Behaviour (swiftc on the host; --require-swift makes its absence a failure), BCDLaunchers.swift,
BCDLauncherDownload.swift and BCDGecko.swift compiled with a driver:
  - the table: each launcher's installer address is https on its own download host, ids and entry ids are
    unique, and the address check refuses http, another host, a look-alike host, a port and a user name;
  - "installed" detection in a temporary drive_c: found, found with another spelling of the folders (as spelled
    on disk), a folder of that name is no program, a link out of drive_c is none, the first choice wins;
  - the session start: the installer's and the launcher's C:\\ paths, no arguments but Battle.net's Chromium switches, no Wine desktop, no services
    batch, the launcher's config block (merged, rewritten, a player's own key kept);
  - the installer's header is looked at (x86 and x64 programs, signed or not; an error page and a truncated file
    are not programs);
  - the download, served from a URLProtocol: the redirect to the same host is followed; another host, a
    look-alike, http and a port are refused with no file left; 404 and 503 say so; an error page, a tiny answer and
    an oversized one are refused; the log names hosts, never an address with a path;
  - the model: Install downloads, then starts the installer; an installer that needs Wine Gecko downloads it
    between the two; Open starts the installed program; a blocked session, a failure and Cancel say so in the card;
    a second tap while busy does nothing;
  - Wine Gecko: version and file names equal dlls/appwiz.cpl/addons.c's GECKO_VERSION, the two SHA-256 values
    equal its GECKO_SHA per architecture (when that source is there: wine/ in this checkout, BCD_WINE_SRC, or
    ~/madeira-bcd/wine), the files sit at <cache>/wine/<file> as addons.c builds the path, XDG_CACHE_HOME is
    exported only when both files are there at their pinned size, the download is checked against the pinned SHA-256
    and size (a wrong one leaves no file), a refused redirect leaves none, Remove deletes them.

  check-bcd-launchers.py [--require-swift]
"""
import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

R = Path(__file__).resolve().parents[2]
APP = R / "app/Madeira"
PREVIEW = R / "app/BCDPreview"
HEADER = ["// SPDX-License-Identifier: GPL-3.0-only",
          "// Copyright 2026 thebcd69 (BCD)",
          "// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md."]
ENGINE = ["LibraryModel", "LibraryEntry", "LibraryController", "LibraryPrefs", "MadeiraConfig", "GameProfile", "LogStore", "ContentView",
          "BCDLiveStore", "BCDLiveID", "BCDLaunchersLive", "wine_process_is_running", "wineserver_is_running", "StikJITHelper",
          "WineProcessBridge", "MadeiraDock", "SteamOwnedLibrary"]
UI_FILES = ["BCDLaunchers.swift", "BCDLauncherView.swift", "BCDLauncherDownload.swift", "BCDGecko.swift", "BCDGeckoSection.swift"]
ALL_FILES = UI_FILES + ["BCDLaunchersLive.swift"]

ap = argparse.ArgumentParser()
ap.add_argument("--require-swift", action="store_true")
args = ap.parse_args()
failures = 0


def check(cond, what, detail=""):
    global failures
    print(("PASS: " if cond else "FAIL: ") + what + (("\n      " + str(detail)) if detail and not cond else ""))
    if not cond:
        failures += 1


def code(text):
    """The text without // comments (a "//" after ":" is an address's, not a comment)."""
    return "\n".join("" if line.strip().startswith("//") else re.split(r"(?<!:)//", line, maxsplit=1)[0] for line in text.split("\n"))


def bare(text):
    """The code with its string literals emptied: words in quotes are text, not types."""
    return re.sub(r'"(?:[^"\\]|\\.)*"', '""', code(text))


def read(p):
    return Path(p).read_text()


files = {n: read(APP / n) for n in ALL_FILES}
sample = read(PREVIEW / "BCDLauncherSample.swift")

# ---- files, headers, project ----------------------------------------------------------------------
for n, t in list(files.items()) + [("BCDLauncherSample.swift", sample)]:
    check(t.split("\n")[:3] == HEADER, f"{n} starts with BCD's licence header")
for n in UI_FILES:
    named = sorted({e for e in ENGINE if re.search(r"\b" + e + r"\b", bare(files[n]))})
    check(not named, f"{n} names no engine type", named)
named = sorted({e for e in ENGINE if re.search(r"\b" + e + r"\b", bare(sample))})
check(not named, "the preview sample names no engine type", named)
preview_tool = read(R / "tools/ui-preview.py")
bound = re.search(r"^ENGINE_BOUND \|= \{\"BCDLaunchersLive.swift\"\}", preview_tool, re.M)
check(bound is not None, "BCDLaunchersLive.swift is in tools/ui-preview.py's ENGINE_BOUND")
project = read(R / "app/Madeira.xcodeproj/project.pbxproj")
for n in ALL_FILES:
    # Once in the build-file list and once in the Sources phase.
    check(len(re.findall(r"\b" + re.escape(n) + r" in Sources\b", project)) == 2 and f"path = {n};" in project,
          f"the Xcode project builds {n} once")
check("-BCDSection launcher-battlenet" in read(PREVIEW / "BCDPreviewApp.swift")
      and "BCDLauncherSample.prepare" in read(PREVIEW / "BCDPreviewApp.swift"), "the preview app opens a launcher's chip (-BCDSection launcher-<id>)")

# ---- a launcher starts in a Wine desktop with Windows services first (build 594; build 593 started them on their own) ----
live = bare(files["BCDLaunchersLive.swift"])
check(not re.search(r'"explorer|explorer\.exe"', live), "the live engine writes no explorer arguments itself (LibraryEntry.launchCommand does)")
check(re.search(r"let inWineDesktop = true", code(files["BCDLaunchers.swift"])) and re.search(r"let startServices = true", code(files["BCDLaunchers.swift"])),
      "BCDLauncherStart says: in a Wine desktop, services batch first")
check("if stored == nil || (stored?.id == id && stored?.launchMode == nil && stored?.startServices == nil) {" in files["BCDLaunchersLive.swift"]
      and 'entry.launchMode = start.inWineDesktop ? "desktop" : nil' in files["BCDLaunchersLive.swift"]
      and "entry.startServices = start.startServices ? true : nil" in files["BCDLaunchersLive.swift"],
      "a new entry, the installer's and BCD's own entry with no start options of its own get them; a hand-added program keeps its own")
check("BCDLauncherStart.installer(spec)" in files["BCDLaunchersLive.swift"] and "BCDLauncherStart.program(spec, relative: program)" in files["BCDLaunchersLive.swift"],
      "the live engine builds both starts from BCDLauncherStart")
check("BCDLauncherConfig.merged(entry.config, lines: start.sessionConfig, id: spec.id)" in files["BCDLaunchersLive.swift"],
      "the launcher's session lines go into the entry's config at every start")
common = re.search(r"static let commonSessionConfig = \[(.*?)\]", files["BCDLaunchers.swift"])
check(common and "env.MADEIRA_GDI_SHARED_SECTION = 1" in common.group(1) and "env.MADEIRA_MSIEXEC_SYSTEM32 = 1" in common.group(1),
      "the common session lines are the GDI table and msiexec in system32")
check(files["BCDLaunchers.swift"].count("sessionConfig: Self.commonSessionConfig") == 2, "both launchers take the common session lines")
content_view = (R / 'app/Madeira/ContentView.swift').read_text()
check('BCDLauncherSpec.all.first(where: { $0.entryID.caseInsensitiveCompare(entry.id.uuidString) == .orderedSame })' in content_view
      and 'BCDLauncherConfig.merged(entry.config, lines: spec.programSessionConfig, id: spec.id)' in content_view
      and "a library start took the launcher's current lines" in content_view,
      'a launcher entry started from its library tile takes the launcher\'s current lines (2026-10-24)')
check('let arguments = BCDLauncherArguments.merged(entry.arguments, table: spec.launcherArguments)' in content_view
      and 'entry.arguments = BCDLauncherArguments.merged(entry.arguments, table: start.arguments)' in files["BCDLaunchersLive.swift"]
      and 'entry.arguments = arguments' in content_view,
      "... and the launcher's own arguments when it has any, as Open does (2026-10-24)")
bn = files["BCDLaunchers.swift"]
bn = bn[bn.index("static let battlenet = BCDLauncherSpec("):bn.index("static let eaApp")]
check('launcherArguments: Self.battlenetChromiumSwitches,' in bn   # round 625: demandStartServices follows
      and 'static let battlenetChromiumSwitches = "--in-process-gpu --disable-gpu-process-for-dx12-info-collection --disable-site-isolation-trials '
          '--renderer-process-limit=2"' in files["BCDLaunchers.swift"],
      "Battle.net (only) starts with Chromium's switches that keep its 32-bit processes within the device's windows (owner's run, 598)")
# Round 625 (the owner's item 27, agent N S5; Y0HFHB): the installer starts Battle.net.exe itself, so its session hands the same four
# words to it through env.MADEIRA_CHILD_ARGS, from the same constant, and splits the pool; the installed launcher's start is unchanged.
check('installerOnlyConfig: ["env.MADEIRA_CHILD_ARGS = Battle.net.exe " + Self.battlenetChromiumSwitches, "pool-split = 1"],' in bn
      and files["BCDLaunchers.swift"].count('installerOnlyConfig: ["') == 1
      and 'var installerOnlyConfig: [String] = []' in files["BCDLaunchers.swift"],
      "Battle.net's installer (only) gives its browser the four switches and splits the JIT pool (round 625)")
check(files["BCDLaunchers.swift"].count("--in-process-gpu") == 1 and "NetworkServiceInProcess\"" not in files["BCDLaunchers.swift"],
      "... no other launcher gets them, and the switch that did nothing is left out")
check('sessionConfig: Self.commonSessionConfig + ["env.LC_ALL = en_US.UTF-8", "env.MADEIRA_APC_BUSY_QUEUE = 1",\n'
      '                                                   "env.MADEIRA_OPTIN_DLLS = msi,mspatcha,odbccp32,sxs,rstrtmgr,dxva2",\n'
      '                                                   "env.MADEIRA_WOW_MIN_FREE_GB = 2",\n'
      '                                                   "env.MADEIRA_TLS_CLEAR_OWN_PROCESS = 1",\n'
      '                                                   "env.MADEIRA_SPAWN_BLOCK = vc_redist-",\n'
      '                                                   "env.MADEIRA_EA_CEF_FLAGS = --disable-features=CalculateNativeWinOcclusion --disable-backgrounding-occluded-windows",\n'
      '                                                   "env.MADEIRA_X64_IMAGE_NOCOPY = 1", "pool-split = 1", "env.MADEIRA_X86_FAULT_DUMP = 1"]' in files["BCDLaunchers.swift"]
      and files["BCDLaunchers.swift"].count('"env.LC_ALL = en_US.UTF-8"') == 1
      and files["BCDLaunchers.swift"].count('"env.MADEIRA_APC_BUSY_QUEUE = 1"') == 1
      and files["BCDLaunchers.swift"].count('"env.MADEIRA_X86_FAULT_DUMP = 1"') == 1,
      "the EA app (only) also gets an English locale (its installer died on a non-English locale, C7-2F79, build 593), keeps a finished "
      "answer for a busy thread (its MSI's first custom action lost one: JunoInitializeSession 1603) and (round 624, 7NKYWT) the 32-bit "
      "fault report for its installer's custom-action hosts")
# ajan32/ea-cef (2026-10-24): the EA app's Chromium lines belong to the installed launcher's session only, never to its installer's.
launchers = files["BCDLaunchers.swift"]
check('launcherOnlyConfig: ["env.MADEIRA_SC_PA_POOLS = 2", "env.MADEIRA_EA_CEF = 1"], needsGecko: true, needsArticle: true,' in launchers
      and launchers.count('launcherOnlyConfig: ["') == 1 and launchers.count('"env.MADEIRA_EA_CEF = 1"') == 1
      and launchers.count('"env.MADEIRA_SC_PA_POOLS = 2"') == 1,
      "only the EA app's table entry has launcherOnlyConfig: layout 2 and env.MADEIRA_EA_CEF = 1 (the EA app's Chromium, 2026-10-24)")
check("var programSessionConfig: [String] { sessionConfig + launcherOnlyConfig }" in launchers
      and "sessionConfig: spec.programSessionConfig," in launchers   # round 625: the demand-start list follows
      and "BCDLauncherStart(windowsPath: spec.installerWindowsPath, arguments: spec.installerArguments, sessionConfig: spec.sessionConfig + spec.installerOnlyConfig," in launchers,
      "the program's start takes sessionConfig + launcherOnlyConfig, the installer's start sessionConfig + installerOnlyConfig (round 625)")
content = read(APP / "ContentView.swift")
check("library.begin(entry, remember: !BCDLaunchersLive.isSetup(entry))" in content,
      "an installer's run is not remembered in the library (ContentView.startLibraryEntry)")
check("BCDGeckoFiles.exportEnvironment()" in content and content.count("exportEnvironment") == 1, "ContentView exports Gecko's cache folder through BCDGeckoFiles, once")
store = read(APP / "BCDLiveStore.swift")
check("func startLauncherEntry(_ entry: LibraryEntry, game: String)" in store and "launch(entry)" in store, "BCDLiveStore hands a launcher's entry to the connected Play")
lib = read(APP / "BCDLibraryView.swift")
check("case all, installed, store(BCDStoreFront), launcher(String)" in lib and "BCDLauncherSpec.shown.map { .launcher($0.id) }" in lib
      and "BCDLauncherPanel(spec: launcher)" in lib, "the Library has one chip per shown launcher and shows its card")
# Build 600 (the owner, 2026-10-24): the public release hides Battle.net and the EA app completely, with no setting to bring them back;
# the next local build sets hiddenInThisBuild back to false. Either way the chips read `shown` and the library's tiles `hidesEntry`.
launchers_src = files["BCDLaunchers.swift"]
hidden = re.search(r"static let hiddenInThisBuild = (true|false)", launchers_src)
check(hidden is not None and "static var shown: [BCDLauncherSpec] { hiddenInThisBuild ? [] : all }" in launchers_src
      and "hiddenInThisBuild && all.contains {" in launchers_src,
      "the launchers can be hidden for a build: no chip, no card, no tile, and no setting reads the switch")
check("if BCDLauncherSpec.hidesEntry(entry.id) { continue }" in store, "the library leaves a hidden launcher's own entries out")
check(not any("hiddenInThisBuild" in read(APP / n) for n in ALL_FILES if n != "BCDLaunchers.swift"),
      "nothing outside BCDLaunchers.swift reads the switch (no setting turns it)")
print("note launchers hidden in this build: " + (hidden.group(1) if hidden else "?"))
settings = read(APP / "BCDSettingsCore.swift")
check("dotnet, vcruntime, gecko, logs" in settings and 'case .gecko: return "HTML engine for installers"' in settings, "Settings has the HTML engine for installers section after Visual C++")
check("BCDGeckoLive()" in read(APP / "BCDSettingsLive.swift") and "BCDPreviewGecko()" in read(PREVIEW / "BCDSettingsPreviewData.swift"),
      "Settings' Gecko page has its live backend and its preview sample")
check(re.search(r'"Madeira', "\n".join(re.findall(r'"(?:[^"\\]|\\.)*"', "\n".join(code(t) for t in files.values())))) is None, "no \"Madeira\" in the words a person reads")
check(re.search("licence", "\n".join(re.findall(r'"(?:[^"\\]|\\.)*"', "\n".join(code(t) for t in files.values()))), re.I) is None, "the strings say \"license\", not \"licence\"")
anim = [m for t in files.values() for m in re.findall(r"repeatForever|\.animation\(", code(t))]
check(not anim, "no animation of its own in the launcher or Gecko screens", anim)
ci = read(R / ".github/workflows/build-ipa.yml")
check("python3 tests/host/check-bcd-launchers.py --require-swift" in ci, "CI runs this check")

# ---- the compiled checks ------------------------------------------------------------------------
SWIFTC = os.environ.get("SWIFTC") or shutil.which("swiftc")
if not SWIFTC:
    print("SKIP: no swiftc for the compiled checks")
    if args.require_swift:
        check(False, "swiftc is required (--require-swift)")
    sys.exit(1 if failures else 0)


def wine_source():
    for base in [os.environ.get("BCD_WINE_SRC"), str(R / "wine"), str(Path.home() / "madeira-bcd/wine")]:
        if base and (Path(base) / "dlls/appwiz.cpl/addons.c").exists():
            return Path(base) / "dlls/appwiz.cpl/addons.c"
    return None


addons = wine_source()
pins = {}
if addons:
    text = addons.read_text()
    version = re.search(r'#define GECKO_VERSION "([^"]+)"', text)
    x86 = re.search(r'#ifdef __i386__\s*#define GECKO_ARCH "x86"\s*#define GECKO_SHA "([0-9a-f]{64})"', text)
    x64 = re.search(r'#elif defined\(__x86_64__\)\s*#define GECKO_ARCH "x86_64"\s*#define GECKO_SHA "([0-9a-f]{64})"', text)
    pins = {"version": version and version.group(1), "x86": x86 and x86.group(1), "x86_64": x64 and x64.group(1)}
    check(all(pins.values()), "addons.c's Gecko constants were read", pins)
else:
    print("SKIP: dlls/appwiz.cpl/addons.c is not in this checkout (wine/ not checked out; BCD_WINE_SRC names another copy): Gecko's pins are compared when it is")

DRIVER = r'''
import CryptoKit
import Foundation

final class Mock: URLProtocol {
    nonisolated(unsafe) static var seen: [String] = []
    nonisolated(unsafe) static var routes: [String: (Int, Data, [String: String])] = [:]   // "host/path" -> status, body, headers
    nonisolated(unsafe) static var redirects: [String: String] = [:]                        // "host/path" -> target
    static let lock = NSLock()
    static func configuration() -> URLSessionConfiguration {
        let c = BCDLauncherDownloader.configuration()
        c.protocolClasses = [Mock.self]
        return c
    }
    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }
    override func stopLoading() {}
    override func startLoading() {
        let url = request.url!
        let key = (url.host ?? "") + url.path
        Mock.lock.lock(); Mock.seen.append(BCDLauncherNet.origin(url)); Mock.lock.unlock()
        if let target = Mock.redirects[key] {
            let response = HTTPURLResponse(url: url, statusCode: 302, httpVersion: "HTTP/1.1", headerFields: ["Location": target])!
            client?.urlProtocol(self, wasRedirectedTo: URLRequest(url: URL(string: target)!), redirectResponse: response)
            client?.urlProtocolDidFinishLoading(self)
            return
        }
        let (status, body, headers) = Mock.routes[key] ?? (404, Data("not here".utf8), [:])
        var fields = headers
        if fields["Content-Length"] == nil { fields["Content-Length"] = String(body.count) }
        let response = HTTPURLResponse(url: url, statusCode: status, httpVersion: "HTTP/1.1", headerFields: fields)!
        client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
        client?.urlProtocol(self, didLoad: body)
        client?.urlProtocolDidFinishLoading(self)
    }
}

var failed = false
func check(_ cond: Bool, _ what: String, _ detail: String = "") {
    print((cond ? "ok   " : "FAIL ") + what + (cond || detail.isEmpty ? "" : "  [" + detail + "]"))
    if !cond { failed = true }
}

func fakePE(size: Int, machine: Int = 0x14C, signed: Bool = true, pe32plus: Bool = false) -> Data {
    var d = Data(count: size)
    d[0] = 0x4D; d[1] = 0x5A
    let pe = 0x80
    d[0x3C] = UInt8(pe)
    d.replaceSubrange(pe..<pe + 4, with: [0x50, 0x45, 0, 0])
    d[pe + 4] = UInt8(machine & 0xFF); d[pe + 5] = UInt8(machine >> 8)
    let optional = pe32plus ? 240 : 224
    d[pe + 20] = UInt8(optional & 0xFF); d[pe + 21] = UInt8(optional >> 8)
    d[pe + 24] = pe32plus ? 0x0B : 0x0B; d[pe + 25] = pe32plus ? 0x02 : 0x01
    let directories = pe + 24 + (pe32plus ? 112 : 96)
    d[directories - 4] = 16
    if signed {
        func put(_ at: Int, _ v: Int) { for i in 0..<4 { d[at + i] = UInt8((v >> (8 * i)) & 0xFF) } }
        put(directories + 32, size - 1000)
        put(directories + 36, 600)
    }
    return d
}

func sha(_ d: Data) -> String { SHA256.hash(data: d).map { String(format: "%02x", $0) }.joined() }

func tempFolder(_ name: String) -> URL {
    let url = FileManager.default.temporaryDirectory.appendingPathComponent("bcd-launchers-\(name)-\(UUID().uuidString)")
    try! FileManager.default.createDirectory(at: url, withIntermediateDirectories: true)
    return url
}

func touch(_ url: URL, _ data: Data = Data("x".utf8)) {
    try! FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
    try! data.write(to: url)
}

@MainActor final class FakeEngine: BCDLauncherEngine {
    var calls: [String] = []
    var installed: [String: String] = [:]
    var blocker: String?
    var componentNeeded = false
    var installerError: Error?
    var componentError: Error?
    var hold = false
    func installedProgram(_ spec: BCDLauncherSpec) -> String? { installed[spec.id] }
    func sessionBlocker() -> String? { calls.append("blocker"); return blocker }
    func downloadInstaller(_ spec: BCDLauncherSpec, progress: @escaping @Sendable (Int64, Int64) -> Void) async throws {
        calls.append("installer-download")
        progress(1_000, 4_000)
        if hold { try await Task.sleep(nanoseconds: 5_000_000_000) }
        if let installerError { throw installerError }
    }
    func cancelDownload() { calls.append("cancel") }
    func missingComponent(_ spec: BCDLauncherSpec) -> String? { spec.needsGecko && componentNeeded ? "the HTML engine" : nil }
    func downloadComponent(_ spec: BCDLauncherSpec, progress: @escaping @Sendable (Int64, Int64) -> Void) async throws {
        calls.append("component-download")
        progress(5, 10)
        if let componentError { throw componentError }
    }
    func startInstaller(_ spec: BCDLauncherSpec) { calls.append("start-installer:" + spec.id) }
    func startLauncher(_ spec: BCDLauncherSpec, program: String) { calls.append("start-launcher:" + spec.id + ":" + program) }
}

@MainActor func waitIdle(_ model: BCDLauncherModel) async {
    for _ in 0..<400 { if !model.busy { return }; try? await Task.sleep(nanoseconds: 10_000_000) }
}

@main struct Driver {
    @MainActor static func main() async {
        let specs = BCDLauncherSpec.all
        // ---- the table
        check(specs.map(\.id) == ["battlenet", "eaapp"], "the table lists Battle.net, then the EA app")
        check(Set(specs.map(\.id)).count == specs.count && Set(specs.map(\.entryID) + specs.map(\.setupEntryID)).count == specs.count * 2
              && (specs.map(\.entryID) + specs.map(\.setupEntryID)).allSatisfy { UUID(uuidString: $0) != nil }
              && !(specs.map(\.entryID) + specs.map(\.setupEntryID)).contains("AF046C35-C32A-497B-92BC-0BBD14F8CB61"),
              "ids and entry ids are unique UUIDs, none the Windows desktop's")
        let bn = BCDLauncherSpec.battlenet, ea = BCDLauncherSpec.eaApp
        check(bn.installerURL.absoluteString == "https://downloader.battle.net/download/getInstallerForGame?os=win&gameProgram=BATTLENET_APP&version=Live"
              && bn.hosts == ["downloader.battle.net"], "Battle.net's installer address is Blizzard's own, on downloader.battle.net")
        check(ea.installerURL.absoluteString == "https://origin-a.akamaihd.net/EA-Desktop-Client-Download/installer-releases/EAappInstaller.exe"
              && ea.hosts == ["origin-a.akamaihd.net"], "the EA app's installer address is the link on ea.com, on EA's own host")
        for s in specs {
            check(BCDLauncherNet.isAllowed(s.installerURL, hosts: s.hosts) && s.installerURL.scheme == "https" && s.hosts.contains(s.installerURL.host!),
                  "\(s.title): the installer address is https on its download host")
            check(s.programs.allSatisfy { !$0.hasPrefix("/") && !$0.contains("..") && !$0.contains("\\") && $0.hasSuffix(".exe") },
                  "\(s.title): programs are drive_c-relative .exe paths with /")
            check(s.installerWindowsPath == "C:\\bcd-launchers\\" + s.installerFile && s.installerRelativePath == "bcd-launchers/" + s.installerFile,
                  "\(s.title): the installer is saved in C:\\bcd-launchers")
            check(!s.sessionConfig.isEmpty && s.sessionConfig.contains("env.MADEIRA_MSIEXEC_SYSTEM32 = 1") && s.sessionConfig.contains("env.MADEIRA_GDI_SHARED_SECTION = 1"),
                  "\(s.title): its table entry holds the session lines")
        }
        check(bn.programs.first == "Program Files (x86)/Battle.net/Battle.net.exe", "Battle.net: the program the installer itself starts is the first choice")
        check(ea.needsGecko && !bn.needsGecko, "the EA app's installer asks for Wine Gecko first, Battle.net's does not")
        check(ea.sessionConfig.contains("env.MADEIRA_APC_BUSY_QUEUE = 1") && !bn.sessionConfig.contains("env.MADEIRA_APC_BUSY_QUEUE = 1"),
              "the EA app (and only it) keeps a finished answer for a busy thread: its MSI's first custom action lost one (JunoInitializeSession 1603)")
        let ok = URL(string: "https://downloader.battle.net/x")!
        check(BCDLauncherNet.isAllowed(ok, hosts: bn.hosts), "the address check takes https on the host")
        for (what, text) in [("http", "http://downloader.battle.net/x"), ("another host", "https://example.com/x"),
                             ("a look-alike", "https://downloader.battle.net.evil.example/x"), ("a port", "https://downloader.battle.net:8443/x"),
                             ("a user name", "https://user@downloader.battle.net/x"), ("a parent host", "https://battle.net/x")] {
            check(!BCDLauncherNet.isAllowed(URL(string: text)!, hosts: bn.hosts), "the address check refuses \(what)")
        }
        check(BCDLauncherNet.origin(URL(string: "https://downloader.battle.net/download/x?token=1")!) == "https://downloader.battle.net", "an origin has no path or query")
        check(bn.companyPossessive == "Blizzard's" && ea.companyPossessive == "Electronic Arts'" && ea.namedFirst == "The EA app" && bn.named == "Battle.net",
              "sentences name each launcher properly")

        // ---- installed detection
        let drive = tempFolder("drive")
        check(BCDLauncherFiles.installedProgram(bn, in: drive) == nil, "nothing in drive_c: not installed")
        touch(drive.appendingPathComponent("Program Files (x86)/Battle.net/Battle Net Notes.txt"))
        check(BCDLauncherFiles.installedProgram(bn, in: drive) == nil, "another file in the folder is no program")
        try! FileManager.default.createDirectory(at: drive.appendingPathComponent("Program Files (x86)/Battle.net/Battle.net.exe"), withIntermediateDirectories: true)
        check(BCDLauncherFiles.installedProgram(bn, in: drive) == nil, "a folder named like the program is no program")
        try! FileManager.default.removeItem(at: drive.appendingPathComponent("Program Files (x86)/Battle.net/Battle.net.exe"))
        touch(drive.appendingPathComponent("Program Files (x86)/Battle.net/Battle.net Launcher.exe"))
        check(BCDLauncherFiles.installedProgram(bn, in: drive) == "Program Files (x86)/Battle.net/Battle.net Launcher.exe", "only the second choice there: it is the program")
        touch(drive.appendingPathComponent("Program Files (x86)/Battle.net/Battle.net.exe"))
        check(BCDLauncherFiles.installedProgram(bn, in: drive) == "Program Files (x86)/Battle.net/Battle.net.exe", "both there: the first choice wins")
        let other = tempFolder("drive2")
        touch(other.appendingPathComponent("program files (x86)/BATTLE.NET/battle.net.EXE"))
        check(BCDLauncherFiles.installedProgram(bn, in: other) == "program files (x86)/BATTLE.NET/battle.net.EXE", "another spelling of the folders is found, as spelled on disk")
        let linked = tempFolder("drive3"), outside = tempFolder("outside")
        touch(outside.appendingPathComponent("Battle.net.exe"))
        try! FileManager.default.createDirectory(at: linked.appendingPathComponent("Program Files (x86)"), withIntermediateDirectories: true)
        try! FileManager.default.createSymbolicLink(at: linked.appendingPathComponent("Program Files (x86)/Battle.net"), withDestinationURL: outside)
        check(BCDLauncherFiles.installedProgram(bn, in: linked) == nil, "a link out of drive_c is not the launcher")
        touch(drive.appendingPathComponent("Program Files/Electronic Arts/EA Desktop/EA Desktop/EALauncher.exe"))
        check(BCDLauncherFiles.installedProgram(ea, in: drive) == "Program Files/Electronic Arts/EA Desktop/EA Desktop/EALauncher.exe", "the EA app's second choice is found")
        check(BCDLauncherFiles.resolve("../x.exe", in: drive) == nil && BCDLauncherFiles.resolve("", in: drive) == nil, "paths that leave drive_c or are empty are none")
        // 2026-10-24: the EA app's junction "EA Desktop\EA Desktop" is Wine's own reparse data, no folder here; the versioned folder is found
        let junction = tempFolder("drive-ea")
        touch(junction.appendingPathComponent("Program Files/Electronic Arts/EA Desktop/EA Desktop"))   // a plain file, as the reparse point looks
        touch(junction.appendingPathComponent("Program Files/Electronic Arts/EA Desktop/13.9.0.1/EA Desktop/EALauncher.exe"))
        touch(junction.appendingPathComponent("Program Files/Electronic Arts/EA Desktop/13.813.1.6327/EA Desktop/EADesktop.exe"))
        touch(junction.appendingPathComponent("Program Files/Electronic Arts/EA Desktop/13.813.1.6327/EA Desktop/EALauncher.exe"))
        check(BCDLauncherFiles.installedProgram(ea, in: junction) == "Program Files/Electronic Arts/EA Desktop/13.813.1.6327/EA Desktop/EADesktop.exe",
              "the EA app behind its junction: the highest version folder's program is found")
        check(BCDLauncherFiles.resolve("Program Files/*/x.exe", in: junction) == nil && BCDLauncherFiles.resolve("*/a/*/b.exe", in: junction) == nil,
              "a wildcard finds nothing that is not there, and only one inner wildcard counts")

        // ---- the session start
        let installerStart = BCDLauncherStart.installer(bn)
        let programStart = BCDLauncherStart.program(bn, relative: "Program Files (x86)/Battle.net/Battle.net.exe")
        check(installerStart.windowsPath == "C:\\bcd-launchers\\Battle.net-Setup.exe" && installerStart.arguments == "" && installerStart.inWineDesktop && installerStart.startServices,
              "the installer starts in a Wine desktop with services: its C:\\ path, no arguments")
        check(programStart.windowsPath == "C:\\Program Files (x86)\\Battle.net\\Battle.net.exe" && programStart.inWineDesktop && programStart.startServices
              && programStart.arguments == "--in-process-gpu --disable-gpu-process-for-dx12-info-collection --disable-site-isolation-trials --renderer-process-limit=2",
              "the launcher starts in a Wine desktop with services: its C:\\ path and Battle.net's Chromium switches (build 599)")
        let eaStart = BCDLauncherStart.program(BCDLauncherSpec.eaApp, relative: "Program Files/Electronic Arts/EA Desktop/EA Desktop/EADesktop.exe")
        check(eaStart.arguments == "", "the EA app starts with no arguments")
        let table = programStart.arguments
        check(BCDLauncherArguments.merged("", table: table) == table && BCDLauncherArguments.merged("--old-switch", table: table) == table,
              "an entry without the table's switches first gets them (build 599)")
        check(BCDLauncherArguments.merged(table + " --renderer-process-limit=1", table: table) == table + " --renderer-process-limit=1"
              && BCDLauncherArguments.merged("  " + table + " --x  ", table: table) == table + " --x",
              "what the player typed after the table's switches is kept (a trial added at the end)")
        check(BCDLauncherArguments.merged("-windowed", table: "") == "-windowed", "a launcher with no switches leaves the player's arguments alone")
        check(programStart.sessionConfig == bn.sessionConfig && installerStart.sessionConfig == bn.sessionConfig + bn.installerOnlyConfig,
              "both starts carry the table's session lines, the installer's also its own (round 625)")
        check(bn.installerOnlyConfig == ["env.MADEIRA_CHILD_ARGS = Battle.net.exe --in-process-gpu --disable-gpu-process-for-dx12-info-collection --disable-site-isolation-trials --renderer-process-limit=2", "pool-split = 1"]
              && "env.MADEIRA_CHILD_ARGS = Battle.net.exe " + programStart.arguments == bn.installerOnlyConfig[0]
              && !programStart.sessionConfig.contains(where: { $0.hasPrefix("env.MADEIRA_CHILD_ARGS") || $0.hasPrefix("pool-split") })
              && ea.installerOnlyConfig.isEmpty && BCDLauncherStart.installer(ea).sessionConfig == ea.sessionConfig,
              "Battle.net's installer hands its browser the launcher's own four switches and splits the pool; its Open and the EA app are unchanged")
        let eaInstaller = BCDLauncherStart.installer(ea)
        let eaProgram = BCDLauncherStart.program(ea, relative: "Program Files/Electronic Arts/EA Desktop/EA Desktop/EADesktop.exe")
        check(bn.launcherOnlyConfig.isEmpty && bn.programSessionConfig == bn.sessionConfig, "Battle.net has no launcher-only lines: its program's lines are the table's")
        check(ea.launcherOnlyConfig == ["env.MADEIRA_SC_PA_POOLS = 2", "env.MADEIRA_EA_CEF = 1"]
              && !ea.sessionConfig.contains("env.MADEIRA_EA_CEF = 1") && !ea.sessionConfig.contains("env.MADEIRA_SC_PA_POOLS = 2"),
              "the EA app's Chromium lines are launcher-only")
        check(eaProgram.sessionConfig == ea.sessionConfig + ["env.MADEIRA_SC_PA_POOLS = 2", "env.MADEIRA_EA_CEF = 1"]
              && eaInstaller.sessionConfig == ea.sessionConfig && !eaInstaller.sessionConfig.contains("env.MADEIRA_EA_CEF = 1"),
              "the EA app's program starts with them, its installer without")
        let lines = ["env.MADEIRA_GDI_SHARED_SECTION = 1", "env.MADEIRA_MSIEXEC_SYSTEM32 = 1"]
        let first = BCDLauncherConfig.merged(nil, lines: lines, id: "battlenet")
        check(first == [BCDLauncherConfig.opening("battlenet")] .joined() + "\n" + lines.joined(separator: "\n") + "\n" + BCDLauncherConfig.closing, "an entry with no config gets the block", first ?? "nil")
        check(BCDLauncherConfig.merged(first, lines: lines, id: "battlenet") == first, "merging again changes nothing")
        let own = "env.MADEIRA_GDI_SHARED_SECTION = 0\nenv.OTHER = 5"
        let mixed = BCDLauncherConfig.merged(own, lines: lines, id: "battlenet")!
        check(mixed.hasPrefix(own) && mixed.contains("env.MADEIRA_MSIEXEC_SYSTEM32 = 1") && mixed.components(separatedBy: "env.MADEIRA_GDI_SHARED_SECTION").count == 2,
              "a key the player wrote is kept and the table's line for it is dropped", mixed)
        let changed = BCDLauncherConfig.merged(mixed, lines: ["env.MADEIRA_MSIEXEC_SYSTEM32 = 0"], id: "battlenet")!
        check(changed.contains("MSIEXEC_SYSTEM32 = 0") && !changed.contains("MSIEXEC_SYSTEM32 = 1") && changed.hasPrefix(own), "a changed table rewrites the block and leaves the player's lines")
        check(BCDLauncherConfig.merged(first, lines: [], id: "battlenet") == nil, "no lines left: no config")
        check(BCDLauncherConfig.merged("a = 1", lines: lines, id: "eaapp")!.hasPrefix("a = 1\n# BCD launcher settings (eaapp)"), "another launcher's block is its own")

        // ---- the header of an installer
        check(BCDLauncherPE.inspect(fakePE(size: 4096)) == .init(machine: 0x14C, signed: true), "a signed x86 program is read")
        check(BCDLauncherPE.inspect(fakePE(size: 4096, machine: 0x8664, signed: false, pe32plus: true)) == .init(machine: 0x8664, signed: false), "an unsigned x64 program is read")
        check(BCDLauncherPE.inspect(Data("<html>Not found</html>".utf8)) == nil && BCDLauncherPE.inspect(Data()) == nil && BCDLauncherPE.inspect(fakePE(size: 4096, machine: 0xAA64)) == nil
              && BCDLauncherPE.inspect(fakePE(size: 4096).prefix(0x90)) == nil, "an error page, nothing, an ARM64 image and a truncated file are not programs")

        // ---- the download
        let installer = fakePE(size: 400_000)
        let folder = tempFolder("download")
        var log: [String] = []
        func fetch(_ spec: BCDLauncherSpec, url: URL? = nil) async -> Result<URL, Error> {
            do {
                let result = try await BCDLauncherInstaller.fetch(spec, into: folder, url: url, configuration: Mock.configuration(), log: { log.append($0) }, progress: { _, _ in })
                return .success(result)
            } catch { return .failure(error) }
        }
        func reason(_ r: Result<URL, Error>) -> String { if case .failure(let e) = r { return (e as? BCDLauncherError)?.logReason ?? "other" }; return "ok" }
        let dl = "downloader.battle.net/download/getInstallerForGame"
        Mock.redirects[dl] = "https://downloader.battle.net/download/installer/win/1.0.68/Battle.net-Setup.exe"
        Mock.routes["downloader.battle.net/download/installer/win/1.0.68/Battle.net-Setup.exe"] = (200, installer, [:])
        let good = await fetch(bn)
        let kept = folder.appendingPathComponent("Battle.net-Setup.exe")
        check({ if case .success(let u) = good { return u == kept && (try? Data(contentsOf: kept)) == installer }; return false }(), "the answer is followed to the same host and kept as Battle.net-Setup.exe")
        check(!FileManager.default.fileExists(atPath: folder.appendingPathComponent("Battle.net-Setup.exe.part").path), "no partial file is left")
        check(log.contains { $0.hasPrefix("[launcher] battlenet installer kept bytes=400000 bits=32 signed=1") }, "the log says what was kept", log.joined(separator: " | "))
        check(log.allSatisfy { !$0.contains("?") && !$0.contains("getInstallerForGame") && !$0.contains("/download/") }, "the log names hosts, never an address with a path or query", log.joined(separator: " | "))
        for (what, target, expected) in [("another host", "https://evil.example.net/Battle.net-Setup.exe", "redirected"),
                                         ("a look-alike host", "https://downloader.battle.net.evil.example.net/x.exe", "redirected"),
                                         ("http", "http://downloader.battle.net/download/installer/x.exe", "redirected"),
                                         ("a port", "https://downloader.battle.net:8443/download/installer/x.exe", "redirected")] {
            Mock.redirects[dl] = target
            let r = await fetch(bn)
            check(reason(r) == expected, "a redirect to \(what) is refused", reason(r))
            check(!FileManager.default.fileExists(atPath: folder.appendingPathComponent("Battle.net-Setup.exe.part").path), "…and leaves no partial file")
        }
        Mock.redirects[dl] = "https://downloader.battle.net/gone"
        check(reason(await fetch(bn)) == "http-404", "a 404 is said")
        Mock.redirects[dl] = nil
        Mock.routes[dl] = (503, Data("busy".utf8), [:])
        check(reason(await fetch(bn)) == "http-503", "a 503 is said")
        Mock.routes[dl] = (200, Data(repeating: 0x20, count: 400_000), [:])
        check(reason(await fetch(bn)) == "not-an-installer", "a page that is no program is not kept")
        Mock.routes[dl] = (200, Data("short".utf8), [:])
        check(reason(await fetch(bn)) == "not-an-installer", "a tiny answer is not kept")
        Mock.routes[dl] = (200, Data(count: 1 << 20), ["Content-Length": String(300 << 20)])
        check(reason(await fetch(bn)) == "too-large", "an oversized answer is stopped")
        check(reason(await fetch(bn, url: URL(string: "https://example.com/x")!)) == "redirected", "a first address off the host is refused before any request")
        check((try? Data(contentsOf: kept)) == installer, "a refused download leaves the earlier copy alone")
        check(BCDLauncherError.http(404, company: "Blizzard").errorDescription!.contains("Blizzard's download server answered 404"), "the words name the company and the status")
        check(Mock.seen.allSatisfy { !$0.contains("?") }, "the requests the mock saw are origins")

        // ---- the model
        let engine = FakeEngine()
        let model = BCDLauncherModel(engine: engine)
        model.settleSeconds = 0.05
        check(!model.status(bn).installed && model.available, "the model starts with nothing installed")
        model.install(bn)
        check(model.status(bn).download != nil && model.busy, "Install shows the download at once")
        model.install(ea)
        await waitIdle(model)
        check(engine.calls == ["blocker", "installer-download", "start-installer:battlenet"], "Install: blocker, download, then the installer starts", engine.calls.joined(separator: ","))
        engine.calls = []
        engine.componentNeeded = true
        model.install(ea)
        await waitIdle(model)
        check(engine.calls == ["blocker", "installer-download", "component-download", "start-installer:eaapp"], "the EA app: the HTML engine is downloaded between the installer and its start", engine.calls.joined(separator: ","))
        engine.calls = []
        model.install(bn)
        await waitIdle(model)
        check(engine.calls == ["blocker", "installer-download", "start-installer:battlenet"], "Battle.net never downloads the HTML engine", engine.calls.joined(separator: ","))
        engine.calls = []
        engine.blocker = "Restart BCD."
        model.install(bn)
        check(model.status(bn).message == "Restart BCD." && !model.busy && engine.calls == ["blocker"], "a blocked session says why and downloads nothing")
        engine.blocker = nil
        engine.installerError = BCDLauncherError.http(503, company: "Blizzard")
        engine.calls = []
        model.install(bn)
        await waitIdle(model)
        check(model.status(bn).message?.contains("503") == true && engine.calls.last == "installer-download", "a failed download is said in the card and nothing starts")
        engine.installerError = nil
        engine.componentError = BCDLauncherError.checksum(what: "HTML engine")
        engine.calls = []
        model.install(ea)
        await waitIdle(model)
        check(model.status(ea).message?.contains("checksum") == true && !engine.calls.contains("start-installer:eaapp"), "a failed HTML engine stops the install and says why")
        engine.componentError = nil; engine.componentNeeded = false
        engine.hold = true
        engine.calls = []
        model.install(bn)
        try? await Task.sleep(nanoseconds: 50_000_000)
        model.cancel(bn)
        await waitIdle(model)
        check(engine.calls.contains("cancel") && model.status(bn).message == nil && !engine.calls.contains("start-installer:battlenet"), "Cancel stops the download without a message and starts nothing")
        engine.hold = false
        engine.calls = []
        model.open(bn)
        check(model.status(bn).message?.contains("isn't installed") == true && engine.calls.isEmpty, "Open with nothing installed says so")
        engine.installed["battlenet"] = "Program Files (x86)/Battle.net/Battle.net.exe"
        model.refresh()
        check(model.status(bn).installed, "refresh finds the launcher")
        model.open(bn)
        model.open(bn)
        check(engine.calls == ["blocker", "start-launcher:battlenet:Program Files (x86)/Battle.net/Battle.net.exe"], "Open starts the installed program once; a second tap while it starts does nothing", engine.calls.joined(separator: ","))
        await waitIdle(model)

        // ---- Wine Gecko
        let packages = BCDGeckoPin.packages
        check(packages.map(\.arch) == ["x86", "x86_64"] && packages.allSatisfy { $0.sha256.count == 64 && $0.sha256.allSatisfy(\.isHexDigit) },
              "Gecko: two packages with 64-digit SHA-256 values")
        check(packages.map(\.file) == ["wine-gecko-\(BCDGeckoPin.version)-x86.msi", "wine-gecko-\(BCDGeckoPin.version)-x86_64.msi"]
              && packages.allSatisfy { $0.url.absoluteString == "https://dl.winehq.org/wine/wine-gecko/\(BCDGeckoPin.version)/" + $0.file && BCDLauncherNet.isAllowed($0.url, hosts: BCDGeckoPin.hosts) },
              "Gecko: file names follow addons.c's pattern and every address is https on dl.winehq.org")
        print("GECKO-PINS \(BCDGeckoPin.version) \(packages[0].sha256) \(packages[1].sha256)")
        let root = tempFolder("gecko")
        let pathOK = packages.allSatisfy { BCDGeckoFiles.file(root, $0).path == BCDGeckoFiles.cacheFolder(root).path + "/wine/" + $0.file }
        check(pathOK, "Gecko: the files sit at <cache>/wine/<file>, where addons.c looks under WINE_HOST_XDG_CACHE_HOME")
        var exported: [String: String] = [:]
        check(BCDGeckoFiles.exportEnvironment(root: root, set: { exported[$0] = $1 }) == nil && exported.isEmpty, "Gecko: not downloaded, nothing is exported")
        for p in packages {
            let file = BCDGeckoFiles.file(root, p)
            try! FileManager.default.createDirectory(at: file.deletingLastPathComponent(), withIntermediateDirectories: true)
            FileManager.default.createFile(atPath: file.path, contents: nil)
            let h = try! FileHandle(forWritingTo: file); try! h.truncate(atOffset: UInt64(p.bytes - 1)); try! h.close()   // a file of the pinned size (a hole)
        }
        check(BCDGeckoFiles.ready(root) == false, "Gecko: a file one byte short is not ready")
        for p in packages {
            let h = try! FileHandle(forWritingTo: BCDGeckoFiles.file(root, p)); try! h.truncate(atOffset: UInt64(p.bytes)); try! h.close()
        }
        check(BCDGeckoFiles.ready(root), "Gecko: both files at their pinned size are ready")
        let line = BCDGeckoFiles.exportEnvironment(root: root, set: { exported[$0] = $1 })
        check(exported == ["XDG_CACHE_HOME": BCDGeckoFiles.cacheFolder(root).path] && line?.hasPrefix("[gecko]") == true, "Gecko: ready, only XDG_CACHE_HOME is exported", "\(exported)")
        try! FileManager.default.removeItem(at: BCDGeckoFiles.file(root, packages[1]))
        exported = [:]
        check(BCDGeckoFiles.exportEnvironment(root: root, set: { exported[$0] = $1 }) == nil && exported.isEmpty, "Gecko: one file missing, nothing is exported")

        // the model, with small stand-in packages served by the mock
        let bodyA = Data((0..<5000).map { UInt8($0 % 251) }), bodyB = Data((0..<7000).map { UInt8($0 % 241) })
        let small = [BCDGeckoPin.Package(arch: "x86", file: "wine-gecko-test-x86.msi", url: URL(string: "https://dl.winehq.org/wine/wine-gecko/test/a.msi")!, sha256: sha(bodyA), bytes: Int64(bodyA.count)),
                     BCDGeckoPin.Package(arch: "x86_64", file: "wine-gecko-test-x86_64.msi", url: URL(string: "https://dl.winehq.org/wine/wine-gecko/test/b.msi")!, sha256: sha(bodyB), bytes: Int64(bodyB.count))]
        Mock.routes["dl.winehq.org/wine/wine-gecko/test/a.msi"] = (200, bodyA, [:])
        Mock.routes["dl.winehq.org/wine/wine-gecko/test/b.msi"] = (200, bodyB, [:])
        let root2 = tempFolder("gecko2")
        let gecko = BCDGeckoModel(root: root2, packages: small)
        var geckoLog: [String] = []
        gecko.log = { geckoLog.append($0) }
        gecko.configuration = Mock.configuration()
        check(gecko.state == .missing && !gecko.isReady, "Gecko model: nothing downloaded at first")
        var reported: [(Int64, Int64)] = []
        let box = Reported()
        do { try await gecko.ensure(progress: { box.add($0, $1) }); reported = box.values } catch { check(false, "Gecko model: the download worked", "\(error)") }
        check(gecko.isReady && gecko.state == .installed && (try? Data(contentsOf: BCDGeckoFiles.file(root2, small[0]))) == bodyA
              && (try? Data(contentsOf: BCDGeckoFiles.file(root2, small[1]))) == bodyB, "Gecko model: both files are downloaded and kept")
        check(reported.last?.0 == Int64(bodyA.count + bodyB.count) && reported.last?.1 == Int64(bodyA.count + bodyB.count), "Gecko model: progress runs to the sum of both files", "\(reported.last ?? (0, 0))")
        check(geckoLog.allSatisfy { !$0.contains("?") && !$0.contains("/wine/wine-gecko/") }, "Gecko model: the log names hosts and sizes only", geckoLog.joined(separator: " | "))
        let before = Mock.seen.count
        try? await gecko.ensure()
        check(Mock.seen.count == before, "Gecko model: ready, ensure downloads nothing")
        gecko.remove()
        check(!gecko.isReady && gecko.state == .missing && !FileManager.default.fileExists(atPath: BCDGeckoFiles.cacheFolder(root2).path), "Gecko model: Remove deletes the files")
        Mock.routes["dl.winehq.org/wine/wine-gecko/test/b.msi"] = (200, Data(bodyB.reversed()), [:])
        do { try await gecko.ensure(); check(false, "Gecko model: a wrong file is refused") }
        catch { check((error as? BCDLauncherError)?.logReason == "checksum", "Gecko model: a file with the wrong SHA-256 is refused", "\(error)") }
        check(!FileManager.default.fileExists(atPath: BCDGeckoFiles.file(root2, small[1]).path) && !FileManager.default.fileExists(atPath: BCDGeckoFiles.file(root2, small[1]).path + ".part")
              && !gecko.isReady && BCDGeckoFiles.ready(root2, packages: [small[0]]), "Gecko model: …it leaves no copy of it, and the first file stays")
        Mock.routes["dl.winehq.org/wine/wine-gecko/test/b.msi"] = (200, bodyB, [:])
        Mock.redirects["dl.winehq.org/wine/wine-gecko/test/b.msi"] = "https://evil.example.net/b.msi"
        do { try await gecko.ensure(); check(false, "Gecko model: a redirect off WineHQ is refused") }
        catch { check((error as? BCDLauncherError)?.logReason == "redirected", "Gecko model: a redirect off WineHQ is refused", "\(error)") }
        Mock.redirects["dl.winehq.org/wine/wine-gecko/test/b.msi"] = nil
        gecko.install()
        for _ in 0..<300 { if gecko.isReady { break }; try? await Task.sleep(nanoseconds: 10_000_000) }
        check(gecko.isReady && gecko.lastError == nil && gecko.state == .installed, "Gecko model: Settings' Download finishes the job after a failure")

        print(failed ? "DRIVER FAILED" : "DRIVER OK")
        exit(failed ? 1 : 0)
    }
}

final class Reported: @unchecked Sendable {
    private let lock = NSLock()
    private(set) var values: [(Int64, Int64)] = []
    func add(_ a: Int64, _ b: Int64) { lock.lock(); values.append((a, b)); lock.unlock() }
}
'''

with tempfile.TemporaryDirectory(prefix="bcd-launchers-") as tmp:
    tmp = Path(tmp)
    (tmp / "driver.swift").write_text(DRIVER)
    sources = [str(APP / n) for n in ("BCDLaunchers.swift", "BCDLauncherDownload.swift", "BCDGecko.swift")]
    build = subprocess.run([SWIFTC, "-parse-as-library", "-swift-version", "5", "-o", str(tmp / "driver"), *sources, str(tmp / "driver.swift")],
                           capture_output=True, text=True)
    check(build.returncode == 0, "the launcher and Gecko files compile on the host with the driver", build.stderr[-4000:])
    if build.returncode == 0:
        run = subprocess.run([str(tmp / "driver")], capture_output=True, text=True, timeout=300)
        out = run.stdout
        for line in out.splitlines():
            if line.startswith("FAIL"):
                check(False, line[5:])
        n_ok = sum(1 for line in out.splitlines() if line.startswith("ok "))
        check(run.returncode == 0 and "DRIVER OK" in out, f"the compiled checks all pass ({n_ok} checks)", (out + run.stderr)[-3000:])
        m = re.search(r"GECKO-PINS (\S+) (\S+) (\S+)", out)
        if addons and m and all(pins.values()):
            check(m.group(1) == pins["version"], f"Gecko's version {m.group(1)} equals addons.c's GECKO_VERSION")
            check(m.group(2) == pins["x86"], "Gecko's x86 SHA-256 equals addons.c's GECKO_SHA")
            check(m.group(3) == pins["x86_64"], "Gecko's x86_64 SHA-256 equals addons.c's GECKO_SHA")

# Round 625 (owner's item 25, agent A S5; EA app setup H95G25): Wine's PunkBuster stubs start on demand in the EA app's and
# Battle.net's sessions (installer and program); the start carries the list, the entry takes it at every start; no other spec has one.
_live = files.get("BCDLaunchersLive.swift") or (APP / "BCDLaunchersLive.swift").read_text()
check(launchers_src.count('demandStartServices: ["PnkBstrA", "PnkBstrB"])') == 2
      and 'var demandStartServices: [String] = []' in launchers_src
      and launchers_src.count('demandStartServices: spec.demandStartServices)') == 2
      and 'entry.demandStartServices = start.demandStartServices.isEmpty ? nil : start.demandStartServices' in _live,
      "round 625: the EA app and Battle.net set PunkBuster's stubs to start on demand in both of their sessions; nothing else does")

# Round 625 (owner's item 27, agent N S5; Battle.net's installer Y0HFHB, build 614): the code fix's auto-tune rule (fixedIn 625) names
# the installer's own browser start without the switches and the exhausted pool, and steps aside once [child-args] appended them.
_rules = (R / "build/tools/autotune_rules.py").read_text()
_bn = _rules[_rules.index('"id": "battlenet-installer-browser-switches"'):]
_bn = _bn[:_bn.index("\n    },")]
_exp = __import__("json").loads((R / "tests/fixtures/autotune/battlenet-installer-pool-exhausted-2026-10-29.expect").read_text())
check('"fixedIn": 625' in _bn and '"lines": []' in _bn
      and 's("--runonstartup=true", "Battle.net.exe"), s("library launch Battle.net-Setup.exe:"), s("[jit-pool] EXHAUSTED")' in _bn
      and 's("[child-args] env.MADEIRA_CHILD_ARGS: appended")' in _bn
      and _exp["matched"][0] == "battlenet-installer-browser-switches" and _exp["status"] == "known",
      "round 625: auto-tune names Battle.net's installer browser without the switches as fixed in 625 (fixture Y0HFHB)")

print("PASS: BCD launchers" if not failures else f"FAIL: {failures} check(s) failed")
sys.exit(1 if failures else 0)

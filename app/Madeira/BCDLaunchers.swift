// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

import Foundation

// PC launchers in BCD (Battle.net first, the EA app second): a launcher is a program of its own
// company that installs and starts games itself. BCD does not list its games; it only gets the
// launcher onto the device and starts it. The Library has one chip per launcher (BCDLibraryView) and
// the chip shows one card (BCDLauncherView): Install when the launcher is not there, Open when it is.
//
// Install downloads the company's official installer over HTTPS from the company's own download
// host (BCDLauncherDownload.swift), saves it in drive_c, and starts it as the session's first program,
// the way a game's program is started (no Wine desktop, no explorer). Open starts the installed
// launcher the same way. BCD never asks for or sees a login: the launcher's own window does that.
//
// To add a launcher (Ubisoft Connect, for one): one more BCDLauncherSpec in `BCDLauncherSpec.all`
// below, with the company's own installer address and download host, the launcher's program in the
// prefix and two new entry ids. Nothing else changes; tests/host/check-bcd-launchers.py reads the table.
//
// This file names no engine type. The engine's side (the files in drive_c, the library entries, the
// session) is BCDLauncherEngine, carried out by BCDLaunchersLive.swift in the app and by a sample in
// the preview app (docs/BCD-UI-ARCHITECTURE.md).

// MARK: - The table

/// One launcher: what it is called, where its installer comes from and where its program lands.
struct BCDLauncherSpec: Identifiable, Hashable {
    /// A short word for ids and the Library filter: "battlenet".
    let id: String
    /// The name the interface uses: "Battle.net".
    let title: String
    /// The company, for sentences: "Blizzard".
    let company: String
    /// A launcher that may not work on every device; its card says so.
    var experimental = false
    /// An SF Symbol, the same plain one for every launcher: no company logo is used anywhere.
    var symbol = "macwindow"
    /// The company's own installer address (copied from the company's download page).
    let installerURL: URL
    /// Every host the download may touch, the first request and each redirect: the company's own.
    let hosts: Set<String>
    /// What the installer is saved as in drive_c/bcd-launchers.
    let installerFile: String
    /// About how large the installer is, for the card ("about 5 MB").
    let sizeHint: String
    /// The launcher's program in the prefix, relative to drive_c with "/" between folders; the first
    /// one that exists is what Open starts. "Installed" means one of them exists, and the card moves from
    /// Install to Open when the session of the installer has ended and one of them is there.
    let programs: [String]
    /// The library entries (UUID text): one for the installed launcher, one for the installer run.
    let entryID: String
    let setupEntryID: String
    /// THE PLACE FOR WHAT THE LAUNCHER'S SESSION NEEDS (one entry per launcher; the engine work on a
    /// launcher adds its lines here). `sessionConfig` is the launcher's own lines in bcd.cfg's syntax
    /// ("env.NAME = value", as in Game details › This game's config), put on the library entry of the
    /// installer run and of the installed launcher at every start; a line the player wrote himself in
    /// that entry's config for the same key wins. `launcherArguments` is the command line Open starts the
    /// launcher with, `installerArguments` the one the installer starts with.
    var sessionConfig: [String] = []
    /// Lines only the installed launcher's session gets, after `sessionConfig`'s (its Open and its library tile), never its
    /// installer's: a layout of the address space the launcher's own program needs and its installer does not. Empty for most.
    var launcherOnlyConfig: [String] = []
    /// Lines only the installer's session gets, after `sessionConfig`'s (round 625, the owner's item 27, agent N S5; Battle.net
    /// Y0HFHB): the installer starts the launcher's browser itself, without `launcherArguments`, so a line that carries those words
    /// to it (env.MADEIRA_CHILD_ARGS) belongs here and nowhere else. Empty for most.
    var installerOnlyConfig: [String] = []
    /// The launcher program's session lines: `sessionConfig` and `launcherOnlyConfig`.
    var programSessionConfig: [String] { sessionConfig + launcherOnlyConfig }
    var launcherArguments = ""
    var installerArguments = ""
    /// An installer outside these sizes is not the installer (an error page is far smaller).
    var minBytes: Int64 = 256 << 10
    var maxBytes: Int64 = 64 << 20
    /// Free room, in gigabytes, the launcher needs once installed (the card refuses an install with less).
    var spaceGigabytes = 1
    /// The installer shows an embedded web page, so Wine asks for Wine Gecko: Install downloads it first with BCD's own
    /// downloader (BCDGecko.swift) when it is not on the device yet. The EA app's installer does (build 592: Wine's
    /// own dialog took minutes at 70 KB/s); Battle.net's session log (build 592) has no Gecko line, so it does not.
    var needsGecko = false

    /// "Battle.net" or "EA app (experimental)".
    var label: String { experimental ? "\(title) (experimental)" : title }
    /// A name that needs "the" in a sentence ("the EA app"); Battle.net does not.
    var needsArticle = false
    /// Round 625 (owner's item 25, agent A S5; EA app setup H95G25): Windows services the session's batch sets to start on demand
    /// (start type 3) before services.exe starts, when the prefix has them. Wine's PunkBuster stubs PnkBstrA/PnkBstrB autostart in
    /// every session and each held one of the 32-bit 4 GB window slots for good: the EA app installer's custom-action host found
    /// none left. On demand they still exist and start when a program asks for them. Empty for most.
    var demandStartServices: [String] = []
    /// How a sentence names the launcher: "Battle.net", "the EA app".
    var named: String { needsArticle ? "the " + title : title }
    /// The same at the start of a sentence: "Battle.net", "The EA app".
    var namedFirst: String { needsArticle ? "The " + title : title }
    /// The company's name as a possessive: "Blizzard's", "Electronic Arts'".
    var companyPossessive: String { company.hasSuffix("s") ? company + "'" : company + "'s" }
    var installerFolder: String { "bcd-launchers" }
    /// The installer's place in drive_c.
    var installerRelativePath: String { installerFolder + "/" + installerFile }
    var installerWindowsPath: String { Self.windowsPath(installerRelativePath) }

    /// drive_c/Program Files/x.exe -> C:\Program Files\x.exe
    static func windowsPath(_ relative: String) -> String { "C:\\" + relative.replacingOccurrences(of: "/", with: "\\") }

    /// What every launcher's session gets (lines in an entry's config syntax; each is a switch the engine already reads):
    /// - env.MADEIRA_GDI_SHARED_SECTION = 1: the GDI table is shared with 32-bit programs. A direct start of a 32-bit
    ///   program arms that by itself; the line covers a launcher whose first program is 64-bit (WineProcessBridge, win32u).
    /// - env.MADEIRA_MSIEXEC_SYSTEM32 = 1: the i386 msiexec.exe (the only one in the app) is linked into the 64-bit
    ///   system32 for the session, so an installer's "Windows Installer service" starts (the EA app's installer failed
    ///   with "Windows Installer service is not responding", build 592; WineProcessBridge.m [msiexec64]).
    /// A launcher that needs more adds its lines to its own entry below.
    static let commonSessionConfig = ["env.MADEIRA_GDI_SHARED_SECTION = 1", "env.MADEIRA_MSIEXEC_SYSTEM32 = 1"]

    /// The launchers BCD offers, in the order the Library lists them.
    static let all: [BCDLauncherSpec] = [battlenet, eaApp]

    /// Build 600 (the owner, 2026-10-24: the public release goes out without Battle.net and the EA app, hide them completely, with no
    /// switch to turn them on; the next local build turns them back on): no launcher chip, no launcher card, no library tile of a launcher's own
    /// entry, and no setting to bring them back. The engine, the lines and the checks stay as they are. Set to false to show them again.
    static let hiddenInThisBuild = false
    /// The launchers the interface offers: all of them, or none while they are hidden.
    static var shown: [BCDLauncherSpec] { hiddenInThisBuild ? [] : all }
    /// A launcher's own library entry (its program's or its installer's), which the library leaves out while they are hidden.
    static func hidesEntry(_ id: UUID) -> Bool {
        hiddenInThisBuild && all.contains {
            $0.entryID.caseInsensitiveCompare(id.uuidString) == .orderedSame || $0.setupEntryID.caseInsensitiveCompare(id.uuidString) == .orderedSame
        }
    }

    static func spec(_ id: String) -> BCDLauncherSpec? { all.first { $0.id == id } }

    /// The id the store's startingJIT names while this launcher's Play turns JIT on ("launcher.battlenet").
    static func gameID(_ id: String) -> String { "launcher." + id }

    /// Blizzard's Battle.net desktop app. The address is the one Blizzard's own download page
    /// (download.battle.net/en-us/desktop, its "Download for Windows" button) sends the browser to;
    /// it answers with one redirect to the current Battle.net-Setup.exe on the same host (4.7 MB, 2026-10-23).
    /// What the installer made on the owner's device (build 592, 2026-10-23): C:\ProgramData\Battle.net\Agent
    /// (Agent.exe, and Agent.<n>\Agent.exe after its update), C:\Program Files (x86)\Battle.net\Battle.net.exe
    /// (32-bit; the installer starts it itself, and Chromium's helper processes are the same file) beside
    /// Battle.net.<build>\, the public Start Menu folder Battle.net and C:\users\Public\Desktop\Battle.net.lnk.
    /// No "Battle.net Launcher.exe" was seen in that install; it is kept as the second choice for builds
    /// that have one. The installer, the launcher and Agent are 32-bit: a direct start of the 32-bit installer
    /// arms the session for 32-bit programs, so the GDI table is shared with them without a line; the line
    /// below says it for a launcher whose first program is 64-bit and costs nothing here.
    static let battlenet = BCDLauncherSpec(
        id: "battlenet", title: "Battle.net", company: "Blizzard",
        installerURL: URL(string: "https://downloader.battle.net/download/getInstallerForGame?os=win&gameProgram=BATTLENET_APP&version=Live")!,
        hosts: ["downloader.battle.net"],
        installerFile: "Battle.net-Setup.exe", sizeHint: "about 5 MB",
        programs: ["Program Files (x86)/Battle.net/Battle.net.exe",
                   "Program Files (x86)/Battle.net/Battle.net Launcher.exe"],
        entryID: "052C9973-3715-42CD-A6FA-D4AE22378966", setupEntryID: "267993CD-F9B9-44B0-977B-02E6D7145894",
        // ajan18/bnet-cef (2026-10-23, builds 594 and 596): the launcher's window spins for ever because its Chromium 108 renderers (the same
        // Battle.net.exe with --type=renderer, 32-bit, Blizzard's own libcef.dll) die 9 times in 12 on an int3 at libcef.dll+0x16D00E1, a few
        // hundred milliseconds after GpuMemoryThread starts. The int3 is V8's own check in v8::base::OS::SetDataReadOnly (V8 10.8, the flag
        // freeze that V8::Initialize does): VirtualProtect( v8_flags, PAGE_READONLY, &old ) succeeds and the check wants old == PAGE_READWRITE;
        // BCD reports PAGE_WRITECOPY, the recorded protection of an image's data page, whether or not the page was ever written.
        // env.MADEIRA_PROTECT_WRITECOPY_AS_RW = 1 makes a 32-bit program's write-protecting of such a page report PAGE_READWRITE, as Windows does
        // for a page that has been written (build/ntdll-unix/ios_wc_old_prot.h, the return value only). env.MADEIRA_INT3_X86_DUMP = 1 prints, at
        // a 32-bit guest's int3 (3 times at most, "[int3-x86]"), the stack, the code, the module list and libcef.dll's CodeView record, so that
        // the next int3 of this program can be named the same way (log only: reads the guest's memory, writes nothing, stops nothing).
        // Only this entry sets them; a 64-bit process (the Rockstar Games Launcher's 64-bit CEF) is never touched by either, set or not.
        sessionConfig: Self.commonSessionConfig + ["env.MADEIRA_PROTECT_WRITECOPY_AS_RW = 1", "env.MADEIRA_INT3_X86_DUMP = 1"],
        // lead (2026-10-24, build 598, the owner's iPhone18,2): every 32-bit process needs a 4 GB window of its own, and this device has 7
        // free. Battle.net, its GPU process, its storage and network services, its renderers and the Agent took all 7: four renderers
        // and a GPU process (the DX12/Vulkan info one, at 126 s) could not start ("BOOT FAILED at stage 'guest-window-reserve'", log
        // 13:58:19), and the Home tab's content showed an error. Chromium's own switches on Battle.net.exe's command line (Chromium 108
        // reads its process's command line): the GPU runs in the browser process, no process for the DX12 info, one renderer process for
        // pages of every site and at most two. The owner's run with them (log 14:23:11): no BOOT FAILED, no GPU process, renderers kept to
        // two, the Home tab and its animated banner drawn, the app smoother. --enable-features=NetworkServiceInProcess, tried with them,
        // did nothing (the network service still started on its own) and is left out. Only this entry gets them.
        // ajan33/bnet-ram (2026-10-24, the logs of 13:58 and 14:23): the four keep the processes within the windows; they do not shrink the
        // memory (footprint 4.0-4.3 GB with and without them), and the 14:23 run still started three renderers (three libcef.dll maps) and
        // ended when a fourth could not load ucrtbase.dll: "[jit-pool] EXHAUSTED", the session's pool had shrunk to 544 MB (624 MB at 13:58;
        // the size follows where the app's stack lands). Why NetworkServiceInProcess did nothing: Chromium 108.0.5359.125 (Battle.net's user
        // agent) names that feature NetworkServiceInProcess2 (content/public/common/content_features.cc, off by default on Windows), and an
        // unknown feature name is ignored without a word. Every Chromium process of Battle.net.exe reads its own copy of libcef.dll into
        // anonymous memory (map_image_view size=0x9f84000, ~159 MiB dirty per process: a PE section is mmapped only at 16 KB-aligned file
        // offsets), so a Chromium process that does not start is worth about 0.4 GB. Nothing beyond these four is shipped; every
        // further switch or line is a trial the owner runs first (ordered list: ajan33.md in intake/analysis/bnet-ram-2026-10-24).
        // Round 625 (the owner's item 27, agent N S5; Y0HFHB, build 614, iPad16,6): Battle.net-Setup.exe starts Battle.net.exe itself
        // ("--detected-country=(NULL) --detected-region=US --runonstartup=true --setlanguage=frFR"), so the four switches below were not on its
        // command line; that session had eleven 32-bit processes at 86 s and its 560 MB pool ran out ("[jit-pool] EXHAUSTED"). The installer's
        // session now gives them to every Battle.net.exe it starts (env.MADEIRA_CHILD_ARGS appends them; Chromium's own subprocesses get them
        // twice, and Chromium reads the last copy of a switch), and splits the JIT pool around the main thread's stack, which auto-tune
        // proposes after such an exhausted pool (pool-split-exhausted). The installed launcher's start is unchanged.
        installerOnlyConfig: ["env.MADEIRA_CHILD_ARGS = Battle.net.exe " + Self.battlenetChromiumSwitches, "pool-split = 1"],
        launcherArguments: Self.battlenetChromiumSwitches,
        demandStartServices: ["PnkBstrA", "PnkBstrB"])

    /// Battle.net's four Chromium switches (above): the installed launcher's command line, and the installer's browser through
    /// env.MADEIRA_CHILD_ARGS, from one place so the two cannot drift.
    static let battlenetChromiumSwitches = "--in-process-gpu --disable-gpu-process-for-dx12-info-collection --disable-site-isolation-trials --renderer-process-limit=2"

    /// Electronic Arts' EA app. The address is the link on ea.com/ea-app (its Windows download), a
    /// direct file on EA's own distribution host. The EA app installs 64-bit into Program Files\Electronic Arts.
    static let eaApp = BCDLauncherSpec(
        id: "eaapp", title: "EA app", company: "Electronic Arts", experimental: true,
        installerURL: URL(string: "https://origin-a.akamaihd.net/EA-Desktop-Client-Download/installer-releases/EAappInstaller.exe")!,
        hosts: ["origin-a.akamaihd.net"],
        installerFile: "EAappInstaller.exe", sizeHint: "about 2 MB",
        programs: ["Program Files/Electronic Arts/EA Desktop/EA Desktop/EADesktop.exe",
                   "Program Files/Electronic Arts/EA Desktop/EA Desktop/EALauncher.exe",
                   // The versioned folder the junction above points at (build 597 install: "13.813.1.6327"); see resolve.
                   "Program Files/Electronic Arts/EA Desktop/*/EA Desktop/EADesktop.exe",
                   "Program Files/Electronic Arts/EA Desktop/*/EA Desktop/EALauncher.exe"],
        entryID: "E53F8EF0-CFB7-49B2-9A3D-D1F488365F6D", setupEntryID: "73C20BA0-D7A0-40BF-B76F-B19AF0106335",
        // madeira-bcd (lead, 2026-10-23): an English locale for the EA app. With the phone's non-English one the installer's bootstrapper
        // loaded its code page (1254) and died on an uncaught ATL::CAtlException right after its telemetry step ("C7-2F79", logs 16:28 and
        // 17:09:52); with env.LC_ALL = en_US.UTF-8 it went on to the MSI package (17:15:17), as Rift Apart did with the same line.
        // ajan14/ea-mono (2026-10-23): env.MADEIRA_APC_BUSY_QUEUE = 1 for the package's first custom action. The MSI stopped at
        // "JunoInitializeSession" (1603): the action runs in rundll32.exe, which asks msiexec's custom action server for every MSI call
        // over an overlapped pipe, and the server's first answer arrived while rundll32's only thread was running, so BCD's own Windows
        // server dropped it ("[apc-requeue] ... dropped, no waiting thread"), SfxCA closed the pipe and every later call read its answer
        // through a NULL pointer. With the line the answer stays with its thread (wine/server/thread.c). Only this entry sets it.
        // lead (2026-10-23, build 594, log 19:20:11): the package then installed and its StartServices action started
        // EABackgroundService.exe, a 64-bit service that imports msi.dll and RstrtMgr.dll; the 64-bit set has neither (c0000135,
        // services.exe 1053, the MSI rolled back with 1627). env.MADEIRA_OPTIN_DLLS gives this session both, and the three DLLs msi.dll
        // imports (tools/build-wine-extra-dlls.sh: OPTIN_WANT). Only this entry sets it.
        // lead (2026-10-23, build 596, log 22:14:28): with the DLLs the service loaded, then the JIT pool ran out at Qt5Core.dll's
        // SHELL32.dll ("[jit-pool] EXHAUSTED (image ...+0x980000)", c0000017): the session's pool had shrunk from 896 to 560 MB ("no
        // hole fits a 896MB pool") and the app's pure x64 images (EABackgroundService.exe alone 19 MB, the Qt DLLs) were copied into
        // it. env.MADEIRA_X64_IMAGE_NOCOPY = 1 runs pure x64 images without a pool copy and pool-split = 1 gives the pool its ~880 MB
        // back (both proven in game lists: BeamNG, Counter-Strike 2). Only this entry sets them.
        // lead (2026-10-24, build 598, the owner's Launch): EADesktop.exe stopped at once with c0000135 and left the empty Wine desktop:
        // its CEF, libcef.dll (64-bit), imports dxva2.dll, which the 64-bit set ships only as dxva2-optin.dll ("Library dxva2.dll (which
        // is needed by ...\\EA Desktop\\libcef.dll) not found"). Every other DLL that CEF imports is in the 64-bit set. Only this entry
        // adds dxva2; the generic dxva2 rule could not, because this entry already sets the key.
        //
        // ajan32 (2026-10-24, branch ajan32/ea-cef; build 598, the owner's Launch with dxva2): EADesktop.exe then started Chromium and died at once. The EA
        // app's CEF 109 asks for four 16 GB PartitionAlloc pools (two copies of PartitionAlloc, chrome_elf.dll's and libcef.dll's, a regular
        // and a BackupRefPtr pool each), the band has four such slots, and the old path gave the third and fourth ask slots that already held
        // the first two pools: two root sets on the same pages, a commit of size 0 at 0x780020d000 and PartitionAlloc's int3
        // (EADesktop.exe); its GPU process then found no slot at all (EACefSubProcess.exe, c0000018). The two lines in launcherOnlyConfig are
        // the installed launcher's alone (the installer's session keeps the band as it was). env.MADEIRA_SC_PA_POOLS = 2 is Social Club's
        // layout 2 (the slots held at boot, the JIT pool's RW alias at 0x7900000000, the emulator's arena higher); env.MADEIRA_EA_CEF = 1
        // gives the four pools their four slots (build/ntdll-unix/virtual_ios.c, "the EA app's Chromium"), runs the browser --single-process
        // (BackupRefPtr off, V8 --jitless, no proxy server, as for Social Club and Steam) and refuses its --type= children
        // (build/ntdll-unix/process_ios.c). env.MADEIRA_EA_CEF_CMDLINE = 0 leaves the command line alone, env.MADEIRA_EA_CEF_FLAGS adds
        // switches. Only this entry sets them; every other program takes the paths it took before.
        // Round 624 (agent A S4, 7NKYWT and H95G25): the installer's managed custom-action hosts (SfxCA) stop on NULL+4; the 32-bit fault
        // report (env.MADEIRA_X86_FAULT_DUMP = 1, log only, three reports at most; build/ntdll-unix/ios_x86_fault_diag.h) names the code
        // and registers in the first run, not only after auto-tune's retry.
        sessionConfig: Self.commonSessionConfig + ["env.LC_ALL = en_US.UTF-8", "env.MADEIRA_APC_BUSY_QUEUE = 1",
                                                   "env.MADEIRA_OPTIN_DLLS = msi,mspatcha,odbccp32,sxs,rstrtmgr,dxva2",
                                                   "env.MADEIRA_WOW_MIN_FREE_GB = 2",
                                                   "env.MADEIRA_TLS_CLEAR_OWN_PROCESS = 1",
                                                   "env.MADEIRA_SPAWN_BLOCK = vc_redist-",
                                                   "env.MADEIRA_EA_CEF_FLAGS = --disable-features=CalculateNativeWinOcclusion --disable-backgrounding-occluded-windows",
                                                   "env.MADEIRA_X64_IMAGE_NOCOPY = 1", "pool-split = 1", "env.MADEIRA_X86_FAULT_DUMP = 1"],
        launcherOnlyConfig: ["env.MADEIRA_SC_PA_POOLS = 2", "env.MADEIRA_EA_CEF = 1"], needsGecko: true, needsArticle: true,
        demandStartServices: ["PnkBstrA", "PnkBstrB"])
}

// MARK: - What a start looks like

/// How the session of a launcher's installer or program starts. madeira-bcd (lead, 2026-10-23, build 594): in a Wine
/// desktop with Windows services first (LibraryEntry.launchMode "desktop", startServices). Build 593 started them on
/// their own, and neither launcher can live without services: Battle.net's AgentHelper found no service manager
/// (\pipe\svcctl), crashed, the Agent quit and the login window never drew (log 16:12, 0 frames in 3 minutes); the EA
/// installer needs the Windows Installer service. On their own, touches also stopped reaching the installer as a mouse
/// while the touch keys were shown. The explorer desktop's Start menu crash of build 592 is fixed in Wine (3eb0623e33d).
struct BCDLauncherStart: Equatable {
    /// The program, C:\...\x.exe.
    let windowsPath: String
    /// Its command line after the program (BCDLauncherSpec.installerArguments / launcherArguments).
    let arguments: String
    /// A launcher starts in explorer's Wine desktop (LibraryEntry.launchMode "desktop").
    let inWineDesktop = true
    /// With the services batch: services.exe first, then the program (the desktop keeps the session alive).
    let startServices = true
    /// The launcher's own lines (BCDLauncherSpec.sessionConfig).
    let sessionConfig: [String]
    /// Round 625: the services its batch sets to start on demand (BCDLauncherSpec.demandStartServices).
    var demandStartServices: [String] = []

    static func installer(_ spec: BCDLauncherSpec) -> BCDLauncherStart {
        BCDLauncherStart(windowsPath: spec.installerWindowsPath, arguments: spec.installerArguments, sessionConfig: spec.sessionConfig + spec.installerOnlyConfig,
                         demandStartServices: spec.demandStartServices)
    }
    static func program(_ spec: BCDLauncherSpec, relative: String) -> BCDLauncherStart {
        BCDLauncherStart(windowsPath: BCDLauncherSpec.windowsPath(relative), arguments: spec.launcherArguments, sessionConfig: spec.programSessionConfig,
                         demandStartServices: spec.demandStartServices)
    }
}

/// The launcher's lines inside an entry's own config text. They sit between two comment lines that are
/// rewritten at every start, so a changed table reaches an entry that was saved before; a key the
/// player set himself outside that block is left alone and the table's line for it is dropped.
/// A launcher entry's Launch arguments (build 599): the table's (Battle.net's Chromium switches) go first at every start, and what
/// the player typed after them is kept, so a switch can be tried by adding it at the end (Chromium takes the last of a repeated
/// switch: `--renderer-process-limit=1` after the table's `=2` wins). An entry whose arguments do not start with the table's gets
/// the table's; a launcher with none leaves the player's as they are.
enum BCDLauncherArguments {
    static func merged(_ current: String, table: String) -> String {
        guard !table.isEmpty else { return current }
        let mine = current.trimmingCharacters(in: .whitespaces)
        return mine.hasPrefix(table) ? mine : table
    }
}

enum BCDLauncherConfig {
    static func opening(_ id: String) -> String { "# BCD launcher settings (\(id)); rewritten at every start" }
    static let closing = "# end of BCD launcher settings"

    /// `text` with the launcher's block replaced by `lines`; nil when nothing is left to say.
    static func merged(_ text: String?, lines: [String], id: String) -> String? {
        var kept: [String] = []
        var inside = false
        for line in (text ?? "").split(omittingEmptySubsequences: false, whereSeparator: { $0 == "\n" }).map(String.init) {
            let trimmed = line.trimmingCharacters(in: .whitespaces)
            if trimmed == opening(id) { inside = true; continue }
            if inside { if trimmed == closing { inside = false }; continue }
            kept.append(line)
        }
        while kept.last?.trimmingCharacters(in: .whitespaces).isEmpty == true { kept.removeLast() }
        let own = Set(kept.compactMap { line -> String? in
            let trimmed = line.trimmingCharacters(in: .whitespaces)
            guard !trimmed.hasPrefix("#"), let eq = trimmed.firstIndex(of: "=") else { return nil }
            return trimmed[..<eq].trimmingCharacters(in: .whitespaces)
        })
        let fresh = lines.filter { line in
            guard let eq = line.firstIndex(of: "=") else { return false }
            return !own.contains(line[..<eq].trimmingCharacters(in: .whitespaces))
        }
        if !fresh.isEmpty { kept += [opening(id)] + fresh + [closing] }
        return kept.isEmpty ? nil : kept.joined(separator: "\n")
    }
}

// MARK: - Files in drive_c

enum BCDLauncherFiles {
    /// `relative` ("Program Files (x86)/Battle.net/x.exe") as it is spelled on disk inside `drive`,
    /// found folder by folder without regard to upper and lower case (Wine makes "Program Files (x86)",
    /// a volume may be case sensitive); nil when it is not a file there, or when it would leave `drive`.
    /// A part that is exactly "*" matches any folder there, the highest version-like name first (lead, 2026-10-24: the EA app
    /// installs into "EA Desktop\<version>\EA Desktop" and reaches it through a junction, "EA Desktop\EA Desktop", which
    /// this Wine keeps as its own reparse data, so the junction is no folder on the iOS side and only the versioned path resolves).
    static func resolve(_ relative: String, in drive: URL, fileManager fm: FileManager = .default) -> String? {
        let parts = relative.split(separator: "/").map(String.init)
        guard !parts.isEmpty, !parts.contains(".."), !parts.contains(".") else { return nil }
        if let star = parts.firstIndex(of: "*") {
            guard star > 0, star < parts.count - 1, parts.filter({ $0 == "*" }).count == 1,
                  let base = resolveFolder(Array(parts[..<star]), in: drive, fileManager: fm) else { return nil }
            let folder = drive.appendingPathComponent(base, isDirectory: true)
            let names = ((try? fm.contentsOfDirectory(atPath: folder.path)) ?? [])
                .filter { name in var dir: ObjCBool = false
                    return fm.fileExists(atPath: folder.appendingPathComponent(name).path, isDirectory: &dir) && dir.boolValue }
                .sorted { $0.compare($1, options: .numeric) == .orderedDescending }
            for name in names {
                if let found = resolve(([base, name] + parts[(star + 1)...]).joined(separator: "/"), in: drive, fileManager: fm) {
                    return found
                }
            }
            return nil
        }
        var url = drive
        var found: [String] = []
        for (index, part) in parts.enumerated() {
            guard let names = try? fm.contentsOfDirectory(atPath: url.path) else { return nil }
            guard let name = names.contains(part) ? part : names.first(where: { $0.caseInsensitiveCompare(part) == .orderedSame }) else { return nil }
            url.appendPathComponent(name, isDirectory: index < parts.count - 1)
            found.append(name)
        }
        // A link out of drive_c is not the launcher.
        guard url.resolvingSymlinksInPath().path.hasPrefix(drive.resolvingSymlinksInPath().path + "/") else { return nil }
        var isFolder: ObjCBool = false
        guard fm.fileExists(atPath: url.path, isDirectory: &isFolder), !isFolder.boolValue else { return nil }
        return found.joined(separator: "/")
    }

    /// A folder path inside `drive`, spelled as on disk (case-insensitive), or nil.
    static func resolveFolder(_ parts: [String], in drive: URL, fileManager fm: FileManager = .default) -> String? {
        var url = drive
        var found: [String] = []
        for part in parts {
            guard part != "*", let names = try? fm.contentsOfDirectory(atPath: url.path),
                  let name = names.contains(part) ? part : names.first(where: { $0.caseInsensitiveCompare(part) == .orderedSame }) else { return nil }
            url.appendPathComponent(name, isDirectory: true)
            found.append(name)
        }
        var dir: ObjCBool = false
        guard fm.fileExists(atPath: url.path, isDirectory: &dir), dir.boolValue,
              url.resolvingSymlinksInPath().path.hasPrefix(drive.resolvingSymlinksInPath().path + "/") else { return nil }
        return found.joined(separator: "/")
    }

    /// The first of the launcher's programs that exists.
    static func installedProgram(_ spec: BCDLauncherSpec, in drive: URL, fileManager fm: FileManager = .default) -> String? {
        for candidate in spec.programs {
            if let found = resolve(candidate, in: drive, fileManager: fm) { return found }
        }
        return nil
    }
}

// MARK: - What the card shows

struct BCDLauncherStatus: Equatable {
    struct Progress: Equatable {
        var received: Int64
        var total: Int64          // 0: not known yet
        /// What is downloading: "the installer", then for some installers "the HTML engine".
        var what = "the installer"
        var fraction: Double { total > 0 ? min(1, Double(received) / Double(total)) : 0 }
    }
    /// The installed launcher's program (relative to drive_c); nil when it is not installed.
    var program: String?
    var installed: Bool { program != nil }
    /// While the installer downloads.
    var download: Progress?
    /// The session is being started (installer or launcher).
    var handingOver = false
    /// Why the last Install or Open could not go on, in plain words.
    var message: String?
    var busy: Bool { download != nil || handingOver }
}

// MARK: - The engine's side

/// Everything a launcher needs from the engine. BCDLaunchersLive carries it out in the app.
@MainActor protocol BCDLauncherEngine: AnyObject {
    /// The installed launcher's program, relative to drive_c as spelled on disk; nil when there is none.
    func installedProgram(_ spec: BCDLauncherSpec) -> String?
    /// nil when a session can start now; otherwise why it cannot, in words (a second session in one run of
    /// BCD needs a restart, and the engine has asked for it already).
    func sessionBlocker() -> String?
    /// Downloads the company's installer into drive_c and checks that it is a Windows program; throws
    /// BCDLauncherError. `progress` gets the bytes so far and the total (0 when not known).
    func downloadInstaller(_ spec: BCDLauncherSpec, progress: @escaping @Sendable (Int64, Int64) -> Void) async throws
    func cancelDownload()
    /// What the installer needs that is not on the device yet, in words for the card ("the HTML engine"); nil when nothing is
    /// missing (BCDLauncherSpec.needsGecko and Wine Gecko not downloaded).
    func missingComponent(_ spec: BCDLauncherSpec) -> String?
    /// Downloads what `missingComponent` named; throws BCDLauncherError. `progress` gets the bytes so far and in all.
    func downloadComponent(_ spec: BCDLauncherSpec, progress: @escaping @Sendable (Int64, Int64) -> Void) async throws
    /// Starts the downloaded installer as the session's first program.
    func startInstaller(_ spec: BCDLauncherSpec)
    /// Starts the installed launcher's program as the session's first program.
    func startLauncher(_ spec: BCDLauncherSpec, program: String)
}

/// Where the model finds its engine: the app's (BCDLaunchersLive, found by its Objective-C name, as this
/// file cannot name it), or the preview app's sample. nil: no launcher chip is shown.
@MainActor enum BCDLauncherSource {
    static var engine: BCDLauncherEngine? =
        (NSClassFromString("BCDLaunchersLive") as? NSObject.Type)?.init() as? BCDLauncherEngine
}

// MARK: - The model

@MainActor final class BCDLauncherModel: ObservableObject {
    static let shared = BCDLauncherModel(engine: BCDLauncherSource.engine)

    @Published private(set) var statuses: [String: BCDLauncherStatus] = [:]
    var engine: BCDLauncherEngine? {
        didSet { refresh() }
    }
    /// How long a started session keeps the card from taking a second tap, in seconds.
    var settleSeconds = 2.0
    private var task: Task<Void, Never>?

    init(engine: BCDLauncherEngine?) {
        self.engine = engine
        refresh()
    }

    /// The Library shows launcher chips only where an engine exists.
    var available: Bool { engine != nil }
    var busy: Bool { statuses.values.contains { $0.busy } }

    func status(_ spec: BCDLauncherSpec) -> BCDLauncherStatus { statuses[spec.id] ?? BCDLauncherStatus() }

    private func change(_ spec: BCDLauncherSpec, _ edit: (inout BCDLauncherStatus) -> Void) {
        var next = status(spec)
        edit(&next)
        if statuses[spec.id] != next { statuses[spec.id] = next }
    }

    /// Reads drive_c again: is each launcher's program there?
    func refresh() {
        guard let engine else { return }
        for spec in BCDLauncherSpec.all {
            let program = engine.installedProgram(spec)
            change(spec) { $0.program = program }
        }
    }

    /// Install: the installer is downloaded, then started as the session's first program.
    func install(_ spec: BCDLauncherSpec) {
        guard let engine, !busy else { return }
        change(spec) { $0.message = nil }
        if let why = engine.sessionBlocker() {
            change(spec) { $0.message = why }
            return
        }
        change(spec) { $0.download = BCDLauncherStatus.Progress(received: 0, total: 0) }
        task = Task { [weak self] in
            do {
                try await engine.downloadInstaller(spec) { received, total in
                    Task { @MainActor in self?.progressed(spec, received, total) }
                }
                try Task.checkCancellation()
                // An installer that shows a web page: Wine Gecko first, with BCD's own downloader (Wine's dialog is slow).
                if let component = engine.missingComponent(spec) {
                    self?.change(spec) { $0.download = BCDLauncherStatus.Progress(received: 0, total: 0, what: component) }
                    try await engine.downloadComponent(spec) { received, total in
                        Task { @MainActor in self?.progressed(spec, received, total) }
                    }
                    try Task.checkCancellation()
                }
                self?.handOver(spec) { engine.startInstaller(spec) }
            } catch {
                self?.failed(spec, error)
            }
        }
    }

    /// Open: the installed launcher is started as the session's first program.
    func open(_ spec: BCDLauncherSpec) {
        guard let engine, !busy else { return }
        change(spec) { $0.message = nil }
        refresh()
        guard let program = status(spec).program else {
            change(spec) { $0.message = "\(spec.title) isn't installed on this device yet." }
            return
        }
        if let why = engine.sessionBlocker() {
            change(spec) { $0.message = why }
            return
        }
        handOver(spec) { engine.startLauncher(spec, program: program) }
    }

    func cancel(_ spec: BCDLauncherSpec) {
        guard status(spec).download != nil else { return }
        engine?.cancelDownload()
        task?.cancel()
    }

    func dismissMessage(_ spec: BCDLauncherSpec) { change(spec) { $0.message = nil } }

    private func progressed(_ spec: BCDLauncherSpec, _ received: Int64, _ total: Int64) {
        guard let before = status(spec).download else { return }
        change(spec) { $0.download = BCDLauncherStatus.Progress(received: received, total: max(0, total), what: before.what) }
    }

    private func handOver(_ spec: BCDLauncherSpec, _ start: () -> Void) {
        change(spec) { $0.download = nil; $0.handingOver = true }
        start()
        let seconds = settleSeconds
        Task { [weak self] in
            try? await Task.sleep(nanoseconds: UInt64(max(0, seconds) * 1_000_000_000))
            self?.change(spec) { $0.handingOver = false }
            self?.refresh()
        }
    }

    private func failed(_ spec: BCDLauncherSpec, _ error: Error) {
        let cancelled = (error as? BCDLauncherError) == .cancelled || error is CancellationError
        change(spec) {
            $0.download = nil
            $0.handingOver = false
            $0.message = cancelled ? nil : (error as? LocalizedError)?.errorDescription ?? error.localizedDescription
        }
    }
}

// SPDX-License-Identifier: GPL-3.0-or-later
// Copyright 2026 125hz
// Copyright 2026 thebcd69 (BCD)
// Madeira Converter Exception: see LICENSE-EXCEPTION.md
// Derived from Onboarding.swift (125hz) by BCD (thebcd69), 2026-10-18: the same setup steps with BCD's
// own look and wording. Not BCD Material: LICENSE-BCD.md's terms cover files BCD wrote from scratch.

import SwiftUI
import UIKit
import UniformTypeIdentifiers

/// BCD's first-run setup (2026-10-18): BCD's own look (BCDStyle) and wording over the setup
/// steps OnboardingRules and OnboardingModel (Onboarding.swift) define. The steps and
/// what each does are unchanged: LocalDevVPN, the JIT method (built-in pairing, a
/// pairing file, StikDebug) and the JIT shortcut, Steam sign-in, Valve's client files
/// for BCD Dock, Wine Mono. Every step can be put off; Settings › JIT and Settings ›
/// Steam reopen the whole setup. Log tag: [onboarding] (no names, tokens or paths).
struct OnboardingView: View {
    @ObservedObject private var model = OnboardingModel.shared
    @ObservedObject private var jit = JITCoordinator.shared
    @ObservedObject private var pairing = OnDevicePairing.shared
    @ObservedObject private var signIn = SteamSignInModel.shared
    @ObservedObject private var dock = MadeiraDockModel.shared
    @ObservedObject private var shortcut = JITNetworkShortcut.shared
    @ObservedObject private var mono = WineMonoModel.shared
    @ObservedObject private var vcrt = VCRuntime.shared
    @State private var showSignIn = false
    /// For BCD's pairing panel (BCDJIT.swift); setup is touch only, so it reads no controller.
    @StateObject private var pairingFocus = BCDFocus(watchControllers: false)
    @State private var importingPairingFile = false
    @State private var pairingImportError: String?
    @State private var jitPath: JITSetupPath?
    /// Checked again whenever BCD returns to the front (from the App Store, say).
    @State private var localDevVPNInstalled = LocalDevVPN.isInstalled
    @Environment(\.scenePhase) private var scenePhase

    enum JITSetupPath: String {
        case onDevice = "in-app", pairingFile = "pairing-file", stikDebug = "stikdebug"
        /// After a method is set up (iOS 27): the JIT shortcut, on its own page.
        case shortcut
    }

    private var device: String { UIDevice.current.userInterfaceIdiom == .pad ? "iPad" : "iPhone" }

    var body: some View {
        NavigationStack {
            ZStack {
                BCDBackdrop()
                ScrollView {
                    VStack(alignment: .leading, spacing: 22) {
                        if let position = OnboardingRules.position(of: model.step, in: model.steps), position.count > 1 {
                            progress(position.number, of: position.count)
                        }
                        switch model.step {
                        case .welcome: welcome
                        case .localDevVPN: localDevVPNPage
                        case .jit: jitPage
                        case .signIn: signInPage
                        case .dockClient: dockClientPage
                        case .wineMono: wineMonoPage
                        case .vcRuntime: vcRuntimePage
                        case .done: donePage
                        }
                    }
                    .padding(.horizontal, 24).padding(.vertical, 28)
                    .frame(maxWidth: 560, alignment: .leading).frame(maxWidth: .infinity)
                    .animation(.default, value: jitPath)
                    .animation(.default, value: pairing.phase)
                }
            }
            .foregroundStyle(BCDStyle.text)
            .tint(BCDStyle.lavender)
        }
        .preferredColorScheme(.dark)
        .interactiveDismissDisabled()
        .sheet(isPresented: $showSignIn) { BCDSteamSignInSheet() }   // BCD's own sign-in (BCDAccountsLive.swift)
        .fileImporter(isPresented: $importingPairingFile,
                      allowedContentTypes: [.propertyList, .data]) { result in
            switch result {
            case .success(let url):
                jit.importPairingFile(url)
                if jit.pairingImported {
                    jit.method = .builtIn
                    pairingImportError = nil
                    LogStore.shared.log("[onboarding] JIT method=built-in pairing=imported")
                }
            case .failure(let failure):
                pairingImportError = failure.localizedDescription
                LogStore.shared.log("[onboarding] pairing import failed")
            }
        }
        .onAppear {
            jit.refreshPairingStatus()
            signIn.refresh()
            dock.refresh()
            localDevVPNInstalled = LocalDevVPN.isInstalled
        }
        .onChange(of: scenePhase) { _, phase in
            if phase == .active { localDevVPNInstalled = LocalDevVPN.isInstalled }
        }
    }

    // MARK: Pieces

    /// "2 / 5" with a bar of segments, lit up to the current step.
    private func progress(_ number: Int, of count: Int) -> some View {
        HStack(spacing: 10) {
            HStack(spacing: 5) {
                ForEach(1...count, id: \.self) { index in
                    Capsule()
                        .fill(index <= number ? AnyShapeStyle(BCDStyle.glow) : AnyShapeStyle(BCDStyle.stroke))
                        .frame(height: 5)
                }
            }
            Text("\(number) / \(count)").font(BCDStyle.label(13)).foregroundStyle(BCDStyle.textSoft).monospacedDigit()
        }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("Step \(number) of \(count)")
    }

    private func header(_ title: String, symbol: String) -> some View {
        VStack(alignment: .leading, spacing: 14) {
            ZStack {
                RoundedRectangle(cornerRadius: 16, style: .continuous).fill(BCDStyle.surfaceRaised)
                RoundedRectangle(cornerRadius: 16, style: .continuous).strokeBorder(BCDStyle.glow, lineWidth: 1.5)
                Image(systemName: symbol).font(.system(size: 26, weight: .semibold))
                    .foregroundStyle(BCDStyle.glow)
            }
            .frame(width: 58, height: 58).accessibilityHidden(true)
            Text(title).font(BCDStyle.title(30)).accessibilityAddTraits(.isHeader)
        }
    }

    private func primary(_ title: String, symbol: String, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Label(title, systemImage: symbol)
        }.buttonStyle(BCDPrimaryButtonStyle())
    }

    private func secondary(_ title: String, action: @escaping () -> Void) -> some View {
        Button(title, action: action).buttonStyle(BCDSecondaryButtonStyle())
    }

    private func note(_ text: LocalizedStringKey, symbol: String) -> some View {
        Label { Text(text).fixedSize(horizontal: false, vertical: true) } icon: { Image(systemName: symbol) }
            .font(BCDStyle.body(14)).foregroundStyle(BCDStyle.textSoft)
    }

    private func status(_ text: String) -> some View {
        Label(text, systemImage: "checkmark.circle.fill")
            .font(BCDStyle.heading(17)).foregroundStyle(BCDStyle.mint)
    }

    private func failure(_ text: String) -> some View {
        Label(text, systemImage: "exclamationmark.triangle.fill")
            .font(BCDStyle.body(15)).foregroundStyle(BCDStyle.alert)
    }

    private func point(_ number: Int, _ text: LocalizedStringKey, done: Bool = false) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: 14) {
            ZStack {
                if done {
                    Circle().fill(BCDStyle.mint.opacity(0.18))
                    Image(systemName: "checkmark").font(.system(size: 12, weight: .heavy)).foregroundStyle(BCDStyle.mint)
                } else {
                    Circle().fill(BCDStyle.glow)
                    Text("\(number)").font(.system(size: 13, weight: .heavy, design: .rounded)).foregroundStyle(BCDStyle.onAccent)
                }
            }
            .frame(width: 26, height: 26).alignmentGuide(.firstTextBaseline) { $0[VerticalAlignment.center] + 5 }
            .accessibilityHidden(true)
            Text(text).font(BCDStyle.body(16)).fixedSize(horizontal: false, vertical: true)
                .foregroundStyle(done ? BCDStyle.textSoft : BCDStyle.text)
        }
        .accessibilityElement(children: .combine)
        .accessibilityValue(done ? Text("Done") : Text(""))
    }

    // MARK: Pages

    private var welcome: some View {
        VStack(alignment: .leading, spacing: 22) {
            BCDLogoMark(size: 84)
            if model.since > 0 {
                // After an update: only the pages added since this device last ran setup.
                Text("Something new to set up").font(BCDStyle.title(34)).accessibilityAddTraits(.isHeader)
                Text("BCD gained a few things since your last setup:").font(BCDStyle.body(18)).foregroundStyle(BCDStyle.textSoft)
            } else {
                Text("Let's get you playing").font(BCDStyle.title(34)).accessibilityAddTraits(.isHeader)
                Text("BCD brings your Windows games to this \(device).").font(BCDStyle.body(19))
                Text("Here is what we'll set up. Every step is optional, and you can come back to any of them later.")
                    .font(BCDStyle.body(15)).foregroundStyle(BCDStyle.textSoft).fixedSize(horizontal: false, vertical: true)
            }
            let pages = model.steps
            let offered: [LocalizedStringKey] =
                (pages.contains(.localDevVPN) ? ["Get LocalDevVPN, the free app BCD uses to turn on JIT"] : [])
                + (pages.contains(.jit) ? ["Pick how this device turns on JIT"] : [])
                + (pages.contains(.signIn) ? ["Connect your Steam account"] : [])
                + (pages.contains(.dockClient) ? ["Fetch the Steam client files that BCD Dock needs"] : [])
                + (pages.contains(.wineMono) ? ["Add Wine Mono for games made with .NET"] : [])
                + (pages.contains(.vcRuntime) ? ["Get Microsoft's Visual C++ runtime, which most games need"] : [])
            VStack(alignment: .leading, spacing: 14) {
                ForEach(offered.indices, id: \.self) { index in point(index + 1, offered[index]) }
            }
            .bcdCard(padding: 18)
            primary("Let's go", symbol: "arrow.right") { model.next() }.padding(.top, 6)
            secondary("Skip for now") { model.skip() }
        }
    }

    /// Offered only when LocalDevVPN was missing when setup opened: every JIT method reaches
    /// this device through it. LocalDevVPN.isInstalled asks iOS whether an app handles
    /// localdevvpn:// (canOpenURL; the scheme is declared in Info.plist), so nothing opens.
    private var localDevVPNPage: some View {
        VStack(alignment: .leading, spacing: 20) {
            header("LocalDevVPN", symbol: "network")
            Text("To switch on JIT, BCD talks to this \(device) over a small network that lives on the device itself. The free LocalDevVPN app provides it: get it from the App Store, then come back here.")
                .font(BCDStyle.body(17)).fixedSize(horizontal: false, vertical: true)
            if localDevVPNInstalled {
                status("LocalDevVPN is ready")
                primary("Continue", symbol: "arrow.right") { model.next() }
            } else {
                primary("Open the App Store", symbol: "arrow.down.app") {
                    LogStore.shared.log("[onboarding] LocalDevVPN app-store")
                    UIApplication.shared.open(LocalDevVPN.appStore)
                }
                secondary("Later") { model.next() }
            }
        }
    }

    // JIT: pick one of three methods, then follow its numbered steps.

    private var pairedOnDevice: Bool { jit.pairingImported && jit.pairingSource == .onDevice }
    private var fileImported: Bool { jit.pairingImported && jit.pairingSource == .imported }

    @ViewBuilder private var jitPage: some View {
        switch jitPath {
        case nil: jitChoices
        case .onDevice: onDeviceGuide
        case .pairingFile: pairingFileGuide
        case .stikDebug: stikDebugGuide
        case .shortcut: shortcutPage
        }
    }

    private var jitChoices: some View {
        VStack(alignment: .leading, spacing: 20) {
            header("Turn on JIT", symbol: "bolt.fill")
            Text("Windows games are made of x86 code that BCD translates while they run, and iOS only allows that with JIT. Choose how this \(device) will get it.")
                .font(BCDStyle.body(17)).fixedSize(horizontal: false, vertical: true)
            VStack(spacing: 12) {
                jitChoice("Built-in", symbol: "iphone.radiowaves.left.and.right",
                          detail: !OnDevicePairing.isSupported ? "Requires iOS 27 or newer."
                              : pairedOnDevice ? "This \(device) is paired."
                              : "Pair once in Settings. No computer involved.",
                          done: pairedOnDevice, enabled: OnDevicePairing.isSupported) { choose(.onDevice) }
                jitChoice("Built-in, with a pairing file", symbol: "doc.badge.plus",
                          detail: fileImported ? "Pairing file added." : "Bring a pairing file made on a computer.",
                          done: fileImported) { choose(.pairingFile) }
                jitChoice("StikDebug", symbol: "ant",
                          detail: "Let the StikDebug app switch JIT on.",
                          done: jit.method == .stikDebug) { choose(.stikDebug) }
            }
            secondary("Decide later") { model.next() }
        }
    }

    private var onDeviceGuide: some View {
        VStack(alignment: .leading, spacing: 20) {
            header("Pair this \(device)", symbol: "iphone.radiowaves.left.and.right")
            VStack(alignment: .leading, spacing: 16) {
                point(1, "Connect to Wi-Fi, tap **Start pairing**, and allow access to the local network.",
                      done: pairing.phase != .idle || pairedOnDevice)
                point(2, "In Settings › Privacy & Security › Developer Mode, scroll to the bottom and tap **Pair with \(OnDevicePairing.hostName)**.",
                      done: pairing.isShowingPin || pairedOnDevice)
                point(3, "Type in the code BCD shows you. You'll also find it in the banner at the top of the screen and in a notification.",
                      done: pairedOnDevice)
            }
            .bcdCard(padding: 18)
            if pairedOnDevice {
                status("This \(device) is paired")
                vpnNote
                primary("Continue", symbol: "arrow.right") { useBuiltIn() }
                secondary("Pair again") { startPairing() }
            } else {
                primary("Start pairing", symbol: "dot.radiowaves.left.and.right") { startPairing() }
                    .disabled(pairing.active)
                BCDJITPairingPanel(run: BCDJITLive.pairingRun(), idPrefix: "setup.jit.") { pairing.cancel() }
                    .environmentObject(pairingFocus)
            }
            secondary("See the other methods") { leaveGuide() }
        }
    }

    private var pairingFileGuide: some View {
        VStack(alignment: .leading, spacing: 20) {
            header("Add a pairing file", symbol: "doc.badge.plus")
            VStack(alignment: .leading, spacing: 16) {
                point(1, "Create this \(device)'s pairing file on a computer by following the [StikDebug pairing-file guide](https://github.com/StikDebug/StikDebug-Guide/blob/main/pairing_file.md).",
                      done: fileImported)
                point(2, "Move it onto this \(device) through Files or AirDrop.", done: fileImported)
                point(3, "Tap **Choose pairing file** and select it.", done: fileImported)
            }
            .bcdCard(padding: 18)
            note("BCD stores the pairing file in this \(device)'s Keychain.", symbol: "lock.fill")
            if fileImported {
                status("Pairing file added")
                vpnNote
                primary("Continue", symbol: "arrow.right") { useBuiltIn() }
                secondary("Pick a different file") { importPairingFile() }
            } else {
                primary("Choose pairing file", symbol: "folder") { importPairingFile() }
            }
            if let error = pairingImportError ?? jit.error {
                failure(error)
            }
            secondary("See the other methods") { leaveGuide() }
        }
    }

    private var stikDebugGuide: some View {
        VStack(alignment: .leading, spacing: 20) {
            header("Use StikDebug", symbol: "ant")
            VStack(alignment: .leading, spacing: 16) {
                point(1, "Install [StikDebug](https://github.com/StikDebug/StikDebug/releases/latest) and add this \(device)'s pairing file to it.")
                point(2, "Install [LocalDevVPN](https://apps.apple.com/us/app/localdevvpn/id6755608044) and connect it.")
                point(3, "Each time you play, BCD hands over to StikDebug for JIT and then comes back on its own.")
            }
            .bcdCard(padding: 18)
            primary("Use StikDebug", symbol: "arrow.right") {
                jit.method = .stikDebug
                LogStore.shared.log("[onboarding] JIT method=StikDebug")
                finishJIT()
            }
            secondary("See the other methods") { leaveGuide() }
        }
    }

    /// Only on iOS 26: from iOS 27 the page after the guide (the shortcut) takes care of
    /// LocalDevVPN.
    @ViewBuilder private var vpnNote: some View {
        if !JITShortcutFile.supported {
            note("Connect [LocalDevVPN](https://apps.apple.com/us/app/localdevvpn/id6755608044) before you start a game: BCD turns JIT on through it.",
                 symbol: "network")
        }
    }

    private func jitChoice(_ title: String, symbol: String, detail: String, done: Bool, enabled: Bool = true,
                           action: @escaping () -> Void) -> some View {
        Button(action: action) {
            HStack(spacing: 14) {
                Image(systemName: symbol).font(.system(size: 22, weight: .semibold))
                    .foregroundStyle(BCDStyle.glow).frame(width: 36)
                VStack(alignment: .leading, spacing: 4) {
                    Text(title).font(BCDStyle.heading(17)).foregroundStyle(BCDStyle.text)
                    Text(detail).font(BCDStyle.body(14)).foregroundStyle(BCDStyle.textSoft)
                        .fixedSize(horizontal: false, vertical: true)
                }
                Spacer(minLength: 8)
                Image(systemName: done ? "checkmark.circle.fill" : "chevron.right")
                    .foregroundStyle(done ? BCDStyle.mint : BCDStyle.textSoft)
            }
            .bcdCard(padding: 16)
            .contentShape(RoundedRectangle(cornerRadius: 20, style: .continuous))
        }
        .buttonStyle(.plain).disabled(!enabled).opacity(enabled ? 1 : 0.45)
    }

    private func choose(_ way: JITSetupPath) {
        pairingImportError = nil
        LogStore.shared.log("[onboarding] JIT way=\(way.rawValue)")
        jitPath = way
    }

    private func leaveGuide() {
        if pairing.active { pairing.cancel() }
        jitPath = nil
    }

    private func startPairing() {
        pairingImportError = nil
        LogStore.shared.log("[onboarding] JIT in-app pairing started")
        pairing.start()
    }

    private func importPairingFile() {
        pairingImportError = nil
        importingPairingFile = true
    }

    private func useBuiltIn() {
        jit.method = .builtIn
        LogStore.shared.log("[onboarding] JIT method=built-in")
        finishJIT()
    }

    /// A method is set up: the JIT shortcut's page on iOS 27, else the next step.
    private func finishJIT() {
        if JITShortcutFile.supported, JITShortcutFile.url != nil {
            jitPath = .shortcut
        } else {
            model.next()
        }
    }

    /// The JIT shortcut (iOS 27; JITShortcutFile), on its own page so each JIT guide fits
    /// on one screen. Added from its iCloud link (straight to Add Shortcut) or, offline,
    /// from the local copy through the share sheet; then switched on. It drives
    /// LocalDevVPN with LocalDevVPN's own Shortcuts action.
    private var shortcutPage: some View {
        VStack(alignment: .leading, spacing: 20) {
            header("Make it automatic", symbol: "bolt.horizontal.circle")
            Text("The JIT shortcut takes care of the network for you. When you tap Enable JIT it connects LocalDevVPN, switches Cellular Data off if there's no Wi-Fi, and puts both back, together with any VPN you had on, as soon as the game is running.")
                .font(BCDStyle.body(17)).fixedSize(horizontal: false, vertical: true)
            VStack(alignment: .leading, spacing: 16) {
                point(1, "Install [LocalDevVPN](https://apps.apple.com/us/app/localdevvpn/id6755608044).",
                      done: LocalDevVPN.isInstalled)
                point(2, "Tap **Add the shortcut**, then **Add Shortcut** in the Shortcuts app.")
                point(3, "Switch on **Use for JIT** below.", done: shortcut.enabled)
            }
            .bcdCard(padding: 18)
            Button {
                LogStore.shared.log("[jit-shortcut] add: iCloud link")
                UIApplication.shared.open(JITShortcutFile.iCloudLink)
            } label: {
                Label("Add the shortcut", systemImage: "plus.square.on.square")
            }
            .buttonStyle(BCDOutlineButtonStyle())
            if let url = JITShortcutFile.url {
                ShareLink(item: url) {
                    Text("Offline? Share the local copy instead, then pick Shortcuts from the list.")
                        .font(BCDStyle.body(13)).foregroundStyle(BCDStyle.lavender).frame(maxWidth: .infinity)
                }
            }
            Toggle("Use for JIT", isOn: $shortcut.enabled)
                .font(BCDStyle.label(16)).tint(BCDStyle.fuchsia)
                .bcdCard(padding: 14)
            primary("Continue", symbol: "arrow.right") {
                LogStore.shared.log("[onboarding] JIT shortcut on=\(shortcut.enabled ? 1 : 0)")
                model.next()
            }
        }
    }

    private var signInPage: some View {
        VStack(alignment: .leading, spacing: 20) {
            header("Connect Steam", symbol: "person.crop.circle.badge.checkmark")
            Text(model.steps.contains(.dockClient)
                 ? "When a game starts through BCD Dock, BCD passes this sign-in to Valve's own Steam client, so Steam can check that the game is yours."
                 : "With your Steam account connected, BCD starts your Steam games as you.")
                .font(BCDStyle.body(17)).fixedSize(horizontal: false, vertical: true)
            VStack(alignment: .leading, spacing: 10) {
                note("Your password goes to Steam alone. BCD never stores it.", symbol: "lock.fill")
                note("BCD keeps the sign-in in this device's Keychain until you sign out.", symbol: "iphone")
            }
            if let name = signIn.accountName {
                status("Connected as \(name)")
                primary("Continue", symbol: "arrow.right") { model.next() }
                secondary("Switch account") {
                    LogStore.shared.log("[onboarding] sign-in replaced")
                    signIn.signOut(); showSignIn = true
                }
            } else {
                primary("Sign in to Steam", symbol: "person.crop.circle") { showSignIn = true }
                secondary("Later") { model.next() }
            }
        }
    }

    private var dockClientPage: some View {
        VStack(alignment: .leading, spacing: 20) {
            header("Get BCD Dock ready", symbol: "shippingbox")
            Text("BCD Dock launches your installed Steam games with Valve's own Steam client, minus its desktop window. For that it needs a handful of client files, which BCD fetches straight from Valve.")
                .font(BCDStyle.body(17)).fixedSize(horizontal: false, vertical: true)
            if dock.clientInstalled {
                status("Steam client files are in place")
                primary("Continue", symbol: "arrow.right") { model.next() }
            } else {
                note("Around 73 MB from Valve's update servers, every file checked against a known SHA-256 hash. Nothing runs in Windows while this happens.",
                     symbol: "checkmark.shield")
                if dock.preparing {
                    HStack(spacing: 12) {
                        ProgressView().tint(BCDStyle.fuchsia)
                        Text(dock.progress).font(BCDStyle.body(15)).foregroundStyle(BCDStyle.textSoft)
                    }
                    .bcdCard(padding: 14)
                } else {
                    if let error = dock.error {
                        failure(error)
                    }
                    primary(dock.error == nil ? "Download the files" : "Try again", symbol: "arrow.down.circle.fill") {
                        LogStore.shared.log("[onboarding] components download")
                        dock.prepareClient()
                    }
                }
                // A running download keeps going; Settings › Steam › BCD Dock shows it.
                secondary("Later") { model.next() }
            }
        }
    }

    /// Wine Mono, offered when this device has none: games made with .NET need it. The
    /// download keeps going if the page is left; Settings › .NET Framework shows it.
    private var wineMonoPage: some View {
        VStack(alignment: .leading, spacing: 20) {
            header(".NET games", symbol: "shippingbox.and.arrow.backward")
            Text("Some games are written for Microsoft's .NET Framework. BCD plays them with Wine Mono, the open-source .NET runtime from the Wine project.")
                .font(BCDStyle.body(17)).fixedSize(horizontal: false, vertical: true)
            if mono.installed {
                status("Wine Mono is installed")
                primary("Continue", symbol: "arrow.right") { model.next() }
            } else {
                note("It comes from WineHQ: roughly 42 MB to download and about 130 MB on the device. Most games don't need it, and Settings can add it at any time.",
                     symbol: "info.circle")
                switch mono.phase {
                case .downloading(let f), .installing(let f):
                    VStack(alignment: .leading, spacing: 8) {
                        ProgressView(value: f).tint(BCDStyle.fuchsia)
                        Text(mono.status).font(BCDStyle.body(14)).foregroundStyle(BCDStyle.textSoft).monospacedDigit()
                    }
                    .bcdCard(padding: 14)
                case .idle:
                    if let error = mono.error {
                        failure(error)
                    }
                    primary(mono.error == nil ? "Download Wine Mono" : "Try again", symbol: "arrow.down.circle.fill") {
                        LogStore.shared.log("[onboarding] Wine Mono download")
                        mono.install()
                    }
                }
                secondary("Later") { model.next() }
            }
        }
    }

    /// Microsoft's Visual C++ runtime, for builds that do not carry it (BCD's public ones):
    /// downloaded from Microsoft by VCRuntime.shared, after the person accepts its licence.
    private var vcRuntimePage: some View {
        VStack(alignment: .leading, spacing: 20) {
            header("Visual C++ runtime", symbol: "cpu")
            Text("Most Windows games are built with Microsoft's Visual C++ and won't start without its runtime files. BCD can fetch them from Microsoft and keep them on this device.")
                .font(BCDStyle.body(17)).fixedSize(horizontal: false, vertical: true)
            if vcrt.isAvailable {
                status(vcrt.sourceDescription)
                primary("Continue", symbol: "arrow.right") { model.next() }
            } else {
                note("About 25 MB, straight from Microsoft's servers. BCD keeps only the runtime's DLLs, exactly as Microsoft ships them, and runs nothing from the download. Downloading means you accept Microsoft's license terms for the runtime.",
                     symbol: "info.circle")
                Link(destination: URL(string: "https://visualstudio.microsoft.com/license-terms/vs2022-cruntime/")!) {
                    Label("Read Microsoft's license terms", systemImage: "doc.text")
                        .font(BCDStyle.label(15)).foregroundStyle(BCDStyle.lavender)
                }
                switch vcrt.status {
                case .downloading(let received, let expected):
                    VStack(alignment: .leading, spacing: 8) {
                        ProgressView(value: expected > 0 ? Double(received) / Double(expected) : 0).tint(BCDStyle.fuchsia)
                        Text(expected > 0 ? "\(received / 1_000_000) of \(expected / 1_000_000) MB" : "Starting the download…")
                            .font(BCDStyle.body(14)).foregroundStyle(BCDStyle.textSoft).monospacedDigit()
                    }
                    .bcdCard(padding: 14)
                    secondary("Cancel") { vcrt.cancel() }
                case .installing(let fraction):
                    VStack(alignment: .leading, spacing: 8) {
                        ProgressView(value: fraction).tint(BCDStyle.fuchsia)
                        Text("Unpacking and checking the files…").font(BCDStyle.body(14)).foregroundStyle(BCDStyle.textSoft)
                    }
                    .bcdCard(padding: 14)
                default:
                    if let error = vcrt.lastError {
                        failure(error)
                    }
                    primary(vcrt.lastError == nil ? "Accept and download" : "Try again", symbol: "arrow.down.circle.fill") {
                        LogStore.shared.log("[onboarding] VC++ runtime download")
                        vcrt.install()
                    }
                }
                if !vcrt.isBusy {
                    secondary("Later") { model.next() }
                }
            }
        }
    }

    private var donePage: some View {
        VStack(alignment: .leading, spacing: 20) {
            BCDLogoMark(size: 72)
            Text("Ready to play").font(BCDStyle.title(34)).accessibilityAddTraits(.isHeader)
            VStack(alignment: .leading, spacing: 12) {
                if model.steps.contains(.jit) {
                    note("Settings › JIT lets you switch methods or add a pairing file.", symbol: "bolt.fill")
                }
                if model.steps.contains(.dockClient) {
                    note("Settings › Steam › BCD Dock shows the Steam games installed in BCD and launches them.", symbol: "shippingbox")
                }
                if model.steps.contains(.wineMono) {
                    note("Settings › .NET Framework adds or removes Wine Mono.", symbol: "shippingbox.and.arrow.backward")
                }
                if model.steps.contains(.vcRuntime) && !vcrt.isAvailable {
                    note("Games that need the Visual C++ runtime won't start until it's downloaded. Settings › Visual C++ runtime gets it any time.", symbol: "cpu")
                }
            }
            Text("Want to change something? Run this setup again from Settings › JIT or Settings › Steam.")
                .font(BCDStyle.body(15)).foregroundStyle(BCDStyle.textSoft).fixedSize(horizontal: false, vertical: true)
            primary("Open my library", symbol: "square.grid.2x2.fill") { model.finish() }
        }
    }
}

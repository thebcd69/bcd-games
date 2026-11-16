// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

import Foundation
import UIKit
import ObjectiveC.runtime
import UniformTypeIdentifiers

// In-place folder picking for the External SSD feature (Settings > Storage > External SSD).
//
// The problem (proven, iOS 26.3 and 18.1 runtime + fileproviderd/DocumentManager disassembly,
// docs/research/external-ssd-2026-10-17/SSD-BULGULARI-VE-COZUM-2026-10-27.md and the 2026-10-28
// report): when BCD is signed with a provisioning profile whose App ID differs from Info.plist's
// CFBundleIdentifier, the iOS folder picker's blue "Open" button silently does nothing. The picker
// asks `fileproviderd` to extend a security-scoped bookmark to a consumer named by
// `DOCConfiguration.hostIdentifier`, which `-[DOCConfiguration init]` sets to
// `[[NSBundle mainBundle] bundleIdentifier]`. `fileproviderd` keys the grant on that consumer id and
// refuses it when it does not match the process's real signed identity, so the bookmark comes back
// nil, the picked item is dropped, and the delegate is never called (Cancel still works). Installs
// whose sideloader rewrites CFBundleIdentifier to equal the signed App ID (SideStore/AltStore free
// profiles, Feather) have no mismatch and already work -- which is why five tester logs show the pick
// succeeding, all on 7-day development profiles.
//
// Verified mechanism:
//   - `-[DOCConfiguration init]` sets `_hostIdentifier = [[NSBundle mainBundle] bundleIdentifier]`
//     (otool: the init calls -mainBundle, -bundleIdentifier, -setHostIdentifier:).
//   - A folder pick with `asCopy: false` leaves `interactionMode == 1`, so
//     `-[DOCConfiguration interactionModeForPreparing]` returns 1 (OpenInPlace): the real folder URL
//     is returned with NO copy to the app's `-Inbox`. (Confirmed on device runtimes by a headless
//     probe: interactionMode 1 -> interactionModeForPreparing 1.) So the owner's "read in place, never
//     copy" rule is met by the stock in-place path; only the host identity is wrong.
//
// The fix: while the External SSD picker is on screen, and only on an install with a real identity
// mismatch, substitute the process's true App ID (read once from the `application-identifier`
// entitlement) for the auto-assigned CFBundleIdentifier in the picker's `DOCConfiguration`. Nothing
// else changes: `asCopy: false` stays, no `-Inbox`, no copy to the device, and this never touches
// `Bundle.main.bundleIdentifier` (StikJITHelper.swift and MemoryPool.m read that) or the engine.
//
// Opt-in by use: the hooks are installed the first time the SSD picker is opened on a mismatched
// install, and are inert at every other moment and on every working install (the guard returns before
// anything is installed or logged). Selectable with bcd.cfg `external-picker-fix` so a single device
// test can choose the strategy if more than one is plausible.

/// The pure identity decision, split out so a host check can exercise it without UIKit.
/// No App ID value is ever logged; only the three booleans below reach the log.
struct BCDPickerIdentityDecision: Equatable {
    /// The signed App ID (team prefix stripped) equals CFBundleIdentifier: this install already works.
    let matches: Bool
    /// The signed App ID is a wildcard (ends with "*"): it already claims the bundle id's namespace.
    let wildcard: Bool
    /// The host identifier to force for the picker, or nil to leave it untouched (working/wildcard/unknown).
    let targetHostID: String?

    /// `application-identifier` entitlement (e.g. "ABCDE12345.com.example.app" or "ABCDE12345.*") vs the
    /// bundle id. Returns a non-nil `targetHostID` only for a real, specific mismatch -- never for a
    /// working install, a wildcard profile, an empty bundle id, or an unreadable entitlement.
    static func decide(entitlementAppID: String?, bundleID: String) -> BCDPickerIdentityDecision {
        guard let raw = entitlementAppID, !raw.isEmpty else {
            return BCDPickerIdentityDecision(matches: true, wildcard: false, targetHostID: nil)
        }
        let stripped = strip(raw)
        let wildcard = stripped.hasSuffix("*")
        let matches = stripped == bundleID
        let target = (!wildcard && !matches && !bundleID.isEmpty && !stripped.isEmpty) ? stripped : nil
        return BCDPickerIdentityDecision(matches: matches, wildcard: wildcard, targetHostID: target)
    }

    /// Drop the "<TeamID>." prefix of an application-identifier; a value without a dot is kept whole.
    static func strip(_ appID: String) -> String {
        guard let dot = appID.firstIndex(of: ".") else { return appID }
        return String(appID[appID.index(after: dot)...])
    }
}

/// Which repair the picker applies. The default `auto` is the proven host-identifier fix.
enum BCDPickerFixStrategy: String {
    /// Correct the picker's host identifier (the proven in-place mechanism).
    case auto
    /// Also set the picker's `_ignoreApplicationEntitlementForImport` ivar (Apple's own bypass), for the
    /// owner's single device test to fall back to if the host-identifier fix is not enough.
    case ignoreEntitlement = "ignore-entitlement"
    /// No runtime change at all: exactly today's behaviour.
    case off

    static var current: BCDPickerFixStrategy {
        switch (MadeiraConfig.get("external-picker-fix") ?? "").lowercased() {
        case "off": return .off
        case "ignore-entitlement", "ignore", "entitlement": return .ignoreEntitlement
        default: return .auto
        }
    }
}

final class BCDExternalPickerFix {
    static let shared = BCDExternalPickerFix()

    let bundleID: String
    let inLiveContainer: Bool
    private let decision: BCDPickerIdentityDecision
    /// True only while the External SSD picker is on screen. Read from the swizzled setter.
    nonisolated(unsafe) fileprivate static var sessionActive = false

    private let lock = NSLock()
    private var installed = false

    private init() {
        bundleID = Bundle.main.bundleIdentifier ?? ""
        inLiveContainer = getenv("LC_HOME_PATH") != nil
        decision = BCDPickerIdentityDecision.decide(entitlementAppID: Self.entitlementApplicationIdentifier(),
                                                    bundleID: bundleID)
    }

    /// For the startup `[device] signing:` line. No App ID value, only booleans.
    var diagnosticSuffix: String {
        "appid-match=\(decision.matches ? 1 : 0) wildcard=\(decision.wildcard ? 1 : 0) lc=\(inLiveContainer ? 1 : 0)"
    }

    /// Whether this install needs (and the config allows) a repair.
    var needsFix: Bool {
        guard BCDPickerFixStrategy.current != .off else { return false }
        return decision.targetHostID != nil || inLiveContainer
    }

    /// Arm the in-place fix around the picker. A no-op (no install, no log) on a working install or when
    /// the fix is switched off, so nothing changes for anyone who does not need it.
    func beginSession() {
        guard needsFix else { return }
        installIfNeeded()
        Self.sessionActive = true
        LogStore.shared.log("[ext-storage] picker in-place fix armed strategy=\(BCDPickerFixStrategy.current.rawValue) "
                            + diagnosticSuffix)
    }

    func endSession() { Self.sessionActive = false }

    /// The guidance to show when the picker closed without an answer on a mismatched install, or nil when
    /// the install is not the reason (so working installs never see it).
    var mismatchHelp: String? {
        guard decision.targetHostID != nil || (inLiveContainer && !decision.matches) else { return nil }
        return "If Open did nothing: the tool that signed BCD kept its bundle id different from the "
             + "certificate's App ID, and iOS may refuse the folder. Re-sign BCD with the option that sets the "
             + "bundle id to the certificate's App ID, or install it with SideStore, AltStore or Feather, then try again."
    }

    // MARK: install

    private func installIfNeeded() {
        lock.lock(); defer { lock.unlock() }
        guard !installed else { return }
        installed = true
        swizzleSetHostIdentifier()
        swizzlePickerViewWillAppear()
    }

    /// Replace `-[DOCConfiguration setHostIdentifier:]`. The replacement rewrites only when the picker
    /// session is armed, the install has a real mismatch, and the incoming value is the auto-assigned
    /// bundle id -- so every other caller, and every call outside an armed session, is left exactly as it
    /// was. This is the document-picker configuration object, never NSBundle.
    private func swizzleSetHostIdentifier() {
        guard let cls = NSClassFromString("DOCConfiguration") else { return }
        let sel = NSSelectorFromString("setHostIdentifier:")
        guard let method = class_getInstanceMethod(cls, sel) else { return }
        typealias Fn = @convention(c) (AnyObject, Selector, NSString?) -> Void
        let orig = unsafeBitCast(method_getImplementation(method), to: Fn.self)
        let bundleID = self.bundleID
        let target = decision.targetHostID
        let block: @convention(block) (AnyObject, NSString?) -> Void = { obj, hostID in
            if Self.sessionActive, let target {
                let incoming = hostID as String?
                if incoming == nil || incoming == bundleID {
                    orig(obj, sel, target as NSString)
                    return
                }
            }
            orig(obj, sel, hostID)
        }
        method_setImplementation(method, imp_implementationWithBlock(block))
    }

    /// Repair the picker's configuration just before it appears (and before the browser is sent over XPC),
    /// as a belt-and-suspenders companion to the setter hook and to carry the LiveContainer / ignore-
    /// entitlement strategies. Gated on an armed session, so it does nothing outside an SSD pick.
    private func swizzlePickerViewWillAppear() {
        let sel = #selector(UIViewController.viewWillAppear(_:))
        guard let method = class_getInstanceMethod(UIDocumentPickerViewController.self, sel) else { return }
        typealias Fn = @convention(c) (UIDocumentPickerViewController, Selector, Bool) -> Void
        let orig = unsafeBitCast(method_getImplementation(method), to: Fn.self)
        let block: @convention(block) (UIDocumentPickerViewController, Bool) -> Void = { [weak self] picker, animated in
            if Self.sessionActive { self?.repairPicker(picker) }
            orig(picker, sel, animated)
        }
        method_setImplementation(method, imp_implementationWithBlock(block))
    }

    private func repairPicker(_ picker: UIDocumentPickerViewController) {
        if let config = pickerConfiguration(picker) {
            if let target = decision.targetHostID, config.responds(to: NSSelectorFromString("setHostIdentifier:")) {
                config.setValue(target, forKey: "hostIdentifier")
            }
            // LiveContainer rewrites a folder request to [.item, .folder], which leaves forPickingFolders NO.
            // Restore the folder picker only there; never changes a normal install.
            if inLiveContainer {
                if config.responds(to: NSSelectorFromString("setForPickingFolders:")) {
                    config.setValue(true, forKey: "forPickingFolders")
                }
                if config.responds(to: NSSelectorFromString("setDocumentContentTypes:")) {
                    config.setValue([UTType.folder], forKey: "documentContentTypes")
                }
            }
        }
        if BCDPickerFixStrategy.current == .ignoreEntitlement {
            let object = picker as NSObject
            if class_getInstanceVariable(type(of: object), "_ignoreApplicationEntitlementForImport") != nil {
                object.setValue(true, forKey: "_ignoreApplicationEntitlementForImport")
            }
        }
    }

    private func pickerConfiguration(_ picker: UIDocumentPickerViewController) -> NSObject? {
        let object = picker as NSObject
        if object.responds(to: NSSelectorFromString("configuration")),
           let config = object.value(forKey: "configuration") as? NSObject {
            return config
        }
        if class_getInstanceVariable(type(of: object), "_configuration") != nil,
           let config = object.value(forKey: "_configuration") as? NSObject {
            return config
        }
        return nil
    }

    // MARK: identity

    /// The process's `application-identifier` entitlement, read through SecTask. Resolved with dlsym so the
    /// Security symbols are not linked at build time. The return value is the owner's real App ID on his
    /// builds, so it is used only for the decision above and never written to the log or the repository.
    private static func entitlementApplicationIdentifier() -> String? {
        typealias CreateFn = @convention(c) (CFAllocator?) -> UnsafeMutableRawPointer?
        typealias CopyFn = @convention(c) (UnsafeMutableRawPointer, CFString, UnsafeMutablePointer<Unmanaged<CFError>?>?) -> Unmanaged<CFTypeRef>?
        let rtldDefault = UnsafeMutableRawPointer(bitPattern: -2)
        guard let createSym = dlsym(rtldDefault, "SecTaskCreateFromSelf"),
              let copySym = dlsym(rtldDefault, "SecTaskCopyValueForEntitlement") else { return nil }
        let create = unsafeBitCast(createSym, to: CreateFn.self)
        let copy = unsafeBitCast(copySym, to: CopyFn.self)
        guard let task = create(nil) else { return nil }
        defer { Unmanaged<AnyObject>.fromOpaque(task).release() }
        guard let value = copy(task, "application-identifier" as CFString, nil)?.takeRetainedValue() as? String,
              !value.isEmpty else { return nil }
        return value
    }
}

// MARK: - UIKit folder picker (replaces SwiftUI .fileImporter for Settings > Storage > External drives)
//
// On some devices SwiftUI's `.fileImporter(allowedContentTypes: [.folder])` on the External SSD group never presents:
// the log shows "[ext-storage] picker opened" and never "closed", because the `isPresented` flag stays true while no
// picker appears (the saves backup's .fileImporter on the same page still works). Presenting the same system picker from
// UIKit, as the controls-share import already does, does not depend on SwiftUI's presentation of the importer.
// `asCopy: false` keeps the in-place read the External SSD feature needs (no -Inbox copy).

@MainActor enum BCDExternalFolderPicker {
    /// Keeps the delegate alive while the picker is up.
    private static var pending: Delegate?

    /// Presents the folder picker. `answered` gets the chosen folder; `closed` runs once when the picker is gone
    /// (after an answer, Cancel or a swipe down). Returns false when there was no screen to present from.
    @discardableResult
    static func present(answered: @escaping (URL) -> Void, closed: @escaping () -> Void) -> Bool {
        guard let top = topController() else { return false }
        let picker = UIDocumentPickerViewController(forOpeningContentTypes: [.folder], asCopy: false)
        picker.allowsMultipleSelection = false
        let delegate = Delegate(answered: answered, closed: closed)
        picker.delegate = delegate
        picker.presentationController?.delegate = delegate
        pending = delegate
        top.present(picker, animated: true)
        return true
    }

    private static func topController() -> UIViewController? {
        let scenes = UIApplication.shared.connectedScenes.compactMap { $0 as? UIWindowScene }
        let scene = scenes.first { $0.activationState == .foregroundActive } ?? scenes.first
        var top = scene?.keyWindow?.rootViewController
        while let presented = top?.presentedViewController, !presented.isBeingDismissed { top = presented }
        return top
    }

    private final class Delegate: NSObject, UIDocumentPickerDelegate, UIAdaptivePresentationControllerDelegate {
        private var answered: ((URL) -> Void)?
        private var closed: (() -> Void)?
        init(answered: @escaping (URL) -> Void, closed: @escaping () -> Void) {
            self.answered = answered; self.closed = closed
        }
        private func finish() {
            let done = closed
            answered = nil; closed = nil
            DispatchQueue.main.async { MainActor.assumeIsolated {
                BCDExternalFolderPicker.pending = nil
                done?()
            } }
        }
        func documentPicker(_ controller: UIDocumentPickerViewController, didPickDocumentsAt urls: [URL]) {
            if let url = urls.first, let answered { answered(url) }
            finish()
        }
        func documentPickerWasCancelled(_ controller: UIDocumentPickerViewController) { finish() }
        func presentationControllerDidDismiss(_ presentationController: UIPresentationController) { if closed != nil { finish() } }
    }
}

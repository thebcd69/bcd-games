// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

import UIKit
import WebKit

/// Blizzard hosts password entry, passkeys and Authenticator approval. The web session is
/// ephemeral; only secure cookies for the account portal go to the account's Keychain item.
/// No developer client secret or another application's OAuth identity is embedded in BCD.
@MainActor final class BCDBattleNetSignIn: UIViewController, WKNavigationDelegate, WKUIDelegate {
    private let accept: (BCDBattleNetCredential) async throws -> Void
    private let closed: () -> Void
    private var web: WKWebView!
    private var popups: [WKWebView] = []
    private let status = UILabel()
    private var checking: Task<Void, Never>?
    private var finished = false

    static func present(accept: @escaping (BCDBattleNetCredential) async throws -> Void,
                        closed: @escaping () -> Void) {
        guard let top = BCDWebSignIn.topController() else { closed(); return }
        let sheet = BCDBattleNetSignIn(accept: accept, closed: closed)
        let nav = UINavigationController(rootViewController: sheet)
        nav.modalPresentationStyle = UIDevice.current.userInterfaceIdiom == .pad ? .formSheet : .fullScreen
        nav.isModalInPresentation = true
        top.present(nav, animated: true)
    }

    private init(accept: @escaping (BCDBattleNetCredential) async throws -> Void, closed: @escaping () -> Void) {
        self.accept = accept; self.closed = closed
        super.init(nibName: nil, bundle: nil)
    }
    required init?(coder: NSCoder) { nil }
    override var supportedInterfaceOrientations: UIInterfaceOrientationMask { .allButUpsideDown }

    override func viewDidLoad() {
        super.viewDidLoad()
        title = "Sign in to Battle.net"
        view.backgroundColor = .systemBackground
        navigationItem.leftBarButtonItem = UIBarButtonItem(systemItem: .cancel, primaryAction: UIAction { [weak self] _ in
            guard let self else { return }
            if let popup = popups.last { webViewDidClose(popup) } else { finish() }
        })
        navigationItem.rightBarButtonItem = UIBarButtonItem(title: "Continue", primaryAction: UIAction { [weak self] _ in
            self?.checkAccount()
        })
        let configuration = WKWebViewConfiguration()
        configuration.websiteDataStore = .nonPersistent()
        web = WKWebView(frame: .zero, configuration: configuration)
        web.navigationDelegate = self; web.uiDelegate = self
        web.translatesAutoresizingMaskIntoConstraints = false
        status.text = "Sign in on Blizzard's page. BCD will read your WoW accounts when it finishes."
        status.font = .preferredFont(forTextStyle: .footnote)
        status.numberOfLines = 0
        status.translatesAutoresizingMaskIntoConstraints = false
        view.addSubview(web); view.addSubview(status)
        NSLayoutConstraint.activate([
            status.leadingAnchor.constraint(equalTo: view.safeAreaLayoutGuide.leadingAnchor, constant: 16),
            status.trailingAnchor.constraint(equalTo: view.safeAreaLayoutGuide.trailingAnchor, constant: -16),
            status.bottomAnchor.constraint(equalTo: view.safeAreaLayoutGuide.bottomAnchor, constant: -8),
            web.topAnchor.constraint(equalTo: view.safeAreaLayoutGuide.topAnchor),
            web.leadingAnchor.constraint(equalTo: view.leadingAnchor), web.trailingAnchor.constraint(equalTo: view.trailingAnchor),
            web.bottomAnchor.constraint(equalTo: status.topAnchor, constant: -8),
        ])
        web.load(URLRequest(url: BCDBattleNetAccount.loginURL))
    }

    func webView(_ webView: WKWebView, decidePolicyFor action: WKNavigationAction,
                 decisionHandler: @escaping @MainActor (WKNavigationActionPolicy) -> Void) {
        // Providers may have their own HTTPS origins; never open custom schemes from this sheet.
        decisionHandler(action.request.url?.scheme == "https" || action.request.url?.absoluteString == "about:blank" ? .allow : .cancel)
    }

    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        guard webView === web, let url = webView.url, BCDBattleNetAccount.validOrigin(url),
              url.path == "/games" || url.path.hasPrefix("/games/") else { return }
        checkAccount()
    }

    private func checkAccount() {
        guard checking == nil, !finished, let url = web.url, BCDBattleNetAccount.validOrigin(url),
              let origin = URLComponents(string: "https://" + (url.host ?? ""))?.url else {
            if checking == nil { status.text = "Finish signing in on Blizzard's page, then tap Continue." }
            return
        }
        navigationItem.rightBarButtonItem?.isEnabled = false
        status.text = "Reading your Battle.net account…"
        checking = Task { [weak self] in
            guard let self, !Task.isCancelled, !finished, let page = web else { return }
            defer { checking = nil; navigationItem.rightBarButtonItem?.isEnabled = true }
            let cookies = await withCheckedContinuation { continuation in
                page.configuration.websiteDataStore.httpCookieStore.getAllCookies { continuation.resume(returning: $0) }
            }
            guard !Task.isCancelled, !finished else { return }
            let made = BCDBattleNetCredential(origin: origin, cookies: cookies.compactMap {
                BCDBattleNetCredential.Cookie($0, origin: origin, now: Date())
            })
            do {
                try await accept(made)
                guard !Task.isCancelled, !finished else { return }
                finish()
            } catch is CancellationError {
                return
            } catch {
                status.text = (error as? LocalizedError)?.errorDescription ?? "Couldn't read the account. Try Continue again."
            }
        }
    }

    func webView(_ webView: WKWebView, createWebViewWith configuration: WKWebViewConfiguration,
                 for action: WKNavigationAction, windowFeatures: WKWindowFeatures) -> WKWebView? {
        guard !finished, let web else { return nil }
        let child = WKWebView(frame: web.frame, configuration: configuration)
        child.autoresizingMask = [.flexibleWidth, .flexibleHeight]
        child.navigationDelegate = self; child.uiDelegate = self
        view.addSubview(child); popups.append(child)
        return child
    }

    func webViewDidClose(_ webView: WKWebView) {
        guard popups.contains(where: { $0 === webView }) else { return }
        popups.removeAll { $0 === webView }; webView.removeFromSuperview()
    }

    private func finish() {
        guard !finished else { return }
        finished = true; checking?.cancel()
        for page in [web].compactMap({ $0 }) + popups {
            page.stopLoading(); page.navigationDelegate = nil; page.uiDelegate = nil
            page.removeFromSuperview()
        }
        web = nil; popups = []
        dismiss(animated: true); closed()
    }
}

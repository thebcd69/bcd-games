// SPDX-License-Identifier: GPL-3.0-or-later
// Copyright (C) 2026 The Porthole authors. See LICENSE.
// Taken from https://github.com/tylerkaska112/Porthole (commit 99ec315, app/Sources/Amazon/AmazonAuth.swift) under GPL-3.0-or-later with the
// Madeira Converter Exception. Modified by BCD (thebcd69), November 2026: BCD's Keychain name and its own network session; the sign-in page is
// BCD's sign-in sheet (BCDWebSignIn, BCDAmazon/BCDLiveStoreAmazon.swift) instead of a web view of its own; the sign-in in progress is kept
// for the code from either the sheet or a pasted address; no SwiftUI, no ObservableObject (AmazonLibrary publishes what the interface reads);
// BCD's log tag. See THIRD-PARTY-NOTICES.md.
//
// Amazon Games sign-in. The person logs in on Amazon's own page (BCD's sign-in sheet; the password never reaches the app). The page ends on
// a redirect that carries a one-time code; the code is exchanged, together with a PKCE verifier made on this device, to register this device
// with Amazon, which answers with access and refresh tokens. This is the same device registration the Amazon Games app does (and every
// open-source client for it, such as Nile). The tokens live in the Keychain, this device only. Nothing here logs a token, a code, a serial or
// an account name.

import Foundation
import Security
import CryptoKit

enum AmazonAuthError: Error, LocalizedError {
    case notSignedIn
    case network
    case rejected(String)

    var message: String {
        switch self {
        case .notSignedIn: return "Sign in to Amazon Games first, in Settings › Accounts."
        case .network: return "Could not reach Amazon. Check your connection and try again."
        case .rejected(let why): return "Amazon refused the sign-in (\(why)). Sign in again."
        }
    }
    var errorDescription: String? { message }
}

struct AmazonTokens: Codable {
    var accessToken: String
    var refreshToken: String
    var expiresAt: Date
    /// The id this device registered under; the entitlement request is bound to it.
    var deviceSerial: String
    var userID: String
    var name: String
}

/// The network session BCD's Amazon calls use: no cookies or cache kept.
enum AmazonNetwork {
    static let session: URLSession = {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.timeoutIntervalForResource = 60 * 60 * 6
        return URLSession(configuration: configuration)
    }()
}

@MainActor
final class AmazonAuth {
    static let shared = AmazonAuth()

    nonisolated static let api = "https://api.amazon.com"
    nonisolated static let appName = "AGSLauncher for Windows"
    nonisolated static let userAgent = "AGSLauncher/1.0.0"
    private let deviceType = "A2UMVHOX7UP4V7"
    private let marketplace = "ATVPDKIKX0DER"
    private let store = AmazonTokenStore()

    /// What a login in progress needs again when Amazon answers: made fresh each time the login page opens.
    private var pending: (verifier: String, clientID: String, serial: String)?

    private init() {}

    /// The signed-in account's first name ("Amazon account" when Amazon gave none), or nil.
    var accountName: String? { store.load().map { $0.name.isEmpty ? "Amazon account" : $0.name } }
    var signedIn: Bool { store.load() != nil }

    // MARK: Login page

    /// A new login: a PKCE verifier and a device id are made, and Amazon's login page for them is returned.
    func beginLogin() -> URL {
        var random = [UInt8](repeating: 0, count: 32)
        _ = SecRandomCopyBytes(kSecRandomDefault, random.count, &random)
        let verifier = Self.base64URL(Data(random))
        let challenge = Self.base64URL(Data(SHA256.hash(data: Data(verifier.utf8))))
        let serial = UUID().uuidString.replacingOccurrences(of: "-", with: "")
        let clientID = Data("\(serial)#\(deviceType)".utf8).map { String(format: "%02x", $0) }.joined()
        pending = (verifier, clientID, serial)
        var c = URLComponents(string: "https://www.amazon.com/ap/signin")!
        c.queryItems = [
            ("openid.ns", "http://specs.openid.net/auth/2.0"),
            ("openid.claimed_id", "http://specs.openid.net/auth/2.0/identifier_select"),
            ("openid.identity", "http://specs.openid.net/auth/2.0/identifier_select"),
            ("openid.mode", "checkid_setup"),
            ("openid.oa2.scope", "device_auth_access"),
            ("openid.ns.oa2", "http://www.amazon.com/ap/ext/oauth/2"),
            ("openid.oa2.response_type", "code"),
            ("openid.oa2.code_challenge_method", "S256"),
            ("openid.oa2.client_id", "device:\(clientID)"),
            ("language", "en_US"),
            ("marketPlaceId", marketplace),
            ("openid.return_to", "https://www.amazon.com"),
            ("openid.pape.max_auth_age", "0"),
            ("openid.assoc_handle", "amzn_sonic_games_launcher"),
            ("pageId", "amzn_sonic_games_launcher"),
            ("openid.oa2.code_challenge", challenge),
        ].map { URLQueryItem(name: $0.0, value: $0.1) }
        return c.url!
    }

    /// The authorization code out of Amazon's redirect address (or whatever was pasted), nil when it is not in there.
    nonisolated static func extractCode(from text: String) -> String? {
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard let url = URLComponents(string: trimmed),
              let code = url.queryItems?.first(where: { $0.name == "openid.oa2.authorization_code" })?.value, !code.isEmpty else { return nil }
        return code
    }

    /// True for the address Amazon's login ends on (the page itself is not wanted, only the code in its address).
    nonisolated static func isLoginResult(_ url: URL) -> Bool {
        url.host == "www.amazon.com" && url.path == "/" && (url.query ?? "").contains("openid.oa2.authorization_code=")
    }

    /// The code from the sign-in sheet: this device is registered with Amazon and the tokens are kept. Throws with a message for the player.
    func finishLogin(code: String) async throws {
        guard let pending else { throw AmazonAuthError.rejected("the sign-in page was not opened from BCD") }
        let tokens = try await register(code: code, pending: pending)
        store.save(tokens)
        self.pending = nil
        LogStore.shared.log("[amazon] signed in")
    }

    // MARK: Tokens

    /// A valid access token and the device id it belongs to, refreshed first when it has expired.
    func session() async throws -> (token: String, serial: String) {
        guard var tokens = store.load() else { throw AmazonAuthError.notSignedIn }
        if tokens.expiresAt > Date().addingTimeInterval(60) { return (tokens.accessToken, tokens.deviceSerial) }
        let body: [String: Any] = [
            "source_token": tokens.refreshToken, "source_token_type": "refresh_token", "requested_token_type": "access_token",
            "app_name": Self.appName, "app_version": "1.0.0",
        ]
        let (json, status) = try await post("/auth/token", body: body)
        guard status < 400, let access = json["access_token"] as? String else {
            // A refresh token Amazon no longer honours means a new sign-in; a hiccup is not a reason to forget the account.
            if status == 400 || status == 401 { signOutLocally(); throw AmazonAuthError.rejected("the saved sign-in expired") }
            throw AmazonAuthError.rejected("status \(status)")
        }
        tokens.accessToken = access
        tokens.expiresAt = Date().addingTimeInterval(Self.seconds(json["expires_in"]) - 60)
        store.save(tokens)
        return (tokens.accessToken, tokens.deviceSerial)
    }

    func signOut() {
        // Tell Amazon this device is gone (best effort: the local sign-out happens either way).
        if let tokens = store.load() {
            Task {
                var request = URLRequest(url: URL(string: Self.api + "/auth/deregister")!)
                request.httpMethod = "POST"
                request.setValue("bearer \(tokens.accessToken)", forHTTPHeaderField: "Authorization")
                request.setValue("application/json", forHTTPHeaderField: "Content-Type")
                request.setValue(Self.userAgent, forHTTPHeaderField: "User-Agent")
                request.httpBody = try? JSONSerialization.data(withJSONObject: ["request_metadata": ["app_name": Self.appName, "app_version": "1.0.0"]])
                request.timeoutInterval = 15
                _ = try? await AmazonNetwork.session.data(for: request)
            }
        }
        signOutLocally()
    }

    private func signOutLocally() {
        store.delete()
        pending = nil
    }

    // MARK: Requests

    private func post(_ path: String, body: [String: Any]) async throws -> (json: [String: Any], status: Int) {
        var request = URLRequest(url: URL(string: Self.api + path)!)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue(Self.userAgent, forHTTPHeaderField: "User-Agent")
        request.httpBody = try JSONSerialization.data(withJSONObject: body)
        request.timeoutInterval = 20
        let data: Data, response: URLResponse
        do { (data, response) = try await AmazonNetwork.session.data(for: request) } catch { throw AmazonAuthError.network }
        guard let http = response as? HTTPURLResponse else { throw AmazonAuthError.network }
        return ((try? JSONSerialization.jsonObject(with: data) as? [String: Any]) ?? [:], http.statusCode)
    }

    private func register(code: String, pending: (verifier: String, clientID: String, serial: String)) async throws -> AmazonTokens {
        let body: [String: Any] = [
            "auth_data": [
                "authorization_code": code, "client_domain": "DeviceLegacy", "client_id": pending.clientID,
                "code_algorithm": "SHA-256", "code_verifier": pending.verifier, "use_global_authentication": false,
            ] as [String: Any],
            "registration_data": [
                "app_name": Self.appName, "app_version": "1.0.0", "device_model": "Windows", "device_name": NSNull(),
                "device_serial": pending.serial, "device_type": deviceType, "domain": "Device", "os_version": "10.0.19044.0",
            ] as [String: Any],
            "requested_extensions": ["customer_info", "device_info"],
            "requested_token_type": ["bearer", "mac_dms"],
            "user_context_map": [String: Any](),
        ]
        let (json, status) = try await post("/auth/register", body: body)
        guard status < 400, let success = (json["response"] as? [String: Any])?["success"] as? [String: Any],
              let bearer = (success["tokens"] as? [String: Any])?["bearer"] as? [String: Any],
              let access = bearer["access_token"] as? String, let refresh = bearer["refresh_token"] as? String else {
            throw AmazonAuthError.rejected("status \(status)")
        }
        let extensions = success["extensions"] as? [String: Any]
        let customer = extensions?["customer_info"] as? [String: Any]
        let device = extensions?["device_info"] as? [String: Any]
        return AmazonTokens(accessToken: access, refreshToken: refresh,
                            expiresAt: Date().addingTimeInterval(Self.seconds(bearer["expires_in"]) - 60),
                            deviceSerial: (device?["device_serial_number"] as? String) ?? pending.serial,
                            userID: (customer?["user_id"] as? String) ?? "", name: (customer?["given_name"] as? String) ?? "")
    }

    private nonisolated static func seconds(_ value: Any?) -> TimeInterval {
        if let n = value as? Double { return n }
        if let s = value as? String, let n = Double(s) { return n }
        return 3600
    }

    private nonisolated static func base64URL(_ data: Data) -> String {
        data.base64EncodedString().replacingOccurrences(of: "+", with: "-").replacingOccurrences(of: "/", with: "_").replacingOccurrences(of: "=", with: "")
    }
}

final class AmazonTokenStore {
    private let serviceName = "bcd.amazon.tokens"
    private func base() -> [String: Any] {
        [kSecClass as String: kSecClassGenericPassword, kSecAttrService as String: serviceName, kSecAttrAccount as String: "amazon_tokens"]
    }

    func save(_ tokens: AmazonTokens) {
        guard let data = try? JSONEncoder().encode(tokens) else { return }
        delete()
        var query = base()
        query[kSecValueData as String] = data
        query[kSecAttrAccessible as String] = kSecAttrAccessibleWhenUnlockedThisDeviceOnly
        SecItemAdd(query as CFDictionary, nil)
    }

    func load() -> AmazonTokens? {
        var query = base()
        query[kSecReturnData as String] = true
        query[kSecMatchLimit as String] = kSecMatchLimitOne
        var result: AnyObject?
        guard SecItemCopyMatching(query as CFDictionary, &result) == errSecSuccess, let data = result as? Data else { return nil }
        return try? JSONDecoder().decode(AmazonTokens.self, from: data)
    }

    func delete() { SecItemDelete(base() as CFDictionary) }
}

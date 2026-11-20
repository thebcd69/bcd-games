// SPDX-License-Identifier: GPL-3.0-or-later
// Copyright 2026 Jfishin, 125hz
// Madeira Converter Exception: see LICENSE-EXCEPTION.md
//
// The request construction comes from the per-flow helper in Jfishin's
// Madeira Steam client (used with the author's permission, see
// docs/STEAM_SIGNIN.md, "Provenance"); the shared transport and the
// x-eresult handling are 125hz's.

import Foundation

/// IAuthenticationService over HTTPS (api.steampowered.com), shared by the
/// QR and password flows.
///
/// Steam reports most authentication failures as HTTP 200 with an
/// `x-eresult` header and an empty body. The original port ignored that
/// header, so a wrong password or Steam Guard code turned into an empty
/// session followed by minutes of polling. The header is checked here and
/// mapped to a specific error. Request and response bodies are never logged.
enum SteamAuthAPI {
    static func call(_ method: String, body: Data, httpMethod: String = "POST") async throws -> Data {
        let baseURL = "https://api.steampowered.com/IAuthenticationService/\(method)/v1/"

        // Percent-encode the base64 string. Base64 contains +, /, = — all need encoding
        // whether sent as a form body (POST) or a query parameter (GET).
        let base64 = body.base64EncodedString()
        let encoded = base64.addingPercentEncoding(withAllowedCharacters: .alphanumerics) ?? base64

        var request: URLRequest
        if httpMethod == "GET" {
            request = URLRequest(url: URL(string: "\(baseURL)?input_protobuf_encoded=\(encoded)")!)
            request.httpMethod = "GET"
        } else {
            request = URLRequest(url: URL(string: baseURL)!)
            request.httpMethod = httpMethod
            request.setValue("application/x-www-form-urlencoded", forHTTPHeaderField: "Content-Type")
            request.httpBody = "input_protobuf_encoded=\(encoded)".data(using: .utf8)
        }
        request.timeoutInterval = 20
        request.cachePolicy = .reloadIgnoringLocalCacheData

        let (data, response) = try await URLSession.shared.data(for: request)
        guard let http = response as? HTTPURLResponse else {
            throw SteamError.authenticationFailed("No answer from Steam. Make sure you're online.")
        }
        if http.statusCode == 429 { throw SteamError.rateLimited }
        guard http.statusCode == 200 else {
            throw SteamError.authenticationFailed("Steam's sign-in service isn't available at the moment (HTTP \(http.statusCode)).")
        }
        if let text = http.value(forHTTPHeaderField: "x-eresult"), let code = UInt32(text), code != 1 {
            SteamLog.trace("\(method) eresult=\(code)")
            throw error(for: code)
        }
        return data
    }

    /// madeira-bcd (build 666, issue #39): a poll the network interrupted is not a failed sign-in. Approving the sign-in in the Steam
    /// app on the same iPhone, or opening the QR link there, puts BCD in the background, and the poll request in flight comes back as
    /// "network connection lost" (-1005) when BCD returns: six players' logs of builds 630-659 end their sign-in that way, one of them
    /// eight times in four minutes (KTD3HS: "[steam-signin] sign-in failed method=qr reason=url--1005", each a moment before the
    /// app's foreground update check), and the sheet said "You're offline". These errors make the poll wait and ask again.
    static func isTransientNetworkError(_ error: Error) -> Bool {
        guard let url = error as? URLError else { return false }
        switch url.code {
        case .networkConnectionLost, .notConnectedToInternet, .timedOut, .cannotConnectToHost, .cannotFindHost, .dnsLookupFailed,
             .dataNotAllowed, .internationalRoamingOff, .callIsActive, .backgroundSessionWasDisconnected:
            return true
        default:
            return false
        }
    }

    /// A poll request (PollAuthSessionStatus) that outlasts a short loss of the network: after a transient error (above) it waits
    /// `interval` seconds (at least one) and asks again, up to `patience` times in a row; past that the error ends the poll as it did
    /// before build 666, so a phone that is really offline still hears so (after about half a minute at Steam's 5-second interval).
    /// Any other error, and cancellation, end it at once.
    static func pollCall(_ method: String, body: Data, interval: Double, patience: Int = 6, flow: String) async throws -> Data {
        var misses = 0
        while true {
            do {
                return try await call(method, body: body)
            } catch {
                guard isTransientNetworkError(error), misses < patience else { throw error }
                misses += 1
                let code = (error as? URLError)?.code.rawValue ?? 0
                SteamLog.event("[steam-signin] poll interrupted method=\(flow) reason=url-\(code), asking again (\(misses) of \(patience))")
                try await Task.sleep(nanoseconds: UInt64(max(1, interval) * 1_000_000_000))
            }
        }
    }

    /// EResult values Steam returns from the authentication service.
    static func error(for code: UInt32) -> SteamError {
        switch code {
        case 5, 18: return .invalidCredentials               // InvalidPassword, AccountNotFound
        case 65, 88: return .authenticationFailed("Steam Guard didn't accept that code. Enter a fresh one.")
        case 84, 87: return .rateLimited                     // RateLimitExceeded, AccountLoginDeniedThrottle
        case 17: return .accountDisabled                     // Banned
        case 9, 27: return .authSessionExpired               // FileNotFound (expired session), Expired
        default: return .authenticationFailed("Steam turned down the sign-in (code \(code)).")
        }
    }
}

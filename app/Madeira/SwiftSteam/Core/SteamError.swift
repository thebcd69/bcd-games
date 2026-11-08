// SPDX-License-Identifier: GPL-3.0-or-later
// Copyright 2026 Jfishin, 125hz
// Madeira Converter Exception: see LICENSE-EXCEPTION.md
// Portions copyright 2026 intraducine and ft7t8drgwf-wq: code taken from https://github.com/willfaust/Madeira (pull requests #477, #490 and #493)
// under GPL-3.0-or-later and modified for BCD in November 2026. See THIRD-PARTY-NOTICES.md.
//
// Derived from Jfishin's Madeira Steam client, used in Madeira with the
// author's permission (see docs/STEAM_SIGNIN.md, "Provenance"). The sign-in
// errors are the ones sign-in raises; the connection, library and download
// errors belong to the owned library and downloads (docs/STEAM_LIBRARY.md).
// Modified by BCD (thebcd69), 2026-11-05: `betaCodeNeeded` (version selection).
// Transport error policy adapted from kuscholar/iDeck; see docs/STEAM_LIBRARY.md.

import Foundation

/// Errors raised by the Steam sign-in, library and download code.
enum SteamError: LocalizedError, Equatable {
    /// Logon EResults that mean the stored sign-in token itself is unusable:
    /// InvalidPassword, AccessDenied, AccountNotFound, Revoked, Expired,
    /// InvalidSignature.
    static let signInExpiredCodes: Set<UInt32> = [5, 15, 18, 26, 27, 94]

    // Sign-in
    case authenticationFailed(String)
    case invalidCredentials
    case rateLimited
    case accountDisabled
    case rsaKeyFetchFailed
    case qrCodeExpired
    case authSessionExpired
    case protobufError(String)

    // Connection
    case noServersAvailable
    case connectionFailed(String)
    /// Safe transport identity only; no URL, headers or credential-bearing error payload.
    case transportFailed(domain: String, code: Int)
    case connectionTimeout
    case disconnected
    /// The CM refused the logon with this EResult. Lets callers tell a revoked
    /// or expired sign-in from a transient network failure.
    case logonDenied(UInt32)
    case invalidMessage

    // Library
    case appInfoNotFound(UInt32)
    case depotNotFound(UInt32)

    // Content
    case manifestFetchFailed(String)
    case chunkDownloadFailed(String)
    /// A content server answered with this HTTP status.
    case contentHTTP(Int)
    /// Every content server refused a depot's file list (HTTP 401 or 403), also with fresh authorization and a fresh request code.
    case contentRefused(Int)
    case decryptionFailed(String)
    case decompressionFailed
    /// A downloaded chunk could not be decoded; the value names the encoding
    /// (vzip, vzstd, zip) or its leading bytes.
    case chunkDecodeFailed(String)
    case checksumMismatch
    case depotKeyNotFound(UInt32)
    case insufficientDiskSpace(needed: UInt64, available: UInt64)
    /// The chosen branch is password-protected and Steam did not accept the
    /// kept beta code for it (or none is kept).
    case betaCodeNeeded(String)

    var errorDescription: String? {
        switch self {
        case .authenticationFailed(let reason):
            return reason
        case .invalidCredentials:
            return "Steam didn't accept that account name and password."
        case .rateLimited:
            return "Steam is limiting sign-in attempts right now. Wait a while, then try again."
        case .accountDisabled:
            return "Steam has disabled this account."
        case .rsaKeyFetchFailed:
            return "BCD couldn't get the key Steam uses to protect your password. Try again."
        case .qrCodeExpired:
            return "This QR code timed out. Get a new one to try again."
        case .authSessionExpired:
            return "The sign-in timed out. Start over to try again."
        case .protobufError(let detail):
            return "Steam sent a reply BCD couldn't read (\(detail))."
        case .noServersAvailable:
            return "BCD couldn't find a Steam server to connect to."
        case .connectionFailed(let reason):
            return "Couldn't connect to Steam: \(reason)"
        case .transportFailed(let domain, let code):
            if domain == NSURLErrorDomain {
                switch URLError.Code(rawValue: code) {
                case .secureConnectionFailed:
                    return "Steam could not make a secure connection (\(code)). Check your network or VPN and try again."
                case .serverCertificateHasBadDate, .serverCertificateUntrusted, .serverCertificateHasUnknownRoot, .serverCertificateNotYetValid:
                    return "Steam could not verify the server certificate (\(code)). Check your device's date and network settings."
                case .clientCertificateRejected, .clientCertificateRequired:
                    return "Steam requested or rejected a client certificate (\(code)). Check your network or VPN settings."
                default: break
                }
            }
            return "Couldn't connect to Steam (\(domain), \(code)). Check that you're online, then try again."
        case .connectionTimeout:
            return "Steam is taking too long to answer. Check that you're online, then try again. If you are online and it keeps "
                + "happening, sign in to Steam again."
        case .disconnected:
            return "The connection to Steam was closed."
        case .logonDenied(let code):
            return SteamError.signInExpiredCodes.contains(code)
                ? "Steam no longer accepts your sign-in. Sign in again."
                : "Steam turned the connection away (code \(code)). Try again in a little while."
        case .invalidMessage:
            return "Steam sent a message BCD couldn't understand."
        case .appInfoNotFound(let appID):
            return "Steam returned no details for app \(appID)."
        case .depotNotFound:
            return "This game has no Windows version to download on Steam."
        case .manifestFetchFailed(let reason):
            return "The game's file list didn't load. \(reason)"
        case .chunkDownloadFailed(let reason):
            return "The download stopped. \(reason)"
        case .contentHTTP(let status):
            return "The download stopped. A content server answered with HTTP \(status)."
        case .contentRefused(let status):
            return "Steam's download servers would not hand out this game's file list (HTTP \(status)), even after BCD asked Steam for new "
                + "download permission. Wait a few minutes and tap Try again; what's already downloaded stays. If it keeps happening, check "
                + "that the game still shows in your Steam library, then send the log."
        case .decryptionFailed(let reason):
            return "Couldn't decrypt the game's data. \(reason)"
        case .decompressionFailed:
            return "Downloaded data couldn't be unpacked."
        case .chunkDecodeFailed(let format):
            return "A piece of the game didn't decode (\(format)). Try again. What's already downloaded stays."
        case .checksumMismatch:
            return "A downloaded piece didn't match its checksum."
        case .depotKeyNotFound:
            return "Steam won't let this account download the game's files. Make sure the game is on this account."
        case .insufficientDiskSpace(let needed, let available):
            return String(format: "This download needs %.1f GB, but only %.1f GB is free on this device.",
                          Double(needed) / 1_000_000_000, Double(available) / 1_000_000_000)
        case .betaCodeNeeded(let branch):
            return "The \(branch) version needs a beta code that Steam accepts. Enter it in the game's Options › Version, or pick another version."
        }
    }
}

/// Where a Steam Guard code comes from.
enum SteamGuardType: String, Codable {
    case email = "email"
    case device = "device"          // Steam Mobile Authenticator
}

/// An endpoint/route negotiation failure can be isolated by trying another CM.
/// Explicit trust failures and cancellation must not be hidden by retries.
enum SteamCMTransportPolicy {
    static func canTryAnotherServer(_ error: Error) -> Bool {
        let failure = error as NSError
        guard failure.domain == NSURLErrorDomain else { return false }
        switch URLError.Code(rawValue: failure.code) {
        case .timedOut, .cannotFindHost, .cannotConnectToHost, .networkConnectionLost,
             .dnsLookupFailed, .secureConnectionFailed: return true
        default: return false
        }
    }
    static func sanitized(_ error: Error) -> SteamError {
        let failure = error as NSError
        let domain = [NSURLErrorDomain, NSPOSIXErrorDomain].contains(failure.domain) ? failure.domain : "other"
        return .transportFailed(domain: domain, code: failure.code)
    }
}

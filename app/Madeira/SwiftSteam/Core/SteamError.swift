// SPDX-License-Identifier: GPL-3.0-or-later
// Copyright 2026 Jfishin, 125hz
// Madeira Converter Exception: see LICENSE-EXCEPTION.md
//
// Derived from Jfishin's Madeira Steam client, used in Madeira with the
// author's permission (see docs/STEAM_SIGNIN.md, "Provenance"). The sign-in
// errors are the ones sign-in raises; the connection, library and download
// errors belong to the owned library and downloads (docs/STEAM_LIBRARY.md).

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
    case decryptionFailed(String)
    case decompressionFailed
    /// A downloaded chunk could not be decoded; the value names the encoding
    /// (vzip, vzstd, zip) or its leading bytes.
    case chunkDecodeFailed(String)
    case checksumMismatch
    case depotKeyNotFound(UInt32)
    case insufficientDiskSpace(needed: UInt64, available: UInt64)

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
        }
    }
}

/// Where a Steam Guard code comes from.
enum SteamGuardType: String, Codable {
    case email = "email"
    case device = "device"          // Steam Mobile Authenticator
}

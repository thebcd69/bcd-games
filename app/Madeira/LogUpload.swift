// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
//
//  LogUpload.swift
//  Madeira
//
//  madeira-bcd FORK-ONLY: never in a pull request to upstream (owner, 2026-10-17).
//
//  Settings › "Send Log": a session log goes to the fork's own log intake (a
//  Cloudflare Worker, bcd-logs/worker) so that testers' crashes reach auto-tune's rules
//  without anyone mailing files. The log is scrubbed and gzip'ed on the phone
//  (LogUploadCore.swift), sent, and the compressed copy deleted the moment the upload
//  ends, sent or not. The app holds no storage key; the intake answers with a short code
//  the tester can quote.
//
//  By hand only, and only the 20 newest logs (owner, 2026-10-17: build 562's automatic
//  sending would bring far too many logs, working sessions included, and an older log must
//  not be one tap away from being sent: "Yoksa çok suistimal edilir").
//

import Foundation
import SwiftUI
import UIKit

enum LogUpload {
    static let endpoint = URL(string: "https://bcd.bcd-logs.workers.dev/v1/logs")!
    /// A screenshot sent with a log (2026-10-25): its own route, JPEG only, 160 KB at most (bcd-logs/worker/worker.js). What goes is a small
    /// copy made for the upload (BCDScreenshotShrink), about 100 KB; the kept picture is not touched.
    static let shotEndpoint = URL(string: "https://bcd.bcd-logs.workers.dev/v1/shots")!
    static let intake = "madeira-bcd/2"
    static let maxPacked = 25 << 20
    /// The newest logs the list offers; nothing older can be sent.
    static let listed = 20
    private static let historyKey = "madeira.bcd.logs.sent"

    struct Sent: Codable, Identifiable {
        var name: String
        var code: String
        var date: Date
        var id: String { code }
    }

    static var history: [Sent] {
        guard let d = UserDefaults.standard.data(forKey: historyKey),
              let list = try? JSONDecoder().decode([Sent].self, from: d) else { return [] }
        return list
    }

    // MARK: A screenshot with the log

    /// The pictures the player chose to send with a log, by the log's file (BCDLogComment): taken by `send` for that log and no other.
    private static var chosenShots: [String: URL] = [:]
    private static let chosenLock = NSLock()
    /// What became of the picture the last send carried ("The screenshot went too."), or nil when it carried none. Set before `done` runs.
    static var lastShotNote: String?

    static func attach(_ shot: URL, to log: URL) {
        chosenLock.lock(); defer { chosenLock.unlock() }
        chosenShots[log.standardizedFileURL.path] = shot
    }

    private static func takeShot(for log: URL) -> URL? {
        chosenLock.lock(); defer { chosenLock.unlock() }
        return chosenShots.removeValue(forKey: log.standardizedFileURL.path)
    }

    /// "Sent. Your code: K7Q2MX", and how the picture fared if one went along.
    static func sentWords(_ code: String) -> String {
        "Sent. Your code: \(code)" + (lastShotNote.map { ". " + $0 } ?? "")
    }

    private static func remember(_ s: Sent) {
        let list = Array(([s] + history).prefix(20))
        if let d = try? JSONEncoder().encode(list) { UserDefaults.standard.set(d, forKey: historyKey) }
    }

    enum UploadError: LocalizedError {
        case tooLarge(Int), refused(Int, String), noCode, notRecent
        var errorDescription: String? {
            switch self {
            case .notRecent: return "Only the \(LogUpload.listed) newest logs can be sent."
            case .tooLarge(let n): return "The compressed log is \(n >> 20) MB; the limit is \(LogUpload.maxPacked >> 20) MB."
            case .refused(let status, let why): return "The server refused it (\(status)\(why.isEmpty ? "" : ": " + why))."
            case .noCode: return "The server did not answer with a code."
            }
        }
    }

    private static let queue = DispatchQueue(label: "madeira.bcd.log-upload", qos: .utility)

    /// The program a session log is named after ("war3.exe" from "war3.exe-2026-10-16_22-24-07.txt").
    static func game(of log: URL) -> String {
        let name = log.deletingPathExtension().lastPathComponent
        if let r = name.range(of: #"-\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}$"#, options: .regularExpression) {
            return String(name[..<r.lowerBound])
        }
        return name
    }

    private static var deviceModel: String {
        var u = utsname()
        uname(&u)
        return withUnsafeBytes(of: &u.machine) { String(decoding: $0.prefix { $0 != 0 }, as: UTF8.self) }
    }

    /// Packs and sends one of the `listed` newest logs; `done` runs on the main queue with the
    /// intake's code.
    static func send(_ log: URL, game: String, comment: String? = nil,
                     progress: ((String) -> Void)? = nil, done: ((Result<String, Error>) -> Void)? = nil) {
        func finish(_ r: Result<String, Error>) {
            switch r {
            case .success(let code):
                remember(Sent(name: log.lastPathComponent, code: code, date: Date()))
                let commented = LogUploadCore.commentLines(comment).isEmpty ? "" : " (with the sender's comment)"
                LogStore.shared.log("[bcd-logs] sent \(log.lastPathComponent): code \(code)\(commented)")
                if let shot = takeShot(for: log) {
                    // The picture follows the log, under the log's code. It cannot undo the log's success: whatever happens to it is a note.
                    if let progress { DispatchQueue.main.async { progress("Sending the screenshot…") } }
                    sendShot(shot, logCode: code, game: game) { note in
                        DispatchQueue.main.async {
                            lastShotNote = note
                            done?(r)
                        }
                    }
                    return
                }
                DispatchQueue.main.async { lastShotNote = nil }
            case .failure(let e):
                LogStore.shared.log("[bcd-logs] could not send \(log.lastPathComponent): \(e.localizedDescription)", level: .error)
                _ = takeShot(for: log)   // a picture chosen for a log that did not go is not kept for a later send
            }
            if let done { DispatchQueue.main.async { done(r) } }
        }
        let say: (String) -> Void = { text in if let progress { DispatchQueue.main.async { progress(text) } } }
        queue.async {
            guard recentLogs().contains(where: { $0.standardizedFileURL == log.standardizedFileURL }) else {
                return finish(.failure(UploadError.notRecent))
            }
            let gz = FileManager.default.temporaryDirectory.appendingPathComponent("bcd-log-\(UUID().uuidString).gz")
            let packed: (raw: Int, packed: Int)
            // Round 604: the session's performance trace, when the player asked for one, goes after the log's last line (BCDPerfTraceLive.swift).
            let trace = BCDPerfTraceSend.lines(for: log)
            if !trace.isEmpty { LogStore.shared.log("[bcd-logs] the performance trace goes with \(log.lastPathComponent): \(max(0, trace.count - 2)) lines") }
            do {
                say("Compressing…")
                packed = try LogUploadCore.pack(log, to: gz, comment: comment, trailer: trace) { f in say("Compressing… \(Int(f * 100))%") }
            } catch {
                try? FileManager.default.removeItem(at: gz)
                return finish(.failure(error))
            }
            guard packed.packed <= maxPacked else {
                try? FileManager.default.removeItem(at: gz)
                return finish(.failure(UploadError.tooLarge(packed.packed)))
            }
            say("Sending \(max(1, packed.packed >> 20)) MB…")
            var req = URLRequest(url: endpoint)
            req.httpMethod = "POST"
            req.setValue("application/gzip", forHTTPHeaderField: "Content-Type")
            req.setValue(intake, forHTTPHeaderField: "X-BCD-Intake")
            let build = Bundle.main.object(forInfoDictionaryKey: "CFBundleVersion") as? String ?? ""
            let os = ProcessInfo.processInfo.operatingSystemVersion
            for (k, v) in ["Build": build, "Game": game, "Device": deviceModel,
                           "Os": "\(os.majorVersion).\(os.minorVersion).\(os.patchVersion)",
                           "Kind": "manual"] {
                req.setValue(v, forHTTPHeaderField: "X-Madeira-" + k)
            }
            let config = URLSessionConfiguration.ephemeral
            config.timeoutIntervalForResource = 600
            let session = URLSession(configuration: config)
            session.uploadTask(with: req, fromFile: gz) { data, response, error in
                // Sent or not, the compressed copy goes now (owner: deleted the moment it is sent).
                try? FileManager.default.removeItem(at: gz)
                session.finishTasksAndInvalidate()
                if let error { return finish(.failure(error)) }
                let status = (response as? HTTPURLResponse)?.statusCode ?? 0
                let body = (data.flatMap { try? JSONSerialization.jsonObject(with: $0) } as? [String: Any]) ?? [:]
                guard status == 200 else { return finish(.failure(UploadError.refused(status, body["error"] as? String ?? ""))) }
                guard let code = body["code"] as? String, !code.isEmpty else { return finish(.failure(UploadError.noCode)) }
                finish(.success(code))
            }.resume()
        }
    }

    /// Sends a screenshot under the code of the log it went with. `note` says how it fared, for the person. What is sent is not the kept file
    /// but a small copy made from it now (BCDScreenshotShrink: 1280 px and quality 0.75 down to 640 px and 0.4, until it is 100 KB or less).
    private static func sendShot(_ shot: URL, logCode: String, game: String, note: @escaping (String) -> Void) {
        // The search decodes and encodes a few pictures: not the URLSession callback's thread. The utility queue is free by now (the log's
        // upload task has been handed over).
        queue.async {
            guard let copy = BCDScreenshotShrink.smallCopy(of: shot), !copy.data.isEmpty else {
                LogStore.shared.log("[bcd-logs] screenshot not sent: \(shot.lastPathComponent) could not be read or made smaller", level: .error)
                return note("The screenshot was not sent: the picture could not be read.")
            }
            let kb = String(format: "%.1f", Double(copy.data.count) / 1024)
            LogStore.shared.log("[bcd-logs] screenshot copy for upload: \(copy.width)x\(copy.height), \(kb) KB (\(copy.data.count) bytes), "
                                + "quality \(String(format: "%.2f", copy.quality)), \(copy.tries) tries\(copy.fits ? "" : ", over the target")")
            guard copy.data.count <= BCDScreenshotRules.maxSendBytes else {
                LogStore.shared.log("[bcd-logs] screenshot not sent: \(copy.data.count) bytes (limit \(BCDScreenshotRules.maxSendBytes))", level: .error)
                return note("The screenshot was not sent: it is too large even made small.")
            }
            var req = URLRequest(url: shotEndpoint)
            req.httpMethod = "POST"
            req.setValue("image/jpeg", forHTTPHeaderField: "Content-Type")
            req.setValue(intake, forHTTPHeaderField: "X-BCD-Intake")
            let build = Bundle.main.object(forInfoDictionaryKey: "CFBundleVersion") as? String ?? ""
            let os = ProcessInfo.processInfo.operatingSystemVersion
            for (k, v) in ["Build": build, "Game": game, "Device": deviceModel, "Os": "\(os.majorVersion).\(os.minorVersion).\(os.patchVersion)",
                           "Log": logCode, "Kind": "screenshot"] {
                req.setValue(v, forHTTPHeaderField: "X-Madeira-" + k)
            }
            let config = URLSessionConfiguration.ephemeral
            config.timeoutIntervalForResource = 120
            let session = URLSession(configuration: config)
            session.uploadTask(with: req, from: copy.data) { data, response, error in
                session.finishTasksAndInvalidate()
                if let error {
                    LogStore.shared.log("[bcd-logs] screenshot not sent: \(error.localizedDescription)", level: .error)
                    return note("The screenshot did not go: \(error.localizedDescription)")
                }
                let status = (response as? HTTPURLResponse)?.statusCode ?? 0
                let body = (data.flatMap { try? JSONSerialization.jsonObject(with: $0) } as? [String: Any]) ?? [:]
                guard status == 200, let code = body["code"] as? String, !code.isEmpty else {
                    LogStore.shared.log("[bcd-logs] screenshot refused: \(status) \(body["error"] as? String ?? "")", level: .error)
                    return note(status == 404 ? "The screenshot did not go: the server does not take pictures yet."
                                              : "The screenshot did not go (\(status)\((body["error"] as? String).map { ": " + $0 } ?? "")).")
                }
                LogStore.shared.log("[bcd-logs] sent the screenshot \(shot.lastPathComponent) with log \(logCode): code \(code), \(copy.data.count) bytes")
                note("The screenshot went too.")
            }.resume()
        }
    }

    // MARK: Logs on this device

    /// The `listed` newest logs: session logs and the app's own two, newest first.
    static func recentLogs() -> [URL] {
        guard let docs = MadeiraConfig.documents else { return [] }
        let fm = FileManager.default
        let dir = docs.appendingPathComponent("logs")
        let runs = ((try? fm.contentsOfDirectory(at: dir, includingPropertiesForKeys: [.contentModificationDateKey])) ?? [])
            .filter { $0.pathExtension == "txt" }
        let own = ["bcd-log.txt", "bcd-log.prev.txt"].map { docs.appendingPathComponent($0) }.filter { fm.fileExists(atPath: $0.path) }
        return Array((runs + own).sorted { modified($0) > modified($1) }.prefix(listed))
    }

    /// Round 624 (agent N S6): whether the log holds a game's session (every session log has a "[bcd] library launch" or "[dock-launch]" line near
    /// its top; a log of BCD's own start-up has neither). The first 64 KB are read.
    static func hasGame(_ url: URL) -> Bool {
        guard let handle = try? FileHandle(forReadingFrom: url) else { return false }
        defer { try? handle.close() }
        let head = String(decoding: (try? handle.read(upToCount: 65536)) ?? Data(), as: UTF8.self)
        return head.contains("[bcd] library launch") || head.contains("[dock-launch]")
    }

    static func modified(_ url: URL) -> Date {
        (try? url.resourceValues(forKeys: [.contentModificationDateKey]).contentModificationDate) ?? .distantPast
    }

    static func size(_ url: URL) -> Int {
        (try? FileManager.default.attributesOfItem(atPath: url.path)[.size] as? NSNumber)?.intValue ?? 0
    }

    /// A log named in auto-tune's history ("bcd-log.prev.txt", or a logs/ name).
    static func log(named name: String) -> URL? {
        guard let docs = MadeiraConfig.documents else { return nil }
        return [docs.appendingPathComponent(name), docs.appendingPathComponent("logs").appendingPathComponent(name)]
            .first { FileManager.default.fileExists(atPath: $0.path) }
    }

    /// Build 562 had automatic sending: its setting and session marker go.
    static func forgetAutomatic() {
        UserDefaults.standard.removeObject(forKey: "madeira.bcd.logs.autosend")
        if let marker = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first?
            .appendingPathComponent("bcd-log-session.json") { try? FileManager.default.removeItem(at: marker) }
    }
}

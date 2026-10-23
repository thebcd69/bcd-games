// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

import Foundation
#if canImport(FoundationNetworking)
import FoundationNetworking
#endif

/// What receives an HTTP body piece by piece. `begin` says where the body starts: 0 means from
/// the beginning, so whatever came before (an earlier, dropped connection) must be forgotten.
protocol BCDHTTPConsumer: AnyObject {
    /// Bytes accepted so far: a dropped connection continues from here.
    var received: Int64 { get }
    func begin(at offset: Int64) throws
    func receive(_ data: Data) throws
}

/// HTTP for the content engine and the stores' APIs, without ever holding a body it does not
/// need: each piece URLSession delivers goes to its consumer at once and is dropped. A connection
/// that drops mid-body continues with a Range request from the bytes already taken; a server that
/// answers the Range with the whole body starts the consumer over. Ephemeral: no cache, no cookies.
/// Never logs: URLs carry tokens.
final class BCDHTTPStream: NSObject, URLSessionDataDelegate, @unchecked Sendable {
    private final class Request {
        let consumer: BCDHTTPConsumer
        let offset: Int64
        let range: Range<Int64>?
        let continuation: CheckedContinuation<Void, Error>
        var failure: Error?
        init(_ consumer: BCDHTTPConsumer, _ offset: Int64, _ range: Range<Int64>?, _ continuation: CheckedContinuation<Void, Error>) {
            self.consumer = consumer
            self.offset = offset
            self.range = range
            self.continuation = continuation
        }
    }

    private var session: URLSession!
    private let lock = NSLock()
    private var requests: [Int: Request] = [:]
    private let redirectAllowed: (@Sendable (URL) -> Bool)?

    /// `protocols`: URLProtocol classes answering before the network (the host checks' stand-in servers).
    init(timeout: TimeInterval = 30, protocols: [AnyClass] = [],
         redirectAllowed: (@Sendable (URL) -> Bool)? = nil) {
        self.redirectAllowed = redirectAllowed
        super.init()
        let config = URLSessionConfiguration.ephemeral
        if !protocols.isEmpty { config.protocolClasses = protocols + (config.protocolClasses ?? []) }
        config.urlCache = nil
        config.requestCachePolicy = .reloadIgnoringLocalCacheData
        config.httpCookieAcceptPolicy = .never
        config.httpShouldSetCookies = false
        config.timeoutIntervalForRequest = timeout
        config.timeoutIntervalForResource = 30 * 60
        config.httpMaximumConnectionsPerHost = 4
        config.waitsForConnectivity = false
        let queue = OperationQueue()
        queue.maxConcurrentOperationCount = 1
        queue.qualityOfService = .utility
        session = URLSession(configuration: config, delegate: self, delegateQueue: queue)
    }

    /// The session keeps its delegate until it is invalidated: call once the stream is done.
    func close() { session.finishTasksAndInvalidate() }

    /// GETs `request` into `consumer`, continuing a dropped connection up to `resumes` times.
    /// Status codes other than 200 and 206 throw `BCDContentError.http`.
    func fetch(_ request: URLRequest, into consumer: BCDHTTPConsumer, resumes: Int = 2,
               range: Range<Int64>? = nil) async throws {
        if let range, range.lowerBound < 0 || range.upperBound <= range.lowerBound { throw BCDContentError.http(206) }
        var dropped = 0
        while true {
            try Task.checkCancellation()
            let offset = consumer.received
            var attempt = request
            if let range {
                guard offset < range.upperBound - range.lowerBound else { throw BCDContentError.http(206) }
                attempt.setValue("bytes=\(range.lowerBound + offset)-\(range.upperBound - 1)", forHTTPHeaderField: "Range")
            } else if offset > 0 { attempt.setValue("bytes=\(offset)-", forHTTPHeaderField: "Range") }
            do {
                try await one(attempt, offset: offset, consumer: consumer, range: range)
                return
            } catch let error as URLError where Self.resumable(error) && dropped < resumes && consumer.received > offset {
                dropped += 1
            }
        }
    }

    /// A small body in memory (API replies, manifests), refused past `limit` bytes.
    func data(_ request: URLRequest, limit: Int) async throws -> Data {
        let collector = BCDHTTPCollector(limit: limit)
        try await fetch(request, into: collector, resumes: 1)
        return collector.data
    }

    static func resumable(_ error: URLError) -> Bool {
        [.networkConnectionLost, .timedOut, .cannotParseResponse, .badServerResponse].contains(error.code)
    }

    private func one(_ request: URLRequest, offset: Int64, consumer: BCDHTTPConsumer, range: Range<Int64>?) async throws {
        let task = session.dataTask(with: request)
        try await withTaskCancellationHandler {
            try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<Void, Error>) in
                lock.lock()
                requests[task.taskIdentifier] = Request(consumer, offset, range, continuation)
                lock.unlock()
                if Task.isCancelled { task.cancel() }
                task.resume()
            }
        } onCancel: {
            task.cancel()
        }
    }

    private func request(_ task: URLSessionTask) -> Request? {
        lock.lock()
        defer { lock.unlock() }
        return requests[task.taskIdentifier]
    }

    // MARK: URLSessionDataDelegate (on the session's own serial queue)

    func urlSession(_ session: URLSession, dataTask: URLSessionDataTask, didReceive response: URLResponse,
                    completionHandler: @escaping (URLSession.ResponseDisposition) -> Void) {
        guard let request = request(dataTask) else { completionHandler(.cancel); return }
        let status = (response as? HTTPURLResponse)?.statusCode ?? 0
        do {
            switch status {
            case 200:
                guard request.range == nil else { throw BCDContentError.http(200) }
                try request.consumer.begin(at: 0)
            case 206:
                // "bytes <start>-<end>/<total>": the body must start where the request asked.
                let range = (response as? HTTPURLResponse)?.value(forHTTPHeaderField: "Content-Range") ?? ""
                let start = range.hasPrefix("bytes ") ? Int64(range.dropFirst(6).prefix { $0 != "-" }) : nil
                guard let start, start == request.offset + (request.range?.lowerBound ?? 0) else { throw BCDContentError.http(206) }
                if let expected = request.range {
                    let end = range.split(separator: "/").first?.split(separator: "-").last.flatMap { Int64($0) }
                    guard end == expected.upperBound - 1 else { throw BCDContentError.http(206) }
                }
                try request.consumer.begin(at: start - (request.range?.lowerBound ?? 0))
            default:
                throw BCDContentError.http(status)
            }
            completionHandler(.allow)
        } catch {
            request.failure = error
            completionHandler(.cancel)
        }
    }

    func urlSession(_ session: URLSession, dataTask: URLSessionDataTask, didReceive data: Data) {
        guard let request = request(dataTask), request.failure == nil else { return }
        do {
            try request.consumer.receive(data)
        } catch {
            request.failure = error
            dataTask.cancel()
        }
    }

    func urlSession(_ session: URLSession, task: URLSessionTask, didCompleteWithError error: Error?) {
        lock.lock()
        let request = requests.removeValue(forKey: task.taskIdentifier)
        lock.unlock()
        guard let request else { return }
        if let failure = request.failure {
            request.continuation.resume(throwing: failure)
        } else if let error = error as? URLError, error.code == .cancelled {
            request.continuation.resume(throwing: CancellationError())
        } else if let error {
            request.continuation.resume(throwing: error)
        } else {
            request.continuation.resume()
        }
    }

    // Redirects are followed only to https.
    func urlSession(_ session: URLSession, task: URLSessionTask, willPerformHTTPRedirection response: HTTPURLResponse,
                    newRequest: URLRequest, completionHandler: @escaping (URLRequest?) -> Void) {
        if let redirectAllowed {
            guard let url = newRequest.url, redirectAllowed(url) else { completionHandler(nil); return }
        }
        completionHandler(newRequest.url?.scheme == "https" || response.url?.scheme == "http" ? newRequest : nil)
    }
}

/// Collects a small body, refusing more than its limit.
final class BCDHTTPCollector: BCDHTTPConsumer {
    private let limit: Int
    private(set) var data = Data()
    var received: Int64 { Int64(data.count) }

    init(limit: Int) { self.limit = limit }

    func begin(at offset: Int64) throws {
        if offset == 0 { data.removeAll(keepingCapacity: true) }
    }

    func receive(_ piece: Data) throws {
        guard data.count + piece.count <= limit else { throw BCDContentError.tooLarge }
        data.append(piece)
    }
}

/// Which hosts answer well: a host that fails moves behind the others, so a dead or corrupting
/// host is tried last. Shared by a job's workers. A host is its name and port.
final class BCDHostHealth: @unchecked Sendable {
    private let lock = NSLock()
    private var failures: [String: Int] = [:]

    private static func key(_ url: URL) -> String { (url.host ?? "") + ":" + String(url.port ?? (url.scheme == "http" ? 80 : 443)) }

    /// `urls` in the store's order, the hosts that failed more moved back.
    func order(_ urls: [URL]) -> [URL] {
        lock.lock()
        defer { lock.unlock() }
        return urls.enumerated().sorted {
            let a = failures[Self.key($0.element)] ?? 0, b = failures[Self.key($1.element)] ?? 0
            return a != b ? a < b : $0.offset < $1.offset
        }.map(\.element)
    }

    func failed(_ url: URL) {
        lock.lock()
        failures[Self.key(url), default: 0] += 1
        lock.unlock()
    }

    func succeeded(_ url: URL) {
        lock.lock()
        if let n = failures[Self.key(url)], n > 0 { failures[Self.key(url)] = n - 1 }
        lock.unlock()
    }
}

/// Where a job's chunks come from: the store's adapter answers with addresses for a chunk, in
/// its order of preference, and renews them when a host refuses them.
protocol BCDChunkSource: AnyObject, Sendable {
    func urls(for chunk: BCDContentChunk) async throws -> [URL]
    /// A host answered 401, 403 or 410 for this group: its addresses expired or were refused.
    func refresh(group: String) async throws
    /// Store-specific validation and archive ranges, with the ordinary GET as the default.
    func fetch(_ chunk: BCDContentChunk, url: URL, into consumer: BCDHTTPConsumer, stream: BCDHTTPStream) async throws
}

extension BCDChunkSource {
    func fetch(_ chunk: BCDContentChunk, url: URL, into consumer: BCDHTTPConsumer, stream: BCDHTTPStream) async throws {
        var request = URLRequest(url: url)
        request.timeoutInterval = 30
        try await stream.fetch(request, into: consumer)
    }
}

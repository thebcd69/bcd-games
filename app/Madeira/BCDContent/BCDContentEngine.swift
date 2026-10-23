// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

import Foundation
#if canImport(Darwin)
import Darwin
#else
import Glibc
#endif

struct BCDContentProgress: Codable, Equatable, Sendable {
    enum Phase: String, Codable, Sendable { case preparing, downloading, verifying, finishing }
    var phase: Phase = .preparing
    /// Bytes on the wire: finished chunks plus what the ones in flight received so far.
    var doneBytes: Int64 = 0
    var totalBytes: Int64 = 0
    var bytesPerSecond: Double = 0
    var fraction: Double { totalBytes > 0 ? min(1, Double(doneBytes) / Double(totalBytes)) : 0 }
}

struct BCDContentOptions: Sendable {
    /// Chunks in flight. Each holds about 300 KiB (a received piece, zlib's window and a 256 KiB
    /// output buffer), never a whole chunk.
    var streams = 4
    /// Tries per chunk, across the store's hosts.
    var attempts = 6
    /// Finished chunks are made durable and journaled at least this often.
    var durableBytes: Int64 = 64 << 20
    var durableSeconds: Double = 3
    /// Free space kept beyond what the download writes.
    var spaceMargin: Int64 = 256 << 20
    /// Hash every file that has a whole-file checksum once its chunks are in (Epic's files; GOG's
    /// chunks carry checksums of their own that already cover every byte).
    var verifyFiles = false
    /// The first wait before a retry, doubled each time up to 8 s (the host checks use 0).
    var backoff: Double = 0.5
    var freeSpace: @Sendable (URL) -> Int64? = { BCDContentEngine.freeSpace($0) }
}

/// The store-neutral download: plan in, checked files in a staging folder out.
///
/// - Every chunk the journal does not list is fetched once, by `streams` workers, each with its
///   own HTTP connection; its bytes are checked (on the wire and decoded) while they stream
///   through, and written straight into every file that uses them (`pwrite` at the part's offset;
///   files are created at their final size first, sparse until written).
/// - Finished chunks are made durable in batches (each file they touched `fsync`ed, then the
///   device's cache flushed once), and only then journaled, so the journal never lists a chunk
///   whose bytes could be lost.
/// - Pausing is cancelling the task: what finished is journaled, the rest is fetched again.
/// - Free space is checked before anything is fetched, and again every GiB.
/// - A failing host moves behind the others; addresses a host refuses (401/403/410) are renewed
///   by the store's source.
enum BCDContentEngine {
    static func freeSpace(_ url: URL) -> Int64? {
        var probe = url
        while !FileManager.default.fileExists(atPath: probe.path), probe.pathComponents.count > 1 {
            probe.deleteLastPathComponent()
        }
        return (try? probe.resourceValues(forKeys: [.volumeAvailableCapacityForImportantUsageKey]))?
            .volumeAvailableCapacityForImportantUsage
    }

    /// Bytes still to be written for the chunks the journal does not list.
    static func remainingBytes(_ plan: BCDContentPlan, done: Set<Int>) -> Int64 {
        var bytes: Int64 = 0
        for part in plan.parts where !done.contains(part.chunk) { bytes += part.length }
        return bytes
    }

    static func download(_ plan: BCDContentPlan, stage: URL, jobFolder: URL, source: BCDChunkSource,
                         options: BCDContentOptions = BCDContentOptions(),
                         progress: @escaping @Sendable (BCDContentProgress) -> Void) async throws {
        let index = BCDContentPlan.Index(plan)
        let fm = FileManager.default
        try fm.createDirectory(at: jobFolder, withIntermediateDirectories: true)
        let journalURL = jobFolder.appendingPathComponent("journal")
        var done = BCDContentJournal.load(journalURL, identity: plan.identity, chunkCount: plan.chunks.count)
        progress(BCDContentProgress(phase: .preparing, totalBytes: plan.downloadBytes))

        // The folders, and every file at its final size. A file that is missing or has another
        // size loses its journaled chunks: they are fetched again.
        try fm.createDirectory(at: stage, withIntermediateDirectories: true)
        for directory in plan.directories {
            try fm.createDirectory(at: stage.appendingPathComponent(directory, isDirectory: true), withIntermediateDirectories: true)
        }
        for (i, file) in plan.files.enumerated() {
            let path = stage.appendingPathComponent(file.path).path
            var info = stat()
            if lstat(path, &info) == 0, (info.st_mode & S_IFMT) == S_IFREG, Int64(info.st_size) == file.size { continue }
            if lstat(path, &info) == 0, (info.st_mode & S_IFMT) != S_IFREG { throw BCDContentError.pathClash }
            done.subtract(index.fileChunks[i])
            let fd = open(path, O_WRONLY | O_CREAT | O_NOFOLLOW, 0o644)
            guard fd >= 0 else { throw BCDContentError.io(errno) }
            let sized = ftruncate(fd, off_t(file.size))
            let failure = errno
            close(fd)
            guard sized == 0 else { throw failure == ENOSPC ? BCDContentError.diskFull : BCDContentError.io(failure) }
        }

        var round = 0
        while true {
            let journal = try BCDContentJournal(url: journalURL, identity: plan.identity, keep: done)
            let remaining = remainingBytes(plan, done: done)
            if remaining > 0, let available = options.freeSpace(stage), available < remaining + options.spaceMargin {
                throw BCDContentError.notEnoughSpace(needed: remaining + options.spaceMargin, available: available)
            }
            let pending = (0..<plan.chunks.count).filter { !done.contains($0) }.sorted {
                let a = plan.parts[index.partRanges[$0].lowerBound], b = plan.parts[index.partRanges[$1].lowerBound]
                return (a.file, a.fileOffset) < (b.file, b.fileOffset)
            }
            let doneBytes = done.reduce(Int64(0)) { $0 + plan.chunks[$1].fetchSize }
            let meter = BCDContentMeter(done: doneBytes, total: plan.downloadBytes)
            let run = BCDContentRun(plan: plan, index: index, stage: stage, journal: journal, pending: pending,
                                    options: options, meter: meter)
            let reporter = Task {
                while !Task.isCancelled {
                    progress(meter.progress(.downloading))
                    try? await Task.sleep(nanoseconds: 250_000_000)
                }
            }
            do {
                try await withThrowingTaskGroup(of: Void.self) { group in
                    let health = BCDHostHealth()
                    for _ in 0..<max(1, min(options.streams, pending.count)) {
                        group.addTask { try await run.work(source: source, health: health) }
                    }
                    try await group.waitForAll()
                }
                reporter.cancel()
                try await run.flush()
            } catch {
                reporter.cancel()
                // What finished before the stop is whole: keep it.
                try? await run.flush()
                throw error
            }
            done = await run.finishedChunks.union(done)
            progress(meter.progress(.downloading))

            guard options.verifyFiles else { return }
            progress(BCDContentProgress(phase: .verifying, doneBytes: plan.downloadBytes, totalBytes: plan.downloadBytes))
            let bad = try await Task.detached(priority: .utility) { try verify(plan, folder: stage) }.value
            if bad.isEmpty { return }
            guard round == 0 else { throw BCDContentError.checkFailed }
            round += 1
            for file in bad { done.subtract(index.fileChunks[file]) }
        }
    }

    /// The files whose whole-file checksum does not match (read in 1 MiB pieces).
    static func verify(_ plan: BCDContentPlan, folder: URL) throws -> [Int] {
        var bad: [Int] = []
        var buffer = [UInt8](repeating: 0, count: 1 << 20)
        for (i, file) in plan.files.enumerated() {
            guard let digest = file.digest else { continue }
            try Task.checkCancellation()
            let fd = open(folder.appendingPathComponent(file.path).path, O_RDONLY | O_NOFOLLOW)
            guard fd >= 0 else { bad.append(i); continue }
            defer { close(fd) }
            var hasher = BCDHasher(digest.kind)
            var total: Int64 = 0
            while true {
                let n = buffer.withUnsafeMutableBytes { read(fd, $0.baseAddress!, $0.count) }
                if n < 0 { if errno == EINTR { continue }; break }
                if n == 0 { break }
                buffer.withUnsafeBytes { hasher.update(UnsafeRawBufferPointer(rebasing: $0[0..<n])) }
                total += Int64(n)
            }
            if total != file.size || hasher.finalize() != digest.hex { bad.append(i) }
        }
        return bad
    }

    /// Moves a finished staging folder into place with one rename. An existing folder is never
    /// replaced. The game's folder is kept out of the device's backup: it can be downloaded again.
    static func commit(stage: URL, to destination: URL) throws {
        try FileManager.default.createDirectory(at: destination.deletingLastPathComponent(), withIntermediateDirectories: true)
        #if canImport(Darwin)
        let moved = renamex_np(stage.path, destination.path, UInt32(RENAME_EXCL))
        #else
        let moved: Int32 = FileManager.default.fileExists(atPath: destination.path) ? { errno = EEXIST; return -1 }() : rename(stage.path, destination.path)
        #endif
        guard moved == 0 else { throw errno == EEXIST ? BCDContentError.folderExists : BCDContentError.io(errno) }
        var folder = destination
        var values = URLResourceValues()
        values.isExcludedFromBackup = true
        try? folder.setResourceValues(values)
    }

    /// Takes a folder out of sight at once (renamed into `trash`, on the same volume), then
    /// deletes it off the main thread.
    static func remove(_ folder: URL, trash: URL) async {
        var target = folder
        try? FileManager.default.createDirectory(at: trash, withIntermediateDirectories: true)
        let moved = trash.appendingPathComponent(UUID().uuidString, isDirectory: true)
        if rename(folder.path, moved.path) == 0 { target = moved }
        await Task.detached(priority: .utility) { try? FileManager.default.removeItem(at: target) }.value
    }
}

/// Download progress, shared by the workers without a hop per piece.
final class BCDContentMeter: @unchecked Sendable {
    private let lock = NSLock()
    private var done: Int64
    private var inFlight: Int64 = 0
    private let total: Int64
    private var samples: [(Date, Int64)] = []

    init(done: Int64, total: Int64) {
        self.done = done
        self.total = total
    }

    func add(_ bytes: Int64) { lock.lock(); inFlight += bytes; lock.unlock() }
    func drop(_ bytes: Int64) { lock.lock(); inFlight -= bytes; lock.unlock() }
    func finished(fetched: Int64, size: Int64) { lock.lock(); inFlight -= fetched; done += size; lock.unlock() }

    func progress(_ phase: BCDContentProgress.Phase) -> BCDContentProgress {
        lock.lock()
        defer { lock.unlock() }
        let now = Date(), bytes = min(total, done + max(0, inFlight))
        samples.append((now, bytes))
        samples.removeAll { now.timeIntervalSince($0.0) > 3 }
        var speed = 0.0
        if let first = samples.first, now.timeIntervalSince(first.0) > 0.5 {
            speed = Double(bytes - first.1) / now.timeIntervalSince(first.0)
        }
        return BCDContentProgress(phase: phase, doneBytes: bytes, totalBytes: total, bytesPerSecond: max(0, speed))
    }
}

/// One pass over a plan's pending chunks: hands them to the workers, and makes the finished
/// ones durable and journaled in batches.
private actor BCDContentRun {
    let plan: BCDContentPlan
    let index: BCDContentPlan.Index
    let stage: URL
    let journal: BCDContentJournal
    let options: BCDContentOptions
    let meter: BCDContentMeter
    private var pending: [Int]
    private var next = 0
    private var unsynced: [Int] = []
    private var touched = Set<Int>()
    private var unsyncedBytes: Int64 = 0
    private var lastSync = Date()
    private var sinceSpaceCheck: Int64 = 0
    private(set) var finishedChunks = Set<Int>()

    init(plan: BCDContentPlan, index: BCDContentPlan.Index, stage: URL, journal: BCDContentJournal, pending: [Int],
         options: BCDContentOptions, meter: BCDContentMeter) {
        self.plan = plan
        self.index = index
        self.stage = stage
        self.journal = journal
        self.pending = pending
        self.options = options
        self.meter = meter
    }

    private func take() -> Int? {
        guard next < pending.count else { return nil }
        next += 1
        return pending[next - 1]
    }

    /// One worker: its own connection, chunk after chunk until none is left.
    nonisolated func work(source: BCDChunkSource, health: BCDHostHealth) async throws {
        let stream = BCDHTTPStream()
        defer { stream.close() }
        let writer = BCDChunkWriter(plan: plan, index: index, stage: stage, meter: meter)
        defer { writer.close() }
        while let chunk = await take() {
            try await fetch(chunk, writer: writer, stream: stream, source: source, health: health)
            try await finished(chunk)
        }
    }

    private nonisolated func fetch(_ c: Int, writer: BCDChunkWriter, stream: BCDHTTPStream,
                                   source: BCDChunkSource, health: BCDHostHealth) async throws {
        let chunk = plan.chunks[c]
        var last = "none"
        for attempt in 0..<max(1, options.attempts) {
            try Task.checkCancellation()
            guard let url = health.order(try await source.urls(for: chunk)).first else {
                throw BCDContentError.chunkFailed("no-address")
            }
            writer.start(c)
            do {
                try await source.fetch(chunk, url: url, into: writer, stream: stream)
                try writer.finish()
                health.succeeded(url)
                return
            } catch let error as BCDContentError {
                writer.abandon()
                switch error {
                case .http(let status) where status == 401 || status == 403 || status == 410:
                    // The addresses expired or were refused: the store renews them, no host is blamed.
                    last = "http-\(status)"
                    try await source.refresh(group: chunk.group)
                    continue
                case .diskFull, .io:
                    throw error
                default:
                    last = error.logReason
                    health.failed(url)
                }
            } catch is CancellationError {
                writer.abandon()
                throw CancellationError()
            } catch {
                writer.abandon()
                if Task.isCancelled { throw CancellationError() }
                last = (error as? URLError).map { "url-\($0.code.rawValue)" } ?? "network"
                health.failed(url)
            }
            if options.backoff > 0, attempt + 1 < options.attempts {
                try await Task.sleep(nanoseconds: UInt64(min(8, options.backoff * pow(2, Double(attempt))) * 1_000_000_000))
            }
        }
        throw BCDContentError.chunkFailed(last)
    }

    private func finished(_ c: Int) throws {
        let chunk = plan.chunks[c]
        meter.finished(fetched: chunk.fetchSize, size: chunk.fetchSize)
        unsynced.append(c)
        for part in plan.parts[index.partRanges[c]] { touched.insert(part.file) }
        unsyncedBytes += chunk.size
        sinceSpaceCheck += chunk.size
        if unsyncedBytes >= options.durableBytes || Date().timeIntervalSince(lastSync) >= options.durableSeconds {
            try flush()
        }
        // The device may have filled up since the start (another app, a Steam download).
        if sinceSpaceCheck >= 1 << 30 {
            sinceSpaceCheck = 0
            let remaining = BCDContentEngine.remainingBytes(plan, done: finishedChunks.union(unsynced))
            if remaining > 0, let available = options.freeSpace(stage), available < min(remaining, 64 << 20) + (64 << 20) {
                throw BCDContentError.notEnoughSpace(needed: remaining, available: available)
            }
        }
    }

    /// Makes the finished chunks' bytes durable, then journals them.
    func flush() throws {
        lastSync = Date()
        guard !unsynced.isEmpty else { return }
        var last: Int32 = -1
        for file in touched.sorted() {
            let fd = open(stage.appendingPathComponent(plan.files[file].path).path, O_WRONLY | O_NOFOLLOW)
            guard fd >= 0 else { throw BCDContentError.io(errno) }
            if fsync(fd) != 0 { let failure = errno; close(fd); throw BCDContentError.io(failure) }
            if last >= 0 { close(last) }
            last = fd
        }
        #if canImport(Darwin)
        // fsync leaves the bytes in the drive's cache; one full sync pushes every file's out.
        if last >= 0 { _ = fcntl(last, F_FULLFSYNC) }
        #endif
        if last >= 0 { close(last) }
        try journal.append(unsynced)
        finishedChunks.formUnion(unsynced)
        unsynced.removeAll()
        touched.removeAll()
        unsyncedBytes = 0
    }
}

/// Receives one chunk at a time from the network: checks the bytes on the wire, unpacks them,
/// checks the result, and writes every part of the chunk into its file as the bytes go by.
final class BCDChunkWriter: BCDHTTPConsumer {
    private let plan: BCDContentPlan
    private let index: BCDContentPlan.Index
    private let stage: URL
    private let meter: BCDContentMeter
    private var chunk: BCDContentChunk!
    private var parts: ArraySlice<BCDContentPart> = []
    private var cursor = 0
    private var fetchHasher: BCDHasher?
    private var hasher: BCDHasher?
    private var inflater: BCDInflater?
    private var produced: Int64 = 0
    private var files: [Int: Int32] = [:]
    private(set) var received: Int64 = 0

    init(plan: BCDContentPlan, index: BCDContentPlan.Index, stage: URL, meter: BCDContentMeter) {
        self.plan = plan
        self.index = index
        self.stage = stage
        self.meter = meter
    }

    deinit { close() }

    func start(_ c: Int) {
        chunk = plan.chunks[c]
        parts = plan.parts[index.partRanges[c]]
        received = 0
        reset()
    }

    private func reset() {
        meter.drop(received)
        received = 0
        produced = 0
        cursor = parts.startIndex
        fetchHasher = chunk.fetchDigest.map { BCDHasher($0.kind) }
        hasher = chunk.digest.map { BCDHasher($0.kind) }
        inflater = nil
    }

    /// A failed try: what it received no longer counts.
    func abandon() {
        meter.drop(received)
        received = 0
        close()
    }

    func begin(at offset: Int64) throws {
        if offset == 0 { reset() } else if offset != received { throw BCDContentError.http(206) }
        if chunk.codec == .zlib, inflater == nil { inflater = try BCDInflater() }
    }

    func receive(_ data: Data) throws {
        guard received + Int64(data.count) <= chunk.fetchSize else { throw BCDContentError.tooLarge }
        received += Int64(data.count)
        meter.add(Int64(data.count))
        try data.withUnsafeBytes { raw in
            fetchHasher?.update(raw)
            switch chunk.codec {
            case .stored: try emit(raw)
            case .zlib: try inflater!.feed(raw) { try emit($0) }
            }
        }
    }

    private func emit(_ bytes: UnsafeRawBufferPointer) throws {
        let start = produced, end = produced + Int64(bytes.count)
        guard end <= chunk.size else { throw BCDContentError.tooLarge }
        hasher?.update(bytes)
        var i = cursor
        while i < parts.endIndex, parts[i].chunkOffset < end {
            let part = parts[i]
            let from = max(start, part.chunkOffset), to = min(end, part.chunkOffset + part.length)
            if from < to {
                try write(file: part.file, bytes.baseAddress! + Int(from - start), count: Int(to - from),
                          at: part.fileOffset + (from - part.chunkOffset))
            }
            i += 1
        }
        produced = end
        while cursor < parts.endIndex, parts[cursor].chunkOffset + parts[cursor].length <= produced { cursor += 1 }
    }

    private func write(file: Int, _ pointer: UnsafeRawPointer, count: Int, at offset: Int64) throws {
        var fd = files[file] ?? -1
        if fd < 0 {
            if files.count >= 32 { close() }
            fd = open(stage.appendingPathComponent(plan.files[file].path).path, O_WRONLY | O_NOFOLLOW)
            guard fd >= 0 else { throw BCDContentError.io(errno) }
            files[file] = fd
        }
        var written = 0
        while written < count {
            let n = pwrite(fd, pointer + written, count - written, off_t(offset) + off_t(written))
            if n < 0 {
                if errno == EINTR { continue }
                throw errno == ENOSPC ? BCDContentError.diskFull : BCDContentError.io(errno)
            }
            written += n
        }
    }

    /// The whole chunk arrived: every check must pass before it counts.
    func finish() throws {
        if let inflater { try inflater.finish() } else if chunk.codec == .zlib { throw BCDContentError.decode }
        guard received == chunk.fetchSize, produced == chunk.size else { throw BCDContentError.checkFailed }
        if let digest = chunk.fetchDigest, fetchHasher?.finalize() != digest.hex { throw BCDContentError.checkFailed }
        if let digest = chunk.digest, hasher?.finalize() != digest.hex { throw BCDContentError.checkFailed }
        received = 0   // the meter now counts the chunk as finished
        close()
    }

    func close() {
        for fd in files.values { Darwin.close(fd) }
        files.removeAll()
    }
}

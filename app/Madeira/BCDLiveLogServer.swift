// SPDX-License-Identifier: GPL-3.0-only
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

import Darwin
import Foundation
import Network

/// Serves the current raw log without depending on the UI log reader or its filters.
final class BCDLiveLogServer: @unchecked Sendable {
    private final class Client: @unchecked Sendable {
        let connection: NWConnection
        var request = Data()
        var file: FileHandle?

        init(_ connection: NWConnection) { self.connection = connection }
    }

    private static let endOfHeaders = Data("\r\n\r\n".utf8)
    private static let endOfLine = Data("\r\n".utf8)
    private static let requestLimit = 4096
    private static let replayBytes: UInt64 = 64 * 1024
    private let queue = DispatchQueue(label: "bcd.live-logs", qos: .utility)
    private let listener: NWListener
    private let logURL: URL
    let address: IPv4Address
    let interface: NWInterface?
    private let target = "/api/log?token=\(UUID().uuidString)"
    private var clients: [ObjectIdentifier: Client] = [:]
    private var stopped = false

    init(logURL: URL, address: IPv4Address, interface: NWInterface?,
         status: @escaping @Sendable (Result<URL, NWError>) -> Void) throws {
        self.logURL = logURL
        self.address = address
        self.interface = interface
        let parameters = NWParameters.tcp
        parameters.requiredInterface = interface
        parameters.requiredLocalEndpoint = .hostPort(host: .ipv4(address), port: .any)
        listener = try NWListener(using: parameters)
        listener.newConnectionHandler = { [weak self] connection in
            guard let self else { connection.cancel(); return }
            self.accept(connection)
        }
        listener.stateUpdateHandler = { [weak self] state in
            guard let self, !self.stopped else { return }
            switch state {
            case .ready:
                guard let port = self.listener.port,
                      let url = URL(string: "http://\(address):\(port.rawValue)\(self.target)") else {
                    self.closeAll()
                    status(.failure(.posix(.EADDRNOTAVAIL)))
                    return
                }
                status(.success(url))
            case .failed(let error), .waiting(let error):
                self.closeAll()
                status(.failure(error))
            default:
                break
            }
        }
        listener.start(queue: queue)
    }

    deinit { closeAll() }

    func stop() { queue.async { self.closeAll() } }

    private func closeAll() {
        stopped = true
        listener.cancel()
        for client in clients.values { client.connection.cancel() }
        clients.removeAll()
    }

    private func close(_ client: Client) {
        clients.removeValue(forKey: ObjectIdentifier(client.connection))
        client.connection.cancel()
    }

    private func accept(_ connection: NWConnection) {
        guard !stopped, clients.count < 4 else { connection.cancel(); return }
        let client = Client(connection)
        clients[ObjectIdentifier(connection)] = client
        connection.stateUpdateHandler = { [weak self, weak client] state in
            guard let client else { return }
            if case .failed = state { self?.close(client) }
        }
        connection.start(queue: queue)
        readRequest(client)
        queue.asyncAfter(deadline: .now() + 5) { [weak self, weak client] in
            guard let client, client.file == nil else { return }
            self?.close(client)
        }
    }

    private func readRequest(_ client: Client) {
        client.connection.receive(minimumIncompleteLength: 1, maximumLength: Self.requestLimit - client.request.count) {
            [weak self, weak client] data, _, complete, error in
            guard let self, let client else { return }
            guard error == nil, let data else { self.close(client); return }
            client.request.append(data)
            if client.request.range(of: Self.endOfHeaders) != nil,
               let firstLineEnd = client.request.range(of: Self.endOfLine)?.lowerBound {
                self.respond(to: client, firstLine: client.request.prefix(upTo: firstLineEnd))
            } else if complete || client.request.count >= Self.requestLimit {
                self.reject(client, status: "400 Bad Request")
            } else {
                self.readRequest(client)
            }
        }
    }

    private func respond(to client: Client, firstLine: Data) {
        client.request = Data()
        guard firstLine == Data("GET \(target) HTTP/1.1".utf8) || firstLine == Data("GET \(target) HTTP/1.0".utf8) else {
            reject(client, status: "404 Not Found")
            return
        }

        do {
            let file = try FileHandle(forReadingFrom: logURL)
            let end = try file.seekToEnd()
            try file.seek(toOffset: end - min(end, Self.replayBytes))
            client.file = file
        } catch {
            reject(client, status: "500 Internal Server Error")
            return
        }

        let headers = "Content-Type: text/plain; charset=utf-8\r\nX-Content-Type-Options: nosniff\r\nReferrer-Policy: no-referrer\r\n"
        send(client, data: Self.response("200 OK", headers: headers))
        // A quiet log still needs to notice when the reader closes its connection.
        client.connection.receive(minimumIncompleteLength: 1, maximumLength: 1) { [weak self, weak client] _, _, _, _ in
            guard let client else { return }
            self?.close(client)
        }
    }

    private static func response(_ status: String, headers: String) -> Data {
        Data("HTTP/1.1 \(status)\r\nConnection: close\r\nCache-Control: no-store\r\n\(headers)\r\n".utf8)
    }

    private func reject(_ client: Client, status: String) {
        client.connection.send(content: Self.response(status, headers: "Content-Length: 0\r\n"),
                               contentContext: .finalMessage, isComplete: true, completion: .contentProcessed { [weak self, weak client] _ in
            guard let client else { return }
            self?.close(client)
        })
    }

    private func send(_ client: Client, data: Data) {
        client.connection.send(content: data, completion: .contentProcessed { [weak self, weak client] error in
            guard let self, let client else { return }
            if error == nil { self.tail(client) } else { self.close(client) }
        })
    }

    private func tail(_ client: Client) {
        guard let file = client.file else { return }
        do {
            if let data = try file.read(upToCount: 32 * 1024), !data.isEmpty {
                send(client, data: data)
            } else {
                if logWasReplaced(file) { client.file = try FileHandle(forReadingFrom: logURL) }
                queue.asyncAfter(deadline: .now() + .milliseconds(250)) { [weak self, weak client] in
                    guard let client else { return }
                    self?.tail(client)
                }
            }
        } catch {
            close(client)
        }
    }

    private func logWasReplaced(_ file: FileHandle) -> Bool {
        // Settings' Clear Log replaces the file while an existing reader has it open.
        var openFile = stat(), currentFile = stat()
        return fstat(file.fileDescriptor, &openFile) == 0 && stat(logURL.path, &currentFile) == 0
            && (openFile.st_dev != currentFile.st_dev || openFile.st_ino != currentFile.st_ino)
    }

    static func ipv4Address(on interface: NWInterface) -> IPv4Address? {
        var list: UnsafeMutablePointer<ifaddrs>?
        guard getifaddrs(&list) == 0, let first = list else { return nil }
        defer { freeifaddrs(first) }
        var entry: UnsafeMutablePointer<ifaddrs>? = first
        while let current = entry?.pointee {
            entry = current.ifa_next
            guard String(cString: current.ifa_name) == interface.name,
                  let address = current.ifa_addr, address.pointee.sa_family == sa_family_t(AF_INET) else { continue }
            var host = [CChar](repeating: 0, count: Int(INET_ADDRSTRLEN))
            if getnameinfo(address, socklen_t(address.pointee.sa_len), &host, socklen_t(host.count), nil, 0, NI_NUMERICHOST) == 0 {
                return IPv4Address(String(cString: host))
            }
        }
        return nil
    }
}

// SPDX-License-Identifier: GPL-3.0-only
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

import Darwin
import dnssd
import Foundation
import Network

/// Serves the current raw log without depending on the UI log reader or its filters.
final class BCDLiveLogServer: @unchecked Sendable {
    struct IPv4Network: Equatable {
        let address: IPv4Address
        let netmask: IPv4Address

        func contains(_ peer: IPv4Address) -> Bool {
            zip(zip(address.rawValue, peer.rawValue), netmask.rawValue).allSatisfy { addresses, mask in
                (addresses.0 & mask) == (addresses.1 & mask)
            }
        }
    }

    private final class Client: @unchecked Sendable {
        let connection: NWConnection
        var request = Data()
        var file: FileHandle?

        init(_ connection: NWConnection) { self.connection = connection }
    }

    private static let endOfHeaders = Data("\r\n\r\n".utf8)
    private static let requestLimit = 4096
    private static let replayBytes: UInt64 = 64 * 1024
    static let port: NWEndpoint.Port = 8766
    private let queue = DispatchQueue(label: "bcd.live-logs", qos: .utility)
    private let listener: NWListener
    private let logURL: URL
    let network: IPv4Network
    let interface: NWInterface?
    private var registration: NWEndpoint?
    private var hostname: String?
    private var clients: [ObjectIdentifier: Client] = [:]
    private var stopped = false

    init(logURL: URL, network: IPv4Network, interface: NWInterface?, port: NWEndpoint.Port = BCDLiveLogServer.port,
         status: @escaping @Sendable (Result<URL, NWError>) -> Void) throws {
        self.logURL = logURL
        self.network = network
        self.interface = interface
        let parameters = NWParameters.tcp
        parameters.requiredInterface = interface
        parameters.requiredLocalEndpoint = .hostPort(host: .ipv4(network.address), port: port)
        listener = try NWListener(using: parameters)
        if let interface {
            listener.service = NWListener.Service(name: "BCD Live Logs", type: "_http._tcp", domain: "local.",
                                                  txtRecord: NWTXTRecord(["path": "/api/log"]))
            listener.serviceRegistrationUpdateHandler = { [weak self] change in
                self?.updateRegistration(change, interfaceIndex: UInt32(interface.index), status: status)
            }
        }
        listener.newConnectionHandler = { [weak self] connection in
            guard let self else { connection.cancel(); return }
            self.accept(connection)
        }
        listener.stateUpdateHandler = { [weak self] state in
            guard let self, !self.stopped else { return }
            switch state {
            case .ready:
                self.reportURL(status)
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

    private func reportURL(_ status: (Result<URL, NWError>) -> Void) {
        guard let port = listener.port,
              let url = URL(string: "http://\(hostname ?? String(describing: network.address)):\(port.rawValue)/api/log") else {
            closeAll()
            status(.failure(.posix(.EADDRNOTAVAIL)))
            return
        }
        status(.success(url))
    }

    private func updateRegistration(_ change: NWListener.ServiceRegistrationChange, interfaceIndex: UInt32,
                                    status: @escaping @Sendable (Result<URL, NWError>) -> Void) {
        guard !stopped else { return }
        switch change {
        case .add(let endpoint):
            registration = endpoint
            DispatchQueue.global(qos: .utility).async { [weak self] in
                guard let hostname = Self.resolveHostname(endpoint, interfaceIndex: interfaceIndex) else { return }
                self?.queue.async { [weak self] in
                    guard let self, !self.stopped, self.registration == endpoint else { return }
                    self.hostname = hostname
                    if self.listener.port != nil { self.reportURL(status) }
                }
            }
        case .remove(let endpoint):
            guard registration == endpoint else { return }
            registration = nil
            hostname = nil
            if listener.port != nil { reportURL(status) }
        @unknown default:
            break
        }
    }

    // Resolve the service's SRV record to get the device's .local hostname.
    private static func resolveHostname(_ endpoint: NWEndpoint, interfaceIndex: UInt32) -> String? {
        guard case let .service(name, type, domain, _) = endpoint else { return nil }
        var hostname: String?
        return withUnsafeMutablePointer(to: &hostname) { result in
            var service: DNSServiceRef?
            let error = DNSServiceResolve(&service, 0, interfaceIndex, name, type, domain, { _, _, _, error, _, host, _, _, _, context in
                guard error == kDNSServiceErr_NoError, let host, let context else { return }
                context.assumingMemoryBound(to: String?.self).pointee = String(cString: host)
            }, result)
            guard error == kDNSServiceErr_NoError, let service else { return nil }
            defer { DNSServiceRefDeallocate(service) }
            var descriptor = pollfd(fd: DNSServiceRefSockFD(service), events: Int16(POLLIN), revents: 0)
            guard poll(&descriptor, 1, 2000) > 0, descriptor.revents & Int16(POLLIN) != 0,
                  DNSServiceProcessResult(service) == kDNSServiceErr_NoError,
                  let name = result.pointee, name.lowercased().hasSuffix(".local.") else { return nil }
            return String(name.dropLast())
        }
    }

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
        guard !stopped, clients.count < 4,
              case .hostPort(.ipv4(let peer), _) = connection.endpoint, network.contains(peer) else {
            connection.cancel()
            return
        }
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
            if let headers = client.request.range(of: Self.endOfHeaders) {
                self.respond(to: client, headers: client.request.prefix(upTo: headers.lowerBound))
            } else if complete || client.request.count >= Self.requestLimit {
                self.reject(client, status: "400 Bad Request")
            } else {
                self.readRequest(client)
            }
        }
    }

    private func respond(to client: Client, headers: Data) {
        client.request = Data()
        let lines = String(decoding: headers, as: UTF8.self).components(separatedBy: "\r\n")
        let legacy = lines.first == "GET /api/log HTTP/1.0"
        guard legacy || lines.first == "GET /api/log HTTP/1.1" else {
            reject(client, status: "404 Not Found")
            return
        }
        let hosts = lines.dropFirst().filter { $0.lowercased().hasPrefix("host:") }
        guard (legacy && hosts.isEmpty) || (hosts.count == 1 && allowsHost(hosts[0].dropFirst(5))) else {
            reject(client, status: "403 Forbidden")
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

        let response = "Content-Type: text/plain; charset=utf-8\r\nX-Content-Type-Options: nosniff\r\nReferrer-Policy: no-referrer\r\n"
        send(client, data: Self.response("200 OK", headers: response))
        // A quiet log still needs to notice when the reader closes its connection.
        client.connection.receive(minimumIncompleteLength: 1, maximumLength: 1) { [weak self, weak client] _, _, _, _ in
            guard let client else { return }
            self?.close(client)
        }
    }

    private func allowsHost(_ value: Substring) -> Bool {
        // Match the listener's own addresses to block public DNS rebinding.
        let parts = value.trimmingCharacters(in: .whitespaces).lowercased().split(separator: ":", omittingEmptySubsequences: false)
        guard parts.count == 1 || (parts.count == 2 && UInt16(parts[1]) == listener.port?.rawValue) else { return false }
        let host = parts[0].hasSuffix(".") ? parts[0].dropLast() : parts[0]
        return host == String(describing: network.address) || String(host) == hostname?.lowercased()
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

    static func ipv4Network(on interface: NWInterface) -> IPv4Network? {
        var list: UnsafeMutablePointer<ifaddrs>?
        guard getifaddrs(&list) == 0, let first = list else { return nil }
        defer { freeifaddrs(first) }
        var entry: UnsafeMutablePointer<ifaddrs>? = first
        while let current = entry?.pointee {
            entry = current.ifa_next
            guard String(cString: current.ifa_name) == interface.name,
                  let address = ipv4(current.ifa_addr), let netmask = ipv4(current.ifa_netmask),
                  netmask.rawValue.contains(where: { $0 != 0 }) else { continue }
            return IPv4Network(address: address, netmask: netmask)
        }
        return nil
    }

    private static func ipv4(_ socket: UnsafePointer<sockaddr>?) -> IPv4Address? {
        guard let socket, socket.pointee.sa_family == sa_family_t(AF_INET) else { return nil }
        let address = socket.withMemoryRebound(to: sockaddr_in.self, capacity: 1) { $0.pointee.sin_addr }
        return withUnsafeBytes(of: address) { IPv4Address(Data($0)) }
    }
}

#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""Compile the live log server and exercise it over macOS loopback TCP."""

from contextlib import contextmanager
from http.client import HTTPConnection
from pathlib import Path
from select import select
from socket import create_connection
from urllib.parse import urlsplit
import os
import subprocess
import sys
import tempfile
import unittest


SERVER = Path(__file__).resolve().parents[2] / "app/Madeira/BCDLiveLogServer.swift"
TIMEOUT = 5
HARNESS = r"""
import Foundation
import Network

func reply(_ message: String) {
    FileHandle.standardOutput.write(Data((message + "\n").utf8))
}

let logURL = URL(fileURLWithPath: CommandLine.arguments[1])
let network = BCDLiveLogServer.IPv4Network(address: .loopback, netmask: IPv4Address("255.0.0.0")!)
let subnet = BCDLiveLogServer.IPv4Network(address: IPv4Address("192.168.42.65")!,
                                          netmask: IPv4Address("255.255.255.192")!)
let macAddress = IPv4Address("192.168.42.66")!
precondition(subnet.contains(macAddress))
precondition(!subnet.contains(IPv4Address("192.168.42.63")!))
precondition(!subnet.contains(IPv4Address("192.168.42.128")!))

let validateHostname = BCDLiveLogServer.validatedHostname
let hostname = "iphone.local."
precondition(validateHostname(hostname, subnet.address, [subnet.address]) == "iphone.local")
precondition(validateHostname(hostname, subnet.address, [macAddress]) == nil)
precondition(validateHostname(hostname, subnet.address, [subnet.address, macAddress]) == nil)
precondition(validateHostname(hostname, subnet.address, []) == nil)
precondition(validateHostname("iphone.example.", subnet.address, [subnet.address]) == nil)

var server: BCDLiveLogServer?
while let line = readLine() {
    let command = line.split(separator: " ")
    switch command[0] {
    case "start":
        let port = NWEndpoint.Port(rawValue: UInt16(command[1])!)!
        server = try BCDLiveLogServer(logURL: logURL, network: network, interface: nil, port: port) { result in
            switch result {
            case .success(let url): reply(url.absoluteString)
            case .failure(let error): fatalError("Listener failed: \(error)")
            }
        }
    case "stop":
        server?.stop()
        server = nil
        reply("stopped")
    default:
        fatalError("Unknown command: \(line)")
    }
}
"""


@contextmanager
def request(endpoint, target=None, host=None):
    connection = HTTPConnection(endpoint.hostname, endpoint.port, timeout=TIMEOUT)
    try:
        headers = {"Host": host} if host is not None else {}
        connection.request("GET", target if target is not None else endpoint.path, headers=headers)
        with connection.getresponse() as response:
            yield response
    finally:
        connection.close()


class LiveLogServerTest(unittest.TestCase):
    def command(self, server, command):
        server.stdin.write(command + "\n")
        server.stdin.flush()
        self.assertTrue(select([server.stdout], [], [], TIMEOUT)[0], f"No reply to {command}")
        line = server.stdout.readline()
        self.assertTrue(line, f"Server exited during {command}")
        return line.strip()

    def start(self, server, port=0):
        endpoint = urlsplit(self.command(server, f"start {port}"))
        self.assertEqual((endpoint.scheme, endpoint.hostname, endpoint.path), ("http", "127.0.0.1", "/api/log"))
        self.assertEqual(endpoint.query, "")
        if port:
            self.assertEqual(endpoint.port, port)
        else:
            self.assertGreater(endpoint.port, 0)
        return endpoint

    def assert_rejected(self, endpoint, status, *, target=None, host=None):
        with request(endpoint, target, host) as response:
            self.assertEqual(response.status, status)
            self.assertEqual(response.read(), b"")

    def test_lan_live_stream(self):
        with tempfile.TemporaryDirectory(prefix="bcd-live-log-") as directory:
            folder = Path(directory)
            source, binary = folder / "main.swift", folder / "live-log-server"
            source.write_text(HARNESS)
            subprocess.run([os.environ.get("SWIFTC", "swiftc"), "-module-cache-path", str(folder / "modules"),
                            str(SERVER), str(source), "-o", str(binary)], check=True, timeout=120)

            log = folder / "bcd-log.txt"
            recent = b"recent log line\n" * 4096
            log.write_bytes(b"older log entry\n" + recent)
            with subprocess.Popen([binary, log], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True) as server:
                try:
                    endpoint = self.start(server)
                    self.assert_rejected(endpoint, 404, target="/api/log?extra=value")
                    self.assert_rejected(endpoint, 404, target="/another-file")
                    self.assert_rejected(endpoint, 403, host="bad.example")
                    self.assert_rejected(endpoint, 403, host="unrelated.local")

                    with request(endpoint) as first, request(endpoint, host=endpoint.hostname) as second:
                        for stream in (first, second):
                            self.assertEqual(stream.status, 200)
                            self.assertEqual(stream.headers.get_content_type(), "text/plain")
                            self.assertEqual(stream.headers["Cache-Control"], "no-store")
                            self.assertEqual(stream.read(len(recent)), recent)

                        appended = b"written while both readers are connected\n"
                        with log.open("ab") as file:
                            file.write(appended)
                        for stream in (first, second):
                            self.assertEqual(stream.read(len(appended)), appended)

                        replacement = b"new log after Clear Log\n"
                        new_log = log.with_suffix(".new")
                        new_log.write_bytes(replacement)
                        new_log.replace(log)
                        for stream in (first, second):
                            self.assertEqual(stream.read(len(replacement)), replacement)

                        self.assertEqual(self.command(server, "stop"), "stopped")
                        for stream in (first, second):
                            try:
                                self.assertEqual(stream.read(1), b"")
                            except ConnectionResetError:
                                pass

                    with self.assertRaises(ConnectionRefusedError):
                        create_connection((endpoint.hostname, endpoint.port), timeout=TIMEOUT).close()

                    next_endpoint = self.start(server, port=endpoint.port)
                    self.assertEqual(next_endpoint, endpoint)
                    with request(next_endpoint) as stream:
                        self.assertEqual(stream.status, 200)
                        self.assertEqual(stream.read(len(replacement)), replacement)
                finally:
                    server.kill()


if __name__ == "__main__":
    if sys.platform != "darwin":
        sys.exit("This check requires macOS and Swift with Network.framework.")
    unittest.main(verbosity=2)

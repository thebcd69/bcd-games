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
var server: BCDLiveLogServer?
while let command = readLine() {
    switch command {
    case "start":
        server = try BCDLiveLogServer(logURL: logURL, address: .loopback, interface: nil) { result in
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
        fatalError("Unknown command: \(command)")
    }
}
"""


@contextmanager
def request(endpoint, target=None):
    connection = HTTPConnection(endpoint.hostname, endpoint.port, timeout=TIMEOUT)
    try:
        connection.request("GET", target if target is not None else f"{endpoint.path}?{endpoint.query}")
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

    def start(self, server):
        endpoint = urlsplit(self.command(server, "start"))
        self.assertEqual((endpoint.scheme, endpoint.hostname, endpoint.path), ("http", "127.0.0.1", "/api/log"))
        self.assertGreater(endpoint.port, 0)
        self.assertTrue(endpoint.query.startswith("token=") and len(endpoint.query) > len("token="))
        return endpoint

    def assert_rejected(self, endpoint, target):
        with request(endpoint, target) as response:
            self.assertEqual(response.status, 404)
            self.assertEqual(response.read(), b"")

    def test_protected_live_stream(self):
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
                    self.assert_rejected(endpoint, "/api/log")
                    self.assert_rejected(endpoint, "/api/log?token=invalid")
                    self.assert_rejected(endpoint, f"/another-file?{endpoint.query}")

                    with request(endpoint) as first, request(endpoint) as second:
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

                    next_endpoint = self.start(server)
                    self.assertNotEqual(next_endpoint.query, endpoint.query)
                    self.assert_rejected(next_endpoint, f"{endpoint.path}?{endpoint.query}")
                    with request(next_endpoint) as stream:
                        self.assertEqual(stream.status, 200)
                finally:
                    server.kill()


if __name__ == "__main__":
    if sys.platform != "darwin":
        sys.exit("This check requires macOS and Swift with Network.framework.")
    unittest.main(verbosity=2)

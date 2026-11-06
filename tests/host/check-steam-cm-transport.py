#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""The Steam connection retries on other servers, and the library is fetched again on returning to the app (round 632, upstream v0.2.0 #477, #538).

Never contacts Steam. BCD logs: "[steam-library] failed reason=SteamError" in 101 logs (builds 565-630), typically 3-45 s after the start; in 45 of
them the playtime call over the same connection succeeded a median 31 s later, so the first connect was the problem. The list was never
refreshed again in any of the 101 (the app stays alive for days; the library refreshed once per app start).

Part A, the connection (app/Madeira/SwiftSteam/Core/SteamConnection.swift, CMServerList.swift, SteamError.swift): the production connect loop,
clearTransport, disconnect, handleDisconnect and received are cut out of the source and run over a scripted transport with the production
endpoint selection (CMServerList) and error policy (SteamCMTransportPolicy):
  - a connect that works is unchanged: one attempt, the best server, a receive loop, no failure marked;
  - a timeout, DNS or connection failure, a lost connection or a generic TLS failure tries another server, at most three distinct ones, and
    the half-open task and session are released before the next try;
  - a certificate failure, cancellation, an offline error and an unknown error stop at once; no endpoint is tried twice; with one server the
    last error is kept; no URL, header or payload text survives in the error or the log;
  - a cancelled caller (also after a late success) leaves nothing published; a replaced connect neither cancels nor feeds the new one;
  - a replaced connection cannot deliver messages or close the newer one; a lost connection fires the disconnect handler once and still marks
    its CM failed when the session reconnects.
  The ping continuation keeps BCD's LogonResumeGuard (a second resume ended the app: tester log ZH16KH, build 574).
Part B, the library (SteamOwnedLibrary.refreshAfterForeground): the production method over a scripted library: nothing when signed out, fresh,
busy or in a session; one fetch for a stale or never loaded library, however many activations come before the task runs; again after a minute;
a manual or startup refresh counts as an attempt; the app's foreground hook calls it.
"""
from pathlib import Path
import os, re, shutil, subprocess, sys, tempfile

root = Path(__file__).resolve().parents[2]
ok = True


def check(what, cond, detail=""):
    global ok
    print(("ok   " if cond else "FAIL ") + what + (("\n     " + str(detail)[:1500]) if (detail and not cond) else ""))
    ok = ok and bool(cond)


core = root / "app/Madeira/SwiftSteam/Core"
conn = (core / "SteamConnection.swift").read_text()
error_swift = (core / "SteamError.swift").read_text()
servers_swift = (core / "CMServerList.swift").read_text()
session_swift = (core / "SteamSession.swift").read_text()
owned = (root / "app/Madeira/SteamOwnedLibrary.swift").read_text()


def function(text, signature):
    a = text.index(signature)
    b = text.index("{", a)
    depth, c = 1, b + 1
    while depth:
        depth += (text[c] == "{") - (text[c] == "}")
        c += 1
    return text[a:c]


# ---------------------------------------------------------------------------------------------------------------- static
check("the connect loop tries at most three distinct endpoints and keeps the pong continuation's once-guard",
      "for attempt in 1...3 {" in conn and "serverList.getServer(excluding: tried)" in conn
      and "guard once.claim() else { return }" in function(conn, "private func connect(to server: CMServerList.CMServer)")
      and "let once = SteamSession.LogonResumeGuard()" in function(conn, "private func connect(to server: CMServerList.CMServer)"))
check("no TLS trust override: URLSession's certificate and host name checks are untouched",
      "didReceive challenge" not in conn and "serverTrust" not in conn and "ServerTrustPolicy" not in conn)
check("the credit lines for the adapted code are in the three files and in docs/STEAM_LIBRARY.md",
      all("kuscholar/iDeck" in t for t in (conn, error_swift, servers_swift)) and "kuscholar/iDeck" in (root / "docs/STEAM_LIBRARY.md").read_text())
check("a log line never carries the error's text or URL: only the sanitized domain and code",
      "SteamLog.event(\"[steam-cm-transport] attempt=\\(attempt) reason=\\(failure)\")" in conn
      and "localizedDescription" not in function(conn, "actor SteamConnection {").split("// MARK: - Receive")[0])
check("the library's failure reason names the connection cases",
      all(w in owned for w in ('case SteamError.noServersAvailable: return "no-servers"', 'case SteamError.connectionFailed: return "connection-failed"',
                               'case SteamError.disconnected: return "disconnected"', 'return "transport-')))
live = (root / "app/Madeira/BCDLiveStore.swift").read_text()
check("returning to the front calls refreshAfterForeground next to reconcileSession",
      "SteamOwnedLibrary.shared.reconcileSession(); SteamOwnedLibrary.shared.refreshAfterForeground()" in function(live, "func becameActive()"))
check("the startup refresh goes through the same guard, and a refresh counts as an attempt",
      "refreshAfterForeground()" in function(owned, "private func refreshIfStale()")
      and "lastLibraryRefreshAttempt = Date()" in function(owned, "func refreshLibrary(interactive: Bool = true) async"))
wf = (root / ".github/workflows/build-ipa.yml").read_text()
check("CI runs this check", "python3 tests/host/check-steam-cm-transport.py" in wf)

swiftc = os.environ.get("SWIFTC") or shutil.which("swiftc")
if not swiftc:
    print("skip: no swiftc")
    print("PASS (static only): Steam CM transport" if ok else "FAILED: Steam CM transport")
    sys.exit(0 if ok else 1)

# ------------------------------------------------------------------------------------------------------------------ Part A
methods = "\n".join(function(conn, s) for s in ("    func connect() async throws {", "    private func clearTransport() {", "    func disconnect() {",
                                                 "    private func handleDisconnect(task:", "    private func received("))
methods = methods.replace("URLSessionWebSocketTask", "FakeTask")
harness_a = r'''
import Foundation
enum SteamLog {
    nonisolated(unsafe) static var lines: [String] = []
    static func trace(_ s: @autoclosure () -> String) {}
    static func event(_ s: String) { lines.append(s) }
}
extension CMServerList {
    func seed(_ count: Int) { servers = (0..<count).map { CMServer(host: "cm\($0).example", port: 443) }; lastFetched = Date() }
    func failures() -> [Int] { servers.map { $0.failCount } }
}
final class FakeTask {
    enum CloseCode { case goingAway }
    enum Message { case data(Data), string(String) }
    var cancelled = false
    func cancel(with: CloseCode, reason: Data?) { cancelled = true }
}
final class FakeSession { var invalidated = false; func invalidateAndCancel() { invalidated = true } }
final class Sink { var messages: [Data] = []; var disconnections = 0 }
actor Harness {
    let serverList: CMServerList
    var currentEndpoint: String?, activeConnect: UUID?
    var webSocketTask: FakeTask?, urlSession: FakeSession?
    var attempts: [String] = [], tasks: [FakeTask] = [], sessions: [FakeSession] = []
    var receives = 0
    var codes: [Int?]
    var disconnectHandler: ((Error?) -> Void)?
    var messageHandler: ((Data) -> Void)?
    let sink: Sink
    var holdNext = false
    var release: CheckedContinuation<Void, Never>?
    var waiters: [CheckedContinuation<Void, Never>] = []
    init(_ list: CMServerList, _ codes: [Int?]) {
        serverList = list; self.codes = codes
        let sink = Sink(); self.sink = sink
        messageHandler = { sink.messages.append($0) }
        disconnectHandler = { _ in sink.disconnections += 1 }
    }
    func scheduleReceive(_ task: FakeTask) { receives += 1 }
    /// The scripted handshake: a code fails with that URL error (carrying text a log must never show), nil succeeds.
    private func connect(to server: CMServerList.CMServer) async throws {
        currentEndpoint = server.endpoint; attempts.append(server.endpoint)
        let task = FakeTask(), session = FakeSession()
        tasks.append(task); sessions.append(session); webSocketTask = task; urlSession = session
        let code = codes.isEmpty ? nil : codes.removeFirst()
        if holdNext {
            holdNext = false
            await withCheckedContinuation { continuation in
                release = continuation
                for waiter in waiters { waiter.resume() }
                waiters = []
            }
        }
        if let code {
            throw NSError(domain: NSURLErrorDomain, code: code,
                          userInfo: [NSLocalizedDescriptionKey: "secret-token", NSURLErrorFailingURLErrorKey: URL(string: "https://private.invalid/?secret-token")!])
        }
    }
''' + methods + r'''
    func staleDisconnect() { handleDisconnect(task: tasks[0], error: nil) }
    func staleMessage() { received(.success(.data(Data([1]))), from: tasks[0]) }
    func currentMessage() { received(.success(.data(Data([2]))), from: tasks.last!) }
    func currentDisconnect() { received(.failure(NSError(domain: NSPOSIXErrorDomain, code: 57)), from: tasks.last!) }
    func currentFailure() { received(.failure(NSError(domain: NSURLErrorDomain, code: -1005, userInfo: [NSLocalizedDescriptionKey: "secret-token"])), from: tasks.last!) }
    func delivered() -> [Data] { sink.messages }
    func disconnected() -> Int { sink.disconnections }
    func holdAttempt() { holdNext = true }
    func waitUntilHeld() async {
        if release != nil { return }
        await withCheckedContinuation { waiters.append($0) }
    }
    func releaseAttempt() { let continuation = release; release = nil; continuation?.resume() }
    func status() -> (attempts: Int, receives: Int, cancelled: Int, invalidated: Int) {
        (attempts.count, receives, tasks.filter { $0.cancelled }.count, sessions.filter { $0.invalidated }.count)
    }
}
nonisolated(unsafe) var failures = 0
func check(_ condition: @autoclosure () -> Bool, _ label: String) {
    if condition() { print("ok   " + label) } else { print("FAIL " + label); failures += 1 }
}
@main struct Main {
    static func main() async throws {
        // the policy
        for code in [-1201, -1202, -1203, -1204, -1205, -1206, -999, -1009] {
            check(!SteamCMTransportPolicy.canTryAnotherServer(URLError(URLError.Code(rawValue: code))), "no other server for URL error \(code) (trust, client certificate, cancelled, offline)")
        }
        for code in [-1001, -1003, -1004, -1005, -1006, -1200] {
            check(SteamCMTransportPolicy.canTryAnotherServer(URLError(URLError.Code(rawValue: code))), "another server for URL error \(code) (timeout, DNS, cannot connect, lost, TLS negotiation)")
        }
        let unknown = NSError(domain: "secret-token", code: 42, userInfo: [NSLocalizedDescriptionKey: "secret-token"])
        check(!SteamCMTransportPolicy.canTryAnotherServer(unknown), "an unknown error is not retried")
        check(SteamCMTransportPolicy.sanitized(unknown) == SteamError.transportFailed(domain: "other", code: 42), "an unknown error keeps no domain text")
        check(!(SteamError.transportFailed(domain: "other", code: 42).errorDescription ?? "").contains("secret"), "and the words say no domain text either")
        // the endpoint selection
        let unhealthy = CMServerList(); await unhealthy.seed(2)
        for _ in 0..<3 { await unhealthy.markFailed(endpoint: "cm0.example:443"); await unhealthy.markFailed(endpoint: "cm1.example:443") }
        let reset = try await unhealthy.getServer(excluding: ["cm0.example:443"])
        check(reset.endpoint == "cm1.example:443", "an excluded server is skipped even when every server is marked failed")
        let counts = await unhealthy.failures()
        check(counts == [3, 0], "and only the servers that may be tried get their count reset")
        do { _ = try await unhealthy.getServer(excluding: ["cm0.example:443", "cm1.example:443"]); check(false, "excluding every server finds none") }
        catch { check((error as? SteamError) == SteamError.noServersAvailable, "with every server excluded there is none") }
        let first = CMServerList(); await first.seed(3)
        let firstChoice = try await first.getServer().endpoint
        check(firstChoice == "cm0.example:443", "with nothing excluded the choice is what it was (the first healthy, oldest attempt)")

        // a connect that works is unchanged
        let okList = CMServerList(); await okList.seed(3)
        let good = Harness(okList, [nil]); try await good.connect()
        let g = await good.status()
        let goodFailures = await okList.failures()
        check(g.attempts == 1 && g.receives == 1 && g.cancelled == 0 && g.invalidated == 0 && goodFailures == [0, 0, 0] && SteamLog.lines.isEmpty,
              "a connect that works: one attempt, one receive loop, nothing released, nothing marked failed, no log line")

        // retried failures
        let list = CMServerList(); await list.seed(3)
        let h = Harness(list, [-1200, -1003, nil]); try await h.connect()
        let s = await h.status()
        check(s.attempts == 3 && s.receives == 1 && s.cancelled == 2 && s.invalidated == 2, "a TLS failure and a DNS failure: the third server connects, the two half-open tasks and sessions were released")
        let names = await h.attempts
        check(Set(names).count == 3, "the three attempts were three distinct servers")
        let failed = await list.failures()
        check(failed == [1, 1, 0], "the two failed servers are marked, the working one is not")
        check(SteamLog.lines == ["[steam-cm-transport] attempt=1 reason=transportFailed(domain: \"NSURLErrorDomain\", code: -1200)",
                                 "[steam-cm-transport] attempt=2 reason=transportFailed(domain: \"NSURLErrorDomain\", code: -1003)"],
              "one log line per failed attempt, with the domain and code only: \(SteamLog.lines)")
        check(!SteamLog.lines.joined().contains("secret") && !SteamLog.lines.joined().contains("private.invalid"), "no URL, header or payload text in the log")
        // stale callbacks
        await h.staleDisconnect(); await h.staleMessage()
        let afterStale = await h.status()
        let oldMessages = await h.delivered()
        check(afterStale.receives == 1 && afterStale.cancelled == 2 && oldMessages.isEmpty, "a replaced connection delivers nothing, schedules nothing and closes nothing")
        await h.currentMessage()
        let valid = await h.delivered()
        check(valid == [Data([2])], "the current connection's message is delivered")
        // a trust failure
        let trustList = CMServerList(); await trustList.seed(3)
        let trust = Harness(trustList, [-1202, nil])
        do { try await trust.connect(); check(false, "a certificate failure is accepted") }
        catch {
            check((error as? SteamError) == .transportFailed(domain: NSURLErrorDomain, code: -1202) && !error.localizedDescription.contains("secret"),
                  "a certificate failure stops at once with a safe error")
        }
        let ts = await trust.status()
        check(ts.attempts == 1 && ts.receives == 0 && ts.cancelled == 1 && ts.invalidated == 1, "and nothing is left open")
        // one server only
        let singleList = CMServerList(); await singleList.seed(1)
        let single = Harness(singleList, [-1200])
        do { try await single.connect(); check(false, "the same endpoint was tried again") }
        catch { check((error as? SteamError) == .transportFailed(domain: NSURLErrorDomain, code: -1200), "with one server the last error is kept and the server is not tried twice") }
        let ss = await single.status()
        check(ss.attempts == 1 && ss.cancelled == 1, "one attempt only")
        // cancelled by the system
        let cancelList = CMServerList(); await cancelList.seed(3)
        let cancelled = Harness(cancelList, [-999, nil])
        do { try await cancelled.connect(); check(false, "a cancelled handshake was retried") } catch { check(error is CancellationError, "a cancelled handshake is a cancellation, not a retry") }
        let cs = await cancelled.status()
        check(cs.attempts == 1 && cs.receives == 0, "and tries no other server")
        // the budget
        let allList = CMServerList(); await allList.seed(5)
        let all = Harness(allList, [-1200, -1200, -1200, nil])
        do { try await all.connect(); check(false, "the budget was exceeded") }
        catch { check((error as? SteamError) == .transportFailed(domain: NSURLErrorDomain, code: -1200), "after three failed servers the last error is thrown") }
        let a = await all.status()
        check(a.attempts == 3 && a.cancelled == 3 && a.invalidated == 3, "exactly three attempts, each released")
        // a success that arrives after the caller was cancelled
        let lateList = CMServerList(); await lateList.seed(3)
        let late = Harness(lateList, [nil])
        await late.holdAttempt()
        let lateStart = Task { try await late.connect() }
        await late.waitUntilHeld(); lateStart.cancel(); await late.releaseAttempt()
        do { try await lateStart.value; check(false, "a cancelled success was accepted") } catch { check(error is CancellationError, "a success after the caller was cancelled is a cancellation") }
        let ls = await late.status()
        let lateFailures = await lateList.failures()
        check(ls.receives == 0 && ls.cancelled == 1 && ls.invalidated == 1 && lateFailures == [0, 0, 0], "and publishes nothing, marks nothing")
        // a replaced connect
        let replaceList = CMServerList(); await replaceList.seed(3)
        let replace = Harness(replaceList, [nil, nil])
        await replace.holdAttempt()
        let oldStart = Task { try await replace.connect() }
        await replace.waitUntilHeld(); try await replace.connect(); await replace.releaseAttempt()
        do { try await oldStart.value; check(false, "the replaced connect was accepted") } catch { check(error is CancellationError, "a connect replaced by a newer one ends as a cancellation") }
        await replace.staleMessage(); await replace.staleDisconnect()
        let rs = await replace.status()
        let replacementMessages = await replace.delivered()
        check(rs.attempts == 2 && rs.receives == 1 && rs.cancelled == 1 && replacementMessages.isEmpty, "the replaced connection feeds and closes nothing of the new one")
        await replace.currentDisconnect(); await replace.currentDisconnect()
        let disconnects = await replace.disconnected()
        check(disconnects == 1, "a lost connection fires the disconnect handler once")
        let endpoint = await replace.currentEndpoint
        check(endpoint != nil, "and keeps its endpoint so reconnect() can mark that CM failed")
        await replace.currentFailure()
        if failures > 0 { print("FAILURES: \(failures)"); exit(1) }
        print("PASS: Steam connection recovery")
    }
}
'''

# ------------------------------------------------------------------------------------------------------------------ Part B
refresh = function(owned, "    func refreshAfterForeground() {")
stale = function(owned, "    private func refreshIfStale() {").replace("private func refreshIfStale", "func refreshIfStale")
harness_b = r'''
import Foundation
@MainActor final class FakeLibrary {
    static var enabled = true
    var signedIn = true, refreshing = false, inSession = false
    var libraryUpdated: Date?
    var lastLibraryRefreshAttempt: Date?
    var fetches = 0, playtime = 0
    func refreshLibrary(interactive: Bool = true) async { fetches += 1 }
    func refreshPlaytime() async { playtime += 1 }
''' + refresh.replace("Self.enabled", "FakeLibrary.enabled") + "\n" + stale + r'''
}
nonisolated(unsafe) var failures = 0
func check(_ condition: @autoclosure () -> Bool, _ label: String) {
    if condition() { print("ok   " + label) } else { print("FAIL " + label); failures += 1 }
}
@main struct Main {
    @MainActor static func main() async {
        func settle() async { for _ in 0..<5 { await Task.yield(); try? await Task.sleep(nanoseconds: 2_000_000) } }
        let old = Date().addingTimeInterval(-7 * 3600), fresh = Date().addingTimeInterval(-3600)
        var l = FakeLibrary(); l.libraryUpdated = nil
        l.refreshAfterForeground(); l.refreshAfterForeground(); l.refreshAfterForeground(); l.refreshAfterForeground(); l.refreshAfterForeground()
        await settle()
        check(l.fetches == 1, "a library never loaded: five activations before the task runs queue exactly one fetch (\(l.fetches))")
        l.refreshAfterForeground(); await settle()
        check(l.fetches == 1, "a sixth activation inside the minute fetches nothing")
        l.lastLibraryRefreshAttempt = Date().addingTimeInterval(-61)
        l.refreshAfterForeground(); await settle()
        check(l.fetches == 2, "after a minute it tries again (the first fetch failed: the list is still not loaded)")
        l = FakeLibrary(); l.libraryUpdated = old
        l.refreshAfterForeground(); await settle()
        check(l.fetches == 1, "a library older than six hours is fetched")
        l = FakeLibrary(); l.libraryUpdated = fresh
        l.refreshAfterForeground(); await settle()
        check(l.fetches == 0, "a library younger than six hours is left alone")
        for (name, set) in [("signed out", { (x: FakeLibrary) in x.signedIn = false }), ("already refreshing", { $0.refreshing = true }),
                            ("in a game session", { $0.inSession = true })] as [(String, (FakeLibrary) -> Void)] {
            l = FakeLibrary(); set(l)
            l.refreshAfterForeground(); await settle()
            check(l.fetches == 0, "nothing when \(name)")
        }
        FakeLibrary.enabled = false
        l = FakeLibrary(); l.refreshAfterForeground(); await settle()
        check(l.fetches == 0, "nothing when the Steam library is switched off")
        FakeLibrary.enabled = true
        // the startup refresh goes through the same guard
        l = FakeLibrary(); l.libraryUpdated = old
        l.refreshIfStale(); l.refreshAfterForeground(); await settle()
        check(l.fetches == 1 && l.playtime == 0, "the startup refresh of a stale library, then an activation: one fetch")
        l = FakeLibrary(); l.libraryUpdated = fresh
        l.refreshIfStale(); await settle()
        check(l.fetches == 0 && l.playtime == 1, "a fresh library at startup refreshes the playtime only, as before")
        // a manual refresh counts as an attempt
        l = FakeLibrary(); l.lastLibraryRefreshAttempt = Date().addingTimeInterval(-10)
        l.refreshAfterForeground(); await settle()
        check(l.fetches == 0, "an attempt ten seconds ago (a manual refresh) holds the foreground retry back")
        if failures > 0 { print("FAILURES: \(failures)"); exit(1) }
        print("PASS: library retry on returning to the app")
    }
}
'''

with tempfile.TemporaryDirectory(prefix="madeira-steam-cm-") as td:
    t = Path(td)
    # Part A: one file, so the test extension can reach CMServerList's private storage
    (t / "a.swift").write_text(error_swift + "\n" + servers_swift + "\n" + harness_a)
    b = subprocess.run([swiftc, "-swift-version", "5", "-warnings-as-errors", "-parse-as-library", str(t / "a.swift"), "-o", str(t / "a")], capture_output=True, text=True)
    check("the production error policy, endpoint selection and connection methods compile (warnings are errors)", b.returncode == 0, b.stderr[-3000:])
    if b.returncode == 0:
        r = subprocess.run([str(t / "a")], capture_output=True, text=True, timeout=60)
        sys.stdout.write(r.stdout)
        check("the connection scenarios pass", r.returncode == 0 and "PASS: Steam connection recovery" in r.stdout, r.stdout[-1500:] + r.stderr[-1500:])
    (t / "b.swift").write_text(harness_b)
    b = subprocess.run([swiftc, "-swift-version", "5", "-warnings-as-errors", "-parse-as-library", str(t / "b.swift"), "-o", str(t / "b")], capture_output=True, text=True)
    check("the production refreshAfterForeground and refreshIfStale compile (warnings are errors)", b.returncode == 0, b.stderr[-3000:])
    if b.returncode == 0:
        r = subprocess.run([str(t / "b")], capture_output=True, text=True, timeout=60)
        sys.stdout.write(r.stdout)
        check("the library scenarios pass", r.returncode == 0 and "PASS: library retry on returning to the app" in r.stdout, r.stdout[-1500:] + r.stderr[-1500:])

print("PASS: Steam CM transport and library retry" if ok else "FAILED: Steam CM transport and library retry")
sys.exit(0 if ok else 1)

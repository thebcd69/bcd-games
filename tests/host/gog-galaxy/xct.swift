// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
//
// check-gog-galaxy.py: the few XCTest names Playport's GalaxyTests.swift calls, as plain checks, so that file runs as a host check
// without the XCTest package. A failed check prints its place and counts; the run ends non-zero when any did.
import Foundation

nonisolated(unsafe) var failures = 0
func fail(_ message: String, file: StaticString = #file, line: UInt = #line) {
    failures += 1
    print("FAIL \(file):\(line): \(message)")
}
func expect(_ ok: @autoclosure () throws -> Bool, _ what: String, file: StaticString = #file, line: UInt = #line) {
    do { if !(try ok()) { fail(what, file: file, line: line) } } catch { fail("\(what): threw \(error)", file: file, line: line) }
}
// MARK: - XCTest's names

class XCTestCase { init() {} }
struct GalaxyTestFailure: Error { let text: String; init(_ text: String) { self.text = text } }
struct XCTUnwrapFailed: Error {}

func XCTAssertEqual<T: Equatable>(_ a: @autoclosure () throws -> T, _ b: @autoclosure () throws -> T, _ message: @autoclosure () -> String = "",
                                  file: StaticString = #file, line: UInt = #line) {
    do {
        let x = try a(), y = try b()
        if x != y { fail("\(x) is not \(y) \(message())", file: file, line: line) }
    } catch { fail("threw \(error) \(message())", file: file, line: line) }
}
func XCTAssertNil(_ value: @autoclosure () throws -> Any?, _ message: @autoclosure () -> String = "", file: StaticString = #file, line: UInt = #line) {
    do { if let v = try value() { fail("\(v) is not nil \(message())", file: file, line: line) } } catch { fail("threw \(error) \(message())", file: file, line: line) }
}
func XCTAssertTrue(_ value: @autoclosure () throws -> Bool, _ message: @autoclosure () -> String = "", file: StaticString = #file, line: UInt = #line) {
    do { if !(try value()) { fail("not true \(message())", file: file, line: line) } } catch { fail("threw \(error) \(message())", file: file, line: line) }
}
func XCTAssertFalse(_ value: @autoclosure () throws -> Bool, _ message: @autoclosure () -> String = "", file: StaticString = #file, line: UInt = #line) {
    do { if try value() { fail("not false \(message())", file: file, line: line) } } catch { fail("threw \(error) \(message())", file: file, line: line) }
}
func XCTAssertThrowsError<T>(_ expression: @autoclosure () throws -> T, _ message: @autoclosure () -> String = "", file: StaticString = #file,
                             line: UInt = #line, _ handler: (Error) -> Void = { _ in }) {
    do { _ = try expression(); fail("did not throw \(message())", file: file, line: line) } catch { handler(error) }
}
func XCTUnwrap<T>(_ expression: @autoclosure () throws -> T?, file: StaticString = #file, line: UInt = #line) throws -> T {
    guard let value = try expression() else {
        fail("nil where a value was expected", file: file, line: line)
        throw XCTUnwrapFailed()
    }
    return value
}

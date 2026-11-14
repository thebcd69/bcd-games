// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

// The checks of tests/host/check-drive-speed.py for app/Madeira/BCDDriveSpeed.swift (compiled with it; it names no other type). The
// measurement really runs here, in a temporary folder of this Mac.

import Foundation

var failures = 0
func check(_ what: String, _ ok: Bool) {
    print((ok ? "PASS: " : "FAIL: ") + what)
    if !ok { failures += 1 }
}

let fm = FileManager.default
let tmp = fm.temporaryDirectory.appendingPathComponent("bcd-drive-speed-\(getpid())", isDirectory: true)
try! fm.createDirectory(at: tmp, withIntermediateDirectories: true)
defer { try? fm.removeItem(at: tmp) }

// the measurement
let quick = try? BCDDriveSpeedTest.quickWriteMBps(in: tmp)
check("the quick write test gives a speed and leaves nothing behind", (quick ?? 0) > 0 && (try? fm.contentsOfDirectory(atPath: tmp.path)) == [])
let full = try? BCDDriveSpeedTest.run(in: tmp)
check("the full test gives the three speeds and the file system's name, and leaves nothing behind",
      (full?.writeMBps ?? 0) > 0 && (full?.readMBps ?? 0) > 0 && (full?.smallFilesPerSecond ?? 0) > 0 && full?.fileSystem?.isEmpty == false
      && (try? fm.contentsOfDirectory(atPath: tmp.path)) == [])
try! fm.createDirectory(at: tmp.appendingPathComponent(BCDDriveSpeedTest.folderName + "/left-over"), withIntermediateDirectories: true)
check("a folder left over from a test that was cut short is cleared first", (try? BCDDriveSpeedTest.quickWriteMBps(in: tmp)) != nil
      && (try? fm.contentsOfDirectory(atPath: tmp.path)) == [])
let missing = tmp.appendingPathComponent("a-file")
fm.createFile(atPath: missing.path, contents: Data([1]))
check("a place nothing can be written to is an error, not a number", (try? BCDDriveSpeedTest.quickWriteMBps(in: missing)) == nil && (try? BCDDriveSpeedTest.run(in: missing)) == nil)
try? fm.removeItem(at: missing)
check("the words", BCDDriveSpeed(writeMBps: 85.4, readMBps: 210.2, smallFilesPerSecond: 480.6, fileSystem: "exfat").summary == "Write 85 MB/s · read 210 MB/s · 481 small files/s · exfat"
      && BCDDriveSpeed(writeMBps: 5, readMBps: 20, smallFilesPerSecond: 12, fileSystem: nil).summary == "Write 5 MB/s · read 20 MB/s · 12 small files/s")
check("under 40 MB/s is a slow drive", BCDDriveWriteMode.mode(forWriteMBps: 39.9) == .gentle && BCDDriveWriteMode.mode(forWriteMBps: 40) == .normal && BCDDriveWriteMode.mode(forWriteMBps: 5) == .gentle)
check("the volume of a folder that does not exist yet is its nearest parent's", BCDDriveSpeedTest.volume(of: tmp.appendingPathComponent("x/y/z")) == BCDDriveSpeedTest.volume(of: tmp)
      && BCDDriveSpeedTest.volume(of: tmp) != nil)

// when a download is written gently
var asked = 0
func measure(_ mbps: Double) -> () throws -> Double { { asked += 1; return mbps } }
let device: [Int32] = [1, 7], drive: [Int32] = [9, 3]
check("without the key: the usual number, nothing measured, nothing logged",
      BCDDriveGentle.decide(keyOn: false, folderVolume: drive, homeVolume: device, measure: measure(5)) == BCDDriveGentle.Decision(limit: nil, log: nil) && asked == 0)
check("with the key, on the device's own volume: the same, and nothing measured",
      BCDDriveGentle.decide(keyOn: true, folderVolume: device, homeVolume: device, measure: measure(5)) == BCDDriveGentle.Decision(limit: nil, log: nil) && asked == 0)
check("with the key, a volume that cannot be read: the same",
      BCDDriveGentle.decide(keyOn: true, folderVolume: nil, homeVolume: device, measure: measure(5)).limit == nil
      && BCDDriveGentle.decide(keyOn: true, folderVolume: drive, homeVolume: nil, measure: measure(5)).limit == nil && asked == 0)
let slow = BCDDriveGentle.decide(keyOn: true, folderVolume: drive, homeVolume: device, measure: measure(12.4))
check("with the key, another volume at 12 MB/s: four chunks at a time, measured once, and the log says so",
      slow.limit == 4 && asked == 1 && slow.log == "[drive] write test before the download: 12 MB/s, writing gently (4 chunks at a time, in order)")
let fast = BCDDriveGentle.decide(keyOn: true, folderVolume: drive, homeVolume: device, measure: measure(310))
check("another volume at 310 MB/s: the usual number, and the log says what was measured",
      fast.limit == nil && asked == 2 && fast.log == "[drive] write test before the download: 310 MB/s, writing as usual")
let broken = BCDDriveGentle.decide(keyOn: true, folderVolume: drive, homeVolume: device) { throw POSIXError(.EIO) }
check("a write test that fails: the usual number, and the log says it failed", broken.limit == nil && broken.log?.contains("the write test before the download failed") == true)
check("the real thing on this Mac's own volume: nothing is measured and nothing changes",
      BCDDriveGentle.decision(for: URL(fileURLWithPath: NSHomeDirectory()).appendingPathComponent("no-such-folder/x"), keyOn: true) == BCDDriveGentle.Decision(limit: nil, log: nil)
      && BCDDriveGentle.decision(for: tmp, keyOn: false) == BCDDriveGentle.Decision(limit: nil, log: nil))

print(failures == 0 ? "ALL PASS" : "FAILED: \(failures)")
exit(failures == 0 ? 0 : 1)

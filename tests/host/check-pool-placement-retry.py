#!/usr/bin/env python3
"""Run the production second-region prefix with simulated address-space races."""
from pathlib import Path
import subprocess
import tempfile

root = Path(__file__).resolve().parents[2]
src = (root / 'app/Madeira/StikJITHelper.swift').read_text()
def function(signature):
    a = src.index(signature)
    return src[a:src.index('\n    }\n', a) + 7]
helper = function('private static func poolRunSize(')
take = function('private static func takeSecondRegion(')
take = take[:take.index('        // madeira-bcd pool-mid:')]
take += '        return (base: b, size: size)\n    }\n'
prefix = r'''
import Foundation
typealias vm_address_t = UInt
typealias vm_size_t = UInt
let mach_task_self_: Int32 = 0
let KERN_SUCCESS: Int32 = 0
struct LogStore {
 enum Level { case error }
 static let shared = LogStore()
 func log(_ message: String, level: Level) { }
}
final class Fixture {
 static var mode = ""
 static var requests = 0
 static var livePlugs = 0
 static var releases = 0
 static var failedRelease = false
 static func reset(_ s: String) { mode=s; requests=0; livePlugs=0; releases=0; failedRelease=false }
 static func vmAllocate(_ p: UnsafeMutablePointer<UInt>) -> Int32 {
  if mode == "alwaysWrong" || (mode == "race" && requests == 0) { return 1 }
  livePlugs += 1; return 0
 }
}
func vm_allocate(_ task:Int32,_ address:UnsafeMutablePointer<UInt>,_ size:UInt,_ flags:Int32)->Int32 {
 return Fixture.vmAllocate(address)
}
@discardableResult func vm_deallocate(_ task:Int32,_ address:UInt,_ size:UInt)->Int32 {
 if size == 256<<20 && (address == 0x11d7a4000 || address == 0x181000000) {
  Fixture.releases += 1
  if Fixture.mode == "releaseFailure" { Fixture.failedRelease=true; return 1 }
 } else { Fixture.livePlugs -= 1; precondition(Fixture.livePlugs >= 0) }
 return 0
}
struct StikJITHelper {
 static func freeRuns(_ lo:UInt,_ hi:UInt,minSize:UInt)->[(base:UInt,size:UInt)] {
  if lo != 0x100000000 { return [(0x16fab8000,261<<20)] }
  return [(0x11d7a4000,540<<20)]
 }
 static func requestRegion(_ size:Int)->UnsafeMutableRawPointer? {
  Fixture.requests += 1
  if Fixture.mode == "nil" { return nil }
  if Fixture.mode == "alwaysWrong" || Fixture.mode == "releaseFailure" || (Fixture.mode == "race" && Fixture.requests == 1) {
   return UnsafeMutableRawPointer(bitPattern:0x11d7a4000)
  }
  if Fixture.mode == "aboveCeiling" { return UnsafeMutableRawPointer(bitPattern:0x181000000) }
  return UnsafeMutableRawPointer(bitPattern:0x16fab8000)
 }
'''
suffix = r'''
 static func run()->(base:UInt,size:UInt)? {
  takeSecondRegion(above:0x16f000000,want:272<<20,pageFit:false,exeWindow:(0x140000000,128<<20))
 }
 static func blockedWindow()->(base:UInt,size:UInt)? {
  takeSecondRegion(above:0x130000000,want:272<<20,pageFit:false,exeWindow:(0x140000000,128<<20))
 }
}
Fixture.reset("race")
let recovered = StikJITHelper.run()
precondition(recovered?.base == 0x16fab8000 && recovered?.size == 256<<20)
precondition(Fixture.requests == 2 && Fixture.releases == 1 && Fixture.livePlugs == 0)
Fixture.reset("success")
precondition(StikJITHelper.run() != nil && Fixture.requests == 1 && Fixture.livePlugs == 0)
Fixture.reset("alwaysWrong")
precondition(StikJITHelper.run() == nil && Fixture.requests == 3 && Fixture.releases == 3 && Fixture.livePlugs == 0)
Fixture.reset("nil")
precondition(StikJITHelper.run() == nil && Fixture.requests == 1 && Fixture.livePlugs == 0)
Fixture.reset("releaseFailure")
precondition(StikJITHelper.run() == nil && Fixture.requests == 1 && Fixture.failedRelease && Fixture.livePlugs == 0)
Fixture.reset("aboveCeiling")
precondition(StikJITHelper.run() == nil && Fixture.requests == 3 && Fixture.releases == 3 && Fixture.livePlugs == 0)
Fixture.reset("success")
precondition(StikJITHelper.blockedWindow() == nil && Fixture.requests == 0)
print("PASS: placement race recovers; valid placement, three-attempt bound, failed release, nil result, ceiling and executable-window checks preserve cleanup")
'''
with tempfile.TemporaryDirectory(prefix='bcd-pool-retry-') as td:
    td = Path(td)
    (td / 't.swift').write_text(prefix + helper + take + suffix)
    subprocess.run(['xcrun', 'swiftc', '-module-cache-path', str(td / 'modules'), '-warnings-as-errors', str(td / 't.swift'), '-o', str(td / 't')], check=True)
    subprocess.run([str(td / 't')], check=True)

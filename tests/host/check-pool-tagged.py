#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright 2026 Bogdan Seniuc
# Taken by BCD (thebcd69) from https://github.com/thebcd69/bcd-games (pull request #55 by bogsen), November 2026. See THIRD-PARTY-NOTICES.md.
# Modified by BCD (thebcd69), 2026-11-15: the vm_protect stub has Darwin's own parameter types, so it shadows the SDK's on an Intel host too.
"""Execute actual tagged routing, owned aliases, and launch-local rollback."""
from pathlib import Path
import subprocess,tempfile,argparse
here=Path(__file__).resolve().parent
ap=argparse.ArgumentParser();ap.add_argument("--source-root",type=Path);args=ap.parse_args()
root=args.source_root or (here if (here/"StikJITHelper.swift").exists() else here.parents[1]/"app"/"Madeira")
s=(root/'StikJITHelper.swift').read_text()
def fn(sig):
 a=s.index(sig);return s[a:s.index('\n    }\n',a)+7]
functions='\n'.join(fn(x) for x in ['@discardableResult private static func releasePoolMapping(', 'private static func poolExemption(', 'private static func requestRegion(', 'private static func remapRW(', 'private static func floorAliasChecked(', 'private static func mapLowAlias(', 'private static func mapSplitAlias(', 'private static func prepareTaggedPool('])
assert s.count('poolExemption(')==10 and s.count('jit_make_region_no_footprint(')==1
assert s.count('taggedPublished = true')==4
assert s.count('guard prepareTaggedPool(')==4
assert 'floorAliasChecked(' not in fn('private static func mapLowAlias(')
assert 'floorAliasChecked(' not in fn('private static func mapSplitAlias(')
functions=functions.replace('StikJITHelper.requestWatchBegin(', 'H.requestWatchBegin(').replace('StikJITHelper.requestWatchEnd(', 'H.requestWatchEnd(')
restart=s[s.index('        guard !taggedRestartRequired else {'):s.index('        exeWindowWidenFailedMB = nil')]
hooks=[]
for line in s.splitlines():
 if 'guard prepareTaggedPool(' in line:hooks.append(line.strip())
for hook in hooks:
 at=s.index(hook);assert at<s.index('taggedPublished = true',at)<s.index('return (rx:',at)

rollback=s[s.index('        var rxPtrOpt:'):s.index('        var requestUnanswered = false')]
pair=s[s.index('        var pairSecond:'):s.index('        guard let rxPtr = rxPtrOpt else')]
mode=s[s.index('        taggedPool = (MadeiraConfig.gameValue'):s.index('        noDebugger = !debuggerAttached')]
fixture=r'''
import Foundation
typealias vm_address_t=UInt;typealias vm_size_t=UInt;typealias vm_prot_t=Int32;typealias kern_return_t=Int32;typealias mach_port_t=UInt32
let mach_task_self_:UInt32=1, KERN_SUCCESS:Int32=0, KERN_FAILURE:Int32=5, KERN_NO_SPACE:Int32=3
let VM_PROT_NONE:Int32=0, VM_PROT_READ:Int32=1, VM_PROT_WRITE:Int32=2, VM_FLAGS_ANYWHERE:Int32=1, VM_FLAGS_OVERWRITE:Int32=2, VM_INHERIT_NONE:Int32=0
struct LogStore {enum Level {case error,success,info};static let shared=LogStore();func log(_ s:String,level:Level = .info){}}
struct MadeiraConfig {static var game:String?,global:String?;static func gameValue(_ k:String)->String?{k=="pool-tagged" ? game:nil};static func get(_ k:String)->String?{k=="pool-tagged" ? global:nil};static func flag(_ k:String)->Bool{false}}
struct BCDBrkTrail {static func begin(site:Int,size:Int,attempt:Int?,region:String){F.trail+=1};static func end(){F.trail-=1}}
struct F {
 static var prepared:[(UInt,UInt,Int)]=[], events:[String]=[]
 static var owned:[UInt:UInt]=[:], next:UInt=0x148000000, legacy=0,tagged=0,nodebug=0,nf=0,remaps=0,coherence=0,trail=0,fail="",releases:[UInt]=[]
 static func reset(_ mode:String=""){owned=[:];prepared=[];events=[];H.taggedRestartRequired=false;next=0x148000000;legacy=0;tagged=0;nodebug=0;nf=0;remaps=0;coherence=0;trail=0;fail=mode;releases=[];H.poolFailure=nil;H.noDebugger=false;H.taggedPool=false;H.poolMid=nil;H.noDebuggerEntries=[:]}
 static func allocate(_ size:Int)->UnsafeMutableRawPointer{let a=next;next+=UInt(size)+0x10000;precondition(owned[a]==nil);owned[a]=UInt(size);return UnsafeMutableRawPointer(bitPattern:a)!}
 static func contains(_ a:UInt,_ n:UInt)->Bool{owned.contains{$0.key<=a && a-$0.key<=$0.value && n<=$0.value-(a-$0.key)}}
}
func jit26_prepare_region(_ p:UnsafeMutableRawPointer?,_ size:Int)->UnsafeMutableRawPointer?{precondition(p==nil&&F.trail==1);F.legacy+=1;return F.allocate(size)}
func jit_tagged_region(_ size:Int,_ error:UnsafeMutablePointer<Int32>)->UnsafeMutableRawPointer?{precondition(F.trail==1);F.tagged+=1;if F.fail=="fatal"{error.pointee=5;return nil};if F.fail=="unavailable"{return nil};return F.allocate(size)}
func jit_tagged_prepare_owned_region(_ rx:UnsafeMutableRawPointer,_ rw:UnsafeMutableRawPointer,_ size:Int)->Bool{precondition(F.trail==1&&F.contains(UInt(bitPattern:rx),UInt(size))&&F.contains(UInt(bitPattern:rw),UInt(size))&&F.remaps>0&&F.coherence==0);F.prepared.append((UInt(bitPattern:rx),UInt(bitPattern:rw),size));F.events.append("prepare");return F.fail != "prepare\(F.prepared.count)"}
func jit_nodebug_region(_ size:Int,_ entry:UnsafeMutablePointer<UInt32>)->UnsafeMutableRawPointer?{F.nodebug+=1;entry.pointee=99;return F.allocate(size)}
func jit_nodebug_release(_ entry:UInt32){}
func jit_nodebug_map_alias(_ entry:UInt32,_ a:UnsafeMutablePointer<UInt>,_ size:Int,_ flags:Int32)->Int32{preconditionFailure("tagged must never choose entry alias")}
func jit_make_region_no_footprint(_ p:UnsafeMutableRawPointer,_ size:Int,_ label:UnsafePointer<CChar>)->Bool{F.nf+=1;return true}
@discardableResult func vm_deallocate(_ task:UInt32,_ a:UInt,_ n:UInt)->Int32{precondition(F.owned[a]==n,"foreign or duplicate release");F.releases.append(a);if F.fail=="release"||(F.fail=="remapRelease"&&a==0x7000000000){return 5};F.owned.removeValue(forKey:a);return 0}
func vm_allocate(_ task:UInt32,_ a:UnsafeMutablePointer<UInt>,_ n:UInt,_ flags:Int32)->Int32{if F.fail=="reserve"{return 3};if flags==1{a.pointee=0x7000000000};precondition(F.owned[a.pointee]==nil);F.owned[a.pointee]=n;return 0}
func vm_remap(_ task:UInt32,_ a:UnsafeMutablePointer<UInt>,_ n:UInt,_ mask:UInt,_ flags:Int32,_ sourceTask:UInt32,_ source:UInt,_ copy:Int32,_ cur:UnsafeMutablePointer<Int32>,_ max:UnsafeMutablePointer<Int32>,_ inherit:Int32)->Int32{
 precondition(copy==0&&sourceTask==task&&inherit==0&&F.contains(source,n));F.remaps+=1;F.events.append("map");if F.fail=="remap"||F.fail=="remapRelease"{return 5};if flags&VM_FLAGS_OVERWRITE != 0{precondition(F.contains(a.pointee,n))}else{precondition(flags==VM_FLAGS_ANYWHERE);a.pointee=0x7000000000;F.owned[a.pointee]=n};cur.pointee=5;max.pointee=7;return 0
}
func vm_protect(_ task:vm_map_t,_ a:vm_address_t,_ n:vm_size_t,_ maximum:boolean_t,_ prot:vm_prot_t)->kern_return_t{precondition(F.contains(a,n));if F.fail=="protect"||(F.fail=="hole"&&prot==0){return 5};return 0}
func bcd_pool_rw_rx_selftest(_ rx:UnsafeRawPointer,_ rw:UnsafeMutableRawPointer,_ size:UInt,_ detail:UnsafeMutablePointer<CChar>,_ count:UInt)->Int32{F.coherence+=1;F.events.append("coherence");precondition(F.contains(UInt(bitPattern:rx),size)&&F.contains(UInt(bitPattern:rw),size));detail.pointee=0;return F.fail=="coherence" ? 0:1}
struct H {
 static var taggedPool=false,taggedRestartRequired=false,noDebugger=false,poolTaken=false
 static var poolFailure:String?,poolMid:(base:UInt,size:UInt)?,poolLow:(rx:Int,size:Int)?
 static var noDebuggerEntries:[UInt:(entry:UInt32,size:UInt)]=[:]
 static let scLayout2Alias:UInt=0x7900000000
 static func requestWatchBegin(region:String,size:Int,attempt:Int?)->Int{0};static func requestWatchEnd(_ value:Int){}
 static func rwAliasHint()->UInt{0x7000000000};static func rwAliasDrop(_ kr:Int32){};static func rwAliasKeep(_ a:UInt)->Bool{true}
'''+functions+r'''
 static func startAgain()->Bool? {
'''+restart+r'''
 return true
 }
 static func modeTest(_ debuggerAttached:Bool)->Bool? {
'''+mode+r'''
 return taggedPool
 }
 static func rollback(_ publish:Bool) {
  var poolSize=0x4000
'''+rollback.replace('StikJITHelper.poolLow','H.poolLow')+r'''
  rxPtrOpt=UnsafeMutableRawPointer(bitPattern:0x148000000);taggedSecond=(0x149000000,0x4000);lowRegion=(0x120000000,0x4000);poolMid=(0x148800000,0x4000);taggedAlias=(0x7000000000,0x1000000)
  for (a,n) in [(UInt(0x148000000),UInt(0x4000)),(0x149000000,0x4000),(0x120000000,0x4000),(0x148800000,0x4000),(0x7000000000,0x1000000)]{F.owned[a]=n}
  poolSize+=0;taggedPublished=publish
 }
 static func takeSecondRegion(above:UInt,want:Int,pageFit:Bool,exeWindow:(base:UInt,size:UInt),minimumUsable:UInt = 0)->(base:UInt,size:UInt)?{nil}
 static func replaceThenFail()->Bool? {
  var poolSize=0x4000
'''+rollback.replace('StikJITHelper.poolLow','H.poolLow')+r'''
  rxPtrOpt=UnsafeMutableRawPointer(bitPattern:0x148000000);F.owned[0x148000000]=0x4000;F.next=0x150000000
  let pairA:(base:UInt,size:UInt)?=(0x148000000,0x4000),pairSingle=0x8000,requestedPoolSize=0x8000,pageFit=true
  let exeWinBase:UInt=0x140000000,exeWinSize:UInt=0x8000000,goodLow=Int(0x119000000),guestLo=Int(0x7000000000),guestHi=Int(0x7400000000)
  func overlapsExeWindow(_ a:UInt,_ n:UInt)->Bool {a<exeWinBase+exeWinSize&&a+n>exeWinBase}
'''+pair+r'''
  lowRegion=nil;taggedAlias=nil;taggedPublished=false
  return false // Later startup failure executes the real deferred rollback.
 }
 static func commitBranch(_ kind:Int, _ regions:[(rx:UInt,size:UInt)], _ alias:UInt)->Bool? {
 let lowRegions=regions, rwLow=alias, rw=alias, rwAddr=alias
 let rxAddrV=regions[kind==0||kind==2 ? 1:0].rx
 let poolSize=Int(regions[kind==0||kind==2 ? 1:0].size)
 let acceptedRegions=regions
 let low=(base:regions[0].rx,size:regions[0].size)
 switch kind {
 case 0:
'''+hooks[0]+r'''
 case 1:
'''+hooks[1]+r'''
 case 2:
'''+hooks[2]+r'''
 default:
'''+hooks[3]+r'''
 }
 return true
 }
 static func testAlias(_ regions:[(rx:UInt,size:UInt)])->UInt? {
  if regions.count != 1 { return mapLowAlias(regions) }
  var alias:UInt=0,cur:Int32=0,max:Int32=0
  guard remapRW(&alias,regions[0].size,VM_FLAGS_ANYWHERE,rx:regions[0].rx,&cur,&max)==0,
        vm_protect(mach_task_self_,alias,regions[0].size,0,VM_PROT_READ|VM_PROT_WRITE)==0 else{return nil}
  return alias
 }
 static func finalTests(){
  let a:(rx:UInt,size:UInt)=(0x148000000,0x4000),m:(rx:UInt,size:UInt)=(0x148008000,0x4000),b:(rx:UInt,size:UInt)=(0x149000000,0x4000),c:(rx:UInt,size:UInt)=(0x120000000,0x4000)
  for (kind,regions) in [(0,[c,a,m,b]),(0,[c,a,b]),(1,[a,m,b]),(1,[a,b]),(2,[c,a]),(3,[a])] {
   for enabled in [false,true] {
    F.reset();taggedPool=enabled
    for r in regions{F.owned[r.rx]=r.size}
    let alias=testAlias(regions)!;precondition(F.prepared.isEmpty&&F.coherence==0)
    precondition(commitBranch(kind,regions,alias)==true)
    precondition(F.prepared.count==(enabled ? regions.count:0)&&F.coherence==(enabled ? regions.count:0))
    if enabled {for (i,r) in regions.enumerated(){precondition(F.prepared[i].0==r.rx&&F.prepared[i].1==alias+r.rx-regions[0].rx&&F.prepared[i].2==Int(r.size))};precondition(F.events==Array(repeating:"map",count:regions.count)+Array(repeating:"prepare",count:regions.count)+Array(repeating:"coherence",count:regions.count))}
   }
  }
  for failure in ["prepare1","prepare2","prepare3","prepare4","coherence"] {
   F.reset(failure);taggedPool=true;let regions=[c,a,m,b];for r in regions{F.owned[r.rx]=r.size};let alias=testAlias(regions)!
   precondition(commitBranch(0,regions,alias)==nil&&taggedRestartRequired&&poolFailure != nil)
   precondition(F.prepared.count==(failure=="coherence" ? 4:Int(failure.last!.wholeNumberValue!)))
   if failure != "coherence"{precondition(F.coherence==0)}
   let attempts=F.tagged;precondition(requestRegion(16384,region:"fallback")==nil&&F.tagged==attempts)
   taggedPool=false;precondition(startAgain()==nil) // Process-lifetime latch survives mode change.
   taggedPool=true;_ = releasePoolMapping(alias,regions.last!.rx+regions.last!.size-regions[0].rx);for r in regions{_ = releasePoolMapping(r.rx,r.size)};precondition(F.owned.isEmpty)
  }
  for regions in [[a,a],[(rx:a.rx,size:UInt(0))],[(rx:UInt(0xfffffc000),size:UInt(0x8000))],[]] {
   F.reset();taggedPool=true;precondition(!prepareTaggedPool(regions,aliasBase:0x7000000000)&&F.prepared.isEmpty&&taggedRestartRequired)
  }
  // Dropped raw C/M/B never reach preparation; surviving A prepares once.
  F.reset("remap");taggedPool=true;for r in [c,a,m,b]{F.owned[r.rx]=r.size};precondition(mapLowAlias([c,a,m,b])==nil&&F.prepared.isEmpty)
  for r in [c,m,b]{_ = releasePoolMapping(r.rx,r.size)};F.fail="";let alias=testAlias([a])!;precondition(commitBranch(3,[a],alias)==true&&F.prepared.count==1&&F.prepared[0].0==a.rx)
  print("PASS final topologies, raw fallback, prepare-once ordering, sticky failure and borrowed cleanup")
 }
 static func tests(){
  for text in [nil,"0","true","01","1 "] as [String?]{F.reset();MadeiraConfig.game=text;MadeiraConfig.global=nil;precondition(modeTest(true)==false)}
  F.reset();MadeiraConfig.game="1";precondition(modeTest(false)==nil&&poolFailure != nil&&F.owned.isEmpty)
  F.reset();MadeiraConfig.game=nil;MadeiraConfig.global="1";precondition(modeTest(true)==true);MadeiraConfig.game="0";precondition(modeTest(true)==false)
  for enabled in [false,true]{F.reset();taggedPool=enabled;for role in ["A","A-single","B","B-mid","C"]{let p=requestRegion(65536,attempt:1,region:role)!;precondition(noDebuggerEntries.isEmpty);_ = releasePoolMapping(UInt(bitPattern:p),65536)};precondition(F.tagged==(enabled ? 5:0)&&F.legacy==(enabled ? 0:5)&&F.trail==0);for _ in 0..<9{precondition(poolExemption(UnsafeMutableRawPointer(bitPattern:1)!,16384,"label"))};precondition(F.nf==(enabled ? 0:9))}
  F.reset("fatal");taggedPool=true;precondition(requestRegion(65536,region:"B")==nil&&poolFailure != nil);precondition(requestRegion(32768,region:"A-single")==nil&&F.tagged==1&&F.trail==0)
  F.reset("unavailable");taggedPool=true;precondition(requestRegion(65536,region:"B")==nil&&poolFailure==nil);precondition(requestRegion(32768,region:"A-single")==nil&&F.tagged==2)
  for enabled in [false,true]{F.reset();taggedPool=enabled;rollback(false);precondition(F.owned.count==(enabled ? 0:5));F.reset();taggedPool=enabled;rollback(true);precondition(F.owned.count==5)}
  F.reset();taggedPool=true;precondition(replaceThenFail()==false);precondition(F.owned.isEmpty&&F.releases==[0x148000000,0x150000000])
  F.reset("fatal");taggedPool=true;precondition(replaceThenFail()==nil);precondition(F.owned.isEmpty&&F.tagged==1&&F.legacy==0)
  F.reset("release");taggedPool=true;precondition(replaceThenFail()==nil);precondition(F.owned.count==2&&poolFailure != nil&&F.tagged==1)
  F.reset("release");taggedPool=true;rollback(false);precondition(F.owned.count==5&&poolFailure != nil);precondition(requestRegion(16384,region:"C")==nil&&F.tagged==0)
  for enabled in [false,true]{F.reset();taggedPool=enabled;F.owned=[0x148000000:0x4000,0x148008000:0x4000];let a=mapSplitAlias(rxA:0x148000000,sizeA:0x4000,rxB:0x148008000,sizeB:0x4000)!;precondition(a==0x7000000000&&F.remaps==2&&F.coherence==0);_ = releasePoolMapping(a,0xc000)}
  for mode in ["remap","protect","hole"]{F.reset(mode);taggedPool=true;F.owned=[0x148000000:0x4000,0x148008000:0x4000];precondition(mapSplitAlias(rxA:0x148000000,sizeA:0x4000,rxB:0x148008000,sizeB:0x4000)==nil);precondition(F.owned.count==2)}
  F.reset("remapRelease");taggedPool=true;F.owned=[0x148000000:0x4000,0x148008000:0x4000];precondition(mapSplitAlias(rxA:0x148000000,sizeA:0x4000,rxB:0x148008000,sizeB:0x4000)==nil);precondition(F.owned.count==3&&poolFailure != nil);precondition(requestRegion(16384,region:"A-single")==nil&&F.tagged==0);precondition(mapLowAlias([(rx:0x148000000,size:0x4000),(rx:0x148008000,size:0x4000)])==nil&&F.owned.count==3)
  F.reset();taggedPool=true;F.owned=[0x120000000:0x4000,0x148000000:0x4000,0x148008000:0x4000,0x149000000:0x4000];let regions=F.owned.sorted{$0.key<$1.key}.map{(rx:$0.key,size:$0.value)};let low=mapLowAlias(regions)!;precondition(F.remaps==4&&F.coherence==0);_ = releasePoolMapping(low,0x149004000-0x120000000)
  print("PASS tagged exact mode, A/A-single/B/M/C routing,9 exemption skips, local rollback, alias/coherence/ownership")
 }
}
H.tests()
H.finalTests()
'''
with tempfile.TemporaryDirectory(prefix='pool-tagged-') as d:
 p=Path(d);(p/'test.swift').write_text(fixture)
 subprocess.run(['xcrun','swiftc','-warnings-as-errors','-module-cache-path',str(p/'cache'),str(p/'test.swift'),'-o',str(p/'test')],check=True);subprocess.run([str(p/'test')],check=True)
 (p/'abi.swift').write_text('import Foundation\nfunc abi(_ size:Int){var cleanup:Int32=0;_ = jit_tagged_region(size,&cleanup);_ = jit_tagged_prepare_owned_region(UnsafeMutableRawPointer(bitPattern:1)!,UnsafeMutableRawPointer(bitPattern:2)!,size)}\n')
 subprocess.run(['xcrun','swiftc','-typecheck','-warnings-as-errors','-module-cache-path',str(p/'cache'),'-import-objc-header',str(root/'JITAllocator.h'),str(p/'abi.swift')],check=True)
 print('PASS actual C helper header Swift ABI')
 for name,old,new in [
  ('ignore-prepare-failure','guard prepared else { return failed() }','_ = prepared'),
  ('remove-sticky-failure','            taggedRestartRequired = true\n            poolFailure = "Tagged JIT preparation failed.','            poolFailure = "Tagged JIT preparation failed.')]:
  assert old in fixture
  bad=p/(name+'.swift');bad.write_text(fixture.replace(old,new,1));exe=p/name
  subprocess.run(['xcrun','swiftc','-warnings-as-errors','-module-cache-path',str(p/'cache'),str(bad),'-o',str(exe)],check=True)
  result=subprocess.run([str(exe)],capture_output=True)
  assert result.returncode != 0,(name,'negative control unexpectedly passed')
  print('PASS negative control:',name)

#!/usr/bin/env python3
# Portions copyright 2026 Jelly-RayTian, 125hz and intraducine: code taken from https://github.com/willfaust/Madeira (pull requests #353, #400 and #462)
# under GPL-3.0-or-later and modified for BCD in November 2026 (the static MoltenVK build flag and app link flag checks are left out: BCD has
# only the dynamic route). See THIRD-PARTY-NOTICES.md.
"""Exercise the binder gate, the per-file selection and the real driver entry with and without Vulkan.
No third-party headers, runtime, GPU or submodule checkout required.
"""
from pathlib import Path
import os,re,subprocess,tempfile
root=Path(__file__).resolve().parents[2]
def function(source,head):
 a=source.index(head);b=source.index('{',a);end=b+1;depth=1
 while depth:
  depth+=(source[end]=='{')-(source[end]=='}');end+=1
 return source[a:end]
def preprocess(source,on):
 cmd=['cc','-E','-P','-x','c','-DWINE_IOS=1']+(['-DMADEIRA_MOLTENVK=1'] if on else [])+['-']
 return subprocess.check_output(cmd,input=source,text=True)
virt=(root/'build/ntdll-unix/virtual_ios.c').read_text()
chain=function(virt,'static NTSTATUS load_builtin_unixlib(')
for on in [False,True]:
 code=preprocess(chain,on)
 assert 'madeira_winevulkan_unix_call_funcs' not in code
 assert 'winevulkan_unix_call_funcs' in code
 assert ('dxmt_winemetal_unix_call_funcs' in code)
 assert 'dxmt_winemetal_unix_call_wow64_funcs' in code
 assert '!wow && match && strstr(match, "winevulkan")' in code
 print('PASS binder Vulkan gate',on)
script=(root/'build/win32u-unix/build.sh').read_text()
case=script[script.index('    case "$name" in'):script.index('    esac')+len('    esac')]
for setting in ['0','1']:
 env=dict(os.environ,MADEIRA_MOLTENVK=setting)
 result=subprocess.check_output(['bash','-c','''
BUILD_DIR=/adapter
compile_one() { echo "$1"; }
for name in vulkan; do
'''+case+'''\necho /stock/vulkan.c
done
'''],env=env,text=True).strip()
 assert result=='/adapter/vulkan_ios.c',result
print('PASS production per-file selection keeps stock Vulkan source by default')
driver=function((root/'build/win32u-unix/driver_ios.c').read_text(),'static UINT nulldrv_VulkanInit(')
with tempfile.TemporaryDirectory() as temp:
 p=Path(temp)/'probe.c';exe=Path(temp)/'probe'
 p.write_text('''
#include <assert.h>
typedef unsigned UINT;
#define STATUS_NOT_IMPLEMENTED 0xc0000002u
struct vulkan_driver_funcs { int value; };
static int called;
static struct vulkan_driver_funcs actual={42};
UINT madeira_ios_VulkanInit(UINT version,void *handle,const struct vulkan_driver_funcs **funcs){
 assert(version==47 && handle==(void*)1);*funcs=&actual;called++;return 1234;
}
'''+driver+'''
int main(void){
 const struct vulkan_driver_funcs *funcs=0;
#ifdef MADEIRA_MOLTENVK
 assert(nulldrv_VulkanInit(47,(void*)1,&funcs)==1234 && funcs==&actual && called==1);
#else
 assert(nulldrv_VulkanInit(47,(void*)1,&funcs)==STATUS_NOT_IMPLEMENTED && !funcs && !called);
#endif
}
''')
 for on in [False,True]:
  subprocess.run(['cc','-std=c11',str(p),'-o',str(exe)]+(['-DMADEIRA_MOLTENVK=1'] if on else []),check=True)
  subprocess.run([str(exe)],check=True)
print('PASS real driver entry keeps default failure and opt-in native result')
project=(root/'app/Madeira.xcodeproj/project.pbxproj').read_text()
assert 'A2000707 /* vulkan */ = {isa = PBXFileReference; lastKnownFileType = folder; path = vulkan;' in project
assert 'A1000707 /* vulkan in Resources */' in project
assert 'MADEIRA_VULKAN_LDFLAGS' not in project and 'libmadeira_vulkan.a' not in project
print('PASS the app project bundles the vulkan folder and has no unconditional Vulkan archive dependency')

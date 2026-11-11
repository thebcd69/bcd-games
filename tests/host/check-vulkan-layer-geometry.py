#!/usr/bin/env python3
# Copyright 2026 Jelly-RayTian. This file was taken from https://github.com/willfaust/Madeira (pull request #400)
# under GPL-3.0-or-later with the Madeira Converter Exception. See THIRD-PARTY-NOTICES.md.
"""Execute Winios' actual placement and Vulkan-layer selection with real
CAMetalLayer objects. External window lookup/desktop fitting are fixtures.
Checks point/pixel separation, resize, fit, repeated updates, DXMT isolation
and HWND reuse. Native GPU acquisition is tested separately.
"""
from pathlib import Path
import subprocess,tempfile
root=Path(__file__).resolve().parents[2]
s=(root/'app/Madeira/Winios/Winios.m').read_text()
def block(head):
 a=s.index(head+' {');b=s.index('{',a);n=1;e=b+1
 while n:n+=(s[e]=='{')-(s[e]=='}');e+=1
 return s[a:e]
fixture=r'''
#import <Foundation/Foundation.h>
#import <QuartzCore/QuartzCore.h>
#include <assert.h>
#include "VulkanLayerGeometry.h"
@interface NSValue (CGRectFixture)
+ (NSValue *)valueWithCGRect:(CGRect)r;
- (CGRect)CGRectValue;
@end
@implementation NSValue (CGRectFixture)
+ (NSValue *)valueWithCGRect:(CGRect)r {return [NSValue valueWithBytes:&r objCType:@encode(CGRect)];}
- (CGRect)CGRectValue {CGRect r;[self getValue:&r size:sizeof r];return r;}
@end
static NSMutableDictionary *g_metal_layers,*g_px_rects,*g_client_rects;
static NSHashTable<CAMetalLayer *> *g_vulkan_layers;
static CGFloat g_px_to_pt=.5,y_scale=.25;
#define WINIOS_PX_TO_PT_Y y_scale
static BOOL fit;
static BOOL winios_desktop_fit(NSNumber *key,CAMetalLayer *ml,CGRect c) {
 if(fit)ml.frame=CGRectMake(13,17,180,101.25);return fit;
}
static CAMetalLayer *winios_metal_layer_for_hwnd(void *hwnd) {return g_metal_layers[@((uintptr_t)hwnd)];}
'''
main=r'''
static void sizeIs(CGSize a,CGFloat w,CGFloat h){assert(fabs(a.width-w)<.001 && fabs(a.height-h)<.001);}
int main(void){@autoreleasepool {
 g_metal_layers=[NSMutableDictionary new];g_px_rects=[NSMutableDictionary new];g_client_rects=[NSMutableDictionary new];
 NSNumber *key=@42;CAMetalLayer *ml=[CAMetalLayer layer];ml.anchorPoint=CGPointZero;ml.contentsScale=2;g_metal_layers[key]=ml;
 g_px_rects[key]=[NSValue valueWithCGRect:CGRectMake(0,0,660,400)];g_client_rects[key]=[NSValue valueWithCGRect:CGRectMake(10,20,640,360)];
 winios_place_metal_layer(key);sizeIs(ml.bounds.size,320,90);assert(ml.contentsScale==2);
 assert(winios_vulkan_layer_for_hwnd((void *)42)==ml);sizeIs(ml.bounds.size,640,360);sizeIs(ml.frame.size,320,90);assert(ml.contentsScale==1 && fabs(ml.frame.origin.x-5)<.001 && fabs(ml.frame.origin.y-5)<.001);
 for(int i=0;i<20;i++)winios_place_metal_layer(key);
 sizeIs(ml.bounds.size,640,360);sizeIs(ml.frame.size,320,90);
 g_px_to_pt=.25;g_client_rects[key]=[NSValue valueWithCGRect:CGRectMake(10,20,800,450)];winios_place_metal_layer(key);
 sizeIs(ml.bounds.size,800,450);sizeIs(ml.frame.size,200,112.5);
 fit=YES;winios_place_metal_layer(key);sizeIs(ml.bounds.size,800,450);sizeIs(ml.frame.size,180,101.25);assert(fabs(ml.frame.origin.x-13)<.001 && fabs(ml.frame.origin.y-17)<.001);
 CGRect before=ml.bounds;assert(!madeira_vulkan_layer_geometry(ml,CGRectMake(0,0,10,10),CGSizeMake(0,10)));assert(CGRectEqualToRect(before,ml.bounds));
 assert(!madeira_vulkan_layer_geometry(ml,CGRectMake(0,0,NAN,10),CGSizeMake(10,10)));assert(CGRectEqualToRect(before,ml.bounds));
 assert(!madeira_vulkan_layer_geometry(ml,CGRectMake(0,0,10,10),CGSizeMake(INFINITY,10)));assert(CGRectEqualToRect(before,ml.bounds));
 fit=NO;CAMetalLayer *replacement=[CAMetalLayer layer];replacement.anchorPoint=CGPointZero;replacement.contentsScale=2;g_metal_layers[key]=replacement;winios_place_metal_layer(key);
 sizeIs(replacement.bounds.size,200,112.5);assert(replacement.contentsScale==2 && ![g_vulkan_layers containsObject:replacement]);
 assert(!winios_vulkan_layer_for_hwnd((void *)123));
 puts("PASS actual Winios placement: logical extent, point placement, resize, fit, no drift, ordinary DXMT layer and HWND reuse");
}return 0;}
'''
with tempfile.TemporaryDirectory() as t:
 p=Path(t);src=p/'test.m';exe=p/'test'
 src.write_text(fixture+'\n'+block('static void winios_place_metal_layer(NSNumber *key)')+'\n'+block('CAMetalLayer *winios_vulkan_layer_for_hwnd(void *hwnd)')+'\n'+main)
 subprocess.run(['xcrun','clang','-fobjc-arc','-fblocks','-Wall','-Werror','-Wno-unused-function','-I'+str(root/'app/Madeira/Winios'),str(src),'-framework','Foundation','-framework','QuartzCore','-framework','CoreGraphics','-o',str(exe)],check=True)
 subprocess.run([str(exe)],check=True)

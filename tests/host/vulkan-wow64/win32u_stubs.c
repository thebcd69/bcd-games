/* SPDX-License-Identifier: GPL-3.0-only
 * Copyright 2026 thebcd69 (BCD)
 * BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md. */
/*
 * The rest of win32u, for tests/host/check-vulkan-wow64.py: what build/win32u-unix/vulkan_ios.c (with Wine's dlls/win32u/vulkan.c
 * and BCD's surface driver inside it) needs from the other win32u files, and from the app. The harness never reaches the D3DKMT
 * sharing paths or a present, so those end the run when called; the surface, window and driver entries do what the test needs.
 */
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>

#include "ntstatus.h"
#define WIN32_NO_STATUS
#include "win32u_private.h"
#include "ntuser_private.h"

uintptr_t stub_layer_hwnd;        /* the window the surface driver asked a layer for */
int stub_layer_acquires, stub_layer_releases;

#define FAKE_LAYER ((void *)(uintptr_t)0x7f00a11ce000ull)

static void unexpected( const char *name )
{
    fprintf( stderr, "FAIL the harness reached win32u's %s, which it has no model for\n", name );
    exit( 3 );
}

ULONG_PTR zero_bits;     /* win32u's task-wide value: 0 here, as build/win32u-unix/syscall_ios.c leaves it */

/* the app's layer bridge (app/Madeira/Winios) */
void *madeira_vulkan_layer_acquire( void *hwnd )
{
    stub_layer_hwnd = (uintptr_t)hwnd;
    stub_layer_acquires++;
    return FAKE_LAYER;
}

void madeira_vulkan_layer_release( void *layer )
{
    stub_layer_releases++;
}

void madeira_vulkan_present_note( void ) { unexpected( "present" ); }

/* win32u's client surfaces (dlls/win32u/window.c) */
void *client_surface_create( UINT size, const struct client_surface_funcs *funcs, HWND hwnd )
{
    struct client_surface *surface = calloc( 1, size );
    surface->funcs = funcs;
    surface->ref = 1;
    surface->hwnd = hwnd;
    return surface;
}

void client_surface_release( struct client_surface *surface )
{
    if (--surface->ref) return;
    surface->funcs->destroy( surface );
    free( surface );
}

void client_surface_present( struct client_surface *surface ) { unexpected( "client_surface_present" ); }
void client_surface_update( struct client_surface *surface ) { unexpected( "client_surface_update" ); }
void add_window_client_surface( HWND hwnd, struct client_surface *surface ) { }
BOOL set_window_pixel_format( HWND hwnd, int format, BOOL internal ) { return TRUE; }
struct client_surface *nulldrv_client_surface_create( HWND hwnd ) { unexpected( "nulldrv_client_surface_create" ); return NULL; }
BOOL get_gpu_info_from_uuid( const GUID *uuid, LUID *luid, UINT32 *node_mask, char *name ) { return FALSE; }

/* D3DKMT sharing: no host extension of the stand-in library leads here */
int d3dkmt_object_get_fd( D3DKMT_HANDLE local ) { unexpected( "d3dkmt_object_get_fd" ); return -1; }
NTSTATUS d3dkmt_destroy_mutex( D3DKMT_HANDLE local ) { return STATUS_SUCCESS; }
D3DKMT_HANDLE d3dkmt_create_resource( int fd, D3DKMT_HANDLE *global ) { unexpected( "d3dkmt_create_resource" ); return 0; }
D3DKMT_HANDLE d3dkmt_open_resource( D3DKMT_HANDLE global, HANDLE shared, D3DKMT_HANDLE *mutex_local, D3DKMT_HANDLE *sync_local )
{ unexpected( "d3dkmt_open_resource" ); return 0; }
NTSTATUS d3dkmt_destroy_resource( D3DKMT_HANDLE local ) { return STATUS_SUCCESS; }
D3DKMT_HANDLE d3dkmt_create_sync( int fd, D3DKMT_HANDLE *global ) { unexpected( "d3dkmt_create_sync" ); return 0; }
D3DKMT_HANDLE d3dkmt_open_sync( D3DKMT_HANDLE global, HANDLE shared ) { unexpected( "d3dkmt_open_sync" ); return 0; }
NTSTATUS d3dkmt_destroy_sync( D3DKMT_HANDLE local ) { return STATUS_SUCCESS; }
NTSTATUS WINAPI NtGdiDdDDIAcquireKeyedMutex( D3DKMT_ACQUIREKEYEDMUTEX *params ) { unexpected( "NtGdiDdDDIAcquireKeyedMutex" ); return 0; }
NTSTATUS WINAPI NtGdiDdDDIReleaseKeyedMutex( D3DKMT_RELEASEKEYEDMUTEX *params ) { unexpected( "NtGdiDdDDIReleaseKeyedMutex" ); return 0; }
NTSTATUS WINAPI NtGdiDdDDIOpenNtHandleFromName( D3DKMT_OPENNTHANDLEFROMNAME *params ) { unexpected( "NtGdiDdDDIOpenNtHandleFromName" ); return 0; }
NTSTATUS WINAPI NtGdiDdDDIOpenSyncObjectNtHandleFromName( D3DKMT_OPENSYNCOBJECTNTHANDLEFROMNAME *params )
{ unexpected( "NtGdiDdDDIOpenSyncObjectNtHandleFromName" ); return 0; }
NTSTATUS WINAPI NtGdiDdDDIShareObjects( UINT count, const D3DKMT_HANDLE *handles, OBJECT_ATTRIBUTES *attr, UINT access, HANDLE *handle )
{ unexpected( "NtGdiDdDDIShareObjects" ); return 0; }

/* ntdll and user calls the swapchain and sharing paths make */
NTSTATUS WINAPI NtClose( HANDLE handle ) { return STATUS_SUCCESS; }
NTSTATUS WINAPI NtDuplicateObject( HANDLE source_process, HANDLE source, HANDLE dest_process, HANDLE *dest, ACCESS_MASK access,
                                   ULONG attributes, ULONG options ) { unexpected( "NtDuplicateObject" ); return 0; }
ULONG WINAPI NtGetTickCount( void ) { return 0; }
ULONG_PTR WINAPI NtUserCallHwnd( HWND hwnd, DWORD code ) { unexpected( "NtUserCallHwnd" ); return 0; }
ULONG_PTR WINAPI NtUserCallHwndParam( HWND hwnd, DWORD_PTR param, DWORD code ) { unexpected( "NtUserCallHwndParam" ); return 0; }
HWND WINAPI NtUserCreateWindowEx( DWORD ex_style, UNICODE_STRING *class_name, UNICODE_STRING *version, UNICODE_STRING *window_name,
                                  DWORD style, INT x, INT y, INT cx, INT cy, HWND parent, HMENU menu, HINSTANCE instance,
                                  void *params, DWORD flags, HINSTANCE client_instance, const WCHAR *class, BOOL ansi )
{ unexpected( "NtUserCreateWindowEx" ); return 0; }
BOOL WINAPI NtUserDestroyWindow( HWND hwnd ) { unexpected( "NtUserDestroyWindow" ); return FALSE; }

/* the user driver: only its Vulkan entry, BCD's own (build/win32u-unix/vulkan_surface_ios.c, compiled into the harness) */
extern UINT madeira_ios_VulkanInit( UINT version, void *handle, const struct vulkan_driver_funcs **funcs );
static const struct user_driver_funcs harness_driver = { .pVulkanInit = madeira_ios_VulkanInit };
const struct user_driver_funcs *user_driver = &harness_driver;

/* Host spike only: the few Win32 names vkd3d-shader's common header uses. */
#pragma once
#include <stdint.h>
typedef int32_t HRESULT; typedef uint32_t ULONG; typedef int32_t LONG; typedef int BOOL; typedef uint16_t WCHAR; typedef void *HANDLE;
typedef uint32_t DWORD; typedef unsigned int UINT;
typedef struct _GUID { uint32_t Data1; uint16_t Data2, Data3; uint8_t Data4[8]; } GUID;
#define S_OK ((HRESULT)0)
#define S_FALSE ((HRESULT)1)
#define E_FAIL ((HRESULT)0x80004005)
#define E_NOTIMPL ((HRESULT)0x80004001)
#define E_OUTOFMEMORY ((HRESULT)0x8007000E)
#define E_INVALIDARG ((HRESULT)0x80070057)
#define E_NOINTERFACE ((HRESULT)0x80004002)
#define SUCCEEDED(hr) ((HRESULT)(hr) >= 0)
#define FAILED(hr) ((HRESULT)(hr) < 0)
#define WINAPI
#define STDMETHODCALLTYPE
#define E_POINTER ((HRESULT)0x80004003)
#define E_ABORT ((HRESULT)0x80004004)
#define DXGI_ERROR_NOT_FOUND ((HRESULT)0x887A0002)
#define DXGI_ERROR_MORE_DATA ((HRESULT)0x887A0003)
#define DXGI_ERROR_ALREADY_EXISTS ((HRESULT)0x887A0036)
#define DXGI_ERROR_INVALID_CALL ((HRESULT)0x887A0001)
#define D3DERR_INVALIDCALL ((HRESULT)0x8876086C)
#define DXGI_ERROR_UNSUPPORTED ((HRESULT)0x887A0004)
#include <string.h>
#ifndef min
#define min(a, b) (((a) < (b)) ? (a) : (b))
#endif
#ifndef max
#define max(a, b) (((a) > (b)) ? (a) : (b))
#endif
#include <stddef.h>
#ifndef CONTAINING_RECORD
#define CONTAINING_RECORD(address, type, field) ((type *)((char *)(address) - offsetof(type, field)))
#endif

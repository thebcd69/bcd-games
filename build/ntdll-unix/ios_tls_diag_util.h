/* TLS-vs-AV diagnostics, shared helpers (read-only; nothing here writes to any TEB, PEB or image).
 *
 * Used by ios_tls_clear.h ([tls-cross-clear], in virtual_ios.c) and ios_nw_tls_diag.h ([nw-tls], in signal_arm64_ios.c).
 *
 *   - ios_tls_diag_seq(): ONE process-wide sequence number shared by both reports. The counter is defined once in virtual_ios.c
 *     (ios_tls_diag_seq_counter); ntdll-unix is one shared object, so both translation units see the same counter.
 *   - ios_tls_diag_now_ns(): CLOCK_UPTIME_RAW in nanoseconds, the same monotonic clock in both reports.
 *   - ios_tls_diag_read(): the only way these reports touch memory that is not theirs: mach_vm_read_overwrite, which fails
 *     instead of faulting. The address range is checked first (>= 0x10000, below the 47-bit user limit, no wrap-around).
 *   - ios_tls_diag_peb_bit(): the allocation bit of a TLS index in a PEB's TlsBitmapBits / TlsExpansionBitmapBits.
 *   - ios_tls_diag_find_module(): walks PEB->LdrData->InLoadOrderModuleList (x64 layout; same offsets as the ml689 module map
 *     in signal_arm64_ios.c) for an ASCII module name; at most 512 entries, every read checked.
 */
#ifndef IOS_TLS_DIAG_UTIL_H
#define IOS_TLS_DIAG_UTIL_H

#include <stdint.h>
#include <stddef.h>
#include <string.h>
#include <time.h>

#define IOS_TLSD_MIN_SLOTS   64     /* TLS_MINIMUM_AVAILABLE */
#define IOS_TLSD_EXP_SLOTS   1024   /* 8 * sizeof(PEB.TlsExpansionBitmapBits) */
#define IOS_TLSD_MAX_MODULES 512

extern unsigned long long ios_tls_diag_seq_counter;

static inline unsigned long long ios_tls_diag_seq( void )
{
    return __atomic_add_fetch( &ios_tls_diag_seq_counter, 1, __ATOMIC_RELAXED );
}

static inline unsigned long long ios_tls_diag_now_ns( void )
{
    return clock_gettime_nsec_np( CLOCK_UPTIME_RAW );
}

/* 1 on success. Never faults. */
static inline int ios_tls_diag_read( uint64_t addr, void *buf, size_t len )
{
    mach_vm_size_t got = 0;

    if (!len || addr < 0x10000 || addr + len < addr || addr + len > 0x800000000000ULL) return 0;
    return mach_vm_read_overwrite( mach_task_self(), (mach_vm_address_t)addr, len, (mach_vm_address_t)buf, &got ) == KERN_SUCCESS &&
           got == len;
}

/* 1 / 0 = allocated / free; -1 = peb unknown, index out of range, or the read failed. The 64-bit PEB's bitmap only. */
static inline int ios_tls_diag_peb_bit( uint64_t peb, unsigned index )
{
    uint32_t word;
    uint64_t off;
    unsigned bit;

    if (!peb || (peb & 7)) return -1;
    if (index < IOS_TLSD_MIN_SLOTS)
    {
        off = peb + offsetof( PEB, TlsBitmapBits ) + 4 * (index / 32);
        bit = index % 32;
    }
    else if (index < IOS_TLSD_MIN_SLOTS + IOS_TLSD_EXP_SLOTS)
    {
        unsigned i = index - IOS_TLSD_MIN_SLOTS;
        off = peb + offsetof( PEB, TlsExpansionBitmapBits ) + 4 * (i / 32);
        bit = i % 32;
    }
    else return -1;
    if (!ios_tls_diag_read( off, &word, 4 )) return -1;
    return (word >> bit) & 1;
}

/* 1 = the module is in the PEB's load-order list (base / SizeOfImage returned when the pointers are not NULL), 0 = list walked, not
 * there, -1 = the list could not be read. `name` is ASCII, at most 16 characters, compared case-insensitively. */
static inline int ios_tls_diag_find_module( uint64_t peb, const char *name, uint64_t *base_out, uint32_t *size_out )
{
    uint64_t ldr = 0, head, cur = 0, next;
    size_t nl = strlen( name );
    unsigned guard;

    if (!peb || (peb & 7) || nl > 16) return -1;
    if (!ios_tls_diag_read( peb + offsetof( PEB, LdrData ), &ldr, 8 ) || !ldr) return -1;
    head = ldr + 0x10;                                       /* PEB_LDR_DATA.InLoadOrderModuleList */
    if (!ios_tls_diag_read( head, &cur, 8 )) return -1;
    for (guard = 0; cur && cur != head && guard < IOS_TLSD_MAX_MODULES; guard++)
    {
        uint64_t dllbase = 0, nbuf = 0;
        uint32_t imgsize = 0;
        uint16_t nlen = 0, wc[16];

        /* LDR_DATA_TABLE_ENTRY (x64): DllBase +0x30, SizeOfImage +0x40, BaseDllName.Length +0x58, .Buffer +0x60 */
        if (!ios_tls_diag_read( cur + 0x30, &dllbase, 8 ) || !ios_tls_diag_read( cur + 0x40, &imgsize, 4 ) ||
            !ios_tls_diag_read( cur + 0x58, &nlen, 2 ) || !ios_tls_diag_read( cur + 0x60, &nbuf, 8 )) return -1;
        if (nlen == nl * 2 && nbuf && ios_tls_diag_read( nbuf, wc, nlen ))
        {
            size_t k;

            for (k = 0; k < nl; k++)
            {
                unsigned a = wc[k], b = (unsigned char)name[k];
                if (a >= 'A' && a <= 'Z') a += 32;
                if (b >= 'A' && b <= 'Z') b += 32;
                if (a != b) break;
            }
            if (k == nl)
            {
                if (base_out) *base_out = dllbase;
                if (size_out) *size_out = imgsize;
                return 1;
            }
        }
        if (!ios_tls_diag_read( cur, &next, 8 )) return -1;
        cur = next;
    }
    return 0;
}

#endif

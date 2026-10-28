// SPDX-License-Identifier: GPL-3.0-or-later
// Additional permission: Madeira Converter Exception, version 1; see LICENSE-EXCEPTION.md.
// Live Auto HDR controls and measurements of the game's presented texture.
#ifndef BCD_HDR_H
#define BCD_HDR_H
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif
enum { BCD_HDR_ENABLED = 1, BCD_HDR_PATTERN = 2, BCD_HDR_PROBE = 4, BCD_HDR_ELIGIBLE = 8 };
typedef struct {
    uint32_t flags, revision, prepared, valid;
    uint32_t requested, age_ms;
    uint32_t probe_state, probe_preparations, probe_allocations, headroom_refreshes;
    uint32_t edr, linear, pixel_format, applied_flags;
    uint32_t samples, above_sdr, invalid, width, height, error;
    float peak, headroom, applied_peak;
    uint64_t sample_ns;
} BCDHDRSnapshot;
void bcd_hdr_read(BCDHDRSnapshot *out);
void bcd_hdr_reset(void);
void bcd_hdr_begin(void);
void bcd_hdr_get_headroom(uint64_t layer, float *current, float *potential);
void bcd_hdr_set_enabled(int enabled);
void bcd_hdr_set_pattern(int enabled);
void bcd_hdr_set_probe(int enabled);
// Called through MadeiraCtl ops 60/61. No pointers cross the PE boundary.
uint32_t bcd_hdr_control_get(uint32_t *revision);
void bcd_hdr_tag_drawable(uint64_t drawable, uint32_t revision, uint32_t flags, float peak);
void bcd_hdr_probe_encode(uint64_t command_buffer, uint64_t drawable);
#ifdef __cplusplus
}
#endif
#ifdef __OBJC__
#import <Foundation/Foundation.h>
FOUNDATION_EXPORT NSNotificationName const BCDHDRChangedNotification;
#endif
#endif

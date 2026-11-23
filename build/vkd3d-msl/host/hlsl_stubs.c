/* Host spike: the HLSL compiler is not built (only DXBC is translated); these are never reached for DXBC input. */
#include <stdio.h>
#include <stdlib.h>
#define STUB(n) void n(void) { fprintf(stderr, "vkd3d stub " #n " reached\n"); abort(); }
STUB(hlsl_calculate_buffer_offsets) STUB(hlsl_emit_vsir) STUB(hlsl_lexer_compile) STUB(hlsl_run_const_passes)
STUB(hlsl_sm1_base_type) STUB(hlsl_sm1_class) STUB(hlsl_transform_ir) STUB(preproc_lexer_parse)

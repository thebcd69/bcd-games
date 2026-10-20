#!/usr/bin/env python3
"""Exercise the production Mach fault counter; this does not run FEX or iOS."""
from pathlib import Path
import os
import subprocess
import tempfile

root = Path(__file__).resolve().parents[2]
source = (root / 'build/ntdll-unix/signal_arm64_ios.c').read_text()
start = source.index('struct ios_fault_loop_state\n')
end = source.index('\nstatic void *ios_mach_exception_thread(', start)
assert 'ios_fault_loop_tick( &stuck, thread_teb, req->exception,' in source
assert '(uintptr_t)fex_state_q, state.__x[28], state_rip_q' in source
code = r'''
#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#define EXC_BREAKPOINT 6
''' + source[start:end] + r'''
static struct ios_fault_loop_state s;
static unsigned tick(unsigned type, uint64_t addr, uintptr_t frame, uintptr_t x28, uint64_t rip)
{ return ios_fault_loop_tick(&s,1,type,0x169ff852c,addr,frame,x28,rip); }
static void reset(void) { s=(struct ios_fault_loop_state){0}; }
int main(void)
{
    /* Same host PC/address, changing guest RIP: the actual D2R sequence.
     * More than the old page-storm ceiling must still not abort progress. */
    for(unsigned i=0;i<30000;i++)
        assert(tick(EXC_BREAKPOINT,0x169ff852c,0x7c001140,0x7c001140,0x71001000+i)<50);
    assert(s.page_count==0);
    puts("PASS: changing guest RIP survives the shared trap stub and storm ceiling");
    /* A genuinely stuck guest breakpoint still trips after 50 repeats. */
    reset();
    for(unsigned i=0;i<=50;i++)
        assert(tick(EXC_BREAKPOINT,0x169ff852c,0x7c001140,0x7c001140,0x71001000)==i);
    /* Unknown/mismatched frames cannot claim progress. */
    reset();
    for(unsigned i=0;i<=50;i++)
        assert(tick(EXC_BREAKPOINT,0x169ff852c,0,0,0x71001000+i)==i);
    reset();
    for(unsigned i=0;i<=50;i++)
        assert(tick(EXC_BREAKPOINT,0x169ff852c,0x7c001140,0x7c002140,0x71001000+i)==i);
    reset();
    for(unsigned i=0;i<=50;i++)
        assert(tick(EXC_BREAKPOINT,0x169ff852c,0x7c001140,0x7c001140,0)==i);
    puts("PASS: stuck guest and unverified/native breakpoint protection retained");
    /* Native access violations do not use guest RIP to evade the counter,
     * including the NULL-page case the old guard specifically covered. */
    reset();
    for(unsigned i=0;i<=50;i++) assert(tick(1,0,0x7c001140,0x7c001140,0x71001000+i)==i);
    assert(s.page==1);
    reset();
    for(unsigned i=0;i<20000;i++) assert(tick(1,0x20000+(i%2),0,0,0)<50);
    assert(tick(1,0x20000,0,0,0)==50 && s.page_count==20000);
    puts("PASS: native/NULL fault threshold and advancing-address storm ceiling retained");
    reset();
    for(unsigned i=0;i<49;i++) tick(1,0,0,0,0);
    assert(ios_fault_loop_tick(&s,2,1,0x169ff852c,0,0,0,0)==0);
    assert(ios_fault_loop_tick(&s,2,EXC_BREAKPOINT,0x169ff852c,0,0,0,0)==0);
    assert(ios_fault_loop_tick(&s,2,EXC_BREAKPOINT,0x169ff8000,0,0,0,0)==0);
    puts("PASS: fault identity distinguishes threads, exception types and host PCs");
}
'''
with tempfile.TemporaryDirectory() as d:
    c, binary = Path(d) / 'check.c', Path(d) / 'check'
    c.write_text(code)
    subprocess.run([os.environ.get('CC', 'cc'), '-std=gnu11', '-Wall', '-Wextra', '-Werror',
                    '-fsanitize=address,undefined', '-fno-sanitize-recover=all', str(c), '-o', str(binary)], check=True)
    subprocess.run([str(binary)], check=True)

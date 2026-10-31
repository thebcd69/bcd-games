/* op82-alias-x86.c -- the 0x82 alias of the byte-immediate ALU group (MADEIRA).
 *
 * WHAT IT PROVES.  In 32-bit mode the opcode byte 0x82 is the same instruction as 0x80: `op r/m8, imm8` with the
 * eight operations selected by the ModRM reg field (ADD OR ADC SBB AND SUB XOR CMP).  FEX's SecondaryALUOp had no
 * case for 0x82 with ADD, OR, AND, SUB or XOR, so the translation of such an instruction failed ("Unknown ALU Op:
 * 0x10/0x11/0x14/0x15/0x16" in the log) and the program got STATUS_ILLEGAL_INSTRUCTION (0xc000001d) when it ran
 * it.  Copy-protection code (the Ubisoft layer of upc.exe and of three Ubisoft games) is full of such bytes.
 *
 * HOW.  The test writes small functions into an executable page at run time, one per case, and calls them:
 *     mov eax,[esp+4] ; clc|stc ; <0x82 or 0x80> <modrm> <imm> ; lahf ; ret 4        (register form, on AL)
 *     mov ecx,[esp+4] ; clc|stc ; [lock] <0x82 or 0x80> <modrm> <imm> ; lahf ; ret 4  (memory form, on [ecx])
 * for the eight operations, six immediates, six inputs and both carry states, and requires the 0x82 form to give
 * the SAME result byte and the SAME flags (LAHF: SF ZF AF PF CF) as the 0x80 form (which FEX has always
 * translated), and the result byte of the five logic/arithmetic operations to equal a C computation.
 * Every function sits at its own address, so each is translated fresh (no self-modifying-code replay involved).
 *
 * RESULT.  stderr: "MADEIRA-X86-82: ok (N cases)" and exit code 0; on a mismatch the first bad case is printed
 * and the exit code is 1.  On a build without the fix the first 0x82 case raises 0xc000001d: the log shows
 * "Unknown ALU Op: 0x10" and "[exc-disp] raise ... code=c000001d", and the process ends with that code.
 *
 * It is i386 on purpose: 0x82 is invalid in 64-bit mode.  Build: tests/x86/build.sh op82-alias-x86; run it as a
 * direct launch with env.MADEIRA_EXE = op82-alias-x86.exe (see hello-x86.c).
 */
#include <windows.h>

static unsigned char *page;
static unsigned used;
static unsigned cases;

typedef unsigned (__stdcall *fn_t)(unsigned);

static void out(const char *s)
{
    DWORD w;
    WriteFile(GetStdHandle(STD_ERROR_HANDLE), s, lstrlenA(s), &w, NULL);
}

static char line[128];
static unsigned linelen;

static void put_s(const char *s) { while (*s) line[linelen++] = *s++; }

static void put_x(unsigned v, int n)
{
    static const char h[] = "0123456789abcdef";
    int i;
    for (i = n - 1; i >= 0; i--) line[linelen++] = h[(v >> (4 * i)) & 15];
}

static void flush_line(void)
{
    line[linelen++] = '\n';
    line[linelen] = 0;
    out(line);
    linelen = 0;
}

static void fail(const char *what, unsigned imm, unsigned in, unsigned a, unsigned b)
{
    put_s("MADEIRA-X86-82: MISMATCH "); put_s(what);
    put_s(" imm="); put_x(imm, 2); put_s(" in="); put_x(in, 2);
    put_s(" got="); put_x(a, 4); put_s(" want="); put_x(b, 4);
    flush_line();
    ExitProcess(1);
}

/* writes one function and returns it; carry: 0 clc, 1 stc; mem: operate on [ecx], lock: F0 prefix */
static fn_t make(unsigned opcode, unsigned reg, unsigned imm, int carry, int mem, int lock)
{
    unsigned char *p = page + used, *start = p;
    if (mem) { *p++ = 0x8B; *p++ = 0x4C; *p++ = 0x24; *p++ = 0x04; }          /* mov ecx,[esp+4] */
    else     { *p++ = 0x8B; *p++ = 0x44; *p++ = 0x24; *p++ = 0x04; }          /* mov eax,[esp+4] */
    *p++ = carry ? 0xF9 : 0xF8;                                               /* stc / clc */
    if (lock) *p++ = 0xF0;
    *p++ = (unsigned char)opcode;                                             /* 0x80 or 0x82 */
    *p++ = (unsigned char)(mem ? ((reg << 3) | 0x01) : (0xC0 | (reg << 3)));  /* [ecx] or al */
    *p++ = (unsigned char)imm;
    *p++ = 0x9F;                                                              /* lahf */
    *p++ = 0xC2; *p++ = 0x04; *p++ = 0x00;                                    /* ret 4 */
    used += 32;
    return (fn_t)start;
}

static unsigned expect_al(unsigned reg, unsigned a, unsigned b, int carry, int *known)
{
    *known = 1;
    switch (reg)
    {
    case 0: return (a + b) & 0xff;
    case 1: return a | b;
    case 2: return (a + b + carry) & 0xff;
    case 3: return (a - b - carry) & 0xff;
    case 4: return a & b;
    case 5: return (a - b) & 0xff;
    case 6: return a ^ b;
    default: *known = 0; return a;                                            /* CMP leaves AL alone */
    }
}

void start(void)
{
    static const unsigned imms[] = { 0x00, 0x01, 0x7f, 0x80, 0xfe, 0xff };
    static const unsigned ins[]  = { 0x00, 0x01, 0x7f, 0x80, 0xfe, 0xff };
    static const char *names[] = { "ADD", "OR", "ADC", "SBB", "AND", "SUB", "XOR", "CMP" };
    unsigned reg, i, j, carry, mem;
    unsigned char cell;

    page = VirtualAlloc(NULL, 0x10000, MEM_COMMIT | MEM_RESERVE, PAGE_EXECUTE_READWRITE);
    if (!page) { out("MADEIRA-X86-82: VirtualAlloc failed\n"); ExitProcess(2); }

    for (reg = 0; reg < 8; reg++)
    for (i = 0; i < 6; i++)
    for (carry = 0; carry < 2; carry++)
    for (mem = 0; mem < 3; mem++)                       /* 0 register, 1 memory, 2 memory with LOCK (not for CMP) */
    {
        fn_t f82, f80;
        if (mem == 2 && reg == 7) continue;
        f80 = make(0x80, reg, imms[i], carry, mem != 0, mem == 2);
        f82 = make(0x82, reg, imms[i], carry, mem != 0, mem == 2);
        for (j = 0; j < 6; j++)
        {
            unsigned r80, r82, v80, v82;
            int known;
            if (!mem)
            {
                r80 = f80(0x5a5a5a00 | ins[j]);          /* AL = ins[j]; AH is overwritten by LAHF */
                r82 = f82(0x5a5a5a00 | ins[j]);
                v80 = r80 & 0xff; v82 = r82 & 0xff;
                if (v80 != v82 || ((r80 >> 8) & 0xff) != ((r82 >> 8) & 0xff))
                    fail(names[reg], imms[i], ins[j], r82 & 0xffff, r80 & 0xffff);
                if (expect_al(reg, ins[j], imms[i], carry, &known) != v82 && known)
                    fail(names[reg], imms[i], ins[j], v82, expect_al(reg, ins[j], imms[i], carry, &known));
            }
            else
            {
                cell = (unsigned char)ins[j]; r80 = f80((unsigned)(ULONG_PTR)&cell); v80 = cell;
                cell = (unsigned char)ins[j]; r82 = f82((unsigned)(ULONG_PTR)&cell); v82 = cell;
                if (v80 != v82 || ((r80 >> 8) & 0xff) != ((r82 >> 8) & 0xff))
                    fail(names[reg], imms[i], ins[j], (v82 << 8) | ((r82 >> 8) & 0xff), (v80 << 8) | ((r80 >> 8) & 0xff));
                if (expect_al(reg, ins[j], imms[i], carry, &known) != v82 && known)
                    fail(names[reg], imms[i], ins[j], v82, expect_al(reg, ins[j], imms[i], carry, &known));
            }
            cases++;
        }
    }
    put_s("MADEIRA-X86-82: ok ("); put_x(cases, 5); put_s(" cases, hex)");
    flush_line();
    ExitProcess(0);
}

// Host driver for madeira_loop_guard_rewrite (src/unix/madeira_ags.cpp): lg in.dxbc out.dxbc [cap]
#include <cstdio>
#include <cstdlib>
#include <cstdint>
extern "C" int madeira_loop_guard_rewrite(const void *, size_t, uint32_t, void **, size_t *, char *, size_t);
int main(int argc, char **argv) {
    FILE *f = fopen(argv[1], "rb"); if (!f) return 2;
    fseek(f, 0, SEEK_END); size_t n = ftell(f); rewind(f);
    void *b = malloc(n); fread(b, 1, n, f); fclose(f);
    void *o = nullptr; size_t on = 0; char note[256];
    int rc = madeira_loop_guard_rewrite(b, n, argc > 3 ? (uint32_t)atoi(argv[3]) : 65536, &o, &on, note, sizeof note);
    printf("rc=%d note='%s' out=%zu bytes\n", rc, note, on);
    if (rc == 1 && argc > 2) { f = fopen(argv[2], "wb"); fwrite(o, 1, on, f); fclose(f); }
    return rc < 0;
}

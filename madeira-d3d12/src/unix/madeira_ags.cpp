/* ml1149: AMD AGS shader-intrinsic 64-bit atomics -> native SM6.6 64-bit atomics.
 *
 * D3D12 titles written before shader model 6.6 reached 64-bit atomics through
 * vendor extensions. AMD's (AGS) form is a sequence of 32-bit
 * InterlockedCompareExchange calls on a "magic" RWByteAddressBuffer in register
 * space 0x7FFF0ADE: the byte offset of each call is an encoded instruction, and
 * the compare/value operands carry the arguments. The driver pattern-matches the
 * sequence into one hardware 64-bit atomic. For a 2D/3D texture target
 * (ags_shader_intrinsics_dx12.hlsl, AmdExtD3DShaderIntrinsics_AtomicOp):
 *
 *   ret.x = magic.CmpXchg(instr phase 0, address.x, address.y)
 *   ret.y = magic.CmpXchg(instr phase 1, address.z, value.lo)
 *   uav[ret.x, ret.x(, ret.x)] = ret.y          <- names the target resource
 *   ret.y = magic.CmpXchg(instr phase 2, value.hi, ret.y)
 *   return ret                                  <- original 64-bit value
 *
 * Apple's converter knows none of this: it refuses such a shader outright (the
 * magic space is in no root signature), and even with it bound the sequence
 * would only scribble on a scratch buffer. This pass rewrites the DXIL module
 * itself (LLVM 15 reads DXIL bitcode and its writer's output converts) into what
 * DXC emits for a native InterlockedMax on a RWTexture2D<uint64_t>:
 *   dx.op.atomicBinOp.i64(78, target, op, x, y, z, hi << 32 | lo)
 * with the target retyped as a 64-bit atomic resource (i64 element, the
 * Atomic64Use tag), the shader flags that say so, and the magic buffer removed.
 * Handles and the declared shader model stay as they were: the converter takes
 * the i64 atomic from a 6.0 createHandle module (measured, tests/offline/atomic64).
 * The one hard rule: nothing may read the atomic's result, because Metal's
 * 64-bit texture atomics return none and the converter then rejects the
 * shader ("unsupported instruction: dx.op.atomicBinOp.i64").
 * The semantics follow vkd3d-proton's dxil-spirv (dxil_ags.cpp), which does the
 * same translation for Vulkan.
 *
 * Returns 1 and a malloc'd container when something was rewritten, 0 when the
 * shader uses no AGS intrinsics (nothing allocated), -1 when it does but the
 * pass could not translate it (note says why; the caller keeps the original). */
#include <cstdint>
#include <cstring>
#include <cstdio>
#include <cstdlib>
#include <vector>
#include <string>

#include "llvm/Bitcode/BitcodeReader.h"
#include "llvm/Bitcode/BitcodeWriter.h"
#include "llvm/IR/CFG.h"
#include "llvm/IR/Constants.h"
#include "llvm/IR/DerivedTypes.h"
#include "llvm/IR/Dominators.h"
#include "llvm/IR/IRBuilder.h"
#include "llvm/IR/Instructions.h"
#include "llvm/IR/LLVMContext.h"
#include "llvm/IR/Metadata.h"
#include "llvm/IR/Module.h"
#include "llvm/Support/Error.h"
#include "llvm/Support/MemoryBuffer.h"
#include "llvm/Support/raw_ostream.h"

using namespace llvm;

namespace {

constexpr uint32_t kAgsSpace = 0x7FFF0ADEu;      /* AmdExtD3DShaderIntrinsicsSpaceId */
constexpr uint32_t kAgsMagic = 0x5u;
constexpr uint32_t kAgsOpAtomicU64 = 0x18u;
constexpr unsigned kOpCreateHandle = 57, kOpTextureStore = 67, kOpAtomicBinOp = 78, kOpAtomicCmpXchg = 79;
constexpr unsigned kClassUAV = 1;
constexpr uint64_t kFlagInt64Ops = 1ull << 20, kFlagAtomicInt64Typed = 1ull << 27;   /* DXIL module flags */
constexpr uint64_t kSfi0Int64Ops = 0x8000, kSfi0AtomicInt64Typed = 0x400000;        /* SFI0 feature bits */

struct note_t { char *buf; size_t cap; };
static void say(note_t &n, const char *fmt, const char *a = "", unsigned b = 0)
{
    if (n.buf && n.cap) snprintf(n.buf, n.cap, fmt, a, b);
}

static uint32_t rd32(const uint8_t *p) { uint32_t v; memcpy(&v, p, 4); return v; }
static void wr32(uint8_t *p, uint32_t v) { memcpy(p, &v, 4); }

static bool is_dx_op(const CallInst *ci, unsigned op)
{
    const Function *f = ci->getCalledFunction();
    if (!f || !f->getName().startswith("dx.op.")) return false;
    auto *c = dyn_cast<ConstantInt>(ci->getArgOperand(0));
    return c && c->getZExtValue() == op;
}
static uint64_t cint(const Value *v, bool *ok)
{
    auto *c = dyn_cast<ConstantInt>(v);
    if (!c) { *ok = false; return 0; }
    return c->getZExtValue();
}

/* dx.resources = !{srvs, uavs, cbvs, samplers}; each entry's operand 0 is its
 * range ID (what createHandle's rangeId refers to), 3 its space, 6 its shape. */
static MDTuple *uav_list(Module &m)
{
    NamedMDNode *res = m.getNamedMetadata("dx.resources");
    if (!res || res->getNumOperands() != 1) return nullptr;
    auto *top = dyn_cast<MDTuple>(res->getOperand(0));
    if (!top || top->getNumOperands() < 2) return nullptr;
    return dyn_cast_or_null<MDTuple>(top->getOperand(1).get());
}
static uint64_t md_int(const MDOperand &o, bool *ok)
{
    auto *c = dyn_cast_or_null<ConstantAsMetadata>(o.get());
    if (!c) { *ok = false; return 0; }
    return cint(c->getValue(), ok);
}

struct seq_t { CallInst *ph[3] = {nullptr, nullptr, nullptr}; CallInst *marker = nullptr; };

static unsigned map_op(uint32_t ags_op, bool *ok)
{
    switch (ags_op) {   /* AmdExtD3DShaderIntrinsicsAtomicOp_* -> DXIL AtomicBinOpCode */
    case 0x01: return 6;   /* MinU64 -> UMin */
    case 0x02: return 7;   /* MaxU64 -> UMax */
    case 0x03: return 1;   /* AndU64 -> And */
    case 0x04: return 2;   /* OrU64 -> Or */
    case 0x05: return 3;   /* XorU64 -> Xor */
    case 0x06: return 0;   /* AddU64 -> Add */
    case 0x07: return 8;   /* XchgU64 -> Exchange */
    default: *ok = false; return 0;   /* CmpXchgU64 needs the other intrinsic shape */
    }
}

static int rewrite_module(Module &m, note_t &note, unsigned *n_rewritten, unsigned *n_used_result)
{
    LLVMContext &ctx = m.getContext();
    MDTuple *uavs = uav_list(m);
    if (!uavs) return 0;

    /* The magic buffer's range ID, and each UAV range's shape. */
    int64_t magic_id = -1;
    std::vector<std::pair<uint64_t, uint64_t>> shapes;   /* (id, shape) */
    for (const MDOperand &op : uavs->operands()) {
        auto *e = dyn_cast_or_null<MDTuple>(op.get());
        if (!e || e->getNumOperands() < 7) continue;
        bool ok = true;
        uint64_t id = md_int(e->getOperand(0), &ok), space = md_int(e->getOperand(3), &ok), shape = md_int(e->getOperand(6), &ok);
        if (!ok) continue;
        if (space == kAgsSpace) magic_id = (int64_t)id;
        shapes.push_back({id, shape});
    }
    if (magic_id < 0) return 0;

    /* Handles to the magic buffer, and the range ID behind any UAV handle. */
    auto uav_range = [&](Value *h) -> int64_t {
        auto *ci = dyn_cast<CallInst>(h);
        if (!ci || !is_dx_op(ci, kOpCreateHandle)) return -1;
        bool ok = true;
        uint64_t cls = cint(ci->getArgOperand(1), &ok), rid = cint(ci->getArgOperand(2), &ok);
        return ok && cls == kClassUAV ? (int64_t)rid : -1;
    };

    std::vector<seq_t> seqs;
    for (Function &f : m) {
        if (f.isDeclaration()) continue;
        for (BasicBlock &bb : f) {
            seq_t cur;
            int want = 0;
            for (Instruction &in : bb) {
                auto *ci = dyn_cast<CallInst>(&in);
                if (!ci) continue;
                if (is_dx_op(ci, kOpAtomicCmpXchg) && uav_range(ci->getArgOperand(1)) == magic_id) {
                    bool ok = true;
                    uint32_t code = (uint32_t)cint(ci->getArgOperand(2), &ok);
                    if (!ok || (code >> 28) != kAgsMagic) { say(note, "AGS magic write with a non-constant or foreign instruction%s", ""); return -1; }
                    uint32_t opcode = code & 0xff, phase = (code >> 24) & 3;
                    if (opcode != kAgsOpAtomicU64) { say(note, "AGS opcode %s0x%x is not translated", "", opcode); return -1; }
                    if ((int)phase != want) { say(note, "AGS AtomicU64 phases out of order (saw %sphase %u)", "", phase); return -1; }
                    cur.ph[phase] = ci;
                    want = phase + 1;
                    if (want == 3) {
                        if (!cur.marker) { say(note, "AGS AtomicU64 without a target store%s", ""); return -1; }
                        seqs.push_back(cur); cur = seq_t(); want = 0;
                    }
                } else if (want == 2 && !cur.marker && is_dx_op(ci, kOpTextureStore) && ci->getArgOperand(2) == cur.ph[0]) {
                    cur.marker = ci;
                }
            }
            if (want != 0) { say(note, "AGS AtomicU64 sequence split across blocks%s", ""); return -1; }
        }
    }
    if (seqs.empty()) { say(note, "magic AGS buffer declared but only used by other intrinsics%s", ""); return -1; }

    /* Target resources: every use of each must be one of our markers, since the
     * resource changes type from uint2 to uint64. */
    std::vector<int64_t> targets;
    for (seq_t &s : seqs) {
        int64_t rid = uav_range(s.marker->getArgOperand(1));
        if (rid < 0) { say(note, "AGS target is not a plain UAV handle%s", ""); return -1; }
        bool seen = false;
        for (int64_t t : targets) seen |= t == rid;
        if (!seen) targets.push_back(rid);
    }
    for (Function &f : m) for (BasicBlock &bb : f) for (Instruction &in : bb) {
        auto *ci = dyn_cast<CallInst>(&in);
        if (!ci || !is_dx_op(ci, kOpCreateHandle)) continue;
        int64_t rid = uav_range(ci);
        bool is_target = false;
        for (int64_t t : targets) is_target |= t == rid;
        if (!is_target) continue;
        for (User *u : ci->users()) {
            bool is_marker = false;
            for (seq_t &s : seqs) is_marker |= u == s.marker;
            if (!is_marker) { say(note, "AGS target UAV %sis also read or written directly (id %u)", "", (unsigned)rid); return -1; }
        }
    }

    /* Rewrite each sequence at its phase-2 call, where every operand dominates. */
    Type *i32 = Type::getInt32Ty(ctx), *i64 = Type::getInt64Ty(ctx);
    Type *handle_ty = seqs[0].marker->getArgOperand(1)->getType();
    FunctionCallee binop = m.getOrInsertFunction("dx.op.atomicBinOp.i64",
        FunctionType::get(i64, {i32, handle_ty, i32, i32, i32, i32, i64}, false));
    if (auto *bf = dyn_cast<Function>(binop.getCallee())) bf->addFnAttr(Attribute::NoUnwind);

    for (seq_t &s : seqs) {
        bool ok = true;
        unsigned op = map_op((uint32_t)(cast<ConstantInt>(s.ph[0]->getArgOperand(2))->getZExtValue() >> 8) & 0xffff, &ok);
        if (!ok) { say(note, "AGS AtomicU64 variant %snot translated", ""); return -1; }
        int64_t rid = uav_range(s.marker->getArgOperand(1));
        uint64_t shape = 2;
        for (auto &sh : shapes) if ((int64_t)sh.first == rid) shape = sh.second;
        Value *undef = UndefValue::get(i32);
        Value *x = s.ph[0]->getArgOperand(5), *y = s.ph[0]->getArgOperand(6), *z = s.ph[1]->getArgOperand(5);
        if (shape == 1) { y = undef; z = undef; }                  /* Texture1D */
        else if (shape == 2 || shape == 3) z = undef;              /* Texture2D(MS) */
        Value *lo = s.ph[1]->getArgOperand(6), *hi = s.ph[2]->getArgOperand(5);

        IRBuilder<> b(s.ph[2]);
        Value *v = b.CreateOr(b.CreateShl(b.CreateZExt(hi, i64), 32), b.CreateZExt(lo, i64));
        Value *r = b.CreateCall(binop, {ConstantInt::get(i32, kOpAtomicBinOp), s.marker->getArgOperand(1),
                                        ConstantInt::get(i32, op), x, y, z, v});

        /* Metal's 64-bit texture atomics return nothing, and the converter
         * refuses the whole shader if the original value is read even by dead
         * code. Nanite never reads it; only materialise it when something does. */
        s.marker->eraseFromParent();
        bool used = !s.ph[2]->use_empty() || !s.ph[0]->use_empty();
        for (User *u : s.ph[1]->users()) used |= u != s.ph[2];
        if (used) {
            Value *old_lo = b.CreateTrunc(r, i32), *old_hi = b.CreateTrunc(b.CreateLShr(r, 32), i32);
            s.ph[2]->replaceAllUsesWith(old_hi);
            s.ph[1]->replaceAllUsesWith(old_hi);   /* ret.y is overwritten by phase 2 */
            s.ph[0]->replaceAllUsesWith(old_lo);
            (*n_used_result)++;
        }
        s.ph[2]->eraseFromParent();
        s.ph[1]->eraseFromParent();
        s.ph[0]->eraseFromParent();
    }

    /* The results now come from the phase-2 point; every remaining use must be
     * dominated by it (in AGS code the results are only used afterwards). */
    for (Function &f : m) {
        if (f.isDeclaration()) continue;
        DominatorTree dt(f);
        for (BasicBlock &bb : f) for (Instruction &in : bb)
            for (Use &u : in.operands()) {
                auto *def = dyn_cast<Instruction>(u.get());
                if (def && !dt.dominates(def, u)) { say(note, "rewrite broke dominance in %s", f.getName().str().c_str()); return -1; }
            }
    }

    /* Drop the magic handles, then the magic resource, renumbering the UAV range
     * IDs above it (createHandle refers to ranges by ID). */
    std::vector<CallInst *> dead;
    for (Function &f : m) for (BasicBlock &bb : f) for (Instruction &in : bb) {
        auto *ci = dyn_cast<CallInst>(&in);
        if (ci && is_dx_op(ci, kOpCreateHandle) && uav_range(ci) == magic_id) {
            if (!ci->use_empty()) { say(note, "magic AGS buffer still used after the rewrite%s", ""); return -1; }
            dead.push_back(ci);
        }
    }
    for (CallInst *ci : dead) ci->eraseFromParent();
    for (Function &f : m) for (BasicBlock &bb : f) for (Instruction &in : bb) {
        auto *ci = dyn_cast<CallInst>(&in);
        int64_t rid = ci && is_dx_op(ci, kOpCreateHandle) ? uav_range(ci) : -1;
        if (rid > magic_id) ci->setArgOperand(2, ConstantInt::get(i32, rid - 1));
    }

    StructType *t64 = StructType::getTypeByName(ctx, "class.RWTexture2D<unsigned long long>");
    std::vector<Metadata *> kept;
    for (const MDOperand &op : uavs->operands()) {
        auto *e = dyn_cast_or_null<MDTuple>(op.get());
        if (!e) continue;
        bool ok = true;
        int64_t id = (int64_t)md_int(e->getOperand(0), &ok);
        if (id == magic_id) continue;
        std::vector<Metadata *> f(e->op_begin(), e->op_end());
        if (id > magic_id) f[0] = ConstantAsMetadata::get(ConstantInt::get(i32, id - 1));
        bool is_target = false;
        for (int64_t t : targets) is_target |= t == id;
        if (is_target) {
            /* uint64 element: DXC types it as { i64 }, element type U32 plus the
             * Atomic64Use tag (3) in the extended properties. */
            const char *nm = "class.RWTexture2D<unsigned long long>";
            uint64_t shape = 2;
            for (auto &sh : shapes) if ((int64_t)sh.first == id) shape = sh.second;
            if (shape == 4) nm = "class.RWTexture3D<unsigned long long>";
            else if (shape == 7) nm = "class.RWTexture2DArray<unsigned long long>";
            else if (shape == 1) nm = "class.RWTexture1D<unsigned long long>";
            t64 = StructType::getTypeByName(ctx, nm);
            if (!t64) t64 = StructType::create(ctx, {i64}, nm);
            f[1] = ConstantAsMetadata::get(UndefValue::get(PointerType::get(t64, 0)));
            std::vector<Metadata *> props;
            if (f.size() > 10 && f[10]) {
                auto *p = cast<MDTuple>(f[10]);
                for (unsigned i = 0; i + 1 < p->getNumOperands(); i += 2) {
                    bool ok2 = true;
                    uint64_t tag = md_int(p->getOperand(i), &ok2);
                    if (ok2 && tag == 3) continue;
                    props.push_back(p->getOperand(i)); props.push_back(p->getOperand(i + 1));
                }
            } else {
                props.push_back(ConstantAsMetadata::get(ConstantInt::get(i32, 0)));
                props.push_back(ConstantAsMetadata::get(ConstantInt::get(i32, 5)));
            }
            props.push_back(ConstantAsMetadata::get(ConstantInt::get(i32, 3)));
            props.push_back(ConstantAsMetadata::get(ConstantInt::get(i32, 1)));
            while (f.size() < 11) f.push_back(nullptr);
            f[10] = MDTuple::get(ctx, props);
        }
        kept.push_back(MDTuple::get(ctx, f));
    }
    {
        NamedMDNode *res = m.getNamedMetadata("dx.resources");
        auto *top = cast<MDTuple>(res->getOperand(0));
        std::vector<Metadata *> t(top->op_begin(), top->op_end());
        t[1] = kept.empty() ? nullptr : MDTuple::get(ctx, kept);
        MDTuple *ntop = MDTuple::get(ctx, t);
        res->setOperand(0, ntop);
        /* dx.entryPoints = !{fn, name, signatures, resources, properties}: point
         * operand 3 at the new list and OR the 64-bit atomic flags into tag 0. */
        if (NamedMDNode *eps = m.getNamedMetadata("dx.entryPoints")) {
            for (unsigned i = 0; i < eps->getNumOperands(); i++) {
                auto *ep = cast<MDTuple>(eps->getOperand(i));
                std::vector<Metadata *> e(ep->op_begin(), ep->op_end());
                if (e.size() < 5) e.resize(5, nullptr);
                if (e[3]) e[3] = ntop;
                std::vector<Metadata *> props;
                bool have_flags = false;
                if (e[4]) {
                    auto *p = cast<MDTuple>(e[4]);
                    for (unsigned k = 0; k + 1 < p->getNumOperands(); k += 2) {
                        bool ok = true;
                        uint64_t tag = md_int(p->getOperand(k), &ok);
                        Metadata *val = p->getOperand(k + 1);
                        if (ok && tag == 0) {
                            bool ok2 = true;
                            uint64_t fl = md_int(p->getOperand(k + 1), &ok2);
                            val = ConstantAsMetadata::get(ConstantInt::get(i64, fl | kFlagInt64Ops | kFlagAtomicInt64Typed));
                            have_flags = true;
                        }
                        props.push_back(p->getOperand(k)); props.push_back(val);
                    }
                }
                if (!have_flags) {
                    props.insert(props.begin(), ConstantAsMetadata::get(ConstantInt::get(i64, kFlagInt64Ops | kFlagAtomicInt64Typed)));
                    props.insert(props.begin(), ConstantAsMetadata::get(ConstantInt::get(i32, 0)));
                }
                e[4] = MDTuple::get(ctx, props);
                eps->setOperand(i, MDTuple::get(ctx, e));
            }
        }
    }
    {   /* declarations nothing calls any more (createHandle, the magic CmpXchg...) */
        std::vector<Function *> unused;
        for (Function &f : m) if (f.isDeclaration() && f.use_empty() && f.getName().startswith("dx.op.")) unused.push_back(&f);
        for (Function *f : unused) f->eraseFromParent();
    }
    *n_rewritten = (unsigned)seqs.size();
    return 1;
}

/* Spider-Man 2 character mesh pool (2,046 MB = 536,346,624 uint elements):
 * Metal's MTLTextureTypeTextureBuffer width limit on Apple7-Apple9 is 2^28
 * (268,435,456 elements = 1 GB), so typed buffer reads/writes past 1 GB return
 * 0 or are dropped. Rewriting TypedBuffer<U32> (dx.op.bufferLoad.i32 /
 * dx.op.bufferStore.i32) on the pool register into RawBuffer
 * (dx.op.rawBufferLoad.i32 / dx.op.rawBufferStore.i32 at index << 2) lets the
 * converted Metal shader address the full 2 GB buffer via its GPU VA. */
constexpr unsigned kOpBufferLoad = 68, kOpBufferStore = 69, kOpRawBufferLoad = 139, kOpRawBufferStore = 140;
constexpr unsigned kOpAnnotateHandle = 216, kOpCreateHandleFromBinding = 217;
constexpr uint64_t kFlagRawBuffers = 1ull << 4;
constexpr uint64_t kSfi0RawBuffers = 0x10ull;

struct t2r_target { uint64_t hash; uint32_t space, reg; bool uav; };
static constexpr t2r_target kT2RTargets[] = {
    { 0x3d50acdb571dce22ull, 0, 1, false },   /* CS_ModelSkinning: Buffer<uint> at t1 */
    { 0x14902794dd7134e4ull, 0, 0, false },   /* vertex morph: Buffer<uint> at t0 */
    { 0x9538ca33c087a84cull, 0, 1, true  },   /* CS_AnimSplineMeshGenIndices (symbiote): RWBuffer<uint> at u1 */
    { 0x125a4957ab64d24dull, 0, 1, true  },   /* CS_AnimSplineMeshGenIndicesFinalize (symbiote): RWBuffer<uint> at u1 */
};

static uint64_t fnv1_shader_hash(const uint8_t *p, size_t size)
{
    uint64_t h = 0xcbf29ce484222325ull;
    for (size_t i = 0; i < size; i++)
        h = (h * 0x100000001b3ull) ^ p[i];
    return h;
}

static bool match_t2r_target(const uint8_t *d, size_t len, t2r_target *out)
{
    if (const char *env = getenv("MADEIRA_T2R_TARGET")) {
        unsigned sp = 0, rg = 0, u = 0;
        if (sscanf(env, "%u:%u:%u", &sp, &rg, &u) == 3) {
            *out = { 0, sp, rg, u != 0 };
            return true;
        }
    }
    uint64_t h = fnv1_shader_hash(d, len);
    for (const auto &t : kT2RTargets) {
        if (t.hash == h) { *out = t; return true; }
    }
    return false;
}

static int rewrite_t2r_module(Module &m, const t2r_target &tgt, note_t &note,
                              unsigned *n_handles, unsigned *n_loads, unsigned *n_stores)
{
    LLVMContext &ctx = m.getContext();
    Type *i32 = Type::getInt32Ty(ctx), *i64 = Type::getInt64Ty(ctx), *i8 = Type::getInt8Ty(ctx);
    NamedMDNode *res = m.getNamedMetadata("dx.resources");
    if (!res || res->getNumOperands() != 1) { say(note, "t2r: missing dx.resources%s", ""); return -1; }
    auto *top = dyn_cast<MDTuple>(res->getOperand(0));
    if (!top || top->getNumOperands() < 2) { say(note, "t2r: malformed dx.resources%s", ""); return -1; }
    unsigned list_idx = tgt.uav ? 1u : 0u;
    auto *rlist = dyn_cast_or_null<MDTuple>(top->getOperand(list_idx).get());
    if (!rlist) { say(note, "t2r: no %s list in dx.resources", tgt.uav ? "UAV" : "SRV"); return -1; }

    /* 1. Resource metadata entry: TypedBuffer (10) -> RawBuffer (11), element-type tag -> null. */
    int64_t target_range_id = -1;
    std::vector<Metadata *> new_rlist;
    for (const MDOperand &op : rlist->operands()) {
        auto *e = dyn_cast_or_null<MDTuple>(op.get());
        if (!e || e->getNumOperands() < (tgt.uav ? 11u : 9u)) {
            new_rlist.push_back(op.get());
            continue;
        }
        bool ok = true;
        uint64_t id = md_int(e->getOperand(0), &ok);
        uint64_t space = md_int(e->getOperand(3), &ok);
        uint64_t reg = md_int(e->getOperand(4), &ok);
        uint64_t cnt = md_int(e->getOperand(5), &ok);
        uint64_t shape = md_int(e->getOperand(6), &ok);
        if (ok && space == tgt.space && reg == tgt.reg && cnt == 1 && shape == 10) {
            target_range_id = (int64_t)id;
            std::vector<Metadata *> f(e->op_begin(), e->op_end());
            f[6] = ConstantAsMetadata::get(ConstantInt::get(i32, 11));
            f[tgt.uav ? 10 : 8] = nullptr;
            new_rlist.push_back(MDTuple::get(ctx, f));
        } else {
            new_rlist.push_back(op.get());
        }
    }
    if (target_range_id < 0) {
        if (note.buf && note.cap)
            snprintf(note.buf, note.cap, "typed %s space %u register %u not found in the resource metadata",
                     tgt.uav ? "UAV" : "SRV", tgt.space, tgt.reg);
        return -1;
    }

    /* 2. Handles created for that binding (SM 6.6 createHandleFromBinding or SM 6.0 createHandle). */
    std::vector<Value *> created;
    auto in_vec = [](const std::vector<Value *> &v, const Value *x) {
        for (const Value *e : v) if (e == x) return true;
        return false;
    };
    for (Function &f : m) for (BasicBlock &bb : f) for (Instruction &in : bb) {
        auto *ci = dyn_cast<CallInst>(&in);
        if (!ci) continue;
        if (is_dx_op(ci, kOpCreateHandleFromBinding) && ci->arg_size() >= 2) {
            Value *b = ci->getArgOperand(1);
            if (isa<ConstantAggregateZero>(b)) {
                if (tgt.reg == 0 && tgt.space == 0 && !tgt.uav) created.push_back(ci);
            } else if (auto *cs = dyn_cast<ConstantStruct>(b)) {
                if (cs->getNumOperands() == 4) {
                    bool ok = true;
                    uint64_t lo = cint(cs->getOperand(0), &ok), hi = cint(cs->getOperand(1), &ok);
                    uint64_t sp = cint(cs->getOperand(2), &ok), cl = cint(cs->getOperand(3), &ok);
                    if (ok && lo == tgt.reg && hi == tgt.reg && sp == tgt.space && cl == (tgt.uav ? 1u : 0u))
                        created.push_back(ci);
                }
            }
        } else if (is_dx_op(ci, kOpCreateHandle) && ci->arg_size() >= 3) {
            bool ok = true;
            uint64_t cl = cint(ci->getArgOperand(1), &ok), rid = cint(ci->getArgOperand(2), &ok);
            if (ok && cl == (tgt.uav ? 1u : 0u) && (int64_t)rid == target_range_id)
                created.push_back(ci);
        }
    }
    if (created.empty()) { say(note, "no handle created for the binding%s", ""); return -1; }

    /* 3. Handle annotations: {10, 261} -> {11, 0} for SRV, {4106, 261} -> {4107, 0} for UAV. */
    std::vector<Value *> handles;
    for (Function &f : m) for (BasicBlock &bb : f) for (Instruction &in : bb) {
        auto *ci = dyn_cast<CallInst>(&in);
        if (!ci || !is_dx_op(ci, kOpAnnotateHandle) || ci->arg_size() < 3) continue;
        if (!in_vec(created, ci->getArgOperand(1))) continue;
        auto *cs = dyn_cast<ConstantStruct>(ci->getArgOperand(2));
        if (!cs || cs->getNumOperands() != 2) continue;
        bool ok = true;
        uint64_t w0 = cint(cs->getOperand(0), &ok), w1 = cint(cs->getOperand(1), &ok);
        if (!ok || w0 != (tgt.uav ? 4106u : 10u) || w1 != 261u) continue;
        ci->setArgOperand(2, ConstantStruct::get(cs->getType(),
            {ConstantInt::get(i32, tgt.uav ? 4107u : 11u), ConstantInt::get(i32, 0)}));
        handles.push_back(ci);
    }
    if (handles.empty()) handles = created;   /* SM 6.0-6.5 createHandle has no annotateHandle */

    /* 4. Collect bufferLoad / bufferStore calls on those handles. */
    std::vector<CallInst *> loads, stores;
    for (Function &f : m) for (BasicBlock &bb : f) for (Instruction &in : bb) {
        auto *ci = dyn_cast<CallInst>(&in);
        if (!ci || ci->arg_size() < 3 || !in_vec(handles, ci->getArgOperand(1))) continue;
        if (is_dx_op(ci, kOpBufferLoad)) loads.push_back(ci);
        else if (tgt.uav && is_dx_op(ci, kOpBufferStore) && ci->arg_size() >= 9) stores.push_back(ci);
    }
    if (loads.empty() && stores.empty()) { say(note, "no typed loads or stores found on the handle%s", ""); return -1; }

    Type *handle_ty = handles[0]->getType();
    Function *raw_load_fn = nullptr, *raw_store_fn = nullptr;
    if (!loads.empty()) {
        Type *ret_ty = loads[0]->getType();
        FunctionCallee fc = m.getOrInsertFunction("dx.op.rawBufferLoad.i32",
            FunctionType::get(ret_ty, {i32, handle_ty, i32, i32, i8, i32}, false));
        raw_load_fn = cast<Function>(fc.getCallee());
        if (Function *orig = loads[0]->getCalledFunction()) raw_load_fn->setAttributes(orig->getAttributes());
        else { raw_load_fn->addFnAttr(Attribute::NoUnwind); raw_load_fn->addFnAttr(Attribute::ReadOnly); }
    }
    if (!stores.empty()) {
        FunctionCallee fc = m.getOrInsertFunction("dx.op.rawBufferStore.i32",
            FunctionType::get(Type::getVoidTy(ctx), {i32, handle_ty, i32, i32, i32, i32, i32, i32, i8, i32}, false));
        raw_store_fn = cast<Function>(fc.getCallee());
        if (Function *orig = stores[0]->getCalledFunction()) raw_store_fn->setAttributes(orig->getAttributes());
        else raw_store_fn->addFnAttr(Attribute::NoUnwind);
    }

    Value *u32_undef = UndefValue::get(i32);
    unsigned t2r_seq = 0;
    for (CallInst *ci : loads) {
        IRBuilder<> b(ci);
        char nm[24]; snprintf(nm, sizeof nm, "t2r%u", ++t2r_seq);
        Value *byte_off = b.CreateShl(ci->getArgOperand(2), ConstantInt::get(i32, 2), nm);
        Value *r = b.CreateCall(raw_load_fn, {ConstantInt::get(i32, kOpRawBufferLoad), ci->getArgOperand(1),
                                              byte_off, u32_undef, ConstantInt::get(i8, 1), ConstantInt::get(i32, 4)});
        ci->replaceAllUsesWith(r);
        ci->eraseFromParent();
    }
    for (CallInst *ci : stores) {
        IRBuilder<> b(ci);
        char nm[24]; snprintf(nm, sizeof nm, "t2r%u", ++t2r_seq);
        Value *byte_off = b.CreateShl(ci->getArgOperand(2), ConstantInt::get(i32, 2), nm);
        Value *val0 = ci->getArgOperand(4);
        b.CreateCall(raw_store_fn, {ConstantInt::get(i32, kOpRawBufferStore), ci->getArgOperand(1),
                                    byte_off, u32_undef, val0, u32_undef, u32_undef, u32_undef,
                                    ConstantInt::get(i8, 1), ConstantInt::get(i32, 4)});
        ci->eraseFromParent();
    }

    /* 5. Publish updated dx.resources and set UseRawAndStructuredBuffers (16) on dx.entryPoints. */
    std::vector<Metadata *> t(top->op_begin(), top->op_end());
    t[list_idx] = MDTuple::get(ctx, new_rlist);
    MDTuple *ntop = MDTuple::get(ctx, t);
    res->setOperand(0, ntop);
    if (NamedMDNode *eps = m.getNamedMetadata("dx.entryPoints")) {
        for (unsigned i = 0; i < eps->getNumOperands(); i++) {
            auto *ep = cast<MDTuple>(eps->getOperand(i));
            std::vector<Metadata *> e(ep->op_begin(), ep->op_end());
            if (e.size() < 5) e.resize(5, nullptr);
            if (e[3]) e[3] = ntop;
            std::vector<Metadata *> props;
            bool have_flags = false;
            if (e[4]) {
                auto *p = cast<MDTuple>(e[4]);
                for (unsigned k = 0; k + 1 < p->getNumOperands(); k += 2) {
                    bool ok = true;
                    uint64_t tag = md_int(p->getOperand(k), &ok);
                    Metadata *val = p->getOperand(k + 1);
                    if (ok && tag == 0) {
                        bool ok2 = true;
                        uint64_t fl = md_int(p->getOperand(k + 1), &ok2);
                        val = ConstantAsMetadata::get(ConstantInt::get(i64, fl | kFlagRawBuffers));
                        have_flags = true;
                    }
                    props.push_back(p->getOperand(k)); props.push_back(val);
                }
            }
            if (!have_flags) {
                props.insert(props.begin(), ConstantAsMetadata::get(ConstantInt::get(i64, kFlagRawBuffers)));
                props.insert(props.begin(), ConstantAsMetadata::get(ConstantInt::get(i32, 0)));
            }
            e[4] = MDTuple::get(ctx, props);
            eps->setOperand(i, MDTuple::get(ctx, e));
        }
    }
    {
        std::vector<Function *> unused;
        for (Function &f : m) if (f.isDeclaration() && f.use_empty() && f.getName().startswith("dx.op.")) unused.push_back(&f);
        for (Function *f : unused) f->eraseFromParent();
    }
    *n_handles = (unsigned)handles.size();
    *n_loads = (unsigned)loads.size();
    *n_stores = (unsigned)stores.size();
    return 1;
}

/* madeira-bcd (build 647): a 64-bit texture atomic whose old value is read, in a pixel shader.
 *
 * The Last of Us Part I's transparency pass (MainPsOitAppend, the owner's logs of 2026-11-08/09, four shaders of 6833 to
 * 6845 bytes) keeps the four nearest layers of every pixel in four RWTexture2D<uint64_t> (R32G32_UINT, u2-u5): each
 * InterlockedMax hands the value it replaced to the next layer (old = max(L0, v); v = min(old, v); ...). Metal's 64-bit
 * texture atomics return nothing, so the converter refuses the shader ("IR contains unsupported instruction:
 * dx.op.atomicBinOp.i64"), on every target (Apple9/iOS 18 and Apple10/iOS 26 both), and the pass was skipped: the menu's
 * curtain and windows were drawn wrong. A pixel shader can do the same without an atomic: with the textures made
 * rasterizer-ordered views (ROV, Metal's raster order groups, which the converter emits), the accesses of fragments on
 * the same pixel run in primitive order, so "load, combine, store, return the loaded value" is exactly the atomic.
 * The pass rewrites every atomicBinOp.i64 on such a texture into textureLoad.i32 + textureStore.i32 of its two 32-bit
 * halves (R = low, G = high, as D3D lays a uint64 out in R32G32_UINT), retypes the resource as a ROV of uint2 (element
 * U32, no Atomic64Use tag, its handles' properties likewise) and sets the shader flags that say so (ROVs, typed UAV
 * loads of additional formats). Only a pixel shader with at least one read result, only plain bindings; anything
 * else leaves the shader as it was (the converter then refuses it as before). Returns 1 rewritten, 0 nothing to do,
 * -1 could not (note says why). */
constexpr unsigned kOpTextureLoad = 66;
constexpr uint64_t kFlagUAVLoadAdditional = 1ull << 13, kFlagROVs = 1ull << 18;   /* DXIL module flags */
constexpr uint64_t kSfi0TypedUAVLoadAdditional = 0x800, kSfi0ROVs = 0x1000;        /* SFI0 feature bits */

struct a64_res { uint32_t space, lo; bool read = false; };

static bool a64_binding(Module &m, Value *h, uint32_t *space, uint32_t *lo)
{
    auto *ci = dyn_cast<CallInst>(h);
    if (!ci) return false;
    if (is_dx_op(ci, kOpAnnotateHandle) && ci->arg_size() >= 2) return a64_binding(m, ci->getArgOperand(1), space, lo);
    if (is_dx_op(ci, kOpCreateHandleFromBinding) && ci->arg_size() >= 3) {
        auto *cs = dyn_cast<ConstantStruct>(ci->getArgOperand(1));
        if (!cs || cs->getNumOperands() != 4) return false;
        bool ok = true;
        uint64_t l = cint(cs->getOperand(0), &ok), hi = cint(cs->getOperand(1), &ok), sp = cint(cs->getOperand(2), &ok),
                 cl = cint(cs->getOperand(3), &ok), idx = cint(ci->getArgOperand(2), &ok);
        if (!ok || cl != kClassUAV || l != hi || idx != l) return false;   /* one register, a constant index */
        *space = (uint32_t)sp; *lo = (uint32_t)l;
        return true;
    }
    if (is_dx_op(ci, kOpCreateHandle) && ci->arg_size() >= 4) {
        bool ok = true;
        uint64_t cl = cint(ci->getArgOperand(1), &ok), rid = cint(ci->getArgOperand(2), &ok);
        if (!ok || cl != kClassUAV) return false;
        MDTuple *uavs = uav_list(m);
        if (!uavs) return false;
        for (const MDOperand &op : uavs->operands()) {
            auto *e = dyn_cast_or_null<MDTuple>(op.get());
            if (!e || e->getNumOperands() < 7) continue;
            bool ok2 = true;
            if (md_int(e->getOperand(0), &ok2) != rid || !ok2) continue;
            uint64_t sp = md_int(e->getOperand(3), &ok2), l = md_int(e->getOperand(4), &ok2), cnt = md_int(e->getOperand(5), &ok2);
            uint64_t idx = cint(ci->getArgOperand(3), &ok2);
            if (!ok2 || cnt != 1 || idx != l) return false;
            *space = (uint32_t)sp; *lo = (uint32_t)l;
            return true;
        }
    }
    return false;
}

static int rewrite_a64rov_module(Module &m, note_t &note, unsigned *n_atomics, unsigned *n_res, bool *any_left)
{
    LLVMContext &ctx = m.getContext();
    NamedMDNode *sm = m.getNamedMetadata("dx.shaderModel");
    auto *smt = sm && sm->getNumOperands() == 1 ? dyn_cast<MDTuple>(sm->getOperand(0)) : nullptr;
    auto *kind = smt && smt->getNumOperands() >= 1 ? dyn_cast_or_null<MDString>(smt->getOperand(0).get()) : nullptr;
    if (!kind || kind->getString() != "ps") return 0;   /* ROVs exist in pixel shaders only */
    MDTuple *uavs = uav_list(m);
    if (!uavs) return 0;

    /* The 64-bit atomics, by binding; which bindings have one whose result is read. */
    std::vector<CallInst *> atomics;
    std::vector<a64_res> res;
    auto find = [&](uint32_t sp, uint32_t lo) -> a64_res * {
        for (a64_res &r : res) if (r.space == sp && r.lo == lo) return &r;
        return nullptr;
    };
    for (Function &f : m) for (BasicBlock &bb : f) for (Instruction &in : bb) {
        auto *ci = dyn_cast<CallInst>(&in);
        if (!ci || !is_dx_op(ci, kOpAtomicBinOp) || !ci->getType()->isIntegerTy(64) || ci->arg_size() < 7) continue;
        uint32_t sp = 0, lo = 0;
        if (!a64_binding(m, ci->getArgOperand(1), &sp, &lo)) {
            if (!ci->use_empty()) { say(note, "a 64-bit atomic whose result is read goes through a handle %sthat is not a plain binding", ""); return -1; }
            continue;
        }
        a64_res *r = find(sp, lo);
        if (!r) { res.push_back({sp, lo}); r = &res.back(); }
        r->read |= !ci->use_empty();
        atomics.push_back(ci);
    }
    /* Nothing read: the converter takes the shader as it is. Something read: every 64-bit atomic of the shader on a plain
     * binding goes the ordered way (one mechanism for the whole pass: the layer nobody reads back, The Last of Us Part I's
     * fourth, would otherwise stay a native atomic beside three ordered textures). */
    bool any_read = false;
    for (a64_res &r : res) any_read |= r.read;
    if (!any_read) return 0;
    std::vector<a64_res> want = res;
    auto wanted = [&](uint32_t sp, uint32_t lo) {
        for (a64_res &r : want) if (r.space == sp && r.lo == lo) return true;
        return false;
    };

    /* The resources: plain 2D textures (shape 2) in the UAV list. */
    for (a64_res &r : want) {
        bool found = false;
        for (const MDOperand &op : uavs->operands()) {
            auto *e = dyn_cast_or_null<MDTuple>(op.get());
            if (!e || e->getNumOperands() < 11) continue;
            bool ok = true;
            uint64_t sp = md_int(e->getOperand(3), &ok), lo = md_int(e->getOperand(4), &ok), cnt = md_int(e->getOperand(5), &ok),
                     shape = md_int(e->getOperand(6), &ok);
            if (!ok || sp != r.space || lo != r.lo) continue;
            if (cnt != 1 || shape != 2) { say(note, "the 64-bit atomic's texture at register %su%u is not one plain 2D texture", "", r.lo); return -1; }
            found = true;
        }
        if (!found) { say(note, "the 64-bit atomic's texture at register %su%u is not in the resource list", "", r.lo); return -1; }
    }

    /* Every other use of those handles must be an annotateHandle or a 64-bit atomic (the shaders seen use nothing else). */
    for (Function &f : m) for (BasicBlock &bb : f) for (Instruction &in : bb) {
        auto *ci = dyn_cast<CallInst>(&in);
        if (!ci || !(is_dx_op(ci, kOpAnnotateHandle) || is_dx_op(ci, kOpCreateHandleFromBinding) || is_dx_op(ci, kOpCreateHandle))) continue;
        uint32_t sp = 0, lo = 0;
        if (!a64_binding(m, ci, &sp, &lo) || !wanted(sp, lo)) continue;
        for (User *u : ci->users()) {
            auto *uc = dyn_cast<CallInst>(u);
            bool fine = uc && (is_dx_op(uc, kOpAnnotateHandle) ||
                               (is_dx_op(uc, kOpAtomicBinOp) && uc->getType()->isIntegerTy(64) && uc->getArgOperand(1) == ci));
            if (!fine) { say(note, "the texture at register %su%u is also used another way", "", lo); return -1; }
        }
    }

    Type *i8 = Type::getInt8Ty(ctx), *i32 = Type::getInt32Ty(ctx), *i64 = Type::getInt64Ty(ctx);
    Type *handle_ty = atomics[0]->getArgOperand(1)->getType();
    StructType *ret_ty = StructType::getTypeByName(ctx, "dx.types.ResRet.i32");
    if (!ret_ty) ret_ty = StructType::create(ctx, {i32, i32, i32, i32, i32}, "dx.types.ResRet.i32");
    FunctionCallee load = m.getOrInsertFunction("dx.op.textureLoad.i32",
        FunctionType::get(ret_ty, {i32, handle_ty, i32, i32, i32, i32, i32, i32, i32}, false));
    if (auto *lf = dyn_cast<Function>(load.getCallee())) { lf->addFnAttr(Attribute::NoUnwind); lf->addFnAttr(Attribute::ReadOnly); }
    FunctionCallee store = m.getOrInsertFunction("dx.op.textureStore.i32",
        FunctionType::get(Type::getVoidTy(ctx), {i32, handle_ty, i32, i32, i32, i32, i32, i32, i32, i8}, false));
    if (auto *sf = dyn_cast<Function>(store.getCallee())) sf->addFnAttr(Attribute::NoUnwind);

    unsigned done = 0;
    for (CallInst *ci : atomics) {
        uint32_t sp = 0, lo = 0;
        if (!a64_binding(m, ci->getArgOperand(1), &sp, &lo) || !wanted(sp, lo)) continue;
        bool ok = true;
        unsigned op = (unsigned)cint(ci->getArgOperand(2), &ok);
        if (!ok || op > 8) { say(note, "a 64-bit atomic with an unknown or non-constant operation %s(%u)", "", op); return -1; }
        Value *h = ci->getArgOperand(1), *x = ci->getArgOperand(3), *y = ci->getArgOperand(4), *z = ci->getArgOperand(5);
        Value *v = ci->getArgOperand(6), *u32 = UndefValue::get(i32);
        IRBuilder<> b(ci);
        Value *ld = b.CreateCall(load, {ConstantInt::get(i32, kOpTextureLoad), h, u32, x, y, z, u32, u32, u32});
        Value *olo = b.CreateExtractValue(ld, 0), *ohi = b.CreateExtractValue(ld, 1);
        Value *old = b.CreateOr(b.CreateShl(b.CreateZExt(ohi, i64), 32), b.CreateZExt(olo, i64));
        Value *nv = nullptr;
        switch (op) {   /* DXIL AtomicBinOpCode */
        case 0: nv = b.CreateAdd(old, v); break;
        case 1: nv = b.CreateAnd(old, v); break;
        case 2: nv = b.CreateOr(old, v); break;
        case 3: nv = b.CreateXor(old, v); break;
        case 4: nv = b.CreateSelect(b.CreateICmpSLT(v, old), v, old); break;   /* IMin */
        case 5: nv = b.CreateSelect(b.CreateICmpSGT(v, old), v, old); break;   /* IMax */
        case 6: nv = b.CreateSelect(b.CreateICmpULT(v, old), v, old); break;   /* UMin */
        case 7: nv = b.CreateSelect(b.CreateICmpUGT(v, old), v, old); break;   /* UMax */
        default: nv = v; break;                                                 /* Exchange */
        }
        Value *nlo = b.CreateTrunc(nv, i32), *nhi = b.CreateTrunc(b.CreateLShr(nv, 32), i32);
        b.CreateCall(store, {ConstantInt::get(i32, kOpTextureStore), h, x, y, z, nlo, nhi, nlo, nlo, ConstantInt::get(i8, 3)});
        ci->replaceAllUsesWith(old);
        ci->eraseFromParent();
        done++;
    }

    /* Handle properties (SM 6.6): {Texture2D | UAV, U32 x1} -> {Texture2D | UAV | ROV, U32 x2}. */
    for (Function &f : m) for (BasicBlock &bb : f) for (Instruction &in : bb) {
        auto *ci = dyn_cast<CallInst>(&in);
        if (!ci || !is_dx_op(ci, kOpAnnotateHandle) || ci->arg_size() < 3) continue;
        uint32_t sp = 0, lo = 0;
        if (!a64_binding(m, ci, &sp, &lo) || !wanted(sp, lo)) continue;
        auto *cs = dyn_cast<ConstantStruct>(ci->getArgOperand(2));
        if (!cs || cs->getNumOperands() != 2) { say(note, "the texture at register %su%u has handle properties BCD cannot read", "", lo); return -1; }
        bool ok = true;
        uint64_t w0 = cint(cs->getOperand(0), &ok), w1 = cint(cs->getOperand(1), &ok);
        if (!ok || (w0 & 0xff) != 2 || !(w0 & 0x1000) || (w1 & 0xff) != 5) { say(note, "the texture at register %su%u is not a typed U32 2D UAV", "", lo); return -1; }
        ci->setArgOperand(2, ConstantStruct::get(cs->getType(),
            {ConstantInt::get(i32, w0 | 0x2000), ConstantInt::get(i32, (w1 & ~0xff00ull) | (2u << 8))}));
    }

    /* The resource list: ROV of uint2, element U32, no Atomic64Use tag. */
    const char *rov_name = "class.RasterizerOrderedTexture2D<vector<unsigned int, 2> >";
    StructType *rov_ty = StructType::getTypeByName(ctx, rov_name);
    if (!rov_ty) rov_ty = StructType::create(ctx, {FixedVectorType::get(i32, 2)}, rov_name);
    std::vector<Metadata *> kept;
    for (const MDOperand &op : uavs->operands()) {
        auto *e = dyn_cast_or_null<MDTuple>(op.get());
        if (!e) continue;
        bool ok = true;
        uint64_t sp = e->getNumOperands() >= 11 ? md_int(e->getOperand(3), &ok) : ~0ull, lo = e->getNumOperands() >= 11 ? md_int(e->getOperand(4), &ok) : ~0ull;
        if (!ok || !wanted((uint32_t)sp, (uint32_t)lo)) { kept.push_back(e); continue; }
        std::vector<Metadata *> f(e->op_begin(), e->op_end());
        f[1] = ConstantAsMetadata::get(UndefValue::get(PointerType::get(rov_ty, 0)));
        f[9] = ConstantAsMetadata::get(ConstantInt::getTrue(ctx));
        std::vector<Metadata *> props;
        bool have_elem = false;
        if (f[10]) {
            auto *p = cast<MDTuple>(f[10]);
            for (unsigned i = 0; i + 1 < p->getNumOperands(); i += 2) {
                bool ok2 = true;
                uint64_t tag = md_int(p->getOperand(i), &ok2);
                if (ok2 && tag == 3) continue;   /* Atomic64Use */
                if (ok2 && tag == 0) {
                    have_elem = true;
                    props.push_back(p->getOperand(i));
                    props.push_back(ConstantAsMetadata::get(ConstantInt::get(i32, 5)));   /* U32 */
                    continue;
                }
                props.push_back(p->getOperand(i)); props.push_back(p->getOperand(i + 1));
            }
        }
        if (!have_elem) {
            props.insert(props.begin(), ConstantAsMetadata::get(ConstantInt::get(i32, 5)));
            props.insert(props.begin(), ConstantAsMetadata::get(ConstantInt::get(i32, 0)));
        }
        f[10] = MDTuple::get(ctx, props);
        kept.push_back(MDTuple::get(ctx, f));
        (*n_res)++;
    }

    /* Any 64-bit atomic left (on other textures, its result unread) keeps the module's 64-bit atomic flag. */
    bool left = false;
    for (Function &f : m) for (BasicBlock &bb : f) for (Instruction &in : bb) {
        auto *ci = dyn_cast<CallInst>(&in);
        left |= ci && is_dx_op(ci, kOpAtomicBinOp) && ci->getType()->isIntegerTy(64);
    }
    *any_left = left;
    {
        NamedMDNode *resn = m.getNamedMetadata("dx.resources");
        auto *top = cast<MDTuple>(resn->getOperand(0));
        std::vector<Metadata *> t(top->op_begin(), top->op_end());
        t[1] = MDTuple::get(ctx, kept);
        MDTuple *ntop = MDTuple::get(ctx, t);
        resn->setOperand(0, ntop);
        if (NamedMDNode *eps = m.getNamedMetadata("dx.entryPoints")) {
            for (unsigned i = 0; i < eps->getNumOperands(); i++) {
                auto *ep = cast<MDTuple>(eps->getOperand(i));
                std::vector<Metadata *> e(ep->op_begin(), ep->op_end());
                if (e.size() < 5) e.resize(5, nullptr);
                if (e[3]) e[3] = ntop;
                std::vector<Metadata *> props;
                bool have_flags = false;
                uint64_t add = kFlagROVs | kFlagUAVLoadAdditional;
                if (e[4]) {
                    auto *p = cast<MDTuple>(e[4]);
                    for (unsigned k = 0; k + 1 < p->getNumOperands(); k += 2) {
                        bool ok = true;
                        uint64_t tag = md_int(p->getOperand(k), &ok);
                        Metadata *val = p->getOperand(k + 1);
                        if (ok && tag == 0) {
                            bool ok2 = true;
                            uint64_t fl = md_int(p->getOperand(k + 1), &ok2) | add;
                            if (!left) fl &= ~kFlagAtomicInt64Typed;
                            val = ConstantAsMetadata::get(ConstantInt::get(i64, fl));
                            have_flags = true;
                        }
                        props.push_back(p->getOperand(k)); props.push_back(val);
                    }
                }
                if (!have_flags) {
                    props.insert(props.begin(), ConstantAsMetadata::get(ConstantInt::get(i64, add)));
                    props.insert(props.begin(), ConstantAsMetadata::get(ConstantInt::get(i32, 0)));
                }
                e[4] = MDTuple::get(ctx, props);
                eps->setOperand(i, MDTuple::get(ctx, e));
            }
        }
    }
    {
        std::vector<Function *> unused;
        for (Function &f : m) if (f.isDeclaration() && f.use_empty() && f.getName().startswith("dx.op.")) unused.push_back(&f);
        for (Function *f : unused) f->eraseFromParent();
    }
    *n_atomics = done;
    return done ? 1 : 0;
}

/* madeira-bcd (build 648): dxil-loop-guard. A loop that never ends hangs the GPU: Metal ends the command buffer with "Caused GPU
 * Hang Error", the command buffers queued behind it are discarded, and whatever they were to compute never comes. The Last of Us
 * Part I (the owner's starts of 2026-11-09 11:25, 13:15 and 13:39; the last two with heap-backing 0 and 1) hangs every time a new
 * game loads in CS_ComputeVolProbeBufferWriteCache, which walks a k-d tree of light probes with a stack: one node popped, at most
 * two children pushed (left = node + 1, right = the node's own index, 0x1FFFFFFF for none). A sound tree visits each node once,
 * and the node indices are 16-bit, so no loop of it runs 65536 times; a tree whose data reads wrong (a child pointing back) never
 * ends. For the shaders named by their bytecode hash (the FNV-1a 64 [dxil-dump] prints), every loop whose back edges are
 * conditional branches that otherwise leave the loop gets a counter: past `cap` iterations the back edge leaves instead. A loop
 * that ends on its own never sees the difference; one that does not end stops instead of hanging the GPU. Loops of any other
 * shape are left as they were (counted in the note). */
static int rewrite_loop_guard_module(Module &m, uint32_t cap, unsigned *n_loops, unsigned *n_left)
{
    LLVMContext &ctx = m.getContext();
    Type *i32 = Type::getInt32Ty(ctx);
    for (Function &f : m) {
        if (f.isDeclaration()) continue;
        DominatorTree dt(f);
        std::vector<BasicBlock *> heads;
        std::vector<std::vector<BasicBlock *>> latches;
        for (BasicBlock &b : f)
            for (BasicBlock *s : successors(&b)) {
                if (!dt.dominates(s, &b)) continue;   /* a back edge: its target dominates its source */
                size_t k = 0;
                while (k < heads.size() && heads[k] != s) k++;
                if (k == heads.size()) { heads.push_back(s); latches.emplace_back(); }
                bool have = false;
                for (BasicBlock *l : latches[k]) have |= l == &b;
                if (!have) latches[k].push_back(&b);
            }
        for (size_t k = 0; k < heads.size(); k++) {
            BasicBlock *h = heads[k];
            /* the loop's body: what reaches a latch without passing the header */
            std::vector<BasicBlock *> body{h}, work(latches[k].begin(), latches[k].end());
            auto in_body = [&](BasicBlock *x) { for (BasicBlock *y : body) if (y == x) return true; return false; };
            while (!work.empty()) {
                BasicBlock *x = work.back(); work.pop_back();
                if (in_body(x)) continue;
                body.push_back(x);
                for (BasicBlock *p : predecessors(x)) work.push_back(p);
            }
            bool fit = true;
            for (BasicBlock *l : latches[k]) {
                auto *br = dyn_cast<BranchInst>(l->getTerminator());
                if (!br || !br->isConditional() || br->getSuccessor(0) == br->getSuccessor(1)) { fit = false; break; }
                BasicBlock *other = br->getSuccessor(0) == h ? br->getSuccessor(1) : br->getSuccessor(0);
                if (in_body(other)) { fit = false; break; }   /* the other way stays in the loop: no exit to take */
            }
            if (!fit) { (*n_left)++; continue; }
            PHINode *cnt = PHINode::Create(i32, 2, "bcd.lg", &h->front());
            std::vector<std::pair<BasicBlock *, Value *>> next;
            for (BasicBlock *l : latches[k]) {
                auto *br = cast<BranchInst>(l->getTerminator());
                IRBuilder<> b(br);
                Value *nx = b.CreateAdd(cnt, ConstantInt::get(i32, 1), "bcd.lg.next");
                Value *more = b.CreateICmpULT(nx, ConstantInt::get(i32, cap), "bcd.lg.more");
                Value *c = br->getCondition();
                br->setCondition(br->getSuccessor(0) == h ? b.CreateAnd(c, more) : b.CreateOr(c, b.CreateNot(more)));
                next.push_back({l, nx});
            }
            for (BasicBlock *p : predecessors(h)) {
                Value *v = ConstantInt::get(i32, 0);
                for (auto &e : next) if (e.first == p) v = e.second;
                cnt->addIncoming(v, p);
            }
            (*n_loops)++;
        }
    }
    return *n_loops ? 1 : 0;
}

/* A container with its DXIL part's bitcode replaced; the hash and the debug and statistics copies of the old bitcode dropped. */
static uint8_t *container_with_bitcode(const uint8_t *d, size_t len, const SmallVector<char, 0> &nb, size_t *out_len)
{
    uint32_t nparts = rd32(d + 28);
    std::vector<std::pair<uint32_t, std::vector<uint8_t>>> parts;
    for (uint32_t i = 0; i < nparts; i++) {
        uint32_t off = rd32(d + 32 + 4 * i), sz = rd32(d + off + 4);
        uint32_t cc; memcpy(&cc, d + off, 4);
        if (!memcmp(&cc, "HASH", 4) || !memcmp(&cc, "ILDB", 4) || !memcmp(&cc, "STAT", 4)) continue;
        std::vector<uint8_t> p(d + off + 8, d + off + 8 + sz);
        if (!memcmp(&cc, "DXIL", 4)) {
            std::vector<uint8_t> q(24 + nb.size());
            memcpy(q.data(), p.data(), 16);
            wr32(q.data() + 4, (uint32_t)(q.size() / 4));
            wr32(q.data() + 16, 16);
            wr32(q.data() + 20, (uint32_t)nb.size());
            memcpy(q.data() + 24, nb.data(), nb.size());
            p.swap(q);
        }
        parts.push_back({cc, std::move(p)});
    }
    size_t total = 32 + 4 * parts.size();
    for (auto &p : parts) total += 8 + p.second.size();
    uint8_t *o = (uint8_t *)malloc(total);
    if (!o) return nullptr;
    memcpy(o, d, 24);
    memset(o + 4, 0, 16);
    wr32(o + 24, (uint32_t)total);
    wr32(o + 28, (uint32_t)parts.size());
    size_t w = 32 + 4 * parts.size();
    for (size_t i = 0; i < parts.size(); i++) {
        wr32(o + 32 + 4 * i, (uint32_t)w);
        memcpy(o + w, &parts[i].first, 4);
        wr32(o + w + 4, (uint32_t)parts[i].second.size());
        memcpy(o + w + 8, parts[i].second.data(), parts[i].second.size());
        w += 8 + parts[i].second.size();
    }
    (void)len;
    *out_len = total;
    return o;
}

}  // namespace

/* madeira-bcd (build 648): see rewrite_loop_guard_module. 1 and a malloc'd container when a loop got its counter, 0 when none fits,
 * -1 when the container or its bitcode cannot be read (note says why; the caller keeps what it had). */
extern "C" int madeira_loop_guard_rewrite(const void *bc, size_t len, uint32_t cap, void **out, size_t *out_len,
                                          char *note_buf, size_t note_cap)
{
    note_t note{note_buf, note_cap};
    const uint8_t *d = (const uint8_t *)bc;
    *out = nullptr; *out_len = 0;
    if (note_buf && note_cap) note_buf[0] = 0;
    if (len < 32 || memcmp(d, "DXBC", 4)) { say(note, "not a DXBC container%s", ""); return -1; }
    uint32_t nparts = rd32(d + 28);
    if (32 + 4ull * nparts > len) { say(note, "container part table runs past its end%s", ""); return -1; }
    const uint8_t *dxil = nullptr; uint32_t dxil_size = 0;
    for (uint32_t i = 0; i < nparts; i++) {
        uint32_t off = rd32(d + 32 + 4 * i);
        if (off + 8ull > len) return -1;
        uint32_t sz = rd32(d + off + 4);
        if (off + 8ull + sz > len) return -1;
        if (!memcmp(d + off, "DXIL", 4)) { dxil = d + off + 8; dxil_size = sz; }
    }
    if (!dxil || dxil_size < 24) { say(note, "no DXIL part%s", ""); return -1; }
    uint32_t bc_off = rd32(dxil + 16), bc_size = rd32(dxil + 20);
    if (8ull + bc_off + bc_size > dxil_size) { say(note, "DXIL part's bitcode runs past its end%s", ""); return -1; }
    LLVMContext ctx;
    ctx.setOpaquePointers(false);
    auto mb = MemoryBuffer::getMemBuffer(StringRef((const char *)dxil + 8 + bc_off, bc_size), "dxil", false);
    auto mod = parseBitcodeFile(mb->getMemBufferRef(), ctx);
    if (!mod) {
        std::string err = toString(mod.takeError());
        say(note, "bitcode reader: %s", err.c_str());
        return -1;
    }
    unsigned n = 0, left = 0;
    if (!rewrite_loop_guard_module(**mod, cap, &n, &left)) {
        if (note_buf && note_cap) snprintf(note_buf, note_cap, "no loop of a shape that can be capped (%u left as they were)", left);
        return 0;
    }
    SmallVector<char, 0> nb;
    { raw_svector_ostream os(nb); WriteBitcodeToFile(**mod, os); }
    while (nb.size() & 3) nb.push_back(0);
    uint8_t *o = container_with_bitcode(d, len, nb, out_len);
    if (!o) return -1;
    *out = o;
    if (note_buf && note_cap)
        snprintf(note_buf, note_cap, "%u loop(s) capped at %u iterations, %u left as they were", n, cap, left);
    return 1;
}

extern "C" int madeira_ags_rewrite(const void *bc, size_t len, void **out, size_t *out_len,
                                   char *note_buf, size_t note_cap)
{
    note_t note{note_buf, note_cap};
    const uint8_t *d = (const uint8_t *)bc;
    *out = nullptr; *out_len = 0;
    if (note_buf && note_cap) note_buf[0] = 0;
    if (len < 32 || memcmp(d, "DXBC", 4)) return 0;
    uint32_t nparts = rd32(d + 28);
    if (32 + 4ull * nparts > len) return 0;

    /* Cheap reject: the magic space never appears in the bytes of a shader that
     * does not declare it (the bitcode stores it as a plain 32-bit literal in a
     * VBR, so scan PSV0's resource table instead: space is a raw uint32 there). */
    const uint8_t *dxil = nullptr; uint32_t dxil_size = 0;
    bool magic = false, a64 = false;
    for (uint32_t i = 0; i < nparts; i++) {
        uint32_t off = rd32(d + 32 + 4 * i);
        if (off + 8 > len) return 0;
        uint32_t sz = rd32(d + off + 4);
        if (off + 8ull + sz > len) return 0;
        if (!memcmp(d + off, "DXIL", 4)) { dxil = d + off + 8; dxil_size = sz; }
        if (!memcmp(d + off, "PSV0", 4)) {
            for (uint32_t k = 0; k + 4 <= sz; k += 4) magic |= rd32(d + off + 8 + k) == kAgsSpace;
            /* madeira-bcd (build 647): a pixel shader (runtime info 1's stage byte, 0) with a typed UAV marked
             * UsedByAtomic64 (bind info flag 1) is a candidate for rewrite_a64rov_module. */
            const uint8_t *q = d + off + 8;
            uint32_t ri = sz >= 4 ? rd32(q) : 0;
            if (ri >= 25 && 4ull + ri + 8 <= sz && q[4 + 24] == 0) {
                uint32_t cnt = rd32(q + 4 + ri), bsz = rd32(q + 4 + ri + 4);
                if (bsz >= 24 && 4ull + ri + 8 + (uint64_t)cnt * bsz <= sz)
                    for (uint32_t k = 0; k < cnt; k++) {
                        const uint8_t *b = q + 4 + ri + 8 + (size_t)k * bsz;
                        a64 |= rd32(b) == 6u && (rd32(b + 20) & 1u);   /* UAVTyped, UsedByAtomic64 */
                    }
            }
        }
    }
    t2r_target t2r{};
    bool want_t2r = !magic && match_t2r_target(d, len, &t2r);
    bool want_a64 = !magic && !want_t2r && a64;   /* bcd.cfg ags-rewrite = 0 turns this off with the rest */
    if (getenv("MADEIRA_AGS_ROUNDTRIP_ONLY")) magic = true;
    if (!dxil || (!magic && !want_t2r && !want_a64) || dxil_size < 24) return 0;
    uint32_t bc_off = rd32(dxil + 16), bc_size = rd32(dxil + 20);
    if (8ull + bc_off + bc_size > dxil_size) return 0;

    LLVMContext ctx;
    ctx.setOpaquePointers(false);   /* DXIL is typed-pointer IR; keep it that way on the way out */
    auto mb = MemoryBuffer::getMemBuffer(StringRef((const char *)dxil + 8 + bc_off, bc_size), "dxil", false);
    auto mod = parseBitcodeFile(mb->getMemBufferRef(), ctx);
    if (!mod) {
        std::string err = toString(mod.takeError());
        say(note, "bitcode reader: %s", err.c_str());
        return -1;
    }
    unsigned n = 0, n_used = 0, t2r_h = 0, t2r_l = 0, t2r_s = 0, a64_n = 0, a64_r = 0;
    bool a64_left = false;
    int rc = getenv("MADEIRA_AGS_ROUNDTRIP_ONLY") ? 1
           : want_t2r ? rewrite_t2r_module(**mod, t2r, note, &t2r_h, &t2r_l, &t2r_s)
           : want_a64 ? rewrite_a64rov_module(**mod, note, &a64_n, &a64_r, &a64_left)
                      : rewrite_module(**mod, note, &n, &n_used);   /* offline control */
    if (rc <= 0) return rc;

    SmallVector<char, 0> nb;
    { raw_svector_ostream os(nb); WriteBitcodeToFile(**mod, os); }
    while (nb.size() & 3) nb.push_back(0);

    /* Rebuild the container: the new DXIL part, SFI0 with the 64-bit atomic
     * feature bits, PSV0 with the magic resource dropped from its bind table,
     * everything else as it was except HASH (stale) and the debug/statistics
     * copies of the old bitcode (ILDB, STAT). */
    std::vector<std::pair<uint32_t, std::vector<uint8_t>>> parts;
    for (uint32_t i = 0; i < nparts; i++) {
        uint32_t off = rd32(d + 32 + 4 * i), sz = rd32(d + off + 4);
        uint32_t cc; memcpy(&cc, d + off, 4);
        std::vector<uint8_t> p(d + off + 8, d + off + 8 + sz);
        if (!memcmp(&cc, "HASH", 4) || !memcmp(&cc, "ILDB", 4) || !memcmp(&cc, "STAT", 4)) continue;
        if (!memcmp(&cc, "DXIL", 4)) {
            std::vector<uint8_t> q(24 + nb.size());
            memcpy(q.data(), p.data(), 16);                         /* program version, size, 'DXIL', dxil version */
            wr32(q.data() + 4, (uint32_t)(q.size() / 4));
            wr32(q.data() + 16, 16);
            wr32(q.data() + 20, (uint32_t)nb.size());
            memcpy(q.data() + 24, nb.data(), nb.size());
            p.swap(q);
        } else if (!memcmp(&cc, "SFI0", 4) && p.size() >= 8) {
            uint64_t fl; memcpy(&fl, p.data(), 8);
            if (want_a64) {
                fl |= kSfi0ROVs | kSfi0TypedUAVLoadAdditional;
                if (!a64_left) fl &= ~kSfi0AtomicInt64Typed;
            } else fl |= want_t2r ? kSfi0RawBuffers : (kSfi0Int64Ops | kSfi0AtomicInt64Typed);
            memcpy(p.data(), &fl, 8);
        } else if (!memcmp(&cc, "PSV0", 4) && p.size() >= 4) {
            /* PSV0: u32 runtime-info size, runtime info, u32 resource count,
             * [u32 bind-info size], bind infos {type, space, lower, upper[, kind, flags]}. */
            uint32_t ri = rd32(p.data());
            size_t at = 4 + ri;
            if (at + 4 <= p.size()) {
                uint32_t cnt = rd32(p.data() + at);
                if (cnt && at + 8 <= p.size()) {
                    uint32_t bsz = rd32(p.data() + at + 4);
                    size_t base = at + 8;
                    if (bsz >= 16 && base + (size_t)cnt * bsz <= p.size()) {
                        for (uint32_t k = 0; k < cnt; k++) {
                            uint8_t *b = p.data() + base + (size_t)k * bsz;
                            if (want_a64) {
                                /* the rewritten textures (all of them when no 64-bit atomic is left) no longer
                                 * take a 64-bit atomic: drop UsedByAtomic64 */
                                if (!a64_left && bsz >= 24 && rd32(b) == 6u) wr32(b + 20, rd32(b + 20) & ~1u);
                                continue;
                            }
                            if (want_t2r) {
                                if (rd32(b + 4) == t2r.space && rd32(b + 8) == t2r.reg && rd32(b + 12) == t2r.reg &&
                                    rd32(b + 0) == (t2r.uav ? 6u : 3u)) {
                                    wr32(b + 0, t2r.uav ? 7u : 4u);   /* SRVTyped(3)->SRVRaw(4), UAVTyped(6)->UAVRaw(7) */
                                    if (bsz >= 20 && rd32(b + 16) == 10u) wr32(b + 16, 11u);   /* TypedBuffer(10)->RawBuffer(11) */
                                }
                            } else if (rd32(b + 4) == kAgsSpace) {
                                memmove(b, b + bsz, p.size() - (size_t)(b + bsz - p.data()));
                                p.resize(p.size() - bsz);
                                wr32(p.data() + at, cnt - 1);
                                break;
                            }
                        }
                    }
                }
            }
        }
        parts.push_back({cc, std::move(p)});
    }
    size_t total = 32 + 4 * parts.size();
    for (auto &p : parts) total += 8 + p.second.size();
    uint8_t *o = (uint8_t *)malloc(total);
    if (!o) return -1;
    memcpy(o, d, 24);
    memset(o + 4, 0, 16);                     /* digest: the converter does not check it */
    wr32(o + 24, (uint32_t)total);
    wr32(o + 28, (uint32_t)parts.size());
    size_t w = 32 + 4 * parts.size();
    for (size_t i = 0; i < parts.size(); i++) {
        wr32(o + 32 + 4 * i, (uint32_t)w);
        memcpy(o + w, &parts[i].first, 4);
        wr32(o + w + 4, (uint32_t)parts[i].second.size());
        memcpy(o + w + 8, parts[i].second.data(), parts[i].second.size());
        w += 8 + parts[i].second.size();
    }
    *out = o; *out_len = total;
    if (want_a64)
        snprintf(note_buf, note_cap, "rewrote %u 64-bit texture atomic(s) whose result is read into ordered load/store on %u "
                 "rasterizer-ordered texture(s) (Metal's raster order groups)%s", a64_n, a64_r,
                 a64_left ? "; other 64-bit atomics stay" : "");
    else if (want_t2r)
        snprintf(note_buf, note_cap, "rewrote TypedBuffer<U32> %s space %u reg %u -> RawBuffer (%u handle(s), %u load(s), %u store(s))",
                 t2r.uav ? "UAV" : "SRV", t2r.space, t2r.reg, t2r_h, t2r_l, t2r_s);
    else if (n_used)
        snprintf(note_buf, note_cap, "rewrote %u AGS 64-bit atomic(s); %u read the old value (Metal cannot return it)", n, n_used);
    else
        say(note, "rewrote %s%u AGS 64-bit atomic(s)", "", n);
    return 1;
}

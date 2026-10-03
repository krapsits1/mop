#!/usr/bin/env python3
"""
check_asm.py - static checker for ARM (XScale / ARMv5TE) GNU assembler files.

Catches the most common mistakes:
  * callee-saved registers r4-r11 modified without being saved (STMFD/PUSH)
  * LR not saved in a function that calls other functions (BL/BLX)
  * push/pop lists that do not match, SUB SP without matching ADD SP
  * stack not 8-byte aligned at calls into C (AAPCS requirement)
  * r1-r3/r12 used after a BL (they are scratch: the callee may destroy them)
  * immediates that cannot be encoded in MOV/ADD/CMP/... (8-bit rotated)
  * instructions that do not exist on XScale (MOVW, MOVT, UBFX, UDIV, ...)
  * multiplication instructions when a task forbids them (--no-mul)
  * MUL Rd, Rm, Rs with Rd == Rm (unpredictable before ARMv6)
  * .word / code after .byte/.ascii/.short without .align
  * required global symbols missing (--expect)

Usage:
  check_asm.py file.s [more.s] [--no-mul] [--expect a,b,c] [-q]

Exit status: 1 if any ERROR was reported, else 0.
The checks are heuristics (no full control-flow analysis): read every WARN,
but do not blindly "fix" code a warning points at - understand it first.
"""
import argparse
import re
import sys

COND = ["eq", "ne", "cs", "hs", "cc", "lo", "mi", "pl", "vs", "vc",
        "hi", "ls", "ge", "lt", "gt", "le", "al"]
DP3 = ["and", "eor", "sub", "rsb", "add", "adc", "sbc", "rsc", "orr", "bic"]
DP_CMP = ["tst", "teq", "cmp", "cmn"]
DP_MOV = ["mov", "mvn"]
SHIFT_OPS = ["lsl", "lsr", "asr", "ror", "rrx"]          # UAL shift instructions
MUL_OPS = ["mul", "mla", "mls", "umull", "umlal", "smull", "smlal",
           "smulbb", "smulbt", "smultb", "smultt", "smulwb", "smulwt",
           "smlabb", "smlabt", "smlatb", "smlatt", "smlawb", "smlawt",
           "smlalbb", "smlalbt", "smlaltb", "smlaltt"]
NOT_ON_XSCALE = ["movw", "movt", "ubfx", "sbfx", "bfi", "bfc", "rbit", "rev",
                 "rev16", "revsh", "udiv", "sdiv", "cbz", "cbnz", "ldrex",
                 "strex", "uxtb", "uxth", "sxtb", "sxth", "uxtab", "uxtah",
                 "sxtab", "sxtah", "it", "ite", "itt", "dmb", "dsb", "isb",
                 "sev", "wfe", "wfi", "yield", "pkhbt", "pkhtb", "sel",
                 "usat", "ssat", "ldrexb", "strexb", "ldrexh", "strexh"]
LDST_SIZES = ["", "b", "h", "sb", "sh", "d", "t", "bt"]
LDM_MODES = ["", "ia", "ib", "da", "db", "fd", "ed", "fa", "ea"]

REGMAP = {f"r{i}": i for i in range(16)}
REGMAP.update({"sp": 13, "lr": 14, "pc": 15, "fp": 11, "ip": 12, "sl": 10,
               "sb": 9, "a1": 0, "a2": 1, "a3": 2, "a4": 3, "v1": 4, "v2": 5,
               "v3": 6, "v4": 7, "v5": 8, "v6": 9, "v7": 10, "v8": 11})
REG_RE = re.compile(r"\b(r1[0-5]|r[0-9]|sp|lr|pc|fp|ip|sl|sb|a[1-4]|v[1-8])\b", re.I)


def rname(n):
    return {13: "sp", 14: "lr", 15: "pc"}.get(n, f"r{n}")


class Msg:
    def __init__(self, path, line, level, text):
        self.path, self.line, self.level, self.text = path, line, level, text

    def __str__(self):
        return f"{self.path}:{self.line}: {self.level}: {self.text}"


# ---------------------------------------------------------------- parsing
def strip_comments(src):
    """Remove /* */ comments (keeping newlines), '@' comments, and lines whose
    leftmost character is '#'. Respects string literals."""
    out, i, n = [], 0, len(src)
    in_str = False
    while i < n:
        c = src[i]
        if in_str:
            out.append(c)
            if c == "\\" and i + 1 < n:
                out.append(src[i + 1]); i += 2; continue
            if c == '"':
                in_str = False
            i += 1; continue
        if c == '"':
            in_str = True; out.append(c); i += 1; continue
        if src.startswith("/*", i):
            j = src.find("*/", i + 2)
            j = n if j < 0 else j + 2
            out.append("\n" * src.count("\n", i, j)); i = j; continue
        if c == "@":
            j = src.find("\n", i)
            i = n if j < 0 else j; continue
        if c == "#" and (i == 0 or src[i - 1] == "\n"):
            j = src.find("\n", i)
            i = n if j < 0 else j; continue
        if c == "'" and i + 1 < n:            # character constant like #'a
            out.append(src[i:i + 2]); i += 2; continue
        out.append(c); i += 1
    return "".join(out)


def split_operands(s):
    """Split on top-level commas (not inside [] or {} or quotes)."""
    parts, depth, cur, in_str = [], 0, [], False
    for ch in s:
        if in_str:
            cur.append(ch)
            if ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        if ch in "[{(":
            depth += 1
        elif ch in "]})":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append("".join(cur).strip()); cur = []
        else:
            cur.append(ch)
    if "".join(cur).strip():
        parts.append("".join(cur).strip())
    return parts


def regs_in(s):
    return [REGMAP[m.lower()] for m in REG_RE.findall(s)]


def reglist(s):
    """Parse '{r4-r7, lr}' -> set of register numbers."""
    m = re.search(r"\{([^}]*)\}", s)
    if not m:
        return set()
    res = set()
    for part in m.group(1).split(","):
        part = part.strip().lower()
        if not part:
            continue
        if "-" in part:
            a, b = [p.strip() for p in part.split("-", 1)]
            if a in REGMAP and b in REGMAP:
                res.update(range(REGMAP[a], REGMAP[b] + 1))
        elif part in REGMAP:
            res.add(REGMAP[part])
    return res


def decode(mn):
    """Return (base, info) for a mnemonic, or (None, None) if unknown.
    Handles both pre-UAL (addnes, ldreqb, ldmeqfd) and UAL (addsne, ldrbeq)."""
    mn = mn.lower()

    def split_cond_s(rem, allow_s):
        for c in [""] + COND:
            for s in ([""] + (["s"] if allow_s else [])):
                if rem in (c + s, s + c):
                    return {"cond": c, "s": bool(s)}
        return None

    def split_cond_x(rem, xs):
        for c in [""] + COND:
            for x in xs:
                if rem in (c + x, x + c):
                    return {"cond": c, "x": x}
        return None

    if mn in NOT_ON_XSCALE:
        return mn, {"cond": "", "s": False}
    bases = sorted(DP3 + DP_CMP + DP_MOV + SHIFT_OPS + MUL_OPS +
                   ["clz", "mrs", "msr", "adr", "swp", "swi", "svc", "nop",
                    "push", "pop", "b", "bl", "bx", "blx", "ldr", "str",
                    "ldm", "stm", "pld", "mcr", "mrc", "cdp", "ldc", "stc"],
                   key=len, reverse=True)
    for b in bases:
        if not mn.startswith(b):
            continue
        rem = mn[len(b):]
        if b in ("ldr", "str"):
            r = split_cond_x(rem, LDST_SIZES)
        elif b in ("ldm", "stm"):
            r = split_cond_x(rem, LDM_MODES)
        elif b in DP3 + DP_MOV + SHIFT_OPS + MUL_OPS:
            r = split_cond_s(rem, True)
        elif b in DP_CMP:
            r = split_cond_s(rem, True)   # cmps/cmpp are tolerated by gas
        elif b == "swp":
            r = split_cond_x(rem, ["", "b"])
        else:
            r = split_cond_s(rem, False)
        if r is not None:
            return b, r
    return None, None


IMM_RE = re.compile(r"^#\s*(-?\s*(0x[0-9a-f]+|0b[01]+|\d+)|'.)\s*$", re.I)


def parse_imm(op):
    m = IMM_RE.match(op.strip())
    if not m:
        return None
    t = m.group(1).replace(" ", "")
    if t.startswith("'"):
        return ord(t[1])
    neg = t.startswith("-")
    t = t.lstrip("-")
    v = int(t, 0)
    return (-v if neg else v) & 0xFFFFFFFF


def encodable(v):
    v &= 0xFFFFFFFF
    for rot in range(0, 32, 2):
        x = ((v << rot) | (v >> (32 - rot))) & 0xFFFFFFFF if rot else v
        if x < 256:
            return True
    return False


# ---------------------------------------------------------------- statement model
class Stmt:
    def __init__(self, line, labels, op, operands):
        self.line, self.labels, self.op, self.operands = line, labels, op, operands
        self.base, self.info = (decode(op) if op and not op.startswith(".") else (None, None))
        self.reads, self.writes = set(), set()
        if self.base:
            self._rw()

    def _rw(self):
        b, ops = self.base, self.operands
        joined = ", ".join(ops)
        if b in DP3 or b in SHIFT_OPS:
            if ops:
                d = regs_in(ops[0])
                self.writes.update(d[:1])
                for o in ops[1:]:
                    self.reads.update(regs_in(o))
                if len(ops) == 2 and b in DP3:      # 'add r1, #1' == add r1, r1, #1
                    self.reads.update(d[:1])
        elif b in DP_MOV:
            if ops:
                self.writes.update(regs_in(ops[0])[:1])
                for o in ops[1:]:
                    self.reads.update(regs_in(o))
        elif b in DP_CMP or b in ("bx", "blx") and ops and regs_in(ops[0]):
            self.reads.update(regs_in(joined))
        elif b in ("mul", "mla", "mls", "clz", "mrs", "adr", "swp"):
            if ops:
                self.writes.update(regs_in(ops[0])[:1])
                for o in ops[1:]:
                    self.reads.update(regs_in(o))
        elif b in ("umull", "umlal", "smull", "smlal"):
            if len(ops) >= 2:
                self.writes.update(regs_in(ops[0])[:1] + regs_in(ops[1])[:1])
                for o in ops[2:]:
                    self.reads.update(regs_in(o))
                if b in ("umlal", "smlal"):
                    self.reads.update(regs_in(ops[0])[:1] + regs_in(ops[1])[:1])
        elif b in ("ldr", "str"):
            if ops:
                first = regs_in(ops[0])[:1]
                if self.info.get("x") == "d" and len(ops) > 1 and regs_in(ops[1]) and "[" not in ops[1]:
                    first += regs_in(ops[1])[:1]
                (self.writes if b == "ldr" else self.reads).update(first)
                mem = joined[joined.find("["):] if "[" in joined else ""
                memregs = regs_in(mem)
                self.reads.update(memregs)
                post_indexed = bool(re.search(r"\]\s*,", mem))
                if memregs and ("!" in mem or post_indexed):
                    self.writes.add(memregs[0])
        elif b in ("ldm", "stm"):
            if ops:
                basereg = regs_in(ops[0])[:1]
                self.reads.update(basereg)
                if "!" in ops[0]:
                    self.writes.update(basereg)
                lst = reglist(joined)
                (self.writes if b == "ldm" else self.reads).update(lst)
        elif b == "push":
            self.reads.update(reglist(joined)); self.reads.add(13); self.writes.add(13)
        elif b == "pop":
            self.writes.update(reglist(joined)); self.reads.add(13); self.writes.add(13)
        elif b == "msr":
            self.reads.update(regs_in(joined))

    # helpers
    def is_push(self):
        if self.base == "push":
            return True
        if self.base == "stm" and self.operands and regs_in(self.operands[0])[:1] == [13] \
                and "!" in self.operands[0] and self.info["x"] in ("fd", "db"):
            return True
        if self.base == "str" and self.operands and re.search(r"\[\s*sp\s*,\s*#\s*-4\s*\]\s*!", ", ".join(self.operands), re.I):
            return True
        return False

    def is_pop(self):
        if self.base == "pop":
            return True
        if self.base == "ldm" and self.operands and regs_in(self.operands[0])[:1] == [13] \
                and "!" in self.operands[0] and self.info["x"] in ("fd", "ia"):
            return True
        if self.base == "ldr" and self.operands and re.search(r"\[\s*sp\s*\]\s*,\s*#\s*4\b", ", ".join(self.operands), re.I):
            return True
        return False

    def pushed(self):
        if self.base in ("push", "pop", "ldm", "stm"):
            return reglist(", ".join(self.operands))
        return set(regs_in(self.operands[0])[:1]) if self.operands else set()

    def is_call(self):
        return self.base in ("bl", "blx")

    def is_return(self):
        j = ", ".join(self.operands).lower().replace(" ", "")
        if self.base == "bx" and j == "lr":
            return True
        if self.base == "mov" and j.startswith("pc,lr"):
            return True
        if (self.is_pop()) and 15 in self.writes:
            return True
        return False


def parse(path, text, msgs):
    clean = strip_comments(text)
    stmts = []
    label_re = re.compile(r"^\s*([A-Za-z_.$][\w.$]*|\d+)\s*:")
    for ln, raw in enumerate(clean.split("\n"), 1):
        if ";" in raw and '"' not in raw:
            msgs.append(Msg(path, ln, "WARN", "';' used as statement separator - "
                            "use one statement per line (';' starts a comment on other architectures)"))
        for piece in raw.split(";") if '"' not in raw else [raw]:
            labels = []
            while True:
                m = label_re.match(piece)
                if not m:
                    break
                labels.append(m.group(1))
                piece = piece[m.end():]
            piece = piece.strip()
            if not piece:
                if labels:
                    stmts.append(Stmt(ln, labels, None, []))
                continue
            parts = piece.split(None, 1)
            op = parts[0]
            # 'sym = expr' assignments
            if len(parts) > 1 and parts[1].lstrip().startswith("=") and not op.startswith("."):
                stmts.append(Stmt(ln, labels, ".set", [op, parts[1].lstrip()[1:].strip()]))
                continue
            operands = split_operands(parts[1]) if len(parts) > 1 else []
            stmts.append(Stmt(ln, labels, op, operands))
    return stmts


# ---------------------------------------------------------------- checks
def check_file(path, args):
    msgs = []
    try:
        text = open(path, encoding="utf-8", errors="replace").read()
    except OSError as e:
        return [Msg(path, 0, "ERROR", str(e))]
    stmts = parse(path, text, msgs)

    # ---- section / alignment / directives pass
    section = ".text"
    misalign = {}       # section -> (kind, line) ; kind in ('byte','half')
    globals_, types, labels_in_text = set(), set(), {}
    has_gnu_stack = False
    bl_targets = set()
    for st in stmts:
        op = (st.op or "").lower()
        for lab in st.labels:
            if section == ".text":
                labels_in_text.setdefault(lab, st.line)
        if op in (".text", ".data", ".bss"):
            section = op; continue
        if op == ".section":
            name = st.operands[0] if st.operands else ""
            if "GNU-stack" in name:
                has_gnu_stack = True
            section = name.split(",")[0].strip(); continue
        if op in (".global", ".globl"):
            for o in st.operands:
                globals_.add(o.strip())
            continue
        if op == ".type":
            if st.operands:
                types.add(st.operands[0].strip())
            continue
        if op in (".align", ".balign", ".p2align"):
            misalign.pop(section, None)
            if op == ".align" and st.operands:
                try:
                    n = int(st.operands[0], 0)
                    if n > 6:
                        msgs.append(Msg(path, st.line, "WARN", f".align {n} on ARM means 2^{n} bytes "
                                        f"({2**n}); use .balign for a byte count"))
                except ValueError:
                    pass
            continue
        if op == ".comm" and len(st.operands) >= 3:
            try:
                size, al = int(eval(st.operands[1], {}, {})), int(st.operands[2], 0)
                if size % 4 == 0 and al < 4:
                    msgs.append(Msg(path, st.line, "WARN", f".comm alignment is in BYTES (not a power of 2): "
                                    f"'{al}' gives {al}-byte alignment; use 4 for word data"))
            except Exception:
                pass
            continue
        if op in (".byte", ".ascii", ".asciz", ".string"):
            misalign[section] = ("byte", st.line); continue
        if op in (".short", ".hword", ".2byte"):
            if misalign.get(section, ("",))[0] == "byte":
                msgs.append(Msg(path, st.line, "WARN", f"{op} after byte data (line {misalign[section][1]}) "
                                "without .align 1 - halfwords must be 2-byte aligned"))
            misalign[section] = ("half", st.line); continue
        if op in (".word", ".long", ".int", ".4byte") or (st.base and section == ".text"):
            if section in misalign:
                kind, l0 = misalign[section]
                what = "instruction" if st.base else op
                msgs.append(Msg(path, st.line, "WARN", f"{what} after {kind} data (line {l0}) without "
                                ".align 2 - words and code must be 4-byte aligned (Data Abort / SIGBUS)"))
                misalign.pop(section, None)
            continue
        if st.base in ("bl", "blx") and st.operands and not regs_in(st.operands[0]):
            bl_targets.add(st.operands[0].strip())

    # ---- expected symbols
    for sym in args.expect:
        if sym not in globals_:
            msgs.append(Msg(path, 1, "ERROR", f"required function '{sym}' is not declared '.global {sym}' "
                            "- the linker/C caller will not find it"))
        if sym not in labels_in_text:
            msgs.append(Msg(path, 1, "ERROR", f"label '{sym}:' not found in .text"))
    if not has_gnu_stack:
        msgs.append(Msg(path, len(text.split("\n")), "INFO", "add  .section .note.GNU-stack,\"\",%progbits  at the end "
                        "to silence the 'missing .note.GNU-stack' linker warning on newer toolchains (harmless elsewhere)"))

    if "main" in labels_in_text and "main" not in globals_:
        msgs.append(Msg(path, labels_in_text["main"], "ERROR", "'main' is not declared '.global main' - "
                        "the C runtime cannot find it (linker error: undefined reference to main)"))

    # ---- split into functions
    func_names = {g for g in globals_ if g in labels_in_text} | {t for t in bl_targets if t in labels_in_text}
    if "main" in labels_in_text:
        func_names.add("main")
    funcs, cur = [], None
    section = ".text"
    for st in stmts:
        op = (st.op or "").lower()
        if op in (".text", ".data", ".bss", ".section"):
            section = op if op != ".section" else (st.operands[0] if st.operands else "")
            if section != ".text":
                cur = None
            continue
        if section != ".text":
            continue
        for lab in st.labels:
            if lab in func_names:
                cur = {"name": lab, "line": st.line, "stmts": []}
                funcs.append(cur)
        if cur is not None and op != ".size":
            cur["stmts"].append(st)
    for g in func_names:
        if g in globals_ and g not in types:
            msgs.append(Msg(path, labels_in_text[g], "INFO", f"consider '.type {g}, %function' "
                            "(+ '.size') so gdb/objdump treat it as a function"))

    for f in funcs:
        check_function(path, f, args, msgs)

    # ---- per-instruction checks
    for st in stmts:
        if not st.base:
            continue
        b, ops = st.base, st.operands
        if b in NOT_ON_XSCALE:
            msgs.append(Msg(path, st.line, "ERROR", f"'{st.op}' does not exist on XScale (ARMv5TE); "
                            "build with -mcpu=xscale"))
        if b in MUL_OPS and args.no_mul:
            msgs.append(Msg(path, st.line, "ERROR", f"multiplication instruction '{st.op}' is forbidden "
                            "by --no-mul (use shifts/adds instead)"))
        if b == "mul" and len(ops) >= 2 and regs_in(ops[0])[:1] == regs_in(ops[1])[:1]:
            msgs.append(Msg(path, st.line, "WARN", "MUL Rd, Rm, Rs with Rd == Rm is UNPREDICTABLE before "
                            "ARMv6 (XScale); swap the source operands: mul rd, rs, rm"))
        if b in DP3 + DP_MOV + DP_CMP and ops:
            last = ops[-1]
            v = parse_imm(last)
            if v is not None:
                alts = [v]
                if b in ("mov", "mvn", "and", "bic"):
                    alts.append(~v & 0xFFFFFFFF)
                if b in ("add", "sub", "cmp", "cmn", "adc", "sbc"):
                    alts.append(-v & 0xFFFFFFFF)
                if not any(encodable(a) for a in alts):
                    msgs.append(Msg(path, st.line, "ERROR", f"immediate {last.strip()} (0x{v:08x}) is not an "
                                    "8-bit value rotated by an even amount - use 'ldr rX, =value' "
                                    "(or build it with mov + orr / shifts)"))
        if b in ("ldr", "str") and st.info.get("x") in ("h", "sh", "sb", "d") and ops:
            mem = ", ".join(ops[1:])
            if re.search(r",\s*(lsl|lsr|asr|ror|rrx)", mem, re.I):
                msgs.append(Msg(path, st.line, "ERROR", f"{st.op}: halfword/signed/doubleword loads and "
                                "stores cannot use a shifted register offset"))
            for imm in re.findall(r"#\s*(-?\d+)", mem):
                if abs(int(imm)) > 255:
                    msgs.append(Msg(path, st.line, "ERROR", f"{st.op}: immediate offset {imm} out of range "
                                    "(-255..255 for LDRH/LDRSB/LDRSH/STRH)"))
        if b in DP3 + DP_MOV and ops and regs_in(ops[0])[:1] == [15] and st.info.get("s"):
            msgs.append(Msg(path, st.line, "WARN", "S-suffix with PC as destination copies SPSR to CPSR "
                            "(exception return) - almost certainly not intended"))
    return msgs


def check_function(path, f, args, msgs):
    name, sts = f["name"], f["stmts"]
    saved, pushes, pops = set(), [], []
    sub_sp, add_sp = 0, 0
    sp_from_other = False          # e.g. 'sub sp, fp, #4' (frame pointer epilogue, gcc -O0)
    SCRATCH = {0, 1, 2, 3}
    has_call, has_return = False, False
    written = {}
    for st in sts:
        if not st.base:
            continue
        if st.is_push():
            regs = st.pushed(); saved |= regs; pushes.append((st, regs - SCRATCH))
            sub_sp += 4 * len(regs & SCRATCH)       # pushing r0-r3 = reserving/saving locals
        elif st.is_pop():
            regs = st.pushed(); pops.append((st, regs - SCRATCH))
            add_sp += 4 * len(regs & SCRATCH)
        j = ", ".join(st.operands).lower().replace(" ", "")
        if 13 in st.writes and not (st.is_push() or st.is_pop()) and not re.match(r"sp,sp,#\d+$", j):
            sp_from_other = True
        m = re.match(r"sp,sp,#(\d+)$", j)
        if m and st.base == "sub":
            sub_sp += int(m.group(1))
        if m and st.base == "add":
            add_sp += int(m.group(1))
        if st.is_call():
            has_call = True
        if st.is_return():
            has_return = True
        for r in st.writes:
            written.setdefault(r, st.line)

    for r in range(4, 12):
        if r in written and r not in saved:
            msgs.append(Msg(path, written[r], "ERROR", f"{name}: callee-saved {rname(r)} is modified but "
                            f"never saved - add it to the STMFD/LDMFD lists (ARM EABI: r4-r11 must survive the call)"))
    if has_call and 14 not in saved:
        line = next(st.line for st in sts if st.base and st.is_call())
        msgs.append(Msg(path, line, "ERROR", f"{name}: calls another function (BL/BLX overwrites LR) but LR "
                        "is not saved - save it with 'stmfd sp!, {..., lr}' and return with 'ldmfd sp!, {..., pc}'"))
    if sts and not has_return and not any(st.base == "b" and not st.info.get("cond") and
                                           st.operands and st.operands[0].strip() not in
                                           {l for s in sts for l in s.labels} for st in sts if st.base):
        msgs.append(Msg(path, f["line"], "WARN", f"{name}: no return found (bx lr / mov pc, lr / ldmfd sp!, {{..., pc}})"))
    # push/pop symmetry
    if pushes:
        p_regs = set().union(*[r for _, r in pushes])
        for st, regs in pops:
            norm = {14 if r == 15 else r for r in regs}
            if norm != p_regs and not (len(pushes) > 1):
                msgs.append(Msg(path, st.line, "WARN", f"{name}: pops {{{', '.join(rname(r) for r in sorted(regs))}}} "
                                f"but pushed {{{', '.join(rname(r) for r in sorted(p_regs))}}} - lists must match "
                                "(LR may become PC)"))
    if sub_sp != add_sp and not sp_from_other:
        msgs.append(Msg(path, f["line"], "WARN", f"{name}: 'sub sp, sp, #{sub_sp}' total vs 'add sp, sp, #{add_sp}' "
                        "total differ - the stack pointer must be restored before returning"))
    if has_call and pushes:
        nbytes = 4 * len(pushes[0][1]) + sub_sp
        if nbytes % 8:
            msgs.append(Msg(path, pushes[0][0].line, "WARN", f"{name}: calls other functions but the frame is "
                            f"{nbytes} bytes - ARM EABI wants SP 8-byte aligned at calls (C library functions "
                            "like printf with long long/double can misbehave). Push an even number of registers."))
    # loads from [sp, #k] that hit the registers saved by the prologue (forgot that the
    # push moved the stack arguments). Frame = pushes / sub sp before the first branch or label.
    push_bytes, local_bytes = 0, 0
    for st in sts:
        if (st.labels and name not in st.labels) or st.base in ("b", "bl", "blx", "bx"):
            break
        if not st.base:
            continue
        if st.is_push():
            regs = st.pushed()
            push_bytes += 4 * len(regs - {0, 1, 2, 3})
            local_bytes = 4 * len(regs & {0, 1, 2, 3})
        j = ", ".join(st.operands).lower().replace(" ", "")
        m = re.match(r"sp,sp,#(\d+)$", j)
        if m and st.base == "sub":
            local_bytes += int(m.group(1))
    if push_bytes:
        for st in sts:
            if st.base == "ldr" and len(st.operands) == 2 and not st.is_pop():
                m = re.match(r"\[\s*sp\s*(?:,\s*#\s*(\d+))?\s*\]$", st.operands[1].strip(), re.I)
                if m:
                    k = int(m.group(1) or 0)
                    if local_bytes <= k < local_bytes + push_bytes:
                        msgs.append(Msg(path, st.line, "WARN", f"{name}: [sp, #{k}] reads a register saved by your "
                                        f"own prologue - stack arguments start at [sp, #{local_bytes + push_bytes}] "
                                        f"after pushing {push_bytes} bytes (5th argument), see abi_layout.py"))
    # scratch registers used after a call (straight-line heuristic)
    dead = set()
    for st in sts:
        if st.labels:
            dead = set()
        if not st.base:
            continue
        for r in sorted(st.reads & dead):
            msgs.append(Msg(path, st.line, "WARN", f"{name}: {rname(r)} is read after a call, but r1-r3 and r12 "
                            "are scratch registers the callee may destroy - keep the value in r4-r11 (saved) or on the stack "
                            "(reading r1 is fine only if the callee returns a 64-bit value in r0:r1)"))
            dead.discard(r)
        dead -= st.writes
        if st.is_call():
            tgt = st.operands[0].strip() if st.operands else ""
            dead = {2, 3, 12} if tgt.startswith("__aeabi_") else {1, 2, 3, 12}
        if st.base == "b" and not st.info.get("cond"):
            dead = set()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("files", nargs="+")
    ap.add_argument("--no-mul", action="store_true", help="forbid multiplication instructions")
    ap.add_argument("--expect", default="", help="comma-separated global functions that must exist")
    ap.add_argument("-q", "--quiet", action="store_true", help="hide INFO messages")
    args = ap.parse_args()
    exp = [e for e in args.expect.split(",") if e]
    args.expect = exp

    errors = 0
    for p in args.files:
        msgs = sorted(check_file(p, args), key=lambda m: (m.line, m.level))
        for m in msgs:
            if args.quiet and m.level == "INFO":
                continue
            print(m)
        e = sum(m.level == "ERROR" for m in msgs)
        w = sum(m.level == "WARN" for m in msgs)
        print(f"{p}: {e} error(s), {w} warning(s)")
        errors += e
    sys.exit(1 if errors else 0)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
abi_layout.py - where does each argument of a C function live on entry to the
assembly implementation (ARM EABI / AAPCS)?

  abi_layout.py "int f(int a, int b, int *p, int c, int d, int *q)"
  abi_layout.py "void g(int x1, int y1, int x2, int y2, int x3, int y3)" --push 10

Rules implemented (simplified AAPCS: integers, pointers, 64-bit values):
  * arguments go in r0, r1, r2, r3 in order; the rest go on the stack,
    the 5th argument at [sp, #0] on entry, then [sp, #4], ...
  * every argument <= 4 bytes (char, short, int, long, pointers, enums) uses a
    whole register / a whole 4-byte stack slot
  * 8-byte arguments (long long, double, uint64_t) use an EVEN register pair
    (r0:r1 or r2:r3, low word in the lower register - little endian) or an
    8-byte aligned stack slot; once one argument went to the stack, all later
    ones do too
  * return value: r0 (or r0:r1 for 8 bytes)

--push N  also shows the offsets after the prologue pushed N registers
          (e.g. stmfd sp!, {r4-r11, lr} pushes 9) and any 'sub sp, sp, #K' (--sub K).
"""
import argparse
import re
import sys

EIGHT = ("long long", "double", "int64_t", "uint64_t", "unsigned long long", "signed long long")


def parse_proto(proto):
    m = re.match(r"^\s*(.+?)\s*\b([A-Za-z_]\w*)\s*\((.*)\)\s*;?\s*$", proto.strip())
    if not m:
        sys.exit(f"cannot parse prototype: {proto!r}")
    ret, name, params = m.group(1), m.group(2), m.group(3).strip()
    out = []
    if params and params != "void":
        for i, p in enumerate([x.strip() for x in params.split(",")]):
            pm = re.match(r"^(.*?)([A-Za-z_]\w*)\s*$", p)
            if pm and pm.group(1).strip() and not pm.group(1).strip().endswith(("struct", "unsigned", "signed")) \
                    and pm.group(2) not in ("int", "char", "short", "long", "float", "double"):
                typ, pname = pm.group(1).strip(), pm.group(2)
            else:
                typ, pname = p, f"arg{i + 1}"
            out.append((typ, pname))
    return ret.strip(), name, out


def size_of(typ):
    t = " ".join(typ.replace("*", " * ").split())
    if "*" in t:
        return 4
    if any(t.endswith(e) or t == e for e in EIGHT):
        return 8
    if t.startswith(("struct", "union")):
        return None   # aggregate passed by value - not handled, pass a pointer
    return 4


def layout(params):
    ncrn, nsaa, res = 0, 0, []
    for typ, name in params:
        sz = size_of(typ)
        if sz is None:
            res.append((typ, name, "?? (struct/unknown type passed by value - pass a pointer instead)"))
            continue
        if sz == 8:
            if ncrn % 2:
                ncrn += 1
            if ncrn + 2 <= 4:
                res.append((typ, name, ("reg", ncrn, 2))); ncrn += 2; continue
            ncrn = 4
            nsaa = (nsaa + 7) & ~7
            res.append((typ, name, ("stack", nsaa, 8))); nsaa += 8; continue
        if ncrn < 4:
            res.append((typ, name, ("reg", ncrn, 1))); ncrn += 1
        else:
            res.append((typ, name, ("stack", nsaa, 4))); nsaa += 4
    return res, nsaa


def describe(proto, push=0, sub=0):
    ret, name, params = parse_proto(proto)
    res, stack_bytes = layout(params)
    lines = [f"{name}: {len(params)} argument(s); return value ({ret}) in "
             f"{'r0:r1 (low word in r0)' if size_of(ret) == 8 else ('nothing' if ret == 'void' else 'r0')}"]
    extra = push * 4 + sub
    for typ, pname, loc in res:
        if isinstance(loc, str):
            lines.append(f"  {pname:<10} {typ:<18} {loc}")
        elif loc[0] == "reg":
            r = f"r{loc[1]}" if loc[2] == 1 else f"r{loc[1]}:r{loc[1] + 1} (low word in r{loc[1]})"
            lines.append(f"  {pname:<10} {typ:<18} {r}")
        else:
            s = f"[sp, #{loc[1]}] on entry"
            if extra:
                s += f"  ->  [sp, #{loc[1] + extra}] after pushing {push} reg(s)" + (f" + sub sp,#{sub}" if sub else "")
            if loc[2] == 8:
                s += "  (8 bytes: low word first)"
            lines.append(f"  {pname:<10} {typ:<18} {s}")
    if stack_bytes:
        lines.append(f"  caller reserves {stack_bytes} byte(s) of stack arguments "
                     f"(sub sp, sp, #{(stack_bytes + 7) & ~7} keeps 8-byte alignment) and removes them after the call")
    if push:
        tot = push * 4 + sub
        lines.append(f"  frame after prologue: {tot} bytes -> SP is {'8-byte aligned' if tot % 8 == 0 else 'NOT 8-byte aligned (fix before calling C functions)'}")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("prototype", nargs="+")
    ap.add_argument("--push", type=int, default=0, help="number of registers pushed by the prologue")
    ap.add_argument("--sub", type=int, default=0, help="bytes reserved by 'sub sp, sp, #K' after the push")
    a = ap.parse_args()
    for p in a.prototype:
        print(describe(p, a.push, a.sub))
        print()


if __name__ == "__main__":
    main()

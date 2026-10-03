#!/usr/bin/env python3
"""
trace.py - run an ARM assembly snippet on the real toolchain (arm-linux-gnueabi-gcc
+ qemu-arm) and print how registers and NZCV flags change after every executed line.

Use it to check "what is in the registers after this code?" answers worked out
by hand, or to watch a buggy routine step by step.

Accepted input
  * a bare snippet (just instructions and labels), or
  * a whole program with  main:  (it is renamed internally).

Defaults: r0-r12 start at 0 and NZCV = 0000;
override with --init / --nzcv.

Examples
  trace.py snippet.s
  trace.py -e 'mov r1, #1
               subs r3, r1, #2'
  trace.py loop.s --init r0=17 --nzcv 0010
  trace.py prog.s --final              # only the final register table
  trace.py prog.s --mem data:5         # also dump 5 words at label 'data'

Requirements: arm-linux-gnueabi-gcc and qemu-arm on PATH.
"""
import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from check_asm import decode, strip_comments, split_operands, regs_in, Stmt  # noqa: E402

REGS = [f"r{i}" for i in range(13)]

ASM_PRELUDE = r"""
	.macro __TRACE line
	stmfd	sp!, {r0-r12, lr}
	mrs	r0, cpsr
	mov	r1, sp
	mov	r2, #(\line & 0xff)
	orr	r2, r2, #(\line & 0xff00)
	bl	__trace_dump
	msr	cpsr_f, r0
	ldmfd	sp!, {r0-r12, lr}
	.endm
"""

ASM_ENTRY = r"""
	.text
	.align	2
	.global	__trace_entry
	.type	__trace_entry, %function
@ void __trace_entry(unsigned *init /*r0..r12, flags*/, unsigned *out /*r0..r12, flags, lr, sp_delta*/)
__trace_entry:
	stmfd	sp!, {r4-r11, lr}
	sub	sp, sp, #4
	ldr	r2, =__trace_ctx
	str	r1, [r2]
	str	sp, [r2, #4]
	ldr	r1, [r0, #52]
	msr	cpsr_f, r1
	ldmia	r0, {r0-r12}
	bl	__snip_main
	stmfd	sp!, {r0-r12}
	mrs	r0, cpsr
	ldr	r2, =__trace_ctx
	ldr	r1, [r2]
	str	r0, [r1, #52]
	str	lr, [r1, #56]
	add	r3, sp, #52
	ldr	r4, [r2, #4]
	sub	r3, r3, r4
	str	r3, [r1, #60]
	mov	r2, #0
.Ltr_copy:
	ldr	r3, [sp, r2, lsl #2]
	str	r3, [r1, r2, lsl #2]
	add	r2, r2, #1
	cmp	r2, #13
	blt	.Ltr_copy
	ldr	r2, =__trace_ctx
	ldr	sp, [r2, #4]
	add	sp, sp, #4
	ldmfd	sp!, {r4-r11, pc}
	.size	__trace_entry, .-__trace_entry
	.ltorg
	.data
	.align	2
__trace_ctx:	.word 0, 0
	.text
"""

C_DRIVER = r"""
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
extern void __trace_entry(unsigned *init, unsigned *out);
static const char *src[] = { %(SRC)s };
static const int nsrc = %(NSRC)d;
static unsigned prev[14];
static unsigned prevflags;
static long steps;
static const long maxsteps = %(MAXSTEPS)d;
static const int stepmode = %(STEPMODE)d;
static const char *names[14] = {"r0","r1","r2","r3","r4","r5","r6","r7","r8","r9","r10","r11","r12","lr"};
%(MEMDECL)s

static void flags(unsigned c, char *b) {
    b[0] = (c >> 31) & 1 ? '1' : '0'; b[1] = (c >> 30) & 1 ? '1' : '0';
    b[2] = (c >> 29) & 1 ? '1' : '0'; b[3] = (c >> 28) & 1 ? '1' : '0'; b[4] = 0;
}

unsigned __trace_dump(unsigned cpsr, unsigned *saved, unsigned line) {
    char fb[5], pb[5], buf[512];
    int i, n = 0;
    buf[0] = 0;
    for (i = 0; i < 13; i++) {          /* lr changes are noise (dump calls) */
        if (saved[i] != prev[i]) {
            n += snprintf(buf + n, sizeof buf - n, "%%s=0x%%08x(%%d) ", names[i], saved[i], (int)saved[i]);
            prev[i] = saved[i];
        }
    }
    if ((cpsr & 0xf0000000u) != (prevflags & 0xf0000000u)) {
        flags(prevflags, pb); flags(cpsr, fb);
        n += snprintf(buf + n, sizeof buf - n, "NZCV %%s->%%s", pb, fb);
        prevflags = cpsr;
    }
    printf("%%4u | %%-34.34s | %%s\n", line, (line >= 1 && (int)line <= nsrc) ? src[line - 1] : "?", buf);
    if (++steps >= maxsteps) {
        printf("... stopped after %%ld executed lines (infinite loop? raise --max-steps)\n", steps);
        fflush(stdout);
        exit(3);
    }
    return cpsr;
}

int main(void) {
    unsigned init[14] = { %(INIT)s };
    unsigned out[16];
    char fb[5];
    int i;
    memcpy(prev, init, sizeof(unsigned) * 13);
    prevflags = init[13];
    if (stepmode)
        printf("line | source                             | changes after executing the line\n"
               "-----+------------------------------------+---------------------------------\n");
    __trace_entry(init, out);
    flags(out[13], fb);
    printf("\nFinal state (after the snippet returned):\n");
    for (i = 0; i < 13; i++)
        printf("  %%-3s = 0x%%08x  %%11d  %%10u%%s\n", names[i], out[i], (int)out[i], out[i],
               out[i] != init[i] ? "   *" : "");
    printf("  NZCV = %%s   (N=%%c Z=%%c C=%%c V=%%c)\n", fb, fb[0], fb[1], fb[2], fb[3]);
    if (out[15] != 0)
        printf("  WARNING: SP differs by %%d bytes from its value before the snippet (unbalanced push/pop?)\n", (int)out[15]);
%(MEMDUMP)s
    return 0;
}
"""


def cstr(s):
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"').replace("\t", "    ") + '"'


def find_tool(name):
    p = shutil.which(name)
    if not p:
        sys.exit(f"trace.py: '{name}' not found on PATH. Install: sudo apt-get install "
                 "gcc-arm-linux-gnueabi qemu-user")
    return p


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("file", nargs="?", help="snippet/program file ('-' = stdin)")
    ap.add_argument("-e", "--expr", help="snippet given inline")
    ap.add_argument("--init", default="", help="initial registers, e.g. r0=17,r1=0x10,r2=-3")
    ap.add_argument("--nzcv", default="0000", help="initial flags as 4 bits, e.g. 0010 = C set")
    ap.add_argument("--final", action="store_true", help="only print the final register table")
    ap.add_argument("--max-steps", type=int, default=2000)
    ap.add_argument("--mem", action="append", default=[], help="label:count - dump words at label afterwards")
    ap.add_argument("--keep", action="store_true", help="keep the generated build directory")
    a = ap.parse_args()

    if a.expr is not None:
        text = a.expr
    elif a.file in (None, "-"):
        text = sys.stdin.read()
    else:
        text = open(a.file, encoding="utf-8").read()
    lines = text.replace("\r", "").split("\n")
    lines = [l.strip("\n") for l in lines]

    gcc, qemu = find_tool("arm-linux-gnueabi-gcc"), find_tool("qemu-arm")

    # ---- initial values
    init = [0] * 14
    for item in filter(None, [x.strip() for x in a.init.split(",")]):
        k, v = item.split("=")
        k = k.strip().lower()
        if k not in REGS:
            sys.exit(f"--init: unknown register {k} (r0..r12)")
        init[int(k[1:])] = int(v.strip(), 0) & 0xFFFFFFFF
    if not re.fullmatch(r"[01]{4}", a.nzcv):
        sys.exit("--nzcv expects 4 bits, e.g. 0110")
    init[13] = int(a.nzcv, 2) << 28

    # ---- decide mode, find instruction lines
    clean = strip_comments("\n".join(lines)).split("\n")
    full = any(re.match(r"^\s*main\s*:", l) for l in clean)
    step = not a.final
    instr_lines = set()
    for i, l in enumerate(clean):
        body = re.sub(r"^\s*(([A-Za-z_.$][\w.$]*|\d+)\s*:\s*)+", "", l).strip()
        if not body or body.startswith(".") or re.match(r"^[\w.$]+\s*=", body):
            continue
        mn = body.split(None, 1)[0]
        base, _ = decode(mn)
        if base:
            instr_lines.add(i)
            st = Stmt(i + 1, [], mn, split_operands(body.split(None, 1)[1]) if " " in body or "\t" in body else [])
            if 15 in st.reads and base not in ("bx", "blx") and step and not st.is_pop():
                print(f"note: line {i+1} reads PC ('{body}'). Inserting trace code would change "
                      "PC-relative offsets, so only the final state is shown.\n")
                step = False

    out = [ASM_PRELUDE, "\t.text\n\t.align\t2\n"]
    if not full:
        out.append("\t.global\t__snip_main\n__snip_main:\n")
    for i, l in enumerate(lines):
        src = re.sub(r"\bmain\b", "__snip_main", l) if full else l
        out.append(src + "\n")
        if step and i in instr_lines:
            out.append(f"\t__TRACE {i + 1}\n")
    if not full:
        out.append("\t.text\n\tbx\tlr\n")
    memdecl, memdump = "", ""
    if a.mem:
        out.append("\t.data\n\t.align 2\n\t.global __trace_memtab\n__trace_memtab:\n")
        names = []
        for m in a.mem:
            lab, cnt = m.split(":")
            out.append(f"\t.word {lab}, {int(cnt)}\n")
            names.append(lab)
        memdecl = "extern unsigned __trace_memtab[];\nstatic const char *memnames[] = {" + \
                  ", ".join(cstr(n) for n in names) + "};"
        memdump = f"""    for (i = 0; i < {len(names)}; i++) {{
        unsigned *p = (unsigned *)__trace_memtab[2 * i]; unsigned k, c = __trace_memtab[2 * i + 1];
        printf("  memory at %s (0x%08x):\\n", memnames[i], (unsigned)p);
        for (k = 0; k < c; k++) printf("    [%s + %2u] = 0x%08x  %11d\\n", memnames[i], 4 * k, p[k], (int)p[k]);
    }}"""
    out.append(ASM_ENTRY)
    out.append('\t.section .note.GNU-stack,"",%progbits\n')

    shown = [" ".join(c.split()) for c in clean]          # comments removed, whitespace collapsed
    srcs = ", ".join(cstr(l) for l in shown) or '""'
    cdrv = C_DRIVER % {"SRC": srcs, "NSRC": len(lines), "MAXSTEPS": a.max_steps,
                       "STEPMODE": int(step), "INIT": ", ".join(str(x) for x in init),
                       "MEMDECL": memdecl, "MEMDUMP": memdump}

    d = tempfile.mkdtemp(prefix="mop_trace_")
    try:
        open(os.path.join(d, "snip.s"), "w").write("".join(out))
        open(os.path.join(d, "drv.c"), "w").write(cdrv)
        r = subprocess.run([gcc, "-mcpu=xscale", "-O0", "-g", "-w", "-o", os.path.join(d, "t"),
                            os.path.join(d, "drv.c"), os.path.join(d, "snip.s")],
                           capture_output=True, text=True)
        if r.returncode:
            err = r.stderr.replace(os.path.join(d, ""), "")
            sys.exit("Build failed - is the snippet valid for -mcpu=xscale?\n" + err)
        if not full:
            print("(bare snippet: r0-r12 start at 0 unless --init is given; NZCV=" + a.nzcv + ")\n")
        sysroot = "/usr/arm-linux-gnueabi"
        cmd = [qemu] + (["-L", sysroot] if os.path.isdir(sysroot) else []) + [os.path.join(d, "t")]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
        except subprocess.TimeoutExpired:
            sys.exit("Timed out (20 s) - the snippet probably never returns. Try without --final to see the loop.")
        sys.stdout.write(r.stdout)
        if r.returncode not in (0, 3):
            sys.stderr.write(r.stderr)
            print(f"\nProgram crashed (exit {r.returncode}). Typical causes: unaligned LDR/STR, bad pointer, "
                  "PC/LR overwritten, unbalanced stack.")
    finally:
        if a.keep:
            print(f"\n(build files kept in {d})")
        else:
            shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    main()

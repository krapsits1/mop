---
name: mop
description: Machine-oriented programming helper for 32-bit ARM assembly on ARMv5TE / Intel XScale with the GNU cross toolchain (arm-linux-gnueabi-gcc/as), qemu-arm, gdb-multiarch and Makefiles, including assembly functions called from C under the ARM EABI. Use it to write, review, debug and test .s files, trace register and NZCV values, and explain instructions, addressing modes, directives, macros and inline asm. Trigger whenever the user mentions ARM or XScale assembly, .s files, qemu-arm, arm-linux-gnueabi, an ARM Makefile, register or flag tracing, machine-oriented programming, or pastes ARM assembly code.
---

# ARM assembly (XScale / ARMv5TE)

The user supplies each task's requirements (prototypes, I/O format, forbidden instructions,
file names). Follow them exactly; if something is unspecified, ask or state the assumption.

## Target and tools
| | |
|---|---|
| CPU | ARMv5TE (Intel XScale), 32-bit ARM state, little-endian, **no divide instruction** |
| Build | `arm-linux-gnueabi-gcc` (C, and as the linker), `arm-linux-gnueabi-as`; always **`-mcpu=xscale`** |
| Run | `qemu-arm -L /usr/arm-linux-gnueabi ./prog args` |
| Debug | `qemu-arm -L … -g PORT ./prog` + `gdb-multiarch`, `target remote localhost:PORT` |
| Install | `apt-get install gcc-arm-linux-gnueabi linux-libc-dev-armel-cross qemu-user gdb-multiarch` |
| Syntax | GNU as, traditional ARM syntax: `@` comments, `#` immediates, `stmfd`/`ldmfd` |

If the sandbox lacks the toolchain, install it before building — run code instead of reasoning
about it. Without `-mcpu=xscale`, `mov r0, #0x1234` silently assembles to ARMv7 `MOVW`, which XScale
does not have. If the code will be judged on a specific machine (often with older gcc/qemu),
the user must test there: newer tools catch more bugs, older ones may accept them.

## Rules that prevent most failures
1. **ARM EABI**: args in r0–r3, the 5th at `[sp]` on entry; result in r0 (r0:r1 for 64-bit);
   save/restore every r4–r11 you modify; save LR if you call anything; r0–r3 and r12 are
   destroyed by calls; stack is Full Descending and 8-byte aligned at calls.
2. **Immediates** are 8-bit values rotated by an even amount; otherwise `ldr rX, =value`.
3. **Unsigned C types → HI/HS/LO/LS; signed → GT/GE/LT/LE.**
4. **Alignment**: words at multiples of 4, halfwords of 2; `.align 2` after byte data;
   characters with `LDRB/STRB`.
5. `MUL Rd, Rm, Rs` needs Rd ≠ Rm on XScale. Aliases are the same register: `ip` = r12, `fp` = r11.
6. Output exactly what is required (e.g. `17`, not `sum=17`, then `\n`); diagnostics to stderr.
7. Use the exact file names, directory names and prototypes the task gives.

## Workflow
1. Read the requirements; list the edge cases (0, max values, overflow, invalid input).
2. Scaffold if starting fresh: `python3 scripts/new_project.py NAME "<C prototype>" [DIR]`.
3. Write the algorithm in C or pseudocode, then a **register plan** (values that must survive a
   `bl` go in r4–r11). Argument locations: `python3 scripts/abi_layout.py "<prototype>" --push N`.
4. Translate line by line in the style below.
5. `python3 scripts/check_asm.py file.s [--no-mul] [--expect f,g]` — read every WARN.
6. `make`, then test exactly: `python3 scripts/qtest.py ./prog -c "ARGS=OUT"` or `--dir tests`.
7. Debug failures with gdb (`references/toolchain-debug.md`).
Explain what the code does and why (register plan, tricky lines) so the user can maintain and
explain it themselves.

## Style
```
@ file.s - purpose
	.text
	.align	2
	.global	sum_to				@ visible to C
	.type	sum_to, %function
@ unsigned int sum_to(unsigned int n);
@ in: r0 = n   out: r0 = 1+...+n
@ registers: r1 = i, r2 = sum
sum_to:
	mov	r2, #0			@ sum = 0
	mov	r1, #1			@ i = 1
	b	.Lsum_test
.Lsum_loop:
	add	r2, r2, r1		@ sum += i
	add	r1, r1, #1		@ i++
.Lsum_test:
	cmp	r1, r0			@ i <= n ?  (unsigned)
	bls	.Lsum_loop
	mov	r0, r2			@ return sum
	bx	lr
	.size	sum_to, .-sum_to
	.section .note.GNU-stack,"",%progbits
```
- Labels at column 0; TAB, lowercase mnemonic, TAB, operands, `@` comment stating the C meaning.
  One statement per line, never `;`.
- `.L` labels inside functions (no clashes between functions); `1:`/`1b`/`1f` in macros.
- Loops: jump to the test first. Use the `S` suffix instead of a `cmp` against 0
  (`subs r1, r1, #1` / `bhi loop`); conditional instructions for short if/else.
- Named constants (`ROW = 40`), `ldr rX, =big`, `#'a` for characters, barrel shifter for
  constant multiplies (`add r1, r1, r1, lsl #2` = ×5).
- Writable data in `.data`, strings in `.section .rodata` (`.asciz`), zeroed buffers with
  `.comm name, bytes, align_in_bytes`.
- Non-leaf function: `stmfd sp!, {r4-…, lr}` … `ldmfd sp!, {r4-…, pc}`; leaf using only r0–r3/r12: `bx lr`.
- C drivers: include the header with the exact prototype; validate `argc`, `scanf` return
  values and `malloc`; use `strtoul` for unsigned arguments (`atoi` clamps at 2147483647).

## Scripts (relative to this skill; Python 3)
| Script | Purpose |
|---|---|
| `new_project.py NAME "proto" [DIR]` | Makefile, header, `.s` skeleton with ABI comments, C driver skeleton |
| `check_asm.py file.s [--no-mul] [--expect a,b] [-q]` | static checks: callee-saved regs, LR, push/pop, stack args, alignment, immediates, non-XScale instructions |
| `abi_layout.py "proto" [--push N] [--sub K]` | register / stack slot of each argument, before and after the prologue |
| `trace.py file.s` or `-e 'code'` | runs code in qemu-arm, prints register/NZCV changes per line |
| `qtest.py ./prog -c "ARGS=OUT" \| --dir D \| --json F` | exact stdout / exit-code tests under qemu-arm |
`check_asm.py` does not expand macros and has no control-flow analysis: treat warnings as questions.

## References (read only what the question needs)
| File | Contents |
|---|---|
| `references/arm-isa.md` | instructions, Operand2, shifter, flags, conditions, branches, addressing modes, LDM/STM, missing instructions |
| `references/abi-functions.md` | EABI, prologue/epilogue, stack arguments, calling C, being called from C, structs and bit fields |
| `references/directives-macros.md` | sections, data, alignment, expressions, conditional assembly, macros, inline asm |
| `references/toolchain-debug.md` | build steps, Makefiles, qemu, gdb commands, object-file tools, crash symptoms, testing |
| `references/tracing.md` | hand-tracing method, exact NZCV rules, verified examples, common traps |
`assets/Makefile.template` — Makefile used by `new_project.py`.

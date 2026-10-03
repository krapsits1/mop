# Functions, the ARM EABI, and C ↔ assembly

## Contents
1. Register roles
2. Arguments and return values
3. The stack
4. Prologue / epilogue patterns
5. Calling C from assembly
6. Assembly called from C
7. Data: globals, locals, structs and bit fields
8. Tested examples
9. Learning from the compiler
10. Checklist

## 1. Register roles
| Register | Role | Callee must preserve? |
|---|---|---|
| r0–r3 | arguments 1–4, result (r0, or r0:r1 for 64-bit), scratch | No |
| r4–r8, r10, r11 | variables | **Yes** |
| r9 | platform register | treat as callee-saved |
| r12 (ip) | scratch (linker veneers) | No |
| r13 (sp) | stack pointer | same value on return |
| r14 (lr) | return address; **overwritten by every BL/BLX** | save it if you call anything |
| r15 (pc) | program counter | – |
Values needed after a `bl` belong in r4–r11 (saved by you) or on the stack. Forgetting to save
r4+ often works by luck and fails elsewhere — never rely on it.

## 2. Arguments and return values
- Arguments 1–4 in r0–r3; further ones on the stack: on entry the 5th is at `[sp]`, the 6th at
  `[sp, #4]`, … (the caller reserves and removes that space).
- Arguments ≤ 4 bytes (char, short, int, long, pointer, enum) use a whole register/slot.
- 8-byte arguments (`long long`, `double`) use an **even** register pair r0:r1 or r2:r3 (low word
  in the lower register) or an 8-byte-aligned stack slot; once one goes to the stack, all later ones do.
- Result in r0, or r0:r1.
- `python3 scripts/abi_layout.py "<prototype>" --push N` prints every location, before and after
  pushing N registers. Example `f(long long, long, short, char, long, short, char)` called with
  1…7: r0:r1 = 1 (r0 = 1, r1 = 0), r2 = 2, r3 = 3, `[sp]` = 4, `[sp,#4]` = 5, `[sp,#8]` = 6,
  `[sp,#12]` = 7.
- **Pushing moves the stack arguments**: after `stmfd sp!, {r4-r11, lr}` (36 bytes) the 5th
  argument is at `[sp, #36]`.

## 3. The stack
- Full Descending: `stmfd sp!, {…}` / `ldmfd sp!, {…}` (= `stmdb`/`ldmia` = `push`/`pop`).
- SP must be **8-byte aligned at every call**: push an even number of registers or add
  `sub sp, sp, #4`. Leaf functions don't need this. (C functions handling `long long`/`double`
  can break on a misaligned stack.)
- Locals: `sub sp, sp, #N` after the push, `[sp, #k]` addressing, `add sp, sp, #N` before the pop.
- Every push has a matching pop, every `sub sp` a matching `add sp`.

## 4. Prologue / epilogue patterns
Start: push used r4–r11 (+ LR if calling); `sub sp, sp, #N` for locals; optionally
`stmia sp, {r0-r3}` to free the argument registers. End: result → r0; `add sp, sp, #N`;
`ldmfd sp!, {…, pc}` (LR popped straight into PC = restore and return).
```
@ leaf, r0-r3/r12 only     @ leaf, needs r4-r6            @ calls other functions
f:  ...                    g:  stmfd sp!, {r4-r6}         h:  stmfd sp!, {r4-r7, lr}   @ 20 bytes
    bx    lr                   ...                            sub   sp, sp, #4         @ 24: aligned
                               ldmfd sp!, {r4-r6}             ...
                               bx    lr                       add   sp, sp, #4
                                                              ldmfd sp!, {r4-r7, pc}
```
Per function: `.global name`, optional `.type name, %function` and `.size name, .-name`.

## 5. Calling C from assembly
Save LR, keep SP 8-byte aligned, keep needed values in r4–r11, put arguments in r0–r3 (+ stack),
`bl func`, take the result from r0. Example: `str lr, [sp, #-4]!` before `bl printf` pushes only
4 bytes — misaligned; use `stmfd sp!, {r4, lr}` instead.
`.ascii` adds no terminating zero; `.asciz` does — `printf("%s")` on an `.ascii` string runs on
into whatever follows it.

## 6. Assembly called from C
- C needs the prototype (header), otherwise arguments may be passed wrongly.
- The `.s` file needs `.global name` with a matching label (case-sensitive).
- A plain `.s` file cannot `.include` a C header. To share constants, use a preprocessed `.S`
  file with C parts guarded by `#ifndef __ASSEMBLER__` — only if the required file name allows it.

## 7. Data
- Globals: `.data` (initialised, writable), `.section .rodata` (constants), `.comm`/`.bss` (zeroed).
  Load the address with `ldr r3, =sym`, or keep it in a literal word (`sym_addr: .word sym`,
  `ldr r3, sym_addr`); `adr` only works within the same section.
- Locals: on the stack (§3). Heap: call `malloc`/`free` like any C function.
- Structs: pass pointers. GCC on ARM allocates **bit fields from bit 0 upward in declaration
  order**; verified: `struct { unsigned r:10, g:10, b:10, op:2; }` → r = bits 0–9, g = 10–19,
  b = 20–29, op = 30–31. Load the word with `ldr`, extract with shifts (`lsl` then `lsr`), since
  masks like `#0x3ff` are not encodable immediates.

## 8. Tested examples (built with -mcpu=xscale, run in qemu-arm, checker-clean)
```
	.text
	.align	2
@ int sum7(int a, int b, int c, int d, int e, int f, int g);
@ r0=a r1=b r2=c r3=d, on entry: [sp]=e [sp,#4]=f [sp,#8]=g
	.global	sum7
	.type	sum7, %function
sum7:
	stmfd	sp!, {r4, r5}		@ 8 bytes pushed -> e, f, g at sp+8, +12, +16
	add	r0, r0, r1		@ a + b
	add	r0, r0, r2		@ + c
	add	r0, r0, r3		@ + d
	ldr	r4, [sp, #8]		@ e
	ldr	r5, [sp, #12]		@ f
	add	r0, r0, r4
	add	r0, r0, r5
	ldr	r4, [sp, #16]		@ g
	add	r0, r0, r4
	ldmfd	sp!, {r4, r5}
	bx	lr
	.size	sum7, .-sum7

@ void print_array(const int *a, int n);  printf("a[%d] = %d\n") per element
	.global	print_array
	.type	print_array, %function
print_array:
	stmfd	sp!, {r4, r5, r6, lr}	@ 16 bytes: SP stays 8-byte aligned
	mov	r4, r0			@ p  (survives printf)
	mov	r5, r1			@ n
	mov	r6, #0			@ i
	b	.Ltest
.Lloop:
	ldr	r0, =fmt		@ arg 1: format
	mov	r1, r6			@ arg 2: i
	ldr	r2, [r4], #4		@ arg 3: *p++
	bl	printf			@ destroys r0-r3, r12, lr
	add	r6, r6, #1		@ i++
.Ltest:
	cmp	r6, r5			@ i < n ?  (signed)
	blt	.Lloop
	ldmfd	sp!, {r4, r5, r6, pc}
	.size	print_array, .-print_array

	.section .rodata
fmt:	.asciz	"a[%d] = %d\n"
	.section .note.GNU-stack,"",%progbits
```
`sum7(1,…,7)` → 28; `print_array((int[]){10,-20,30}, 3)` prints three lines.

## 9. Learning from the compiler
Write a small C prototype and compile it to assembly to see the exact calling convention:
`make proto.c.s` (Makefile template) or
`arm-linux-gnueabi-gcc -mcpu=xscale -O1 -S -fomit-frame-pointer -o - proto.c`.
`-O0` output keeps every variable on the stack (literal but verbose); `-O1` is closer to
hand-written code. Use it to learn conventions, not as the solution.

## 10. Checklist
- [ ] `.global` + label match the C name exactly
- [ ] every modified r4–r11 pushed and popped; LR pushed if the function calls anything
- [ ] stack arguments read at offsets **after** the pushes
- [ ] SP restored; 8-byte aligned at calls
- [ ] signed/unsigned conditions match the C types
- [ ] result in r0 on every path
- [ ] `check_asm.py` clean

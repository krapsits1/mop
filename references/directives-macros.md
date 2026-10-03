# Directives, data, expressions, macros, inline assembler
GNU as manual: <http://sourceware.org/binutils/docs/as/>. Examples marked "tested" were assembled
with `-mcpu=xscale` and run in qemu-arm.

## Contents
1. Sections and symbols
2. Defining data (numbers, strings, arrays, BSS)
3. Alignment
4. Expressions and symbol assignment
5. Conditional assembly
6. Macros
7. Inline assembler in C (GCC extended asm)

## 1. Sections and symbols
| Directive | Meaning |
|---|---|
| `.text` | code (and constant data / literal words) |
| `.data` | initialised data you will **modify** |
| `.section .rodata` | read-only data (strings, tables) |
| `.bss` / `.comm` | zero-initialised data (memory pages filled with 0 at load time) |
| `.global sym` | export symbol to the linker (functions called from C, `main`) |
| `.type sym, %function` / `.size sym, .-sym` | metadata for gdb/objdump |
| `.ltorg` | dump the literal pool (for `ldr rX, =const`) here — needed if code is > 4 KB from the pool |
| `.section .note.GNU-stack,"",%progbits` | silences "missing .note.GNU-stack" linker warnings on new toolchains |
Code in `.text` cannot reach `.data` with `adr` (different section); load the address:
`ldr r3, tb_addr` with `tb_addr: .word tenbeef` in `.text`, or `ldr r3, =tenbeef`.

## 2. Defining data
```
a:       .short  0xbeef, 0xfeed, 0xcafe      @ several values -> an array
w:       .word   1, 2, 3                     @ 32-bit (.long and .int are the same)
b8:      .byte   'x, 0x41, 10                @ characters with an apostrophe
hello:   .ascii  "Hello "                    @ NO terminating 0
fmt:     .asciz  "%d\n"                      @ terminating 0 added (C string); also .string
tenbeef: .rept   10                          @ repeat a block
         .word   0xdeadbeef
         .endr
gap:     .skip   16                          @ 16 zero bytes (.space is the same)
         .comm   c, 3, 2                     @ BSS: name, size in bytes, alignment IN BYTES
         .comm   b, 10*4, 4                  @ 40 bytes, word aligned (expressions allowed)
```
- `.comm` alignment is in **bytes**; `.align` on ARM is a **power of two**.
- Strings: process with `LDRB/STRB` (not LDR/STR — alignment and little-endian break it).
- String length: fixed in the program, stored in a separate field (current and maximum), or
  marked by a terminator — prefer C-style `0x00`.

## 3. Alignment
`.align n` on ARM = align to 2ⁿ bytes (`.align 2` → 4, `.align 1` → 2); `.balign 4` = 4 bytes.
Listing (`as -alh`):
```
         .data
         .align 2
0000 EFBEADDE  .word  0xdeadbeef
0004 BF        .byte  0xbf
0005 00        .align 1          @ pad to 2
0006 EFBE      .short 0xbeef
0008 BF        .byte  0xbf
0009 000000    .align 2          @ pad to 4
000c EFBEADDE  .word  0xdeadbeef
```
Put `.align 2` after any `.byte/.ascii/.asciz/.short` run that is followed by words or code.
Misaligned word/halfword access → Data Abort → process killed with SIGBUS.

## 4. Expressions and symbol assignment
- Expressions may appear wherever a number is expected (immediates, data, addresses). An omitted
  expression is 0. Values are either **absolute** numbers or **section + offset** (fixed up later by
  the linker/loader); if neither can be determined, the assembler reports an error.
- Operators, highest precedence first:
  1. prefix `-` (negation), `~` (bitwise not)
  2. `*`, `/`, `%`, `<<`, `>>`
  3. `|`, `&`, `^`, `!` (bitwise or-not — not logical not)
  4. `+`, `-` (cannot combine symbols from different sections), `==`, `<>`/`!=`, `<`, `>`, `<=`, `>=`
     (comparisons are signed; true = −1, false = 0)
  5. `&&`, `||` (true = 1, false = 0)
- Tested: `ldr r0, numbers + 2 << 2` loads `numbers[2]` (`<<` binds tighter than `+`, offset 8).
- Assignment: `seventeen = 13 + 4` or `.set sym, expr` (= `.equ`). `.equiv sym, expr` errors if
  `sym` already exists and prevents redefinition. `.eqv` is like `.equiv` but evaluated lazily at
  each use. `. = . + 4` advances the location counter (leaves space).
- Use named constants instead of magic numbers:
  `ROW_BYTES = 4 * 40`, `COLOR_MASK = 0x3ff`, then `ldr r1, =COLOR_MASK`.

## 5. Conditional assembly
`.if expr` (non-zero, absolute) / `.elseif` / `.else` / `.endif`, plus `.ifdef sym`, `.ifndef`
(`.ifnotdef`), `.ifb text` (blank), `.ifnb`, `.ifc s1,s2` (strings equal; may be quoted with `'`),
`.ifnc`, `.ifeqs "s1","s2"`, `.ifnes`, `.ifeq expr` (== 0), `.ifne` (= `.if`), `.ifge`, `.ifgt`,
`.ifle`, `.iflt`.
Define a symbol from the command line: `arm-linux-gnueabi-as --defsym USE_MUL=1 …`, or through gcc
`-Wa,--defsym,USE_MUL=1`. Tested example (multiplication with or without `MUL`):
```
	.ifdef USE_MUL
	mul	r0, r1, r0
	bx	lr
	.else			@ shift-and-add: for each set bit k of r0, add r1 << k
	movs	r2, r0
	bxeq	lr
	stmfd	sp!, {r4, r5, lr}
	mov	r0, #0
	mov	r5, #1
loop:	clz	r3, r2
	rsb	r4, r3, #31		@ k = index of the highest set bit
	add	r0, r0, r1, LSL r4	@ result += r1 << k
	bics	r2, r2, r5, LSL r4	@ clear bit k
	bne	loop
	ldmfd	sp!, {r4, r5, pc}
	.endif
```

## 6. Macros
```
	.macro clear_reg reg		@ definition
	sub	\reg, \reg, \reg	@ parameters are used with a backslash
	.endm
	clear_reg r3			@ use -> sub r3, r3, r3
```
- Parameters: comma-separated; `name:req` (required), `name=default`, last may be `name:vararg`.
  Omitted optional parameters become empty/0 or the default. Call by position or by name:
  `LIST 9, 17` ≡ `LIST to=17, from=9`. Macro names are case-insensitive.
- Problems with special characters (`:`, `.`) next to a parameter: separate with a space or `\()`,
  e.g. `\label\():`, or write `\label :` (space before the colon).
- `\@` = counter of macros expanded so far → unique labels per expansion (`ps_a\@`).
  Local numeric labels (`1:` / `1b`) also work inside macros.
- A macro hard-wired to fixed registers/labels can be used only once (duplicate label) — pass them as
  parameters, or use `\@`.
- Macros may switch sections and switch back (tested):
  ```
	.macro PUTS string
	ldr	r0, ps_a\@
	mov	pc, pc			@ PC reads as +8: skips the .word below
  ps_a\@: .word	puts_string\@
	bl	puts
	.data
  puts_string\@: .asciz "\string"
	.text
	.endm
  ```
- Recursion works with `.if` as the stop condition; `.rept`/`.endr` with a
  symbol counter is usually simpler. Both tested: `LIST 0, 2` → `.long 0, 1, 2`.
- Macros expand at assembly time (no call overhead); see expansions in the `-alh=file.lis`
  listing (lines marked `>`).
- `check_asm.py` does not expand macros: review macro bodies (saved registers, LR) by hand.

## 7. Inline assembler (GCC extended asm)
Inline functions (`inline`) are copied into the caller; inline asm is the same idea written in assembly.
Used for: hand-optimised code, CPU-specific instructions (`SWP` for synchronisation primitives,
`CLZ`), system calls, coprocessors, non-standard tricks (e.g. manipulating the stack).
```
asm ("add sp, #8");                         /* basic asm: no operands */
asm ( "code" : outputs : inputs : clobbers );
```
- `"code"`: may be multi-line — separate instructions with `\n\t`; operands are `%0`, `%1`, … in the
  order outputs then inputs.
- Operand = `"constraint" (C expression)`; outputs need an lvalue and a constraint starting with `=`.
  Constraints: `r` register, `m` memory, `rm`, `ri` register or immediate, `g` anything.
  `"0"` = must be in the same place as operand 0 (input that is also the output).
  `&` (early clobber, `"=&r"`) = output written before all inputs are read → must not share a register.
- Clobbers: registers destroyed (`"r8"`, `"r10"`), `"cc"` if flags change, `"memory"` if memory other
  than the outputs is modified. Prefer extra dummy outputs over clobbering fixed registers for
  temporaries — it gives the compiler more freedom.
- `volatile` (`asm volatile (...)` or `__asm__ __volatile__`) stops the compiler from moving or deleting
  the block; **required** if it touches memory not listed in the operands. `__asm__` works even in
  strict ANSI mode.
- You cannot jump between different asm blocks with labels; use local labels (`1:`, `1f`) inside one block.
Tested (`-O2`):
```c
unsigned int log_floor(unsigned int num) {           /* CLZ */
    unsigned int res;
    asm ("clz %0, %1" : "=r" (res) : "r" (num));
    return 31 - res;                                  /* 1->0, 1000->9, 0x80000000->31 */
}
unsigned int clear_bits(unsigned int num, unsigned int mask) {   /* "0": shares operand 0 */
    asm ("bic %0, %1, %2" : "=r" (mask) : "r" (num), "0" (mask));
    return mask;                                      /* (0xff, 0x0f) -> 0xf0 */
}
int add_carry(int a, int b, int *carry) {            /* flags + early clobber */
    int r, c;
    asm volatile ("adds %0, %2, %3\n\t"
                  "movcc %1, #0\n\t"
                  "movcs %1, #1"
                  : "=&r" (r), "=&r" (c) : "r" (a), "r" (b) : "cc");
    *carry = c;
    return r;                                         /* (-1, 2) -> 1, carry = 1 */
}
```

# ARM instructions (ARMv5TE / XScale, 32-bit ARM state, GNU as syntax)

## Contents
1. Statement syntax, comments, symbols, labels
2. Registers and CPSR flags
3. Operand2, immediates, loading constants
4. Data movement and arithmetic
5. Barrel shifter
6. Logical / bit operations and bit tricks
7. Compare instructions, S suffix, condition codes
8. Branches and PC
9. Memory access and addressing modes
10. LDM / STM and stacks
11. Not available on XScale

## 1. Statement syntax
- One statement per line: `[label:] [mnemonic operands] [@ comment]`. Mnemonics are case-insensitive
  (write them in lowercase). `;` also separates statements but **don't** use it (it starts a
  comment on other architectures).
- Comments: `@ to end of line`; `/* multi-line */`; `#` only when it is the **leftmost** character
  of a line (`# 12 "file"` lines may be interpreted like C preprocessor output — avoid).
- Whitespace (spaces/TABs) collapses to one space; align columns with TABs: label, mnemonic, operands, comment.
- Symbol names: letters, digits, `_`, `.`, `$`; start with a letter, `_` or `.`. Avoid `$`.
  Every symbol has a name, value, type (see with `nm`).
- `.Lname` = **local symbol**, not written to the object file — use for internal jump targets.
- Numeric local labels `1:` … referenced as `1f` (next one forward) / `1b` (previous one backward);
  reusable many times — ideal in macros and inline asm.

## 2. Registers and CPSR
- `r0`–`r12` general; `r13 = sp`, `r14 = lr` (return address), `r15 = pc`.
  Aliases: `fp = r11`, `ip = r12`, `sl = r10`, `sb = r9`, `a1–a4 = r0–r3`, `v1–v8 = r4–r11`.
- **Reading PC gives the address of the current instruction + 8** (pipeline).
- CPSR: `N Z C V` in bits 31–28 (together called "pazīme"), bits 27–8 SBZ, `I F` (7, 6) interrupt
  disables, bit 5 SBZ (T on later cores), bits 4–0 processor **Mode**.
  - **N** result bit 31 (negative as signed) · **Z** result is 0
  - **C** carry out of bit 31 for add/shift; for **subtraction C = NOT borrow** (C=1 means no borrow,
    i.e. `a >= b` unsigned)
  - **V** signed overflow (result does not fit as a signed 32-bit number)
- Flags are changed only by compares (`cmp cmn tst teq`) and by instructions with the `S` suffix.
  Read/write directly with `MRS`/`MSR` (rarely needed).

## 3. Operand2 and constants
Typical forms: `MOV Rd, <Operand2>`, `ADD Rd, Rn, <Operand2>` (Rn is never changed).
Operand2 is one of:
- **immediate** `#imm`: an **8-bit value rotated right by an even amount** (0, 2, …, 30).
  Valid: `#255`, `#0x3FC`, `#0xFF000000`, `#0x104`. Invalid: `#257`, `#0x1234`, **`#0x3FF`**, `#0x101`.
  gas automatically swaps to the complementary instruction when that makes it fit:
  `mov #-1 → mvn #0`, `cmp #-1 → cmn #1`, `add #-4 → sub #4`, `and #0xffffff00 → bic #0xff`.
- **register** `Rm`
- **shifted register** `Rm, LSL #n` / `Rm, LSL Rs` (barrel shifter, §5)

Loading any 32-bit constant:
- `ldr Rd, =0xdeadbeef` — **pseudo-op**: becomes `mov`/`mvn` if possible, otherwise a PC-relative
  `ldr` from the nearest **literal pool** (placed at section end or at `.ltorg`; must be within ±4 KB).
- `ldr Rd, =label` — address of a label in any section (literal pool holds the address).
- `adr Rd, label` — pseudo-op: `add/sub Rd, pc, #off`; only for nearby labels **in the same section**.
- `ldr Rd, label` (no `=`) — loads the **word stored at** `label` (PC-relative), e.g.
  `ldr r3, b_addr` where `b_addr: .word b`.
- Build constants by hand: `mov r0, #0x12` then `orr r0, r0, #0x3400` (any 32-bit value in ≤ 4 instructions).
- Characters: `mov r2, #'M` (apostrophe, no closing quote).

## 4. Data movement and arithmetic
| Instruction | Meaning |
|---|---|
| `MOV Rd, op2` | Rd = op2 |
| `MVN Rd, op2` | Rd = NOT op2 = −op2 − 1 (so `mvn r4, #3` → −4) |
| `ADD Rd, Rn, op2` / `ADC` | Rd = Rn + op2 (+ C) |
| `SUB Rd, Rn, op2` / `SBC` | Rd = Rn − op2 (− NOT C) |
| `RSB Rd, Rn, op2` / `RSC` | Rd = op2 − Rn (− NOT C) — reverse subtract; `rsb r1, r0, #0` = −r0 |
| `CLZ Rd, Rm` | number of leading zero bits (32 for 0); `31 − clz(x)` = ⌊log2 x⌋ |
| `MUL Rd, Rm, Rs` | Rd = Rm·Rs (low 32 bits, same for signed/unsigned). **On XScale Rd must differ from Rm** (`mul r0, r0, r1` is unpredictable — write `mul r0, r1, r0`). Not fast. |
| `MLA Rd, Rm, Rs, Rn` | Rd = Rm·Rs + Rn |
| `UMULL/SMULL RdLo, RdHi, Rm, Rs` | 64-bit product; `UMLAL/SMLAL` accumulate |

- Two-operand shorthand is accepted: `add r1, #1` ≡ `add r1, r1, #1`; `subs r2, #'0`.
- **There is no divide instruction** on XScale. Divide by 2^n with `LSR` (unsigned) / `ASR` (signed,
  rounds toward −∞: −5 ASR 2 = −2); otherwise loop/shift-subtract, or call C.
- Multiplying by constants with the barrel shifter: `add r1, r1, r1, lsl #2` (×5),
  `rsb r1, r1, r1, lsl #3` (×7), `mov r1, r1, lsl #5` (×32).

## 5. Barrel shifter (Operand2 manipulation; source registers are not changed)
| Shift | Effect | Amount |
|---|---|---|
| `LSL #n` | logical shift left, fills 0: ×2ⁿ | 0–31 |
| `LSR #n` | logical shift right, fills 0: unsigned ÷2ⁿ | 1–32 |
| `ASR #n` | arithmetic shift right, fills sign bit: signed ÷2ⁿ | 1–32 |
| `ROR #n` | rotate right | 1–31 (`ror #32` is invalid: that encoding is RRX) |
| `RRX` | rotate right 1 bit through carry: C becomes bit 31, bit 0 → C (with S) | – |
- Amount may be a register: `mov r8, r5, asr r2` (uses the low byte of r2).
- With the `S` suffix, logical instructions set C from the last bit shifted out.
- 4-operand looks: `sub r0, r1, r2, lsl #2` (technically `r2, lsl #2` is one operand).
- UAL spellings `lsl r0, r1, #2` = `mov r0, r1, lsl #2` (gas accepts both).

## 6. Logical / bit operations
`AND` (and), `ORR` (or), `EOR` (xor), `BIC Rd, Rn, op2` (Rd = Rn AND NOT op2 — clears the bits set in op2).
Shifts/rotations are done with `MOV` + shifted operand: `mov r1, r2, lsl #5`.

Idioms:
- set bit k: `orr r0, r0, #(1<<k)` · clear: `bic r0, r0, #(1<<k)` · toggle: `eor r0, r0, #(1<<k)`
- test bit k: `tst r0, #(1<<k)` then `bne set` / `beq clear`
- low byte: `and r1, r0, #0xff`
- extract a field of width w at bit p (any w): `mov r1, r0, lsl #(32-p-w)` then `mov r1, r1, lsr #(32-w)`
  (use `asr` instead of `lsr` for a signed field). Example 10-bit field at bit 10:
  `mov r1, r0, lsl #12` / `mov r1, r1, lsr #22`.
- insert: clear with `bic` (mask in a register if not encodable) then `orr` the shifted value.
- absolute value: `cmp r0, #0` / `rsblt r0, r0, #0`.

## 7. Compares, S suffix, condition codes
| Instruction | Sets flags from | Typical use |
|---|---|---|
| `CMP Rn, op2` | Rn − op2 | ordering / equality |
| `CMN Rn, op2` | Rn + op2 | compare with a negative constant |
| `TST Rn, op2` | Rn AND op2 | is a bit set? |
| `TEQ Rn, op2` | Rn EOR op2 | equality without touching C/V |
Compares change **only** CPSR, never registers.

`S` suffix: any arithmetic/logical/move instruction sets flags if asked: `MOVS`, `SUBS`, `ADDS`…
Pre-UAL order is condition then S: `ADDNES` (UAL also accepts `ADDSNE`). This saves a `CMP`, e.g.
`subs r1, r1, #1` + `bhi loop` (loop while the decremented counter is non-zero, unsigned).

Every instruction can be **conditional** (`moveq`, `addne`, `ldrgt`, `bxeq lr`). A condition
that fails turns the instruction into a no-op (nothing written, flags unchanged).
| Cond | Meaning | Flags | Use after `cmp a, b` |
|---|---|---|---|
| `EQ` / `NE` | equal / not equal | Z=1 / Z=0 | both |
| `CS`=`HS` | carry set / unsigned ≥ | C=1 | **unsigned** a ≥ b |
| `CC`=`LO` | carry clear / unsigned < | C=0 | **unsigned** a < b |
| `HI` | unsigned > | C=1 and Z=0 | **unsigned** a > b |
| `LS` | unsigned ≤ | C=0 or Z=1 | **unsigned** a ≤ b |
| `GE` | signed ≥ | N=V | **signed** a ≥ b |
| `LT` | signed < | N≠V | **signed** a < b |
| `GT` | signed > | Z=0 and N=V | **signed** a > b |
| `LE` | signed ≤ | Z=1 or N≠V | **signed** a ≤ b |
| `MI` / `PL` | negative / positive or zero | N=1 / N=0 | sign test |
| `VS` / `VC` | overflow / no overflow | V=1 / V=0 | signed overflow |
| `AL` | always (default, not written) | – | |

**Pick unsigned conditions for `unsigned` C types and pointers** (HI/HS/LO/LS) and signed ones
for `int` (GT/GE/LT/LE). Mixing them up is the most common logic bug (e.g. a huge unsigned `n`
looking negative to `BLT`).
- Unsigned overflow of `adds`: **C=1** (`bcs overflow`). Signed overflow: **V=1** (`bvs`).

## 8. Branches and the PC
| Instruction | Effect |
|---|---|
| `B label` | jump, range ±32 MB |
| `BL label` | jump and put the return address in LR (call) |
| `BX Rm` | jump to the address in Rm (`bx lr` = return) |
| `BLX Rm` | call the address in Rm, return address in LR |
Also: `mov pc, rX`, `ldr pc, [..]`, `ldmfd sp!, {…, pc}`, arithmetic on PC (`add pc, pc, #4`
skips the next instruction pair because PC reads as current + 8).
Control structures:
```
@ for (i = 0; i <= 16; i++) s += i;          @ if (i < 17) j = i; else j = -i;   (r0 = i, unsigned)
        mov   r0, #0          @ s = 0                cmp   r0, #16
        mov   r1, #0          @ i = 0                bhi   else
        b     test                           then:   mov   r1, r0          @ j = i
loop:   add   r0, r0, r1      @ s += i               b     endif
        add   r1, r1, #1      @ i++          else:   mvn   r1, r0          @ ~i
test:   cmp   r1, #16                                add   r1, r1, #1      @ -i
        ble   loop                           endif:
```
"Jump to the test first" keeps one conditional branch per iteration. `while` and `for` are the
same thing in assembly.

## 9. Memory access
| Instruction | Effect |
|---|---|
| `LDR` / `STR` | 32-bit word |
| `LDRB` / `STRB` | byte (LDRB zero-extends) |
| `LDRH` / `STRH` | halfword (LDRH zero-extends) |
| `LDRSB` / `LDRSH` | signed byte / halfword (sign-extends) |
Addressing modes (Rn = base):
| Mode | Syntax | Address used | Rn afterwards |
|---|---|---|---|
| offset | `[Rn]`, `[Rn, #±imm12]`, `[Rn, ±Rm]`, `[Rn, ±Rm, LSL #k]` | Rn + offset | unchanged |
| pre-indexed | same + `!` e.g. `[Rn, #4]!` | Rn + offset | Rn + offset (write-back) |
| post-indexed | `[Rn], #±imm12`, `[Rn], ±Rm`, `[Rn], ±Rm, LSL #k` | Rn | Rn + offset |
- imm12: −4095…4095. Rm is never modified. SCALE ∈ LSL #0–31, LSR/ASR #1–32, ROR #1–31, RRX.
- **LDRH, LDRSH, LDRSB, STRH** (newer instructions): **no shifted register offset** and
  immediate offset only **−255…255**.
- `*p++` → post-indexed `ldr r0, [r1], #4`; `*--p` → pre-indexed `str r2, [r4, #-4]!`;
  `p[i]` (int) → `ldr r0, [r1, r2, lsl #2]`; `p[i]` (char) → `ldrb r0, [r1, r2]`.
- **Alignment**: words at addresses divisible by 4, halfwords by 2. Misaligned access →
  Data Abort → the OS kills the process (usually **SIGBUS**). Strings/bytes: use `LDRB/STRB`
  (LDR on characters breaks on alignment and little-endian byte order).
- **Little-endian**: `.word 0xdeadbeef` is stored as bytes `EF BE AD DE`.

## 10. LDM / STM (multiple registers) and stacks
`LDM<cond><mode> Rd{!}, {reglist}` / `STM<cond><mode> Rd{!}, {reglist}`
- The list is a set; registers are always stored/loaded **in ascending register order**, lowest
  register at the lowest address, regardless of how the list is written. `!` writes back Rd.
- n registers:  IB: Rd+4 … Rd+4n · IA: Rd … Rd+4n−4 · DB: Rd−4n … Rd−4 · DA: Rd−4n+4 … Rd.
  With `!`: Rd ± 4n.
- Stack names (SP points to the last used = **Full**, or next free = **Empty**; grows **Descending**/Ascending):
  `LDMFA=LDMDA, LDMFD=LDMIA, LDMEA=LDMDB, LDMED=LDMIB, STMED=STMDA, STMEA=STMIA, STMFD=STMDB, STMFA=STMIB`.
- **ARM EABI stack is Full Descending**: push = `stmfd sp!, {…}`, pop = `ldmfd sp!, {…}`
  (`push {…}` / `pop {…}` are the same instructions).
- Single register: `str r0, [sp, #-4]!` (push) / `ldr r0, [sp], #4` (pop).
- Example (sp → points at the top element):
  ```
  sub   sp, sp, #8          @ make space
  stmfd sp!, {r1, r2}       @ sp-> 1, 2
  ldmfd sp!, {r5, r6}       @ 1, 2, sp->        (r5=1, r6=2)
  stmia sp, {r3, r4}        @ 1, 2, sp-> 3, 4   (no write-back)
  add   sp, sp, #8          @ 1, 2, 3, 4, sp->
  ```

## 11. Not available on XScale (ARMv5TE) — the assembler rejects them with -mcpu=xscale
`MOVW/MOVT`, `UDIV/SDIV`, `UBFX/SBFX/BFI/BFC`, `RBIT/REV`, `UXTB/UXTH/SXTB/SXTH`, `LDREX/STREX`,
Thumb-2 `IT`, `CBZ/CBNZ`, barriers `DMB/DSB/ISB`. Code copied from modern ARM tutorials (Raspberry Pi
ARMv7/v8 examples, compiler output for other CPUs) often uses these — rewrite with classic instructions.
`SWP` (atomic swap) exists and is the classic synchronisation primitive on this CPU.

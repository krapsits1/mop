# Tracing code by hand

Question type: "what are the register values (and flags) after this fragment runs?"
Every result below was verified on qemu-arm with `scripts/trace.py`.

## Contents
1. Method
2. Exact flag rules
3. Shifter rules
4. Verified worked examples
5. Traps that cost points
6. Checking with trace.py

## 1. Method
1. Write a table: one row per **executed** line (loops produce several rows), columns for each
   register used and NZCV. Assume registers/flags start at 0 unless the task says otherwise.
2. Keep every value as **32-bit hex** (8 digits); convert to decimal only at the end, and decide
   whether the task wants it signed or unsigned.
3. Negative numbers: −x = NOT x + 1 (two's complement). `MVN r, #x` gives −x − 1.
4. For each instruction ask: is the condition true? (if not: nothing changes, not even flags);
   does it have `S` or is it CMP/CMN/TST/TEQ? (only then do flags change); does it write back a base
   register (`!` or post-indexed)?
5. Follow branches using the flags *as they are at that moment*.

## 2. Exact flag rules
| Instruction (with S) | N, Z | C | V |
|---|---|---|---|
| `ADDS a, b` / `CMN` | from result | 1 if the unsigned sum ≥ 2³² (carry out) | 1 if a, b have the same sign and the result's sign differs |
| `ADCS` | from result | carry out of a + b + C | as ADD |
| `SUBS a − b` / `CMP` | from result | **1 if a ≥ b unsigned (no borrow)**, 0 if a borrow happened | 1 if a, b have different signs and the result's sign differs from a |
| `SBCS` | from result | as SUB for a − b − (1 − C) | as SUB |
| `RSBS` / `RSCS` | from result | as SUB with operands swapped (b − a) | as SUB swapped |
| `ANDS ORRS EORS BICS MOVS MVNS TST TEQ` | from result | **shifter carry-out** (see §3); unchanged if nothing was shifted | **unchanged** |
| `MULS` (rare) | from result | unpredictable on ARMv5 – don't rely on it | unchanged |

N = bit 31 of the result, Z = result is 0. Non-S instructions (plain `add`, `mov`, `ldr`…)
never change flags.

## 3. Shifter rules (Operand2)
- `LSL #n`: shift in zeros from the right; carry-out = last bit shifted out (bit 32−n of the source).
- `LSR #n`: zeros from the left; carry-out = bit n−1. `LSR #32` → 0, carry = bit 31.
- `ASR #n`: copies of the sign bit from the left (−5 ASR 2 = −2: rounds toward −∞).
- `ROR #n`: bits leaving on the right re-enter on the left; carry-out = bit n−1.
- `RRX`: result = (C << 31) | (x >> 1); carry-out = old bit 0. **The result depends on the old C flag.**
- Shift by register: only the low byte of the register counts.
- A rotated immediate (e.g. `#0xff000000`) with a logical S instruction sets C = bit 31 of the
  constant; a small immediate (`#0x0f`) leaves C unchanged.
Verified with r1 = 0x80000001:
```
movs r0, r1, lsl #1      r0 = 0x00000002   NZCV 0010   (bit 31 shifted out -> C=1)
movs r2, r1, lsr #1      r2 = 0x40000000   NZCV 0010   (bit 0 shifted out -> C=1)
movs r3, r1              r3 = 0x80000001   NZCV 1010   (no shift: C unchanged)
ands r4, r1, #0xff000000 r4 = 0x80000000   NZCV 1010   (rotated immediate: C = bit 31 of it = 1)
ands r5, r1, #0x0f       r5 = 0x00000001   NZCV 0010   (C unchanged)
adds r6, r1, r1          r6 = 0x00000002   NZCV 0011   (carry out and signed overflow)
cmp  r1, #1                                NZCV 1010   (0x80000000, no borrow)
cmp  r5, r1                                NZCV 1001   (1 < 0x80000001: borrow -> C=0; overflow -> V=1)
teq  r1, r1                                NZCV 0101   (zero; C and V unchanged)
```

## 4. Verified worked examples
**Flag-setting sequence** — start NZCV = 0000:
| Line | Result | NZCV | Why |
|---|---|---|---|
| `subs r0, r0` | r0 = 0 | 0110 | 0 − 0: zero, no borrow |
| `mov r1, #1` / `mov r2, #2` | | 0110 | no S |
| `subs r3, r1, r2` | −1 (0xFFFFFFFF) | 1000 | 1 − 2 borrows → C=0 |
| `sbcs r4, r1, r2` | −2 | 1000 | 1 − 2 − (1 − C) with C=0 → −2 |
| `rsbs r5, r1, r2` | 1 | 0010 | 2 − 1, no borrow |
| `rscs r6, r1, r2` | 1 | 0010 | 2 − 1 − (1 − 1) |
| `adds r7, r3, r4` | −3 | 1010 | 0xFFFFFFFF + 0xFFFFFFFE carries out |
| `adcs r8, r3, r4` | −2 | 1010 | −1 + −2 + C(1) |
| `mov r9, r3, LSR #1` | 0x7FFFFFFF | 1010 | no S |
| `adds r10, r9, r9` | −2 (0xFFFFFFFE) | 1001 | max positive + max positive: signed overflow, no carry |

**Arithmetic and shifts**:
`mov r2,#2` → 2; `mvn r4,#3` → −4; `sub r5,r4,#1` → −5; `rsb r6,r4,r2` → 2 − (−4) = 6;
`add r7,r5,r6,LSL #1` → −5 + 12 = 7; `mov r8,r5,ASR r2` → −2; `mov r9,r5,LSR r2` → 0x3FFFFFFE
(1073741822); `mov r10,r6,RRX` → 3 if C=0, **0x80000003** if C=1;
`mov r11,r6,ROR #1` → 3.

**Loops**: `for (i = 0; i <= 16; i++) s += i` → r0 = 136, r1 = 17, NZCV 0010
(last `cmp 17, 16`). `while (--i) s += i` with i = 17 using `subs r1,r1,#1` / `bhi loop` →
r0 = 136 (16+…+1), r1 = 0, NZCV 0110.

**Addressing modes** with `data: .word 0xbeef0000, …01, …02, …03`, r0 = data, r1 = 4:
- pre-indexed: `ldr r2,[r0,#4]!` → …01 (r0 = data+4); `ldr r3,[r0,r1,LSL #1]!` → …03 (r0 = data+12);
  `ldr r4,[r0,-r1]!` → …02 (r0 = data+8)
- post-indexed: `ldr r2,[r0],#4` → …00; `ldr r3,[r0],r1,LSL #1` → …01; `ldr r4,[r0],-r1` → …03;
  `ldr r5,[r0]` → …02
- offset (no write-back), r1 = 8: `[r0]` → …00, `[r0,#4]` → …01, `[r0,r1]` → …02, `[r0,r1,LSL #1]` → …04

**Decimal string parsing** on `"1234x"`: r1 = 1234 (each step `r1 = r1*5`, then
`r1 = 2*r1 + digit`); loop stops at `'x'` with r2 = 'x' − '0' = 72.

## 5. Traps
- **Pre- vs post-indexed**: `[r0, #4]!` uses r0+4 and updates r0; `[r0], #4` uses r0 *then* adds 4.
  Plain `[r0, #4]` never changes r0.
- `LDM/STM` order is by register number, not by how the list is written.
- Two-operand forms: `subs r2, #'0` means `subs r2, r2, #48`; `add r0, #seventeen`.
- `MVN` is NOT, not negation: `mvn r0, #0` → −1, `mvn r0, #5` → −6.
- `ASR` vs `LSR` on negative numbers; `RRX`/`ADC`/`SBC`/`RSC` depend on the **incoming** C.
- `ldr r0, label` loads the word at `label`; `ldr r0, =label` and `adr r0, label` load the address.
- PC reads as the current instruction + 8: `add pc, pc, #4` at address X jumps to X + 12, skipping the next two instructions.
- Conditional instruction with a false condition: no register change, no flag change.
- Compares never write registers; instructions without `S` never write flags.
- `bhi`/`blo`… are **unsigned**, `bgt`/`blt`… **signed**: after `cmp r0, #16` with r0 = −1,
  `bhi` is taken (0xFFFFFFFF > 16 unsigned) but `bgt` is not.
- `subs` + `bhi` loop: continues while the new value is non-zero (C=1, Z=0); with the counter
  starting at 0 it wraps to 0xFFFFFFFF and C=0 → stops immediately.
- Aliases name the same register: `ip` = r12, `fp` = r11, `sl` = r10, `sb` = r9 (writing `ip` changes r12).
- Byte data: `ldrb` zero-extends, `ldrsb` sign-extends (0xFF → 255 vs −1).

## 6. Checking with trace.py
```
python3 scripts/trace.py fragment.s                       # line-by-line changes + final table
python3 scripts/trace.py -e 'mvn r4, #3
                             sub r5, r4, #1'
python3 scripts/trace.py fragment.s --init r0=20 --nzcv 0010   # given initial values / carry
python3 scripts/trace.py prog_with_data.s --mem data:4         # also dump memory
```
Taken branches print no row (execution continues at the next printed line). Fragments that read PC
arithmetically are run in final-state-only mode automatically.

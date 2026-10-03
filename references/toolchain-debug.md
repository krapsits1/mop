# Toolchain, make, emulator, debugger, testing

## Contents
1. Build pipeline and commands
2. Makefiles
3. Running in qemu-arm
4. Debugging with gdb
5. gdb commands for assembly
6. Inspecting object files
7. Crash diagnosis
8. Testing and delivery checklist

## 1. Build pipeline and commands
| Stage | Command | Result |
|---|---|---|
| compile C | `arm-linux-gnueabi-gcc -g -mcpu=xscale -O0 -Wall -c f_main.c -o f_main.o` | object file |
| C → assembly (to study) | `arm-linux-gnueabi-gcc -mcpu=xscale -O1 -S f.c -o f.c.s` | `.s` text |
| assemble | `arm-linux-gnueabi-as -g -mcpu=xscale -alh=f.lis -L f.s -o f.o` | object + listing |
| link | `arm-linux-gnueabi-gcc -g -o f f_main.o f.o` | executable |
| run | `qemu-arm -L /usr/arm-linux-gnueabi ./f 10` | |
- Link with gcc, not `ld` directly: gcc adds the C start-up files and libraries.
- `as` flags: `-g` debug info (gdb steps through `.s` lines), `-alh=f.lis` listing with addresses,
  encodings and macro expansions, `-L` keeps `.L` symbols visible to gdb.
- The linker merges same-named sections (`.text`, `.data`, `.bss`) and relocates addresses;
  at run time the dynamic loader resolves shared libraries (`LD_LIBRARY_PATH`, `LD_PRELOAD`).

## 2. Makefiles
`assets/Makefile.template` (filled in by `new_project.py`):
`make` builds · `make test ARGS="10" IN=input.txt` builds and runs in qemu ·
`make debug` waits for gdb on port uid + 20000 · `make foo.c.s` shows gcc's assembly for `foo.c` ·
`make clean`.
Anatomy: `target: prerequisites` + TAB-indented recipe; `:=` immediate vs `=` deferred variables;
`$@` target, `$<` first prerequisite, `$^` all prerequisites, `$*` pattern stem; `%.o: %.s` pattern
rules; `.PHONY` for non-file targets; the first target is the default.
Avoid a `%.s: %.c` rule: if `x.c` ever appears next to a hand-written `x.s`, make regenerates
`x.s` from it and destroys your code. The template names that output `x.c.s` instead.
| Message | Cause |
|---|---|
| `missing separator` | recipe line indented with spaces instead of a TAB |
| `No rule to make target 'x.o'` | source missing or misspelled (case-sensitive) |
| `undefined reference to 'f'` | `.global f` missing, label misspelled, object not linked |
| `multiple definition of 'x'` | defined in two files, or a `.c` `#include`d into another |
| `missing .note.GNU-stack section…` | harmless warning; add `.section .note.GNU-stack,"",%progbits` |

## 3. Running in qemu-arm
`qemu-arm` emulates ARM user-mode programs on x86; `-L /usr/arm-linux-gnueabi` points to the ARM
libc and loader. Redirection and exit codes work normally: `qemu-arm -L … ./f < in.txt > out.txt; echo $?`.

## 4. Debugging with gdb
Use `gdb-multiarch` (or `arm-linux-gnueabi-gdb`). On shared machines pick a unique port, e.g.
`$(( $(id -u) + 20000 ))`.
```
A$ qemu-arm -L /usr/arm-linux-gnueabi -g 21234 ./f 10       # waits for gdb; program output appears here
B$ gdb-multiarch ./f
(gdb) set sysroot /usr/arm-linux-gnueabi
(gdb) target remote localhost:21234
(gdb) break f
(gdb) continue                                              # not 'run' with a remote target
```
One terminal: start qemu with `&`, then gdb. GUI: `ddd --debugger gdb-multiarch ./f`
(over SSH use `ssh -X`). Helper function for `~/.bashrc`:
```
debug() {
    port=$(( $(id -u) + 20000 ))
    qemu-arm -L /usr/arm-linux-gnueabi -g $port "$@" &      # program output shares this terminal
    gdb-multiarch -ex "set sysroot /usr/arm-linux-gnueabi" \
                  -ex "target remote localhost:$port" "$1"
}
```
Usage: `debug ./f 10`.

## 5. gdb commands for assembly (verified with qemu-arm)
| Command | Use |
|---|---|
| `break f` / `break f.s:25` / `break *0x10510` | stop at function, line, address |
| `continue` (`c`) | run to the next breakpoint |
| `stepi` / `nexti` (`si` / `ni`) | one instruction (`ni` steps over `bl`) |
| `step` / `next` (`s` / `n`) | one source line |
| `info registers` / `i r r0 r4 cpsr` | registers |
| `p/x $r0`, `p/t $r1`, `p (int)$r2` | one register as hex / binary / signed |
| `x/8wx $sp`, `x/4dw $r2`, `x/s $r0`, `x/16xb &buf` | memory (count, format, size b/h/w) |
| `display/i $pc` | show the next instruction after every step |
| `disassemble f` | machine code of a function |
| `set $r0 = 5`, `set {int}0x21000 = 7` | change registers / memory |
| `bt`, `info line *$pc`, `info breakpoints`, `delete` | context, breakpoints |
| `layout asm`, `layout regs` | TUI views (interactive only) |
CPSR: the top hex digit is NZCV (`0x20000010` → C=1, user mode).
Method for a wrong result: break at the function, `display/i $pc`, `si` repeatedly with
`i r r0 r1 r2 r3 r4`, and compare against the values worked out by hand — the first mismatch is the bug.

## 6. Inspecting object files
- `arm-linux-gnueabi-objdump -d f.o` — what the assembler really generated (pseudo-ops, literal pools).
- `arm-linux-gnueabi-nm f.o` — `T` global code, `t` local, `U` undefined, `D/d` data, `B/b` bss, `C` common.
- `arm-linux-gnueabi-readelf -S` / `-s` — sections / symbols; `size f` — section sizes.

## 7. Crash diagnosis
| Symptom | Likely cause |
|---|---|
| SIGSEGV (exit 139) | bad pointer (`ldr r0, label` vs `ldr r0, =label`), wrong offset, corrupted SP/LR, unbalanced push/pop |
| SIGBUS (135) | misaligned `LDR/STR/LDRH` — missing `.align`, LDR on byte data |
| SIGILL (132) | jumped into data, corrupted PC, instruction not on XScale (built without `-mcpu=xscale`) |
| hangs | loop condition never false: wrong signed/unsigned condition, counter not updated, missing `S` |
| crash after your function returns / inside printf | r4–r11 not restored, LR overwritten by `bl`, SP not restored or misaligned |
| works on one machine, fails on another | unsaved registers, uninitialised registers/memory, alignment, tool-version differences |

## 8. Testing and delivery checklist
Test cases to always consider: 0 and 1; the largest valid input and the first invalid one;
results crossing 2³¹ (break signed conditions and `%d`) and 2³² (unsigned overflow — check C after
`adds`); negative command-line arguments (`strtoul("-1")` = 4294967295); malformed or truncated
input; every error path with its required exit code. Compute expected values with a script, not
by hand. Run them with `qtest.py` (exact stdout, `--tokens` for whitespace-insensitive matching).
Before delivering:
- [ ] exact file/directory names and prototypes from the task
- [ ] `make clean && make` without warnings, on the machine where it will be judged
- [ ] `check_asm.py` 0 errors, warnings understood
- [ ] all tests pass; stdout contains only the required output
- [ ] no leftover debug output or hard-coded test values; code commented

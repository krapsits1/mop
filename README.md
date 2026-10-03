# mop

A Claude skill for 32-bit ARM assembly on ARMv5TE / Intel XScale: writing, reviewing, debugging and testing `.s` files, C/assembly interop under the ARM EABI, and register/NZCV tracing.

## Install

Copy this directory to `~/.claude/skills/mop/`. The skill triggers on ARM assembly topics, or invoke it with `/mop`.

Toolchain:

```sh
apt-get install gcc-arm-linux-gnueabi linux-libc-dev-armel-cross qemu-user gdb-multiarch
```

## Contents

- `SKILL.md`: rules, workflow and style
- `scripts/`: project scaffolding (`new_project.py`), static checks (`check_asm.py`), ABI layout (`abi_layout.py`), tracing (`trace.py`), tests (`qtest.py`)
- `references/`: ISA, ABI, directives/macros, toolchain/debugging, tracing
- `assets/Makefile.template`: Makefile used by `new_project.py`

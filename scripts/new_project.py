#!/usr/bin/env python3
"""
new_project.py - scaffold an ARM assembly + C project: Makefile, header with the
prototypes, an assembly skeleton per function (prologue/epilogue + where every
argument lives), and a C driver skeleton.

  new_project.py NAME "PROTOTYPE" ["PROTOTYPE" ...] [options]

  new_project.py sum "unsigned int sum_to(unsigned int n)" --args 10
  new_project.py mat "int mat_add(int h, int w, int *a, int *b, int *c)" --stdin input.txt
  new_project.py gfx "void plot(int x, int y)" "void fill(int x1, int y1, int x2, int y2)" \
                 --calls --extra screen

Files: NAME.h, NAME.s, NAME_main.c, Makefile (+ one EXTRA.c per --extra module).
Existing files are never overwritten unless --force.

Options
  --dir DIR      output directory (default: NAME)
  --calls        the functions call other functions: save LR and keep SP 8-byte aligned
  --extra a,b    additional C modules (a.c, b.c) linked into the program
  --args "..."   default arguments for 'make test'
  --stdin FILE   default stdin file for 'make test'
"""
import argparse
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from abi_layout import describe  # noqa: E402

GNU_STACK = '\n\t.section .note.GNU-stack,"",%progbits\n'


def func_name(proto):
    m = re.match(r"^\s*.+?\b([A-Za-z_]\w*)\s*\(", proto)
    if not m:
        sys.exit(f"cannot parse prototype: {proto!r}")
    return m.group(1)


def makefile(target, objs, args, stdin):
    tpl = open(os.path.join(HERE, "..", "assets", "Makefile.template")).read()
    return (tpl.replace("@TARGET@", target).replace("@OBJ@", " ".join(objs))
               .replace("@ARGS@", args).replace("@IN@", stdin))


def asm_function(proto, calls):
    name = func_name(proto)
    sub = 4 if calls else 0
    abi = "\n".join("@ " + l for l in describe(proto, 9, sub).split("\n"))
    pro = "\tstmfd\tsp!, {r4-r11, lr}\t@ save callee-saved regs + LR (trim to what you use)\n"
    epi = "\tldmfd\tsp!, {r4-r11, pc}\t@ restore and return\n"
    if calls:
        pro += "\tsub\tsp, sp, #4\t\t@ 36 + 4 bytes: SP 8-byte aligned for calls\n"
        epi = "\tadd\tsp, sp, #4\n" + epi
    return f"""
@ {proto};
{abi}
@ registers:
@   r4 -
	.global	{name}
	.type	{name}, %function
{name}:
{pro}
	@ TODO

{epi}	.size	{name}, .-{name}
"""


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("name")
    ap.add_argument("prototypes", nargs="+")
    ap.add_argument("--dir")
    ap.add_argument("--calls", action="store_true")
    ap.add_argument("--extra", default="")
    ap.add_argument("--args", default="")
    ap.add_argument("--stdin", default="")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()

    protos = [p.strip().rstrip(";") for p in a.prototypes]
    extras = [e.strip() for e in a.extra.split(",") if e.strip()]
    n = a.name
    guard = re.sub(r"\W", "_", n.upper()) + "_H"
    files = {
        "Makefile": makefile(n, [f"{n}_main.o", f"{n}.o"] + [f"{e}.o" for e in extras], a.args, a.stdin),
        f"{n}.h": f"#ifndef {guard}\n#define {guard}\n\n" + "".join(p + ";\n" for p in protos) + f"\n#endif\n",
        f"{n}.s": f"@ {n}.s\n\t.text\n\t.align\t2\n" + "".join(asm_function(p, a.calls) for p in protos) + GNU_STACK,
        f"{n}_main.c": (f'#include <stdio.h>\n#include <stdlib.h>\n#include "{n}.h"\n\n'
                        "int main(int argc, char **argv)\n{\n"
                        "    /* TODO: validate and convert input (strtoul for unsigned values),\n"
                        f"     *       call {', '.join(func_name(p) for p in protos)}, print only the required output */\n"
                        "    return 0;\n}\n"),
    }
    for e in extras:
        files[f"{e}.c"] = f'#include "{n}.h"\n\n/* TODO */\n'

    d = a.dir or n
    os.makedirs(d, exist_ok=True)
    for fn, content in files.items():
        p = os.path.join(d, fn)
        if os.path.exists(p) and not a.force:
            print(f"skip   {p} (exists; --force to overwrite)")
            continue
        open(p, "w").write(content)
        print(f"wrote  {p}")
    print(f"\nNext: cd {d} && make")


if __name__ == "__main__":
    main()

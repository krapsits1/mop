#!/usr/bin/env python3
"""
qtest.py - run an ARM program under qemu-arm against test cases and compare
stdout (exactly, by default) and the exit code.

Exact comparison catches extra text such as "sum=17" instead of "17".

Ways to give test cases
  -c "ARGS=EXPECTED"        quick command-line cases
        qtest.py ./prog -c "10=55" -c "0=0"
  --dir DIR                 DIR/NAME.in (stdin), DIR/NAME.out (expected stdout),
                            optional DIR/NAME.args (one line) and DIR/NAME.exit
        qtest.py ./prog --dir tests
  --json FILE               [{"name": "...", "args": ["10"], "stdin": "",
                              "stdout": "55\\n", "exit": 0}, ...]

Options
  --tokens     compare whitespace-separated tokens instead of exact text
               (when only the numbers matter, not the spacing)
  --native     run the binary directly instead of through qemu-arm
  -v           show stdout of passing tests too

Exit status: number of failed tests (0 = all passed).
"""
import argparse
import glob
import json
import os
import shlex
import shutil
import subprocess
import sys


def load_cases(a):
    cases = []
    for c in a.case:
        if "=" not in c:
            sys.exit(f"-c expects ARGS=EXPECTED, got {c!r}")
        args, exp = c.split("=", 1)
        cases.append({"name": f"args[{args}]", "args": shlex.split(args),
                      "stdin": "", "stdout": exp + "\n", "exit": None})
    if a.dir:
        for inp in sorted(glob.glob(os.path.join(a.dir, "*.in"))):
            base = inp[:-3]
            case = {"name": os.path.basename(base), "args": [], "stdin": open(inp).read(),
                    "stdout": None, "exit": None}
            if os.path.exists(base + ".out"):
                case["stdout"] = open(base + ".out").read()
            if os.path.exists(base + ".args"):
                case["args"] = shlex.split(open(base + ".args").read().strip())
            if os.path.exists(base + ".exit"):
                case["exit"] = int(open(base + ".exit").read().strip())
            cases.append(case)
    if a.json:
        for i, c in enumerate(json.load(open(a.json))):
            cases.append({"name": c.get("name", f"json#{i}"), "args": [str(x) for x in c.get("args", [])],
                          "stdin": c.get("stdin", ""), "stdout": c.get("stdout"), "exit": c.get("exit")})
    if not cases:
        sys.exit("no test cases given (use -c, --dir or --json)")
    return cases


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("program")
    ap.add_argument("-c", "--case", action="append", default=[])
    ap.add_argument("--dir")
    ap.add_argument("--json")
    ap.add_argument("--tokens", action="store_true")
    ap.add_argument("--native", action="store_true")
    ap.add_argument("--timeout", type=float, default=10)
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args()

    prog = a.program if os.path.dirname(a.program) else "./" + a.program
    if not os.path.exists(prog):
        sys.exit(f"{prog} does not exist - run make first")
    if a.native:
        runner = []
    else:
        q = shutil.which("qemu-arm")
        if not q:
            sys.exit("qemu-arm not found (sudo apt-get install qemu-user)")
        runner = [q] + (["-L", "/usr/arm-linux-gnueabi"] if os.path.isdir("/usr/arm-linux-gnueabi") else [])

    failed = 0
    cases = load_cases(a)
    for c in cases:
        try:
            r = subprocess.run(runner + [prog] + c["args"], input=c["stdin"], capture_output=True,
                               text=True, timeout=a.timeout)
            out, code, err = r.stdout, r.returncode, r.stderr
        except subprocess.TimeoutExpired:
            out, code, err = "", None, f"TIMEOUT after {a.timeout}s"
        ok, why = True, []
        if code is None:
            ok = False; why.append(err)
        if c["stdout"] is not None:
            same = (out.split() == c["stdout"].split()) if a.tokens else (out == c["stdout"])
            if not same:
                ok = False; why.append(f"stdout expected {c['stdout']!r}\n           got      {out!r}")
        if c["exit"] is not None and code != c["exit"]:
            ok = False; why.append(f"exit code expected {c['exit']}, got {code}")
        if code is not None and code < 0:
            ok = False; why.append(f"killed by signal {-code} (SIGSEGV=11 bad pointer/stack, SIGBUS=7 unaligned, "
                                    "SIGILL=4 instruction not on this CPU)")
        status = "PASS" if ok else "FAIL"
        failed += not ok
        print(f"[{status}] {c['name']}")
        for w in why:
            print("       " + w)
        if (not ok or a.verbose) and err and code is not None:
            print("       stderr: " + err.strip().replace("\n", "\n               "))
        if a.verbose and ok:
            print("       stdout: " + repr(out))
    print(f"\n{len(cases) - failed}/{len(cases)} passed")
    sys.exit(min(failed, 125))


if __name__ == "__main__":
    main()

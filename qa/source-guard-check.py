"""Tests for the source guard (qa/check-sources.py) and repair (qa/fix_docstrings.py).

These are the checks that make the guard trustworthy: rather than asserting the
scripts "look right", this drives them against real damaged input in a temp
directory and asserts the outcome, including the exit codes a pre-commit hook
depends on.

Run from the repo root:  python qa/source-guard-check.py
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = sys.executable
CHECK = ROOT / "qa" / "check-sources.py"
REPAIR = ROOT / "qa" / "fix_docstrings.py"

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

Q = '"'
Q3 = '"' * 3

# A file carrying both failure modes the guard exists to catch:
#   - a collapsed docstring opener and closer (two quotes instead of three)
#   - that collapse is itself a syntax error, so ast.parse must fail too
DAMAGED = (
    "def f():\n"
    '    ""Collapsed opener docstring.\n'
    "    More text.\n"
    '    ""\n'
    "    return 1\n"
)

# A lowercase opener. An earlier, narrower OPENER regex required a capital or
# '(', so this form was detected but could not be repaired. It is kept as a
# regression case: detection alone is not enough, the repair must fix it.
LOWERCASE_DAMAGED = (
    "def g():\n"
    '    ""lowercase opener.\n'
    "    body\n"
    '    ""\n'
    "    return 2\n"
)

HEALTHY = (
    "def f():\n"
    f'    {Q3}A properly quoted docstring.{Q3}\n'
    "    return 1\n"
)

# A closing delimiter collapsed all the way to ONE quote (two quotes lost, not
# one). This is the case a bare-two-quote rule misses entirely: the module
# docstring is simply never terminated. It is not hypothetical -- it is what
# happened to qa/livescore-check.py, where the checker stayed silent on the
# delimiter, the repairer was a no-op, and the file only failed because
# ast.parse caught it as a syntax error.
TERMINATOR_DAMAGED = (
    f'{Q3}Module docstring.\n'
    "\n"
    "Body text.\n"
    f'{Q}\n'
    "import json\n"
)


def run(script: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [PY, str(script), *args],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
    )


def main() -> int:
    failures: list[str] = []
    total = 0

    def check(name: str, condition: bool, detail: str = "") -> None:
        nonlocal total
        total += 1
        if condition:
            print(f"  PASS  {name}")
        else:
            print(f"  FAIL  {name}  {detail}")
            failures.append(name)

    workdir = Path(tempfile.mkdtemp(prefix="ge-guard-"))
    try:
        # --- 1. the real repo is clean -----------------------------------
        print("=== repo is clean ===")
        r = run(CHECK)
        check("check-sources exits 0 on a clean tree", r.returncode == 0, r.stdout.strip())

        # --- 2. damage is detected --------------------------------------
        print("\n=== collapsed docstrings are detected ===")
        target_dir = workdir / "app"
        target_dir.mkdir(parents=True)
        damaged = target_dir / "damaged.py"
        damaged.write_text(DAMAGED, encoding="utf-8")

        r = run(CHECK, "--root", str(target_dir))
        check("check-sources exits 1 on damage", r.returncode == 1, f"rc={r.returncode}")
        check(
            "damage is reported with the file name",
            "damaged.py" in r.stdout,
            r.stdout.strip(),
        )
        check(
            "a repair hint is printed",
            "fix_docstrings" in r.stdout,
            r.stdout.strip(),
        )

        # --- 3. check mode does not modify anything ---------------------
        print("\n=== --check never writes ===")
        before = damaged.read_text(encoding="utf-8")
        check("check mode left the file byte-identical", damaged.read_text(encoding="utf-8") == before)

        # --- 4. repair fixes it and the repair parses -------------------
        print("\n=== repair fixes the file ===")
        r = run(REPAIR, "--root", str(target_dir))
        check("fix_docstrings exits 0", r.returncode == 0, f"rc={r.returncode}")

        repaired = damaged.read_text(encoding="utf-8")
        check("no collapsed delimiters remain", '    ""\n' not in repaired, repr(repaired))
        check("opener is three quotes", Q3 + "Collapsed opener" in repaired, repr(repaired))

        import ast

        try:
            ast.parse(repaired)
            parses = True
        except SyntaxError:
            parses = False
        check("repaired file parses", parses)

        # --- 5. the guard now passes on the repaired tree ---------------
        print("\n=== repaired tree passes the guard ===")
        r = run(CHECK, "--root", str(target_dir))
        check("check-sources exits 0 after repair", r.returncode == 0, r.stdout.strip())

        # --- 6. repair is idempotent ------------------------------------
        print("\n=== repair is idempotent ===")
        stable = damaged.read_text(encoding="utf-8")
        run(REPAIR, "--root", str(target_dir))
        check(
            "a second repair changes nothing",
            damaged.read_text(encoding="utf-8") == stable,
        )

        # --- 7. healthy code is left alone ------------------------------
        print("\n=== correct code is untouched ===")
        healthy_dir = workdir / "healthy"
        healthy_dir.mkdir()
        healthy_file = healthy_dir / "ok.py"
        healthy_file.write_text(HEALTHY, encoding="utf-8")
        before_ok = healthy_file.read_text(encoding="utf-8")
        r = run(REPAIR, "--root", str(healthy_dir))
        check("repair does not touch good file", healthy_file.read_text(encoding="utf-8") == before_ok)
        r = run(CHECK, "--root", str(healthy_dir))
        check("check passes on good file", r.returncode == 0, r.stdout.strip())

        # --- 8. a lowercase opener is repaired, not just detected -------
        print("\n=== lowercase docstring opener is repaired ===")
        lower_dir = workdir / "lower"
        lower_dir.mkdir()
        lower_file = lower_dir / "lower.py"
        lower_file.write_text(LOWERCASE_DAMAGED, encoding="utf-8")

        r = run(CHECK, "--root", str(lower_dir))
        check("lowercase damage is detected", r.returncode == 1, f"rc={r.returncode}")

        r = run(REPAIR, "--root", str(lower_dir))
        check("repair accepts a lowercase opener", r.returncode == 0, r.stdout.strip())
        check(
            "repair did not refuse the file",
            "refuses to write" not in r.stdout,
            r.stdout.strip(),
        )

        fixed_lower = lower_file.read_text(encoding="utf-8")
        check("lowercase opener is now three quotes", Q3 + "lowercase opener" in fixed_lower)

        r = run(CHECK, "--root", str(lower_dir))
        check("repaired lowercase file passes the guard", r.returncode == 0, r.stdout.strip())

        # --- 9. a generic syntax error (not just docstrings) is caught ---
        print("\n=== generic syntax errors are caught ===")
        syntax_dir = workdir / "syntax"
        syntax_dir.mkdir()
        (syntax_dir / "broken.py").write_text("def f(:\n    pass\n", encoding="utf-8")
        r = run(CHECK, "--root", str(syntax_dir))
        check("check-sources exits 1 on a plain syntax error", r.returncode == 1, f"rc={r.returncode}")
        check("SyntaxError is named in the output", "SyntaxError" in r.stdout, r.stdout.strip())

        # --- 10. a collapsed TERMINATOR is detected AND repaired ---------
        # The regression this pins: the checker reported only a SyntaxError
        # here (never the delimiter), and the repairer did nothing, so the
        # documented recovery command did not recover the file.
        print("\n=== collapsed single-quote terminator is detected and repaired ===")
        term_dir = workdir / "terminator"
        term_dir.mkdir()
        term_file = term_dir / "term.py"
        term_file.write_text(TERMINATOR_DAMAGED, encoding="utf-8")

        r = run(CHECK, "--root", str(term_dir))
        check("checker exits 1 on a collapsed terminator", r.returncode == 1, f"rc={r.returncode}")
        check(
            "the DELIMITER is named, not just the syntax error",
            "collapsed docstring delimiter" in r.stdout,
            r.stdout.strip(),
        )

        r = run(REPAIR, "--root", str(term_dir))
        check("repairer accepts the file", r.returncode == 0, r.stdout.strip())
        check(
            "repairer actually rewrote it (was a no-op before)",
            "repaired 1" in r.stdout,
            r.stdout.strip(),
        )

        fixed_term = term_file.read_text(encoding="utf-8")
        try:
            ast.parse(fixed_term)
            term_parses = True
        except SyntaxError:
            term_parses = False
        check("repaired terminator file parses", term_parses, repr(fixed_term))

        r = run(CHECK, "--root", str(term_dir))
        check("re-check passes after the repair", r.returncode == 0, r.stdout.strip())

    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    print(f"\n{total - len(failures)}/{total} checks passed")
    if failures:
        print("FAILURES:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())

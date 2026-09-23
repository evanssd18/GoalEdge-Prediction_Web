"""Guarded repair for collapsed triple-quote docstrings.

Why this exists
---------------
Editing this project's Python files with the fuzzy-matching editor repeatedly
collapsed `\"` docstring delimiters down to `\"`, which is a silent syntax
error. Rather than hand-writing a one-off patch script on every occurrence
(which is churn, not a fix), this single idempotent script is the one command to
run after edits:

    .\\.venv\\Scripts\\python.exe ..\\qa\\fix_docstrings.py --check   # report only
    .\\.venv\\Scripts\\python.exe ..\\qa\\fix_docstrings.py           # repair

It is safe to run at any time: the definition of "damaged" lives in
:mod:`source_guard` and is shared with ``check-sources.py``, so the two can never
disagree; only lines proven damaged are rewritten; and every file is re-parsed
before it is written, so a repair can never make things worse.
"""
from __future__ import annotations

import argparse
import ast
import io
import os
import sys
from pathlib import Path

# The detector and the repairer must agree on what damage is. They are the same
# code, imported here rather than duplicated.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from source_guard import line_is_damaged, repair_line  # noqa: E402

#: Default directory to scan when run with no --root (relative to the backend).
SCAN_ROOT = "app"


def _is_damaged(lines: list[str]) -> bool:
    return any(line_is_damaged(line) for line in lines)


def _repair(lines: list[str]) -> tuple[list[str], int]:
    changed = 0
    for i, line in enumerate(lines):
        new, did = repair_line(line)
        if did:
            lines[i] = new
            changed += 1
    return lines, changed


def _parses(source: str) -> bool:
    """Whether ``source`` is syntactically valid Python.

    The gate that stops a repair writing a file it just made worse.
    """
    try:
        ast.parse(source)
        return True
    except SyntaxError:
        return False


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="report without writing")
    parser.add_argument("--root", default=SCAN_ROOT, help="directory to scan")
    args = parser.parse_args()

    damaged_files = 0
    repaired_files = 0
    still_broken: list[str] = []

    for root, _dirs, names in os.walk(args.root):
        if "__pycache__" in root or ".venv" in root:
            continue
        for name in sorted(names):
            if not name.endswith(".py"):
                continue
            path = os.path.join(root, name)
            lines = io.open(path, encoding="utf-8").readlines()
            if not _is_damaged(lines):
                continue

            damaged_files += 1
            print(f"damaged: {path}")

            if args.check:
                continue

            fixed, count = _repair(lines)
            candidate = "".join(fixed)
            if not _parses(candidate):
                still_broken.append(path)
                print(f"  refuses to write (still invalid): {path}")
                continue
            io.open(path, "w", encoding="utf-8").write(candidate)
            repaired_files += 1
            print(f"  repaired {count} delimiter(s)")

    if args.check:
        print(f"\nchecked: {damaged_files} damaged file(s) found")
        return 1 if damaged_files else 0

    print(f"\nrepaired: {repaired_files} file(s)")
    if still_broken:
        print("STILL BROKEN:", still_broken)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())

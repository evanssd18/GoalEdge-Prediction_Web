"""Fail the build on any damaged Python source in this repo.

Two failure modes are checked, both of which have actually bitten this project:

1. **Collapsed docstring delimiters.** The fuzzy-matching editor used on this
   codebase silently turns `\"` into `\"`, which is a syntax error. It is easy to
   miss in a large diff, so it is detected explicitly and reported per line.
2. **Any other syntax error.** A plain `ast.parse` over every module we own.

Run from the repo root:

    python qa/check-sources.py

Exit codes:
    0  everything parses and no collapsed delimiters were found
    1  at least one file is damaged

To *repair* the collapsed-delimiter damage rather than just detect it:

    python qa/fix_docstrings.py
"""
from __future__ import annotations
import argparse
import ast
import io
import os
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
# One shared definition of "damaged", so this check and the repair script can
# never disagree about what counts as damage (they once did -- see source_guard).
from source_guard import damaged_lines  # noqa: E402
ROOT = Path(__file__).resolve().parent.parent
# The trees that hold Python this project owns. Vendored code, virtualenvs and
# bytecode caches are excluded so the check reports our mistakes, not theirs.
SCAN_ROOTS = [ROOT / "backend" / "app", ROOT / "qa"]
SKIP_DIRS = {"__pycache__", ".venv", "venv", "node_modules", ".git"}


def iter_python_files(extra_roots: list[Path] | None = None):
    bases = list(extra_roots) if extra_roots else list(SCAN_ROOTS)
    for base in bases:
        if not base.is_dir():
            continue
        for root, dirs, names in os.walk(base):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
            for name in sorted(names):
                if name.endswith(".py"):
                    yield Path(root) / name


def collapsed_delimiters(source: str) -> list[int]:
    """Line numbers carrying a collapsed `\"` docstring delimiter."""
    # Delegates to the shared detector so this checker and fix_docstrings.py
    # can never disagree about what is damaged.
    return damaged_lines(source)


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        action="append",
        default=None,
        help="scan this directory instead of the default project trees (repeatable)",
    )
    args = parser.parse_args()

    extra = [Path(r).resolve() for r in args.root] if args.root else None
    files = list(iter_python_files(extra))
    problems: dict[str, list[str]] = {}

    for path in files:
        try:
            rel = path.relative_to(ROOT).as_posix()
        except ValueError:
            # A caller-supplied root outside the repo (e.g. a temp dir in tests)
            # still needs a stable display name.
            rel = path.as_posix()
        try:
            source = io.open(path, encoding="utf-8").read()
        except OSError as exc:
            problems[rel] = [f"unreadable: {exc}"]
            continue

        issues: list[str] = []

        for line_no in collapsed_delimiters(source):
            issues.append(
                f"line {line_no}: collapsed docstring delimiter '\"\"' "
                f"(run qa/fix_docstrings.py to repair)"
            )

        try:
            ast.parse(source)
        except SyntaxError as exc:
            issues.append(f"line {exc.lineno}: SyntaxError: {exc.msg}")

        if issues:
            problems[rel] = issues

    print(f"scanned {len(files)} Python file(s)")
    if not problems:
        print("OK: all sources parse, no collapsed docstring delimiters")
        return 0

    print(f"\nFAILED: {len(problems)} file(s) with problems\n")
    for rel, issues in sorted(problems.items()):
        print(f"  {rel}")
        for issue in issues:
            print(f"    - {issue}")
    print("\nRepair the collapsed-delimiter cases with: python qa/fix_docstrings.py")
    return 1


if __name__ == "__main__":
    sys.exit(main())

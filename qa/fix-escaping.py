"""Repair docstrings collapsed by the editor's fuzzy matcher.

The editor's whitespace-tolerant replace silently collapses a triple-quoted
delimiter (three quotes) down to two, and treats an escaped quote as a plain
one. Both are syntax errors and both are invisible in a large diff.

The existing qa/fix_docstrings.py covers the first case via source_guard. This
script additionally repairs the *escaped-quote* variant introduced when a
script additionally repairs the collapsed-delimiter variant wherever it appears,
and refuses to write a result that does not parse.

    backend\\.venv\\Scripts\\python.exe qa\\fix-escaping.py <file> [...]
"""
import ast
import re
import sys
from pathlib import Path

#: A line that is just `""` where a docstring delimiter was, i.e. an opening
#: delimiter collapsed to two quotes.
COLLAPSED_OPEN = re.compile(r'^(\s*)""(\S.*)$')
COLLAPSED_CLOSE = re.compile(r'^(\s*)""\s*$')


def repair(text: str) -> tuple[str, int]:
    lines = text.split("\n")
    out: list[str] = []
    fixes = 0
    # Track whether an unterminated collapsed docstring is open, so its closing
    # line can be repaired to match.
    open_at: int | None = None

    for index, line in enumerate(lines):
        stripped = line.strip()

        # An opening delimiter written as `""Something` -- restore to `"""`.
        if open_at is None and stripped.startswith('""') and len(stripped) > 2 and stripped[2] != '"':
            indent = line[: len(line) - len(line.lstrip())]
            out.append(f'{indent}"""{stripped[2:]}')
            open_at = index
            fixes += 1
            continue

        # The matching close: a bare `""` while a docstring is open.
        if open_at is not None and stripped == '""':
            indent = line[: len(line) - len(line.lstrip())]
            # Only close it if the next non-empty line is dedented or blank,
            # which is what a closing delimiter looks like.
            out.append(f'{indent}"""')
            open_at = None
            fixes += 1
            continue

        out.append(line)

    return "\n".join(out), fixes


def main() -> int:
    targets = [Path(a) for a in sys.argv[1:]]
    if not targets:
        targets = list((Path(__file__).resolve().parent.parent / "backend" / "app").glob("*.py"))
        targets += list(Path(__file__).resolve().parent.glob("*.py"))

    failed = 0
    for path in targets:
        try:
            original = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            print(f"skip {path.name}: {exc}")
            continue

        try:
            ast.parse(original)
            continue  # already valid; nothing to do
        except SyntaxError:
            pass

        fixed, count = repair(original)
        if count == 0:
            print(f"UNREPAIRED {path.name}: still invalid, no collapsed delimiter found")
            failed += 1
            continue
        try:
            ast.parse(fixed)
        except SyntaxError as exc:
            print(f"REFUSED {path.name}: repair would not parse ({exc.msg} line {exc.lineno})")
            failed += 1
            continue

        path.write_text(fixed, encoding="utf-8")
        print(f"repaired {path.name}: {count} delimiter(s) restored")

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

"""Repair collapsed docstring delimiters in matchdetail.py.

The editor's fuzzy matcher rewrites `\"` as `\"` on the way in. This does a
byte-level fix with character codes -- no quote literals in the source, so shell
and editor quoting cannot distort it -- and refuses to write unless the module
parses.
"""
from __future__ import annotations

import ast
from pathlib import Path

Q = chr(0x22)  # "
T = Q * 3      # """

path = Path(__file__).resolve().parent.parent / "backend" / "app" / "matchdetail.py"
lines = path.read_text(encoding="utf-8").split("\n")

# A damaged opener/closer is a line whose stripped text starts or ends with
# exactly two quotes where three are needed: `\"Text...` or `...text\"`.
fixed_lines: list[str] = []
changed: list[int] = []
for index, line in enumerate(lines):
    stripped = line.strip()
    # `\"Some docstring text` -- opener missing one quote. Must not match a
    # legitimate empty string like `\"\",` (that is two quotes then a comma, so
    # it does not start with two quotes followed by a letter).
    if stripped.startswith(Q * 2) and len(stripped) > 2 and stripped[2] not in (Q, ",", ")", "]"):
        indent = line[: len(line) - len(line.lstrip())]
        fixed_lines.append(indent + T + stripped[2:])
        changed.append(index + 1)
        continue
    # `...docstring text\"` -- closer missing one quote.
    if stripped.endswith(Q * 2) and not stripped.endswith(T) and len(stripped) > 2:
        fixed_lines.append(line[: -len(Q * 2)] + T)
        changed.append(index + 1)
        continue
    fixed_lines.append(line)

if not changed:
    raise SystemExit("no collapsed delimiters found; nothing to do")

repaired = "\n".join(fixed_lines)
try:
    ast.parse(repaired)
except SyntaxError as exc:
    raise SystemExit(f"REFUSING TO WRITE -- still does not parse: {exc}") from exc

path.write_text(repaired, encoding="utf-8", newline="")
print("repaired lines:", changed)
print("module parses")

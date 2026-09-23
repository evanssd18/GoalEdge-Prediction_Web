"""Unit checks for the shared damage detector in :mod:`source_guard`.

The detector's whole job is to fire on collapsed docstring delimiters and on
NOTHING else. A false positive silently rewrites working code, which is how a
bad repair once corrupted 12 files, so each case below is pinned explicitly --
including the empty-string element (``"",``) that caused that false positive.

Run from the repo root:  python qa/source-guard-units.py
"""
from __future__ import annotations
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from source_guard import (  # noqa: E402
    is_collapsed_closer,
    is_collapsed_opener,
    line_is_damaged,
    repair_line,
)

Q = '"'
CASES = [
    # (line, expect_damaged, why)
    ("        " + Q + Q + ",", False, "empty string list element with comma"),
    ("        " + Q + Q, True, "bare two quotes: collapsed closer"),
    ("    " + Q + Q + "Text", True, "collapsed opener, capital"),
    ("    " + Q + Q + "lowercase", True, "collapsed opener, lowercase"),
    ("    " + Q * 3 + "Text", False, "valid three-quote opener"),
    ("    " + Q * 3, False, "valid empty docstring"),
    ("    x = " + Q + Q, False, "empty string assignment"),
    ('    ""' + " + y", False, "empty string in expression"),
    ("    " + Q + Q + " + y", False, "empty string plus"),
    ("    " + Q, True, "lone quote: collapsed TERMINATOR (top-level)"),
    (Q, True, "lone quote with no indent"),
    ("        " + Q, True, "lone quote indented: nested closer collapsed"),
    ("", False, "blank line"),
    ("    return 1", False, "ordinary code"),
]

failures = []
for line, expected, why in CASES:
    got = line_is_damaged(line)
    ok = got == expected
    marker = "PASS" if ok else "FAIL"
    print(f"  {marker}  damaged={got!s:5} expected={expected!s:5}  {why!r}  {line!r}")
    if not ok:
        failures.append(why)

print()
print("opener-only check on the empty-string literal:")
print("  is_collapsed_opener('    x = \"\"') ->", is_collapsed_opener('    x = ""'))
print("  is_collapsed_closer('        \"\",') ->", is_collapsed_closer('        "",'))

if failures:
    print("\nFAILURES:", failures)
    sys.exit(1)
print("\nALL PASS")
sys.exit(0)

"""Single definition of "damaged source" shared by the check and the repair.

Why this module exists
----------------------
The detector (``check-sources.py``) and the repairer (``fix_docstrings.py``)
originally each carried their own copy of the docstring pattern. They drifted,
and the two disagreed about what counted as damage -- which is how a bad repair
once corrupted 12 files. One definition, imported by both, makes that class of
bug impossible: there is nothing left to drift.

What counts as damage
---------------------
* **Collapsed opener** -- exactly two quotes followed by non-quote text.
  Written out with `Q` standing in for a double quote, so this docstring does
  not itself contain a damaged-looking line:

      Q Some docstring.   <- damaged (only two quotes)
      Q Some docstring. <- correct (three quotes)

  The lookahead ``(?=[^\\s\"])`` requires a third character that is neither
  whitespace nor a quote. That is precisely what separates a damaged opener from
  a *valid* one and from an empty string literal (``x = ""``), both of which
  must be left alone. An earlier pattern looked on the wrong side of the quote
  pair and matched either valid code or nothing at all -- see
  ``qa/source-guard-check.py``, which pins every one of these cases.
* **Collapsed closer** -- a line whose entire content is two quotes. A line
  that is exactly ``\"`` (three) or ``\"`` (six) is valid.
* **Collapsed terminator** -- a line whose entire content is ONE quote. The
  same accident one step further along: the closing delimiter lost two of its
  three quotes, so the docstring is never terminated. A rule matching only a
  bare two-quote line misses it entirely. This is the case that broke
  ``qa/livescore-check.py``, where the checker reported only the resulting
  ``SyntaxError`` (never the delimiter) and the repairer was a no-op -- so the
  recovery command it printed did not actually recover the file. A lone quote
  is never valid Python outside a string, so the rule cannot fire on working
  code.
"""
from __future__ import annotations

import re

Q = '"'
Q3 = '"' * 3
Q2 = '"' * 2

#: A *candidate* opener: two quotes, then a character that is neither
#: whitespace nor a quote. This alone is not enough to call something damage --
#: `        "",` also matches, and that is a legitimate empty string element.
OPENER = re.compile(r'^(\s*)""(?=[^\s"])')

#: Matches a line that is already a complete, VALID double-quoted string:
#: an opening quote, a body, a closing quote, then only a trailing comma,
#: bracket or comment. Such a line is real code, never a lost docstring quote.
VALID_QUOTED_LINE = re.compile(r'^\s*"[^"\\]*(?:\\.[^"\\]*)*"\s*,?\s*(?:#.*)?$')


def is_collapsed_opener(line: str) -> bool:
    """True only when two quotes genuinely open a docstring.

    The candidate pattern also fires on `"",` -- an empty string element in a
    list, which is valid code. Rewriting that as damage corrupts working source,
    so any line that is already a complete quoted string is rejected first.
    """
    if VALID_QUOTED_LINE.match(line.rstrip("\n")):
        return False
    return bool(OPENER.match(line))


def is_collapsed_closer(line: str) -> bool:
    """A bare ``""`` line is always a collapsed closing delimiter.

    an empty docstring, and every other all-quote line is excluded
    excluded because they are valid Python, not damage.
    """
    return line.strip() == Q2


def is_collapsed_terminator(line: str) -> bool:
    """A line whose entire content is ONE quote.

    The same accident as a collapsed closer, one quote further along: the
    closing delimiter lost two of its three quotes, so the docstring is
    never terminated.

    A rule matching only a bare ``""`` misses it -- which is what happened
    to qa/livescore-check.py, where the detector stayed silent, the repairer
    was a no-op, and only the ast.parse gate noticed the breakage.

    A lone quote is never valid Python outside a string, so this cannot fire
    on working code.
    """
    return line.strip() == Q


def line_is_damaged(line: str) -> bool:
    return (
        is_collapsed_opener(line)
        or is_collapsed_closer(line)
        or is_collapsed_terminator(line)
    )


def damaged_lines(source: str) -> list[int]:
    """1-based line numbers carrying a collapsed delimiter."""
    return [
        n
        for n, line in enumerate(source.splitlines(), start=1)
        if line_is_damaged(line)
    ]


def repair_line(line: str) -> tuple[str, bool]:
    """Fix one line if it is damaged. Returns (line, changed)."""
    if is_collapsed_closer(line) or is_collapsed_terminator(line):
        indent = line[: len(line) - len(line.lstrip())]
        suffix = "\n" if line.endswith("\n") else ""
        return indent + Q3 + suffix, True
    if is_collapsed_opener(line):
        m = OPENER.match(line)
        assert m is not None
        return line[: m.end()] + '"' + line[m.end():], True
    return line, False

"""Market taxonomy: the category list a bookmaker publishes.

Bookmakers do not expose a neat list of "markets the engine prices". They expose
dozens of categories — ``1X2 - 1UP``, ``Asian Over/Under``, ``GG/NG 2+``,
``Any Team To Score 2 or More Goals in a Row`` — and the set varies by book and
by match.

This module is the single place that answers three questions:

1. Which category does a raw market name belong to?  (:func:`categorise`)
2. Can GoalEdge's engine price that category at all?  (:func:`is_model_priced`)
3. What is the canonical display order?              (:data:`CATEGORIES`)

Question 2 is the important one. The engine derives **1X2, Over/Under and BTTS**
from its Dixon-Coles scoreline matrix. Everything else a book offers is a
re-expression of that same matrix — a handicap and a 1X2 price carry identical
information once de-vigged — so those categories are shown with their prices and
**no model claim**. Marking them otherwise would be inventing authority the
engine does not have.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Category:
    """One bookmaker market category."""

    key: str
    label: str
    #: True when the prediction engine can compute a probability for it.
    model_priced: bool
    #: Short explanation shown in the UI for the model-priced ones.
    note: str = ""


#: Canonical category list, in the order the panel renders it.
#: `model_priced` is the single source of truth used by both the API payload and
#: the frontend badge, so the two can never disagree.
CATEGORIES: tuple[Category, ...] = (
    Category("1x2", "1X2", True, "Directly off the scoreline matrix."),
    Category("1x2_1up", "1X2 - 1UP", False),
    Category("1x2_2up", "1X2 - 2UP", False),
    Category("1x2_never_down", "1X2 - Never Down", False),
    Category("ou", "Over/Under", True, "Read off the same matrix at each line."),
    Category("asian_ou", "Asian Over/Under", False),
    Category("ou_early", "Over/Under - Early Goals", False),
    Category("dc", "Double Chance", False),
    Category("dc_1up", "Double Chance - 1UP", False),
    Category("first_goal", "1st Goal", False),
    Category("handicap", "Handicap", False),
    Category("asian_handicap", "Asian Handicap", False),
    # Every category from here down IS published by the engine: build_markets()
    # emits it with a probability. "model_priced" below means the model has a
    # real, independent price to compare against -- the three categories read
    # straight off the scoreline grid against genuine book odds. The rest are
    # model-only: no book price exists to blend or to measure an edge against,
    # so they carry a probability and no edge claim at all.
    Category(
        "ou_1st_half",
        "1st Half O/U",
        False,
        "Half-time split, model-only \u2014 no book price to compare against.",
    ),
    Category(
        "handicap_1st_half",
        "1st Half - Handicap",
        False,
        "Half-time split, model-only \u2014 no book price to compare against.",
    ),
    Category(
        "ht_ft",
        "Half Time / Full Time",
        False,
        "Both halves combined, model-only.",
    ),
    Category("dc_btts", "Double Chance & GG/NG", False),
    Category("dc_ou25", "Double Chance & O/U", False),
    Category("1x2_ou25", "1X2 & O/U", False),
    Category(
        "goal_window",
        "1X2 From x to x Minutes",
        False,
        "Flat goal rate, model-only.",
    ),
    Category("btts", "GG/NG", True, "Both teams to score, off the matrix."),
    Category("btts_2plus", "GG/NG 2+", False),
    Category("streak_any_2", "Any Team To Score 2 or More Goals in a Row", False),
    Category("streak_any_3", "Any Team To Score 3 or More Goals in a Row", False),
    Category("streak_home_2", "Home Team To Score 2 or More Goals in a Row", False),
    Category("streak_home_3", "Home Team To Score 3 or More Goals in a Row", False),
    Category("streak_away_2", "Away Team To Score 2 or More Goals in a Row", False),
    Category("streak_away_3", "Away Team To Score 3 or More Goals in a Row", False),
    # Not in the reference list, but the feed emits them and they need a home:
    Category("correct_score", "Correct Score", False),
    Category("half_time", "Half Time", False),
    Category("team_goals", "Team Goals", False),
    Category("corners", "Corners", False),
    Category("cards", "Cards", False),
    Category("other", "Other", False),
)

BY_KEY: dict[str, Category] = {c.key: c for c in CATEGORIES}
_ORDER: dict[str, int] = {c.key: i for i, c in enumerate(CATEGORIES)}


def _has(n: str, *needles: str) -> bool:
    return any(x in n for x in needles)


def categorise(name: str) -> str:
    """Return the category key for a raw bookmaker market name.

    Ordered most-specific first: ``1X2 - 1UP`` must not fall through to plain
    ``1x2``, and ``Asian Handicap`` must not fall through to ``Handicap``.
    """
    n = (name or "").lower().strip()
    if not n:
        return "other"

    # --- streak markets (longest names, check before generic "goal") -------
    if _has(n, "in a row"):
        if "home team" in n or "away team" in n:
            if "3 or more" in n:
                return "streak_home_3" if "home team" in n else "streak_away_3"
            return "streak_home_2" if "home team" in n else "streak_away_2"
        if "3 or more" in n:
            return "streak_any_3"
        return "streak_any_2"

    # --- combination markets (MUST precede the single-market branches below:
    # a combo's name contains the names of the markets it combines, so
    # "Double Chance & GG/NG" would otherwise be swallowed by the GG/NG test) --
    if "&" in n or " and " in n:
        dc = _has(n, "double chance", "1x", "12", "x2")
        gg = _has(n, "gg/ng", "gg / ng", "both teams", "btts")
        ou = _has(n, "over/under", "over / under", "o/u", "over", "under")
        x2 = _has(n, "1x2", "match result", "winner")
        if dc and gg:
            return "dc_btts"
        # 1X2 & O/U is the more specific of the two win-market combos, so it is
        # tested before Double Chance & O/U: "1X2 & Over/Under" contains no
        # double-chance wording, and "Double Chance & O/U" contains no "1x2".
        if x2 and ou:
            return "1x2_ou25"
        if dc and ou:
            return "dc_ou25"

    # --- both teams to score ----------------------------------------------
    if _has(n, "both teams", "btts", "gg/ng", "gg / ng"):
        return "btts_2plus" if _has(n, "2+", "2 +", "two or more") else "btts"

    # --- goal time windows -------------------------------------------------
    # "1X2 From 1 to 15 Minutes". Matched on the "from ... to ... minute" shape
    # rather than on "1x2", which would otherwise swallow it whole.
    if "minute" in n or "min " in n:
        if _has(n, "from", "to", "between", "1x2"):
            return "goal_window"

    # --- half-time conditional markets -------------------------------------
    # Half Time / Full Time is its own thing, not a half-time result.
    if _has(n, "half time / full time", "half time/full time", "ht/ft", "ht / ft"):
        return "ht_ft"
    if "handicap" in n and ("1st half" in n or "first half" in n or "1h" in n):
        return "handicap_1st_half"
    if _has(n, "over/under", "over / under", "o/u", "over", "under") and (
        "1st half" in n or "first half" in n or "1h" in n
    ):
        return "ou_1st_half"

    # --- double chance (before 1x2: names contain "1up" too) --------------
    if _has(n, "double chance"):
        return "dc_1up" if _has(n, "1up", "1 up") else "dc"

    # --- match result ------------------------------------------------------
    if _has(n, "1x2", "match result", "match winner", "winner"):
        if _has(n, "never down"):
            return "1x2_never_down"
        if _has(n, "2up", "2 up"):
            return "1x2_2up"
        if _has(n, "1up", "1 up"):
            return "1x2_1up"
        return "1x2"

    # --- correct score / half time ----------------------------------------
    if "correct score" in n:
        return "correct_score"
    if "half" in n and _has(n, "result", "time", "1st half", "2nd half"):
        return "half_time"

    # --- over / under ------------------------------------------------------
    if _has(n, "over/under", "over / under", "total goals", "o/u"):
        if "asian" in n:
            return "asian_ou"
        if _has(n, "early", "1st", "first"):
            return "ou_early"
        return "ou"
    if _has(n, "over", "under") and "corner" not in n and "card" not in n:
        if "asian" in n:
            return "asian_ou"
        return "ou"

    # --- first goal --------------------------------------------------------
    if _has(n, "1st goal", "first goal", "last goal", "goal scorer", "scorer"):
        return "first_goal"

    # --- handicaps ---------------------------------------------------------
    if "handicap" in n:
        return "asian_handicap" if "asian" in n else "handicap"

    # --- team totals -------------------------------------------------------
    if _has(n, "team goals", "home team goals", "away team goals", "team total"):
        return "team_goals"

    if "corner" in n:
        return "corners"
    if _has(n, "card", "booking") and "score" not in n:
        return "cards"

    return "other"


def is_model_priced(name_or_key: str) -> bool:
    """True when the engine can price this market.

    Accepts a raw market name or an already-resolved category key.
    """
    key = name_or_key if name_or_key in BY_KEY else categorise(name_or_key)
    cat = BY_KEY.get(key)
    return bool(cat and cat.model_priced)


def order_index(key: str) -> int:
    """Sort position for a category, so the UI order is stable."""
    return _ORDER.get(key, len(_ORDER))


def category_payload() -> list[dict]:
    """The category list as the API returns it."""
    return [
        {
            "key": c.key,
            "label": c.label,
            "model_priced": c.model_priced,
            "note": c.note or None,
        }
        for c in CATEGORIES
    ]

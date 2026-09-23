"""QA: a market the engine cannot price must never claim a price.

The engine publishes a probability for **everything** its Dixon-Coles matrix can
resolve -- 28 markets per fixture, not three. Honesty is not carried by omitting
the derived ones; it is carried by the prices. A market with no bookmaker price
must ship with ``market_probability``, ``odds`` and ``edge`` all None, so the UI
renders ``market —`` / ``odds —`` / ``edge —`` rather than an edge asserted
against a price that does not exist.

This is the invariant that makes the two surfaces agree:

* the fixture page's category index badges exactly three categories **model**
  (1X2, Over/Under, GG/NG) -- the three with a real book to disagree with;
* the All-markets panel still lists Correct Score, Handicap and the rest, but
  every derived row shows dashes for price and edge.

Getting this wrong is silent. A derived market that picked up a stray odds value
would render a confident ``edge +12%`` next to a pick no book ever offered, and
nothing in the UI would look broken.

Runs offline against the predictions already cached in the database -- no feed,
no browser, no network. Run from repo root:

    python qa/model-only-pricing-check.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from app.database import SessionLocal  # noqa: E402
from app.models import Fixture  # noqa: E402

#: The three categories the engine can price against genuine book odds. Note
#: the Over/Under market's *key* is `ou25` even though its taxonomy key is `ou`
#: and its panel heading is "TOTAL GOALS 2.5" -- three names for one market, so
#: the key is taken from the payload rather than guessed.
PRICEABLE = {"1x2", "ou25", "btts"}

#: How many cached predictions to walk. A handful would miss a market that only
#: appears on some fixtures; 60 covers every branch of build_markets() cheaply.
SAMPLE = 60

fails: list[str] = []
checked_rows = 0
checked_fixtures = 0
seen_markets: set[str] = set()
seen_derived: set[str] = set()


def expect(label: str, ok: bool, detail: str = "") -> None:
    if not ok:
        fails.append(label)
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}{(' -> ' + detail) if (detail and not ok) else ''}")


db = SessionLocal()
rows = (
    db.query(Fixture)
    .filter(Fixture.prediction_json.isnot(None))
    .order_by(Fixture.id)
    .limit(SAMPLE)
    .all()
)

print(f"=== walking {len(rows)} cached prediction(s) ===")
for fixture in rows:
    try:
        pred = json.loads(fixture.prediction_json)
    except (TypeError, ValueError):
        fails.append(f"fixture {fixture.id}: prediction_json is not valid JSON")
        continue

    markets = pred.get("markets") or []
    if not markets:
        continue
    checked_fixtures += 1

    for market in markets:
        key = market.get("key")
        seen_markets.add(key)
        for sel in market.get("selections") or []:
            checked_rows += 1
            has_market_price = sel.get("market_probability") is not None

            # A selection from one of the three priceable categories may or may
            # not have a price (the demo seed leaves some odds unset), so only
            # the *inverse* is an invariant worth asserting: anything without a
            # market probability must carry no price claims at all.
            if not has_market_price:
                seen_derived.add(key)
                where = f"fixture {fixture.id} {key} {sel.get('label')!r}"
                expect(
                    f"{where}: no odds on an unpriced selection",
                    sel.get("odds") is None,
                    f"odds={sel.get('odds')!r}",
                )
                expect(
                    f"{where}: no edge on an unpriced selection",
                    sel.get("edge") is None,
                    f"edge={sel.get('edge')!r}",
                )
                expect(
                    f"{where}: no bookmaker on an unpriced selection",
                    not sel.get("bookmaker"),
                    f"bookmaker={sel.get('bookmaker')!r}",
                )
                expect(
                    f"{where}: fair odds still derivable from the probability",
                    (sel.get("probability") or 0) > 0 and sel.get("fair_odds") is not None,
                    f"p={sel.get('probability')!r} fair={sel.get('fair_odds')!r}",
                )

            # A selection WITH a market price must be able to show an edge --
            # the whole point of carrying the price.
            if has_market_price:
                expect(
                    f"fixture {fixture.id} {key}: a priced selection carries an edge",
                    sel.get("edge") is not None,
                    "market_probability set but edge is None",
                )

print("\n=== coverage ===")
print(f"  fixtures walked : {checked_fixtures}")
print(f"  selection rows  : {checked_rows}")
print(f"  distinct markets: {len(seen_markets)}")
print(f"  unpriced markets: {len(seen_derived)}")

expect("the sample covers more than the three priceable markets", len(seen_markets) > 3)
for key in PRICEABLE:
    expect(f"{key!r} appears in the sample", key in seen_markets)

db.close()

print(f"\n{'ALL PASSED' if not fails else str(len(fails)) + ' FAILED'}")
sys.exit(1 if fails else 0)

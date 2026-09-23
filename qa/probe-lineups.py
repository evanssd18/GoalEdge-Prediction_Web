"""Decode the lineups feed (df_li_1) into its player rows.

Prints each parsed player so the field mapping can be confirmed against the
reference page rather than assumed.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from app import flashscore as fs  # noqa: E402

mid = sys.argv[1] if len(sys.argv) > 1 else "vq6T9aFr"
raw = fs._fetch(f"/x/feed/df_li_1_{mid}")
records = [r for r in raw.split("\x7e") if r.strip()]
print(f"records: {len(records)}")
for i, record in enumerate(records[:14]):
    fields: dict[str, str] = {}
    for pair in record.split("\xac"):
        key, _, value = pair.partition("\xf7")
        if key:
            fields.setdefault(key.lstrip("~"), value)
    print(f"[{i:>2}]", {k: ascii(v[:34]) for k, v in fields.items() if k in (
        "LA", "LB", "LC", "LD", "LG", "LH", "LI", "LJ", "LK", "LP",
        "LPR", "LR", "LRR", "LS", "LN", "LNA", "LNB", "LNC", "LND", "LNF", "LNG")})

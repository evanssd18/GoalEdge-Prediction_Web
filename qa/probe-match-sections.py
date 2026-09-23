r"""Dump every feed section Flashscore serves for a match id.

The match page shows sections that each come from their own feed, so "which
feed backs this section" is the first question when one of them is empty. This
walks all of them for one match id and reports which exist, how big they are,
and which field keys they carry -- so the parser is built on an observed
payload rather than a guess.

Usage, from the repo root:

    backend\\Scripts\\python.exe qa\\probe-match-sections.py vq6T9aFr
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from app import flashscore as fs  # noqa: E402

#: The feed paths Flashscore's own match page reads. Each one backs a tab.
SECTIONS = {
    "summary": "/x/feed/df_sui_1_{mid}",
    "stats": "/x/feed/df_st_1_{mid}",
    "lineups": "/x/feed/df_li_1_{mid}",
    "history/h2h": "/x/feed/df_hh_1_{mid}",
    "commentary": "/x/feed/df_dc_1_{mid}",
    "player stats": "/x/feed/df_ps_1_{mid}",
    "odds": "/x/feed/df_od_1_{mid}",
    "standings": "/x/feed/df_ts_1_{mid}",
}


def dump_state(mid: str) -> None:
    print("=" * 78)
    print("MATCH", mid)
    print("=" * 78)
    for label, template in SECTIONS.items():
        path = template.format(mid=mid)
        try:
            raw = fs._fetch(path)
        except fs.FlashscoreUnavailable as exc:
            print(f"\n-- {label}: UNAVAILABLE ({exc})")
            continue
        print(f"\n-- {label}: {len(raw)} bytes --")
        if not raw:
            print("   (empty)")
            continue
        records = [r for r in raw.split("\x7e") if r.strip()]
        print(f"   records: {len(records)}")
        keys: dict[str, str] = {}
        for record in records:
            for pair in record.split("\xac"):
                key, _, value = pair.partition("\xf7")
                if key:
                    keys.setdefault(key.lstrip("~"), value[:60])
        print("   keys:", ", ".join(sorted(keys)))
        print("   first record:", repr(records[0][:300]))


if __name__ == "__main__":
    for arg in sys.argv[1:] or ["vq6T9aFr"]:
        dump_state(arg)

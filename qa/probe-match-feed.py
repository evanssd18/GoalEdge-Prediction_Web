r"""Probe the Flashscore match-detail feed for one match id and dump the raw
keys, so the parser in app/matchdetail.py is built on observed fields rather
than guessed ones.

Usage (from the repo root, inside backend/):
    ..\backend\.venv\Scripts\python.exe ..\qa\probe-match-feed.py <match_id>
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from app import flashscore as fs  # noqa: E402


def dump(match_id: str, path: str) -> None:
    print("=" * 78)
    print("PATH:", path)
    try:
        raw = fs._fetch(path)
    except fs.FlashscoreUnavailable as exc:
        print("UNAVAILABLE:", exc)
        return
    print("bytes:", len(raw))
    records = raw.split("\xac")
    print("records:", len(records))
    keys: dict[str, int] = {}
    for record in records:
        key, _, value = record.partition("\xf7")
        if not key:
            continue
        keys[key] = keys.get(key, 0) + 1

    print("\n-- keys present (count, sample value) --")
    samples: dict[str, str] = {}
    for record in records:
        key, _, value = record.partition("\xf7")
        if key and key not in samples:
            samples[key] = value[:70]
    for key in sorted(keys):
        print(f"  {key:>18} x{keys[key]:<4} {samples.get(key, '')!r}")

    print("\n-- first 40 records, verbatim (-- -> micro sign) --")
    for i, record in enumerate(records[:40]):
        if record.strip():
            print(f"  [{i:>3}] {record[:200]!r}")


def main() -> None:
    match_id = sys.argv[1] if len(sys.argv) > 1 else "Uuc6OAi2"
    for path in (
        f"/x/feed/df_sui_1_{match_id}",
        f"/x/feed/df_hh_1_{match_id}",
        f"/x/feed/df_st_1_{match_id}",
        f"/x/feed/df_li_1_{match_id}",
    ):
        dump(match_id, path)


if __name__ == "__main__":
    main()

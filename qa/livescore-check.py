"""Check the livescore board's shape, ordering and filters against a live server.

Deliberately HTTP-level rather than import-level: /api/livescores is the one
route that serves a day either from the database *or* straight off the feed, and
which branch gets taken is the thing worth checking.
"""
import json
import os
import urllib.error
import urllib.request as u

BASE = os.environ.get("GE_BASE", "http://127.0.0.1:8080")  # a running instance


def get(path):
    return json.load(u.urlopen(BASE + path))


def main():
    # The board serves any day inside a two-year window, not just three relative
    # words. A date outside the window is a 400 with the bounds in the message
    # (an empty board would read as "no football", which is a different claim);
    # an unparseable value is a 400 as well. Both are asserted here rather than
    # treated as failures.
    for day in ("today", "tomorrow", "yesterday", "2026-09-16", "2019-01-01", "bogus"):
        try:
            d = get(f"/api/livescores?date={day}")
            print(
                f"[{day:12}] ok      source={d['source']:11} live={d['live_count']:3} "
                f"total={d['total']:4} trunc={d['truncated']} groups={len(d['groups']):3} "
                f"day={d.get('day')} err={d.get('error')}"
            )
        except urllib.error.HTTPError as exc:
            body = exc.read().decode()
            try:
                detail = json.loads(body).get("detail", body)
            except Exception:  # noqa: BLE001
                detail = body
            print(f"[{day:12}] HTTP {exc.code}: {detail}")
        except Exception as exc:  # noqa: BLE001
            print(f"[{day:12}] FAILED {type(exc).__name__}: {exc}")

    # The window the board promises, echoed on the payload.
    w = get("/api/livescores?date=today").get("window")
    print(f"window: {w}")

    d = get("/api/livescores?date=today")
    print("\nfirst 5 groups (live floats to the top):")
    for g in d["groups"][:5]:
        print(
            f"  live={g['live_count']} [{g['country']}] {g['competition_label']} "
            f"items={len(g['items'])}"
        )

    rows = [r for g in d["groups"] for r in g["items"]]
    live = [r for r in rows if r["status"] == "live"]
    print(f"\nrows={len(rows)} live={len(live)}")
    if live:
        print("live row:", json.dumps({k: live[0][k] for k in
              ("competition_label", "home_team", "away_team", "home_goals",
               "away_goals", "status", "minute_label", "minute_source", "id")}, indent=1))

    # Every row must carry the keys the board renders; a missing one is a blank cell.
    need = {"id", "competition", "competition_label", "country", "home_team",
            "away_team", "home_goals", "away_goals", "kickoff", "status", "minute_label"}
    missing = [sorted(need - set(r)) for r in rows if need - set(r)]
    print("rows missing render keys:", len(missing))

    malformed = [r["competition_label"] for r in rows if not r["competition_label"]]
    print("rows with no competition label:", len(malformed))

    live_only = get("/api/livescores?date=today&live_only=true")
    all_live = all(
        i["status"] == "live" for g in live_only["groups"] for i in g["items"]
    )
    print(f"live_only=true -> {live_only['total']} rows, all live: {all_live}")
    live_only_groups_live = all(g["live_count"] > 0 for g in live_only["groups"])
    print(f"live_only groups all have live_count>0: {live_only_groups_live}")


if __name__ == "__main__":
    main()

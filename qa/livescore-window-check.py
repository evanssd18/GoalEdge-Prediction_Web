"""HTTP-level check of the livescore board's 2-year window and row shape.

Talks to a *running* server rather than importing the app, because the thing
worth checking is what the route actually returns: the window bounds, the
sync-on-miss branch, and the per-row fields the board renders.
"""
import json
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8080"


def get(path):
    with urllib.request.urlopen(BASE + path, timeout=90) as r:
        return json.load(r)


def main():
    print("== window bounds ==")
    d = get("/api/livescores?date=today")
    print("window:", d.get("window"))
    print("day:", d.get("day"), "source:", d.get("source"),
          "live:", d.get("live_count"), "total:", d.get("total"))

    rows = [r for g in d["groups"] for r in g["items"]]
    print("\n== row shape ==")
    need = {"id", "home_team", "away_team", "kickoff", "date", "time",
            "status", "minute_label", "minute_source", "kickoff_time_exact"}
    missing = [sorted(need - set(r)) for r in rows if need - set(r)]
    print(f"rows={len(rows)} missing_keys={len(missing)}")
    if missing:
        print("  first missing:", missing[0])

    print("\n== sample live row ==")
    live = [r for r in rows if r["status"] == "live"]
    if live:
        r = live[0]
        print(json.dumps({k: r[k] for k in
                          ("id", "home_team", "away_team", "date", "time",
                           "status", "minute_label", "minute_source",
                           "kickoff_time_exact", "home_goals", "away_goals")}, indent=1))
    else:
        print("  (nothing in play right now)")

    print("\n== clickable rows (have a fixture id) ==")
    clickable = sum(1 for r in rows if r["id"] is not None)
    print(f"{clickable}/{len(rows)} rows have an id -> match details reachable")

    print("\n== window edge cases ==")
    for day in ("yesterday", "tomorrow", "2025-01-01", "2019-01-01", "2030-01-01", "bogus"):
        try:
            x = get(f"/api/livescores?date={day}")
            print(f"  [{day:12}] ok  day={x['day']} total={x['total']} source={x['source']}")
        except urllib.error.HTTPError as e:
            body = e.read().decode()
            try:
                detail = json.loads(body).get("detail")
            except Exception:
                detail = body[:90]
            print(f"  [{day:12}] HTTP {e.code}: {detail}")


if __name__ == "__main__":
    main()

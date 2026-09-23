"""Unit + HTTP tests for the site design editor and the admin guards.

Three groups, all offline (FastAPI's TestClient against a temporary database):

1. **Validation** -- what `app/design.py` accepts as a colour. This is the
   security-relevant half: a value that reaches the stylesheet must not be able
   to close the declaration or the block it is written into.
2. **Normalisation** -- what happens to an INVALID value. It must be ignored,
   never reverted, because reverting silently wipes an operator's saved choice.
3. **Guards** -- every admin route must refuse an anonymous caller. Four of them
   depended on nothing but their `tags=["admin"]` label when this was written,
   which is documentation rather than a permission.

Run from the repo root:
    backend\\.venv\\Scripts\\python.exe qa\\design-check.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# --------------------------------------------------------------------------
# Point the app at a THROWAWAY database, and turn the feed off, BEFORE the first
# `app.*` import below.
#
# Order matters and is the whole point: `app.config` builds its Settings once, at
# import, and caches it (`@lru_cache` + a module-level `settings`), and
# `app.database` binds its engine from that settings object at import too. Setting
# the env var after the first `import app...` is silently ignored -- the engine is
# already pointed at the live DB, which is how this check ended up asserting
# against real data and reporting an operator's saved palette (changed=24) as its
# own failure.
#
# `FLASHSCORE_ENABLED=false`: cold booting an empty DB runs the lifespan seed and,
# with the feed on, starts a sync thread that re-inserts the same fixtures and
# trips the unique key on (home_team_id, away_team_id, kickoff). This check tests
# the design routes, not the feed.
import os  # noqa: E402
import tempfile  # noqa: E402

_tmp_db = tempfile.NamedTemporaryFile(
    prefix="goaledge-design-check-", suffix=".db", delete=False
)
_tmp_db.close()
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp_db.name.replace(os.sep, '/')}"
os.environ["FLASHSCORE_ENABLED"] = "false"

from app import design  # noqa: E402

fails: list[str] = []
total = 0


def check(label: str, got, expected) -> None:
    global total
    total += 1
    ok = got == expected
    if not ok:
        fails.append(f"{label}: got {got!r}, expected {expected!r}")
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}" + ("" if ok else f" -> got {got!r}, expected {expected!r}"))


# --------------------------------------------------------------------------
print("=== is_valid_colour rejects anything that could break the stylesheet ===")
# These are the values that matter. Each one, written into `html:root { --x: V; }`
# would either break the block or inject a rule.
HOSTILE = [
    "red; } body { display:none }",
    "#fff;",
    "}",
    "</style><script>alert(1)</script>",
    "url(javascript:alert(1))",
    "expression(alert(1))",
    'red"',
    "red'",
    "var(--accent)",
    "var( --accent )",
    "red\nblue",
    "/* comment */red",
    "red/*",
    "",
    "   ",
    "#gg0000",
    "#12345",
    "rgb(1,2,3",
    "a" * 100,
    None,
    123,
    True,
    ["#fff"],
]
for value in HOSTILE:
    check(f"rejects {str(value)[:38]!r}", design.is_valid_colour(value), False)

print("\n=== ...and accepts every legitimate form ===")
VALID = [
    "#fff", "#ffff", "#ffffff", "#ffffff80",
    "rgb(1,2,3)", "rgba(1,2,3,0.5)", "hsl(1,2%,3%)", "hsla(1,2%,3%,.5)",
    "rgb(1 2 3)", "rebeccapurple", "goldenrod", "transparent",
    "#00A54E",  # uppercase hex
    " #fff ",   # padded
]
for value in VALID:
    check(f"accepts {value!r}", design.is_valid_colour(value), True)

# --------------------------------------------------------------------------
print("\n=== an invalid value is IGNORED, never reverted ===")
# The bug this pins: `normalise` used to fall back to the DEFAULT, so a bad
# colour silently turned a purple site green and reported success.
saved = design.normalise({"colours": {"accent": "#7c3aed"}, "layout": "compact"})
check("the good value is stored", saved["colours"]["accent"], "#7c3aed")

after_bad = design.normalise(
    {"colours": {"accent": "red; } body{}", "link": "#a78bfa"}}, base=saved
)
check("a hostile accent leaves the saved one alone", after_bad["colours"]["accent"], "#7c3aed")
check("a valid sibling still applies", after_bad["colours"]["link"], "#a78bfa")
check("an untouched setting is preserved", after_bad["layout"], "compact")

after_unknown = design.normalise({"hover_template": "sparkles", "layout": "nope"}, base=saved)
check("an unknown template keeps the current one", after_unknown["hover_template"], saved["hover_template"])
check("an unknown layout keeps the current one", after_unknown["layout"], "compact")

print("\n=== with no base, an invalid value falls back to the shipped default ===")
fresh = design.normalise({"colours": {"accent": "not-a-colour"}})
check("a fresh design uses the default", fresh["colours"]["accent"], design.TOKEN_DEFAULTS["accent"])

print("\n=== normalise never raises, whatever it is handed ===")
for junk in [None, "string", 42, [], {"colours": "not-a-dict"}, {"colours": {"accent": None}}]:
    result = design.normalise(junk)
    ok = isinstance(result, dict) and set(result["colours"]) == set(design.TOKEN_KEYS)
    check(f"normalise({str(junk)[:24]!r}) yields a complete design", ok, True)

print("\n=== diff_against_default counts real differences ===")
check("the default design has no differences", design.diff_against_default(design.default_design()), 0)
check(
    "one colour change is one difference",
    design.diff_against_default(design.normalise({"colours": {"accent": "#123456"}})),
    1,
)
check(
    "a template change counts too",
    design.diff_against_default(design.normalise({"layout": "compact"})),
    1,
)

# --------------------------------------------------------------------------
print("\n=== HTTP: the design routes and the admin guards ===")
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

with TestClient(app) as client:
    r = client.get("/api/site/design")
    check("GET /site/design is public", r.status_code, 200)
    body = r.json()
    check("it returns every token", len(body["colours"]) >= len(design.TOKEN_KEYS), True)
    check("it starts at the shipped design", body["changed_from_default"], 0)

    r = client.get("/api/site/design/templates")
    check("GET /site/design/templates is public", r.status_code, 200)
    t = r.json()
    check("it lists every token group", len(t["token_groups"]), len(design.TOKEN_GROUPS))
    check("it lists every hover template", len(t["hover_templates"]), len(design.HOVER_TEMPLATES))
    check("it lists every layout", len(t["layout_templates"]), len(design.LAYOUT_TEMPLATES))

    print("\n  -- writing is refused without an admin token --")
    r = client.put("/api/admin/site/design", json={"layout": "wide"})
    check("PUT /admin/site/design refuses anonymous", r.status_code in (401, 403), True)
    r = client.post("/api/admin/site/design/reset")
    check("POST /admin/site/design/reset refuses anonymous", r.status_code in (401, 403), True)

    print("\n  -- the maintenance routes must be gated (they were NOT) --")
    for path in ["/api/admin/seed", "/api/admin/settle", "/api/admin/sync", "/api/admin/refresh/run"]:
        r = client.post(path)
        check(f"POST {path} refuses anonymous", r.status_code in (401, 403), True)

    print("\n  -- the rest of the admin surface --")
    for path in ["/api/admin/stats", "/api/admin/users", "/api/admin/audit"]:
        r = client.get(path)
        check(f"GET {path} refuses anonymous", r.status_code in (401, 403), True)
    r = client.patch("/api/admin/users/1/role", json={"is_admin": True})
    check("PATCH role refuses anonymous", r.status_code in (401, 403), True)
    r = client.delete("/api/admin/users/1")
    check("DELETE user refuses anonymous", r.status_code in (401, 403), True)

    print("\n  -- a normal user must not reach the admin surface --")
    client.post(
        "/api/auth/register",
        json={"email": "plain@example.com", "username": "plainuser", "password": "secret123"},
    )
    login = client.post(
        "/api/auth/login", json={"email": "plain@example.com", "password": "secret123"}
    )
    check("the normal user can sign in", login.status_code, 200)
    token = login.json().get("access_token")
    auth = {"Authorization": f"Bearer {token}"}
    r = client.put("/api/admin/site/design", json={"layout": "wide"}, headers=auth)
    check("a non-admin cannot change the design", r.status_code, 403)
    r = client.post("/api/admin/seed?force=true", headers=auth)
    check("a non-admin cannot reseed", r.status_code, 403)

    print("\n  -- /auth/me tells the client whether to offer the admin link --")
    r = client.get("/api/auth/me", headers=auth)
    check("GET /auth/me succeeds", r.status_code, 200)
    me = r.json()
    check("it exposes is_admin (needed for the panel link)", "is_admin" in me, True)
    check("a normal user reports is_admin False", me["is_admin"], False)

print(f"\n{'ALL PASSED' if not fails else str(len(fails)) + ' FAILED'}"
      f"  ({total} assertions)")
for f in fails:
    print(f"  - {f}")

# Remove the throwaway DB this check created (SQLite also leaves -wal/-shm).
for suffix in ("", "-wal", "-shm"):
    try:
        os.unlink(_tmp_db.name + suffix)
    except OSError:
        pass

sys.exit(1 if fails else 0)

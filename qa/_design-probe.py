"""Scratch: exercise the new design endpoints through TestClient."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

# The lifespan runs `create_all`, which is what creates the new tables -- a bare
# TestClient(app) never triggers startup, so the first query failed with "no such
# table: site_settings". Enter it as a context manager.
import contextlib  # noqa: E402
_cm = TestClient(app)
_cm.__enter__()
c = _cm
r = c.get("/api/site/design")
print("GET /api/site/design ->", r.status_code)
if r.status_code == 200:
    d = r.json()
    print("  colours      :", len(d["colours"]))
    print("  hover        :", d["hover_template"], "/", d["hover_intensity"])
    print("  layout       :", d["layout"], "radius:", d["radius"])
    print("  changed      :", d["changed_from_default"])
else:
    print("  body:", r.text[:400])

r = c.get("/api/site/design/templates")
print("\nGET /api/site/design/templates ->", r.status_code)
if r.status_code == 200:
    t = r.json()
    print("  token groups :", [g["key"] for g in t["token_groups"]])
    print("  hovers       :", [h["key"] for h in t["hover_templates"]])
    print("  layouts      :", [l["key"] for l in t["layout_templates"]])
else:
    print("  body:", r.text[:400])

print("\n--- write must be admin-gated ---")
r = c.put("/api/admin/site/design", json={"colours": {"accent": "#ff0000"}})
print("PUT without a token ->", r.status_code, "(expect 401)")

print("\n--- the maintenance routes must be gated too ---")
for path in ["/api/admin/seed", "/api/admin/settle", "/api/admin/sync", "/api/admin/refresh/run"]:
    r = c.post(path)
    print(f"  POST {path} -> {r.status_code} (expect 401)")

print("\n--- public design must not leak admin data ---")
r = c.get("/api/site/design")
body = r.text
for needle in ("updated_by", "email", "token", "password"):
    if needle in body:
        print(f"  !! response mentions {needle!r}")
print("  (no matches above means clean)")

print("\n--- validation: hostile colour values must not survive ---")
from app import design  # noqa: E402

BAD = [
    "red; } body { display:none }",
    "var(--accent)",
    "url(x)",
    "#fff;",
    "</style><script>alert(1)</script>",
    "",
    "#gg",
    "a" * 200,
]
for value in BAD:
    print(f"  is_valid_colour({value[:40]!r:44}) = {design.is_valid_colour(value)}")
GOOD = ["#00a54e", "#abc", "#aabbccdd", "rgb(1,2,3)", "rgba(1,2,3,.5)", "rebeccapurple"]
for value in GOOD:
    print(f"  is_valid_colour({value!r:44}) = {design.is_valid_colour(value)}")

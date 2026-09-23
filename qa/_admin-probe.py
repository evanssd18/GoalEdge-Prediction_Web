"""Scratch: drive the design write path as a real admin through HTTP."""
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

BASE = "http://127.0.0.1:8097/api"


def call(method, path, body=None, token=None):
    req = urllib.request.Request(
        f"{BASE}{path}",
        method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={
            "Content-Type": "application/json",
            **({"Authorization": f"Bearer {token}"} if token else {}),
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read().decode() or "null")
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, json.loads(raw)
        except ValueError:
            return e.code, raw[:300]


sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

# Promote a user to admin directly in the DB -- there is no bootstrap endpoint,
# and adding one would itself be a privilege-escalation hole.
from app.database import SessionLocal  # noqa: E402
from app.models import User  # noqa: E402

db = SessionLocal()
# A real TLD: `EmailStr` rejects ".test" as a reserved domain, which is what the
# 422 on registration was.
EMAIL = "admin@goaledge.example.com"
PASSWORD = "adminpass123"
user = db.query(User).filter(User.email == EMAIL).first()
if user is None:
    print("no such user -- registering first")
    print("register ->", call("POST", "/auth/register", {
        "email": EMAIL, "username": "siteadmin", "password": PASSWORD,
    })[0])
    user = db.query(User).filter(User.email == EMAIL).first()
user.is_admin = True
db.commit()
print(f"{EMAIL} is_admin = {user.is_admin}")
db.close()

status, auth = call("POST", "/auth/login", {"email": EMAIL, "password": PASSWORD})
print("login ->", status)
token = (auth or {}).get("access_token")
if not token:
    print("no token:", auth)
    sys.exit(1)

print("\n--- read the design as admin ---")
status, d = call("GET", "/site/design", token=token)
print(" ", status, "accent =", d["colours"]["accent"], "changed =", d["changed_from_default"])

print("\n--- save a new design ---")
status, d = call("PUT", "/admin/site/design", {
    "colours": {"accent": "#7c3aed", "link": "#a78bfa"},
    "hover_template": "glow",
    "hover_intensity": "strong",
    "layout": "compact",
    "radius": "pill",
}, token=token)
print(" ", status)
if status == 200:
    print("  accent :", d["colours"]["accent"])
    print("  link   :", d["colours"]["link"])
    print("  hover  :", d["hover_template"], "/", d["hover_intensity"])
    print("  layout :", d["layout"], "radius:", d["radius"])
    print("  changed:", d["changed_from_default"], "by", d["updated_by"])

print("\n--- a hostile colour must be refused, not stored ---")
status, d = call("PUT", "/admin/site/design", {
    "colours": {"accent": "red; } body { display:none } /*"},
}, token=token)
print(" ", status, "-> accent now =", d["colours"]["accent"], "(should be the saved purple)")

print("\n--- an unknown template must fall back, not break the page ---")
status, d = call("PUT", "/admin/site/design", {"hover_template": "sparkles"}, token=token)
print(" ", status, "hover =", d["hover_template"], "(should be glow, the saved one)")

print("\n--- garbage payload types ---")
for bad in [{"colours": "not-a-dict"}, {"layout": 123}, {"colours": {"accent": None}}]:
    status, d = call("PUT", "/admin/site/design", bad, token=token)
    print(f"  {bad} -> {status}")

print("\n--- auth checks ---")
print("  PUT without token ->", call("PUT", "/admin/site/design", {"layout": "wide"})[0])
print("  GET /admin/stats without token ->", call("GET", "/admin/stats")[0])
print("  GET /admin/users without token ->", call("GET", "/admin/users")[0])
print("  GET /admin/audit without token ->", call("GET", "/admin/audit")[0])

print("\n--- audit log recorded the changes ---")
status, rows = call("GET", "/admin/audit?limit=6", token=token)
print(" ", status)
for r in (rows or [])[:6]:
    print(f"  {r['action']:22} by {r['actor']:12} {r['detail'] or ''}")

print("\n--- reset ---")
status, d = call("POST", "/admin/site/design/reset", token=token)
print(" ", status, "accent =", d["colours"]["accent"], "changed =", d["changed_from_default"])

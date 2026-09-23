"""QA: verify every team's badge URL actually resolves.

Checks the source the app really uses. Run from backend/:

    .\\.venv\\Scripts\\python.exe ..\\qa\\crest-check.py
"""
from __future__ import annotations

import socket
import sys
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from app.crests import CREST_FILES, FALLBACK_LOGO, crest_for_slug  # noqa: E402

# Two clubs pointing at one badge file is usually a mapping bug -- but not
# always: the seed data contains a genuine alias (Caykur Rizespor appears as both
# "Rizespor" and "Caykur Rizespor"). Shared targets are reported for review
# rather than failed outright, since only a human can tell an alias from a slip.
targets: dict[str, list[str]] = {}
for slug, path in CREST_FILES.items():
    targets.setdefault(path, []).append(slug)
shared = {k: v for k, v in targets.items() if len(v) > 1}

KNOWN_ALIASES = {frozenset({"rizespor", "caykur-rizespor"})}
print("=== shared badge targets ===")
unexpected: dict[str, list[str]] = {}
if shared:
    for path, slugs in shared.items():
        tag = "known alias" if frozenset(slugs) in KNOWN_ALIASES else "REVIEW"
        if tag == "REVIEW":
            unexpected[path] = slugs
        print(f"  [{tag}] {path}: {', '.join(slugs)}")
else:
    print("  none")


def probe(slug: str) -> str | None:
    url = crest_for_slug(slug)
    if not url or url == FALLBACK_LOGO:
        return f"{slug} (no url)"
    req = urllib.request.Request(
        url, method="HEAD", headers={"User-Agent": "Mozilla/5.0 (compatible; GoalEdgeAI/1.0)"}
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            if resp.status != 200:
                return f"{slug} (HTTP {resp.status})"
            # A badge must actually be an image, not an HTML error page.
            ctype = resp.headers.get("Content-Type", "")
            if not ctype.startswith("image/"):
                return f"{slug} (content-type {ctype!r})"
    except (urllib.error.URLError, socket.timeout, OSError) as exc:
        return f"{slug} ({exc})"
    return None


slugs = sorted(CREST_FILES)
print(f"\n=== checking {len(slugs)} mapped badges ===")
with ThreadPoolExecutor(max_workers=16) as pool:
    results = list(pool.map(probe, slugs))
failures = [r for r in results if r]

print(f"checked : {len(slugs)}")
print(f"failed  : {len(failures)}")
for f in failures:
    print("  FAIL", f)

ok = not failures and not unexpected
print("\nRESULT:", "ALL RESOLVE" if ok else "FAILURES ABOVE")
sys.exit(0 if ok else 1)

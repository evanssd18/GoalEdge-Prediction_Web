"""Team crest resolution for GoalEdge.

Maps each team in the seed dataset to a real club badge image and resolves it to
a URL the frontend can render.

Two sources sit behind one interface:

A. **Local badge store** (``BADGE_DIR``) -- preferred. Point it at a folder of
   ``<slug>.png`` files and it is used for everything. This is how a deployment
   with its own licensed or self-made badges should run: no network, no third
   party, no rights question.
B. **Remote CDN fallback** (``REMOTE_TEMPLATE``) -- used only when no local file
   exists, so the demo works out of the box.

``LOGO_SOURCE`` selects ``local`` | ``remote`` | ``auto`` (local first).
``REMOTE_TEMPLATE_LOCAL`` is the open per-club dataset used for the fallback.

A rights note that matters: club crests are trademarks owned by the clubs.
Neither a CDN nor a logo aggregator can grant a licence it does not hold, and
several logo sites state explicitly that their imagery may not be reused. The
remote fallback exists to make the demo work, not as a licensing answer -- see
the README section on badges before shipping this anywhere real.
"""
from __future__ import annotations

import os
import socket
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from .crest_map import CREST_FILES

#: Where local ``<slug>.png`` badges live, if any.
BADGE_DIR = Path(
    os.environ.get("BADGE_DIR", str(Path(__file__).resolve().parent / "static" / "badges"))
)

#: Source preference: "local", "remote" or "auto" (local first, then remote).
LOGO_SOURCE = os.environ.get("LOGO_SOURCE", "auto").strip().lower()

#: Public crest CDN used as the fallback. One stable numeric URL per club.
REMOTE_TEMPLATE = "https://crests.football-data.org/{id}.png"

#: URL prefix under which main.py serves BADGE_DIR.
LOCAL_URL_PREFIX = "/static/badges"

#: Root of the open club-logo dataset mirror used by the remote fallback.
DATASET_RAW = (
    "https://raw.githubusercontent.com/luukhopman/football-logos/master/logos"
)

#: Kept so existing callers/tests that reference LOGO_TEMPLATE keep working.
LOGO_TEMPLATE = REMOTE_TEMPLATE

#: Workspace-wide fallback: a neutral ball glyph as a data URI, so a team with
#: no mapped crest never renders a broken image.
FALLBACK_LOGO = (
    "data:image/svg+xml,"
    "%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E"
    "%3Ccircle cx='12' cy='12' r='10' fill='%23e8e8e8'/%3E"
    "%3Cpath d='M12 7l3 2-1 3h-4l-1-3z' fill='%238b8b8b'/%3E"
    "%3C/svg%3E"
)

#: Team slug (as produced by seed.slugify) -> football-data.org crest id.
#: Keyed by slug so it is independent of display-name tweaks.
TEAM_CRESTS: dict[str, int] = {
    # ---------------------------------------------------------- Premier League
    "arsenal": 57,
    "manchester-city": 65,
    "liverpool": 64,
    "chelsea": 61,
    "tottenham": 73,
    "newcastle": 67,
    "aston-villa": 58,
    "manchester-united": 66,
    "brighton": 397,
    "west-ham": 563,
    "brentford": 402,
    "crystal-palace": 354,
    "fulham": 63,
    "bournemouth": 1044,
    "everton": 62,
    "nottingham-forest": 351,
    "wolves": 76,
    "leeds": 341,
    "burnley": 328,
    "sunderland": 71,
    # ---------------------------------------------------------------- LaLiga
    "real-madrid": 86,
    "barcelona": 81,
    "atletico-madrid": 78,
    "athletic-club": 77,
    "villarreal": 94,
    "real-betis": 90,
    "real-sociedad": 92,
    "sevilla": 559,
    "valencia": 95,
    "celta-vigo": 558,
    "girona": 298,
    "getafe": 82,
    "osasuna": 79,
    "rayo-vallecano": 87,
    "espanyol": 80,
    "mallorca": 89,
    "alaves": 263,
    "elche": 285,
    "levante": 88,
    "real-oviedo": 1048,
    # ---------------------------------------------------------------- Serie A
    "inter-milan": 108,
    "napoli": 113,
    "ac-milan": 98,
    "juventus": 109,
    "atalanta": 102,
    "roma": 100,
    "lazio": 110,
    "bologna": 103,
    "fiorentina": 99,
    "como-1907": 7397,
    "torino": 586,
    "udinese": 115,
    "sassuolo": 471,
    "cagliari": 104,
    "genoa": 107,
    "parma": 112,
    "lecce": 5890,
    "verona": 450,
    "pisa": 5911,
    "cremonese": 5911,
    # -------------------------------------------------------------- Bundesliga
    "bayern-munich": 5,
    "bayer-leverkusen": 3,
    "borussia-dortmund": 4,
    "rb-leipzig": 721,
    "eintracht-frankfurt": 19,
    "vfb-stuttgart": 10,
    "sc-freiburg": 17,
    "werder-bremen": 12,
    "wolfsburg": 11,
    "borussia-mgladbach": 18,
    "hoffenheim": 2,
    "union-berlin": 28,
    "mainz": 15,
    "augsburg": 16,
    "st-pauli": 20,
    "heidenheim": 44,
    "fc-koln": 1,
    "hamburger-sv": 7,
    # ---------------------------------------------------------------- Ligue 1
    "paris-sg": 524,
    "marseille": 516,
    "monaco": 548,
    "lille": 521,
    "lyon": 523,
    "nice": 522,
    "lens": 546,
    "strasbourg": 576,
    "rennes": 529,
    "toulouse": 511,
    "brest": 512,
    "auxerre": 519,
    "nantes": 543,
    "angers": 532,
    "le-havre": 533,
    "metz": 545,
    "lorient": 525,
    "paris-fc": 1049,
    # ------------------------------------------------------------- Eredivisie
    "psv": 674,
    "ajax": 678,
    "feyenoord": 675,
    "az-alkmaar": 682,
    "fc-utrecht": 676,
    "fc-twente": 666,
    "go-ahead-eagles": 718,
    "nec-nijmegen": 1910,
    "sc-heerenveen": 673,
    "fortuna-sittard": 1920,
    "sparta-rotterdam": 680,
    "pec-zwolle": 684,
    "heracles": 671,
    "nac-breda": 681,
    "excelsior": 683,
    "telstar": 1925,
    "volendam": 1911,
    "willem-ii": 686,
    # ---------------------------------------------------------- Primeira Liga
    "sporting-cp": 498,
    "fc-porto": 503,
    "benfica": 1903,
    "sc-braga": 5613,
    "vitoria-guimaraes": 5543,
    "famalicao": 7529,
    "moreirense": 5531,
    "gil-vicente": 5533,
    "santa-clara": 5545,
    "estoril": 5537,
    "rio-ave": 5539,
    "estrela-amadora": 7555,
    "casa-pia": 5541,
    "nacional-madeira": 5529,
    "arouca": 5535,
    "avs": 7655,
    "tondela": 5538,
    "alverca": 5547,
    # --------------------------------------------------------------- Super Lig
    "galatasaray": 610,
    "fenerbahce": 611,
    "besiktas": 612,
    "trabzonspor": 613,
    "istanbul-basaksehir": 614,
    "samsunspor": 615,
    "goztepe": 616,
    "konyaspor": 617,
    "gaziantep-fk": 618,
    "rizespor": 619,
    "antalyaspor": 620,
    "alanyaspor": 621,
    "kasimpasa": 622,
    "kayserispor": 623,
    "eyupspor": 624,
    "caykur-rizespor": 619,
    "kocaelispor": 625,
    "genclerbirligi": 626,
    # ------------------------------------------------- Scottisch Premiership
    "celtic": 732,
    "rangers": 7540,
    "hibernian": 747,
    "heart-of-midlothian": 744,
    "aberdeen": 741,
    "motherwell": 748,
    "dundee-united": 751,
    "st-mirren": 749,
    "kilmarnock": 750,
    "dundee-fc": 746,
    "falkirk": 753,
    "livingston": 752,
    # ------------------------------------------------------------- Championship
    "leicester-city": 338,
    "southampton": 340,
    "ipswich-town": 349,
    "middlesbrough": 343,
    "coventry-city": 1076,
    "west-bromwich-albion": 74,
    "norwich-city": 68,
    "millwall": 384,
    "bristol-city": 387,
    "hull-city": 322,
    "preston": 1081,
    "swansea-city": 72,
    "watford": 346,
    "stoke-city": 70,
    "blackburn-rovers": 59,
    "charlton-athletic": 1094,
    "derby-county": 342,
    "oxford-united": 1082,
    "sheffield-united": 356,
    "portsmouth": 1080,
    "queens-park-rangers": 69,
    "wrexham": 1086,
    "birmingham-city": 332,
    "sheffield-wednesday": 345,
}


def _local_badge_url(slug: str) -> str | None:
    """URL for a locally stored badge, or None when the file is absent."""
    for ext in ("png", "svg", "webp", "jpg"):
        if (BADGE_DIR / f"{slug}.{ext}").is_file():
            return f"{LOCAL_URL_PREFIX}/{slug}.{ext}"
    return None


def crest_for_slug(slug: str | None) -> str | None:
    """Badge URL for a team slug, or None when nothing resolves.

    A local file wins when present; otherwise the remote CDN is used for clubs
    that have a mapped id.
    """
    if not slug:
        return None
    slug = slug.strip().lower()

    if LOGO_SOURCE in ("local", "auto"):
        local = _local_badge_url(slug)
        if local:
            return local
    if LOGO_SOURCE == "local":
        return None

    # Dataset file takes precedence over a numeric crest id: the filename names
    # the club, so it cannot silently belong to a different team.
    rel = CREST_FILES.get(slug)
    if rel:
        return f"{DATASET_RAW}/" + urllib.parse.quote(rel)

    crest_id = TEAM_CRESTS.get(slug)
    if crest_id is None:
        return None
    return REMOTE_TEMPLATE.format(id=crest_id)


def crest_for_team(team) -> str:
    """Badge URL for a Team instance, falling back to a neutral glyph."""
    return crest_for_slug(getattr(team, "slug", None)) or FALLBACK_LOGO


def _head_ok(url: str, timeout: float = 6.0) -> bool:
    request = urllib.request.Request(
        url, method="HEAD", headers={"User-Agent": "Mozilla/5.0 (compatible; GoalEdgeAI/1.0)"}
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            return resp.status == 200
    except (urllib.error.URLError, socket.timeout, OSError):
        return False


def verify_mapping(sample: int | None = None, timeout: float = 6.0) -> dict:
    """Check mapped crests actually resolve. Network-bound; run on demand.

    Used by ``qa/crest-check.py``. A wrong id is worse than no badge, so this
    reports every failure rather than stopping at the first.
    """
    items = list(TEAM_CRESTS.items())
    if sample:
        items = items[:sample]

    def probe(item: tuple[str, int]) -> str | None:
        slug, crest_id = item
        if _head_ok(REMOTE_TEMPLATE.format(id=crest_id), timeout=timeout):
            return None
        return f"{slug} (id {crest_id})"

    # Concurrent: 198 sequential HEADs takes minutes, which is long enough to
    # look like a hang and get killed.
    with ThreadPoolExecutor(max_workers=16) as pool:
        results = list(pool.map(probe, items))
    missing = [r for r in results if r]
    return {
        "checked": len(items),
        "unreachable": missing,
        "ok": not missing,
        "mapped": len(TEAM_CRESTS),
    }

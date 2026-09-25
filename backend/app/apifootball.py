"""API-Football (API-Sports) client.

Schedule and scores still come from Flashscore. This module supplies
**injuries** so the engine can shade expected goals when a side is missing
players. It never writes ``fixtures.odds_*`` — The Odds API is the book feed.

Auth is probed on first use. A rejected key is cached for the process so a
bad credential cannot hammer the host on every refresh. Free plans are ~100
requests a day, so injuries are fetched by *date* (one call per day), cached
for hours, and skipped entirely when the key is unusable.

A RapidAPI key and a dashboard (api-sports.io) key use different hosts; both
are tried once.
"""
from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any

from .config import settings
from .oddsapi import canonical_team

log = logging.getLogger("goaledge.apifootball")

_DASHBOARD_HOST = "https://v3.football.api-sports.io"
_RAPID_HOST = "https://api-football-v1.p.rapidapi.com/v3"

_probe: dict[str, Any] | None = None
#: (fetched_at, canonical_team -> list of injury dicts)
_INJURY_CACHE: tuple[datetime, dict[str, list[dict[str, Any]]]] | None = None
_INJURY_TTL = timedelta(hours=6)

#: Positions that, when missing, cut attack rather than inflate defence.
_ATTACK_POSITIONS = frozenset(
    {"attacker", "forward", "centre-forward", "center forward", "striker", "winger"}
)
_GK_POSITIONS = frozenset({"goalkeeper", "goalie", "gk"})


def _headers(key: str, *, rapid: bool) -> dict[str, str]:
    headers = {
        "x-apisports-key": key,
        "x-rapidapi-key": key,
        "User-Agent": "GoalEdge/1.0",
    }
    if rapid:
        headers["x-rapidapi-host"] = "api-football-v1.p.rapidapi.com"
    return headers


def _get(url: str, headers: dict[str, str]) -> dict[str, Any]:
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=settings.api_football_timeout_seconds) as resp:
        return json.loads(resp.read().decode())


def probe() -> dict[str, Any]:
    """Hit ``/status`` once and remember whether the key is usable."""
    global _probe
    if _probe is not None:
        return _probe
    key = (settings.api_football_key or "").strip()
    if not key:
        _probe = {"ok": False, "reason": "API_FOOTBALL_KEY is not set"}
        return _probe

    attempts: list[str] = []
    for host, rapid in ((_DASHBOARD_HOST, False), (_RAPID_HOST, True)):
        url = f"{host.rstrip('/')}/status"
        try:
            payload = _get(url, _headers(key, rapid=rapid))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", "replace")[:300]
            attempts.append(f"{host} HTTP {exc.code}: {body}")
            continue
        except urllib.error.URLError as exc:
            attempts.append(f"{host} unreachable: {exc.reason}")
            continue

        errors = payload.get("errors") or {}
        if errors:
            attempts.append(f"{host} rejected key: {errors}")
            continue
        account = payload.get("response") if isinstance(payload.get("response"), dict) else {}
        _probe = {"ok": True, "account": account, "host": host, "rapid": rapid}
        log.info("api-football: authenticated via %s", host)
        return _probe

    _probe = {
        "ok": False,
        "reason": (
            "API-Football did not accept this value. The dashboard host wants the "
            "32-character application key from https://dashboard.api-football.com/ "
            "(Account → API-KEY), not a RapidAPI app id. "
            + " | ".join(attempts)
        ),
        "attempts": attempts,
    }
    log.warning("api-football: %s", _probe["reason"])
    return _probe


def available() -> bool:
    return bool(probe().get("ok"))


def _request(path: str, params: dict[str, str]) -> list[Any]:
    info = probe()
    if not info.get("ok"):
        return []
    key = (settings.api_football_key or "").strip()
    host = info.get("host") or settings.api_football_base_url
    query = urllib.parse.urlencode(params)
    url = f"{host.rstrip('/')}/{path.lstrip('/')}?{query}"
    try:
        payload = _get(url, _headers(key, rapid=bool(info.get("rapid"))))
    except (urllib.error.HTTPError, urllib.error.URLError) as exc:
        log.warning("api-football: %s failed: %s", path, exc)
        return []
    errors = payload.get("errors") or {}
    if errors:
        log.warning("api-football: %s errors: %s", path, errors)
        return []
    data = payload.get("response")
    return data if isinstance(data, list) else []


def _position(player: dict) -> str:
    raw = (
        (player.get("player") or {}).get("type")
        or (player.get("player") or {}).get("position")
        or ""
    )
    return str(raw).strip().lower()


def _team_name(row: dict) -> str:
    team = row.get("team") or {}
    return team.get("name") or ""


def injuries_by_team(*, days: int = 2, force: bool = False) -> dict[str, list[dict[str, Any]]]:
    """Upcoming-window injuries, keyed by canonical team name.

    One request per calendar date. Missing or rejected keys return {}.
    """
    global _INJURY_CACHE
    now = datetime.now(timezone.utc)
    if _INJURY_CACHE and not force and now - _INJURY_CACHE[0] < _INJURY_TTL:
        return _INJURY_CACHE[1]
    if not available():
        return {}

    grouped: dict[str, list[dict[str, Any]]] = {}
    today = now.date()
    for offset in range(max(1, days)):
        day = (today + timedelta(days=offset)).isoformat()
        for row in _request("injuries", {"date": day}):
            if not isinstance(row, dict):
                continue
            name = canonical_team(_team_name(row))
            if not name:
                continue
            player = row.get("player") or {}
            grouped.setdefault(name, []).append(
                {
                    "player": player.get("name") or "Unknown",
                    "type": player.get("type") or player.get("reason") or "Unavailable",
                    "reason": player.get("reason") or "",
                    "position": _position(row) or str(player.get("type") or "").lower(),
                }
            )
    _INJURY_CACHE = (now, grouped)
    log.info("api-football: injuries for %s team(s)", len(grouped))
    return grouped


def team_availability(team_name: str, grouped: dict[str, list[dict[str, Any]]] | None = None) -> dict[str, Any]:
    """Conservative attack/defence shades from listed absences.

    Multipliers stay inside 0.90–1.10 so a noisy injury feed cannot overwhelm
    the ratings fitted from results.
    """
    grouped = grouped if grouped is not None else injuries_by_team()
    players = grouped.get(canonical_team(team_name), [])
    attack = 1.0
    defence = 1.0
    notes: list[str] = []
    n = len(players)
    if n >= 2:
        attack *= max(0.92, 1.0 - 0.012 * n)
        defence *= min(1.08, 1.0 + 0.008 * n)
        notes.append(f"{team_name}: {n} listed absences")
    gk = [p for p in players if p.get("position") in _GK_POSITIONS]
    attackers = [p for p in players if p.get("position") in _ATTACK_POSITIONS]
    if gk:
        defence *= 1.05
        notes.append(f"{team_name}: goalkeeper listed unavailable ({gk[0]['player']})")
    if attackers:
        attack *= max(0.94, 1.0 - 0.02 * min(3, len(attackers)))
        names = ", ".join(p["player"] for p in attackers[:3])
        notes.append(f"{team_name}: attacking absences ({names})")
    return {
        "missing": n,
        "attack_mult": max(0.90, min(1.0, attack)),
        "defence_mult": max(1.0, min(1.10, defence)),
        "notes": notes,
        "players": players,
    }


def shade_lambdas(
    lam_home: float,
    lam_away: float,
    home_name: str,
    away_name: str,
    grouped: dict[str, list[dict[str, Any]]] | None = None,
) -> tuple[float, float, list[str]]:
    """Apply injury shades to already-fitted expected goals.

    A weaker home attack cuts ``lam_home``; a weaker home defence raises
    ``lam_away``. Same, mirrored, for the away side.
    """
    grouped = grouped if grouped is not None else injuries_by_team()
    home = team_availability(home_name, grouped)
    away = team_availability(away_name, grouped)
    new_home = lam_home * home["attack_mult"] * away["defence_mult"]
    new_away = lam_away * away["attack_mult"] * home["defence_mult"]
    notes = home["notes"] + away["notes"]
    return max(0.15, new_home), max(0.15, new_away), notes

"""Seed the database with leagues, teams, played history, standings and
upcoming fixtures with market odds.

The generated results are *simulated* from per-team strength profiles using a
fixed random seed, so every install produces the same reproducible dataset and
the prediction engine has a real history to fit against. This is demo data —
see README for swapping in a live feed.
"""
from __future__ import annotations

import json
import random
import unicodedata
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .crests import crest_for_slug, FALLBACK_LOGO
from .models import Competition, Country, Fixture, Standing, Team

RNG_SEED = 20260114

# name, short, country, tier, avg_goals, [(team, attack, defence), ...]
LEAGUES: list[dict] = [
    {
        "name": "Premier League", "country": "England", "tier": 1, "avg_goals": 1.42,
        "teams": [
            ("Arsenal", "Arsenal", 1.55, 0.62), ("Manchester City", "Man City", 1.62, 0.66),
            ("Liverpool", "Liverpool", 1.52, 0.72), ("Chelsea", "Chelsea", 1.22, 0.88),
            ("Tottenham", "Spurs", 1.30, 1.05), ("Newcastle", "Newcastle", 1.18, 0.86),
            ("Aston Villa", "Aston Villa", 1.12, 0.94), ("Manchester United", "Man United", 1.10, 1.00),
            ("Brighton", "Brighton", 1.08, 1.10), ("West Ham", "West Ham", 0.98, 1.16),
            ("Brentford", "Brentford", 1.02, 1.12), ("Crystal Palace", "Crystal Palace", 0.94, 1.05),
            ("Fulham", "Fulham", 0.96, 1.08), ("Bournemouth", "Bournemouth", 0.98, 1.12),
            ("Everton", "Everton", 0.78, 1.02), ("Nottingham Forest", "Nottm Forest", 0.92, 1.04),
            ("Wolves", "Wolves", 0.82, 1.24), ("Leeds", "Leeds", 0.80, 1.20),
            ("Burnley", "Burnley", 0.68, 1.34), ("Sunderland", "Sunderland", 0.74, 1.22),
        ],
    },
    {
        "name": "LaLiga", "country": "Spain", "tier": 1, "avg_goals": 1.28,
        "teams": [
            ("Real Madrid", "Real Madrid", 1.58, 0.70), ("Barcelona", "Barcelona", 1.60, 0.72),
            ("Atletico Madrid", "Atletico", 1.22, 0.62), ("Athletic Club", "Athletic", 1.02, 0.74),
            ("Villarreal", "Villarreal", 1.10, 0.92), ("Real Betis", "Betis", 1.02, 0.90),
            ("Real Sociedad", "Real Sociedad", 0.96, 0.86), ("Sevilla", "Sevilla", 0.90, 0.98),
            ("Valencia", "Valencia", 0.86, 1.00), ("Celta Vigo", "Celta", 0.94, 1.00),
            ("Girona", "Girona", 0.88, 1.10), ("Getafe", "Getafe", 0.72, 0.86),
            ("Osasuna", "Osasuna", 0.80, 0.96), ("Rayo Vallecano", "Rayo", 0.80, 0.90),
            ("Espanyol", "Espanyol", 0.76, 1.06), ("Mallorca", "Mallorca", 0.74, 0.94),
            ("Alaves", "Alaves", 0.72, 1.00), ("Elche", "Elche", 0.72, 1.06),
            ("Levante", "Levante", 0.68, 1.24), ("Real Oviedo", "Oviedo", 0.62, 1.20),
        ],
    },
    {
        "name": "Serie A", "country": "Italy", "tier": 1, "avg_goals": 1.34,
        "teams": [
            ("Inter Milan", "Inter", 1.60, 0.72), ("Napoli", "Napoli", 1.34, 0.70),
            ("AC Milan", "AC Milan", 1.26, 0.80), ("Juventus", "Juventus", 1.18, 0.72),
            ("Atalanta", "Atalanta", 1.34, 0.88), ("Roma", "Roma", 1.14, 0.76),
            ("Lazio", "Lazio", 1.06, 0.86), ("Bologna", "Bologna", 1.10, 0.84),
            ("Fiorentina", "Fiorentina", 1.04, 0.92), ("Como 1907", "Como", 0.96, 0.94),
            ("Torino", "Torino", 0.80, 0.92), ("Udinese", "Udinese", 0.88, 1.00),
            ("Sassuolo", "Sassuolo", 0.82, 1.08), ("Cagliari", "Cagliari", 0.84, 1.06),
            ("Genoa", "Genoa", 0.74, 0.98), ("Parma", "Parma", 0.76, 1.08),
            ("Lecce", "Lecce", 0.70, 1.10), ("Verona", "Verona", 0.68, 1.12),
            ("Pisa", "Pisa", 0.66, 1.18), ("Cremonese", "Cremonese", 0.72, 1.14),
        ],
    },
    {
        "name": "Bundesliga", "country": "Germany", "tier": 1, "avg_goals": 1.56,
        "teams": [
            ("Bayern Munich", "Bayern", 1.80, 0.72), ("Bayer Leverkusen", "Leverkusen", 1.34, 0.86),
            ("Borussia Dortmund", "Dortmund", 1.32, 0.92), ("RB Leipzig", "RB Leipzig", 1.24, 0.90),
            ("Eintracht Frankfurt", "Frankfurt", 1.28, 1.12), ("VfB Stuttgart", "Stuttgart", 1.18, 1.00),
            ("SC Freiburg", "Freiburg", 1.04, 0.96), ("Werder Bremen", "Bremen", 1.00, 1.10),
            ("Wolfsburg", "Wolfsburg", 0.94, 1.06), ("Borussia M'gladbach", "Gladbach", 0.96, 1.16),
            ("Hoffenheim", "Hoffenheim", 1.00, 1.20), ("Union Berlin", "Union Berlin", 0.82, 1.02),
            ("Mainz", "Mainz", 0.88, 1.04), ("Augsburg", "Augsburg", 0.86, 1.14),
            ("St Pauli", "St Pauli", 0.72, 1.06), ("Heidenheim", "Heidenheim", 0.80, 1.26),
            ("FC Koln", "Koln", 0.84, 1.18), ("Hamburger SV", "Hamburger SV", 0.78, 1.14),
        ],
    },
    {
        "name": "Ligue 1", "country": "France", "tier": 1, "avg_goals": 1.32,
        "teams": [
            ("Paris SG", "Paris SG", 1.72, 0.68), ("Marseille", "Marseille", 1.32, 0.88),
            ("Monaco", "Monaco", 1.24, 0.98), ("Lille", "Lille", 1.14, 0.86),
            ("Lyon", "Lyon", 1.10, 0.86), ("Nice", "Nice", 0.96, 0.96),
            ("Lens", "Lens", 1.02, 0.84), ("Strasbourg", "Strasbourg", 1.04, 0.98),
            ("Rennes", "Rennes", 0.98, 1.00), ("Toulouse", "Toulouse", 0.94, 1.02),
            ("Brest", "Brest", 0.88, 1.06), ("Auxerre", "Auxerre", 0.74, 1.14),
            ("Nantes", "Nantes", 0.74, 1.06), ("Angers", "Angers", 0.70, 1.04),
            ("Le Havre", "Le Havre", 0.72, 1.14), ("Metz", "Metz", 0.70, 1.22),
            ("Lorient", "Lorient", 0.76, 1.20), ("Paris FC", "Paris FC", 0.80, 1.12),
        ],
    },
    {
        "name": "Eredivisie", "country": "Netherlands", "tier": 1, "avg_goals": 1.60,
        "teams": [
            ("PSV", "PSV", 1.78, 0.76), ("Ajax", "Ajax", 1.44, 0.92),
            ("Feyenoord", "Feyenoord", 1.42, 0.92), ("AZ Alkmaar", "AZ", 1.30, 1.02),
            ("FC Utrecht", "Utrecht", 1.20, 0.98), ("FC Twente", "Twente", 1.16, 1.00),
            ("Go Ahead Eagles", "Go Ahead", 1.06, 1.14), ("NEC Nijmegen", "NEC", 1.02, 1.10),
            ("SC Heerenveen", "Heerenveen", 0.98, 1.22), ("Fortuna Sittard", "Fortuna", 0.90, 1.24),
            ("Sparta Rotterdam", "Sparta", 0.88, 1.28), ("PEC Zwolle", "Zwolle", 0.86, 1.30),
            ("Heracles", "Heracles", 0.82, 1.38), ("NAC Breda", "NAC", 0.86, 1.30),
            ("Excelsior", "Excelsior", 0.78, 1.36), ("Telstar", "Telstar", 0.72, 1.42),
            ("Volendam", "Volendam", 0.76, 1.44), ("Willem II", "Willem II", 0.74, 1.36),
        ],
    },
    {
        "name": "Primeira Liga", "country": "Portugal", "tier": 1, "avg_goals": 1.28,
        "teams": [
            ("Sporting CP", "Sporting", 1.56, 0.68), ("FC Porto", "Porto", 1.50, 0.70),
            ("Benfica", "Benfica", 1.46, 0.74), ("SC Braga", "Braga", 1.22, 0.82),
            ("Vitoria Guimaraes", "Guimaraes", 1.00, 0.94), ("Famalicao", "Famalicao", 0.98, 1.00),
            ("Moreirense", "Moreirense", 0.94, 1.02), ("Gil Vicente", "Gil Vicente", 0.92, 1.00),
            ("Santa Clara", "Santa Clara", 0.84, 1.08), ("Estoril", "Estoril", 0.88, 1.10),
            ("Rio Ave", "Rio Ave", 0.82, 1.12), ("Estrela Amadora", "Estrela", 0.80, 1.16),
            ("Casa Pia", "Casa Pia", 0.74, 1.14), ("Nacional Madeira", "Nacional", 0.78, 1.20),
            ("Arouca", "Arouca", 0.80, 1.18), ("AVS", "AVS", 0.72, 1.22),
            ("Tondela", "Tondela", 0.70, 1.26), ("Alverca", "Alverca", 0.68, 1.24),
        ],
    },
    {
        "name": "Super Lig", "country": "Turkey", "tier": 1, "avg_goals": 1.42,
        "teams": [
            ("Galatasaray", "Galatasaray", 1.62, 0.76), ("Fenerbahce", "Fenerbahce", 1.56, 0.78),
            ("Besiktas", "Besiktas", 1.30, 0.94), ("Trabzonspor", "Trabzonspor", 1.26, 0.94),
            ("Istanbul Basaksehir", "Basaksehir", 1.14, 1.02), ("Samsunspor", "Samsunspor", 1.12, 1.00),
            ("Goztepe", "Goztepe", 1.06, 1.06), ("Konyaspor", "Konyaspor", 0.96, 1.06),
            ("Gaziantep FK", "Gaziantep", 0.94, 1.12), ("Rizespor", "Rizespor", 0.92, 1.14),
            ("Antalyaspor", "Antalyaspor", 0.88, 1.16), ("Alanyaspor", "Alanyaspor", 0.92, 1.08),
            ("Kasimpasa", "Kasimpasa", 0.86, 1.14), ("Kayserispor", "Kayserispor", 0.82, 1.18),
            ("Eyupspor", "Eyupspor", 0.90, 1.10), ("Caykur Rizespor", "C. Rizespor", 0.84, 1.16),
            ("Kocaelispor", "Kocaelispor", 0.80, 1.20), ("Genclerbirligi", "Genclerbirligi", 0.82, 1.18),
        ],
    },
    {
        "name": "Scottish Premiership", "country": "Scotland", "tier": 1, "avg_goals": 1.36,
        "teams": [
            ("Celtic", "Celtic", 1.68, 0.70), ("Rangers", "Rangers", 1.46, 0.82),
            ("Hibernian", "Hibernian", 1.08, 0.98), ("Heart of Midlothian", "Hearts", 1.10, 0.94),
            ("Aberdeen", "Aberdeen", 1.02, 0.96), ("Motherwell", "Motherwell", 0.98, 1.04),
            ("Dundee United", "Dundee Utd", 0.90, 1.10), ("St Mirren", "St Mirren", 0.88, 1.06),
            ("Kilmarnock", "Kilmarnock", 0.86, 1.12), ("Dundee FC", "Dundee", 0.86, 1.18),
            ("Falkirk", "Falkirk", 0.82, 1.14), ("Livingston", "Livingston", 0.74, 1.16),
        ],
    },
    {
        "name": "Championship", "country": "England", "tier": 2, "avg_goals": 1.34,
        "teams": [
            ("Leicester City", "Leicester", 1.30, 0.86), ("Southampton", "Southampton", 1.24, 0.90),
            ("Ipswich Town", "Ipswich", 1.22, 0.92), ("Middlesbrough", "Middlesbrough", 1.18, 0.96),
            ("Coventry City", "Coventry", 1.16, 0.94), ("West Bromwich Albion", "West Brom", 1.06, 0.98),
            ("Norwich City", "Norwich", 1.10, 1.10), ("Millwall", "Millwall", 0.98, 0.98),
            ("Bristol City", "Bristol City", 1.04, 1.02), ("Hull City", "Hull", 0.96, 1.06),
            ("Preston", "Preston", 0.92, 1.02), ("Swansea City", "Swansea", 0.98, 1.04),
            ("Watford", "Watford", 0.96, 1.08), ("Stoke City", "Stoke", 0.90, 1.06),
            ("Blackburn Rovers", "Blackburn", 0.88, 1.10), ("Charlton Athletic", "Charlton", 0.86, 1.12),
            ("Derby County", "Derby", 0.88, 1.10), ("Oxford United", "Oxford", 0.80, 1.18),
            ("Sheffield United", "Sheffield Utd", 1.00, 1.06), ("Portsmouth", "Portsmouth", 0.82, 1.16),
            ("Queens Park Rangers", "QPR", 0.90, 1.12), ("Wrexham", "Wrexham", 0.92, 1.10),
            ("Birmingham City", "Birmingham", 1.00, 1.04), ("Sheffield Wednesday", "Sheff Wed", 0.78, 1.22),
        ],
    },
]


def slugify(value: str) -> str:
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    return "-".join(value.lower().replace("'", "").replace(".", "").split())


def _poisson_sample(rng: random.Random, lam: float) -> int:
    """Knuth's algorithm — small lambdas only, which is all football needs."""
    import math

    threshold = math.exp(-lam)
    product, k = 1.0, 0
    while True:
        product *= rng.random()
        if product <= threshold or k > 12:
            return k
        k += 1


from .prediction import MAIN_OU_LINE as _MAIN_OU_LINE  # noqa: E402
from .prediction import OU_LINES as _OU_LINES  # noqa: E402


def _make_odds(rng: random.Random, prediction: dict, overround: float = 1.06) -> dict:
    """Turn model probabilities into plausible bookmaker prices with a margin."""
    def price(p: float) -> float:
        p = max(0.03, min(0.95, p))
        return round(max(1.05, (1.0 / p) / overround), 2)

    odds = {
        "odds_home": price(prediction["home_win"]),
        "odds_draw": price(prediction["draw"]),
        "odds_away": price(prediction["away_win"]),
        "odds_over25": price(prediction["over25"]),
        "odds_under25": price(prediction["under25"]),
        "odds_btts_yes": price(prediction["btts"]),
        "odds_btts_no": price(1 - prediction["btts"]),
    }

    # Per-line Over/Under board. Lines well above the projected total get
    # very short "under" prices and long "over" ones, which is what a real
    # book shows, so these are derived from the model rather than invented.
    ou_lines: dict[str, list[float]] = {}
    for line in _OU_LINES:
        if line == _MAIN_OU_LINE:
            continue  # already in the dedicated columns
        over_p = prediction.get(f"over{str(line).replace('.', '_')}")
        if over_p is None:
            continue
        ou_lines[str(line)] = [price(over_p), price(1 - over_p)]
    if ou_lines:
        odds["ou_lines_json"] = json.dumps(ou_lines)

    return odds


def seed(db: Session, force: bool = False) -> dict:
    """Populate the database. Idempotent unless ``force`` is set."""
    existing = db.scalar(select(func.count()).select_from(Fixture))
    if existing and not force:
        return {"status": "skipped", "reason": "database already seeded", "fixtures": existing}

    rng = random.Random(RNG_SEED)
    # Late January 2026 style calendar: history behind us, fixtures ahead.
    today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    history_start = today - timedelta(days=150)

    fixtures_created = 0
    # Import here to avoid a circular import at module load time.
    from .prediction import (
    MAIN_OU_LINE,
    OU_LINES,
    build_matrix,
    expected_goals,
)

    for spec in LEAGUES:
        country = db.scalars(select(Country).where(Country.name == spec["country"])).first()
        if country is None:
            country = Country(name=spec["country"], code=spec["country"][:3].upper())
            db.add(country)
            db.flush()

        comp = db.scalars(
            select(Competition).where(
                Competition.name == spec["name"], Competition.country_id == country.id
            )
        ).first()
        if comp is None:
            comp = Competition(
                name=spec["name"],
                slug=slugify(f"{spec['country']}-{spec['name']}"),
                country_id=country.id,
                tier=spec["tier"],
                is_cup=False,
                avg_goals=spec["avg_goals"],
            )
            db.add(comp)
            db.flush()

        teams: list[Team] = []
        for name, short, attack, defence in spec["teams"]:
            team = db.scalars(select(Team).where(Team.slug == slugify(name))).first()
            if team is None:
                team = Team(
                    name=name,
                    short_name=short,
                    slug=slugify(name),
                    competition_id=comp.id,
                    country_id=country.id,
                    attack=attack,
                    defence=defence,
                    # Real club badge where one resolves, otherwise a neutral
                    # glyph. Never a generated placeholder that looks like a
                    # crest but isn't.
                    logo=crest_for_slug(slugify(name)) or FALLBACK_LOGO,
                )
                db.add(team)
                db.flush()
            teams.append(team)

        league_avg = spec["avg_goals"]
        n = len(teams)

        # ---- generate a round-robin-ish history so the model has data ----
        pairings: list[tuple[Team, Team]] = []
        for i in range(n):
            for j in range(n):
                if i == j:
                    continue
                # ~1.6 meetings per ordered pair over the season window
                if rng.random() < 0.62:
                    pairings.append((teams[i], teams[j]))
        rng.shuffle(pairings)

        home_adv = 1.16
        for idx, (home, away) in enumerate(pairings):
            kickoff = history_start + timedelta(
                days=int(idx * 150 / max(1, len(pairings))), hours=rng.choice([12, 14, 16, 18, 20])
            )
            if kickoff >= today:
                kickoff = today - timedelta(days=rng.randint(1, 20))

            lam_h = league_avg * home.attack * away.defence * home_adv
            lam_a = league_avg * away.attack * home.defence / home_adv
            hg = _poisson_sample(rng, lam_h)
            ag = _poisson_sample(rng, lam_a)

            # The market prices that were available before kick-off. Stored so
            # the model can be backtested against real closing lines.
            closing = build_matrix(lam_h, lam_a, -0.06)
            hist_prices = {
                "home_win": closing.home_win(),
                "draw": closing.draw(),
                "away_win": closing.away_win(),
                "over25": closing.over(2.5),
                "under25": 1 - closing.over(2.5),
                **{
                    f"over{str(_l).replace(chr(46), chr(95))}": closing.over(_l)
                    for _l in _OU_LINES
                },
                "btts": closing.btts(),
            }
            # bookmakers price favourites a touch shorter than the raw model
            fav = hist_prices["home_win"] >= hist_prices["away_win"]
            hist_prices["home_win"] = min(0.94, hist_prices["home_win"] * (1.03 if fav else 0.97))
            hist_prices["away_win"] = min(0.94, hist_prices["away_win"] * (0.97 if fav else 1.03))
            hist_prices["draw"] = max(0.05, 1 - hist_prices["home_win"] - hist_prices["away_win"])
            hist_odds = _make_odds(rng, hist_prices, overround=rng.uniform(1.04, 1.09))

            db.add(
                Fixture(
                    competition_id=comp.id,
                    home_team_id=home.id,
                    away_team_id=away.id,
                    kickoff=kickoff,
                    season="2025/26",
                    status="finished",
                    home_goals=hg,
                    away_goals=ag,
                    venue=f"{home.short_name} Stadium",
                    bookmaker="Bet365",
                    **hist_odds,
                )
            )
            fixtures_created += 1

        db.flush()

        # ---- standings from the generated results ----
        rows = db.scalars(select(Fixture).where(Fixture.competition_id == comp.id)).all()
        table: dict[int, dict] = {
            t.id: {"team": t, "p": 0, "w": 0, "d": 0, "l": 0, "gf": 0, "ga": 0, "pts": 0, "form": []}
            for t in teams
        }
        for fx in rows:
            hg, ag = fx.home_goals, fx.away_goals
            if hg is None or ag is None:
                continue
            h, a = table[fx.home_team_id], table[fx.away_team_id]
            h["p"] += 1; a["p"] += 1
            h["gf"] += hg; h["ga"] += ag
            a["gf"] += ag; a["ga"] += hg
            if hg > ag:
                h["w"] += 1; h["pts"] += 3; a["l"] += 1; h["form"].append("W"); a["form"].append("L")
            elif hg == ag:
                h["d"] += 1; a["d"] += 1; h["pts"] += 1; a["pts"] += 1
                h["form"].append("D"); a["form"].append("D")
            else:
                a["w"] += 1; a["pts"] += 3; h["l"] += 1; a["form"].append("L"); a["form"].append("W")

        ordered = sorted(
            table.values(),
            key=lambda r: (-r["pts"], -(r["gf"] - r["ga"]), -r["gf"]),
        )
        for pos, row in enumerate(ordered, start=1):
            db.add(
                Standing(
                    competition_id=comp.id,
                    team_id=row["team"].id,
                    season="2025/26",
                    position=pos,
                    played=row["p"],
                    won=row["w"],
                    drawn=row["d"],
                    lost=row["l"],
                    goals_for=row["gf"],
                    goals_against=row["ga"],
                    points=row["pts"],
                    form="".join(row["form"][-5:]),
                )
            )

        # ---- upcoming fixtures with odds derived from team profiles ----
        scheduled = 0
        attempts = 0
        used: set[tuple[int, int]] = set()
        while scheduled < 8 and attempts < 400:
            attempts += 1
            home, away = rng.sample(teams, 2)
            key = (home.id, away.id)
            if key in used:
                continue
            used.add(key)

            # Model the fixture the same way the engine will, so the seeded
            # odds are consistent with the model (plus a small bookmaker skew).
            kickoff = today + timedelta(days=rng.randint(0, 4), hours=0)
            kickoff = kickoff.replace(hour=rng.choice([12, 14, 16, 18, 19, 20]), minute=rng.choice([0, 15, 30, 45]))

            ratings_h = estimate_ratings_from_profile(home, league_avg)
            ratings_a = estimate_ratings_from_profile(away, league_avg)
            lam_h, lam_a = expected_goals(ratings_h, ratings_a, league_avg)
            matrix = build_matrix(lam_h, lam_a, -0.06)
            base = {
                "home_win": matrix.home_win(),
                "draw": matrix.draw(),
                "away_win": matrix.away_win(),
                "over25": matrix.over(2.5),
                "under25": 1 - matrix.over(2.5),
                **{
                    f"over{str(_l).replace(chr(46), chr(95))}": matrix.over(_l)
                    for _l in _OU_LINES
                },
                "btts": matrix.btts(),
            }
            # bookmaker skew: nudge favourite probabilities up slightly
            skew = lambda p, fav: min(0.94, p * (1.03 if fav else 0.97))  # noqa: E731
            fav_home = base["home_win"] >= base["away_win"]
            base["home_win"] = skew(base["home_win"], fav_home)
            base["away_win"] = skew(base["away_win"], not fav_home)
            base["draw"] = max(0.05, 1 - base["home_win"] - base["away_win"])

            odds = _make_odds(rng, base, overround=rng.uniform(1.04, 1.09))
            db.add(
                Fixture(
                    competition_id=comp.id,
                    home_team_id=home.id,
                    away_team_id=away.id,
                    kickoff=kickoff,
                    season="2025/26",
                    status="scheduled",
                    venue=f"{home.short_name} Stadium",
                    bookmaker="Bet365",
                    **odds,
                )
            )
            scheduled += 1
            fixtures_created += 1

    db.commit()

    return {
        "status": "seeded",
        "competitions": len(LEAGUES),
        "teams": db.scalar(select(func.count()).select_from(Team)),
        "fixtures": fixtures_created,
    }


class _ProfileRatings:
    """Minimal stand-in for prediction.Ratings built straight from the profile."""

    def __init__(self, attack: float, defence: float):
        self.attack = attack
        self.defence = defence
        self.played = 20
        self.attack_raw = attack
        self.defence_raw = defence


def estimate_ratings_from_profile(team: Team, league_avg: float) -> _ProfileRatings:
    return _ProfileRatings(team.attack, team.defence)

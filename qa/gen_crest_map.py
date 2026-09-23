"""Generate an explicit GoalEdge-slug -> dataset-filename map.

Fuzzy-matching club names is how you end up with the wrong badge (Monza on
Pisa). So this builds the mapping from an explicit, hand-checked alias table and
then *verifies* every entry resolves to a real file in the dataset.

Run from repo root:  python qa/gen_crest_map.py
Writes:              backend/app/crest_map.py
"""
from __future__ import annotations

import json
import sys
import unicodedata
import urllib.parse
import urllib.request
from pathlib import Path
# Club names contain accents; the Windows console default (cp1252) cannot encode
# them and would crash the report before it printed anything useful.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
DATASET = "https://raw.githubusercontent.com/luukhopman/football-logos/master/logos"

#: league folder in the dataset -> the GoalEdge competition it feeds
LEAGUE_DIRS = {
    "England - Premier League": "Premier League",
    "Spain - LaLiga": "LaLiga",
    "Italy - Serie A": "Serie A",
    "Germany - Bundesliga": "Bundesliga",
    "France - Ligue 1": "Ligue 1",
    "Netherlands - Eredivisie": "Eredivisie",
    "Portugal - Liga Portugal": "Primeira Liga",
    "Türkiye - Süper Lig": "Super Lig",
    "Scotland - Scottish Premiership": "Scottish Premiership",
}

#: GoalEdge team slug -> exact club name as it appears in the dataset filename.
#: Every entry checked against the listing; no guessing.
ALIASES: dict[str, str] = {
    # ------------------------------------------------------- Premier League
    "arsenal": "Arsenal FC",
    "manchester-city": "Manchester City",
    "liverpool": "Liverpool FC",
    "chelsea": "Chelsea FC",
    "tottenham": "Tottenham Hotspur",
    "newcastle": "Newcastle United",
    "aston-villa": "Aston Villa",
    "manchester-united": "Manchester United",
    "brighton": "Brighton & Hove Albion",
    # dataset uses full names below; exact strings copied from the listing
    "west-ham": "",  # not in dataset
    "brentford": "Brentford FC",
    "crystal-palace": "Crystal Palace",
    "fulham": "Fulham FC",
    "bournemouth": "AFC Bournemouth",
    "everton": "Everton FC",
    "nottingham-forest": "Nottingham Forest",
    "wolves": "",  # not in dataset
    "leeds": "Leeds United",
    "burnley": "",  # not in dataset
    "sunderland": "Sunderland AFC",
    # ------------------------------------------------------------- LaLiga
    "real-madrid": "Real Madrid",
    "barcelona": "FC Barcelona",
    "atletico-madrid": "Atlético de Madrid",
    "athletic-club": "Athletic Bilbao",
    "villarreal": "Villarreal CF",
    "real-betis": "Real Betis Balompié",
    "real-sociedad": "Real Sociedad",
    "sevilla": "Sevilla FC",
    "valencia": "Valencia CF",
    "celta-vigo": "Celta de Vigo",
    "girona": "",  # not in dataset
    "getafe": "Getafe CF",
    "osasuna": "CA Osasuna",
    "rayo-vallecano": "Rayo Vallecano",
    "espanyol": "RCD Espanyol Barcelona",
    "mallorca": "",  # not in dataset
    "alaves": "Deportivo Alavés",
    "elche": "Elche CF",
    "levante": "Levante UD",
    "real-oviedo": "",  # not in dataset
    # ------------------------------------------------------------- Serie A
    "inter-milan": "Inter Milan",
    "napoli": "SSC Napoli",
    "ac-milan": "AC Milan",
    "juventus": "Juventus FC",
    "atalanta": "Atalanta BC",
    "roma": "AS Roma",
    "lazio": "SS Lazio",
    "bologna": "Bologna FC 1909",
    "fiorentina": "ACF Fiorentina",
    "como-1907": "Como 1907",
    "torino": "Torino FC",
    "udinese": "Udinese Calcio",
    "sassuolo": "US Sassuolo",
    "cagliari": "Cagliari Calcio",
    "genoa": "Genoa CFC",
    "parma": "Parma Calcio 1913",
    "lecce": "US Lecce",
    "verona": "",  # not in dataset (only AC Monza / Venezia / Frosinone present)
    "pisa": "",  # not in dataset
    "cremonese": "",  # not in dataset
    # ---------------------------------------------------------- Bundesliga
    "bayern-munich": "Bayern Munich",
    "bayer-leverkusen": "Bayer 04 Leverkusen",
    "borussia-dortmund": "Borussia Dortmund",
    "rb-leipzig": "RB Leipzig",
    "eintracht-frankfurt": "Eintracht Frankfurt",
    "vfb-stuttgart": "VfB Stuttgart",
    "sc-freiburg": "SC Freiburg",
    "werder-bremen": "SV Werder Bremen",
    "wolfsburg": "",  # not in dataset
    "borussia-mgladbach": "Borussia Mönchengladbach",
    "hoffenheim": "TSG 1899 Hoffenheim",
    "union-berlin": "1.FC Union Berlin",
    "mainz": "1.FSV Mainz 05",
    "augsburg": "FC Augsburg",
    "st-pauli": "",  # not in dataset
    "heidenheim": "",  # not in dataset
    "fc-koln": "1.FC Köln",
    "hamburger-sv": "Hamburger SV",
    # ------------------------------------------------------------- Ligue 1
    "paris-sg": "Paris Saint-Germain",
    "marseille": "Olympique Marseille",
    "monaco": "AS Monaco",
    "lille": "LOSC Lille",
    "lyon": "Olympique Lyon",
    "nice": "OGC Nice",
    "lens": "RC Lens",
    "strasbourg": "RC Strasbourg Alsace",
    "rennes": "Stade Rennais FC",
    "toulouse": "FC Toulouse",
    "brest": "Stade Brestois 29",
    "auxerre": "AJ Auxerre",
    "nantes": "",  # not in dataset
    "angers": "Angers SCO",
    "le-havre": "Le Havre AC",
    "metz": "",  # not in dataset
    "lorient": "FC Lorient",
    "paris-fc": "Paris FC",
    # ---------------------------------------------------------- Eredivisie
    "psv": "PSV Eindhoven",
    "ajax": "Ajax Amsterdam",
    "feyenoord": "Feyenoord Rotterdam",
    "az-alkmaar": "AZ Alkmaar",
    "fc-utrecht": "FC Utrecht",
    "fc-twente": "FC Twente Enschede",
    "go-ahead-eagles": "Go Ahead Eagles",
    "nec-nijmegen": "NEC Nijmegen",
    "sc-heerenveen": "SC Heerenveen",
    "fortuna-sittard": "Fortuna Sittard",
    "sparta-rotterdam": "Sparta Rotterdam",
    "pec-zwolle": "PEC Zwolle",
    "heracles": "",  # not in dataset
    "nac-breda": "",  # not in dataset
    "excelsior": "Excelsior Rotterdam",
    "telstar": "SC Telstar",
    "volendam": "",  # not in dataset
    "willem-ii": "Willem II Tilburg",
    # ------------------------------------------------------- Primeira Liga
    "sporting-cp": "Sporting CP",
    "fc-porto": "FC Porto",
    "benfica": "SL Benfica",
    "sc-braga": "SC Braga",
    "vitoria-guimaraes": "Vitória Guimarães SC",
    "famalicao": "FC Famalicão",
    "moreirense": "Moreirense FC",
    "gil-vicente": "Gil Vicente FC",
    "santa-clara": "CD Santa Clara",
    "estoril": "GD Estoril Praia",
    "rio-ave": "Rio Ave FC",
    "estrela-amadora": "CF Estrela Amadora",
    "casa-pia": "Casa Pia AC",
    "nacional-madeira": "CD Nacional",
    "arouca": "FC Arouca",
    "avs": "",  # not in dataset
    "tondela": "",  # not in dataset
    "alverca": "FC Alverca",
    # ----------------------------------------------------------- Super Lig
    "galatasaray": "Galatasaray",
    "fenerbahce": "Fenerbahce",
    "besiktas": "Besiktas JK",
    "trabzonspor": "Trabzonspor",
    "istanbul-basaksehir": "Basaksehir FK",
    "samsunspor": "Samsunspor",
    "goztepe": "Göztepe",
    "konyaspor": "Konyaspor",
    "gaziantep-fk": "Gaziantep FK",
    "rizespor": "Caykur Rizespor",
    "antalyaspor": "",  # not in dataset
    "alanyaspor": "Alanyaspor",
    "kasimpasa": "Kasimpasa",
    "kayserispor": "",  # not in dataset
    "eyupspor": "Eyüpspor",
    "caykur-rizespor": "Caykur Rizespor",
    "kocaelispor": "Kocaelispor",
    "genclerbirligi": "Genclerbirligi Ankara",
    # ------------------------------------------------ Scottish Premiership
    "celtic": "Celtic FC",
    "rangers": "Rangers FC",
    "hibernian": "Hibernian FC",
    "heart-of-midlothian": "Heart of Midlothian FC",
    "aberdeen": "Aberdeen FC",
    "motherwell": "Motherwell FC",
    "dundee-united": "Dundee United FC",
    "st-mirren": "St Mirren FC",
    "kilmarnock": "Kilmarnock FC",
    "dundee-fc": "Dundee FC",
    "falkirk": "Falkirk FC",
    "livingston": "",  # not in dataset
}


def slugify(value: str) -> str:
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    return "-".join(value.lower().replace("'", "").replace(".", "").split())


def dataset_listing() -> dict[str, str]:
    """{dataset filename slug: 'League Dir/Club Name.png'} for every league."""
    out: dict[str, str] = {}
    for folder in LEAGUE_DIRS:
        url = f"https://api.github.com/repos/luukhopman/football-logos/contents/logos/{urllib.parse.quote(folder)}"
        req = urllib.request.Request(url, headers={"User-Agent": "GoalEdgeAI/1.0"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            files = json.loads(resp.read().decode())
        for f in files:
            if f["name"].endswith(".png"):
                club = f["name"][:-4]
                out[slugify(club)] = f"{folder}/{f['name']}"
    return out


def main() -> int:
    listing = dataset_listing()
    print(f"dataset files: {len(listing)}")

    resolved: dict[str, str] = {}
    unresolved: list[str] = []
    skipped: list[str] = []
    for ge_slug, club_name in ALIASES.items():
        # An explicit empty string marks a club the dataset genuinely lacks.
        # Recording that deliberately is better than silently falling through
        # to a wrong badge.
        if not club_name:
            skipped.append(ge_slug)
            continue
        key = slugify(club_name)
        if key in listing:
            resolved[ge_slug] = listing[key]
        else:
            unresolved.append(f"{ge_slug} -> {club_name!r} (slug {key!r})")

    print(f"explicitly unmapped (no dataset art): {len(skipped)}")

    print(f"resolved: {len(resolved)}")
    if unresolved:
        print("\nUNRESOLVED (need an exact dataset name):")
        for u in unresolved:
            print("  -", u)
        # Dump the dataset's own names so the alias table can be corrected by
        # copying them verbatim instead of guessing spelling again.
        print("\n=== DATASET CLUB NAMES BY LEAGUE (copy exact) ===")
        for folder in LEAGUE_DIRS:
            names = sorted(
                path.split("/", 1)[1][:-4]
                for path in listing.values()
                if path.startswith(folder + "/")
            )
            print(f"\n-- {folder} ({len(names)})")
            print("   " + " | ".join(names))

    body = (
        '"""Generated by qa/gen_crest_map.py -- do not edit by hand.\n\n'
        "GoalEdge team slug -> path within the open club-logo dataset.\n"
        "Regenerate after editing the alias table in the generator.\n"
        '"""\n\n'
        "CREST_FILES: dict[str, str] = "
        + json.dumps(resolved, indent=4, ensure_ascii=False, sort_keys=True)
        + "\n"
    )
    (ROOT / "backend" / "app" / "crest_map.py").write_text(body, encoding="utf-8", newline="\n")
    print(f"\nwrote backend/app/crest_map.py with {len(resolved)} entries")
    return 1 if unresolved else 0


if __name__ == "__main__":
    raise SystemExit(main())

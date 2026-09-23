"""Check the top-league matcher, including the European cups.

Flashscore names the senior continental cups with a round suffix
("Champions League - League phase"). A tail test that rejected every "league
phase" therefore rejected the tournament itself -- which is why the sidebar and
the "Top leagues" filter never offered UCL or UEL. This pins the fixed rule.

Run:  python qa/top-league-cups-check.py
"""
import sys

sys.path.insert(0, "backend")
from app import flashscore as fs  # noqa: E402

failed = 0


def expect(country, comp, want):
    global failed
    got = fs.is_top_league(country, comp)
    mark = "ok  " if got == want else "FAIL"
    if got != want:
        failed += 1
    print(f"{mark} {country} - {comp}  -> {got} (want {want})")


print("--- senior leagues stay pinned ---")
expect("England", "Premier League", True)
expect("Spain", "LaLiga", True)
expect("Italy", "Serie A", True)
expect("Germany", "Bundesliga", True)
expect("France", "Ligue 1", True)
expect("Netherlands", "Eredivisie", True)

print("--- the continental cups, in every name the feed uses ---")
expect("Europe", "Champions League", True)
expect("Europe", "Champions League - League phase", True)
expect("Europe", "UEFA Champions League - League phase", True)
expect("Europe", "Europa League", True)
expect("Europe", "Europa League - League phase", True)
expect("Europe", "UEFA Europa League - League phase", True)
expect("Europe", "Champions League - Group stage", True)

print("--- but not a different competition sharing the name ---")
expect("Europe", "UEFA Champions League Women - League phase", False)
expect("Europe", "Champions League Women", False)
expect("Europe", "Champions League Qualification", False)
expect("Europe", "Champions League - Play Offs", False)
expect("Europe", "Europa League Qualification", False)
expect("Europe", "UEFA Europa Cup Women - Qualification", False)
expect("Europe", "UEFA Youth League - Winners Play Offs", False)

print("--- and the domestic false positives stay out ---")
expect("England", "Premier League Cup", False)
expect("England", "Premier League 2", False)
expect("England", "Premier League U18", False)
expect("Netherlands", "Eredivisie Women", False)
expect("Kenya", "Premier League", False)
expect("Brazil", "Serie A Betano", False)
expect("Asia", "AFC Champions League - League phase", False)
expect("Africa", "CAF Champions League - Qualification", False)

print()
if failed:
    print(f"{failed} case(s) failed")
    sys.exit(1)
print("OK  top-league cups rule holds")

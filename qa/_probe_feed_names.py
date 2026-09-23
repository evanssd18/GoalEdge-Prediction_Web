import sys
sys.path.insert(0, "backend")
from app import flashscore as fs

# Pull a day feed and report every competition name that mentions Champions/Europa.
for day in ("2026-09-16", "2026-09-23", "2026-09-30", "today"):
    try:
        matches = fs.fetch_matches_for_day(day)
    except Exception as e:
        print(day, "ERR", e)
        continue
    names = sorted({(m.country, m.competition) for m in matches
                    if "champ" in m.competition.lower() or "europa" in m.competition.lower()})
    print(f"{day}: {len(matches)} matches; CL/EL comps:")
    for n in names:
        print("   ", n)

import sqlite3
c = sqlite3.connect('backend/goaledge.db')
cur = c.cursor()
cols = [r[1] for r in cur.execute("PRAGMA table_info(fixtures)").fetchall()]
print("fixture cols:", cols)
# Find any stored minute text containing 90+
for col in cols:
    if 'minute' in col.lower():
        rows = cur.execute(f"SELECT id, status, kickoff, {col} FROM fixtures WHERE {col} LIKE '%90%' LIMIT 20").fetchall()
        print(f"{col} with 90:", rows)
# live rows with their kickoff
print("live rows:")
for r in cur.execute("SELECT id, status, kickoff, home_goals, away_goals FROM fixtures WHERE status='live' LIMIT 30").fetchall():
    print("  ", r)

# GoalEdge AI — Football Predictions & AI Betting Tips

A full-stack football match prediction site in the style of
[sportytrader.com](https://www.sportytrader.com/en/betting-tips/football/): daily
tips, model probabilities, bookmaker odds, value-bet detection, expected goals,
and an AI-written match preview for every fixture.

**Stack:** FastAPI + SQLAlchemy + SQLite (backend) · vanilla HTML/CSS/JS
(frontend, no build step) · Dixon-Coles Poisson model + optional LLM layer.

---

## Quick start

```powershell
cd backend

# 1. create a virtual environment (uv or plain venv both work)
uv venv .venv
#    or: python -m venv .venv

# 2. install dependencies
uv pip install -r requirements.txt
#    or: .\.venv\Scripts\python.exe -m pip install -r requirements.txt

# 3. run it
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8080
```

Open **http://127.0.0.1:8080** — the frontend and the API are served from the
same origin, so there is no CORS setup and no separate dev server.

On first boot the app creates the database and seeds ~2,150 fixtures across 10
leagues and 186 teams (takes a few seconds). Interactive API docs live at
**/docs**.

> **No Python?** Any 3.11+ interpreter works. If `python` is not on your PATH,
> point at it explicitly.

---

## What's in it

| Page               | What it does                                                                                                                                                                      |
| ------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Livescores**     | A Flashscore-style board: matches grouped by competition, live first, with each match's date and exact kick-off time. Any day in a **two-year window** via the date picker. Clicking a row opens that **match page**. No predictions are mixed in. |
| **Match page**     | What a row opens into, laid out like the reference board: the minute-by-minute clock, the **Match information** block (venue, city, referee, attendance), the event timeline with each goal's running score, the match statistics, both **lineups** (formation, shirt numbers, captain, keeper, ratings, coaches), and a **Full analysis & prediction** button into the model page. Works for any match id — including one the engine has no stored fixture for. |
| **Predictions**    | Every upcoming fixture with the model's headline tip, probability, edge, best price, confidence and star rating. Filter by date, competition, confidence, team, or value-only.    |
| **Value Bets**     | Selections where the model's probability beats the bookmaker's _de-vigged_ implied probability — with edge, expected value and quarter-Kelly stake sizing.                        |
| **Results**        | Every finished match graded against the pick the model published before kick-off, filterable by the **same market set the Predictions tab offers** — so "Correct Score" answers *how the scoreline calls did*, not merely *which matches had one*. |
| **Performance**    | A live backtest (accuracy + log loss), the published tip record with ROI, and a walkthrough of how the model works.                                                               |
| **Fixture detail** | Full 1X2 / O-U 2.5 / BTTS breakdown (model vs market side by side), top correct-score lines, both teams' form, head-to-head, key factors, risk notes, and the AI-written preview. |
| **Admin** (`#/admin`) | Administrator only. Live dashboard, a **colour / hover / layout editor**, user management and maintenance actions, and an append-only audit log. See [The admin panel](#the-admin-panel). |

---

## The admin panel
`#/admin`. Five tabs, and a visitor without an admin account never sees any of
them — the route renders a sign-in prompt instead.

| Tab | What it does |
| --- | --- |
| **Overview** | Users total / active / blocked / online, signups over 14 days, tracked tips. |
| **Appearance** | The design editor: 20 colour tokens, 6 hover templates × 4 strengths, 4 layouts, 3 corner radii. |
| **Users** | Search, filter and sort; block, unblock, kick, promote, demote, delete. |
| **Maintenance** | Refresh fixtures, sync a day, settle tips, reseed. Each is audited; `seed` needs an explicit confirmation. |
| **Audit log** | Who changed what, newest first. Append-only — nothing in the API updates or deletes a row. |

### Appearance: three design decisions worth stating
**Why the tokens are stored server-side.** A per-browser theme changes the site
for one reader. An operator editing the brand colour expects every visitor to
see it, so the design lives in the database and is served from
`GET /api/site/design`. The existing **light/dark switch stays client-side and
independent** — the operator's palette is applied on top, in both themes.

**Why the token list is fixed rather than free-form.** Accepting arbitrary `--var`
names would let an operator define a variable nothing reads (so the change
silently does nothing) or shadow an internal one with no way to see the damage.
The list is exactly the set the stylesheet consumes.

**Why colours are validated, not escaped.** These values are interpolated into a
`<style>` element, where HTML escaping is meaningless and the only thing that
matters is that the value cannot close the declaration or the block it sits in.
A strict colour grammar rejects `red; } body { display:none }` at the door —
and the client re-checks before writing, because this is the rendering path.

An invalid value is **ignored, never reverted**. That distinction is a bug fix:
falling back to the default meant a mistyped colour silently turned a saved
purple site green and reported a successful save. Editing is also live-previewed on the
real page, and nothing reaches visitors until **Save**.

### Two security fixes found while building this
Both were pre-existing, and both are the kind that look fine from the outside:

- **Four `/admin/*` routes had no authentication.** `refresh/run`, `sync`,
  `settle` and `seed` carried `tags=["admin"]` and nothing else — a tag is
  documentation, not a dependency. Anyone who knew the path could regenerate the
database. All four now depend on `get_current_admin`, and `seed` additionally
  requires an explicit `force=true` so an accidental call is refused rather than
  quietly overwriting rows.
- **The frontend never sent its token.** Only `/auth/me` attached the
  `Authorization` header; `api()` did not. Any authenticated endpoint was
  therefore unreachable from the browser, which is how the whole admin panel
  arrived anonymous. The token is now attached centrally, and an explicit header
  still wins for the rare call that must be unauthenticated.

Granting admin has no API route at all — there is no bootstrap endpoint, because
one would itself be a privilege-escalation hole. Promote the first administrator
directly in the database (`is_admin = 1`); after that the Users tab does it.

### A CSS-specificity trap the panel exposed

The stylesheet declares the same colour tokens twice: on `:root` (dark) and again
on `html[data-theme="light"]`. The light block is an element+attribute selector,
which **outranks a bare `:root`** — so an operator's colours were applied in dark
mode and silently ignored in light mode. Since the browser default resolves to
light for most readers, the feature would have looked broken for the majority.
The token block is written to `html:root` with `!important`: it matches the same
element at the same specificity, and source order then decides.

---

## The Livescore board
Day feed, grouped by competition, live matches floating to the top. Two details
are worth stating plainly because they are easy to get wrong:

**The date range is two years, not three days.** `/api/livescores?date=` accepts
`today` / `tomorrow` / `yesterday` or any `YYYY-MM-DD` inside a **730-day** window.
A date outside it is a `400` carrying the bounds, deliberately not an empty board
— "no football that day" and "that day is not available" are different claims and
the UI says which applies.

**The live "minute" on the board is derived, and says so.** The **day** feed
publishes no running clock. This was established by probing it directly, not
assumed:

| Field | What it actually is |
| --- | --- |
| `AC` (`dc_1` `DB`) | a *phase code* — `13` or `12`, keyed to the kick-off slot |
| `AO` | a Unix timestamp of the **last state change** — it did not move across an 80-second sample |
| `IB` (`df_sui_1`) | the minute of the last recorded *event*, so it only moves when someone scores |

The minute is therefore computed from kick-off, which the feed *does* publish
exactly. The board renders it with a `~` prefix, a title explaining it, and
`minute_source: "estimated"` on the API row — so a derived number is never
presented as a measured one. The kick-off time beside it is exact.

**The match page shows a continuous MM:SS clock, counted from kick-off.** The
board's estimate exists because a day feed covering hundreds of matches cannot
carry a per-match clock; opening a match replaces `~63'` with `63:24`, ticking
once a second.

It is counted from the **published kick-off**, not from the last incident. That
was a deliberate reversal, and the reasoning is worth keeping because the
intuitive answer is wrong. Anchoring on the latest published minute makes the
clock a function of the last thing that happened, so it stalls until someone
scores — and a match whose feed carries no incident at all gets no clock. Kick-off
is a timestamp the feed always ships and is exact, so counting from it is right
even when nothing has happened yet, which is also the only way to show real
seconds. `clock.source` records which was used:

| `source` | When | Shown as |
| --- | --- | --- |
| `derived` | a kick-off exists (the normal case) | plain `63:24` |
| `feed` | no usable kick-off, so the feed's last incident anchors it | marked, because it lags |

The offset applies from the second half: real time since kick-off minus the
interval **is** the match clock, so the clock is continuous from 0:00.
`period_offset` carries the interval so the second half resumes past 45:00 rather
than restarting at it.

The display ticks locally once a second and the network poll runs every 20s; the
ticked value is derived from the server's own fields, so the two cannot disagree
for long, and a poll simply wins. The clock cell carries no `~`: it is not an
estimate of the feed's clock — the feed has none.

**Two limits, stated rather than hidden.** The model cannot tell "playing
first-half stoppage" from "walked off", because both are simply real time past
45 minutes, so a match in real stoppage reads `HT` a few minutes early. And the
stale bound assumes a 15-minute interval, which is a convention rather than a
measurement. Neither affects the second half, where the arithmetic is exact.

That feed's vocabulary had to be read rather than guessed, and two traps are
worth recording because both are silent:

- **`AC` means different things in the two feeds.** In the day feed it is a
  phase code; in the summary feed it is the *period name*; in the history feed
  it is a *status code* (3 = finished). Three meanings, one key.
- **A goal carries no `IK`.** Incidents are typed by the `IE` code (3 = goal,
  1 = yellow, 7 = substitution). Keying the parser off the human-readable `IK`
  field silently dropped every goal — the timeline showed the bookings but not
the score. `qa/probe-match-feed.py` dumps the real payload's keys, and that is
  how the codes were established.

**The summary feed's incident list is abridged.** It reports a subset of a
match's events, so a 1-0 can legitimately arrive with no goal in its timeline.
The page says so rather than inventing the missing goal to match the score:
the scoreline is the published fact, and the timeline is exactly what the feed
reported.

**One record can hold several incidents.** A goal record carries its assist and
a substitution record carries *both* players, as consecutive `IE` groups inside
one record. Reading one incident per record halves every substitution and
mislabels a goal as an assist. Two further traps sit inside that:

- **The minute is stamped once**, on the first incident of the record, so every
  later group inherits it. A group read without it has no time and is dropped.
- **`IE=6` is the player going OFF and `IE=7` the one coming on** — the opposite
  of what the digits suggest. Getting it backwards puts the substituted player's
  name on the pitch.

**The separators are the two-byte UTF-8 form.** Fields are split on `\u00ac` and
`\u00f7`, not on the raw bytes `\xac`/`\xf7`. Matching the single byte appears to
work, which is exactly why it is worth stating: `\xc2\xac` *contains* `\xac`, but
so does the last byte of any accented letter — an `é` is `\xc3\xa9`, and a name
containing one gets split through the middle of its own character. That is how a
player name arrives blank while the rest of the record parses cleanly.

### What the match page reproduces
| Section | Source |
| --- | --- |
| Match information — round, venue, city, referee, attendance | `MIT`/`MIV` pairs in the summary feed |
| Timeline, with each goal's running score (`1:0`) | `IE` incidents + `INX`/`IOX` |
| Card reason and shirt number | `IL`, `IJ` |
| Substitutions as one row (on / off) | `IE=7` + `IE=6` in one record |
| Statistics | `df_st_1_` |
| Lineups — formation, shirt, captain, keeper, rating, coaches, **portraits** | `df_li_1_` |

Lineups are printed as the feed gives them; the formation is shown as a string
(`1-4-2-3-1`) rather than drawn as a pitch diagram, because a diagram built from
a guessed layout would claim more than the data does. A player the feed does not
name — some cards genuinely arrive with an empty name field — falls back to the
event type rather than rendering a blank row.

### Player portraits
Each lineup row carries the player's photograph. The feed publishes it, but not
under an obvious key, and only one of the six fields is needed — which is why
looking for a single "photo" field finds nothing and the feature appears to be
missing entirely:

| Field | Size | Role |
| --- | --- | --- |
| `LPI` | 36×36 |
| `LPZ` | 42×42 |
| `LPL` | 72×72 | **the row image** |
| `LPY` | 84×84 |
| `LPX` | 108×108 |
| `LPQ` | 126×126 | **the 2× source** |

The six are the **same portrait at six sizes**, verified by measuring the
downloaded files rather than inferred from the key names. `LPL` (72px) is the
row because it stays crisp at the ~28px the list renders it at, and `LPQ` is
offered via `srcset 2x` so a retina screen is not upscaling a 72px thumbnail.
The feed value is a bare filename (`x0F2GvEd-CKhortiC.png`), so the host is
prepended here — with a guard, since a value that already carries a scheme would
otherwise become `https://…/https://…`.

The sizes are **independent**: a record can carry some and not others, so the
parser falls back through the list rather than trusting `LPL`. And not every
player has one at all — in the sample match 41 of 42 had a portrait. A missing
photo renders **the player's initials** in the same circle, not a broken-image
glyph: a gap in the data must not look like a bug in the page. A photo that
*starts* to load and then fails has its `src` removed by an `onerror` handler so
the browser's broken icon never appears either.

A day that is **not yet synced** is ingested on first request
(`/api/livescores?sync=false` to keep it read-only), so every row carries a real
fixture id and its details open. Without that, the detail panel the board
promises would be unreachable on exactly the days that are not yet in the
database.

---

## How the prediction engine works

The whole model lives in `backend/app/prediction.py`.

1. **Team ratings.** Each side gets an `attack` and `defence` multiplier from its
   recent results. Two important details:
   - _Recency weighting_ — a match 10 games ago counts ~2.7× less than the last one.
   - _Bayesian shrinkage_ — ratings are pulled toward the league mean in
     proportion to games played, so a team with 3 matches isn't ranked like a
     30-match side.
2. **Expected goals.** Ratings combine with the league's average goals and a
   home-advantage factor give a λ (lambda) for each side.
3. **Dixon-Coles scoreline matrix.** A full Poisson grid up to 9-9, with the τ
   correction that fixes plain Poisson's known mispricing of 0-0, 1-0, 0-1 and 1-1.
4. **Markets.** 1X2, Over/Under 2.5 and BTTS are read directly off the matrix,
   along with the most likely correct scores.
5. **Market blending.** Model probabilities are blended **28% market / 72% model**
   with de-vigged bookmaker prices — the closing line contains information the
   results history does not.
6. **Value detection.** `edge = blended probability − implied probability`.
   Positive edge at acceptable odds flags a value bet, sized with quarter-Kelly
   capped at 5% of bankroll.

### Why the numbers are clamped

A naive `attack × defence × home_advantage` multiplication compounds without
bound and produces absurd lines (4.5-goal projections in a 2.8-goal league).
`expected_goals()` damps each multiplier toward 1.0 and then clamps the
**projected total** — not each side independently, which would let both sides sit
at the cap simultaneously. Measured effect across 80 fixtures:

|        | stdev of projected/league total | range           |
| ------ | ------------------------------- | --------------- |
| before | 0.244                           | 0.69 – 1.58     |
| after  | **0.163**                       | **0.79 – 1.45** |

### Honest calibration

The model is graded on **log loss**, not just accuracy, because for a betting
site the _probabilities_ must be trustworthy — the entire edge/EV calculation
depends on them:

| Metric       | Model      | Baseline                               |
| ------------ | ---------- | -------------------------------------- |
| 1X2 accuracy | 0.590      | 0.525 (always pick home)               |
| Log loss     | **0.9215** | 1.0986 (uniform) · ~1.03 (always home) |

A higher-accuracy but badly-calibrated variant was rejected during development;
the tighter, better-calibrated model produces fewer but more defensible value bets.

---

## The AI layer

`backend/app/ai.py` has two interchangeable providers behind one interface:

- **`TemplateProvider`** (default) — a deterministic sports-writer built from the
  model's own numbers. No API key, no network, no cost, works offline.
- **`OpenAIProvider`** — used when `OPENAI_API_KEY` is set.

The important design decision: **the LLM never invents the prediction.** The
statistical model's output is injected into the prompt as ground truth, and the
LLM is instructed to explain it — never to contradict it, and never to fabricate
injuries, lineups or quotes. This matters because an LLM asked to _predict_ a
match hallucinates; an LLM asked to _explain a Poisson matrix_ produces genuinely
useful copy.

Any failure (no key, timeout, rate limit, malformed JSON) degrades silently to
the template provider, so the product never breaks.

---

## Configuration

Copy `backend/.env.example` to `backend/.env`:

```ini
# Optional — leave blank for the built-in offline analyst
OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-4o-mini

# Change before deploying anywhere real
JWT_SECRET=...

# Model tuning
MARKET_BLEND=0.28            # 0 = pure model, 1 = pure market
DIXON_COLES_RHO=-0.06        # low-score correlation
VALUE_EDGE_THRESHOLD=0.045   # min edge to flag a value bet
```

Swap SQLite for Postgres by setting
`DATABASE_URL=postgresql+psycopg://user:pass@host/db`.

---

## API

Full interactive docs at `/docs`. Main endpoints:

```
GET  /api/tips                   headline tip per upcoming fixture  (date, min_confidence, limit)
GET  /api/fixtures               list + filter (date, competition_id, country, status, search, sort, page)
GET  /api/fixtures/{id}          fixture with cached prediction
GET  /api/matches/{match_id}     the match page: clock, score, events, statistics (by FEED match id)
GET  /api/matches/{match_id}/live  just the clock and score, for the page's live poll
GET  /api/fixtures/{id}/prediction   full model output (refresh=true to recompute)
GET  /api/fixtures/{id}/sportybet-markets  SportyBet board + market category index (display only)
GET  /api/fixtures/{id}/ai       AI-written preview
POST /api/fixtures/{id}/track    save the pick to the public record
GET  /api/value-bets             positive-edge selections (min_edge, min_odds, max_odds)
GET  /api/stats/overview         backtest accuracy, log loss, record
GET  /api/standings?competition_id=...
GET  /api/teams/{id}/profile     form, ratings, recent + upcoming
POST /api/auth/register|login    JWT auth
GET  /api/record                 transparent tracked-tip record
GET  /api/site/design            the colours/hover/layout the site renders with (public)
GET  /api/site/design/templates   every option the editor offers (public, static)

# --- admin (all require an admin JWT) ---
GET    /api/admin/stats          dashboard numbers
GET    /api/admin/users          search / filter / sort / page
GET    /api/admin/users/{id}     one user
POST   /api/admin/users/{id}/block|unblock|kick
PATCH  /api/admin/users/{id}/role     promote / demote / premium
DELETE /api/admin/users/{id}          delete (refuses the last admin)
PUT    /api/admin/site/design    save the design
POST   /api/admin/site/design/reset   restore the shipped design
GET    /api/admin/audit          recent admin actions
POST   /api/admin/refresh/run    pull fixtures now
POST   /api/admin/sync?date=     one day of fixtures
POST   /api/admin/settle         settle finished fixtures (cron in prod)
POST   /api/admin/seed?force=true regenerate the demo dataset
```

Predictions are cached in the `fixtures.prediction_json` column and
invalidated when `MODEL_VERSION` changes, so the model isn't recomputed per request.

---

## Project layout

```
Predictions/
├── backend/
│   ├── app/
│   │   ├── main.py         FastAPI app, static mounting, startup seed
│   │   ├── config.py       settings (env / .env)
│   │   ├── database.py     engine + session
│   │   ├── models.py       SQLAlchemy ORM
│   │   ├── schemas.py      Pydantic response models
│   │   ├── design.py       ★ site design: editable tokens, templates, validation
│   │   ├── prediction.py   ★ the Dixon-Coles engine
│   │   ├── matchdetail.py  ★ match page feed: minute-by-minute clock, events, stats
│   │   ├── ai.py           ★ AI analyst (LLM + offline fallback)
│   │   ├── markets.py      market taxonomy: categories + what the engine can price
│   │   ├── crests.py       team badge resolution (local store + remote fallback)
│   │   ├── crest_map.py    generated team -> badge file map
│   │   ├── sportybet.py    SportyBet market board (display only, never modelled)
│   │   ├── routers.py      all endpoints
│   │   ├── security.py     PBKDF2 hashing + JWT
│   │   └── seed.py         demo data generator
│   └── requirements.txt
├── frontend/
│   ├── index.html
│   ├── styles.css
│   ├── icons.js            Material Symbols as inline SVG
│   ├── admin.js            ★ admin panel (separate module, admin-only)
│   └── app.js              SPA (hash routing, no framework)
└── qa/                     browser-driven UI checks
```

---

## Tests & QA

The `qa/` folder holds browser-driven checks (run with the
browser-automation skill's `browser.mjs`). They drive the real UI and assert
what actually rendered:

- `ui-check.mjs` — predictions list, filters, value-bets page, performance page,
  fixture detail with AI panel
- `value-filter-check.mjs` — the value-only filter never marks a card VALUE with a
  negative edge
- `track-check.mjs` — sign-up via the modal, header updates, tracking a tip, and
  the record incrementing
- `sportybet-panel-check.mjs` — the SportyBet board renders, is labelled as book
  prices rather than GoalEdge picks, and the model's own panel stays separate
- `livescore-render-check.mjs` — drives the board's **real render functions**
  (`livescoreInner` / `livescoreRow` / `lsMatchDetail` / the day navigation)
  against a live payload and asserts on the produced HTML: the two-year date
  picker and its bounds, one kick-off time per row, the live minute rendered as
  an estimate, the match-page link on every row, the in-place detail panel, and
  that a row **without** a feed match id offers no page it could not load. Needs
  no browser — it loads `app.js` behind a small DOM shim — so it runs anywhere
  Node does. `livescore-check.py` covers the same route at HTTP level, including
  the window bounds and the `sync` branch.
- `match-clock-check.mjs` — the match page's live clock. Discovers a fixture
  that is genuinely in play from the board (so it does not hardcode a match id
  that will be finished next run), then asserts the label is MM:SS and **that it
  advances on its own over ~4 seconds**. That second assertion is the point: a
  clock that only moves when polled is exactly the bug this feature exists to
  fix, and it is invisible to any check that just reads the value once.
- `matchdetail-check.py` — the match-page feed: 95 assertions over the clock,
the incident parser, the match-information block and the lineups, offline (a
captured payload, so a shape change is a test failure rather than a broken page)
and then against real match ids. The regression cases it pins are the ones that
were silent when wrong: a record holding several incidents, a minute stamped
only on the first of them, `IE=6`/`IE=7` the opposite way round from the digits,
and a two-byte separator matching the low byte of an accented name.
- `match-page-check.py` — the `/api/matches/{id}` routes end to end through
  FastAPI's TestClient, against a live match and a finished one, including that
  an unknown id carries no invented teams, events or clock.
- `match-page-click.mjs` — drives the **real board in a browser**: clicks a row,
  asserts the match page opens at the right address with the right teams, that
  the Stats tab switches and renders real rows, and that **Full analysis &
  prediction** reaches the model page.
- `probe-match-feed.py` — dumps every key the live match feed actually sends for
  a match id. This is the tool that established the `IE` incident codes and the
  three different meanings of `AC`; run it after any feed-shape surprise.
- `results-market-check.mjs` — the Results market filter. It reads the
  prediction text off every visible card and requires it to belong to the market
  that was asked for. This exists because the bug it pins passed a row-count
  assertion for a whole revision: the filter narrowed the list correctly while
  every card kept showing its headline pick, so "Over 2.5 Goals" produced a
  shorter list of matches about something else entirely.
- `probe-match-sections.py` — walks every per-match feed (summary, stats,
  lineups, history, commentary, player stats, odds, standings) for a match id and
  reports which exist, their size, and their field names. Use it to find the feed
  behind a section that is not rendering.
- `probe-lineups.py` — prints the parsed lineups for a match id, so the field
  mapping can be confirmed against the reference page rather than assumed.
- `player-photo-check.py` — 26 assertions over the portrait fields: that **every
  one of the six size keys** yields a URL (so a rename cannot silently drop one),
  the row/2× preference, the fallback chain when only a small size is present,
  the no-photo case returning `None` rather than omitting the key, and the URL
  guards for values that already carry a scheme. Pins the silent regression this
  feature is prone to: the names, shirts and formations keep rendering perfectly
  while every face disappears, so the page looks fine at a glance.
- `probe-player-photo.py` — tries the plausible CDN URL shapes for a player id
  and reports which return a real image. This is how the photo host was
  established; run it if the portraits ever stop resolving.
- `fix-escaping.py` — repairs the docstring delimiters the editor's fuzzy matcher
  collapses mid-edit. Idempotent, and refuses to write a result that does not
  parse. Run it whenever an edit leaves a file with a `SyntaxError`.

All pass with **0 console errors and 0 failed requests**.

`design-check.py` — 77 assertions over the design editor and the admin guards.
The colour grammar is tested against hostile input specifically
(`red; } body{...}`, `</style><script>`, `url(javascript:...)`, `var(--accent)`),
the "invalid is ignored, not reverted" rule is pinned for both a colour and a
template, and every admin route is asserted to refuse an anonymous caller and a
signed-in non-admin. It runs against a temporary database, so it never touches
the live one.

`admin-panel-check.mjs` drives the panel in a browser: the sign-in gate, all five
tabs, editing a colour (checked in **both themes** — see the specificity trap
above), a hostile value being refused in the field, discard reverting the
preview, save persisting server-side, and the audit log recording it. It resets
the design at the end, so it is safe to re-run.

`sportybet-check.py` unit-tests the board helpers (team-name normalisation,
market grouping, payload parsing). Its group assertions pin the **delegation** to
`markets.py` rather than a second private bucket table: a distinct set of labels
for the panel heading is how the heading and the `model_priced` badge drifted
apart once already. `fake_sportybet.py` is the mock the live feed is exercised
against — see the SportyBet section above for why.

`markets-check.py` unit-tests the market taxonomy (classification, model-priced
flags, and the precedence traps). `markets-e2e.py` drives the real route through
FastAPI's TestClient against the mock and asserts the category index, ordering
and caveat. `market-index-check.mjs` confirms the index actually renders in the
browser and asserts exactly three categories carry the `model` tag.
`sportybet-panel-check.mjs` asserts the panel's two layers separately — the raw
book name and the canonical **group label** — because the group label is what the
“GoalEdge prices this” badge keys off.

`model-only-pricing-check.py` pins the invariant that makes the two pricing
surfaces agree. The engine publishes a probability for **28 markets per fixture,
not three**; honesty is carried by the prices, not by omission. So every market
without a book price must ship with `market_probability`, `odds` and `edge` all
`None`, and the UI renders `market —` / `odds —` / `edge —`. A derived market that
picked up a stray odds value would render a confident `edge +12%` beside a pick no
book ever offered, and nothing in the UI would look broken. It walks 60 cached
predictions offline — no feed, no browser, no network — and checks 7,620
selection rows.

One naming trap worth recording, found while writing the above: the Over/Under
market answers to **three different names**. Its taxonomy key is `ou`, its market
key in the payload is `ou25`, and its panel heading is `TOTAL GOALS 2.5`. A test
that guesses the wrong one silently asserts over an empty set.

`crest-check.py` asserts every mapped crest resolves and flags duplicate ids
(two clubs sharing one badge is always a copy-paste error). `icons-check.mjs`
confirms Material Symbols render as inline SVG with real path geometry and
non-zero size, that every badge loads, and that no emoji or raw ligature names
appear in the rendered text. `crest-layout-check.mjs` asserts crests carry no
circular crop, disc or border, and that the away crest sits to the right of the
away team name in both the tips list and the fixture hero.

### Running the browser checks

The `.mjs` checks drive a real browser, so they need a server. Two things bite
here, and both produce an *empty pass* rather than a failure if you get them
wrong:

- **The SportyBet board is off by default.** A server started without
  `SPORTYBET_MARKETS_ENABLED=true` returns `available: false` with a reason, so
  every board assertion would pass vacuously against a panel with no rows in it.
  `market-index-check.mjs` therefore *reports* that the board was off instead of
  claiming all-clear, and takes its port from `GOALEDGE_PORT` (default `8097`).
- `node` is not on `PATH` on every machine. Point at the interpreter explicitly
  if the bare command is not found.

```powershell
# one-time: the mock book, and a server with the board enabled
Start-Process .\backend\.venv\Scripts\python.exe -ArgumentList "qa\fake_sportybet.py 8123"

cd backend
$env:SPORTYBET_MARKETS_ENABLED="true"
$env:SPORTYBET_BASE_URL="http://127.0.0.1:8123/api"
Start-Process .\.venv\Scripts\python.exe -ArgumentList "-m","uvicorn","app.main:app","--port","8097"

# then, from the repo root
node <skill-dir>\browser.mjs http://127.0.0.1:8097/ --script ./qa/sportybet-panel-check.mjs
```

### Source guard (run this after editing Python)

The editor used on this codebase has a fuzzy, whitespace-tolerant matcher that
silently collapses a triple-quoted docstring delimiter (three quotes) down to two. That is a syntax
error, and it is easy to miss in a large diff. Two scripts make it a non-event:

```powershell
# from the repo root
python qa/check-sources.py        # fail loudly on any damaged source (exit 1)
python qa/fix_docstrings.py       # repair collapsed delimiters in place
```

`check-sources.py` re-parses every module under `backend/app` and `qa` and
reports the exact file and line for both a collapsed delimiter and any other
syntax error. `fix_docstrings.py` is idempotent, only rewrites lines it can
prove are damaged, and refuses to write a "repair" that would not parse.

**One definition of damage.** Both scripts import the detector from
`qa/source_guard.py` rather than each carrying a copy. They originally had
duplicated patterns that drifted apart — and a repair built on the wrong one
corrupted 12 files. There is now nothing to drift.

**The detector is pinned by tests.** It must fire on collapsed delimiters and on
nothing else, because a false positive silently rewrites working code. The case
that caused the corruption above was a bare `"",` — an empty string element in a
list, which is valid Python:

```powershell
python qa/source-guard-units.py    # 11 cases: valid code vs real damage
python qa/source-guard-check.py    # 20 cases: check -> repair -> re-check
```

It is wired to run without being remembered:

| Trigger | Where |
| --- | --- |
| Build task (Ctrl+Shift+B) | `.vscode/tasks.json` → *Check Python sources* |
| Per-commit (Windows) | `powershell -File qa/install-hooks.ps1` |
| Per-commit (POSIX) | `sh qa/install-hooks.sh` |

---

## Team badges and icons
### Icons
All UI iconography uses **Material Symbols** from
[Google Fonts Icons](https://fonts.google.com/icons) (Apache 2.0), inlined as
SVG by `frontend/icons.js`. No emoji anywhere — emoji render differently on every
OS and cannot be themed.

**Why inline SVG rather than the icon webfont.** The font ships its glyphs as
ligatures, so `<i class="mi">sports_soccer</i>` renders the literal words
`sports_soccer` until the font file arrives. That is not a hypothetical: in
testing the font request was aborted and every icon degraded to visible text.
Wrapping the element in `visibility: hidden` until `document.fonts` resolves is
the obvious patch, but it fails *closed* — a blocked CDN or an offline user then
loses all iconography. Inline SVG has no network request, cannot flash, sizes
with `em`, and inherits `currentColor` for free.

`icon(name, extraClass)` returns the markup and fails to an empty string for an
unknown name, so a typo degrades to missing decoration rather than a broken box.
Adding an icon means adding its path data to `icons.js`.

### Badges
`backend/app/crests.py` resolves each team to a club badge. Two sources sit
behind one interface:

| Source | When | Notes |
| --- | --- | --- |
| `BADGE_DIR` (local) | whenever `<slug>.png` exists | preferred; no network, no third party |
| Remote dataset | fallback | works out of the box for the demo |

`LOGO_SOURCE` = `local` \| `remote` \| `auto` (default: local first).

**On rights.** Club crests are trademarks owned by the clubs. No logo
aggregator can grant a licence it does not hold — several state outright that
their imagery may not be reused, and [1000logos.net](https://1000logos.net/soccer/)
carries a blanket "all rights reserved" with no reuse permission. The remote
fallback here exists to make the demo work, **not** as a licensing answer.
Before deploying publicly, drop your own licensed or commissioned badges into
`BADGE_DIR` and set `LOGO_SOURCE=local`; the app then makes no third-party
requests at all.

Badges are matched by an **explicit, generated** map (`backend/app/crest_map.py`),
not by fuzzy name matching. Fuzzy matching is how the wrong club's badge ends up
on a team — during development an id guessed for Pisa turned out to belong to AC
Monza. Regenerate after editing the alias table:

```powershell
cd backend
.\\.venv\\Scripts\\python.exe ..\\qa\\gen_crest_map.py     # rewrites crest_map.py
.\\.venv\\Scripts\\python.exe ..\\qa\\backfill_logos.py   # updates an existing DB
```

All 186 seeded teams currently resolve to a real badge. Clubs with no badge fall
back to a neutral glyph, never to a broken image.

---

## The SportyBet market board (display only)

`backend/app/sportybet.py` fetches the real SportyBet market board for a fixture
and shows it in a panel on the fixture page, **clearly separated from the model**.

The separation is the whole point, and it is enforced in three places:

- The module never writes to `fixtures.odds_*` and is never imported by
  `prediction.py` — the engine's three markets are untouched.
- The response carries **no probability, edge or confidence**. There is nothing
  in it to mistake for a prediction.
- The UI renders it in a dashed card labelled *"Book prices · not GoalEdge
  picks"*, with a caveat paragraph and, where the board is truncated, a count.

Why it is display-only: the engine prices **1X2, Over/Under and BTTS** off its
Dixon-Coles matrix. Everything else a book offers — double chance, Asian
handicaps, correct score, HT/FT — is *derived from that same matrix*, so it
carries no information the model doesn't already have. Listing those markets
with anything resembling a model claim would be padding, not analysis.

### Market categories
`backend/app/markets.py` is the single taxonomy for market categories. It
decides three things: which category a raw book name belongs to, what order the
categories display in, and **whether the engine can price each one**.

The fixture page lists the book's full category index — `1X2 - 1UP`,
`Asian Handicap`, `GG/NG 2+`, the goal-streak markets, and so on — with each row
marked either **model** (GoalEdge computes a probability) or **book only**
(prices shown, no prediction). Categories the live feed returned for this match
are highlighted; the rest are dimmed, so the index describes the book rather
than only this fixture.

Exactly three categories are model-priced, and the list is derived from the
engine rather than hardcoded in the UI:

| Category | Priced? | Why |
| --- | --- | --- |
| 1X2 | **yes** | read off the scoreline matrix |
| Over/Under | **yes** | same matrix, each line |
| GG/NG (BTTS) | **yes** | same matrix |
| everything else | no | re-expressions of that same matrix |

That last row is the point. A handicap and a 1X2 price carry identical
information once de-vigged, so listing handicaps with anything resembling a
model claim would be inventing authority the engine does not have.

`markets-check.py` covers the precedence traps that a naive substring matcher
gets wrong — `1X2 - 1UP` must not fall through to `1x2`, `Double Chance - 1UP`
must not become `1x2` because it contains "1UP", and `Asian Handicap` must not
collapse into plain `Handicap`.

### Enabling the live board
Off by default (the board is large, and SportyBet's endpoints are undocumented):

```ini
SPORTYBET_MARKETS_ENABLED=true
SPORTYBET_BASE_URL=https://www.sportybet.com/api
SPORTYBET_COUNTRY=ng
```

### Verified against a mock, not against SportyBet
**The live feed is unverified.** At the time of writing every SportyBet API path
probed returned **HTTP 403 from Akamai** — the endpoints are unofficial and
bot-protected. The parsing, matching and rendering paths were therefore tested
end-to-end against `qa/fake_sportybet.py`, a local mock that serves the same
payload *shape*:

```powershell
cd backend
..\.venv\Scripts\python.exe ..\qa\fake_sportybet.py 8123   # in one terminal
$env:SPORTYBET_MARKETS_ENABLED="true"
$env:SPORTYBET_BASE_URL="http://127.0.0.1:8123/api"
```

Expect to adjust `fetch_events_for_date` / `fetch_event_markets` in
`sportybet.py` once a real payload is captured — the field names are guesses at
SportyBet's conventions, not a documented contract. The failure mode is safe:
an unreachable or reshaped feed degrades to "board available: false" with a
reason, and the model's own output is unaffected.

Fixture matching is deliberately strict — normalised team names must match
exactly and kickoff must be within 6 hours. A near-miss shows **no board** rather
than another match's prices, which is the right trade: a wrong price is worse
than a missing one.

---

## Replacing the demo data

`seed.py` generates a reproducible synthetic dataset (fixed RNG seed) so the
engine has real history to fit. For live data, fetch real fixtures/results and
populate the same tables — the model reads only from `Fixture`, `Team`,
`Competition` and `Standing`, so a feed loader is a drop-in replacement. Set
`fixtures.odds_*` from a bookmaker API to enable value detection.

---

## Disclaimer

This is a **demonstration project**. Fixtures, results and prices are simulated,
it holds no licence to operate a betting service, and nothing here is betting
advice. Probabilities are estimates, not guarantees — football is a
high-variance, low-information sport and a well-calibrated model still loses
regularly. **18+.** Bet only what you can afford to lose.

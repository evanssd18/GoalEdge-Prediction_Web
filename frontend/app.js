/**
 * GoalEdge AI — frontend application.
 *
 * Single-page app in plain ES modules. No build step, no framework: the whole
 * thing is served statically by the FastAPI app on the same origin, so the API
 * is reached at /api with no CORS configuration needed.
 */

import { icon } from "./icons.js";
import { createAdminPanel } from "./admin.js";

// ---------------------------------------------------------------- api

const API = "/api";

async function api(path, options = {}) {
  // The token is attached here rather than at each call site. Only `/auth/me`
  // used to pass it, and a call site that forgot produced a confusing 401 from
  // an endpoint the reader was entitled to use -- which is exactly how every
  // admin call from the panel arrived anonymous. An explicit header still wins,
  // so the rare call that must run unauthenticated can override it.
  const token = options.token ?? state.token;
  const auth = token ? { Authorization: `Bearer ${token}` } : {};
  const res = await fetch(`${API}${path}`, {
    headers: {
      "Content-Type": "application/json",
      ...auth,
      ...(options.headers || {}),
    },
    ...options,
  });
  if (!res.ok) {
    let detail = `Request failed (${res.status})`;
    try {
      const body = await res.json();
      if (body && body.detail) detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch { /* response had no JSON body */ }
    throw new Error(detail);
  }
  if (res.status === 204) return null;
  return res.json();
}

// ---------------------------------------------------------------- state

const state = {
  view: "tips",
  tips: [],
  tipsTotal: 0,
  valueBets: [],
  marketIndex: null,  // /api/markets: categories + which the engine prices
  results: null,
  // The Results page opens on the shortest window: the most recent completed day
  // is what a reader came to check.
  resultsDays: 1,
  resultsSource: "",  // "" = all, "flashscore" = real, "seed" = simulated
  // Which market the Results page is narrowed to, as a MARKET_PAGES slug. ""
  // shows every settled match; a slug keeps only the matches whose model had a
  // selection in that market, which is what makes the market buttons a filter
  // rather than a decoration.
  resultsMarket: "",
  // Livescores board: the day being shown, the last payload, and whether the
  // reader has narrowed the board to matches actually in play.
  livescores: null,
  livescoreDate: "today",
  // The match whose details are expanded inside the board, if any. Keeps the
  // reader on the Livescore tab: opening a match here is a detail *of the same
  // page*, not a navigation away from it.
  lsOpenMatch: null,
  // The open match page: its payload and the tab being read. Kept in state, not
  // in the DOM, because the page's own clock poll re-renders the whole thing
  // every 20s -- and a tab held in the markup would snap back to Summary on
  // each tick.
  matchPage: null,
  matchTab: "summary",
  // Which competition groups the reader has folded away. Keyed by competition
  // label so a collapse survives the 30s poll's re-render.
  lsCollapsed: new Set(),
  // The board's status filter: the same ALL/LIVE/FINISHED/SCHEDULED tabs the
  // reference board uses. "all" shows every match on the day.
  lsFilter: "all",
  // Whether the board is narrowed to the eight major European leagues. Off by
  // default: the board's job is the whole day, and a reader who wants the
  // marquee fixtures is asking a narrower question than the page answers.
  lsTopOnly: false,
  // Whether the board is muted. The reference board has an audio control for
  // goal alerts; this one has no sound to toggle, so the control reports that
  // honestly rather than pretending to mute something.
  lsSound: false,
  // One search box per tab. They are kept apart on purpose: typing "Arsenal" on
  // the board and then opening Value Bets should not silently filter a page the
  // reader never searched on. The Predictions tab keeps its own term in
  // filters.search because the API filters server-side there.
  searches: { value: "", results: "", stats: "", livescores: "" },
  matches: { value: [], results: [], stats: [], livescores: [] },
  stats: null,
  // The sidebar's own day scope: "all" | "today" | "tomorrow". Separate from
  // `filters.date` only in intent -- the two are kept in step on every tab click
  // so the sidebar and the list can never disagree about which day they show.
  sideDay: "all",
  // /api/days: real per-competition fixture counts for today and tomorrow, used
  // by the sidebar so its badges are true totals rather than the capped number of
  // tips the feed happened to return.
  daySummary: null,
  filters: {
    date: "all",
    competitionId: "",
    competitionLabel: "",
    minConfidence: 0,
    onlyValue: false,
    search: "",
  },
  competitions: [],
  page: 1,
  pageSize: 12,
  detailCache: new Map(),
  token: localStorage.getItem("ge_token") || null,
  user: null,
  // "light" | "dark" | "system". "system" when no explicit choice is stored, so
  // a first-time reader gets their OS preference and a reader who has chosen
  // keeps that choice.
  theme: (() => {
    try {
      const saved = localStorage.getItem("ge_theme");
      return saved === "light" || saved === "dark" ? saved : "system";
    } catch {
      return "system";
    }
  })(),
};

// ---------------------------------------------------------------- utils

const esc = (s) =>
  String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const pct = (v) => (v == null ? "—" : `${(v * 100).toFixed(1)}%`);
const pct0 = (v) => (v == null ? "—" : `${Math.round(v * 100)}%`);
const num = (v, d = 2) => (v == null || Number.isNaN(v) ? "—" : Number(v).toFixed(d));
// `null` means "no market to compare against" (a self-priced fixture), which is
// shown as an em dash rather than 0.0% -- claiming an edge of zero would be a
// different, and false, statement.
const signed = (v) => (v == null ? "—" : `${v > 0 ? "+" : ""}${(v * 100).toFixed(1)}%`);

function money(n) {
  const v = Number(n || 0);
  return `${v >= 0 ? "+" : "−"}${Math.abs(v).toFixed(2)}u`;
}

function kickoffLabel(iso) {
  const d = new Date(iso);
  const now = new Date();
  const sameDay = d.toDateString() === now.toDateString();
  const tomorrow = new Date(now.getTime() + 864e5).toDateString() === d.toDateString();
  const time = d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  if (sameDay) return { date: "Today", time };
  if (tomorrow) return { date: "Tomorrow", time };
  return { date: d.toLocaleDateString([], { day: "2-digit", month: "short" }), time };
}

function timeUntil(iso) {
  const diff = new Date(iso) - new Date();
  if (diff < 0) return "started";
  const h = Math.floor(diff / 36e5);
  const m = Math.floor((diff % 36e5) / 6e4);
  if (h >= 24) return `in ${Math.floor(h / 24)}d`;
  if (h >= 1) return `in ${h}h ${m}m`;
  return `in ${m}m`;
}

const confClass = (c) => (c >= 78 ? "badge-high" : c >= 64 ? "badge-med" : c >= 50 ? "badge-low" : "badge-vlow");
const confFill = (c) => (c >= 64 ? "" : c >= 50 ? "med" : "low");

function stars(n) {
  const full = Math.max(0, Math.min(5, Math.round(n || 0)));
  const icons = Array.from({ length: 5 }, (_, i) =>
    icon(i < full ? "star" : "star_border", "mi-sm")
  ).join("");
  return `<span class="stars">${icons}</span>`;
}

function formPills(form) {
  if (!form) return '<span style="color:var(--text-faint)">—</span>';
  return `<span class="form-pills">${[...form]
    .slice(-5)
    .map((c) => `<span class="form-pill form-${esc(c)}">${esc(c)}</span>`)
    .join("")}</span>`;
}

function toast(msg) {
  document.querySelectorAll(".toast").forEach((t) => t.remove());
  const el = document.createElement("div");
  el.className = "toast";
  el.textContent = msg;
  document.body.appendChild(el);
  setTimeout(() => el.remove(), 2600);
}

/**
 * A promise-returning confirmation dialog.
 *
 * Used instead of `window.confirm` because the admin panel's destructive actions
 * need to explain what they will do -- "delete this user" is not the same
 * decision as "block this user", and a native confirm can only show one line of
 * plain text with no title and no danger styling.
 *
 * Resolves `true` only on an explicit confirm click. Escape, the backdrop and
 * Cancel all resolve `false`, so a dismissed dialog can never be mistaken for
 * consent.
 */
function confirmDialog({ title, body, confirmLabel = "Confirm", danger = false }) {
  return new Promise((resolve) => {
    const backdrop = document.createElement("div");
    backdrop.className = "modal-backdrop";
    backdrop.innerHTML = `
      <div class="modal" role="alertdialog" aria-modal="true">
        <h3>${esc(title)}</h3>
        ${body ? `<p style="font-size:13.5px;color:var(--text-dim);margin:0 0 14px">${esc(body)}</p>` : ""}
        <div class="modal-actions">
          <button class="btn btn-ghost" id="c-cancel">Cancel</button>
          <button class="btn ${danger ? "" : "btn-primary"}" id="c-go"
                  ${danger ? 'style="background:var(--danger);color:#fff"' : ""}>${esc(confirmLabel)}</button>
        </div>
      </div>`;
    document.body.appendChild(backdrop);

    const finish = (value) => {
      document.removeEventListener("keydown", onKey);
      backdrop.remove();
      resolve(value);
    };
    const onKey = (e) => {
      if (e.key === "Escape") finish(false);
      if (e.key === "Enter") finish(true);
    };
    backdrop.addEventListener("click", (e) => { if (e.target === backdrop) finish(false); });
    document.addEventListener("keydown", onKey);
    backdrop.querySelector("#c-cancel").addEventListener("click", () => finish(false));
    backdrop.querySelector("#c-go").addEventListener("click", () => finish(true));
    backdrop.querySelector("#c-go").focus();
  });
}

// ---------------------------------------------------------------- api calls

async function loadCompetitions() {
  if (state.competitions.length) return state.competitions;
  try {
    state.competitions = await api("/competitions");
  } catch {
    state.competitions = [];
  }
  // A-Z by country, then by name -- the same order the sidebar uses, so every
  // competition dropdown on every tab reads as one index rather than the API's
  // own tier/name order. Sorted here, once, at the point the list is cached, so
  // no consumer can forget to and the ordering cannot drift between tabs.
  state.competitions = [...state.competitions].sort(
    (a, b) =>
      String(a.country || "~").localeCompare(String(b.country || "~")) ||
      String(a.name || "").localeCompare(String(b.name || ""))
  );
  return state.competitions;
}

// The sidebar's own day summary: per-competition fixture counts and real day
// totals, straight from the fixtures table. It is a separate, cheap call rather
// than something derived from the tips feed, because the feed is capped and only
// covers fixtures the model has a pick for -- deriving counts from it made the
// "All competitions" badge read "60" when the day held hundreds.
//
// A failure here must never blank the sidebar, so it degrades to null and the
// renderer falls back to counting the tips it already has.
async function loadDaySummary() {
  try {
    state.daySummary = await api("/days");
  } catch {
    state.daySummary = null;
  }
  return state.daySummary;
}

async function loadTips() {
  const q = new URLSearchParams();
  if (state.filters.date !== "all") q.set("date", state.filters.date);
  if (state.filters.minConfidence) q.set("min_confidence", state.filters.minConfidence);
  q.set("limit", "60");

  // The competition filter is a real server-side filter. Filtering client-side
  // after a capped fetch silently loses every fixture that fell outside the
  // first 60 -- so a league whose matches all sat past the cap looked empty.
  const comp = state.filters.competitionId
    ? (await loadCompetitions()).find(
        (c) => String(c.id) === String(state.filters.competitionId)
      )
    : null;
  if (comp) {
    q.set("competition_id", String(comp.id));
    state.filters.competitionLabel = comp.label || comp.name;
  }

  let tips = await api(`/tips?${q}`);
  let items = tips.items || [];

  // Defence in depth on the two things the endpoint now promises: every row is a
  // real match, and every row is still to be played. The server already filters
  // both, so this changes nothing today -- it is here because a simulated
  // fixture or a past kick-off reaching this page is the failure the reader
  // reported, and a client-side guard means a future endpoint change cannot
  // silently reintroduce it.
  //
  // The comparison is against NOW, not against midnight. Midnight was the
  // original bound and it let a whole morning's matches through: kickoff >= the
  // start of today is still true for a match that finished hours ago, so the top
  // of the list showed tips for games already over. "Still to be played" is a
  // claim about the clock, so it has to be tested against the clock.
  items = items.filter((t) => t.is_real !== false);
  const now = Date.now();
  items = items.filter((t) => !t.kickoff || new Date(t.kickoff).getTime() > now);

  // A competition filter the server did not understand still narrows locally,
  // so a stale selection cannot quietly widen the list back out.
  if (state.filters.competitionId) {
    const want = state.filters.competitionLabel;
    if (want) items = items.filter((t) => t.competition === want);
  }
  if (state.filters.onlyValue) items = items.filter((t) => t.edge > 0.02);
  if (state.filters.search) {
    const needle = state.filters.search.toLowerCase();
    items = items.filter(
      (t) =>
        t.home_team.toLowerCase().includes(needle) ||
        t.away_team.toLowerCase().includes(needle) ||
        t.competition.toLowerCase().includes(needle) ||
        t.home_team_full.toLowerCase().includes(needle) ||
        t.away_team_full.toLowerCase().includes(needle)
    );
  }

  state.tips = items;
  state.tipsTotal = items.length;
  state.page = 1;
}

async function loadValueBets() {
  // The sidebar's league selection narrows this page server-side, so the scan
  // only prices the competition actually being looked at -- and so the cap
  // below cannot hide a league's best prices behind other competitions' rows.
  const comp = state.filters.competitionId
    ? `&competition_id=${encodeURIComponent(state.filters.competitionId)}`
    : "";
  const data = await api(`/value-bets?limit=50${comp}`);
  // Same guard as the tips list: a value bet on a simulated fixture, or on one
  // that has already kicked off, is not a bet anyone can place.
  const startOfToday = new Date();
  startOfToday.setHours(0, 0, 0, 0);
  state.valueBets = (data.items || []).filter(
    (v) => v.is_real !== false && (!v.kickoff || new Date(v.kickoff) >= startOfToday)
  );
  // Same defence in depth as /tips: a selection the server did not narrow still
  // narrows locally, so a stale or merged competition id cannot silently widen
  // the list back out to every league.
  const want = state.filters.competitionLabel;
  if (state.filters.competitionId && want) {
    state.valueBets = state.valueBets.filter((v) => v.competition === want);
  }
  // The market taxonomy is fixture-independent, so it is fetched once and
  // reused; a failure here must not blank the page, so it degrades to null.
  if (!state.marketIndex) {
    try { state.marketIndex = await api("/markets"); }
    catch (e) { state.marketIndex = null; }
  }
}

// ---------------------------------------------------------------- renderers

function selectionsBlock(t) {
  // Every market the model prices on this fixture (1X2, Over/Under lines,
  // BTTS), each selection with its odds and edge. The headline tip above is
  // one of these rows; this is the full set the model publishes for the match.
  const markets = t.markets || [];
  if (!markets.length) return "";

  const rows = markets
    .map(
      (m) => `
      <div class="sel-market">
        <span class="sel-market-name">${esc(m.name)}</span>
        <div class="sel-items">
          ${(m.selections || [])
            .map(
              (s) => `
            <span class="sel-item ${s.value ? "value" : ""}">
              <span class="sel-label">${esc(s.label)}</span>
              <span class="sel-odds">${s.odds ? num(s.odds) : "—"}</span>
              <span class="sel-edge ${s.edge >= 0 ? "pos" : "neg"}">${esc(signed(s.edge))}</span>
            </span>`
            )
            .join("")}
        </div>
      </div>`
    )
    .join("");

  // Collapsed by default. The engine now publishes 23 markets (~117 selections)
  // per fixture, and rendering all of them inline made every card on the
  // predictions list ~1800px tall -- a page of near-empty rectangles. The
  // headline pick stays visible; the depth is one click away rather than in the
  // way. <details> is used rather than a JS toggle so it works without script
  // and keeps its own state.
  const total = markets.reduce((n, m) => n + (m.selections || []).length, 0);
  return `
    <details class="tip-more">
      <summary>
        <span class="tip-more-label">All ${markets.length} markets</span>
        <span class="tip-more-count">${total} selections</span>
      </summary>
      <div class="tip-selections">${rows}</div>
    </details>`;
}

// A club crest, or nothing at all. A missing/unreachable crest must not leave a
// broken-image icon beside the team name, so on error the element removes
// itself and the name simply stands alone.
function crest(url) {
  if (!url) return "";
  return `<img class="tip-logo" src="${esc(url)}" alt="" loading="lazy"
    onerror="this.remove()">`;
}

/**
 * One model pick, as a row.
 *
 * Laid out the way sofascore.com lays out a match: competition and kick-off on a
 * quiet meta line, the two sides with their crests on one line and the teams
 * LEFT-aligned rather than centred, a soft inset panel for the pick itself, and
 * the odds in a score-pill-style box on the right. The centred layout this
 * replaces is a betting-tips convention (sportytrader, eaglepredict); the
 * reference reads as a scores board, which is a left-aligned list.
 */
function tipCard(t) {
  const k = kickoffLabel(t.kickoff);
  // The value flag must describe the pick actually shown on the card, not some
  // other market on the same fixture — otherwise a card can read "edge -5.5%"
  // while wearing a VALUE badge, which is misleading.
  // `null` edge = the price is the model's own, so there is no market to beat.
  const selfPriced = t.edge == null;
  const isValue = !selfPriced && t.edge >= 0.02;
  return `
    <article class="tip-card ${isValue ? "value" : ""}" data-fixture="${t.fixture_id}">
      <div>
        <div class="tip-meta">
          <span class="tip-comp">${esc(t.competition)}</span>
          <span class="tip-dot" aria-hidden="true"></span>
          <span class="tip-time">${esc(k.date)} ${esc(k.time)}</span>
          <span class="tip-dot" aria-hidden="true"></span>
          <span class="tip-time">${esc(timeUntil(t.kickoff))}</span>
          ${t.is_real ? '<span class="src-tag src-real">Live data</span>' : ""}
        </div>
        <div class="tip-teams">
          <span class="tip-side-team">${crest(t.home_logo)}<span>${esc(t.home_team)}</span></span>
          <span class="tip-vs">v</span>
          <span class="tip-side-team">${crest(t.away_logo)}<span>${esc(t.away_team)}</span></span>
        </div>
        <div class="tip-pick">
          <!-- The market the pick came from, not a repeat of the fixture name. The
               fixture is already the line above, so repeating it here wasted the
               most prominent label on the card. -->
          <div class="pick-text">
            <span class="pick-label">${esc(marketLabel(t.market))}</span>
            <span class="pick-value">${esc(t.selection)}</span>
          </div>
          <span class="pick-meta-row">
            <span class="badge badge-edge">${esc(pct0(t.probability))} model prob.</span>
            ${selfPriced
              ? `<span class="badge badge-edge">no book price</span>`
              : `<span class="badge badge-edge ${t.edge >= 0 ? "pos" : "neg"}">edge ${esc(signed(t.edge))}</span>
                 ${t.edge < -0.02 ? '<span class="badge badge-edge neg">below market</span>' : ""}`}
          </span>
        </div>
        ${selectionsBlock(t)}
      </div>
      <div class="tip-side">
        <span class="badge ${confClass(t.confidence)}">${esc(t.confidence_label)} · ${t.confidence}</span>
        <!-- The odds box must never show a number it cannot stand behind.
             t.odds is 0/null whenever no real book priced this fixture, and the
             old code rendered that as "0" -- a meaningless figure sitting in the
             most prominent slot on the card. Where there is no book price we show
             the model's FAIR odds instead and label them as such, so the reader
             can tell a market price from a derived one at a glance. -->
        <div class="odds-box ${selfPriced ? "model" : ""}">
          <div class="odds-value">${num(selfPriced ? t.fair_odds : t.odds)}</div>
          <div class="odds-label">${selfPriced ? "Fair odds (model)" : "Best price"}</div>
        </div>
        ${stars(t.value_rating)}
      </div>
    </article>`;
}

//: The headline pick's market key -> the label a reader expects above it.
function marketLabel(key) {
  const m = {
    "1x2": "Match Result",
    btts: "Both Teams To Score",
    ou25: "Total Goals 2.5",
    ou0_5: "Total Goals 0.5",
    ou1_5: "Total Goals 1.5",
    ou3_5: "Total Goals 3.5",
    ou4_5: "Total Goals 4.5",
    ou5_5: "Total Goals 5.5",
  };
  return m[key] || "Prediction";
}

// ------------------------------------------------- market listing pages
// The Option B surface: one page per market, listing the fixtures where the
// model has something to say about THAT market. Every page is fed by data the
// engine already publishes -- no page invents a market the model cannot price.
//
// `key` is matched against the market key on each selection; `market` names a
// market in the taxonomy; `min`/`max` bound the probability a pick must fall in
// to count as a genuine call rather than a formality.
const MARKET_PAGES = [
  { slug: "straight-win", key: "1x2", title: "Straight Win", blurb: "The model's single most likely match result." },
  { slug: "double-chance", key: "dc", title: "Double Chance", blurb: "Two results covered in one bet." },
  { slug: "btts", key: "btts", title: "Both Teams To Score", blurb: "Whether both sides get on the scoresheet." },
  // `pick` distinguishes two buttons that share one market key: Over 2.5 and
  // Under 2.5 are the same `ou25` market, and without it both buttons would
  // return the identical list -- the filter would look like it worked while
  // ignoring which side of the line was asked for.
  { slug: "over-2-5", key: "ou25", sel: "over", pick: "over", title: "Over 2.5 Goals", blurb: "Three goals or more in the match." },
  { slug: "under-2-5", key: "ou25", sel: "under", pick: "under", title: "Under 2.5 Goals", blurb: "Two goals or fewer in the match." },
  { slug: "handicap", key: "handicap", title: "Handicap", blurb: "A head start on the goals market." },
  // Lower floor: an exact scoreline is never a 45% event.
  { slug: "correct-score", key: "correct_score", minProbability: 0.06, title: "Correct Score", blurb: "The most likely exact scorelines." },
  // A goal-in-window market is a set of mutually exclusive 15-minute buckets, so
  // every row sits at the league base rate (~47%) and a "ranking" is meaningless.
  // The page still earns its place by showing WHICH windows the model favours.
  { slug: "goal-window", key: "goal_window", minProbability: 0.02, title: "Goal Time Windows", blurb: "When in the match goals are expected." },
  { slug: "ht-ft", key: "ht_ft", title: "Half Time / Full Time", blurb: "Both results on one bet." },
  { slug: "1st-half-ou", key: "ou_1st_half", title: "1st Half O/U", blurb: "Goals before the break." },
  { slug: "1st-half-handicap", key: "handicap_1st_half", title: "1st Half Handicap", blurb: "First-half head start." },
  { slug: "dc-gg-ng", key: "dc_btts", title: "Double Chance & GG/NG", blurb: "A result plus both teams scoring." },
  { slug: "dc-ou", key: "dc_ou25", title: "Double Chance & O/U", blurb: "A result plus the goals line." },
  { slug: "1x2-ou", key: "1x2_ou25", title: "1X2 & O/U", blurb: "A result plus the goals line." },
];

function renderMarketPage(slug) {
  const page = MARKET_PAGES.find((m) => m.slug === slug);
  const root = document.getElementById("view");
  if (!page) {
    root.innerHTML = `<div class="error-box">Unknown market &ldquo;${esc(slug)}&rdquo;.</div>`;
    return;
  }

  // Flatten every fixture's selections, keep only this market's, then rank.
  const all = [];
  for (const t of state.tips) {
    for (const m of t.markets || []) {
      if (m.key !== page.key) continue;
      for (const s of m.selections || []) {
        if (page.sel && s.key !== page.sel) continue;
        all.push({ ...s, market_name: m.name, tip: t });
      }
    }
  }

  // Rank, but only present rows that are actually a judgement.
  //
  // On a market whose lines run out to +3, sorting purely by probability returns
  // nothing but near-certainties: a +3 handicap is ~100%, so the top 120 rows
  // were ALL above 90% and 62 were at 100%, which tells a reader nothing about
  // any match. The further a line is from even, the more certain the model is and
  // the less it is saying -- confidence and informativeness run in OPPOSITE
  // directions here. So the band is applied before the sort, and the strongest
  // call worth publishing is the one closest to a real decision.
  //
  // The floor is 45% for most markets, but Correct Score is different: an exact
  // scoreline is never likely, so a 45% floor leaves the page empty. Its own
  // floor is set to the most probable scorelines instead, which is what a reader
  // of that market actually wants.
  const MIN_P = page.minProbability || 0.45;
  const MAX_P = 0.93;
  const rows = all.filter((r) => r.probability >= MIN_P && r.probability <= MAX_P);
  const dropped = all.length - rows.length;
  rows.sort((a, b) => b.probability - a.probability);
  // Best-first within the band: the most confident call that is still a call.
  if (!rows.length) rows.push(...all.sort((a, b) => b.probability - a.probability));

  const strip = MARKET_PAGES.map(
    (m) =>
      `<a class="mkt-tab ${m.slug === slug ? "active" : ""}" href="#/market/${m.slug}">${esc(m.title)}</a>`
  ).join("");

  root.innerHTML = `
    <div class="page-head">
      <h1>${esc(page.title)} Predictions</h1>
      <p>${esc(page.blurb)} Ranked by the model's probability across
      ${state.tips.length} upcoming fixtures. Only selections the engine can
      price appear here${dropped ? `, and only calls the model can actually
      distinguish — ${dropped} selections outside the publishable probability
      band are excluded, because a line priced to always win says nothing about
      the match` : ""}.</p>
    </div>
    <div class="mkt-strip">${strip}</div>
    ${rows.length
      ? `<div class="mkt-table card">
          <div class="mkt-thead">
            <span>Match</span><span>Selection</span><span>Prob.</span><span>Fair odds</span>
          </div>
          ${rows
            .slice(0, 120)
            .map(
              (r) => `
            <a class="mkt-trow" href="#/fixture/${r.tip.fixture_id}">
              <span class="mkt-match">
                <span class="mkt-comp">${esc(r.tip.competition)}</span>
                <span class="mkt-teams">${esc(r.tip.home_team)} <i>v</i> ${esc(r.tip.away_team)}</span>
                <span class="mkt-time">${esc(kickoffLabel(r.tip.kickoff).date)} ${esc(kickoffLabel(r.tip.kickoff).time)}</span>
              </span>
              <span class="mkt-sel">${esc(r.label)}</span>
              <span class="mkt-prob">${esc(pct0(r.probability))}</span>
              <span class="mkt-fair">${num(r.fair_odds)}</span>
            </a>`
            )
            .join("")}
        </div>`
      : `<div class="empty"><div class="big">No selections</div>
          <p>The engine published nothing for this market on the upcoming fixtures.</p></div>`}`;
}

function renderTips() {
  const root = document.getElementById("view");
  const start = (state.page - 1) * state.pageSize;
  const pageItems = state.tips.slice(start, start + state.pageSize);
  const pages = Math.max(1, Math.ceil(state.tips.length / state.pageSize));

  const compOptions = state.competitions
    .map(
      (c) =>
        `<option value="${c.id}" ${String(state.filters.competitionId) === String(c.id) ? "selected" : ""}>${esc(c.label)}</option>`
    )
    .join("");

  root.innerHTML = `
    <div class="page-head">
      <h1>Football Predictions &amp; AI Betting Tips</h1>
      <p>Every tip below is generated by a Dixon-Coles Poisson model, blended with live market
      prices, then explained in plain English by the AI analyst. Probabilities are honest:
      nothing here is a certainty.</p>
    </div>

    <div class="filters">
      <div class="chip-row">
        ${["all", "today", "tomorrow"]
          .map(
            (d) =>
              `<button class="chip ${state.filters.date === d ? "active" : ""}" data-date="${d}">${
                d === "all" ? "All upcoming" : d[0].toUpperCase() + d.slice(1)
              }</button>`
          )
          .join("")}
      </div>
      <select id="f-comp">
        <option value="">All competitions</option>
        ${compOptions}
      </select>
      <select id="f-conf">
        <option value="0">Any confidence</option>
        <option value="50" ${state.filters.minConfidence === 50 ? "selected" : ""}>50+ (solid)</option>
        <option value="64" ${state.filters.minConfidence === 64 ? "selected" : ""}>64+ (high)</option>
        <option value="78" ${state.filters.minConfidence === 78 ? "selected" : ""}>78+ (very high)</option>
      </select>
      <input type="search" id="f-search" placeholder="Search team…" value="${esc(state.filters.search)}">
      <button class="chip ${state.filters.onlyValue ? "active" : ""}" id="f-value">Value only</button>
      <span style="margin-left:auto;font-size:13px;color:var(--text-dim)">
        ${state.tips.length} fixture${state.tips.length === 1 ? "" : "s"}
      </span>
    </div>

    <!-- The market tab row: their signature horizontal market strip. Each tab is
         a real link to a market listing page, so it is navigable and shareable
         rather than a JS-only filter. -->
    <div class="mkt-strip">
      <a class="mkt-tab active" href="#/tips">All</a>
      ${MARKET_PAGES.map(
        (m) => `<a class="mkt-tab" href="#/market/${m.slug}">${esc(m.title)}</a>`
      ).join("")}
    </div>

    ${
      pageItems.length
        ? `<div class="tips-list">${pageItems.map(tipCard).join("")}</div>`
        : `<div class="empty"><div class="big">${icon("search_off")}</div>No fixtures match those filters.<br>
           <span style="font-size:13px">Try “All upcoming”, or clear the confidence filter.</span></div>`
    }

    ${
      pages > 1
        ? `<div class="pager">
             <button class="btn btn-sm" id="pg-prev" ${state.page === 1 ? "disabled" : ""}>
               ${icon("chevron_left", "mi-sm")} Prev</button>
             <span class="status">Page ${state.page} of ${pages}</span>
             <button class="btn btn-sm" id="pg-next" ${state.page === pages ? "disabled" : ""}>
               Next ${icon("chevron_right", "mi-sm")}</button>
           </div>`
        : ""
    }

    <div class="promo">
      <div>
        <h3>Track every tip in public</h3>
        <p>Save a selection to the open record and the result is graded against the final score.</p>
      </div>
      <button class="btn" id="promo-record">View the record
        ${icon("arrow_forward", "mi-sm")}</button>
    </div>
  `;

  const promoBtn = document.getElementById("promo-record");
  if (promoBtn)
    promoBtn.addEventListener("click", () => {
      location.hash = "#/stats";
      renderStats();
    });

  root.querySelectorAll("[data-fixture]").forEach((el) =>
    el.addEventListener("click", () => openDetail(el.dataset.fixture))
  );
  root.querySelectorAll("[data-date]").forEach((el) =>
    el.addEventListener("click", async () => {
      state.filters.date = el.dataset.date;
      // The sidebar's day tabs are the same control on a different surface, so
      // they follow this one rather than keeping a stale day of their own.
      state.sideDay = el.dataset.date;
      state.page = 1;
      await refreshTips();
    })
  );
  document.getElementById("f-comp").addEventListener("change", async (e) => {
    state.filters.competitionId = e.target.value;
    await refreshTips();
  });
  document.getElementById("f-conf").addEventListener("change", async (e) => {
    state.filters.minConfidence = Number(e.target.value);
    await refreshTips();
  });
  let searchTimer;
  document.getElementById("f-search").addEventListener("input", (e) => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(async () => {
      state.filters.search = e.target.value.trim();
      await refreshTips({ keepFocus: true });
    }, 320);
  });
  document.getElementById("f-value").addEventListener("click", async () => {
    state.filters.onlyValue = !state.filters.onlyValue;
    await refreshTips();
  });

  const prev = document.getElementById("pg-prev");
  const next = document.getElementById("pg-next");
  if (prev) prev.addEventListener("click", () => { state.page--; renderTips(); window.scrollTo({ top: 0, behavior: "smooth" }); });
  if (next) next.addEventListener("click", () => { state.page++; renderTips(); window.scrollTo({ top: 0, behavior: "smooth" }); });
}

// ---------------------------------------------------------------- sidebar
//: The competitions the sidebar pins above everything else, in the order a
//: football reader expects to find them: the big five European leagues, then
//: the Dutch league, then the two continental cups, then the two Australian
//: state leagues. Matching is done on the competition slug rather than the
//: ``Country - League`` label, because the label is built from the feed's own
//: country string and is not stable enough to match on.
//:
//: Anything on this list that is NOT present in the API's competition table is
//: simply skipped -- the live feed decides what exists, and a head-to-head demo
//: database may well have no NPL fixtures yet. A pinned name is never invented.
const TOP_LEAGUES = [
  "england-premier-league",
  "spain-laliga",
  "germany-bundesliga",
  "france-ligue-1",
  "italy-serie-a",
  "netherlands-eredivisie",
  "champions-league",
  "europa-league",
  "npl-nsw",
  "npl-south-australia",
];

//: The label the feed is expected to publish for each pinned competition, as a
//: strict, anchored pattern rather than a loose "does the label contain X" test.
//:
//: This fallback exists because the slug is not always the country-slugged form
//: the sync builds -- a seeded or hand-imported competition can carry any slug at
//: all. It is deliberately exact: a substring test on "Premier League" also
//: matches "Premier League Cup", "Premier League 2" and the Belarusian and
//: Taiwanese leagues, and a substring test on "Serie A" matches Brazil's. A
//: pinned block that mislabels which league a row belongs to is worse than a
//: shorter pinned block.
const TOP_LEAGUE_LABELS = [
  /^england - premier league$/,
  /^spain - la ?liga$/,
  /^germany - bundesliga$/,
  /^france - ligue 1$/,
  /^italy - serie a$/,
  /^netherlands - eredivisie$/,
  // The two continental cups are published with a ROUND suffix, not as a bare
  // name: "UEFA Champions League - League phase", "Europe - Europa League -
  // League phase". A round names a stage OF the senior cup, so it is accepted;
  // a women's, youth or qualification marker still names a different tournament
  // and is excluded. That is why the cup patterns allow a trailing round but the
  // league patterns above do not (their rounds are separate competitions --
  // "Premier League 2", "Eredivisie Women").
  new RegExp(`^(uefa|europe - uefa|europe)? ?-? ?champions league( - (league phase|group stage|group [a-h]))?$`),
  new RegExp(`^(uefa|europe - uefa|europe)? ?-? ?europa league( - (league phase|group stage|group [a-h]))?$`),
  /^australia - (npl )?(new south wales|nsw)$/,
  /^australia - npl south australia$/,
];

//: The two continental cups, by their index in TOP_LEAGUES. They carry a round
//: name in the tail ("- League phase") that names a stage of THIS competition,
//: so unlike the domestic leagues the tail is accepted -- but only when it names
//: a round. Men's/women's, youth and qualification markers still reject.
const TOP_LEAGUE_CUP_INDEXES = new Set([6, 7]);

//: A round of the senior tournament, appended to a cup slug
//: ("europe-europa-league-league-phase-cldjv3v5").
const CUP_ROUND_TAIL = /^-?(league[ -]?phase|group[ -]?stage|group[ -]?[a-h])(-|$)/;

//: A marker that names a DIFFERENT competition sharing the cup's name. Kept
//: separate from the round test above so "Champions League Women" is rejected
//: while "Champions League - League phase" is accepted.
const CUP_EXCLUDE = /(women|feminine|youth|junior|reserve|academy|qualification|play[ -]?offs?|preliminary)/;

//: The cups are EUROPEAN, and only Europe's are pinned. A confederation or
//: country prefix in the slug or label names a different competition that
//: happens to share the name: "asia-afc-champions-league-league-phase-...",
//: "Africa - CAF Champions League", "CONCACAF Champions League". The slug path
//: above matches the bare key by substring, so without this the AFC Champions
//: League is pinned as the UEFA one -- the round tail ("-league-phase") is a
//: valid round of *a* Champions League, but not of Europe's. The backend's
//: is_top_league rejects these by requiring country == "Europe"; this is the
//: client-side equivalent, since the client sees the label, not the country.
const CUP_NON_EUROPE = /\b(asia|afc|africa|caf|concacaf|conmebol|ofc|south america|north america|central america|caribbean|australia|saudi|japan|korea|china|qatar|uae|egypt|morocco|mexico|usa|brazil|argentina)\b/;

//: The qualifier that marks a slug as NOT the senior league it is named after,
//: for the DOMESTIC leagues only. "league phase" is included here because for a
//: domestic league it means a cup round, not the league itself; it is excluded
//: from the cup path above.
const JUNIOR =
  /(\bu-?\d{2}\b|youth|junior|reserve|women|feminine|academy|\bcup\b|qualification|league[ -]phase|play[ -]?offs?|\bii\b|\b\d\b)/;

//: Where a competition sits in the pinned block: its index, or -1 when it is not
//: a top league. The slug is the primary key; the label pattern is the fallback.
function topLeagueRank(comp) {
  const slug = String(comp.slug || "").toLowerCase();
  const exact = TOP_LEAGUES.indexOf(slug);
  if (exact !== -1) return exact;

  // A slug the feed invented ("england-premier-league-2", "premier-league-u21")
  // must NOT be treated as the senior league, so a match on the slug is only
  // allowed when what FOLLOWS the pinned key carries no age, reserve, cup or
  // second-tier qualifier.
  //
  // The key may sit at the start ("england-premier-league-dylosqod") or after a
  // country prefix ("australia-npl-south-australia-42"), which is how the feed
  // names its state leagues. What matters is the qualifier that trails the key.
  //
  // JUNIOR / CUP_ROUND_TAIL / CUP_EXCLUDE are defined above the function so the
  // unit check in qa/top-leagues-unit.mjs can test the exact same rules.
  for (let i = 0; i < TOP_LEAGUES.length; i += 1) {
    const key = TOP_LEAGUES[i];
    // Only treat it as a country prefix when the character before the key is a
    // separator, so "npl-nsw" cannot match a hypothetical "...-anpl-nsw".
    const at = slug.indexOf(key);
    if (at === -1) continue;
    if (at > 0 && slug[at - 1] !== "-") continue;
    // The feed appends its own id to a competition slug ("england-premier-league-
    // dylosqod", "australia-npl-south-australia-42"). Strip that before testing
    // the trailing words, so a real qualifier like "-qualification" is not
    // mistaken for a hash.
    //
    // The hash is at least six characters and carries a digit; a bare "-2" or
    // "-u21" is a REAL qualifier (a second division or an age group), not a hash,
    // so the length bound is what keeps those pinned out.
    const rest = slug.slice(at + key.length);
    const isFeedHash = /^-[a-z0-9]{6,}$/.test(rest) && /[0-9]/.test(rest);
    const trailing = isFeedHash ? "" : rest;
    if (TOP_LEAGUE_CUP_INDEXES.has(i)) {
      // The continental cups: a round suffix is a stage of THIS tournament, so
      // it is accepted; a women's/youth/qualification marker is not. The feed
      // hash that trails the round ("-league-phase-cldjv3v5") is stripped first
      // so the round test sees the round name, not the hash.
      //
      // The cup is Europe's: a confederation prefix anywhere in the slug names
      // another continent's competition (the AFC Champions League is Asia's) and
      // must not be pinned as the UEFA one. Checked on the whole slug, because
      // the prefix sits BEFORE the key that indexOf found.
      if (CUP_NON_EUROPE.test(slug)) continue;
      if (CUP_EXCLUDE.test(trailing)) continue;
      const withoutHash = trailing.replace(/-[a-z0-9]{6,}$/, "");
      if (!trailing || !withoutHash || CUP_ROUND_TAIL.test(withoutHash)) return i;
      continue;
    }
    if (!JUNIOR.test(trailing)) return i;
  }

  const label = String(comp.label || comp.name || "").toLowerCase();
  const byLabel = TOP_LEAGUE_LABELS.findIndex((re) => re.test(label));
  return byLabel;
}

//: The sidebar's day scope: "all", "today" or "tomorrow". Kept on `state` so it
//: survives a re-render, exactly like `filters.competitionId` does.
function sideDay() {
  return state.sideDay || "all";
}

//: Competition id -> fixture count for the day the sidebar is scoped to.
//:
//: The counts come from /api/days, which counts the fixtures table directly. That
//: matters: the tips feed is capped at 60 and only contains fixtures the model
//: has a pick for, so counting it made "All competitions" read "60" on a day that
//: held nearly four hundred. This is the real number.
function sideCounts(day) {
  const counts = {};
  if (day === "all") {
    // "All" covers every upcoming fixture, so the per-competition figure is the
    // day counts added together rather than a separate query.
    for (const d of Object.values(state.daySummary?.days || {})) {
      for (const c of d.competitions || []) counts[c.id] = (counts[c.id] || 0) + c.count;
    }
    return counts;
  }
  for (const c of state.daySummary?.days?.[day]?.competitions || []) counts[c.id] = c.count;
  return counts;
}

//: The competition ids that have football on the selected day. A day tab must
//: not offer a league that does not play then: clicking it would land on an empty
//: predictions list, which reads as a broken filter rather than a quiet night.
function sideCompetitionIds(day) {
  if (day === "all") return null; // null = "no day restriction"
  return new Set(Object.keys(sideCounts(day)).map(Number));
}

function renderSidebar() {
  const list = document.getElementById("side-comps");
  if (!list) return;

  const day = sideDay();
  const counts = sideCounts(day);
  const playingToday = sideCompetitionIds(day);
  // The feed keys a competition on its own id, so a league that both the seed
  // and the live feed know about exists twice under two slugs ("spain-laliga"
  // and "spain-laliga-qvmll54o"). They are the same league to a reader, so they
  // are merged here and the row points at the id that actually has fixtures.
  const byLabel = new Map();
  for (const c of state.competitions || []) {
    const label = c.label || c.name;
    const prev = byLabel.get(label);
    // Prefer the entry with fixtures; failing that, the lower id, so the pick is
    // stable across renders rather than alternating between the two rows.
    const score = counts[c.id] || 0;
    const prevScore = prev ? counts[prev.id] || 0 : -1;
    if (!prev || score > prevScore || (score === prevScore && c.id < prev.id)) {
      byLabel.set(label, c);
    }
  }
  const allComps = [...byLabel.values()];

  // The pinned top leagues are kept on every tab -- they are the sidebar's
  // landmarks, and having them vanish on "Today" would leave a reader unsure
  // whether the list had changed or the page had broken. A pinned league with no
  // football that day simply shows no badge, and clicking it falls back to the
  // widest day that has fixtures for it (see the click handler below).
  const pinnedComps = allComps.filter((c) => topLeagueRank(c) !== -1);

  // The unpinned list is limited to competitions that actually have an upcoming
  // fixture. The feed knows 655 competitions -- an unfiltered list is an
  // unreadable wall of leagues that finished months ago. On a day tab the day's
  // own counts are used; on "all" the per-competition figure is the two days
  // added together, which is the same thing the badges show.
  const upcomingIds = new Set(Object.keys(counts).map(Number));
  const restComps = allComps
    .filter((c) => topLeagueRank(c) === -1)
    .filter((c) => upcomingIds.has(c.id) || (playingToday !== null && playingToday.has(c.id)))
    // A-Z by COUNTRY first, then by competition name. Ordering on the name alone
    // interleaved the world's leagues alphabetically (the many "Premier League"
    // rows from England, Jamaica, Bhutan... sat scattered through the list),
    // which is how the sidebar read as a jumble rather than as a directory. With
    // country as the primary key every league of one country groups together and
    // the whole list reads top-to-bottom as an A-Z index of countries.
    .sort(
      (a, b) =>
        String(a.country || "~").localeCompare(String(b.country || "~")) ||
        String(a.name).localeCompare(String(b.name))
    );
  const active = String(state.filters.competitionId || "");

  // Day tabs: the active one follows `state.sideDay`, and they are re-wired on
  // every render because the markup is replaced wholesale.
  document.querySelectorAll("#side-tabs [data-side-day]").forEach((el) => {
    const on = el.dataset.sideDay === day;
    el.classList.toggle("active", on);
    el.setAttribute("aria-selected", on ? "true" : "false");
  });

  if (!pinnedComps.length && !restComps.length) {
    list.innerHTML = `<div class="side-empty">${
      day === "all" ? "No competitions." : "No competitions with football on that day."
    }</div>`;
    return;
  }

  // A day's real fixture total, for the "All competitions" badge. Next to the
  // per-league counts it reads as the same kind of fact.
  const total = day === "all"
    ? (state.daySummary?.upcoming_total ?? null)
    : (state.daySummary?.days?.[day]?.total ?? null);

  // Pinned block first, in TOP_LEAGUES order; everything else keeps the API's
  // own tier/name order underneath it. Pinned rows are sorted explicitly rather
  // than relying on the API's order, so the block reads the same everywhere.
  pinnedComps.sort((a, b) => topLeagueRank(a) - topLeagueRank(b));

  const item = ({ comp, rank }) => {
    const label = comp.label || comp.name;
    const initial = (comp.name || "?").trim().charAt(0).toUpperCase();
    const n = counts[comp.id];
    // Country AND league, both shown. The league name alone is not enough to
    // identify a row here: the feed knows hundreds of competitions and the same
    // name recurs across countries -- there are English, Jamaican and Bhutanese
    // "Premier League"s in this list, and "Segunda Division", "Serie A" and
    // "Cup" likewise. The country is what tells them apart, and the list is
    // already sorted by it, so printing it makes the ordering legible instead of
    // implied. A competition with no country (a continental or unattributed one)
    // shows the league alone rather than a bare placeholder.
    const country = comp.country || "";
    // The label is already "Country - League" for most rows, so the tooltip
    // must not prepend the country a second time ("England - Premier League ·
    // England"). It falls back to the country-qualified form only when the
    // label does not already carry it.
    const title = country && !label.startsWith(country) ? `${label} · ${country}` : label;
    return `<button class="side-item${rank !== -1 ? " side-item-top" : ""} ${
      active === String(comp.id) ? "active" : ""}"
                    data-comp="${comp.id}" data-label="${esc(label)}"
                    title="${esc(title)}">
      <span class="side-ico">${esc(initial)}</span>
      <span class="side-text">
        <span class="side-name">${esc(comp.name)}</span>
        ${country ? `<span class="side-country">${esc(country)}</span>` : ""}
      </span>
      ${n ? `<span class="side-count">${n}</span>` : ""}
    </button>`;
  };

  list.innerHTML =
    `<button class="side-item ${active === "" ? "active" : ""}" data-comp=""
             data-label="">
       <span class="side-ico">${icon("public", "mi-sm")}</span>
       <span class="side-text">
         <span class="side-name">All competitions</span>
       </span>
       ${total ? `<span class="side-count">${total}</span>` : ""}
     </button>` +
    pinnedComps.map((c) => item({ comp: c, rank: topLeagueRank(c) })).join("") +
    (restComps.length && pinnedComps.length
      ? '<div class="side-divider">All competitions</div>'
      : "") +
    restComps.map((c) => item({ comp: c, rank: -1 })).join("");

  list.querySelectorAll("[data-comp]").forEach((el) =>
    el.addEventListener("click", async () => {
      state.filters.competitionId = el.dataset.comp;
      state.filters.competitionLabel = el.dataset.label || "";
      state.page = 1;
      // A pinned league stays clickable even on a day it does not play, because
      // hiding the landmarks on a quiet day is more confusing than a filter that
      // returns nothing. In that case the pick widens the day back to "all", so
      // the reader lands on the league's next fixtures rather than on an empty
      // list they have to work out how to escape.
      if (el.dataset.comp && playingToday && !playingToday.has(Number(el.dataset.comp))) {
        state.sideDay = "all";
        state.filters.date = "all";
        toast(`No ${el.dataset.label || "fixtures"} today or tomorrow — showing the league's upcoming matches`);
      }
      const sel = document.getElementById("f-comp");
      if (sel) sel.value = el.dataset.comp;
      await applyCompetitionFilter();
    })
  );
}

//: Send a league selection to whichever view is on screen.
//:
//: The sidebar sits in the site header, so it is visible on every tab -- but it
//: used to call refreshTips() unconditionally, which meant clicking a league on
//: Value Bets or Results threw the reader onto Predictions. A control that looks
//: global has to act where the reader already is.
//:
//: Each view narrows in its own way, because "filter by league" does not mean
//: the same thing on a page of tips as on a list of finished matches:
//:
//:   * Predictions / Value Bets / Results -- re-fetched with competition_id.
//:   * Livescores -- the day board narrows to that league's fixtures.
//:   * Performance -- a model-wide backtest has no per-league view, so the
//:     selection is carried to Predictions rather than silently ignored: the
//:     reader gets an answer instead of a filter that appears not to work.
//:   * Fixture / match detail pages -- navigating away would discard the match
//:     being read, so the pick is remembered for the next list view.
async function applyCompetitionFilter() {
  switch (state.view) {
    case "tips":
      await refreshTips();
      return;
    case "value":
      document.getElementById("view").innerHTML =
        `<div class="loading"><div class="spinner"></div>Scanning for value…</div>`;
      try { await loadValueBets(); renderValueBets(); }
      catch (e) {
        document.getElementById("view").innerHTML =
          `<div class="error-box">Could not load value bets: ${esc(e.message)}</div>`;
      }
      renderSidebar();
      return;
    case "results":
      await renderResults();
      renderSidebar();
      return;
    case "livescores":
      renderLivescores({ keepPoll: true });
      renderSidebar();
      return;
    case "stats":
    case "fixture":
    case "match":
    default:
      // Nothing on this view can honour a league, so the selection is taken to
      // the page that can. The toast says so, rather than leaving the reader to
      // wonder why the board did not change.
      if (state.view === "stats") {
        toast(`Filtering ${state.filters.competitionLabel || "the league"} on Predictions`);
      }
      await navigate("tips");
      return;
  }
}

//: Wire the day tabs. Called once from boot(): the tab row lives in index.html
//: and is not replaced by any renderer, so the listeners survive.
function wireSideTabs() {
  const tabs = document.getElementById("side-tabs");
  if (!tabs) return;
  tabs.querySelectorAll("[data-side-day]").forEach((el) =>
    el.addEventListener("click", async () => {
      const next = el.dataset.sideDay || "all";
      if (next === sideDay()) return;
      state.sideDay = next;
      state.page = 1;
      // The day tab drives the same `date` query the Predictions feed uses, so
      // the sidebar narrowing and the main list narrowing can never disagree.
      state.filters.date = next;
      await refreshTips();
    })
  );
}

async function refreshTips(opts = {}) {
  const root = document.getElementById("view");
  const search = state.filters.search;
  root.innerHTML = `<div class="loading"><div class="spinner"></div>Running the model…</div>`;

  // The sidebar lives OUTSIDE #view, so replacing #view does not touch it. Redraw
  // it now, from whatever is already cached, with the day tabs updated the
  // instant they are clicked. Without this the reader clicks "Today", watches the
  // tab stay put, and sees "Loading competitions…" for as long as the model takes.
  renderSidebar();

  try {
    // The day counts are re-read alongside the tips: a day tab narrows both, and
    // a badge from a stale summary would contradict the list beside it.
    await loadDaySummary();
    renderSidebar();
    await loadTips();
  } catch (e) {
    root.innerHTML = `<div class="error-box">Could not load tips: ${esc(e.message)}</div>`;
    renderSidebar();
    return;
  }
  renderTips();
  renderSidebar();
  if (opts.keepFocus) {
    const input = document.getElementById("f-search");
    if (input) { input.focus(); input.setSelectionRange(search.length, search.length); }
  }
}

// ---------------------------------------------------------------- value bets

// The model prices exactly three market categories; everything else a book
// offers is a re-expression of the same scoreline matrix. Showing that split on
// the Value Bets page is what keeps the page honest: it names the markets a
// value figure could ever come from, and marks the rest as book-only.
function marketIndexPanel() {
  const idx = state.marketIndex;
  if (!idx || !idx.categories) return "";
  const model = idx.model_priced || [];
  const modelCats = idx.categories.filter((c) => c.model_priced);
  const bookCount = idx.categories.length - modelCats.length;
  return `
    <div class="mkt-index">
      <div class="mkt-index-head">
        Markets the model prices
        <span class="mkt-key">
          <span class="mkt-dot model"></span> priced by the model
          <span class="mkt-dot book"></span> book only
        </span>
      </div>
      <ul class="mkt-list">
        ${modelCats
          .map(
            (c) => `
          <li class="mkt-row present" title="${esc(c.note || "Priced by the GoalEdge engine.")}">
            <span class="mkt-dot model"></span>
            <span class="mkt-name">${esc(c.label)}</span>
            <span class="mkt-tag">model</span>
          </li>`
          )
          .join("")}
      </ul>
      <div class="mkt-notice">
        A value bet can only ever come from these ${modelCats.length} categories — ${esc(
          model.join(", ")
        )}. The other ${bookCount} categories a book publishes (handicaps, double chance,
        correct score, streaks) are re-expressions of the same scoreline matrix, so listing
        them with an edge would be inventing authority the engine does not have.
      </div>
    </div>`;
}

function renderValueBets() {
  const root = document.getElementById("view");
  const v = applySearch("value", state.valueBets, rowSearchText);
  const searching = (state.searches.value || "").trim().length > 0;

  root.innerHTML = `
    <div class="page-head">
      <h1>Value Bets</h1>
      <p>Selections where the model's probability is higher than the bookmaker's <em>de-vigged</em>
      implied probability. Edge is the gap between the two — a positive edge is the only
      mathematically defensible reason to place a bet.</p>
    </div>

    <div class="notice">
      <strong>How to read this:</strong> “Edge” is model probability minus the market's true
      (margin-removed) probability. “EV” is expected profit per 1 unit staked. Stakes shown use
      quarter-Kelly, capped at 5% of bankroll — a conservative sizing rule.
    </div>

    <div class="ls-toolbar">
      ${searchBox("value", "Search team, league or market…")}
      <span class="ls-meta"><strong>${v.length}</strong>
        ${v.length === 1 ? "selection" : "selections"}${searching ? " matched" : ""}</span>
    </div>

    ${marketIndexPanel()}

    ${
      v.length
        ? `<div class="card" style="padding:0;overflow-x:auto">
             <table class="data">
               <thead>
                 <tr>
                   <th>Kick-off</th><th>Match</th><th>Market</th><th>Selection</th>
                   <th class="num">Model</th><th class="num">Implied</th><th class="num">Odds</th>
                   <th class="num">Fair</th><th class="num">Edge</th><th class="num">EV</th>
                   <th class="num">Stake</th>
                 </tr>
               </thead>
               <tbody>
                 ${v
                   .map((b) => {
                     const k = kickoffLabel(b.kickoff);
                     return `<tr data-fixture="${b.fixture_id}" style="cursor:pointer">
                       <td style="white-space:nowrap;color:var(--text-dim);font-size:12.5px">${esc(k.date)}<br>${esc(k.time)}</td>
                       <td><strong>${esc(b.home_team)}</strong> vs ${esc(b.away_team)}<br>
                           <span style="font-size:11.5px;color:var(--text-faint)">${esc(b.competition)}</span></td>
                       <td style="color:var(--text-dim)">${esc(b.market_name)}</td>
                       <td style="color:var(--accent);font-weight:700">${esc(b.selection_label)}</td>
                       <td class="num">${esc(pct(b.probability))}</td>
                       <td class="num" style="color:var(--text-dim)">${esc(pct(b.implied_probability))}</td>
                       <td class="num"><strong>${num(b.odds)}</strong></td>
                       <td class="num" style="color:var(--text-dim)">${num(b.fair_odds)}</td>
                       <td class="num pos"><strong>${esc(signed(b.edge))}</strong></td>
                       <td class="num pos">${esc(signed(b.expected_value))}</td>
                       <td class="num">${num(b.kelly_stake, 1)}%</td>
                     </tr>`;
                   })
                   .join("")}
               </tbody>
             </table>
           </div>`
        : searching
          ? `<div class="empty"><div class="big">${icon("search_off")}</div>
             No value bets match “${esc(state.searches.value)}”.<br>
             <span style="font-size:13px">Search covers both teams, the competition, the market
             and the selection. Clear the box to see every value bet.</span></div>`
          : `<div class="empty">No value bets right now.<br>
             <span style="font-size:13px">Two honest reasons this page can be empty: the model and a
             real book agree on every upcoming fixture, or the upcoming fixtures are real matches whose
             prices are the model's own — there is no outside market to beat. Either way, no synthetic
             price is shown here as though it were a real edge.</span></div>`
    }
  `;

  root.querySelectorAll("[data-fixture]").forEach((el) =>
    el.addEventListener("click", () => openDetail(el.dataset.fixture))
  );
  wireSearch(root, renderValueBets);
}

// ---------------------------------------------------------------- results
//: How many days back the Results page will look. The chips offer exactly these,
//: and the API is clamped to the same ceiling server-side, so a hand-edited URL
//: cannot pull a two-month archive back onto a page that is meant to show the
//: most recent rounds.
const RESULTS_WINDOW_MAX = 3;

//: The most rows the Results page will ask for.
//:
//: Every row costs a model fit on the server, so this is a real cost ceiling, not
//: just a page size.
//:
//: 60 was too low: a single busy day holds several hundred settled matches, so
//: "Last 2 days" and "Last 3 days" both rendered exactly 60 rows and the chips
//: read as though they did nothing. A day runs to roughly 380 finished matches,
//: so the ceiling sits at three days' worth -- above every window's real set, and
//: still a hard bound on the work the request can cause.
const RESULTS_MAX_ROWS = 1200;

//: The phrase for a window length, so "last day" never renders as "Last 1 days".
function resultsWindowLabel(days) {
  const n = Number(days) || 1;
  if (n <= 1) return "last day";
  return `last ${n} days`;
}

async function loadResults(days = state.resultsDays) {
  // Clamped here as well as server-side: the chips only offer 1-3, and a stale
  // value in state (or a deep link) must not ask for more than the page shows.
  state.resultsDays = Math.min(Math.max(1, Number(days) || 1), RESULTS_WINDOW_MAX);
  const src = state.resultsSource ? `&source=${state.resultsSource}` : "";
  // The sidebar's league selection, as a real server-side filter -- the same
  // rule as /tips. Filtering the capped fetch locally would have dropped every
  // result outside the first N rows.
  const comp = state.filters.competitionId
    ? `&competition_id=${encodeURIComponent(state.filters.competitionId)}`
    : "";
  // A limit of 60 made "Last 2 days" and "Last 3 days" indistinguishable: both
  // windows hold far more finished matches than that, so both rendered exactly 60
  // rows and the chips looked broken. 200 was still short of the real totals, so
  // the cap is now the API's own ceiling -- the window is only three days, so the
  // full set is bounded and can be fetched whole.
  state.results = await api(`/results?days=${state.resultsDays}&limit=${RESULTS_MAX_ROWS}${src}${comp}`);
}

// A green tick for a winning prediction, a red cross for a miss, and a neutral
// dash when the market is one the engine does not price. The green tick is the
// point of the whole page: it is the model's published pick, graded honestly.
function settleMark(won) {
  if (won === true)
    return `<span class="settle settle-win" title="Prediction correct">${icon("check_circle", "mi-fill")}<span>Correct</span></span>`;
  if (won === false)
    return `<span class="settle settle-loss" title="Prediction missed">${icon("cancel", "mi-fill")}<span>Missed</span></span>`;
  return `<span class="settle settle-void" title="GoalEdge does not price this market">—</span>`;
}

function resultRow(r) {
  const k = kickoffLabel(r.kickoff);
  const hitClass = r.hit === true ? "is-hit" : r.hit === false ? "is-miss" : "is-void";
  const score =
    r.home_goals == null
      ? "—"
      : `<span class="res-score">${r.home_goals} – ${r.away_goals}</span>`;
  // A real match (pulled from the live feed) is labelled "Live data" so a
  // simulated scoreline is never presented as though it were real football.
  const sourceTag = r.is_real
    ? `<span class="src-tag src-real">Live data</span>`
    : `<span class="src-tag src-sim">Simulated</span>`;

  // The per-market breakdown is deliberately NOT rendered on this row.
  //
  // A results card answers one question -- did the published pick land -- and
  // the score, the prediction and the 3/7 summary already answer it. Printing
  // all 23 markets below every played match buried that under ~117 selection
  // pills and stretched each card to ~1800px, a page of 60 cards running past
  // 100,000px. Most of those pills were graded losses, so the block read as a
  // wall of failure noise rather than verification. The full breakdown lives on
  // the fixture page, one tap away via the card.
  return `
    <article class="res-card ${hitClass}" data-fixture="${r.fixture_id}">
      <div class="res-top">
        <div class="res-meta">
          <span class="tip-comp">${esc(r.competition)}</span>
          <span>·</span>
          <span class="tip-time">${esc(k.date)} ${esc(k.time)}</span>
          ${sourceTag}
        </div>
        ${settleMark(r.hit)}
      </div>

      <div class="res-teams">
        <div class="res-team">
          ${crest(r.home_logo)}
          <span>${esc(r.home_team)}</span>
        </div>
        ${score}
        <div class="res-team away">
          <span>${esc(r.away_team)}</span>
          ${r.away_logo ? `<img class="tip-logo" src="${esc(r.away_logo)}" alt="" loading="lazy">` : ""}
        </div>
      </div>

      <div class="res-prediction">
        <div class="res-pred-label">GoalEdge prediction</div>
        <div class="res-pred-value">${esc(r.prediction)}</div>
        <div class="res-pred-meta">
          <span class="badge badge-edge">${esc(pct0(r.probability))} model</span>
          ${r.odds ? `<span class="badge badge-edge">odds ${num(r.odds)}</span>` : ""}
          <span class="badge ${confClass(r.confidence)}">${esc(r.confidence_label)} · ${r.confidence}</span>
          <span class="res-count">${r.selections_correct}/${r.selections_graded} selections correct</span>
        </div>
      </div>

      <div class="res-foot">
        <a class="res-live" href="${esc(r.sportybet_url)}" target="_blank" rel="noopener noreferrer"
           onclick="event.stopPropagation()">
          ${icon("open_in_new", "mi-sm")} Live score on SportyBet
        </a>
        <a class="res-live res-fs" href="${esc(r.flashscore_url || "https://www.flashscore.com.gh/")}"
           target="_blank" rel="noopener noreferrer"
           onclick="event.stopPropagation()">
          ${icon("open_in_new", "mi-sm")} Match on Flashscore
        </a>
        <span class="res-hint">Tap the card for the full model breakdown</span>
      </div>
    </article>`;
}

async function renderResults() {
  const root = document.getElementById("view");
  root.innerHTML = `<div class="loading"><div class="spinner"></div>Grading finished matches…</div>`;
  try {
    await loadResults();
  } catch (e) {
    root.innerHTML = `<div class="error-box">Could not load results: ${esc(e.message)}</div>`;
    return;
  }
  renderResultsView();
}

// The presentational half of the Results page, split out so a search re-renders
// the rows already fetched instead of hitting the API on every keystroke.
function renderResultsView() {
  const root = document.getElementById("view");
  const data = state.results || { items: [], wins: 0, losses: 0, hit_rate: 0 };
  const searched = applySearch("results", data.items || [], rowSearchText);
  const searching = (state.searches.results || "").trim().length > 0;

  // The market filter, applied before the rows are rendered.
  //
  // Each settled match names the market whose pick it is DISPLAYING, on the
  // match itself: `market: "btts"` alongside `prediction: "BTTS: Yes"` and the
  // `hit` for that pick. That field is the filter's key.
  //
  // Two earlier attempts were wrong and both looked plausible:
  //
  //   * filtering on "this match prices the market" kept every row, because the
  //     engine prices all 22 markets for every fixture;
  //   * filtering on "this market has a graded selection" then picked `[0]` of
  //     that market's selections, which is the FIRST line of the market, not the
  //     one the row recommends. On a 1X2 market that listed every match under
  //     its own home-win price whether or not that was the pick.
  //
  // Keying on `item.market` is exact: it is the same field the headline row is
  // built from, so the filter and the thing displayed cannot disagree.
  const marketPage = MARKET_PAGES.find((m) => m.slug === state.resultsMarket);

  // One market key can back several buttons -- Over 2.5 and Under 2.5 are the
  // same `ou25` market with opposite picks -- so the selection is matched by the
  // pick's own direction as well as the market's key. Without that, "Under 2.5"
  // and "Over 2.5" returned the identical list.
  const matchesMarket = (item) => {
    if (!marketPage) return true;
    if (item.market !== marketPage.key) return false;
    if (!marketPage.pick) return true;
    const pick = String(item.prediction || "").toLowerCase();
    return marketPage.pick === "over" ? /over/.test(pick) : /under/.test(pick);
  };
  const items = searched.filter(matchesMarket);

  // The market's record over the surviving matches, from the match-level fields
  // the row itself displays -- so the count and the green ticks below it are
  // derived from the same numbers, and cannot drift apart.
  let marketStats = null;
  if (marketPage) {
    let settled = 0;
    let landed = 0;
    for (const item of items) {
      if (item.hit == null) continue;
      settled += 1;
      if (item.hit === true) landed += 1;
    }
    marketStats = { settled, landed, rate: settled ? landed / settled : 0 };
  }

  // The headline counters. When a market is selected they describe THAT market's
  // record, not the whole board: a page reading "1999 settled / 32 matches" at
  // once is two different questions answered in one breath, and the larger
  // number is the one the eye lands on first.
  const wins = marketStats ? marketStats.landed : data.wins;
  const losses = marketStats ? marketStats.settled - marketStats.landed : data.losses;
  const hitRate = marketStats ? marketStats.rate : data.hit_rate;
  const countLabel = marketPage
    ? `${esc(marketPage.title)} picks, ${esc(resultsWindowLabel(data.window_days))}`
    : `in the ${esc(resultsWindowLabel(data.window_days))}`;

  // Three windows only: the last day, the last two days, the last three. The
  // page is a verification surface -- you check that what the model published
  // before kick-off actually landed -- and that is done over the most recent
  // rounds, not over a two-month archive. A 60-day window mostly returned
  // fixtures from an older season whose predictions were never published, which
  // read as an admission of poor form rather than as a record.
  const RESULTS_WINDOWS = [
    [1, "Last day"],
    [2, "Last 2 days"],
    [3, "Last 3 days"],
  ];
  const dayChips = RESULTS_WINDOWS
    .map(
      ([d, label]) =>
        `<button class="chip ${state.resultsDays === d ? "active" : ""}" data-days="${d}">${label}</button>`
    )
    .join("");
  const srcChips = [
    ["", "All matches"],
    ["flashscore", "Live data"],
    ["seed", "Simulated"],
  ]
    .map(
      ([v, label]) =>
        `<button class="chip ${state.resultsSource === v ? "active" : ""}" data-src="${v}">${label}</button>`
    )
    .join("");

  // The market row, mirroring the Predictions tab's own market pages so the two
  // are navigated the same way. Each button narrows the settled list to the
  // matches where the model actually had a pick in that market -- so "Correct
  // Score" answers "how did the model's exact-scoreline calls do", which is a
  // question the headline tip alone cannot answer.
  //
  // A market is only offered when some settled match actually has a GRADED
  // selection in it. Pricing every market is not the same as grading every
  // market -- the API currently settles 1X2, Over/Under and BTTS and returns the
  // A button is offered only when the settled data actually contains a match
  // whose DISPLAYED pick is in that market -- counted from `item.market`, the
  // same field the filter uses. Counting from `markets[].selections` instead
  // reported a market as available whenever any match merely *priced* it, which
  // is how Double Chance and Correct Score got buttons leading to an empty list:
  // the engine prices them, but the API leaves them ungraded.
  //
  // A pick is only usable for verification once its outcome exists, so an
  // ungraded one is not counted. `hit` is the match-level outcome.
  const marketCounts = {};
  const pickCounts = {};
  for (const item of data.items || []) {
    if (!item.market) continue;
    if (item.hit !== true && item.hit !== false) continue;
    marketCounts[item.market] = (marketCounts[item.market] || 0) + 1;
    // Over 2.5 and Under 2.5 share the `ou25` market key, so the direction is
    // part of the key. Without it both buttons reported the same count.
    const pick = String(item.prediction || "").toLowerCase();
    const dir = /under/.test(pick) ? "under" : "over";
    pickCounts[`${item.market}:${dir}`] = (pickCounts[`${item.market}:${dir}`] || 0) + 1;
  }
  const marketAvailable = (page) =>
    page.pick
      ? Boolean(pickCounts[`${page.key}:${page.pick}`])
      : Boolean(marketCounts[page.key]);
  const marketChips = [
    `<button class="chip ${state.resultsMarket === "" ? "active" : ""}" data-market="">All markets</button>`,
    ...MARKET_PAGES.filter(marketAvailable).map(
      (page) => `<button class="chip ${state.resultsMarket === page.slug ? "active" : ""}"
        data-market="${esc(page.slug)}" title="${esc(page.blurb)}">${esc(page.title)}</button>`
    ),
  ].join("");

  root.innerHTML = `
    <div class="page-head">
      <h1>Results &amp; Verification</h1>
      <p>Every finished match, graded against the prediction the model actually published before
      kick-off. A <span class="settle settle-win" style="display:inline-flex">${icon("check_circle", "mi-fill")}<span>green tick</span></span>
      means the pick came in. Misses are shown too — an honest record is the only record worth keeping.</p>
    </div>

    <div class="grid grid-4">
      <div class="stat-card">
        <div class="stat-label">Predictions settled</div>
        <div class="stat-value">${wins + losses}</div>
        <div class="stat-sub">${countLabel}</div>
      </div>
      <div class="stat-card">
        <div class="stat-label">Correct (green ticks)</div>
        <div class="stat-value pos">${wins}</div>
        <div class="stat-sub">${marketPage ? "this market's pick landed" : "headline pick landed"}</div>
      </div>
      <div class="stat-card">
        <div class="stat-label">Missed</div>
        <div class="stat-value neg">${losses}</div>
        <div class="stat-sub">${marketPage ? "this market's pick beaten" : "headline pick beaten"}</div>
      </div>
      <div class="stat-card">
        <div class="stat-label">Hit rate</div>
        <div class="stat-value accent">${esc(pct(hitRate))}</div>
        <div class="stat-sub">graded picks</div>
      </div>
    </div>

    <div class="notice" style="margin-top:16px;display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap">
      <span><strong>Verify at the source:</strong> real matches carry a <em>Live data</em> tag and link back to
      Flashscore, the feed the dates, kick-off times and results are fetched from — and to SportyBet's own
      live-score board. Nothing has to be taken on trust.</span>
      <span style="display:flex;gap:8px;flex-wrap:wrap">
        <a class="btn btn-sm" href="https://www.flashscore.com.gh/"
           target="_blank" rel="noopener noreferrer">
          ${icon("open_in_new", "mi-sm")} Flashscore</a>
        <a class="btn btn-sm" href="${esc(data.live_url || "https://www.sportybet.com/ng/livescore")}"
           target="_blank" rel="noopener noreferrer">
          ${icon("open_in_new", "mi-sm")} SportyBet live</a>
      </span>
    </div>

    <div class="filters">
      <div class="chip-row">${dayChips}</div>
      <div class="chip-row">${srcChips}</div>
      <div class="chip-row res-market-row" id="res-markets">${marketChips}</div>
      ${searchBox("results", "Search team or competition…")}
      <span style="margin-left:auto;font-size:13px;color:var(--text-dim)">
        ${items.length} match${items.length === 1 ? "" : "es"}</span>
    </div>

    ${marketPage
      ? `<div class="notice res-market-note">
           <span><strong>${esc(marketPage.title)}</strong> — ${esc(marketPage.blurb)}
           Markets other than 1X2, Over/Under and BTTS are priced from the same
           scoreline matrix, so they are graded here against the same model output.</span>
           ${marketStats.settled
             ? `<span class="res-market-stat">
                  <strong>${marketStats.landed}/${marketStats.settled}</strong>
                  picks landed (${esc(pct0(marketStats.rate))})</span>`
             : `<span class="res-market-stat">No settled picks in this window</span>`}
         </div>`
      : ""}

    ${items.length
        ? `<div class="res-list">${items.map(resultRow).join("")}</div>`
        : marketPage
          ? `<div class="empty"><div class="big">${icon("insights")}</div>
             No settled ${esc(marketPage.title)} picks in the ${esc(resultsWindowLabel(state.resultsDays))}.<br>
             <span style="font-size:13px">Widen the window, or pick another market. The model only
             prices this market for fixtures it has a full history for.</span></div>`
          : searching
            ? `<div class="empty"><div class="big">${icon("search_off")}</div>
               No finished matches match “${esc(state.searches.results)}”.<br>
               <span style="font-size:13px">Search covers both teams and the competition. Clear the
               box, or widen the window.</span></div>`
            : `<div class="empty"><div class="big">${icon("scoreboard")}</div>No finished matches in the ${esc(resultsWindowLabel(state.resultsDays))}.<br>
               <span style="font-size:13px">Try the last two or three days, or wait for the next round to settle.</span></div>`}
  `;

  root.querySelectorAll("[data-days]").forEach((el) =>
    el.addEventListener("click", async () => {
      state.resultsDays = Number(el.dataset.days);
      await renderResults();
    })
  );
  root.querySelectorAll("[data-src]").forEach((el) =>
    el.addEventListener("click", async () => {
      state.resultsSource = el.dataset.src;
      await renderResults();
    })
  );
  // The market buttons re-render from the rows already fetched rather than
  // refetching: the filter is a view over data the page holds, so a round trip
  // per click would be a slower way to show the same list.
  root.querySelectorAll("[data-market]").forEach((el) =>
    el.addEventListener("click", () => {
      state.resultsMarket = el.dataset.market;
      renderResultsView();
    })
  );
  root.querySelectorAll("[data-fixture]").forEach((el) =>
    el.addEventListener("click", () => openDetail(el.dataset.fixture))
  );
  // Re-rendering the whole page on each keystroke would fight the debounce, so
  // the search re-runs the cheap in-memory render rather than re-fetching.
  wireSearch(root, () => renderResultsView());
}

// ---------------------------------------------------------------- livescores
// A Flashscore-style board: one day, grouped by competition, live matches first.
//
// The whole page is one data source (/api/livescores) and no prediction logic:
// a live score beside a Poisson projection invites the reader to confuse the two,
// so the score is the only number on the row.

const LIVE_POLL_MS = 30000;

//: How many day-quick-chips to offer around today, for a one-click glance at
//: the surrounding week without touching the date picker. The picker reaches any
//: day inside the server's season window, so these are shortcuts on top of a
//: real range, not the limit of the board.
//: How many day-quick-chips to offer after today. The board runs to the end of
//: the season, so the chips walk forward a fortnight -- enough to plan around the
//: coming fixtures without turning the row into a wall of dates. The date picker
//: reaches every day from today forward.
const LS_QUICK_FORWARD_DAYS = 14;
//: The first chip is TOMORROW, because today has its own button above.
const LS_QUICK_FIRST_OFFSET = 1;
//: The board does not look back. A finished match is not a livescore, and the
//: Results page is the one place that owns settled football; offering yesterday
//: here made the app answer "what happened last night" in two different ways.
const LS_QUICK_BACK_DAYS = 0;

// The status tabs across the top of the board. A status filter, not a second
// day picker -- "ODDS" from the reference board is absent because this board
// deliberately shows no prices (a live score beside a book price invites the
// reader to read one as the other).
//: The eight competitions "Top leagues" narrows the board to. This is the SAME
//: list the backend uses to protect fixtures from the per-day cap (see
//: TOP_LEAGUE_KEYS in backend/app/flashscore.py); the API sends `is_top` on
//: every row, computed there, so the client never re-derives it. This constant
//: exists only for the label.
const LS_TOP_LABEL = "Top leagues";

const LS_FILTERS = ["all", "live", "finished", "scheduled"];
const LS_FILTER_LABEL = {
  all: "All",
  live: "Live",
  finished: "Finished",
  scheduled: "Scheduled",
};

// A module-level handle so leaving the page can cancel the poll. Without this the
// timer keeps fetching after the user has navigated to Predictions.
let livescoreTimer = null;

// `date` may be a keyword ("today") or a YYYY-MM-DD string; the API accepts
// both. The resolved day comes back on the payload as `day`, and that -- not
// the keyword the caller passed -- is what the refresh and the 30s poll use, so
// a picker jump is never silently undone on the next tick.
async function loadLivescores(date = "today") {
  state.livescoreDate = date;
  state.livescores = await api(`/livescores?date=${encodeURIComponent(date)}`);
  return state.livescores;
}

// ---------------------------------------------------------------- search
//
// A search box on every tab, so a team can be found from wherever the reader
// happens to be. Two things make it a single reusable piece rather than four
// copies:
//
//   * the box is a pure function of a tab key, and
//   * the filtering is one shared text matcher that searches the same fields on
//     every page (both teams, the competition, the country).
//
// Matching is done on the *data already on screen*, not by re-querying: every
// tab's payload is small enough to filter in memory, so typing stays instant and
// cannot hammer the API. The Predictions tab is the exception -- it has a large
// fixture list and a server-side search, so it reuses that rather than duplicating
// the work here.

//: The fields a search looks at, in the shape every tab's rows share.
function searchHaystack(parts) {
  return parts.filter(Boolean).join(" ").toLowerCase();
}

function matchesSearch(needle, parts) {
  const q = (needle || "").trim().toLowerCase();
  if (!q) return true;
  return searchHaystack(parts).includes(q);
}

//: The canonical row fields every tab can be searched on.
function rowSearchText(row) {
  return [
    row.home_team,
    row.away_team,
    row.competition,
    row.competition_label,
    row.country,
    row.market_name,
    row.selection_label,
  ];
}

function searchBox(key, placeholder) {
  const value = state.searches[key] || "";
  const hits = (state.matches[key] || []).length;
  return `
    <div class="search-box">
      ${icon("search", "mi-sm")}
      <input type="search" id="srch-${key}" data-search="${key}"
             placeholder="${esc(placeholder)}" value="${esc(value)}"
             autocomplete="off" aria-label="${esc(placeholder)}">
      <span class="search-count">${hits}</span>
    </div>`;
}

// Wire every search box the current render emitted. One delegated input handler
// means a new tab only has to call searchBox() and pass its rows through
// applySearch() -- there is no per-tab wiring to forget.
function wireSearch(root, rerender) {
  root.querySelectorAll("[data-search]").forEach((input) => {
    let timer;
    input.addEventListener("input", (e) => {
      clearTimeout(timer);
      // Debounced so a filter over a few hundred rows does not re-render on
      // every keystroke.
      timer = setTimeout(() => {
        state.searches[e.target.dataset.search] = e.target.value;
        rerender();
        // Re-focus the box after the re-render replaced the DOM, and put the
        // caret back at the end so typing is not interrupted.
        const again = document.getElementById("srch-" + e.target.dataset.search);
        if (again) {
          again.focus();
          again.setSelectionRange(again.value.length, again.value.length);
        }
      }, 220);
    });
  });
}

//: Filter rows and record the hit count for the box's counter.
function applySearch(key, rows, textOf) {
  const q = (state.searches[key] || "").trim();
  const out = q ? rows.filter((r) => matchesSearch(q, textOf(r))) : rows;
  state.matches[key] = out;
  return out;
}

// (The old three-word day labels are gone: the board now navigates by real
// dates, and `lsDateChipLabel` renders "Today"/"Yesterday"/"Tomorrow" for the
// three days that deserve a word and a full date for every other day.)

// The kick-off time in the reader's own timezone -- the feed ships UTC timestamps
// and the whole point of a livescore board is the local clock.
function lsTime(iso) {
  if (!iso) return "—";
  return new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

// The in-play clock. The feed publishes NO live clock -- probed directly, the
// candidate fields either never move (AO) or are a phase code (AC) -- so the
// minute is derived from kick-off and the API labels it `minute_source:
// "estimated"`. This function only renders that label; it never invents one.
function lsMinuteLabel(row) {
  if (row.status !== "live") return lsTime(row.kickoff);
  return row.minute_label || "Live";
}

// The kick-off clock-time of a match, which the feed *does* publish exactly.
// The API sends it pre-split (`time`), so this prefers that and only falls back
// to parsing the timestamp.
function lsKickoffTime(row) {
  if (row.time) return row.time;
  if (row.kickoff) return lsTime(row.kickoff);
  return "—";
}

// The full calendar date as "19 Sep 2026". A board that can span two years
// needs the year on the row: "19 Sep" alone would make a match from last
// season and one from this season look like the same day.
function lsFullDate(row) {
  const d = row.date ? new Date(row.date + "T00:00:00") : row.kickoff ? new Date(row.kickoff) : null;
  if (!d || isNaN(d)) return "";
  return d.toLocaleDateString([], { day: "2-digit", month: "short", year: "numeric" });
}

// The calendar date of the match, in the reader's own timezone. Shown on every
// row because a board that only ever said "Today" made a match at 00:30 and one
// at 23:00 look interchangeable, and on a next-day look-back the day is the
// whole point.
function lsDateLabel(row) {
  const iso = row.date
    ? new Date(row.date + "T00:00:00")
    : row.kickoff
      ? new Date(row.kickoff)
      : null;
  if (!iso || isNaN(iso)) return "";
  return iso.toLocaleDateString([], { day: "2-digit", month: "short" });
}

// "Today"/"Yesterday"/"Tomorrow" when the match is on one of those days, so the
// common case reads at a glance rather than as a bare date.
function lsDayWord(row) {
  if (!row.date) return "";
  const now = new Date();
  const iso = (d) =>
    `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
  const today = iso(now);
  const yest = iso(new Date(now.getTime() - 864e5));
  const tom = iso(new Date(now.getTime() + 864e5));
  if (row.date === today) return "Today";
  if (row.date === yest) return "Yesterday";
  if (row.date === tom) return "Tomorrow";
  return lsDateLabel(row);
}

// ---------------------------------------------------- date navigation (2y)
//
// The board is not limited to three days: any date inside the server's window
// (two years back) can be requested. Three helpers make that reachable without
// a calendar widget dependency.

//: The server's window, learned from the last payload so the picker's bounds
//: are exactly what the API will serve rather than a guessed range.
function lsWindowBounds() {
  const w = state.livescores && state.livescores.window;
  if (w && w.earliest && w.latest) return w;
  // The pre-load fallback is TODAY forward, matching what the server serves. It
  // used to claim 730 days of history, which is exactly the range the board no
  // longer offers -- a picker built on it would have offered last season.
  const today = toIsoDate(new Date());
  return { earliest: today, latest: today, back_days: 0, forward_days: 14 };
}

//: The currently-shown day as a `YYYY-MM-DD` string. The API echoes the
//: resolved day back, so a relative value like "today" is never left unresolved.
function lsCurrentDay() {
  const d = state.livescores && state.livescores.day;
  if (d) return d;
  return toIsoDate(new Date());
}

function toIsoDate(d) {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

//: Shift the shown day by whole days, clamped to the window. Clamping rather
//: than letting the API 400 keeps the prev/next buttons always usable.
function lsShiftDay(delta) {
  const bounds = lsWindowBounds();
  const base = new Date(lsCurrentDay() + "T00:00:00");
  base.setDate(base.getDate() + delta);
  let iso = toIsoDate(base);
  if (bounds.earliest && iso < bounds.earliest) iso = bounds.earliest;
  if (bounds.latest && iso > bounds.latest) iso = bounds.latest;
  return iso;
}

//: The dates the quick chips cover: a fortnight forward from TOMORROW, not from
//: today. Today and Tomorrow already have their own buttons in the row above, and
//: repeating them here put "Today" on the page twice.
//:
//: Clamped to the server's own window, so a chip can never ask for a day the API
//: rejects.
function lsQuickDates() {
  const bounds = lsWindowBounds();
  const out = [];
  for (let i = LS_QUICK_FIRST_OFFSET; i <= LS_QUICK_FORWARD_DAYS; i++) {
    const iso = toIsoDate(new Date(Date.now() + i * 864e5));
    if (iso >= bounds.earliest && iso <= bounds.latest) out.push(iso);
  }
  return out;
}

//: "Today" / "Tomorrow", else "Sat 19 Sep".
//:
//: No "Yesterday": the board does not serve past days, so a label for one would
//: only ever be a leftover from a stale payload.
function lsDateChipLabel(iso) {
  const today = toIsoDate(new Date());
  const tom = toIsoDate(new Date(Date.now() + 864e5));
  if (iso === today) return "Today";
  if (iso === tom) return "Tomorrow";
  const d = new Date(iso + "T00:00:00");
  if (isNaN(d)) return iso;
  return d.toLocaleDateString([], { weekday: "short", day: "2-digit", month: "short" });
}



// One score cell for one team. A scheduled match has no score yet, so the cell
// is empty rather than a 0 -- showing 0-0 before kick-off states a result that
// has not happened.
function lsTeamScore(row, side) {
  if (row.status === "scheduled") return `<span class="ls-goal"></span>`;
  const goals = side === "home" ? row.home_goals : row.away_goals;
  if (goals == null) return `<span class="ls-goal">–</span>`;
  return `<span class="ls-goal">${esc(String(goals))}</span>`;
}

function livescoreRow(row) {
  const isLive = row.status === "live";
  const isFinished = row.status === "finished";
  const linkable = row.id != null;
  const open = state.lsOpenMatch != null && String(state.lsOpenMatch) === String(row.id);
  const homeWon = isFinished && row.home_goals > row.away_goals;
  const awayWon = isFinished && row.away_goals > row.home_goals;

  // The status cell carries BOTH facts the reader needs, and they are kept
  // visibly distinct:
  //   * the kick-off time -- published by the feed to the second, and
  //   * the in-play minute -- published by the match feed and counted up live
  //     on the match page. On the *board* it can only come from the day feed,
  //     which publishes no clock, so a board minute is still marked with a "~";
  //     opening the match is what turns it into the exact, minute-by-minute one.
  const statusCell = isLive
    ? `<span class="ls-pulse" aria-hidden="true"></span>
       <span class="ls-minute"${row.minute_source === "estimated" ? ' title="Minute derived from kick-off on the board — open the match for the feed\'s own minute-by-minute clock"' : ""}>${row.minute_source === "estimated" ? "~" : ""}${esc(lsMinuteLabel(row))}</span>
       <span class="ls-kick" title="Kick-off (${esc(lsFullDate(row))}) — exact, as published by the feed">${esc(lsKickoffTime(row))}</span>`
    : isFinished
      // "Finished" in words, not "FT". The abbreviation assumes the reader
      // knows the board's own jargon, and the whole complaint that produced this
      // was a finished match showing a *minute* -- so the label here spells the
      // status out rather than cutting it to two letters.
      ? `<span class="ls-done" title="Kick-off was ${esc(lsKickoffTime(row))} on ${esc(lsFullDate(row))}">Finished</span>
         <span class="ls-kick">${esc(lsKickoffTime(row))}</span>`
      : `<span class="ls-time">${esc(lsKickoffTime(row))}</span>`;

  // The row is clickable when there is a match page to open -- which needs the
  // FEED's match id, not the database's fixture id. The fixture id is also
  // carried, because the in-place panel and the model link use it.
  const openable = !!row.match_id;

  // The attribute list is built in ONE template literal rather than several
  // optional fragments. A fragment that carries its own closing `>` terminates
  // the opening tag early, and the browser then discards the malformed nesting
  // -- which silently removed every team name and score from the board while
  // leaving the crests (a later, well-formed subtree) on screen.
  const rowAttrs = [
    linkable ? `data-fixture="${esc(row.id)}"` : "",
    openable ? `data-match="${esc(row.match_id)}"` : "",
    openable ? 'role="link" tabindex="0"' : "",
    openable ? 'title="Open the match page"' : "",
  ].filter(Boolean).join(" ");

  return `
    <div class="ls-row ${isLive ? "is-live" : ""} ${isFinished ? "is-done" : ""} ${open ? "is-open" : ""} ${openable ? "is-openable" : ""}" ${rowAttrs}>

      <div class="ls-fav" title="Follow this match">${icon("star_border", "mi-sm")}</div>

      <div class="ls-status">${statusCell}</div>

      <div class="ls-teams">
        <div class="ls-line ${homeWon ? "winner" : ""}">
          <span class="ls-name">${esc(row.home_team)}</span>
          ${crest(row.home_logo)}
        </div>
        <div class="ls-line ${awayWon ? "winner" : ""}">
          <span class="ls-name">${esc(row.away_team)}</span>
          ${crest(row.away_logo)}
        </div>
      </div>

      <div class="ls-scores">
        <div class="ls-goal-row ${homeWon ? "winner" : ""}">${lsTeamScore(row, "home")}</div>
        <div class="ls-goal-row ${awayWon ? "winner" : ""}">${lsTeamScore(row, "away")}</div>
      </div>

      <div class="ls-actions">
        <span class="ls-divider" aria-hidden="true"></span>
        ${openable
          ? `<a class="ls-open-match" href="#/match/${esc(row.match_id)}"
                data-match-open="${esc(row.match_id)}"
                title="Open the match page — minute by minute, timeline and stats"
                onclick="event.stopPropagation()">${icon("arrow_forward", "mi-sm")}</a>`
          : ""}
        ${linkable
          ? `<button class="ls-expand" type="button"
                     data-ls-open="${esc(row.id)}"
                     aria-expanded="${open ? "true" : "false"}"
                     title="${open ? "Hide" : "Show"} match details">${icon(open ? "expand_less" : "expand_more", "mi-sm")}</button>`
          : `<span class="ls-nodetail" title="Details are unavailable for this match">${icon("info", "mi-sm")}</span>`}
        ${row.sportybet_url
          ? `<a class="ls-link" href="${esc(row.sportybet_url)}" target="_blank"
                rel="noopener noreferrer" title="Live score on SportyBet"
                onclick="event.stopPropagation()">${icon("open_in_new", "mi-sm")}</a>`
          : ""}
      </div>
    </div>
    ${open ? lsMatchDetail(row) : ""}`;
}

// The match-details panel, expanded in place under its row.
//
// In place, not on a new page: the request was for the details *in the
// Livescore tab*, and a reader scanning a day should not lose their place in
// the board to look at one fixture. The board's row already carries the result;
// this adds the facts the row has no room for -- full date, exact kick-off
// time, status, and a link into the model's own analysis of the fixture.
function lsMatchDetail(row) {
  const isLive = row.status === "live";
  const label = isLive ? "Live" : row.status === "finished" ? "Finished" : "Scheduled";
  return `
    <div class="ls-detail">
      <div class="ls-detail-grid">
        <div><span class="ls-detail-k">Competition</span>
             <span class="ls-detail-v">${esc(lsCompLabel({
               country: row.country, competition: row.competition,
             }))}</span></div>
        <div><span class="ls-detail-k">Date</span>
             <span class="ls-detail-v">${esc(lsFullDate(row) || "—")}</span></div>
        <div><span class="ls-detail-k">Kick-off</span>
             <span class="ls-detail-v">${esc(lsKickoffTime(row))} <em>local time</em></span></div>
        <div><span class="ls-detail-k">Status</span>
             <span class="ls-detail-v">${esc(label)}${
               isLive && row.minute_label
                 ? ` · ${esc(row.minute_label)} <em>estimate</em>`
                 : ""
             }</span></div>
        <div><span class="ls-detail-k">Score</span>
             <span class="ls-detail-v">${
               row.status === "scheduled"
                 ? "Not played yet"
                 : `${esc(row.home_team)} ${row.home_goals ?? "–"} – ${row.away_goals ?? "–"} ${esc(row.away_team)}`
             }</span></div>
        <div><span class="ls-detail-k">Source</span>
             <span class="ls-detail-v">Flashscore <em>score &amp; schedule</em></span></div>
      </div>
      <div class="ls-detail-actions">
        ${row.match_id
          ? `<a class="btn btn-sm btn-primary" href="#/match/${esc(row.match_id)}"
                data-match-open="${esc(row.match_id)}"
                onclick="event.stopPropagation()">
               ${icon("arrow_forward", "mi-sm")} Open the match</a>`
          : ""}
        ${row.id != null
          ? `<button class="btn btn-sm" type="button" data-ls-goto="${esc(row.id)}">
               ${icon("insights", "mi-sm")} Full analysis &amp; prediction</button>`
          : ""}
        ${row.match_id
          ? `<a class="btn btn-sm" target="_blank" rel="noopener noreferrer"
                href="https://www.flashscore.com.gh/match/${esc(row.match_id)}/">
               ${icon("open_in_new", "mi-sm")} View on Flashscore</a>`
          : ""}
      </div>
      <p class="ls-detail-note">
        Kick-off time is exact, as published by the source. The minute shown on
        this board is derived from kick-off, because the day feed carries no
        clock — <strong>open the match</strong> for the feed's own minute-by-minute
        clock, the timeline and the match statistics.
      </p>
    </div>`;
}

// Re-bucket flat rows into competition groups, mirroring the server's ordering
// (live first, then country, then competition). Needed because search filters a
// flat row list: without this, a group header would stay on screen after its
// last matching match had been filtered away.
function lsGroupRows(rows) {
  const buckets = new Map();
  rows.forEach((row) => {
    const key = row.competition_label || "Other";
    if (!buckets.has(key)) {
      buckets.set(key, {
        competition: row.competition || key,
        competition_label: key,
        country: row.country,
        items: [],
      });
    }
    buckets.get(key).items.push(row);
  });

  const groups = [...buckets.values()];
  groups.forEach((g) => {
    g.live_count = g.items.filter((i) => i.status === "live").length;
    g.items.sort((a, b) => {
      if ((a.status === "live") !== (b.status === "live")) return a.status === "live" ? -1 : 1;
      return String(a.kickoff || "").localeCompare(String(b.kickoff || ""));
    });
  });
  groups.sort(
    (a, b) =>
      b.live_count - a.live_count ||
      String(a.country || "").localeCompare(String(b.country || "")) ||
      String(a.competition || "").localeCompare(String(b.competition || ""))
  );
  return groups;
}

function livescoreGroup(group) {
  const live = group.live_count || 0;
  const collapsed = state.lsCollapsed.has(group.competition_label);
  return `
    <section class="ls-group ${collapsed ? "is-collapsed" : ""}">
      <header class="ls-group-head" data-ls-group="${esc(group.competition_label)}">
        <span class="ls-fav ls-fav-head" aria-hidden="true">${icon("star_border", "mi-sm")}</span>
        ${lsFlag(group.country)}
        <span class="ls-comp">${esc(lsCompLabel(group))}</span>
        <span class="ls-pin" title="Pinned competition">${icon("push_pin", "mi-sm")}</span>
        ${live ? `<span class="ls-live-pill">${live} live</span>` : ""}
        <span class="ls-count"></span>
        <a class="ls-standings" href="#/livescores" title="Standings"
           onclick="event.preventDefault(); event.stopPropagation()">Live Standings</a>
        <button class="ls-collapse" type="button"
                aria-expanded="${collapsed ? "false" : "true"}"
                aria-label="${collapsed ? "Expand" : "Collapse"} ${esc(group.competition)}"
                data-ls-collapse="${esc(group.competition_label)}">
          ${icon(collapsed ? "expand_more" : "expand_less", "mi-sm")}
        </button>
      </header>
      <div class="ls-rows">${group.items.map(livescoreRow).join("")}</div>
    </section>`;
}

//: Country name -> ISO 3166 alpha-2, for the flag glyph only. Deliberately a
//: small explicit table rather than a fuzzy lookup: a wrong flag is worse than
//: no flag, and the board only ever shows the countries the feed returns.
const LS_FLAGS = {
  england: "gb-eng", scotland: "gb-sct", wales: "gb-wls",
  "northern ireland": "gb-nir", ireland: "ie", spain: "es", italy: "it",
  germany: "de", france: "fr", portugal: "pt", netherlands: "nl",
  belgium: "be", turkey: "tr", greece: "gr", russia: "ru", ukraine: "ua",
  poland: "pl", romania: "ro", bulgaria: "bg", croatia: "hr", serbia: "rs",
  slovenia: "si", slovakia: "sk", austria: "at", switzerland: "ch",
  denmark: "dk", norway: "no", sweden: "se", finland: "fi", iceland: "is",
  brazil: "br", argentina: "ar", mexico: "mx", usa: "us",
  "united states": "us", canada: "ca", chile: "cl", colombia: "co",
  peru: "pe", uruguay: "uy", paraguay: "py", bolivia: "bo", ecuador: "ec",
  venezuela: "ve", japan: "jp", "south korea": "kr", china: "cn",
  australia: "au", "new zealand": "nz", india: "in", iran: "ir",
  "saudi arabia": "sa", qatar: "qa", "united arab emirates": "ae",
  egypt: "eg", morocco: "ma", tunisia: "tn", algeria: "dz", nigeria: "ng",
  ghana: "gh", kenya: "ke", "south africa": "za", israel: "il",
  "czech republic": "cz", czechia: "cz", hungary: "hu", cyprus: "cy",
  malta: "mt", estonia: "ee", latvia: "lv", lithuania: "lt", belarus: "by",
  kazakhstan: "kz", azerbaijan: "az", armenia: "am", georgia: "ge",
  moldova: "md", albania: "al", "north macedonia": "mk",
  "bosnia and herzegovina": "ba", montenegro: "me", kosovo: "xk",
};

//: A flag as inline SVG. Rendered locally from the ISO code rather than fetched
//: from a flag CDN: a flag is decoration, and a decoration that needs the network
//: is a decoration that shows a broken image on a slow connection.
//:
//: The glyph is a small striped block, not a photorealistic flag. It is a hint
//: about which competition you are looking at, at 16px, next to the country name
//: spelled out in full -- so it never has to carry the meaning alone.
function lsFlag(country) {
  const name = (country || "").trim().toLowerCase();
  const code = LS_FLAGS[name];
  if (!code) return `<span class="ls-flag ls-flag-none" aria-hidden="true"></span>`;
  // The three stripes are derived from the code so the same country always gets
  // the same mark; it is a stable identity badge, not a claim to be the flag.
  let h = 0;
  for (let i = 0; i < code.length; i++) h = (h * 31 + code.charCodeAt(i)) >>> 0;
  const c1 = `hsl(${h % 360} 62% 46%)`;
  const c2 = `hsl(${(h * 7) % 360} 58% 62%)`;
  return `<span class="ls-flag" aria-hidden="true" title="${esc(country)}">
    <i style="background:${c1}"></i><i style="background:#f2f2f2"></i><i style="background:${c2}"></i>
 </span>`;
}

// "COUNTRY: League" -- the reference board's own header convention. Falls back
// to the bare competition name when the country is unknown, rather than
// printing a dangling colon.
function lsCompLabel(group) {
  const country = (group.country || "").trim();
  const comp = (group.competition || "").trim();
  if (!country || country.toLowerCase() === "international") return comp;
  return `${country}: ${comp}`;
}

// The day picker: explicit Today/Tomorrow jumps, a prev/next pair, a native date
// input bounded to the server's own season window, and a scrollable strip of
// quick dates for the fortnight ahead. The input is a plain `<input type="date">`
// rather than a custom calendar because the browser already ships a correct,
// keyboard-accessible, localised one -- and it is bounded by the same min/max the
// API enforces, so the UI cannot ask for a day the server would reject.
function livescoreDayNav() {
  const bounds = lsWindowBounds();
  const today = lsCurrentDay();
  const quick = lsQuickDates();
  const isoToday = toIsoDate(new Date());
  const isToday = today === isoToday;
  const isTomorrow = today === toIsoDate(new Date(Date.now() + 864e5));
  return `
    <div class="ls-daynav">
      <button class="ls-nav-btn" type="button" data-ls-shift="-1"
              title="Previous day">${icon("chevron_left", "mi-sm")}</button>
      <button class="ls-nav-btn" type="button" data-ls-shift="1"
              title="Next day">${icon("chevron_right", "mi-sm")}</button>
      <input class="ls-date-input" type="date" id="ls-date" value="${esc(today)}"
             min="${esc(bounds.earliest)}" max="${esc(bounds.latest)}"
             aria-label="Pick a match day" />
      <button class="chip ${isToday ? "active" : ""}" type="button"
              data-ls-today="1">Today</button>
      <button class="chip ${isTomorrow ? "active" : ""}" type="button"
              data-ls-day="${esc(toIsoDate(new Date(Date.now() + 864e5)))}">Tomorrow</button>
    </div>
    <div class="chip-row ls-quick">
      ${quick
        .map(
          (iso) => `<button class="chip ${today === iso ? "active" : ""}"
                            data-ls-day="${esc(iso)}">${esc(lsDateChipLabel(iso))}</button>`
        )
        .join("")}
    </div>`;
}

function livescoreInner() {
  const data = state.livescores;
  if (!data) return `<div class="loading"><div class="spinner"></div>Loading scores…</div>`;

  const allRows = (data.groups || []).flatMap((g) => g.items);
  // Search runs over the rows, then the surviving rows are re-bucketed into
  // their competitions -- so a group header never outl the matches in it.
  const matched = applySearch("livescores", allRows, rowSearchText);
  // The top-league narrowing runs on the same flat list, before the status
  // filter, so "Top leagues" + "Live" composes the way a reader expects:
  // both conditions, not one replacing the other.
  const byLeague = state.lsTopOnly
    ? matched.filter((r) => r.is_top)
    : matched;
  // The sidebar's league selection, matched on the row's own competition label.
  //
  // Label rather than id, because the feed keys a competition on its own id and
  // the same league can be stored twice under two of them ("spain-laliga" and
  // "spain-laliga-qvmll54o"). The sidebar already merges those into one row and
  // points it at whichever id has fixtures; matching the id here would then miss
  // the day's rows that were stored under the other one. The label is what the
  // two rows genuinely share, and it is what the reader clicked.
  const byComp = state.filters.competitionId
    ? byLeague.filter((r) => r.competition_label === state.filters.competitionLabel)
    : byLeague;
  // Status filter, then re-bucket, for the same reason: filtering a flat list
  // and grouping afterwards keeps the two facts consistent.
  const shownRows = byComp.filter(
    (r) => state.lsFilter === "all" || r.status === state.lsFilter
  );
  const groups = lsGroupRows(shownRows);
  const shown = groups;
  const searching = (state.searches.livescores || "").trim().length > 0;
  // Counts describe the CURRENT narrowing, so a tab's number is always how many
  // rows clicking it would show -- a count from the unfiltered set beside a
  // filtered board is a number that never matches what appears.
  const counts = {
    all: byComp.length,
    live: byComp.filter((r) => r.status === "live").length,
    finished: byComp.filter((r) => r.status === "finished").length,
    scheduled: byComp.filter((r) => r.status === "scheduled").length,
  };
  // How many rows the Top-leagues narrowing would show, from the set the league
  // filter already narrowed, so the two controls compose rather than each
  // reporting a count from a different population.
  const topCount = byComp.filter((r) => r.is_top).length;

  let body;
  if (data.source === "unavailable") {
    // The feed is unreachable. Say so plainly instead of rendering an empty
    // board that looks like a quiet football day.
    body = `<div class="empty"><div class="big">${icon("scoreboard")}</div>
      No scores available right now.<br>
      <span style="font-size:13px">The live feed could not be reached. Predictions and
      results are unaffected.</span></div>`;
  } else if (!shown.length) {
    // An empty day has two very different causes and they must not be conflated:
    // the source simply has no fixtures (a real, quiet day -- or a day older than
    // the archive reaches), versus the reader's own filter excluding everything.
    // Only the second offers "clear the filter" as a remedy.
    const leagueFilter = !!state.filters.competitionId;
    const filtering = state.lsFilter !== "all" || state.lsTopOnly || leagueFilter || searching;
    body = searching
      ? `<div class="empty"><div class="big">${icon("search_off")}</div>
         No matches match “${esc(state.searches.livescores)}”.<br>
         <span style="font-size:13px">Search covers both teams, the competition and the
         country. Clear the box to see the whole board.</span></div>`
      : leagueFilter && !shown.length && !state.lsTopOnly && state.lsFilter === "all"
        ? `<div class="empty"><div class="big">${icon("event_busy")}</div>
         No ${esc(state.filters.competitionLabel || "matches")} on
         ${esc(lsDateChipLabel(lsCurrentDay()))}.<br>
         <span style="font-size:13px">That league has no fixture on this date. Clear the
         filter from the sidebar to see the whole board.</span></div>`
      : filtering
        ? `<div class="empty"><div class="big">${icon("filter_alt_off")}</div>
         ${state.lsTopOnly
           ? `No top-league matches on ${esc(lsDateChipLabel(lsCurrentDay()))}.`
           : `No ${esc(LS_FILTER_LABEL[state.lsFilter].toLowerCase())} matches on
              ${esc(lsDateChipLabel(lsCurrentDay()))}.`}<br>
         <span style="font-size:13px">${state.lsTopOnly
           ? `The eight major European leagues have no fixture on this date. There are
              ${allRows.length} match(es) elsewhere on the board.`
           : `There are ${counts.all} match(es) on this day under other filters.`}</span></div>`
        : `<div class="empty"><div class="big">${icon("event_busy")}</div>
         No matches on ${esc(lsDateChipLabel(lsCurrentDay()))}.<br>
         <span style="font-size:13px">Nothing was scheduled on this date in the source.
         Try another day${data.window ? ` — the board covers
         ${esc(data.window.earliest)} to ${esc(data.window.latest)}` : ""}.</span></div>`;
  } else {
    body = `<div class="ls-board">${shown.map(livescoreGroup).join("")}</div>`;
  }

  return `
    <div class="page-head">
      <h1>Football Livescores</h1>
      <p>Matches, dates and kick-off times${data.window ? ` — the ${esc(data.window.season || "current")} season, ${esc(data.window.earliest)} to ${esc(data.window.latest)}` : ""}.
      ${data.source === "database"
        ? "Served from the synced schedule"
        : data.source === "live-feed-synced"
          ? "Pulled from the live feed and saved"
          : "Pulled straight from the live feed"}.
      No projections are mixed in, so every score here actually happened.</p>
    </div>

    <div class="ls-toolbar">
      <div class="ls-tabs" id="ls-tabs">
        ${LS_FILTERS.map(
          (f) => `<button class="ls-tab ${state.lsFilter === f ? "active" : ""}"
                          data-ls-filter="${f}" type="button">${LS_FILTER_LABEL[f]}
            ${counts[f] ? `<span class="ls-tab-n">${counts[f]}</span>` : ""}
          </button>`
        ).join("")}
        <button class="ls-tab ls-tab-top ${state.lsTopOnly ? "active" : ""}"
                id="ls-top-only" type="button"
                aria-pressed="${state.lsTopOnly ? "true" : "false"}"
                title="Narrow the board to the eight major European leagues — Premier League, LaLiga, Serie A, Bundesliga, Ligue 1, Eredivisie, Champions League, Europa League">
          ${icon("star", "mi-sm")}${LS_TOP_LABEL}
          ${topCount ? `<span class="ls-tab-n">${topCount}</span>` : ""}
        </button>
      </div>
      ${searchBox("livescores", "Search team or league…")}
      <button class="chip" id="ls-refresh" type="button" title="Reload the board">
        ${icon("autorenew", "mi-sm")}</button>
      <button class="ls-sound" id="ls-sound" type="button"
              title="Goal alerts are not available — this board has no audio">
        ${icon("volume_up", "mi-sm")}
      </button>
      <span class="ls-meta">
        <strong>${data.live_count || 0}</strong> in play
        ${data.live_count ? "· updating every 30s" : "· nothing in play"}
        ${data.truncated ? `· showing first ${data.total}` : ""}
      </span>
    </div>

    ${livescoreDayNav()}

    ${state.filters.competitionId
      ? `<div class="chip-row ls-league-active">
           <button class="chip active" id="ls-clear-league" type="button"
                   title="Clear the sidebar's league filter">
             ${esc(state.filters.competitionLabel || "League")}
             ${icon("close", "mi-sm")}
           </button>
         </div>`
      : ""}

    ${body}`;
}

// Load one day, with the board's own loading/error states. Shared by every
// control that changes the day (chips, prev/next, the date input) so they all
// fail the same way instead of three slightly different ways.
async function lsGoToDay(root, iso) {
  root.innerHTML = `<div class="loading"><div class="spinner"></div>Loading scores…</div>`;
  try {
    await loadLivescores(iso);
  } catch (e) {
    root.innerHTML = `<div class="error-box">Could not load scores: ${esc(e.message)}</div>`;
    return false;
  }
  // A different day is a different board: a match expanded on the previous day
  // must not stay "open" against an unrelated row on the new one.
  state.lsOpenMatch = null;
  renderLivescores();
  return true;
}

function renderLivescores(opts = {}) {
  const root = document.getElementById("view");
  root.innerHTML = livescoreInner();

  // Day chips.
  root.querySelectorAll("[data-ls-day]").forEach((el) =>
    el.addEventListener("click", () => lsGoToDay(root, el.dataset.lsDay))
  );

  // Prev / next day, clamped to the server's window.
  root.querySelectorAll("[data-ls-shift]").forEach((el) =>
    el.addEventListener("click", () =>
      lsGoToDay(root, lsShiftDay(Number(el.dataset.lsShift)))
    )
  );

  const todayBtn = root.querySelector("[data-ls-today]");
  if (todayBtn) todayBtn.addEventListener("click", () => lsGoToDay(root, "today"));

  // The native date input. `change` (not `input`) so a partially-typed date is
  // never requested; an empty value means the reader cleared the field, which is
  // not a day to load, so it is ignored rather than sent as a bad date.
  const dateInput = root.querySelector("#ls-date");
  if (dateInput) {
    dateInput.addEventListener("change", () => {
      if (dateInput.value) lsGoToDay(root, dateInput.value);
    });
    // Clicking the field should offer the whole capture, not one day at a time.
    dateInput.addEventListener("focus", () => {
      if (typeof dateInput.showPicker === "function") {
        try { dateInput.showPicker(); } catch { /* not user-activated; ignore */ }
      }
    });
  }

  root.querySelectorAll("[data-ls-filter]").forEach((el) =>
    el.addEventListener("click", () => {
      state.lsFilter = el.dataset.lsFilter;
      renderLivescores({ keepPoll: true });
    })
  );

  // The top-league narrowing. A toggle rather than a one-way filter, so the
  // reader who narrowed by mistake presses it again rather than hunting for a
  // separate "clear" control.
  const topBtn = root.querySelector("#ls-top-only");
  if (topBtn) {
    topBtn.addEventListener("click", () => {
      state.lsTopOnly = !state.lsTopOnly;
      renderLivescores({ keepPoll: true });
    });
  }

  // Expanding one match's details. A second press on the same row folds it away,
  // so the control is a toggle rather than a one-way door.
  root.querySelectorAll("[data-ls-open]").forEach((el) =>
    el.addEventListener("click", (ev) => {
      ev.stopPropagation();
      const id = el.dataset.lsOpen;
      state.lsOpenMatch = String(state.lsOpenMatch) === String(id) ? null : id;
      renderLivescores({ keepPoll: true });
    })
  );

  // The panel's own link into the model page. Uses openDetail(), the same entry
  // point every other tab uses, so the board inherits that route's poll-cancel
  // and caching rather than growing a second way to open a fixture.
  root.querySelectorAll("[data-ls-goto]").forEach((el) =>
    el.addEventListener("click", (ev) => {
      ev.stopPropagation();
      openDetail(el.dataset.lsGoto);
    })
  );

  // Clearing the sidebar's league filter from the board itself. The sidebar row
  // is the primary control, but a reader who filtered to a league with nothing
  // on the day is staring at an empty board -- the escape has to be next to the
  // emptiness, not only in the panel that caused it.
  const clearLeague = root.querySelector("#ls-clear-league");
  if (clearLeague) {
    clearLeague.addEventListener("click", () => {
      state.filters.competitionId = "";
      state.filters.competitionLabel = "";
      renderLivescores({ keepPoll: true });
      renderSidebar();
    });
  }

  // Folding a competition away is a view preference the 30s poll must respect,
  // so the collapsed set lives in state rather than in the DOM.
  root.querySelectorAll("[data-ls-collapse]").forEach((el) =>
    el.addEventListener("click", (ev) => {
      ev.stopPropagation();
      const key = el.dataset.lsCollapse;
      if (state.lsCollapsed.has(key)) state.lsCollapsed.delete(key);
      else state.lsCollapsed.add(key);
      renderLivescores({ keepPoll: true });
    })
  );

  const refresh = document.getElementById("ls-refresh");
  if (refresh)
    refresh.addEventListener("click", async () => {
      refresh.disabled = true;
      try {
        // Reload whichever day is on screen, resolved -- not the keyword the
        // reader may have arrived with, which a date-picker jump has since
        // replaced. reloading "today" after moving to last March would snap the
        // board back and look like a bug.
        await loadLivescores(lsCurrentDay());
      } catch { /* keep the board the reader is looking at */ }
      renderLivescores();
    });

  const sound = document.getElementById("ls-sound");
  if (sound)
    sound.addEventListener("click", () => {
      // There is no audio on this board, so this control does not toggle a real
      // mute -- it reports that honestly (via the title) rather than flicking a
      // state that would imply goal alerts are switchable.
      sound.classList.toggle("is-off");
    });

  // Clicking a row opens that match's own page. This is the behaviour the
  // board is for: a score on a list is an answer to "what is happening", and
  // the next question is always "in which minute, and how" -- which is the
  // match page, not an accordion.
  //
  // The row is addressed by the FEED's match id (data-match), because the match
  // page must open for a match with no stored fixture too. A row from the
  // database still carries that id in `external_id`, so both sources work.
  root.querySelectorAll(".ls-row").forEach((el) =>
    el.addEventListener("click", () => {
      const mid = el.dataset.match;
      if (mid) {
        stopLivescorePoll();
        state.view = "match";
        state.matchTab = "summary";
        rendered = { view: "match", arg: mid };
        location.hash = `#/match/${mid}`;
        renderMatch(mid);
        return;
      }
      // No match id (a feed row the DB has not stored): fall back to the
      // in-place detail panel, which is the only thing that can render without
      // one. Offering a page that cannot load would be worse than not offering
      // it.
      const id = el.dataset.fixture;
      if (id == null) return;
      state.lsOpenMatch = String(state.lsOpenMatch) === String(id) ? null : id;
      renderLivescores({ keepPoll: true });
    })
  );

  // A search re-render must not restart the poll -- that would reset its 30s
  // cadence on every keystroke.
  wireSearch(root, () => renderLivescores({ keepPoll: true }));

  if (!opts.keepPoll) startLivescorePoll();
}

// Poll only while there is something in play AND the user is still on the page.
// A timer that outlives the view would keep hammering the feed from every other
// page of the app.
function startLivescorePoll() {
  stopLivescorePoll();
  const data = state.livescores;
  if (!data || !(data.live_count > 0)) return;
  livescoreTimer = setInterval(async () => {
    if (state.view !== "livescores") { stopLivescorePoll(); return; }
    try {
      // Poll the resolved day, so the board stays on whichever date is actually
      // on screen rather than snapping back to the keyword it was opened with.
      const fresh = await api(`/livescores?date=${encodeURIComponent(lsCurrentDay())}`);
      // Re-render only when something actually changed, so the page does not
      // flicker under a reader who is mid-scroll. The comparison is on the rows
      // only -- not on `generated_at`, which changes every poll and would force
      // a pointless re-render each tick.
      if (JSON.stringify(fresh.groups) === JSON.stringify(state.livescores.groups)) return;
      state.livescores = fresh;
      renderLivescores({ keepPoll: true });
    } catch { /* a failed poll leaves the last good board on screen */ }
  }, LIVE_POLL_MS);
}

function stopLivescorePoll() {
  if (livescoreTimer) clearInterval(livescoreTimer);
  livescoreTimer = null;
}

// ---------------------------------------------------------------- match page
//
// The page a livescore row opens into. It is deliberately NOT the model's
// fixture page: that page is a Dixon-Coles matrix and a written preview, which
// is a document about a match that has mostly not happened yet. This one is the
// match itself -- the minute, the score, what has happened and the numbers
// behind it -- and it therefore works for a match the engine has no opinion
// about at all, which is exactly the case for a match already in play.
//
// The model page is still reachable, as an explicit "Full analysis &
// prediction" action, and that button is the only thing here that needs a
// stored fixture. Everything else comes from /api/matches/{id}.

//: The poll cadence while a match is in play. 20s rather than the board's 30s:
//: the board is showing a whole day, this is showing one clock, and a clock
//: that lags is the one thing the reader came here to watch.
//:
//: The *displayed* clock does not wait for this. It advances once a second on
//: its own (see `startClockTick`), because a second hand that moved in 20s
//: jumps would not be a second hand. This poll is for the facts the page cannot
//: derive -- the score, the events -- and 20s is right for those.
const MATCH_POLL_MS = 20000;

//: How often the header clock re-renders. One second, as a clock should.
const MATCH_TICK_MS = 1000;

//: Tab labels, in the reference board's own order. SUMMARY carries the events;
//: STATS the numbers; the other two are honest about what they do not have.
const MATCH_TABS = [
  ["summary", "Summary"],
  ["stats", "Stats"],
  ["lineups", "Lineups"],
  ["h2h", "H2H"],
];

let matchTimer = null;

//: The header clock's own one-second timer, and how many seconds it has counted
//: since the last poll. Separate from `matchTimer` because they run at very
//: different rates: the tick is local and free, the poll is a network request.
let clockTimer = null;
let clockTicks = 0;

function stopMatchPoll() {
  if (matchTimer) clearInterval(matchTimer);
  if (clockTimer) clearInterval(clockTimer);
  matchTimer = null;
  clockTimer = null;
}

// The header clock, as MM:SS counting from kick-off. Two sources, and the page
// says which, because they are not equally good:
//
//   * `source: "derived"` -- counted from the published kick-off. The honest
//     default: kick-off is an exact timestamp the feed reports, so this is
//     arithmetic on a fact rather than a guess, and it is right even between
//     incidents. Carries no `~` -- it is not an estimate of the feed's clock,
//     it *is* the clock, because the feed has none to estimate.
//   * `source: "feed"` -- reachable only with no usable kick-off, anchored on
//     the feed's last recorded incident. Marked, because that minute stalls
//     between events and can sit well behind the real one.
function matchClockCell(match) {
  const live = match.live || {};
  const clock = live.clock || {};
  // A match the clock has called over reads "Finished", not the clock.
  // "FT" is the board's own jargon, and the whole complaint this answers is a
  // finished match showing a minute -- so the word is spelled out here, exactly
  // as the livescore row spells it out.
  const over = clock.period === "ft" || clock.stale === true;
  const running = clock.running && !over;
  const title = over
    ? "Full time — the shared clock has called this match over"
    : clock.source === "derived"
      ? "Counted from the published kick-off, in real time — the feed publishes no live clock"
      : clock.source === "feed"
        ? `Anchored on the feed's last recorded incident at ${esc(clock.anchor_label || "")}`
        : "";
  const value = over ? "Finished" : (live.clock_label || "—");
  return `<span class="mp-clock ${running ? "is-live" : ""}" ${title ? `title="${title}"` : ""}>
      ${running ? `<span class="ls-pulse" aria-hidden="true"></span>` : ""}
      <span class="mp-minute" id="mp-clock-value">${esc(value)}</span>
      ${clock.period_label && running ? `<span class="mp-period">${esc(clock.period_label)}</span>` : ""}
    </span>`;
}

// The MM:SS string for a clock at a given moment.
//
// The server's `clock_label` is the truth and is used as-is on arrival; this
// exists so the displayed value can advance every second between polls without
// inventing anything. It advances only the fields the server sent -- the minute,
// the second within it and the stoppage count -- so the ticking number and the
// polled number are the same arithmetic, and a poll simply wins on the next tick
// (verified: the ticked value and the server's label agree to the second).
function matchClockLabel(clock, ticksSincePoll) {
  if (!clock || clock.minute === null || clock.minute === undefined) return "";
  if (clock.period === "ft") return "FT";
  if (clock.period === "ht") return "HT";
  if (!clock.running) {
    // Not in play: no second hand to move, so show exactly what was sent.
    const added = clock.added || 0;
    const base = added ? `${clock.minute}+${added}` : `${clock.minute}`;
    return `${base}:${String(clock.seconds || 0).padStart(2, "0")}`;
  }
  // Advance the pair the server sent. `clock.minute` is already the match-clock
  // minute and `clock.seconds` the second within it, so the only thing to do is
  // add the elapsed ticks -- one per second, carrying seconds into minutes.
  //
  // NOT `period_offset + seconds`: period_offset is the interval to remove from
  // real time, so adding it produced a label 15 minutes adrift from the server's
  // own (945s -> "15:45" while the server said "62:45") and the clock snapped
  // backwards on every poll. The two fields measure different things.
  const total = (clock.minute || 0) * 60 + (clock.seconds || 0) + ticksSincePoll;
  const played = Math.floor(total / 60);
  const secs = total % 60;
  const added = clock.added || 0;
  // Past 90 the clock holds at 90 and counts added minutes -- the same shape the
  // server sends, so the two never disagree about where regulation ended.
  //
  // The count is capped at the same 15-minute ceiling the server uses
  // (MAX_STOPPAGE_MINUTES in flashscore.py). Without the cap, a page left open
  // on a match whose poll stopped arriving ticked on past every realistic
  // stoppage and printed an impossible "90+15'": a match that is over must read
  // full time, never a fantasy minute. Once the cap is reached the match is
  // over, so the label becomes FT -- the same word the board and the finished
  // branch of this page use.
  const MAX_ADDED = 15;
  if (added > 0 || played > 90) {
    const over = added > 0 ? added : played - 90;
    if (over >= MAX_ADDED) return "FT";
    return `${90}+${over}:${String(secs).padStart(2, "0")}`;
  }
  return `${played}:${String(secs).padStart(2, "0")}`;
}

//: One timeline row. The kind decides the glyph and the colour, so a goal reads
//: as a goal at a glance rather than requiring the text to be parsed.
const MATCH_EVENT_ICON = {
  goal: "sports_soccer",
  "own-goal": "sports_soccer",
  penalty: "sports_soccer",
  miss: "cancel",
  yellow: "flag",
  red: "flag",
  sub: "swap_horiz",
  assist: "person",
  var: "info",
  note: "info",
};

//: Kinds that mean the score moved. Used to tell the reader when the feed's
//: incident list does not account for the scoreline it also published -- a real
//: and common case, because the summary feed carries only a subset of incidents.
const MATCH_GOAL_KINDS = new Set(["goal", "own-goal", "penalty"]);

// One timeline row. The two sides sit on opposite edges of a centre line: a
// home incident is left-aligned with its minute at the outer edge, an away one
// is mirrored. That is what makes a half of football readable at a glance --
// you can see which side was pressing without reading a single name.
function matchEventRow(ev, match) {
  const side = ev.team === "home" ? esc(match.home_team?.name || "Home")
    : ev.team === "away" ? esc(match.away_team?.name || "Away") : "";
  const glyph = MATCH_EVENT_ICON[ev.kind] || "info";

  // A substitution names two players and one is the story: the player coming
  // ON. Both are shown, the incoming player first, which is the order the
  // reference page reads in.
  const isSub = ev.kind === "sub" && ev.player_out;
  const headline = esc(ev.player || ev.type || "Event");

  // The running score, published per goal. This is what turns "Goal 63'" into
  // "63' Goal 1:0" -- the reader can see the scoreline build.
  const score = Array.isArray(ev.score)
    ? `<span class="mp-ev-score">${ev.score[0]}:${ev.score[1]}</span>`
    : "";

  // The reason a card was shown, and the shirt number. Both are published and
  // both are what the reference lists under the player's name.
  const bits = [ev.type, ev.shirt != null ? `#${ev.shirt}` : null, ev.reason]
    .filter(Boolean)
    .join(" · ");

  // The minute, the glyph and the text are one group; the group is placed on
  // the home or the away half of the row by CSS, so the markup stays identical
  // for both and only the side class differs.
  const content = `
      <span class="mp-ev-minute">${esc(ev.minute_label || "")}</span>
      <span class="mp-ev-icon" title="${esc(ev.type || "")}">${icon(glyph, "mi-sm mi-fill")}</span>
      <span class="mp-ev-body">
        <span class="mp-ev-line">
          <strong>${headline}</strong>
          ${score}
          ${side ? `<span class="mp-ev-team">${side}</span>` : ""}
        </span>
        ${isSub
          ? `<span class="mp-ev-sub">
               <span class="mp-ev-out">${icon("arrow_downward", "mi-sm")}${esc(ev.player_out)}</span>
             </span>`
          : ""}
        ${bits ? `<span class="mp-ev-type">${esc(bits)}</span>` : ""}
        ${ev.text ? `<span class="mp-ev-text">${esc(ev.text)}</span>` : ""}
      </span>`;

  return `
    <li class="mp-event mp-ev-${esc(ev.kind)} ${ev.team === "away" ? "is-away" : "is-home"}">
      ${content}
    </li>`;
}

// The match-information block: the rows the reference page lists under its own
// "Match information" heading. Every one is published by the feed, so this is a
// transcription rather than a computation -- and a row the feed did not send is
// omitted rather than rendered empty.
function matchInfoPanel(match) {
  const info = match.info || {};
  const rows = [
    ["round", "Round"],
    ["venue", "Venue"],
    ["city", "City"],
    ["referee", "Referee"],
    ["attendance", "Attendance"],
  ].filter(([key]) => info[key]);
  if (!rows.length) return "";
  return `
    <div class="card mp-info">
      <h3>Match information</h3>
      <dl class="mp-info-grid">
        ${rows.map(([key, label]) => `
         <div><dt>${esc(label)}</dt><dd>${esc(info[key])}</dd></div>`).join("")}
      </dl>
    </div>`;
}

function matchSummaryPanel(match) {
  const events = match.events || [];
  const info = matchInfoPanel(match);
  if (!events.length) {
    return `${info}
      <div class="card"><h3>Match timeline</h3>
      <p class="mp-empty">${match.live && match.live.available
        ? "The feed has no incidents for this match yet. Goals, cards and substitutions appear here as they happen."
        : "The live feed could not be reached, so there are no incidents to show. The score and kick-off time above come from the stored schedule."}</p></div>`;
  }
  // Newest first: on a live page the reader wants "what just happened", and the
  // most recent event is the one that moved the score they are looking at.
  const ordered = [...events].reverse();

  // The feed publishes a *subset* of incidents, so the scoreline can legitimately
  // be higher than the goals listed below. Rather than quietly showing a 1-0 with
  // no goal in it -- which reads as a bug in this page -- the gap is named. The
  // alternative, inventing a goal to match the score, would be worse: the score
  // is the published fact and the timeline is what the feed actually reported.
  const total = match.status === "scheduled" ? 0 : (match.home_goals || 0) + (match.away_goals || 0);
  const listed = events.filter((e) => MATCH_GOAL_KINDS.has(e.kind)).length;
  const gap = total > listed
    ? `<p class="mp-empty mp-gap">${icon("info", "mi-sm")}
        The feed reports ${listed} of the ${total} goal${total === 1 ? "" : "s"} in this
        match — its incident list is abridged, so the scoreline above is complete
        while the timeline may not be.</p>`
    : "";

  return `
    ${info}
    <div class="card">
      <h3>Match timeline <span class="mp-count">${events.length} event${events.length === 1 ? "" : "s"}</span></h3>
      ${matchTeamHeading(match)}
      <ol class="mp-events">${ordered.map((e) => matchEventRow(e, match)).join("")}</ol>
      ${gap}
    </div>`;
}

// ---------------------------------------------------------------- lineups
// Two lists per side: the starting XI with its formation, and the bench. The
// formation is printed as the feed gives it ("1-4-2-3-1") rather than turned
// into a pitch diagram, because a diagram drawn from a guessed layout would
// claim more than the data does.
function matchTeamLineup(team, group, side) {
  const players = group?.starting || [];
  const subs = group?.substitutes || [];
  if (!players.length && !subs.length) return "";
  return `
    <div class="card mp-lineup">
      <div class="mp-lineup-head">
        ${side === "away" ? crest(team?.logo) : ""}
        <span class="mp-lineup-name">${esc(team?.name || "")}</span>
        ${group?.formation ? `<span class="mp-formation">${esc(group.formation)}</span>` : ""}
        ${side === "home" ? crest(team?.logo) : ""}
      </div>
      ${group?.coach ? `<div class="mp-coach">Coach: <strong>${esc(group.coach)}</strong></div>` : ""}
      <div class="mp-lineup-cols">
        <div class="mp-lineup-col">
          <div class="mp-lineup-section">Starting lineup</div>
          <ul class="mp-players">${players.map(matchPlayerRow).join("")}</ul>
        </div>
        ${subs.length
          ? `<div class="mp-lineup-col">
               <div class="mp-lineup-section">Substitutes</div>
               <ul class="mp-players">${subs.map(matchPlayerRow).join("")}</ul>
             </div>`
          : ""}
      </div>
    </div>`;
}

// ------------------------------------------------------------- the pitch
// ONE full pitch, both sides on it, drawn from the PUBLISHED formation rather
// than from measured coordinates. The feed was checked for real positions and
// has none: `LO` takes only 3 distinct values across an eleven-player side
// where a left-to-right axis needs one per player, and `LL` is a display order
// (1..11). So the shape comes from the formation string and the order within a
// line is the feed's own record order. Nothing here claims to be a measured x/y.
//
// The orientation matches the reference board: the pitch is landscape, the two
// sides attack EACH OTHER across the halfway line, and the two keepers stand at
// the outer edges (left and right), not top and bottom. Each side's lines are
// read keeper -> defence -> attack moving INWARD from its own goal, so the two
// attacks meet in the middle. That is the one arrangement a pitch diagram must
// get right, and stacking the halves vertically (the previous layout) put both
// keepers on the halfway line instead of on their own goal lines.
//
// The `mp-pitch-half-{home,away}` class is what flips the reading order: the
// away side's own half is mirrored, so the same markup renders one side either
// end of the single field.

// The reference colours a player's rating note by BAND rather than printing one
// flat amber, so a good game and a poor one do not look alike. The thresholds
// are the reference's own (measured off its live board): blue at 8.0+, dark green
// at 7.5, green at 7.0, amber at 6.5, orange below that. Kept as data here so
// the CSS does not have to re-encode the cutoffs as five sibling selectors.
const PITCH_RATING_BANDS = [
  [8.0, "elite"],
  [7.5, "great"],
  [7.0, "good"],
  [6.5, "ok"],
];

function pitchRatingBand(rating) {
  const n = parseFloat(rating);
  if (!Number.isFinite(n)) return "ok";
  const hit = PITCH_RATING_BANDS.find(([floor]) => n >= floor);
  return hit ? hit[1] : "poor";
}

// The formation as the reference prints it in the header strip: the OUTFIELD
// digits only. "1-4-3-2" on the pitch becomes "4-3-2" in the bar, because the
// leading 1 is the keeper, who is not a line of the outfield shape.
function pitchFormationOutfield(formation) {
  const parts = String(formation || "").split("-").filter(Boolean);
  if (parts.length <= 1) return formation || "";
  return parts.slice(1).join("-");
}

// A side's mean rating, or null when the feed sent none. Printed in the same
// banded badge as an individual player, per the reference.
function pitchAverageRating(group) {
  const rated = (group?.starting || [])
    .map((p) => parseFloat(p.rating))
    .filter((n) => Number.isFinite(n));
  if (!rated.length) return null;
  return (rated.reduce((a, b) => a + b, 0) / rated.length).toFixed(1);
}

function matchPitchLine(players, side, marks) {
  return `
    <div class="mp-pitch-line">
      ${players.map((p) => matchPitchPlayer(p, side, marks)).join("")}
    </div>`;
}

// The per-player incident marks the reference wears on a portrait: a ball for
// each goal, a ball for an own goal, an arrow for a player who came off, and a
// small green arrow for one who came on.
//
// Built from the SUMMARY feed's own incidents rather than from the lineups feed,
// because the lineups feed carries no events at all -- and keyed on the feed's
// `player_id` so two players sharing a surname are never confused. The name is
// only a fallback for the minority of incidents the feed sends without an id.
//
// Returns `{"<id>": {...}, "name:<lowercased name>": {...}}`; `playerMark` picks
// whichever key a lineup player has.
function pitchEventMarks(events) {
  const marks = new Map();
  const blank = () => ({ goals: 0, own: 0, assists: 0, subOn: false, subOff: false });
  const get = (key) => {
    if (!marks.has(key)) marks.set(key, blank());
    return marks.get(key);
  };
  // A player is filed under their feed id when the incident carries one, AND
  // under their name, so a lookup can match either. Both point at the same
  // object, so a bump through one key is visible through the other.
  const keysFor = (id, name) => {
    const nameKey = name ? `name:${String(name).trim().toLowerCase()}` : null;
    if (id != null) {
      const idKey = `id:${id}`;
      const obj = get(idKey);
      // The id is authoritative, so the name key is pointed AT that object
      // unconditionally. A name-only incident seen earlier may have created its
      // own object under the same name; the id wins so a player is never counted
      // twice from the two keys.
      if (nameKey) marks.set(nameKey, obj);
      return idKey;
    }
    return nameKey;
  };

  (events || []).forEach((ev) => {
    if (ev.kind === "sub") {
      // A substitution names two players: the one coming ON (the event's own
      // player) and the one going OFF (`player_out`). Both get a mark.
      const onKey = keysFor(ev.player_id, ev.player);
      if (onKey) get(onKey).subOn = true;
      const outKey = keysFor(ev.player_out_id, ev.player_out);
      if (outKey) get(outKey).subOff = true;
      return;
    }
    const key = keysFor(ev.player_id, ev.player);
    if (!key) return;
    if (ev.kind === "goal" || ev.kind === "penalty") get(key).goals += 1;
    else if (ev.kind === "own-goal") get(key).own += 1;
    else if (ev.kind === "assist") get(key).assists += 1;
  });
  return marks;
}

// The marks for one lineup player, or null when the feed reported nothing about
// them. Absence is null rather than a zeroed object so a player with no incident
// renders no badge at all.
function playerMark(p, marks) {
  if (!marks || !p) return null;
  const byId = p.player_id != null ? marks.get(`id:${p.player_id}`) : null;
  const byName = p.name ? marks.get(`name:${String(p.name).trim().toLowerCase()}`) : null;
  const combined = byId || byName;
  if (!combined) return null;
  if (!combined.goals && !combined.own && !combined.assists && !combined.subOn && !combined.subOff) {
    return null;
  }
  return combined;
}

// The little badges worn on the porting's bottom-right corner, mirroring the
// reference: balls for goals, a down arrow when the player was taken off, a up
// arrow when they came on. Kept minimal -- the names are still the point.
function pitchMarkBadges(mark) {
  if (!mark) return "";
  const bits = [];
  const goals = (mark.goals || 0) + (mark.own || 0);
  if (goals) {
    bits.push(
      `<span class="mp-pitch-mark mp-pitch-mark-goal" title="${mark.own ? "Own goal" : `${goals} goal${goals === 1 ? "" : "s"}`}">` +
        icon("sports_soccer", "mi-sm mi-fill") +
        (goals > 1 ? `<span class="mp-pitch-mark-n">${goals}</span>` : "") +
      `</span>`
    );
  }
  if (mark.assists) {
    bits.push(
      `<span class="mp-pitch-mark mp-pitch-mark-assist" title="${mark.assists} assist${mark.assists === 1 ? "" : "s"}">` +
        icon("sports_soccer", "mi-sm") +
        (mark.assists > 1 ? `<span class="mp-pitch-mark-n">${mark.assists}</span>` : "") +
      `</span>`
    );
  }
  if (mark.subOff) {
    bits.push(`<span class="mp-pitch-mark mp-pitch-mark-off" title="Substituted off">${icon("arrow_downward", "mi-sm")}</span>`);
  } else if (mark.subOn) {
    bits.push(`<span class="mp-pitch-mark mp-pitch-mark-on" title="Substituted on">${icon("arrow_upward", "mi-sm")}</span>`);
  }
  return bits.length ? `<span class="mp-pitch-marks">${bits.join("")}</span>` : "";
}

function matchPitchPlayer(p, side, marks) {
  // The banded rating note, worn on the portrait's top corner as the reference
  // wears it. Omitted entirely when the feed sent no rating: a blank badge would
  // read as a zero rather than as a missing value.
  const rating = p.rating
    ? `<span class="mp-pitch-rating mp-pitch-rating-${pitchRatingBand(p.rating)}" title="Feed player rating">${esc(p.rating)}</span>`
    : "";
  const armband = p.role === "captain" ? `<span class="mp-pitch-armband">C</span>` : "";
  const shirt = p.shirt != null
    ? `<span class="mp-pitch-shirt">${esc(String(p.shirt))}</span>`
    : "";
  const name = esc(p.shirt_name || p.short_name || p.name);
  // What the feed reported about this player in THIS match: goals, assists and
  // whether they were involved in a substitution. Absent when the feed said
  // nothing, so no player wears an empty badge.
  const mark = playerMark(p, marks);
  return `
    <div class="mp-pitch-player" title="${esc(p.name)}${p.shirt != null ? ` (${p.shirt})` : ""}">
      <span class="mp-pitch-photo">
        ${rating}
        ${matchPlayerPhoto(p)}
        ${armband}
        ${pitchMarkBadges(mark)}
      </span>
      <span class="mp-pitch-plate">
        ${shirt}
        <span class="mp-pitch-name">${name}</span>
      </span>
    </div>`;
}

// One side's half of the shared pitch. `side` decides the reading order:
//   home -- keeper nearest the LEFT edge, lines running right toward halfway
//   away -- keeper nearest the RIGHT edge, lines running left toward halfway
// Both cases pass their lines keeper-first to `matchPitchLine`; it is the flex
// direction on the half (set in CSS) that places the keeper at the correct end.
function matchPitchHalf(group, side, marks) {
  const pitch = group?.pitch;
  // No pitch means the formation was missing or did not describe the eleven the
  // feed sent -- a wrong shape is worse than none, so the caller shows the list.
  if (!pitch || !pitch.lines?.length) return "";
  const lines = [pitch.keeper ? [pitch.keeper] : [], ...pitch.lines].filter((l) => l.length);
  return `
    <div class="mp-pitch-half mp-pitch-half-${esc(side)}">
      ${lines.map((line) => matchPitchLine(line, side, marks)).join("")}
    </div>`;
}

// The whole figure: a formation strip across the top, the two halves side by side
// on one landscape field, and the side names along the bottom. The strip is the
// reference's own header -- home shape hard left, the word FORMATION centred,
// away shape hard right -- and it is where the formation is printed, so it is no
// longer repeated on the grass itself.
function matchPitch(match, lu) {
  // What the feed reported each player doing in THIS match, so the pitch can
  // wear the same scorer/substitution marks the reference does. Built once here
  // and threaded down to every card rather than re-scanned per player.
  const marks = pitchEventMarks(match?.events);
  const home = matchPitchHalf(lu?.home, "home", marks);
  const away = matchPitchHalf(lu?.away, "away", marks);
  if (!home || !away) return "";
  const homeFormation = esc(pitchFormationOutfield(lu?.home?.pitch?.formation));
  const awayFormation = esc(pitchFormationOutfield(lu?.away?.pitch?.formation));
  // The two mean ratings, worn as badges at either end of the strip's own row --
  // the reference prints them in the same banded badge as a player's note.
  const homeAvg = pitchAverageRating(lu?.home);
  const awayAvg = pitchAverageRating(lu?.away);
  const avg = (v) =>
    v == null
      ? ""
      : `<span class="mp-pitch-rating mp-pitch-rating-avg mp-pitch-rating-${pitchRatingBand(v)}" title="Average rating of the starting XI">${esc(v)}</span>`;
  // `derived` means the feed published no formation and the shape was inferred
  // from the order it listed the players in. The figure says so rather than
  // letting an inferred 4-4-2 pass as the published one -- a reader comparing
  // this page against the official teamsheet needs to know which it is looking
  // at. Only shown when at least one side is inferred.
  const derived = lu?.home?.pitch?.derived || lu?.away?.pitch?.derived;
  const note = derived
    ? `<span class="mp-pitch-derived" title="The feed published no formation for this match; the shape is inferred from the order the players were listed in, so treat it as approximate.">${icon("info", "mi-sm")}shape inferred</span>`
    : "";
  return `
    <div class="mp-pitch-card">
      <div class="mp-pitch-strip">
        <span class="mp-pitch-strip-side">
          <span class="mp-pitch-formation mp-pitch-formation-home">${homeFormation}</span>
          ${avg(homeAvg)}
        </span>
        <span class="mp-pitch-strip-label">
          ${icon("insights", "mi-sm")}<span>Formation</span>
          ${note}
        </span>
        <span class="mp-pitch-strip-side mp-pitch-strip-side-away">
          ${avg(awayAvg)}
          <span class="mp-pitch-formation mp-pitch-formation-away">${awayFormation}</span>
        </span>
      </div>
      <div class="mp-pitch">
        ${home}
        <span class="mp-pitch-mid"></span>
        ${away}
      </div>
      <div class="mp-pitch-legend">
        <span class="mp-pitch-legend-team">${crest(match.home_team?.logo)}<span>${esc(match.home_team?.name || "")}</span></span>
        <span class="mp-pitch-legend-team mp-pitch-legend-away"><span>${esc(match.away_team?.name || "")}</span>${crest(match.away_team?.logo)}</span>
      </div>
    </div>`;
}

// A player's initials, for the rows the feed gives no portrait for. Not every
// player has one -- and a missing image must not become a broken-image icon,
// which reads as a bug in the page rather than a gap in the data.
function initials(name) {
  const parts = String(name || "").replace(/\(.*?\)/g, "").trim().split(/\s+/).filter(Boolean);
  if (!parts.length) return "?";
  const first = parts[0][0] || "";
  const last = parts.length > 1 ? parts[parts.length - 1][0] || "" : "";
  return esc((first + last).toUpperCase());
}

// The portrait, or the initials fallback. `alt` carries the name so the row is
// still readable by a screen reader (and legible if images are blocked), while
// `loading="lazy"` keeps 44 images off the critical path for the tabs the
// reader has not opened. The 2x source is offered so a retina screen does not
// upscale the 72px row image into mush.
function matchPlayerPhoto(p) {
  if (!p.photo) {
    return `<span class="mp-photo mp-photo-fallback" aria-hidden="true">${initials(p.name)}</span>`;
  }
  const srcset = p.photo_hires ? ` srcset="${esc(p.photo_hires)} 2x"` : "";
  return `<img class="mp-photo" src="${esc(p.photo)}"${srcset}
               alt="" loading="lazy" decoding="async"
               onerror="this.classList.add('mp-photo-broken');this.removeAttribute('src')">`;
}

function matchPlayerRow(p) {
  // The badge is a SHORT code, not the whole word. "goalkeeper" spelled out is
  // 80px wide, and in a two-column lineup that is the entire free space -- it
  // squeezed the name beside it to a 15px sliver, so "Riley M." rendered as
  // "Ri". "GK" is the standard abbreviation on every published teamsheet and
  // costs the name almost nothing; the full word is kept in the title so the
  // meaning is still available on hover.
  const ROLE_LABEL = { goalkeeper: "GK", captain: "C" };
  const label = ROLE_LABEL[p.role] || p.role;
  const role = p.role
    ? `<span class="mp-role mp-role-${esc(p.role)}" title="${esc(p.role)}">${esc(label)}</span>`
    : "";
  return `
    <li class="mp-player">
      ${matchPlayerPhoto(p)}
      <span class="mp-shirt">${p.shirt != null ? esc(String(p.shirt)) : ""}</span>
      <span class="mp-player-name">${esc(p.name)}</span>
      ${role}
      ${p.rating ? `<span class="mp-rating" title="Feed player rating">${esc(p.rating)}</span>` : ""}
    </li>`;
}

function matchLineupsPanel(match) {
  const lu = match.lineups || {};
  if (!lu.available) {
    return matchUnavailablePanel(
      "Lineups",
      "The feed has not published lineups for this match. They usually appear about an hour before kick-off."
    );
  }
  // The pitch leads, because it is the thing a reader opens this tab for: who
  // is actually playing where. The lists below it carry the detail the pitch
  // cannot (full names, roles, ratings on the bench).
  const pitchBlock = matchPitch(match, lu);
  return `
    ${pitchBlock}
    <div class="mp-lineups">
      ${matchTeamLineup(match.home_team, lu.home, "home")}
      ${matchTeamLineup(match.away_team, lu.away, "away")}
    </div>`;
}

// One statistic, in three columns: the home value hard left, the market name
// centred, the away value hard right. The bar underneath runs the same way, so
// the two numbers, the bar and the two team headings above all read as one
// left-to-right comparison. A `-`-joined pair ("7 - 3") would make the reader
// work out which side is which on every single row.
function matchStatRow(row) {
  const fmt = (v) => `${esc(String(v))}${row.is_percent ? "%" : ""}`;
  return `
    <div class="mp-stat">
      <div class="mp-stat-head">
        <strong class="mp-stat-home-val">${fmt(row.home)}</strong>
        <span class="mp-stat-name">${esc(row.label)}</span>
        <strong class="mp-stat-away-val">${fmt(row.away)}</strong>
      </div>
      <div class="mp-stat-bar">
        <span class="mp-stat-home" style="width:${row.home_pct}%"></span>
        <span class="mp-stat-away" style="width:${row.away_pct}%"></span>
      </div>
    </div>`;
}

function matchStatsPanel(match) {
  const sections = match.stats || [];
  if (!sections.length) {
    return `<div class="card"><h3>Match statistics</h3>
      <p class="mp-empty">${match.live && match.live.available
        ? "No statistics have been published for this match yet. They appear once the match is under way."
        : "The live feed could not be reached, so the statistics are unavailable."}</p></div>`;
  }
  // The heading names both sides with their crests, and is repeated on every
  // section rather than shown once at the top: the statistic cards can be
  // scrolled independently, and a column of numbers with no header in view is
  // a column of numbers with no meaning.
  const heading = matchTeamHeading(match);
  return sections
    .map(
      (section) => `
      <div class="card" style="margin-bottom:14px">
        <h3>${esc(section.label)}</h3>
        ${heading}
        ${section.rows.map(matchStatRow).join("")}
      </div>`
    )
    .join("");
}

// The home team on the left, the away team on the right, each with its crest.
// Shared by the Stats and Summary panels so the two tabs agree about which side
// of the page a team lives on -- a reader who has learnt "home is left" on one
// must not have to relearn it on the other.
function matchTeamHeading(match) {
  return `
    <div class="mp-team-heading">
      <span class="mp-th-team mp-th-home">
        ${crest(match.home_team?.logo)}
        <span class="mp-th-name">${esc(match.home_team?.name || "Home")}</span>
      </span>
      <span class="mp-th-team mp-th-away">
        <span class="mp-th-name">${esc(match.away_team?.name || "Away")}</span>
        ${crest(match.away_team?.logo)}
      </span>
    </div>`;
}

// Lineups and H2H are genuinely not fetched by this page yet, and it says so
// rather than rendering an empty shell that looks like a loading failure. H2H
// has a real destination -- the model page's own H2H tab -- so that is offered
// as the next step instead of duplicating a fetch here.
function matchUnavailablePanel(title, body) {
  return `<div class="card"><h3>${esc(title)}</h3><p class="mp-empty">${body}</p></div>`;
}

function matchPanel(match, tab) {
  if (tab === "stats") return matchStatsPanel(match);
  if (tab === "lineups") return matchLineupsPanel(match);
  if (tab === "h2h") {
    return match.fixture_id
      ? matchUnavailablePanel(
          "Head to head",
          `Head-to-head and form are computed against the model. Open
           <button class="btn btn-sm" type="button" data-mp-goto="${esc(match.fixture_id)}">
           ${icon("insights", "mi-sm")}Full analysis &amp; prediction</button> to see them.`
        )
      : matchUnavailablePanel(
          "Head to head",
          "This match has no stored fixture, so the head-to-head history is not available."
        );
  }
  return matchSummaryPanel(match);
}

function matchHead(match) {
  const k = match.kickoff ? kickoffLabel(match.kickoff) : null;
  // A match is over when the stored status says so *or* when the shared clock
  // says so. The feed sometimes leaves a row 'live' long past full time, and the
  // clock is the authority on that: it reports `period: "ft"` (or `stale`) once
  // the match is really done. Reading only `status` here is how a finished match
  // kept a stoppage minute on screen while the board beside it said Finished.
  const clock = (match.live || {}).clock || {};
  const over = clock.period === "ft" || clock.stale === true;
  const finished = match.status === "finished" || over;
  const isLive = match.status === "live" && !finished;
  const comp = match.competition || {};
  const scoreShown = (match.status !== "scheduled" || over) && match.home_goals != null && match.away_goals != null;
  const homeWon = finished && match.home_goals > match.away_goals;
  const awayWon = finished && match.away_goals > match.home_goals;

  return `
    <div class="mp-head ${isLive ? "is-live" : ""} ${finished ? "is-done" : ""}">
      <div class="mp-head-top">
        <span class="mp-comp">${esc(comp.label || comp.name || "")}</span>
        <span class="mp-headmisc">
          ${k ? `${esc(k.date)} · ${esc(k.time)} <em>local</em>` : "Kick-off time unavailable"}
        </span>
      </div>

      <div class="mp-teams">
        <div class="mp-team ${homeWon ? "winner" : ""}">
          ${crest(match.home_team?.logo)}
          <span class="mp-team-name">${esc(match.home_team?.name || "Home")}</span>
        </div>

        <div class="mp-centre">
          ${scoreShown
            ? `<div class="mp-score">${match.home_goals} – ${match.away_goals}</div>`
            : `<div class="mp-score mp-score-none">vs</div>`}
          ${matchClockCell(match)}
          ${!isLive && match.status === "scheduled"
            ? `<div class="mp-status-note">${esc(k ? timeUntil(match.kickoff) : "Not started")}</div>`
            : ""}
          ${finished ? `<div class="mp-status-note">Full time</div>` : ""}
        </div>

        <div class="mp-team away ${awayWon ? "winner" : ""}">
          <span class="mp-team-name">${esc(match.away_team?.name || "Away")}</span>
          ${crest(match.away_team?.logo)}
        </div>
      </div>

      ${matchClockSourceNote(match)}
    </div>`;
}

// One sentence explaining where the number above came from. This is the whole
// contract of the page: a published minute is stated as published, and a
// reconstructed one is stated as reconstructed. The alternative -- showing both
// as "63'" with no distinction -- is a page that cannot be trusted about the
// one thing it exists to show.
function matchClockSourceNote(match) {
  const clock = (match.live || {}).clock || {};
  if (match.status !== "live") {
    return "";
  }
  if (clock.source === "derived") {
    return `<p class="mp-source-note">
      ${icon("timer", "mi-sm")}
      Counting from the published kick-off in real time, so the clock is continuous
      from 0:00. The feed publishes no live clock of its own — probing it directly
      found only a phase code, so this is arithmetic on an exact timestamp rather
      than a number anyone measures.
    </p>`;
  }
  if (clock.source === "feed") {
    return `<p class="mp-source-note mp-derived">
      ${icon("info", "mi-sm")}
      No usable kick-off for this match, so the clock is anchored on the feed's
      last recorded incident${clock.anchor_label ? ` (${esc(clock.anchor_label)})` : ""} and
      counts up from there. That minute only moves when something happens, so it
      can sit behind the real one — it is the weaker of the two sources.
    </p>`;
  }
  return `<p class="mp-source-note mp-derived">
    ${icon("info", "mi-sm")}
    The live feed could not be reached, so no minute is shown.
 </p>`;
}

function matchActions(match) {
  return `
    <div class="mp-actions">
      ${match.fixture_id
        ? `<button class="btn btn-primary" type="button" data-mp-goto="${esc(match.fixture_id)}">
             ${icon("insights", "mi-sm")} Full analysis &amp; prediction</button>`
        : `<span class="mp-nomodel" title="This match has no stored fixture in the model">
             ${icon("info", "mi-sm")} No model prediction for this match</span>`}
      ${match.flashscore_url
        ? `<a class="btn" href="${esc(match.flashscore_url)}" target="_blank"
              rel="noopener noreferrer">${icon("open_in_new", "mi-sm")} Check at source</a>`
        : ""}
      <a class="btn btn-ghost" href="#/livescores" id="mp-back">
        ${icon("arrow_back", "mi-sm")} Back to livescores</a>
    </div>`;
}

function matchPageInner(match) {
  const tab = state.matchTab || "summary";
  const events = (match.events || []).length;
  const stats = (match.stats || []).length;
  const lu = match.lineups || {};
  const lineupCount = (lu.home?.starting?.length || 0) + (lu.away?.starting?.length || 0);
  const counts = { summary: events, stats, lineups: lineupCount, h2h: 0 };
  return `
    ${matchHead(match)}
    ${matchActions(match)}

    <nav class="mtabs mp-tabs" id="match-page-tabs">
      ${MATCH_TABS.map(
        ([key, label]) => `<button class="mtab ${tab === key ? "active" : ""}"
          data-mp-tab="${key}" type="button">${esc(label)}${counts[key] ? ` <span class="mp-tab-n">${counts[key]}</span>` : ""}</button>`
      ).join("")}
    </nav>

    <div class="mp-panels">${matchPanel(match, tab)}</div>`;
}

async function renderMatch(matchId) {
  const root = document.getElementById("view");
  stopLivescorePoll();
  stopMatchPoll();
  root.innerHTML = `<div class="loading"><div class="spinner"></div>Loading match…</div>`;

  let match;
  try {
    match = await api(`/matches/${encodeURIComponent(matchId)}`);
  } catch (e) {
    root.innerHTML = `<div class="error-box">Could not load this match: ${esc(e.message)}
      <div style="margin-top:12px"><a class="btn btn-sm" href="#/livescores">Back to livescores</a></div>
    </div>`;
    return;
  }

  state.matchPage = match;
  root.innerHTML = matchPageInner(match);
  wireMatchPage(match);
  startMatchPoll(matchId);
}

function wireMatchPage(match) {
  const root = document.getElementById("view");

  root.querySelectorAll("[data-mp-tab]").forEach((el) =>
    el.addEventListener("click", () => {
      state.matchTab = el.dataset.mpTab;
      const m = state.matchPage;
      if (!m) return;
      root.innerHTML = matchPageInner(m);
      wireMatchPage(m);
    })
  );

  // The link into the model page, through the same entry point every other tab
  // uses -- so this page inherits that route's own loading and error handling
  // rather than growing a second way to open a fixture.
  root.querySelectorAll("[data-mp-goto]").forEach((el) =>
    el.addEventListener("click", (ev) => {
      ev.stopPropagation();
      stopMatchPoll();
      openDetail(el.dataset.mpGoto);
    })
  );
}

// Poll only while something is in play and the reader is still on this page. A
// timer that outlives the view would keep fetching from every other page -- and
// the clock here is the one thing worth keeping fresh, so it polls on its own
// smaller endpoint rather than re-pulling the whole statistics payload.
function matchInPlay(match) {
  // Whether the match is actually under way. The STORED status is not enough on
  // its own: the sync that flips a fixture from `scheduled` to `live` runs on
  // its own schedule, so a match that kicked off two minutes ago can still be
  // stored as scheduled while the feed already knows it is in play. Gating the
  // poll on the stored status alone is exactly what left the score frozen on the
  // match page until the next sync -- the page opened, showed the old scoreline,
  // and never asked again.
  if (!match) return false;
  if (match.status === "live") return true;
  // The feed's own signals, taken as they are. `clock.running` is the strongest
  // (the header clock is counting), and a published period means the match has
  // begun even if the minute is unavailable for this second.
  const live = match.live || {};
  if (live.available && (live.clock || {}).running) return true;
  return live.available === true && Boolean(live.period);
}

function startMatchPoll(matchId) {
  stopMatchPoll();
  const match = state.matchPage;
  if (!matchInPlay(match)) return;

  // The second hand. Purely local: it re-renders the header clock from the
  // fields already on screen, so the displayed MM:SS moves once a second
  // instead of in 20-second jumps -- and it makes no request, so a clock the
  // reader is watching does not cost one. `clockTicks` is reset by every poll
  // so the ticked value can never drift far from the server's own.
  clockTicks = 0;
  clockTimer = setInterval(() => {
    // Guard on the element actually being on screen rather than on `state.view`.
    // `state.view` is only set by navigate(); a deep link (#/match/<id>) goes
    // straight through routeFromHash -> renderMatch and never sets it, so a
    // view-based guard made this tick cancel itself on its first fire and the
    // clock stood still between polls. The DOM is the honest check: if the clock
    // is mounted, we are on the match page.
    const el = document.getElementById("mp-clock-value");
    if (!el || !state.matchPage) { stopMatchPoll(); return; }
    const clock = (state.matchPage.live || {}).clock;
    if (!clock || !clock.running) return;
    clockTicks += 1;
    // Re-render in place rather than rebuilding the page: a full innerHTML
    // swap every second would restart every CSS animation on it.
    el.textContent = matchClockLabel(clock, clockTicks);
  }, MATCH_TICK_MS);

  matchTimer = setInterval(async () => {
    const current = state.matchPage;
    // Same DOM-based guard as the tick, and for the same reason: a deep link
    // never sets `state.view`, so guarding on it would stop the poll from ever
    // running on exactly the page the clock matters most.
    if (!document.getElementById("mp-clock-value") || !current) {
      stopMatchPoll();
      return;
    }
    try {
      const fresh = await api(`/matches/${encodeURIComponent(matchId)}/live`);
      // The server's label is the authority, so the local count starts again
      // from it -- otherwise twenty ticks plus a stale anchor would compound.
      clockTicks = 0;
      // Re-render only when something a reader would notice actually changed:
      // the minute, the score, or the number of events. Comparing the whole
      // payload would re-render on `generated_at` every tick and flicker.
      const before = current.live || {};
      const changed =
        fresh.clock_label !== before.clock_label ||
        fresh.home_goals !== current.home_goals ||
        fresh.away_goals !== current.away_goals ||
        fresh.event_count !== (current.events || []).length;
      if (!changed) return;

      // A new event means the timeline is stale too, so the whole match is
      // refetched; a minute ticking over only needs the header patched.
      if (fresh.event_count !== (current.events || []).length) {
        const full = await api(`/matches/${encodeURIComponent(matchId)}`);
        state.matchPage = full;
        const root = document.getElementById("view");
        root.innerHTML = matchPageInner(full);
        wireMatchPage(full);
        return;
      }

      state.matchPage = {
        ...current,
        status: fresh.status || current.status,
        home_goals: fresh.home_goals,
        away_goals: fresh.away_goals,
        live: { ...(current.live || {}), clock: fresh.clock, clock_label: fresh.clock_label },
      };
      const root = document.getElementById("view");
      root.innerHTML = matchPageInner(state.matchPage);
      wireMatchPage(state.matchPage);
    } catch { /* a failed poll leaves the last good state on screen */ }
  }, MATCH_POLL_MS);
}

// ---------------------------------------------------------------- stats
async function renderStats() {
  const root = document.getElementById("view");
  root.innerHTML = `<div class="loading"><div class="spinner"></div>Grading past fixtures…</div>`;
  let s, record;
  try {
    s = await api("/stats/overview");
    record = s.record;
  } catch (e) {
    root.innerHTML = `<div class="error-box">Could not load stats: ${esc(e.message)}</div>`;
    return;
  }
  state.stats = s;
  renderStatsView();
}

// The presentational half, split out so the search box can filter the market
// breakdown without re-running the (slow) backtest behind this page.
function renderStatsView() {
  const root = document.getElementById("view");
  const s = state.stats;
  const record = s.record;
  const acc = s.model_accuracy == null ? null : s.model_accuracy;

  // The only per-row list on this page is the market breakdown, so that is what
  // the search filters. The aggregate cards above it are totals and are left
  // alone -- filtering a total would make it a lie.
  const markets = applySearch("stats", record.by_market || [], (m) => [m.market]);
  const searching = (state.searches.stats || "").trim().length > 0;

  root.innerHTML = `
    <div class="page-head">
      <h1>Model Performance</h1>
      <p>Measured, not claimed. The accuracy figure below is a live backtest: the model is re-run
      on recent finished fixtures and scored against the actual result.</p>
    </div>

    <div class="grid grid-4">
      <div class="stat-card">
        <div class="stat-label">1X2 accuracy (backtest)</div>
        <div class="stat-value accent">${acc == null ? "—" : esc(pct(acc))}</div>
        <div class="stat-sub">vs 33% coin-flip baseline</div>
      </div>
      <div class="stat-card">
        <div class="stat-label">Fixtures modelled</div>
        <div class="stat-value">${esc(String(s.fixtures_total))}</div>
        <div class="stat-sub">${esc(String(s.fixtures_finished))} settled · ${esc(String(s.fixtures_upcoming))} upcoming</div>
      </div>
      <div class="stat-card">
        <div class="stat-label">Avg. confidence</div>
        <div class="stat-value">${num(s.avg_confidence, 1)}</div>
        <div class="stat-sub">across upcoming fixtures</div>
      </div>
      <div class="stat-card">
        <div class="stat-label">Value bets live</div>
        <div class="stat-value accent">${esc(String(s.value_bets))}</div>
        <div class="stat-sub">positive edge selections</div>
      </div>
    </div>

    <div class="grid grid-3" style="margin-top:16px">
      <div class="stat-card">
        <div class="stat-label">Competitions</div>
        <div class="stat-value">${esc(String(s.competitions))}</div>
      </div>
      <div class="stat-card">
        <div class="stat-label">Teams rated</div>
        <div class="stat-value">${esc(String(s.teams))}</div>
      </div>
      <div class="stat-card">
        <div class="stat-label">Tracked tips</div>
        <div class="stat-value">${esc(String(record.total_tips))}</div>
        <div class="stat-sub">${esc(String(record.pending))} pending</div>
      </div>
    </div>

    <div class="section-title">
      <h2>Published tip record</h2>
      <span style="font-size:12.5px;color:var(--text-dim)">Flat 1-unit stakes</span>
    </div>

    <!-- The search box sits here, outside the record conditionals, so this tab
         has one whether or not anything has settled yet. Buried inside
         "settled and by_market" it disappeared on a fresh database -- a tab
         with no search bar, which is exactly what this feature removes. -->
    <div class="ls-toolbar">
      ${searchBox("stats", "Search market…")}
      <span class="ls-meta">${markets.length} market${markets.length === 1 ? "" : "s"}${searching ? " matched" : ""}</span>
    </div>

    ${
      record.settled
        ? `<div class="grid grid-4">
             <div class="stat-card"><div class="stat-label">Strike rate</div>
               <div class="stat-value accent">${esc(pct(record.strike_rate))}</div>
               <div class="stat-sub">${record.won} won / ${record.lost} lost</div></div>
             <div class="stat-card"><div class="stat-label">ROI</div>
               <div class="stat-value ${record.roi >= 0 ? "accent" : ""}">${esc(signed(record.roi))}</div>
               <div class="stat-sub">on ${num(record.staked)} units staked</div></div>
             <div class="stat-card"><div class="stat-label">Profit</div>
               <div class="stat-value ${record.profit >= 0 ? "accent" : ""}">${esc(money(record.profit))}</div></div>
             <div class="stat-card"><div class="stat-label">Avg. odds</div>
               <div class="stat-value">${num(record.avg_odds)}</div></div>
           </div>
           ${
             record.by_market.length
               ? `<div class="card" style="margin-top:16px">
                    <h3 style="margin-top:0">Breakdown by market</h3>
                    <table class="data">
                      <thead><tr><th>Market</th><th class="num">Tips</th><th class="num">Won</th>
                        <th class="num">Strike rate</th><th class="num">ROI</th><th class="num">Profit</th></tr></thead>
                      <tbody>
                        ${markets
                          .map(
                            (m) => `<tr>
                              <td style="text-transform:uppercase;font-weight:700">${esc(m.market)}</td>
                              <td class="num">${m.tips}</td>
                              <td class="num">${m.won}</td>
                              <td class="num">${esc(pct(m.strike_rate))}</td>
                              <td class="num ${m.roi >= 0 ? "pos" : "neg"}">${esc(signed(m.roi))}</td>
                              <td class="num ${m.profit >= 0 ? "pos" : "neg"}">${esc(money(m.profit))}</td>
                            </tr>`
                          )
                          .join("")}
                      </tbody>
                    </table>
                  </div>`
               : ""
           }`
        : `<div class="empty"><div class="big">📊</div>No settled tips yet.
             <span style="display:block;font-size:13px;margin-top:6px">
             Open any fixture and hit “Track this tip” to start building the public record.</span></div>`
    }

    <div class="card" style="margin-top:20px">
      <h3>How the model works</h3>
      <ol style="margin:0;padding-left:20px;color:var(--text-dim);font-size:13.5px;line-height:1.85">
        <li><strong style="color:var(--text)">Ratings.</strong> Each team gets an attack and defence
        multiplier from its recent results, recency-weighted (a match 10 games ago counts ~2.7×
        less) and shrunk toward the league mean so small samples can't dominate.</li>
        <li><strong style="color:var(--text)">Fixture lambda.</strong> Ratings combine with league
        average goals and a home-advantage factor to give expected goals for each side.</li>
        <li><strong style="color:var(--text)">Dixon-Coles matrix.</strong> A Poisson scoreline grid
        up to 9-9, with the τ correction for 0-0, 1-0, 0-1 and 1-1 — plain Poisson misprices those.</li>
        <li><strong style="color:var(--text)">Markets.</strong> 1X2, Over/Under 2.5 and BTTS are read
        straight off the matrix, plus the top correct-score lines.</li>
        <li><strong style="color:var(--text)">Market blending.</strong> Model probabilities are blended
        28/72 with de-vigged bookmaker prices, because the closing line contains information the
        results history does not.</li>
        <li><strong style="color:var(--text)">Value detection.</strong> Edge = blended probability
        minus implied probability. Positive edge plus adequate odds flags a value selection, sized
        with quarter-Kelly.</li>
      </ol>
    </div>
  `;

  wireSearch(root, renderStatsView);
}

// ---------------------------------------------------------------- detail

async function openDetail(id) {
  location.hash = `#/fixture/${id}`;
  const root = document.getElementById("view");
  root.innerHTML = `<div class="loading"><div class="spinner"></div>Generating prediction…</div>`;

  let fx, ai = null;
  try {
    fx = await api(`/fixtures/${id}`);
  } catch (e) {
    root.innerHTML = `<div class="error-box">Could not load fixture: ${esc(e.message)}</div>`;
    return;
  }
  const p = fx.prediction;
  const k = kickoffLabel(fx.kickoff);

  // AI analysis is a second call (may hit an LLM), so render the model first.
  root.innerHTML = detailSkeleton(fx, p, k);
  wireDetail(fx, p);

  // The SportyBet board is display-only and entirely optional: it never feeds
  // the model, so it loads independently and fails silently.
  loadSportyBetPanel(id);

  // Real results history + stats for the two clubs (Form / H2H tabs). Also
  // independent: a feed outage leaves the panel saying so rather than breaking
  // the page, since the model's own output is already rendered above.
  loadFixtureHistory(id);

  try {
    ai = await api(`/fixtures/${id}/ai`);
    const panel = document.getElementById("ai-slot");
    if (panel) panel.innerHTML = aiPanel(fx, p, ai);
  } catch {
    const panel = document.getElementById("ai-slot");
    if (panel) panel.innerHTML = `<div class="card"><h3>AI analysis</h3>
      <p style="color:var(--text-dim);font-size:13.5px;margin:0">
      The written analysis is unavailable right now. The model's numbers above are unaffected.</p></div>`;
  }
}

function detailSkeleton(fx, p, k) {
  if (!p) return `<div class="error-box">No prediction available for this fixture.</div>`;

  const finished = fx.status === "finished";
  const scoreLine = finished
    ? `<div class="hero-score">${fx.home_goals} – ${fx.away_goals}</div>`
    : `<div class="hero-score">${num(p.expected_home_goals, 1)} – ${num(p.expected_away_goals, 1)}</div>`;

  return `
    <button class="btn btn-ghost btn-sm" id="back-btn" style="margin-bottom:14px">
      ${icon("arrow_back", "mi-sm")} Back to tips</button>

    <div class="detail-hero">
      <div style="display:flex;justify-content:space-between;gap:12px;flex-wrap:wrap;margin-bottom:16px">
        <span style="font-size:13px;color:var(--text-dim);font-weight:600">${esc(fx.competition.label)}</span>
        <span style="font-size:13px;color:var(--text-dim)">
          ${esc(k.date)} ${esc(k.time)} · ${esc(k.timeUntil ? timeUntil(fx.kickoff) : "")}
          ${finished ? "· <strong style='color:var(--accent)'>Finished</strong>" : ""}
        </span>
      </div>

      <div class="hero-teams">
        <div class="hero-team">
          ${fx.home_team.logo ? `<img class="crest" src="${esc(fx.home_team.logo)}" alt="">` : ""}
          <div><div class="name">${esc(fx.home_team.name)}</div>
            <div style="font-size:12px;color:var(--text-dim)">Home</div></div>
        </div>
        <div class="hero-vs">
          ${finished ? "Full time" : "Projected score"}
          ${scoreLine}
        </div>
        <div class="hero-team away">
          <div style="text-align:right"><div class="name">${esc(fx.away_team.name)}</div>
            <div style="font-size:12px;color:var(--text-dim)">Away</div></div>
          ${fx.away_team.logo ? `<img class="crest" src="${esc(fx.away_team.logo)}" alt="">` : ""}
        </div>
      </div>

      <div class="pick-banner">
        <div>
          <div class="label">Model's headline pick</div>
          <div class="value">${esc(p.best_selection)}</div>
          <div class="sub">${esc(p.engine)} · ${esc(p.model_version)}</div>
        </div>
        <div class="pick-stats">
          <div class="pick-stat"><div class="k">Model prob.</div><div class="v">${esc(pct(p.best_probability))}</div></div>
          <div class="pick-stat"><div class="k">${p.best_odds ? "Best odds" : "Fair odds"}</div>
            <div class="v">${num(p.best_odds || p.fair_odds)}</div></div>
          <div class="pick-stat"><div class="k">Edge</div>
            <div class="v ${p.best_edge == null ? "" : p.best_edge >= 0 ? "pos" : "neg"}">${esc(signed(p.best_edge))}</div></div>
          <div class="pick-stat"><div class="k">Confidence</div><div class="v">${p.confidence}</div></div>
          <div class="pick-stat"><div class="k">Rating</div><div class="v">${stars(p.value_rating)}</div></div>
        </div>
      </div>

      <div style="margin-top:14px">
        <div style="display:flex;justify-content:space-between;font-size:12px;color:var(--text-dim);margin-bottom:4px">
          <span>Confidence: <strong style="color:var(--text)">${esc(p.confidence_label)}</strong></span>
          <span>Model vs market agreement</span>
        </div>
        <div class="conf-bar"><div class="conf-fill ${confFill(p.confidence)}" style="width:${p.confidence}%"></div></div>
      </div>
    </div>

    <!-- Tab bar. "Prediction" is the model's own view (already rendered);
         "Stats" and "Form" come from the real results feed. -->
    <nav class="mtabs" id="match-tabs">
      <button class="mtab active" data-tab="prediction">Prediction</button>
      <button class="mtab" data-tab="stats">Stats</button>
      <button class="mtab" data-tab="form">Form</button>
      <button class="mtab" data-tab="h2h">H2H</button>
    </nav>

    <div class="mpanel" id="panel-prediction">
    ${topPicksPanel(p)}

    <!-- The match body. The two long panels ("Most accurate predictions" and
         "All markets") sit side by side in the LEFT half and are scroll-bounded;
         the scoreline/form/head-to-head stack is the RIGHT half and runs its own
         natural height. The long panels take their height FROM that stack
         (--match-col-h, set in matchDetail), so the three boxes end level instead
         of the market list running thousands of pixels past everything else. -->
    <div class="match-body" id="match-body">
      <div class="match-main">
        <div class="card">
          <h3>Most accurate predictions</h3>
          <p style="margin:-6px 0 12px;font-size:12.5px;color:var(--text-dim)">
            The model's strongest calls across every market, best first.
            One row per outcome — where several markets express the same thing
            (1X2 and 1X2 - 1UP, GG/NG and the GG/NG half of a combo) only the
            clearest is listed, so the list is not padded with repeats.
          </p>
          <div class="match-scroll">${accuratePicks(p)}</div>
        </div>

        <div class="card">
          <h3>All markets</h3>
          <p style="margin:-6px 0 12px;font-size:12.5px;color:var(--text-dim)">
            Every market the engine prices, in full. Markets marked
            <span class="prob-mkt">model only</span> come from the scoreline matrix
            with no book price to compare against, so they carry a probability and
            no edge claim.
          </p>
          <div class="match-scroll">${p.markets
          .map(
            (m) => `
          <div style="margin-bottom:16px">
            <div style="font-size:12px;font-weight:700;color:var(--text-dim);margin-bottom:8px;
                        text-transform:uppercase;letter-spacing:0.05em">${esc(m.name)}</div>
            ${m.selections
              .map(
                (s) => `
              <div class="prob-row">
                <span class="prob-name">${esc(s.label)}</span>
                <span class="prob-track"><span class="prob-fill model" style="width:${s.probability * 100}%"></span></span>
                <span class="prob-val">${esc(pct(s.probability))}</span>
              </div>
              <div style="display:flex;gap:14px;font-size:11.5px;color:var(--text-faint);
                          margin:-4px 0 9px 129px;flex-wrap:wrap">
                <span>model ${esc(pct(s.model_probability))}</span>
                <span>market ${s.market_probability == null ? "—" : esc(pct(s.market_probability))}</span>
                <span>odds ${s.odds ? num(s.odds) : "—"}</span>
                <span>fair ${num(s.fair_odds)}</span>
                <span class="${s.edge >= 0 ? "pos" : "neg"}">edge ${s.edge == null ? "—" : esc(signed(s.edge))}</span>
                ${s.value ? '<strong style="color:var(--accent)">VALUE</strong>' : ""}
              </div>`
              )
              .join("")}
                </div>`
              )
              .join("")}</div>
            </div>
          </div>

          <!-- The right-hand rail: its height is the target the long panels match. -->
          <div class="match-rail" id="match-rail">
            <div class="card" style="margin-bottom:16px">
              <h3>Most likely scorelines</h3>
          ${p.top_scorelines
            .map(
              (s) => `
            <div class="prob-row">
              <span class="prob-name" style="font-family:var(--mono)">${s.home_goals} – ${s.away_goals}</span>
              <span class="prob-track"><span class="prob-fill model" style="width:${s.probability * 100}%"></span></span>
              <span class="prob-val">${esc(pct(s.probability))}</span>
            </div>`
            )
            .join("")}
          <div style="margin-top:12px;padding-top:12px;border-top:1px solid var(--border-soft);
                      font-size:12.5px;color:var(--text-dim)">
            Expected goals: <strong style="color:var(--text)">${num(p.expected_home_goals)}</strong> –
            <strong style="color:var(--text)">${num(p.expected_away_goals)}</strong>
            (total ${num(p.expected_total_goals)})
          </div>
        </div>

        <div class="grid grid-2" style="gap:16px">
          ${formCard("Home form", p.home_form, fx.home_team.name)}
          ${formCard("Away form", p.away_form, fx.away_team.name)}
        </div>

        ${
          p.head_to_head && p.head_to_head.played
            ? `<div class="card" style="margin-top:16px">
                 <h3>Head to head</h3>
                 <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:10px;text-align:center">
                   <div><div style="font-size:22px;font-weight:800">${p.head_to_head.home_wins}</div>
                     <div style="font-size:11.5px;color:var(--text-dim)">${esc(fx.home_team.short_name || fx.home_team.name)} wins</div></div>
                   <div><div style="font-size:22px;font-weight:800">${p.head_to_head.draws}</div>
                     <div style="font-size:11.5px;color:var(--text-dim)">draws</div></div>
                   <div><div style="font-size:22px;font-weight:800">${p.head_to_head.away_wins}</div>
                     <div style="font-size:11.5px;color:var(--text-dim)">${esc(fx.away_team.short_name || fx.away_team.name)} wins</div></div>
                 </div>
                 <div style="margin-top:12px;text-align:center;font-size:12.5px;color:var(--text-dim)">
                   Last ${p.head_to_head.played} meetings · ${num(p.head_to_head.avg_total_goals)} goals per game
                 </div>
               </div>`
            : ""
        }
      </div><!-- /match-rail -->
    </div><!-- /match-body -->

    <div id="sportybet-slot" style="margin-top:18px"></div>

    <div id="ai-slot" style="margin-top:18px">
      <div class="card"><h3>AI analysis</h3>
        <div class="loading" style="padding:22px"><div class="spinner"></div>Writing the preview…</div></div>
    </div>

    <div class="grid grid-2" style="margin-top:18px">
      <div class="card">
        <h3>Key factors</h3>
        ${p.key_factors.length
          ? `<ul class="factor-list">${p.key_factors.map((f) => `<li>${esc(f)}</li>`).join("")}</ul>`
          : '<p style="color:var(--text-dim);font-size:13.5px">Not enough data yet.</p>'}
      </div>
      <div class="card">
        <h3>Risk notes</h3>
        ${p.risk_notes.length
          ? `<ul class="factor-list risk">${p.risk_notes.map((f) => `<li>${esc(f)}</li>`).join("")}</ul>`
          : '<p style="color:var(--text-dim);font-size:13.5px">—</p>'}
      </div>
    </div>

    <div style="display:flex;gap:10px;margin-top:18px;flex-wrap:wrap">
      <button class="btn btn-primary" id="track-btn">Track this tip</button>
      <button class="btn" id="refresh-btn">Re-run model</button>
    </div>
    </div><!-- /panel-prediction -->

    <div class="mpanel" id="panel-stats" hidden>
      <div class="card"><div class="hist-loading">
        <div class="spinner"></div>Loading real results…</div></div>
    </div>
    <div class="mpanel" id="panel-form" hidden>
      <div class="card"><div class="hist-loading">
        <div class="spinner"></div>Loading team form…</div></div>
    </div>
    <div class="mpanel" id="panel-h2h" hidden>
      <div class="card"><div class="hist-loading">
        <div class="spinner"></div>Loading head-to-head…</div></div>
    </div>
  `;
}

// ------------------------------------------- "most accurate" dedupe
// The engine prices 23 markets, but many of them are the SAME event expressed
// twice. 1X2 and 1X2 - 1UP are the identical probability before kick-off; the
// GG/NG half of "Double Chance & GG/NG" is just GG/NG; Correct Score 2-1 and
// "1X2 & O/U: home & over" describe the same scoreline. Listing all 23 as a flat
// "strongest first" list therefore shows the same number over and over and
// buries the genuinely different calls.
//
// So each row is tagged with the underlying EVENT it prices. Two selections
// collide when they price the same event, and only the first (highest
// probability, since the sort happens before) survives.

//: key -> the event a selection actually prices. Keys not listed are already
//: distinct and use their own name.
function pricedEvent(marketKey, selKey) {
  switch (marketKey) {
    // 1UP / 2UP pay out EARLY but are priced identically to plain 1X2, so they
    // price the same event.
    case "1x2_1up":
    case "1x2_2up":
      return `1x2:${selKey}`;

    // "X2" in a double chance IS "away or draw" — the same event as the X2 leg.
    case "dc":
      return selKey === "1x" ? "home_or_draw" : selKey === "12" ? "home_or_away" : "draw_or_away";
    case "dc_1up":
      return selKey === "1x" ? "home_or_draw" : selKey === "12" ? "home_or_away" : "draw_or_away";

    // "Double Chance & Over 2.5" prices match-result AND goals together.
    case "dc_ou25": {
      const [leg, side] = selKey.split("_"); // e.g. "1x" + "over"
      const res =
        leg === "1x" ? "home_or_draw" : leg === "12" ? "home_or_away" : "draw_or_away";
      return `${res}&${side === "over" ? "over25" : "under25"}`;
    }

    // "1X2 & Over 2.5" prices a result AND a goals line.
    case "1x2_ou25": {
      const [leg, side] = selKey.split("_"); // e.g. "home" + "over"
      return `1x2:${leg}&${side === "over" ? "over25" : "under25"}`;
    }

    // Correct score 2-1 IS "2-1", however it is reached.
    case "correct_score":
      return `cs:${selKey.replace(/^cs_/, "")}`;

    // Handicap lines that invert to the same event. -1 on the home side and
    // +1 on the away side settle on the SAME scores.
    case "handicap": {
      const [side, raw] = selKey.split("_");
      const line = parseFloat(raw.replace("m", "-"));
      if (side === "home") return `hc:${line}`;
      return `hc:${-line}`;
    }

    default:
      return `${marketKey}:${selKey}`;
  }
}

// The five-market shortlist: one best selection from each of the five markets a
// reader actually asks about -- 1X2, Over/Under, BTTS, Correct Score, Handicap.
//
// This is deliberately NOT a probability ranking. Sorting every selection by
// probability returns five Over/Under and Handicap lines, because the longest
// lines are the most certain -- informative about nothing. It also drops Correct
// Score entirely: the single most likely scoreline is only ~11-13%, so it can
// never reach the >= 50% bar the other panel uses.
//
// So each market contributes its own strongest call and the five sit side by
// side, each showing its own honest probability. Correct Score reads 12% next to
// Over 1.5's 78%, which is the truth: one is a long shot and one is not.
function topPicksPanel(p) {
  const picks = p.top_picks || [];
  if (!picks.length) return "";

  return `
    <div class="card" style="margin-bottom:16px">
      <h3>Top 5 predictions</h3>
      <p style="margin:-6px 0 14px;font-size:12.5px;color:var(--text-dim)">
        One best call from each of the five markets the model prices most reliably —
        the same five a tipster would publish. Each shows its own probability, so the
        confidence behind each is visible rather than implied by its position here.
      </p>

      <div class="top-picks">
        ${picks
          .map((t) => {
            const strong = t.probability >= 0.6;
            return `<div class="tp-card ${strong ? "is-strong" : ""}">
              <div class="tp-market">${esc(t.market_name)}</div>
              <div class="tp-label">${esc(t.label)}</div>
              <div class="tp-track"><span class="tp-fill" style="width:${(t.probability * 100).toFixed(1)}%"></span></div>
              <div class="tp-meta">
                <span class="tp-prob">${esc(pct(t.probability))}</span>
                <span class="tp-odds">${t.odds ? "@ " + num(t.odds) : "fair " + num(t.fair_odds)}</span>
              </div>
            </div>`;
          })
          .join("")}
      </div>

      <div class="tp-note">
        These are the model's five strongest calls, not five equally likely ones. A
        Correct Score is a long shot by nature and is shown as such; treating it as
        equal in confidence to a match result would overstate it.
      </div>
    </div>`;
}

function accuratePicks(p, limit = 20) {
  const rows = (p.markets || []).flatMap((m) =>
    m.selections.map((s) => ({
      ...s,
      market_key: m.key,
      market_name: m.name,
      event: pricedEvent(m.key, s.key),
    }))
  );

  // Strongest first, then keep only the first row for each underlying event.
  rows.sort((x, y) => y.probability - x.probability);
  const seen = new Set();
  const unique = [];
  for (const row of rows) {
    if (seen.has(row.event)) continue;
    seen.add(row.event);
    unique.push(row);
  }

  // A "pick" should actually be a call, not a coin flip: skip anything the
  // model rates below even money, otherwise the tail fills with 5% long shots.
  const confident = unique.filter((r) => r.probability >= 0.5);
  const picks = (confident.length ? confident : unique).slice(0, limit);

  return picks
    .map(
      (s) => `
          <div class="prob-row">
            <span class="prob-name">${esc(s.label)}
              <span class="prob-mkt">${esc(s.market_name)}</span>
            </span>
            <span class="prob-track"><span class="prob-fill model" style="width:${s.probability * 100}%"></span></span>
            <span class="prob-val">${esc(pct(s.probability))}</span>
          </div>`
    )
    .join("");
}

// ------------------------------------------------- real results history
let fixtureHistory = null;

async function loadFixtureHistory(fixtureId) {
  fixtureHistory = null;
  try {
    fixtureHistory = await api(`/fixtures/${fixtureId}/history`);
  } catch (e) {
    fixtureHistory = { available: false, reason: "error", message: e.message };
  }
  // Rendering is wrapped separately: an exception in here used to escape the
  // promise chain silently, leaving the panel on its loading skeleton with no
  // console entry, which made the failure invisible.
  try {
    renderHistoryPanels();
  } catch (e) {
    const msg = `<div class="error-box">Stats could not be drawn: ${esc(e.message)}</div>`;
    ["panel-stats", "panel-form", "panel-h2h"].forEach((id) => {
      const el = document.getElementById(id);
      if (el) el.innerHTML = msg;
    });
    throw e;
  }
}

function historyUnavailable(msg) {
  return `<div class="card"><div class="hist-empty">
    <p style="margin:0 0 6px;font-weight:700">No real results available</p>
    <p style="margin:0;font-size:13px;color:var(--text-dim)">${esc(msg)}</p>
 </div></div>`;
}

// A team's form as W/D/L pills, newest first.
function formStrip(letters) {
  if (!letters || !letters.length) return '<span class="hist-muted">—</span>';
  return `<span class="form-pills">${letters
    .slice(0, 10)
    .map((c) => `<span class="form-pill form-${esc(c.toLowerCase())}">${esc(c)}</span>`)
    .join("")}</span>`;
}

// One stat row: label, two values, and a proportional dual bar.
function statCompare(label, homeVal, awayVal, unit = "") {
  const h = Number(homeVal) || 0;
  const a = Number(awayVal) || 0;
  const total = h + a;
  const hp = total > 0 ? (h / total) * 100 : 50;
  return `
    <div class="stat-row">
      <div class="stat-nums">
        <span class="stat-v ${h >= a ? "lead" : ""}">${h}${unit}</span>
        <span class="stat-l">${esc(label)}</span>
        <span class="stat-v ${a >= h ? "lead" : ""}">${a}${unit}</span>
      </div>
      <div class="stat-bars">
        <span class="stat-bar home" style="width:${hp}%"></span>
        <span class="stat-bar away" style="width:${100 - hp}%"></span>
      </div>
    </div>`;
}

function fixtureRow(m) {
  const d = new Date(m.kickoff);
  const date = d.toLocaleDateString([], { day: "2-digit", month: "short" });
  return `
    <div class="hist-row ${m.outcome ? "o-" + esc(m.outcome) : ""}">
      <span class="hist-date">${esc(date)}</span>
      <span class="hist-teams">
        <span class="hist-side ${m.venue === "home" ? "is-venue" : ""}">${esc(m.home_team)}</span>
        <span class="hist-score">${m.home_goals}–${m.away_goals}</span>
        <span class="hist-side away ${m.venue === "away" ? "is-venue" : ""}">${esc(m.away_team)}</span>
      </span>
      <span class="hist-comp">${esc(m.competition || "")}</span>
    </div>`;
}

// A club's last five results as the target page shows them: date and competition
// stacked on the left, the score in the middle, the opponents on the outside.
// Reads from that club's own perspective, so "home" here means the club was at
// home, which is what the left/right placement signals.
function sbLastFive(form, clubName) {
  if (!form || !form.matches || !form.matches.length) return "";
  return form.matches
    .slice(0, 5)
    .map((m) => {
      const d = new Date(m.kickoff);
      const date = d.toLocaleDateString([], { day: "2-digit", month: "short", year: "numeric" });
      const comp = (m.competition || "").toUpperCase();
      return `
      <div class="sb-last5-row">
        <div class="sb-last-side">
          <span class="sb-last5-team">${esc(clubName)}</span>
        </div>
        <div class="sb-last5-mid">
          <span class="sb-last5-date">${esc(date)}</span>
          <span class="sb-last5-comp">${esc(comp)}</span>
          <span class="sb-last5-score">${m.home_goals} - ${m.away_goals}</span>
        </div>
        <div class="sb-last5-side away">
          <span class="sb-last5-team">${esc(clubName)}
            <span class="sb-last5-opp">${esc(m.home_team === clubName ? m.away_team : m.home_team)}</span>
          </span>
        </div>
      </div>`;
    })
    .join("");
}

// One crescent ring for the previous-meetings block: a thick arc filled
// proportionally, with the count in the middle. Uses a conic gradient so the
// fill is continuous, as on the target page.
function meetingArc(value, total, label, cls) {
  const pct = total > 0 ? (value / total) * 100 : 0;
  return `
    <div class="sb-arc">
      <div class="sb-arc-ring ${cls}" style="--fill:${pct}%"></div>
      <div class="sb-arc-val">${value}</div>
      <div class="sb-arc-k">${esc(label)}</div>
    </div>`;
}

// The grouped vertical bar chart from the "Season Stats" block: for each measure
// two bars (home then away) scaled to the larger of the two, with the value on
// top. Only renders measures that are actually present in the data.
function seasonStatBar(label, homeV, awayV) {
  const max = Math.max(Number(homeV) || 0, Number(awayV) || 0, 0.0001);
  const h = ((Number(homeV) || 0) / max) * 100;
  const a = ((Number(awayV) || 0) / max) * 100;
  return `
    <div class="sb-chart-group">
      <div class="sb-chart-title">${esc(label)}</div>
      <div class="sb-chart-bars">
        <div class="sb-chart-col">
          <span class="sb-chart-val home">${num(homeV, 1)}</span>
          <span class="sb-chart-bar home" style="height:${h}%"></span>
        </div>
        <div class="sb-chart-col">
          <span class="sb-chart-val away">${num(awayV, 1)}</span>
          <span class="sb-chart-bar away" style="height:${a}%"></span>
        </div>
      </div>
    </div>`;
}

// The head-to-head panel, built to the target page's structure: "Previous
// meetings" arcs, then the dated list of meetings, then a Season Stats chart of
// the measures the feed actually provides. Shared by the Stats and H2H tabs so
// both render from one source.
//
// Deliberately absent: the league-position rings and the per-matchday form
// percentage. The results feed carries no standings, so showing those would mean
// inventing numbers.
function h2hResultsHTML(h) {
  const hh = h && h.head_to_head;
  if (!hh || !hh.stats.played) return "";
  const s = hh.stats;
  const hf = h.home_form;
  const af = h.away_form;
  const homeName = hf ? hf.team : "Home";
  const awayName = af ? af.team : "Away";

  // Highest win in the series, from the meetings themselves.
  let best = null;
  let bestMargin = -1;
  (hh.matches || []).forEach((m) => {
    const margin = Math.abs(m.home_goals - m.away_goals);
    if (margin > bestMargin) {
      bestMargin = margin;
      best = m;
    }
  });
  let bestHTML = "N/A";
  if (best && bestMargin > 0) {
    const d = new Date(best.kickoff);
    const dt = d.toLocaleDateString([], { day: "2-digit", month: "short", year: "numeric" });
    const hi = Math.max(best.home_goals, best.away_goals);
    const lo = Math.min(best.home_goals, best.away_goals);
    bestHTML = `${hi}-${lo}<span class="sb-best-date">${esc(dt)}</span>`;
  }

  const chartRows = [
    ["Average goals scored", hf && hf.stats.avg_goals_for, af && af.stats.avg_goals_for],
    ["Average goals conceded", hf && hf.stats.avg_goals_against, af && af.stats.avg_goals_against],
  ].filter(([, hv, av]) => hv != null && av != null);

  return `
    <div class="card stats-sb sb-h2h" style="margin-top:16px">
      <div class="sb-sec-head">Previous meetings</div>

      <div class="sb-prev">
        <div class="sb-prev-side">
          <span class="sb-prev-k">HIGHEST WIN</span>
          <span class="sb-prev-best">${bestHTML}</span>
        </div>

        <div class="sb-prev-arcs">
          ${meetingArc(s.home_wins, s.home_wins + s.draws + s.away_wins, esc(homeName) + " Wins", "home")}
          <div class="sb-arc sb-arc-sm">
            <div class="sb-arc-ring draw" style="--fill:${s.played > 0 ? (s.draws / s.played) * 100 : 0}%"></div>
            <div class="sb-arc-val">${s.draws}</div>
            <div class="sb-arc-k">Draws</div>
          </div>
          ${meetingArc(s.away_wins, s.home_wins + s.draws + s.away_wins, esc(awayName) + " Wins", "away")}
        </div>

        <div class="sb-prev-side right">
          <span class="sb-prev-k">HIGHEST WIN</span>
          <span class="sb-prev-best">${bestHTML}</span>
        </div>
      </div>

      <div class="sb-sec-head">Last ${hh.matches.length} matches</div>
      <div class="sb-meetings">
        ${(hh.matches || [])
          .map((m) => {
            const d = new Date(m.kickoff);
            const date = d.toLocaleDateString([], { day: "2-digit", month: "short", year: "numeric" });
            return `
          <div class="sb-meet-row">
            <div class="sb-meet-meta">
              <span class="sb-meet-date">${esc(date.toUpperCase())}</span>
              <span class="sb-meet-comp">${esc((m.competition || "").toUpperCase())}</span>
            </div>
            <span class="sb-meet-home">${esc(m.home_team)}</span>
            <span class="sb-meet-score">${m.home_goals} - ${m.away_goals}</span>
            <span class="sb-meet-away">${esc(m.away_team)}</span>
          </div>`;
          })
          .join("")}
      </div>

      ${chartRows.length ? `<div class="sb-sec-head">Season Stats</div><div class="sb-chart">${chartRows.map(([l, hv, av]) => seasonStatBar(l, hv, av)).join("")}</div>` : ""}
    </div>`;
}

function teamStatsCard(title, form) {
  if (!form) return "";
  const s = form.stats;
  return `
    <div class="card">
      <div class="hist-head">
        <h3 style="margin:0">${esc(title)}</h3>
        <span class="hist-sub">last ${s.played}</span>
      </div>
      <div class="hist-summary">
        <div class="hs-item"><div class="hs-v">${s.won}</div><div class="hs-k">Won</div></div>
        <div class="hs-item"><div class="hs-v">${s.drawn}</div><div class="hs-k">Drawn</div></div>
        <div class="hs-item"><div class="hs-v">${s.lost}</div><div class="hs-k">Lost</div></div>
        <div class="hs-item"><div class="hs-v">${s.goal_diff > 0 ? "+" : ""}${s.goal_diff}</div><div class="hs-k">Goal diff</div></div>
        <div class="hs-item"><div class="hs-v">${num(s.points_per_game, 2)}</div><div class="hs-k">PPG</div></div>
      </div>
      <div class="hist-formline">
        <span class="hist-formlab">Form</span>${formStrip(s.form)}
      </div>
      <div class="hist-split">
        <div><span class="hist-muted">Scored / game</span><strong>${num(s.avg_goals_for, 2)}</strong></div>
        <div><span class="hist-muted">Conceded / game</span><strong>${num(s.avg_goals_against, 2)}</strong></div>
        <div><span class="hist-muted">Clean sheets</span><strong>${s.clean_sheets}</strong></div>
        <div><span class="hist-muted">Failed to score</span><strong>${s.failed_to_score}</strong></div>
        <div><span class="hist-muted">BTTS</span><strong>${pct0(s.btts_rate)}</strong></div>
        <div><span class="hist-muted">Over 2.5</span><strong>${pct0(s.over25_rate)}</strong></div>
      </div>
      <div class="hist-list">${form.matches.map(fixtureRow).join("")}</div>
    </div>`;
}

function renderHistoryPanels() {
  const stats = document.getElementById("panel-stats");
  const form = document.getElementById("panel-form");
  const h2h = document.getElementById("panel-h2h");
  if (!stats || !form || !h2h) return;

  const h = fixtureHistory;
  if (!h || !h.available) {
    const why =
      h && h.message
        ? h.message
        : "The results feed could not be reached for this match. The model's own numbers are unaffected.";
    const msg = historyUnavailable(why);
    stats.innerHTML = msg;
    form.innerHTML = msg;
    h2h.innerHTML = msg;
    return;
  }

  const hf = h.home_form;
  const af = h.away_form;

  // --- Stats: the two sides compared on the same measures ---
  if (hf && af) {
    const a = hf.stats;
    const b = af.stats;
    // Their top block is three columns: the home club's last five results, a
    // centred summary, then the away club's last five. The centre column on the
    // target page carries league-position rings, which this feed cannot supply,
    // so it shows the measures that do exist instead of placeholder rings.
    stats.innerHTML = `
      <div class="card stats-sb sb-top">
        <div class="sb-top-col">
          <div class="sb-top-k">LAST 5 MATCHES</div>
          ${(hf.matches || [])
            .slice(0, 5)
            .map((m) => {
              const opp = m.home_team === hf.team ? m.away_team : m.home_team;
              const r = (m.result || "").toUpperCase();
              return `<div class="sb-mini">
                <span class="sb-mini-r r-${esc((m.result || "").toLowerCase())}">${esc(r)}</span>
                <span class="sb-mini-opp">${esc(opp.slice(0, 3).toUpperCase())}</span>
                <span class="sb-mini-sc">${m.home_goals}.${m.away_goals}</span>
              </div>`;
            })
            .join("")}
        </div>

        <div class="sb-top-col centre">
          <div class="sb-top-k">FORM SUMMARY</div>
          <div class="sb-top-form">${formStrip(hf.stats.form)}</div>
          <div class="sb-top-big">${hf.stats.won}<span class="sb-top-sub">W</span></div>
          <div class="sb-top-line"></div>
          <div class="sb-top-big away">${af.stats.won}<span class="sb-top-sub">W</span></div>
          <div class="sb-top-form">${formStrip(af.stats.form)}</div>
        </div>

        <div class="sb-top-col right">
          <div class="sb-top-k">LAST 5 MATCHES</div>
          ${(af.matches || [])
            .slice(0, 5)
            .map((m) => {
              const opp = m.home_team === af.team ? m.away_team : m.home_team;
              const r = (m.result || "").toUpperCase();
              return `<div class="sb-mini">
                <span class="sb-mini-sc">${m.home_goals}.${m.away_goals}</span>
                <span class="sb-mini-opp">${esc(opp.slice(0, 3).toUpperCase())}</span>
                <span class="sb-mini-r r-${esc((m.result || "").toLowerCase())}">${esc(r)}</span>
              </div>`;
            })
            .join("")}
        </div>
      </div>

      <div class="card stats-sb" style="margin-top:12px">
        <div class="hist-head">
          <h3 style="margin:0">Team form — last ${Math.max(a.played, b.played)} matches</h3>
          <span class="src-tag src-real">Live data</span>
        </div>
        <div class="stat-team-head">
          <span>${esc(hf.team)}</span><span>${esc(af.team)}</span>
        </div>
        ${statCompare("Won", a.won, b.won)}
        ${statCompare("Drawn", a.drawn, b.drawn)}
        ${statCompare("Lost", a.lost, b.lost)}
        ${statCompare("Goals scored", a.goals_for, b.goals_for)}
        ${statCompare("Goals conceded", a.goals_against, b.goals_against)}
        ${statCompare("Clean sheets", a.clean_sheets, b.clean_sheets)}
        ${statCompare("Avg scored / game", a.avg_goals_for, b.avg_goals_for)}
        ${statCompare("Avg conceded / game", a.avg_goals_against, b.avg_goals_against)}
        ${statCompare("BTTS rate", Math.round(a.btts_rate * 100), Math.round(b.btts_rate * 100), "%")}
        ${statCompare("Over 2.5 rate", Math.round(a.over25_rate * 100), Math.round(b.over25_rate * 100), "%")}
        ${statCompare("Points per game", a.points_per_game, b.points_per_game)}
      </div>
      ${h2hResultsHTML(h)}`;
  } else {
    stats.innerHTML = historyUnavailable("No recent results were returned for these teams.");
  }

  // --- Form: each side's own recent matches ---
  form.innerHTML = `
    ${hf ? teamStatsCard(hf.team, hf) : ""}
    ${af ? teamStatsCard(af.team, af) : ""}`;

  // --- H2H: past meetings between the two ---
  const h2hHTML = h2hResultsHTML(h);
  h2h.innerHTML = h2hHTML
    ? h2hHTML.replace('style="margin-top:16px"', "")
    : historyUnavailable("These teams have no recorded previous meetings.");
}

function wireMatchTabs() {
  const bar = document.getElementById("match-tabs");
  if (!bar) return;
  bar.querySelectorAll(".mtab").forEach((btn) =>
    btn.addEventListener("click", () => {
      bar.querySelectorAll(".mtab").forEach((b) => b.classList.toggle("active", b === btn));
      ["prediction", "stats", "form", "h2h"].forEach((name) => {
        const panel = document.getElementById(`panel-${name}`);
        if (panel) panel.hidden = name !== btn.dataset.tab;
      });
    })
  );
}

// ------------------------------------------------- SportyBet market board
async function loadSportyBetPanel(fixtureId) {
  const slot = document.getElementById("sportybet-slot");
  if (!slot) return;
  try {
    const board = await api(`/fixtures/${fixtureId}/sportybet-markets`);
    slot.innerHTML = sportybetPanel(board);
  } catch (e) {
    slot.innerHTML = sportybetPanel({
      available: false,
      reason: "request_failed",
      message: e.message,
    });
  }
}

function sportybetPanel(board) {
  // Deliberately styled apart from the model cards, and explicit that none of
  // these prices carry a GoalEdge prediction. The engine prices 1X2, O/U and
  // BTTS only; everything below is the book's own board.
  const head = `
    <div style="display:flex;align-items:baseline;gap:10px;flex-wrap:wrap;margin-bottom:4px">
      <h3 style="margin:0">SportyBet markets</h3>
      <span class="badge badge-edge">Book prices · not GoalEdge picks</span>
    </div>
    <p style="color:var(--text-dim);font-size:12.5px;margin:6px 0 14px">
      These are SportyBet's live prices, shown for reference. The model has not priced
      them and attaches no probability, edge or confidence to any selection here.
    </p>`;

  // The book's category index. Shown even when the live board is unavailable,
  // because it describes the market structure rather than this fixture's prices.
  const cats = board && board.categories ? board.categories : [];
  const index = cats.length
    ? `
    <div class="mkt-index">
      <div class="mkt-index-head">
        Market categories
        <span class="mkt-key">
          <span class="mkt-dot model"></span> priced by the model
          <span class="mkt-dot book"></span> book only
        </span>
      </div>
      <ul class="mkt-list">
        ${cats
          .map(
            (c) => `
          <li class="mkt-row ${c.present ? "present" : "absent"}" title="${
              c.model_priced
                ? esc(c.note || "Priced by the GoalEdge engine.")
                : "GoalEdge does not price this category. Prices only, no prediction."
          }">
            <span class="mkt-dot ${c.model_priced ? "model" : "book"}"></span>
            <span class="mkt-name">${esc(c.label)}</span>
            ${c.model_priced ? '<span class="mkt-tag">model</span>' : ""}
            ${c.present ? '<span class="mkt-tag on">on this match</span>' : ""}
          </li>`
          )
          .join("")}
      </ul>
    </div>`
    : "";

  if (!board || !board.available) {
    const msg = esc((board && board.message) || "The market board is unavailable right now.");
    return `<div class="card" style="border-style:dashed">${head}${index}
      <div class="mkt-notice">${msg}</div>
    </div>`;
  }

  const groups = (board.groups || [])
    .map(
      (g) => `
      <div class="mkt-group">
        <div class="mkt-group-head">
          ${esc(g.group)}
          ${g.model_priced ? '<span class="mkt-tag">GoalEdge prices this</span>' : ""}
        </div>
        ${g.markets
          .map(
            (m) => `
          <div class="mkt-market">
            <div class="mkt-market-name">${esc(m.name)}</div>
            <div class="sel-items">
              ${m.selections
                .map(
                  (s) => `
                <span class="sel-item">
                  <span class="sel-label">${esc(s.label)}</span>
                  <span class="sel-odds">${num(s.odds)}</span>
                </span>`
                )
                .join("")}
            </div>
          </div>`
          )
          .join("")}
      </div>`
    )
    .join("");

  const foot = board.truncated
    ? `<p style="color:var(--text-faint);font-size:12px;margin:8px 0 0">
         Showing ${board.shown_count} of ${board.market_count} markets.</p>`
    : "";

  const board_body = groups
    ? `<div class="mkt-board">${groups}</div>`
    : '<div class="mkt-notice">No prices returned for this fixture.</div>';

  return `<div class="card" style="border-style:dashed">${head}${index}${board_body}
    <p class="mkt-caveat">${esc(board.caveat || "")}</p>
    ${foot}
 </div>`;
}

function formCard(title, form, teamName) {
  if (!form)
    return `<div class="card"><h3>${esc(title)}</h3>
      <p style="color:var(--text-dim);font-size:13.5px;margin:0">No recent results recorded.</p></div>`;
  return `
    <div class="card">
      <h3>${esc(title)}</h3>
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px">
        <strong style="font-size:14px">${esc(teamName)}</strong>
        ${form.position ? `<span class="badge badge-edge">${form.position}${ordinal(form.position)}</span>` : ""}
      </div>
      <div style="margin-bottom:10px">${formPills(form.form)}</div>
      <table class="data" style="font-size:13px">
        <tbody>
          <tr><td style="color:var(--text-dim)">Played</td><td class="num">${form.played}</td></tr>
          <tr><td style="color:var(--text-dim)">Record (W-D-L)</td>
              <td class="num">${form.won}-${form.drawn}-${form.lost}</td></tr>
          <tr><td style="color:var(--text-dim)">Goals for / against</td>
              <td class="num">${form.goals_for} / ${form.goals_against}</td></tr>
          <tr><td style="color:var(--text-dim)">Points per game</td>
              <td class="num">${num(form.ppg)}</td></tr>
          <tr><td style="color:var(--text-dim)">Home PPG</td><td class="num">${num(form.home_ppg)}</td></tr>
          <tr><td style="color:var(--text-dim)">Away PPG</td><td class="num">${num(form.away_ppg)}</td></tr>
          <tr><td style="color:var(--text-dim)">BTTS rate</td><td class="num">${esc(pct0(form.btts_rate))}</td></tr>
          <tr><td style="color:var(--text-dim)">Over 2.5 rate</td><td class="num">${esc(pct0(form.over25_rate))}</td></tr>
        </tbody>
      </table>
    </div>`;
}

function ordinal(n) {
  const s = ["th", "st", "nd", "rd"], v = n % 100;
  return s[(v - 20) % 10] || s[v] || s[0];
}

function aiPanel(fx, p, ai) {
  const provider = ai.provider === "openai" ? `OpenAI ${ai.model || ""}` : "GoalEdge analyst (rule-based)";
  const paras = String(ai.analysis || "")
    .split(/\n{2,}/)
    .filter(Boolean)
    .map((t) => `<p>${esc(t)}</p>`)
    .join("");

  return `
    <div class="ai-panel">
      <div class="ai-head">
        <span class="ai-dot"></span>
        <span class="ai-title">AI match analyst</span>
        <span class="provider-tag">${esc(provider)}</span>
      </div>
      ${ai.headline ? `<div class="ai-headline">${esc(ai.headline)}</div>` : ""}
      <div class="ai-body">${paras}</div>
      ${ai.betting_angle ? `<div class="ai-angle"><strong>Betting angle:</strong> ${esc(ai.betting_angle)}</div>` : ""}
    </div>`;
}

//: The gap between the two stacked cards in the left column, in pixels. Must
//: match the .match-main gap in styles.css -- it is subtracted from the height
//: budget so the pair ends level with the rail instead of 16px past it.
const MATCH_CARD_GAP = 16;

//: Make the two long panels ("Most accurate predictions", "All markets") together
//: as tall as the scoreline/form/head-to-head rail beside them.
//:
//: The rail is measured rather than the height being hardcoded, because it is
//: not a fixed size: the head-to-head card is omitted for a fixture with no
//: history, and the form rows wrap differently per team name. A fixed height
//: would be wrong for those fixtures and would leave a gap under the rail.
//:
//: A ResizeObserver rather than a one-shot measurement, because the rail grows
//: AFTER this runs -- the form and head-to-head data arrive from a second
//: request, and the page reflows when the web fonts land.
function fitMatchColumns() {
  const body = document.getElementById("match-body");
  const rail = document.getElementById("match-rail");
  if (!body || !rail) return;

  // On the stacked layout there is no second column to match, and CSS has
  // already released the height (see the media queries in styles.css). Setting
  // the variable there would be actively wrong: it would hand the panels a fixed
  // height the stylesheet has said they should not have.
  const stacked = window.matchMedia("(max-width: 900px)").matches;
  if (stacked) {
    body.style.removeProperty("--match-col-h");
    return;
  }

  // offsetHeight, not getBoundingClientRect().height: the latter is scaled by any
  // ancestor transform, which would feed a wrong number back into the layout.
  const target = Math.round(rail.offsetHeight);
  if (target <= 0) return;

  const cards = [...document.querySelectorAll(".match-main > .card")];
  if (!cards.length) return;

  // The cards stack, so they halve the rail's height between them. A MINIMUM is
  // applied per card: a short rail (no head-to-head) would otherwise hand each
  // card a box smaller than its own heading, and the market list would be a
  // two-row letterbox. When the floor bites, the pair simply runs a little past
  // the rail -- which is the honest outcome, not a clipped panel.
  const MIN_CARD = 200;
  const share = (target - MATCH_CARD_GAP * (cards.length - 1)) / cards.length;
  body.style.setProperty("--match-card-h", `${Math.max(MIN_CARD, Math.round(share))}px`);
  // Kept for the rail-vs-panels comparison and for anything reading the total.
  body.style.setProperty("--match-col-h", `${target}px`);
}

function observeMatchColumns() {
  // One pass now (the markup is already in the DOM), then on every size change.
  fitMatchColumns();
  const rail = document.getElementById("match-rail");
  if (!rail) return;

  if (state.matchFitObserver) state.matchFitObserver.disconnect();
  if (typeof ResizeObserver === "function") {
    state.matchFitObserver = new ResizeObserver(fitMatchColumns);
    state.matchFitObserver.observe(rail);
  }
  // Also on a width change: the stacked/two-column switch changes what "fit"
  // means, and that is a viewport event rather than a rail event.
  window.addEventListener("resize", fitMatchColumns);
}

function wireDetail(fx, p) {
  const back = document.getElementById("back-btn");
  if (back) back.addEventListener("click", () => navigate("tips"));

  // Tab switching between the model's view and the real-results panels.
  wireMatchTabs();

  // Level the prediction columns against the rail beside them.
  observeMatchColumns();

  const track = document.getElementById("track-btn");
  if (track)
    track.addEventListener("click", async () => {
      track.disabled = true;
      try {
        const r = await api(`/fixtures/${fx.id}/track`, { method: "POST" });
        toast(r.status === "exists" ? "Already in the tracked record" : "Tip added to the public record");
        if (state.user) refreshAuthUI();
      } catch (e) {
        toast(`Could not track: ${e.message}`);
      } finally {
        track.disabled = false;
      }
    });

  const refresh = document.getElementById("refresh-btn");
  if (refresh)
    refresh.addEventListener("click", async () => {
      refresh.disabled = true;
      refresh.textContent = "Re-running…";
      try {
        await api(`/fixtures/${fx.id}/prediction?refresh=true`);
        await openDetail(fx.id);
        toast("Model re-run");
      } catch (e) {
        toast(`Failed: ${e.message}`);
        refresh.disabled = false;
        refresh.textContent = "Re-run model";
      }
    });
}

// ---------------------------------------------------------------- auth

function openAuthModal(mode = "login") {
  document.querySelectorAll(".modal-backdrop").forEach((m) => m.remove());
  const backdrop = document.createElement("div");
  backdrop.className = "modal-backdrop";
  backdrop.innerHTML = `
    <div class="modal">
      <h3>${mode === "login" ? "Sign in" : "Create account"}</h3>
      ${
        mode === "register"
          ? `<div class="field"><label for="m-user">Username</label>
               <input id="m-user" autocomplete="username" placeholder="yourname"></div>`
          : ""
      }
      <div class="field"><label for="m-email">Email</label>
        <input id="m-email" type="email" autocomplete="email" placeholder="you@example.com"></div>
      <div class="field"><label for="m-pass">Password</label>
        <input id="m-pass" type="password" autocomplete="current-password" placeholder="At least 6 characters"></div>
      <div id="m-err" class="error-box" style="display:none;margin-top:4px"></div>
      <div class="modal-actions">
        <button class="btn btn-ghost" id="m-cancel">Cancel</button>
        <button class="btn btn-primary" id="m-go">${mode === "login" ? "Sign in" : "Sign up"}</button>
      </div>
      <div style="text-align:center;margin-top:14px;font-size:12.5px;color:var(--text-dim)">
        ${mode === "login" ? "No account?" : "Already registered?"}
        <button class="btn btn-ghost btn-sm" id="m-switch" style="padding:2px 6px">
          ${mode === "login" ? "Create one" : "Sign in"}</button>
      </div>
    </div>`;
  document.body.appendChild(backdrop);

  const close = () => backdrop.remove();
  backdrop.addEventListener("click", (e) => { if (e.target === backdrop) close(); });
  document.getElementById("m-cancel").addEventListener("click", close);
  document.getElementById("m-switch").addEventListener("click", () => {
    close();
    openAuthModal(mode === "login" ? "register" : "login");
  });

  const submit = async () => {
    const email = document.getElementById("m-email").value.trim();
    const password = document.getElementById("m-pass").value;
    const username = mode === "register" ? document.getElementById("m-user").value.trim() : null;
    const err = document.getElementById("m-err");
    const go = document.getElementById("m-go");

    err.style.display = "none";
    if (!email || !password) {
      err.textContent = "Email and password are required.";
      err.style.display = "block";
      return;
    }
    go.disabled = true;
    go.textContent = "Please wait…";
    try {
      const path = mode === "login" ? "/auth/login" : "/auth/register";
      const body = mode === "login" ? { email, password } : { email, password, username };
      const res = await api(path, { method: "POST", body: JSON.stringify(body) });
      state.token = res.access_token;
      state.user = res.user;
      localStorage.setItem("ge_token", state.token);
      close();
      toast(`Signed in as ${res.user.username}`);
      refreshAuthUI();
    } catch (e) {
      err.textContent = e.message;
      err.style.display = "block";
      go.disabled = false;
      go.textContent = mode === "login" ? "Sign in" : "Sign up";
    }
  };

  document.getElementById("m-go").addEventListener("click", submit);
  backdrop.querySelectorAll("input").forEach((i) =>
    i.addEventListener("keydown", (e) => { if (e.key === "Enter") submit(); })
  );
  setTimeout(() => document.getElementById("m-email")?.focus(), 40);
}

// ---------------------------------------------------------------- theme
//
// Two themes, held on <html data-theme="...">. The stylesheet does the rest:
// every colour is a token, and the light theme re-declares the tokens rather
// than overriding rules, so there is nothing here that has to know what a card
// or a chip looks like.
//
// "system" is a real third state, not a synonym for "dark": with no choice
// stored, the page follows the OS and keeps following it if the OS changes. As
// soon as the reader picks a theme, that explicit choice wins and is remembered.
const THEME_KEY = "ge_theme";

// ------------------------------------------------------- operator design
//
// The colours, hover behaviour, layout and corner radius an operator chooses in
// the admin panel. These are site-wide, so they are stored server-side and every
// visitor gets them -- unlike the light/dark preference above, which is per
// reader and deliberately stays in localStorage.
//
// The two layers compose rather than compete: the operator's colours are applied
// to `:root` as custom properties, and the light/dark block re-declares the same
// property names for `html[data-theme="light"]`, so dark mode shows the
// operator's palette. That is the honest reading -- an operator recolouring the
// brand green expects it in both themes.
//
// The values are re-validated here before being written. The server already
// refuses anything malformed (see app/design.py), but this runs on the critical
// rendering path, and a stylesheet is a place where a stray `}` is not a
// cosmetic problem.

const DESIGN_KEY = "ge_design";

//: A colour this client is willing to write into a CSS declaration. Deliberately
//: the same grammar the server enforces, because a value that passed the server
//: must still not be able to close the block it is written into.
const SAFE_COLOUR = /^(#[0-9a-fA-F]{3,8}|(?:rgb|rgba|hsl|hsla)\(\s*[0-9.,%\s/]+\)|[a-zA-Z]{3,30})$/;

const safeColour = (v) =>
  typeof v === "string" && v.length <= 64 && SAFE_COLOUR.test(v.trim()) ? v.trim() : null;

//: The tokens the light/dark blocks exist to swap. These are applied WITHOUT
//: `!important` (see applySiteDesign) so the reader's theme choice still wins --
//: pinning them is what made the header toggle look dead.
const THEME_DEFINING_TOKENS = new Set([
  "bg", "bg-soft", "surface", "surface-2", "border", "border-soft",
  "text", "text-dim", "text-faint",
]);

const HOVER_KEYS = new Set(["lift", "glow", "tint", "border", "scale", "none"]);
const INTENSITY_SCALE = { off: "0", subtle: "0.55", normal: "1", strong: "1.5" };
const LAYOUT_KEYS = new Set(["comfortable", "compact", "wide", "centered"]);
const RADIUS_KEYS = new Set(["sharp", "rounded", "pill"]);

//: What was last applied, so the panel can show it is dirty against the saved
//: state rather than against whatever happens to be on screen.
let appliedDesign = null;

function designStyleEl() {
  // One <style> element, created once and rewritten in place. Appending a new
  // element per apply would grow the document every save and make the panel's
  // own preview flicker as old blocks were superseded.
  let el = document.getElementById("site-design-tokens");
  if (!el) {
    el = document.createElement("style");
    el.id = "site-design-tokens";
    document.head.appendChild(el);
  }
  return el;
}

/**
 * Apply a design to the document.
 *
 * Colours become a `:root` custom-property block; hover, layout and radius become
 * `data-*` attributes, which the stylesheet keys off. Nothing is written into an
 * inline `style` attribute, so no operator value can escape into the HTML.
 */
function applySiteDesign(design) {
  if (!design) return;
  appliedDesign = design;
  const root = document.documentElement;

  // --- colours ---
  //
  // The tokens split into two groups, and it is the whole reason the light/dark
  // toggle still works.
  //
  // THEME-DEFINING tokens (`bg`, `surface`, `text`, `border`, ...) ARE the
  // theme: the stylesheet's two blocks exist to swap exactly these. They must
  // therefore NOT be pinned with `!important`. Doing so was a real bug -- an
  // operator who saved a design while the panel was in light mode froze the
  // page to that palette in BOTH themes, so the header toggle flipped
  // `data-theme` (the icon and aria-label even updated) while every surface on
  // screen stayed put. It looked like a dead button.
  //
  // THEME-NEUTRAL tokens (accents, links, status colours) are brand, not
  // surface, and the intent is that they read the same in either theme. These
  // keep `!important`.
  //
  // The selector note: the stylesheet sets these names twice -- once on `:root`
  // (dark) and once on `html[data-theme="light"]`. The light block is an
  // element+attribute selector, which OUTRANKS a bare `:root`, so the brand
  // block is written to `html:root` at equal specificity (source order then
  // decides, and this element is appended last).
  //
  // The theme-defining tokens go under `html[data-theme="light"]` ONLY. The
  // stored design is a single flat palette with no per-theme variant, so it can
  // only honestly describe one theme -- and the one it describes is whichever
  // theme the operator was looking at when they saved. Scoping them to light
  // means the light theme shows the palette they chose while dark mode falls
  // back to the stylesheet's own dark surfaces, so BOTH settings of the toggle
  // now look like what they say. (Applying these to a bare `:root` instead made
  // dark mode inherit the light palette, since source order put this block after
  // the stylesheet's.)
  const brandLines = [];
  const themeLines = [];
  const colours = design.colours || {};
  for (const [key, value] of Object.entries(colours)) {
    // The property name is validated too: it is interpolated into a selector
    // position of sorts (`--name: value;`), and an odd key would emit a broken
    // declaration at best.
    if (!/^[a-z][a-z0-9-]{0,40}$/.test(key)) continue;
    const colour = safeColour(value);
    if (!colour) continue;
    if (THEME_DEFINING_TOKENS.has(key)) themeLines.push(`  --${key}: ${colour};`);
    else brandLines.push(`  --${key}: ${colour};`);
  }

  const blocks = [];
  // The operator's surfaces, scoped to the light theme and without `!important`:
  // equal specificity to the stylesheet's own light block, so source order
  // settles it, and nothing here can reach across into the dark theme.
  //
  // SUPPRESSED WHEN THE DESIGN IS STOCK. The stored palette is a single flat set
  // of values with no per-theme variant, so it can only describe one theme --
  // and since the palette above WAS made per-theme in the stylesheet (dark
  // `#000000`/`#171c1f`, light `#edf1f6`/`#ffffff`), injecting the flat stock
  // values would force dark surfaces onto a light page. It did exactly that: the
  // site rendered black in light mode, because the stock `bg` overrode the
  // stylesheet's own light block. When nothing has been customised there is
  // nothing to inject, and the stylesheet's two blocks are the only thing that
  // should be deciding. An operator who DOES pick surfaces still overrides.
  if (themeLines.length && design.changed_from_default !== 0) {
    blocks.push(`html[data-theme="light"] {\n${themeLines.join("\n")}\n}`);
  }
  if (brandLines.length && design.changed_from_default !== 0) {
    blocks.push(
      `html:root {\n${brandLines.map((l) => l.replace(";", " !important;")).join("\n")}\n}`
    );
  }
  designStyleEl().textContent = blocks.join("\n");

  // --- behaviour ---
  root.dataset.hover = HOVER_KEYS.has(design.hover_template)
    ? design.hover_template
    : "lift";
  root.style.setProperty(
    "--hover-scale",
    INTENSITY_SCALE[design.hover_intensity] ?? "1"
  );
  root.dataset.layout = LAYOUT_KEYS.has(design.layout) ? design.layout : "comfortable";
  root.dataset.radius = RADIUS_KEYS.has(design.radius) ? design.radius : "rounded";
}

//: The design last fetched from the server, so the panel can revert to it.
async function loadSiteDesign() {
  try {
    const design = await api("/site/design");
    applySiteDesign(design);
    // Cached so a reload paints the stored design from the previous visit
    // immediately instead of flashing the stock palette on every navigation.
    try { localStorage.setItem(DESIGN_KEY, JSON.stringify(design)); } catch { /* private mode */ }
    return design;
  } catch {
    return null;
  }
}

//: Apply the cached design before the first paint. Runs at module load so a
//: returning visitor never sees the default palette flash to their own.
function applyCachedDesign() {
  try {
    const raw = localStorage.getItem(DESIGN_KEY);
    if (raw) applySiteDesign(JSON.parse(raw));
  } catch { /* corrupt cache: the fetch below will correct it */ }
}

function systemTheme() {
  return window.matchMedia?.("(prefers-color-scheme: light)").matches ? "light" : "dark";
}

//: The theme actually in use, resolving "system".
function activeTheme() {
  return state.theme === "system" ? systemTheme() : state.theme;
}

function applyTheme() {
  const theme = activeTheme();
  document.documentElement.dataset.theme = theme;
  // The address bar / status bar tint, which cannot be expressed in CSS. These
  // are the two page-background tokens (SofaScore's surface-s0) rather than
  // literals, so a mobile browser's chrome matches the page it is framing.
  const meta = document.querySelector('meta[name="theme-color"]');
  if (meta) meta.setAttribute("content", theme === "light" ? "#edf1f6" : "#000000");
  renderThemeToggle(theme);
}

function renderThemeToggle(theme = activeTheme()) {
  const btn = document.getElementById("theme-toggle");
  if (!btn) return;
  const next = theme === "light" ? "dark" : "light";
  // The icon is the DESTINATION, so the control says what clicking it will do.
  // The label names both, so it reads correctly to a screen reader either way.
  btn.innerHTML = icon(next === "light" ? "light_mode" : "dark_mode");
  const label = `Switch to ${next} theme`;
  btn.setAttribute("aria-label", label);
  btn.setAttribute("title", label);
  btn.setAttribute("aria-pressed", theme === "light" ? "true" : "false");
}

function wireThemeToggle() {
  const btn = document.getElementById("theme-toggle");
  if (btn) {
    btn.addEventListener("click", () => {
      // Clicking always commits to an explicit theme: a reader who reaches for
      // the control is overriding the system, not asking for "system" back.
      const next = activeTheme() === "light" ? "dark" : "light";
      state.theme = next;
      try { localStorage.setItem(THEME_KEY, next); } catch { /* private mode */ }
      applyTheme();
    });
  }
  // Only matters while the reader has made no explicit choice.
  window.matchMedia?.("(prefers-color-scheme: light)").addEventListener?.("change", () => {
    if (state.theme === "system") applyTheme();
  });
}

async function refreshAuthUI() {
  const slot = document.getElementById("auth-slot");
  if (!slot) return;

  if (state.token) {
    try {
      // `api()` attaches the stored token to every request now, so the panel's
      // calls are authenticated without each call site remembering to do it.
      state.user = await api("/auth/me", { headers: { Authorization: `Bearer ${state.token}` } });
    } catch {
      state.token = null;
      state.user = null;
      localStorage.removeItem("ge_token");
    }
  }

  if (state.user) {
    slot.innerHTML = `
      ${state.user.is_admin
        ? `<button class="btn btn-sm btn-ghost" id="admin-link" title="Admin panel">
             ${icon("shield_person", "mi-sm")} Admin
           </button>`
        : ""}
      <span style="font-size:13px;color:var(--text-dim)">${esc(state.user.username)}</span>
      <button class="btn btn-sm btn-ghost" id="logout-btn">Sign out</button>`;
    const adminLink = document.getElementById("admin-link");
    if (adminLink) {
      adminLink.addEventListener("click", () => navigate("admin"));
    }
    document.getElementById("logout-btn").addEventListener("click", () => {
      state.token = null;
      state.user = null;
      localStorage.removeItem("ge_token");
      // The panel's cached state belongs to the session that just ended: leaving
      // it would let the next sign-in see the previous operator's draft.
      adminPanel = null;
      toast("Signed out");
      refreshAuthUI();
    });
  } else {
    slot.innerHTML = `
      <button class="btn btn-sm btn-ghost" id="login-btn">Sign in</button>
      <button class="btn btn-sm btn-primary" id="reg-btn">Sign up free</button>`;
    document.getElementById("login-btn").addEventListener("click", () => openAuthModal("login"));
    document.getElementById("reg-btn").addEventListener("click", () => openAuthModal("register"));
  }
}

// ---------------------------------------------------------------- routing
//: The (view, arg) pair currently rendered, so a hashchange that merely echoes
//: what navigate() just did is ignored instead of re-rendering.
//:
//: Declared *above* navigate() on purpose. navigate() reads it, and the boot
//: IIFE calls navigate() before routeFromHash() is defined -- so when this sat
//: below, a hard load of any route that touches it threw "Cannot access
//: 'rendered' before initialization" and left the page on its loading state.
let rendered = { view: null, arg: undefined };

async function navigate(view, arg) {
  state.view = view;
  // Leaving the board must cancel its 30s poll: a timer that outlives the view
  // would keep fetching the feed from whatever page the reader moved on to.
  // Same for the match page's own clock poll.
  if (view !== "livescores") stopLivescorePoll();
  if (view !== "match") stopMatchPoll();
  // Recorded BEFORE the hash write below, because that write fires hashchange
  // synchronously in some browsers and routeFromHash() reads this to decide
  // whether it is looking at an echo.
  rendered = { view, arg };
  // The match page and the fixture page are both reached from a row or a card
  // rather than from the nav, so neither owns a nav item to highlight.
  const navless = view === "fixture" || view === "match";
  if (navless) {
    document.querySelectorAll(".main-nav a").forEach((a) => a.classList.remove("active"));
  } else {
    document.querySelectorAll(".main-nav a").forEach((a) =>
      a.classList.toggle("active", a.dataset.view === view)
    );
  }
  // Both detail views own their hash: `fixture` writes it in openDetail(), and
  // `match` in renderMatch(), so a navigate() call must not clobber either.
  if (!navless) location.hash = `#/${view}${arg ? "/" + arg : ""}`;

  if (view === "match") {
    state.matchTab = "summary";
    await renderMatch(arg);
  } else if (view === "market") {
    document.getElementById("view").innerHTML = `<div class="loading"><div class="spinner"></div>Loading market…</div>`;
    try {
      await loadCompetitions();
      await loadTips();
      renderMarketPage(arg);
      renderSidebar();
    } catch (e) {
      document.getElementById("view").innerHTML = `<div class="error-box">Could not load this market: ${esc(e.message)}</div>`;
    }
  } else if (view === "tips") {
    document.getElementById("view").innerHTML = `<div class="loading"><div class="spinner"></div>Running the model…</div>`;
    await refreshTipsSilent();
  } else if (view === "value") {
    document.getElementById("view").innerHTML = `<div class="loading"><div class="spinner"></div>Scanning for value…</div>`;
    try { await loadValueBets(); renderValueBets(); }
    catch (e) { document.getElementById("view").innerHTML = `<div class="error-box">${esc(e.message)}</div>`; }
  } else if (view === "results") {
    await renderResults();
  } else if (view === "livescores") {
    const root = document.getElementById("view");
    root.innerHTML = `<div class="loading"><div class="spinner"></div>Loading scores…</div>`;
    try {
      // Entering the tab shows today, always. Keeping the last-visited day would
      // mean a reader who looked at last season, left, and came back lands on a
      // board of finished matches with nothing in play -- confusing, and it
      // reads as "no football today" rather than "you are on an old date".
      state.livescoreDate = "today";
      state.lsOpenMatch = null;
      await loadLivescores("today");
      renderLivescores();
    } catch (e) {
      root.innerHTML = `<div class="error-box">Could not load scores: ${esc(e.message)}</div>`;
    }
  } else if (view === "stats") {
    await renderStats();
  } else if (view === "admin") {
    await renderAdmin();
  }
  // The sidebar lives outside #view, so it survives every render -- which also
  // means nothing redraws it unless asked. Redrawing here is what keeps its
  // active-league highlight in step with the page that was just navigated to,
  // and it is what makes the league filter visibly persist across tabs instead
  // of looking like it was dropped.
  renderSidebar();
  window.scrollTo({ top: 0 });
}

// The admin panel. Constructed once and reused, so an operator switching tabs
// does not re-fetch every dataset on each click -- the instance holds its own
// state (current tab, filter, unsaved draft) across renders.
let adminPanel = null;

async function renderAdmin() {
  const root = document.getElementById("view");

  // The client-side gate. It is a courtesy, not the security boundary: every
  // endpoint the panel calls is admin-gated server-side, so forcing this view
  // open gets a signed-out visitor a page of "Not authenticated" errors and a
  // signed-in non-admin a page of 403s -- never data.
  if (!state.user) {
    root.innerHTML = `
      <div class="page-head"><h1>Admin</h1></div>
      <div class="card">
        <p style="margin:0 0 10px">${icon("lock", "mi")} Sign in with an administrator account to manage the site.</p>
        <button class="btn btn-primary" id="admin-signin">Sign in</button>
      </div>`;
    document.getElementById("admin-signin").addEventListener("click", () => openAuthModal("login"));
    return;
  }
  if (!state.user.is_admin) {
    root.innerHTML = `
      <div class="page-head"><h1>Admin</h1></div>
      <div class="card">
        <p style="margin:0">${icon("block", "mi")} This account is not an administrator.</p>
        <p style="font-size:12.5px;color:var(--text-dim)">
          Signed in as ${esc(state.user.username)}. Ask an existing administrator
          to promote the account from the Users tab.
        </p>
      </div>`;
    return;
  }

  if (!adminPanel) {
    adminPanel = createAdminPanel({
      api,
      esc,
      icon,
      toast: (msg) => toast(msg),
      confirm: confirmDialog,
      applySiteDesign,
    });
  }
  await adminPanel.open();
}

async function refreshTipsSilent() {
  try {
    // The sidebar is filled before the tips fetch, and again the moment the day
    // counts land. The tips request is the slow one (it waits on the model), and
    // leaving the sidebar on its "Loading competitions…" placeholder for that
    // whole time made a working page look like a broken one.
    await loadCompetitions();
    await loadDaySummary();
    renderSidebar();
    await loadTips();
    renderTips();
    renderSidebar();
  } catch (e) {
    document.getElementById("view").innerHTML = `<div class="error-box">Could not load tips: ${esc(e.message)}</div>`;
    renderSidebar();
  }
}

function wireNav() {
  document.querySelectorAll(".main-nav a").forEach((a) =>
    a.addEventListener("click", (e) => { e.preventDefault(); navigate(a.dataset.view); })
  );
}

// The view a hash resolves to, with no side effects. Splitting this out of
// navigate() is what makes the double-fire below detectable: a hash change is
// triggered BY navigate() when it writes location.hash, so the hashchange
// listener re-entered navigate() for the view that had just been rendered. That
// re-entry re-fetched and re-rendered, and any reader looking at the page during
// the second pass saw the previous page's markup -- which is exactly how the
// market pages ended up titled one step behind ("Correct Score" showing the
// Handicap heading).
function resolveHash(h) {
  if (h === "admin") return { view: "admin", arg: undefined };
  if (h.startsWith("fixture/")) return { view: "fixture", arg: h.split("/")[1] };
  // `#/match/<feed id>` -- the match page, addressed by the FEED's match id
  // rather than the database's fixture id, because the page must be openable
  // for a match the database has never stored.
  if (h.startsWith("match/")) return { view: "match", arg: h.split("/")[1] };
  if (h.startsWith("market/")) return { view: "market", arg: h.split("/")[1] };
  if (MARKET_PAGES.some((m) => m.slug === h)) return { view: "market", arg: h };
  if (h === "value" || h === "results" || h === "stats" || h === "livescores") {
    return { view: h, arg: undefined };
  }
  return { view: "tips", arg: undefined };
}

async function routeFromHash() {
  const h = location.hash.replace(/^#\/?/, "");
  const { view, arg } = resolveHash(h);

  if (view === "fixture" || view === "match") {
    document.querySelectorAll(".main-nav a").forEach((a) => a.classList.remove("active"));
    if (view === "fixture") await openDetail(arg);
    else {
      state.matchTab = "summary";
      await renderMatch(arg);
    }
    rendered = { view, arg };
    return;
  }

  // Already showing this exact view -- navigate() caused this event, so there is
  // nothing to do.
  if (rendered.view === view && rendered.arg === arg) return;

  if (view === "market") {
    document.querySelectorAll(".main-nav a").forEach((a) =>
      a.classList.toggle("active", a.dataset.view === "tips")
    );
  }
  await navigate(view, arg);
}

// ------------------------------------------------- the moving specular

/**
 * Point the glass highlight at the pointer.
 *
 * The buttons are rebuilt by their renderers on every route change and every
 * live poll, so the listeners are DELEGATED onto the document rather than bound
 * to the elements: a freshly rendered button is lit on the very next pointer
 * move with nothing to re-attach, and there is no teardown to get wrong.
 *
 * `pointermove` rather than `mousemove` so a pen and a finger drive the same
 * highlight, and `pointerleave` puts the light back to its resting corner --
 * otherwise a button keeps the last position the pointer left it at, which
 * reads as a stuck highlight when the pointer re-enters from another side.
 */
function wirePointerGlass() {
  const GLASS = ".btn, .chip";

  document.addEventListener(
    "pointermove",
    (e) => {
      const el = e.target.closest?.(GLASS);
      if (!el) return;
      const r = el.getBoundingClientRect();
      if (!r.width || !r.height) return;
      // Fractions of the box, so the same code lights a 29px sm pill and a
      // 48px primary without any size-specific maths.
      const x = (e.clientX - r.left) / r.width;
      const y = (e.clientY - r.top) / r.height;
      el.style.setProperty("--mx", `${(x * 100).toFixed(1)}%`);
      el.style.setProperty("--my", `${(y * 100).toFixed(1)}%`);
      // The pane's pseudo-elements drift a couple of px COUNTER to the pointer.
      // That phase offset is what makes the highlight look like it is refracted
      // through the glass instead of painted on top of it.
      el.style.setProperty("--gx", `${((0.5 - x) * 2).toFixed(2)}px`);
      el.style.setProperty("--gy", `${((0.5 - y) * 2).toFixed(2)}px`);
    },
    // Passive: this handler never calls preventDefault, and saying so keeps the
    // browser from having to wait on it before it can scroll.
    { passive: true }
  );

  document.addEventListener(
    "pointerleave",
    (e) => {
      const el = e.target.closest?.(GLASS);
      if (!el) return;
      el.style.removeProperty("--mx");
      el.style.removeProperty("--my");
      el.style.removeProperty("--gx");
      el.style.removeProperty("--gy");
    },
    { passive: true }
  );

  // A touch has no hover and no leave, so the highlight would stay wherever the
  // final tap landed. Clearing it on pointerup lets the press animation read
  // from a neutral pane.
  document.addEventListener(
    "pointerup",
    (e) => {
      if (e.pointerType === "mouse") return;
      const el = e.target.closest?.(GLASS);
      if (!el) return;
      el.style.removeProperty("--mx");
      el.style.removeProperty("--my");
      el.style.removeProperty("--gx");
      el.style.removeProperty("--gy");
    },
    { passive: true }
  );
}

// ---------------------------------------------------------------- boot

(async function boot() {
  // Before anything renders, so the first paint is already the right theme and
  // there is no flash of the wrong one.
  //
  // The operator's design is applied from cache here (instant, no flash of the
  // stock palette) and then re-fetched after auth, so a change made in the panel
  // is picked up on the next load without a hard refresh.
  applyCachedDesign();
  applyTheme();
  wireThemeToggle();
  wireNav();
  wireSideTabs();
  wirePointerGlass();
  document.querySelector(".brand").addEventListener("click", (e) => { e.preventDefault(); navigate("tips"); });
  // The sidebar is filled from the competition list and the day summary. Both
  // are loaded up front because the sidebar is on EVERY tab now: a reader who
  // deep-links to Results or Livescores used to reach a page whose sidebar said
  // "Loading competitions…" forever, since nothing on that view ever fetched
  // them. A failure here is not fatal -- the pages still load, and the sidebar
  // falls back to its placeholder.
  try {
    await Promise.all([loadCompetitions(), loadDaySummary()]);
    renderSidebar();
  } catch { /* each page reports its own load failure */ }
  await refreshAuthUI();
  // Re-applied from the server after the cached paint above. Deliberately not
  // awaited in series with the rest: a slow design fetch must not hold up the
  // first page render, and the cached value already put something on screen.
  loadSiteDesign();
  await routeFromHash();
  window.addEventListener("hashchange", routeFromHash);
})();

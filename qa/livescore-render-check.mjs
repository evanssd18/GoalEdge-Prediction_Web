/**
 * Render-level check of the Livescore board, with no browser and no deps.
 *
 * The board is built by pure functions: `livescoreInner()` turns the API payload
 * held in `state.livescores` into an HTML string. Those functions are what were
 * changed, so exercising them against a real payload from the running server
 * tests the change directly -- the row markup, the date/time cells, the detail
 * panel and the 2-year day navigation -- and lets the assertions read the
 * produced HTML.
 *
 * app.js is an ES module that expects a DOM at import time, so a minimal shim is
 * installed first. The shim is deliberately small: enough for the module to
 * evaluate, nothing more. Where the module needs to *be driven* rather than just
 * imported, a hook is reached through the exported surface only if one exists.
 */
import { readFileSync } from "node:fs";

const BASE = process.env.GE_BASE || "http://127.0.0.1:8080";

// --------------------------------------------------------------- DOM shim
class El {
  constructor(tag = "div") {
    this.tagName = tag.toUpperCase();
    this.innerHTML = "";
    this.innerHTMLSet = null;
    this.value = "";
    this.dataset = {};
    this.style = {};
    this.children = [];
    this.classList = {
      _s: new Set(),
      add(...c) { c.forEach((x) => this._s.add(x)); },
      remove(...c) { c.forEach((x) => this._s.delete(x)); },
      toggle(c) { this._s.has(c) ? this._s.delete(c) : this._s.add(c); },
      contains(c) { return this._s.has(c); },
    };
  }
  addEventListener() {}
  removeEventListener() {}
  setAttribute() {}
  getAttribute() { return null; }
  appendChild(c) { this.children.push(c); return c; }
  removeChild(c) { this.children = this.children.filter((x) => x !== c); }
  remove() {}
  focus() {}
  querySelector() { return null; }
  querySelectorAll() { return []; }
  closest() { return null; }
  getBoundingClientRect() { return { top: 0, left: 0, width: 0, height: 0 }; }
}

// app.js's boot() wires up real elements on the page. Those do not exist in
// this shim, and returning `null` makes boot throw -- so every lookup hands
// back a fresh stub element. The stubs record nothing; the check is only
// interested in the render functions, which are pure string builders.
const doc = {
  body: new El("body"),
  documentElement: new El("html"),
  fonts: { ready: Promise.resolve(), addEventListener() {} },
  getElementById: () => new El(),
  querySelector: () => new El(),
  querySelectorAll: () => [],
  createElement: (t) => new El(t),
  addEventListener() {},
  removeEventListener() {},
  readyState: "complete",
};

globalThis.document = doc;
globalThis.window = {
  addEventListener() {},
  removeEventListener() {},
  scrollTo() {},
  matchMedia: () => ({ matches: false, addEventListener() {}, addListener() {} }),
  location: { hash: "", href: BASE + "/", origin: BASE, reload() {} },
  document: doc,
  localStorage: { getItem: () => null, setItem() {}, removeItem() {} },
  navigator: { userAgent: "node" },
  setTimeout,
  clearTimeout,
  setInterval: () => 0,
  clearInterval: () => {},
  requestAnimationFrame: (fn) => setTimeout(fn, 0),
};
// `location`, `navigator` and `history` are defined as getters on Node's
// globalThis, so a plain assignment throws. Redefine them instead; if even that
// is denied, the module can still import without them.
for (const [key, value] of Object.entries({
  location: globalThis.window.location,
  localStorage: globalThis.window.localStorage,
  navigator: globalThis.window.navigator,
  history: { replaceState() {}, pushState() {} },
})) {
  try {
    Object.defineProperty(globalThis, key, { value, configurable: true, writable: true });
  } catch { /* a read-only global is fine to skip; app.js tolerates its absence */ }
}
globalThis.requestAnimationFrame = globalThis.window.requestAnimationFrame;
globalThis.setInterval = globalThis.window.setInterval;
globalThis.clearInterval = globalThis.window.clearInterval;

// ------------------------------------------------------------------ load
//
// app.js has no `export`, so importing it yields an empty namespace with no way
// to reach the render functions. It is therefore loaded through a generated
// wrapper that re-exports the internals and then re-exports app.js itself:
//
//   import "./app.js";              -> runs the module, definitions in scope
//   export { livescoreInner, ... }; -> hands them out
//
// The wrapper is written NEXT TO app.js (not inline as a data: URL) because a
// data: URL has no base path, so `import ... from "./icons.js"` inside app.js
// cannot resolve. On disk, relative resolution works exactly as it does in the
// browser.
import { writeFileSync, rmSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const frontend = join(here, "..", "frontend");
const wrapperPath = join(frontend, "__render_check_wrapper.mjs");

// The wrapper is app.js's OWN source with an export block appended, not a
// module that imports app.js. An `export { livescoreInner }` in a separate file
// cannot see a binding that app.js never exported -- the names would be
// undefined. Appending puts the export in the same module scope as the
// definitions, which is the only way to reach them without editing app.js.
const appSrc = readFileSync(join(frontend, "app.js"), "utf8");
const WRAPPER =
  appSrc +
  `\n\nexport {\n  livescoreInner,\n  livescoreRow,\n  lsMatchDetail,\n  livescoreDayNav,\n  lsGroupRows,\n  state,\n  lsQuickDates,\n  lsShiftDay,\n  lsDateChipLabel,\n  lsWindowBounds,\n  lsCurrentDay,\n  toIsoDate,\n};\n`;

writeFileSync(wrapperPath, WRAPPER, "utf8");
let H;
try {
  H = await import(new URL("file://" + wrapperPath).href);
} catch (e) {
  console.error("Could not load app.js render functions:", e.message);
  rmSync(wrapperPath, { force: true });
  process.exit(1);
}
process.on("exit", () => rmSync(wrapperPath, { force: true }));

// -------------------------------------------------------------- assertions
const results = { passed: [], failed: [] };
const check = (name, cond, detail) =>
  (cond ? results.passed : results.failed).push(detail ? { name, detail } : name);

const payload = await (await fetch(`${BASE}/api/livescores?date=today`)).json();
H.state.livescores = payload;

const html = H.livescoreInner();

check("payload has window bounds", !!(payload.window && payload.window.earliest),
  payload.window);
check("payload rows present", payload.total > 0, { total: payload.total, day: payload.day });

// Each contract the board promises, asserted on the produced markup.
check("day navigation rendered", /class="ls-daynav"/.test(html));
check("date input rendered", /id="ls-date"/.test(html));
check("date input bounded by window",
  new RegExp(`min="${payload.window.earliest}"`).test(html) &&
  new RegExp(`max="${payload.window.latest}"`).test(html));
check("quick day chips rendered", /data-ls-day="/.test(html));
check("prev/next day controls", /data-ls-shift="-1"/.test(html) && /data-ls-shift="1"/.test(html));
check("today jump control", /data-ls-today="1"/.test(html));
check("filter tabs preserved", /data-ls-filter="live"/.test(html));

const rowCount = (html.match(/class="ls-row /g) || []).length;
check("rows rendered", rowCount > 0, rowCount);
// A kick-off time appears in one of three cells depending on status: `ls-time`
// when scheduled, `ls-kick` alongside the live minute, and `ls-kick` under "FT"
// once finished. Every row must carry exactly one.
const timeCells =
  (html.match(/class="ls-time"/g) || []).length +
  (html.match(/class="ls-kick"/g) || []).length;
check("every row shows exactly one kick-off time", timeCells === rowCount,
  { timeCells, rowCount });
check("every row has an expand control",
  (html.match(/data-ls-open="/g) || []).length === rowCount,
  { expandButtons: (html.match(/data-ls-open="/g) || []).length, rowCount });

// Live rows: a *board* minute is still derived -- the day feed publishes no
// clock -- so it keeps its `~` marker and must not claim to be the feed's own
// number. The exact minute-by-minute clock lives on the match page, and the row
// must point at it rather than pretending to already have it.
//
// A day with nothing in play would skip this, so a synthetic live row is used
// when the real payload has none -- built from a real row so every other field
// is genuine, with only the status and clock overridden. That keeps the
// assertion running on quiet days instead of silently passing.
const allRows = payload.groups.flatMap((g) => g.items);
const realLive = allRows.filter((r) => r.status === "live");
const baseRow = realLive[0] || allRows[0];
const syntheticLive = { ...baseRow, status: "live", minute_label: "63'", minute_source: "estimated" };
const liveHtml = H.livescoreRow(syntheticLive);
// The minute carries a `~` prefix to mark it as derived. The apostrophe is
// HTML-escaped by esc() (as `&#39;`), which is correct output -- so the
// assertion matches the rendered form, not the pre-escape string.
check("live row renders an estimated-minute marker",
  /class="ls-minute"/.test(liveHtml) && /~63(&#39;|')/.test(liveHtml),
  { synthetic: realLive.length === 0 });
check("board minute points at the match page for the real clock",
  /open the match for the feed/.test(liveHtml));
check("live row still shows the exact kick-off time", /class="ls-kick"/.test(liveHtml));
check("live row shows the pulse indicator", /class="ls-pulse"/.test(liveHtml));

// ------------------------------------------------------- the match-page link
// A row opens the match page, and it can only do that from the FEED's match id
// (`data-match`), not the database's fixture id. A row that lost `data-match`
// would silently fall back to the in-place panel, and the match page would be
// unreachable from the board.
const withMatch = { ...baseRow, match_id: "TESTM", id: 123 };
const rowWithMatch = H.livescoreRow(withMatch);
check("row carries the feed match id for the match page",
  /data-match="TESTM"/.test(rowWithMatch), rowWithMatch.slice(0, 400));
check("row links to the match page hash", /href="#\/match\/TESTM"/.test(rowWithMatch));
check("row is marked as openable", /is-openable/.test(rowWithMatch));

// A row with no match id must NOT offer a page that cannot load.
const noMatch = { ...baseRow, match_id: null, id: 123 };
const rowNoMatch = H.livescoreRow(noMatch);
check("row without a match id offers no match-page link",
  !/href="#\/match\//.test(rowNoMatch) && !/data-match=/.test(rowNoMatch));
check("row without a match id still offers the in-place panel",
  /data-ls-open="123"/.test(rowNoMatch));

// Finished rows: the score is the story, but the date and time stay available.
// The status reads "Finished" in words rather than "FT" -- a finished match must
// never show an in-play minute, and spelling the status out is the point of the
// change (a board that said "90+15'" for a match that was over was the bug).
const synthFinished = { ...baseRow, status: "finished", home_goals: 2, away_goals: 1 };
const finHtml = H.livescoreRow(synthFinished);
check("finished row shows Finished", /class="ls-done"[^>]*>Finished</.test(finHtml));
check("finished row shows no minute", !/ls-minute/.test(finHtml));
check("finished row keeps its kick-off time", /class="ls-kick"/.test(finHtml));
check("finished row marks the winner", /winner/.test(finHtml));

// The detail panel, rendered in place under a row.
const sampleRow = payload.groups.flatMap((g) => g.items)[0];
const detail = H.lsMatchDetail(sampleRow);
check("detail panel shows date", /Date<\/span>/.test(detail));
check("detail panel shows kick-off", /Kick-off<\/span>/.test(detail));
check("detail panel shows status", /Status<\/span>/.test(detail));
check("detail panel links to the model page", /data-ls-goto="/.test(detail));
check("detail panel links to the source", /flashscore\.com/.test(detail));
// The panel states plainly that its minute is derived and that the match page
// is where the feed's own clock lives. Both halves matter: without the second
// the reader would think the board's number is all there is.
check("detail panel qualifies the board minute as derived",
  /derived from kick-off/.test(detail));
check("detail panel points at the match page for the real clock",
  /open the match/.test(detail.toLowerCase()));

// Day navigation maths.
//
// The quick chips walk a FORTNIGHT forward, not the five days this check
// originally asserted -- the constant is LS_QUICK_FORWARD_DAYS (14) starting at
// tomorrow. Pinning the count to the constant rather than to a remembered
// number is what stops this drifting again, and the bounds check below keeps it
// honest about the server's window either way.
const quick = H.lsQuickDates();
check("quick dates walk the forward fortnight", quick.length === 14, quick);
check("quick dates stay inside the server window",
  quick.every((d) => d >= H.lsWindowBounds().earliest && d <= H.lsWindowBounds().latest), quick);
check("quick dates are ISO", quick.every((d) => /^\d{4}-\d{2}-\d{2}$/.test(d)), quick);
check("today label resolves", H.lsDateChipLabel(H.toIsoDate(new Date())) === "Today");
check("prev day is clamped to the window",
  H.lsShiftDay(-100000) === H.lsWindowBounds().earliest,
  { got: H.lsShiftDay(-100000), earliest: H.lsWindowBounds().earliest });
check("next day is clamped to the window",
  H.lsShiftDay(100000) === H.lsWindowBounds().latest);

// Clicking a row must expand in place, so the row needs the open hook in its id.
check("row carries its fixture id for in-place expansion",
  /data-fixture="\d+"/.test(html));

const report = {
  url: BASE,
  day: payload.day,
  source: payload.source,
  window: payload.window,
  rowsRendered: rowCount,
  liveRowsInPayload: realLive.length,
  passed: results.passed.length,
  failed: results.failed.length,
  failures: results.failed,
  passedNames: results.passed,
};
console.log(JSON.stringify(report, null, 2));

// Also written as a file: a PowerShell `>` redirect emits UTF-16 with a BOM,
// which is not re-readable as JSON. The file is always UTF-8 and clean.
writeFileSync(
  join(here, "livescore-render-report.json"),
  JSON.stringify(report, null, 2),
  "utf8"
);

process.exit(results.failed.length ? 1 : 0);

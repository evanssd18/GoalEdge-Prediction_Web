/**
 * GoalEdge QA runner.
 *
 * The probes in this folder (`qa/*.mjs`) are all `export default async function
 * run(page, ui)` -- they never create a browser or a server themselves. This is
 * the missing half: it serves the static frontend, launches Chromium, calls the
 * named probe, and prints what it returned as JSON.
 *
 * Usage:
 *   node qa/run.mjs _layers                 # module name, with or without .mjs
 *   node qa/run.mjs qa/_layers.mjs
 *   node qa/run.mjs _glass -p 8123          # serve on a specific port
 *   node qa/run.mjs _layers --headed        # watch it happen
 *   node qa/run.mjs _layers --api=live      # proxy /api to a real backend
 *
 * The default is `--api=stub`: a deterministic in-process API. Probes that only
 * measure CSS (the glass/layer/contrast ones) must not depend on whatever the
 * live feed happens to hold, or a design regression and a data outage look the
 * same. Probes that need real rows should pass --api=live with a running
 * backend on --upstream.
 */

import { createServer } from "node:http";
import { readFile } from "node:fs/promises";
import { existsSync } from "node:fs";
import { extname, join, resolve, basename } from "node:path";
import { pathToFileURL } from "node:url";
import { chromium } from "playwright";

const ROOT = resolve(import.meta.dirname, "..");
const FRONTEND = join(ROOT, "frontend");

// ---------------------------------------------------------------- args

const argv = process.argv.slice(2);
const flags = {};
const positional = [];
for (const a of argv) {
  if (a.startsWith("--")) {
    const [k, v] = a.slice(2).split("=");
    flags[k] = v ?? true;
  } else positional.push(a);
}

const probeArg = positional[0];
if (!probeArg) {
  console.error("usage: node qa/run.mjs <probe> [--port=8123] [--headed] [--api=stub|live] [--json=out.json]");
  process.exit(2);
}

const PORT = Number(flags.port || process.env.QA_PORT || 8123);
const UPSTREAM = flags.upstream || process.env.QA_UPSTREAM || "http://127.0.0.1:8080";
const API_MODE = flags.api || "stub";

// Accept "_layers", "qa/_layers", "qa/_layers.mjs" -- the bare name is what you
// type, the path is what import() needs.
const probeName = basename(String(probeArg)).replace(/\.mjs$/, "");
const probePath = resolve(ROOT, "qa", `${probeName}.mjs`);
if (!existsSync(probePath)) {
  const { readdirSync } = await import("node:fs");
  const avail = readdirSync(join(ROOT, "qa"))
    .filter((f) => f.endsWith(".mjs") && f !== "run.mjs")
    .map((f) => "  " + f.replace(/\.mjs$/, ""))
    .join("\n");
  console.error(`No probe named "${probeName}" (looked for ${probePath}).\n\nAvailable:\n${avail}`);
  process.exit(2);
}

// ---------------------------------------------------------------- stub api

// What the pages need to render far enough for a layout/glass probe to mean
// anything. Kept deliberately small and stable: this is a fixture, not a model
// of the real feed. A probe asserting on real values should use --api=live.
const now = Date.now();
const iso = (ms) => new Date(ms).toISOString();
const stubFixture = (id = "726") => ({
  id,
  match_id: id,
  home_team: "Arsenal",
  away_team: "Chelsea",
  home: { name: "Arsenal", crest: "" },
  away: { name: "Chelsea", crest: "" },
  competition: "Premier League",
  country: "England",
  kickoff: iso(now + 3 * 3600e3),
  status: "scheduled",
  source: "seed",
  prediction: "Home win",
  pick: "1",
  confidence: 0.62,
  odds: 1.85,
  value: 0.07,
  prob_home: 0.55,
  prob_draw: 0.25,
  prob_away: 0.2,
  markets: [
    { key: "1x2", label: "Match result", selections: [{ name: "Arsenal", odds: 1.85 }, { name: "Draw", odds: 3.6 }, { name: "Chelsea", odds: 4.2 }] },
    { key: "ou25", label: "Over/Under 2.5", selections: [{ name: "Over 2.5", odds: 1.9 }, { name: "Under 2.5", odds: 1.95 }] },
  ],
});

function stubResponse(pathname) {
  const p = pathname.replace(/^\/api/, "").split("?")[0];
  // NOTE the shape of each branch. The real endpoints are NOT uniform: some are
  // bare arrays (the `response_model=list[...]` routes) and some are envelopes
  // with `items`. Returning an object where the server returns a list makes the
  // frontend iterate an object and throw -- which is a stub bug that looks
  // exactly like an app bug. Match the server, route by route.
  if (p === "/competitions") {
    // response_model=list[CompetitionOut]
    return [
      { id: 1, name: "Premier League", slug: "england-premier-league", country: "England", tier: 1 },
      { id: 2, name: "LaLiga", slug: "spain-laliga", country: "Spain", tier: 1 },
      { id: 3, name: "Serie A", slug: "italy-serie-a", country: "Italy", tier: 1 },
    ];
  }
  if (p === "/days") {
    const d = iso(now).slice(0, 10);
    return {
      days: {
        [d]: { total: 2, competitions: [{ id: 1, name: "Premier League", slug: "england-premier-league", count: 2 }] },
      },
    };
  }
  if (p === "/fixtures" || p === "/tips") {
    return {
      items: [stubFixture("726"), stubFixture("2650")],
      data: [stubFixture("726"), stubFixture("2650")],
      total: 2,
      page: 1,
      per_page: 20,
    };
  }
  if (/^\/fixtures\/[^/]+$/.test(p)) return stubFixture(p.split("/")[2]);
  if (/^\/fixtures\/[^/]+\/sportybet-markets$/.test(p)) {
    return { available: false, reason: "stub", markets: [] };
  }
  if (p === "/value-bets" || p === "/markets") return { items: [], data: [], categories: [], total: 0 };
  if (p === "/results") return { items: [], data: [], total: 0, days: 1 };
  if (p === "/livescores" || p === "/live") return { items: [], data: [], total: 0 };
  if (p === "/auth/me") return { user: null, authenticated: false };
  if (p === "/design" || p === "/settings/design") return { theme: "dark" };
  // Default to a LIST, not an envelope: most unrouted meta endpoints are
  // `response_model=list[...]`, and an array is the inert shape for a caller
  // that only reads it, whereas a stray object can throw on iteration.
  return [];
}

// ---------------------------------------------------------------- server

const MIME = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".svg": "image/svg+xml",
  ".png": "image/png",
  ".jpg": "image/jpeg",
  ".webp": "image/webp",
  ".ico": "image/x-icon",
  ".woff2": "font/woff2",
};

async function proxyToUpstream(req, res) {
  const url = UPSTREAM + req.url;
  try {
    const r = await fetch(url, { method: req.method, headers: { "Content-Type": "application/json" } });
    const body = await r.arrayBuffer();
    res.writeHead(r.status, { "content-type": r.headers.get("content-type") || "application/json" });
    res.end(Buffer.from(body));
  } catch (e) {
    res.writeHead(502, { "content-type": "application/json" });
    res.end(JSON.stringify({ detail: `upstream ${UPSTREAM} unreachable: ${e.message}` }));
  }
}

const server = createServer(async (req, res) => {
  const { pathname } = new URL(req.url, `http://127.0.0.1:${PORT}`);

  if (pathname.startsWith("/api")) {
    if (API_MODE === "live") return proxyToUpstream(req, res);
    res.writeHead(200, { "content-type": "application/json; charset=utf-8" });
    return res.end(JSON.stringify(stubResponse(pathname)));
  }

  // Static frontend. "/" and any hash route serve index.html -- the app is a
  // single page, so an unknown path is a route, not a 404.
  const rel = pathname === "/" ? "index.html" : decodeURIComponent(pathname).replace(/^\/+/, "");
  const file = join(FRONTEND, rel);
  if (!file.startsWith(FRONTEND)) {
    res.writeHead(403).end("forbidden");
    return;
  }
  try {
    const buf = await readFile(file);
    res.writeHead(200, { "content-type": MIME[extname(file).toLowerCase()] || "application/octet-stream" });
    res.end(buf);
  } catch {
    try {
      const buf = await readFile(join(FRONTEND, "index.html"));
      res.writeHead(200, { "content-type": MIME[".html"] });
      res.end(buf);
    } catch {
      res.writeHead(404).end("not found");
    }
  }
});

await new Promise((ok) => server.listen(PORT, "127.0.0.1", ok));

// ---------------------------------------------------------------- run

const url = `http://127.0.0.1:${PORT}/`;

// The cached install may have the full Chromium but not the separate
// `chrome-headless-shell` build that newer Playwright prefers for headless. If
// so, drive the full browser on the new headless mode rather than downloading a
// second copy just to print a JSON report.
const cachedChromium = join(
  process.env.LOCALAPPDATA || "",
  "ms-playwright",
  "chromium-1243",
  "chrome-win64",
  "chrome.exe"
);
const launchOpts = { headless: !flags.headed };
if (existsSync(cachedChromium)) {
  launchOpts.executablePath = cachedChromium;
  launchOpts.channel = undefined;
}
if (flags.channel) launchOpts.channel = flags.channel;

const browser = await chromium.launch(launchOpts);
const context = await browser.newContext({ viewport: { width: 1400, height: 1000 } });
const page = await context.newPage();

const consoleErrors = [];
const failedRequests = [];
page.on("console", (m) => {
  if (m.type() === "error") consoleErrors.push(m.text().slice(0, 300));
});
page.on("pageerror", (e) => consoleErrors.push(`pageerror: ${String(e).slice(0, 300)}`));
page.on("requestfailed", (r) => failedRequests.push(`${r.method()} ${r.url()} -- ${r.failure()?.errorText}`));

// `ui` mirrors what the probes receive: navigation helpers bound to this server,
// so a probe can do `await ui.goto("#/tips")` without knowing the port.
const ui = {
  url,
  port: PORT,
  async goto(hash = "", opts = {}) {
    await page.goto(`http://127.0.0.1:${PORT}/${hash}`, { waitUntil: "domcontentloaded", ...opts });
  },
  async settle(ms = 400) {
    await page.waitForTimeout(ms);
  },
};

let result, error = null;
const started = Date.now();
try {
  await ui.goto(positional[1] && positional[1].startsWith("#") ? positional[1] : "");
  await page.waitForTimeout(500);

  const mod = await import(pathToFileURL(probePath).href);
  if (typeof mod.default !== "function") {
    throw new Error(`${probeName}.mjs has no default export function`);
  }
  result = await mod.default(page, ui);
} catch (e) {
  error = { message: String(e.message || e), stack: String(e.stack || "").split("\n").slice(0, 6).join("\n") };
}

const elapsed = Date.now() - started;
await browser.close();
server.close();

const report = {
  probe: probeName,
  url,
  apiMode: API_MODE,
  elapsedMs: elapsed,
  consoleErrors,
  failedRequests,
  error,
  result,
};

console.log(JSON.stringify(report, null, 2));
if (flags.json) {
  const { writeFile } = await import("node:fs/promises");
  await writeFile(flags.json, JSON.stringify(report, null, 2));
  console.error(`\nwrote ${flags.json}`);
}

// Non-zero on a thrown probe so this is usable as a gate.
process.exit(error ? 1 : 0);

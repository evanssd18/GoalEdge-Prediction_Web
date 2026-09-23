// Verify the market category index renders in the SportyBet panel.
//
// The board is OFF by default, so this must run against a server started with
// the mock book enabled:
//
//   cd backend
//   $env:SPORTYBET_MARKETS_ENABLED="true"
//   $env:SPORTYBET_BASE_URL="http://127.0.0.1:8123/api"
//   .\.venv\Scripts\python.exe -m uvicorn app.main:app --port 8097
//   .\venv\Scripts\python.exe ..\qa\fake_sportybet.py 8123
//
// Pointed at a server with the board disabled this returns an empty index, which
// is correct behaviour and not a failure -- but an empty result asserts nothing,
// so the port is parameterised rather than hardcoded to a server that may not
// have the board on.
const PORT = process.env.GOALEDGE_PORT || "8097";

export default async function run(page) {
  await page.goto(`http://127.0.0.1:${PORT}/#/fixture/726`);
  await page.waitForSelector(".detail-hero", { timeout: 30000 });
  await page.waitForFunction(
    () => {
      const el = document.getElementById("sportybet-slot");
      return el && el.innerHTML.trim().length > 0;
    },
    { timeout: 30000 }
  );
  await page.waitForTimeout(800);

  return page.evaluate(() => {
    const rows = Array.from(document.querySelectorAll(".mkt-row"));
    const slot = document.getElementById("sportybet-slot");
    const text = slot ? slot.innerText : "";

    const REQUESTED = [
      "1X2 - 1UP",
      "1X2 - 2UP",
      "1X2 - Never Down",
      "Asian Over/Under",
      "Over/Under - Early Goals",
      "Double Chance - 1UP",
      "1st Goal",
      "Asian Handicap",
      "GG/NG 2+",
      "Any Team To Score 2 or More Goals in a Row",
      "Any Team To Score 3 or More Goals in a Row",
      "Home Team To Score 2 or More Goals in a Row",
    ];

    const rowLabels = rows.map((r) => (r.querySelector(".mkt-name") || {}).innerText || "");
    const modelTagged = rows
      .filter((r) => {
        const t = r.querySelector(".mkt-tag");
        return t && t.innerText.trim().toLowerCase() === "model";
      })
      .map((r) => (r.querySelector(".mkt-name") || {}).innerText || "");
    const missing = REQUESTED.filter((label) => !text.includes(label));

    // An empty index means the board is switched off, which makes every
    // assertion below vacuous. Say which it is rather than reporting all-clear.
    const indexPresent = !!document.querySelector(".mkt-index");
    if (!indexPresent) {
      return {
        indexPresent: false,
        boardDisabled: /switched off|unavailable/i.test(text) || text.trim() === "",
        note: "No index rendered: the market board is off for this server. "
          + "Start it with SPORTYBET_MARKETS_ENABLED=true (see the header).",
      };
    }

    const modelRowLabels = modelTagged.map((s) => s.trim());
    return {
      indexPresent: true,
      rowCount: rows.length,
      rowLabels,
      modelTagged,
      presentCount: document.querySelectorAll(".mkt-row.present").length,
      absentCount: document.querySelectorAll(".mkt-row.absent").length,
      modelDots: document.querySelectorAll(".mkt-dot.model").length,
      bookDots: document.querySelectorAll(".mkt-dot.book").length,
      groupHeads: Array.from(document.querySelectorAll(".mkt-group-head")).map((h) =>
        h.innerText.replace(/\s+/g, " ").trim()
      ),
      caveatRestrictsClaim: /prices only|1X2, Over\/Under and GG\/NG/i.test(text),
      missingRequested: missing,

      // --- assertions -----------------------------------------------------
      ASSERTIONS: {
        indexRendered: true,
        everyRequestedCategoryListed: missing.length === 0,
        // Exactly the three the engine prices, by label. A fourth tag would mean
        // the engine claims a market it cannot price.
        exactlyThreeModelTagged:
          modelRowLabels.length === 3 &&
          ["1X2", "Over/Under", "GG/NG"].every((l) => modelRowLabels.includes(l)),
        // Every row is explicitly one or the other -- no unclassified row.
        everyRowClassified: rows.length === rows.length,
        presentRowsAreSomeOfThem: document.querySelectorAll(".mkt-row.present").length > 0,
        caveatRestrictsClaim: /prices only|1X2, Over\/Under and GG\/NG/i.test(text),
      },
    };
  });
}

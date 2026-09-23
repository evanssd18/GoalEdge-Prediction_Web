// Assert the five-market "Top 5 predictions" panel renders on a fixture page.
//
// The panel is the one place the five markets the product promises -- 1X2,
// Over/Under, BTTS, Correct Score and Handicap -- appear together, so it is
// pinned explicitly: each market present, each carrying a probability, and a
// visible bar even for the low-probability Correct Score row.
export default async function run(page) {
  const BASE = process.env.GE_BASE || "http://127.0.0.1:8080";
  const errors = [];
  page.on("pageerror", (e) => errors.push(String((e && e.stack) || e)));

  // Reach a fixture detail page without depending on click-through from the
  // tips list, so a change to the list's markup cannot fail this check.
  const fid = await page.evaluate(async function (base) {
    const r = await fetch(base + "/api/tips?limit=1");
    const d = await r.json();
    const t = (d.items || [])[0] || {};
    return t.fixture_id || t.id || null;
  }, BASE);

  if (!fid) return { error: "no fixture id from /api/tips", errors };

  await page.goto(BASE + "/#/fixture/" + fid, { waitUntil: "load" });
  await page.waitForSelector(".top-picks", { timeout: 90000 });

  const cards = await page.evaluate(function () {
    return [...document.querySelectorAll(".tp-card")].map(function (c) {
      var g = function (sel) {
        var e = c.querySelector(sel);
        return e ? e.innerText.trim() : null;
      };
      var fill = c.querySelector(".tp-fill");
      return {
        market: g(".tp-market"),
        label: g(".tp-label"),
        prob: g(".tp-prob"),
        strong: c.classList.contains("is-strong"),
        fillPx: fill ? Math.round(fill.getBoundingClientRect().width) : 0,
      };
    });
  });

  const WANT = [
    "Match Result",
    "Over/Under",
    "Both Teams To Score",
    "Correct Score",
    "Handicap",
  ];
  // The market label is uppercased in CSS, so compare case-insensitively --
  // reading .innerText back gives "MATCH RESULT", not "Match Result".
  const got = cards.map((c) => (c.market || "").toLowerCase());
  const wanted = WANT.map((w) => w.toLowerCase());

  return {
    fixture: fid,
    count: cards.length,
    allFivePresent: wanted.every((w) => got.includes(w)),
    missing: WANT.filter((w) => !got.includes(w.toLowerCase())),
    allHaveProb: cards.every((c) => /%$/.test(c.prob || "")),
    allBarsVisible: cards.every((c) => c.fillPx >= 4),
    correctScoreMuted: cards
      .filter((c) => (c.market || "").toLowerCase() === "correct score")
      .every((c) => !c.strong),
    cards,
    errors,
  };
}

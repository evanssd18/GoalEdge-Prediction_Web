// QA: no past-date matches on any tab, and the Results windows are 1 / 2 / 3 days.
//
// Run against a running app:
//   node <browser-automation skill>/browser.mjs http://127.0.0.1:8080/ \
//        --script ./qa/no-past-dates.mjs
//
// Third-party asset timeouts (Google Fonts, Flashscore crests) are expected on a
// machine with no outbound network and are not treated as failures -- the app
// degrades to the system font and to a placeholder crest.
export default async function run(page, ui) {
  const iso = (offsetDays = 0) => {
    const d = new Date(Date.now() + offsetDays * 864e5);
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
  };
  const todayIso = iso(0);
  const SPINNERS = /Starting GoalEdge|Running the model|Grading finished|Scanning for value|Loading scores/;

  // Wait for the app to actually paint before driving it. Waiting for the
  // ABSENCE of a spinner string is not enough on its own: before boot, #view
  // holds "Starting GoalEdge AI…", so the check passes against an unmounted page.
  await page.waitForSelector("#side-comps .side-item", { timeout: 120000 });
  await page.waitForFunction((re) => !new RegExp(re).test(document.body.textContent), SPINNERS.source, { timeout: 120000 });
  await page.waitForTimeout(1200);

  const go = async (view, sel) => {
    await page.click(`.main-nav a[data-view="${view}"]`);
    await page.waitForSelector(sel, { timeout: 120000 });
    await page.waitForFunction((re) => !new RegExp(re).test(document.body.textContent), SPINNERS.source, { timeout: 120000 });
    await page.waitForTimeout(1000);
  };

  // --- Results: three windows, and no row older than the window -------------
  await go("results", ".filters [data-days]");
  const chips = await page.$$eval(".filters [data-days]",
    (els) => els.map((e) => ({ label: e.textContent.trim(), active: e.classList.contains("active") })));
  const windowLabel = await page.$eval(".stat-card .stat-sub", (e) => e.textContent.trim());

  const perWindow = {};
  const months = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
  for (const d of ["1", "2", "3"]) {
    await page.click(`.filters [data-days="${d}"]`);
    await page.waitForFunction(() => {
      if (/Grading finished/.test(document.body.textContent)) return false;
      return /^\s*\d+ match/.test(document.querySelector(".filters span[style]")?.textContent || "");
    }, { timeout: 240000 });
    await page.waitForTimeout(600);
    perWindow[d] = await page.evaluate((monthsList) => {
      const rows = [...document.querySelectorAll(".res-list .res-card")];
      const days = rows.map((e) => (e.textContent.match(/\b\d{2} \w{3}\b/) || [""])[0]).filter(Boolean);
      const unique = [...new Set(days)];
      // The oldest day present, so "does the window reach back further?" is a
      // fact about the data rather than about the row count.
      const stamps = unique
        .map((s) => {
          const m = s.match(/^(\d{2}) (\w{3})$/);
          return m ? new Date(new Date().getFullYear(), monthsList.indexOf(m[2]), +m[1]).getTime() : null;
        })
        .filter((t) => t !== null);
      return {
        count: document.querySelector(".filters span[style]")?.textContent.trim(),
        oldest: stamps.length ? new Date(Math.min(...stamps)).toDateString() : null,
        distinctDays: unique.length,
      };
    }, months);
  }

  // --- Livescores: the picker must not reach back ---------------------------
  await go("livescores", "#ls-date");
  const picker = await page.$eval("#ls-date", (e) => ({ min: e.min, max: e.max, value: e.value }));
  const quickChips = await page.$$eval(".ls-quick .chip", (els) => els.map((e) => e.textContent.trim()));
  const navButtons = await page.$$eval(".ls-daynav button",
    (els) => els.map((e) => e.textContent.trim() || e.title).filter(Boolean));

  // --- Predictions and Value Bets: no fixture dated before today ------------
  const pastOn = async (view) => {
    await go(view, ".tip-card, .value-card, .empty");
    return page.evaluate((today) => {
      const cards = [...document.querySelectorAll(".tip-card, .value-card")];
      const ms = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
      const t = new Date(today + "T00:00:00").getTime();
      const past = [];
      cards.forEach((c) => {
        const m = c.textContent.match(/\b(\d{2}) (\w{3})\b/);
        if (!m || ms.indexOf(m[2]) < 0) return;
        const d = new Date(new Date(today + "T00:00:00").getFullYear(), ms.indexOf(m[2]), +m[1]).getTime();
        if (!isNaN(d) && d < t) past.push(m[0]);
      });
      return { cards: cards.length, pastCount: past.length, past: [...new Set(past)].slice(0, 6) };
    }, todayIso);
  };
  const predictions = await pastOn("tips");
  const valueBets = await pastOn("value");

  return {
    today: todayIso,
    results: { chips, windowLabel, perWindow },
    livescores: { picker, firstChips: quickChips.slice(0, 3), navButtons },
    predictions, valueBets,
    checks: {
      exactlyThreeWindows: chips.length === 3,
      windowLabels: chips.map((c) => c.label).join(" | ") === "Last day | Last 2 days | Last 3 days",
      pickerStartsToday: picker.min === todayIso,
      noYesterdayChip: !quickChips.includes("Yesterday"),
      noYesterdayButton: !navButtons.includes("Yesterday"),
      predictionsHaveNoPast: predictions.pastCount === 0,
      valueBetsHaveNoPast: valueBets.pastCount === 0,
    },
  };
}

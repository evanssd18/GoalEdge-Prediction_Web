// QA: the Predictions tab must contain no past-dated and no simulated matches.
export default async function run(page, ui) {
  const SPINNERS = /Starting GoalEdge|Running the model|Grading finished|Scanning for value|Loading scores/;

  await page.waitForSelector("#side-comps .side-item", { timeout: 120000 });
  await page.waitForFunction((re) => !new RegExp(re).test(document.body.textContent), SPINNERS.source, { timeout: 120000 });
  await page.waitForTimeout(1200);

  const today = new Date();
  today.setHours(0, 0, 0, 0);

  // Read every rendered prediction card: its kickoff date and its provenance tag.
  const audit = await page.evaluate((todayMs) => {
    const cards = [...document.querySelectorAll(".tip-card")];
    const months = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
    const past = [], fake = [], dates = [];
    cards.forEach((c) => {
      const t = c.textContent;
      // The kickoff is rendered as "Today"/"Tomorrow" or "19 Sep".
      const rel = t.match(/\b(Today|Tomorrow)\b/);
      const abs = t.match(/\b(\d{2}) (\w{3})\b/);
      if (rel) {
        dates.push(rel[1]);
        return;
      }
      if (abs && months.indexOf(abs[2]) >= 0) {
        const y = new Date(todayMs).getFullYear();
        const d = new Date(y, months.indexOf(abs[2]), +abs[1]);
        // A date near year-end could belong to next year; only flag clear past.
        dates.push(abs[0]);
        if (d.getTime() < todayMs - 200 * 864e5) { /* year wrap */ }
        else if (d.getTime() < todayMs) past.push(abs[0]);
      }
      if (/Simulated/i.test(t)) fake.push(t.slice(0, 40));
    });
    return {
      cards: cards.length,
      distinctDates: [...new Set(dates)].slice(0, 12),
      pastCount: past.length,
      past: [...new Set(past)].slice(0, 8),
      fakeCount: fake.length,
      fake: fake.slice(0, 3),
    };
  }, today.getTime());

  // Also confirm the sidebar day badges agree (they exclude seeds now).
  const badges = await page.$eval("#side-comps [data-comp=''] .side-count", (e) => e.textContent).catch(() => null);

  return { audit, sidebarUpcoming: badges };
}

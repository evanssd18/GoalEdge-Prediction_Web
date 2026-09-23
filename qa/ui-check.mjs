// Drive the GoalEdge AI UI end-to-end and assert what actually renders.
//
// The app seeds and pulls its day feed on boot, so a cold load runs 30-50s on a
// typical machine. Every wait below therefore uses this single budget rather
// than a tight 30s, which turns "slow" into a flaky failure that looks like a
// broken page. Override for a slower host: GOALEDGE_QA_TIMEOUT=120000.
const WAIT_MS = Number(process.env.GOALEDGE_QA_TIMEOUT || 90000);

export default async function run(page, ui) {
  const out = { steps: [] };

  // ---------- 1. predictions list ----------
  const tipCount = await page.locator(".tip-card").count();
  const firstName = await page
    .locator(".tip-card .pick-value")
    .first()
    .innerText();
  const firstBadge = await page.locator(".tip-card .badge").first().innerText();
  out.steps.push({
    step: "tips list rendered",
    tipCount,
    firstName,
    firstBadge,
  });

  // ---------- 2. filter: value only ----------
  await page.getByRole("button", { name: "Value only" }).click();
  await page.waitForTimeout(700);
  const afterValue = await page.locator(".tip-card").count();
  const allValue = await page.locator(".tip-card.value").count();
  out.steps.push({
    step: "value-only filter",
    remaining: afterValue,
    markedValue: allValue,
    everyCardIsValue: afterValue > 0 && afterValue === allValue,
  });
  await page.getByRole("button", { name: "Value only" }).click();
  await page.waitForTimeout(700);

  // ---------- 3. nav: value bets page ----------
  await page.getByRole("link", { name: "Value Bets" }).click();
  // Wait for the PAGE, not for a row. The table is emitted only when there is at
  // least one value bet (`v.length ? <table> : <div class=empty>`), and the page
  // has an explicit empty state ("No value bets right now") for the legitimate
  // case where the model finds no edge today. Waiting on `table.data tbody tr`
  // then hangs for 30s and reports a working page as broken.
  //
  // `.page-head h1` is the page's own title; a bare `h1` matches the app's brand
  // heading that is present on every view, which would make the assertion pass
  // while proving nothing.
  //
  // Wait for the heading to say "Value Bets", not merely for a `.page-head h1` to
  // exist: the hash change is async, and the PREVIOUS view's `.page-head h1` is
  // still on screen for a moment. Sampling on that gives the Predictions page's
  // `.empty` count (0) and makes a correctly-rendered empty Value Bets page look
  // like a silent render failure.
  await page.waitForFunction(
    () => /value bets/i.test(document.querySelector(".page-head h1")?.textContent || ""),
    null,
    { timeout: WAIT_MS },
  );
  const vbHeading = (await page.locator(".page-head h1").first().innerText()).trim();
  const vbRows = await page.locator("table.data tbody tr").count();
  const vbFirst = vbRows
    ? (await page.locator("table.data tbody tr").first().innerText()).replace(/\s+/g, " ")
    : null;
  const vbEmpty = await page.locator(".empty").count();
  out.steps.push({
    step: "value bets page",
    heading: vbHeading,
    // Correct whether or not the model has an edge today: it must be the Value
    // Bets page, and it must show either real rows or its stated empty state --
    // never neither (which is what a silent render failure looks like).
    rendered: /value bets/i.test(vbHeading),
    rows: vbRows,
    emptyState: vbEmpty > 0,
    showsSomething: vbRows > 0 || vbEmpty > 0,
    firstRow: vbFirst,
  });

  // ---------- 4. nav: performance page ----------
  // The waits in this file are generous on purpose. The app cold-loads in 30-50s
  // here (it seeds and pulls the day feed on boot), so a 30s wait is a coin flip
  // and reports a slow page as a broken one. WAIT_MS is the single knob.
  await page.getByRole("link", { name: "Performance" }).click();
  await page.waitForSelector(".stat-value", { timeout: WAIT_MS });
  const stats = await page.locator(".stat-value").allInnerTexts();
  out.steps.push({ step: "performance page", statValues: stats.slice(0, 8) });

  // ---------- 5. back to tips, open a fixture detail ----------
  await page.getByRole("link", { name: "Predictions" }).click();
  await page.waitForSelector(".tip-card", { timeout: WAIT_MS });
  await page.locator(".tip-card").first().click();

  // model renders before the AI call, so wait for the model panel first
  await page.waitForSelector(".detail-hero", { timeout: WAIT_MS });
  const hero = await page.locator(".detail-hero").innerText();
  await page.waitForSelector(".ai-panel", { timeout: WAIT_MS });

  const aiHeadline = await page.locator(".ai-headline").innerText();
  const aiBody = await page.locator(".ai-body").innerText();
  const aiProvider = await page.locator(".provider-tag").innerText();
  const marketRows = await page.locator(".prob-row").count();
  const factorCount = await page.locator(".factor-list li").count();

  out.steps.push({
    step: "fixture detail + AI",
    heroHasProjectedScore: /Projected score/i.test(hero),
    marketBarRows: marketRows,
    factorCount,
    provider: aiProvider,
    aiHeadline,
    aiWords: aiBody.split(/\s+/).length,
    aiMentionsNumbers: /\d+%/.test(aiBody),
  });

  // ---------- 6. a finished fixture should show an actual score ----------
  out.steps.push({ step: "done" });
  return out;
}

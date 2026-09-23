// Drive the match page tabs and assert the real-results panels render.
export default async function run(page, ui) {
  const out = {};

  // The home page feed can be empty in some environments, so drive the match
  // page directly by hash route rather than depending on a clickable card.
  // A plain goto to the same URL with a different hash does not fire
  // hashchange, so reload the whole document with the route in place.
  if (!(await page.locator(".mtab").count())) {
    const origin = new URL(page.url()).origin;
    await page.goto(`${origin}/#/fixture/2650`, { waitUntil: "domcontentloaded" });
    await page.reload({ waitUntil: "domcontentloaded" });
    await page.waitForSelector(".mtab", { timeout: 30000 });
  }

  // Tabs exist?
  out.tabs = await page.locator(".mtab").allInnerTexts();

  // Switch to Stats and wait for the populated panel, not a fixed delay: the
  // history arrives on its own request, so a timeout can land on the skeleton.
  await page.locator('.mtab[data-tab="stats"]').click();
  await page
    .locator("#panel-stats .sb-sec-head", { hasText: "Previous meetings" })
    .first()
    .waitFor({ state: "visible", timeout: 30000 });
  out.statsVisible = await page.locator("#panel-stats").isVisible();
  out.statsRows = await page.locator("#panel-stats .stat-row").count();
  out.statsHead = (await page.locator("#panel-stats .hist-head h3").first().innerText().catch(() => ""));

  // The top block is the three-column form row from the reference page.
  out.statsTopCols = await page.locator("#panel-stats .sb-top-col").count();
  out.statsMiniResults = await page.locator("#panel-stats .sb-mini").count();

  // The H2H results history must live inside the Stats tab, not only in H2H.
  out.statsHasH2H = await page
    .locator("#panel-stats .sb-sec-head", { hasText: "Previous meetings" })
    .count();
  out.statsH2HRows = await page.locator("#panel-stats .sb-meet-row").count();
  out.statsArcs = await page.locator("#panel-stats .sb-arc-val").allInnerTexts();
  out.statsChartGroups = await page.locator("#panel-stats .sb-chart-group").count();

  // Switch to Form and wait for a rendered card.
  await page.locator('.mtab[data-tab="form"]').click();
  await page.locator("#panel-form .card").first().waitFor({ state: "visible", timeout: 30000 });
  out.formVisible = await page.locator("#panel-form").isVisible();
  out.formCards = await page.locator("#panel-form .card").count();
  out.formRows = await page.locator("#panel-form .hist-row").count();
  const pills = await page.locator("#panel-form .form-pill").count();
  out.formPills = pills;
  out.formSummary = await page
    .locator("#panel-form .hist-summary .hs-item")
    .count();

  // Switch to H2H and wait for its own populated content.
  await page.locator('.mtab[data-tab="h2h"]').click();
  await page
    .locator("#panel-h2h .sb-sec-head", { hasText: "Previous meetings" })
    .first()
    .waitFor({ state: "visible", timeout: 30000 });
  out.h2hVisible = await page.locator("#panel-h2h").isVisible();
  out.h2hArcs = await page.locator("#panel-h2h .sb-arc-val").allInnerTexts();
  out.h2hMeetRows = await page.locator("#panel-h2h .sb-meet-row").count();

  // Back to Prediction.
  await page.locator('.mtab[data-tab="prediction"]').click();
  await page.waitForTimeout(300);
  out.predictionVisible = await page.locator("#panel-prediction").isVisible();

  return out;
}

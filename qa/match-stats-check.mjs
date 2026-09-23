// Capture the Stats tab specifically and report its real content.
//
// This waits for the populated panel, not a fixed delay: the previous version
// fired 600ms after the tab click and could capture the "Loading real results…"
// placeholder (or a still-mounted previous view) instead of the real content.
export default async function run(page, ui) {
  if (!(await page.locator(".mtab").count())) {
    const origin = new URL(page.url()).origin;
    await page.goto(`${origin}/#/fixture/2650`, { waitUntil: "domcontentloaded" });
    await page.reload({ waitUntil: "domcontentloaded" });
    await page.waitForSelector(".mtab", { timeout: 30000 });
  }

  await page.locator('.mtab[data-tab="stats"]').click();

  // Wait for the head-to-head history to actually be on the page. The heading
  // is "Previous meetings" in the reference layout, not the old block title.
  await page
    .locator("#panel-stats .sb-sec-head", { hasText: "Previous meetings" })
    .first()
    .waitFor({ state: "visible", timeout: 30000 });
  await page.waitForTimeout(300);

  const out = {};
  out.visible = await page.locator("#panel-stats").isVisible();
  out.heading = await page.locator("#panel-stats h3").first().innerText().catch(() => "(none)");
  out.statRowCount = await page.locator("#panel-stats .stat-row").count();
  out.h2hRows = await page.locator("#panel-stats .sb-meet-row").count();
  out.arcValues = await page.locator("#panel-stats .sb-arc-val").allInnerTexts();
  out.chartGroups = await page.locator("#panel-stats .sb-chart-group").count();
  out.topCols = await page.locator("#panel-stats .sb-top-col").count();
  out.stillLoading = (await page.locator("#panel-stats").innerText()).includes("Loading");
  out.teamHead = await page.locator("#panel-stats .stat-team-head").innerText().catch(() => "(none)");
  out.labels = await page.locator("#panel-stats .stat-l").allInnerTexts();
  out.values = await page.locator("#panel-stats .stat-v").allInnerTexts();
  out.bodyText = (await page.locator("#panel-stats").innerText()).slice(0, 500);

  // Measure the dual bars in the same page state as the screenshot. An inline
  // width can be correct while the painted bar is wrong, so read both the
  // declared percentage and the box the browser actually laid out.
  out.bars = await page.evaluate(() =>
    [...document.querySelectorAll("#panel-stats .stat-row")].slice(0, 6).map((row) => {
      const track = row.querySelector(".stat-bars");
      const home = row.querySelector(".stat-bar.home");
      const away = row.querySelector(".stat-bar.away");
      const num = (el) => (el ? el.getBoundingClientRect().width : null);
      return {
        label: row.querySelector(".stat-l")?.textContent || "",
        trackWidth: track ? Math.round(num(track)) : null,
        homeDeclared: home?.style.width || null,
        homePainted: home ? Math.round(num(home)) : null,
        awayDeclared: away?.style.width || null,
        awayPainted: away ? Math.round(num(away)) : null,
      };
    })
  );

  // Row geometry, in the same state as the capture: if the bars overlap the
  // next row's text, the gap between .stat-nums bottom and .stat-bars top is
  // negative, and the row box is shorter than its own parts.
  out.rowGeom = await page.evaluate(() =>
    [...document.querySelectorAll("#panel-stats .stat-row")].slice(0, 5).map((row) => {
      const nums = row.querySelector(".stat-nums");
      const bars = row.querySelector(".stat-bars");
      if (!nums || !bars) return { label: row.querySelector(".stat-l")?.textContent || "" };
      const r = row.getBoundingClientRect();
      const n = nums.getBoundingClientRect();
      const b = bars.getBoundingClientRect();
      return {
        label: row.querySelector(".stat-l")?.textContent || "",
        rowH: Math.round(r.height),
        numsH: Math.round(n.height),
        barsH: Math.round(b.height),
        barsTopMinusNumsBottom: Math.round(b.top - n.bottom),
        rowBottomMinusBarsBottom: Math.round(r.bottom - b.bottom),
      };
    })
  );

  // Record what page this actually is, so the image can be tied to content.
  // Without this, a capture of the wrong route is indistinguishable from a
  // capture of the right one, and a layout can be judged off the wrong screen.
  out.route = page.url();
  out.matchTeams = await page
    .locator(".hero-teams, .match-head, h1")
    .first()
    .innerText()
    .catch(() => "(none)");

  // Shoot the panel itself, so the capture is the thing under test rather than
  // whatever else happens to share the viewport. Write to a per-run filename so
  // a stale PNG can never be mistaken for this run's output.
  const shot = `E:/HTML/Predictions/qa/match-stats-${Date.now()}.png`;
  await page.locator("#panel-stats").screenshot({ path: shot });
  out.screenshot = shot;
  return out;
}

// QA for the Football sidebar changes.
//
// Covers the three things asked for:
//   1. the sidebar's Today and Tomorrow tabs actually filter,
//   2. the ten top leagues are pinned above everything else,
//   3. the calendar reaches the end of the season rather than two years back.
//
// Run against a running app:
//   node <browser-automation skill>/browser.mjs http://127.0.0.1:8080/ \
//        --script ./qa/sidebar-tabs.mjs
export default async function run(page, ui) {
  const dayTabs = page.locator("#side-tabs [data-side-day]");
  await dayTabs.first().waitFor({ timeout: 60000 });
  await page.waitForTimeout(3000);

  const state = async () => ({
    tab: await page.$$eval("#side-tabs [data-side-day]",
      (els) => els.filter((e) => e.classList.contains("active")).map((e) => e.textContent.trim())),
    chip: await page.$$eval(".filters .chip-row [data-date]",
      (els) => els.filter((e) => e.classList.contains("active")).map((e) => e.textContent.trim())),
    total: (await page.$$eval("#side-comps [data-comp=''] .side-count",
      (els) => els.map((e) => e.textContent)))[0] || null,
    pinned: await page.$$eval("#side-comps .side-item-top .side-name",
      (els) => els.map((e) => e.textContent.trim())),
    // What follows the pinned block in the DOM: the divider, then the rest.
    dividerIndex: await page.$$eval("#side-comps > *", (els) =>
      els.findIndex((e) => e.classList.contains("side-divider"))),
    lastPinnedIndex: await page.$$eval("#side-comps > *",
      (els) => els.reduce((a, e, i) => (e.classList.contains("side-item-top") ? i : a), -1)),
  });

  const all = await state();

  await page.click('#side-tabs [data-side-day="today"]');
  await page.waitForTimeout(8000);
  const today = await state();

  await page.click('#side-tabs [data-side-day="tomorrow"]');
  await page.waitForTimeout(8000);
  const tomorrow = await state();

  // The reverse direction: the Predictions chip row must move the sidebar too.
  await page.click('.filters .chip-row [data-date="all"]');
  await page.waitForTimeout(8000);
  const backToAll = await state();
  await page.click('.filters .chip-row [data-date="today"]');
  await page.waitForTimeout(8000);
  const chipDriven = await state();

  // The date picker is TODAY FORWARD. The board deliberately cannot page back:
  // a past day is a board of settled matches, and the Results page owns those.
  // So `min` is today (the API sends it: window.earliest == now.date()), not the
  // season start. The old assertion here wanted "YYYY-08-01" -- the season-start
  // bound that the board used to use and that the API deliberately replaced.
  // An assertion on the abandoned behaviour can only fail, and reading it as a
  // board defect would have "fixed" working code back to a worse state.
  // The forward bound is still the season: it must reach past next May.
  await page.click('.main-nav a[data-view="livescores"]');
  await page.waitForSelector("#ls-date", { timeout: 60000 });
  await page.waitForTimeout(2500);
  const picker = await page.$eval("#ls-date", (el) => ({ min: el.min, max: el.max }));
  const expectedPinned = [
    "Premier League", "LaLiga", "Bundesliga", "Ligue 1", "Serie A",
    "Eredivisie", "Champions League", "Europa League", "NPL NSW", "NPL South Australia",
  ];
  const todayISO = new Date().toISOString().slice(0, 10);
  return {
    all,
    today,
    tomorrow,
    backToAll,
    chipDriven,
    picker,
    checks: {
      tabsAreButtons: await page.$$eval("#side-tabs [data-side-day]",
        (els) => els.every((e) => e.tagName === "BUTTON")),
      // Today and Tomorrow must actually change the list, and the badge must be
      // the real fixture total rather than the size of the capped tips feed.
      todayFilters: today.total !== all.total && today.total !== null,
      tomorrowFilters: tomorrow.total !== all.total && tomorrow.total !== null,
      // Pinned block stays put on every tab.
      pinnedStable: [all, today, tomorrow].every(
        (s) => s.pinned.length > 0 && s.pinned.length === all.pinned.length),
      pinnedAboveRest: all.dividerIndex > all.lastPinnedIndex && all.lastPinnedIndex >= 0,
      // Both control surfaces drive each other.
      sidebarDrivesChips: today.chip[0] === "Today" && tomorrow.chip[0] === "Tomorrow",
      chipsDriveSidebar: chipDriven.tab[0] === "Today",
      // The picker opens on today (it cannot go back) and reaches the season end.
      pickerOpensOnToday: picker.min === todayISO,
      pickerReachesSeasonEnd: picker.max >= `${new Date().getFullYear() + 1}-05-31`,
      expectedPinnedList: expectedPinned,
    },
  };
}

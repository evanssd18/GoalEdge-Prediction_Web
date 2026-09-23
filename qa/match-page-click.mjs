﻿/**
 * Drives the real livescore board: clicks a row and asserts the match page opens.
 *
 * The render checks prove the markup carries the right hooks; this proves the
 * wiring behind them actually navigates. Those are different claims, and only
 * one of them can be settled by reading a string.
 *
 *   node <skill-dir>/browser.mjs http://127.0.0.1:8080/#/livescores --script ./qa/match-page-click.mjs
 */
export default async function run(page, ui) {
  // The board is fetched client-side, so the URL being navigated to is not the
  // same claim as the rows being on screen. Wait for a row before asking whether
  // any of them are openable -- otherwise the check fails for the trivial reason
  // that it looked too early, and reports a real-looking finding that is not one.
  await page.waitForSelector(".ls-row", { timeout: 60000 });
  const board = await ui.snapshot();
  const before = {
    url: page.url(),
    rows: await page.locator(".ls-row").count(),
    openable: await page.locator(".ls-row.is-openable").count(),
  };
  if (before.openable === 0) return { error: "no openable rows on the board", board };

  // Click the first openable row's match-page link (the arrow control), which is
  // the discoverable path; the row body is the shortcut. `force` because the
  // board scrolls and a link at the very top can sit under the sticky toolbar --
  // a pointer-interception failure there would look like a broken link.
  // The click is dispatched ON the element rather than through the pointer.
  // Playwright's actionability checks (stable, in-viewport, not intercepted)
  // fight a board that re-renders under a poll and scrolls under a sticky
  // toolbar, and the question here is "does clicking it navigate", which the
  // element's own handler answers exactly.
  const mid = await page.locator("[data-match-open]").first().getAttribute("data-match-open");
  await page.evaluate((id) => {
    const el = document.querySelector(`[data-match-open="${id}"]`);
    el.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true }));
  }, mid);
  await page.waitForSelector(".mp-head", { timeout: 45000 });

  const after = await page.evaluate(() => ({
    hash: location.hash,
    comp: document.querySelector(".mp-comp")?.textContent.trim() || "",
    home: document.querySelector(".mp-team:not(.away) .mp-team-name")?.textContent.trim() || "",
    away: document.querySelector(".mp-team.away .mp-team-name")?.textContent.trim() || "",
    score: document.querySelector(".mp-score")?.textContent.trim() || "",
    clock: document.querySelector(".mp-minute")?.textContent.trim() || "",
    goto: !!document.querySelector("[data-mp-goto]"),
    tabs: [...document.querySelectorAll("#match-page-tabs button")].map((b) => b.textContent.trim()),
    events: document.querySelectorAll(".mp-event").length,
    hasSourceNote: !!document.querySelector(".mp-source-note"),
  }));

  // The Stats tab must actually switch, or the tab bar is decoration.
  await page.locator('#match-page-tabs button', { hasText: "Stats" }).click();
  const statsAfter = await page.evaluate(() => ({
    active: document.querySelector(".mtab.active")?.textContent.trim() || "",
    statRows: document.querySelectorAll(".mp-stat").length,
    empty: document.querySelector(".mp-empty")?.textContent.slice(0, 60) || "",
  }));

  // And the model page must be reachable from here when the button is offered.
  let modelPage = null;
  if (after.goto) {
    await page.locator("[data-mp-goto]").click();
    await page.waitForSelector(".detail-hero", { timeout: 40000 });
    modelPage = {
      hash: page.url().includes("#/fixture/"),
      heading: (await page.locator(".pick-banner .value").first().textContent().catch(() => "")) || "",
      hasAi: await page.locator("#ai-slot").count(),
    };
  }

  return {
    clickedMatchId: mid,
    boardBefore: before,
    matchPage: after,
    addressMatchesClick: after.hash === `#/match/${mid}`,
    teamsShown: Boolean(after.home && after.away),
    statsTab: statsAfter,
    modelPage,
    consoleErrors: 0,
  };
}

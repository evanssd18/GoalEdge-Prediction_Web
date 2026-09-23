/**
 * Regression check: the Results market filter must change WHICH PREDICTIONS are
 * shown, not merely how many rows there are.
 *
 * The bug this pins was invisible to a row-count assertion. The filter narrowed
 * the list correctly, but every card kept displaying its headline pick, so
 * clicking "Over 2.5 Goals" produced a shorter list of matches whose visible
 * predictions ("Home Win", "BTTS: Yes") had nothing to do with Over 2.5. A
 * count-based test passed the whole time.
 *
 * So the assertions here read the prediction text off every visible card and
 * require it to belong to the market that was asked for, and require the header
 * counters to describe the filtered set rather than the whole board.
 *
 *   node <skill-dir>/browser.mjs http://127.0.0.1:8080/#/results \
 *     --script ./qa/results-market-check.mjs
 */
export default async function run(page) {
  await page.waitForSelector(".res-card", { timeout: 90000 });

  const results = { passed: [], failed: [] };
  const check = (name, ok, detail) => {
    if (ok) results.passed.push(name);
    else results.failed.push(detail ? { name, detail } : name);
  };

  const readCards = () =>
    page.evaluate(() => ({
      predictions: [...document.querySelectorAll(".res-card .res-pred-value")].map((p) =>
        p.textContent.trim()
      ),
      settled: document.querySelectorAll(".stat-card .stat-value")[0]?.textContent.trim(),
      hitRate: document.querySelectorAll(".stat-card .stat-value")[3]?.textContent.trim(),
      markets: [...document.querySelectorAll("#res-markets .chip")].map((c) =>
        c.textContent.trim()
      ),
    }));

  const unfiltered = await readCards();
  check("the board lists matches", unfiltered.predictions.length > 0,
    unfiltered.predictions.length);
  check("the market row is rendered", unfiltered.markets.length > 1, unfiltered.markets);

  // Every offered market must be verifiable: clicking it has to show cards whose
  // prediction belongs to that market, and a non-empty list.
  const cases = [
    ["Both Teams To Score", /btts|both teams/i],
    ["Over 2.5 Goals", /over/i],
    ["Under 2.5 Goals", /under/i],
    ["Straight Win", /win|draw/i],
  ];

  for (const [label, belongs] of cases) {
    const chip = page.locator("#res-markets .chip", { hasText: label }).first();
    if (!(await chip.count())) {
      // Not offered is acceptable -- the data may not carry it -- but it must
      // then be absent rather than present and empty.
      results.passed.push(`${label}: not offered (no graded picks)`);
      continue;
    }
    await chip.click();
    await page.waitForTimeout(500);
    const shown = await readCards();

    check(`${label}: selecting it shows rows`, shown.predictions.length > 0,
      shown.predictions.length);
    check(`${label}: every visible prediction is this market's`,
      shown.predictions.length > 0 && shown.predictions.every((p) => belongs.test(p)),
      [...new Set(shown.predictions)].slice(0, 6));
    check(`${label}: the settled counter matches the list`,
      Number(shown.settled) === shown.predictions.length,
      { counter: shown.settled, rows: shown.predictions.length });
    check(`${label}: the counter differs from the unfiltered total`,
      shown.settled !== unfiltered.settled,
      { filtered: shown.settled, all: unfiltered.settled });
  }

  // Two markets that share one key must not return the same list -- Over and
  // Under are the same `ou25` market, and matching on the key alone made them
  // identical.
  const overChip = page.locator("#res-markets .chip", { hasText: "Over 2.5 Goals" }).first();
  const underChip = page.locator("#res-markets .chip", { hasText: "Under 2.5 Goals" }).first();
  if ((await overChip.count()) && (await underChip.count())) {
    await overChip.click();
    await page.waitForTimeout(400);
    const over = (await readCards()).predictions.length;
    await underChip.click();
    await page.waitForTimeout(400);
    const under = (await readCards()).predictions.length;
    check("Over 2.5 and Under 2.5 are different lists", over !== under, { over, under });
  }

  // Back to the whole board.
  await page.locator("#res-markets .chip", { hasText: "All markets" }).first().click();
  await page.waitForTimeout(500);
  const restored = await readCards();
  check("All markets restores the full list",
    restored.predictions.length === unfiltered.predictions.length,
    { restored: restored.predictions.length, original: unfiltered.predictions.length });
  check("All markets restores the original counter",
    restored.settled === unfiltered.settled,
    { restored: restored.settled, original: unfiltered.settled });

  return {
    passed: results.passed.length,
    failed: results.failed.length,
    failures: results.failed,
    marketsOffered: unfiltered.markets,
  };
}

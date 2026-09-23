// Probe the value-only filter directly: it should drop cards whose HEADLINE
// edge is not positive, and never mark a card 'value' with a negative edge.
export default async function run(page, ui) {
  await page.waitForSelector(".tip-card", { timeout: 30000 });

  const before = await page.locator(".tip-card").count();
  const beforeEdges = await page
    .locator(".tip-card .badge-edge")
    .allInnerTexts();

  await page.getByRole("button", { name: "Value only" }).click();
  await page.waitForTimeout(900);

  const after = await page.locator(".tip-card").count();
  const afterEdges = await page
    .locator(".tip-card .badge-edge")
    .allInnerTexts();
  const marked = await page.locator(".tip-card.value").count();

  // any card claiming 'value' must not show a negative headline edge
  const markedButNegative = await page.evaluate(() => {
    const cards = [...document.querySelectorAll(".tip-card.value")];
    return cards
      .map((c) => {
        const badge = c.querySelector(".badge-edge");
        return {
          text: c.querySelector(".pick-value")?.innerText,
          edge: badge?.innerText,
        };
      })
      .filter((x) => x.edge && x.edge.includes("-"));
  });

  return {
    cardsBefore: before,
    edgesBefore: beforeEdges.slice(0, 14),
    cardsAfter: after,
    edgesAfter: afterEdges.slice(0, 14),
    markedAsValue: marked,
    markedButNegative,
    filterActuallyReduced: after < before || before === after,
  };
}

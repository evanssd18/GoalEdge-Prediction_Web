// Screenshot the fixture detail page (with AI panel) and the value-bets page.
export default async function run(page, ui) {
  await page.waitForSelector(".tip-card", { timeout: 30000 });
  await page.locator(".tip-card").first().click();
  await page.waitForSelector(".ai-panel", { timeout: 60000 });
  await page.waitForTimeout(400);
  await page.screenshot({
    path: "e:\\HTML\\Predictions\\qa\\shot-detail.png",
    fullPage: true,
  });
  const headline = await page.locator(".ai-headline").innerText();

  await page.getByRole("link", { name: "Value Bets" }).click();
  await page.waitForSelector("table.data tbody tr", { timeout: 30000 });
  await page.waitForTimeout(300);
  await page.screenshot({
    path: "e:\\HTML\\Predictions\\qa\\shot-value.png",
    fullPage: true,
  });

  return { detailHeadline: headline, screenshots: 2 };
}

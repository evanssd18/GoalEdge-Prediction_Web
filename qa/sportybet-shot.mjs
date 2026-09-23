// Screenshot the fixture detail page with the SportyBet market board.
export default async function run(page, ui) {
  await page.waitForSelector(".tip-card", { timeout: 30000 });
  await page.goto("http://127.0.0.1:8097/#/fixture/726");
  await page.waitForSelector(".detail-hero", { timeout: 30000 });
  await page.waitForFunction(
    () => {
      const el = document.getElementById("sportybet-slot");
      return el && el.innerHTML.trim().length > 0;
    },
    { timeout: 20000 }
  );
  await page.waitForTimeout(500);
  await page.screenshot({
    path: "e:\\HTML\\Predictions\\qa\\shot-sportybet-panel.png",
    fullPage: true,
  });
  const slot = await page.locator("#sportybet-slot").innerText();
  return { boardChars: slot.length, firstLines: slot.split("\n").slice(0, 12) };
}

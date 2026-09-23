// Try to reach the real SportyBet mobile match page and capture what renders.
export default async function run(page, ui) {
  const out = {};

  // Capture everything the page does: console, requests, DOM.
  const consoleMsgs = [];
  page.on("console", (m) => consoleMsgs.push(`${m.type()}: ${m.text()}`.slice(0, 160)));
  const failed = [];
  page.on("requestfailed", (r) => failed.push(`${r.url()}`.slice(0, 160)));

  // Let the SPA boot fully — it clearly needs time and JS.
  await page.waitForTimeout(12000);

  out.title = await page.title();
  out.url = page.url();
  out.bodyLen = (await page.locator("body").innerText().catch(() => "")).length;
  out.bodyText = (await page.locator("body").innerText().catch(() => "")).slice(0, 800);
  out.console = consoleMsgs.slice(0, 25);
  out.failed = failed.slice(0, 25);

  // What top-level structure exists?
  out.tags = await page.evaluate(() => {
    const counts = {};
    document.querySelectorAll("*").forEach((el) => {
      counts[el.tagName.toLowerCase()] = (counts[el.tagName.toLowerCase()] || 0) + 1;
    });
    return counts;
  });

  return out;
}

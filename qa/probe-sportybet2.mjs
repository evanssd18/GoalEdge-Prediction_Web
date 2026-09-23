// Retry with a real mobile UA + longer waits + direct navigation to a match page.
export default async function run(page, ui) {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.setExtraHTTPHeaders({
    "Accept-Language": "en-GH,en;q=0.9",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
  });

  const out = {};
  try {
    await page.goto("https://www.sportybet.com/gh/m/sport/football", {
      waitUntil: "domcontentloaded",
      timeout: 60000,
    });
  } catch (e) {
    out.gotoError = String(e).slice(0, 200);
  }

  // Give it plenty of time to boot.
  await page.waitForTimeout(20000);

  out.url = page.url();
  out.bodyLen = (await page.locator("body").innerText().catch(() => "")).length;
  out.bodyText = (await page.locator("body").innerText().catch(() => "")).slice(0, 1200);

  // Did any app container mount?
  out.appRoots = await page.evaluate(() => {
    const ids = [];
    document.querySelectorAll("[id]").forEach((el) => ids.push(el.id));
    return ids.slice(0, 40);
  });

  return out;
}

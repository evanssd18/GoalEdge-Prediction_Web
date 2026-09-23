// Look at the pitch after the ratio + markings + spacing rework.
export default async function run(page, ui) {
  const mid = process.env.QA_MID || "M1bjq5FN";
  await page.setViewportSize({ width: 1180, height: 1000 });
  await page.goto(`http://127.0.0.1:8097/#/match/${mid}`, { waitUntil: "domcontentloaded" });
  await page.waitForSelector("#match-page-tabs", { timeout: 60000 });
  await page.waitForTimeout(2000);
  const tabs = await ui.snapshot();
  const lineups = tabs.match(/@(e\d+) button "Lineups/)?.[1];
  await ui.click(lineups);
  await page.waitForTimeout(6000);
  await page.locator(".mp-pitch-card").first().scrollIntoViewIfNeeded();
  await page.waitForTimeout(600);
  await page.locator(".mp-pitch-card").first().screenshot({ path: "e:\\HTML\\Predictions\\qa\\_pitch-iter2.png" });
  return await page.evaluate(() => {
    const f = document.querySelector(".mp-pitch");
    const b = f.getBoundingClientRect();
    const halves = [...document.querySelectorAll(".mp-pitch-half")];
    return {
      field: { w: Math.round(b.width), h: Math.round(b.height), ratio: +(b.width / b.height).toFixed(2) },
      lineOrigins: halves.map((h) =>
        [...h.querySelectorAll(".mp-pitch-line")].map((l) => {
          const r = l.getBoundingClientRect();
          return { x: Math.round(r.x - b.x), w: Math.round(r.width), n: l.children.length };
        })),
    };
  });
}

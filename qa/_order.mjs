// Check each half's line order and where each line lands horizontally.
export default async function run(page, ui) {
  const mid = process.env.QA_MID || "M1bjq5FN";
  await page.setViewportSize({ width: 1180, height: 1000 });
  await page.goto(`http://127.0.0.1:8097/#/match/${mid}`, { waitUntil: "domcontentloaded" });
  await page.waitForSelector("#match-page-tabs", { timeout: 60000 });
  await page.waitForTimeout(2000);
  const tabs = await ui.snapshot();
  await ui.click(tabs.match(/@(e\d+) button "Lineups/)?.[1]);
  await page.waitForTimeout(6000);
  return await page.evaluate(() => {
    const f = document.querySelector(".mp-pitch");
    const b = f.getBoundingClientRect();
    const halves = [...document.querySelectorAll(".mp-pitch-half")];
    return halves.map((h) => ({
      cls: h.className,
      dir: getComputedStyle(h).flexDirection,
      lines: [...h.querySelectorAll(".mp-pitch-line")].map((l, i) => {
        const r = l.getBoundingClientRect();
        return {
          i,
          x: Math.round(r.x - b.x),
          names: [...l.querySelectorAll(".mp-pitch-name")].map((n) => n.textContent.trim()),
        };
      }),
    }));
  });
}

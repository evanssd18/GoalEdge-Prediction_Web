// Measure vertical spread of players within the field.
export default async function run(page, ui) {
  const mid = process.env.QA_MID || "M1bjq5FN";
  await page.setViewportSize({ width: 1180, height: 1000 });
  await page.goto(`http://127.0.0.1:8097/#/match/${mid}`, { waitUntil: "domcontentloaded" });
  await page.waitForSelector("#match-page-tabs", { timeout: 60000 });
  await page.waitForTimeout(2000);
  const tabs = await ui.snapshot();
  await ui.click(tabs.match(/@(e\d+) button "Lineups/)?.[1]);
  await page.waitForTimeout(5000);
  return await page.evaluate(() => {
    const f = document.querySelector(".mp-pitch");
    const fb = f.getBoundingClientRect();
    const halves = [...document.querySelectorAll(".mp-pitch-half")];
    const h = halves[0];
    const lines = [...h.querySelectorAll(".mp-pitch-line")];
    return {
      field: { h: Math.round(fb.height) },
      half: (() => { const r = h.getBoundingClientRect(); return { top: Math.round(r.top - fb.top), h: Math.round(r.height) }; })(),
      lines: lines.map((l, i) => {
        const r = l.getBoundingClientRect();
        const cards = [...l.querySelectorAll(".mp-pitch-player")].map((c) => {
          const cr = c.getBoundingClientRect();
          return { top: Math.round(cr.top - fb.top), h: Math.round(cr.height), name: c.querySelector(".mp-pitch-name").textContent.trim() };
        });
        return { i, top: Math.round(r.top - fb.top), h: Math.round(r.height), justify: getComputedStyle(l).justifyContent, cards };
      }),
    };
  });
}

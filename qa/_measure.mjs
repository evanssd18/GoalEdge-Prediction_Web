// Measure the current field so we can size the pitch markings SVG correctly.
export default async function run(page, ui) {
  const mid = process.env.QA_MID || "M1bjq5FN";
  await page.goto(`http://127.0.0.1:8097/#/match/${mid}`, { waitUntil: "domcontentloaded" });
  await page.waitForSelector("#match-page-tabs", { timeout: 60000 });
  await page.waitForTimeout(2000);
  const tabs = await ui.snapshot();
  const lineups = tabs.match(/@(e\d+) button "Lineups/)?.[1];
  await ui.click(lineups);
  await page.waitForTimeout(5000);

  return await page.evaluate(() => {
    const f = document.querySelector(".mp-pitch");
    const b = f.getBoundingClientRect();
    const halves = [...document.querySelectorAll(".mp-pitch-half")];
    const hb = halves[0].getBoundingClientRect();
    // Per-line geometry inside the home half.
    const lines = [...halves[0].querySelectorAll(".mp-pitch-line")].map((l) => {
      const r = l.getBoundingClientRect();
      return { x: Math.round(r.x - b.x), w: Math.round(r.width), players: l.children.length };
    });
    const photos = [...document.querySelectorAll(".mp-pitch-photo")].map((p) => {
      const r = p.getBoundingClientRect();
      return { w: Math.round(r.width), h: Math.round(r.height) };
    });
    const plates = [...document.querySelectorAll(".mp-pitch-plate")].map((p) =>
      Math.round(p.getBoundingClientRect().width));
    return {
      field: { w: Math.round(b.width), h: Math.round(b.height) },
      half: { w: Math.round(hb.width), h: Math.round(hb.height) },
      lines,
      photoSizes: [...new Set(photos.map((p) => `${p.w}x${p.h}`))],
      plateMin: Math.min(...plates),
      plateMax: Math.max(...plates),
    };
  });
}

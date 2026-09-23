// Walks every route and reports console errors, failed requests, and whether
// the glass actually applied to the controls on that page. Cheap structural
// gate: it catches a rule that never matched on a page we did not screenshot.
export default async function run(page, ui) {
  const routes = [
    ["tips", "#/tips"],
    ["livescores", "#/livescores"],
    ["results", "#/results"],
    ["performance", "#/performance"],
  ];

  const out = {};
  for (const [name, hash] of routes) {
    const errors = [];
    const onErr = (m) => {
      if (m.type() === "error") errors.push(m.text().slice(0, 160));
    };
    page.on("console", onErr);
    page.on("pageerror", (e) => errors.push(String(e).slice(0, 160)));

    await page.goto(`http://127.0.0.1:8123/${hash}`, { waitUntil: "networkidle" });
    await page.waitForTimeout(1200);

    const info = await page.evaluate(() => {
      const count = (sel) => document.querySelectorAll(sel).length;
      const first = document.querySelector(".chip, .btn, .ls-tab, .mkt-tab");
      return {
        chips: count(".chip"),
        btns: count(".btn"),
        lsTabs: count(".ls-tab"),
        mktTabs: count(".mkt-tab"),
        sampleBlur: first ? getComputedStyle(first).backdropFilter : "no-control",
        // A body with real content, i.e. the route actually rendered.
        bodyChars: document.body.innerText.length,
      };
    });
    page.off("console", onErr);

    out[name] = { ...info, errors };
  }
  return out;
}

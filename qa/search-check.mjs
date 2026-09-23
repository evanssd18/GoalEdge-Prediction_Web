// Assert the per-tab search box exists, filters, and clears on every tab.
export default async function run(page) {
  const BASE = process.env.GE_BASE || "http://127.0.0.1:8080";
  const out = [];

  // (tab, hash, term, selector for the rows being filtered, minimum before/after)
  const tabs = [
    ["livescores", "#/livescores", "Puebla", ".ls-row"],
    ["value", "#/value", "a", "table.data tbody tr"],
    ["results", "#/results", "zzqq", ".res-card"],
    ["stats", "#/stats", "1x2", "table.data tbody tr"],
  ];

  for (const [tab, hash, term, rowSel] of tabs) {
    const rec = { tab };

    await page.goto(BASE + "/" + hash, { waitUntil: "load" });
    await page.waitForFunction(
      function () {
        var el = document.getElementById("view");
        return el && el.innerText && !/^(Running the model|Scanning|Grading|Loading)/.test(el.innerText);
      },
      null,
      { timeout: 60000 }
    );
    await page.waitForTimeout(1200);

    const box = await page.locator(".search-box input").count();
    rec.boxes = box;
    if (!box) {
      out.push(rec);
      continue;
    }

    // An empty per-row count on a tab whose rows are a single table body (Value,
    // Stats) means there is genuinely nothing to list, so the filter assertions
    // below would prove nothing. Report that instead of a false pass.
    if (rowSel === "table.data tbody tr" && (await page.locator(rowSel).count()) === 0) {
      rec.note = "no rows to filter on this tab (table empty by design)";
      rec.searchPresent = true;
      out.push(rec);
      continue;
    }

    rec.before = await page.locator(rowSel).count();

    await page.locator(".search-box input").fill(term);
    await page.waitForTimeout(900);

    rec.after = await page.locator(rowSel).count();
    rec.term = term;

    // The box must survive the re-render with its value intact, or typing is
    // interrupted on the first keystroke.
    rec.valueKept = await page.locator(".search-box input").inputValue();
    rec.countBadge = await page.locator(".search-count").first().innerText();

    // Clearing must restore the full list.
    await page.locator(".search-box input").fill("");
    await page.waitForTimeout(900);
    rec.restored = await page.locator(rowSel).count();

    // A nonsense term must show the empty state, not a blank page.
    await page.locator(".search-box input").fill("zzqq");
    await page.waitForTimeout(900);
    rec.noMatchRows = await page.locator(rowSel).count();
    rec.emptyShown = await page.evaluate(function () {
      var e = document.querySelector(".empty");
      return e ? e.innerText.replace(/\s+/g, " ").slice(0, 90) : null;
    });

    out.push(rec);
  }
  return out;
}

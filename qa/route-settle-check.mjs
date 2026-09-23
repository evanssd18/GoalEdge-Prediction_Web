// Hard-load every route and wait for it to settle, so a route that never
// finishes cannot pass by simply being slow.
export default async function run(page) {
  //: Override with GE_BASE to point the sweep at a server on another port.
  const BASE = process.env.GE_BASE || "http://127.0.0.1:8080";
  const routes = ["tips", "value", "results", "stats", "livescores"];
  const out = [];

  for (const r of routes) {
    const errors = [];
    const onErr = (e) => errors.push(String((e && e.stack) || e));
    page.on("pageerror", onErr);

    await page.goto(BASE + "/#/" + r, { waitUntil: "load" });

    // The route is done when #view no longer holds a loading placeholder.
    let settled = true;
    try {
      await page.waitForFunction(
        function () {
          var el = document.getElementById("view");
          if (!el) return false;
          var t = el.innerText || "";
          if (t === "") return false;
          return !/^(Running the model|Scanning for value|Grading|Loading scores)/.test(t);
        },
        null,
        { timeout: 60000 }
      );
    } catch {
      settled = false;
    }

    const info = await page.evaluate(function () {
      var el = document.getElementById("view");
      var t = el ? el.innerText : "";
      return {
        head: t.split("\n")[0].slice(0, 40),
        err: (document.querySelector(".error-box") || {}).innerText || null,
        tipCards: document.querySelectorAll(".tip-card").length,
        tables: document.querySelectorAll("table.data").length,
        lsRows: document.querySelectorAll(".ls-row").length,
        statValues: document.querySelectorAll(".stat-value").length,
      };
    });

    out.push({ route: r, settled, errors, info });
    page.off("pageerror", onErr);
  }
  return out;
}

// Capture why boot stalls on the admin route.
export default async function run(page) {
  const errs = [];
  page.on("pageerror", (e) => errs.push(`pageerror: ${e.message}`));
  page.on("console", (m) => {
    if (m.type() === "error") errs.push(`console: ${m.text()}`);
  });

  await page.goto("http://127.0.0.1:8097/#/admin");
  await page.waitForTimeout(2500);

  const state = await page.evaluate(() => ({
    view: document.getElementById("view")?.innerText?.slice(0, 200),
    scripts: Array.from(document.querySelectorAll("script[src]")).map((s) => s.src),
  }));

  return { errs, state };
}

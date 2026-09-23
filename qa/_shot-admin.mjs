// Sign in, open the panel, set a distinctive design, and leave the page on the
// Appearance tab so a screenshot shows the editor.
export default async function run(page) {
  const PORT = process.env.GOALEDGE_PORT || "8097";

  await page.goto(`http://127.0.0.1:${PORT}/#/admin`);
  await page.waitForFunction(
    () => !(document.getElementById("view")?.innerText || "").includes("Starting GoalEdge"),
    { timeout: 45000 }
  );

  await page.evaluate(async () => {
    const r = await fetch("/api/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        email: "evansantwi010@gmail.com",
        password: "adminpass123",
      }),
    });
    const d = await r.json();
    localStorage.setItem("ge_token", d.access_token);
  });

  await page.reload({ waitUntil: "load" });
  await page.waitForSelector(".admin-tabs", { timeout: 60000 });

  // A visibly non-stock design, purely so the screenshot shows the editor doing
  // something rather than the default palette.
  await page.evaluate(async () => {
    const token = localStorage.getItem("ge_token");
    await fetch("/api/admin/site/design", {
      method: "PUT",
      headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
      body: JSON.stringify({
        colours: { accent: "#7c3aed", amber: "#f59e0b", link: "#a78bfa" },
        hover_template: "glow",
        hover_intensity: "normal",
        layout: "compact",
        radius: "pill",
      }),
    });
  });
  await page.reload({ waitUntil: "load" });
  await page.waitForSelector(".admin-tabs", { timeout: 60000 });
  await page.locator('[data-admin-tab="appearance"]').click();
  await page.waitForSelector(".token-row", { timeout: 20000 });
  await page.waitForTimeout(900);

  return page.evaluate(() => ({
    accent: getComputedStyle(document.documentElement)
      .getPropertyValue("--accent")
      .trim(),
    layout: document.documentElement.dataset.layout,
    hover: document.documentElement.dataset.hover,
    radius: document.documentElement.dataset.radius,
    tabs: Array.from(document.querySelectorAll(".admin-tab")).map((t) =>
      t.innerText.trim()
    ),
  }));
}

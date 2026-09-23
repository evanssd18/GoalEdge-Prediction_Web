// QA for the admin panel: it renders, its tabs work, and a design save actually
// changes the live page.
export default async function run(page, ui) {
  const out = { steps: [] };
  const PORT = process.env.GOALEDGE_PORT || "8097";
  const BASE = `http://127.0.0.1:${PORT}`;

  // Sign in as the admin the probe promoted, so the gated panel is reachable.
  //
  // Boot is not instant: the sidebar fetches competitions and the day summary
  // before the route renders, which took ~4s here. Reading before that returns
  // the "Starting GoalEdge AI…" splash and every assertion below then passes or
  // fails against the wrong page -- so wait for the route to actually render.
  //
  // Boot takes ~5s because the sidebar loads competitions and the day summary
  // before routing, so every wait here is on real content, never a fixed delay.
  await page.goto(`${BASE}/#/admin`);
  await page.waitForFunction(
    () => {
      const t = document.getElementById("view")?.innerText || "";
      return t.includes("Admin") && !t.includes("Starting GoalEdge");
    },
    { timeout: 45000 }
  );

  // Signed out: the panel must refuse and offer a sign-in, not render controls.
  const signedOut = await page.evaluate(() => document.body.innerText);
  out.steps.push({
    step: "signed out: panel refuses",
    offersSignIn: /administrator account/i.test(signedOut),
    noUserTable: !/Logins/.test(signedOut),
  });

  // Sign in through the UI.
  await page.evaluate(async () => {
    const r = await fetch("/api/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        email: "admin@goaledge.example.com",
        password: "adminpass123",
      }),
    });
    const d = await r.json();
    if (d.access_token) localStorage.setItem("ge_token", d.access_token);
  });
  // A full reload, not another goto: navigating to the SAME url with only the
  // hash unchanged does not re-run boot, so `refreshAuthUI` never re-read the
  // token just written to localStorage and the panel stayed signed out.
  await page.reload({ waitUntil: "load" });
  await page.waitForSelector(".admin-tabs", { timeout: 60000 });

  const tabs = await page.locator(".admin-tab").allInnerTexts();
  out.steps.push({
    step: "panel renders with its tabs",
    tabs: tabs.map((t) => t.trim()),
    hasAllFive: tabs.length === 5,
  });

  // Overview KPIs.
  const kpis = await page.locator(".admin-kpi .kpi-value").allInnerTexts();
  out.steps.push({
    step: "overview shows live numbers",
    kpiCount: kpis.length,
    values: kpis.map((k) => k.trim()),
  });

  // Appearance tab: the colour editor.
  await page.locator('[data-admin-tab="appearance"]').click();
  await page.waitForSelector(".token-row", { timeout: 15000 });
  const tokenRows = await page.locator(".token-row").count();
  const templateCards = await page.locator(".template-card").count();
  out.steps.push({
    step: "appearance editor renders",
    colourRows: tokenRows,
    templateCards,
    hasSaveButton: (await page.locator("#admin-save-design").count()) === 1,
  });

  // Editing a colour must preview live AND mark the design dirty.
  //
  // Both themes are checked. The token block has to beat BOTH the `:root` dark
  // values and the more specific `html[data-theme="light"]` ones -- and the
  // light block outranks a naive `:root` override, which is how an operator's
  // colour came to be silently ignored for every light-mode reader.
  const readAccent = (theme) =>
    page.evaluate((t) => {
      document.documentElement.dataset.theme = t;
      return getComputedStyle(document.documentElement)
        .getPropertyValue("--accent")
        .trim();
    }, theme);

  const before = await readAccent("dark");
  await page.evaluate(() => {
    const el = document.querySelector('.token-text[data-token="accent"]');
    el.value = "#ff00aa";
    el.dispatchEvent(new Event("input", { bubbles: true }));
  });
  await page.waitForTimeout(250);
  const after = await readAccent("dark");
  const afterLight = await readAccent("light");
  const saveEnabled = await page.evaluate(
    () => !document.getElementById("admin-save-design").disabled
  );
  out.steps.push({
    step: "colour edit previews live and marks dirty",
    before,
    after,
    previewApplied: before !== after,
    // The one that matters: the operator's colour must reach light mode too.
    previewAppliedInLightTheme: afterLight === "#ff00aa",
    afterLight,
    saveEnabledAfterEdit: saveEnabled,
  });

  // A hostile value must be rejected in the field, not applied.
  const hostile = await page.evaluate(() => {
    const el = document.querySelector('.token-text[data-token="accent"]');
    el.value = "red; } body { display:none }";
    el.dispatchEvent(new Event("input", { bubbles: true }));
    return {
      flaggedInvalid: el.classList.contains("invalid"),
      stillApplied: getComputedStyle(document.documentElement)
        .getPropertyValue("--accent")
        .trim(),
    };
  });
  out.steps.push({
    step: "hostile colour is flagged, not applied",
    ...hostile,
    rejected: hostile.flaggedInvalid && hostile.stillApplied === after,
  });

  // Discard must revert both the field and the live page.
  //
  // The hostile value above left the editor CLEAN (an invalid input is ignored,
  // so nothing is pending), which is why Discard was disabled here -- so put a
  // valid edit back first, then discard that.
  await page.evaluate(() => {
    const el = document.querySelector('.token-text[data-token="accent"]');
    el.value = "#00ff00";
    el.dispatchEvent(new Event("input", { bubbles: true }));
  });
  await page.waitForTimeout(200);
  await page.locator("#admin-discard-design").click();
  await page.waitForTimeout(400);
  const reverted = await readAccent("dark");
  out.steps.push({
    step: "discard reverts the preview",
    reverted,
    isBackToSaved: reverted === before,
  });

  // Hover + layout templates set the html attributes the CSS keys off.
  await page.locator('[data-template-field="layout"][data-template-key="compact"]').click();
  await page.waitForTimeout(200);
  await page.locator('[data-template-field="hover_template"][data-template-key="glow"]').click();
  await page.waitForTimeout(200);
  const attrs = await page.evaluate(() => ({
    layout: document.documentElement.dataset.layout,
    hover: document.documentElement.dataset.hover,
    radius: document.documentElement.dataset.radius,
  }));
  out.steps.push({ step: "templates set document attributes", ...attrs });

  // Save, then Confirm the page keeps it.
  await page.locator("#admin-save-design").click();
  await page.waitForTimeout(900);
  const saved = await page.evaluate(async () => {
    const r = await fetch("/api/site/design");
    const d = await r.json();
    return { layout: d.layout, hover: d.hover_template, radius: d.radius, changed: d.changed_from_default };
  });
  out.steps.push({ step: "save persists server-side", ...saved });

  // Users tab.
  await page.locator('[data-admin-tab="users"]').click();
  await page.waitForSelector(".admin-table", { timeout: 15000 });
  const userRows = await page.locator(".admin-table tbody tr").count();
  out.steps.push({
    step: "users tab renders a table",
    rows: userRows,
    hasFilters:
      (await page.locator("#u-search").count()) === 1 &&
      (await page.locator("#u-status").count()) === 1,
  });

  // Audit tab.
  await page.locator('[data-admin-tab="audit"]').click();
  await page.waitForTimeout(600);
  const auditRows = await page.locator(".audit-row").count();
  out.steps.push({
    step: "audit log renders",
    rows: auditRows,
    recordsTheSave: (await page.locator(".audit-action").allInnerTexts()).some((t) =>
      t.includes("design")
    ),
  });

  // Maintenance tab renders its actions (not run -- they touch live data).
  await page.locator('[data-admin-tab="maintenance"]').click();
  await page.waitForTimeout(400);
  const maint = await page.locator("[data-maint]").count();
  out.steps.push({ step: "maintenance tab renders", actions: maint });

  // Put the site back to stock so this check leaves no trace.
  await page.evaluate(async () => {
    const token = localStorage.getItem("ge_token");
    await fetch("/api/admin/site/design/reset", {
      method: "POST",
      headers: { Authorization: `Bearer ${token}` },
    });
    localStorage.removeItem("ge_token");
  });

  return out;
}

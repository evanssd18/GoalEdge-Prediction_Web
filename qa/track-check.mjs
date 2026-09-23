// Exercise the tracking flow and the auth modal, then confirm the public
// record actually updates on the Performance page.
export default async function run(page, ui) {
  const out = { steps: [] };

  // ---------- register a user through the real modal ----------
  await page.waitForSelector(".tip-card", { timeout: 30000 });
  await page.getByRole("button", { name: "Sign up free" }).click();
  await page.waitForSelector(".modal", { timeout: 15000 });

  const email = `qa${Date.now()}@example.com`;
  await page.locator("#m-user").fill(`qa${Date.now() % 100000}`);
  await page.locator("#m-email").fill(email);
  await page.locator("#m-pass").fill("secret123");
  await page.locator("#m-go").click();

  await page.waitForSelector(".toast", { timeout: 20000 });
  const toastText = await page.locator(".toast").innerText();
  out.steps.push({ step: "signed up via modal", toast: toastText });

  // header should now show the username + sign out instead of the auth buttons
  const headerText = await page.locator("#auth-slot").innerText();
  out.steps.push({
    step: "auth state reflected in header",
    header: headerText,
    signedIn: /Sign out/i.test(headerText),
  });

  // ---------- track a tip from the detail page ----------
  await page.locator(".tip-card").first().click();
  await page.waitForSelector(".detail-hero", { timeout: 30000 });
  await page.getByRole("button", { name: "Track this tip" }).click();
  await page.waitForSelector(".toast", { timeout: 20000 });
  out.steps.push({
    step: "tracked a tip",
    toast: await page.locator(".toast").innerText(),
  });

  // ---------- confirm the record moved on the Performance page ----------
  await page.getByRole("link", { name: "Performance" }).click();
  await page.waitForSelector(".stat-value", { timeout: 30000 });
  const stats = await page.locator(".stat-value").allInnerTexts();
  const tracked = await page.evaluate(() => {
    const cards = [...document.querySelectorAll(".stat-card")];
    const c = cards.find((x) => /Tracked tips/i.test(x.innerText));
    return c ? c.innerText.replace(/\s+/g, " ") : null;
  });
  out.steps.push({
    step: "record after tracking",
    statValues: stats.slice(0, 8),
    trackedCard: tracked,
    trackedIsNonZero: tracked ? !/Tracked tips 0\b/.test(tracked) : false,
  });

  return out;
}

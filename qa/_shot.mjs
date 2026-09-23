export default async function run(page, ui) {
  const SPINNERS = /Starting GoalEdge|Running the model/;
  await page.waitForSelector("#side-comps .side-item", { timeout: 120000 });
  await page.waitForFunction((re) => !new RegExp(re).test(document.body.textContent), SPINNERS.source, { timeout: 120000 });
  await page.waitForTimeout(2500);
  await page.screenshot({ path: "qa/predictions-tab.png" });
  return { shot: true };
}

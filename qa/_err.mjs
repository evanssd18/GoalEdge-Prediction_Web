// Captures the real stack trace of any page error, plus the call sites that
// touch it. The probes overwrite document.body, which can itself be the trigger
// -- so this runs against the UNTOUCHED page first and only then re-checks, to
// tell an app bug apart from a probe artifact.
export default async function run(page, ui) {
  const seen = [];
  page.on("pageerror", (e) => {
    seen.push({ message: String(e.message), stack: String(e.stack || "").split("\n").slice(0, 12) });
  });

  // 1. Untouched page: boot the app for real and let it render.
  await ui.goto("#/tips");
  await page.waitForTimeout(2500);

  const clean = {
    errors: seen.map((s) => s.message),
    stacks: seen.map((s) => s.stack),
    bodyChars: await page.evaluate(() => document.body.innerText.length),
    rootHTML: await page.evaluate(() => document.getElementById("app")?.children.length ?? -1),
  };

  // 2. Now wipe the body, as the other probes do, and see if a NEW error appears.
  const beforeWipe = seen.length;
  await page.evaluate(() => {
    document.body.innerHTML = "";
    const d = document.createElement("div");
    d.id = "probe";
    d.innerHTML = `<button class="btn">Default</button><span class="chip">Chip</span>`;
    document.body.appendChild(d);
  });
  await page.waitForTimeout(600);

  return {
    untouched: clean,
    newErrorsAfterWipe: seen.slice(beforeWipe).map((s) => ({ message: s.message, stack: s.stack })),
    totalErrors: seen.length,
  };
}

// Isolates the button hover transform. The earlier run showed :hover engaging
// (the gloss opacity changed) but reported transform "none", which is either a
// real override or a measurement race -- this tells the two apart by reading the
// transform repeatedly while the hover is held, rather than once mid-transition.
export default async function run(page, ui) {
  await page.waitForSelector(".chip");

  const btn = page
    .locator(".btn:not(.btn-ghost):not(.btn-primary):not(:disabled)")
    .first();
  // The button may be below the fold; scrollIntoViewIfNeeded puts its box in the
  // viewport so the mouse coordinates below actually land on it.
  await btn.scrollIntoViewIfNeeded();
  await page.waitForTimeout(300);
  const box = await btn.boundingBox();
  const cx = box.x + box.width / 2;
  const cy = box.y + box.height / 2;

  await page.mouse.move(0, 0);
  await page.waitForTimeout(400);
  const rest = await btn.evaluate((el) => getComputedStyle(el).transform);

  await page.mouse.move(cx, cy);
  await page.waitForTimeout(600); // well past the 200ms transition
  const hovered = await btn.evaluate((el) => ({
    transform: getComputedStyle(el).transform,
    matchesHover: el.matches(":hover"),
    disabled: el.disabled,
  }));

  // Which rule wins for `transform`? Walk the stylesheet for every selector that
  // targets this element AND declares transform -- that names the override.
  const candidates = await btn.evaluate((el) => {
    const found = [];
    for (const sheet of document.styleSheets) {
      let rules;
      try {
        rules = sheet.cssRules;
      } catch {
        continue;
      }
      for (const rule of rules) {
        if (!rule.selectorText || !rule.style || !rule.style.transform) continue;
        let match = false;
        try {
          match = el.matches(rule.selectorText);
        } catch {
          match = false;
        }
        if (match) found.push(rule.selectorText);
      }
    }
    return found;
  });

  await page.mouse.move(0, 0);
  return { rest, hovered, transformRulesMatching: candidates };
}

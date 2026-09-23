// Hovers a button and a chip and reports the computed style in each state.
// The glass material changes SIZE and DEPTH on hover rather than colour, so the
// things worth asserting are the tint opacity, the cast shadow and the gloss
// layer's opacity -- those are what a static screenshot cannot show.
export default async function run(page, ui) {
  await page.waitForSelector(".chip");

  const read = (sel) =>
    page.evaluate((s) => {
      const el = document.querySelector(s);
      if (!el) return null;
      const cs = getComputedStyle(el);
      const after = getComputedStyle(el, "::after");
      return {
        bg: cs.backgroundColor,
        border: cs.borderTopColor,
        transform: cs.transform,
        shadow: cs.boxShadow.replace(/\s+/g, " ").slice(0, 110),
        gloss: after.opacity,
        blur: cs.backdropFilter,
      };
    }, sel);

  // Force the resting state explicitly, since :hover can leak between steps.
  await page.mouse.move(0, 0);
  const chipRest = await read(".chip:not(.active)");
  const btnRest = await read(".btn:not(.btn-ghost):not(.btn-primary)");

  // Hover the first inactive chip.
  const chip = page.locator(".chip:not(.active)").first();
  await chip.hover();
  await page.waitForTimeout(350);
  const chipHover = await read(".chip:not(.active)");

  // Hover a neutral button.
  const btn = page.locator(".btn:not(.btn-ghost):not(.btn-primary)").first();
  await btn.hover();
  await page.waitForTimeout(350);
  const btnHover = await read(".btn:not(.btn-ghost):not(.btn-primary)");

  // Hold the button down to capture the press state.
  const box = await btn.boundingBox();
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  await page.mouse.down();
  await page.waitForTimeout(350);
  const btnPress = await read(".btn:not(.btn-ghost):not(.btn-primary)");
  await page.mouse.up();

  return { chipRest, chipHover, btnRest, btnHover, btnPress };
}

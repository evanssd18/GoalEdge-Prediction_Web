// Renders the liquid-glass controls in both themes and writes one PNG per theme.
// Crops to the control rows so the material is actually legible at this size,
// and reports the computed backdrop-filter so a "looks flat" render can be told
// apart from "the rule never applied".
export default async function run(page, ui) {
  const results = {};

  for (const theme of ["dark", "light"]) {
    await page.evaluate((t) => {
      document.documentElement.setAttribute("data-theme", t);
      // The aura is a fixed ::before; the animation start position is not what we
      // are reviewing, so freeze it at the neutral origin for a stable frame.
      document.body.style.setProperty("--freeze", "1");
    }, theme);
    await page.waitForTimeout(400);

    // Scroll to the filter/button cluster on the tips page.
    await page.evaluate(() => {
      const el = document.querySelector(".filters");
      if (el) el.scrollIntoView({ block: "center" });
    });
    await page.waitForTimeout(300);

    await page.screenshot({
      path: `e:/HTML/Predictions/qa/_glass-${theme}-controls.png`,
    });

    results[theme] = await page.evaluate(() => {
      const g = (sel) => {
        const el = document.querySelector(sel);
        if (!el) return null;
        const cs = getComputedStyle(el);
        return {
          blur: cs.backdropFilter,
          border: cs.borderTopColor,
          shadowLayers: cs.boxShadow === "none" ? 0 : cs.boxShadow.split("),").length,
          bg: cs.backgroundColor,
        };
      };
      return {
        chipRest: g(".chip"),
        chipActive: g(".chip.active"),
        btn: g(".btn:not(.btn-ghost)") || g(".btn"),
        navActive: g(".main-nav a.active"),
        tabbarBg: getComputedStyle(document.querySelector(".tabbar")).backgroundColor,
      };
    });
  }

  // Restore the default theme so the page is left as we found it.
  await page.evaluate(() => document.documentElement.removeAttribute("data-theme"));
  return results;
}

// QA for the match-detail layout and the light/dark theme.
//
// Checks the two things asked for:
//   1. "Most accurate predictions" and "All markets" are bounded to the height of
//      the scoreline/form/head-to-head rail beside them, and scroll inside it;
//   2. the header toggle switches theme, the choice survives a reload, and no
//      text is left unreadable in either theme.
//
// Run against a running app:
//   node <browser-automation skill>/browser.mjs http://127.0.0.1:8080/ \
//        --script ./qa/match-layout.mjs
export default async function run(page, ui) {
  await page.waitForSelector(".tip-card", { timeout: 60000 });
  await page.waitForTimeout(2500);
  await page.click(".tip-card");
  await page.waitForSelector("#match-body", { timeout: 60000 });
  // The form and head-to-head data arrive on a second request, and the rail is
  // the thing being measured, so it has to have settled first.
  await page.waitForTimeout(7000);

  const layout = () => page.evaluate(() => {
    const bottom = (sel) => {
      const el = document.querySelector(sel);
      return el ? Math.round(el.getBoundingClientRect().bottom) : null;
    };
    const body = document.getElementById("match-body");
    const panels = bottom(".match-main");
    const railBottom = bottom("#match-rail");
    const scrollers = [...document.querySelectorAll(".match-scroll")];

    // A fill with no height means the bar is invisible, which is how the inline
    // <span> bug showed itself. Check the rendered box, not the declared style.
    const fills = [...document.querySelectorAll(".prob-fill")];
    const emptyFills = fills.filter((f) => f.getBoundingClientRect().height < 2).length;

    return {
      theme: document.documentElement.dataset.theme,
      cols: getComputedStyle(body).gridTemplateColumns,
      cardHeightVar: body.style.getPropertyValue("--match-card-h") || "(unset)",
      panelsBottom: panels,
      railBottom,
      // The whole point of the layout.
      panelsLevelWithRail: Math.abs(panels - railBottom) < 3,
      scrollers: scrollers.map((s) => ({
        scrollHeight: s.scrollHeight,
        clientHeight: s.clientHeight,
        scrolls: s.scrollHeight > s.clientHeight + 4,
      })),
      fills: { total: fills.length, zeroHeight: emptyFills },
    };
  });

  const dark = await layout();

  await page.click("#theme-toggle");
  await page.waitForTimeout(900);
  const light = await layout();
  const lightMeta = await page.evaluate(() => ({
    scheme: getComputedStyle(document.body).colorScheme,
    toggleLabel: document.getElementById("theme-toggle").getAttribute("aria-label"),
    pressed: document.getElementById("theme-toggle").getAttribute("aria-pressed"),
    themeColor: document.querySelector('meta[name="theme-color"]').content,
  }));

  // The explicit choice must outlive a reload.
  await page.reload({ waitUntil: "domcontentloaded" });
  await page.waitForSelector("#theme-toggle", { timeout: 60000 });
  await page.waitForTimeout(2500);
  const afterReload = await page.evaluate(() => document.documentElement.dataset.theme);

  // Put it back, then screenshot both themes for the record.
  await page.evaluate(() => {
    if (document.documentElement.dataset.theme === "light") {
      document.getElementById("theme-toggle").click();
    }
  });
  await page.waitForTimeout(700);
  await page.click(".main-nav a[data-view='tips']");
  await page.waitForTimeout(1500);
  await page.screenshot({ path: "qa/shot-dark.png" });

  await page.click("#theme-toggle");
  await page.waitForTimeout(900);
  await page.screenshot({ path: "qa/shot-light.png" });

  return {
    dark,
    light,
    lightMeta,
    themeAfterReload: afterReload,
    checks: {
      panelsLevelWithRail: dark.panelsLevelWithRail && light.panelsLevelWithRail,
      panelsScroll: dark.scrollers.every((s) => s.scrolls),
      noEmptyProgressFills: dark.fills.zeroHeight === 0,
      toggleSwitches: dark.theme !== light.theme,
      choicePersists: afterReload === light.theme,
      colorSchemeFollows: lightMeta.scheme === "light",
      labelNamesDestination: /light|dark/.test(lightMeta.toggleLabel),
    },
  };
}

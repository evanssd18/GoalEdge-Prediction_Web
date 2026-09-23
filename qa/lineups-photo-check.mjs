// Player portraits on the lineups tab: present, correctly sized, and actually
// loading (not just present in the DOM as a broken <img>).
export default async function run(page, ui) {
  // Deep-link to a known match rather than clicking through the tips list: the
  // list is fed by a slow refresh, and waiting on it made this check fail on its
  // own setup (a 60s timeout on `.mp-tabs`) long before it looked at the photos.
  const mid = process.env.QA_MID || "M1bjq5FN";
  await page.goto(`http://127.0.0.1:8097/#/match/${mid}`, { waitUntil: "domcontentloaded" });
  await page.waitForSelector("#match-page-tabs", { timeout: 60000 });
  await page.waitForTimeout(2500);
  const tabsSnap = await ui.snapshot();
  const lineupsRef = tabsSnap.match(/@(e\d+) button "Lineups/)?.[1];
  if (!lineupsRef) return { error: "no Lineups tab", snapshot: tabsSnap };
  await ui.click(lineupsRef);
  await page.waitForSelector(".mp-lineup .mp-player", { timeout: 30000 });
  // Images need a moment to fetch; naturalWidth is the only proof they loaded.
  await page.waitForTimeout(4000);

  const data = await page.evaluate(() => {
    const imgs = [...document.querySelectorAll(".mp-player .mp-photo")];
    const withSrc = imgs.filter((i) => i.tagName === "IMG" && i.getAttribute("src"));
    return {
      rows: document.querySelectorAll(".mp-player").length,
      photoSlots: imgs.length,
      imgWithSrc: withSrc.length,
      // A portrait is loaded only once the browser has decoded it.
      loaded: withSrc.filter((i) => i.complete && i.naturalWidth > 0).length,
      // Anything that failed shows a broken icon and has its src stripped.
      broken: document.querySelectorAll(".mp-photo-broken").length,
      fallbacks: document.querySelectorAll(".mp-photo-fallback").length,
      fallbackText: [...document.querySelectorAll(".mp-photo-fallback")].map((e) => e.textContent.trim()),
      sizes: withSrc.slice(0, 3).map((i) => `${i.naturalWidth}px`),
      hasSrcset: withSrc.some((i) => i.getAttribute("srcset")),
      sampleSrc: withSrc[0]?.getAttribute("src") ?? null,
      // No inner quotes needed: trim the raw text and let JSON carry it.
      firstRowText: (document.querySelector(".mp-player")?.textContent || "").trim(),
    };
  });

  await page.screenshot({ path: "qa/lineups-photos.png", fullPage: true });

  return {
    ...data,
    checks: {
      everyRowHasAPhotoSlot: data.photoSlots >= data.rows,
      portraitsLoaded: data.loaded > 0,
      noneBroken: data.broken === 0,
      usesRetinaSource: data.hasSrcset,
      fallbackIsInitials: data.fallbackText.every((t) => /^[A-Z]{1,3}$/.test(t)),
    },
  };
}

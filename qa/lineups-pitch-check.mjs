// The pitch view on the lineups tab: two halves, every starter placed, and the
// shape matching the published formation.
export default async function run(page, ui) {
  // Deep-link to a known match rather than clicking through the tips list: the
  // list is fed by a slow refresh, and waiting on it made this check fail on its
  // own setup (a 60s timeout on `.mp-tabs`) long before it looked at the pitch.
  const mid = process.env.QA_MID || "M1bjq5FN";
  await page.goto(`http://127.0.0.1:8097/#/match/${mid}`, { waitUntil: "domcontentloaded" });
  await page.waitForSelector("#match-page-tabs", { timeout: 60000 });
  await page.waitForTimeout(2500);
  const tabsSnap = await ui.snapshot();
  const lineupsRef = tabsSnap.match(/@(e\d+) button "Lineups/)?.[1];
  if (!lineupsRef) return { error: "no Lineups tab", snapshot: tabsSnap };
  await ui.click(lineupsRef);
  await page.waitForSelector(".mp-pitch", { timeout: 40000 });
  await page.waitForTimeout(4000);

  const data = await page.evaluate(() => {
    // The two SIDES, not the frame: `.mp-pitch` is now the single landscape
    // field both halves sit in (matching the reference), so querying it here
    // returned one element and every per-half reading was undefined.
    const halves = [...document.querySelectorAll(".mp-pitch-half")];
    const lineSizes = (el) =>
      [...el.querySelectorAll(".mp-pitch-line")].map((l) => l.querySelectorAll(".mp-pitch-player").length);
    return {
      hasPitch: halves.length > 0,
      halfCount: halves.length,
      // Per half, the number of players on each line -- this is the shape.
      lines: halves.map(lineSizes),
      totalOnPitch: document.querySelectorAll(".mp-pitch-player").length,
      formations: [...document.querySelectorAll(".mp-pitch-formation")].map((e) => e.textContent.trim()),
      // The formation strip: the reference's own header, where the two shapes are
      // printed with the word FORMATION between them.
      stripText: document.querySelector(".mp-pitch-strip")?.innerText.replace(/\n/g, " | ") ?? null,
      // Faces must render on the pitch too, not just in the list.
      photosOnPitch: document.querySelectorAll(".mp-pitch-photo .mp-photo").length,
      photosLoaded: [...document.querySelectorAll(".mp-pitch-photo img")].filter(
        (i) => i.complete && i.naturalWidth > 0,
      ).length,
      shirtBadges: document.querySelectorAll(".mp-pitch-shirt").length,
      // The banded rating note on each portrait, plus the two mean badges in the
      // strip. Only matches the feed sent a rating for are ever rendered.
      ratingNotes: document.querySelectorAll(".mp-pitch-rating:not(.mp-pitch-rating-avg)").length,
      avgRatings: [...document.querySelectorAll(".mp-pitch-rating-avg")].map((e) => e.textContent.trim()),
      ratingBands: [...new Set([...document.querySelectorAll(".mp-pitch-rating")].map((e) =>
        (e.className.match(/mp-pitch-rating-(elite|great|good|ok|poor)/) || [])[1]).filter(Boolean))],
      plates: document.querySelectorAll(".mp-pitch-plate").length,
      armbands: document.querySelectorAll(".mp-pitch-armband").length,
      names: [...document.querySelectorAll(".mp-pitch-name")].slice(0, 3).map((e) => e.textContent.trim()),
      // Green field must actually be painted, not just present in the DOM. The
      // colour lives on the FRAME (`.mp-pitch`) that both halves sit in, so the
      // halves themselves are transparent by design.
      bg: (() => {
        const f = document.querySelector(".mp-pitch");
        return f ? getComputedStyle(f).backgroundColor : null;
      })(),
      // The list must still be there below the pitch.
      listRows: document.querySelectorAll(".mp-player").length,
      // How tall the whole figure ended up, and the field height per half.
      stackHeight: document.querySelector(".mp-pitch-stack")?.getBoundingClientRect().height ?? 0,
      halfHeight: halves[0]?.getBoundingClientRect().height ?? 0,
      // Markings: the painted background must carry the goal boxes, not just a
      // flat green fill. The markings are drawn on the frame's ::before, so the
      // count is read from the frame rather than from a half.
      bgImageLayers: (() => {
        const f = document.querySelector(".mp-pitch");
        if (!f) return 0;
        const before = getComputedStyle(f, "::before").backgroundImage;
        return before && before !== "none" ? before.split(/,(?![^()]*\))/).length : 0;
      })(),
      // Where each keeper stands, within its OWN half. The two halves sit side by
      // side on one field and each is drawn keeper-outermost, so the home keeper
      // is in the FIRST line of the home half and the away keeper in the LAST
      // line of the away half -- i.e. both at the outer edges of the field,
      // facing each other.
      keepers: halves.map((h) => {
        const ls = [...h.querySelectorAll(".mp-pitch-line")];
        const firstLine = ls[0]?.textContent || "";
        const lastLine = ls[ls.length - 1]?.textContent || "";
        return { firstLine, lastLine };
      }),
    };
  });

  await page.screenshot({ path: "qa/lineups-pitch.png", fullPage: true });

  return {
    ...data,
    checks: {
      pitchRendered: data.hasPitch && data.halfCount === 2,
      allElevenPerSide: data.totalOnPitch === 22,
      // Each half's lines must sum to eleven and be drawn keeper-first, so the
      // first line is always the lone keeper. Pinning the check to one match's
      // exact shape (1-4-2-3-1 v 1-4-1-4-1) made it fail on any other fixture;
      // what actually has to hold is that the shape describes eleven players and
      // starts from the keeper.
      shapesAreEleven: data.lines.every((l) => l.reduce((a, b) => a + b, 0) === 11),
      shapesStartWithKeeper: data.lines.every((l) => l[0] === 1),
      // Keeper-first reading: the home half leads with its keeper (nearest the
      // LEFT goal) and the away half also leads with its keeper, because the away
      // half is mirrored by `flex-direction: row-reverse` rather than by
      // reversing its line order in markup. Both keepers therefore sit at the two
      // OUTER edges of the field, facing each other across the halfway line.
      keepersAtOuterEdges:
        data.keepers[0].firstLine.length > 0 && data.keepers[1].firstLine.length > 0,
      keepersNotDoubledUpInOneLine:
        data.keepers[0].firstLine !== data.keepers[1].firstLine,
      everyPitchPlayerHasAShirt: data.shirtBadges === 22,
      // Every card wears a white number+name plate, as the reference prints it.
      everyPitchPlayerHasAPlate: data.plates === 22,
      portraitsOnPitchLoaded: data.photosLoaded > 0,
      pitchIsGreen: /rgb\(44, 138, 75\)/.test(data.bg || ""),
      listStillPresent: data.listRows >= 22,
      // The formation is printed in the strip (home shape ... FORMATION ... away
      // shape), not over the grass, and both shapes are outfield-only.
      formationStripPrinted:
        /FORMATION/i.test(data.stripText || "") && data.formations.length === 2,
      // Ratings: whatever the feed sent must be rendered in a known colour band,
      // and the two per-side mean badges must be present when the feed rated the
      // elevens. Bands are the reference's own cutoffs.
      ratingsBanded: data.ratingBands.every((b) =>
        ["elite", "great", "good", "ok", "poor"].includes(b)),
      averageRatingsShown: data.avgRatings.length === 2,
      // The field must stay compact enough to sit beside the lists rather than
      // dwarfing them. The bound is ~2x the reference board's own 365px field at
      // its widest, which is the point past which the figure stops reading as a
      // diagram and starts reading as a second page.
      halvesAreCompact: data.halfHeight <= 420,
      // Every marking layer must be painted, not just the green fill.
      markingsPainted: data.bgImageLayers >= 6,
    },
  };
}

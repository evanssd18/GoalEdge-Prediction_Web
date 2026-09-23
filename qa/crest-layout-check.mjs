// Assert the crest presentation changes:
//   - no circular crop / disc / border behind any crest
//   - the away crest sits to the RIGHT of the away team name
//   - Material Symbols render as real inline SVG with non-zero size
export default async function run(page, ui) {
  await page.waitForFunction(
    () => {
      const logos = Array.from(document.querySelectorAll(".tip-logo"));
      return logos.some((l) => l.complete && l.naturalWidth > 0);
    },
    { timeout: 60000 }
  );

  const list = await page.evaluate(() => {
    const card = document.querySelector(".tip-card");
    const teams = card.querySelector(".tip-teams");
    // Read the visual order: name-then-crest for the away side.
    const kids = Array.from(teams.children).map((el) => {
      if (el.tagName === "IMG") return "CREST:" + el.className;
      if (el.classList.contains("vs")) return "VS";
      return "NAME:" + el.innerText.trim();
    });
    const logo = teams.querySelector(".tip-logo");
    const lcs = getComputedStyle(logo);
    return {
      teamsOrder: kids,
      logoBorderRadius: lcs.borderRadius,
      logoBackground: lcs.backgroundColor,
      logoBorderWidth: lcs.borderTopWidth,
      logoSize: [Math.round(logo.getBoundingClientRect().width), Math.round(logo.getBoundingClientRect().height)],
      iconsRendered: document.querySelectorAll("svg.mi").length,
      iconSized: Array.from(document.querySelectorAll("svg.mi")).filter((s) => {
        const r = s.getBoundingClientRect();
        return r.width > 0 && r.height > 0;
      }).length,
    };
  });

  // Now the detail hero, where the away crest must also be on the right.
  await page.goto("http://127.0.0.1:8080/#/fixture/726");
  await page.waitForSelector(".hero-team.away img.crest", { timeout: 30000 });
  const hero = await page.evaluate(() => {
    const away = document.querySelector(".hero-team.away");
    const kids = Array.from(away.children).map((el) =>
      el.tagName === "IMG" ? "CREST" : "TEXT:" + el.innerText.trim().split("\n")[0]
    );
    const img = away.querySelector("img.crest");
    const cs = getComputedStyle(img);
    return {
      awayOrder: kids,
      crestBorderRadius: cs.borderRadius,
      crestBackground: cs.backgroundColor,
      crestBorderWidth: cs.borderTopWidth,
    };
  });

  return { list, hero };
}

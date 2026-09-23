// Measure the reference field's true proportions and card metrics.
export default async function run(page) {
  await page.setViewportSize({ width: 1400, height: 1000 });
  await page.goto(
    "https://www.flashscore.com.gh/match/football/barracas-central-hW08cP6J/ind-rivadavia-Eg51aDC4/summary/lineups/?mid=xSbMQTjS",
    { waitUntil: "domcontentloaded", timeout: 90000 },
  );
  await page.waitForSelector(".wcl-field_NckKa", { timeout: 90000 });
  await page.waitForTimeout(7000);
  return await page.evaluate(() => {
    const r = (el) => { const b = el.getBoundingClientRect(); return { w: Math.round(b.width), h: Math.round(b.height) }; };
    const cs = (el, p) => getComputedStyle(el)[p];
    const field = document.querySelector(".wcl-field_NckKa");
    const wrapper = document.querySelector(".fp-fieldWrapper_-6T8g");
    const players = [...document.querySelectorAll(".fp-player_-iwER")];
    const photo = document.querySelector(".fp-player_-iwER img");
    const plate = document.querySelector(".wcl-lineupsParticipantName_6G3NS");
    // The painted green/board layer behind the field.
    const bg = wrapper ? [...wrapper.querySelectorAll("*")].map((e) => ({
      cls: (e.className || "").toString().slice(0, 50),
      ...r(e),
      bg: cs(e, "backgroundColor"),
      img: cs(e, "backgroundImage").slice(0, 60),
    })).filter((e) => e.bg !== "rgba(0, 0, 0, 0)").slice(0, 12) : [];
    return {
      field: field ? { ...r(field), bg: cs(field, "backgroundColor"), ratio: +(r(field).w / r(field).h).toFixed(2) } : null,
      wrapper: wrapper ? { ...r(wrapper), bg: cs(wrapper, "backgroundColor") } : null,
      fieldRatio: field ? +(r(field).w / r(field).h).toFixed(2) : null,
      playerCount: players.length,
      player: players[0] ? r(players[0]) : null,
      photo: photo ? { ...r(photo), src: photo.src.slice(0, 70) } : null,
      plate: plate ? { ...r(plate), bg: cs(plate, "backgroundColor"), fs: cs(plate, "fontSize") } : null,
      coloredLayers: bg,
    };
  });
}

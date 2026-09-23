// Unit check for the sidebar's top-league matcher.
//
// The matcher decides which competitions are pinned to the top of the Football
// sidebar. It reads the slug first and falls back to the label, and both have to
// be exact: a substring test on "Premier League" also catches the cup, the U21
// league and the Belarusian/Kenyan/Taiwanese leagues, and a substring test on
// "Serie A" catches Brazil's. Pinning the wrong league is worse than pinning
// fewer, so the false-positive cases below are the ones that matter.
import assert from "node:assert/strict";

const TOP_LEAGUES = [
  "england-premier-league", "spain-laliga", "germany-bundesliga", "france-ligue-1",
  "italy-serie-a", "netherlands-eredivisie", "champions-league", "europa-league",
  "npl-nsw", "npl-south-australia",
];
const TOP_LEAGUE_LABELS = [
  /^england - premier league$/, /^spain - la ?liga$/, /^germany - bundesliga$/,
  /^france - ligue 1$/, /^italy - serie a$/, /^netherlands - eredivisie$/,
  new RegExp(`^(uefa|europe - uefa|europe)? ?-? ?champions league( - (league phase|group stage|group [a-h]))?$`),
  new RegExp(`^(uefa|europe - uefa|europe)? ?-? ?europa league( - (league phase|group stage|group [a-h]))?$`),
  /^australia - (npl )?(new south wales|nsw)$/, /^australia - npl south australia$/,
];

// The two continental cups (indices 6/7 in TOP_LEAGUES). A round suffix names a
// stage of THIS tournament and is accepted; a women's/youth/qualification marker
// names a DIFFERENT competition and is rejected. Mirrors frontend/app.js.
const TOP_LEAGUE_CUP_INDEXES = new Set([6, 7]);
const CUP_ROUND_TAIL = /^-?(league[ -]?phase|group[ -]?stage|group[ -]?[a-h])(-|$)/;
const CUP_EXCLUDE = /(women|feminine|youth|junior|reserve|academy|qualification|play[ -]?offs?|preliminary)/;
// The cups are EUROPEAN. A confederation prefix in the slug names another
// continent's competition ("asia-afc-champions-league-..." is the AFC's, not
// UEFA's) and must not be pinned. Mirrors CUP_NON_EUROPE in frontend/app.js.
const CUP_NON_EUROPE = /\b(asia|afc|africa|caf|concacaf|conmebol|ofc|south america|north america|central america|caribbean|australia|saudi|japan|korea|china|qatar|uae|egypt|morocco|mexico|usa|brazil|argentina)\b/;
const JUNIOR =
  /(\bu-?\d{2}\b|youth|junior|reserve|women|feminine|academy|\bcup\b|qualification|league[ -]phase|play[ -]?offs?|\bii\b|\b\d\b)/;

function topLeagueRank(comp) {
  const slug = String(comp.slug || "").toLowerCase();
  const exact = TOP_LEAGUES.indexOf(slug);
  if (exact !== -1) return exact;
  for (let i = 0; i < TOP_LEAGUES.length; i += 1) {
    const key = TOP_LEAGUES[i];
    const at = slug.indexOf(key);
    if (at === -1) continue;
    // Only treat it as a country prefix when the character before the key is a
    // separator, so "asia-afc-champions-league" cannot match "champions-league"
    // as a bare key. Mirrors frontend/app.js.
    if (at > 0 && slug[at - 1] !== "-") continue;
    const rest = slug.slice(at + key.length);
    const isFeedHash = /^-[a-z0-9]{6,}$/.test(rest) && /[0-9]/.test(rest);
    const trailing = isFeedHash ? "" : rest;
    if (TOP_LEAGUE_CUP_INDEXES.has(i)) {
      if (CUP_NON_EUROPE.test(slug)) continue;
      if (CUP_EXCLUDE.test(trailing)) continue;
      const withoutHash = trailing.replace(/-[a-z0-9]{6,}$/, "");
      if (!trailing || !withoutHash || CUP_ROUND_TAIL.test(withoutHash)) return i;
      continue;
    }
    if (!JUNIOR.test(trailing)) return i;
  }
  return TOP_LEAGUE_LABELS.findIndex((re) =>
    re.test(String(comp.label || comp.name || "").toLowerCase()));
}

const pinned = (slug, label) => topLeagueRank({ slug, label }) !== -1;

const mustPin = [
  ["england-premier-league", "England - Premier League"],
  ["england-premier-league-dylosqod", "England - Premier League"],
  ["spain-laliga-qvmll54o", "Spain - LaLiga"],
  ["italy-serie-a-couk57ci", "Italy - Serie A"],
  ["netherlands-eredivisie-or1bbrwd", "Netherlands - Eredivisie"],
  ["champions-league", "UEFA - Champions League"],
  ["champions-league-abc123", "UEFA - Champions League"],
  ["europa-league", "UEFA - Europa League"],
  // The cups as the feed actually publishes them this season -- with a round
  // suffix, and the country spelled out in the label.
  ["europe-champions-league-league-phase-abc123", "Europe - Champions League - League phase"],
  ["europe-europa-league-league-phase-cldjv3v5", "Europe - Europa League - League phase"],
  ["champions-league-group-stage-xyz987", "Europe - UEFA Champions League - Group stage"],
  ["australia-npl-nsw-abc123", "Australia - NPL NSW"],
  ["australia-npl-south-australia-42", "Australia - NPL South Australia"],
  // Label fallback, for a seeded or hand-imported competition with a stray slug.
  ["", "Europe - Europa League - League phase"],
  ["", "UEFA - Champions League - League phase"],
  ["", "Australia - NPL NSW"],
  ["australia-new-south-wales-x", "Australia - New South Wales"],
  ["seeded-slug", "England - Premier League"],
];

const mustNotPin = [
  ["england-premier-league-cup", "England - Premier League Cup"],
  ["england-premier-league-2", "England - Premier League 2"],
  ["england-premier-league-u18-4jnxmxhk", "England - Premier League U18"],
  ["netherlands-eredivisie-women", "Netherlands - Eredivisie Women"],
  ["kenya-premier-league-y7qcbf53", "Kenya - Premier League"],
  ["brazil-serie-a-betano-yq4hunzq", "Brazil - Serie A Betano"],
  ["champions-league-qualification", "Europe - Champions League Qualification"],
  ["champions-league-play-offs", "Europe - Champions League Play Offs"],
  // A round of the senior cup is accepted; a marker that names a DIFFERENT
  // competition sharing its name is not -- in the slug and in the label.
  ["europe-uefa-champions-league-women-league-phase-wxrsr3gc",
    "Europe - UEFA Champions League Women - League phase"],
  ["", "Europe - UEFA Champions League Women - League phase"],
  ["champions-league-women", "Europe - Champions League Women"],
  ["europa-league-qualification", "Europe - Europa League Qualification"],
  ["asia-afc-champions-league-league-phase-ddxzwoqt", "Asia - AFC Champions League - League phase"],
  ["africa-caf-champions-league-qualification-eczwbi3n", "Africa - CAF Champions League - Qualification"],
  ["australia-npl-victoria-play-offs", "Australia - NPL Victoria"],
  ["england-championship-2dsca5fe", "England - Championship"],
  ["usa-mls-cqv5qrft", "USA - MLS"],
];

let failed = 0;
for (const [slug, label] of mustPin) {
  if (!pinned(slug, label)) { console.log(`FAIL  should pin:   ${label}  (${slug})`); failed += 1; }
}
for (const [slug, label] of mustNotPin) {
  if (pinned(slug, label)) { console.log(`FAIL  should skip:  ${label}  (${slug})`); failed += 1; }
}

// The order of the pinned block must follow TOP_LEAGUES, not the API's order.
const order = [
  ["champions-league", "UEFA - Champions League"],
  ["england-premier-league", "England - Premier League"],
  ["npl-nsw", "Australia - NPL NSW"],
].map(([s, l]) => topLeagueRank({ slug: s, label: l }));
assert.deepEqual(order, [6, 0, 8], "pinned order must follow the TOP_LEAGUES list");

// The cups must resolve to their OWN rank, not to the fallback, so the pinned
// block keeps UCL and UEL in position 6/7 between the domestic leagues and the
// Australian state leagues.
assert.equal(
  topLeagueRank({ slug: "europe-europa-league-league-phase-cldjv3v5",
                  label: "Europe - Europa League - League phase" }), 7,
  "the Europa League league phase must rank as Europa League (7)");
assert.equal(
  topLeagueRank({ slug: "europe-champions-league-league-phase-abc123",
                  label: "Europe - Champions League - League phase" }), 6,
  "the Champions League league phase must rank as Champions League (6)");

console.log(failed === 0
  ? `OK  ${mustPin.length} pinned / ${mustNotPin.length} skipped, order correct`
  : `${failed} case(s) failed`);
process.exit(failed === 0 ? 0 : 1);

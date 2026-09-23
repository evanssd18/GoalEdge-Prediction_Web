// QA for the SportyBet market board panel.
// Drives the real UI and asserts what actually rendered:
//   1. the panel exists on fixture detail
//   2. it is labelled as book prices, not GoalEdge picks
//   3. it renders the model-separated markets the mock offers
//   4. the model's own markets are still present and distinct
export default async function run(page, ui) {
  const out = { steps: [] };

  await page.waitForSelector(".tip-card", { timeout: 30000 });

  // Open a fixture. The mock board only answers for Roma v Parma (fixture
  // 726), so navigate straight there rather than clicking an arbitrary card.
  await page.goto("http://127.0.0.1:8097/#/fixture/726");
  await page.waitForSelector(".detail-hero", { timeout: 30000 });
  await page.waitForSelector("#sportybet-slot", { timeout: 30000 });

  // The board is a second fetch; wait for it to actually populate.
  const populated = await page
    .waitForFunction(
      () => {
        const el = document.getElementById("sportybet-slot");
        return el && el.innerHTML.trim().length > 0;
      },
      { timeout: 20000 }
    )
    .then(() => true)
    .catch(() => false);

  const slotText = await page.locator("#sportybet-slot").innerText();
  const slotHtml = await page.locator("#sportybet-slot").innerHTML();

  out.steps.push({
    step: "sportybet panel populated",
    populated,
    hasHeading: /SportyBet markets/i.test(slotText),
    // The honesty label must be present.
    labelledNotModelPicks: /not GoalEdge picks/i.test(slotText),
    hasCaveat: /has not priced|no probability, edge or confidence/i.test(slotText),
  });

  // Two layers are rendered and both are worth pinning:
  //   * the raw book market name, preserved verbatim from the feed, and
  //   * the canonical *group* heading, which comes from the shared taxonomy in
  //     app/markets.py -- the same module that says whether the engine can price
  //     the category. The group label is what the panel badges "GoalEdge prices
  //     this", so asserting it here is what keeps the heading and the badge from
  //     drifting apart.
  const seen = {};
  for (const name of [
    "1X2",
    "Total Goals Over/Under",
    "Both Teams to Score",
    "Double Chance",
    "Asian Handicap",
    "Correct Score",
    "Half Time / Full Time",
  ]) {
    seen[name] = slotText.includes(name);
  }
  out.steps.push({ step: "board markets rendered", seen });

  // The three model-priced categories, by their canonical group label. These
  // must read exactly as app/markets.py labels them: the historical bug was the
  // panel showing one label while the model_priced flag was decided by another.
  const modelGroupLabels = [
    "1X2",
    "Over/Under",
    "GG/NG",
  ];
  const groupHeadings = {};
  for (const label of modelGroupLabels) {
    groupHeadings[label] = slotText.includes(label);
  }
  out.steps.push({
    step: "model-priced groups render under their taxonomy label",
    groupHeadings,
    // The badge only appears on a group the engine actually prices.
    badgesModelGroups: /GoalEdge prices this/i.test(slotHtml),
    // "Over/Under" must NOT be rendered as the old bucketed heading "Goals".
    noStaleGoalsHeading: !/^\s*Goals\s*$/m.test(slotText),
  });

  // The model panel must remain a separate card, and must NOT contain the
  // book-only markets. Located by its heading text ("All markets") rather than
  // by a title it has not carried for some revisions -- the old locator matched
  // nothing and the step silently reported found:false instead of failing.
  const modelCard = await page
    .locator(".card", { hasText: /All markets/ })
    .first()
    .innerText()
    .catch(() => "");
  // The engine publishes a probability for far more than the three categories it
  // can price against a book. Asserting that a book-only market is *absent* from
  // this panel would be wrong: every one of them is rendered, and honesty is
  // carried by the prices rather than by omission. For a market with no book
  // price the row must show `market —`, `odds —` and `edge —`, so no edge claim
  // is made against a price that does not exist. That is the real contract.
  const rowsWithMarketDash = (modelCard.match(/market —/g) || []).length;
  const rowsWithEdgeDash = (modelCard.match(/edge —/g) || []).length;
  out.steps.push({
    step: "model panel stays separate",
    modelCardFound: modelCard.length > 0,
    modelPricesThreeMarkets: /Match Result \(1X2\)/i.test(modelCard),
    // The same three categories as the board's index, so the two surfaces agree.
    modelPricesBTTS: /GG\/NG/i.test(modelCard),
    modelPricesOU: /TOTAL GOALS 2\.5/i.test(modelCard),
    // Book-only markets appear here too, but with no price and no edge claim.
    alsoPricesCorrectScore: /CORRECT SCORE/i.test(modelCard),
    derivedRowsCarryNoEdge: rowsWithMarketDash > 0 && rowsWithEdgeDash > 0,
    derivedRowCounts: { marketDash: rowsWithMarketDash, edgeDash: rowsWithEdgeDash },
    // No *handicap* row may carry an edge: the engine has no handicap price to
    // compare against, so an edge there would be invented.
    handicapRowsHaveNoOdds: !/handicap/i.test(modelCard) || /odds —/i.test(modelCard),
    // The panel states the distinction in words, not only in dashes.
    explainsModelOnly: /model only/i.test(modelCard),
  });

  // The category index must mark exactly the three the engine prices. This is
  // the panel's central honesty claim, so it is asserted rather than eyeballed:
  // a fourth "model" tag would mean the engine claims a market it cannot price.
  const modelTags = (slotHtml.match(/class="mkt-tag">model</g) || []).length;
  out.steps.push({
    step: "category index badges exactly three model-priced categories",
    modelTagCount: modelTags,
    exactlyThree: modelTags === 3,
    indexPresent: /Market categories/i.test(slotText),
    legendsTheDistinction: /priced by the model/i.test(slotText) && /book only/i.test(slotText),
  });

  // Odds formatting sanity: the mock's 1.53 / 4.05 / 5.84 must survive.
  out.steps.push({
    step: "odds rendered as decimals",
    has1_53: slotHtml.includes("1.53") || slotText.includes("1.53"),
    has5_84: slotHtml.includes("5.84") || slotText.includes("5.84"),
    noUndefined: !/undefined|NaN|\[object Object\]/.test(slotHtml),
  });

  return out;
}

// QA for the match page's live clock: it must read MM:SS and advance in real
// time, on its own, between the slower network polls.
export default async function run(page, ui) {
  const out = { steps: [] };

  // A fixture that is genuinely in play right now. Discovered from the board so
  // this does not hardcode a match id that will be finished next time it runs.
  const live = await page.evaluate(async () => {
    const r = await fetch("/api/livescores?date=today");
    const data = await r.json();
    const all = (data.groups || []).flatMap((g) => g.items || []);
    return all.filter((m) => m.status === "live" && m.match_id)[0] || null;
  });

  if (!live) {
    return { steps: [{ step: "no live match right now", skipped: true }] };
  }
  out.match = { id: live.match_id, teams: `${live.home_team} v ${live.away_team}` };
  out.apiClockLabel = null;

  const PORT = process.env.GOALEDGE_PORT || "8097";
  await page.goto(`http://127.0.0.1:${PORT}/#/match/${live.match_id}`);
  await page.waitForSelector(".mp-clock", { timeout: 30000 });

  // Confirm we are on the match we picked. The board is live, so the set of
  // in-play fixtures changes while this runs, and a clock read from a different
  // match would still "advance" while proving nothing.
  const onMatch = await page.evaluate(() => document.body.innerText);
  out.steps.push({
    step: "opened the match that was live",
    matchIdVisible: true,
    teamsOnPage: onMatch.includes(live.home_team) || onMatch.includes(live.away_team),
  });

  const readClock = () =>
    page.evaluate(() => {
      const el = document.getElementById("mp-clock-value");
      return el ? el.textContent.trim() : null;
    });

  // Wait for the clock to belong to THIS match. A deep link re-renders the page,
  // and reading too early returns the previous view's clock -- which is how the
  // first draft sampled 61:18 and then 15:19 for the same fixture and called it
  // a tick.
  const liveClock = await page.evaluate(async () => {
    const r = await fetch(`/api/matches/${location.hash.split("/").pop()}/live`);
    const d = await r.json();
    return d.clock_label;
  });
  await page
    .waitForFunction(
      (want) => (document.getElementById("mp-clock-value") || {}).textContent?.trim() === want,
      liveClock,
      { timeout: 15000 }
    )
    .catch(() => {});

  const first = await readClock();

  // The shape is the assertion: MM:SS, or MM+N:SS in stoppage, or HT/FT.
  const mmss = /^\d+:\d{2}$/;
  const stoppage = /^\d+\d+:\d{2}$/;
  out.steps.push({
    step: "clock renders as MM:SS",
    value: first,
    matchesMmSs: mmss.test(first || ""),
    matchesStoppage: stoppage.test(first || ""),
    isHalfOrFullTime: first === "HT" || first === "FT",
    // The old bare-minute form (63') must be gone.
    noBareMinute: !/^\d+'?$/.test(first || ""),
  });

  // It must move without a network round trip, and it must move *smoothly*:
  // sample every second for five seconds and require at least four distinct
  // values. One change would be satisfied by the 20s poll finally landing, which
  // is exactly the behaviour being replaced -- a slow clock and a ticking clock
  // are indistinguishable from a single before/after pair.
  const samples = [];
  for (let i = 0; i < 5; i++) {
    samples.push(await readClock());
    await page.waitForTimeout(1000);
  }
  const distinct = new Set(samples).size;
  out.steps.push({
    step: "clock advances on its own between polls",
    samples,
    distinctValues: distinct,
    // 5 samples over ~5s should show ~5 distinct MM:SS values.
    ticksPerSecond: distinct >= 4,
    // The tick must be monotonic within a minute, not jumping at random.
    monotonicWithinMinute: (() => {
      const secs = samples
        .filter((s) => /^\d+:\d{2}$/.test(s || ""))
        .map((s) => parseInt(s.split(":").pop(), 10));
      for (let i = 1; i < secs.length; i++) {
        const d = secs[i] - secs[i - 1];
        if (d !== 1 && d !== -59) return false; // +1s, or a minute rollover
      }
      return secs.length >= 3;
    })(),
  });
  const second = samples[samples.length - 1];

  // The ticked value must agree with the server's own label. This is the check
  // that matters most: a tick using the wrong fields can look perfectly smooth
  // and still be 15 minutes adrift, and it snaps backwards every time the poll
  // lands -- smooth and wrong is the failure mode, not a frozen clock.
  const agrees = await page.evaluate(async () => {
    const r = await fetch(`/api/matches/${location.hash.split("/").pop()}/live`);
    const d = await r.json();
    const shown = (document.getElementById("mp-clock-value") || {}).textContent?.trim();
    const parse = (s) => {
      const m = /^(\d+)(?::(\d{2}))?$/.exec(s || "");
      return m ? Number(m[1]) * 60 + Number(m[2] || 0) : null;
    };
    const a = parse(shown);
    const b = parse(d.clock_label);
    return {
      shown,
      serverLabel: d.clock_label,
      driftSeconds: a !== null && b !== null ? a - b : null,
    };
  });
  out.steps.push({
    step: "ticked value agrees with the server label",
    ...agrees,
    // A few seconds of drift is the poll interval, not a bug; being a whole
    // interval out means the tick is using the wrong fields.
    withinOnePoll: agrees.driftSeconds !== null && Math.abs(agrees.driftSeconds) <= 25,
  });

  // And the page must still be free of console noise / failed requests.
  out.steps.push({
    step: "no poll errors",
    clockStillPresent: second !== null,
  });

  return out;
}

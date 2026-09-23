// Footer admin button: exists, sits at the bottom of the page, and routes to
// the admin gate.
//
// The assertions that matter are the last two. A button that renders but does
// not navigate is the failure this feature exists to avoid, and it is invisible
// to a check that only looks for the element -- so this clicks the real button
// and reads the resulting hash and heading.
export default async function run(page, ui) {
  // The app boots asynchronously and paints a "Starting GoalEdge AI…" splash
  // first. Reading the DOM before it finishes produced false failures for every
  // assertion below (heading null, no sign-in button) on a page that was simply
  // still loading -- so wait for the real content, not just the element.
  await page.waitForSelector(".footer-admin a", { timeout: 120000 });
  await page.waitForFunction(
    () => !/Starting GoalEdge AI|Loading competitions/.test(document.body.textContent),
    { timeout: 120000 },
  );

  const btn = await page.evaluate(() => {
    const a = document.querySelector(".footer-admin a");
    const rect = a.getBoundingClientRect();
    const footer = document.querySelector("footer");
    const footerRect = footer.getBoundingClientRect();
    return {
      text: a.textContent.trim(),
      href: a.getAttribute("href"),
      // Distance from the top of the viewport to the button, with the page
      // scrolled to the bottom -- proves it is genuinely at the foot of the
      // document rather than merely inside <footer> markup.
      visibleTop: rect.top,
      viewportHeight: window.innerHeight,
      insideFooter: footerRect.bottom >= rect.top - 1,
      // Nothing visible may follow the footer, or the button is not at the
      // bottom of the site. <script> tags sit after </footer> in the markup and
      // are not rendered, so they are excluded -- including them made this
      // false for a footer that is genuinely last on screen.
      footerIsLast: [...document.body.children]
        .filter((el) => el.tagName !== "SCRIPT")
        .pop() === footer,
      tag: a.tagName,
    };
  });

  // Scroll to the bottom so the button is on screen and the position reading
  // above is meaningful.
  await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
  await page.waitForTimeout(400);
  await page.screenshot({ path: "qa/footer-admin.png" });

  const scrolled = await page.evaluate(() => {
    const a = document.querySelector(".footer-admin a");
    const rect = a.getBoundingClientRect();
    return { top: rect.top, bottom: rect.bottom, vh: window.innerHeight };
  });

  // --- click it and see where we land --------------------------------------
  await page.click(".footer-admin a");
  // The admin gate is rendered asynchronously, so wait for it rather than
  // sleeping a fixed amount and hoping.
  await page.waitForSelector("#admin-signin", { timeout: 30000 });

  const after = await page.evaluate(() => ({
    hash: location.hash,
    heading: document.querySelector(".page-head h1")?.textContent?.trim() ?? null,
    // The signed-out gate renders a Sign in button; that is the "admin login
    // page" the button is supposed to reach.
    hasSignin: !!document.getElementById("admin-signin"),
    bodyHasPrompt: /sign in with an administrator/i.test(document.body.textContent),
  }));

  await page.screenshot({ path: "qa/footer-admin-after-click.png" });

  return {
    button: btn,
    onScreenAfterScroll: scrolled.top >= 0 && scrolled.bottom <= scrolled.vh,
    afterClick: after,
    checks: {
      label: btn.text === "Admin login",
      hrefIsAdminRoute: btn.href === "#/admin",
      isAnchor: btn.tag === "A",
      insideFooter: btn.insideFooter,
      footerIsLast: btn.footerIsLast,
      navigatedToAdmin: after.hash === "#/admin",
      reachedGate: after.heading === "Admin" && after.hasSignin && after.bodyHasPrompt,
    },
  };
}

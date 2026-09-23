// Finds out WHY the glass layers are not visible: checks that each pseudo-element
// actually paints, what its computed background resolves to, and whether the
// scrim has swallowed the transparency.
export default async function run(page, ui) {
  await page.evaluate(() => {
    document.body.innerHTML = "";
    const root = document.createElement("div");
    root.id = "probe";
    root.style.cssText = [
      "position:fixed;inset:0;padding:30px;font-family:Inter,sans-serif",
      // A controlled, mid-tone backdrop so "is the fill transparent" is
      // answerable: if the button shows flat colour, it has a fill.
      "background:#6a4a8a",
    ].join(";");
    root.innerHTML = `
      <div style="display:flex;gap:14px;align-items:center;flex-wrap:wrap;margin-bottom:24px">
        <button class="btn" id="b1">Default</button>
        <button class="btn btn-primary" id="b2">Primary</button>
        <button class="btn btn-sm" id="b3">Small</button>
        <span class="chip" id="c1">Chip</span>
      </div>`;
    document.body.appendChild(root);
  });
  await page.waitForTimeout(400);

  return page.evaluate(() => {
    const px = (sel, pseudo) => {
      const el = document.getElementById(sel);
      const cs = getComputedStyle(el, pseudo);
      return {
        content: cs.content,
        bg: cs.backgroundImage === "none" ? "none" : cs.backgroundImage.slice(0, 80),
        opacity: cs.opacity,
        blend: cs.mixBlendMode,
        inset: cs.inset || `${cs.top} ${cs.right} ${cs.bottom} ${cs.left}`,
        shadow: cs.boxShadow.slice(0, 70),
        position: cs.position,
      };
    };
    const el = (sel) => {
      const e = document.getElementById(sel);
      const cs = getComputedStyle(e);
      return { bg: cs.backgroundImage.slice(0, 90), bgColor: cs.backgroundColor, color: cs.color };
    };
    return {
      // If ::after/::before report content "none" they are NOT being generated.
      b1: { after: px("b1", "::after"), before: px("b1", "::before"), self: el("b1") },
      c1: { after: px("c1", "::after"), before: px("c1", "::before"), self: el("c1") },
    };
  });
}

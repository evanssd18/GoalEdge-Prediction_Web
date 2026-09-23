// Renders the button set and a card over a saturated backdrop so the glass is
// actually visible, and reports the computed radius of each so the "cards a bit
// rounded, buttons very rounded" split is checked rather than assumed.
export default async function run(page, ui) {
  const info = await page.evaluate(() => {
    document.body.innerHTML = "";
    const root = document.createElement("div");
    root.style.cssText = [
      "position:fixed;inset:0;padding:34px;overflow:auto;font-family:Inter,sans-serif",
      // Strong colour behind everything: a transparent pane must let this through
      // undiluted, which is the whole point of the material.
      "background:linear-gradient(125deg,#1230b8 0%,#c2185b 34%,#ef8f00 62%,#0b8f4d 100%)",
    ].join(";");
    root.innerHTML = `
      <div style="display:flex;gap:14px;align-items:center;flex-wrap:wrap;margin-bottom:30px">
        <button class="btn">Default</button>
        <button class="btn btn-primary">Primary action</button>
        <button class="btn btn-ghost">Ghost</button>
        <button class="btn btn-sm">Small</button>
        <button class="btn btn-sm btn-primary">Small primary</button>
        <span class="chip">Chip</span>
        <span class="chip active">Chip active</span>
      </div>
      <div class="tip-card" style="max-width:420px;background:rgba(255,255,255,.12);padding:18px">
        <strong style="color:#fff">A card</strong>
        <p style="color:rgba(255,255,255,.85);margin:8px 0 0">
          Cards stay only a bit rounded -- soft corners, clearly not a pill.
        </p>
      </div>
    `;
    document.body.appendChild(root);

    const read = (el) => {
      const cs = getComputedStyle(el);
      const r = parseFloat(cs.borderTopLeftRadius);
      return {
        cls: el.className,
        text: (el.innerText || "").trim().slice(0, 16),
        radius: cs.borderTopLeftRadius,
        h: el.offsetHeight,
        // h/2 is the largest radius the box can show, i.e. a true pill.
        pill: r >= el.offsetHeight / 2,
      };
    };
    return {
      buttons: [...root.querySelectorAll(".btn,.chip")].map(read),
      card: read(root.querySelector(".tip-card")),
      tokens: {
        card: getComputedStyle(document.documentElement).getPropertyValue("--radius").trim(),
        btn: getComputedStyle(document.documentElement).getPropertyValue("--radius-btn").trim(),
      },
    };
  });
  await page.waitForTimeout(400);
  return info;
}

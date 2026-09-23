// Measures what is ACTUALLY behind each button, then checks the label contrast
// against it. A transparent pane's legibility depends on the backdrop, so this
// samples the real rendered pixels rather than reading a declared colour.
export default async function run(page, ui) {
  await page.evaluate(() => {
    document.body.innerHTML = "";
    const root = document.createElement("div");
    root.id = "probe";
    root.style.cssText = [
      "position:fixed;inset:0;padding:34px;font-family:Inter,sans-serif",
      "background:linear-gradient(125deg,#1230b8 0%,#c2185b 34%,#ef8f00 62%,#0b8f4d 100%)",
    ].join(";");
    root.innerHTML = `
      <div style="display:flex;gap:14px;align-items:center;flex-wrap:wrap">
        <button class="btn">Default</button>
        <button class="btn btn-primary">Primary action</button>
        <button class="btn btn-sm">Small</button>
        <span class="chip">Chip</span>
        <span class="chip active">Chip active</span>
      </div>`;
    document.body.appendChild(root);
  });
  await page.waitForTimeout(300);

  const rects = await page.evaluate(() =>
    [...document.querySelectorAll("#probe .btn,#probe .chip")].map((e) => {
      const r = e.getBoundingClientRect();
      return {
        cls: e.className,
        text: (e.innerText || "").trim(),
        color: getComputedStyle(e).color,
        x: Math.round(r.x),
        y: Math.round(r.y),
        w: Math.round(r.width),
        h: Math.round(r.height),
      };
    })
  );

  // Screenshot the strip and read the pixel just inside each button's left edge,
  // which is backdrop-plus-glass. That tells us the field the text sits on.
  const buf = await page.screenshot();
  await page.screenshot({ path: "qa/_probe-strip.png" });

  const lum = (c) => {
    const [r, g, b] = c.match(/\d+/g).slice(0, 3).map((n) => {
      const v = n / 255;
      return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4);
    });
    return 0.2126 * r + 0.7152 * g + 0.0722 * b;
  };

  return {
    // WCAG contrast of each label against its declared colour -- this is the
    // worst case, since a transparent pane can only be lighter than its backdrop.
    contrast: rects.map((r) => {
      const textIsLight = lum(r.color) > 0.5;
      const l1 = lum(r.color);
      const l2 = textIsLight ? 0.95 : 0.05; // approximate worst-case field
      const ratio = (Math.max(l1, l2) + 0.05) / (Math.min(l1, l2) + 0.05);
      return { cls: r.cls, text: r.text, color: r.color, worstCaseRatio: +ratio.toFixed(2) };
    }),
    rects,
    stripBytes: buf.length,
  };
}

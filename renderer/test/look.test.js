const test = require("node:test");
const assert = require("node:assert/strict");
const { chromium } = require("playwright");
const fs = require("fs");
const path = require("path");

const FABRIC_PATH = require.resolve("fabric/dist/fabric.min.js");
const PAGE_HTML = fs.readFileSync(path.join(__dirname, "../render-page.html"), "utf8");
const INJECT_PATH = path.join(__dirname, "../inject.js");

const TEMPLATES_PATH = path.join(__dirname, "../../backend/app/seeds/default_templates.json");
const defaultTemplates = JSON.parse(fs.readFileSync(TEMPLATES_PATH, "utf8"));

function getTemplate(name) {
  const entry = defaultTemplates.find((t) => t.name === name);
  if (!entry) throw new Error(`Template not found: ${name}`);
  return entry.template_json;
}

let browser;

test.before(async () => {
  browser = await chromium.launch({
    headless: true,
    args: ["--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage"],
  });
});

test.after(async () => {
  if (browser) await browser.close();
});

async function runInPage(fn, ...args) {
  const page = await browser.newPage();
  try {
    await page.setContent(PAGE_HTML);
    await page.addScriptTag({ path: FABRIC_PATH });
    await page.addScriptTag({ path: INJECT_PATH });
    return await page.evaluate(fn, ...args);
  } finally {
    await page.close();
  }
}

// 1. normalizeLook
test("1. normalizeLook parses string and object, falls back, handles invalid input", async () => {
  const result = await runInPage(() => {
    const r1 = window.normalizeLook("#ffc400");
    const r2 = window.normalizeLook("bad");
    const r3 = window.normalizeLook({ primary: "#2563EB", secondary: "#FFC21A" });
    return { r1, r2, r3 };
  });

  assert.deepEqual(result.r1, {
    primary: "#FFC400",
    secondary: "#FFC400",
    badge: "#FFC400",
    badgeText: "#0B0B0B",
    quoteMarks: "#FFC400",
    streak: true,
  });
  assert.equal(result.r2, null);
  assert.equal(result.r3.secondary, "#FFC21A");
  assert.equal(result.r3.badgeText, "#FFFFFF");
});

// 2. String compat
test("2. String compat: adaptTemplateForAccent string equals look object with primary", async () => {
  const gpNews = getTemplate("GP News — Breaking");
  const result = await runInPage((tpl) => {
    const fromStr = window.adaptTemplateForAccent(tpl, "#FFC400");
    const fromObj = window.adaptTemplateForAccent(tpl, { primary: "#FFC400" });
    return { fromStr, fromObj };
  }, gpNews);

  assert.deepEqual(result.fromStr, result.fromObj);
});

// 3. GP News with primary/secondary
test("3. GP News with primary/secondary look: title accent, no glow added, gradient stops, label fill", async () => {
  const gpNews = getTemplate("GP News — Breaking");
  const result = await runInPage((tpl) => {
    const adapted = window.adaptTemplateForAccent(tpl, { primary: "#2563EB", secondary: "#FFC21A" });
    const titleObj = adapted.objects.find((o) => o.placeholderRole === "title");
    const badgeObj = adapted.objects.find((o) => o.placeholderRole === "labelBadge");
    const labelObj = adapted.objects.find((o) => o.placeholderRole === "label");
    return {
      titleAccentColor: titleObj ? titleObj.titleAccentColor : null,
      glowColor: titleObj && titleObj.titleHighlightGlow ? titleObj.titleHighlightGlow.color : null,
      colorStops: badgeObj && badgeObj.fill ? badgeObj.fill.colorStops : null,
      labelFill: labelObj ? labelObj.fill : null,
    };
  }, gpNews);

  assert.equal(result.titleAccentColor, "#FFC21A");
  // Owner 2026-10-23: no glow on text; the look recolours a glow only when a template has one.
  assert.equal(result.glowColor, null);
  assert.ok(Array.isArray(result.colorStops) && result.colorStops.length === 2);
  const HEX_RE = /^#[0-9A-Fa-f]{6}$/;
  assert.match(result.colorStops[0].color, HEX_RE);
  assert.match(result.colorStops[1].color, HEX_RE);
  assert.notEqual(result.colorStops[0].color.toUpperCase(), "#F2523F");
  assert.notEqual(result.colorStops[1].color.toUpperCase(), "#F2523F");
  assert.equal(result.labelFill, "#FFFFFF");
});

// 4. Highlight News (flat pill)
test("4. Highlight News (flat pill): badge fill/discussionFill/hotFill and label fill/contrast", async () => {
  const hlNews = getTemplate("News Highlight — Red · Center");
  const result = await runInPage((tpl) => {
    const adapted1 = window.adaptTemplateForAccent(tpl, { primary: "#2563EB" });
    const b1 = adapted1.objects.find((o) => o.placeholderRole === "labelBadge");
    const l1 = adapted1.objects.find((o) => o.placeholderRole === "label");

    const adapted2 = window.adaptTemplateForAccent(tpl, { primary: "#C7F754" });
    const l2 = adapted2.objects.find((o) => o.placeholderRole === "label");

    return {
      b1Fill: b1 ? b1.fill : null,
      b1DiscussionFill: b1 ? b1.discussionFill : null,
      b1HotFill: b1 ? b1.hotFill : null,
      l1Fill: l1 ? l1.fill : null,
      l1DiscussionColor: l1 ? l1.discussionColor : null,
      l1HotColor: l1 ? l1.hotColor : null,
      l2Fill: l2 ? l2.fill : null,
      l2DiscussionColor: l2 ? l2.discussionColor : null,
      l2HotColor: l2 ? l2.hotColor : null,
      l2ShadowBlur: l2 && l2.shadow ? l2.shadow.blur : null,
    };
  }, hlNews);

  assert.equal(result.b1Fill, "#2563EB");
  assert.equal(result.b1DiscussionFill, "#2563EB");
  assert.equal(result.b1HotFill, "#2563EB");
  assert.equal(result.l1Fill, "#FFFFFF");
  assert.equal(result.l1DiscussionColor, "#FFFFFF");
  assert.equal(result.l1HotColor, "#FFFFFF");

  assert.equal(result.l2Fill, "#0B0B0B");
  assert.equal(result.l2DiscussionColor, "#0B0B0B");
  assert.equal(result.l2HotColor, "#0B0B0B");
  assert.equal(result.l2ShadowBlur, 0);
});

// 5. Highlight Quote
test("5. Highlight Quote with quoteMarks: quoteIcon textbox fill", async () => {
  const hlQuote = getTemplate("Quote Card (word-highlight)");
  const result = await runInPage((tpl) => {
    const adapted = window.adaptTemplateForAccent(tpl, { primary: "#C7F754", quoteMarks: "#FEFEFC" });
    const q = adapted.objects.find((o) => o.placeholderRole === "quoteIcon");
    return {
      quoteIconFill: q ? q.fill : null,
    };
  }, hlQuote);

  assert.equal(result.quoteIconFill, "#FEFEFC");
});

// 6. Classic Quote
test("6. Classic Quote with primary: subtitleBadge, subtitle, caption has no titleAccentColor, quoteIcon fill", async () => {
  const classicQuote = getTemplate("Quote Card (name badge)");
  const result = await runInPage((tpl) => {
    const adapted = window.adaptTemplateForAccent(tpl, { primary: "#FFC21A" });
    const sb = adapted.objects.find((o) => o.placeholderRole === "subtitleBadge");
    const sub = adapted.objects.find((o) => o.placeholderRole === "subtitle");
    const cap = adapted.objects.find((o) => o.placeholderRole === "caption");
    const q = adapted.objects.find((o) => o.placeholderRole === "quoteIcon");
    return {
      subtitleBadgeFill: sb ? sb.fill : null,
      subtitleFill: sub ? sub.fill : null,
      captionHasAccent: cap ? "titleAccentColor" in cap && Boolean(cap.titleAccentColor) : false,
      quoteIconFill: q ? q.fill : null,
    };
  }, classicQuote);

  assert.equal(result.subtitleBadgeFill, "#FFC21A");
  assert.equal(result.subtitleFill, "#0B0B0B");
  assert.equal(result.captionHasAccent, false);
  assert.equal(result.quoteIconFill, "#FFC21A");
});

// 7. GP Quote
test("7. GP Quote with quoteMarks and streak:false: tab rect gradient, path fill, streak visibility", async () => {
  const gpQuote = getTemplate("GP Quote");
  const result = await runInPage((tpl) => {
    const adapted1 = window.adaptTemplateForAccent(tpl, { primary: "#E8362B", quoteMarks: "#FFC21A" });
    const q1 = adapted1.objects.find((o) => o.placeholderRole === "quoteIcon");
    const rectChild1 = q1 && Array.isArray(q1.objects) ? q1.objects.find((o) => o.type === "rect") : null;
    const pathChildren1 = q1 && Array.isArray(q1.objects) ? q1.objects.filter((o) => o.type === "path") : [];

    const adapted2 = window.adaptTemplateForAccent(tpl, { primary: "#E8362B", streak: false });
    const q2 = adapted2.objects.find((o) => o.placeholderRole === "quoteIcon");
    const imgChild2 = q2 && Array.isArray(q2.objects) ? q2.objects.find((o) => o.type === "image") : null;

    return {
      rectStops: rectChild1 && rectChild1.fill ? rectChild1.fill.colorStops : null,
      pathFills: pathChildren1.map((p) => p.fill),
      imgVisible: imgChild2 ? imgChild2.visible : undefined,
    };
  }, gpQuote);

  assert.ok(Array.isArray(result.rectStops) && result.rectStops.length === 2);
  const HEX_RE = /^#[0-9A-Fa-f]{6}$/;
  assert.match(result.rectStops[0].color, HEX_RE);
  assert.match(result.rectStops[1].color, HEX_RE);
  assert.notEqual(result.rectStops[0].color.toUpperCase(), "#F2523F");
  assert.notEqual(result.rectStops[1].color.toUpperCase(), "#D72C23");
  assert.ok(result.pathFills.length > 0);
  for (const fill of result.pathFills) {
    assert.equal(fill, "#0B0B0B");
  }
  assert.equal(result.imgVisible, false);
});

// 8. Full render Highlight News
test("8. Full render: News Highlight with accent primary renders labelBadge fill on canvas", async () => {
  const baseTpl = getTemplate("News Highlight — Red · Center");
  const result = await runInPage(async (tpl) => {
    const tplCopy = JSON.parse(JSON.stringify(tpl));
    tplCopy.accentFollowsFanpage = true;

    let renderedBadgeFill = null;
    const origRender = fabric.StaticCanvas.prototype.renderAll;
    fabric.StaticCanvas.prototype.renderAll = function () {
      const objs = this.getObjects();
      const b = objs.find((o) => o.placeholderRole === "labelBadge");
      if (b) renderedBadgeFill = b.fill;
      return origRender.apply(this, arguments);
    };

    try {
      await window.renderTemplate({
        templateJson: tplCopy,
        width: 1080,
        height: 1350,
        label: "BREAKING",
        title: "Test headline",
        accent: { primary: "#2563EB" },
      });
    } finally {
      fabric.StaticCanvas.prototype.renderAll = origRender;
    }

    return { renderedBadgeFill };
  }, baseTpl);

  assert.equal(result.renderedBadgeFill, "#2563EB");
});

// 9. Full render GP Quote
test("9. Full render: GP Quote with streak:false has non-visible streak image on canvas", async () => {
  const baseTpl = getTemplate("GP Quote");
  const result = await runInPage(async (tpl) => {
    const tplCopy = JSON.parse(JSON.stringify(tpl));
    tplCopy.accentFollowsFanpage = true;

    let streakVisible = null;
    const origRender = fabric.StaticCanvas.prototype.renderAll;
    fabric.StaticCanvas.prototype.renderAll = function () {
      const objs = this.getObjects();
      const q = objs.find((o) => o.placeholderRole === "quoteIcon");
      if (q && q.getObjects) {
        const img = q.getObjects().find((o) => o.type === "image");
        if (img) streakVisible = img.visible;
      }
      return origRender.apply(this, arguments);
    };

    try {
      await window.renderTemplate({
        templateJson: tplCopy,
        width: 1080,
        height: 1350,
        title: "Test quote headline",
        accent: { primary: "#E8362B", streak: false },
      });
    } finally {
      fabric.StaticCanvas.prototype.renderAll = origRender;
    }

    return { streakVisible };
  }, baseTpl);

  assert.equal(result.streakVisible, false);
});

// Owner 2026-10-24: a bright red streak recoloured to the accent must stay bright (it vanished when tinted by luma).
test("recolorStreakDataUri keeps a bright red pixel at full accent strength", async () => {
  const result = await runInPage(async () => {
    const c = document.createElement("canvas");
    c.width = 2; c.height = 1;
    const ctx = c.getContext("2d");
    ctx.fillStyle = "#FF2020"; ctx.fillRect(0, 0, 2, 1);
    const out = await window.recolorStreakDataUri(c.toDataURL("image/png"), "#2563EB");
    const img = new Image();
    await new Promise((r) => { img.onload = r; img.src = out; });
    const c2 = document.createElement("canvas"); c2.width = 2; c2.height = 1;
    const ctx2 = c2.getContext("2d"); ctx2.drawImage(img, 0, 0);
    return Array.from(ctx2.getImageData(0, 0, 1, 1).data);
  }, null);
  assert.ok(Math.abs(result[0] - 0x25) <= 2 && Math.abs(result[1] - 0x63) <= 2 && Math.abs(result[2] - 0xEB) <= 2, String(result));
});

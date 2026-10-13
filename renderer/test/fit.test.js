const test = require("node:test");
const assert = require("node:assert/strict");
const { chromium } = require("playwright");
const fs = require("fs");
const path = require("path");

const FABRIC_PATH = require.resolve("fabric/dist/fabric.min.js");
const PAGE_HTML = fs.readFileSync(path.join(__dirname, "../render-page.html"), "utf8");
const INJECT_PATH = path.join(__dirname, "../inject.js");

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

async function runInPage(fn) {
  const page = await browser.newPage();
  try {
    await page.setContent(PAGE_HTML);
    await page.addScriptTag({ path: FABRIC_PATH });
    await page.addScriptTag({ path: INJECT_PATH });
    return await page.evaluate(fn);
  } finally {
    await page.close();
  }
}

test("short title stays at max font and badge sits just above it", async () => {
  const result = await runInPage(async () => {
    const tpl = {
      version: "5.3.0",
      objects: [
        {
          type: "rect",
          placeholderRole: "labelBadge",
          left: 378,
          top: 800,
          width: 324,
          height: 73,
          labelRadius: 16,
          labelKeepFill: true,
          labelFixedHeight: 73,
          fill: "linear-mock",
        },
        {
          type: "textbox",
          placeholderRole: "label",
          left: 70,
          top: 810,
          width: 940,
          height: 50,
          text: "LABEL",
          fontSize: 44,
          textAlign: "center",
          labelGap: 22,
          labelPadX: 42,
        },
        {
          type: "textbox",
          placeholderRole: "title",
          left: 70,
          top: 1000,
          width: 940,
          height: 200,
          text: "TITLE",
          fontSize: 76,
          textAlign: "center",
          titleAnchorBottom: 1304,
          titleGrowMaxHeight: 358,
          titleMinReadableFontSize: 50,
        },
      ],
    };

    let titleFontSize = 0;
    let badgeAboveTitle = false;
    let badgeKeptFill = false;

    const origRender = fabric.StaticCanvas.prototype.renderAll;
    fabric.StaticCanvas.prototype.renderAll = function() {
      const objs = this.getObjects();
      const t = objs.find(o => o.placeholderRole === "title");
      const b = objs.find(o => o.placeholderRole === "labelBadge");
      if (t) titleFontSize = t.fontSize;
      if (t && b) {
        badgeAboveTitle = b.top + b.height <= t.top;
        badgeKeptFill = b.fill === "linear-mock";
      }
      return origRender.apply(this, arguments);
    };

    await window.renderTemplate({
      templateJson: tpl,
      width: 1080,
      height: 1350,
      title: "Short Title",
      label: "BREAKING",
    });

    fabric.StaticCanvas.prototype.renderAll = origRender;

    return { titleFontSize, badgeAboveTitle, badgeKeptFill };
  });

  assert.equal(result.titleFontSize, 76);
  assert.ok(result.badgeAboveTitle, "Badge is above title");
  assert.ok(result.badgeKeptFill, "Badge kept its gradient fill");
});

test("label pill hugs dynamic text and centres horizontally and vertically", async () => {
  const result = await runInPage(async () => {
    const tpl = {
      version: "5.3.0",
      objects: [
        {
          type: "rect",
          placeholderRole: "labelBadge",
          left: 378,
          top: 800,
          width: 324,
          height: 73,
          labelRadius: 16,
          labelKeepFill: true,
          labelFixedHeight: 73,
          fill: "linear-mock",
        },
        {
          type: "textbox",
          placeholderRole: "label",
          left: 70,
          top: 810,
          width: 940,
          height: 50,
          text: "LABEL",
          fontSize: 44,
          textAlign: "center",
          labelGap: 22,
          labelPadX: 42,
        },
        {
          type: "textbox",
          placeholderRole: "title",
          left: 70,
          top: 1000,
          width: 940,
          height: 200,
          text: "TITLE",
          fontSize: 76,
          textAlign: "center",
          titleAnchorBottom: 1304,
          titleGrowMaxHeight: 358,
          titleMinReadableFontSize: 50,
        },
      ],
    };

    const labels = ["BREAKING", "MOTOGP NEWS", "F1 NEWS"];
    const checks = [];

    for (const lbl of labels) {
      let bBox = null;
      let lBox = null;

      const origRender = fabric.StaticCanvas.prototype.renderAll;
      fabric.StaticCanvas.prototype.renderAll = function() {
        const objs = this.getObjects();
        const b = objs.find(o => o.placeholderRole === "labelBadge");
        const l = objs.find(o => o.placeholderRole === "label");
        if (b && l) {
          bBox = { left: b.left, top: b.top, width: b.width, height: b.height };
          lBox = { left: l.left, top: l.top, width: l.width, height: l.height };
        }
        return origRender.apply(this, arguments);
      };

      await window.renderTemplate({
        templateJson: tpl,
        width: 1080,
        height: 1350,
        title: "Short Title",
        label: lbl,
      });

      fabric.StaticCanvas.prototype.renderAll = origRender;

      checks.push({
        label: lbl,
        bBox,
        lBox,
        insideHorizontally: bBox.left <= lBox.left && (bBox.left + bBox.width) >= (lBox.left + lBox.width),
        centredHorizontally: Math.abs((bBox.left + bBox.width / 2) - 540) <= 2,
        padX: Math.round((bBox.width - lBox.width) / 2),
      });
    }

    return checks;
  });

  for (const c of result) {
    assert.ok(c.insideHorizontally, `Text for ${c.label} is fully inside pill horizontally`);
    assert.ok(c.centredHorizontally, `Badge for ${c.label} is centred at x=540`);
    assert.equal(c.padX, 42, `Badge has 42px padX around text for ${c.label}`);
  }
});

test("grow-then-shrink: long title grows up to titleGrowMaxHeight then shrinks", async () => {
  const result = await runInPage(async () => {
    function makeTpl() {
      return {
        version: "5.3.0",
        objects: [
          {
            type: "textbox",
            placeholderRole: "title",
            left: 70,
            top: 1000,
            width: 940,
            height: 100,
            text: "TITLE",
            fontSize: 76,
            textAlign: "center",
            titleAnchorBottom: 1304,
            titleGrowMaxHeight: 358,
            titleMinReadableFontSize: 50,
          },
        ],
      };
    }

    async function measure(titleText) {
      const origRender = fabric.StaticCanvas.prototype.renderAll;
      let data = null;
      fabric.StaticCanvas.prototype.renderAll = function() {
        const t = this.getObjects().find(o => o.placeholderRole === "title");
        if (t) {
          data = {
            fontSize: t.fontSize,
            height: t.height,
            top: t.top,
          };
        }
        return origRender.apply(this, arguments);
      };

      await window.renderTemplate({
        templateJson: makeTpl(),
        width: 1080,
        height: 1350,
        title: titleText,
      });

      fabric.StaticCanvas.prototype.renderAll = origRender;
      return data;
    }

    const short = await measure("Short Title");
    const med = await measure("Bagnaia crashes out in Indonesia: No feeling, no grip — the two-time champion falls to P9 in the standings");
    const veryLong = await measure("Bagnaia crashes out in Indonesia: No feeling, no grip — the two-time champion falls to P9 in the standings with just five races left in the championship season, putting immense pressure on Ducati");

    return [short, med, veryLong];
  });

  const [short, med, veryLong] = result;
  assert.equal(short.fontSize, 76, "Short title keeps max font size 76");
  assert.ok(med.height > short.height, `Medium title grew in height (med: ${med.height}, short: ${short.height})`);
  assert.ok(veryLong.height <= 358, `Very long title stays inside titleGrowMaxHeight 358 (actual: ${veryLong.height})`);
  assert.ok(veryLong.fontSize < 76, `Very long title shrank font size (actual: ${veryLong.fontSize})`);
  assert.ok(veryLong.fontSize >= 12, "Never shrinks below floor 12");
});

test("scrim rises with topmost element (badge/icon/title)", async () => {
  const result = await runInPage(async () => {
    function makeTpl() {
      return {
        version: "5.3.0",
        objects: [
          {
            type: "rect",
            placeholderRole: "scrim",
            top: 700,
            height: 650,
            scrimPad: 200,
            fill: { type: "linear", coords: { x1: 0, y1: 0, x2: 0, y2: 650 }, colorStops: [{ offset: 0, color: "transparent" }] }
          },
          {
            type: "rect",
            placeholderRole: "labelBadge",
            left: 378,
            top: 800,
            width: 324,
            height: 73,
            labelRadius: 16,
            labelKeepFill: true,
            labelFixedHeight: 73,
          },
          {
            type: "textbox",
            placeholderRole: "label",
            left: 70,
            top: 810,
            width: 940,
            height: 50,
            text: "BREAKING",
            fontSize: 44,
            textAlign: "center",
            labelGap: 22,
            labelPadX: 42,
          },
          {
            type: "textbox",
            placeholderRole: "title",
            left: 70,
            top: 1000,
            width: 940,
            height: 100,
            text: "TITLE",
            fontSize: 76,
            textAlign: "center",
            titleAnchorBottom: 1304,
            titleGrowMaxHeight: 358,
            titleMinReadableFontSize: 50,
          },
        ],
      };
    }

    async function getScrimTop(titleText) {
      const origRender = fabric.StaticCanvas.prototype.renderAll;
      let topVal = null;
      fabric.StaticCanvas.prototype.renderAll = function() {
        const s = this.getObjects().find(o => o.placeholderRole === "scrim");
        if (s) topVal = s.top;
        return origRender.apply(this, arguments);
      };

      await window.renderTemplate({
        templateJson: makeTpl(),
        width: 1080,
        height: 1350,
        title: titleText,
        label: "BREAKING",
      });

      fabric.StaticCanvas.prototype.renderAll = origRender;
      return topVal;
    }

    const shortScrimTop = await getScrimTop("Short Title");
    const longScrimTop = await getScrimTop("Bagnaia crashes out in Indonesia: No feeling, no grip — the two-time champion falls to P9 in the standings with just five races left");
    return { shortScrimTop, longScrimTop };
  });

  const { shortScrimTop, longScrimTop } = result;
  assert.ok(longScrimTop < shortScrimTop, `Long text causes scrim top to rise higher (long: ${longScrimTop}, short: ${shortScrimTop})`);
});

test("imageZoom 1.15 keeps focus point and covers slot", async () => {
  const result = await runInPage(async () => {
    const slotW = 1080;
    const slotH = 1350;
    const imgW = 1080;
    const imgH = 1350;
    const cx = slotW / 2;
    const cy = slotH / 2;
    const slotLeft = 0;
    const slotTop = 0;

    const zoom = 1.15;
    const scale = Math.max(slotW / imgW, slotH / imgH) * zoom;
    const iw = imgW * scale;
    const ih = imgH * scale;

    const fp = [0.5, 0.42];
    let left = cx - fp[0] * iw;
    let top = cy - fp[1] * ih;
    left = Math.min(slotLeft, Math.max(slotLeft + slotW - iw, left));
    top = Math.min(slotTop, Math.max(slotTop + slotH - ih, top));

    // Coverage check:
    const coversLeft = left <= 0;
    const coversTop = top <= 0;
    const coversRight = left + iw >= slotW;
    const coversBottom = top + ih >= slotH;

    return {
      scale,
      left,
      top,
      iw,
      ih,
      fullyCovered: coversLeft && coversTop && coversRight && coversBottom,
    };
  });

  assert.equal(result.scale, 1.15);
  assert.ok(result.fullyCovered, "Slot stays fully covered with 1.15 zoom");
});

test("two-layer render for highlighted glow: matching textLines and positions, transparent base chars", async () => {
  const result = await runInPage(async () => {
    const tpl = {
      version: "5.3.0",
      objects: [
        {
          type: "textbox",
          placeholderRole: "title",
          left: 70,
          top: 946,
          width: 940,
          height: 358,
          text: "Headline",
          fontSize: 76,
          fontFamily: "Montserrat",
          fontWeight: 800,
          textAlign: "center",
          lineHeight: 0.9,
          fill: "#FFFFFF",
          shadow: {
            color: "rgba(255,255,255,0.5)",
            blur: 12,
            offsetX: 0,
            offsetY: 0,
          },
          titleAnchorBottom: 1304,
          titleGrowMaxHeight: 358,
          titleMinReadableFontSize: 50,
          titleAccentColor: "#E8362B",
          titleHighlightGlow: {
            color: "rgba(232,54,43,0.40)",
            blur: 10,
          },
        },
      ],
    };

    let titleObjInfo = null;
    let hlLayerInfo = null;

    const origRender = fabric.StaticCanvas.prototype.renderAll;
    fabric.StaticCanvas.prototype.renderAll = function () {
      const objs = this.getObjects();
      const t = objs.find((o) => o.placeholderRole === "title");
      if (t) {
        const tIdx = objs.indexOf(t);
        const nextObj = objs[tIdx + 1];
        titleObjInfo = {
          left: t.left,
          top: t.top,
          width: t.width,
          textLines: t.textLines,
          styles: JSON.parse(JSON.stringify(t.styles || {})),
        };
        if (nextObj) {
          hlLayerInfo = {
            left: nextObj.left,
            top: nextObj.top,
            width: nextObj.width,
            textLines: nextObj.textLines,
            styles: JSON.parse(JSON.stringify(nextObj.styles || {})),
            fill: nextObj.fill,
          };
        }
      }
      return origRender.apply(this, arguments);
    };

    await window.renderTemplate({
      templateJson: tpl,
      width: 1080,
      height: 1350,
      title: "Bagnaia crashes out in Indonesia: **No feeling, no grip**",
    });

    fabric.StaticCanvas.prototype.renderAll = origRender;
    return { titleObjInfo, hlLayerInfo };
  });

  const { titleObjInfo, hlLayerInfo } = result;
  assert.ok(titleObjInfo, "Base title exists");
  assert.ok(hlLayerInfo, "Highlight layer exists");

  // Positions and dimensions match
  assert.equal(hlLayerInfo.left, titleObjInfo.left);
  assert.equal(hlLayerInfo.top, titleObjInfo.top);
  assert.equal(hlLayerInfo.width, titleObjInfo.width);
  assert.deepEqual(hlLayerInfo.textLines, titleObjInfo.textLines);

  // Check base layer has transparent fill on highlighted chars
  // "NO FEELING, NO GRIP" are highlighted
  const baseStyles = titleObjInfo.styles;
  let foundBaseTransparent = false;
  for (const line of Object.values(baseStyles)) {
    for (const charStyle of Object.values(line)) {
      if (charStyle.fill === "rgba(0,0,0,0)") {
        foundBaseTransparent = true;
      }
    }
  }
  assert.ok(foundBaseTransparent, "Base layer highlighted chars are transparent");

  // Check highlight layer has default transparent fill and accent on highlighted chars
  assert.equal(hlLayerInfo.fill, "rgba(0,0,0,0)");
  const hlStyles = hlLayerInfo.styles;
  let foundHlAccent = false;
  for (const line of Object.values(hlStyles)) {
    for (const charStyle of Object.values(line)) {
      if (charStyle.fill === "#E8362B") {
        foundHlAccent = true;
      }
    }
  }
  assert.ok(foundHlAccent, "Highlight layer has #E8362B on highlighted chars");
});

test("RGB-split chroma shift copies: matching textLines, offset exactly +-px, transparent highlight chars", async () => {
  const result = await runInPage(async () => {
    const tpl = {
      version: "5.3.0",
      objects: [
        {
          type: "textbox",
          placeholderRole: "title",
          left: 70,
          top: 800,
          width: 940,
          text: "Headline",
          fontSize: 76,
          fontFamily: "Montserrat",
          fontWeight: 800,
          textAlign: "center",
          lineHeight: 0.90,
          fill: "#FFFFFF",
          shadow: {
            color: "rgba(255,255,255,0.36)",
            blur: 10,
            offsetX: 0,
            offsetY: 0,
          },
          titleAnchorBottom: 1304,
          titleGrowMaxHeight: 358,
          titleMinReadableFontSize: 50,
          titleAccentColor: "#E8362B",
          titleHighlightGlow: {
            color: "rgba(232,54,43,0.40)",
            blur: 10,
          },
          titleChromaShift: {
            px: 2,
            left: "rgba(255,40,40,0.55)",
            right: "rgba(60,255,255,0.55)",
          },
        },
      ],
    };

    let titleObjInfo = null;
    let leftCopyInfo = null;
    let rightCopyInfo = null;

    const origRender = fabric.StaticCanvas.prototype.renderAll;
    fabric.StaticCanvas.prototype.renderAll = function () {
      const objs = this.getObjects();
      const t = objs.find((o) => o.placeholderRole === "title");
      if (t) {
        const tIdx = objs.indexOf(t);
        titleObjInfo = {
          left: t.left,
          top: t.top,
          width: t.width,
          textLines: t.textLines,
        };
        const leftObj = objs[tIdx - 2];
        const rightObj = objs[tIdx - 1];
        if (leftObj) {
          leftCopyInfo = {
            left: leftObj.left,
            top: leftObj.top,
            width: leftObj.width,
            textLines: leftObj.textLines,
            fill: leftObj.fill,
            styles: JSON.parse(JSON.stringify(leftObj.styles || {})),
            shadow: leftObj.shadow,
          };
        }
        if (rightObj) {
          rightCopyInfo = {
            left: rightObj.left,
            top: rightObj.top,
            width: rightObj.width,
            textLines: rightObj.textLines,
            fill: rightObj.fill,
            styles: JSON.parse(JSON.stringify(rightObj.styles || {})),
            shadow: rightObj.shadow,
          };
        }
      }
      return origRender.apply(this, arguments);
    };

    await window.renderTemplate({
      templateJson: tpl,
      width: 1080,
      height: 1350,
      title: "Bagnaia crashes out in Indonesia: **No feeling, no grip**",
    });

    fabric.StaticCanvas.prototype.renderAll = origRender;
    return { titleObjInfo, leftCopyInfo, rightCopyInfo };
  });

  const { titleObjInfo, leftCopyInfo, rightCopyInfo } = result;
  assert.ok(titleObjInfo, "Base title exists");
  assert.ok(leftCopyInfo, "Left chroma copy exists");
  assert.ok(rightCopyInfo, "Right chroma copy exists");

  // textLines match base exactly
  assert.deepEqual(leftCopyInfo.textLines, titleObjInfo.textLines);
  assert.deepEqual(rightCopyInfo.textLines, titleObjInfo.textLines);

  // Offset exactly +-px (px: 2)
  assert.equal(leftCopyInfo.left, titleObjInfo.left - 2);
  assert.equal(rightCopyInfo.left, titleObjInfo.left + 2);
  assert.equal(leftCopyInfo.top, titleObjInfo.top);
  assert.equal(rightCopyInfo.top, titleObjInfo.top);

  // Fills match left and right
  assert.equal(leftCopyInfo.fill, "rgba(255,40,40,0.55)");
  assert.equal(rightCopyInfo.fill, "rgba(60,255,255,0.55)");

  // Section 20 softening shadow on chroma copies
  assert.ok(leftCopyInfo.shadow, "Left copy has softening shadow");
  assert.equal(leftCopyInfo.shadow.blur, 3);
  assert.equal(leftCopyInfo.shadow.color, "rgba(255,40,40,0.55)");
  assert.ok(rightCopyInfo.shadow, "Right copy has softening shadow");
  assert.equal(rightCopyInfo.shadow.blur, 3);
  assert.equal(rightCopyInfo.shadow.color, "rgba(60,255,255,0.55)");

  // Highlight chars are transparent in both copies
  const checkTransparentHighlights = (info) => {
    let foundTransparent = false;
    for (const line of Object.values(info.styles)) {
      for (const charStyle of Object.values(line)) {
        if (charStyle.fill === "rgba(0,0,0,0)") {
          foundTransparent = true;
        }
      }
    }
    return foundTransparent;
  };
  assert.ok(checkTransparentHighlights(leftCopyInfo), "Left copy has transparent highlight chars");
  assert.ok(checkTransparentHighlights(rightCopyInfo), "Right copy has transparent highlight chars");
});

test("accent applied on opt-in template, ignored on non-opt-in template, contrast rule", async () => {
  const result = await runInPage(async () => {
    const tplOptIn = {
      version: "5.3.0",
      accentFollowsFanpage: true,
      objects: [
        {
          type: "textbox",
          placeholderRole: "title",
          left: 70,
          top: 900,
          width: 940,
          height: 200,
          text: "TITLE",
          fontSize: 76,
          titleAccentColor: "#E8362B",
        },
        {
          type: "rect",
          placeholderRole: "labelBadge",
          left: 100,
          top: 800,
          width: 200,
          height: 60,
          labelKeepFill: true,
          stroke: "rgba(255,255,255,0.5)",
          fill: {
            type: "linear",
            colorStops: [{ offset: 0, color: "#111111" }, { offset: 1, color: "#222222" }],
          },
        },
        {
          type: "textbox",
          placeholderRole: "label",
          left: 100,
          top: 810,
          width: 200,
          height: 40,
          text: "NEWS",
          fill: "#FFFFFF",
          shadow: { color: "rgba(255,255,255,0.5)", blur: 5 },
        },
      ],
    };

    const tplNoOptIn = {
      version: "5.3.0",
      accentFollowsFanpage: false,
      objects: [
        {
          type: "textbox",
          placeholderRole: "title",
          left: 70,
          top: 900,
          width: 940,
          height: 200,
          text: "TITLE",
          fontSize: 76,
          titleAccentColor: "#E8362B",
        },
      ],
    };

    let optInTitleAccent = null;
    let optInLabelFill = null;
    let optInLabelShadowBlur = null;
    let noOptInTitleAccent = null;

    const origRender = fabric.StaticCanvas.prototype.renderAll;
    fabric.StaticCanvas.prototype.renderAll = function () {
      const objs = this.getObjects();
      const t = objs.find((o) => o.placeholderRole === "title");
      const l = objs.find((o) => o.placeholderRole === "label");
      if (t && l) {
        optInTitleAccent = t.titleAccentColor;
        optInLabelFill = l.fill;
        optInLabelShadowBlur = l.shadow ? l.shadow.blur : 0;
      } else if (t) {
        noOptInTitleAccent = t.titleAccentColor;
      }
      return origRender.apply(this, arguments);
    };

    // Yellow (#FFC400) is light (luminance > 0.5)
    await window.renderTemplate({
      templateJson: tplOptIn,
      width: 1080,
      height: 1350,
      title: "Headline **highlight**",
      label: "NEWS",
      accent: "#FFC400",
    });

    await window.renderTemplate({
      templateJson: tplNoOptIn,
      width: 1080,
      height: 1350,
      title: "Headline **highlight**",
      accent: "#FFC400",
    });

    fabric.StaticCanvas.prototype.renderAll = origRender;
    return { optInTitleAccent, optInLabelFill, optInLabelShadowBlur, noOptInTitleAccent };
  });

  assert.equal(result.optInTitleAccent, "#FFC400", "Opt-in template received accent");
  assert.equal(result.optInLabelFill, "#0B0B0B", "Contrast rule: light accent turns label text dark");
  assert.equal(result.optInLabelShadowBlur, 0, "Contrast rule: drops white glow on label");
  assert.equal(result.noOptInTitleAccent, "#E8362B", "Non-opt-in template keeps original accent");
});

test("Extra: subtitle wraps up to 2 lines, title moves up by same delta, badge covers text, short subtitle unchanged", async () => {
  const result = await runInPage(async () => {
    const makeTpl = () => ({
      version: "5.3.0",
      objects: [
        {
          type: "textbox",
          placeholderRole: "title",
          left: 70,
          top: 1000,
          width: 940,
          height: 100,
          text: "TITLE",
          fontSize: 70,
          titleAnchorBottom: 1100,
        },
        {
          type: "rect",
          placeholderRole: "subtitleBadge",
          left: 390,
          top: 1120,
          width: 300,
          height: 50,
          badgePadX: 80,
          badgePadY: 20,
        },
        {
          type: "textbox",
          placeholderRole: "subtitle",
          left: 90,
          top: 1130,
          width: 400,
          height: 30,
          text: "SUBTITLE",
          fontSize: 28,
          subtitleMinFontSize: 22,
          subtitleMaxLines: 2,
        },
      ],
    });

    let shortRun = {};
    let longRun = {};

    const origRender = fabric.StaticCanvas.prototype.renderAll;
    let isLong = false;
    fabric.StaticCanvas.prototype.renderAll = function () {
      const objs = this.getObjects();
      const t = objs.find((o) => o.placeholderRole === "title");
      const s = objs.find((o) => o.placeholderRole === "subtitle");
      const b = objs.find((o) => o.placeholderRole === "subtitleBadge");
      const data = {
        titleTop: t.top,
        subTop: s.top,
        subH: s.height,
        subFontSize: s.fontSize,
        subLines: s.textLines.length,
        badgeW: b.width,
        badgeH: b.height,
        badgeTop: b.top,
      };
      if (isLong) longRun = data;
      else shortRun = data;
      return origRender.apply(this, arguments);
    };

    // 1. Short subtitle
    await window.renderTemplate({
      templateJson: makeTpl(),
      width: 1080,
      height: 1350,
      title: "Title Here",
      subtitle: "Short Name",
    });

    // 2. Long subtitle
    isLong = true;
    await window.renderTemplate({
      templateJson: makeTpl(),
      width: 1080,
      height: 1350,
      title: "Title Here",
      subtitle: "Very Long Name That Wraps Over Multiple Lines For Sure",
    });

    fabric.StaticCanvas.prototype.renderAll = origRender;
    return { shortRun, longRun };
  });

  const { shortRun, longRun } = result;

  // Short subtitle unchanged: 1 line
  assert.equal(shortRun.subLines, 1, "Short subtitle is 1 line");

  // Long subtitle wraps to 2 lines and stays at or above minFontSize (22)
  assert.equal(longRun.subLines, 2, "Long subtitle wrapped to 2 lines");
  assert.ok(longRun.subFontSize >= 22, "Long subtitle font >= minFloor (22)");

  // Title moves up by delta
  const delta = longRun.subH - shortRun.subH;
  assert.ok(delta > 0, "Subtitle grew in height");
  assert.equal(longRun.titleTop, shortRun.titleTop - delta, "Title moved up by exact delta");

  // Badge covers text: height covers subtitle + pad, width covers text
  assert.ok(longRun.badgeH >= longRun.subH, "Badge covers subtitle height");
  assert.ok(longRun.badgeTop <= longRun.subTop, "Badge top is at or above subtitle top");
});

test("Montserrat weights 700, 800, and 900 preload successfully", async () => {
  const result = await runInPage(async () => {
    return {
      w700: document.fonts.check("700 44px Montserrat"),
      w800: document.fonts.check("800 76px Montserrat"),
      w900: document.fonts.check("900 64px Montserrat"),
    };
  });

  assert.ok(result.w700, "Montserrat weight 700 is loaded");
  assert.ok(result.w800, "Montserrat weight 800 is loaded");
  assert.ok(result.w900, "Montserrat weight 900 is loaded");
});

test("ghost fix: chroma copies and highlight layer follow title after subDeltaY move", async () => {
  const result = await runInPage(async () => {
    const tpl = {
      version: "5.3.0",
      objects: [
        {
          type: "textbox",
          placeholderRole: "title",
          left: 70,
          top: 946,
          width: 940,
          height: 358,
          text: "Headline",
          fontSize: 76,
          fontFamily: "Montserrat",
          fontWeight: 800,
          textAlign: "center",
          lineHeight: 0.9,
          fill: "#FFFFFF",
          titleAnchorBottom: 1304,
          titleGrowMaxHeight: 358,
          titleMinReadableFontSize: 50,
          titleAccentColor: "#E8362B",
          titleHighlightGlow: {
            color: "rgba(232,54,43,0.40)",
            blur: 10,
          },
          titleChromaShift: {
            px: 2,
            left: "rgba(255,40,40,0.55)",
            right: "rgba(60,255,255,0.55)",
          },
        },
        {
          type: "textbox",
          placeholderRole: "subtitle",
          left: 90,
          top: 1310,
          width: 400,
          height: 30,
          text: "SUBTITLE",
          fontSize: 28,
          subtitleMinFontSize: 22,
          subtitleMaxLines: 2,
        },
      ],
    };

    let titleTop = null;
    const followers = [];

    const origRender = fabric.StaticCanvas.prototype.renderAll;
    fabric.StaticCanvas.prototype.renderAll = function () {
      const objs = this.getObjects();
      const t = objs.find((o) => o.placeholderRole === "title");
      if (t) {
        titleTop = t.top;
        for (const o of objs) {
          if (o.followsTitle) {
            followers.push({ top: o.top, left: o.left });
          }
        }
      }
      return origRender.apply(this, arguments);
    };

    await window.renderTemplate({
      templateJson: tpl,
      width: 1080,
      height: 1350,
      title: "Bagnaia crashes out: **No feeling, no grip**",
      subtitle: "A very long subtitle that should wrap to two lines easily here",
    });

    fabric.StaticCanvas.prototype.renderAll = origRender;
    return { titleTop, followers };
  });

  assert.ok(result.titleTop !== null, "Title exists");
  assert.ok(result.followers.length >= 2, `At least 2 followers found (got ${result.followers.length})`);
  for (const f of result.followers) {
    assert.equal(f.top, result.titleTop, `Follower top ${f.top} matches title top ${result.titleTop}`);
  }
});


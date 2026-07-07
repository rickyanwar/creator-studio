/**
 * Design renderer service — headless Chromium (Playwright) + Fabric.js.
 *
 * Per the Feature 2 spec (VPS 4 GB memory notes):
 * - concurrency = 1 (requests are queued, one render at a time)
 * - the browser is spun up per job and closed afterwards (no keep-warm)
 *
 * Driver note: the spec named Puppeteer + system Chromium, but Debian's
 * Chromium 150 crashes (SIGTRAP) whenever CDP remote debugging is enabled,
 * so this uses Playwright's own Chromium build — same headless-Chromium +
 * Fabric.js rendering, identical output contract.
 *
 * POST /render {template_json, width, height, title, image_src}
 *   image_src: data URI (preferred — no network dependency) or URL
 *   → 200 image/png
 */

const fs = require("fs");
const path = require("path");
const express = require("express");
const { chromium } = require("playwright");

const PORT = process.env.PORT || 3001;
const FABRIC_PATH = require.resolve("fabric/dist/fabric.min.js");
const PAGE_HTML = fs.readFileSync(path.join(__dirname, "render-page.html"), "utf8");
const RENDER_TIMEOUT_MS = 60_000;

const app = express();
app.use(express.json({ limit: "30mb" }));

// simple promise-chain mutex → concurrency 1
let queue = Promise.resolve();
function enqueue(fn) {
  const run = queue.then(fn, fn);
  queue = run.catch(() => {});
  return run;
}

async function renderOnce({ template_json, width, height, title, image_src }) {
  const browser = await chromium.launch({
    headless: true,
    args: ["--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage"],
  });
  try {
    const page = await browser.newPage();
    await page.setContent(PAGE_HTML);
    await page.addScriptTag({ path: FABRIC_PATH });
    await page.addScriptTag({ path: path.join(__dirname, "inject.js") });

    const dataUrl = await page.evaluate(
      (args) => window.renderTemplate(args),
      {
        templateJson: template_json,
        width,
        height,
        title: title || "",
        imageSrc: image_src || null,
      }
    );

    const base64 = String(dataUrl).replace(/^data:image\/png;base64,/, "");
    return Buffer.from(base64, "base64");
  } finally {
    await browser.close();
  }
}

app.get("/health", (_req, res) => res.json({ status: "ok" }));

app.post("/render", (req, res) => {
  const { template_json, width, height } = req.body || {};
  if (!template_json || !width || !height) {
    return res.status(400).json({ detail: "template_json, width and height are required" });
  }

  enqueue(async () => {
    const timeout = new Promise((_, rej) =>
      setTimeout(() => rej(new Error("render timed out")), RENDER_TIMEOUT_MS)
    );
    return Promise.race([renderOnce(req.body), timeout]);
  })
    .then((png) => {
      res.type("png").send(png);
    })
    .catch((err) => {
      console.error("render failed:", err.message);
      res.status(500).json({ detail: `render failed: ${err.message}` });
    });
});

app.listen(PORT, () => console.log(`design-renderer listening on :${PORT} (playwright chromium)`));

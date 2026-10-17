// Reads the Codex quota from the local 9Router through its own CLI client (local CLI token, no password).
// Prints {"window_pct","weekly_pct","resets_at","source","accounts"} or exits 1. Never prints tokens.
const fs = require("node:fs");
const Module = require("node:module");
const CLIENT = "/usr/lib/node_modules/9router/src/cli/api/client.js";
const src = fs.readFileSync(CLIENT, "utf8") + "\nmodule.exports.__request = makeRequest;\n";
const m = new Module(CLIENT, module);
m.filename = CLIENT;
m.paths = Module._nodeModulePaths(require("node:path").dirname(CLIENT));
m._compile(src, CLIENT);
const c = m.exports;
(async () => {
  const prov = await c.getProviders();
  const list = (prov.data && (prov.data.connections || prov.data.providers || prov.data)) || [];
  const codex = (Array.isArray(list) ? list : []).filter((x) => x && x.provider === "codex" && x.isActive !== false);
  const shapes = [];
  for (const conn of codex) {
    const r = await c.__request("GET", `/api/usage/${conn.id}`);
    if (!r.success) continue;
    const q = (r.data && r.data.quotas) || {};
    if (!q.session) continue;
    const reached = !!r.data.limitReached;
    shapes.push({ window_pct: reached ? 100 : Number(q.session.used), weekly_pct: q.weekly ? Number(q.weekly.used) : null, resets_at: q.session.resetAt ? String(q.session.resetAt).replace(/Z$/, "+00:00") : null, limit_reached: reached });
  }
  if (!shapes.length) { console.error("no codex usage", codex.length, "connections"); process.exit(1); }
  // 9Router rotates accounts: the least used one decides whether Codex can still work.
  shapes.sort((a, b) => a.window_pct - b.window_pct);
  const best = shapes[0];
  console.log(JSON.stringify({ window_pct: best.window_pct, weekly_pct: best.weekly_pct, resets_at: best.resets_at, source: "9router", accounts: shapes.length }));
})().catch((e) => { console.error("error", e && e.message); process.exit(1); });

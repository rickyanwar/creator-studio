# News-scraper Cloudflare relay

`worker.js` is a fallback fetch path for `app/services/news_scraper.py`'s
scrape chain (direct → proxy pool → **relay pool** → Playwright). A "relay"
fetches a URL server-side from its own egress IP and returns the raw
response — useful when a site blocks the VPS's own IP (or an already-abused
relay IP on another platform) but not a fresh one.

## Current deployment

- Account: the Cloudflare account tied to this project's `scraper_relays`
  entries (Settings → News Scraper in the app).
- Script name: `news-relay`
- Public URL: `https://news-relay.rickyanwar-relay.workers.dev`
- The URL above is what's stored in `Settings.scraper_relays` (DB-configured
  at runtime, editable from the app's Settings page — one relay base URL per
  line, same as the proxy pool). This file is the source of truth for the
  Worker's *code*; the DB is the source of truth for *which relay URLs are
  active*.

## Redeploying / replacing it

Needed if this relay's IP ever gets reputation-flagged by a target site's WAF
(the same way an earlier Vercel-hosted relay did) and you need a fresh one.
Requires a Cloudflare API token with `Workers Scripts: Edit` permission for
the target account (Cloudflare dashboard → My Profile → API Tokens →
Create Token).

```bash
TOKEN="<cloudflare api token>"
ACCOUNT="<cloudflare account id>"   # GET /client/v4/accounts to find it
SCRIPT="news-relay"                 # or a new name for a fresh deployment

# 1. One-time: claim a workers.dev subdomain if the account doesn't have one
curl -X PUT "https://api.cloudflare.com/client/v4/accounts/$ACCOUNT/workers/subdomain" \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"subdomain":"<your-chosen-subdomain>"}'

# 2. Upload the script (service-worker syntax — single file, no build step)
curl -X PUT "https://api.cloudflare.com/client/v4/accounts/$ACCOUNT/workers/scripts/$SCRIPT" \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/javascript" \
  --data-binary @worker.js

# 3. Enable the public workers.dev route
curl -X POST "https://api.cloudflare.com/client/v4/accounts/$ACCOUNT/workers/scripts/$SCRIPT/subdomain" \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"enabled": true, "previews_enabled": true}'
```

The resulting URL is `https://<script>.<subdomain>.workers.dev`. Add it to
`Settings.scraper_relays` in the app (one URL per line — a stale/dead relay
in the pool wastes fetch attempts since `_POOL_TRIES` picks randomly, so
remove any relay confirmed dead rather than just appending new ones).

Never commit a Cloudflare API token to this repo — it's only needed for the
one-time deploy, not for the relay's ongoing operation.

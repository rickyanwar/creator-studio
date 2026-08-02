// News-scraper relay — deployed on Cloudflare Workers.
//
// Contract expected by app/services/relay_pool.py (fetch_via_relay): a GET
// request to this Worker's URL with header `x-relay-target: <url>` fetches
// that URL server-side and returns its raw body/status verbatim. Cloudflare
// egresses from its own IP range, which clears sites that block on the
// scraper's own IP reputation (VPS datacenter IP, or an already-abused relay
// IP on another platform) without needing a paid proxy.
//
// Deployed via the Cloudflare API (Workers script + workers.dev subdomain),
// not wrangler — see README.md in this directory for the exact steps if this
// ever needs to be redeployed or replaced.

addEventListener('fetch', event => {
  event.respondWith(handleRequest(event.request))
})

async function handleRequest(request) {
  const target = request.headers.get('x-relay-target')
  if (!target) {
    return new Response('missing x-relay-target header', { status: 400 })
  }
  try {
    const resp = await fetch(target, {
      headers: {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.9',
      },
    })
    const body = await resp.text()
    return new Response(body, {
      status: resp.status,
      headers: { 'content-type': resp.headers.get('content-type') || 'text/html' },
    })
  } catch (err) {
    return new Response('relay fetch error: ' + err.message, { status: 502 })
  }
}

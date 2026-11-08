# Group Share PIC gateway plugin

Routes WhatsApp and Telegram PIC direct messages to Studio without invoking the
Hermes AI, tools, or skills. Uses Python's standard library only.

## Hard rules

- Registers `pre_gateway_dispatch`, before gateway authorization and AI dispatch.
- Only WhatsApp and Telegram `source.chat_type` values `dm` / `private` qualify.
  Groups, forums, unknown chat types, and other platforms remain unchanged.
- Owners in comma-separated `WHATSAPP_ALLOWED_USERS` / `TELEGRAM_ALLOWED_USERS`
  bypass the app immediately. WhatsApp number/JID/device suffixes are normalized
  for this comparison. Keep PICs out of these AI allowlists.
- POSTs to `{STUDIO_API_BASE}/group-share/pic-reply`, with a Bearer token and
  `channel`, `sender` (`source.user_id`), and `text` (empty for non-text messages).
  Includes optional `username` from `source.username` or Hermes `source.user_name`
  when available. The numeric/JID sender stays authoritative; display names must
  not be treated as authenticated identifiers by the app.
- Only a strict JSON boolean `handled: false` falls through to normal Hermes
  allowlist behavior. Unknown senders are cached for 600 seconds, per channel;
  registering a previously unknown PIC can take up to ten minutes to take effect.
  Restarting the gateway clears this process-local cache.
- `handled: true` sends a non-empty string `reply` through the same platform's
  adapter to `source.chat_id`, then returns `skip`, reason `group-share-pic`.
- HTTP errors, non-200 responses, invalid JSON/schema, missing sender, and reply
  delivery failures return `skip`, reason `group-share-pic-error`. Errors log only
  exception class names, never tokens, response bodies, or message text.
- HTTP runs in `asyncio.to_thread` with `urlopen(..., timeout=10)`; no AI fallback.
- Missing/blank `STUDIO_PIC_TOKEN` skips non-owner WhatsApp/Telegram DMs with reason
  `group-share-pic-misconfigured` and logs once per process at error level. Owners
  still bypass the plugin unchanged; non-owner DMs never fall through to AI.

## Install (owner's agent)

1. Deploy the app endpoint and its `group_share:pic` token scope first. Create a
   dedicated revocable API token using the same mechanism as Muse labeler tokens
   described in `docs/muse-labeler.md`, but use **only** `group_share:pic`, not
   `labels:write`. The authenticated admin endpoint is `POST /api/strategy/tokens`
   with `{"name":"Hermes group-share PIC","scopes":["group_share:pic"]}`.
   Store the one-time returned token securely; do not put it into chat or logs.
2. Copy `plugin.yaml` and `__init__.py` from this folder to
   `/root/.hermes/plugins/group_share_pic/`. The README may be copied too; tests
   are not required on the server. This is a user plugin, not a project plugin
   or a shell hook.
3. Add `group_share_pic` to the existing `plugins.enabled` list in
   `/root/.hermes/config.yaml`, preserving other entries. Ensure it is absent
   from `plugins.disabled` (the denylist wins). Equivalent CLI:
   `hermes plugins enable group_share_pic`.
4. Set these variables in `/root/.hermes/.env`:

   ```dotenv
   STUDIO_API_BASE=https://autostudio.pro/api
   STUDIO_PIC_TOKEN=<dedicated group_share:pic token>
   ```

   Preserve the owner's existing platform allowlists. Do not allowlist a PIC to
   make this plugin work: interception happens before normal authorization.
5. Restart the gateway using the installation's existing service manager (or
   `hermes gateway restart` where supported). Confirm the plugin loads and no
   missing-token error appears. Complete the spare-number checks below before
   relying on interception.

## Hermes API contract

The supplied `.notes/hermes-hooks.md`, sections **Plugin Hooks** and
**pre_gateway_dispatch**, documents registration, keyword arguments, async
callbacks, `source.chat_type`, and skip directives. Callback exceptions otherwise
fall through to normal dispatch; this plugin catches its own routing errors.

The supplied hook document abbreviates `send(...)`; its full signature was
confirmed against upstream `gateway/platforms/base.py`:
`async send(chat_id: str, content: str, reply_to=None, metadata=None) -> SendResult`.
The plugin uses `gateway.adapters[source.platform]` to preserve Hermes enum keys.
Hermes `SessionSource` in `gateway/session.py` exposes `user_id` and `user_name`.
Upstream `website/docs/user-guide/features/plugins.md` confirms the manifest,
`__init__.py` entry point, user-plugin directory, and explicit enablement.
The owner's agent should confirm these contracts in the installed Hermes version.

## Offline checks

From the Studio worktree root, run twice:

```bash
"/Users/rickyanwar/Developments/Content Creator/ig-fb-reposter/backend/venv/bin/python" -m pytest ops/hermes/plugins/group_share_pic/tests -q
```

Tests use fake event/gateway objects and mocked HTTP; no Hermes, DB, or production
network calls. They cover replies, strict response parsing, owner bypass, errors,
out-of-scope traffic, missing configuration, and cache expiry/channel isolation.

## Spare-number test plan (after installation)

1. Register a spare WhatsApp number or Telegram identity as an app PIC, outside
   both Hermes owner allowlists. Send a valid task confirmation in a direct chat.
   Verify the app records it once, the expected app reply arrives in that chat,
   and no AI turn, tool, skill, pairing prompt, or AI-generated reply occurs.
2. Send off-topic text and a non-text message from that PIC. Confirm app-defined
   replies or silence, and verify the Hermes AI remains silent in both cases.
3. Make the app unreachable using a temporary invalid `STUDIO_API_BASE` in a
   controlled test gateway, then restart it. Send from the spare PIC: no AI or
   pairing response may occur; logs should show a sanitized routing error.
   Restore configuration and restart before continuing.
4. From an unregistered spare identity, send two DMs within ten minutes. Verify
   one app lookup and ordinary Hermes stranger handling, with no AI reply.
5. From the allowlisted owner, verify ordinary Hermes AI behavior and no app
   lookup. From a group chat, verify this plugin does not intercept messages.
6. Test both platforms where configured. Record gateway/plugin versions and
   results without credentials or private message content.

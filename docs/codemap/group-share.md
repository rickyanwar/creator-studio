# Group Share: Facebook Groups, Groups-Only Production, and PIC Delivery

Refreshed 2026-11-10 at main 8bc0f82.

## 1. Scope and switches

Group Share has its own group catalog, brand settings, content ledger, and per-account delivery settings.
It reuses published Facebook content or produces content exclusively for groups.
Accounts are the brand's `SocialDestination` rows of kind `facebook_profile` or `facebook_page`.
The UI describes a Page as sharing through its parent profile login (`parent_profile_id`).
Actual automatic Facebook group posting through a phone is not implemented here: `phone_auto` tasks remain `planned`.
Implemented outbound delivery sends instructions and media to a PIC (person in charge) through WhatsApp or Telegram.
The PIC posts manually and replies to update the ledger.

Deployment baseline supplied for this refresh: deployed but **OFF**, with `FB_SHARE_DISPATCH_ENABLED` unset and no enabled brands.
This is task-provided operational context, not a live production check.

| Switch / prerequisite | Effect and default |
|---|---|
| `FB_SHARE_DISPATCH_ENABLED=1` | Exact environment check in `services/fb_share/planner.py:dispatch_due`. Any other value disables outbound PIC dispatch **and its expiry/stale-claim sweep**. Does not disable collection, classification, planning, or groups-only generation. |
| `fb_share_brand_settings.enabled` | Missing row means false. Tick selects enabled brands; `plan_brand`, generation quota, and PIC dispatch also check it. Managed by `api/group_share.py:put_settings`. |
| `settings.niches` | Default `[]`. Required to collect published content or hand over group-only jobs. Multiple niches leave the item niche blank for classification. |
| `settings.sources.<mode>.source` | `off` (default), `follow`, `groups_only`, or `both`. Follow collection accepts `follow`/`both`; extra production accepts `groups_only`/`both`. |
| `settings.sources.<mode>.groups_only_per_day` | Default 0; schema allows 0–50. Independent UTC-day production budget, counted from marker rows, not successful group posts. |
| `fb_share_deliveries.is_active`, `mode`, `paused_until` | Default inactive with no mode. Planning/dispatch require active, selected mode, and no current pause. Modes: `phone_auto`, `pic_whatsapp`, `pic_telegram`. |
| `fb_share_deliveries.calibrated_at` | Required to plan `phone_auto` and to activate it through the API. Changing mode/device clears calibration. No calibration execution endpoint exists in these routers. |
| `fb_groups.is_active`, membership `status='joined'` | Both required for planning. Groups default active; membership defaults `unknown`. |
| Normal generation switches | Extra generation still runs inside existing mode pipelines. Their normal enablement, active-brand, source, and `project_can_generate()` gates remain relevant; see §4. |
| `PHONE_RUNNER_BASE_URL`, `PHONE_RUNNER_TOKEN` | Used by `services/phone_runner_client.py:PhoneRunnerClient.send_pic_messages`. Missing URL/token returns `missing_configuration`. |
| Hermes token scope `group_share:pic` | Required for inbound `POST /group-share/pic-reply`; independent of the outbound environment switch. |
| `settings.muse.enabled`, `settings.muse.per_day`, `settings.credit`, `settings.must_edit` | Stored/UI controls, defaults false/0/true/false. The inspected Group Share pipeline does not implement Muse production, credit insertion, or mandatory editing from these settings. |

Settings contracts: `backend/app/schemas/fb_group_share.py:ShareSettings`, `SourceSettings`, `DeliveryIn`, `merge_settings`.
`Contract` forbids extra fields. `merge_settings` recursively overlays stored values onto defaults.
Mode keys: `ig`, `news`, `discussion`, `pinterest`, `fb_recreate`, `yt_clips`, `ai_video`, `viral_radar`.

## 2. Data model and migrations

Definitions: `backend/app/models/fb_group_share.py`.
Brand configuration lives in **`fb_share_brand_settings`, not `target_fanpages`**: a separate table avoids the blocking `ALTER TABLE` lock on the busy fanpage table (lock-safe fix `fcbb3da`).

| Table / model | Key columns and constraints |
|---|---|
| `fb_groups` / `FbGroup` | Unique normalized `url`; indexed `niche`; `fb_group_id`, `name`, `kind`, `subject`, `language='en'`, `posting_identity='page_allowed'`, `needs_admin_approval=false`, JSON `allowed_days`/`banned_content`, `daily_cap=1`, `is_active=true`, `paused_reason`, `notes`, timestamps. Kinds: `person_fan`, `team_fan`, `general`, `meme`; identities: `page_allowed`, `profile_only`. |
| `fb_share_brand_settings` / `FbShareBrandSettings` | `fanpage_id` is PK and cascading FK to `target_fanpages`; `enabled=false`, JSON `settings`, timestamps. |
| `fb_share_deliveries` / `FbShareDelivery` | Unique cascading `destination_id`; nullable `mode`, `phone_device_id` (FK, set null on deletion), `pic_name`, `pic_contact`; task hours `08:00`–`21:00`; nullable `start_per_day`, `weekly_increase`, `max_per_day`; JSON `calibration`, `calibrated_at`, `paused_until`, `block_count=0`, `is_active=false`, timestamps. |
| `fb_group_memberships` / `FbGroupMembership` | Unique `(destination_id, group_id)`, both cascading FKs; `status`, `checked_at`, `joined_at`. States: `unknown`, `joined`, `pending_approval`, `not_member`, `removed`, `banned`. |
| `fb_share_items` / `FbShareItem` | Cascading/indexed `fanpage_id`; unique `(fanpage_id, source_kind, source_ref)`; `niche`, `media_kind`, JSON `media`, `phash`, JSON `subjects`/`sentiment`, `content_type`, `caption`, `hashtags`, `status='new'`, `expires_at`, `created_at`. Source kinds: `publish_job`, `video_job`, `radar`, `fb_source`, `muse`, `manual`; current feeder uses `publish_job`. Media kinds: `photo`, `album`, `video`. Statuses: `new`, `routed`, `done`, `skipped`, `expired`. |
| `fb_share_tasks` / `FbShareTask` | Cascading FKs `item_id`, `group_id`, `destination_id`; unique `(item_id, group_id)` across accounts; index `(destination_id, scheduled_for)` and item index. `delivery_mode`, `scheduled_for`, `state='planned'`, `post_url`, `error`, `pic_message_ref`, `attempts=0`, `next_retry_at`, timestamps. |
| `fb_share_group_jobs` / `FbShareGroupJob` | Indexed `fanpage_id`, `mode`, independently unique/indexed nullable `publish_job_id` and `video_job_id`, `created_at`, `handed_at`. Check requires at least one job ID. These IDs and `fanpage_id` have no FKs, so markers can survive normal job cleanup. |

Task states allowed by the DB: `planned`, `sent_to_pic`, `publishing`, `published`, `pending_admin`, `rejected`, `blocked`, `not_member`, `expired`, `failed`, `uncertain`.
Some states are schema groundwork rather than transitions implemented by PIC delivery.
JSON columns use JSONB on Postgres and JSON elsewhere.

Migrations (entry point `upgrade`, with reverse `downgrade`):
- `backend/alembic/versions/c80000000056_fb_group_share.py`: creates the catalog, settings, deliveries, memberships, items, and tasks; parent revision `c80000000055`.
- `backend/alembic/versions/c80000000057_fb_share_group_jobs.py`: creates production markers and unique job indexes; parent revision `c80000000056`.

`services/fb_share/brand_settings.py:get_brand_share`, `set_brand_share`, `enabled_fanpage_ids` centralize defaults, upsert, and enabled-brand selection.

## 3. Tick, collection, classification, routing, and pacing

Entrypoint: `backend/app/tasks/fb_share.py:fb_share_tick`.
`backend/app/tasks/celery_app.py` includes `app.tasks.fb_share`, routes `app.tasks.fb_share.*` to **`ai`**, and schedules beat entry `fb-share-tick` every **300 seconds**.
Task limits are soft 600 s / hard 660 s.
A Redis `SET NX EX` lock (`fb_share:tick`, 900 s) prevents overlapping ticks; token-checked Lua releases only its own lock.

For each enabled brand, the tick runs:
1. `feeder.py:collect_published_items`.
2. `feeder.py:collect_group_only_items`.
3. `classify.py:classify_pending_items`.
4. `planner.py:plan_brand`.
5. After all brands: `planner.py:dispatch_due` with `PhoneRunnerClient`.

A brand failure rolls back and logs, then the next brand continues. Dispatch has a separate exception boundary.
Collection precedes planning; generation itself is performed by the existing content-mode tasks, not by this tick.

### Collection

`backend/app/services/fb_share/feeder.py:collect_published_items` reads `PublishDelivery` rows that are `published` within the last 48 h for the brand's Facebook profiles/pages.
It copies immutable delivery media/caption snapshots, not current mutable job text.
Already-seen publish job IDs are deduplicated per brand. Radar-linked jobs use `viral_radar` instead of the ordinary IG source key.

| Publish content type | Source setting key |
|---|---|
| `ig_repost`, `ig_recreate` | `ig` |
| `news_content` | `news` |
| `discussion` | `discussion` |
| `pinterest_content` | `pinterest` |
| `facebook_recreate` | `fb_recreate` |
| `youtube_clip` | `yt_clips` |
| `ai_video` | `ai_video` |

Trailing hashtag-only suffixes are split into `hashtags`.
News items expire 12 h after collection; other modes expire after 72 h.
Photos/albums require a first-image perceptual hash; failure skips collection. Videos use source-reference deduplication only.
`feeder.py:load_image_bytes` handles local/storage paths or streamed HTTP images, with a 15 MiB bound and 15 s HTTP timeout.

`collect_group_only_items` loads the oldest ten unhanded marker-linked publish jobs.
Each hand-over commits separately; one failure rolls back only that job and does not block later jobs.

### Classification and routing

`backend/app/services/fb_share/classify.py:classify_pending_items` labels at most ten oldest `new` items with null sentiment.
It sends caption text (first 600 characters) and media kind to `gptcli_client.generate_text(get_gptcli_config(db), ...)`.
It records up to four subjects, per-subject `positive`/`negative`/`neutral`/`unsure`, content type, and a configured niche.
Generation/JSON failures leave items untouched. Invalid sentiment becomes `unsure`; invalid content type defaults from media kind.
An invalid returned niche preserves an existing niche, or marks an otherwise niche-less item `skipped`.

`backend/app/services/fb_share/types.py` defines frozen transport records `GroupInfo`, `ShareItem`, `SentRecord`, `PicMessage`, and `ParsedReply`.
`backend/app/services/fb_share/routing.py:select_groups` is the pure routing filter:
- Active group, case-insensitive matching niche, explicitly joined membership.
- Pages cannot use `profile_only` groups.
- `allowed_days` uses Monday=0; `banned_content` rejects matching content types.
- Person/team fan groups require positive sentiment for their normalized subject. Normalization removes accents and collapses whitespace.
- Meme groups require `content_type='meme'`.
- Same-group history blocks the same `source_ref` or image hash distance ≤10. History has no age cutoff.
- Sort person/team fans first, memes next, general last; then group name. Return at most the requested count.

### Planner and caps

`backend/app/services/fb_share/planner.py:plan_brand` considers active, unpaused deliveries with a mode; phone mode also needs calibration.
It expires old `new` items and skips unclassified ones.
It chooses the first eligible account for an item, creates at most five group tasks, marks the item `routed`, then stops trying other accounts for that item.
History excludes `expired`, `failed`, and `not_member` tasks, but includes other states.

`backend/app/services/fb_share/caps.py` entry points:
- `effective_daily_cap`: `min(max_per_day, start_per_day + weekly_increase * full_weeks)`, floored at zero.
- `remaining_today`: daily cap minus already planned task count, floored at zero.
- `next_slot`: first slot now; later slots at least the configured gap, randomly up to 3× gap, never before now.
- `in_task_hours`: start inclusive, end exclusive; supports overnight windows (equal start/end covers all day).

Brand cap defaults: start 3/day, increase 2/week, maximum 12/day, minimum gap 30 min, per-group 1/day.
Per-account ramp overrides fall back to brand values. Ramp starts at calibration for phone mode, delivery creation for PIC mode.
Account and brand-wide group counts use the receiving destination's local day (`SocialDestination.timezone`, UTC fallback during planning).
Group count limit is `min(group.daily_cap, caps.per_group_per_day)`.
Existing task slots, including future slots, advance account pacing; PIC slots move into the next local task window when needed.

## 4. Groups-only production and safety

Entrypoints: `backend/app/services/fb_share/group_jobs.py:mark_group_job`, `remaining_groups_only_today`, `hand_over`, `media_cleanup_allowed`.
Markers are created before render/finalize dispatch; they reserve production budget on creation, including unsuccessful jobs.
Quota counts markers for exactly `(fanpage_id, mode)` within the current UTC day.
`group_publish_job_ids` / `group_video_job_ids` provide SQL subqueries used to exclude group work from Page counts, stock, queue listings, and normal publishing.

| Mode / entry point | Actual extra-content path |
|---|---|
| IG (Modes 1/3), `tasks/ig_recreate.py:_publish_ig_overflow` | Rescues `ig_repost`/`ig_recreate` jobs that are `pending_publish`, older than 24 h, unmarked, and have **no** `PublishDelivery`. Marks under `ig`, then queues `publish_job`. This is overflow rescue, not a new independent IG crawler. |
| News (Mode 2), `tasks/design_renderer.py:render_pending_designs`; `services/visual_fair_queue.py:pick_next_news_job` | Converts stale/no-Page-slot news candidates into marked `news` work when group budget remains, instead of skipping them. Marked jobs use normal render-engine queues but bypass Page fairness counters, JIT slot claims, and Page capacity checks. Stale sweep threshold is `NEWS_STALE_HOURS` (24 h); visual freshness also uses the configured news freshness calculation. |
| Discussion (Mode 4), `tasks/discussion.py:generate_discussion_content`, `_consume_one(group_only=True)` | Separate extra pass consumes real discussion ideas and creates/render-dispatches marked jobs, at most three per tick. |
| Pinterest (Mode 5), `tasks/pinterest.py:generate_pinterest_content`, `_consume_one(group_only=True)` | Separate extra pass consumes ideas and generates real caption/card jobs, bounded by `_MAX_CATCHUP_PER_TICK` and remaining group quota. |
| FB recreate (Mode 6), `tasks/facebook_photo.py:generate_facebook_photo_content`, `_consume_one(group_only=True)` | Separate extra pass uses the existing source/idea pipeline, marks new jobs, and renders them. Same catch-up bound pattern. |
| YouTube clips (Mode 7), `tasks/yt_clip.py:generate_yt_clip_content`, `_consume_one(group_only=True)` | Separate extra pass generates captions and renderable clip jobs. `_dispatch_analysis` includes remaining group quota in the pending-idea capacity. |
| AI video (Mode 8), `tasks/ai_video_plan.py:plan_ai_videos` | Runs Page and group branches separately. Group branch selects a real approved idea/template, builds a `VideoJob`, and marks it by `video_job_id`. At most one open group video (`queued`/`claimed`/`generating`) versus two Page videos. |
| Viral Radar, `tasks/radar_dispatch.py:radar_dispatch_tick`, `_process_story`; `tasks/ig_recreate.py:recreate_post_for_fanpage(group_only=True)` | Daily Page-cap overflow can dispatch under the group budget. Pending `group_only:` decisions reserve budget before a publish job exists; those decisions are excluded from Page counts. Recreate checks remaining quota and marks the resulting job as `viral_radar`. |

Extra passes do **not** override normal mode enablement. Discussion/Pinterest/FB photo require their existing `*_enabled`, positive `*_daily_count`, active brand, and generation eligibility; they still use the normal WIB generation window.
YouTube requires `yt_clip_enabled`, active brand, eligibility, and its normal window.
Mode 8 requires `ai_video_enabled`, active brand, eligibility, and available approved/prepared ideas.
`services/social_project_eligibility.py:project_can_generate` requires either a connected nonempty legacy Repliz account or an active phone/mixed social destination; Group Share enabled alone does not satisfy it.
The frontend's per-mode brand flags are `mode1_ig_repost_enabled`, `mode2_news_content_enabled`, `discussion_enabled`, `pinterest_enabled`, `facebook_photo_enabled`, `yt_clip_enabled`, `ai_video_enabled`, and `radar_enabled`.

Mode 8 keeps group videos out of Page production/stock counts but includes their spend in `ai_video_max_credits_per_day` for **both** branches.
It also enforces `ai_video_max_credits_per_video` and a group template-credit budget derived from `groups_only_per_day * credits_estimate(template, clips)` minus group spend that UTC day.
`tasks/ai_video_finalize.py:finalize_ai_video` links the marker to the resulting publish job through `link_video_publish_job`.
Group completion can dispatch in manual publish mode only when final QA allows `pending_publish`; failed/held outputs are not handed over.

### Hand-over and cleanup invariants

- `tasks/publisher.py:publish_job` checks `is_group_only` **before** the ordinary publish claim, calls `hand_over`, commits, and returns without Page/Repliz delivery.
- `services/social_delivery_planner.py:ensure_deliveries` independently returns no normal deliveries for group-only work.
- `hand_over` locks the marker and is idempotent by source key and `handed_at`.
- Only `pending_publish` or `pending_review` jobs with no truthy `last_error` can hand over. Missing niches, invalid payload, or failed photo hash leave the job unhanded.
- Payload is built by the normal `build_delivery_payload`; photos/albums require a perceptual hash, videos do not. Caption/hashtags and expiry follow feeder rules.
- Successful hand-over sets `handed_at`, creates/reuses the item, and marks the publish job `skipped` with `last_error='group_only: handed to Group Share'`.
- `media_cleanup_allowed` requires item status `done`, `expired`, or `skipped`; no tasks in `planned`, `sent_to_pic`, `publishing`, `pending_admin`, or `uncertain`; and seven days after the latest task update/item expiry.
- Missing/uncompleted items retain media. Normal unrelated jobs are immediately eligible for this guard.
- `tasks/cleanup.py` and `tasks/yt_clip_render.py` consult these guards; video cleanup also protects marked video jobs. Group markers remain separate from ordinary Page cleanup/counting.

## 5. PIC transport and replies

`backend/app/services/fb_share/pic_messages.py:build_task_messages` creates four ordered messages:
1. Account/page name.
2. First media only.
3. Caption plus hashtags.
4. Numbered group URLs.

`ONBOARDING_TEXT` is prepended until that destination has a previously successful/non-error bundle.
Albums currently send only `item.media[0]`; the PIC flow is not an automatic Facebook album publisher.

`planner.py:dispatch_due` bundles tasks by `(item_id, destination_id)`, including all unclaimed planned tasks for that pair.
It dispatches when the earliest slot is due and the account is within local task hours.
Any bundle member's future `next_retry_at` defers the entire bundle.
It checks brand enablement, active delivery, pause, and `pic_whatsapp`/`pic_telegram` task mode.
An existing `pic_message_ref` for the same item/account suppresses sending.
It commits `pic-<item_id>-<destination_id>` **before** contacting the runner.

Success sets `sent_to_pic`. A reported failure clears the ref, increments attempts, and retries after 15/30/60 minutes; the fourth failure becomes `failed` (general backoff formula capped at 240 min).
A still-`planned` claim older than 30 min becomes `uncertain` and is not resent.
Expired content moves `planned`/`sent_to_pic` tasks to `expired` while dispatch is enabled.

`backend/app/services/phone_runner_client.py:send_pic_messages` calls authenticated host-runner **`POST /pic-send`** with `{target, messages}` and a 130 s request timeout.
This endpoint belongs to `phone_runner/server.py`, not the FastAPI/nginx API.
`phone_runner/pic_send.py:validate_request`, `send_pic_messages`, `download_media`:
- Validate WhatsApp/Telegram target syntax and 1–12 messages; text ≤4000 characters; media HTTP(S) URLs.
- Download to a temporary directory, maximum 60 MiB per media; delete the directory on completion/failure.
- Use Hermes `send -t <target> -q -- <text>` through `hermes_command`, without shell execution.
- Media uses a generated `MEDIA:<local path>` message; user text has `MEDIA:` neutralized.
- Send sequentially with 1.5 s spacing, 20 s maximum per CLI call, and 100 s whole-bundle deadline. Stop on the first failure; sanitize errors.

`backend/app/services/fb_share/pic_replies.py:parse_reply` is deterministic Indonesian/English parsing, not an LLM:
numbered done/not-member/removed/pending replies, `all`/`semua` done, blocked/restricted phrases, and Facebook group post/permalink URLs.
Priority is removed, then not-member, then pending, then done. URL matching uses group slug, with a single mentioned number as fallback.

`backend/app/api/group_share_pic.py:pic_reply` requires scope **`group_share:pic`** and a registered PIC contact.
WhatsApp normalizes sender digits/JID; Telegram matches case-insensitive username or sender ID.
It locks matching tasks and chooses the most recently updated bundle with an open `sent_to_pic` task.
Only open tasks change: done becomes `published` (optional `post_url`); not-member/removed becomes task `not_member` and upserts the matching membership status.
Pending replies leave tasks open. Unrelated requests return an off-topic response; unknown contacts return `handled=false`.
Blocked replies mark that account's open sent tasks `blocked`, pause delivery for 72 h, increment `block_count`, and best-effort notify the owner.
The endpoint returns reply text; it does not itself send that confirmation through Hermes.

## 6. API entry points

`backend/app/main.py` registers all three routers.
Paths below are **bare FastAPI paths**; public requests add `/api` through nginx.
Catalog/brand endpoints require `CurrentUser`; the PIC endpoint uses its scoped Hermes token.

| Method | Path | Handler / purpose |
|---|---|---|
| GET | `/fb-groups` | `api/fb_groups.py:list_groups`; optional exact `niche`, `include_inactive=false`. |
| GET | `/fb-groups/niches` | `list_niches`; distinct active-group niches. |
| POST | `/fb-groups` | `create_group`; validated catalog entry. |
| PATCH | `/fb-groups/{group_id}` | `patch_group`; partial update. |
| DELETE | `/fb-groups/{group_id}` | `delete_group`; soft-deactivate, does not delete ledger history. |
| GET / PUT | `/fanpages/{fanpage_id}/group-share` | `api/group_share.py:get_settings` / `put_settings`; brand enablement and settings. |
| GET | `/fanpages/{fanpage_id}/group-share/accounts` | `list_accounts`; brand Facebook profiles/pages, parent login name, delivery configuration. |
| PUT | `/fanpages/{fanpage_id}/group-share/accounts/{destination_id}` | `put_delivery`; validate brand ownership/device, reset changed calibration, reject uncalibrated active phone mode. |
| GET / PUT | `/fanpages/{fanpage_id}/group-share/memberships` | `list_memberships` / `put_memberships`; validate all destinations/groups before upsert; stamp `checked_at`, first joined timestamp. |
| GET | `/fanpages/{fanpage_id}/group-share/tasks` | `list_tasks`; read-only ledger with optional `state`, limit 1–200 (default 50), newest scheduled first. |
| GET | `/fanpages/{fanpage_id}/group-share/summary` | `get_summary`; UTC-day published/planned, all pending-admin, paused accounts, distinct joined groups. |
| POST | `/group-share/pic-reply` | `api/group_share_pic.py:pic_reply`; `channel`, `sender`, optional `username`, `text` ≤2000 characters. |

`schemas/fb_group_share.py:normalize_group_url` accepts supported Facebook/fb host variants, canonicalizes group URLs, drops query/trailing slash, and rejects reserved directory slugs.
Duplicate URL writes return 409. PATCH rejects explicit null for nonnullable catalog fields.
`DeliveryIn` requires PIC name/contact, valid contact format, selected mode for activation, and a device for phone mode.

## 7. Frontend

| File / entry point | Role |
|---|---|
| `frontend/src/app/(dashboard)/brands/[id]/page.tsx` | Mounts `GroupShareTab` under tab key `groupshare`. |
| `frontend/src/components/brand/group-share/GroupShareTab.tsx:GroupShareTab` | SWR configuration, niches, accounts, memberships, queue, summary. Brand on/off, source modes, stored Muse/rule controls, safety caps, account editor, batch membership save. Queue/summary refresh every 30 s; latest 50 queue tasks. |
| `frontend/src/components/brand/group-share/GroupModeList.tsx:GroupModeList` | Eight source-mode panels. Group toggle defaults to `follow` if the brand mode is on, otherwise `groups_only`; disables follow/both buttons when brand mode is off. Edits extra daily count; UI limit 5 for AI video, 50 otherwise (backend schema allows 50 for all). |
| `frontend/src/components/brand/group-share/DeliveryEditor.tsx:DeliveryEditor` | Phone/PIC mode, device, calibration display, contact, task hours, nullable cap overrides, active toggle. No calibration action. Telegram help requires the PIC to start the bot once. |
| `frontend/src/components/brand/group-share/Fields.tsx` | Shared `Field`, `NumberField`, `Toggle`, `TableWrap`, error formatting, styling, delivery labels; labeled controls and horizontally scrollable keyboard-focusable tables. |
| `frontend/src/app/(dashboard)/facebook-groups/page.tsx:FacebookGroupsPage`, `GroupForm` | Shared `/facebook-groups` catalog: niche filters, add/edit, pause/resume, optional inactive rows, group rules and notes. No hard-delete action. |

API functions/types live in `frontend/src/lib/api.ts` and `frontend/src/lib/types.ts`.

## 8. Tests (map only; not run for this documentation refresh)

All backend paths below are under `backend/tests/`.

| File | Coverage |
|---|---|
| `test_fb_group_share_api.py` | Catalog URL/CRUD validation, defaults, settings, account calibration, membership scope/atomicity, task ledger, UTC summaries. |
| `test_fb_group_share_models.py` | Model constraints, migration parity/downgrade, brand-settings defaults/upsert/rollback. |
| `test_fb_share_caps.py` | Ramp, task hours/overnight windows, invalid hours, gap floor/random bounds, remaining quota. |
| `test_fb_share_classify.py` | Caption batch/prompt, generator result, JSON failures, invalid labels, niche preservation/skip, item selection. |
| `test_fb_share_feeder.py` | Source mapping, snapshots/expiry/dedupe, Radar override, filters, media formats, hashtags, hash failure, image loading. |
| `test_fb_share_routing.py` | Membership/identity/day/content filters, fan sentiment/normalization, meme restriction, hash/source dedupe, ordering. |
| `test_fb_share_planner.py` | Local-day caps, ramps, account routing, pacing/windows, dispatch switch, bundle claims/no resend, retries/expiry/stale claims. |
| `test_fb_share_task.py` | Tick registration, per-brand isolation, Redis lock contention and token-safe release. |
| `test_fb_share_pic_messages.py` | Four-message contract, required fields, onboarding/off-topic constants, confirmation text. |
| `test_fb_share_pic_replies.py` | Reply vocabulary, numbered clauses, URL association, conflicts, pending/category precedence. |
| `test_group_share_pic_api.py` | Sender matching, scope/body validation, latest bundle, membership updates, blocked pauses, immutable published tasks. |
| `test_fb_share_group_jobs.py` | Marker/link queries, UTC production quota, idempotent hand-over, readiness/hash fail-closed, media retention. |
| `test_fb_share_group_guards.py` | Publisher/delivery/list exclusions and guarded media cleanup. |
| `test_fb_share_group_counted_modes.py` | Extra generation passes for counted modes, YouTube idea supply, hand-over from manual-mode renders. |
| `test_fb_share_group_overflow.py` | News stale/capacity/freshness overflow, Radar cap overflow, old undelivered IG rescue, marked render completion. |
| `test_fb_share_group_ai_video.py` | Mode 8 group branch, open-job limit, separate Page counts/stock, combined and per-video credit limits, finalization. |
| `test_fb_share_group_review_fixes.py` | Held-output guards, manual-review hand-over, QA finalization, per-job collection isolation, pending Radar budgets, cleanup. |
| `phone_runner/tests/test_pic_send.py` | Ordered sends, literal text/options safety, deadlines, downloads/size limits, sanitized failure, temporary cleanup, endpoint auth/validation. |

## 9. Setup gaps and code concerns

Before PIC use, configure real catalog groups/niches, joined account memberships, source settings/budgets, active PIC deliveries, runner URL/token, working Hermes channel transport, and inbound forwarding with `group_share:pic` credentials.
Enable the brand and set `FB_SHARE_DISPATCH_ENABLED=1` in the task worker's environment when dispatch should run.
The switch is read directly from `os.environ`, not a Pydantic setting; merely enabling a brand is insufficient for outbound sends.

Disabled/unimplemented paths and observed concerns:
- Automatic phone group publishing/calibration is groundwork only. `phone_auto` cannot be activated through the provided UI/API without an existing calibration, and `dispatch_due` never sends phone tasks.
- Muse research, credit, and must-edit controls are persisted but unused by the inspected sharing flow.
- The UI permits `groups_only` while the normal brand mode is off, but generation still selects normally enabled modes. Such configuration can produce nothing; discussion/Pinterest/FB photo additionally require positive Page daily counts.
- PIC bundle transport can partly succeed before returning failure. The planner clears the committed ref and retries the whole bundle on a reported failure, so already-sent messages can repeat. Only abandoned committed claims are conservatively quarantined as `uncertain`.
- PIC replies update tasks, not the parent item's status. No completion step in these modules promotes `routed` items to `done`/`expired`; the cleanup guard therefore retains their media even after tasks finish.
- Planning checks caps/day eligibility before allocating all future slots, rather than rechecking the resulting slot dates. Large backlogs can cross a day boundary; weekday/cap checks describe the planning day.
- `needs_admin_approval` and `language` are stored catalog metadata but are not consumed by `select_groups` or the PIC reply transitions.

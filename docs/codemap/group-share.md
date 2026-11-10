# Group Share: Facebook Groups, Group Lanes, and PIC Delivery

Refreshed 2026-11-10 (group lanes). Checked against the local group-share-lanes implementation; production was not checked.

## 1. Scope and switches

Group Share has its own group catalog, brand settings, content ledger, and per-account delivery settings.
Group content and brand Page content are separate streams with separate daily targets:
- **From brand page posts (follow):** the N newest published Page posts per mode per brand local day are copied into the ledger (§3).
- **Group content (lane):** every brand can own one hidden **group lane** fanpage that generates content only for groups, exactly the way each mode generates for a Page (§4).
Accounts are the brand's `SocialDestination` rows of kind `facebook_profile` or `facebook_page`.
The UI describes a Page as sharing through its parent profile login (`parent_profile_id`).
Actual automatic Facebook group posting through a phone is not implemented here: `phone_auto` tasks remain `planned`.
Implemented outbound delivery sends instructions and media to a PIC (person in charge) through WhatsApp or Telegram.
The PIC posts manually and replies to update the ledger.

Deployment baseline: deployed but **OFF**, with `FB_SHARE_DISPATCH_ENABLED` unset and no enabled brands.
This is task-provided operational context, not a live production check.

| Switch / prerequisite | Effect and default |
|---|---|
| `FB_SHARE_DISPATCH_ENABLED=1` | Exact environment check in `services/fb_share/planner.py:dispatch_due`. Any other value disables outbound PIC dispatch **and its expiry/stale-claim sweep**. Does not disable collection, classification, planning, or lane generation. |
| `fb_share_brand_settings.enabled` | Missing row means false. Tick selects enabled brands; `plan_brand` and PIC dispatch also check it. Managed by `api/group_share.py:put_settings`, which also sets the lane's `is_active` to the same value (`lanes.set_lane_active`). It is also the lane's generation gate (`project_can_generate`, §4). |
| Lane mode switches (`mode1_ig_repost_enabled`, `ig_recreate_enabled`, `mode2_news_content_enabled`, `discussion_enabled`, `pinterest_enabled`, `facebook_photo_enabled`, `yt_clip_enabled`, `ai_video_enabled`, `radar_enabled` **on the lane row**) | All OFF on a new lane. Edited in the Group Share tab; independent of the brand's switches, so groups-only content works while the Page mode is off. |
| `settings.niches` | Default `[]`. Required to collect published content or hand over lane jobs. Multiple niches leave the item niche blank for classification. |
| `settings.sources.<mode>.follow_per_day` | Default 0, schema 0–50. Newest brand Page posts of that mode copied to groups per brand local day; 0 = none. |
| `settings.sources.<mode>.groups_only_per_day` | Default 0, schema 0–50. Used **only** as the lane cap for `ig` and `news` (they have no daily-count column). Counted from today's group-job markers in the brand's local day. |
| `settings.sources.<mode>.source` | Kept for stored-JSON compatibility; no longer used for decisions. |
| `fb_share_deliveries.is_active`, `mode`, `paused_until` | Default inactive with no mode. Planning/dispatch require active, selected mode, and no current pause. Modes: `phone_auto`, `pic_whatsapp`, `pic_telegram`. |
| `fb_share_deliveries.calibrated_at` | Required to plan `phone_auto` and to activate it through the API. Changing mode/device clears calibration. No calibration execution endpoint exists in these routers. |
| `fb_groups.is_active`, membership `status='joined'` | Both required for planning. Groups default active; membership defaults `unknown`. |
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
| `fb_share_lanes` / `FbShareLane` | Integer PK `id`; independently unique/indexed, nonnullable `brand_fanpage_id` and `lane_fanpage_id`; timezone-aware `created_at` with server default `now()`. No foreign keys. Maps one brand to one hidden `TargetFanpage` lane. |
| `fb_share_deliveries` / `FbShareDelivery` | Unique cascading `destination_id`; nullable `mode`, `phone_device_id` (FK, set null on deletion), `pic_name`, `pic_contact`; task hours `08:00`–`21:00`; nullable `start_per_day`, `weekly_increase`, `max_per_day`; JSON `calibration`, `calibrated_at`, `paused_until`, `block_count=0`, `is_active=false`, timestamps. |
| `fb_group_memberships` / `FbGroupMembership` | Unique `(destination_id, group_id)`, both cascading FKs; `status`, `checked_at`, `joined_at`. States: `unknown`, `joined`, `pending_approval`, `not_member`, `removed`, `banned`. |
| `fb_share_items` / `FbShareItem` | Cascading/indexed `fanpage_id`; unique `(fanpage_id, source_kind, source_ref)`; `niche`, `media_kind`, JSON `media`, `phash`, JSON `subjects`/`sentiment`, `content_type`, `caption`, `hashtags`, `status='new'`, `expires_at`, `created_at`. Source kinds: `publish_job`, `video_job`, `radar`, `fb_source`, `muse`, `manual`; current feeder uses `publish_job`. Media kinds: `photo`, `album`, `video`. Statuses: `new`, `routed`, `done`, `skipped`, `expired`. |
| `fb_share_tasks` / `FbShareTask` | Cascading FKs `item_id`, `group_id`, `destination_id`; unique `(item_id, group_id)` across accounts; index `(destination_id, scheduled_for)` and item index. `delivery_mode`, `scheduled_for`, `state='planned'`, `post_url`, `error`, `pic_message_ref`, `attempts=0`, `next_retry_at`, timestamps. |
| `fb_share_group_jobs` / `FbShareGroupJob` | Indexed `fanpage_id`, `mode`, independently unique/indexed nullable `publish_job_id` and `video_job_id`, `created_at`, `handed_at`. For lane jobs, marker `fanpage_id` is the **brand id**, while the underlying job belongs to the lane. Check requires at least one job ID. These IDs and `fanpage_id` have no FKs, so markers can survive normal job cleanup. |

Task states allowed by the DB: `planned`, `sent_to_pic`, `publishing`, `published`, `pending_admin`, `rejected`, `blocked`, `not_member`, `expired`, `failed`, `uncertain`.
Some states are schema groundwork rather than transitions implemented by PIC delivery.
JSON columns use JSONB on Postgres and JSON elsewhere.

Migrations (entry point `upgrade`, with reverse `downgrade`):
- `backend/alembic/versions/c80000000056_fb_group_share.py`: creates the catalog, settings, deliveries, memberships, items, and tasks; parent revision `c80000000055`.
- `backend/alembic/versions/c80000000057_fb_share_group_jobs.py`: creates production markers and unique job indexes; parent revision `c80000000056`.
- `backend/alembic/versions/c80000000058_fb_share_lanes.py`: creates only `fb_share_lanes` and its two unique indexes, without FKs or changes to existing tables; Postgres `lock_timeout='15s'`; parent revision `c80000000057`.

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

`backend/app/services/fb_share/feeder.py:collect_published_items` reads `PublishDelivery` rows that are `published` **today in the brand's local day** for the brand's Facebook profiles/pages (`published_at >= max(now - 48 h, local midnight)`, UTC fallback for an invalid/missing timezone).
It copies immutable delivery media/caption snapshots, not current mutable job text.
Already-seen publish job IDs are deduplicated per brand. Radar-linked jobs use `viral_radar` instead of the ordinary IG source key.
Newest deliveries come first, with at most `sources[mode].follow_per_day` items per mode; 0 disables collection for that mode. Items already collected since local midnight consume that quota, excluding group-job markers. The legacy `source` setting is ignored.

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
The same call returns up to four subjects, per-subject `positive`/`negative`/`neutral`/`unsure`, content type, a configured niche, and short `group_caption` / `group_hashtags`. The prompt requests 1–2 sentences in the brand's `caption_language` (default English), at most 280 characters, no inline hashtags, and 2–3 hashtags separately. Quotes must stay verbatim; an overlong quote must be omitted, never shortened or paraphrased.
Caption validation precedes all classification writes: empty/overlong captions, inline `#`, changed quoted passages, or fewer than two/more than three valid distinct hashtags reject the result. The original fields remain and sentiment stays null, so the item cannot route and is retried. Redis `fb_share:classify_fail:<item_id>` increments with a refreshed seven-day TTL; the third rejection marks the item `skipped`. Duplicate results consume at most one rejection per item per batch. If Redis fails, the item remains unclassified without a reliable retry limit.
Generation/JSON failures leave items untouched and do not increment that per-item counter. Invalid sentiment becomes `unsure`; invalid content type defaults from media kind.
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

## 4. Group lanes (groups-only production) and safety

### Lanes

A lane is a normal `TargetFanpage` row owned by one brand, so every per-mode setting, source table, Content-tab component and generation pipeline works on it unchanged.
- Table `fb_share_lanes` / `FbShareLane` (`brand_fanpage_id` unique, `lane_fanpage_id` unique, `created_at`; **no foreign keys**). Migration `backend/alembic/versions/c80000000058_fb_share_lanes.py` (new table only, `lock_timeout` 15 s).
- Service `backend/app/services/fb_share/lanes.py`: `get_lane_id`, `ensure_lane` (creates on first call; `ValueError` when the id is itself a lane), `lane_brand_id`, `is_lane`, `lane_fanpage_ids()` (Select for `TargetFanpage.id.notin_(...)`), `sync_lane_from_brand`, `set_lane_active`, `sibling_fanpage_ids`.
- New lane: name `"<brand> (groups)"`, `repliz_account_id=None`, `is_connected=False`, `publish_mode=auto`, every mode toggle OFF, `is_active` = brand share enabled, no source rows.
- `LANE_OWNED_FIELDS` (mode settings edited by the Content-tab sections plus identity/publishing/bookkeeping) belong to the lane. `LOOK_FIELDS` (templates, colours, badges, caption style, niches/gallery, watermark/logo, timezone, design settings) are copied brand → lane on creation and on every brand save through `PUT /fanpages/{id}` (`api/fanpages.py`). A unit test fails when a new `TargetFanpage` column is in neither list.
- Lanes are hidden from every fanpage list/count endpoint (`TargetFanpage.id.notin_(lane_fanpage_ids())`). Detail/update endpoints by id (`/fanpages/{id}` and its source sub-resources) keep working for lane ids, because the Group Share UI edits the lane through them. Beat/Celery generation loops include lanes.
- Generation gate: `services/social_project_eligibility.py:project_can_generate` accepts a lane whose brand's `fb_share_brand_settings.enabled` is true (a lane has no Repliz/phone destination).

### Marker listener

`lanes.py:_mark_lane_jobs` is a SQLAlchemy `Session` `after_flush` listener (registered on import; `backend/tests/conftest.py` imports it for every test).
For each **new** `PublishJob` / `VideoJob` whose `fanpage_id` is a lane it inserts an `FbShareGroupJob` marker with `fanpage_id` = the **brand** id.
Mode comes from the content type: `ig_repost`/`ig_recreate` → `ig`, `news_content` → `news`, `discussion`, `pinterest_content` → `pinterest`, `facebook_recreate` → `fb_recreate`, `youtube_clip` → `yt_clips`, `VideoJob` → `ai_video`.
A lane `ai_video` PublishJob gets no new marker (`ai_video_finalize` links it to the VideoJob's marker). When a `RadarStoryDecision` is linked to a lane IG job, the marker's mode becomes `viral_radar`.
Because of the marker, all existing group-job guards apply: no Page deliveries, hand-over to Group Share instead of publishing, exclusion from Page counts/stock/queues/review lists, slot/JIT bypass for marked news renders, cleanup protection.
`group_jobs.py:mark_group_job` is a no-op for a job that already has a marker.

### Lane daily caps

| Mode | Daily target for groups |
|---|---|
| `ig` | `sources.ig.groups_only_per_day`; `reserve_lane_slot` in `tasks/fan_out.fanout_link` and `tasks/ig_recreate.recreate_post_for_fanpage`, before AI work/job creation. Radar-linked work bypasses this cap and uses Radar's own target. |
| `news` | `sources.news.groups_only_per_day`; `reserve_lane_slot` in `tasks/news_copywriter.py` before copywriting. |
| `discussion` | Lane's own `discussion_daily_count` (normal pipeline). |
| `pinterest` | Lane's own `pinterest_daily_count`. |
| `fb_recreate` | Lane's own `facebook_photo_daily_count`. |
| `yt_clips` | Lane's own `yt_clip_daily_count`. |
| `ai_video` | Lane's own `ai_video_daily_count`. In `services/ai_video_dispatch.claim_job` lane video jobs count in the round-robin `started_today` / `last_started` counters like any page, so lanes take turns with brands. |
| `viral_radar` | Lane's own `radar_daily_max`. |

`lane_daily_cap_reached` is always false for a non-lane fanpage; a cap of 0 means no group posts of that mode. It counts today's markers of brand + mode from the brand's local midnight (`TargetFanpage.timezone`, UTC fallback).
`group_jobs.py:reserve_lane_slot` serializes concurrent IG/news capacity per brand+mode+brand-local date. Under a short Redis lock (`SET NX PX 5000`) it counts today's group-job markers plus live reservation keys `fb_share:lane_res:<brand_id>:<mode>:<YYYY-MM-DD>:<uuid>` (each `EX 1800`, so a reservation left by a killed worker frees itself after 30 min) and creates a new key when below the cap. Returns that key as the token, `None` when denied (or when the lock stays busy for ~6 s), `False` for non-lanes / the marker-count fallback. `release_lane_slot` deletes the key; callers release in `finally`, including after the job commit (the marker then counts instead). Redis failure logs and falls back to the DB count, which does not serialize concurrent work. Unsupported modes raise `ValueError`.
`release_lane_slot` decrements a truthy token. Plain IG fan-out, IG recreate, and news copywriting release when no job is created; successful creation retains the reservation. The recreate scheduling check in `fanout_link` always releases its temporary reservation, because the worker reserves again before AI work.

### Removed 8bc0f82 overflow paths

Lanes replaced the earlier "groups_only" extra-generation paths. Removed: `tasks/ig_recreate._publish_ig_overflow` and `recreate_post_for_fanpage(group_only=...)`; news overflow (converting stale/no-slot Page news into group work) in `tasks/design_renderer.render_pending_designs` and `services/visual_fair_queue` (the bypass for already-marked jobs stays); Viral Radar `group_only:` overflow decisions in `tasks/radar_dispatch`; the extra `_consume_one(group_only=True)` passes in `tasks/{discussion,pinterest,facebook_photo,yt_clip}.py`; the extra idea capacity in `yt_clip._dispatch_analysis`; the Mode 8 group branch in `tasks/ai_video_plan.plan_ai_videos`.
Group content is never leftovers: no stale news, no old unslotted IG jobs, no Page radar overflow.
`render_pending_designs` skips pending news older than 24 hours, including lane/group jobs (`last_error='stale: news older than 24 h, not rendered'`). Fresh marked news still bypasses Page slot/JIT gates.

### Short group captions and follow N/day

- `classify.py:classify_pending_items` produces the short caption in the same classification call and fails closed on invalid output: no fallback to the long Page caption for routing; three counted rejections skip the item (§3).
- `feeder.py:collect_published_items` copies only deliveries published **today** in the brand's local day (`published_at >= max(now - 48 h, local midnight)`), newest first, at most `sources[mode].follow_per_day` per mode, counting `publish_job` items already collected today (lane/group-job items do not use this quota).

### Edit once (see code: `services/ai_image_reuse.py`)

Gate (FIXES4): reuse happens only when the job's fanpage or the candidate's fanpage is a lane whose brand has Group Share enabled; Page↔Page never reuses, and nothing is reused or saved as a raw file while Group Share is off. A cross-brand pair (same Facebook profile) also needs the same accent colours, template style and caption language. Provenance: each record stores `origin_job_id`/`origin_path`; a candidate is skipped when its origin is this job or when the origin job is deleted, skipped, failed, back in pending_design/rendering, or no longer holds that image. `forget(job, reject=True)` runs in `publish_jobs.reedit_job` (and `forget(job)` in render-now), so an image the owner rejected can never come back through a copy.

For jobs whose render calls an AI image engine (IG redesign / IG photo edit, also used by Viral Radar; Facebook photo edit; news/Facebook speaker design; news Pro poster), the AI image is reused across `lanes.py:sibling_fanpage_ids(job.fanpage_id)`: the brand, its lane, and fanpages owning destinations whose `id`/`parent_profile_id` matches one of the brand's Facebook destination keys, plus their lanes.
A sibling job with the same source item, same effective format, same edit prompt and a finished AI image on disk is reused: the file is **copied** to the job's own path (cleanup deletes per job), the engine call is skipped, and the job's engine note records `reused AI image from job <id>`. A sibling still rendering is not waited for. Only the caption differs.
`ai_image_reuse.py:remember` stores a record in `design_analysis_json["ai_image_reuse"]`: `source_key`, `format_key`, `prompt_sha` (SHA-256 of stripped prompt text), `path`, `engine`, `qa_checked`, plus format metadata such as layout. The writer is named `remember`, not `record`.
`find_reusable` checks at most 200 newest sibling jobs, excluding the current fanpage, deleted jobs, and `pending_design`/`rendering`/`failed`/`skipped` states. It requires matching source/format/prompt digest and an existing file; when this job requires QA, the record must have `qa_checked=true`. Lookup failure means render normally.

| Source / format key | Call site and effective prompt |
|---|---|
| `ig:<post.id>` / `ig_edit` | `tasks/ig_recreate.py` original-photo edit; source edit prompt. |
| `ig:<post.id>` / `ig_redesign` | `services/redesign_pipeline.py`; source edit prompt + classified content type + sport. Preview jobs do not reuse. Raw image is composed with this job's own template/text. |
| `news:<article_id>` / `pro_poster` | `services/pro_poster/pipeline.py`; empty prompt key; raw AI image reused before this job's text/template composition. |
| `news:<article_id>` or `fb:<fbid>` / `speaker` | `services/speaker_design.py`; normalized speaker + sorted normalized subjects + sport; this job adds its own title/template. |
| `fb:<fbid>` / `fb_edit` | `tasks/design_renderer.py`; Facebook original-photo edit prompt. Source fbid comes from `GalleryImage.keyword='facebook_photo:<fanpage_id>:<fbid>'`, not the per-fanpage gallery row id. |

`copy_reused` uses `shutil.copyfile` into `designs/job_<id>_reused_<random>.<ext>`. `save_ai_image` retains raw images for composition as `job_<id>_ai_source_<random>`; raw redesign/speaker/Pro poster images are saved only when siblings exist. `reused_note` appends the source job id within the 200-character note limit.
`tasks/cleanup.py:cleanup_old_designs` deletes `owned_reuse_file(job)` with the design only after ordinary live/retention checks and the group-media guard pass. It excludes the design path itself and paths without this job's `job_<id>_` prefix, so removing a source job does not remove a sibling's copy.

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
| GET | `/fanpages/{brand_id}/group-share/lane` | `get_lane`; returns `{"lane_fanpage_id": int | null}`. |
| POST | `/fanpages/{brand_id}/group-share/lane` | `create_lane`; idempotently ensures the lane, returns `{"lane_fanpage_id": int}`; 400 if the id is already a lane; recovers a concurrent-create uniqueness race. |
| GET | `/fanpages/{fanpage_id}/group-share/accounts` | `list_accounts`; brand Facebook profiles/pages, parent login name, delivery configuration. |
| PUT | `/fanpages/{fanpage_id}/group-share/accounts/{destination_id}` | `put_delivery`; validate brand ownership/device, reset changed calibration, reject uncalibrated active phone mode. |
| GET / PUT | `/fanpages/{fanpage_id}/group-share/memberships` | `list_memberships` / `put_memberships`; validate all destinations/groups before upsert; stamp `checked_at`, first joined timestamp. |
| GET | `/fanpages/{fanpage_id}/group-share/tasks` | `list_tasks`; read-only ledger with optional `state`, limit 1–200 (default 50), newest scheduled first. |
| GET | `/fanpages/{fanpage_id}/group-share/summary` | `get_summary`; UTC-day published/planned, all pending-admin, paused accounts, distinct joined groups. |
| POST | `/group-share/pic-reply` | `api/group_share_pic.py:pic_reply`; `channel`, `sender`, optional `username`, `text` ≤2000 characters. |

`schemas/fb_group_share.py:normalize_group_url` accepts supported Facebook/fb host variants, canonicalizes group URLs, drops query/trailing slash, and rejects reserved directory slugs.
Duplicate URL writes return 409. PATCH rejects explicit null for nonnullable catalog fields.
`DeliveryIn` requires PIC name/contact, valid contact format, selected mode for activation, and a device for phone mode.

Lane list exclusions use `lane_fanpage_ids()` in `api/fanpages.py` (fanpage list), `social_brands.py` (brand list), `content_projects.py` (project list), `analytics.py` (overview), `hermes.py` (fanpage reporting), `radar.py` (shadow-report fanpage selection), and `dashboard.py` (fanpage counts, queues, and aggregates). Detail/update and source endpoints keep accepting lane ids.
Global `api/ai_video_ideas.py` idea lists and `api/ai_video_jobs.py` job lists/counts exclude lane fanpage ids **only when no explicit `fanpage_id` is supplied**; lane-scoped UI access remains available. These AI video endpoints filter lanes, not legacy group markers on ordinary brands. Dashboard PublishJob aggregates exclude both lane fanpage ids and `group_publish_job_ids()` (including legacy group jobs); ordinary publish-job lists also exclude group markers.

## 7. Frontend

| File / entry point | Role |
|---|---|
| `frontend/src/app/(dashboard)/brands/[id]/page.tsx` | Mounts `GroupShareTab` under tab key `groupshare`. |
| `frontend/src/components/brand/group-share/GroupShareTab.tsx:GroupShareTab` | SWR configuration, niches, accounts, memberships, queue, summary. Brand on/off, "Group content" (`LaneContent`), "From brand page posts" (`BrandFollowTable`), stored Muse/rule controls, safety caps, account editor, batch membership save. Queue/summary refresh every 30 s; latest 50 queue tasks. |
| `frontend/src/components/brand/group-share/useLaneFanpage.ts` | Calls `POST /fanpages/{brandId}/group-share/lane` (`ensureGroupShareLane`), loads the lane fanpage, and wires the same handlers the brand page uses (IG/news/discussion/Pinterest/FB/YouTube sources, AI video, radar). Saves skip `LANE_LOCKED_FIELDS`. |
| `frontend/src/components/brand/group-share/LaneContent.tsx:LaneContent` | Renders the brand Content-tab mode tabs (`IgTab`, `NewsTab`, `DiscussionTab`, `PinterestTab`, `FacebookTab`, YouTube, AI video, `RadarTab`) for the **lane** fanpage id. IG and news add a "Posts per day for groups" field (`sources.ig/news.groups_only_per_day`); other modes use their own daily count. IG source cards say "Follow group default" (`IGSourceCard` prop `followDefaultLabel`). |
| `frontend/src/components/brand/group-share/laneLock.tsx` | `LANE_LOCKED_FIELDS` = the backend `LOOK_FIELDS` list (keep both in sync); these brand-owned look fields are shown read-only in the lane UI. |
| `frontend/src/components/brand/group-share/BrandFollowTable.tsx:BrandFollowTable` | Table Mode / On the page (brand switch) / Posts per day to groups (`sources[mode].follow_per_day`, 0–50, disabled while the brand mode is off). |
| `frontend/src/components/brand/group-share/DeliveryEditor.tsx:DeliveryEditor` | Phone/PIC mode, device, calibration display, contact, task hours, nullable cap overrides, active toggle. No calibration action. Telegram help requires the PIC to start the bot once. |
| `frontend/src/components/brand/group-share/Fields.tsx` | Shared `Field`, `NumberField`, `Toggle`, `TableWrap`, error formatting, styling, delivery labels; labeled controls and horizontally scrollable keyboard-focusable tables. |
| `frontend/src/app/(dashboard)/facebook-groups/page.tsx:FacebookGroupsPage`, `GroupForm` | Shared `/facebook-groups` catalog: niche filters, add/edit, pause/resume, optional inactive rows, group rules and notes. No hard-delete action. |

`GroupModeList.tsx` (8bc0f82 source-mode panels) was removed.
API functions/types live in `frontend/src/lib/api.ts` and `frontend/src/lib/types.ts`.

## 8. Tests

All backend paths below are under `backend/tests/`.

| File | Coverage |
|---|---|
| `test_fb_group_share_api.py` | Catalog URL/CRUD validation, defaults, settings, account calibration, membership scope/atomicity, task ledger, UTC summaries. |
| `test_fb_group_share_models.py` | Model constraints, migration parity/downgrade, brand-settings defaults/upsert/rollback. |
| `test_fb_share_caps.py` | Ramp, task hours/overnight windows, invalid hours, gap floor/random bounds, remaining quota. |
| `test_fb_share_classify.py` | Same-call short captions/hashtags, verbatim quotes, caption validation, JSON failures, invalid labels, niche preservation/skip, item selection. |
| `test_fb_share_feeder.py` | Local-day follow quota/newest-first selection, source mapping, snapshots/expiry/dedupe, Radar override, filters, media formats, hashtags, hash failure, image loading. |
| `test_fb_share_routing.py` | Membership/identity/day/content filters, fan sentiment/normalization, meme restriction, hash/source dedupe, ordering. |
| `test_fb_share_planner.py` | Local-day caps, ramps, account routing, pacing/windows, dispatch switch, bundle claims/no resend, retries/expiry/stale claims. |
| `test_fb_share_task.py` | Tick registration, per-brand isolation, Redis lock contention and token-safe release. |
| `test_fb_share_pic_messages.py` | Four-message contract, required fields, onboarding/off-topic constants, confirmation text. |
| `test_fb_share_pic_replies.py` | Reply vocabulary, numbered clauses, URL association, conflicts, pending/category precedence. |
| `test_group_share_pic_api.py` | Sender matching, scope/body validation, latest bundle, membership updates, blocked pauses, immutable published tasks. |
| `test_fb_share_group_jobs.py` | Marker/link queries, idempotent hand-over, readiness/hash fail-closed, media retention. |
| `test_fb_share_group_guards.py` | Publisher/delivery/list exclusions and guarded media cleanup. |
| `test_fb_share_group_counted_modes.py` | Lane-owned counted-mode generation and hand-over from manual-mode renders; no extra overflow passes. |
| `test_fb_share_group_overflow.py` | Marked news bypasses Page gates/counters; stale Page news is skipped, not converted; manual-mode IG edit hand-over. |
| `test_fb_share_group_ai_video.py` | Normal-pass lane Mode 8 production/daily count, Page stock, disabled-brand gate, lane idea writer, finalization/linking in manual mode. |
| `test_fb_share_group_review_fixes.py` | Held-output guards, manual-review hand-over, QA finalization, per-job collection isolation, lane Radar daily maximum, cleanup. |
| `test_fb_share_lanes.py` | Field ownership completeness, creation/defaults/sync, sibling profiles, generation gate, automatic brand markers, Radar/video linking and brand-ledger hand-over. |
| `test_fb_share_lanes_api.py` | Lane API, hidden list access, look synchronization and lane-scoped editing. |
| `test_fb_share_lane_caps.py` | Brand-local IG/news quotas, fan-out/recreate/copywriter gates and reservation release; lane allow-lists and brand templates. |
| `test_fb_share_review_fixes2.py` | Concurrent Redis reservations/fallback, global vs scoped AI video visibility, dashboard legacy/lane exclusions, invalid captions retry then skip. |
| `test_ai_image_reuse.py` | Bidirectional brand/lane and same-profile reuse, prompt/type/QA/file guards, IG edit/redesign, Facebook edit, Pro poster/speaker, stale lane news. |
| `test_ai_image_reuse_cleanup.py` | Raw-file retention/deletion, group guards, sibling-copy independence and path ownership. |
| `test_ai_video_lane_fairness.py` | Lane video markers and round-robin fairness against brands and other lanes. |
| `phone_runner/tests/test_pic_send.py` | Ordered sends, literal text/options safety, deadlines, downloads/size limits, sanitized failure, temporary cleanup, endpoint auth/validation. |

## 9. Setup gaps and code concerns

Before PIC use, configure real catalog groups/niches, joined account memberships, source settings/budgets, active PIC deliveries, runner URL/token, working Hermes channel transport, and inbound forwarding with `group_share:pic` credentials.
Enable the brand and set `FB_SHARE_DISPATCH_ENABLED=1` in the task worker's environment when dispatch should run.
The switch is read directly from `os.environ`, not a Pydantic setting; merely enabling a brand is insufficient for outbound sends.

Disabled/unimplemented paths and observed concerns:
- Automatic phone group publishing/calibration is groundwork only. `phone_auto` cannot be activated through the provided UI/API without an existing calibration, and `dispatch_due` never sends phone tasks.
- Muse research, credit, and must-edit controls are persisted but unused by the inspected sharing flow.
- PIC bundle transport can partly succeed before returning failure. The planner clears the committed ref and retries the whole bundle on a reported failure, so already-sent messages can repeat. Only abandoned committed claims are conservatively quarantined as `uncertain`.
- PIC replies update tasks, not the parent item's status. No completion step in these modules promotes `routed` items to `done`/`expired`; the cleanup guard therefore retains their media even after tasks finish.
- Planning checks caps/day eligibility before allocating all future slots, rather than rechecking the resulting slot dates. Large backlogs can cross a day boundary; weekday/cap checks describe the planning day.
- `needs_admin_approval` and `language` are stored catalog metadata but are not consumed by `select_groups` or the PIC reply transitions.

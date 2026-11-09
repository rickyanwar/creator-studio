# Deep Dive D1b: Card Rendering & AI Recreate Pipeline

Refreshed 2026-11-10 at main 8bc0f82.


Architectural analysis of card generation across Mode 2 (News), Mode 3 (IG Recreate), Mode 4 (Discussion), Mode 5 (Pinterest), and Mode 6 (Facebook Photo Recreate), covering Celery task orchestration, headless Chromium rendering, Fabric.js injection contracts, AI visual generation (`visual_engine`, `redesign_pipeline`, `speaker_design`), and failure recovery.

---

## 1. Architectural Topology & Execution Flow

```
[Trigger / Sweep]
   │
   ├─► News (Mode 2): render_design (tasks/design_renderer.py:294)
   ├─► Discussion (Mode 4): render_discussion (tasks/design_renderer.py:568)
   ├─► Pinterest (Mode 5): render_pinterest (tasks/design_renderer.py:768)
   ├─► FB Photo (Mode 6): render_facebook_photo (tasks/design_renderer.py:1069)
   └─► IG Recreate (Mode 3): recreate_post_for_fanpage -> render_ig_recreate (tasks/ig_recreate.py:123, 405)
         │
         ├── [visual_engine in ('chatgpt','auto','gflow')] ──► redesign_card (services/redesign_pipeline.py:44)
         │                                                      └─► visual_engine.generate (9Router cx/gpt-image-2.5)
         │                                                      └─► visual_qa.check_generated
         │                                                      └─► if non-radio: forward to renderer:3001/render
         │                                                      └─► upscaler.upscale_image_bytes (2700px edge)
         │
         └── [Standard / Template Path]
               │
               ▼
   [HTTP POST json payload] (timeout 120s)
               │
               ▼
   [Renderer Service: renderer/server.js:80] (Port 3001)
         │
         ├─► Mutex Queue: Concurrency 1 promise-chain (server.js:31-37)
         ├─► Browser Spawn: chromium.launch() per job (server.js:40, no keep-warm)
         ├─► Page Context: render-page.html + Fabric.js + renderer/inject.js (server.js:46-48)
         └─► Canvas Execution: window.renderTemplate(args) (inject.js:178)
                ├─► Font Preload: Montserrat (700, 800, 900) (inject.js:289-298)
                ├─► Brand Look & Accent Adaptation: normalizeLook + adaptTemplateForAccent (inject.js:120, 136)
                ├─► Layout Engine: Auto-fit loops, line-cap floors, upward anchoring (inject.js:307-887)
               ├─► Image Slots: Cover-fit, face focus points, slot clipping (inject.js:891-981)
               ├─► Watermark Injection: Logo / Text with auto-fit <= 2 lines (inject.js:184-231)
               └─► Export: canvas.toDataURL at multiplier (default scale 2) (inject.js:234)
               │
               ▼
   [Write PNG: /var/www/media/designs/job_{id}_{uuid}.png]
   [Update PublishJob: status='pending_publish']
   [Auto Publish: publish_job.delay(job.id) if publish_mode=='auto']
```

---

## 2. Content-Type Processing Matrix

| Content Type / Task | Celery Task & Trigger | Template Resolution | Photo Sourcing Strategy | Render Payload Specialty | Success State & Side Effects |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **News Content** (`news_content`) | `render_design` (`design_renderer.py:294`); swept by `render_pending_designs` (`:1274`) when `mode2_publish_mode == auto` or manual UI click | `job.design_template_id` category ("news"\|"quote") -> `resolve_template` (`:344`) | `_maybe_compose_speaker_design` if `news_visual_engine == 'chatgpt'` (`:370`); else 3-attempt cascade via `select_image_for_job` (`:377`) | `label`: Fanpage badge (`_news_badge_text`, e.g. "F1 NEWS" / "BREAKING"); `subtitle`/`caption` mapped via `_speaker_line_fields` (`:489`) | Writes PNG (`:519`); `job.status = pending_publish`; `gallery_image.is_used = True`; `article.status = designed` (`:533`); dispatches `publish_job` if auto |
| **Discussion** (`discussion`) | `render_discussion` (`design_renderer.py:568`); swept by `render_pending_designs` (`:1320`) unconditionally (no manual canvas UI) | Category `"discussion"`; falls back to `"news"` (`:613, 619`) | Subject in `design_caption`: fresh gallery (`image_type='face'`, vision match) -> fresh Getty -> stale gallery fallback (`:634-656`) | `title`: question; `label`: `job.design_subtitle` or `"DISCUSSION"`; no `subtitle`/`caption` body text sent (`:708-721`) | Writes PNG (`:730`); `job.status = pending_publish`; `gallery_image.is_used = True`; dispatches `publish_job` if `discussion_publish_mode == auto` |
| **Pinterest** (`pinterest_content`) | `render_pinterest` (`design_renderer.py:768`); swept by `render_pending_designs` (`:1337`) unconditionally | `classify_pinterest_content` (`:827`) -> `"quote"` if face detected, else `"news"` | Bound photo `job.source_gallery_image_id` (`:801`). If `photo_crops_well` is False (`:844`), bypasses renderer entirely & posts raw source image | `title`: idea title; `caption`: idea description; `label` intentionally omitted (`:913-915`) so inject.js hides badge/pill | Writes PNG (`:931`) or posts raw photo; `job.status = pending_publish`; `gallery_image.is_used = True`; dispatches `publish_job` if `pinterest_publish_mode == auto` |
| **Facebook Photo** (`facebook_recreate`) | `design_renderer.py:render_facebook_photo`; swept by `render_pending_designs` | Direct-photo formats bypass templates when original exists; otherwise detected category, then news fallback | Retained original for repost/custom edit; source ChatGPT without prompt forces `_maybe_compose_speaker_design`; otherwise `_pick_fitting_facebook_photo` (3 attempts) | Direct JPEG has no template text. Template quote: speaker subtitle; discussion/news: badge. See §5c | JPEG or template PNG; `pending_publish`; template gallery photos marked used; publishes if `facebook_photo_publish_mode == auto` |
| **IG Recreate** (`ig_recreate`) | `recreate_post_for_fanpage` (`ig_recreate.py:123`) -> `render_ig_recreate` (`:405`); Celery queue `'visual'` if AI engine enabled | `template_ctype` from `analysis.type` ("quote"\|"news"\|split) via `resolve_template` (`:238, 243`) | Traditional: clean photo via `source_news_main` (`:571`) or fallback IG image (`:523`). AI: `plan_photos` (`redesign_pipeline.py:96`) | Standard template payload (`:611-630`); or AI generated image (`cx/gpt-image-2.5`) overlaid with template text via `/render` (`redesign_pipeline.py:373`) | Writes PNG (`:638`, `redesign_pipeline.py:413`); `job.status = pending_publish`; `decision.preview_status = 'done'`; dispatches `publish_job` if auto |

---

## 3. Template Resolution & Text Zone Engine

### Template Hierarchy
Template resolution (`design_images.py:resolve_template`, called by all tasks):
1. **Explicit Job Override**: `job.design_template_id` matches `DesignTemplate.id`.
2. **Fanpage Category Setting**: `TargetFanpage.default_quote_template_id`, `default_discussion_template_id`, or `default_news_template_id`.
3. **Shared Default Pool**: `DesignTemplate.is_default == True` and matching `category`. Fanpage-scoped (`fanpage_id == fanpage.id`) takes precedence over global default (`fanpage_id IS NULL`).

### Category Fallback Rules
- **Discussion Tasks** (`design_renderer.py:613-620`): Tries `"discussion"`; if missing, falls back to `"news"`.
- **Facebook Photo Tasks** (`design_renderer.py:1114-1118`): Tries detected category (`"quote"` or `"discussion"`); if missing, falls back to `"news"`.
- **Pinterest Tasks** (`design_renderer.py:826-834`): Classifies idea text/face into `"quote"` or `"news"`. If template missing, fails job to `pending_design` with configuration error.

### Template Styles & Brand Look Engine

- **Template Styles Architecture** (`backend/app/services/template_styles.py`):
  - Curated styles: `gp` ("GP Racing"), `highlight` ("Highlight"), `classic` ("Classic") (`STYLES`).
  - Standard categories: `news`, `quote`, `discussion` (`CATEGORIES`).
  - Active set: 9 style templates (3 styles × 3 categories); 15 legacy templates marked `is_hidden = True` (backed up to `design_templates_bak_c27`; Release 2 will delete them).
  - Schema additions: `design_templates.style`, `design_templates.is_hidden`; `target_fanpages.template_style` (nullable: `gp` | `highlight` | `classic`), `brand_override`, `accent_color` (override primary), `accent_secondary_color`, `badge_color`, `badge_text_color`, `quote_mark_color`, `quote_streak`.
  - Default colors: stored directly in `template_json.brandDefaults`. Migrations: `c80000000026` (columns), `c80000000027` (9 style templates + 15 hidden), `c80000000028` (projects moved to styles, queued jobs remapped, backup tables `brand_move_bak_c28_*`).
  - Helpers: `upgrade_kept_template(tj, style, category)`, `make_discussion_template(news_tj, style)`, `plan_brand_move(fanpages, templates, jobs)`, `style_template_ids(db, style)`, `style_defaults(db, style)`.
  - Templates API (`backend/app/api/templates.py`): `GET /templates/styles`, `PUT /templates/styles/{style}/defaults`. Serializer adds `style`, `is_hidden`, `brand_defaults`, `replacement_style`. Star default is per category. Updating a fanpage's `template_style` (`PUT /fanpages/{id}`) automatically assigns default template IDs for all 3 categories via `style_template_ids` unless explicitly sent.

- **Brand Look Resolution** (`backend/app/services/brand_look.py:brand_look(fanpage, template)`):
  - Returns resolved dictionary `{primary, secondary, badge, badgeText, quoteMarks, streak}`:
    - If `fanpage.brand_override == True` and valid `fanpage.accent_color`: uses fanpage custom overrides (`accent_color`, `accent_secondary_color`, `badge_color`, `badge_text_color`, `quote_mark_color`, `quote_streak`).
    - Else: extracts `template.template_json.brandDefaults` (falling back to `STYLE_DEFAULTS` or `#E8362B`).
    - Cascade fallbacks: `secondary` and `badge` fall back to `primary`; `badgeText` falls back to `auto_ink` (WCAG relative luminance > 0.5 -> `#0B0B0B` dark ink, else `#FFFFFF` light ink); `quoteMarks` falls back to `badge`; `streak` defaults to `True`.
  - Dispatched as `"look"` alongside legacy `"accent"` across all 6 renderer payloads:
    1. `tasks/design_renderer.py:render_design` (Mode 2)
    2. `tasks/design_renderer.py:render_discussion` (Mode 4)
    3. `tasks/design_renderer.py:render_pinterest` (Mode 5)
    4. `tasks/design_renderer.py:render_facebook_photo` (Mode 6)
    5. `tasks/ig_recreate.py:render_ig_recreate` (Mode 3)
    6. `services/redesign_pipeline.py:redesign_card` (Mode 3 AI)

### Fabric.js Placeholder Roles (`renderer/inject.js`)

| Placeholder Role | Target Element | Layout & Geometry Behaviour | Auto-Fit & Typography Rules |
| :--- | :--- | :--- | :--- |
| `title` (`:307`) | Headline / Quote / Debate Question | Bottom-anchored when `titleAnchorBottom` is set (`:436`): `top = titleAnchorBottom - height`. Fixed width `fixedW = titleObj.width` | **Pass 1**: Shrink by `titleFontSizeStep` (default 2) to `titleMinReadableFontSize` (`:335`). **Pass 2**: Shrink down to absolute floor 12 (`:344`). **Line-Count Mode**: If `titlePreferMaxLines` set (`:377`), shrink to `titlePreferredMinFontSize` (default 55) for target lines, else fallback lines, then floor 12. Parses `**word**` markers into `ranges` (`:242`). |
| `label` (`:543`) | Badge Pill Text ("DISCUSSION", "HOT TAKE", "F1 NEWS", "BREAKING") | Anchored above headline (`:564`): `top = titleObj.top - labelGap - height`. Centered or left-aligned (`:573`). Strips trailing colon (`:549`) | Case-transformed via `titleTextTransform` (`:547`). Fill: `isHot` -> `hotColor` (default `#ffffff`); else `discussionColor` (default `#111111`) (`:554`). Hidden (`visible: false`) if `label` argument is empty (`:623`). |
| `labelBadge` (`:544`) | Pill Background Rect behind `label` | `width = max(labelFixedWidth, textW + padX * 2)` (`:587`). `height = labelFixedHeight` or `labelObj.height + padY * 2`. `top = labelObj.top - padY` | `labelRadius` (`:597`): `"pill"` or `-1` -> `height / 2`; number -> explicit px; default -> `round(height * 0.28)`. Fill: `isHot` -> `hotFill` (`#8e1b1b`); else `discussionFill` (`#f2c300`) (`:614`). |
| `subtitle` (`:629`) | Speaker name / Secondary line | Box grows upward, bottom edge fixed (`:660`): `top = origBottom - height`. Delta `subDeltaY` pushes `title`, glow, chroma, and label upward (`:694-718`) | Font size shrinks to `subtitleMinFontSize` (default `max(22, 0.75 * fontSize)`), then floor 14 (`:648-658`). Supports `**` accent coloring. Hidden if no subtitle (`:688`). |
| `subtitleBadge` (`:673`) | Pill background behind speaker name | Dynamically hugs subtitle text width: `width = max(badge.height, textW + badgePadX)` (default `badgePadX=80`, `badgePadY=20`) (`:677`). Centered on subtitle | Matches subtitle expansion and positioning. Hidden if no subtitle (`:690`). |
| `caption` (`:723`) | Descriptive text under subtitle | Box grows upward, bottom edge fixed (`:752`): `top = origBottom - height` | Font shrinks to `captionMinFontSize` (floor `max(22, 0.75 * fontSize)`), then floor 14 (`:741-750`). Hidden if empty (`:852`). |
| `quoteIcon` (`:859`) | Quote mark / streak glyph | Anchored above headline (`:861`): `top = titleObj.top - iconGap - height` (default `iconGap=20`) | Moves dynamically as headline auto-fits and expands upward. |
| `scrim` (`:872`) | Dark gradient background under text | Dynamically resized: finds `highestY` across title, label, labelBadge, quoteIcon (`:875-878`). `top = max(0, highestY - scrimPad)` (default `scrimPad=70`). `height = canvasHeight - top` | Linear gradient coordinates updated to stretch exactly: `y1 = 0, y2 = height` (`:883-885`). Prevents gradient from darkening image above the text block. |
| `image`, `image_2`, `image_N` (`:895`) | Visual photo slots | Cover-fit scale: `scale = max(slotW / img.width, slotH / img.height) * zoom` (`:939`). `zoom = paramZoom * slotZoom`. Positioned via `focus_points` (`:946`) and clamped to slot bounds | Clipped to `fabric.Circle` if slot is circle/ellipse (`:963`); clipped to `fabric.Rect` otherwise (`:972`). Original placeholder slot removed from canvas. |

### Visual Effects Engine (`renderer/inject.js`)
- **Brand Look & Accent Adaptation** (`inject.js:120, 136`):
  - `normalizeLook(lookOrHex)` accepts hex string or look object `{primary, secondary, badge, badgeText, quoteMarks, streak}`.
  - `adaptTemplateForAccent(templateJson, lookOrHex)`:
    - If `template_json.accentFollowsFanpage` is true, updates title & caption `titleAccentColor = look.secondary`.
    - Updates label and quote icon inks: badge background to `look.badge`, text to `look.badgeText`.
    - Quote marks: set to `look.quoteMarks` (supports group/path and textbox icon fills).
    - Quote streak (`look.streak`): if `streak === false`, hides streak children; otherwise recolors streak PNG via `recolorStreakDataUri` using `max(r,g,b)/255.0 * targetRGB`, preserving bright red streak intensity and alpha. Covered by `renderer/test/look.test.js`.
- **Highlight Glow** (`inject.js:441`): When `titleHighlightGlow` / `captionHighlightGlow` is configured and `**marked**` ranges exist:
  - Accent adaptation recolors an existing title glow only; it never creates one on a template without glow.
  - Base layer sets highlighted text fill to `rgba(0,0,0,0)`.
  - Duplicate `fabric.Textbox` created with glowing `fabric.Shadow` (`blur=14`, `color=accentGlowColor`), transparent base text, and accent fill on ranges, stacked immediately above base layer.
- **Chroma Shift Fringe** (`inject.js:482`): When `titleChromaShift.px` / `captionChromaShift.px` is configured:
  - Clones text twice: `leftCopy` shifted `-px` (default red `rgba(255,40,40,0.55)`), `rightCopy` shifted `+px` (default cyan `rgba(60,255,255,0.55)`).
  - Stacked directly **below** white base text to create RGB-split chromatic aberration.

---

## 4. The Renderer Service Contract

### HTTP Interface (`renderer/server.js`)
- **Endpoint**: `POST /render` (JSON body limit `30mb`).
- **Port**: Default `3001` (`PORT` env).
- **Concurrency**: Guaranteed **1** via promise-chain queue (`server.js:31-37`):
  ```javascript
  let queue = Promise.resolve();
  function enqueue(fn) {
    const run = queue.then(fn, fn);
    queue = run.catch(() => {});
    return run;
  }
  ```
- **Lifecycle**: Chromium spun up per request and closed in `finally` block (`server.js:40, 74`). Timeout: `60_000ms` (`RENDER_TIMEOUT_MS`).
- **Accent/Look Parsing** (`server.js:68`): Passes `body.look || body.accent` (`accent: (body.look && typeof body.look === "object" && !Array.isArray(body.look)) ? body.look : (body.accent || null)`).
- **HTML covers** (`server.js:renderHtmlOnce`, `POST /render-html`): accepts `html`, `width`, `height`, optional `ready_flag` (default `__coverReady`). Uses the same single-render queue and 60 s timeout, waits up to 15 s for `window[ready_flag] === true`, then screenshots the requested viewport as PNG. Empty `ready_flag` disables that wait. Used for HTML-based covers rather than Fabric card templates.

### Payload Schema (`renderer:3001/render`)

```json
{
  "template_json": { "objects": [...], "accentFollowsFanpage": true, "brandDefaults": { "primary": "#E8362B" } },
  "width": 1080,
  "height": 1350,
  "title": "CLEAN HEADLINE WITHOUT EXTRA SPACES",
  "subtitle": "SPEAKER NAME OR ATTRIBUTION",
  "caption": "OPTIONAL CONTEXT LINE",
  "label": "F1 NEWS",
  "watermark": "FANPAGE BRANDING",
  "watermark_image": "data:image/png;base64,...",
  "image_src": null,
  "image_srcs": ["data:image/jpeg;base64,...", "data:image/jpeg;base64,..."],
  "focus_points": [[0.5, 0.38], [0.5, 0.45]],
  "image_zooms": [1.0, 1.15],
  "scale": 2,
  "accent": "#E10600",
  "look": {
    "primary": "#E8362B",
    "secondary": "#E8362B",
    "badge": "#E8362B",
    "badgeText": "#0B0B0B",
    "quoteMarks": "#E8362B",
    "streak": true
  }
}
```

### Watermark Priority & Fitting (`renderer/inject.js:184-231`)
1. **Logo Image (`watermark_image`)**:
   - Takes priority over text if provided and valid.
   - Fixed target width: `width * 0.20` (20% of canvas width). Placed at `left = width * 0.05`, `top = width * 0.045`.
   - Visual properties: `opacity: 0.92`, `shadow: blur 10, offsetY 1`.
2. **Text Watermark (`watermark`)**:
   - Drawn if no image logo provided and string is non-empty.
   - Textbox: `left = width * 0.052`, `top = width * 0.045`, `width = width * 0.6`, `fontSize = width * 0.032`, `fill = rgba(255,255,255,0.6)`, `charSpacing = 60`.
   - **Auto-fit Loop** (`inject.js:224`): Shrinks font size until `textLines.length <= 2 && fontSize > 10`. Never exceeds 2 lines.

---

## 5. Visual Engine & AI Redesign Pipeline

### Mode Toggles & Behavior

| Toggle Field | Valid Values | Behavior in Tasks | AI Engine Execution Path |
| :--- | :--- | :--- | :--- |
| `TargetFanpage.visual_engine` + source override | `"off"`, `"chatgpt"`, `"auto"`, `"gflow"` | Mode 3 uses `ig_mode.resolve_effective_engine`; source override wins. AI renders use `visual`; `gflow` is treated as `chatgpt`. | Effective ChatGPT + source edit prompt: `ig_photo_edit.edit_ig_photo`; otherwise `redesign_pipeline.redesign_card` (§5c) |
| `TargetFanpage.news_visual_engine` | `"off"`, `"chatgpt"` | Controls Mode 2 News (`design_renderer.py:77`). If `"chatgpt"`, intercepts `render_design` before image cascade. | `speaker_design.compose_speaker_design` (`:485`) -> `visual_engine.generate` |
| `FacebookPhotoSource.format` | `follow`, `repost`, `template`, `chatgpt` (NULL = follow) | Controls Mode 6 per source; `fb_visual_engine` is no longer read by rendering/routing. Follow means template. | Repost: original; ChatGPT + prompt: photo edit; ChatGPT without prompt: speaker design; otherwise template (§5c) |

### Visual Engine Architecture (`services/visual_engine.py`)
- **API Target**: 9Router `/images/generations` (`visual_engine.py:220`).
- **Default Model**: `cx/gpt-image-2.5` (`:32`).
- **Default Geometry**: Size `1024x1280` (or `1024x1536` in speaker design), `quality="high"`.
- **Daily Quota Cap** (`visual_engine.py:197-208`):
  - Queries `Settings.visual_engine_daily_max` (default `60`).
  - Counts `AICopyEvent` where `context == 'image_gen'` since midnight Asia/Jakarta (`ZoneInfo("Asia/Jakarta")`).
  - Raises `VisualEngineError('quota', 'daily cap reached')` if count >= daily max.
- **Retry & Error Classification** (`visual_engine.py:249-301`):
  - 2 attempts loop (`for attempt in range(2)`).
  - Status 401/403 -> `VisualEngineError('auth')` (no retry).
  - Status 429 or quota substrings (`quota`, `limit`, `exhausted`, `entitled`) -> `VisualEngineError('quota')` (no retry).
  - Timeout -> `VisualEngineError('timeout')`.
  - Transport / HTTP 5xx -> Retries once; raises `VisualEngineError('transport')`.

### gpt-cli engine layer (2026-10, owner plan `docs/plans/RENCANA-GPTCLI-CREATOR-STUDIO.md`; runbook `docs/runbooks/gptcli-engine.md`)
- **Entry point** `visual_engine.generate_ex(db, req, *, timeout, fanpage_id, exclude) -> GenerationResult(image, engine, meta, event_id)`. `generate()` is now `generate_ex(...).image`.
  - When gpt-cli does not apply (switch off, no token, or fanpage not listed) it calls `_generate_legacy`, which is the old `generate` body verbatim: own daily cap over all `image_gen` events, own event.
- **Engine order** comes from `engine_router.plan_engines`:
  - If gpt-cli does not apply, immediately returns Codex with reason `gptcli_off`; legacy generation handles its own cap.
  - Night is `ai_night_start` to `ai_day_start` WIB (defaults `00:00`–`05:30`, supports a window crossing midnight). Codex only, paced by `codex_budget.wait_night_gap`.
  - Day defaults to `(gptcli, 9router)`, reason `gptcli_first`. A shared cooldown returns Codex only, reason `gptcli_unavailable`.
  - Share selection uses `codex_share_percent` (code/model default 35), `codex_share_window_jobs` (40), and probability `max(0, target_share - observed_share)`. Observed share counts successful-provider Codex events with `plan=share` or `overflow` over recent events whose reason is share/overflow/gptcli_first. It is not a hard percentage cap.
  - A queue length ≥ `codex_overflow_queue_len` (3) in Redis `visual` or gpt serve chooses `(9router, gptcli)`, reason `overflow`, while usage is below `CODEX_OVERFLOW_MAX_PCT=70`. Ordinary share requires usage below `codex_share_max_pct` (60). gpt serve health queue length includes a running profile and is cached 30 s at `gptcli:queue_len_cache`.
  - Weekly usage ahead of `(WIB weekday + 1)/7*100 + 15` disables intentional share/overflow; it does not remove Codex as a fallback.
  - `codex_allowed` removes Codex for rest state, 5 h usage ≥ `codex_night_max_pct` (70) at night or `codex_fallback_max_pct` (85) by day, or Codex-only `image_gen` events today ≥ `visual_engine_daily_max` (60). Explicit `exclude` also removes engines.
  - Empty order → `VisualEngineError('quota')` by day, `VisualEngineDeferred(until)` at night.
- **gpt serve client** `gptcli_client.generate_image` (POST `/v1/images/generations`, size `gptcli_size`, thread `gptcli_thread`, `X-Request-Id = request_id_for(prompt, images)`).
  - Initial POST wait is 90 s, then `job_status` polling every 15 s after ReadTimeout; total wait defaults to 1800 s, running limit 600 s. Completion re-POSTs with the same request ID to retrieve the result. Four missing polls or two unknown polls fail.
  - Temporary errors set Redis `gptcli:unavailable_until` and `gptcli:unavailable_reason`. `unavailable` needs 3 errors within 900 s (`gptcli:unavailable_streak`); success clears the streak. Positive `Retry-After` wins; otherwise quota 10800 s, soft_cap 3600 s, logged_out/cloudflare_challenge 1800 s, unavailable/unreachable 300 s, sleeping until day start. Redis TTL is the delay + 60 s.
  - `generate_text` uses `/v1/text`, high thinking, default 900 s total wait. `gptcli_text_unavailable_until` ignores image-only `quota`/`soft_cap` cooldowns, so image capacity does not block text.
- **One `image_gen` event per call**, with `error_message` starting `plan=<reason>;`. `outcome` is success | recovered | failed | deferred.
- **ChatGPT results** skip full QA and, when mode QA is on, go through `identity_check.check_identity`: SFace < 0.25 rejects at once, otherwise Gemini must confirm same person with confidence ≥ 0.6. Team radio layouts also get OCR (`redesign_pipeline._radio_ocr_ok`), even with mode QA off. Failed identity/OCR returns a failed redesign; it does not generate a second Codex image. Engine transport failure can still fall back within `generate_ex`.
  - In `speaker_design.compose_speaker_design(..., content_kind="news"|"facebook")`, mode QA enables ChatGPT identity checking and the Codex `identity_score ≥ 0.40` gate; QA off bypasses both generated-result gates.
- **Night deferral**: `VisualEngineDeferred` propagates out of `redesign_card` / `compose_speaker_design`. The tasks call `design_renderer._defer_job`, which sets `pending_design` plus `publish_jobs.design_not_before`, with no Celery retry and no long ETA.
  - `render_pending_designs` skips jobs whose `design_not_before` is in the future, and re-dispatches deferred `ig_recreate` jobs (queue `visual`) once the time passes.
  - A manual IG preview fails fast instead of waiting.
- **Queue**: `engine_router.render_queue_for(db, fanpage, "news"|"facebook"|"instagram", visual_engine_override=...)` returns `"visual"` when gpt-cli applies and the effective toggle is `chatgpt`. Facebook uses the explicit per-source override (§5c). Otherwise the old queue is kept (visual-routed news is dispatched by the fair queue §5b rather than direct enqueue).

### AI Card Redesign Pipeline (`services/redesign_pipeline.py`)

1. **Classification & Layout Choice**:
   - `choose_layout` (`services/layout_chooser.py:23`):
     - `team_radio`: F1 niche + `radio_lines` present.
     - `inset`: Transfer/what-if scenario (`scenario != 'none' and target_team`) or 1 distinct inset.
     - `inset2`: 2 distinct insets from `inset_contexts`.
     - `action`: News type + hero labeled action + no secondary.
     - `solo`: Fallback layout.
2. **Photo Sourcing (`services/visual_sourcing.py:plan_photos`)**:
   - Hero: `GalleryImage` with exact name variants, then surname ILIKE only for multiword person names; niche-scoped keywords, nonpending/nonmismatch filters. Person face candidates require `vision_verify_subject` confidence ≥ 0.70, trying the picker winner then up to two alternatives. Nonperson heroes use action photos; F1 people outside the driver grid require face photos.
   - Inset: `_find_inset_pick` rejects hero face/helmet and mismatched context/sport; person face candidates also need identity verification. Jina fallback is event-window-only and requires explicit same-sport/context matches. Team-logo assets are allowed. Missing real person references are dropped, never imagined; MotoGP also drops all imagined insets.
   - What-If Alternative: Finds alternate photo of hero with different pose (`:646`).
3. **Prompt Composition**:
   - `prompt_rules.py:text_zone_line` reserves the bottom 35% for template badge/headline: plain, not dark; no faces, hands, insets or important objects. Shared prompts retain real logos and prohibit invented faces, added text and glow.
4. **Visual QA Gate (`services/visual_qa.py:check_generated`)**:
   - `visual_qa.py:check_generated`, `evaluate` gate identity, source branding and team-radio OCR. `plain_whatif_suit` and `inset_shows_hero` are notes, not blockers. No aesthetic/AI-look/text-zone score gate. Vision exceptions retry through `vision_retry.run_vision_check`, then fail with `qa_unavailable`.
   - Single-call mode: If QA fails, `notes.append("QA failed; no retry (single-call mode)")` (`redesign_pipeline.py:306`); returns `ok=False`.
5. **Final Text Rendering**:
   - If `layout == 'team_radio'`: `final_text_rendered_by = 'ai'`. Text generated directly in image.
   - If non-radio: `final_text_rendered_by = 'template'`. Generated AI image passed as `image_src` to `renderer:3001/render` with fanpage's template (`redesign_pipeline.py:340, 373`).
6. **Upscale**: `upscale_image_bytes(gen_bytes, target_edge=2700)` (`:400`).

### Speaker-Led Design Composition (`services/speaker_design.py`)
- **Trigger** (`design_renderer.py:_maybe_compose_speaker_design`): News uses `news_visual_engine == "chatgpt"`; Facebook calls with `force=True` only for source format `chatgpt` with no edit prompt. `fb_visual_engine` is not consulted.
- **Facial Expression Alignment** (`speaker_design.py:161-226, 438-443`):
  - Computes `story_mood(article_text)`: `"serious"`, `"happy"`, or `"any"`.
  - Checks if speaker photo expression contradicts story mood via `is_bad_expression(speaker_photo, mood)`.
  - If contradiction detected, injects explicit prompt override:
    `_EXPRESSION_EDIT_SERIOUS`: *"Change ONLY the speaker's facial expression to a calm, serious, focused look (mouth closed, no smile, no teeth showing)..."*
- **Identity Gate** (`speaker_design.py:27, 527-529`):
  - Uses OpenCV SFace model (`cv2.FaceRecognizerSF_create`, model `face_recognition_sface_2021dec.onnx`).
  - Computes cosine similarity `identity_score(speaker_bytes, generated)`.
   - With mode QA enabled, Codex results below `IDENTITY_THRESHOLD` (`0.40`) return `None` for standard image fallback. ChatGPT results use `identity_check.check_identity` instead; see §5.
- Prompt text shared with the redesign prompts comes from `services/prompt_rules.py` (`realism_block`, `text_zone_line`, `avoid_block`).

## 5a. Pro poster (Mode 2 news, live since 2026-10-19, 8baa04f)

Switch: `Settings.pro_poster_enabled` + `pro_poster_fanpage_ids` (`[]` = every fan page), checked by `pipeline.pro_poster_applies`. Settings UI: `ProPosterSettingsCard` (Settings > Image). It runs **before** speaker design in `tasks/design_renderer.py` (~590).

`pipeline.compose_pro_poster(db, fanpage, job, article, dry_run=False)`:
1. **Brief** (`brief.build_brief`, text AI): subjects, event, story type, mood, badge label, accent colours, visual ideas, `must_avoid`, decided from the article text only. Cached in `job.design_analysis_json["pro_poster_brief"]`. Parse errors fail closed (`BriefError`).
2. **Story photo**: `story_photo.fetch_story_photo` → `storage/designs/pro_poster_story_<job>.{png,jpg}`.
3. **Photos** (`photos.select_photos`, at most 12 checks, 3 in parallel per batch, early stop once a strong hero (or hero + frontal portrait) and 2 story-fitting context photos are found): candidates from the story photo + gallery (Getty slug = trusted caption). One strict-JSON Gemini check per photo (`check_photo`: person, event match, obstruction, watermark, `face_frontal`, `story_fit`, quality). Picks `hero` (sorted by face_frontal, story_fit, quality), `context`, a front-facing `portrait` when the hero is not frontal, or `face_ref`.
4. **Concept** (`concept.build_concept`, Gemini): key moment, scene, inset, palette, mood, graphic words. Optional; errors → no concept.
5. **Art direction** (`art_direction.direct`): layout `hero_with_context` / `hero_solo` / `face_ref_scene` / `action_full_bleed` / `scene` (no verified photo). Before concept building, a hero without a verified hero/face reference promotes the first secondary subject if their photo verifies; the original cached brief stays unchanged and `pro_poster_promoted` records the substitution. With no references, generate a full-bleed scene without people, faces, team kit or specific vehicles, mapped to engine layout `action`. Photo-check errors defer instead (`typographic` is only the error marker). Designer-brief prompt v4: house style = white-ring inset with a different context, front-facing face reference, no invented faces, series logos banned. The text zone is the bottom 35% (`prompt_rules.TEMPLATE_TEXT_ZONE_PCT`). The wording says "covered later by text, keep it plain, not dark".
6. **One generation**: follows `engine_router.plan_engines` (§5), including configured share, overflow, night and ChatGPT cooldown. Calls `visual_engine.generate_ex(db, req, fanpage_id=fanpage_id)`. `VisualEngineDeferred` propagates; no allowed engine reports attempts=0, generation errors attempts=1.
7. **Safety QA only** (`qa.check_safety`), conditional on `qa_toggles.qa_enabled(fanpage, "news")`: identity via `identity_check` (SFace, reject < 0.25, then Gemini), `gear_identity` (helmet, number, kit, livery) when identity failed without an SFace score, plus event check. No aesthetic scores or second generation; check errors use `vision_retry`.

Results in `design_renderer`: image → `image_marker="pro_poster"`, `design_engine` labels: gptcli → `chatgpt`, 9router → `codex`; notes identify scenes and promoted subjects. No typographic card is published: failures use the old speaker/gallery/manual-image path, with photo-check errors deferred until retries are exhausted. If the AI generation was already used (`pro_poster_spent`), speaker design is skipped, so there is never a second AI image. QA result is stored in `design_analysis_json["pro_poster_qa"]`.

- `pipeline.py:promote_secondary` is tried only without hero/face_ref and without initial photo-check errors. It swaps the first secondary into hero and the original hero into mentioned; the substitution takes effect only with a verified alternate hero/face_ref. Context-only selection can use `action_full_bleed`; no references uses `scene`, mapped to engine layout `action` without people/faces/kit/specific vehicles.
- `design_renderer.py:render_design` defers photo-check errors twice, 10 min each (`PHOTO_CHECK_MAX_RETRIES=2`, `PHOTO_CHECK_RETRY_MINUTES=10`), releasing the fair-queue page claim. Exhaustion uses the ordinary image path; no usable image leaves `pending_design` with `needs manual image`.
- `services/pro_poster/typo_background.py:make_typographic_background`, `background_datauri` still exist. Production rendering no longer calls them. `scripts/pro_poster_eval.py` retains a typographic preview branch and tests still reference the module; `pro_poster_typo` comparisons in `design_renderer` are leftover checks, not an active image-production branch.
- Pro poster photo cache keys/TTLs and the shared breaker are in [design-images.md §1](design-images.md#1-architectural-overview--configuration).

Eval: `python -m app.scripts.pro_poster_eval --jobs <ids> --run <name> [--pace s] [--fresh-brief]` runs dry, writes `storage/pro_poster_eval/<run>/` (`index.json`, `<job>_new.png`, attempts). On the VPS run it with `docker run` on the prod image, not `compose run`.

## 5b. Fair ChatGPT queue (2026-10-20)

- **Service**: `backend/app/services/visual_fair_queue.py` (`pick_next_news_job`, `news_job_waiting`, `served_today`, `mark_served`, `LAST_JOB_KEY`).
- **Beat Dispatch**: `backend/app/tasks/design_renderer.py` `dispatch_fair_visual_news`: beat every 30 s (`celery_app.py` "dispatch-fair-visual-news").
- **How it behaves**:
  - It keeps at most one news render waiting in the visual queue (`LAST_JOB_KEY` + `render_design:queued:<job_id>` check in `news_job_waiting`).
  - Breaking news goes first, then the fan page with the fewest ChatGPT renders today (a Redis counter per WIB day: `served_today`, `visual_fair:news:<YYYY-MM-DD>:<fanpage_id>`).
  - It keeps working when only one page has jobs (work-conserving).
  - `news_copywriter` and `render_pending_designs` no longer enqueue visual-routed news (`render_queue_for(db, fp, "news") == "visual"`).
- **Pre-render stale skip**: `_has_news_slot(db, job, now_utc)` checks `social_delivery_planner.news_slot_available`. Jobs with no News slot within 24 h are skipped BEFORE rendering, with `last_error` "stale: no News slot within 24 h, AI poster not rendered".
- **Just-in-time design (JIT)**: normally a nonpriority job waits until its page's next free slot (`social_delivery_planner.next_news_slot_utc`) is ≤ `JIT_LEAD_MINUTES` (30) away; lookahead can render earlier.
  - The newest job of that page is picked.
  - On dispatch, the page is claimed (`visual_fair:news:claim:<fanpage_id>`, TTL until the slot + 10 min), so one slot gets one design.
  - Jobs that never get a near slot stay `pending_design` and are retired by the 24 h stale sweep without being rendered.
  - Breaking and live-event news bypass JIT; newest priority job is considered first. Served counters expire after 48 h; page claims use `max(900 s, slot delay + 600 s)`.
- **Lookahead exception** (`visual_fair_queue.py:chatgpt_can_look_ahead`, `lookahead_horizon`, `ahead_count`): up to `LOOKAHEAD_HOURS=6`, with `MAX_AHEAD_PER_PAGE=6`. ChatGPT must be outside its night window, idle, available, and below 42 `gptcli` image events in 3 h (`GPTCLI_WINDOW_BUDGET`, leaving headroom). Codex lookahead requires allowed budget and an empty visual queue; night horizon ends at day start, daytime cooldown horizon ends at ChatGPT availability. Lookahead bypasses an existing page claim.
- **Freshness / groups**: `pick_next_news_job` applies `event_days.news_fresh_hours` using the minimum configured active-destination stale hours. Stale nonbreaking jobs or jobs without a same-news-day/24 h slot may be converted to group-only jobs if quota remains, otherwise skipped. Group-only jobs bypass Page fair counters/JIT and are dispatched by `render_pending_designs`.
- **Stale-only jobs leave the queue**: `publisher.publish_job` sets a job to `skipped` when ALL its deliveries are `skipped` with a "stale:" error (before, they stayed `pending_publish` = "Ready to publish" forever).
- **Reuse finished designs (one-off)**: `python -m app.scripts.replan_stale_designs [--apply] [--hours 24] [--fanpage-id N]`.
  - Dry-run by default.
  - `scripts/replan_stale_designs.py:replan_stale_designs` selects nondeleted `pending_publish` News jobs with at least one delivery, all skipped with `stale:` errors, and no `repliz_schedule_id`/`facebook_post_id`. It does not select jobs already marked skipped.
  - Fresh jobs (default ≤24 h) are processed newest first per page: delete old skipped deliveries, call `ensure_deliveries`, skip if all replacements are stale. Old candidates become skipped. Dry run executes planning then rolls back; `--apply` commits.
  - It never calls Repliz itself.

---

## 5c. Per-source photo edits & Facebook formats (2026-11-04)

- **Result QA switches** (`services/qa_toggles.py:qa_enabled`, `qa_mode_for_content_kind`): migration `c80000000045_fanpage_qa_toggles.py:upgrade` adds `qa_news_enabled`, `qa_ig_enabled`, `qa_facebook_enabled`, `qa_ai_video_enabled` to fanpages, all default True. Missing fanpage/value or unknown mode keeps QA enabled. The switches govern generated-result checks in Pro poster, speaker design, IG redesign/edit, Facebook edit, and AI video; they do not disable source-photo identity/usability gates.
- **Badge switches** (`design_renderer.py:badge_label_for`, `_news_badge_text`, `_discussion_badge_text`, `render_card_png`): migration `c80000000047_badge_switches.py:upgrade` adds `badge_breaking_enabled=True`, `badge_discussion_enabled=True`, `news_badge_percent=100`. News selection is stable per job: SHA-1 of `badge:<job_id>` modulo 100 < percent (clamped 0–100); no job ID always selects. Disabling BREAKING falls back to the regular page/niche news label. Discussion/HOT TAKE obey only the discussion switch, not the percentage. Empty labels hide both text and pill in `inject.js`; speaker subtitle badges are separate.

- **IG entry** (`tasks/ig_recreate.py:render_ig_recreate`, `_try_ig_edit`, `_render_ig_edit`): before template/title validation, resolve the `(fanpage_id, ig_source_id)` link. A nonblank `fanpage_sources.chatgpt_edit_prompt` plus `ig_mode.resolve_effective_engine(...) == "chatgpt"` edits the first stored IG image, bypassing card rendering. Blank prompt keeps the existing redesign path. Migration `c80000000052_fanpage_source_edit_prompt.py:upgrade` adds the nullable Text field.
- **Shared editor** (`services/ig_photo_edit.py:edit_ig_photo`, `build_edit_prompt`): references the original through `visual_engine.generate_ex` (300 s timeout). Owner instructions are followed by rules preserving real faces, natural skin/grain, logos and camera perspective; no HDR/over-sharpening or added borders/captions/watermarks/frames. Engine is recorded as `chatgpt`, `codex` or `codex_fallback`; no aesthetic QA pass.
- **Identity gate** (`ig_photo_edit.py:edit_ig_photo`, `identity_check.py:check_identity`): when mode QA is enabled and a reference face is detected, SFace below 0.25 rejects immediately; otherwise Gemini must explicitly confirm identity at confidence ≥ 0.6. Unsure/error rejects the edit (fail closed); the caller uses the original instead. The gate is conditional on QA and face detection.
- **JPEG contract** (`ig_photo_edit.py:clean_jpeg`): applies EXIF orientation, converts to RGB, clears metadata and re-encodes without EXIF/XMP/C2PA. Successful edits and IG original fallbacks are cover-fit to **1080×1350**. Facebook repost/original fallback passes `size=None`, preserving dimensions instead of forcing 4:5.
- **Facebook branches** (`tasks/design_renderer.py:render_facebook_photo`, `_render_facebook_direct`): resolve the newest matching idea and its source's current format. `repost` posts the retained original plus generated caption; `chatgpt` + nonblank prompt edits it without template text. `chatgpt` without prompt forces speaker-led AI design; `follow`/`template` use the category template with clean-photo sourcing. Missing retained originals fall back to template rendering with an “original photo missing” note.
- **Queue** (`services/fb_source_format.py:queue_override`, `engine_router.py:render_queue_for`): both ChatGPT design and custom edit supply the explicit `chatgpt` override, routed to `visual` when gpt-cli applies. Facebook never reads the legacy fanpage engine toggle. Consumers and pending sweeps use these helpers.
- **Failure / publish** (`_render_ig_edit`, `_render_facebook_direct`): failed/empty/identity-rejected edit uses a cleaned original JPEG and `design_engine="original"` with the reason in `design_engine_note`. `VisualEngineDeferred` instead calls `_defer_job`. Success/original fallback becomes `pending_publish`, clears `last_error`, and enqueues publishing only when the mode is auto. IG also completes a linked Radar preview.
- **Source provenance / retention**: [content-sources.md §6.3](content-sources.md#63-per-source-formats--retained-originals-2026-11-04); **UI source cards**: [frontend.md §4](frontend.md#4-fanpagesid-tab-configuration--field-specifications).

## 6. Status Transitions, Concurrency & Recovery

```
                     ┌───────────────────────────────────────────────┐
                     │                                               │
                     ▼                                               │
             [pending_design]                                        │
                     │                                               │
                     │  Atomic Claim (UPDATE ... WHERE status=       │
                     │  'pending_design' SET status='rendering')     │
                     ▼                                               │
                [rendering] ────► [Interrupted / Container Kill]     │
                     │                         │                     │
                     │                         ▼                     │
                     │                Orphaned > 30 mins             │
                     │                         │                     │
                     │                         ▼                     │
                     │              recover_stuck_renders            │
                     │              (design_renderer.py:1388)        │
                     │                         │                     │
                     │                         └─────────────────────┘
                     ├─────────────────────────┬─────────────────────────┐
                     │ (Success)               │ (Needs Manual Image)    │ (Fatal Error)
                     ▼                         ▼                         ▼
             [pending_publish]          [pending_design]              [failed]
                     │                 (last_error set)           - empty title
                     │                         │                  - 'other' redesign fail
                     ▼                         ▼
             [publish_job] delay      render_pending_designs
             (if auto publish)        (held for 6h cooldown)
```

### Atomic Claim Pattern
Every render worker performs an atomic SQL query:
```python
claimed = db.query(PublishJob).filter(
    PublishJob.id == job_id,
    PublishJob.status == PublishJobStatus.pending_design,
    PublishJob.content_type == expected_type,
).update({"status": PublishJobStatus.rendering}, synchronize_session=False)
db.commit()
if not claimed:
    return
```
Prevents race conditions between Celery beat sweeps, manual UI triggers, and retries.

### Recovery Sweeps & Guards
1. **`recover_stuck_renders`** (`design_renderer.py:1388`):
   - Periodic Celery task.
   - Finds `PublishJob.status == 'rendering'` and `updated_at < now - 30 minutes` (`_STALE_RENDERING_MINUTES = 30`).
   - Resets status to `pending_design` with `last_error = "Recovered from a stuck 'rendering' state (likely an interrupted worker) — retried automatically."`.
2. **Missing Image Cooldown** (`design_renderer.py:1270, 1292`):
   - `_NEEDS_MANUAL_IMAGE_RETRY_COOLDOWN_HOURS = 6`.
   - Jobs whose `last_error` matches `needs manual image%` are excluded from the `render_pending_designs` sweep until 6 hours have elapsed, preventing continuous paid Getty/vision API burns.
3. **Queue Separation**:
   - `celery_app.py` routes `render_design` to `ai` (worker-ai); explicit `queue="visual"` overrides this for fair-dispatched gpt-cli News. Other traditional card tasks retain their existing default routing.
   - AI visual recreate jobs dispatched to dedicated queue `'visual'` (`ig_recreate.py:329`): `render_ig_recreate.apply_async((job.id,), queue='visual')`.

---

## 7. Refine Passes, Retries, and Failure Hierarchy

### Verification & Retry Matrix

| Mechanism / Pass | File & Lines | Trigger Condition | Action Taken | Fallback / Exhaustion |
| :--- | :--- | :--- | :--- | :--- |
| **Headline Number Check** | `ig_recreate.py:63-69` | Source headline numbers missing from rewritten title (`not source_numbers.issubset(...)`) | Re-prompts model with `ERROR: You must include these exact numbers...` | If second attempt still fails, reverts AI rewrite to raw cleaned source headline (`:68`) |
| **Caption Similarity Check** | `ig_recreate.py:278-295` | `captions_too_similar` returns True against other fanpage captions for same radar story | Retries generation up to 2 times | If still similar after 2 retries, marks `decision.status = SKIPPED_SIMILAR_CAPTION` and halts job creation (`:292`) |
| **Face-Fit Safe Zone Loop** | `design_renderer.py:377-397, 1050-1065` | Candidate photo fails `single_photo_face_fits` (face center exceeds template safe ceiling) | Excludes candidate via `exclude_marker` and retries up to 3 candidate photos | If all 3 fail, resets job to `pending_design` with `last_error="needs manual image..."` |
| **Expression Override Pass** | `speaker_design.py:438-443` | `is_bad_expression(speaker_photo, mood)` detects expression contradicting story mood | Injects `_EXPRESSION_EDIT_SERIOUS` or `_EXPRESSION_EDIT_HAPPY` into image generation prompt | Generation request proceeds with edited prompt; SFace identity score verifies result |
| **SFace Identity Gating** | `speaker_design.py:compose_speaker_design` | Mode QA on, Codex result, `identity_score(speaker_bytes, generated) < 0.40` | Rejects generated image, logs warning, returns `None` | `design_renderer` falls back to standard image selection |
| **Visual QA Verification** | `redesign_pipeline.py:redesign_card` | Mode QA on, Codex result, `check_generated` rejects identity/branding/radio text | No second generation; check exceptions can retry through `vision_retry` | Returns `RedesignResult(ok=False)`. Falls back to template path or fails job |
| **Celery Task Retries** | `design_renderer.py:293, 567, 767, 1068`; `ig_recreate.py:404` | Unhandled Python exception during render task execution | `job.status` reset to `pending_design`, task re-queued with `self.retry(countdown=180, max_retries=2)` | Celery marks task failed; `recover_stuck_renders` acts as outer safety net |

### What Fails the Job vs. What Halts/Retries
- **Fatal Job Failure (`job.status = failed`)**:
  - Empty `design_title` in `render_ig_recreate` (`ig_recreate.py:456`).
  - Redesign pipeline fails when `content_type == 'other'` (`ig_recreate.py:512`). There is no safe template fallback for unclassified "other" posts.
- **Graceful Halt to Manual Review (`job.status = pending_design`)**:
  - Missing template configuration (`last_error = "no design template configured..."`).
  - No matching photo found in gallery/search (`last_error = "needs manual image..."`).
  - Celery task exception caught (`job.status` released from `rendering` to `pending_design` before `self.retry`).

---

## Verified claims

renderer/server.js:31 - Concurrency 1 mutex queues every Chromium render request through a single chained Promise.
renderer/server.js:40 - Chromium instance launches per request with --no-sandbox, --disable-gpu, --disable-dev-shm-usage and closes immediately in a finally block.
renderer/inject.js:184 - Watermark rendering prioritizes image logo over text, scaling logo to 20% canvas width with opacity 0.92 and drop shadow blur 10.
renderer/inject.js:377 - Discussion cards with titlePreferMaxLines use a 3-tier font size descent, falling back to titlePreferredMinFontSize (default 55) before shrinking to floor 12 without truncating.
renderer/inject.js:597 - Label badge pill radius defaults to round(badgeH * 0.28) unless labelRadius is explicitly "pill" or -1 (which forces badgeH / 2).
renderer/inject.js:873 - Scrim overlay dynamically repositions top to highestY - pad (default pad 70) and recalculates linear gradient coords to hug text.
backend/app/tasks/design_renderer.py:306 - render_design uses atomic SQL UPDATE (pending_design -> rendering) to claim jobs and exits silently if 0 rows update.
backend/app/tasks/design_renderer.py:1270 - _NEEDS_MANUAL_IMAGE_RETRY_COOLDOWN_HOURS suppresses auto-retry of jobs stuck on "needs manual image" for 6 hours.
backend/app/tasks/design_renderer.py:1384 - recover_stuck_renders resets jobs orphaned in rendering status older than 30 minutes back to pending_design.
backend/app/tasks/ig_recreate.py:63 - _rewrite_news_title verifies source numbers exist in AI rewrite; if missing after re-prompt, falls back to raw source text.
backend/app/services/visual_engine.py:197 - generate queries AICopyEvent count since midnight Asia/Jakarta and raises VisualEngineError('quota') if >= visual_engine_daily_max (default 60).
backend/app/services/speaker_design.py:compose_speaker_design - With mode QA enabled, Codex images must pass IDENTITY_THRESHOLD = 0.40; ChatGPT images use identity_check instead.

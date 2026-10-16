# Deep Dive D1b: Card Rendering & AI Recreate Pipeline

> Generated 2026-10-16 at main 25edf38 from a full read of the listed files. The "Verified claims" list at the bottom was checked line by line against the code. Other details are a careful map but not each re-checked. Read the real code before changing behaviour, and update this file when the code changes.


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
               ├─► Accent Adaptation: Color recalculation & streak tint (inject.js:117, 269)
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
| **Facebook Photo** (`facebook_recreate`) | `render_facebook_photo` (`design_renderer.py:1069`); swept by `render_pending_designs` (`:1355`) unconditionally | `_facebook_photo_category` (`:982`) ("discussion"\|"quote"\|"news"); falls back to `"news"` (`:1115`) | `_maybe_compose_speaker_design` if `fb_visual_engine == 'chatgpt'` (`:1128`); else `_pick_fitting_facebook_photo` (`:1137`) 3 attempts | Grounded photo search (`:1023`). If quote: `subtitle` is speaker, `label` is empty. If discussion: `label` is badge. If news: `label` is badge (`:1179-1185`) | Writes PNG (`:1215`); `job.status = pending_publish`; `gallery_image.is_used = True`; dispatches `publish_job` if `facebook_photo_publish_mode == auto` |
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
- **Accent Adaptation** (`inject.js:117`): If `accent` hex provided and `template_json.accentFollowsFanpage` is true:
  - Computes luminance: `relativeLuminance(rgb)` (`:24`). If `isLight > 0.5`, sets dark text color `#0B0B0B` on label and quote path.
  - Updates title & caption: `titleAccentColor = accentHex`.
  - Recolor Streak (`:79`): Recolor quoteIcon PNG image pixel-by-pixel using luma weighting `(0.299*r + 0.587*g + 0.114*b)/255.0 * targetRGB`.
- **Highlight Glow** (`inject.js:441`): When `titleHighlightGlow` / `captionHighlightGlow` is configured and `**marked**` ranges exist:
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

### Payload Schema (`renderer:3001/render`)

```json
{
  "template_json": { "objects": [...], "accentFollowsFanpage": true },
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
  "accent": "#E10600"
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
| `TargetFanpage.visual_engine` | `"off"`, `"chatgpt"`, `"auto"`, `"gflow"` | Controls Mode 3 IG Recreate (`ig_recreate.py:176, 328, 479`). Routed to Celery queue `'visual'`. If `"gflow"`, logged and treated as `"chatgpt"` (`:481`). | `redesign_pipeline.redesign_card` (`:44`) -> `visual_engine.generate` (`:265`) -> 9Router `cx/gpt-image-2.5` |
| `TargetFanpage.news_visual_engine` | `"off"`, `"chatgpt"` | Controls Mode 2 News (`design_renderer.py:77`). If `"chatgpt"`, intercepts `render_design` before image cascade. | `speaker_design.compose_speaker_design` (`:485`) -> `visual_engine.generate` |
| `TargetFanpage.fb_visual_engine` | `"off"`, `"chatgpt"` | Controls Mode 6 FB Recreate (`design_renderer.py:77, 1103`). If `"chatgpt"`, intercepts `render_facebook_photo`. | `speaker_design.compose_speaker_design` (`:485`) -> `visual_engine.generate` |

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

### AI Card Redesign Pipeline (`services/redesign_pipeline.py`)

1. **Classification & Layout Choice**:
   - `choose_layout` (`services/layout_chooser.py:23`):
     - `team_radio`: F1 niche + `radio_lines` present.
     - `inset`: Transfer/what-if scenario (`scenario != 'none' and target_team`) or 1 distinct inset.
     - `inset2`: 2 distinct insets from `inset_contexts`.
     - `action`: News type + hero labeled action + no secondary.
     - `solo`: Fallback layout.
2. **Photo Sourcing (`services/visual_sourcing.py:plan_photos`)**:
   - Hero: `GalleryImage` (label `face` or `action`) with exact keyword match -> surname ILIKE -> vision picker prompt (`:531`).
   - Inset: Inset context search in gallery. If candidate shows hero face/helmet, vision rejects it (`:328`). Fallback to Jina search if in event window (`:354`), or asset team logo (`:405`), or imagined mode (`:412`).
   - What-If Alternative: Finds alternate photo of hero with different pose (`:646`).
3. **Prompt Composition**:
   - Bottom 35% explicitly reserved: *"Bottom 35% MUST be a smooth dark gradient with no faces, no bodies, no objects, completely empty (a headline is added later)"* (`visual_engine.py:79, 91, 101, 148`).
4. **Visual QA Gate (`services/visual_qa.py:check_generated`)**:
   - Evaluates: `identity_hero`, `identity_inset`, `plain_whatif_suit`, `inset_shows_hero`, `text_on_image`, `source_branding_visible`, `ai_look >= 4`, `bottom_area_empty`, `radio_text_matches`.
   - Single-call mode: If QA fails, `notes.append("QA failed; no retry (single-call mode)")` (`redesign_pipeline.py:306`); returns `ok=False`.
5. **Final Text Rendering**:
   - If `layout == 'team_radio'`: `final_text_rendered_by = 'ai'`. Text generated directly in image.
   - If non-radio: `final_text_rendered_by = 'template'`. Generated AI image passed as `image_src` to `renderer:3001/render` with fanpage's template (`redesign_pipeline.py:340, 373`).
6. **Upscale**: `upscale_image_bytes(gen_bytes, target_edge=2700)` (`:400`).

### Speaker-Led Design Composition (`services/speaker_design.py`)
- **Trigger**: Enabled when `news_visual_engine == "chatgpt"` or `fb_visual_engine == "chatgpt"` (`design_renderer.py:77-79`).
- **Facial Expression Alignment** (`speaker_design.py:161-226, 438-443`):
  - Computes `story_mood(article_text)`: `"serious"`, `"happy"`, or `"any"`.
  - Checks if speaker photo expression contradicts story mood via `is_bad_expression(speaker_photo, mood)`.
  - If contradiction detected, injects explicit prompt override:
    `_EXPRESSION_EDIT_SERIOUS`: *"Change ONLY the speaker's facial expression to a calm, serious, focused look (mouth closed, no smile, no teeth showing)..."*
- **Identity Gate** (`speaker_design.py:27, 527-529`):
  - Uses OpenCV SFace model (`cv2.FaceRecognizerSF_create`, model `face_recognition_sface_2021dec.onnx`).
  - Computes cosine similarity `identity_score(speaker_bytes, generated)`.
  - Rejection threshold: If score `< IDENTITY_THRESHOLD` (`0.40`), rejects and returns `None` (falling back to standard image cascade).

---

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
   - Standard renders run on default Celery worker.
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
| **SFace Identity Gating** | `speaker_design.py:527-529` | `identity_score(speaker_bytes, generated) < 0.40` | Rejects generated image, logs warning, returns `None` | `design_renderer` falls back to standard multi-tier image selection cascade (`:376`) |
| **Visual QA Verification** | `redesign_pipeline.py:305-308` | `check_generated` returns `passed == False` on any of 8 criteria | Single-call mode: logs `notes.append("QA failed; no retry (single-call mode)")` | Returns `RedesignResult(ok=False)`. Falls back to template path or fails job |
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
backend/app/services/speaker_design.py:27 - compose_speaker_design rejects generated images with OpenCV SFace cosine similarity below IDENTITY_THRESHOLD = 0.40.

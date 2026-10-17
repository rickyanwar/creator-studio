# Deep Dive D1a: `backend/app/services/design_images.py`

> Generated 2026-10-16 at main 25edf38 from a full read of the listed files. The "Verified claims" list at the bottom was checked line by line against the code. Other details are a careful map but not each re-checked. Read the real code before changing behaviour, and update this file when the code changes.


Comprehensive architectural mapping of image sourcing, face detection, crop geometry, vision gating, split/inset composition, and fail-safe boundaries across 3,836 lines of production design pipeline code.

---

## 1. Architectural Overview & Configuration

- **Core Module Purpose**: Resolves templates, sources slot images, performs computer vision gating (identity, quality, watermarks, framing), computes face-centered crops, and synthesizes full-bleed canvas extensions for single-slot, circular-inset, and dual-subject split layouts.
- **Render Scale Multiplier** (`design_images.py:21`): `DESIGN_SCALE = int(os.getenv("DESIGN_RENDER_SCALE", "2"))`. All internal canvas extensions (`fit_crop_top_solid`, `reflect_extend`, `content_aware_split_extend`, `prepare_design_images`) composite at 2× slot dimensions (~2K target) so detail survives Fabric.js rendering.
- **Vision Transport & Payloads** (`design_images.py:40`): `_vision_datauri(image_bytes, max_dim=768, quality=78)` rescales images to <=768px JPEG quality 78 before sending to 9Router. Prevents HTTP 413 Payload Too Large when bundling up to 8 candidate data-URIs. Callers keep original uncompressed bytes for final rendering.
- **Model Fallback Chain & Circuit Breaking** (`design_images.py:77-105`):
  - Primary: `settings.nine_router_vision_model`.
  - Ordered fallbacks (`_VISION_MODEL_FALLBACKS`, 2026-10-17): `FlashFast` (combo), `ag/gemini-3.8-flash-medium`, `-low`, `-high` — owner rule: Gemini 3.8 Flash only.
  - Excluded models: `ag/claude-*` (ignores image), plain `ag/gemini-3-flash` (returns empty), `ag/gemini-3.1-flash-image` (hallucinates descriptions), and any `cx/*` model.
  - Active dead-model filtering: `model_health.is_dead(model)` prunes known broken endpoints; retains full list if all are flagged dead.
- **Low-Level Vision Execution** (`design_images.py:108-174`): `_vision_chat` sets `OpenAI(timeout=30.0, max_retries=0)` to prevent SDK exponential backoff from compounding across model fallback attempts. Emits telemetry via `ai_caption.log_ai_copy_event` (tracks `latency_ms`, `models_tried`, `outcome="success"|"recovered"|"failed"`).

---

## 2. Template Resolution Cascade

Implemented in `design_images.py:resolve_template(db, category, fanpage=None, job_template_id=None)` (lines 195-229).

```
Job Override (job_template_id)
   └─► DB query: DesignTemplate.id == job_template_id
          └─► Found: Return immediately
          └─► None: Proceed to Fanpage Setting

Fanpage Default Setting
   └─► Fanpage attribute lookup:
          "quote"      -> fanpage.default_quote_template_id
          "discussion" -> fanpage.default_discussion_template_id
          "news" / all -> fanpage.default_news_template_id
   └─► Found in DB: Return template
   └─► None: Proceed to Shared Default Pool

Shared Default Pool (DesignTemplate.is_default == True, DesignTemplate.category == category)
   └─► If fanpage provided: filter (fanpage_id == fanpage.id) | (fanpage_id IS NULL)
   └─► If fanpage None: filter (fanpage_id IS NULL)
   └─► Order by: DesignTemplate.fanpage_id.desc().nullslast() (fanpage-specific overrides global)
```

### Cross-Pipeline Category Routing
| Template Category | Pipeline / Mode Callers | Trigger Condition |
| :--- | :--- | :--- |
| `"news"` | Mode 2 (`news_copywriter`), Mode 3 (`ig_recreate`), Mode 5 (`pinterest`) | Standard news articles; Mode 5 Pinterest ideas whose photo has **no face** |
| `"quote"` | Mode 3 (`ig_recreate`), Mode 5 (`pinterest`) | Quote cards; Mode 5 Pinterest ideas whose photo **has a detected face** |
| `"discussion"` | Mode 4 (`discussion`), manual debate cards | Two-subject debate / head-to-head graphics |

---

## 3. Photo Sourcing Cascade

Source order: **Cooldown-Fresh Gallery DB** -> **Live Getty Sourcing** -> **Stale Gallery DB Fallback** -> **Topic Web Search**.

```
[News Headline / Story]
         │
         ▼
extract_two_subjects(title, niche)  ──► extract primary subject
         │
         ▼
pick_split_image_type(title, niche) ──► classify "face" vs "action"
         │
         ▼
Step 1: find_gallery_datauri(..., allow_stale_reuse=False)
         │──► HIT: return (datauri, local_path)
         └──► MISS: Proceed to Step 2
         │
         ▼
Step 2: fetch_subject_datauri(..., image_type, niche) [Live Getty Search]
         │──► Searches Getty API via 9Router Jina markdown fetch
         │──► Persists candidate files to storage/gallery/<slug>/
         │──► Inserts GalleryImage records with date estimates (parse_asset_id)
         │──► Identity verification: vision_verify_subject (conf >= 0.70)
         │──► HIT: return (datauri, source_image_url)
         └──► MISS: Proceed to Step 3
         │
         ▼
Step 3: find_gallery_datauri(..., allow_stale_reuse=True) [Stale Fallback]
         │──► Bypasses 14-28 day cooldown; selects oldest used photo
         │──► HIT: return (datauri, local_path)
         └──► MISS: Proceed to Step 4 / Caller Fallback
         │
         ▼
Step 4: fetch_topic_datauri(title, niche, excerpt) [Caller Level]
         │──► Query: title + niche (Getty search template -> Google fallback)
         │──► Upscales candidate bytes (FSRCNN)
         │──► Gated by vision_verify_match(title, excerpt, niche) (Fail-closed)
         └──► MISS: Fall back to scraped IG/article hero image
```

### DB Gallery Retrieval Details
- **Active Niche Keywords** (`design_images.py:177`): `niche_keywords(db, niches)` queries active `GalleryKeyword.keyword` mapped to subscribed niches.
- **Cooldown Window** (`design_images.py:2847-2889`): `GALLERY_REUSE_COOLDOWN_MIN_DAYS = 14`, `MAX_DAYS = 28`. `_eligible_rows` evaluates `last_used_at < (utcnow() - random.randint(14, 28) days)`. Ordered by `captured_at.desc().nullslast(), func.random()`. Stale fallback sorts by `last_used_at.asc().nullsfirst()`.
- **Usage Tracking** (`design_images.py:2901`): `_mark_gallery_image_used` marks `is_used = True`, `last_used_at = datetime.utcnow()`, commits DB.
- **Substring Needle Matching** (`design_images.py:2934-2945`): Replaces non-alphanumeric chars with `%`. Matches `GalleryImage.keyword.ilike()` OR `func.array_to_string(GalleryImage.extra_keywords, ',').ilike()`.
- **Exclusion Filters** (`design_images.py:2958-2962`): Respects `exclude_paths` covering `GalleryImage.id`, `source_image_url`, or `local_path`.

---

## 4. Detection & Crop Mathematics

### Detectors: YuNet DNN vs Haar Cascade
- **YuNet Initialization** (`design_images.py:589-616`):
  - Model ONNX URL: `_YUNET_MODEL_URL = "https://media.githubusercontent.com/media/opencv/opencv_zoo/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx"` (fetched via `media.githubusercontent.com` to bypass 131-byte Git LFS pointer files).
  - Cache target: `storage_base_path/sr_models/face_detection_yunet_2023mar.onnx` (>100KB verification).
  - Score threshold: `score_threshold = 0.6`.
  - Lazy thread-safe load: `_yunet_lock` and `_yunet_state`.
- **Input Normalization Trick** (`design_images.py:621-643`): `_YUNET_WORKING_SIZE = 640`. Full-resolution images (e.g. 2448×1632) cause YuNet confidence to drop to ~0.58 on obvious faces. Downscaling to max edge 640px raises confidence to 0.94 without losing normalized bbox precision.
- **Haar Fallback** (`design_images.py:669-681`): `haarcascade_frontalface_default.xml`, `scaleFactor=1.1`, `minNeighbors=5`, `minSize=(w*0.06, h*0.06)`.
- **Combined Dominant BBox** (`design_images.py:656`): `_dominant_face_bbox(image_bytes)` returns `(fx, fy, fw, fh)` as normalized fractions (0..1). Selects max face area `fw * fh`.

### Image Quality & Sharpness Floor
- **Laplacian Variance Floor** (`design_images.py:694-739`): `_is_low_quality_photo(image_bytes)`.
  - Minimum edge threshold: `_LOW_QUALITY_MIN_EDGE = 400` px.
  - Variance cutoff: `_LOW_QUALITY_BLUR_VARIANCE = 150.0` computed on size-normalized 500px-wide grayscale copy.
  - Calibration: job 5300 blurry photo scored ~85; confirmed sharp photos scored ~948 to ~1697.
  - Unconditional: runs prior to face detection to block upscaled blurry images regardless of incidental background faces.

### Focus Points and Ceilings
- **Safe Ceiling Calculation** (`design_images.py:1607-1637`): `_safe_face_cy_ceiling(template_json, canvas_height)` reads `top` coordinate of `title` or `scrim` object. Returns `round(max(0.20, min(0.7, top_frac - 0.10)), 4)`. Fallback: `0.44`.
- **Focus Point Calculation** (`design_images.py:1639-1720`): `detect_focus_point(image_bytes, for_split, safe_cy_ceiling)`.
  - Face confidence floor: requires `fw >= 0.14 * w` to eliminate Haar false positives.
  - Split layout (`for_split=True`): true face center `cy = min(0.62, max(0.3, (fy + fh/2)/h + 0.04))`.
  - Single card (`for_split=False`): upward lift bias `cy = min(ceiling, max(0.24, (fy + fh/2)/h - 0.08))` to clear bottom text scrim.
  - Saliency fallback: `cv2.saliency.StaticSaliencySpectralResidual_create()`. Horizontal center of mass trusted (`0.2 <= cx <= 0.8`). Vertical blended 55% saliency + 45% default bias.
- **Split Focus Alignment** (`design_images.py:1830-1856`): `align_split_focus_points(focus_points)` levels vertical coordinate to `safe_fy = min(p0[1], p1[1])` (safer/higher in frame, not average, preventing chin cropping on tighter crops).

### Expand & Composition Algorithms
- **`smart_expand(image_bytes, target_w, target_h)`** (`design_images.py:1478-1569`):
  1. *Extreme close-up* (`face[3] >= 0.32` or `face[1] + face[3] >= 0.78`): calls `fit_crop_top_solid(face_bbox=face, face_zoom=50, pad=0.78, top_bias=0.04, face_anchor_y=True, blur_bg=True)`.
  2. *Portrait/near-square* (`img_ar < slot_ar * 1.15`): with face calls `fit_crop_top_solid(face_anchor_y=True, blur_bg=True)`; without face returns `None` (plain cover-fit).
  3. *Wide/landscape with face*: calls `fit_with_blur_bg(face_bbox=face)`.
  4. *Wide/landscape no face*: calls `reflect_extend()`.
- **`fit_crop_top_solid(...)`** (`design_images.py:386-563`):
  - Solves side blur margin problem by decoupling width and height.
  - Desired crop: `crop_h = min(ih, max(1, (ffh * ih) * face_zoom))`; `crop_w = min(iw, crop_h * (target_w / target_h) / max(pad, 0.01))`.
  - Cheek/ear safety floor: `crop_w = max(crop_w, min(iw, ffw * iw * 1.15))`.
  - `face_anchor_y=True`: when `fh > target_h`, centers visible window at 40% down from face center `face_cy_fg`: `t = int(max(0, min(fh - target_h, face_cy_fg - target_h * 0.40)))`.
  - Bottom treatment: `blur_bg=True` blends sharp foreground into blurred (`blur=40`), darkened (`darken=0.78`) background with `feather=220` px alpha ramp. If False, paints solid `fill_color=(8, 8, 10)`.
- **`fit_with_blur_bg(...)`** (`design_images.py:251-326`):
  - Full-photo blurred/darkened cover-crop background (`blur=40, darken=0.78`).
  - Foreground zoomed to face (`face_zoom=5.5`) or whole photo, padded (`pad=0.98`), top-biased (`top_bias=0.24`), feathered at bottom (`feather=220`).
- **`reflect_extend(...)`** (`design_images.py:328-384`):
  - Scales width by `zoom=1.08`. Center crops sides if overflow.
  - Empty vertical areas padded with `np.pad(arr, mode="reflect")`.
  - Extension blurred (`blur=26`), edge-darkened (`edge_dark=0.55`), feathered (`feather=70`).
- **`fix_unsafe_single_photo_face(...)`** (`design_images.py:1230-1325`):
  - Triggers only when `img_ar > slot_ar` (zero vertical cover-fit slack) AND face natural center `natural_cy = face[1] + face[3]/2 - 0.08 > safe_cy_ceiling`.
  - Re-composites photo using `content_aware_split_extend(zoom=1.0, face_bbox=face, target_cy=safe_cy_ceiling)`.
- **`single_photo_face_fits(...)`** (`design_images.py:1327-1418`):
  - Rejection gate. Evaluates `_is_low_quality_photo`.
  - Scaled face height: `scaled_fh = fh * slot_ar / img_ar`.
  - Checks if `best_case_bottom = safe_cy_ceiling + scaled_fh / 2 > min(0.95, safe_cy_ceiling + 0.10) + 0.03`. If True, rejects candidate.
- **`photo_crops_well(...)`** (`design_images.py:1423-1476`):
  - Mode 5 Pinterest gate.
  - If `img_ar <= slot_ar`: checks if `(ih * canvas_width / iw - canvas_height) / canvas_height >= 0.08` (`_PINTEREST_MIN_VERTICAL_SLACK`).
  - If landscape: checks `_dominant_face_bbox is not None`. If false, returns `False` to trigger direct raw post without template.

---

## 5. Split & Inset Two-Subject Flows

### Dual-Subject Extraction Consensus
- **`extract_two_subjects(title, niche)`** (`design_images.py:3055-3095`):
  - Executes `_extract_two_subjects_once(title, niche)` **twice** independently via 9Router.
  - Requires exact consensus: `primary1.lower() == primary2.lower()` AND `secondary1.lower() == secondary2.lower()`.
  - Guard: `primary.lower() == secondary.lower()` rejected (returns `primary, None`).
  - Any disagreement falls back to single-subject `primary1, None`. Prevents false-positive splits on passing mentions.

### Circular Inset Pipeline (`image_2` circle/ellipse)
1. `prepare_design_images` calls `extract_secondary_subject(title, niche)`.
2. Sourcing: `fetch_subject_datauri(..., "face", niche)` -> `find_gallery_datauri(..., image_type="face")`.
3. Face location: `analyze_subject_side(main_datauri)` (`design_images.py:3569`) returns `"left"|"right"|"center"`.
4. Repositioning: `position_secondary_slot(template_json, side, canvas_width)` (`design_images.py:3609`):
   - Inset placed opposite main subject: `cx = margin + r` if subject right/center; `cx = W - margin - r` if subject left (`margin=44, r=180`).
   - Also repositions decorative ring object immediately preceding `image_2`.
5. Optional avatar: `_with_inset(template_json, srcs)` (`design_images.py:3646`) populates `image_3` by duplicating the last resolved photo.

### Rectangular Split Pipeline (`image_2` rect)
Implemented in `build_split_srcs` (`design_images.py:3452-3567`):

```
Candidate Gathering:
   Left:  fetch_subject_datauris(top_n=8) + find_gallery_datauris(top_n=8, pool_limit=30)
   Right: fetch_subject_datauris(top_n=8) + find_gallery_datauris(top_n=8, pool_limit=30)
         │
         ▼
Framing Classification (_classify_split_frames, line 2057):
   Labels: HEAD (0), HALF (1), FULL (2), VEHICLE (10), NONE (10)
   Distance metric: d = abs(rank(L) - rank(R))
         │
         ▼
Aesthetic Pairing & Gating (_pick_best_aesthetic_pair, line 2206):
   Iterates tiers starting at d=0.
   Hard rejects:
     - VEHICLE paired with person (rank delta = 8-10)
     - NONE (crowd/stadium) paired with person
     - Inadequate face margin, extreme lighting mismatch, background clutter
   Returns (li, ri, accepted)
         │
         ▼
Face Width Matching & Safe Zoom:
   Measures face width fractions: _face_width_fraction / _vision_face_width_fraction
   If ratio >= 1.08 (_SPLIT_ZOOM_RATIO_THRESHOLD):
     zoom = min(ratio, _max_safe_zoom(img, frac))
     _max_safe_zoom caps zoom to _MAX_SPLIT_ZOOM_ABSOLUTE = 1.3
         │
         ▼
Content-Aware Composition Pass (content_aware_split_pair, line 1119):
   Target face fraction: _SPLIT_TARGET_FACE_FRAC = 0.62 of slot width
   Fill cap: _SPLIT_MAX_SYNTHETIC_FILL = 0.65
   Calls content_aware_split_extend(blur_bg=True)
         │
         ▼
Seam Harmonization:
   1. _match_split_exposure: balances RGB channel means, gain in (0.85, 1.18)
   2. _seam_vignette: darkens inner seam edge over 8% width, strength 0.35
         │
         ▼
Template Annotation & Fallback:
   Stores zooms: template_json["_splitImageZooms"] = [zoom_l, zoom_r]
   If no split accepted or secondary missing:
     _widen_to_full sets image["width"] = canvas_width, image_2["visible"] = False
```

---

## 6. Vision Checks & Prompt Disciplines

| Function | Lines | Context | Task Prompt Summary | Failure Mode | Behavior on Error |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `_vision_chat` | 108-175 | All vision | Model fallback dispatch; 30s timeout; 0 max retries | Model error / 413 / timeout | Iterates fallback list; raises last exception |
| `classify_and_gate_image` | 1881-1976 | Downloader | Label (FACE/ACTION/OTHER) + usability gate; flags crowds, text overlays, and blurry person in foreground | Parse/API error | **Fail-Open**: `("other", True)` |
| `classify_closeup_match` | 1978-2009 | Admin Filter | Matches photo against criteria string | Parse/API error | **Fail-Open**: `{"match": True, "confidence": 0.0}` |
| `vision_pick_best` | 2011-2052 | Selection | Ranks 1..N candidate data-URIs for clarity and subject portrayal | Parse/API error | **Fail-Open**: index `0` |
| `_classify_split_frames` | 2057-2138 | Split sizing | Categorizes into `HEAD`, `HALF`, `FULL`, `VEHICLE`, `NONE` | Parse/API error | Defaults missing labels to `HALF` |
| `_pick_best_aesthetic_pair` | 2206-2280 | Split pair | Ranks aesthetic pair; verifies lighting/framing; hard-rejects vehicle/NONE mismatches | Parse/API error | Accepts pick index 0,0 |
| `vision_pick_split_pair` | 2367-2498 | Split pair | Top-level coordinator of classification, aesthetic review, and zoom matching | Unhandled exception | **Fail-Open**: `(0, 0, 1.0, 1.0, True)` |
| `vision_verify_match` | 2500-2550 | Topic search | Verifies photo depicts headline, excerpt, and niche | Parse/API error | **Fail-Closed**: `{"match": False, "confidence": 0.0}` |
| `vision_verify_subject` | 2574-2618 | Identity gate | Verifies photo is specific named subject in niche; checks name collisions | Parse/API error | **Fail-Closed**: `{"match": False, "confidence": 0.0}` |
| `_is_confident_subject_match` | 2570-2572 | Subject gate | `result["match"] == True` AND `confidence >= 0.70` (`_SUBJECT_MATCH_MIN_CONFIDENCE`) | Low confidence | **Fail-Closed**: rejects match |
| `vision_check_pin_description` | 2620-2673 | Mode 5 Pin | Validates scraped pin description matches image; outputs cleaned title/hook | Parse/API error | **Fail-Closed**: `{"valid": False, "title": "", "description": ""}` |
| `vision_identify_pin_subject` | 2675-2721 | Mode 5 Pin | Identifies subject from scratch on uncaptioned pins; writes title and 125-char hook | Parse/API error | **Fail-Closed**: `{"identified": False, "title": "", "description": ""}` |
| `vision_check_photo_quality` | 2723-2790 | Mode 5 Pin | Evaluates skin texture, hair, fabric weave; requires detail_score >= 6 | Parse/API error | **Fail-Closed**: returns `False` |
| `vision_has_watermark` | 2791-2825 | Mode 5 Pin | Flags agency watermarks, logos, URLs, copyright credits (LAT, XPB, RaceFans) | Parse/API error | **Fail-Closed**: returns `True` |
| `analyze_subject_side` | 3569-3607 | Inset slot | Locates main subject head: `LEFT`, `RIGHT`, `CENTER` | Parse/API error | **Fail-Open**: returns `"center"` |

---

## 7. `image_type` Handling

- **Categories**: `"face"` (clear portrait), `"action"` (on-track/vehicle performance), `"other"` (context/scene).
- **Classification** (`design_images.py:1858, 1881`): 9Router labels downloaded images; persisted to `GalleryImage.label`.
- **Editorial Decision** (`design_images.py:3034`): `pick_split_image_type(title, niche)` parses headline:
  - Quotes, contracts, statements, rivalries -> `"face"`.
  - Qualifying, race results, speed, favorites -> `"action"`.
- **Pipeline Impacts**:
  1. *Gallery Sourcing*: `_gallery_verified_candidates` filters `GalleryImage.label == image_type` before falling back to all images.
  2. *Getty Query Formulation*: `_fetch_verified_subject_candidates` formats query as `{subject} {niche} portrait press` for `"face"`, versus `{subject} {niche}` for `"action"`.
  3. *Vision Prompt Tuning*: `vision_pick_best` requests portrait clarity for `"face"` vs dynamic riding/driving for `"action"`.
  4. *Split Consistency*: Forces left and right halves to share identical style. Prevents mixing bike action shots with studio headshots.
  5. *Inset Fallback Guard*: When split fails and reverts to circular inset (`design_images.py:3824`), `image_type` is passed to ensure secondary inset matches main photo style.

---

## 8. Fail-Closed vs Fail-Open Architectural Decisions

```
                           Vision / Crop Evaluation
                                      │
          ┌───────────────────────────┴───────────────────────────┐
          ▼                                                       ▼
   [FAIL-CLOSED GATES]                                     [FAIL-OPEN GATES]
  (Accuracy Over Availability)                           (Operational Continuity)
  • vision_verify_subject (conf >= 0.70)                 • classify_and_gate_image (download)
  • vision_verify_match (topic/hero)                     • classify_closeup_match (admin)
  • single_photo_face_fits (crop floor)                  • vision_pick_best (defaults idx 0)
  • vision_check_photo_quality (Pinterest)               • analyze_subject_side (defaults center)
  • vision_has_watermark (direct pins)                   • _match_split_exposure (cosmetic)
  • vision_check_pin_description                         • _seam_vignette (cosmetic)
  • vision_identify_pin_subject                          • smart_expand (returns None -> raw cover)
  • vision_pick_split_pair (aesthetic reject)            • vision_pick_split_pair exception
```

- **Fail-Closed Rationale**: Publishing a wrong identity (e.g. Fede Valverde for Ilia Topuria), watermarked agency image, or truncated face under title scrim destroys publication credibility. Skipping candidate or falling back to single-image is strictly preferred.
- **Fail-Open Rationale**: Ingestion downloads, admin gallery review tools, minor cosmetic lighting tweaks, and initial best-pick selections must not block queue processing on transient network or API hiccups.

---

## 9. Public Functions and Callers Catalog

Comprehensive reference of every public function in `design_images.py` with cross-repository call sites:

| Public Function | Signature & Return | Side Effects & Writes | Callers Across `backend/app/` |
| :--- | :--- | :--- | :--- |
| `template_has_role` | `(template_json, role: str) -> bool` | Pure function | `design_images.py:25` |
| `find_role_object` | `(template_json, role: str) -> dict \| None` | Pure function | `design_images.py:25, 1632, 3656, 3672, 3676, 3711, 3727, 3737`; `tasks/design_renderer.py:54, 55, 447, 681, 1153` |
| `file_to_datauri` | `(path: str) -> str` | Reads filesystem | `design_images.py:2968, 3278` |
| `niche_keywords` | `(db, niches: list[str]) -> list[str]` | Queries `GalleryKeyword` | `tasks/design_renderer.py:238`; `api/publish_jobs.py:210` |
| `resolve_template` | `(db, category: str, fanpage=None, job_template_id=None) -> DesignTemplate \| None` | Queries `DesignTemplate` | `tasks/facebook_photo.py:178`; `tasks/pinterest.py:166`; `tasks/design_renderer.py:344, 613, 619, 828, 1114, 1118`; `tasks/discussion.py:559`; `tasks/ig_recreate.py:243`; `tasks/news_copywriter.py:132`; `api/publish_jobs.py:196`; `services/redesign_pipeline.py:331`; `services/news_copywriter.py:119` |
| `watermark_datauri` | `(fanpage) -> str \| None` | Reads logo file from storage | `tasks/yt_clip_render.py:81`; `tasks/design_renderer.py:504, 715, 916, 1200`; `tasks/ig_recreate.py:622`; `api/ai_video_jobs.py:57`; `services/ai_video_overlay.py:124`; `services/redesign_pipeline.py:383` |
| `fit_with_blur_bg` | `(image_bytes, target_w, target_h, ...) -> bytes \| None` | Pure OpenCV/PIL transform | `design_images.py:1567` |
| `reflect_extend` | `(image_bytes, target_w, target_h, ...) -> bytes \| None` | Pure NumPy/PIL transform | `design_images.py:1568` |
| `fit_crop_top_solid` | `(image_bytes, target_w, target_h, face_bbox, ...) -> bytes \| None` | Pure NumPy/PIL transform | `design_images.py:1540, 1561` |
| `content_aware_split_extend` | `(image_bytes, target_w, target_h, ...) -> bytes \| None` | Optional `cv2.xphoto.inpaint` | `design_images.py:1207, 1208, 1315` |
| `content_aware_split_pair` | `(left_bytes, right_bytes, slot_w, slot_h) -> tuple[str, str] \| None` | Face detection + inpaint/blur extend | `design_images.py:3560` |
| `fix_unsafe_single_photo_face` | `(image_bytes, canvas_width, canvas_height, safe_cy_ceiling) -> str \| None` | Face detection + content-aware extend | `tasks/design_renderer.py:470, 693, 879, 1164` |
| `single_photo_face_fits` | `(image_bytes, canvas_width, canvas_height, safe_cy_ceiling) -> bool` | Sharpness check + face geometry evaluation | `tasks/design_renderer.py:385, 1055` |
| `photo_crops_well` | `(image_bytes, canvas_width, canvas_height) -> bool` | Aspect ratio slack + face detection | `tasks/design_renderer.py:844` |
| `smart_expand` | `(image_bytes, target_w, target_h) -> bytes \| None` | Dispatches expand strategies | `design_images.py:1578` |
| `extract_secondary_subject` | `(title: str, niche: str) -> str \| None` | 9Router AI call via `ai_caption` | `design_images.py:3749, 3792`; `tasks/design_renderer.py:100` |
| `detect_focus_point` | `(image_bytes, for_split=False, safe_cy_ceiling=None) -> list[float]` | Local OpenCV Haar + Saliency | `design_images.py:1824` |
| `vision_focus_point` | `(image_bytes, for_split=False, safe_cy_ceiling=None) -> list[float] \| None` | 9Router vision call | `design_images.py:1823` |
| `focus_points_for` | `(image_srcs: list, for_split=False, safe_cy_ceiling=None) -> list` | Dispatches vision/OpenCV focus points | `tasks/design_renderer.py:477, 700, 886, 1171`; `tasks/ig_recreate.py:624`; `services/redesign_pipeline.py:385` |
| `align_split_focus_points` | `(focus_points: list) -> list` | Levels vertical coords to `min(y0, y1)` | `tasks/design_renderer.py:479, 702, 1173` |
| `classify_image_type` | `(image_bytes: bytes) -> str` | 9Router vision call (`face`/`action`/`other`) | Standalone tools / debug scripts |
| `classify_and_gate_image` | `(image_bytes, subject=None, allow_low_quality=False) -> tuple[str, bool]` | 9Router vision call (label + usable) | `services/image_downloader.py:457, 465` |
| `classify_closeup_match` | `(image_bytes, criteria: str) -> dict` | 9Router vision call (`match`, `confidence`) | `tasks/gallery_downloader.py:722` |
| `vision_pick_best` | `(candidates: list, subject: str, image_type=None) -> int` | 9Router vision ranking | `design_images.py:3010, 3308, 3394` |
| `vision_pick_split_pair` | `(left_candidates, right_candidates, primary, secondary, image_type=None) -> tuple` | Classification + aesthetic gating + safe zoom | `design_images.py:3533` |
| `vision_verify_match` | `(image_bytes, title, excerpt="", niche="") -> dict` | 9Router vision call (`match`, `confidence`) | `design_images.py:3387` |
| `vision_verify_subject` | `(image_bytes, subject: str, niche="") -> dict` | 9Router vision identity call | `design_images.py:2838, 3291` |
| `vision_check_pin_description` | `(image_bytes, description, niche="", custom_prompt="") -> dict` | 9Router vision caption validator | `services/pinterest_source.py:212` |
| `vision_identify_pin_subject` | `(image_bytes, niche="", custom_prompt="") -> dict` | 9Router vision uncaptioned identifier | `services/pinterest_source.py:215` |
| `vision_check_photo_quality` | `(image_bytes: bytes) -> bool` | 9Router vision texture/detail scoring | `services/pinterest_source.py:205` |
| `vision_has_watermark` | `(image_bytes: bytes) -> bool` | 9Router vision watermark detector | `services/gallery_link_import.py:227`; `services/pinterest_source.py:202`; `services/story_photo.py:217` |
| `find_gallery_datauri` | `(db, subject, exclude_paths=None, use_vision=True, image_type=None, allow_stale_reuse=True, niche="") -> tuple` | Queries `GalleryImage`, marks `last_used_at` | `design_images.py:3427, 3441, 3755, 3824`; `tasks/design_renderer.py:636, 652` |
| `find_gallery_datauris` | `(db, subject, exclude_paths=None, image_type=None, allow_stale_reuse=True, niche="", top_n=3, pool_limit=8) -> list` | Queries `GalleryImage` candidate tuples | `design_images.py:3519` |
| `pick_split_image_type` | `(title: str, niche: str) -> str` | 9Router AI call via `ai_caption` | `design_images.py:3425, 3767` |
| `extract_two_subjects` | `(title: str, niche: str) -> tuple[str \| None, str \| None]` | Dual 9Router calls with consensus verification | `design_images.py:3417, 3765`; `tasks/design_renderer.py:95`; `tasks/ig_recreate.py:591` |
| `fetch_subject_datauri` | `(db, subject, image_type="face", niche="MotoGP", max_candidates=5, exclude_paths=None) -> tuple` | HTTP Getty fetch + writes to storage & DB | `design_images.py:3436, 3753`; `tasks/design_renderer.py:643`; `services/visual_sourcing.py:357, 559` |
| `fetch_subject_datauris` | `(db, subject, image_type="face", niche="MotoGP", max_candidates=5, top_n=3, exclude_paths=None) -> list` | HTTP Getty fetch + writes candidates | `design_images.py:3517` |
| `fetch_topic_datauri` | `(title, niche, excerpt="", max_candidates=6) -> str \| None` | HTTP Getty/Google fetch + FSRCNN upscale | `tasks/design_renderer.py:276, 1035` |
| `source_news_main` | `(db, title, niche, use_vision=True, exclude_paths=None, known_primary=None) -> tuple` | Sourcing cascade (gallery -> Getty -> stale) | `tasks/design_renderer.py:231, 1025`; `tasks/ig_recreate.py:571` |
| `build_split_srcs` | `(db, primary, secondary, image_type="face", niche="MotoGP", slot_w=None, slot_h=None) -> tuple \| None` | Joint candidate pairing + content-aware extend | `design_images.py:3770` |
| `analyze_subject_side` | `(main_datauri: str) -> str` | 9Router vision call (`left`/`right`/`center`) | `design_images.py:3759` |
| `position_secondary_slot` | `(template_json, side: str, canvas_width=1080) -> dict` | Mutates Fabric.js circle & ring slot coords | `design_images.py:3760` |
| `prepare_design_images` | `(db, template_json, canvas_width, title, niche, main_datauri, main_path=None, smart=True, expand=False) -> tuple` | Orchestrates all template slot images & geometry | `tasks/design_renderer.py:425, 673, 865, 1145`; `tasks/ig_recreate.py:598`; `services/redesign_pipeline.py:361` |

---

## Verified claims

backend/app/services/design_images.py:138 - OpenAI client is initialized with timeout=30.0 and max_retries=0 to eliminate SDK-level retry latency before trying model fallbacks.
backend/app/services/design_images.py:583 - YuNet model is fetched from media.githubusercontent.com because raw.githubusercontent.com only serves a 131-byte Git LFS pointer text file.
backend/app/services/design_images.py:640 - Input image is resized to max dimension 640px prior to YuNet inference because full-resolution input drops detection confidence below 0.60.
backend/app/services/design_images.py:695 - Photo sharpness floor _LOW_QUALITY_BLUR_VARIANCE is 150.0 Laplacian variance evaluated on a 500px-width normalized image.
backend/app/services/design_images.py:751 - _SPLIT_TARGET_FACE_FRAC enforces that both split halves' faces equal exactly 0.62 of the slot width.
backend/app/services/design_images.py:771 - _SPLIT_MAX_SYNTHETIC_FILL caps allowed synthetic background fill at 0.65 when compositing split halves with blurred cover backgrounds.
backend/app/services/design_images.py:802 - _match_split_exposure balances per-channel RGB means between split halves within a clamped gain range of 0.85 to 1.18.
backend/app/services/design_images.py:1636 - _safe_face_cy_ceiling caps face center vertical placement at max(0.20, min(0.7, top_frac - 0.10)) derived from template title or scrim position.
backend/app/services/design_images.py:2119 - _FRAME_RANK isolates both VEHICLE and NONE at rank 10 so they never distance-match against person framing tiers HEAD, HALF, or FULL.
backend/app/services/design_images.py:2567 - _SUBJECT_MATCH_MIN_CONFIDENCE requires self-reported vision confidence of at least 0.70 to accept a subject identity match.
backend/app/services/design_images.py:2880 - Gallery reuse cooldown cutoff is randomly sampled between 14 and 28 days to prevent predictable photo reoccurrence.
backend/app/services/design_images.py:3084 - extract_two_subjects executes two independent model calls and drops to a single subject if there is any naming disagreement.

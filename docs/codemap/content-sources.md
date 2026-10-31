# Deep Dive D4: Content Sources + AI Copy (Modes 1, 2, 4, 5, 6, 7 + Gallery)

> Generated 2026-10-16 at main 25edf38 from a full read of the listed files. The "Verified claims" list at the bottom was checked line by line against the code. Other details are a careful map but not each re-checked. Read the real code before changing behaviour, and update this file when the code changes.


Comprehensive architecture and implementation map covering content discovery, deduplication, pacing/quota math, copy generation prompts, model fallbacks, breaking-news fast track, event-aware gallery throttling, and content skip gates across backend tasks and services.

---

## 1. Cross-Cutting AI Infrastructure & Model Fallbacks

### 1.1 9Router Resolution & Model Health
- **Config Resolution** (`backend/app/services/nine_router.py:43`): `_resolve()` fetches DB `Settings` (id=1) columns `nine_router_base_url`, `nine_router_model`, `nine_router_api_key_encrypted` (decrypted via `app.services.encryption.decrypt`), and `nine_router_discussion_model` (default `"smart-combo"`). DB settings override env vars with 30-second memory cache TTL (`nine_router.py:20, 78`).
- **Health Tracking** (`backend/app/services/model_health.py:32`): Status stored in Redis hash `model_health` with 26-hour TTL (`model_health.py:33`). Probes models sequentially with 0.5s pause (`model_health.py:35`). Hard rules:
  1. Never probe or store `cx/*` IDs (`model_health.py:128, 174`).
  2. Fetches live `/v1/models` (`model_health.py:69`); configured IDs not on live list immediately marked `dead("not listed")` without HTTP probe (`model_health.py:179`).
  3. `is_dead(model)` (`model_health.py:190`) is fail-open: returns `False` on any Redis error so fallback chain is not silently starved.

### 1.2 Multi-Tier AI Provider & Fallback Chains
- **Caption Generation Fallback** (`backend/app/services/ai_caption.py:330`):
  1. **Primary**: 9Router (`_call_router`, `ai_caption.py:223`) with timeout 75.0s, temperature 0.7, max_tokens 8192, `max_retries=0` (`ai_caption.py:253`). Order tried: `preferred_router_model` (if passed) $\to$ configured default model $\to$ `ROUTER_MODEL_FALLBACKS` (14 models from Claude Opus/Sonnet down to Gemini 3.1 Pro; `ai_caption.py:201-216`), filtering out dead models via `is_dead()` (`ai_caption.py:373-379`).
  2. **Failover to Gemini**: (`_call_gemini`, `ai_caption.py:281`) using `settings.gemini_model`. Consecutive errors increment Redis key `ai:gemini_consecutive_failures` (`ai_caption.py:70`).
  3. **Failover to Groq**: If consecutive Gemini failures $\ge$ `settings.ai_fallback_after_failures`, sets Redis key `ai:switched_to_groq_until` with TTL `ai_fallback_reset_after_minutes * 60` (`ai_caption.py:84`). Groq uses `settings.groq_model` (gpt-oss-120b) with `max_tokens=5000` (`ai_caption.py:318`) to allow hidden reasoning budget while staying below 8000 TPM limit.
- **JSON Recovery Engine** (`backend/app/services/news_copywriter.py:549` `_generate_with_fallback`):
  - Reasoning models (e.g., My-Combo) can return HTTP 200 with truncated JSON because hidden chain-of-thought exhausts tokens. This is a parse failure, not transport failure.
  - Recovery sequence:
    1. If provider was Groq and output fails to parse: retries once via Gemini (`news_copywriter.py:623`).
    2. If provider was 9Router and output fails to parse: iterates through live `ROUTER_MODEL_FALLBACKS` (`news_copywriter.py:657`).
    3. If all 9Router fallbacks fail to produce parseable JSON: retries once via Gemini (`news_copywriter.py:676`).
  - Logs every attempt to `AICopyEvent` table via `log_ai_copy_event` (`ai_caption.py:23`) with latency, models tried, and outcome (`success`, `recovered`, `failed`).

---

## 2. Mode 1: Instagram Repost

### 2.1 Discovery & Scraping Pipeline
| Component | Function / Line | Behavior / Gates |
|---|---|---|
| Wave Controller | `backend/app/tasks/crawler.py:55` `crawl_all_sources` | Skips if WIB hour in `[crawl_sleep_start_wib, crawl_sleep_end_wib)` (default 01:00–06:00 WIB; `crawler.py:48-52`). Acquires Redis string lock `ig_crawl:wave` (TTL 1800s, `crawler.py:33, 105`). Enforces `crawl_interval_minutes` (`crawler.py:79-98`). Filters active sources with `TargetFanpage.mode1_ig_repost_enabled == True` (`crawler.py:110-123`). |
| Wave Step Dispatch | `crawler.py:171` `crawl_wave_step` | Processes sources sequentially with 30–90s random delay (`crawler.py:214`). 90-minute wave deadline (`crawler.py:36, 193`). If viewer busy, retries current step after 60s (`crawler.py:199`). |
| Login-free Scraper | `backend/app/services/ig_viewer_scraper.py:980` `fetch_recent_posts` | Distributed Redis mutex `ig_viewer:browser_lock` (`ig_viewer_scraper.py:35`) with 300s TTL. Launches Patchright/Playwright with extensive anti-detection flags (`ig_viewer_scraper.py:86-113`). Iterates `_TIERS`: GramSnap $\to$ AnonyIG $\to$ IGStoryViewer (`ig_viewer_scraper.py:875-879`). Inter-tier jitter 2.0–5.0s (`ig_viewer_scraper.py:904`). |
| Ingestion & Dedup | `crawler.py:279` `_ingest_mode1_medias` | Fetches 9–15 posts. Dedup: checks `Post.ig_media_id` (`crawler.py:291`). Skips posts older than `max_post_age_days` (`crawler.py:285-301`). Skips videos and reels (`crawler.py:316`). Album handling: `media_type == 8`, counts image resources (`crawler.py:305`); skips video-only albums (`crawler.py:310`). Subsets image URLs by `source.album_image_indices or [1]` (`crawler.py:343`). Creates `Post(status=crawled)`. |
| Cross-Ingest Radar | `crawler.py:237` `_try_radar_ingest` | If account tracked in Radar or linked via `trigger == 'viral_only'`, cross-ingests medias into Radar pipeline (`crawler.py:258-273`). |
| Source Niche & Routing | `ig_sources.py`, `ig_recreate.py` | `ig_sources.niche` assigns niche category; `GET /ig-sources` computes `niche_mismatch` against linked fanpage gallery niches (Motorsport matches F1/MotoGP). Mode 3 IG recreate uses source niche for gallery photo lookup and AI context. |

### 2.2 Image Storage & Fan-Out
- **Image Saver** (`backend/app/tasks/image_saver.py:87` `save_post_images`):
  - SSRF Protection: `download_ig_image` (`image_saver.py:22`) validates URL against Instagram CDN host regex (`is_ig_cdn_url`, `image_saver.py:19`).
  - Limits: max 3 redirects, 2 MB size cap (`image_saver.py:51, 70`).
  - Stores images under `{storage_base_path}/posts/{post_uuid}/{idx}.jpg`.
  - Sets `Post.status = stored`, dispatches `create_fanout_jobs.delay(post_id)` (`image_saver.py:127, 134`).
- **Fan-Out** (`backend/app/tasks/fan_out.py:94` `create_fanout_jobs`):
  - Skips links where `trigger == 'viral_only'` (`fan_out.py:125`).
  - Mode 1 Gate: skips fanpage if `not link.fanpage.mode1_ig_repost_enabled` (`fan_out.py:29`).
  - Idempotency: checks if `PublishJob` already exists for `(post_id, fanpage_id)` (`fan_out.py:37`).
  - Stagger: random 60 to 1500s delay (`_fanpage_stagger`, `fan_out.py:13, 18`) between fanpages sharing the same IG source.
  - Recreate Gate: if `link.ig_recreate_enabled` (or fanpage blanket setting), delegates to Mode 3 (`recreate_post_for_fanpage.apply_async`, `fan_out.py:56`).
  - Otherwise: creates `PublishJob(status=pending_caption)`, dispatches `generate_caption_for_job` with stagger countdown (`fan_out.py:59-81`).

### 2.3 Copywriting & Pacing
- **Prompt Enforcements** (`backend/app/services/ai_caption.py:107` `build_caption_prompt`):
  - Language (`cs.language`), tone (`cs.tone`), max length (`cs.max_length`).
  - Mobile hook rule: *"The FIRST sentence is the hook a reader sees before 'See more' on Facebook mobile: at most 125 characters, concrete and specific (who + what), no generic opener."* (`ai_caption.py:161`).
  - Keywords to include/avoid, hashtag count, CTA text (`ai_caption.py:162-165`).
  - Attribution: `- Attribution: add line "{attribution_template}" at the {attribution_position}` (`ai_caption.py:129`).
  - Verbatim Quote: if `quote_text` provided, prompt enforces exact reproduction inside quotation marks without paraphrasing (`ai_caption.py:143-150`).
- **Publish Pacing** (`backend/app/tasks/ai_generator.py:90` `_gap_since_last_publish`):
  - Auto-publish adds random 120–180s countdown if fanpage has published before (`ai_generator.py:113`).

---

## 3. Mode 2: News / Hot News

### 3.1 Scraping & Ingestion
- **Scheduler** (`backend/app/tasks/news_scraper.py:27` `scrape_all_sources`): Ticks every minute via beat; filters active `NewsSource` rows where `now - last_scraped_at >= scrape_interval_minutes` (`news_scraper.py:38-40`).
- **Fetch Chains & Protocols** (`backend/app/services/news_scraper.py:66` `_fetch_with_fallbacks`):
  1. Direct fetch via Scrapling `Fetcher` (browser TLS impersonation).
  2. Proxy pool fetch via `pick_proxy()` (`news_scraper.py:78`).
  3. Server-side relay pool via `pick_relay()` and `fetch_via_relay()` (`news_scraper.py:88`).
  4. Playwright headless browser fallback for `render_mode == "js"` sources if tiers 1–3 fail (`news_scraper.py:236-238`).
  5. RSS Mode (`render_mode == "rss"`, `news_scraper.py:242`): uses direct HTTP `httpx.get` (bypasses Scrapling to avoid XML tag munging).
- **Robots.txt** (`news_scraper.py:203` `_robots_allowed`): Cached in `_robots_cache` per netloc; respects `RobotFileParser.can_fetch`. Fails open if robots.txt unreachable.
- **Deduplication & Throttling**:
  - Max 10 new articles extracted per run (`_MAX_NEW_ARTICLES_PER_RUN = 10`, `news_scraper.py:22`).
  - Polite crawl delay between article fetches: random 5–15s (`news_scraper.py:19-20, 146`).
  - Dedup: checks `ScrapedArticle.article_url.in_(links)` (`news_scraper.py:137, 180`).
- **Skip Gates & Permanent Failures**:
  - Video Page Detection: `_looks_like_video_page` (`news_scraper.py:134`) detects `.m3u8`, `application/vnd.apple.mpegurl`. Returns `ExtractedArticle(skip_reason="video content...")` (`news_scraper.py:473`).
  - Max Age Gate: Skips articles where `now - published_at > max_age_days` (`news_scraper.py:253`). Missing publish date fails open (`news_scraper.py:250`).
  - Failure Stubs: If title/content extraction fails and page is NOT a bot challenge (`_looks_like_bot_challenge`, `news_scraper.py:154`), sets `permanent_failure = True` and writes `ScrapedArticle(status=skipped)` to halt infinite re-scraping (`news_scraper.py:189-215, 241-248`).

### 3.2 Editorial Gate & Copywriting
- **Editorial Gate** (`backend/app/services/editorial_gate.py:123` `evaluate_article`):
  - Opt-in via `TargetFanpage.mode2_editorial_gate_enabled` (`tasks/news_copywriter.py:93`). Fails open on exception (`tasks/news_copywriter.py:107-114`).
  - **One editorial call** (`_judge_post_worthiness`): no fact-check and no web search since 2026-10-31 (owner: the model's knowledge lags the current season and it rejected real news as "fabricated"). The prompt says never to judge whether facts are true; it rejects only off-niche, purely promotional, or empty/garbled articles, and predicts engagement `low`/`medium`/`high`. `EditorialVerdict.fact_check` is always `"off"`.
- **News Copywriting Prompt** (`backend/app/services/news_copywriter.py:137` `build_news_copy_prompt`):
  - Classifies into `"news"` vs `"quote"`. Defaults to `"news"` for ~90% of stories; `"quote"` is chosen only when a spoken statement is the headline itself (`news_copywriter.py:203-205`).
  - Red Highlight Syntax: wraps exactly ONE key phrase (2–5 words) in `**double asterisks**` (`news_copywriter.py:166, 175`).
  - Text Fit & Retry: Resolves `titleFitChars` from template (`news_copywriter.py:112`). If generated title exceeds budget, re-prompts model once with explicit correction line (`news_copywriter.py:780-796`).
  - Quote Card Rules: Subtitle restricted strictly to speaker's name; trailing roles/titles stripped via regex (`_clean_quote_subtitle`, `news_copywriter.py:48-54`). `_sync_caption_quote` deterministically forces caption's `🗣️ "..."` line to match title quote verbatim (`news_copywriter.py:60-77`).
- **Breaking News Fast Track**:
  - Detection: Prompt sets `is_breaking: true` for high-impact events (major signings, race results, severe injuries) (`news_copywriter.py:207`).
  - Render Stagger: Normal stagger is 60–1500s; breaking news stagger is 5–90s (`tasks/news_copywriter.py:30-31, 38`).
  - Publisher Bypass (`backend/app/tasks/publisher.py:134, 162-179`): `breaking=True` skips daily cap check and calendar day-hopping. Respects sleep window (`_push_past_sleep`), but schedules immediately with a tight 5–10 min minimum gap from the fanpage's last post (`publisher.py:173`).

---

## 4. Mode 4: Discussion & Hot Take Cards

### 4.1 Queue Architecture & Pacing Math
- **Staging Model**: Topics become `DiscussionContentIdea(status="pending")` (`backend/app/tasks/discussion.py:493`).
- **Active Hours**: WIB 08:00 to 22:00 (`_WINDOW_START_HOUR = 8`, `_WINDOW_END_HOUR = 22`, `discussion.py:58-59`). Outside this window, beat task exits immediately (`discussion.py:617`).
- **Paced Quota Math** (`discussion.py:78` `_target_by_now`):
  $$\text{target} = \begin{cases} 0 & \text{if } h < 8 \\ \text{quota} & \text{if } h \ge 22 \\ \min\left(\text{quota}, \left\lceil \text{quota} \times \frac{h - 8}{22 - 8} + 0.0001 \right\rceil\right) & \text{otherwise} \end{cases}$$
- **Top-Up Threshold**: `_MIN_QUEUE_SIZE = 3` (`discussion.py:427`). If pending ideas < 3, calls `_topup_queue` (`discussion.py:455`).

### 4.2 Sourcing Logic & Dual Fact-Check
- **Sources (`discussion_topic_mode`)**:
  1. `"news"`: Freshest scraped article from last 3 days (`_NEWS_FRESH_DAYS = 3`, `discussion.py:63`) not already having a `PublishJob` on this fanpage (`_pick_news_article`, `discussion.py:92`).
  2. `"evergreen"`: LRU active `DiscussionTopic` seed (`_pick_evergreen_topic`, `discussion.py:124`).
  3. `"both"`: Tries AI general knowledge first (`_generate_general_topic`, `discussion.py:357`). If dual fact-check fails 2 attempts, falls back to news, then evergreen seed (`discussion.py:466-484`).
- **General Topic Grounding & Dual Fact-Check**:
  - Prompt Grounding: Prompt receives recent headlines from past 60 days (`_recent_headlines`, `discussion.py:264`) and history of questions to prevent repeats:
    - Avoids subjects used in last 14 days (`_recent_discussion_subjects`, `discussion.py:136`).
    - Avoids question angles used in last 7 days (`_recent_discussion_questions`, `discussion.py:190`).
    - Avoids all questions already posted today (`_todays_discussion_questions`, `discussion.py:221`).
  - Layer 1 Grounding: `_grounding_headlines_for_claim` (`discussion.py:288`) queries all articles in DB across all fanpages matching extracted names and outcome keywords (`"champion"`, `"wins the"`, etc.).
  - Layer 2 Model Check: Calls `factcheck_discussion_claim` via primary router model (`discussion.py:391`).
  - Layer 3 Cross-Check: Calls `factcheck_discussion_claim` forced to Groq (`discussion.py:402`). Both providers must validate claim as `valid`.
- **Copy Constraints** (`backend/app/services/news_copywriter.py:266, 300`):
  - Uses `NineRouterConfig.discussion_model` (default and prod: "FlashFast" since 2026-10-17).
  - Max question length: 90 characters (`_DISCUSSION_MAX_QUESTION_CHARS`, `news_copywriter.py:266`).
  - `discussion_label_mode`: `"discussion"` forces yes/no question ending in `?`; `"hot_take"` forces punchy declarative claim (`news_copywriter.py:308-313`).
- **Consumption**: `_consume_one` (`discussion.py:518`) pops oldest pending idea (FIFO). Resolves discussion template. Creates `PublishJob(content_type=discussion, status=pending_design)`. In auto mode, schedules `render_discussion` with 5–90s countdown (`discussion.py:597`).

---

## 5. Mode 5: Pinterest Content

### 5.1 Staging Queue & Catch-Up Loop
- **Active Hours**: WIB 08:00 to 22:00 (`_WINDOW_START_HOUR = 8`, `_WINDOW_END_HOUR = 22`, `backend/app/tasks/pinterest.py:33-34`).
- **Queue Limits**: `_MIN_QUEUE_SIZE = 5`, `_TOPUP_BATCH = 6` (`pinterest.py:49-50`).
- **Catch-Up Loop** (`pinterest.py:264-270`):
  - If previous ticks produced 0 surviving ideas, fanpage falls behind paced `_target_by_now`.
  - While loop consumes up to `_MAX_CATCHUP_PER_TICK = 3` ideas in a single tick to recover pacing without bursting (`pinterest.py:67, 264`).

### 5.2 Sourcing, Vision Verification & Skip Gates
- **Candidate Sourcing** (`backend/app/services/pinterest_source.py:80` `collect_new_candidates`):
  - Dedup: verifies image URL not in `GalleryImage` where `source_engine == "pinterest"` (`pinterest_source.py:24, 93`).
  - AI Keyword Path: `generate_pinterest_search_keyword` (`news_copywriter.py:835`) generates search term steering by `pinterest_custom_prompt`, avoiding last 12 used topics (`_recent_pinterest_topics`, `pinterest_source.py:36`). Fetches search results via 9Router jina-reader (`pinterest_source.py:113`).
  - Curated Path: Pulls from active `PinterestSource` (LRU) across pins, boards, or profile boards (`pinterest_source.py:121-152`).
- **Vision Quality & Fact-Check Gates** (`pinterest_source.py:164` `build_idea_from_candidate`):
  1. Resolution: downloads image, validates min size (400, 400) (`pinterest_source.py:20, 184`).
  2. Pixel Math Quality: `_is_low_quality_photo` rejects blurry/low-res images (`pinterest_source.py:199`).
  3. Watermark Gate: `vision_has_watermark` rejects overlays/stamps (`pinterest_source.py:202`).
  4. Vision Quality: `vision_check_photo_quality` evaluates lighting/clarity (`pinterest_source.py:205`).
  5. Description / Subject Verification: `vision_check_pin_description` checks pin text against image; if no pin description, `vision_identify_pin_subject` identifies subject (`pinterest_source.py:211-217`). If unidentifiable, candidate rejected and file unlinked (`pinterest_source.py:196, 219`).
  6. Stores image as `GalleryImage(source_engine="pinterest")` and stages `PinterestContentIdea(status="pending")` (`pinterest_source.py:221-243`).

### 5.3 Classification & Consume
- **Template Classification** (`backend/app/services/pinterest_classifier.py:25` `classify_pinterest_content`):
  - Classifies title into `"quote"` vs `"news"`.
  - Guard against combat-sports/motorsport nicknames: `_has_real_quote` (`pinterest_classifier.py:10`) requires quoted span $\ge$ 4 words. Names like `Anderson "The Spider" Silva` are classified as `"news"`, preventing giant decorative quote marks around person names (`pinterest_classifier.py:32-50`).
- **Hashtag Injection** (`backend/app/services/news_copywriter.py:883` `generate_pinterest_hashtags`):
  - Mode 5 description serves as Facebook caption. At consume time, AI generates exactly `cs.hashtag_count` hashtags, appended to caption (`tasks/pinterest.py:179-182`).

---

## 6. Mode 6: Facebook Photo Recreate

### 6.1 Sourcing & Image Scraping
- **Public Grid Fetcher** (`backend/app/services/facebook_photo_source.py:167` `_fetch_grid_markdown`):
  - Pulls public page `/photos` grid markdown via 9Router jina-reader with cache-busting query parameter `?_cb=...` (`facebook_photo_source.py:178`).
  - Regex `_PHOTO_ENTRY_RE` (`facebook_photo_source.py:45`) extracts `fbid`, CDN image URL, and alt text.
  - High-Res Upgrade: Strips `ctp=s206x206` thumbnail override (`_upgrade_image_url`, `facebook_photo_source.py:59, 63`), prompting Facebook CDN to return full 1080x1080 image.
  - Dedup: Dedupes on `fbid` (extracted from permalink) stored in `GalleryImage.keyword` as `facebook_photo:<fbid>` (`facebook_photo_source.py:137, 151`). URL dedup avoided because CDN domains rotate.
  - Download: Bypasses standard `classify_and_gate_image` since source images are meme/news graphics (`facebook_photo_source.py:86-95`).

### 6.2 Vision Classification & Filtering
- **Classifier** (`backend/app/services/facebook_content_classifier.py:110` `classify_facebook_photo`):
  - Single vision prompt categorizes photo into `"news"`, `"quote"`, `"discussion"`, or `"other"`.
  - Reject Gate: `"other"` (memes, raw photos, promos, schedules) rejected immediately (`facebook_content_classifier.py:16, 61`).
  - Topic Filter: If `facebook_photo_topic_filter` set, prompt enforces strict topic matching; fails closed if `on_topic` is not explicitly `True` (`facebook_content_classifier.py:74-80, 106`).
  - Incomplete Quote Gate: `is_incomplete_quote` (`facebook_photo_source.py:364`) rejects trailing ellipses or clipped quotes.
  - Localization: Rewrites headline/quote/discussion into fanpage language (`_localize_title`, `facebook_photo_source.py:309`).
  - Stages `FacebookPhotoIdea(status="pending")`. Source photo is deleted; clean subject photo is sourced later during card rendering.
- **Consumption & Caption** (`backend/app/tasks/facebook_photo.py:142` `_consume_one`):
  - Paced 08:00–22:00 WIB with catch-up loop up to 3 ideas/tick (`_MAX_CATCHUP_PER_TICK = 3`, `facebook_photo.py:50, 251`).
  - Caption generated via `build_caption_prompt` with `with_attribution=False` (`facebook_photo.py:169`) to avoid crediting the competitor page.

---

## 7. Mode 7: YouTube Clips

### 7.1 Pipeline Lifecycle & Video Ingestion
```
[1] Discover (yt_clip_cycle, 30m) ──► YtVideo(status="discovered")
[2] Analyze (on-demand, video queue) ──► YtClipIdea(status="pending")
[3] Consume (generate_yt_clip_content, 08-22 WIB) ──► PublishJob(status="pending_design")
[4] Render & Publish (yt_clip_render) ──► 9:16 Video + Single-line Caption
```
- **Discovery** (`backend/app/tasks/yt_clip.py:77` `discover_source`):
  - RSS feed parsed via `fetch_feed` (`backend/app/services/youtube_source.py:79`).
  - Skips YouTube Shorts (`e.is_short`, `yt_clip.py:88`).
  - Skips videos older than `yt_clip_max_video_age_days` (default 7 days) (`yt_clip.py:90`).
  - Upserts `YtVideo(status="discovered")` with `ON CONFLICT DO NOTHING` on `(fanpage_id, video_id)` (`yt_clip.py:73`).
- **On-Demand Analysis Trigger** (`yt_clip.py:384` `_dispatch_analysis`):
  - Triggered only if pending ideas < $2 \times \text{daily\_count}$ (`yt_clip.py:389`).
  - Gated by `blocked_until(db)`: halts if YouTube rate limit/ban detected (`yt_clip.py:387`).
  - Enqueues `analyze_video.delay(video.id)` on Celery `video` queue (`yt_clip.py:394`).

### 7.2 Subtitle Extraction & Highlight Search
- **Video Skip Gates** (`yt_clip.py:182` `_skip_reason`):
  - Live streams (`is_live`, `is_upcoming`, `post_live`).
  - Duration < 180s (`_MIN_VIDEO_S = 180`) or > 3 hours (`_MAX_VIDEO_S = 10800`).
  - Missing subtitle track (`meta.sub_track is None`).
- **Highlight Model Chain** (`backend/app/services/yt_highlights.py:333` `find_highlights`):
  - Parses JSON3 subtitles to lines and words (`yt_transcript.py:parse_json3`).
  - Formats transcript as SRT with timestamps.
  - Tries preferred models (`_PREFERRED_MODELS`: Claude Sonnet 4.6, Claude Opus 4.6, Gemini 3.8 Flash, etc.; `yt_highlights.py:38-44`) down router fallback chain within total budget of 300s (`_TOTAL_BUDGET_S = 300.0`, `yt_highlights.py:46`).
- **Pause Snapping** (`yt_highlights.py:266` `fit_clip`):
  - Snaps raw AI start/end timestamps to speech pause boundaries detected in transcript words (`nearest_pause`). Enforces clip duration within `[min_s, max_s]` (`yt_highlights.py:288`).
- **Deduplication & Overlap Prevention**:
  - `_taken_ranges` (`yt_clip.py:225`): collects all existing clip ranges for `video_id` across ALL fanpages from both `YtClipIdea` (`pending`, `used`) and active `PublishJob`.
  - `is_similar` (`yt_highlights.py:60`): drops candidate if overlap $\ge$ 30% of shorter clip (`overlap_fraction >= 0.30`, `yt_highlights.py:48`).
- **Title Validation (S2f)** (`backend/app/services/yt_clip_title.py:273` `clean_clip_title`):
  - Featured person's name MUST appear in title (`_contains_name`, `yt_clip_title.py:73`); prefixes surname if missing.
  - Drops title if no person extracted or if title exceeds max length (default 60 chars) (`yt_clip_title.py:291, 301`).
  - Strips forbidden channel names, clickbait qualifiers, and source attributions (`yt_clip_title.py:35-50`).

### 7.3 Consumption & Single-Line Captions (S12)
- **Pacing**: Consumes ideas 08:00–22:00 WIB ordered by `YtVideo.published_at.desc(), YtClipIdea.virality_score.desc()` (`yt_clip.py:449`). Bounded catch-up up to 3 clips/tick (`_MAX_CATCHUP_PER_TICK = 3`, `yt_clip.py:35, 577`).
- **Single-Line Caption Format** (`yt_clip.py:472-506`, `yt_clip_title.py:512` `one_line_caption`):
  - Enforces strict single line without newlines or paragraphs.
  - Hook + context sentence $\le 180$ chars (`desc_max`).
  - Strips all attribution / channel / source lines (`strip_source_lines`, `yt_clip_title.py:202`).
  - Strips gaming/sim-racing hashtags (`clean_clip_hashtags`, `yt_clip_title.py:474`). Appends exactly `hashtag_count` allowed hashtags on the same line.

---

## 8. Image Gallery: Ingestion, Throttling & Labeling

### 8.1 Download Engine & Quality Gates
- **Search Backend**: 9Router `/v1/web/fetch` (jina-reader) fetches Getty Images editorial search results as markdown (`backend/app/services/image_downloader.py:3-11`).
- **Flickr (second source, same `download_images` call)**: Flickr search (`gallery_flickr_search_url_template`, taken yesterday 00:00 .. today 23:59 WIB, pages 1-2) runs first and stores up to half of `max_num` as `source_engine="flickr"` (`_collect_urls_flickr`); thumbnails are upgraded to the 1024 px `_b` size (`_h` returns 410); `captured_at` = window start (search results carry no per-photo date). A Flickr failure is logged and Getty still runs. Off switch: `gallery_flickr_enabled`.
- **Dateline Parsing**: Regex `_CAPTION_DATE_RE` (`image_downloader.py:133`) extracts shot date directly from Getty image captions (`"on August 09, 2026"`).
- **Quality & Usability Gates** (`image_downloader.py:387` `_fetch_and_store`):
  1. Time Budget: 15-minute hard execution cap (`_FETCH_LOOP_TIME_BUDGET_SECONDS = 900`, `image_downloader.py:48, 390`).
  2. Dedup: Query parameters stripped to create stable canonical URL key (`_dedup_key`, `image_downloader.py:399`).
  3. Resolution: Original image pre-upscale must be $\ge$ 500x500 (`min_size`, `image_downloader.py:413`).
  4. Upscaling: Small comps (e.g. 612px) upscaled via FSRCNN x2 + sharpen, or UltraSharpV2 + GFPGAN if `hq_upscale_enabled` (`image_downloader.py:430-435`).
  5. Vision Usability Gate: `classify_and_gate_image` (`backend/app/services/design_images.py:1330`) evaluates face/action shot usability. Rejects crowd/stage/logo-only shots, screenshots, text overlays, and blurry/tiny/obstructed subjects (`image_downloader.py:447-472`).

### 8.2 Event-Aware Download Throttling
- **Scheduler** (`backend/app/tasks/gallery_downloader.py:347` `download_all_keywords`):
  - Global pause kill-switch: skips if `Settings.gallery_scraping_paused == True` (`gallery_downloader.py:373`).
  - Base interval: 24 hours (`_KEYWORD_INTERVAL_HOURS = 24`, `gallery_downloader.py:43`).
- **Event Window Logic** (`gallery_downloader.py:188` `_in_event_window`):
  - Window opens before `next_event_date`: MotoGP (H-3), F1 (H-2), UFC/Boxing (H-3), other (H-2) (`_NICHE_EVENT_WINDOW_DAYS_BEFORE`, `gallery_downloader.py:102-107`).
  - Window closes: `next_event_date + 1 day` (`_EVENT_WINDOW_DAYS_AFTER = 1`, `gallery_downloader.py:185`).
  - Event date refresh (`refresh_keyword_event_dates`, beat 02:00 WIB): a future date is never re-checked; a date that passed within the last 14 days is re-checked every 20 h (`_EVENT_PASSED_LOOKUP_INTERVAL_HOURS`, `_EVENT_PASSED_FAST_RECHECK_DAYS`) so back-to-back race weekends are found in time; otherwise every 5 days (`_EVENT_LOOKUP_INTERVAL_DAYS`). News freshness (6 h race day / 12 h other days, `event_days.news_fresh_hours`) is decided by the day the news job was created, not by today (2026-10-31).
  - Inside window: interval tightens from 24h to 4h (`_EVENT_WINDOW_INTERVAL_HOURS = 4`, `gallery_downloader.py:147, 208`).
  - Tight Hourly Window: If `next_event_datetime_utc` is known, interval tightens to 1 hour (`_EVENT_TIME_TIGHT_INTERVAL_HOURS = 1`, `gallery_downloader.py:160, 206`) during estimated upload window (event time + 3h upload buffer, spanning -1h to +4h; `gallery_downloader.py:157-159`).
  - Far From Event Tiers: 3–6 days out $\to$ 48h; 7–13 days out $\to$ 96h; 14+ days out $\to$ 168h (weekly) (`_FAR_FROM_EVENT_INTERVAL_TIERS`, `gallery_downloader.py:123-140`).
  - Prominence Multiplier: `"star"` floored to $\le 24$h; `"minor"` interval multiplied by 2 (`_PROMINENCE_MINOR_MULTIPLIER = 2`, `gallery_downloader.py:173, 214-218`).
- **Stock Ceilings & Eviction**:
  - Active Tier (mentioned in scraped article within 7 days or in event window): ceiling = 500 (`_ACTIVE_KEYWORD_CEILING = 500`, `gallery_downloader.py:61, 390`).
  - Quiet Tier: ceiling = 60 (`_QUIET_KEYWORD_CEILING = 60`, `gallery_downloader.py:62`). Top-up throttled by `_fresh_supply_status` based on 3-week pick rate (`gallery_downloader.py:246-290`).
  - Event Window Eviction: If keyword at ceiling (500) AND in event window, soft-deletes oldest 30 photos (`_EVENT_WINDOW_EVICTION_BATCH = 30`, `gallery_downloader.py:77, 406`) ordered by `captured_at.asc().nullsfirst()`, `downloaded_at.asc()` (`gallery_downloader.py:334`) to make room for race weekend coverage.

### 8.3 Rich Gallery Labeling
- **Tasks**: `label_gallery_image` (`backend/app/tasks/gallery_labels.py:32`) and safety net `label_new_gallery_images` (`gallery_labels.py:90`).
- **Budget Control**: Redis counter `reserve(s.gallery_labeling_daily_cap)` (`backend/app/services/gallery_label_budget.py:34`). Drops task if daily cap reached (`gallery_labels.py:50, 120`). Safety net caps at `min(daily_cap, 40)` per tick (`gallery_labels.py:100`).
- **Vision Schema** (`backend/app/services/gallery_labeler.py:69` `LABEL_PROMPT`):
  - Analyzes image at max 512px thumbnail (`gallery_labeler.py:112`).
  - Extracts sport, venue, shot_type (13 categories), people_count, activity (22 categories), expression (11 categories), context (session, rain, night, circuit_visible), livery/kit colors, event series/name/year/session, and short caption (`gallery_labeler.py:13-44, 69-99`).
  - Merges into `GalleryImage.attributes` JSON (`gallery_labels.py:67, 126`).

---

## 9. Comprehensive Content Skip Gates Summary

| Mode | Skip Gate Condition | Action Taken | Exact Location |
|---|---|---|---|
| **1 (IG)** | Crawl sleep window (01:00–06:00 WIB) | Entire crawl wave skipped | `backend/app/tasks/crawler.py:48-61` |
| **1 (IG)** | IG Post older than `max_post_age_days` | Post discarded | `backend/app/tasks/crawler.py:299-301` |
| **1 (IG)** | Video, Reel, or video-only album | Post discarded | `backend/app/tasks/crawler.py:310, 316` |
| **1 (IG)** | URL not matching Instagram CDN host regex | Download blocked (SSRF guard) | `backend/app/tasks/image_saver.py:39-44` |
| **1 (IG)** | Fanpage source trigger is `viral_only` | Skipped during crawl fanout (waits for Radar) | `backend/app/tasks/fan_out.py:125-130` |
| **2 (News)** | robots.txt disallows fetch | Scrape aborted with PermissionError | `backend/app/services/news_scraper.py:230, 256` |
| **2 (News)** | Article page contains HLS streaming markers (.m3u8) | Skipped as video content | `backend/app/services/news_scraper.py:472-473` |
| **2 (News)** | Article published older than `source.max_age_days` | Skipped as stale news | `backend/app/tasks/news_scraper.py:253-259` |
| **2 (News)** | Title/content extraction failed (non-transient) | Saved as `status=skipped` stub to prevent retry | `backend/app/tasks/news_scraper.py:189-215` |
| **2 (News)** | Editorial Gate judges `post_worthy == False` | Discarded for that fanpage | `backend/app/tasks/news_copywriter.py:105-106` |
| **4 (Disc)** | Current WIB hour outside 08:00–22:00 | Sweep exits immediately | `backend/app/tasks/discussion.py:617-618` |
| **4 (Disc)** | Count today $\ge$ `_target_by_now(quota)` | Idea consumption deferred to later slot | `backend/app/tasks/discussion.py:654-655` |
| **4 (Disc)** | General topic fails dual fact-check twice | Candidate discarded, cycle skipped | `backend/app/tasks/discussion.py:416-420` |
| **4 (Disc)** | Idea's source article already has publish job | Idea status marked `skipped` | `backend/app/tasks/discussion.py:548-554` |
| **5 (Pin)** | Image resolution $< 400\times 400$ | Candidate dropped, unlinked | `backend/app/services/pinterest_source.py:184-190` |
| **5 (Pin)** | Watermark detected or vision quality check fails | Candidate dropped, unlinked | `backend/app/services/pinterest_source.py:202-206` |
| **5 (Pin)** | Vision cannot identify subject or verify description | Candidate dropped, unlinked | `backend/app/services/pinterest_source.py:218-220` |
| **6 (FB)** | Vision classifies photo as `"other"` | Candidate discarded | `backend/app/services/facebook_content_classifier.py:16, 90-91` |
| **6 (FB)** | `topic_filter` active and `on_topic` is not `True` | Candidate discarded (fail-closed) | `backend/app/services/facebook_content_classifier.py:106, 135` |
| **6 (FB)** | Quote text ends with trailing ellipsis / incomplete | Candidate discarded | `backend/app/services/facebook_photo_source.py:364-388` |
| **7 (YT)** | Video is Short, or channel video $> \text{max\_age}$ | Video skipped during discovery | `backend/app/tasks/yt_clip.py:88-91` |
| **7 (YT)** | Pending idea queue $\ge 2 \times \text{daily\_count}$ | Video analysis deferred | `backend/app/tasks/yt_clip.py:389-390` |
| **7 (YT)** | Video duration $< 180$s or $> 3$ hours, or no subtitles | Video status marked `skipped` | `backend/app/tasks/yt_clip.py:182-195` |
| **7 (YT)** | Highlight overlap $\ge 30\%$ with existing clip range | Highlight dropped | `backend/app/tasks/yt_clip.py:284-289` |
| **7 (YT)** | Highlight title missing featured person's name (S2f) | Highlight dropped at idea creation | `backend/app/tasks/yt_clip.py:298-303` |
| **Gallery** | Global pause setting enabled (`gallery_scraping_paused`) | Entire download sweep skipped | `backend/app/tasks/gallery_downloader.py:373-375` |
| **Gallery** | Keyword at ceiling and outside event window | Download skipped | `backend/app/tasks/gallery_downloader.py:394-400, 416-418` |
| **Gallery** | Quiet keyword fresh count $\ge$ usage target | Download skipped | `backend/app/tasks/gallery_downloader.py:419-424` |
| **Gallery** | Download time budget exceeds 15 minutes | Search loop breaks, defers remaining URLs | `backend/app/services/image_downloader.py:390-396` |
| **Gallery** | `classify_and_gate_image` labels image unusable | File unlinked and rejected | `backend/app/services/image_downloader.py:469-472` |
| **Gallery** | Daily labeling cap reached in Redis | Label task skipped / tick halted | `backend/app/tasks/gallery_labels.py:51, 121, 141` |

---

## Verified claims

1. `backend/app/services/nine_router.py:28` - Default discussion model is "FlashFast" (owner rule 2026-10-17: text and vision use only the FlashFast combo + gemini-3.8-flash ids).
2. `backend/app/services/model_health.py:128` - Model health probing strictly asserts that model names starting with "cx/" are never probed.
3. `backend/app/services/ai_caption.py:253` - OpenAI SDK max_retries is set to 0 to prevent internal retries from stacking on top of ROUTER_MODEL_FALLBACKS.
4. `backend/app/services/news_copywriter.py:623` - If Groq output fails JSON parsing, it retries once via Gemini before failing.
5. `backend/app/tasks/crawler.py:36` - IG crawl wave enforces a 90-minute hard deadline after which busy viewer accounts are skipped.
6. `backend/app/services/news_scraper.py:245` - RSS feed fetching deliberately bypasses Scrapling to avoid synthetic HTML wrappers breaking XML title tags.
7. `backend/app/tasks/news_copywriter.py:31` - Breaking news fanout stagger is shortened to 5-90s compared to the normal 60-1500s stagger.
8. `backend/app/tasks/discussion.py:402` - Mode 4 general-knowledge hot-take claims undergo an independent cross-check on Groq after passing primary router fact-checking.
9. `backend/app/tasks/pinterest.py:67` - Mode 5 idea consumption has a catch-up loop capped at 3 ideas per tick to recover from bursty survival rates.
10. `backend/app/services/facebook_photo_source.py:63` - Facebook photo scraping regex strips "ctp=s206x206" to force Facebook CDN to serve high-resolution 1080x1080 images.
11. `backend/app/tasks/yt_clip.py:389` - YouTube video analysis is dispatched only on demand when pending ideas fall below 2x daily count.
12. `backend/app/tasks/gallery_downloader.py:406` - When a gallery keyword at its 500 ceiling enters its event window, it soft-deletes its 30 oldest photos to make room for race weekend photos.

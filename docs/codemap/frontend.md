# Deep Dive D5: Frontend Architecture (Next.js App Router)

> Generated 2026-10-16 at main 25edf38 from a full read of the listed files. The "Verified claims" list at the bottom was checked line by line against the code. Other details are a careful map but not each re-checked. Read the real code before changing behaviour, and update this file when the code changes.


Comprehensive technical reference for the Studio frontend: App Router page hierarchy, component composition, SWR/Axios data layer, authentication lifecycle, styling tokens, Fabric.js card designer, multi-tab fanpage configuration, and routing mismatch audit.

---

## 1. System Architecture & Core Patterns

```
Browser Client (Next.js 14.2.18 App Router)
 │
 ├── Auth Gate: RootLayout -> DashboardLayout -> localStorage.getItem("token")
 │     ├─► None -> router.replace("/login") [frontend/src/app/(dashboard)/layout.tsx:15]
 │     └─► Present -> Renders Shell (Sidebar + TopAppBar + Content + BottomTabBar)
 │
 ├── State & Data Fetching:
 │     ├─► SWR (swr ^2.2.5): Polling intervals (5s–60s), cache keys, optimistic mutate()
 │     └─► Axios (axios ^1.7.9): Client singleton [frontend/src/lib/api.ts:6]
 │           ├─► Request Interceptor: Injects "Authorization: Bearer <token>" [api.ts:12]
 │           └─► Response Interceptor: 401 HTTP status -> clears token, window.location = "/login" [api.ts:21]
 │
 ├── UI & Design System:
 │     ├─► Tailwind CSS 3.4.17 + PostCSS [frontend/tailwind.config.js]
 │     ├─► Custom theme variables in globals.css (Minimal/Solar palette: bg-default, bg-paper, text-primary, text-secondary)
 │     ├─► ThemeContext [frontend/src/contexts/ThemeContext.tsx:25]: light/dark (.dark), compact (.compact), presets, navColor
 │     └─► Iconography: @iconify/react (Solar Duotone `solar:*`, Eva `eva:*`, MingCute `mingcute:*`, MDI `mdi:*`)
 │
 └── Ingress & Reverse Proxy Contract:
       └─► Nginx strips `/api/` before passing to FastAPI backend:
             `location /api/ { proxy_pass http://api:8000/; }` [nginx/conf.d/app.conf:65]
```

---

## 2. Nginx `/api` Proxy Stripping & Backend Route Mismatches

Nginx proxy configuration (`nginx/conf.d/app.conf:65-66`) defines `proxy_pass http://api:8000/;` with a trailing slash. Nginx strips the `/api/` URI prefix before dispatching requests to the FastAPI ASGI application (`http://api:8000`). Consequently, FastAPI routers must register with bare prefixes (e.g., `APIRouter(prefix="/fanpages")`).

### Route Mismatch Findings

| Frontend Call (`frontend/src/lib/api.ts`) | Frontend Path | Backend Router Prefix / Definition | Real URL sent by Nginx | Backend Expectation | Status / Impact |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `listRadarAccounts` (`api.ts:395`), `listRadarStories` (`api.ts:404`), `getShadowReport` (`api.ts:406`), etc. | `/radar/...` | `backend/app/api/radar.py:12`:<br>`prefix="/api/radar"` | `GET /radar/...` | `GET /api/radar/...` | **BROKEN (404)**: Frontend omits `/api`, while backend router hardcodes `/api/radar`. Fails in both dev and prod. |
| `getScraperHealth` (`api.ts:39`) | `/scraper-health` | `backend/app/api/scraper_health.py:14`:<br>`prefix="/scraper-health"` | `GET /scraper-health` | `GET /scraper-health` | **FIXED 2026-10-18**: switched to bare prefix `/scraper-health` on frontend and backend. |
| `listF1Drivers` (`api.ts:486`), `syncF1Drivers` (`api.ts:489`), `createF1Driver` (`api.ts:492`) | `/f1-drivers/...` | `backend/app/main.py:40`:<br>`prefix="/f1-drivers"` | `GET /f1-drivers/...` | `GET /f1-drivers/...` | **FIXED 2026-10-18**: switched to bare prefix `/f1-drivers` on frontend and backend. |
| `listBurners` (`api.ts:69`), `createBurner` (`api.ts:71`), `deleteBurner` (`api.ts:74`), etc. | `/burners/...` | **None**: No burner router in `backend/app/main.py` | `GET /burners` | _No router_ | **DEAD CODE**: Burner endpoints in `api.ts` point to nonexistent backend routes. Not consumed by any active frontend component. |

---

## 3. Page → Component → API → Backend Mapping

| Route | Page File (`frontend/src/app/...`) | Key Components Used | `lib/api.ts` Functions | Backend Endpoint & Handler |
| :--- | :--- | :--- | :--- | :--- |
| `/` | `page.tsx:5` | None (Redirect router) | None | Client-side redirect (`/dashboard` or `/login`) |
| `/login` | `login/page.tsx:7` | HTML form, custom inputs | `login()` (`api.ts:33`) | `POST /auth/login` (`backend/app/api/auth.py:27`) |
| Shell | `(dashboard)/layout.tsx:10` | `Sidebar`, `TopAppBar`, `BottomTabBar`, `SettingsDrawer`, `NotificationDropdown` | `getNotifications()` (`api.ts:435`) | `GET /notifications` (`backend/app/api/notifications.py:14`) |
| `/dashboard` | `(dashboard)/dashboard/page.tsx:21` | `KpiCard`, `CrawlerHealthCard` | `getDashboardStats`, `getCrawlerHealth`, `triggerCrawl`, `triggerFanpageSync`, `restartBeat` (`api.ts:37,38,373-375`) | `GET /dashboard/stats`, `GET /dashboard/health`, `POST /jobs/crawl-now`, `POST /jobs/sync-fanpages`, `POST /jobs/restart-beat` |
| `/fanpages` | `(dashboard)/fanpages/page.tsx:85` | `FanpageMenu` | `listFanpages`, `updateFanpage`, `triggerFanpageSync` (`api.ts:42,44,374`) | `GET /fanpages`, `PUT /fanpages/{id}`, `POST /jobs/sync-fanpages` |
| `/fanpages/[id]` | `(dashboard)/fanpages/[id]/page.tsx:82` | `SettingsTabs`, `GlobalPublishMode`, `YtClipSection`, `YtClipLists`, `AiVideoSection` | ~30 functions (see section 4) | `GET /fanpages/{id}`, `PUT /fanpages/{id}`, sub-resource endpoints in `fanpages.py` & `yt_clips.py` |
| `/queue` | `(dashboard)/queue/page.tsx:66` | `QueueCard`, `ClipPlayer`, Lightbox | `listJobs`, `publishJob`, `skipJob`, `updateJobCaption`, `regenerateCaption`, `renderJobNow` (`api.ts:85-106,353`) | `GET /publish-jobs`, `POST /publish-jobs/{id}/publish`, `POST /publish-jobs/{id}/skip`, `PUT /publish-jobs/{id}/caption`, `POST /publish-jobs/{id}/regenerate-caption`, `POST /publish-jobs/{id}/render-now` |
| `/history` | `(dashboard)/history/page.tsx:98` | `HistoryCard`, `ClipPlayer`, `ConfirmDialog`, `Toast` | `listJobs`, `listFanpages`, `getJobStats`, `deletePublishJob`, `reeditJob` (`api.ts:85,42,91,104,106`) | `GET /publish-jobs`, `GET /fanpages`, `GET /publish-jobs/stats`, `DELETE /publish-jobs/{id}`, `POST /publish-jobs/{id}/reedit` |
| `/designer/[jobId]` | `(dashboard)/designer/[jobId]/page.tsx:41` | `TemplateEditor` (dynamic ssr:false) | `getDesignPayload`, `uploadDesignImage`, `proxyImageUrl` (`api.ts:344,346,355`) | `GET /publish-jobs/{id}/design-payload`, `POST /publish-jobs/{id}/design-image`, `GET /gallery/proxy` |
| `/templates` | `(dashboard)/templates/page.tsx:31` | `TemplateThumbnail` | `listTemplates`, `listTemplateStyles`, `updateStyleDefaults`, `createTemplate`, `updateTemplate`, `deleteTemplate` (`lib/designTemplatesApi.ts`, `api.ts:334-341`) | `GET /templates`, `GET /templates/styles`, `PUT /templates/styles/{style}/defaults`, `POST /templates`, `PUT /templates/{id}`, `DELETE /templates/{id}` |
| `/templates/[id]` | `(dashboard)/templates/[id]/page.tsx:23` | `TemplateEditor`, `TemplateThumbnail` | `getTemplate`, `updateTemplate` (`api.ts:336,339`) | `GET /templates/{id}`, `PUT /templates/{id}` |
| `/sources` | `(dashboard)/sources/page.tsx:32` | Source rows, Health badges | `listIGSources`, `deleteIGSource`, `getCrawlerHealth`, `getScraperHealth` (`api.ts:109,128,38,39`) | `GET /ig-sources`, `DELETE /ig-sources/{id}`, `GET /dashboard/health`, `GET /api/scraper-health` |
| `/news-sources` | `(dashboard)/news-sources/page.tsx:76` | Article drawer, Selector tester | `listNewsSources`, `createNewsSource`, `updateNewsSource`, `deleteNewsSource`, `scrapeNewsSourceNow`, `testNews*`, `listNewsArticles` (`api.ts:143-168`) | `GET /news-sources`, `POST /news-sources`, `PUT /news-sources/{id}`, `DELETE /news-sources/{id}`, `POST /news-sources/{id}/scrape-now`, `POST /news-sources/test-*`, `GET /news-sources/{id}/articles` |
| `/gallery` | `(dashboard)/gallery/page.tsx:79` | `ConfirmDialog`, `PromptDialog`, `Toast`, Image viewer | `listGalleryKeywords`, `listGalleryImages`, `uploadGalleryImage`, `importGalleryLinks`, `startAIFilterScan`, `bulkDeleteGalleryImages`, `updateGalleryImageLabels` (`api.ts:171-215`) | `GET /gallery/keywords`, `GET /gallery/images`, `POST /gallery/images/upload`, `POST /gallery/images/import-links`, `POST /gallery/ai-filter/scan`, `POST /gallery/images/bulk-delete`, `PATCH /gallery/images/{id}/labels` |
| `/ai-video` | `(dashboard)/ai-video/page.tsx:67` | `ResearchIdeaDetails`, `ClipPlayer` | `listAiVideoTemplates`, `listAiVideoIdeas`, `createAiVideoIdea`, `approveAiVideoIdea`, `rejectAiVideoIdea`, `listAiVideoJobs`, `retryAiVideoJob`, `cancelAiVideoJob`, `listGflowAccounts`, `updateGflowAccount`, `listGflowWorkers` (`api.ts:463-481`) | `GET /ai-video/templates`, `GET /ai-video/ideas`, `POST /ai-video/ideas`, `POST /ai-video/ideas/{id}/approve`, `POST /ai-video/ideas/{id}/reject`, `GET /ai-video/jobs`, `POST /ai-video/jobs/{id}/retry`, `POST /ai-video/jobs/{id}/cancel`, `GET /ai-video/accounts`, `PATCH /ai-video/accounts/{id}`, `GET /ai-video/workers` |
| `/radar` | `(dashboard)/radar/page.tsx:27` | AccountCard, StoryCard, ShadowReport | `listRadarAccounts`, `createRadarAccount`, `updateRadarAccount`, `deleteRadarAccount`, `listRadarNiches`, `listRadarStories`, `getShadowReport`, `previewRadarDecision`, `regenerateRadarDecision` (`api.ts:394-412`) | `GET /radar/accounts`, `POST /radar/accounts`, `PATCH /radar/accounts/{id}`, `DELETE /radar/accounts/{id}`, `GET /radar/niches`, `GET /radar/stories`, `GET /radar/shadow-report`, `POST /radar/decisions/{id}/preview`, `POST /radar/decisions/{id}/regenerate` |
| `/strategy` | `(dashboard)/strategy/page.tsx:18` | RecCard, MemoryDiff | `listRecommendations`, `approveRecommendation`, `rejectRecommendation`, `getContentMemory`, `refreshContentMemory` (`api.ts:447-456`) | `GET /strategy/recommendations`, `POST /strategy/recommendations/{id}/approve`, `POST /strategy/recommendations/{id}/reject`, `GET /strategy/memory/{id}`, `POST /strategy/memory/{id}/refresh` |
| `/analytics` | `(dashboard)/analytics/page.tsx:9` | StatCards, TrendBadges, PostTable | `getAnalyticsOverview`, `getFanpageAnalytics` (`api.ts:378,380`) | `GET /analytics/overview`, `GET /analytics/fanpages/{id}` |
| `/logs` | `(dashboard)/logs/page.tsx:49` | LogRows, CategoryFilter | `getLogs` (`api.ts:384`) | `GET /logs` (`backend/app/api/logs.py:20`) |
| `/settings` | `(dashboard)/settings/page.tsx:17` | `YouTubeSettingsCard`, `HermesTokensCard`, `AiVideoSettingsCard`, `GalleryLabelsSettingsCard`, `F1DriversSettings`, `DevicesSettingsCard` | `getSettings`, `updateSettings`, `testReplizCredentials`, `testProxies`, `testRelays`, `listF1Drivers`, `syncF1Drivers`, `listPhoneDevices`, `discoverPhoneDevices` (`api.ts:361-370, 485-506, 562, 574`) | `GET /settings`, `PUT /settings`, `POST /settings/repliz/test`, `POST /settings/proxies/test`, `POST /settings/relays/test`, `GET/POST /api/f1-drivers`, `GET /social-media/devices` |
| `/content-projects`| `(dashboard)/content-projects/page.tsx:24` | ProjectCard, CreateModal | `listContentProjects`, `createContentProject` (`api.ts:528,534`) | `GET /content-projects`, `POST /content-projects` |
| `/social-media` | `(dashboard)/social-media/page.tsx:100` | `SocialTabs` | `listBrands` (`lib/socialBrandsApi.ts:154`) | `GET /social-media/brands` |
| `/social-media/accounts` | `(dashboard)/social-media/accounts/page.tsx` | `SocialTabs`, `AccountDevicesCard` | `listReplizAccounts` (`lib/socialBrandsApi.ts:180`), `listPhoneDevices` (`api.ts:562`) | `GET /social-media/repliz-accounts`, `GET /social-media/devices` |
| `/social-media/brands/[id]` | `(dashboard)/social-media/brands/[id]/page.tsx` | `SocialTabs`, `DesignTemplatesSection`, `BrandPacingCard`, `BrandContentTable`, `BrandAiImageCard`, `BrandChannelCard`, `BrandExplainerBox` | `getBrand`, `setBrandGptcli` (`lib/socialBrandsApi.ts:157,160`), `updateFanpage`, `listTemplates`, `listTemplateStyles` | `GET /social-media/brands/{id}`, `PUT /social-media/brands/{id}/gptcli`, `PUT /fanpages/{id}`, `GET /templates`, `GET /templates/styles` |
| `/social-media/channels/new` | `(dashboard)/social-media/channels/new/page.tsx` | `StepBrand`, `StepPlatform`, `StepPublishMethod`, `StepPacing` | `createSocialDestination` (`api.ts:542`), `listPhoneDevices`, `listReplizAccounts` | `POST /social-media/destinations`, `GET /social-media/devices`, `GET /social-media/repliz-accounts` |
| `/social-media/channels/[id]` | `(dashboard)/social-media/channels/[id]/page.tsx` | `ChannelHeader`, `PublishTab`, `FallbackTab`, `MediaTab`, `PacingTab`, `ContentTab`, `HistoryTab` | `getDestination`, `updateDestination` (`api.ts`), `getEstimatedTimes`, `getRecentDeliveries` (`lib/socialBrandsApi.ts:165,170`) | `GET/PATCH /social-media/destinations/{id}`, `GET /social-media/destinations/{id}/estimated-times`, `GET /social-media/destinations/{id}/recent` |
| `/social-media/legacy` | `(dashboard)/social-media/legacy/page.tsx` | `AccountDevicesCard`, `SchedulePreview` (no `SocialTabs`) | `listSocialDestinations`, `createSocialDestination`, `updateSocialDestination`, `deleteSocialDestination`, `getSchedulePreview`, `listSocialMediaActivity` (`api.ts:538-556, 594`) | `GET /social-media/destinations`, `POST /social-media/destinations`, `PATCH /social-media/destinations/{id}`, `DELETE /social-media/destinations/{id}`, `GET /social-media/destinations/{id}/schedule-preview`, `GET /social-media/activity` |

---

## 4. `fanpages/[id]` Tab Configuration & Field Specifications

Target page: `frontend/src/app/(dashboard)/fanpages/[id]/page.tsx:82`.
Global Save handler (`page.tsx:781-798`): dispatches `updateFanpage(fanpageId, payload)` where `payload = { ...form, mode2_caption_language: form.caption_language }`.

### 4.1 Tab Structure (`SettingsTabs.tsx:5-15`)

1. **`global` (Global & Safety)**:
   - **Publish Modes** (`GlobalPublishMode.tsx:3-11`): `publish_mode`, `mode2_publish_mode`, `discussion_publish_mode`, `pinterest_publish_mode`, `facebook_photo_publish_mode`, `yt_clip_publish_mode`, `ai_video_publish_mode` (values: `"auto" | "manual_review"`).
   - **Target Locale & Schedule**: `target_country` (ISO-2 code), `timezone` (IANA string verified via `Intl.supportedValuesOf`), `publish_sleep_start_hour` (int 0–23), `publish_sleep_end_hour` (int 0–23), `publish_daily_limit` (int >= 1).
   - **Global Caption Rules**: `caption_language`, `mode2_caption_language`, `caption_tone`, `caption_max_length` (int), `caption_hashtag_count` (int), `caption_cta_text` (string), `caption_custom_prompt` (string).
   - **Attribution**: `use_attribution` (boolean), `caption_attribution_template` (string with placeholder `{source}`), `attribution_position` (`"top" | "bottom"`).
    - **Default Card Templates & Brand Look**: `DesignTemplatesSection` (`components/brand/DesignTemplatesSection.tsx`) manages brand look. Binds `template_style` (`"gp" | "highlight" | "classic"`), `brand_override` (boolean), custom colors (`accent_color`, `accent_secondary_color`, `badge_color`, `badge_text_color`, `quote_mark_color`, `quote_streak`) vs style defaults. Card template slots: `default_quote_template_id` (int | null), `default_news_template_id` (int | null), `default_discussion_template_id` (int | null).
    - **Watermark & Branding**: `watermark_text` (string), `design_expand` (boolean). Watermark image handled via multipart upload: `uploadWatermarkImage` (`POST /fanpages/{id}/watermark-image`), `deleteWatermarkImage` (`DELETE /fanpages/{id}/watermark-image`).

2. **`ig` (IG Repost — Mode 1 & 3)**:
   - **Feature Flags**: `mode1_ig_repost_enabled` (toggled via dedicated optimistic API call `page.tsx:800-815`), `ig_recreate_enabled` (boolean), `visual_engine` (`"off" | "chatgpt"`).
   - **Layout**: `ig_recreate_smart_layout` (boolean), `ig_recreate_split_template_id` (int | null).
   - **IG Source Accounts Table**: Add source (`POST /fanpages/{id}/sources`), delete source (`DELETE /fanpages/{id}/sources/by-username/{username}`), per-source trigger override (`PUT /fanpages/{id}/sources/{sourceId}/trigger`, values: `"every_post" | "viral_only"`), per-source recreate override (`PUT /fanpages/{id}/sources/{sourceId}/recreate`, boolean | null), caption preview (`previewCaption`).

3. **`news` (News Cards — Mode 2)**:
   - **Feature Flags**: `mode2_news_content_enabled` (boolean), `news_visual_engine` (`"off" | "fal" | "chatgpt"`), `mode2_editorial_gate_enabled` (boolean).
   - **Design & Layout**: `quote_shorten_enabled` (boolean), `mode2_gallery_niches` (string array of enabled gallery niches), `mode2_title_max_chars` (int), `mode2_badge_text` (string | null). (Note: the legacy "Template accent colour" picker was removed; accent colors are configured via Brand Look on the Global tab).
   - **Source Links**: `addFanpageNewsSource` (`POST /fanpages/{id}/news-sources`), `removeFanpageNewsSource` (`DELETE /fanpages/{id}/news-sources/{newsSourceId}`), copy preview (`previewNewsCopy`).

4. **`discussion` (Discussion Cards — Mode 4)**:
   - **Configuration**: `discussion_enabled` (boolean), `discussion_daily_count` (int), `discussion_topic_mode` (`"ai_auto" | "seeded_only"`), `discussion_label_mode` (`"niche_drivers" | "f1_all"`).
   - **Seed Topics**: `addDiscussionTopic` (`POST /fanpages/{id}/discussion-topics`), `updateDiscussionTopic` (`PUT ...`), `deleteDiscussionTopic` (`DELETE ...`).
   - **Content Ideas Pipeline**: `listDiscussionContentIdeas`, `createDiscussionContentIdea`, `updateDiscussionContentIdea` (edits: `label`, `question`, `subject_name`, `caption`), `deleteDiscussionContentIdea`.

5. **`pinterest` (Pinterest Cards — Mode 5)**:
   - **Configuration**: `pinterest_enabled` (boolean), `pinterest_daily_count` (int), `pinterest_source_mode` (`"random" | "round_robin"`), `pinterest_custom_prompt` (string), `pinterest_allow_low_quality` (boolean).
   - **Sources**: `addPinterestSource` (`POST /fanpages/{id}/pinterest-sources`), `updatePinterestSource`, `deletePinterestSource`.
   - **Ideas**: `listPinterestContentIdeas`, `updatePinterestContentIdea` (edits: `title`, `description`), `deletePinterestContentIdea`.

6. **`facebook` (FB Photo Recreate — Mode 6)**:
   - **Configuration**: `facebook_photo_enabled` (boolean), `fb_visual_engine` (`"off" | "fal" | "chatgpt"`), `facebook_photo_daily_count` (int), `facebook_photo_topic_filter` (string).
   - **Sources**: `addFacebookPhotoSource` (`POST /fanpages/{id}/facebook-photo-sources`), `updateFacebookPhotoSource`, `deleteFacebookPhotoSource`.
   - **Ideas**: `listFacebookPhotoIdeas`, `updateFacebookPhotoIdea` (edits: `design_title`, `design_subtitle`, `design_caption`), `deleteFacebookPhotoIdea`.

7. **`youtube` (YouTube Clips — Mode 7)** (`YtClipSection.tsx:56-139`):
   - **Configuration**: `yt_clip_enabled` (boolean), `yt_clip_daily_count` (int 0–20), `yt_clip_min_s` (int 15–170), `yt_clip_max_s` (int 25–180), `yt_clip_per_video` (int 1–10), `yt_clip_min_score` (int 0–10), `yt_clip_max_video_age_days` (int 1–365), `yt_clip_action_crop` (`"smart" | "fit"`), `yt_clip_captions` (boolean), `yt_clip_watermark` (boolean), `yt_clip_title_max_chars` (int 20–5000), `yt_clip_description_max_chars` (int 20–5000).
   - **Sources & Downloads** (`YtClipLists.tsx`): `addYtClipSource` (`POST /fanpages/{id}/yt-clip-sources`), `checkYtClipSource`, `listYtClipIdeas`, `updateYtClipIdea` (`title`, `hook_text`), `deleteYtClipIdea`, `listYtVideos`, `retryYtVideo`.

8. **`aivideo` (AI Video — Mode 8)** (`AiVideoSection.tsx:13-298`):
   - **Generation Limits**: `ai_video_enabled` (boolean), `ai_video_daily_count` (int 0–10), `ai_video_min_score` (int 0–100 | null), `ai_video_templates` (string array of template keys), `ai_video_template` (string | null), `ai_video_seconds_min` (int >= 4), `ai_video_seconds_max` (int >= 4), `ai_video_max_credits_per_video` (int | null), `ai_video_max_credits_per_day` (int | null), `ai_video_narrator` (`"EN-DOC" | "EN-PADDOCK" | "EN-AUSSIE-ENG" | "EN-SCOT" | "EN-JOURNO-F" | "EN-US-ENG"`), `ai_video_title_max_chars` (int), `ai_video_description_max_chars` (int).
   - **Overlay Branding**: `ai_video_logo_enabled` (bool), `ai_video_logo_size_pct` (5–30%), `ai_video_logo_position` (`"top_right" | "top_left" | "bottom_right" | "bottom_left"`), `ai_video_logo_margin_pct` (0–10%), `ai_video_captions_enabled` (bool), `ai_video_caption_size` (40–140px), `ai_video_caption_bottom_pct` (5–70%), `ai_video_caption_highlight` (hex string).
   - **Overlay Preview**: `previewAiVideoOverlay` (`POST /ai-video/fanpages/{id}/overlay-preview`, blob response converted to object URL).

9. **`radar` (Viral Radar)**:
   - **Parameters**: `radar_enabled` (boolean), `radar_shadow` (boolean), `radar_niches` (string array), `radar_min_likes` (int >= 1), `radar_confirm_ratio` (float >= 0.1), `radar_fast_ratio` (float >= 0.1), `radar_fast_window_min` (int >= 1), `radar_shelf_news_h` (int >= 1), `radar_shelf_evergreen_h` (int >= 1), `radar_burst_enabled` (boolean), `radar_daily_max` (int >= 0), `visual_engine` (string).

---

## 5. Core Interactive Workflows

### 5.1 Review Queue (`queue/page.tsx`)
- **Query Aggregation** (`page.tsx:13-24`): Parallels three `listJobs` requests via `Promise.all`: `pending_review` (Mode 1 IG reposts), `pending_design` (Mode 2/3/4/5/6 card renders awaiting manual review), and `pending_publish` (approved designs awaiting Repliz dispatch). Merges and sorts by `created_at DESC`. Refreshes every 15 seconds.
- **Lightbox**: Dedicated keyboard listener for `ArrowLeft`, `ArrowRight`, and `Escape` (`page.tsx:78-83`). Handles carousel multi-image navigation and video player embedding (`ClipPlayer`).
- **Post Operations**:
  - `handlePublish(id)`: Invokes `publishJob(id)` (`POST /publish-jobs/{id}/publish`), transitions job to `pending_publish` or directly triggers publishing.
  - `handleSkip(id)`: Invokes `skipJob(id)` (`POST /publish-jobs/{id}/skip`), marks job `skipped`.
  - `handleSaveCaption(id)`: Saves edited text via `updateJobCaption(id, editText)` (`PUT /publish-jobs/{id}/caption`).
  - `handleRegenerate(id)`: Dispatches `regenerateCaption(id)` (`POST /publish-jobs/{id}/regenerate-caption`).
  - `handleRerender(id)`: Re-triggers video clip render or news card layout via `renderJobNow(id)` (`POST /publish-jobs/{id}/render-now`).

### 5.2 History & Publishing Ledger Audit (`history/page.tsx`)
- **Pagination**: Uses `useSWRInfinite` with fixed `PAGE_SIZE = 30` (`page.tsx:96, 120`). Flattens paginated pages into a continuous array.
- **Publication Lag Verification** (`page.tsx:24-32`): `jobTimes()` compares `published_at` (hand-off to Repliz/phone dispatch) against `scheduled_for` (actual live slot on Facebook after sleep-window and pacing gap evaluation). Flags items as `isPendingLive` if scheduled live time is >60 seconds in the future.
- **Job Deletion & Re-edit**:
  - `deletePublishJob(id)`: Presents `ConfirmDialog`. Calls `DELETE /publish-jobs/{id}`, deleting the job record from PostgreSQL and issuing a remote cancel call to the Repliz API (`repliz_deleted`).
  - `reeditJob(id)`: Dispatches `POST /publish-jobs/{id}/reedit`, resetting job status back to `pending_design` or `pending_review` for re-authoring.

### 5.3 Card Review Designer (`designer/[jobId]/page.tsx`)
- **No-SSR Dynamic Mount**: Dynamically loads `TemplateEditor` with `{ ssr: false }` (`page.tsx:18`) to ensure HTML5 `<canvas>` and Fabric.js globals instantiate exclusively in browser DOM.
- **Payload & Candidate Injection** (`page.tsx:59-76`): Loads `getDesignPayload(jobId)`. Injects `design_title`, `design_subtitle`, and `design_caption` into matching placeholder textboxes on the Fabric canvas. Fetches the primary image candidate through the backend proxy (`proxyImageUrl` -> `GET /gallery/proxy`) to eliminate cross-origin taint (CORS) on the canvas element.
- **PNG Export & Transition** (`page.tsx:91-105`): Invokes `apiRef.current.exportPng()`, converting the Fabric canvas to a high-resolution PNG Blob. Uploads via `uploadDesignImage(jobId, blob)` (`POST /publish-jobs/{jobId}/design-image`, multipart form data). Automatically transitions job state to `pending_publish` and navigates back to `/queue`.

### 5.4 Fabric.js Template Editor (`TemplateEditor.tsx` & `applyTemplate.ts`)
- **Placeholder Contract**: Objects identify their semantic role via custom property `placeholderRole` (`TemplateEditor.tsx:67`):
  - `placeholderRole = "title"`: Primary headline textbox. Supports uppercase, lowercase, and capitalize transforms (`transformTitle`), dynamic two-tone split color (`applyTwoTone`), and custom markup parsing (`[acc]highlight[/acc]` via `parseMarks`).
  - `placeholderRole = "subtitle"`: Secondary sub-header textbox.
  - `placeholderRole = "caption"`: Tertiary body/credit textbox.
  - `placeholderRole = "image"`: Photo slot rectangle. Images injected into the slot are cover-fitted (`coverFitImage`, lines 118-120) and clipped precisely to the slot's bounding box (`slotBounds`).
- **Typography & Font Pre-caching**: Constrained to fonts vendored in `renderer/fonts/` (`FONTS` array, line 24: `Poppins`, `Montserrat`, `Roboto`, `Oswald`, `Playfair Display`, `Bebas Neue`, `Anton`, `Pacifico`, `Tusker Grotesk`, `Agrandir`, `Agrandir Grand Light`) ensuring WYSIWYG identity between browser canvas and the headless Chrome renderer.
- **Serialization**: `toTemplateJson()` produces a serialized Fabric JSON object alongside a `placeholderConfig` metadata map confirming whether `has_title` and `has_image_slot` are satisfied before persistence (`TemplateEditorPage:43-48`).

---

## 6. Shared Components & Design Tokens

### 6.1 Reusable UI Primitives
- `ConfirmDialog` (`frontend/src/components/ui/ConfirmDialog.tsx:17`): Accessible modal with backdrop blur, customizable danger/primary actions, and loading spinners.
- `PromptDialog` (`frontend/src/components/ui/PromptDialog.tsx:14`): Single-input modal for dynamic prompts and criteria definition.
- `Toast` (`frontend/src/components/ui/Toast.tsx:11`): Transient toast notification stack supporting auto-dismiss.
- `ClipPlayer` (`frontend/src/components/ClipPlayer.tsx:21`): HTML5 video player component handling aspect ratios, custom progress scrubbing, and play/pause states for Mode 7 (YouTube) and Mode 8 (AI Video).
- `CaptionCriteriaEditor` (`frontend/src/components/CaptionCriteriaEditor.tsx:54`): Form component binding `caption_tone`, `caption_language`, `caption_max_length`, `caption_hashtag_count`, `caption_cta_text`, and `caption_custom_prompt`.
- `SchedulePreview` (`frontend/src/components/social-media/SchedulePreview.tsx:14`): Gantt-style timeline visualization of queued posts against active destination sleep-windows.
- `AccountDevicesCard` (`frontend/src/components/social-media/AccountDevicesCard.tsx:21`): Device pool status manager showing ADB device health and active assignments.
- `DesignTemplatesSection` (`frontend/src/components/brand/DesignTemplatesSection.tsx`): Brand styling manager mounted on `fanpages/[id]` (Global tab) and `/social-media/brands/[id]`. Configures `template_style` (`gp`, `highlight`, `classic`), brand color overrides (`brand_override`, `accent_color`, `accent_secondary_color`, `badge_color`, `badge_text_color`, `quote_mark_color`, `quote_streak`), and previews 3 template category cards.
- `TemplateThumbnail` (`frontend/src/components/designer/TemplateThumbnail.tsx`): Client-side Fabric.js card preview with neutral photo slot fill (`#3A4152`). Accepts `look` (`BrandLook`), `label`, `title`, `subtitle`, `caption` props.
- `adaptLook.ts` (`frontend/src/components/designer/adaptLook.ts`): TypeScript mirror of renderer `renderer/inject.js` color logic (`normalizeLook`, `adaptLook`, `tintStreakDataUri`).
- `brandLook.ts` (`frontend/src/lib/brandLook.ts`): Palettes (`SWATCHES`, `PRESETS`), WCAG luminance / contrast calculations, `autoInk`, and look resolvers (`resolveLook`, `lookForFanpage`).
- `designTemplatesApi.ts` (`frontend/src/lib/designTemplatesApi.ts`): Typed API client for `/templates/styles` and `/templates/styles/{style}/defaults`.
- `socialBrandsApi.ts` (`frontend/src/lib/socialBrandsApi.ts`): Typed API client for `/social-media/brands`, `/destinations/{id}/estimated-times`, `/recent`, `/repliz-accounts`.
- `SocialTabs` (`frontend/src/components/social-media/SocialTabs.tsx`): Sub-navigation bar across `/social-media` with tabs `Brands`, `Publishing accounts`, `Classic view`, and `Activity`. Specialized subcomponents in `components/social-media/brand/*` (`BrandPacingCard`, `BrandContentTable`, `BrandAiImageCard`, `BrandChannelCard`, `BrandExplainerBox`) and `components/social-media/channel/*` (`ChannelHeader`, `StepBrand`, `StepPlatform`, `StepPublishMethod`, `StepPacing`, `PublishTab`, `FallbackTab`, `MediaTab`, `PacingTab`, `ContentTab`, `HistoryTab`, `EstimatedSlotsView`).

### 6.2 Token Architecture (`globals.css` & `tailwind.config.js`)
- **Palettes**: Custom HSL/RGB CSS variables mapped in Tailwind:
  - Backgrounds: `--bg-default`, `--bg-paper`, `--bg-paper-hover`
  - Text: `--text-primary`, `--text-secondary`, `--text-disabled`
  - Accents: `--primary-main` (default `#00A76F` Emerald), `--primary-light`, `--primary-dark`
  - Semantics: `--error-main` (`#FF5630`), `--warning-main` (`#FFAB00`), `--info-main` (`#00B8D9`)
- **Theme Variables**: Injected via HTML data attributes: `data-preset` (`cyan`, `purple`, `blue`, `orange`, `red`), `data-nav-color` (`integrate`, `apparent`), and `.compact` (reduces padding and font scale).

---

## 7. Claims to Verify

1. `frontend/src/lib/api.ts:4` - `API_URL` defaults to `"http://localhost:8000"` if `process.env.NEXT_PUBLIC_API_URL` is unset.
2. `frontend/src/lib/api.ts:24` - Response interceptor removes `"token"` from `localStorage` and triggers `window.location.href = "/login"` on HTTP 401.
3. `nginx/conf.d/app.conf:66` - Nginx proxy passes `/api/` to `http://api:8000/`, stripping the `/api/` prefix.
4. `backend/app/api/radar.py:12` - Radar APIRouter declares `prefix="/api/radar"`, conflicting with `api.ts:395` which calls `/radar/accounts`.
5. `backend/app/api/scraper_health.py:14` - Scraper health router uses `prefix="/api/scraper-health"`; it works in prod only because `api.ts:39` also adds `/api` (double prefix). CORRECTED by reviewer: the executor first claimed it 404s; live check shows 403 (route exists).
6. `backend/app/main.py:40` - F1 drivers router is registered with `prefix="/api/f1-drivers"`; works in prod via the same double-`/api` pattern (`api.ts:486`). CORRECTED by reviewer: not a 404.
7. `frontend/src/lib/api.ts:69` - `listBurners`/`createBurner` call `/burners`, which has no backend router (live 404). The functions are unused by any page, so this is dead code, not a visible bug.
8. `frontend/src/app/(dashboard)/fanpages/[id]/page.tsx:786` - `handleSave` injects `mode2_caption_language: form.caption_language` into the fanpage update payload.
9. `frontend/src/components/fanpages/GlobalPublishMode.tsx:3` - `PUBLISH_MODE_FIELDS` binds 7 distinct content modes to `"auto"` or `"manual_review"`.
10. `frontend/src/app/(dashboard)/queue/page.tsx:14` - Queue data fetcher aggregates `pending_review`, `pending_design`, and `pending_publish` jobs in a single `Promise.all`.
11. `frontend/src/components/designer/TemplateEditor.tsx:24` - Editor typography is restricted to 11 hardcoded system/vendored fonts (`Poppins`, `Montserrat`, `Roboto`, `Oswald`, `Playfair Display`, `Bebas Neue`, `Anton`, `Pacifico`, `Tusker Grotesk`, `Agrandir`, `Agrandir Grand Light`).
12. `frontend/src/app/(dashboard)/designer/[jobId]/page.tsx:96` - Exporting a card canvas uploads the PNG blob via `uploadDesignImage` which transitions the job from `pending_design` to `pending_publish`.

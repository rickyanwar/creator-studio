# Spec — Hermes Content Strategist + GFlow video (roadmap)

Status: planned 2026-10-11 (owner vision of 2026-10-11; plan approved by Claude on the owner's
standing instruction "kamu setujui semuanya menurutmu"). Phase 0 and Phase 1 are built on
branches; nothing here is deployed without the owner's explicit OK.

## Goal

Every fanpage gets its own data-driven strategy: Hermes studies how that page's posts perform,
learns the best topics, content types, days and hours for that page's audience (in the page's
own timezone), proposes ideas and schedules with a stated rationale, and — later — produces
AI video through GFlow. Decisions come from measured history, not random posting.

## Architecture (decided)

- **The studio app is the single source of truth**: database, queues, dashboard, publishing.
  Hermes never writes to the database directly; it calls the app API with a scoped token.
- **Hermes = intelligence**: research, analysis, ideas, prompts, strategy, evaluation.
- **GFlow = video generation engine**, a worker that consumes video jobs from the app.
- **If Hermes is down, publishing continues** on the rule-based schedule (sleep window, daily cap,
  per-fanpage timezone).
- **Approval**: strategy changes that alter a fanpage's settings need the owner's approval
  (dashboard button or the existing Telegram approval flow). No daily/weekly digests
  (owner rule) — insights live on the dashboard.

## Facts that shape the plan (checked 2026-10-11)

- Repliz post metrics: `GET /public/content/{postId}/statistic?accountId=…` — **Gold plan or
  higher** (the owner is on Premium; Gold is a one-time Rp49k/1 month … Rp420k/12 months).
  For Facebook it returns only **likes, comments, shares** — no reach, impressions or views.
  So learning is based on engagement snapshots over time (velocity), not reach.
- `publish_jobs.repliz_response_json` holds Repliz's `postId` (`{pageId}_{postId}`) once a post
  is live; ~2,935 historic jobs have one. `status_sync` stopped capturing new postIds after
  2026-10-08 because of a JSON/`.astext` bug — fixed on branch `hotfix-status-sync`.
- The go-live time of a post is `publish_jobs.scheduled_for` (UTC); `published_at` is when the
  schedule was sent to Repliz.
- Audience country/timezone is not available from Repliz for Facebook → it is the owner's
  per-fanpage setting (Phase 0).
- GFlow: model omni-flash, 9:16, 10 s per clip at 15 credits; longer videos = several clips, each
  continuing from the previous clip's last frame, joined with ffmpeg; narration is spoken inside
  the clip (English). `G-FLow_Automation_dekstop` (Mac, Flask 127.0.0.1:8770) already drives
  several Chrome profiles (multi-account), has a queue, credit tracking, video types
  (`motor_30s` = 3 × 10 s), a part merger and an AI prompt builder. Hermes on the VPS has the
  `media/google-flow` and `media/motorsport-tech-explainer` skills (gflow-cli v0.83).
- VPS RAM is tight (~1.4–4 GB free depending on load); Chrome-based GFlow workers are heavy.

## Phases

### Phase 0 — per-fanpage timezone (BUILT, branch `fanpage-timezone`)
IANA timezone + target country per fanpage; sleep window, daily cap (per local day) and
event-boost day use local time, so DST is automatic. Existing rows are backfilled to
Asia/Jakarta at deploy (identical behaviour — verified 1,500/1,500 schedules equal), then the
one-off script maps: Fight Today → America/New_York, GP Weekend → Europe/Paris, every other page →
Europe/London, converting the WIB sleep hours to local hours. Must be live before 2026-10-25
(UK DST end); US DST ends 2026-11-01.

### Phase 1 — metrics foundation (BUILT behind a flag, branch `metrics-foundation`)
- `post_metric_snapshots`: likes/comments/shares per published post at fixed ages
  (1h, 6h, 24h, 72h, 7d) + one `backfill` snapshot for older history. A snapshot only counts for
  a bucket when taken inside that bucket's window (threshold … threshold + max(15 min, 10 %)),
  so comparisons across posts are fair.
- Celery: `collect_post_metrics` (every 5 min) and `backfill_post_metrics` (hourly), both off
  until `settings.metrics_ingestion_enabled`; HTTP 402 marks the plan as `plan_required` and the
  task re-probes every 6 h, so metrics start automatically after the Gold upgrade.
- Analytics API + dashboard page (data only): per-fanpage totals, daily series (local dates),
  engagement by local hour and weekday, by content type, top posts, and a trend label
  (rising/flat/falling/insufficient: median 24h-engagement, last 14 days vs the 14 before,
  n ≥ 10 each, ±15 %). Medians, not means (viral outliers); every number shows its n.
- Owner actions to switch on: buy Repliz Gold → enable "Post metrics" in Settings.

### Phase 2 — Hermes insights + content memory (next)
- `strategy_recommendations` table: fanpage, kind (sleep_window, daily_cap, best_hours,
  content_mix, topic), proposed change (JSON), rationale, evidence (n, period, uplift), status
  (proposed → approved/rejected → applied), decided_by. Approve in the dashboard or via Telegram
  (SHA/ID-bound, single use, like the deploy approval).
- Scoped API token for Hermes: read analytics, write recommendations and ideas — nothing else.
- Content memory per fanpage (weekly): what worked (types, topics, hours), what flopped, current
  audience timezone — a JSON document Hermes reads before ideating. Viral Radar's
  `design_analysis_json` (type, mood, topic) is the topic signal for Mode 3 cards.
- Guardrails: minimum samples, insights cite n / period / uplift, ~10–20 % exploration budget.

### Phase 3 — scored ideas queue + GFlow video Mode 8
- Mode 8 uses the studio's `video_ideas` and `video_jobs` flow. Hermes proposes
  scored ideas through the scoped API; the planner fills one registered
  template with one LLM text call that writes the template variables, title,
  and caption. The worker then generates and uploads the video.
- A video uses one Flow account and one Flow project. Clip 1 is text-to-video;
  each later clip is image-to-video from the previous clip's last frame, with
  the same profile and project. Clips are joined with ffmpeg and published as
  Reels through the existing Mode 7 video path.
- The worker is the standalone `gflow_worker` package and uses `gflow-cli`
  only. It pulls jobs from the studio API over HTTP; it does not require
  inbound ports or use the multi-profile Flask/Chrome approach from
  `G-FLow_Automation_dekstop`. It can run on the Mac first and later on the
  VPS as the `gflow` user with `GFLOW_MAX_CONCURRENT=1` and a
  `GFLOW_MEM_RESERVE_MB=1536` memory reserve.
- Account selection follows Flow credits: credits minus the global reserve
  must cover the whole remaining video, and an account runs at most one video
  at a time. Failed clips retry on the same account; if it cannot continue,
  the whole video restarts from clip 1 on another account. Local and server
  checkpoints allow a job to resume without mixing generations.
- Free technical QC always runs. Vision QC is a global setting, off by
  default; when on, it is one contact-sheet vision call per video and fails
  closed. Each fanpage independently chooses Auto or Manual review. Mode 8 is
  disabled by default per fanpage.
- Hermes' weekly strategist step may submit at most two ideas per enabled
  fanpage. Each idea needs source-backed `research_notes`, a score, and a
  rationale; topics must not repeat, and narration is always English. The
  Hermes token uses `ideas:write`; the worker has a separate `gflow:worker`
  token.

### Phase 4 — closed loop
Expected vs actual engagement per idea/schedule (age-normalised via snapshots), small controlled
experiments (bandit-style exploration), guarded auto-optimisation within owner-approved limits.

## Compliance and risks
- Meta: label AI-generated video where required; unoriginal/reposted content can lose reach —
  prefer original Mode 3/8 content for pages Hermes optimises.
- Repliz rate limits are undocumented: ingestion is paced (≤ 100 calls per 5-minute tick,
  0.3 s apart) and stops on 429.
- Statistics endpoint response shape must be confirmed with one real call after the Gold
  upgrade; the parser is tolerant and the raw JSON is stored.

## Open question for the owner
1. Repliz Gold: when to buy (metrics start the moment it is active and the toggle is on)?

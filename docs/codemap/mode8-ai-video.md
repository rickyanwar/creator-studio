# Deep Dive D3: Mode 8 AI Video Generation Subsystem

Refreshed 2026-11-10 at main 8bc0f82.


Comprehensive architectural and implementation reference for Mode 8 (Google Flow AI Video generation via `gflow-cli`, Celery planning/dispatch/finalization, and publication to Facebook Reels).

---

## 1. Subsystem Architecture & End-to-End Pipeline

```
Idea Sources [Owner (API) | Hermes (API) | In-App Writer (Celery) | Muse (Research Desk API)]
   │
   ▼
VideoIdea (status: proposed / pilot / approved)
   │
   ▼ [app.tasks.ai_video_plan.plan_ai_videos]
   ├─► Check open_count < 2; stop only when daily target AND 20-day stock target are met
   ├─► Auto-approve proposed ideas if score >= ai_video_min_score
   ├─► Rotate templates: choose approved idea with least recently used template
   ├─► Fit clips to credit caps (ai_video_max_credits_per_video / per_day)
   ├─► Use matching prepared prompts when polish is enabled; otherwise fill_from_research / fill_idea
   └─► Insert VideoJob (status: queued), VideoIdea (status: queued)
          │
          ▼ [Worker Loop: python -m gflow_worker]
          ├─► POST /worker/gflow/heartbeat (sync accounts, credits, paused status)
          ├─► POST /worker/gflow/claim (pinned first, then fair-share & sticky account)
          │      └─► Job claimed: status=claimed, lease_until=now+20m, account pinned
          ├─► Generate Clips in sequence (profile & project locked):
          │      ├─► Clip 1: t2v prompt via gflow-cli
          │      ├─► POST /worker/gflow/jobs/{id}/progress (event=clip_done)
          │      ├─► Clip N>1: extract last_frame (offset 100ms) -> i2v prompt
          │      └─► POST /worker/gflow/jobs/{id}/progress (event=clip_done)
          ├─► Join & Media Assembly:
          │      ├─► concat with 100ms tail trim on intermediate clips
          │      └─► re-encode if > 40MB (target 38MB, hard ceiling 44MB)
          └─► POST /worker/gflow/jobs/{id}/complete (multipart mp4 + thumbnail jpg)
                 │
                 ▼ [app.tasks.ai_video_finalize.finalize_ai_video]
                 ├─► Technical QC (ffprobe, duration, aspect 9:16, black_s <= 2s, frozen_s <= 3s)
                 ├─► Vision QC (optional): 8-frame contact sheet -> LLM vision inspection
                 ├─► TTS mode: per-clip narrator -> fit windows -> duck/mix Flow audio; failure -> pending_review (§9)
                 ├─► Overlays: faster-whisper CPU ASR (or fallback silencedetect) -> ASS/PIL burning
                 ├─► Create PublishJob (ContentType.ai_video)
                 │      ├─► Passed QC & Auto: publish_job.delay(publish_job.id)
                 │      └─► Failed Vision / Manual: status = pending_review
                 └─► VideoJob status = done, VideoIdea status = done
```

---

## 2. Idea Lifecycle & Ingestion Sources

### 2.1 Sources and Ingestion Matrix

| Source | Ingestion Path | Initial Status | Validation & Limits | Reference |
| :--- | :--- | :--- | :--- | :--- |
| `owner` | `POST /ai-video/ideas` | `approved` | Clips must be in template options; immediate approval; no score required | `ai_video_ideas.py:71` |
| `hermes` | `POST /hermes/video-ideas` | `proposed` | Requires `score` (0-100) and `rationale`; max 30 proposed per page; 30d topic dedup | `ai_video_ideas.py:60` |
| `ai` (in-app) | `top_up_ai_video_ideas` (gallery queue; ChatGPT gpt-cli `/v1/text` first, 9Router Gemini Flash fallback) | `proposed` | Off when `ai_video_researcher == 'muse'`, except for pages with an empty bank and no Muse idea for `MUSE_DRY_HOURS` (6 h); batch <= 4; 30d topic avoidance (`used_topics`: all pages' non-cancelled job topics + non-rejected or duplicate-rejected ideas; prompt gets the newest 150, and a written idea `similar_subjects()` to any of them is dropped before insert); cap 2x daily per 24 h not counting `duplicate subject` rejections, hard cap 6x daily (2026-11-05) | `ai_video_plan.py:134` |
| `muse` | `POST /research/packs` | `pilot` or `approved` | Multi-channel pack; pilot mode holds as `pilot`; live mode auto-approves if score >= min | `research_desk.py:347` |

### 2.2 VideoIdea State Machine

Defined in `backend/app/models/ai_video.py:62` (`VideoIdea.status`).

| State | Meaning | Transitions In From | Transitions Out To | Driven By (path:line) |
| :--- | :--- | :--- | :--- | :--- |
| `proposed` | Awaiting review or planner score threshold | Initial (`hermes`, `ai`, live low-score `muse`) | `approved`, `rejected`, `failed` | `ai_video_ideas.py:78` |
| `pilot` | Muse research pack variant awaiting manual review | Initial (`muse` in pilot mode) | `approved`, `rejected` | `research_desk.py:348` |
| `approved` | Cleared for production; eligible for planning | `owner` initial, auto-approval, manual approve | `queued`, `failed` | `ai_video_plan.py:37`, `ai_video_ideas.py:89` |
| `queued` | Claimed by planner; VideoJob created | `approved` | `done`, `failed` | `ai_video_plan.py:110` |
| `done` | Video successfully produced and finalized | `queued` | _Terminal_ | `ai_video_finalize.py:129, 146` |
| `failed` | Generation aborted or budget exceeded | `approved`, `queued` | _Terminal_ | `ai_video_plan.py:85, 115`, `ai_video_finalize.py:68` |
| `rejected` | Discarded manually or via review feedback | `proposed`, `pilot` | _Terminal_ | `ai_video_ideas.py:98`, `research_desk.py:953` |

### 2.3 Auto-Approval and Topic Guard Rules
- **Planner Auto-Approval**: In `ai_video_plan.py:36-37`, proposed ideas with `score >= fanpage.ai_video_min_score` transition to `status="approved"`, `decided_by="auto"`, `decided_at=utcnow`.
- **Global 30-Day Topic Dedup**: In `ai_video_ideas.py:65-70`, `_normalized_topic(topic)` strips punctuation/whitespace and enforces a 30-day cross-fanpage uniqueness window against non-rejected, non-pilot ideas.
- **Muse Saturated Topics**: In `research_desk.py:473`, topics used across >= 10 fanpages (`MAX_CHANNELS_PER_PACK`) are marked saturated and rejected.
- **Cross-Variant Anti-Collusion**: In `research_desk.py:813`, Muse variants in the same pack cannot share titles or have narration string similarity ratio >= 0.85 via `difflib.SequenceMatcher`.
- **Real Name Blacklist**: In `research_desk.py:785, 794, 804`, `find_real_names()` strips real constructors, sponsors, drivers, and governing bodies from title, caption, and slot variables.

---

## 3. Template System, Length Math, and Credit Budgets

### 3.1 Registry & Template Data Model
- Templates loaded from `backend/app/video_templates/*.json` and merged with `_shared_blocks.json` via `@lru_cache(maxsize=1)` (`video_templates.py:60`).
- Registry entry points: `backend/app/services/video_templates.py:list_templates`, `get_template`, `render_clips`, `clip_templates_for`.
- Templates include `motorsport_tech_explainer`, `motorsport_corner_masterclass`, `motorsport_legend_innovation`, `motorsport_tech_dive`, `motorcycle_commercial`, `health_3d_explainer`.
- Core constants: `model="omni-flash"`, `aspect="9:16"`, `clip_seconds=10`, `credits_per_clip=15`.
- `backend/app/video_templates/health_3d_explainer.json`: `[1, 2, 3, 4, 5, 6]` clips, default 4; no vehicle; independent t2v clips, each with two shots and one cut. Pink/salmon medical animation, research-backed facts, no personal medical advice. The name says three cuts, but the rendered prompts request two shots.
- `backend/app/video_templates/motorcycle_commercial.json`: exactly 3 clips (30 s / 45 credits), t2v then two i2v continuations. Timed commercial shots of one real bike/year through 2026; real sponsor lettering and race number preserved. `polish_fallback="render"`; Flow narration budget 18–22 English words, TTS 14–16. Narration engine is selected by page settings despite the JSON generation metadata saying never external TTS.
- `backend/app/video_templates/motorsport_tech_dive.json`: exactly 1 t2v clip (10 s / 15 credits); braking hook 0–2 s, locked transparent mechanical reveal 2–7 s, whole-vehicle exit 7–10 s. Two deliberate cuts, 26–32 English narration words, original livery and page lettering; `polish_fallback="render"`. Single-clip jobs always use Flow voice.
- **Templates without a vehicle** (`"uses_vehicle": false`, `video_templates.uses_vehicle`): no `vehicle_preset`, livery or lettering. `render_clips` → `_render_plain`: style + audio + one identical narrator sentence in every clip, slots `visual_subject` + `shot_<n>a/b` + `narration_<n>`, every clip `t2v` (two shots with one clean cut per clip, no frame chaining). Prompts over `MAX_PROMPT_CHARS` (2900) fail, because Flow refuses ~3,000+ characters.
- **Niche** (`video_templates.fanpage_niche`, shared by fill, idea writer and research desk): `motogp` from gallery niches / name; else a non-motorsport niche from the page's templates (all health → `health`) or name; else `f1`.

### 3.2 Template Rotation Math
In `ai_video_plan.py:62-77`, the planner prevents template fatigue on a fanpage:
1. Queries `VideoJob.template_key` ordered by `created_at DESC` to compute `last_used_map[template_key]`.
2. Selects the candidate idea minimizing `(last_used_map.get(idea.template_key, min_dt), -(idea.score or 0), idea.created_at)`.
3. Ensures least-recently-used template runs first; ties broken by highest score, then oldest idea.

### 3.3 Clip Selection & Credit Fitting Algorithm
`video_templates.py:249-289`:
- **Owner Override**: If `idea.source == 'owner'` and `idea.clips in template.clip_options`, owner choice is honored (`ai_video_plan.py:81`).
- **Fanpage Seconds Range**: If `ai_video_seconds_min` / `ai_video_seconds_max` set, converts to allowable clip counts `[c for c in options if lo_s <= c * 10 <= hi_s]`.
- **Credits Fit**: `fit_clips_to_credits(template, clips, budget)` finds largest `c <= clips` where `c * 15 <= budget`.
- **Hard Rejection**: If `fanpage.ai_video_max_credits_per_video` cannot cover the minimum clip count (`credits_estimate(template, min(clip_options))`), the idea fails permanently (`ai_video_plan.py:85-88`).
- **Daily Budget Exhaustion**: If `per_day` budget minus spent today cannot cover minimum clips, idea stays `approved` and waits for midnight reset (`ai_video_plan.py:102`).

### 3.4 Variable Filling & Look Injection
- **Clip Sets (`clip_sets`, `clip_templates_for`)**: Templates can define dedicated full-story clip lists per length in `clip_sets` (`"1"`, `"2"`). `clip_templates_for(t, clips)` returns the dedicated set when present, falling back to `clip_templates[:clips]`.
- **Length matching in `research_ready`**: Muse research ideas are only rendered directly via `fill_from_research` when `idea.clips is None or idea.clips == clips`; length mismatches route through `fill_idea` (LLM) to write a complete story for the chosen length.
- **Muse Bypass (`fill_from_research`)**: If `research_ready()`, idea already contains complete variables, title, and caption. Skips LLM generation entirely. Injects `GENERIC_LOCK` for `moving_part` and `fixed_parts` if missing, only when the template uses those slots.
- **LLM Filling (`fill_idea`)**: Calls 9Router via `news_copywriter._generate_with_fallback`. Vehicle templates: mechanical rules (`MECHANICAL_ACCURACY`), vehicle presets, mechanism references. Templates without a vehicle: `NICHE_ACCURACY[niche]` (health: anatomy, mainstream mechanisms, one subject, shots match words, no advice/dosage) and "never name real brands, companies or products". Both get language word budgets. Budget (`video_templates.get_narration_budget`): template `narration_words` x language factor; only ASMR (text on screen) is x0.8; Google TTS uses the clip's `narration_words_tts` when the template has one (motorcycle_commercial 14-16: the slow GP Racing voice fits ~15 words in the 9.25 s window), else the template budget. Accepted minimum = target low - 2 for TTS (`accepted_min`), - 6 for Flow/ASMR, so TTS clips keep little silence (owner 2026-11-05; TTS mixer speeds up <=1.12x then shortens). Research desk tells Muse and checks Muse packs with the page's `prompt_engine_for` budget.
- **Muse research desk**: niches `f1`, `motogp`, `health`. A niche in `research_desk.NICHE_GUIDES` (health) gets a `guide` in the brief that replaces the top-level studio, idea sources, global rules, research defaults and motion check. Health variants must not send `vehicle_preset` / `team_livery`.
- **Studio Look Resolution (`apply_system_look`, `ai_video_fill.py:136`)**:
  - `narrator_preset`: Fanpage timezone `Australia/*` -> `EN-AUSSIE-ENG`; `America/*` -> `EN-US-ENG`; else `fanpage.id % 4` of `["EN-PADDOCK", "EN-SCOT", "EN-JOURNO-F", "EN-DOC"]`.
  - `livery`: `pick_livery(db, fanpage_id, template)` inspects past 50 jobs, picks unused livery or least recently used.
  - `channel_name`: `sanitize_channel_name(fanpage.name)` uppercased, alphanumeric/spaces only, truncated to 24 chars.

### 3.5 Prompt Polish Engine Order (owner 2026-11-02)
- **Engine Order** (`prompt_polish.py`, `ai_video_prepare.py`): ChatGPT first (`POST /v1/text` on `gpt serve` with thinking `high`) -> 9Router Codex (`cx/gpt-6.1-sol`, `cx/gpt-6-sol`, skipped while the Codex budget rests) -> Argon (`gemini-web-serve`) last.
  ChatGPT is skipped only when gptcli is down for a non-image reason; the image cap (soft_cap/quota) does not block polish or the idea writer (gptcli_text_unavailable_until).
- **Kill Switch**: Set `GPT_SERVE_TEXT_DAILY_CAP=0` in `/etc/gpt-serve.env` on the VPS to disable the ChatGPT text endpoint.
- `backend/app/services/prompt_polish.py:_ask`, `polish_clips`, `repolish_clip`: fallback is per request. `chatgpt_sleeping` means HTTP 503 or upstream error type `sleeping` from the text endpoint; `codex_resting` means the Redis rest deadline is active or usage reaches `codex_rest_pct` (default 95%). Neither means Argon answered: later engines are still tried. Argon requests `/v1/generate` with `extended_thinking=True` and a 900 s timeout.
- `polish_clips` validates up to three answers: exact quoted text, clip indexes/order, i2v prefix, timed shots, protected restrictions, shared voice/ambience and motorsport motion sentence, with a 2,600-character ceiling. Availability failure after an answer raises `PolishInterrupted` for resumable feedback.
- **Actual engine**: `backend/app/services/ai_video_prepare.py:prepare_idea` stores the answering account in `video_ideas.prepared["account"]`: `chatgpt:<profile>`, `codex:<model>`, or Argon's account. `backend/app/api/prompt_polish.py:polish_engine` maps those prefixes to `chatgpt` / `codex`, other nonempty accounts to `argon`, missing accounts to null.

### 3.6 Early Preparation & Health
- `backend/app/services/ai_video_prepare.py:pick_run_ideas`, `candidate_ideas`, `prepare_idea`: prepare at most three ideas per run round-robin, prioritizing pages below today's production target without a matching prepared idea. Ahead target is `max(2, ai_video_daily_count)`; failed rounds wait 60 min. Payload matching checks fingerprint, version, template, clip count and narration prompt engine.
- `backend/app/tasks/ai_video_prepare.py:prepare_ai_video_ideas`: `gallery` queue, every 120 s (`expires=110`), UUID-owned Redis lock `ai_video_prepare:lock` for 3,600 s. Active eligible pages must enable AI video and prompt polish. Redis failure permits the run. Slow polish runs outside the read transaction.
- Interrupted drafts expire after 6 h. Three completed failed rounds fail an idea unless its template permits rendered fallback. `motorcycle_commercial` and `motorsport_tech_dive` accept original rendered clips with `render_fallback=True`; planner waits for matching preparation when polish is enabled.
- `backend/app/services/prompt_polish_health.py:record_run`, `snapshot`: Redis last run (24 h) and last success (7 d), Argon `/health`, prepared/waiting/failed counts, and brands starving for 60 min. It still measures Argon service health even with ChatGPT/Codex answering.
- `backend/app/api/prompt_polish.py`: authenticated `GET /prompt-polish/health`, `/ideas/{idea_id}`, `/jobs/{job_id}`, `/ideas-state`, `/ideas-engine` (bulk maximum 200 ids). Job clips with `prompt_original` override fingerprint validity for the polished state; engine metadata still comes from the idea's prepared account.
- `backend/app/tasks/prompt_polish_watchdog.py:check_prompt_polish`: every 600 s (`expires=590`), maintains `OperationalNotification` key `gemini_web:serve`; owner-action problems alert immediately once per problem/day. Other problems escalate after 90 min, deduped for 6 h; recovery notifies only after an alert. `_send_notification` uses Hermes with Telegram fallback.
- `backend/app/scripts/gemini_web_health.py:main`: prints `snapshot` as JSON or `--pretty` table; exit 2 on snapshot failure. This is an operator entry point, not a separate health engine.

---

## 4. Job State Machine, Lease Protocols, and Sweep Engine

### 4.1 VideoJob State Transitions

Defined in `backend/app/models/ai_video.py:88` (`VideoJob.status`).

| Status | Meaning | Leased? | Transitions In | Transitions Out | Triggered At |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `queued` | Ready for worker claim; optionally pinned to account | No | Planner insert, unpin, retry, requeue, sweep expire | `claimed`, `cancelled`, `failed` | `ai_video_plan.py:109`, `ai_video_dispatch.py:409, 521` |
| `claimed` | Claimed by worker; project or clip 1 starting | Yes (20m) | `queued` | `generating`, `queued` (requeue/expire), `failed`, `cancelled` | `ai_video_dispatch.py:242` |
| `generating`| At least clip 1 recorded in `clips_done` | Yes (20m) | `claimed` | `uploaded`, `queued` (stale sweep), `failed`, `cancelled` | `ai_video_dispatch.py:357` |
| `uploaded` | MP4 and thumbnail uploaded; ready for finalize QC | No | `generating`, `claimed` | `done`, `failed` | `ai_video_dispatch.py:639` |
| `done` | Passed QC and publish job queued | No | `uploaded` | _Terminal_ | `ai_video_finalize.py:127, 144` |
| `failed` | Policy violation, unrecoverable error, restarts exhausted | No | `claimed`, `generating`, `uploaded` | `queued` (via manual retry) | `ai_video_dispatch.py:428, 451`, `ai_video_finalize.py:66` |
| `cancelled` | Administratively stopped via API | No | `queued`, `claimed`, `generating` | `queued` (via manual retry) | `ai_video_dispatch.py:669` |

### 4.2 Distributed Claim Protocol (`claim_job`)
Implemented in `ai_video_dispatch.py:307-550`:
1. **Worker Pause Check**: Fails if `GflowWorker.paused_until > now`. After claiming, sets a per-account 8-20 min `cooldown_until` plus a 60-240 s worker pause (`WORKER_GAP_SECONDS = (60, 240)`).
2. **Account Candidate Pool**: Accounts for worker with `enabled=True`, `authenticated=True`, `status="ok"`, credits known, and not present in any other `claimed` or `generating` job (`get_candidate_accounts`, line 67). Accounts with status `needs_confirm`, `needs_login`, `needs_terms`, `auth_error` are excluded.
3. **Step 1 - Pinned Jobs**: Jobs in `queued` with non-null `account_id` matching an available candidate and `not_before <= now`.
   - Remaining cost = `(len(clips) - len(clips_done)) * credits_per_clip`.
   - `ai_video_dispatch.effective_credits(db, acc)`: the credits minus the clips made on this account since `credits_checked_at`.
   - Selected if `effective_credits - credit_reserve >= remaining_cost`.
   - A pinned job whose account cannot pay for the remaining clips is restarted on another account (`generation += 1`, `clips_done = []`, `account_id = None`, `status = "queued"`; `restarts` is not incremented).
4. **Step 2 - Unpinned Jobs (Fair Share)**:
   - Sorted by stock-heavy flag (`stock >= daily_count * STOCK_PRIORITY_DAYS`), starts today (WIB), oldest previous start, then job age. Group-only jobs are excluded from stock and page-start counts.
5. **Step 3 - Sticky Account Selection (`acc_sort_key`)**:
   - Filters candidates where `effective_credits - credit_reserve >= credits_estimate`.
   - Sort key: `(0, -last_used_at.timestamp(), -credits)` for previously used accounts; `(1, 0.0, -credits)` for fresh accounts.
   - The sticky account choice is intentional (Google flags hosts that hop between accounts).
6. **Step 4 - Concurrency Lock**:
   - `SELECT ... FOR UPDATE SKIP LOCKED` on `VideoJob` row.
   - Sets `status="claimed"`, `lease_until=now + 20m`, `worker_id=W`, `account_id=acc.id`, `last_progress_at=now`.

### 4.3 Error Handling & Restart Backoff (`record_failure`)
`ai_video_dispatch.py:716-880`:

| Failure Reason | Worker Condition | Action on Job & Account |
| :--- | :--- | :--- |
| `unusual_activity` / no submit reply | Flow anti-bot trigger, submit timeout ("of clicking submit") | Increments `unusual_hits`. Sets escalating account cooldown by strike within 24h (1st: 60-120m, 2nd: 120-180m, 3rd+: 180-240m) subject to share guard (max 40% cooling accounts per worker). Job requeues with backoff (30m, 60m, 120m * 0.85..1.25) before next account; restarts if `clips_done`. Circuit breaker pauses worker 30-60m if 3 distinct accounts hit within 30m or share guard trips. Fails permanently if `unusual_hits > 3`. |
| `agent_only` | Flow marks account as agent-only | Persists `account.agent_only=True`, sets 2–3 h cooldown. Restarts if clips exist, otherwise requeues unpinned; no clip retry increment. Future claims skip i2v jobs on this account but allow all-t2v jobs. |
| `usage_limit` | Daily generation limit hit | Sets 6–8 h cooldown. Also records `agent_only` if the message identifies that composer. Restarts if clips exist, otherwise requeues unpinned; no clip retry increment. |
| `identity_check` | Google identity check required (Flow `/about` page) | Flags account `status="needs_confirm"`, sets `session_flagged_at=now`, `authenticated=False`, queues owner alert, and requeues job unpinned like `out_of_credits` (no clip attempt increment, no strike). |
| `policy` | Safety / NSFW / RAI block | Fails job and idea immediately (`status="failed"`). Exception (2026-11-05): the first refusal of a `motorcycle_commercial` job (`ai_video_brand_safe.BRAND_SAFE_TEMPLATES`) restarts it with `not_before` +2 h and enqueues `rewrite_job_brand_safe` (gallery queue): Flash rewrites the clip prompts without brand names (quotes, digits kept), marks clips `brand_safe`, clears `not_before`; a rewrite failure or a second refusal fails job + idea. |
| `out_of_credits` | Balance 0 or exit code 37 | Sets `account.credits = 0`, requeues job unpinned (`status="queued"`). |
| `auth` | Session expired (exit 8, 12, 14, 38, 39) | Sets `account.status="auth_error"`, `authenticated=False`, requeues job unpinned. |
| `lost_checkpoint` | Local file missing vs server `clips_done` | Triggers `_restart()`: increments `generation`, `restarts+=1`, clears `clips_done`, unpins account. Fails if `restarts > 2`. |
| `upload_terms` | Flow upload terms dialog | Sets `account.status="needs_terms"`, triggers `_restart()` on another account. |
| `transient` / `other` | Network glitch, timeout, UI drift | Increments `clip_attempts`. With finished clips: five retries at `(2, 5, 10, 20, 30)` min, preserving account/project/generation and checkpoints. Without clips: three retries at `(2, 5, 10)` min. Exhaustion restarts; each accepted new clip resets the counter. |

- Brand-safe entry points: `backend/app/services/ai_video_brand_safe.py:rewrite_brand_safe` and `backend/app/tasks/ai_video_brand_safe.py:rewrite_job_brand_safe` (`gallery`). Only `BRAND_SAFE_TEMPLATES={"motorcycle_commercial"}` qualify. Text-model fallback replaces names outside quotes with visual descriptions; code checks prompt count, exact quoted strings, retained digits and length (60% of original through `MAX_PROMPT_CHARS`). Success marks clips `brand_safe`; task failure fails the job and idea. The task's slow rewrite currently retains its DB transaction.

### 4.4 Session Keep-Alive & Account Status Rules
Implemented in `ai_video_dispatch.py:1115-1215` and `api/gflow_worker.py:router.post("/session")`:
- **New Account Statuses**:
  - `needs_confirm` — Google asks to confirm identity (Flow `/about` page). Set by session check (`public_landing`) or job fail (`identity_check`).
  - `needs_login` — Saved session is signed out or expired. Set by session check (`no_session`, `google_session_only`, `profile_marker_missing`, `cookie_store_unreadable`). Other non-success results (`labs_stale`, `labs_unverified`, `verification_error`, `launch_error`, `in_use`, unknown) are transient: no status change, no alert, retried in 30–90 min.
- **Heartbeat Protection (`record_heartbeat`)**:
  - Accounts in `needs_terms`, `needs_confirm`, and `needs_login` are protected: worker heartbeats never reset their status to `ok` and never set `authenticated=True`.
  - Only a session check returning `authenticated` (gflow-cli 0.92 success; legacy `renewed` also accepted) resets `needs_confirm`, `needs_login`, or `auth_error` back to `status="ok"` and `authenticated=True`.
- **3-in-1-Hour Worker Auto-Pause**:
  - If >= 3 accounts on the same worker have `status in ("needs_confirm", "needs_login")` with `session_flagged_at >= now - 1h`, `worker.paused_until = max(paused_until or now, now + 6h)`.
  - Queues an owner alert via Celery task `app.tasks.ai_video_alerts.send_gflow_alert` with dedupe key `gflow_session_pause:{worker_id}:{now:%Y%m%d}` (at most once per day).
- **Single Account Alerts**:
   - First transition to `needs_confirm` or `needs_login` queues an owner alert with dedupe key `gflow_session:{account.id}:{status}:{now:%Y%m%d%H%M}`. Duplicate checks in the same status do not re-alert.
- `backend/app/tasks/ai_video_alerts.py:send_gflow_alert` opens a DB session and delegates to the watchdog's `_send_notification` (Hermes, then Telegram fallback).

### 4.5 Sweep Engine (`sweep`)
`ai_video_dispatch.py:886-980`:
- **Expired Active Leases**: `status in ('claimed', 'generating')` with `lease_until < now` reset to `status="queued"`, `worker_id=None`, `lease_until=None`. Pins and `clips_done` preserved.
- **Empty Checkpoint Unpinning**: `status="queued"` with `clips_done == []` has `account_id=None`, `flow_project_id=None` cleared to allow other accounts to claim.
- **Stale Pinned Rescue**: `status="queued"` with non-empty `clips_done` and `last_progress_at < now - 12h` (`PIN_MAX_HOURS = 12`) whose pinned account is no longer a candidate or lacks credits triggers `_restart()` (increments generation, clears clips, unpins).

---

## 5. Worker Loop, Concurrency, and Proxy Guard

### 5.1 Main Loop & Resource Governance
- Configured via `gflow_worker/config.py`: `GFLOW_MAX_CONCURRENT` default 1 (clamped to 1..2, line 70), `GFLOW_MEM_RESERVE_MB=1536`, `GFLOW_CHROME_PEAK_MB=1200`.
- Memory check (`gflow_worker/memory.py:23`): `can_start = running < max_concurrent and (avail - 1200 >= 1536)`. Reads Linux `/proc/meminfo` (`MemAvailable`) or macOS `vm_stat`.
- Main poll loop in `Worker.tick` (`gflow_worker/main.py:124`):
  1. Purges finished jobs from `self.jobs`.
  2. Runs account credits refresh (24 h ± 4 h) only when `jobs` is empty (prevents opening extra Chrome on active profiles).
   3. Probes unreadable credit accounts via Playwright headless browser (`probe_credits.py`), in a background refresh thread.
  4. Transmits `POST /heartbeat` with version, memory, running jobs, account states.
  5. Sends `progress(event="alive")` for active jobs to extend lease.
   6. Claims new jobs while `accept_jobs` is true and memory allows.
- `backend/app/api/gflow_worker.py:HeartbeatRequest.phase` accepts `dict | str | None`; credit refresh sends `{"kind":"checking_credits","done":n,"total":m}`. `record_heartbeat` stores it in `gflow_workers.phase`. Heartbeats continue while background checks run.
- `gflow_worker/main.py:Worker._run_keepalive`, `is_in_keepalive_window`: idle-only `auth renew` under profile/global locks, enabled by `GFLOW_KEEPALIVE_ENABLED` (default 1). Defaults: every 8–16 h/profile, 20–90 min between profiles, new profiles 10–60 min, window `07:00-22:00` in `Asia/Jakarta`; schedule persists in `keepalive.json`. A first schedule spreads profiles from 30 min to 16 h. `authenticated` / legacy `renewed` refresh credits and post `/session`; transient results retry in 30–90 min without flagging the account.

### 5.2 Locking Hierarchy (Flock Protocol)
Implemented in `gflow_worker/lock.py:32-60`:
1. **Global Shared Lock (`fcntl.LOCK_SH`)**: Acquired on `GFLOW_LOCK_FILE` (`/tmp/gflow-flow.lock`). Allows up to 2 concurrent worker jobs while completely locking out Hermes `flow.sh` which takes `fcntl.LOCK_EX`.
2. **Profile Exclusive Lock (`fcntl.LOCK_EX`)**: Acquired on `<lock_dir>/gflow-flow.<profile>.lock`. Ensures no two processes or probe scripts manipulate the same Chrome user profile directory concurrently.
3. **Strict Lock Order**: Global shared first, profile exclusive second. Releases in reverse order. Prevents deadlocks.

### 5.3 Fail-Closed Proxy Guard
Implemented in `gflow_worker/proxy_guard.py`:
- `is_proxy_reachable(proxy_url, timeout=2.0)` tests TCP connect to host and SOCKS5 port (1080) (`proxy_guard.py:12`).
- Auto-heal routine (`revive_phone_proxy`): connects to the configured phone ADB target (`<redacted>`), sleeps 1 s, starts `hev.socks5/.Socks5Service` with action `hev.socks5.START`.
- Fail-Closed Gate (`ensure_proxy_ready`, line 51): If proxy unreachable, attempts auto-heal once. If still unreachable, raises `RuntimeError` (`proxy_guard.py:76`). Prevents launching gflow or probes, shielding residential accounts from datacenter IP exposure.
- The CLI env carries the proxy as `GFLOW_PROXY` / `GFLOW_SOCKS5_PROXY` / `ALL_PROXY` / `HTTP(S)_PROXY` (read by the hand-patched gflow-cli 0.86.0 at `/home/gflow/gflow-cli-0832`) and `GFLOW_CLI_BROWSER_PROXY` (read natively by gflow-cli >= 0.88; v0.92.1 is installed side by side at `/home/gflow/gflow-cli-0921`, no patches needed) (`gflow_worker/gflow.py` `run`, `list_videos`, `recover_from_project`).

### 5.4 Playwright UI Credit Probe
Implemented in `gflow_worker/probe_credits.py`:
- Launches headless Chromium with persistent user profile directory (`probe_credits.py:76`).
- Navigates to `flow.google.com/project/{project_id}`.
- Dismisses overlays (close buttons, onboarding dialogs like "Got it", "Mengerti", "Start Creating").
- Regex extraction (`CREDITS: <number>`) supports English ("credits") and Indonesian ("kredit"), explicitly skipping non-balance phrases like "refund credits" (`probe_credits.py:31`).
- Transient toleration: If probe returns unknown but previous balance existed, tolerates 1 failure before clearing credits in heartbeat (`gflow_worker/main.py:86-93`).
- `gflow_worker/runner.py:JobRunner._fail` marks `credits_stale` plus the failing profile after a no-reply submit. Next idle tick refreshes only stale profiles, skipping profiles probed within 2 h; unknown profiles trigger a full refresh.

### 5.5 Billed Clip Recovery
- `gflow_worker/runner.py:JobRunner._recover_clip`: submit-no-reply, missing signed URL, signed-URL HTTP errors (including 302), and CLI poll timeout trigger download recovery before reporting failure. Extract media id from payload/error/asset/workflow id or message, then call `data download` for a fresh link without regenerating.
- Stranded candidates are retried every 30 s for 240 s, or 600 s for `was not terminal within the poll timeout` / `it may still finish`. Pending catalog status does not prevent a download attempt. Cancellation is checked throughout.
- Final recovery uses `gflow_worker/gflow.py:GflowRunner.recover_from_project` and `gflow_worker/project_recover.py:collect_listing`, `parse_zzl0ze_entries`, `filter_candidates`, `main`: read the Flow project `Zzl0ze` listing with up to four page loads (20 s wait, 3 s between loads), select newest entries created since submit minus 90 s, exclude checkpoint media ids, then download candidates.
- `gflow_worker/runner.py:JobRunner._generate_clip`: pre-submit exit codes 9/23 get one in-process retry after 45–120 s; an i2v upload rejection without upload terms retries with a seed 200 ms earlier. Successful files are checkpointed before progress reporting; transient Studio errors preserve local state.

---

## 6. Clip Continuity, Media Assembly, and Size Controls

### 6.1 Inter-Clip Visual Continuity
`gflow_worker/runner.py:99-106`:
- Clip 1: text-to-video (`t2v`) with template prompt.
- Clip N > 1: image-to-video (`i2v`) using `Media.last_frame(clip_prev, seed_png, offset_ms=100)`, unless the clip's `mode` is `t2v` (cut-style templates such as health): then it is its own t2v generation.
- `Media.last_frame` (`media.py:20-33`): Runs `ffmpeg -sseof -0.1 -i clip.mp4 -frames:v 1 seed.png`. Fallback to positive seek if `-sseof` fails.

### 6.1a Per-Clip Logic QC (brand switch `ai_video_clip_qc_enabled`, default off; owner 2026-10-24)
- Setting: `TargetFanpage.ai_video_clip_qc_max_attempts` (integer, default 4, range 1–10). Count is initial-generation-inclusive (1 initial try + up to N-1 retries).
- First-claim freeze semantics: At first claim in `claim_job`, `VideoJob.clip_qc_max_attempts` snapshots the fanpage limit atomically before `started_at`. Running/reclaimed jobs keep their frozen limit; legacy jobs with null snapshot default to 4 (`max_attempts_for_job`).
- Claim payload carries `clip_qc` + `clip_qc_max_attempts`.
- Worker (`runner.py` `_qc_clip`, state in `state.data["qc"][index]` for resume): reads `max_attempts = job.get("clip_qc_max_attempts", 4)`. Generate attempt -> `POST jobs/{id}/clips/{index}/qc` (multipart clip) -> poll `qc-status` every `clip_qc_poll_seconds` (20 s). States: `pending`, `pass`, `retry` (new prompt, same seed frame), `use_attempt` (best attempt after reaching limit), `failed` (QC unavailable for 6 h); an unknown state fails the job. Only the accepted attempt becomes the checkpoint / `clip_done`, so the next i2v clip seeds from it.
- Server upload & task enforcement:
  - Upload endpoint `POST /worker/gflow/jobs/{id}/clips/{index}/qc` validates `1 <= attempt <= max_attempts_for_job(job)` (422 if out of range).
  - Entry points: `backend/app/services/ai_video_clip_qc.py:logic_check`, `best_attempt`, `accept_attempt`, `clip_qc_history`; `backend/app/tasks/ai_video_clip_qc.py:check_clip`. Rows in `video_clip_qc` are unique per job/generation/clip/attempt. The task runs on `gallery`, claims the row (`processing_until`, 40 min), then uses an 8-frame contact sheet plus the previous accepted clip's last frame. `run_vision_check` retries vision availability/parse failures; a clip passes with no major issue.
  - `contact_sheet_times`: eight samples from 0.5 s through duration minus 0.5 s, listed in the review prompt. `SHOT_RULES` judges direction, shape and parts within one timed shot; across cuts, only vehicle/livery identity. Requested X-ray, transparent cutaway, exploded/float/reassembly effects are intended. Camera position, angle, distance and framing are never errors, even when different from the prompt.
  - On pass: accepts immediately (verdict `pass`), marking clip as accepted.
  - On failed logic check: below the cap, re-polish only that clip (`prompt_polish.repolish_clip`, ChatGPT -> Codex -> Argon, quotes/i2v prefix/2,600 chars validated), re-check job status/generation and return `retry`. Missing polish config or invalid rewrite reuses the old prompt; upstream unavailability waits. At the cap (or speech-triggered early stop), `best_attempt` minimizes `(major_count, minor_count, attempt)`, marks `accepted_best=True`, and returns `use_attempt`.
  - Vision or re-polish unavailable -> re-queue every 120 s, verdict `failed` after 6 h. `qc-status` re-queues a lost task after 15 min.
- Credits & manual review: Attempt >= 2 is charged at upload ((N-1) * credits_per_clip). If any clip was accepted via `accepted_best` (failed all attempts up to limit), `finalize_ai_video` sets `publish_status = PublishJobStatus.pending_review` with reason `kept the best of {attempts} attempts ({first_problem})`. QC clip copies (`job{id}_g{gen}_c{clip}_a{attempt}.mp4`) and the 8-frame contact sheet QC looked at (`..._sheet.jpg`, saved by `logic_check`) live in `media/ai_videos/clip_qc/`. They are kept for the QC history (`GET /ai-video/jobs/{id}/clip-qc`, "Show QC history" on the AI Video job card; the job list carries `clip_qc_summary`) and deleted after 14 days by `cleanup.cleanup_old_clip_qc_files` (daily 04:15 WIB), except while the video is still in progress or its post waits for review (owner 2026-11-02).

### 6.2 Concat Assembly & Frozen Frame Removal
`gflow_worker/media.py:35-63`:
- Trims trailing 100ms (`trim_tail_ms=100`) from preceding clips (`trim=end=duration-0.1`). Eliminates static/frozen last frames at clip boundary transitions.
- Audio synthesis: If a clip has no audio stream, generates stereo silence via `anullsrc=r=48000:cl=stereo` matching exact trimmed duration (`media.py:49`).
- Encodes output using `libx264`, `crf=18`, `preset=medium`, `pix_fmt=yuv420p`, `aac` audio at 192k, and `+faststart`.

### 6.3 Video Re-Encoding Size Guard
`gflow_worker/media.py:69-78`:
- If joined video exceeds 40 MB, triggers `reencode_for_upload`.
- Target size: 38 MB (`target_bytes = 38 * 1024 * 1024`).
- Calculates dynamic video bitrate: `video_bitrate = max(500_000, int(target_bytes * 8 / duration) - 128_000)`.
- Re-encodes with `-b:v bitrate -maxrate bitrate -bufsize bitrate*2 -b:a 128k`.
- Worker aborts with `other: video too large after re-encode` if still > 44 MB (`runner.py:130`). Server rejects uploads > 45 MB (`gflow_worker.py:176`).

---

## 7. Quality Control Pipeline

### 7.1 Technical QC Checks & Thresholds
Implemented in `backend/app/services/ai_video_qc.py:37-76` (`technical_check`):

| Check Metric | Command / Probe Filter | Pass Threshold | Failure Reason |
| :--- | :--- | :--- | :--- |
| Video Stream | `ffprobe -show_streams` | Codec type video present | `"no video stream"` |
| Audio Stream | `ffprobe -show_streams` | Codec type audio present | `"no audio stream"` |
| Duration | `ffprobe -show_format` | `clips*clip_sec - 3 <= dur <= clips*clip_sec + 2` | `"duration outside expected range"` |
| Aspect Ratio | Video stream `width`, `height` | `height > width` and `abs(w/h - 0.5625) <= 0.02` | `"wrong aspect ratio"` |
| File Size | `stat().st_size` | `>= 204,800 bytes` (200 KB) | `"file too small"` |
| Black Frames | `ffmpeg -vf blackdetect=d=0.5:pix_th=0.10` | Cumulative black duration `<= 2.0s` | `"too much black video"` |
| Frozen Video | `ffmpeg -vf freezedetect=n=0.003:d=1.5` | Cumulative frozen duration `<= 3.0s` | `"too much frozen video"` |

If any check fails, `job.status="failed"`, `job.error="; ".join(reasons)`, and idea fails (`ai_video_finalize.py:66-70`).

### 7.2 Vision Contact Sheet QC
Implemented in `ai_video_qc.py:79-174`:
- Controlled by global `Settings.ai_video_vision_qc_enabled` and per-page `qa_enabled(fanpage, "ai_video")`; migration `c80000000024` changed the global default to on. Technical QC is always run.
- Generates 8-frame contact sheet (`contact_sheet`): extracts 8 frames evenly spaced from `t=0.5s` to `duration-0.5s`, scales width to 320px, pastes into 4x2 grid image, exports JPEG quality 85.
- Vision prompt: Injects topic expectations (`part`, `mechanism_action`, `final_state`, `moving_part`, `fixed_parts`, `parts_list`, `channel_name`).
- Strict rejection criteria:
  - Rigid parts bend, twist, melt or tilt unnaturally.
  - Shown part or motion contradicts expected mechanism.
  - Fixed parts move or tilt.
  - Exploded view missing when requested.
  - Presence of official logos: F1, Formula 1, FIA, MotoGP, FIM, Dorna, NBA, UFC, NFL, FIFA, UEFA.
  - Garbled text, corner watermarks, vehicle color changes across frames, NSFW.
- Fail-closed behavior: Any LLM error, missing response, or JSON decode failure returns `passed=False` (`ai_video_qc.py:173`).
- Demotion on Vision Fail: In `ai_video_finalize.py:74`, passed technical QC + failed vision QC routes the post to `PublishJobStatus.pending_review` instead of failing the job.

---

## 8. Overlay Branding, Whisper ASR, and Publish Dispatch

### 8.1 Word Timings & Transcription
Implemented in `backend/app/services/speech_words.py`:
- Real Voice ASR: `transcribe_words` uses `faster_whisper.WhisperModel` on CPU (`int8`, `cpu_threads=2`, `vad_filter=True`, `beam_size=1`, line 21). Cached at `CAPTION_ASR_CACHE` (`/var/lib/studio-yt/whisper-models`).
- Script Alignment (`align_to_script`, line 52): Uses `difflib.SequenceMatcher` to align transcribed tokens with script tokens:
  - `equal`: Keeps script spelling with spoken audio timestamps.
  - `replace`: Distributes script words across spoken span weighted by token string length.
  - `delete`: Keeps spoken non-script words.
  - `insert`: Silently drops script words that were never spoken.
- Rejection Ratio: If `len(spoken) < 0.5 * script_word_count`, returns `None` to force estimation fallback (`speech_words.py:85`).
- Fallback Estimation:
  - Audio Silence Windows: `speech_windows` runs `ffmpeg -af silencedetect=noise=-30dB:d=0.25` (`ai_video_overlay.py:22`).
  - Word Weighting: `estimate_words` distributes words across detected audio windows proportional to token character length, adding a 1.5x pause weight for terminal punctuation (`,`, `.`, `?`, `!`) (`ai_video_overlay.py:64`).

### 8.2 Subtitle & Logo Burning
Implemented in `backend/app/services/ai_video_overlay.py`:
- ASS Subtitle Rendering: Generates `.ass` file with `write_ass`, setting `fontsize=fanpage.ai_video_caption_size`, `margin_v=round(1920 * caption_bottom_pct / 100)`, highlight color `ai_video_caption_highlight` (default `#FFE500`).
- FFmpeg Filter Chain: `_build_ffmpeg_cmd` chains `ass=filename=...:fontsdir=...` and `overlay=x:y` for watermark logo.
- Fallback Frame Engine (`_fallback_render`, line 132): If ffmpeg lacks the `ass` filter (e.g. minimal ffmpeg build), catches exception and pipes uncompressed raw video (`rgb24`) through PIL (`ImageDraw.Draw`), compositing logo and burning outlined text frame-by-frame (`ai_video_overlay.py:240`).

### 8.3 Publish Job Hand-Off
`backend/app/tasks/ai_video_finalize.py:134-150`:
- Cover entry points: `backend/app/services/ai_video_cover.py:build_cover_copy_prompt`, `build_cover_image_prompt`, `build_cover_html`; `backend/app/services/ai_video_cover_build.py:prepare_cover`, `apply_cover`. Copy is generated at job creation, independently of prompt polish. Worker Flow t2i supplies `cover_bg`; renderer `/render-html` draws all title text. The cover starts at frame zero, so a thumbnail sampled around 0.5 s still sees it: `COVER_HOLD_SECONDS=1.0`, then `COVER_FADE_SECONDS=0.4`; neither value is 0.5 s. Audio keeps playing. Generated cover JPG becomes the thumbnail. Missing background falls back to a source frame at 3 s, then 0 s; failed cover application returns None.
- Brand style: Poppins-Black uppercase, white text with thick black stroke, page caption-highlight colour, page logo size/position/margins. Copy normally asks one short natural question, avoids the last eight hooks; motorcycle titles use model/year when extractable. Health covers show one real-looking adult with the relevant anatomy glowing through skin/clothes, natural face and no gore.
- Motorcycle covers (`motorcycle_commercial` or `motogp`) use the idea's `bike_look` / `livery` via `cover_bike_description`: whole bike on paddock stands before its team garage, real livery, sponsor/maker lettering and race-number digits/colour/positions retained. Only series logos are forbidden. Clip `bike_rules` keeps large logos crisp in fixed positions, with only tiny decals soft; `bike_look` permits up to four researched sponsor/maker names.
- Final branded video written to `<storage_base_path>/ai_videos/job_{id}_{uid8}_branded_{uuid8}.mp4`; cover application appends `_c{uuid6}` before `.mp4`. TTS inserts `_tts_{uuid8}` into the raw stem before branding. Without overlays, the cover suffix is applied directly to the current video stem (`ai_video_finalize.py:161`, `ai_video_cover_build.py:250`).
- Thumbnail extracted at `ss=3` to match branded overlay (or set to cover jpg when cover is enabled).
- Inserts `PublishJob`:
  - `content_type = ContentType.ai_video`
  - `video_path`, `video_url`, `video_thumbnail_path`, `video_thumbnail_url`, `video_duration_s`
  - `design_title = job.title`, `ai_generated_caption = job.caption`
- Dispatch Routing:
  - Auto mode (`fanpage.ai_video_publish_mode == PublishMode.auto` and QC passed): immediately calls `publish_job.delay(publish_job.id)`.
  - Manual review mode: leaves `PublishJob` with status `pending_review`.
- State closure: `job.status = "done"`, `idea.status = "done"`, `job.finished_at = utcnow`.

### 8.4 Video Stock & Release Horizon
- `AI_VIDEO_STOCK_DAYS = 20`: Planner produces ahead into stock up to `ai_video_daily_count * AI_VIDEO_STOCK_DAYS` pending jobs even after hitting today's daily count (`made_today >= ai_video_daily_count`), keeping Google Flow active 24/7.
- `VIDEO_STOCK_RELEASE_HOURS = 36`: `publish_job` holds `ContentType.ai_video` in `pending_publish` if its earliest destination slot is > 36h away (`video_slot_soon(...) is False`).
- Why: `cleanup.py` (`cleanup_old_ai_videos`) deletes source video files 7 days after Repliz handoff (`published_at`). Videos scheduled further out would lose files before go-live, so they are only released to Repliz shortly before their slot.
- `recover_stuck_auto_publishes` sweeps held stock periodically, dispatching oldest stock first (`order_by(PublishJob.created_at.asc())`).
- `backend/app/tasks/ai_video_plan.py:_daily_counts` replaces the first two failed jobs/page/day without consuming the daily production count; failed jobs charge completed clips only. Group-only generation has its own open-job limit (1), skips page stock/daily target gates, but shares total daily credit spend; finalization links the result via `fb_share.group_jobs`.
- Daily delivery cap is separate from generation stock. `backend/app/scripts/respread_ai_video_deliveries.py:respread`, `main` is a one-off operator tool, dry-run by default (`--apply`, `--limit`, `--destination-id`). Counts destination-local days; moves excess `repliz_scheduled` deliveries only with schedule id, no Facebook id/uncertainty, and >30 min lead. Delete must be verified (deleted/cancelled status or 404/410) before replanning. Clears `claim_token`, `claimed_at`, `published_at`, `attempt_count` and schedule ids, restores `pending_publish`, records `respread_moved`. Slot failure falls back to old slot +1 day.

---

## 9. TTS narration (Google Gemini TTS via 9Router, 2026-11-03)

- **Google TTS + ASMR:** `{engine: "tts", voice, bed: "asmr"}` keeps the TTS voice/finalization path; `prompt_engine_for` selects ASMR for rendering, budgets, polish and prepared-cache matching, while `prompt_engine` selects ASMR for job re-polish. Missing/null bed keeps the music bed.

- **Three engines / UI:** Per fan page per template, choose `flow` (default Flow voice), `tts` (Flow effects + instrumental music, narrator added later), or `asmr` (Flow sounds + on-screen narration, no voice or music). Controls require a selected template with a 2+ clip option; `engine_for` also requires at least two actual clips, otherwise it returns `flow`. The voice selector appears only for TTS.
- **Automatic voices / documentary style:** Auto uses Iapetus by default, Algenib for `motorsport_legend_innovation` and `motorcycle_commercial`, and Kore for `EN-JOURNO-F` (preset takes priority over template). A catalog voice overrides Auto. `tts_style` reads the merged template block: shared style is an engaging, confident, clear, warm documentary narrator with natural energy and a lively, flowing pace; health overrides it with a calm health educator. Language follows the fan page except `motorsport_tech_explainer`, which stays English.
- **Entry points / data:** `backend/app/services/ai_video_narration.py:engine_for`, `prompt_engine_for`, `resolve_narration`; `backend/app/services/tts_voices.py:narration_choice`, `auto_voice`, `tts_allowed`. `ai_video_narration` stores per-template `{engine, voice, bed}`; no fanpage id/name is hard-coded to TTS. Flow is default, including ineligible one-clip jobs; eligible templates such as health and motorcycle use Gemini only when that page selects TTS. `resolve_narration` returns null for Flow, engine/language for ASMR, voice/style/language and optional ASMR bed for TTS. Finalization adds segments, model, clean audio path or error. `build_narration` returns requests/mode but `narrate_video` omits them.
- **Word budget / narrator-free prompts:** `video_templates.py:get_narration_budget`, `accepted_min` apply language factors, template `narration_words_tts` only for TTS, and `NO_VOICE_WORD_FACTOR=0.8` only for ASMR. Accepted lower bound is target low minus 2 for TTS, minus 6 for Flow/ASMR; motorcycle TTS targets 14–16 English words. TTS with ASMR bed uses the ASMR prompt/budget path. Both renderers select `audio_tts` / `audio_asmr`, remove narrator + quoted narration placeholders, and reject narrator blocks/text. Quoted lettering remains allowed. Polish requires empty voice_sentence and preserved no-voice restrictions; ASMR also preserves no music.
- **Single-request TTS / per-clip fallback:** `build_narration` first synthesizes the whole script in ONE TTS synthesis call per video, then `split_by_pauses` splits at the longest internal pauses and validates sentence shares. Result `mode` is `single` on a usable split, otherwise `per_clip`; failed splitting adds per-clip synthesis calls. `requests` counts synthesis calls, not HTTP retries/model fallbacks; shortening can add calls even in `single` mode. Model fallback is 3.1 Flash preview, 2.5 Flash preview, then 2.5 Pro preview. Fitting allows 0.25 s lead-in, 0.5 s tail gap, tempo ≤ 1.12, and two shortening rounds, accounting for intermediate 100 ms trims and actual final duration.
- **Timing-based ducking / loudness:** `backend/app/services/ai_video_tts.py:build_narration`, `narrate_video`, `duck_expression`, `mix_narration` use fitted speech windows: `DUCK_GAIN=0.25`, `BED_LUFS=-16`, `VOICE_LUFS=-16`, `FINAL_LUFS=-14`, 0.2 s down, 0.15 s hold, 0.4 s up. Overlapping envelopes take the lowest gain, applied at 20 ms frame resolution. Measured linear final normalization plus `alimiter=0.95` preserves ducking; non-finite measurements fall back to ordinary loudnorm. Legacy calls without segments use sidechain compression. Video is stream-copied.
- **ASMR / always-burned captions:** `audio_asmr` requests close-miked action sounds, no music or voices. `_apply_asmr` calls `normalize_bed` (-14 LUFS + limiter, copied video) and stores clip-based caption windows without synthesizing speech. Finalize and re-render force captions through `_CaptionsEnabled`, preserving the page's saved switch and styling. TTS captions instead align final segment text against the clean narrator WAV, falling back to fitted windows; the WAV is retained as `narration_job_{id}_{uuid8}.wav`.
- **Speech checks / failure handling:** `backend/app/tasks/ai_video_speech_check.py:check_clip_speech` runs on `video` (Whisper cache), stores word count/text or `{unavailable: true}`, then resumes `check_clip` on gallery. `SPEECH_MIN_WORDS=3` is a major QC issue for TTS/ASMR. Speech at attempt >=2 stops regeneration early and selects the best for review. Without clip QC, finalization holds speech or unavailable transcription for review. TTS/ASMR processing failure records `narration.error`, preserves original video, skips overlays/cover and routes to pending_review.
- **Request format:** `synthesize` sends style/language in the input prefix ending `": "`, with voice embedded in `gemini/<model>/<Voice>` at `/audio/speech` under the configured 9Router base URL. It requires a RIFF response and uses bounded retries/model fallbacks.

### 9.1 TTS Code Pointers

backend/app/services/ai_video_narration.py:7-11,25-38 - TTS/ASMR require two actual clips and a template with a 2+ option; resolution snapshots engine/language, plus voice/style for TTS.
backend/app/services/tts_voices.py:36-47 - Auto prefers Kore for EN-JOURNO-F, then Algenib for legend/motorcycle templates, then Iapetus.
backend/app/services/ai_video_narration.py:14-22 - Style reads the template tts_style block; language follows the page except the English tech explainer.
backend/app/video_templates/_shared_blocks.json:3,6 - Shared blocks define documentary tts_style and narrator-free, music-free audio_asmr.
backend/app/video_templates/health_3d_explainer.json:26,30 - Health overrides tts_style with a health educator and audio_asmr with organic process sounds.
backend/app/services/video_templates.py:get_narration_budget,accepted_min - Only ASMR scales by 0.8; TTS uses its optional dedicated budget and low-minus-two acceptance threshold.
backend/app/services/video_templates.py:226-234,325-341,370-381 - Both renderers select engine audio blocks and strip narrator placeholders; the prompt guard rejects narrator blocks/text while allowing lettering.
backend/app/services/prompt_polish.py:208-234,328-335,376-380,581-619 - TTS/ASMR polish requires no voice; ASMR adds no music, with protected ambience retained during re-polish.
backend/app/services/ai_video_tts.py:68-81 - Synthesis prefixes style/language in input and embeds voice in gemini/<model>/<voice>.
backend/app/services/ai_video_tts.py:28-39 - Ordered TTS models, fitting limits, LUFS targets and duck ramp constants are defined together.
backend/app/services/ai_video_tts.py:132-180 - split_by_pauses selects longest internal pauses, trims silence and rejects implausible sentence shares.
backend/app/services/ai_video_tts.py:224-262 - build_narration tries one full-script synthesis before per-clip fallback and returns synthesis-call requests plus single/per_clip mode.
backend/app/services/ai_video_tts.py:265-276 - duck_expression builds timing-based ramps and takes the lowest overlapping gain.
backend/app/services/ai_video_tts.py:279-288 - normalize_bed normalizes ASMR to -14 LUFS with a limiter while copying video.
backend/app/services/ai_video_tts.py:291-335 - mix_narration normalizes bed/voice to -16 LUFS, applies timed ducking, then measured linear -14 LUFS normalization and alimiter; video is copied.
backend/app/services/ai_video_tts.py:359-376 - narrate_video passes fitted segments into mixing and retains the clean WAV, but omits requests/mode from its result.
backend/app/tasks/ai_video_finalize.py:72-118 - _apply_asmr checks speech when needed, normalizes the bed, stores caption windows and records failures.
backend/app/tasks/ai_video_finalize.py:121-153,236-263,418-455 - The captions proxy forces ASMR captions in finalize/re-render; TTS captions use clean audio or fitted windows.
backend/app/tasks/ai_video_clip_qc.py:96-105,115-117,138-148 - no_voice covers TTS/ASMR, schedules speech checks and limits speech-triggered regeneration to one within the attempt cap.
frontend/src/components/fanpages/AiVideoSection.tsx:33,222-271 - Eligible templates offer flow/tts/asmr; only TTS exposes the voice selector.

---

## 10. Schema & Operational Caveats

Migrations live in `backend/alembic/versions/`; ids below are revision ids.

| Revision | Fields / purpose |
| :--- | :--- |
| `c80000000038` | `gflow_accounts.cooldown_until`: account rests independent of worker pause. |
| `c80000000039` | `gflow_workers.phase` JSON: background credit-check progress or legacy string. |
| `c80000000041` | `video_jobs.cover_lines`, `cover_highlight`, `cover_prompt`, `cover_bg_path`; page `ai_video_cover_enabled` default true. |
| `c80000000042` | `gflow_accounts.unusual_strikes` default 0, `last_unusual_at`: escalating strike cooldowns. |
| `c80000000043` | `settings.ai_night_start` default `00:00`: shared AI engine night window, separate from worker keep-alive. |
| `c80000000044` | `gflow_accounts.agent_only` default false: persist composer restriction for claim filtering. |
| `c80000000046` | Idea `prepared`, `prepared_at`, `polish_attempts`, `polish_error`; page `ai_video_prompt_polish_enabled`; settings `prompt_polish_base_url`, `prompt_polish_token_encrypted`, `prompt_polish_model`. |
| `c80000000048` | Page `ai_video_clip_qc_enabled` default false, `video_clip_qc` attempts/verdicts with unique job/generation/clip/attempt key. |
| `c80000000049` | Page `ai_video_clip_qc_max_attempts` default 4; job `clip_qc_max_attempts` nullable first-claim snapshot. |
| `c80000000050` | `gflow_accounts.session_flagged_at`: recent login/identity failures drive worker auto-pause. |
| `c80000000051` | Page `ai_video_narration`, job `narration`, clip-QC `speech` JSON. |

- Possible recovery bug: `gflow_worker/runner.py:JobRunner._recover_clip` calls `json.loads` while scanning older state files, but the module does not import `json`. Its broad exception silently drops those exclusion ids.
- Possible speech-QC gap: `check_clip_speech` records unavailable transcription, but `check_clip` treats missing word count as zero. Finalization skips its fallback speech check whenever any clip has QC-attempt metadata, so unavailable clip speech evidence alone does not force review.
- Health counts use `valid_prepared` without narration-engine/clip-count matching, while planner uses `_is_matching_prepared`; a configuration change can make health look prepared while planning waits.

### 10.1 Core Code Pointers

backend/app/tasks/ai_video_plan.py:33 - Planner skips fanpage if open_count >= 2 or both daily target and stock cap (made_today >= ai_video_daily_count and stock >= ai_video_daily_count * AI_VIDEO_STOCK_DAYS) are reached.
backend/app/tasks/ai_video_plan.py:76 - Approved ideas rotate by least recently used template on that fanpage before considering score and age.
backend/app/tasks/ai_video_plan.py:84 - An approved idea is marked failed immediately if template's minimum clip option exceeds fanpage ai_video_max_credits_per_video.
backend/app/services/ai_video_dispatch.py:188 - Worker claim requests are throttled by MIN_CLAIM_GAP_MINUTES = 10 based on GflowAccount.last_used_at.
backend/app/services/ai_video_dispatch.py:288 - Unpinned job account selection uses sticky algorithm: prefers most recently used account with sufficient credits over never-used accounts.
backend/app/services/ai_video_dispatch.py:545 - Google Flow unusual activity or no submit reply puts account on escalating cooldown (1-2h, 2-3h, 3-4h), requeues job with backoff (30m, 60m, 120m), caps cooling accounts at 40%, and trips worker circuit breaker on 3 distinct hits in 30m.
gflow_worker/proxy_guard.py:66 - If configured Flow proxy is unreachable, worker attempts phone proxy revival via ADB before failing closed to prevent leaking datacenter IP.
gflow_worker/lock.py:40 - Locking enforces hierarchical order: global shared lock (LOCK_SH) first, then per-profile exclusive lock (LOCK_EX) second.
gflow_worker/media.py:44 - Video concatenation trims seed_offset_ms (100ms) from preceding clips to eliminate duplicated static freeze frames at clip junctions.
backend/app/services/ai_video_qc.py:71 - Technical QC rejects video if cumulative black duration exceeds 2.0s or frozen video duration exceeds 3.0s.
backend/app/services/speech_words.py:85 - Real-voice Whisper transcription is discarded for fallback timing estimation if transcribed word count is under 50% of script words.
backend/app/services/ai_video_overlay.py:240 - If host ffmpeg binary lacks the ass subtitle filter, overlay engine falls back to an uncompressed rawvideo pipe with PIL drawing.

# Deep Dive D3: Mode 8 AI Video Generation Subsystem

> Generated 2026-10-16 at main 25edf38 from a full read of the listed files. The "Verified claims" list at the bottom was checked line by line against the code. Other details are a careful map but not each re-checked. Read the real code before changing behaviour, and update this file when the code changes.


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
   ├─► Check fanpage caps: made_today < ai_video_daily_count AND open_count < 2
   ├─► Auto-approve proposed ideas if score >= ai_video_min_score
   ├─► Rotate templates: choose approved idea with least recently used template
   ├─► Fit clips to credit caps (ai_video_max_credits_per_video / per_day)
   ├─► Variable fill: fill_from_research (no LLM) OR fill_idea (9Router LLM)
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
| `ai` (in-app) | `top_up_ai_video_ideas` | `proposed` | Disabled when `ai_video_researcher == 'muse'`; batch <= 4; 30d topic avoidance | `ai_video_plan.py:134` |
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
- Models: `motorsport_tech_explainer`, `motorsport_corner_masterclass`, `motorsport_legend_innovation`, `health_3d_explainer` (2026-10-20, for Ayok sehat).
- Core constants: `model="omni-flash"`, `aspect="9:16"`, `clip_seconds=10`, `credits_per_clip=15`. Clip options typically `[2, 3]` (20s or 30s); health `[4, 5]` (40–50 s, 60–75 credits).
- **Templates without a vehicle** (`"uses_vehicle": false`, `video_templates.uses_vehicle`): no `vehicle_preset`, livery or lettering. `render_clips` → `_render_plain`: style + audio + one identical narrator sentence in every clip, slots `visual_subject` + `shot_<n>a/b/c` + `narration_<n>`, every clip `t2v` (three hard-cut shots per clip, no frame chaining). Prompts over `MAX_PROMPT_CHARS` (2900) fail, because Flow refuses ~3,000+ characters.
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
- **Muse Bypass (`fill_from_research`)**: If `research_ready()`, idea already contains complete variables, title, and caption. Skips LLM generation entirely. Injects `GENERIC_LOCK` for `moving_part` and `fixed_parts` if missing, only when the template uses those slots.
- **LLM Filling (`fill_idea`)**: Calls 9Router via `news_copywriter._generate_with_fallback`. Vehicle templates: mechanical rules (`MECHANICAL_ACCURACY`), vehicle presets, mechanism references. Templates without a vehicle: `NICHE_ACCURACY[niche]` (health: anatomy, mainstream mechanisms, one subject, shots match words, no advice/dosage) and "never name real brands, companies or products". Both get language word budgets.
- **Muse research desk**: niches `f1`, `motogp`, `health`. A niche in `research_desk.NICHE_GUIDES` (health) gets a `guide` in the brief that replaces the top-level studio, idea sources, global rules, research defaults and motion check. Health variants must not send `vehicle_preset` / `team_livery`.
- **Studio Look Resolution (`apply_system_look`, `ai_video_fill.py:136`)**:
  - `narrator_preset`: Fanpage timezone `Australia/*` -> `EN-AUSSIE-ENG`; `America/*` -> `EN-US-ENG`; else `fanpage.id % 4` of `["EN-PADDOCK", "EN-SCOT", "EN-JOURNO-F", "EN-DOC"]`.
  - `livery`: `pick_livery(db, fanpage_id, template)` inspects past 50 jobs, picks unused livery or least recently used.
  - `channel_name`: `sanitize_channel_name(fanpage.name)` uppercased, alphanumeric/spaces only, truncated to 24 chars.

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
Implemented in `ai_video_dispatch.py:151-317`:
1. **Worker Pause Check**: Fails if `GflowWorker.paused_until > now` or last claim was `< 10 minutes` ago (`MIN_CLAIM_GAP_MINUTES = 10`, lines 179-189).
2. **Account Candidate Pool**: Accounts for worker with `enabled=True`, `authenticated=True`, `status="ok"`, credits known, and not present in any other `claimed` or `generating` job (`get_candidate_accounts`, line 41).
3. **Step 1 - Pinned Jobs**: Jobs in `queued` with non-null `account_id` matching an available candidate and `not_before <= now`.
   - Remaining cost = `(len(clips) - len(clips_done)) * credits_per_clip`.
   - `ai_video_dispatch.effective_credits(db, acc)`: the credits minus the clips made on this account since `credits_checked_at`.
   - Selected if `effective_credits - credit_reserve >= remaining_cost`.
   - A pinned job whose account cannot pay for the remaining clips is restarted on another account (`generation += 1`, `clips_done = []`, `account_id = None`, `status = "queued"`; `restarts` is not incremented).
4. **Step 2 - Unpinned Jobs (Fair Share)**:
   - Sorted by fanpage whose previous job started longest ago: `last_started = max(VideoJob.started_at) group by fanpage_id` (`ai_video_dispatch.py:267-273`).
5. **Step 3 - Sticky Account Selection (`acc_sort_key`, line 288)**:
   - Filters candidates where `effective_credits - credit_reserve >= credits_estimate`.
   - Sort key: `(0, -last_used_at.timestamp(), -credits)` for previously used accounts; `(1, 0.0, -credits)` for fresh accounts.
   - The sticky account choice is intentional (Google flags hosts that hop between accounts).
6. **Step 4 - Concurrency Lock**:
   - `SELECT ... FOR UPDATE SKIP LOCKED` on `VideoJob` row.
   - Sets `status="claimed"`, `lease_until=now + 20m`, `worker_id=W`, `account_id=acc.id`, `last_progress_at=now`.

### 4.3 Error Handling & Restart Backoff (`record_failure`)
`ai_video_dispatch.py:377-495`:

| Failure Reason | Worker Condition | Action on Job & Account |
| :--- | :--- | :--- |
| `unusual_activity` / no submit reply | Flow anti-bot trigger or submit timeout ("of clicking submit") | Increments `unusual_hits`. Sets account on 6h cooldown (`cooldown_until = now + 6h`). Worker is NOT paused; job moves to another account immediately (restarts if `clips_done`, unpinned requeue if empty). Fails permanently if `unusual_hits > 4`. |
| `policy` | Safety / NSFW / RAI block | Fails job and idea immediately (`status="failed"`, line 451). |
| `out_of_credits` | Balance 0 or exit code 37 | Sets `account.credits = 0`, requeues job unpinned (`status="queued"`, line 455). |
| `auth` | Session expired (exit 8, 12, 14, 38) | Sets `account.status="auth_error"`, `authenticated=False`, requeues job unpinned (line 461). |
| `lost_checkpoint` | Local file missing vs server `clips_done` | Triggers `_restart()`: increments `generation`, `restarts+=1`, clears `clips_done`, unpins account. Fails if `restarts > 2` (line 469). |
| `upload_terms` | Flow upload terms dialog | Sets `account.status="needs_terms"`, triggers `_restart()` on another account (line 471). |
| `transient` / `other` | Network glitch, timeout, UI drift | Increments `clip_attempts`. If <= 3, retries on same account with backoff `(2, 5, 10)` mins. If > 3 attempts, triggers `_restart()` on another account (line 477). |

### 4.4 Sweep Engine (`sweep`)
`ai_video_dispatch.py:497-592`:
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
  2. Runs account credits refresh every `credits_refresh_minutes` (30m) only when `jobs` is empty (prevents opening extra Chrome on active profiles).
  3. Probes unreadable credit accounts via Playwright headless browser (`probe_credits.py`).
  4. Transmits `POST /heartbeat` with version, memory, running jobs, account states.
  5. Sends `progress(event="alive")` for active jobs to extend lease.
  6. Claims new jobs while `accept_jobs` is true and memory allows.

### 5.2 Locking Hierarchy (Flock Protocol)
Implemented in `gflow_worker/lock.py:32-60`:
1. **Global Shared Lock (`fcntl.LOCK_SH`)**: Acquired on `GFLOW_LOCK_FILE` (`/tmp/gflow-flow.lock`). Allows up to 2 concurrent worker jobs while completely locking out Hermes `flow.sh` which takes `fcntl.LOCK_EX`.
2. **Profile Exclusive Lock (`fcntl.LOCK_EX`)**: Acquired on `<lock_dir>/gflow-flow.<profile>.lock`. Ensures no two processes or probe scripts manipulate the same Chrome user profile directory concurrently.
3. **Strict Lock Order**: Global shared first, profile exclusive second. Releases in reverse order. Prevents deadlocks.

### 5.3 Fail-Closed Proxy Guard
Implemented in `gflow_worker/proxy_guard.py`:
- `is_proxy_reachable(proxy_url, timeout=2.0)` tests TCP connect to host and SOCKS5 port (1080) (`proxy_guard.py:12`).
- Auto-heal routine (`revive_phone_proxy`, line 28): Runs `adb connect 100.64.96.50:5555`, sleeps 1s, runs `adb shell am start-foreground-service -a hev.socks5.START hev.socks5/.Socks5Service`.
- Fail-Closed Gate (`ensure_proxy_ready`, line 51): If proxy unreachable, attempts auto-heal once. If still unreachable, raises `RuntimeError` (`proxy_guard.py:76`). Prevents launching gflow or probes, shielding residential accounts from datacenter IP exposure.

### 5.4 Playwright UI Credit Probe
Implemented in `gflow_worker/probe_credits.py`:
- Launches headless Chromium with persistent user profile directory (`probe_credits.py:76`).
- Navigates to `flow.google.com/project/{project_id}`.
- Dismisses overlays (close buttons, onboarding dialogs like "Got it", "Mengerti", "Start Creating").
- Regex extraction (`CREDITS: <number>`) supports English ("credits") and Indonesian ("kredit"), explicitly skipping non-balance phrases like "refund credits" (`probe_credits.py:31`).
- Transient toleration: If probe returns unknown but previous balance existed, tolerates 1 failure before clearing credits in heartbeat (`gflow_worker/main.py:86-93`).
- `gflow_worker` `runner.credits_stale`: after a "no YhhmEf/eb1hJf/MZZa6b/nprQif reply … of clicking submit" failure (`gflow_worker/runner.py:SUBMIT_NO_REPLY`), the runner sets `runner.credits_stale = True`. On the next idle tick (when no jobs are running), `gflow_worker/main.py` refreshes credits immediately (`last_refresh = None`). That message usually means the account ran out of credits.

---

## 6. Clip Continuity, Media Assembly, and Size Controls

### 6.1 Inter-Clip Visual Continuity
`gflow_worker/runner.py:99-106`:
- Clip 1: text-to-video (`t2v`) with template prompt.
- Clip N > 1: image-to-video (`i2v`) using `Media.last_frame(clip_prev, seed_png, offset_ms=100)`, unless the clip's `mode` is `t2v` (cut-style templates such as health): then it is its own t2v generation.
- `Media.last_frame` (`media.py:20-33`): Runs `ffmpeg -sseof -0.1 -i clip.mp4 -frames:v 1 seed.png`. Fallback to positive seek if `-sseof` fails.

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
- Controlled by global switch `Settings.ai_video_vision_qc_enabled` (default `False`, `ai_video_finalize.py:41`).
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
- Final video written to `<storage_base_path>/ai_videos/job_{id}_{uid8}_branded.mp4`.
- Thumbnail extracted at `ss=3` to match branded overlay.
- Inserts `PublishJob`:
  - `content_type = ContentType.ai_video`
  - `video_path`, `video_url`, `video_thumbnail_path`, `video_thumbnail_url`, `video_duration_s`
  - `design_title = job.title`, `ai_generated_caption = job.caption`
- Dispatch Routing:
  - Auto mode (`fanpage.ai_video_publish_mode == PublishMode.auto` and QC passed): immediately calls `publish_job.delay(publish_job.id)`.
  - Manual review mode: leaves `PublishJob` with status `pending_review`.
- State closure: `job.status = "done"`, `idea.status = "done"`, `job.finished_at = utcnow`.

---

## Verified claims

backend/app/tasks/ai_video_plan.py:33 - Planner skips fanpage if daily target reached (made_today >= ai_video_daily_count) or 2+ jobs are currently in flight (open_count >= 2).
backend/app/tasks/ai_video_plan.py:76 - Approved ideas rotate by least recently used template on that fanpage before considering score and age.
backend/app/tasks/ai_video_plan.py:84 - An approved idea is marked failed immediately if template's minimum clip option exceeds fanpage ai_video_max_credits_per_video.
backend/app/services/ai_video_dispatch.py:188 - Worker claim requests are throttled by MIN_CLAIM_GAP_MINUTES = 10 based on GflowAccount.last_used_at.
backend/app/services/ai_video_dispatch.py:288 - Unpinned job account selection uses sticky algorithm: prefers most recently used account with sufficient credits over never-used accounts.
backend/app/services/ai_video_dispatch.py:521 - Google Flow unusual activity or no submit reply puts only the account on 6h cooldown (worker not paused); job moves to another account.
gflow_worker/proxy_guard.py:66 - If configured Flow proxy is unreachable, worker attempts phone proxy revival via ADB before failing closed to prevent leaking datacenter IP.
gflow_worker/lock.py:40 - Locking enforces hierarchical order: global shared lock (LOCK_SH) first, then per-profile exclusive lock (LOCK_EX) second.
gflow_worker/media.py:44 - Video concatenation trims seed_offset_ms (100ms) from preceding clips to eliminate duplicated static freeze frames at clip junctions.
backend/app/services/ai_video_qc.py:71 - Technical QC rejects video if cumulative black duration exceeds 2.0s or frozen video duration exceeds 3.0s.
backend/app/services/speech_words.py:85 - Real-voice Whisper transcription is discarded for fallback timing estimation if transcribed word count is under 50% of script words.
backend/app/services/ai_video_overlay.py:240 - If host ffmpeg binary lacks the ass subtitle filter, overlay engine falls back to an uncompressed rawvideo pipe with PIL drawing.

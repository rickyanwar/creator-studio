# Deep Dive D2: Publishing Ledger, Delivery Dispatch, and Phone Automation

Refreshed 2026-11-10 at main 8bc0f82.


Comprehensive technical reference for the publishing subsystem: legacy Repliz vs S1b delivery ledger path selection, `PublishDelivery` state transitions, distributed claim/lease semantics, audience-aware slot math, device recovery, reconciliation, and fail-closed phone automation.

---

## 1. Legacy vs. Ledger Path Decision

Entrypoint: `backend/app/tasks/publisher.py:publish_job(self, job_id: int)` (line 241).

```
PublishJob (status: pending_publish)
   │
   ├─► Atomic Claim: UPDATE publish_jobs SET status='publishing' WHERE id=job_id AND status='pending_publish'
   │      └─► If 0 rows updated: EXIT (duplicate Celery execution or manual double-click prevented)
   │
   ├─► Query: SocialDestination.content_project_id == job.fanpage_id AND is_active == True (publisher.py:274-282)
   │      │
   │      ├─► has_destinations > 0 ──► [LEDGER PATH] (publisher.py:284-323)
   │      │      ├─► ensure_deliveries(db, job) (social_delivery_planner.py:231)
   │      │      ├─► dispatch_due_deliveries(db, job_id=job.id) (social_delivery_dispatch.py:1754)
   │      │      ├─► Query all PublishDelivery rows for job.id:
   │      │      │      ├─► services/job_status_sync.py: all accepted, or all terminal with any success:
   │      │      │      │      ├─► job.status = 'published', published_at = newest delivery timestamp (or now)
   │      │      │      │      ├─► If single Repliz dest: sync job.repliz_schedule_id, job.scheduled_for
   │      │      │      │      ├─► ScrapedArticle.status = 'published' (if source_article_id present)
   │      │      │      │      └─► _maybe_mark_post_done(db, post) (if post_id present)
   │      │      │      ├─► All terminal without success: failed if any failed, otherwise skipped (lowest-id error)
   │      │      │      └─► Unfinished/uncertain: release publishing to pending_publish for recovery
   │      │      └─► db.commit()
   │      │
   │      └─► has_destinations == 0 ──► [LEGACY REPLIZ PATH] (publisher.py:324-415)
   │             ├─► Requires fanpage.repliz_account_id; if missing -> job.status = 'failed'
   │             ├─► Media routing:
   │             │      ├─► news_content, ig_recreate, discussion, pinterest_content, facebook_recreate: _publish_news_job()
   │             │      ├─► youtube_clip, ai_video: _publish_video_job()
   │             │      └─► ig_repost: create_album_schedule() (>=2 images) or create_image_schedule() (1 image)
   │             ├─► Slot allocation: _next_schedule_at(db, job.fanpage_id, breaking=job.is_breaking)
    │             ├─► Sets job.status = 'published'; cleanup_at = max(now, go-live) + 6h (video: go-live + 48h)
   │             └─► _maybe_mark_post_done(db, post)
```

`services/job_status_sync.py` preserves stale-only skip errors and never downgrades published jobs.
`sync-finished-publish-jobs` runs every 15 min to close pending jobs finished through any path,
updating source article/post records on success. Repliz schedules mixed with failed/skipped stay pending.

`job_status_sync.py:decide_job_status` and `apply_job_status` are the shared pure status helpers. All `repliz_scheduled`/`published` sets are accepted; all-terminal sets succeed if any delivery is published. Otherwise failures precede skips, selecting the lowest-ID error; all stale-only skips preserve their original error. Uncertain/unfinished sets make no decision. Published parents are never downgraded or re-timestamped. Success sets `cleanup_at = max(job.published_at, delivery scheduled_for values) + 6h`; one actual Repliz delivery also copies schedule ID/time to the parent.

Hermes always runs via `systemd-run` (transient unit outside the phone-runner sandbox, `HERMES_OUTSIDE_SANDBOX=1`) because it must write `/root/.hermes` while the runner keeps `ProtectHome=read-only` (2026-10-17, 432cbd0).

---

## 2. PublishDelivery State Machine

Defined in `backend/app/models/social_publishing.py:215-219` (`ck_publish_deliveries_state`).

| State | Role & Meaning | Transitions In From | Transitions Out To | Driven By (path:line) |
| :--- | :--- | :--- | :--- | :--- |
| `waiting_for_slot` | Media kind unsupported by destination or waiting replan | Initial planning | `scheduled` | `social_delivery_planner.py:341`, `social_delivery_dispatch.py:1087, 1562` |
| `waiting_for_device`| Intended for phone but no verified device is `available` | Initial planning, lease miss, connect error | `publishing`, `needs_human`, `waiting_for_slot` | `social_delivery_planner.py:366`, `social_delivery_dispatch.py:1250, 1409` |
| `scheduled` | Slot allocated; ready for paced dispatch | `waiting_for_slot`, `waiting_for_device` | `publishing` | `social_delivery_planner.py:363, 369`, `social_delivery_dispatch.py:1591, 1725` |
| `publishing` | Atomic lease/claim held; remote execution running | `scheduled`, `waiting_for_device`, `waiting_for_slot` | `repliz_scheduled`, `published`, `uncertain`, `failed` | `social_delivery_dispatch.py:245, 301` |
| `repliz_scheduled` | Repliz accepted schedule payload via REST | `publishing` | `published`, `failed` | `social_delivery_dispatch.py:486`, `status_sync.py:166, 212` |
| `published` | Terminal success; post verified live on Facebook | `publishing`, `repliz_scheduled` | _Terminal_ | `social_delivery_dispatch.py:1370`, `status_sync.py:172, 250`, `social_post_reconciliation.py:320` |
| `uncertain` | In-flight failure, timeout, 5xx, or unproven post ID | `publishing` | `published` (via follow-up checks; manual buttons otherwise) | `social_delivery_dispatch.py:387, 1608, 1644, 1682, 2277` |
| `needs_human` | Fallback blocked (e.g. repliz disconnected) or recovery exhausted | `waiting_for_device`, `publishing` | _Manual action_ | `social_delivery_dispatch.py:1133`, `phone_runner/recovery.py:545, 646` |
| `failed` | Deterministic unrecoverable error (e.g. 4xx, bad auth) | `publishing`, `repliz_scheduled` | _Terminal_ | `social_delivery_dispatch.py:415, 429, 528, 1346, 1455`, `status_sync.py:168, 237` |
| `skipped` | Administrative bypass or unroutable destination | `scheduled`, `waiting_for_slot` | _Terminal_ | Database constraint supported |

> **IMPORTANT Gotcha (`published_at`)**: For Repliz deliveries, `published_at` is the HAND-OFF time to Repliz (`social_delivery_dispatch.py` ~L642), not the live time. The live time is `scheduled_for`. For phone deliveries, `published_at` is the real live time (~L1565).

---

## 3. Claim, Lease, and Locking Protocol

Implemented in `backend/app/services/social_delivery_dispatch.py`.

### 3.1 Lock Ordering Hierarchy (Deadlock Prevention)
1. **First Lock**: `SocialDestination` row (`with_for_update()`, line 135).
2. **Second Lock**: `PublishDelivery` row (`with_for_update()`, line 149).
3. **Third Lock (Phone only)**: `PhoneDevice` row (`with_for_update()`, line 679).

### 3.2 SQLAlchemy Session Invalidation (`populate_existing()`)
- Explicitly called on both `SocialDestination` (line 133, 1660) and `PublishDelivery` (line 148, 1680).
- **Function**: Forces SQLAlchemy to discard cached Python attribute state in `SessionLocal` and reload raw PostgreSQL column values from the `SELECT ... FOR UPDATE` query. Prevents race conditions where a concurrent worker updated `state`, `claim_token`, or `attempt_count` during the same transaction.

### 3.3 Atomic Delivery Claims
- Paced claim: `claim_paced_delivery_for_dispatch` (`social_delivery_dispatch.py:96-264`):
  - Eligible states: `scheduled`, `waiting_for_slot`, `waiting_for_device` (line 118).
  - Validation: `claim_token is None`, `uncertain_since is None`, `facebook_post_id is None`, `repliz_schedule_id is None`.
  - Immediate send mutual exclusion: If candidate is immediate (`intended_method == 'phone'` or `scheduled_for <= now`):
    - Blocks claim if ANY other delivery for the same `destination_id` is currently in `publishing` state (lines 184-200).
    - Enforces `min_gap_minutes`: queries previous live sends (`published` or immediate `repliz_scheduled` within `naive_now - min_gap_minutes`). Excludes future pre-schedules (lines 203-241).
  - Claim write: `candidate.state = 'publishing'`, `candidate.claim_token = uuid.uuid4().hex`, `candidate.claimed_at = now` (lines 244-249). Logs `DeliveryEvent(event_type="claim_acquired")` and commits immediately.

### 3.4 Device Leases
- Table `device_leases` (`backend/app/models/social_publishing.py:330-349`): Primary key `device_id`, unique foreign key `delivery_id`, `expires_at`.
- Lease duration: `lease_ttl_minutes = 45` (`social_delivery_dispatch.py:585`).
- Selection criteria (`select_and_lease_phone_device`, lines 581-710):
  1. Priority order: `DestinationDeviceAssignment.priority.asc()` (line 604).
  2. Device checks: `is_active == True`, `verified_at is not None`, `device.status == 'available'`, `calibration_verified_at is not None`, `adb_serial` present.
  3. Identity verification (`assert_publish_identity`, line 639, 661): matches `expected_profile_id` vs `verified_profile_actor_id` and (for pages) `expected_page_id` vs `verified_target_actor_id`.
  4. Acquisition: locks `PhoneDevice` with `with_for_update()`. If lease exists and `expires_at > now`, skips to next candidate. If expired, reclaims. If none, inserts new `DeviceLease`. Wrapped in `try...except IntegrityError` with `db.rollback()` (lines 675-707).
- Release: `release_device_lease` (`social_delivery_dispatch.py:712-719`) executes hard `DELETE FROM device_leases WHERE device_id=... AND delivery_id=...` in `finally` block of `dispatch_phone_delivery` (line 1481).

### 3.5 Worker Crash & Stale Claim Recovery
- `recover_stale_deliveries_and_leases` (`social_delivery_dispatch.py:327-395`):
  - Threshold: `stale_ttl_minutes = 45` (line 330).
  - Finds deliveries in `publishing` with `claimed_at <= now - 45 min`.
  - State change: `state = 'uncertain'`, `uncertain_since = now`, `last_error = "Stale claim expired..."`.
  - Clears expired `device_leases` where `expires_at <= now`. If associated delivery is `publishing`, marks it `uncertain`.

---

## 4. Slot Computation & Scheduling Engine

Implemented in `backend/app/services/publishing_schedule.py`.

### 4.1 Constants
- `DEFAULT_ACTIVE_START_HOUR = 7` (07:00 local, line 31).
- `DEFAULT_ACTIVE_END_HOUR = 22` (22:00 local, line 32).
- `_MAX_BOUNDED_DAYS = 60` (search horizon limit, line 33).
- `MIN_LEAD_SECONDS = 60` (minimum lead time from current instant, line 34).
- `MIN_GAP_FLOOR_MINUTES = 15` (minimum gap floor between posts on one destination).
- `GRID_JITTER_SPAN = 0.70` (maximum jitter span within quantile cells).
- `MAX_DAILY_TARGET = 1000`.
- Jitter bounds: `0.5 + (rnd - 0.5) * span` where span shrinks below 0.70 in narrow cells (`span = min(0.70, max(0.0, 1.0 - gap_minutes / cell_minutes))`).

### 4.2 Audience Hour Weighting (`get_hour_weight`)
- Midday window (12:00 to 14:00 local): weight = `1.5`.
- Evening window (18:00 to 21:00 local): weight = `2.0`.
- Baseline active window: weight = `1.0`.
- Sleep window: weight = `0.0`.

### 4.3 Quantile Grid & Deterministic Jitter Math (`compute_audience_slot`)
1. **Minute Array**: 1,440 minute weights computed per local calendar day based on sleep window or defaults (`_day_minute_weights`).
2. **Fitting Daily Cap**: Day capacity capped by `fitting_daily_target` so every cell in the heaviest hour is at least `gap` wide (`total // (peak * gap)` with `gap = max(min_gap, 15)`).
3. **Target Cell**: Capped target $N$ partitions total weight $W$: $[ \frac{i}{N} W, \frac{i+1}{N} W ]$.
4. **Cumulative Search**: Finds index interval $[k_{start}, k_{end}]$ within active minutes where cumulative weight matches cell bounds.
5. **Deterministic Pseudo-Random Jitter**:
   $$\text{seed} = \text{SHA256}(\texttt{"YYYY-MM-DD:namespace:idx"})$$
   $$\text{rnd} = \frac{\text{uint64}(\text{seed}[0:8])}{2^{64}-1}, \quad \text{span} = \min(0.70, \max(0.0, 1.0 - \frac{\text{gap}}{\text{cell\_minutes}}))$$
   $$\text{jitter} = 0.5 + (\text{rnd} - 0.5) \times \text{span}$$
   $$k_{chosen} = \text{round}(k_{start} + \text{jitter} \times (k_{end} - k_{start}))$$
6. **Candidate Filtering & Selection**:
   - `cand_utc >= now_utc + 60s`
   - `cand_local` outside sleep window
   - Bidirectional gap separation: for every existing reservation $E$, $|cand - E| \ge \max(\text{min\_gap\_minutes}, 15) \times 60$.
   - Selection: if `prefer_peak=True` (non-news), picks the candidate in the highest weighted local hour (evening/midday) with earliest tiebreak; otherwise returns earliest valid candidate (news keeps earliest).
   - Rollover: if calendar day has $\ge \text{n\_slots}$ reservations or no candidates qualify, advances to next day (up to 60 days).

### 4.4 Reservation Loading (`get_destination_reservations_utc`, lines 307-342)
1. Active `PublishDelivery` rows for destination where `scheduled_for IS NOT NULL` and `state NOT IN ('failed', 'skipped')`.
2. Authoritative instant: `_pick_slot_instant` selects `max(scheduled_for, published_at)` (line 240).
3. Legacy `PublishJob` rows: loaded strictly when `destination.mode IN ('repliz', 'mixed')` AND `destination.repliz_account_id == fanpage.repliz_account_id`. Excludes jobs with existing ledger deliveries (`PublishJob.id NOT IN (SELECT publish_job_id FROM publish_deliveries)`).

### 4.5 Untouched Stale Replan (`replan_stale_scheduled_deliveries`, lines 1612-1751)
- Trigger: Delivery in `scheduled` state with `attempt_count == 0`, no external IDs, `scheduled_for < now - 10 min`.
- Action: Serialized under destination row lock; recomputes `scheduled_for` using `compute_audience_slot` to prevent immediate burst execution after worker downtime. Synchronizes parent `PublishJob.scheduled_for`.

### 4.6 Pre-Render Slot Availability (`news_slot_available`)
- `social_delivery_planner.news_slot_available(db, job, now_utc)` and `_plan_slot()`:
  - Shared slot math between pre-render stale check and `ensure_deliveries`.
  - Read-only (no row locks, no database writes) and fail-open (returns `True` on missing fan page, no active destinations, or unexpected errors).
   - Inspects all active destinations: freshness is `event_days.news_fresh_hours`, the destination limit (default 24 h) capped at 6 h on event days or 12 h otherwise, using the job's creation day in destination timezone. Ordinary news must post on its arrival local day. `_overnight_news_ok` permits news created within 4 h before sleep or during sleep into the first 3 waking hours, still within its age limit. Used by `visual_fair_queue` before image generation.

### 4.7 Event Boost, Quotas & News Priority
- `backend/app/services/event_days.py:effective_daily_target` doubles destination slots inside the fanpage's matching gallery-keyword event window. `publisher.py:_event_boosted_daily_limit` also doubles legacy daily limits. Window closes inclusively at H+1 (`gallery_downloader._EVENT_WINDOW_DAYS_AFTER = 1`); event refresh holds the date through H+2 (`_EVENT_REFRESH_HOLD_DAYS_AFTER = 2`) so western destination timezones retain H+1 boost.
- `backend/app/services/mode_quota.py:pick_slot`, `group_quotas`: largest-remainder mode split, after event boost and `fitting_daily_target` capacity cap. Non-news prefers peak-weight slots; news takes earliest. News may borrow idle quotas at any time when `borrow_hours_before_sleep > 0` (default 3); other modes borrow only inside that end-of-day window. A mode with fresh waiting content is protected; AI-video quota above its cap can be borrowed even while it waits.
- `mode_quota.py:ai_video_daily_cap`: positive `ai_video_daily_count` caps AI-video reservations per destination local day, including when mode shares are off; nonpositive/missing means no cap. It cannot borrow past its cap. Search blocks up to 14 days, then warns and returns a plain audience slot, so this bounded fallback is not a strict cap guarantee.
- `social_delivery_planner.py:_supersedable_news_delivery`, `ensure_deliveries`: when no fresh slot fits, a newer job may take an older untouched non-breaking news delivery's future slot; the replaced delivery becomes skipped with `stale: superseded by fresher news (job <id>)`. Claims, attempts, external IDs and uncertainty prevent replacement. `visual_fair_queue.py:pick_next_news_job` prioritizes breaking/event news, newest first; ordinary news is newest first within each fanpage, with least-served fanpage fairness.
- `social_delivery_dispatch.py:_news_dispatch_gate`: ordinary Repliz news waits until at most `REPLIZ_NEWS_SUBMIT_LEAD_MINUTES = 45` before go-live. Over-age unsent news becomes skipped (`stale: news too old to post at dispatch`). Non-event breaking news bypasses this gate; event news still gets its freshness check.

### 4.8 Event-Window Live News (`services/event_days.py:is_live_event_news`)
- During a matching niche's event window (MotoGP/F1/UFC/Boxing: H-3 through H+1), new news posts immediately outside the daily schedule:
  1. **Minimum Gap**: 10 minutes between posts on the same destination (`scheduled_for = max(now + 60s, latest reservation/live send + 10 min)`). `claim_paced_delivery_for_dispatch` uses `min(destination.min_gap_minutes, 10)` as the pace gap.
  2. **Sleep Window Bypass**: Posts even inside the audience's sleep window.
  3. **Extra Posts (No Quota Consumption)**: Deliveries are extra; they do not count toward daily target or mode quotas in `pick_slot` / `fitting_daily_target`.
   4. **Dispatch & Replan**: `_news_dispatch_gate` returns "go" immediately (skipping 45-min Repliz lead) while keeping 6 h stale check. `replan_stale_scheduled_deliveries` leaves live event news alone (never moves them back onto the normal grid).
   5. **Reservation horizon**: `mode_quota.pick_slot` only uses reservations up to `now + 10 min` for the immediate-path anchor; later pre-booked slots do not delay live news. Live news still respects in-flight mutual exclusion. This path is separate from the normal 15-minute grid floor.

---

## 5. Daily Caps & Interleaved Method Allocation

Implemented in `backend/app/services/social_delivery_policy.py`.

### 5.1 Route Validation Rules (`validate_route`, lines 14-72)
- Invariant: `phone_daily_target + repliz_daily_target == daily_target`.
- Profile: `kind == 'profile'` -> `mode == 'phone'`, `parent_profile_id IS NULL`, `repliz_account_id IS NULL`, `phone_daily_target == daily_target`, `repliz_daily_target == 0`.
- Page Phone: `kind == 'page'`, `mode == 'phone'` -> `parent_profile_id` required, `repliz_daily_target == 0`.
- Page Repliz: `kind == 'page'`, `mode == 'repliz'` -> `repliz_account_id` required, `phone_daily_target == 0`.
- Page Mixed: `kind == 'page'`, `mode == 'mixed'` -> `parent_profile_id` required, `repliz_account_id` required, `phone_daily_target > 0`, `repliz_daily_target > 0`.

### 5.2 Slot Allocation Cycle (`choose_method_for_slot`, lines 75-106)
- In mixed mode, slots interleave across cycle length $L = \text{phone\_target} + \text{repliz\_target}$:
  $$\text{method} = \begin{cases} \text{"phone"}, & \text{if } (\text{slot\_index} \pmod L) < \text{phone\_daily\_target} \\ \text{"repliz"}, & \text{otherwise} \end{cases}$$
- Example (Phone=2, Repliz=1): Slot 0=Phone, Slot 1=Phone, Slot 2=Repliz, Slot 3=Phone...

### 5.3 Brand-First Social Endpoints & Estimated Times (`backend/app/api/social_brands.py`)
Mounted at `/social-media` (prefix `/social-media`), read-only except GPT-CLI toggle:
- `GET /social-media/brands`: returns `list[BrandSummaryOut]` with per-brand channels, today counts (published, scheduled, failed, needs_attention), next post at, attention alerts.
- `GET /social-media/brands/{id}`: returns `BrandDetailOut` with today's AI images count by engine (`chatgpt`, `codex`, `codex_fallback`, `template`) and WIB day boundary.
- `PUT /social-media/brands/{id}/gptcli`: toggles `gptcli_fanpage_ids` in Settings.
- `GET /social-media/destinations/{id}/estimated-times`: returns `EstimatedTimesOut` with today's real delivery slots and simulated upcoming audience slots.
- `GET /social-media/destinations/{id}/recent`: returns recent deliveries with content type, title, design engine.
- `GET /social-media/repliz-accounts`: lists connected Facebook accounts from Repliz API.

**Estimated Times & Slot Index Rule** (`api/social_brands.py:get_destination_estimated_times`):
1. **Day window**: computed for destination timezone `[start_naive_utc, end_naive_utc)` on current local date.
2. **Real slots**: today's `PublishDelivery` rows (`published`, `failed`, `in_progress`, `scheduled`), mapped to `EstimatedSlotOut` with local time, WIB time, and method (`actual_method or intended_method`).
3. **Remaining slots**: `remaining = daily_target - non_failed_real_slots`.
4. **Simulated slots**:
   - Starts with `get_destination_reservations_utc(db, dest)` into `simulated_reservations`.
   - Loops up to `remaining + 3` times: calls `compute_audience_slot(...)` to find `next_slot`.
   - **Slot Index Rule**: planner slot index `idx` = count of existing simulated reservations on the same local date (`slot.astimezone(dest_tz).date() == slot_local_date`).
   - Mixed mode method selection: `choose_method_for_slot(dest.mode, slot_index=idx, phone_daily_target, repliz_daily_target)`. In mixed mode: `idx mod (phone + repliz) < phone` -> `"phone"`, else `"repliz"`.
   - Appends `next_slot` to `simulated_reservations`.
5. **Assembly**: real slots + estimated slots merged, sorted by `at` UTC timestamp, and indexed sequentially `0..N-1`.

### 5.4 Today's Plan (`backend/app/services/day_plan.py:destination_day_plan`)
- Returns destination-local date/time, event-adjusted `daily_target`, `base_daily_target`, mode `quotas`, `used`, `remaining`, `remaining_total`, and up to 30 simulated `upcoming_slots` that still fall today. No writes; adds phantom reservations locally during simulation. Invalid timezone falls back to UTC for day accounting.
- Uses `mode_quota.group_reservations` and shared audience-slot math. This summary calculates quotas on the raw boosted target, without the planner's fitting cap, AI-video cap, borrowing or live-news exclusions; treat it as a breakdown, not a dispatch guarantee.

### 5.5 Target Gap Diagnosis (`backend/app/scripts/target_gap_report.py:build_report`, `main`)
- Read-only CLI: `python -m app.scripts.target_gap_report --days 3 --json --destination <id>` from `backend`; flags are optional. Reports 1–14 completed destination-local days plus today-so-far, event multiplier, fitting-cap target, supply, news outcomes and unused quotas.
- Shortfall attribution order: `DELIVERY_FAILED`, `QUOTA_BLOCKED`, `FANPAGE_INACTIVE`, `AI_VIDEO_CAP`, `NO_SUPPLY`, `NEWS_DISCARDED`, `UNDER_TARGET_OTHER`. Invalid destination timezone falls back to `Europe/London`.
- `fetch_day_deliveries` counts `uncertain` and `repliz_scheduled` as posted alongside `published`; these are diagnostic counts, not confirmed live posts. Past boosts use current keyword event dates, so a historical event can appear unboosted after refresh.

---

## 6. Phone -> Repliz Fallback Policy

Implemented in `backend/app/services/social_delivery_dispatch.py:evaluate_and_execute_fallback` (lines 1033-1227) and `backend/app/services/social_delivery_policy.py:fallback_allowed` (lines 207-224).

### 6.1 Gating Rules (Fail-Closed)
All 7 gates must pass simultaneously before Repliz fallback is permitted:
1. **Target Kind**: Must be `facebook_page` (`kind == "page"`). Profiles never fall back to Repliz (`reason: profile_fallback_forbidden`).
2. **Certainty**: `delivery.state != 'uncertain'` and `delivery.uncertain_since IS NULL`. Uncertain deliveries must never fall back.
3. **Media Capability**: `delivery.media_kind IN destination.supported_media` and `media_kind IN ('image', 'album', 'video')`. Unsupported media waits at `waiting_for_slot`.
4. **Recovery Exhaustion**: Associated `RecoveryIncident.state == 'needs_human'` AND `RecoveryIncident.check_count >= 2` (repeated automated checks failed).
5. **Repliz Connection**: `destination.repliz_account_id` present and `destination.is_active == True`.
6. **Absence Verification (`reconcile_and_verify_absence`, lines 800-839)**:
   - No sibling delivery for same job and destination in `published` or `repliz_scheduled`.
   - Fingerprint deduplication: no existing delivery for same destination with identical `caption_sha256` and `media_sha256` in `published` or `repliz_scheduled`.
7. **Policy Assertion**: `fallback_allowed(...) == True`.

### 6.2 Fallback Dispatch Workflow
1. Mutate method: `delivery.intended_method = 'repliz'`.
2. Audit log: `DeliveryEvent(event_type="fallback_to_repliz")`.
3. Notification: sends Telegram alert to owner via Hermes (`notify_owner_via_hermes`, lines 1198-1212).
4. Remote dispatch: calls `dispatch_repliz_delivery(db, delivery)`.
5. Outcome notification: sends follow-up Telegram alert with updated state (`repliz_scheduled` or `failed`).

---

## 7. Recovery Incidents & Device Self-Healing

Implemented in `backend/app/models/social_publishing.py:351-399` (`RecoveryIncident`), `backend/app/services/social_delivery_dispatch.py:execute_device_recovery` (lines 841-1031), and `phone_runner/recovery.py` (lines 425-661).

### 7.1 Recovery Incident State Machine
`checking` -> `repairing` -> `awaiting_approval` -> `needs_human` -> `resolved`.

### 7.2 Two-Tier Recovery Architecture
```
Recovery Trigger (device offline, asleep, portal unready, lease expired)
   │
   ├─► Tier 1: Deterministic Routine Recovery (phone_runner/recovery.py:265)
   │      ├─► check_device_state(): adb get-state == 'device'
   │      ├─► query_power_state(): if 'asleep', wake_and_unlock() (keyevent 224 + dismiss-keyguard)
   │      ├─► ping_portal(): query content://com.mobilerun.portal/ping
   │      │      └─► if failed: am start-foreground-service com.mobilerun.portal/.PortalService
   │      ├─► check_and_revive_accessibility(): settings put secure enabled_accessibility_services
   │      │      └─► optional helper: /usr/local/bin/enable-mobilerun-a11y.sh
   │      └─► If all checks pass -> state='resolved', PhoneDevice.status='available'
   │
   └─► Tier 2: Larger Repair (phone_runner/recovery.py:541)
          ├─► If owner_decision in ('denied', 'rejected', 'cancelled') -> state='needs_human'
          ├─► If owner_decision != 'approved':
          │      ├─► If no auto_approve_after:
          │      │      ├─► auto_approve_after = now + 15 minutes
          │      │      ├─► hermes send -t telegram "[REPAIR NOTICE] Device requires larger repair..."
          │      │      └─► state='awaiting_approval'
          │      └─► If now < auto_approve_after -> remain in 'awaiting_approval'
          ├─► If owner_decision == 'approved' OR now >= auto_approve_after:
          │      ├─► hermes -z "<strict device-only repair prompt>"  (one-shot agent; `hermes heal` does not exist)
          │      ├─► Re-run Tier 1 Deterministic Recovery
          │      ├─► If re-check passes -> state='resolved'
          │      ├─► If heal fails -> wait RECOVERY_SETTLE_SEC (8 s), re-check once; healthy -> state='resolved'
          │      └─► Still broken -> hermes send -t telegram "[ALERT] Recovery exhausted..."
          │             └─► state='needs_human'
```

---

## 8. Status Polling & Facebook Post ID Resolution

Implemented in `backend/app/tasks/status_sync.py`, `backend/app/services/post_metrics.py`, and `backend/app/services/social_post_reconciliation.py`.

### 8.1 Status Polling (`sync_pending_deliveries`, lines 195-278)
- Target: `PublishDelivery.state == 'repliz_scheduled'`.
- Age bounds: `scheduled_for <= now - 2 min` (`_POLL_MIN_AGE`) and `scheduled_for >= now - 3 days` (`_POLL_MAX_AGE`).
- Batch size: 50 rows ordered by `scheduled_for ASC`.
- API Call: `GET /public/schedule/{schedule_id}`.
- Result mapping:
  - Failure (`failed`, `error`, `rejected`, `canceled`, `cancelled`): `deliv.state = 'failed'`, `deliv.last_error = _best_error_message(data)`.
  - Success (`success`, `published`, `posted`, `done`, `completed`): `deliv.state = 'published'`.

### 8.2 Three Paths to Obtain `facebook_post_id`
1. **Direct Phone Verification** (`phone_runner/modern_composer.py:resolve_latest_post_link`):
   - Polls device UI hierarchy and profile feed for live post link matching caption prefix after tap.
2. **Repliz Status Payload Extraction** (`post_metrics.py:extract_post_id`, line 93):
   - Inspects `postId` or `post_id` in Repliz JSON response matching regex `^\d+(_\d+)?$` (top-level or in `data`/`result`).
3. **Conservative Content Reconciliation** (`social_post_reconciliation.py:reconcile_delivery_repliz`, lines 207-364):
   - Triggered when Repliz status reports success but `extract_post_id()` returns `None`.
   - Fetches recent content: `GET /public/content?accountId=...&type=media` (bounded to `MAX_PAGINATION_PAGES = 3`).
   - Match filters (`match_single_content_item`, lines 92-205):
     1. `docs[].owner.id == destination.facebook_actor_id` (foreign owner rejected).
     2. `normalize_caption(docs[].description) == normalize_caption(delivery.caption_snapshot)`.
     3. $| \text{createdAt} - \text{expected\_time} | \le 2\text{ hours}$ (`DEFAULT_RECONCILIATION_WINDOW`).
   - Outcome:
     - Exactly 1 match: sets `delivery.facebook_post_id = doc['id']`, `delivery.facebook_permalink = doc['url']`, logs `DeliveryEvent(event_type="reconciled")`.
     - $>1$ matches: marks `reconciliation_ambiguous`, does NOT assign post ID.
     - 0 matches: marks `reconciliation_not_found`, does NOT assign post ID.

---

## 9. End-to-End Idempotency Guarantees

Mechanisms preventing duplicate social posts across crashes, retries, and concurrent workers:

1. **Database Unique Constraints**:
   - `uq_publish_job_destination`: `(publish_job_id, destination_id)` prevents multiple deliveries per destination for a job (`models/social_publishing.py:213`).
   - `uq_delivery_destination_facebook_post`: `(destination_id, facebook_post_id)` prevents recording the same live Facebook post twice (`models/social_publishing.py:214`).
   - `device_leases.delivery_id`: unique foreign key ensures a delivery can hold at most one device lease (`models/social_publishing.py:342`).
   - `operational_notifications.dedupe_key`: prevents duplicate owner alerts (`models/social_publishing.py:411`).
2. **Atomic State Claims**:
   - `PublishJob`: status transitions `pending_publish -> publishing` via single SQL `UPDATE ... WHERE status='pending_publish'` returning row count (`publisher.py:256-262`).
   - `PublishDelivery`: paced claim verifies eligible state, absent claim token, and absent uncertainty under row lock (`social_delivery_dispatch.py:244-263`).
3. **Serialized Pacing & In-Flight Gate**:
   - `claim_paced_delivery_for_dispatch` blocks any new immediate send if another immediate delivery is currently in `publishing` state (`social_delivery_dispatch.py:184-200`).
   - Live minimum gap (`min_gap_minutes`) verified against recent completed sends.
4. **Circuit Breaker on Ambiguity (`state = 'uncertain'`)**:
   - Any remote call failure where submission may have initiated (HTTP 5xx, read timeout, socket disconnect, missing post ID) transitions delivery to `uncertain`.
   - `uncertain` deliveries are excluded from automated retries and Repliz fallback.
5. **Fingerprint Deduplication in Fallback**:
   - `reconcile_and_verify_absence` checks if another delivery for the destination has identical `(caption_sha256, media_sha256)` in `published` or `repliz_scheduled` (`social_delivery_dispatch.py:823-837`).

---

## 10. Phone Fail-Closed Boundaries & Gating Invariants

Implemented in `phone_runner/publish.py`, `phone_runner/identity_proof.py`, `phone_runner/modern_composer.py`, and `phone_runner/vision_verify.py`.

### 10.1 Hardware & Lock Gates
- **Secure Keyguard Detection** (`publish.is_device_secure_locked`): checks for PIN, password, or pattern lock. Fails closed with `waiting_for_device` (`secure_lock_detected`). Never attempts to enter credentials or bypass screen lock.
- **Sleep Restoration** (`publish.execute_publish`): Initial display sleep state recorded; device put back to sleep in `finally` block on any exit path.

### 10.2 Identity & Navigation Gates
- **Conflict Short-Circuit** (`publish._check_candidate_identity`): If candidate capture observes root ID $\ne$ expected main profile ID or composer ID $\ne$ expected target ID, immediately fails with `identity_mismatch`. Model navigation is never invoked on conflicting sessions.
- **Proof Chain Verification** (`identity_proof.prove_identity`, `identity_proof.find_own_profile_name_identity`):
   - Profiles use `own_profile_name_v1`: exact profile name text in package `com.facebook.katana`, at least one clickable owner edit control (`Edit cover photo`, `Edit profile picture`, `Edit profile`, `Edit Personal details`), AND exact `Dashboard`. A clickable visitor marker fails closed. `_bind_profile_id` opens `fb://profile/<expected id>` once and requires the owner view, then the proof composer must match the author. No Settings/clipboard URL chain by default; deterministic navigation may scroll a restored profile back to its header.
  - `own_url_chain_v1` (Settings → Copy link → clipboard) only runs for profiles when `collect_own_url_proof_chain(profile_url_proof=True)` is passed (tests only). Facebook renamed the button to "Copy <name>'s link" (child text "Copy link") around 2026-10-28; `find_root_settings_controls` / `find_page_settings_controls` accept both labels.
  - Pages retain strict URL chain verification.
- **Identity UI retry** (`publish._identity_failure_is_ui_state`, 2026-10-31): when the proof could not read the screen (collector exception, or an error with `not found` / `missing` / `window focus` / `IME restoration` / `timed out` / `timeout` / `not awake` / `deadline exceeded` (2026-11-05) and no `mismatch` / `observed` / `does not match` / `visitor`), the runner returns `waiting_for_device` + `identity_ui_unready` (nothing posted) instead of `failed`. The backend (`social_delivery_dispatch.IDENTITY_UI_MAX_RETRIES = 2`) retries via routine recovery twice, then fails the delivery with `identity_unverified: identity could not be read after 3 tries`. A passed-but-contradictory proof or a seen mismatch stays a terminal `identity_unverified`. `identity_proof.verify_foreground_facebook` wakes a screen that fell asleep mid-proof once (`SCREEN_REWAKE_SETTLE_SECONDS` 1.5) and re-checks before failing; every later identity check still runs.
- **Play Store cleanup** (`publish._close_play_store_if_focused`): right before the identity gate, if `com.android.vending` holds window focus it is force-stopped (stage `foreground_cleanup`). Best effort; never touches Settings or the system updater.
- **IME Restoration Guard** (`identity_proof.PortalImeContext`): Records default IME on entry. In `finally` block, restores original IME and verifies readback. If restoration fails, returns `IME_RESTORATION_FAILED` and halts publishing.

### 10.3 Modern Composer Layout Gates (Fail-Closed)
- **Draft State Certainty** (`publish.execute_publish`): Empty proof composer checked and discarded before staging; share-intent composer must have empty input. If uncertain, fails closed with `draft_state_uncertain` to prevent typing over or publishing a preexisting draft.
- **Submission Flow Calibration** (`modern_composer`): Navigates via `Next` to "Post settings" (`UnifiedShareSheetActivity`) with verified `SHARE` button before proceeding.

### 10.4 Composer Content & Pre-Publish Gates
- **Literal Text Typing** (`modern_composer.type_caption_portal`): Caption typed via Portal IME (never interpolated into LLM prompts or passed to raw shell).
- **Caption paste + Enter** (`publish._enter_caption`): the caption is pasted from the Portal clipboard (typing is the fallback). When it ends in a `#hashtag` or `@mention`, one Enter (keycode 66) follows, because Facebook's suggestion list otherwise covers the caption and the `composer_after_typing` vision gate read a suggestion (`#motorsport`) instead of the text (2026-10-17). Both caption checks strip the trailing newline.
- **Content Pre-Tap Verification** (`modern_composer`, `vision_verify.verify_gate`):
  - Hierarchy verification: verifies caption input node contains expected text and media preview matches.
   - Pre-SHARE gates (`composer_before_typing`, `composer_after_typing`, `post_settings`) block submission on mismatch or API error. `_pre_share_vision_failure` returns `waiting_for_device` / `vision_unavailable` when vision could not run; a content mismatch returns `failed` / `vision_gate_failed`. At `composer_after_typing`, an empty/scrolled-off author is allowed only if the pre-typing gate already verified it; a visible wrong author still fails.

- **Photo albums (2-9 photos)** (`album_composer.attach_album_from_gallery`): `am start` cannot send a list of URIs, so SEND_MULTIPLE is not used. The album path keeps the empty, author-verified proof composer open, stages the photos at least 1.5 s apart (`stage_media_files(min_gap_seconds=...)`), opens the composer's **Gallery** → **Select multiple**, checks that the newest N picker items ("Photo, item K, taken on <minute>") fall inside the phone-clock staging window (and item N+1 does not), taps them oldest-first so the album keeps the media order, taps **Next** and requires tiles "Photo 1..N of N". Over 9 photos, any video, an unreadable phone clock/timezone, or any mismatch fails closed with `media_attach_failed`. Live rehearsal with 2 photos passed every gate on 2026-10-17.
- **Facebook pop-ups** (`interstitials.clear_interstitials`): taps only exact safe labels ("Not now", "Maybe later", "Skip", "No thanks", "Got it", and "Wait" on an Android "isn't responding" dialog). It never acts on composer/picker/share-sheet screens, and never when a danger label (Retry, Discard, Delete, Post, Share, Allow, Update, Log out, ...) is in the same dialog container. It is called when the own profile is not found, when the share intent does not reach the composer, and before each post-link search. After SHARE, a posting problem on screen ("Couldn't post", a "Retry"/"Try again" button) stops the search with no tap, and the delivery stays `uncertain`.
- **Reel duration pre-check** (`dispatch_phone_delivery`): configured by `phone_video_max_seconds: int = 90` in `backend/app/config.py:Settings`. Evaluated before device selection/leasing: if `delivery.media_kind == "video"` and the parent `PublishJob.video_duration_s` is not None and exceeds `phone_video_max_seconds`, publishing aborts immediately without leasing a device or calling the runner. The delivery is set to `state="failed"`, `last_error="video_too_long: <dur:.0f> s > <max> s"`, and emits a `failed` event. No device lease is acquired, runner is not invoked, and no Repliz fallback is attempted. When `video_duration_s` is None, no check is performed.
- **300 MiB media cap and size-aware pre-SHARE budget** (runner side): `media_staging.MAX_MEDIA_BYTES = 300 * 1024 * 1024` (300 MiB hard limit); files exceeding 300 MiB fail closed with `media_too_large` before download or staging. Pre-SHARE time budget dynamically scales with media payload size up to the deadline before SHARE, preventing slow video transfers from starving validation.

### 10.5 Post-Tap Verification Gate
- **Single Submission**: `SHARE` button tapped exactly once with humanized coordinate jitter (`publish_may_have_started = True`).
- **Post Link Resolution & Downgrade** (`modern_composer.resolve_latest_post_link`): Resolves permalink by finding post matching first 30 normalized caption characters on profile. For photo posts, attempts link resolution. For video/Reels: 8 attempts 15s apart bounded by a deadline, checking profile posts, All tab + pull-to-reload + Feed fallback. If unproven within the deadline, delivery downgrades to `uncertain` (`post_identity_unproven`). Post-SHARE vision failure (`posted` gate) also downgrades to `uncertain` (`post_vision_mismatch`). Never retries after SHARE.
- **Posted-Gate Override** (`publish.py:_execute_publish_core`): a caption-matched post ID with matching permalink owner overrides a `posted` vision mismatch. Vision-service failure after link resolution also keeps the proven post published. A missing/unproven post link remains uncertain.
- **When Facebook is Closed**:
  Facebook app is explicitly force-stopped/closed:
  1. At the start of every run (ensures clean initial state).
  2. After a confirmed post (leaves app in clean background state).
  3. Right away after a failure before SHARE (discards dirty draft and cleans up).
  4. Never right after SHARE (allows background upload/processing to complete; also closed after 4th unconfirmed follow-up attempt).

### 10.6 Follow-up Checks for Late Phone Posts (`follow_up_uncertain_phone_post`)
- **Celery Task & Beat**: `follow_up_phone_posts` (`backend/app/tasks/social_delivery.py`) routes to queue `delivery` in `celery_app.py` and ticks every 60s via beat schedule `follow-up-phone-posts`.
- **Candidates**: `state == "uncertain"`, `device_id` is not null, `facebook_post_id` is null, `uncertain_since >= now - 45 min`.
- **Schedule**: `FOLLOW_UP_OFFSETS_MIN = (2, 5, 10, 20)` minutes after `uncertain_since`. `done` = count of `DeliveryEvent(event_type="post_link_followup")`. Due when `done < 4` and `now >= uncertain_since + FOLLOW_UP_OFFSETS_MIN[done]`. At most one due delivery is processed per call (oldest `uncertain_since` first).
- **Device Lease**: Leases device exclusively (`lease_phone_device_by_id`, TTL 45m). If busy or unavailable, skips WITHOUT recording an attempt. Always releases the lease in `finally`.
- **Runner Query**: Read-only `POST /resolve-post` (`serial`, `main_profile`, `destination`, `caption`, `media_kind`, `max_seconds=150`, `close_app_after=(done == 3)`). Response 409 `busy` records no attempt.
- **Resolution**:
  - `found` AND valid post ID AND `actor_id == destination.facebook_actor_id`: sets `state="published"`, `actual_method="phone"`, `facebook_post_id`, `facebook_permalink`, `published_at=now`, `last_error=None`, records `post_link_followup` event (`status="found"`), and adds `DeliveryEvent(event_type="published", details={"via": "post_link_followup", ...})`.
  - `found` with mismatched actor: stays `uncertain`, records event with `status="actor_mismatch"`.
  - `not_found` / `error`: stays `uncertain`, records event with `status=...` and attempt number.
  - Invariants: NEVER sets `failed`, never re-dispatches, never calls `/publish`.
- **Owner Alert**: After the 4th attempt without success (`done == 3`), leaves `uncertain` and sends ONE owner alert via `notify_owner_via_hermes` (`dedupe_key=f"phone_post_followup:{delivery.id}"`):
  `"Phone post for delivery <id> (<destination name>) is still unconfirmed 20 minutes after SHARE. The post link was not found on the profile. Check the profile and use Mark posted / Mark not posted. It will not be posted again automatically."`

---

## 11. Modern Facebook Composer Flow (2026-10)

Implemented across `phone_runner/publish.py`, `phone_runner/modern_composer.py`, `phone_runner/identity_proof.py`, `phone_runner/media_staging.py`, `phone_runner/vision_verify.py`, and `phone_runner/humanize.py`.

### 11.1 Identity & Navigation Protocol
- **Profile Name Identity (`own_profile_name_v1`)**:
   - Facebook Profile destinations require exact profile name, a clickable owner edit control AND `Dashboard`, plus matching proof-composer author (`identity_proof.find_own_profile_name_identity`). `Dashboard` alone is insufficient; `Messages` is not owner proof.
  - The numeric id is bound by the `fb://profile/<id>` owner-view check (`identity_proof._bind_profile_id`, live-verified 2026-10-30: own id → owner view, other id → visitor view with "Follow"). The URL chain (`own_url_chain_v1`) is not used for profiles in production. Pages strictly require URL chain.
  - Exact match prevents posting as Page or mismatched account ("Agung Baelah" != "Agung"). If session is a Page, name check fails and nothing is typed.
   - **Page id on the phone**: New Pages Experience URL chains expose a profile-style page ID, not necessarily Repliz's classic `generatedId`. Store the phone-observed ID in `facebook_actor_id`; a different ID fails closed with `identity_unverified`.
- **Deterministic Root Navigation** (`identity_proof._try_open_own_root_without_model`):
  - Direct intents (`fb://feed`, `fb://profile`) with `allow_name_identity=True` for profile targets.
   - Model navigation (`mobilerun`) is last resort: capped at 120s with 60s reserved (`MODEL_NAV_MAX_SECONDS = 120`, `MODEL_NAV_RESERVE_SECONDS = 60`).
   - `_switch_back_to_root_session` tries the exact root row first. If absent, `_switch_via_other_account_entry` opens the unique account-level entry (`another account`, `other account`, `switch account`, `see all profiles`), then requires the exact root row. Login/credential screens stop navigation. It never chooses an arbitrary account or types credentials.

### 11.2 Media Attachment via Share Intent
- Photo and Video/Reel media attached via Android `SEND` share intent instead of UI gallery picker:
  - Component: `com.facebook.katana/com.facebook.composer.shareintent.ImplicitShareIntentHandlerDefaultAlias`.
  - Media URI: `content://media/external/images/media/<id>` (photo) or `content://media/external/video/media/<id>` (video) from MediaStore content query on staged media file (`media_staging.get_media_content_uri`).
  - Opens `ComposerActivity` with media attached, correct author header, and empty "What's on your mind?" field.
  - Back dialog shows "Discard changes?" with `KEEP EDITING` / `DISCARD` (contrasts with proof composer's "Discard post").

### 11.3 Step-by-Step Modern Publishing Pipeline

```
[1. Verify Root & Proof Composer]
        │  Verify own-profile identity; open & verify empty proof composer -> DISCARD
        ▼
[2. Stage Media & Launch Share Intent]
        │  Stage image or video via MediaStore -> SEND share intent opens ComposerActivity
        ▼
[3. Vision Gate: composer_before_typing]
        │  Verify author header, attached media, empty input (fail-closed)
        ▼
[4. Type Caption via Portal IME]
        │  Portal clipboard paste (Portal IME typing fallback); A11y checks exact caption
        ▼
[5. Vision Gate: composer_after_typing]
        │  Verify caption rendered with photo intact (fail-closed)
        ▼
[6. Tap Next -> Post Settings Screen]
        │  Opens UnifiedShareSheetActivity with Button "SHARE"
        ▼
[7. Vision Gate: post_settings]
        │  Verify SHARE screen layout (fail-closed)
        ▼
[8. Tap SHARE (Exactly Once)]  ──► [Point of No Return]
        │  Humanized coordinate tap; never retried on failure
        ▼
[9. Resolve Post Link]
        │  modern_composer.resolve_latest_post_link: profile search matching first 30
        │  chars of normalized caption -> "More options" above post -> "Copy link"
        │  (for video: 8 attempts 15s apart bounded by deadline, All tab + pull-to-reload
        │  + Feed fallback). Missing link -> state='uncertain'
        ▼
[10. Vision Gate: posted]
        │  Verify live post on profile feed (mismatch -> state='uncertain')
        ▼
[11. State: published]
```

### 11.4 Vision Verification Gates (`vision_verify.py`)
- Verification only (no agentic decisions): Gemini models via VPS 9Router (`MOBILERUN_API_BASE` + runner key, request sets `"stream": false`). Model IDs filtered to Gemini and intersected with live `/v1/models`.
- Fail-closed boundary:
   - **Pre-SHARE gates** (`composer_before_typing`, `composer_after_typing`, `post_settings`): mismatches fail with `vision_gate_failed`; unavailable vision waits with `vision_unavailable`. No SHARE occurs on either outcome.
   - **Post-SHARE gate** (`posted`): submission cannot be undone. An unoverridden mismatch becomes `uncertain` / `post_vision_mismatch`; proven post identity can override it as described in §10.5.
- Gate verdicts recorded in `evidence.vision_gates`.
- `vision_verify.py:ask_vision` uses 25s per model inside a 110s call budget. `_prepare_vision_image` uses ffmpeg JPEG shrinking (screenshot max side 2000, reference 1280); above 3,000,000 combined raw bytes it tries half those dimensions. Shrink failure retains original bytes/MIME, so the threshold is not a hard payload cap. Safe attempt traces retain only model, elapsed time and outcome, never response bodies or credentials.

### 11.5 Human-like Interaction & Time Budgets
- **Anti-Bot Interaction (`humanize.py`)**: Random tap coordinates within inner 60% of bounding boxes; randomized 1–3s pauses between actions. Real device, real app only.
- **Time Limits**:
  - Backend timeout: `PhoneRunnerClient.publish_timeout = 600`s (triggers `uncertain` on expiration). Raised from 280 s on 2026-10-17: a real rehearsal reached SHARE after ~170 s (identity proof + 3 vision gates + typing a 426-char caption).
   - Runner internal budget: `publish.py:pre_share_max_seconds` starts at 300s and scales with media size up to 420s; the whole-run budget is 540s. Pre-SHARE exhaustion returns `waiting_for_device` / `time_budget_exceeded`. A resolved post can remain published when the final vision service is unavailable; without proven post identity it remains uncertain.
  - Rehearsal facts (2026-10-17): `fb://profile` restores the last scroll position, so deterministic navigation scrolls the own profile back to the top (only when the owner-only "Manage posts" control is visible). A long caption plus the on-screen keyboard hides the attached photo; the runner hides the keyboard with one Back press (tapping KEEP EDITING if the discard dialog appears) before re-checking the photo.

### 11.6 Rehearsal Mode & Host Requirements
- **Rehearsal Endpoint**: `POST /rehearse` on phone runner runs full flow through step 7 (`post_settings`), discards draft, and returns `rehearsal_ok`. Used by operators via `.notes/rehearse/rehearse_delivery.py` on VPS host (never called by Celery backend).
- **Media Host Allowlist**: `/etc/phone-runner.env` requires `PHONE_RUNNER_MEDIA_HOSTS=autostudio.pro` to download staged media.
- **Python 3.12 TLS Compatibility**: `media_staging.PinnedIPHTTPSHandler` omits `check_hostname` from `do_open()` (removed in Python 3.12); TLS verified via `wrap_socket(server_hostname=...)`.
- **Operational Deploy Guard**: Never push to `main` while any delivery is `publishing` or an active device lease exists. Container restart kills running `/publish` and strands delivery in `uncertain`.

---

## 12. Cleanup & Queue Monitoring

### 12.1 Media Retention (`backend/app/tasks/cleanup.py`)
- `cleanup_old_media`: normal source posts need at least one job, all jobs published, and all `cleanup_at` values elapsed. `_post_media_still_needed` retains any schedule until go-live + 6h, pending-publish/publishing jobs up to 48h (or indefinitely without creation time), and live delivery states with no schedule or a schedule newer than `now - 6h`. `_radar_still_watching` protects active `viral_only` destinations lacking a job inside `radar_track_max_age_h` (default 48). No age-only orphan/stuck-post deletion runs.
- `cleanup_old_designs`: normal candidates are published jobs with `published_at` at least 7 days old, or missing `published_at` and `updated_at` at least 21 days old. Pending, review, rendering, failed and skipped normal jobs are not age-deleted. `_job_go_live_pending` and latest `_job_go_live_time` protect scheduled deliveries and the 7-day live retention; deletion clears both design path and URL.
- `cleanup_old_ai_videos`: normal publish output follows 7-day published retention, with 14-day `updated_at` fallback when publish time is absent. A `VideoJob` must be `done` and linked to a published parent before video/thumbnail/narration files qualify. Unpublished/failed stock is retained. Paths must resolve under `storage_base_path/ai_videos`; deletion clears stored video/thumbnail paths and URLs.
- `_cleanup_facebook_photos`: used originals need matching jobs, every job published, no pending go-live and every latest live timestamp strictly older than 7 days; missing jobs/timestamps retain the original. Successful deletion clears `image_local_path`.
- `cleanup_old_clip_qc_files`: deletes attempt/contact-sheet files older than 14 days except videos in `queued`, `claimed`, `generating`, `uploaded` or linked to a pending-review publish job. QC artifacts have their own lifecycle. `cleanup_old_thumbs` deletes cached WEBP thumbnails older than 30 days, weekly.
- The `_LIVE_DELIVERY_STATES` guard only protects a dated live delivery until scheduled time + 6h. It does not retain an overdue uncertain/needs-human delivery indefinitely; parent success is not proof that every destination finished.

### 12.2 Backlog Watchdog (`backend/app/tasks/queue_watchdog.py:check_queue_backlog`)
- Beat `check-queue-backlog`: every 300s, expires 290s. `backlog_over_limits` checks Redis `LLEN` for strict exceedance: `celery: 200`, `crawl: 50`, `visual: 100`, `ai: 150`, `video: 50`, `gallery: 300`. Queue `delivery` is not monitored here.
- Redis `queue_watchdog:alerted:<queue>` (`SET NX EX`, 3600s) limits owner Telegram alerts to once per queue/hour. Alerts identify the matching worker via `phone_watchdog.send_telegram_fallback`; this task does not restart workers. Redis errors log and return an empty result.

---

## Verified claims

backend/app/tasks/publisher.py:274 - Branch between legacy Repliz publishing and S1b delivery ledger path is decided by checking if SocialDestination has count > 0 with content_project_id == job.fanpage_id and is_active == True.
backend/app/tasks/publisher.py:publish_job - services/job_status_sync.py closes accepted or terminal delivery sets; only unfinished publishing jobs return to pending_publish. The sync-finished-publish-jobs beat task checks finished pending jobs every 15 min without duplicating deliveries.
backend/app/services/social_delivery_dispatch.py:133 - Lock ordering strictly acquires SocialDestination row lock before PublishDelivery row lock and applies populate_existing to prevent stale session data and deadlocks.
backend/app/services/social_delivery_dispatch.py:184 - Immediate phone or live dispatch claims are blocked if another immediate delivery on the same destination is currently in the publishing state, regardless of minimum gap.
backend/app/services/social_delivery_dispatch.py:330 - Stale publishing claims expire after exactly 45 minutes (stale_ttl_minutes = 45) and transition to uncertain to prevent automatic duplicate posting.
backend/app/services/social_delivery_dispatch.py:585 - Phone device leases are acquired exclusively for 45 minutes (lease_ttl_minutes = 45) in the device_leases table.
backend/app/services/publishing_schedule.py:31 - Audience scheduling uses default active hours of 07:00 to 22:00 (DEFAULT_ACTIVE_START_HOUR = 7, DEFAULT_ACTIVE_END_HOUR = 22), minimum lead of 60 seconds (MIN_LEAD_SECONDS = 60), and search horizon of 60 days (_MAX_BOUNDED_DAYS = 60).
backend/app/services/publishing_schedule.py - Candidate slot jitter within each quantile cell uses seed SHA-256 digest of f"{current_date.isoformat()}:{namespace}:{idx}"; jitter span is centered at 0.5 and bounded by GRID_JITTER_SPAN (0.70) in wide cells, shrinking in narrow cells to prevent gap violations; gap floor is 15 minutes and day capacity is capped by fitting_daily_target.
backend/app/services/social_delivery_policy.py:216 - Automated fallback to Repliz is strictly limited to Pages (kind == 'page'), requires incident state needs_human, forbids uncertain deliveries, and requires verified absence of duplicates.
phone_runner/recovery.py:563 - Larger repairs enter awaiting_approval with an automatic approval deadline set to exactly 15 minutes (deadline = now + timedelta(minutes=15)) if the owner has not responded.
backend/app/services/social_post_reconciliation.py:30 - Post reconciliation against Repliz GET /public/content queries up to 3 pages (MAX_PAGINATION_PAGES = 3) and enforces an expected publish time tolerance of +/- 2 hours (DEFAULT_RECONCILIATION_WINDOW = timedelta(hours=2)).
phone_runner/identity_proof.py - Profile destination identity verification uses own_profile_name_v1 (exact profile name, owner-only controls, fb://profile/<id> owner view, matching composer author) and skips the Settings/Copy-link chain unless profile_url_proof=True.
phone_runner/media_staging.py - Photo and video/Reel attachments on modern Facebook composer use Android SEND share intent to ImplicitShareIntentHandlerDefaultAlias with MediaStore content URI.

# Deep Dive D2: Publishing Ledger, Delivery Dispatch, and Phone Automation

> Generated 2026-10-16 at main 25edf38 from a full read of the listed files. The "Verified claims" list at the bottom was checked line by line against the code. Other details are a careful map but not each re-checked. Read the real code before changing behaviour, and update this file when the code changes.


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
   │      │      │      ├─► If all in ('repliz_scheduled', 'published'):
   │      │      │      │      ├─► job.status = 'published', job.published_at = utcnow
   │      │      │      │      ├─► If single Repliz dest: sync job.repliz_schedule_id, job.scheduled_for
   │      │      │      │      ├─► ScrapedArticle.status = 'published' (if source_article_id present)
   │      │      │      │      └─► _maybe_mark_post_done(db, post) (if post_id present)
   │      │      │      └─► Else:
   │      │      │             └─► job.status = 'pending_publish' (releases lock for Celery recovery sweep)
   │      │      └─► db.commit()
   │      │
   │      └─► has_destinations == 0 ──► [LEGACY REPLIZ PATH] (publisher.py:324-415)
   │             ├─► Requires fanpage.repliz_account_id; if missing -> job.status = 'failed'
   │             ├─► Media routing:
   │             │      ├─► news_content, ig_recreate, discussion, pinterest_content, facebook_recreate: _publish_news_job()
   │             │      ├─► youtube_clip, ai_video: _publish_video_job()
   │             │      └─► ig_repost: create_album_schedule() (>=2 images) or create_image_schedule() (1 image)
   │             ├─► Slot allocation: _next_schedule_at(db, job.fanpage_id, breaking=job.is_breaking)
   │             ├─► Sets job.status = 'published', job.cleanup_at = now + 6h (or 48h for video)
   │             └─► _maybe_mark_post_done(db, post)
```

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
| `uncertain` | In-flight failure, timeout, 5xx, or unproven post ID | `publishing` | `published` (via reconciliation) | `social_delivery_dispatch.py:352, 504, 523, 547, 565, 1389, 1440, 1469` |
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
- `MAX_DAILY_TARGET = 1000` (line 88).
- Jitter bounds: `[0.15, 0.85]` of quantile cell (line 184).

### 4.2 Audience Hour Weighting (`get_hour_weight`, lines 37-46)
- Midday window (12:00 to 14:00 local): weight = `1.3`.
- Evening window (18:00 to 21:00 local): weight = `1.3`.
- Baseline active window: weight = `1.0`.
- Sleep window: weight = `0.0`.

### 4.3 Quantile Grid & Deterministic Jitter Math (`compute_audience_slot`, lines 58-236)
1. **Minute Array**: 1,440 minute weights computed per local calendar day based on sleep window or defaults.
2. **Target Cell**: Daily target $N$ partitions total weight $W$: $[ \frac{i}{N} W, \frac{i+1}{N} W ]$.
3. **Cumulative Search**: Finds index interval $[k_{start}, k_{end}]$ within active minutes where cumulative weight matches cell bounds.
4. **Deterministic Pseudo-Random Jitter**:
   $$\text{seed} = \text{SHA256}(\texttt{"YYYY-MM-DD:namespace:idx"})$$
   $$\text{rnd} = \frac{\text{uint64}(\text{seed}[0:8])}{2^{64}-1}, \quad \text{jitter} = 0.15 + 0.70 \times \text{rnd}$$
   $$k_{chosen} = \text{round}(k_{start} + \text{jitter} \times (k_{end} - k_{start}))$$
5. **Candidate Filtering**:
   - `cand_utc >= now_utc + 60s`
   - `cand_local` outside sleep window
   - Bidirectional gap separation: for every existing reservation $E$, $|cand - E| \ge \text{min\_gap\_minutes} \times 60$.
   - Rollover: if calendar day has $\ge \text{daily\_target}$ reservations or no candidates qualify, advances to next day (up to 60 days).

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
  - Inspects all active destinations for the fan page: returns `True` if at least one destination can schedule the news job within `destination.news_stale_hours` (default 24 h) from job creation. Used by `visual_fair_queue` to skip dead news jobs before generating images.

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
  - Profiles use `own_profile_name_v1` as the primary path (2026-10-30): exact profile name on the own-profile root screen, owner-only controls (Edit cover photo / Edit profile picture / Dashboard), then ONE deep link `fb://profile/<expected id>` that must show the owner view (a visitor view with Follow / Add friend / Message fails closed, `Profile id binding failed`), then the matching composer author header. No Settings visit, clipboard or scrolling for profiles.
  - `own_url_chain_v1` (Settings → Copy link → clipboard) only runs for profiles when `collect_own_url_proof_chain(profile_url_proof=True)` is passed (tests only). Facebook renamed the button to "Copy <name>'s link" (child text "Copy link") around 2026-10-28; `find_root_settings_controls` / `find_page_settings_controls` accept both labels.
  - Pages retain strict URL chain verification.
- **IME Restoration Guard** (`identity_proof.PortalImeContext`): Records default IME on entry. In `finally` block, restores original IME and verifies readback. If restoration fails, returns `IME_RESTORATION_FAILED` and halts publishing.

### 10.3 Modern Composer Layout Gates (Fail-Closed)
- **Draft State Certainty** (`publish.execute_publish`): Empty proof composer checked and discarded before staging; share-intent composer must have empty input. If uncertain, fails closed with `draft_state_uncertain` to prevent typing over or publishing a preexisting draft.
- **Submission Flow Calibration** (`modern_composer`): Navigates via `Next` to "Post settings" (`UnifiedShareSheetActivity`) with verified `SHARE` button before proceeding.

### 10.4 Composer Content & Pre-Publish Gates
- **Literal Text Typing** (`modern_composer.type_caption_portal`): Caption typed via Portal IME (never interpolated into LLM prompts or passed to raw shell).
- **Caption paste + Enter** (`publish._enter_caption`): the caption is pasted from the Portal clipboard (typing is the fallback). When it ends in a `#hashtag` or `@mention`, one Enter (keycode 66) follows, because Facebook's suggestion list otherwise covers the caption and the `composer_after_typing` vision gate read a suggestion (`#motorsport`) instead of the text (2026-10-17). Both caption checks strip the trailing newline.
- **Content Pre-Tap Verification** (`modern_composer`, `vision_verify.verify_gate`):
  - Hierarchy verification: verifies caption input node contains expected text and media preview matches.
  - Pre-SHARE vision verification gates (`composer_before_typing`, `composer_after_typing`, `post_settings`) fail closed (`vision_gate_failed`) on any mismatch or vision API error.

- **Photo albums (2-9 photos)** (`album_composer.attach_album_from_gallery`): `am start` cannot send a list of URIs, so SEND_MULTIPLE is not used. The album path keeps the empty, author-verified proof composer open, stages the photos at least 1.5 s apart (`stage_media_files(min_gap_seconds=...)`), opens the composer's **Gallery** → **Select multiple**, checks that the newest N picker items ("Photo, item K, taken on <minute>") fall inside the phone-clock staging window (and item N+1 does not), taps them oldest-first so the album keeps the media order, taps **Next** and requires tiles "Photo 1..N of N". Over 9 photos, any video, an unreadable phone clock/timezone, or any mismatch fails closed with `media_attach_failed`. Live rehearsal with 2 photos passed every gate on 2026-10-17.
- **Facebook pop-ups** (`interstitials.clear_interstitials`): taps only exact safe labels ("Not now", "Maybe later", "Skip", "No thanks", "Got it", and "Wait" on an Android "isn't responding" dialog). It never acts on composer/picker/share-sheet screens, and never when a danger label (Retry, Discard, Delete, Post, Share, Allow, Update, Log out, ...) is in the same dialog container. It is called when the own profile is not found, when the share intent does not reach the composer, and before each post-link search. After SHARE, a posting problem on screen ("Couldn't post", a "Retry"/"Try again" button) stops the search with no tap, and the delivery stays `uncertain`.

### 10.5 Post-Tap Verification Gate
- **Single Submission**: `SHARE` button tapped exactly once with humanized coordinate jitter (`publish_may_have_started = True`).
- **Post Link Resolution & Downgrade** (`modern_composer.resolve_latest_post_link`): Resolves permalink by finding post matching first 30 normalized caption characters on profile. If unproven within 4 attempts (10s apart), delivery downgrades to `uncertain` (`post_identity_unproven`). Post-SHARE vision failure (`posted` gate) also downgrades to `uncertain` (`post_vision_mismatch`). Never retries after SHARE.

---

## 11. Modern Facebook Composer Flow (2026-10)

Implemented across `phone_runner/publish.py`, `phone_runner/modern_composer.py`, `phone_runner/identity_proof.py`, `phone_runner/media_staging.py`, `phone_runner/vision_verify.py`, and `phone_runner/humanize.py`.

### 11.1 Identity & Navigation Protocol
- **Profile Name Identity (`own_profile_name_v1`)**:
  - Facebook Profile destinations require: exact profile name on own-profile root screen, presence of owner-only controls ("Edit cover photo", "Edit profile picture", or "Dashboard"), and composer author header matching exact profile name (`identity_proof.find_own_profile_name_identity`).
  - The numeric id is bound by the `fb://profile/<id>` owner-view check (`identity_proof._bind_profile_id`, live-verified 2026-10-30: own id → owner view, other id → visitor view with "Follow"). The URL chain (`own_url_chain_v1`) is not used for profiles in production. Pages strictly require URL chain.
  - Exact match prevents posting as Page or mismatched account ("Agung Baelah" != "Agung"). If session is a Page, name check fails and nothing is typed.
  - **Page id on the phone**: for New Pages Experience pages the URL chain sees the page's profile-style id (e.g. GP Weekend `100094901131361`), not the classic page id Repliz reports as `generatedId` (`113659828455762`). A phone `facebook_page` destination must store the profile-style id in `facebook_actor_id`; the classic id fails closed with `identity_unverified`. Page rehearsal (GP Weekend under Agung) passed every gate on 2026-10-17, and a profile run that starts in the Page session switches back to the root profile.
- **Deterministic Root Navigation** (`identity_proof._try_open_own_root_without_model`):
  - Direct intents (`fb://feed`, `fb://profile`) with `allow_name_identity=True` for profile targets.
  - Model navigation (`mobilerun`) is last resort: capped at 120s with 60s reserved (`MODEL_NAV_MAX_SECONDS = 120`, `MODEL_NAV_RESERVE_SECONDS = 60`).

### 11.2 Media Attachment via Share Intent
- Photo attached via Android `SEND` share intent instead of UI gallery picker:
  - Component: `com.facebook.katana/com.facebook.composer.shareintent.ImplicitShareIntentHandlerDefaultAlias`.
  - Media URI: `content://media/external/images/media/<id>` from MediaStore content query on staged image file (`media_staging.get_media_content_uri`).
  - Opens `ComposerActivity` with photo attached ("Photo", "Edit photo", "Remove photo"), correct author header, and empty "What's on your mind?" field.
  - Back dialog shows "Discard changes?" with `KEEP EDITING` / `DISCARD` (contrasts with proof composer's "Discard post").

### 11.3 Step-by-Step Modern Publishing Pipeline

```
[1. Verify Root & Proof Composer]
        │  Verify own-profile identity; open & verify empty proof composer -> DISCARD
        ▼
[2. Stage Media & Launch Share Intent]
        │  Stage image via MediaStore -> SEND share intent opens ComposerActivity
        ▼
[3. Vision Gate: composer_before_typing]
        │  Verify author header, attached photo, empty input (fail-closed)
        ▼
[4. Type Caption via Portal IME]
        │  mobilerun type -> A11y tree check confirms exact caption text
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
        │  (up to 4 attempts, 10s apart). Missing link -> state='uncertain'
        ▼
[10. Vision Gate: posted]
        │  Verify live post on profile feed (mismatch -> state='uncertain')
        ▼
[11. State: published]
```

### 11.4 Vision Verification Gates (`vision_verify.py`)
- Verification only (no agentic decisions): Gemini models via VPS 9Router (`MOBILERUN_API_BASE` + runner key, request sets `"stream": false`). Model IDs filtered to Gemini and intersected with live `/v1/models`.
- Fail-closed boundary:
  - **Pre-SHARE gates** (`composer_before_typing`, `composer_after_typing`, `post_settings`): Any failure or API error aborts and discards draft -> `vision_gate_failed`.
  - **Post-SHARE gates** (`posted`): Submission already executed; failure cannot abort, only downgrades to `uncertain` (`post_vision_mismatch`).
- Gate verdicts recorded in `evidence.vision_gates`.

### 11.5 Human-like Interaction & Time Budgets
- **Anti-Bot Interaction (`humanize.py`)**: Random tap coordinates within inner 60% of bounding boxes; randomized 1–3s pauses between actions. Real device, real app only.
- **Time Limits**:
  - Backend timeout: `PhoneRunnerClient.publish_timeout = 600`s (triggers `uncertain` on expiration). Raised from 280 s on 2026-10-17: a real rehearsal reached SHARE after ~170 s (identity proof + 3 vision gates + typing a 426-char caption).
  - Runner internal budget: aborts safely before SHARE if elapsed time > 300 s (`time_budget_exceeded`); the whole run stays under 540 s.
  - Rehearsal facts (2026-10-17): `fb://profile` restores the last scroll position, so deterministic navigation scrolls the own profile back to the top (only when the owner-only "Manage posts" control is visible). A long caption plus the on-screen keyboard hides the attached photo; the runner hides the keyboard with one Back press (tapping KEEP EDITING if the discard dialog appears) before re-checking the photo.

### 11.6 Rehearsal Mode & Host Requirements
- **Rehearsal Endpoint**: `POST /rehearse` on phone runner runs full flow through step 7 (`post_settings`), discards draft, and returns `rehearsal_ok`. Used by operators via `.notes/rehearse/rehearse_delivery.py` on VPS host (never called by Celery backend).
- **Media Host Allowlist**: `/etc/phone-runner.env` requires `PHONE_RUNNER_MEDIA_HOSTS=autostudio.pro` to download staged media.
- **Python 3.12 TLS Compatibility**: `media_staging.PinnedIPHTTPSHandler` omits `check_hostname` from `do_open()` (removed in Python 3.12); TLS verified via `wrap_socket(server_hostname=...)`.
- **Operational Deploy Guard**: Never push to `main` while any delivery is `publishing` or an active device lease exists. Container restart kills running `/publish` and strands delivery in `uncertain`.

---

## Verified claims

backend/app/tasks/publisher.py:274 - Branch between legacy Repliz publishing and S1b delivery ledger path is decided by checking if SocialDestination has count > 0 with content_project_id == job.fanpage_id and is_active == True.
backend/app/tasks/publisher.py:319 - When delivery ledger path has not all deliveries accepted, parent PublishJob.status is released back to pending_publish so Celery recovery sweep can re-dispatch without duplicating deliveries.
backend/app/services/social_delivery_dispatch.py:133 - Lock ordering strictly acquires SocialDestination row lock before PublishDelivery row lock and applies populate_existing to prevent stale session data and deadlocks.
backend/app/services/social_delivery_dispatch.py:184 - Immediate phone or live dispatch claims are blocked if another immediate delivery on the same destination is currently in the publishing state, regardless of minimum gap.
backend/app/services/social_delivery_dispatch.py:330 - Stale publishing claims expire after exactly 45 minutes (stale_ttl_minutes = 45) and transition to uncertain to prevent automatic duplicate posting.
backend/app/services/social_delivery_dispatch.py:585 - Phone device leases are acquired exclusively for 45 minutes (lease_ttl_minutes = 45) in the device_leases table.
backend/app/services/publishing_schedule.py:31 - Audience scheduling uses default active hours of 07:00 to 22:00 (DEFAULT_ACTIVE_START_HOUR = 7, DEFAULT_ACTIVE_END_HOUR = 22), minimum lead of 60 seconds (MIN_LEAD_SECONDS = 60), and search horizon of 60 days (_MAX_BOUNDED_DAYS = 60).
backend/app/services/publishing_schedule.py:182 - Candidate slot jitter within each quantile cell is deterministically bounded to [0.15, 0.85] via the SHA-256 digest of f"{current_date.isoformat()}:{namespace}:{idx}".
backend/app/services/social_delivery_policy.py:216 - Automated fallback to Repliz is strictly limited to Pages (kind == 'page'), requires incident state needs_human, forbids uncertain deliveries, and requires verified absence of duplicates.
phone_runner/recovery.py:563 - Larger repairs enter awaiting_approval with an automatic approval deadline set to exactly 15 minutes (deadline = now + timedelta(minutes=15)) if the owner has not responded.
backend/app/services/social_post_reconciliation.py:30 - Post reconciliation against Repliz GET /public/content queries up to 3 pages (MAX_PAGINATION_PAGES = 3) and enforces an expected publish time tolerance of +/- 2 hours (DEFAULT_RECONCILIATION_WINDOW = timedelta(hours=2)).
phone_runner/identity_proof.py - Profile destination identity verification uses own_profile_name_v1 (exact profile name, owner-only controls, fb://profile/<id> owner view, matching composer author) and skips the Settings/Copy-link chain unless profile_url_proof=True.
phone_runner/media_staging.py - Photo attachments on modern Facebook composer use Android SEND share intent to ImplicitShareIntentHandlerDefaultAlias with MediaStore content URI.

# Hermes Strategist API

Base URL: `https://<studio-host>/api`
Dashboard API prefix may differ. Check deployment.

## 1. Getting a token

Hermes requires an API token to access the studio.
- **Creation**: The owner creates it in the dashboard (Settings → Hermes API tokens) or via `POST /strategy/tokens` using an admin login.
- **Scopes**: Select `read`, `memory:write`, and `recommendations:write`.
- **Storage**: The plaintext token starts with `hst_` and is shown ONLY ONCE. Store it securely on the Hermes side in the `HERMES_STUDIO_TOKEN` environment variable.
- **Revocation/Rotation**: If compromised, revoke the token in the Settings UI (or `DELETE /strategy/tokens/{id}` as admin) and create a new one.

## 2. Endpoints & Examples

Base environment variables for examples:
```bash
export STUDIO_URL="https://your-studio-host/api"
export HERMES_STUDIO_TOKEN="hst_your_token_here"
```

### GET /hermes/fanpages
Scope: `read`

```bash
curl -H "Authorization: Bearer $HERMES_STUDIO_TOKEN" "$STUDIO_URL/hermes/fanpages"
```
```json
[
  {
    "id": 1,
    "name": "Auto & Motorsport",
    "is_active": true,
    "timezone": "Europe/London",
    "target_country": "UK",
    "publish_sleep_start_hour": 23,
    "publish_sleep_end_hour": 7,
    "publish_daily_limit": 10,
    "mode2_gallery_niches": ["f1"],
    "caption_language": "en"
  }
]
```

### GET /hermes/analytics/fanpages/{id}?days=28
Scope: `read`

```bash
curl -H "Authorization: Bearer $HERMES_STUDIO_TOKEN" "$STUDIO_URL/hermes/analytics/fanpages/1?days=28"
```
```json
{
  "fanpage": {"id": 1, "name": "intan", "timezone": "Europe/London", "target_country": "GB"},
  "days": 28,
  "metrics": {"enabled": true, "plan_status": "ok", "checked_at": "2026-10-20T08:00:00", "last_error": null, "snapshots_total": 5120},
  "totals": {"posts": 120, "posts_with_metrics": 80, "likes": 1234, "comments": 56, "shares": 78, "engagement": 1368},
  "daily": [{"date": "2026-10-01", "posts": 5, "n": 4, "engagement": 140}],
  "by_hour": [{"hour": 19, "posts": 9, "n": 7, "eng_median": 31.5}],
  "by_weekday": [{"weekday": 0, "label": "Mon", "posts": 17, "n": 12, "eng_median": 31.5}],
  "by_content_type": [{"content_type": "news_content", "posts": 40, "n": 30, "eng_median": 22.0}],
  "top_posts": [{"job_id": 1, "content_type": "ig_recreate", "scheduled_for": "2026-10-03T18:20:00Z", "title": "…", "likes": 900, "comments": 40, "shares": 12, "engagement": 952}],
  "trend": {"label": "rising", "change_pct": 18.4, "n_recent": 22, "n_prior": 19}
}
```

### GET /hermes/memory/{fanpage_id}
Scope: `read`

```bash
curl -H "Authorization: Bearer $HERMES_STUDIO_TOKEN" "$STUDIO_URL/hermes/memory/1"
```
```json
{
  "fanpage_id": 1,
  "auto": {
    "best_hours": [{"hour": 19, "n": 7, "eng_median": 31.5}],
    "best_weekdays": [{"weekday": 0, "label": "Mon", "n": 12, "eng_median": 31.5}],
    "content_types": [{"content_type": "news_content", "n": 30, "eng_median": 22.0}],
    "top_posts": [{"job_id": 1, "title": "…", "engagement": 952}],
    "trend": "rising",
    "timezone": "Europe/London",
    "computed_at": "2026-10-10T12:00:00Z"
  },
  "notes": {
    "hypotheses": ["Evening posts perform better because audience is off work."],
    "flopped": ["Early morning posts"]
  },
  "auto_updated_at": "2026-10-10T12:00:00Z",
  "notes_updated_at": "2026-10-03T09:00:00Z"
}
```

### PUT /hermes/memory/{fanpage_id}
Scope: `memory:write`

```bash
curl -X PUT -H "Authorization: Bearer $HERMES_STUDIO_TOKEN" \
     -H "Content-Type: application/json" \
     -d '{"notes": {"worked": ["Video content at 18:00"]}}' \
     "$STUDIO_URL/hermes/memory/1"
```
```json
{
  "fanpage_id": 1,
  "auto": { "...": "..." },
  "notes": {"worked": ["Video content at 18:00"]},
  "auto_updated_at": "2026-10-10T12:00:00Z",
  "notes_updated_at": "2026-10-11T09:00:00Z"
}
```

### POST /hermes/recommendations
Scope: `recommendations:write`

```bash
curl -X POST -H "Authorization: Bearer $HERMES_STUDIO_TOKEN" \
     -H "Content-Type: application/json" \
     -d '{
       "fanpage_id": 1,
       "kind": "sleep_window",
       "title": "Shift sleep window to match low engagement period",
       "proposal": {"publish_sleep_start_hour": 1, "publish_sleep_end_hour": 9},
       "rationale": "Metrics show engagement drops significantly after 1 AM. Shifting the sleep window frees up daily cap for peak 18:00-22:00 hours.",
       "evidence": {"n": 35, "period_days": 28, "uplift_pct": 25, "metric": "engagement"}
     }' \
     "$STUDIO_URL/hermes/recommendations"
```
```json
{
  "id": 101,
  "fanpage_id": 1,
  "fanpage_name": "Auto & Motorsport",
  "kind": "sleep_window",
  "title": "Shift sleep window to match low engagement period",
  "proposal": {"publish_sleep_start_hour": 1, "publish_sleep_end_hour": 9},
  "rationale": "Metrics show engagement drops...",
  "evidence": {"n": 35, "period_days": 28, "uplift_pct": 25, "metric": "engagement"},
  "status": "proposed",
  "source": "hermes",
  "created_at": "2026-10-11T14:00:00Z",
  "decided_at": null,
  "decided_by": null,
  "applied_at": null,
  "apply_error": null,
  "previous_values": null,
  "current_values": {"publish_sleep_start_hour": 23, "publish_sleep_end_hour": 7}
}
```

### GET /hermes/recommendations?fanpage_id=&status=
Scope: `read`

```bash
curl -H "Authorization: Bearer $HERMES_STUDIO_TOKEN" "$STUDIO_URL/hermes/recommendations?fanpage_id=1&status=proposed"
```
```json
[
  {
    "id": 101,
    "fanpage_id": 1,
    "fanpage_name": "Auto & Motorsport",
    "kind": "sleep_window",
    "status": "proposed",
    "...": "..."
  }
]
```

## 3. Full Analytics JSON Shape

The `GET /hermes/analytics/fanpages/{id}` endpoint returns exactly this shape:

- `fanpage`: Fanpage identity (`id`, `name`, `timezone`, `target_country`).
- `days`: Lookback window length in days.
- `metrics`: Metrics status (`enabled`, `plan_status`, `checked_at`, `last_error`, `snapshots_total`).
- `totals`: Period totals (`posts`, `posts_with_metrics`, `likes`, `comments`, `shares`, `engagement`).
- `daily`: Array of daily stats. Dates are LOCAL.
- `by_hour`: Always 24 items (hour 0-23 in the fanpage's LOCAL time).
- `by_weekday`: Always 7 items (Mon=0 … Sun=6).
- `by_content_type`: Medians by content type.
- `top_posts`: Top performing posts in the period.
- `trend`: Trend object (`label`, `change_pct`, `n_recent`, `n_prior`).

**Field Meanings:**
- `posts`: published posts that went live in the window.
- `n`: those with final metrics (7-day or backfill snapshot).
- `eng_median`: median engagement. Null when `n < 5`.
- `engagement`: likes + comments + shares.
- `trend`: median 24h engagement of the last 14 days vs the 14 days before (`n ≥ 10` each, `±15 %`), label one of `rising`, `flat`, `falling`, or `insufficient`.

## 4. Proposal Validation & Fields

### Validation by Kind
- `sleep_window`: `{"publish_sleep_start_hour": 0-23|null, "publish_sleep_end_hour": 0-23|null}`. Both must be null OR both must be integers 0-23 and `start != end`. Hours are LOCAL to the fanpage timezone.
- `daily_cap`: `{"publish_daily_limit": 1-100}`. Value must be between 1 and 100.
- `best_hours`, `content_mix`, `topic`, `other`: Any valid JSON object ≤ 4 KB serialized.

### Recommendation Item Fields
| Field | Type | Description |
|---|---|---|
| `id` | Integer | Unique ID |
| `fanpage_id` | Integer | Associated fanpage |
| `fanpage_name` | String | Fanpage name (for UI context) |
| `kind` | String | e.g. `sleep_window`, `daily_cap`, `topic` |
| `title` | String | Max 200 chars |
| `proposal` | JSON | The proposed change |
| `rationale` | String | Max 4000 chars |
| `evidence` | JSON | Optional, conventional keys: `n`, `period_days`, `uplift_pct`, `metric` |
| `status` | String | `proposed`, `approved`, `rejected`, `applied`, `failed`, `superseded` |
| `source` | String | e.g. `hermes` |
| `created_at` | DateTime | When the proposal was made |
| `decided_at` | DateTime | When owner approved/rejected |
| `decided_by` | String | Owner username |
| `applied_at` | DateTime | When it was applied to the fanpage |
| `apply_error` | String | Error trace if apply failed |
| `previous_values` | JSON | Fanpage settings before apply (for audit/undo) |
| `current_values` | JSON | Live fanpage settings at the time of query (for before→after diffs) |

## 5. Status Flow

```text
proposed ──┬──→ approved ──→ applied
           │                 └──→ failed (with apply_error)
           ├──→ rejected
           └──→ superseded (when a newer proposal of the same applicable kind arrives for this fanpage)
```

## 6. Error Table

| HTTP Status | Meaning | Resolution |
|---|---|---|
| `401 Unauthorized` | Bad or revoked token | Create a new token in dashboard Settings and update Hermes. |
| `403 Forbidden` | Missing required scope | Token is valid but lacks `read`, `memory:write`, or `recommendations:write`. |
| `404 Not Found` | Unknown fanpage | Check `fanpage_id` exists in `GET /hermes/fanpages`. |
| `409 Conflict` | Not proposed | Attempted to approve/reject an item that is already decided. |
| `422 Unprocessable` | Validation failed | Check proposal shape matches validation rules for the specific `kind`. |
| `429 Too Many Requests` | Limit exceeded | Fanpage has reached the maximum limit of 20 `proposed` items. Wait for owner decisions. |

## 7. Recommended Hermes Behaviour

- **Minimum Samples**: Wait for sufficient data. `n ≥ 30` posts with metrics overall before proposing any timing changes. `n ≥ 5` within a specific hour bucket to draw conclusions about that hour.
- **Evidence Structure**: Always cite `n`, `period_days`, `uplift_pct`, and `metric` in the `evidence` object.
- **Limits**: At most ONE open (`proposed`) proposal per applicable kind per fanpage. ≤ 2 proposals total per fanpage per weekly run.
- **Exploration**: Dedicate ~10-20% of recommendations to bandit-style exploration to discover new optima.
- **Prerequisites**: Never propose changes when `metrics.enabled` is `false` or `metrics.plan_status` != `ok`.
- **Feedback Loop**: Periodically query `GET /hermes/recommendations` to learn from `approved` and `rejected` outcomes.

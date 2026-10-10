---
name: content-strategist
description: Analyze fanpage analytics, build content memory, and propose strategy recommendations.
---

# Content Strategist

Hermes skill for fanpage strategy optimization. Runs weekly per fanpage.

## Weekly Procedure

1. **Fetch Fanpages**: Call `GET /hermes/fanpages`.
2. **Loop Active Fanpages**: For each `is_active` fanpage:
   - **Fetch Analytics**: Call `GET /hermes/analytics/fanpages/{id}?days=28` and `days=90`.
   - **Fetch Memory**: Call `GET /hermes/memory/{fanpage_id}`.
   - **Check Metrics**: Stop processing this fanpage if metrics are not ok (`metrics.enabled` is false or `metrics.plan_status` != "ok").
   - **Analyze Hours**: Compare the best local hours (from analytics) against the current `sleep_window`.
   - **Compute Uplift**: Calculate expected `uplift_pct` and total `n`.
   - **Write Notes**: Call `PUT /hermes/memory/{fanpage_id}`. Log what worked, what flopped, hypotheses, and running experiments.
   - **Decide Proposals**: Decide on 0-2 proposals based on decision rules (see below).
   - **POST Recommendations**: Call `POST /hermes/recommendations` with full rationale and evidence.
   - **Notify**: Send ONE Telegram approval request per proposal ONLY IF the owner enabled `TELEGRAM_NOTIFICATIONS_ENABLED`. Otherwise, send none.

## Decision Rules

- **Sample Size**: `n ≥ 30` posts with metrics before proposing timing changes. `n ≥ 5` posts per hour bucket to trust that hour's median.
- **Limits**: 
  - Max 2 proposals per fanpage per weekly run.
  - Max 1 open (`proposed`) proposal per applicable kind (`sleep_window`, `daily_cap`).
- **Exploration**: ~10-20% budget for exploration.

## Example Rationales

- *Sleep Window Shift*: "Metrics over 28 days (n=45) show engagement drops 60% after 23:00. Shifting the sleep window to 23:00-07:00 focuses the daily cap on the peak 18:00-22:00 window (uplift: 25%)."
- *Content Mix*: "Video posts at 18:00 (n=12) have a median engagement of 500, outperforming image posts by 40%. Recommend increasing video ratio."

## Never Do List

- **NEVER** change settings directly. Always use the proposal flow.
- **NEVER** exceed the 2 proposals per fanpage weekly limit.
- **NEVER** send daily or weekly Telegram digests. Telegram is for approval requests or incidents only.
- **NEVER** guess without data. Wait for minimum samples.

## Environment Variables

- `STUDIO_URL`: Base API URL.
- `HERMES_STUDIO_TOKEN`: API token (`hst_...`).
- `TELEGRAM_NOTIFICATIONS_ENABLED`: Set to `true` to send single approval requests, otherwise `false`.

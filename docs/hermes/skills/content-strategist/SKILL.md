---
name: content-strategist
description: Analyze fanpage analytics, build content memory, and propose strategy recommendations.
---

# Content Strategist

Hermes skill for fanpage strategy optimization. Runs weekly per fanpage. Video ideas run daily; the rest of this skill remains weekly.

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
   - **Video Ideas (daily)**: Fetch `GET /hermes/fanpages` and `GET /hermes/video-ideas?fanpage_id={id}`. For each active fanpage with Mode 8 enabled, keep about one day of buffer by proposing `max(0, ai_video_daily_count - (ideas currently proposed + approved + queued for that page))` new ideas, capped at 10 per page per day. Fetch `GET /hermes/video-templates`, use the page's `ai_video_template` as `template_key` (or `motorsport_tech_explainer` when null), and ensure topics differ between every fanpage. The API answers 409 for a topic used on any page in the last 30 days: pick another topic and do not retry the same one. Spread topics so pages in the same niche do not cover the same subject in the same week. Avoid topics already proposed or completed, and call `POST /hermes/video-ideas` with source-backed `research_notes`, a score, and a rationale. Narration for every idea must be in English. If any 9Router call fails with a quota, rate-limit, or 5xx error, stop proposing for the rest of that day with no retry loop and do not send a Telegram message for it.

### Video Idea Research

Before writing any idea, research on the web with Hermes's own tools: use `web_search` to find sources, `web_extract` or the browser to read them, and the installed skills `master-fetch` for Cloudflare/Turnstile pages, `blocked-page-recovery` for 403/429/paywall/bot-wall pages, and `grounded-citations` to cite every fact.

Use sources in this priority order: official sources (FIA / FIM-MotoGP regulations, formula1.com, motogp.com, and team sites), then tech journalism (The Race, Motorsport.com, Autosport, Racecar Engineering, F1Technical, and Crash.net). Use YouTube, forums, and Wikipedia only as leads that must be re-verified.

Read at least 2 real pages per idea. Every specific number needs 2 independent sources and its season/year. Leave anything unverified out of the narration. `research_notes` must list the facts with their URLs; the planner uses `research_notes` as the ONLY source of specific facts. Research runs on 9Router; the existing stop-for-the-day rule on quota/rate-limit/5xx errors still applies.

## Decision Rules

- **Sample Size**: `n ≥ 30` posts with metrics before proposing timing changes. `n ≥ 5` posts per hour bucket to trust that hour's median.
- **Limits**: 
  - Max 2 proposals per fanpage per weekly run.
  - Max 1 open (`proposed`) proposal per applicable kind (`sleep_window`, `daily_cap`).
- **Exploration**: ~10-20% budget for exploration.
   - **Video Ideas**: Run daily, keep one day of buffer, and cap proposals at 10 per fanpage per day. Use each page's configured template, complete the required web research before proposing, include fact-and-URL citations in `research_notes`, score each idea, explain the score in `rationale`, use a different topic for every fanpage, and require English narration.

## Example Rationales

- *Sleep Window Shift*: "Metrics over 28 days (n=45) show engagement drops 60% after 23:00. Shifting the sleep window to 23:00-07:00 focuses the daily cap on the peak 18:00-22:00 window (uplift: 25%)."
- *Content Mix*: "Video posts at 18:00 (n=12) have a median engagement of 500, outperforming image posts by 40%. Recommend increasing video ratio."

## Never Do List

- **NEVER** change settings directly. Always use the proposal flow.
- **NEVER** exceed the 2 proposals per fanpage weekly limit.
- **NEVER** send daily or weekly Telegram digests. Telegram is for approval requests or incidents only.
- **NEVER** guess without data. Wait for minimum samples.
- **NEVER** propose a video idea without source-backed `research_notes`, a score, and a rationale.
- **NEVER** repeat a topic already proposed or completed for any fanpage within the last 30 days.
- **NEVER** write narration in a language other than English.

## Environment Variables

- `STUDIO_URL`: Base API URL.
- `HERMES_STUDIO_TOKEN`: API token (`hst_...`).
- `TELEGRAM_NOTIFICATIONS_ENABLED`: Set to `true` to send single approval requests, otherwise `false`.
The Hermes token needs the `ideas:write` scope to propose video ideas through `POST /hermes/video-ideas`.

# Muse research desk (Mode 8) — owner notes

Muse researches Mode 8 video ideas through the research desk API. Muse's contract is
`backend/app/assets/research-desk-api.md`, served to Muse at `GET /api/research/docs`.

## What Muse can and cannot see
Muse sees "channels" (fanpage ids), a niche, an audience language/country, templates and copy limits.
It never sees fanpage names, Facebook, Repliz or publishing data. `tests/test_research_desk.py` checks
the brief, docs and feedback for leaks. Keep it that way when changing `app/services/research_desk.py`.

## Settings → AI Video
- **Researcher**: Muse or Hermes. With Hermes, Muse's brief is always idle.
- **Mode**: pilot only for now. Muse's ideas arrive with status `pilot`; the planner never produces
  them until you approve one on the AI Video page.
- **Pilot ideas per channel** (default 3): Muse stops researching for a channel once this many of its
  ideas wait (`pilot` or `approved`). Rejecting or producing one frees a slot.
- **References** per niche, one link per line (`url | note`).
- **Activity**: every Muse call, Muse's notes and its last run report.

## Packs and variants
Muse researches a topic once (a pack) and writes one variant per channel (max 10 channels per pack).
The server rejects a variant when its slots do not render, narration is outside the word range, real
team/driver/brand/circuit names appear, the hook is over 125 characters, the channel had the topic in
the last 30 days, or the topic is already on 10 channels. Muse gets the reasons in the response.

## Feedback both ways
- You → Muse: "Show research, slots and feedback" on a Muse idea card, or Reject with a reason.
  Muse reads it at `GET /research/feedback` (the brief's `latest_feedback_at` tells it when).
- Reviewer token (scope `research:review`): `GET /research/review/packs`, `POST /research/review/feedback`.
- Muse → you: `POST /research/notes`, shown in Settings → AI Video.

## Tokens
Muse may use its labeler token (`labels:write`) or a token with `research`. A `research` token
cannot read the Hermes endpoints (they carry page names).

## Not built yet (next phase)
Live mode (auto-approve), Muse-first planner tiers with Hermes as fallback, two-day queue math,
videos-per-day ranges, Hermes reading a fallback "ideas needed" field.

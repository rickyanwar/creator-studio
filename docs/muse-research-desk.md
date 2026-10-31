# Muse research desk (Mode 8) — owner notes

Muse researches Mode 8 video ideas through the research desk API. Muse's contract is
`backend/app/assets/research-desk-api.md`, served to Muse at `GET /api/research/docs`.

## What Muse can and cannot see
Muse sees "channels" (fanpage ids), a niche, an audience language/country, templates and copy limits.
It never sees fanpage names, Facebook, Repliz or publishing data. `tests/test_research_desk.py` checks
the brief, docs and feedback for leaks. Keep it that way when changing `app/services/research_desk.py`.

## Settings → AI Video
- **Researcher**: Muse or Hermes. With Hermes, Muse's brief is always idle.
- **Mode**: Pilot or Live. Pilot: Muse's ideas arrive as `pilot` and wait for your approval. Live: ideas that pass the server checks are approved at once when their score reaches the fanpage's minimum score (else `proposed`); switching to Live releases the waiting pilot ideas the same way.
- **Idea bank (days)** (default 3): each channel keeps videos/day × days ideas waiting (any source:
  pilot, proposed, approved). Muse's brief asks for the gap, emptiest channel first.
- **Pilot ideas per channel** (pilot mode only) (default 3): at most this many Muse ideas wait for your review per channel.
- With Muse as researcher the in-app idea writer is off, except for a fan page whose idea bank is empty and that got no Muse idea for 6 h (`MUSE_DRY_HOURS`); video jobs are claimed fanpage by fanpage (fair share).
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
Muse-first planner tiers with Hermes as fallback,
videos-per-day ranges, Hermes reading a fallback "ideas needed" field.

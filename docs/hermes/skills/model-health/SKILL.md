# Hermes Skill: model-health

> **Docs-only** — not installed anywhere. Reference for Hermes to self-operate
> the model chain without owner involvement for routine hygiene.

## Purpose

Keep the 9Router model chains (`text` and `vision`) healthy automatically:
detect dead IDs, send ONE deduplicated Telegram alert, open a cleanup PR, and
wait for owner approval before merging — mirroring the single-use SHA-bound
approval flow used by `docs/runbooks/ig-viewer-self-heal.md`.

Never send digests. Never touch `cx/*` models. Never add a model ID not present
on the live `/v1/models` list.

---

## Trigger conditions (check daily)

Call `GET /hermes/model-health` (scope `read`). Act if **any** of:

1. A **primary** model (`primaries.text`, `primaries.discussion`, or
   `primaries.vision`) has `status == "dead"`.
2. A chain (`chains.text` or `chains.vision`) has **fewer than 2** models with
   `status == "ok"` or `status == "unknown"` (unknown = not yet probed).
3. Any chain entry has had `status == "dead"` for more than 24 hours
   (`checked_at` is > 24 h ago and status is still `dead`).

---

## Alert (Telegram, deduped per 24 h)

Send **one** message per trigger condition per 24-hour window. Do not repeat.
Message format (plain text, no markdown tables):

```
⚠️ Model-health alert — <date>

Dead models: <comma-separated list from response.dead>
Primary affected: <yes/no, which role>
Chains with < 2 live: <text / vision / none>

A cleanup PR will be opened on branch hermes/model-health-<date>.
Reply APPROVE <sha7> to merge and deploy, or REJECT to cancel.
```

---

## PR construction

Branch: `hermes/model-health-<YYYY-MM-DD>`

Changes — in this order, one commit:

1. **Remove** dead IDs from `ROUTER_MODEL_FALLBACKS` in
   `backend/app/services/ai_caption.py`.
2. **Remove** dead IDs from `_VISION_MODEL_FALLBACKS` in
   `backend/app/services/design_images.py`.
3. If a chain would be left with fewer than 2 live IDs, **add** live same-family
   `ag/*` IDs from `response.unused_live_models` (pick the most specific
   match by model family) — **never** add `cx/*` or any ID not on the live
   list.
4. If a **primary** (`nine_router_vision_model` in `backend/app/config.py` or
   the DB `nine_router_model` setting) is dead, propose a replacement from the
   top of the live chain (do not auto-apply; include in the PR diff for owner
   review).

PR title: `chore: remove dead 9Router models (model-health <date>)`

PR description: list dead IDs removed, replacements added, source
(`unused_live_models` from `/hermes/model-health`).

---

## Approval flow (identical to ig-viewer-self-heal)

- Single-use: approval message must contain the exact 7-char commit SHA of the
  PR's head commit.
- SHA-bound: if the PR is amended after the approval message, the approval is
  void — Hermes must re-alert and wait.
- Merge + deploy only after the owner replies `APPROVE <sha7>` on Telegram.
- On `REJECT`, close the PR and do nothing until the next daily trigger check.

---

## What this skill does NOT do

- No digests, no scheduled summaries.
- No merging without explicit owner approval.
- No touching models outside `ag/*` (never `cx/*`, never non-9Router model IDs).
- No changing primary model values in the DB or env without explicit PR review.

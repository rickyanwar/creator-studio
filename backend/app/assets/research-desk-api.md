# Research Desk API (v1, pilot)

You are the research desk for a studio that makes short vertical (9:16) motorsport explainer videos: 20–30 seconds, photorealistic 3D CGI, with a voice-over spoken inside the clips. You find what is worth explaining, verify the facts, and write production-ready **research packs**. The studio turns every pack variant into AI-generated video clips by placing your slot values into a fixed prompt template. You never generate video.

- Base URL: `https://autostudio.pro/api` (HTTPS only)
- Auth: header `Authorization: Bearer <token>`
  - **Pilot:** use the studio token you already hold for the gallery labeler.
  - **Later:** a token with the `research` scope also works.
- Content type: `application/json`
- Schedule during the pilot: every 30 minutes.
- **Latest version of this document:** `GET /research/docs` returns it as Markdown. The brief carries `docs_version`; re-read the document whenever that value changes.

## How a run works

```
1. GET  /research/brief      -> read docs_version and latest_feedback_at first (step 2), then:
                                action "idle"?  stop: no research, no further calls
2. GET  /research/feedback   -> only when latest_feedback_at is newer than the last feedback you read
3. GET  /research/schema     -> JSON Schema for the packs (cache it per schema_version)
4. research + write packs + self-QC
5. POST /research/packs      -> per-pack and per-variant results; fix and resend rejected ones as new packs
6. POST /research/notes      -> optional: a question or problem for the studio
```

**Idle rule.** If the brief says `"action": "idle"`, do no research and make no further calls in that run. The only exceptions are reading new feedback (step 2) and a changed document. Most scheduled runs should end at step 1. That is expected and keeps usage low.

**Queue cap.** The studio caps its queue. `ideas_needed` and `packs_needed` already subtract what is waiting, and the server rejects variants for channels that no longer need ideas.

## Concepts

- **Channel.** One output stream with its own audience. It is identified by `channel_id` and has a `niche` (`f1` or `motogp`), an audience language, allowed templates and copy limits.
- **Research pack.** One topic, researched once: verified facts (`research_notes` + `sources`), the reference videos that justify it, a score and a rationale. It has 1–10 **variants**.
- **Variant.** One channel's version of the pack. It has its own `angle`, template `variables` (the slots), `title` and `caption`, all written by you in that channel's audience language.
  - Variants of the same pack must read clearly differently: different angle, hook, narration wording and title.
  - They share only the facts.
  - A pack can serve at most 10 channels.
- **Template.** A fixed sequence of 10-second clips. The first clip is text-to-video; later clips continue from the previous clip's last frame. Your slot values are placed into `clip_prompts`. The studio fills `{style}`, `{audio}`, `{narrator}`, `{vehicle}` and similar placeholders itself.

## GET /research/brief

Always call this first.

### Idle response

```json
{"brief_id": "202610132300-1a2b3c4d", "schema_version": 1, "docs_version": "3f2a9c1d", "mode": "pilot",
 "action": "idle", "reason": "no channel needs ideas", "latest_feedback_at": "2026-10-14T01:12:09", "niches": []}
```

### Research response

Abridged; real values come from the server.

```json
{
  "brief_id": "202610132300-9f8e7d6c",
  "schema_version": 1,
  "docs_version": "3f2a9c1d",
  "mode": "pilot",
  "action": "research",
  "latest_feedback_at": null,
  "queue_policy": "...",
  "studio": "...",
  "idea_sources": ["New or changed motorsport regulations (FIA, FIM/MotoGP) ...", "..."],
  "global_rules": ["..."],
  "research_defaults": {"source_priority": ["..."], "fact_rules": ["..."]},
  "pack_rules": {"max_channels_per_pack": 10, "max_packs_per_request": 3, "min_score": 70, "variants": "..."},
  "niches": [
    {
      "niche": "f1",
      "packs_needed": 3,
      "references": {
        "video_style": [{"url": "https://www.instagram.com/...", "note": "..."}],
        "topic_signals": [{"url": "https://www.instagram.com/...", "note": "..."}]
      },
      "channels": [
        {
          "channel_id": 12,
          "audience": {"language": "en", "country": "GB"},
          "ideas_needed": 3,
          "templates": {
            "motorsport_tech_explainer": {
              "clip_options": [2, 3],
              "vehicle_presets": ["f1_red", "f1"],
              "narration_words": {"narration_1": {"target": "22-24", "accepted": "16-24"}, "narration_2": {"...": "..."}}
            }
          },
          "copy_rules": {"title_max_chars": 100, "caption_max_chars": 600, "hashtags_max": 5, "hook_max_chars": 125},
          "recent_topics": ["topics this channel already had in the last 30 days"]
        }
      ]
    }
  ],
  "saturated_topics": ["topics already used on 10 channels in the last 30 days: do not use"],
  "templates": [
    {
      "key": "motorsport_tech_explainer",
      "name": "...", "niches": ["f1", "motogp"], "usage": "...", "inspiration": "...",
      "clip_seconds": 10, "clip_options": [2, 3],
      "slots_by_clips": {"2": ["part", "mechanism_details", "..."], "3": ["..."]},
      "clip_prompts": [{"index": 1, "role": "reveal", "continues_previous_clip": false, "prompt": "{style} {vehicle} ... \"{narration_1}\""}],
      "studio_filled_placeholders": {"...": "..."},
      "vehicle_presets": {"f1": {"full": "...", "short": "..."}},
      "rules": ["..."], "research": {"questions": ["..."]},
      "keywords_that_worked": ["..."], "starter_topics": ["..."],
      "example": {"note": "Format example only ...", "topic": "...", "variables": {"...": "..."}}
    }
  ]
}
```

Plan the run from the brief:
- **How many packs.** Make `packs_needed` packs per niche, no more.
- **Assigning channels.** Give each pack's variants to channels with `ideas_needed > 0`, at most 10 channels per pack. A channel never receives more variants in total than its `ideas_needed`, and never two variants of the same pack.
- **Topics.** Never give a channel a topic from its `recent_topics`, and never use a `saturated_topics` entry.
- **Templates.** Pick each pack's template from the templates every one of its channels allows, and `clips` from the `clip_options` all of them allow.

## GET /research/schema

Returns the JSON Schema (draft 2020-12) of the `POST /research/packs` body, including the exact slots each template and clip count requires. Validate your body against it before every POST.

## POST /research/packs

```json
{
  "brief_id": "202610132300-9f8e7d6c",
  "packs": [
    {
      "client_ref": "4b0b6c1e-7f0a-4c8e-9d2b-3f5a6e7d8c9b",
      "niche": "f1",
      "template_key": "motorsport_tech_explainer",
      "clips": 2,
      "topic": "What the 2026 active aero rules change on the rear wing",
      "research_notes": "1. ... [S1][S2]\n2. ... [S3]",
      "sources": [
        {"id": "S1", "url": "https://...", "publisher": "FIA", "title": "...", "published_at": "2025-06-10"},
        {"id": "S2", "url": "https://...", "publisher": "The Race", "title": "...", "published_at": null}
      ],
      "references": [
        {"url": "https://www.instagram.com/reel/...", "platform": "instagram", "kind": "video_style",
         "views": 1200000, "views_approx": true, "likes": 54000, "comments": 800, "shares": null,
         "posted_at": "2026-10-09", "duration_s": 21, "why": "far above the account's usual views"}
      ],
      "score": 84,
      "rationale": "Why this will perform, citing the reference numbers.",
      "qc": {"passed": true, "facts_two_sources": true, "season_checked": "2026", "no_real_names": true,
             "placement_checked": true, "notes": ""},
      "variants": [
        {"channel_id": 12, "angle": "...", "variables": {"part": "...", "vehicle_preset": "f1", "narration_1": "...", "...": "..."},
         "title": "...", "caption": "..."},
        {"channel_id": 15, "angle": "a clearly different angle", "variables": {"...": "..."}, "title": "...", "caption": "..."}
      ]
    }
  ],
  "run_report": {"started_at": "2026-10-13T23:00:00Z", "finished_at": "2026-10-13T23:40:00Z",
                 "sources_checked": 14, "videos_checked": 25, "usage": "...", "problems": []}
}
```

- At most 3 packs per request. A big run can POST several times.
- `run_report` is optional on every POST but please send it on the last POST of a run.

### Response (200)

```json
{
  "results": [
    {"client_ref": "4b0b6c1e-...", "status": "partial", "errors": [],
     "variants": [
       {"channel_id": 12, "status": "created", "idea_id": 431, "errors": []},
       {"channel_id": 15, "status": "rejected", "errors": ["narration_2 must contain 16–24 words"]}
     ]}
  ]
}
```

**Pack status:**

| Status | Meaning |
|---|---|
| `created` | Every variant was stored. |
| `partial` | Some variants were stored. |
| `rejected` | Nothing was stored; `errors` say why. |
| `exists` | This `client_ref` was stored before. Nothing new was stored; `variants` show what is stored. |

**Variant status:** `created`, `rejected` or `exists`.

**Fixing rejections:**
- A rejected pack, or the rejected variants of a partial pack, are not stored.
- Fix them and send them in a **new** pack with a **new** `client_ref`.
- That new pack carries the same facts and only the channels still missing.

### Other responses

| Code | Meaning |
|---|---|
| 401 | Bad or revoked token. Stop and report. |
| 403 | The token lacks the scope. Stop and report. |
| 422 | The body is not `{brief_id, packs: [1-3 items]}`. |
| 429 / 5xx | Retry with backoff; honour `Retry-After`. |
| 404 / 502 | The API is not deployed yet or is restarting. End the run quietly and keep the schedule. |

## GET /research/packs?client_ref=...&since=ISO-8601

Status of packs you sent (newest first, max 100):

```json
[{"client_ref": "4b0b6c1e-...", "niche": "f1", "topic": "...", "template_key": "...", "created_at": "...",
  "variants": [{"channel_id": 12, "idea_id": 431, "status": "in_review", "error": null}]}]
```

Variant status meanings:

| Status | Meaning |
|---|---|
| `in_review` | Held for the studio's review. All pilot packs are held. |
| `approved` | Accepted and waiting for production. |
| `in_production` | Video is being made. |
| `produced` | The video was made. |
| `failed` | Production failed; `error` says why. |
| `rejected` | Turned down by the studio. |

## GET /research/feedback?since=ISO-8601

The studio's review notes on your packs, newest first:

```json
[{"id": 7, "created_at": "2026-10-14T01:12:09", "client_ref": "4b0b6c1e-...", "idea_id": 431, "channel_id": 12,
  "topic": "...", "kind": "rejected", "message": "The 2026 figure for ... has only one source; ..."}]
```

`kind` is either `comment` (advice for future packs) or `rejected` (that variant was turned down for the reason given).

Read it whenever `latest_feedback_at` in the brief is newer than the last item you read. For each item:
- **Learn from it.** Remember the lesson in your notes and apply it to every future pack.
- **Rejected variants.** Treat each rejected variant as a free slot. When the brief asks for ideas again, you may redo it, fixed, in a new pack with a new `client_ref`.

## POST /research/notes

Send the studio a short message: a question, a blocked source, an account you could not reach, or a suggestion for a template.

```json
{"message": "The reference account ... is private now; using ... instead.", "client_ref": null}
```

Returns `201 {"id": 12}`. Keep notes short and only when they matter. Answers come back through `/research/feedback`.

## What the server checks

A variant or pack failing any check is rejected, with the reason.

**Pack:**
1. `client_ref` is a UUID.
2. `niche` is `f1` or `motogp`.
3. `template_key` exists for that niche, and `clips` is one of its options.
4. `topic` is 3–200 characters and not saturated.
5. `research_notes` has at most 8000 characters and cites its sources as `[S1]`; every cited id exists.
6. There are 2–8 `sources` and 1–5 `references`.
7. `score` is at least 70, or the channel's own minimum if that is higher.
8. `rationale` is present.
9. `qc.passed` is `true`.
10. There are 1–10 variants with no repeated channel.

**Variant:**
1. The channel is in the brief, still needs ideas, allows the template and `clips`, and did not have this topic in the last 30 days.
2. `vehicle_preset` is allowed for the channel's niche.
3. All slots for the template and `clips` are present. Slots are plain text of at most 600 characters, with no `{` or `}`, and narration has no double quotes.
4. Never send `livery`, `channel_name` or `narrator_preset`; the studio sets them.
5. Every narration line fits the channel's `narration_words.accepted` range. Aim for `target`.
6. No real team, constructor, manufacturer, driver, rider, sponsor, brand or circuit names in any slot, the title or the caption.
7. `title` and `caption` respect the channel's `copy_rules`.
   - The caption's first sentence is the hook a viewer sees before the text is truncated: at most `hook_max_chars`, concrete, no generic opener.
   - At most `hashtags_max` hashtags.
8. The variant's narration and title are not near-copies of another variant in the same pack.

## Research and writing rules

1. **Topic sources, in priority order:**
   1. New or changed motorsport regulations from the FIA and FIM/MotoGP: technical and sporting rules, technical directives, clarifications and bans, for this season and next. Explain what the rule changes on the car or bike, and why.
   2. Recent technical news and controversies: protests, upgrades, legality disputes.
   3. Topics drawing unusual engagement on the reference accounts, compared with each account's usual level.
   4. Evergreen engineering or history topics, only when nothing timely fits.
2. **References.**
   - Study the `video_style` reels for the first 2–3 seconds, pacing, camera moves and length.
   - Use `topic_signals` posts to see which topics draw unusual engagement.
   - Search for more on your own, especially when a niche has none.
   - Record the videos you rely on in `references`. Set `views_approx: true` when the platform rounds views, and leave `shares` null when it is hidden.
   - Copy the visual style only, never footage, logos, watermarks or wording.
3. **Facts.**
   - Follow `research_defaults` and each template's `research` questions.
   - Every number needs two independent sources.
   - State the season of every rule-dependent claim.
   - Check the technology is still current, or frame the idea as history or as what replaced it.
   - Leave out anything unverified.
   - `research_notes` are numbered facts, each ending with its source ids, for example `2. ... [S1][S3]`. They are the **only** facts the narration, title and caption may use.
4. **Slots.**
   - Read `clip_prompts` to see exactly where each slot lands, and write values that read naturally there.
   - Components must sit where they really are on the vehicle.
   - Action slots say exactly what moves, around which pivot or axis, in which direction, and what stays fixed.
   - Parts lists hold only 3–5 large, clearly separate components.
5. **Narration and copy.**
   - Write narration, title and caption in the channel's `audience.language`.
   - One narration line per 10-second clip, inside the channel's `narration_words`. Count the words in code.
   - Later clips continue the story of clip 1.
6. **Self-QC.** Check every pack against the server checks above and the rules here before sending. Only send packs you would score 70 or more. `score` is your honest 0–100 estimate of how well the video will perform, and `rationale` explains it with the reference numbers.
7. **Idempotency.** Give every pack a new UUID v4 `client_ref` and keep a sent-log. On a retry of the same pack, reuse its `client_ref`. The server answers `exists` instead of storing it twice.

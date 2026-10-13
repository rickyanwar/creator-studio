# TEMPORARY: Muse Gallery Labeler

This temporary API lets the external Muse agent label downloaded gallery photos. It is protected by a revocable API token and is intended to be removed after the backlog is complete.

## Connection

- Base URL: `https://autostudio.pro/api` (HTTPS only)
- Header: `Authorization: Bearer <token>`
- Required scope: `labels:write`

Call `GET /labeler/schema` once. Then repeat `POST /labeler/claim` with `{"limit":50,"worker":"muse"}`, or use the optional legacy gallery-label filter `{"limit":50,"worker":"muse","label":"face"}` where `label` is `face`, `action`, or `other`. Label every returned image, and post one result per image to `POST /labeler/results`. Stop when claim returns zero items, or sleep five minutes and retry. Finish when `GET /labeler/stats` reports `remaining: 0`.

## Label Schema

The schema is sport-agnostic and additive. Existing motorsport values remain valid.

- `sport`: `motorsport | basketball | mma | boxing | football | tennis | other | unknown`
- `venue.type`: `circuit | court | octagon | ring | pitch | stadium | arena | gym | studio | paddock | garage | pitlane | street | other | unknown`
- `venue.visible`: boolean
- `shot_type`: existing values plus `athlete_in_action | team_group | vehicle_static`
- `activity`: existing values plus `playing | shooting | dunking | fighting | striking | grappling | training | warming_up | weigh_in | face_off | press_conference | walkout | signing_autographs`
- `context.session`: existing values plus `game | fight | weigh_in | training | warm_up | press_conference | media_day | ceremony`
- `context.session` and `event.session` also allow `sprint` and `sprint_qualifying`.
- `expression`: existing values plus `angry | shouting | emotional`
- `livery_colors`: up to 3 colors for motorsport; `kit_colors`: up to 3 colors for other sports
- `event.season`: optional `YYYY` or `YYYY-YY` string, such as `2026` or `2025-26`
- `people`: up to 10 visible identifiable people, each with `name` (maximum 80 characters) and `confidence` from 0 to 1. Use the photo's gallery keyword as the name hint. If one clear person or car fits the keyword's team, kit, livery, or number, add the proper-cased keyword name with confidence >= 0.8 and evidence such as `gallery keyword + Red Bull livery`. If several people are visible, name only the matching person when clear; otherwise leave `people` empty.
- `keyword_mismatch`: optional boolean. Set `true` when the photo clearly contradicts the gallery keyword (different team, kit, or person), and leave `people` empty.
- When `people` is empty and `keyword_mismatch` is not `true`, the server fills it from the gallery keyword when it can derive a non-generic name: `[{"name":"Max Verstappen","confidence":0.8,"source":"keyword"}]`. Generic keywords such as `podium`, `paddock`, or `crash` are skipped.
- `event`: `series` (maximum 40), `event_name` (maximum 80), `year` from 1950 to 2100, `season`, and a session enum
- `evidence`: maximum 300 characters
- `caption`: maximum 160 characters, in English

`context.circuit_visible` remains for compatibility and must be true only when a circuit is visible. Use `venue` for all new labels. Always fill `event.event_name` and `event.year` when the event can be identified. Photos from the same keyword download batch are usually from the same weekend, so keep event and session values consistent across the batch. Set `captured_at` to the session date with `captured_at_confidence: "day"` when the event and session date are identified from visual cues; use `"estimated"` when only the event weekend is known. Use session `unknown` when the session is unclear instead of guessing `race`. Dates require evidence. Never invent names or dates. The optional claim `label` filter accepts only `face`, `action`, or `other` and restricts the returned photos to that existing gallery label.

## Dates and event hints (provided by the server)

Almost all photos are Getty editorial images. The server computes the shot date from the Getty asset ID: IDs increase with upload time, and editorial sports photos are usually uploaded on the same day. This method has been validated to be within one day for 99% of the reference anchors. Each claimed photo includes `captured_at`, `captured_at_confidence`, `captured_at_source`, and `getty_hints` containing the location, session hint, and original slug.

Muse should not spend effort guessing dates. Use `getty_hints` to keep `event_name` and session values consistent across photos from the same keyword batch. Override the server date only with stronger visual or caption evidence. Set `archival_photo: true` when the visual evidence (livery, car generation, or kit) clearly shows the photo is older than the estimated date; the server keeps the date and lowers confidence to `estimated`. Focus effort on visual labels such as `shot_type`, `activity`, `expression`, keyword-guided `people`, and `quality`.

## Examples

### MotoGP photo

```json
{"id":14868,"attributes":{"sport":"motorsport","venue":{"type":"circuit","visible":true},"shot_type":"car_on_track","people_count":1,"activity":"driving","expression":"focused","context":{"session":"race","rain":false,"night":false,"circuit_visible":true},"livery_colors":["red","white"],"quality":{"blurry":false,"obstructed":false},"caption":"Marc Marquez leans the Ducati into a fast right-hander"},"people":[{"name":"Marc Marquez","confidence":0.93}],"event":{"series":"MotoGP","event_name":"Indonesian GP","year":2026,"session":"race"},"captured_at":"2026-10-05T14:20:00Z","captured_at_confidence":"day","evidence":"Getty caption + Mandalika circuit background"}
```

### UFC octagon photo

```json
{"id":14869,"attributes":{"sport":"mma","venue":{"type":"octagon","visible":true},"shot_type":"athlete_in_action","people_count":2,"activity":"grappling","expression":"focused","context":{"session":"fight","rain":false,"night":false,"circuit_visible":false},"kit_colors":["black","red"],"quality":{"blurry":false,"obstructed":false},"caption":"Two fighters grapple against the cage"},"people":[],"event":{"series":"UFC","event_name":"Fight Night","year":2026,"session":"fight"},"evidence":"Visible octagon branding and event backdrop; faces not identifiable"}
```

### NBA court photo

```json
{"id":14870,"attributes":{"sport":"basketball","venue":{"type":"court","visible":true},"shot_type":"athlete_in_action","people_count":1,"activity":"dunking","expression":"emotional","context":{"session":"game","rain":false,"night":false,"circuit_visible":false},"kit_colors":["purple","gold"],"quality":{"blurry":false,"obstructed":false},"caption":"A forward finishes a dunk during the game"},"people":[],"keyword_mismatch":true,"event":{"series":"NBA","event_name":"Regular Season","year":2026,"season":"2025-26","session":"game"},"evidence":"Gallery keyword does not match the visible kit; faces not identifiable"}
```

Use one label set per photo. Use the gallery keyword as a name hint: when the photo fits that keyword, name the matching person with confidence at least 0.8 and evidence `gallery keyword + <visual cue>`; when it clearly contradicts the keyword, leave `people` empty and set `keyword_mismatch: true`. Do not call an AI model through this API; Muse performs the vision work and posts the result. A per-item validation failure is reported in the results response with the failing field name and does not cancel the batch.

For already-labeled photos, the operator can run `python -m app.scripts.fill_people_from_keyword --dry-run` to preview eligible rows, then run `python -m app.scripts.fill_people_from_keyword` to apply the idempotent fill.

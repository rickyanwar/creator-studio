# TEMPORARY: Muse Gallery Labeler

This temporary API lets the external Muse agent label downloaded gallery photos. It is protected by a revocable API token and is intended to be removed after the backlog is complete.

## Connection

- Base URL: `http://109.123.232.184/api`
- Header: `Authorization: Bearer <token>`
- Required scope: `labels:write`

Call `GET /labeler/schema` once. Then repeat `POST /labeler/claim` with `{"limit":50,"worker":"muse"}`, label every returned image, and post one result per image to `POST /labeler/results`. Stop when claim returns zero items, or sleep five minutes and retry. Finish when `GET /labeler/stats` reports `remaining: 0`.

## Label Schema

The schema is sport-agnostic and additive. Existing motorsport values remain valid.

- `sport`: `motorsport | basketball | mma | boxing | football | tennis | other | unknown`
- `venue.type`: `circuit | court | octagon | ring | pitch | stadium | arena | gym | studio | paddock | garage | pitlane | street | other | unknown`
- `venue.visible`: boolean
- `shot_type`: existing values plus `athlete_in_action | team_group | vehicle_static`
- `activity`: existing values plus `playing | shooting | dunking | fighting | striking | grappling | training | warming_up | weigh_in | face_off | press_conference | walkout | signing_autographs`
- `context.session`: existing values plus `game | fight | weigh_in | training | warm_up | press_conference | media_day | ceremony`
- `expression`: existing values plus `angry | shouting | emotional`
- `livery_colors`: up to 3 colors for motorsport; `kit_colors`: up to 3 colors for other sports
- `event.season`: optional `YYYY` or `YYYY-YY` string, such as `2026` or `2025-26`
- `people`: up to 10 visible identifiable people, each with `name` (maximum 80 characters) and `confidence` from 0 to 1. Only list a person when the name is supported by evidence (caption, visible name/number, or unmistakable face) with confidence >= 0.8; otherwise leave `people` empty.
- `event`: `series` (maximum 40), `event_name` (maximum 80), `year` from 1950 to 2100, `season`, and a session enum
- `evidence`: maximum 300 characters
- `caption`: maximum 160 characters, in English

`context.circuit_visible` remains for compatibility and must be true only when a circuit is visible. Use `venue` for all new labels. Dates require evidence. Never invent names or dates.

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
{"id":14870,"attributes":{"sport":"basketball","venue":{"type":"court","visible":true},"shot_type":"athlete_in_action","people_count":1,"activity":"dunking","expression":"emotional","context":{"session":"game","rain":false,"night":false,"circuit_visible":false},"kit_colors":["purple","gold"],"quality":{"blurry":false,"obstructed":false},"caption":"A forward finishes a dunk during the game"},"people":[],"event":{"series":"NBA","event_name":"Regular Season","year":2026,"season":"2025-26","session":"game"},"evidence":"Court lines, basket and scoreboard are visible; faces not identifiable"}
```

Use one label set per photo. Only name a person when the name is supported by the caption, a visible name or number, or an unmistakable face, and confidence is at least 0.8; otherwise leave `people` empty. Do not call an AI model through this API; Muse performs the vision work and posts the result. A per-item validation failure is reported in the results response and does not cancel the batch.

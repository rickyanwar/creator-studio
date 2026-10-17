# AGENTS.md — Studio (ig-fb-reposter)

This is the single source of instructions for every coding agent: Claude Code, Codex, opencode, Gemini and others. `CLAUDE.md` only imports this file.
Keep it short. Detailed knowledge lives in `docs/CODEMAP.md` and `docs/codemap/*.md`. Read only the area you need.

## 0. Start here (every session, every agent)

**Executors / sub-agents:** if an orchestrator gave you a specific task (a prompt or a `.notes/TASK*.md` file), do only that task. Treat `HANDOFF.md` as background context, never as your instructions, and follow the task's own forbidden list.

1. Read `.notes/HANDOFF.md` (intent, written by the last agent) and `.notes/HANDOFF.auto.md` (facts, written by a script after every agent turn: branches, worktrees, changed files, running executors, and whether HANDOFF.md is STALE). Continue from them rather than restarting. When HANDOFF.auto.md says STALE, the last session died before recording its last step: trust the facts, inspect the changed files, then rewrite HANDOFF.md before you continue.
2. For the area you will touch, read the matching `docs/codemap/<area>.md` (index in `docs/CODEMAP.md`). Do not re-scan the whole codebase.
3. Before editing a function, read the real code. The codemap points the way; the code is the truth. If they disagree, fix the codemap in the same change.

## 1. What this app is

Studio is a content factory for Facebook fan pages (motorsport, UFC and other niches). It collects source material, makes posts with AI (copy, image cards, video), and publishes them through Repliz or real Android phones. It runs on one VPS (`/opt/studio`, domain autostudio.pro) with docker compose.

Content modes:

| Mode | What it makes |
|---|---|
| 1 | IG repost |
| 2 | News → image card |
| 3 | IG recreate |
| 4 | Discussion card |
| 5 | Pinterest card |
| 6 | FB photo recreate |
| 7 | YouTube clips |
| 8 | AI video (Google Flow via `gflow_worker`) |
| Viral Radar | Recreates fast-moving IG posts |

Layout:

| Path | What |
|---|---|
| `backend/app` | FastAPI + Celery: `api/`, `tasks/`, `services/`, `models/`; migrations in `backend/alembic` |
| `frontend/src` | Next.js 14 admin UI (app router) |
| `renderer/` | headless Chrome + Fabric.js card renderer (:3001) |
| `gflow_worker/` | Mode 8 worker, runs on the VPS host via systemd |
| `phone_runner/` | Android phone publishing over Tailscale ADB (host service) |
| `nginx/` | reverse proxy. It strips `/api` before FastAPI |
| `.github/workflows/deploy.yml` | push to `main` = production deploy |

## 2. Commands

Run from the repo root. Never run a bare `pytest` from the root: it crawls media folders and `node_modules`.

```bash
backend/venv/bin/python -m pytest backend/tests -q                   # ~1.5k backend tests, SQLite in-memory
backend/venv/bin/python -m pytest backend/tests/test_x.py -k name -v  # one file / test
backend/venv/bin/python -m pytest gflow_worker/tests -q
backend/venv/bin/python -m pytest phone_runner/tests -q
(cd renderer && node --test test/fit.test.js)                        # renderer layout tests
(cd frontend && npm run lint && npm run build)                       # lint + typecheck + build
# Celery import check — REQUIRED before any push that touches backend/app/tasks or its imports:
(cd backend && venv/bin/python -c "from app.tasks.celery_app import celery_app; [__import__(m) for m in celery_app.conf.include]; print('ok')")
```

- Integration tests (`backend/tests/integration/`) need a migrated Postgres. Set `TEST_DATABASE_URL=<local postgres url>`; without it they skip.
- `backend/venv` exists on the owner's Mac. Elsewhere, create a venv from `backend/requirements.txt`.
- Do not run `npm ci`/`npm install` when `frontend/node_modules` already exists.

## 3. Hard rules

Breaking these has caused real incidents.

**Git and deploy**
- Never commit, push, merge or deploy without the owner's explicit OK for that specific action. One approval does not carry over to the next one.
- Pushing to `main` deploys to production right away.
- Never use `git reset --hard`, `stash`, a force push, or delete branches or worktrees you did not create.
- Sub-agents and executors must never run git write commands.

**Database**
- Never use `alembic revision --autogenerate`. Write migrations by hand.
- Migrations must stay **additive**: a deploy rollback restores old images but keeps the new schema.
- Keep exactly one alembic head.

**API**
- FastAPI routers use bare prefixes (`APIRouter(prefix="/fanpages")`), because nginx strips `/api`.
- `TestClient` tests do not catch a wrong `/api` prefix. Check the real URL.

**Celery**
- Every task module must be listed in `celery_app.py` `include=[...]`. One bad import crash-loops every worker. Run the import check above.
- Long tasks never go on the default `celery` queue:
  - `gallery` queue: gallery downloads and labels
  - `video` queue: ffmpeg work
  - `visual` queue: AI image redesign
  - `delivery` queue: social delivery dispatch (phone posting) + phone watchdog, own single-slot `worker-delivery`

**VPS**
- Small box, about 1.4 GB free RAM. Never enable `hq_upscale` (it OOMs). Keep `worker-video` memory caps.
- Never publish db/redis ports (no `ports:` for `db`/`redis`).
- After editing `.env` on the VPS, recreate **all** containers.
- A change in `gflow_worker/` needs a `gflow-worker` systemd restart. The deploy only restarts it when no video job is running.

**Secrets**
- Never commit `.env`, keys or tokens.
- Never print secrets in logs, docs or reports. Write `<redacted>`.

**Language**
- Everything in the repo is **English**: UI labels, docs, comments, commit messages.
- Only chat replies to the owner are in Indonesian.

## 4. Product rules (owner decisions)

- **Accuracy over availability.** Image and identity checks fail closed: when a vision check is unsure or errors, skip the photo. A wrong-person photo is worse than no post.
- **Quotes stay verbatim.** Never shorten or paraphrase a quote from the source. Fix long quotes with layout only.
- **AI image edits:**
  - Never change a real face (verify identity numerically).
  - Keep natural skin texture and grain.
  - No over-sharp or HDR look on bikes and cars.
  - Keep all real logos. Text comes from the fan page's own templates.
- **Mode 8 videos must look real and be physically logical.** Reject illogical scenes and give the reason.
- **AI model use:**
  - All AI goes through 9Router.
  - Only use model ids listed on the live `/v1/models`. Never guess ids.
  - Use long fallback chains.
  - `cx/*` (Codex) models are for image generation only.
- **Renderer:** keep the Chrome renderer (not Pillow) for cards.
- **Publishing pacing:**
  - Per-destination timezone + sleep window.
  - Minimum gap is a floor.
  - Never resubmit a delivery that already has an external id or is `uncertain`.

## 5. Working style

- Plan first for multi-file work. Write tests with or before the code. Run the relevant suites twice for flaky areas.
- Read whole functions before changing them. Grep the diff for changed signatures and removed imports before you call it done.
- Match the surrounding code style and comment density.
- Report honestly: failed tests, skipped steps and unverified claims are stated as such.

## 6. Handoff protocol (switching agent or account mid-task)

Quota on one account can end at any time, and the next agent may be a different product. So:

- Keep `.notes/HANDOFF.md` (local, git-ignored) current. Update it after each finished step, and immediately if you notice you are running low on budget. Use this format:

```markdown
# HANDOFF — <task title>            (updated <YYYY-MM-DD HH:MM>, by <agent/account>)
Goal: <one line>
Branch / worktree: <branch> at <path>        Base: <sha>
Plan file: <path to PLAN.md, if any>
Done:  1. ... (commit/sha or "uncommitted")  2. ...
Next:  <the exact next action, with file/function names>
Verify: <commands that prove it works>
Risks / open questions: ...
Do not: <anything the owner forbade for this task>
```

- Automation on the owner's Mac:
  - `~/.local/bin/agent-handoff-snapshot` rewrites `.notes/HANDOFF.auto.md` after every turn. It is triggered by the Claude Stop hook in `.claude/settings.json`, Codex `notify` (`~/.codex/notify-chain.sh`) and the Gemini `AfterAgent` hook.
  - For Claude, `agent-handoff-guard` also blocks the stop once when files changed after HANDOFF.md was last written.
  - Other agents must do the HANDOFF.md update themselves, at the end of every turn that changed files.
- Multi-step tasks keep a `PLAN.md` in their worktree with per-step status.
- When the task is merged or abandoned, clear `HANDOFF.md`, or move it to `.notes/archive/`.
- Put durable knowledge (how something works, why it was decided) in `docs/codemap/*.md` or `docs/`, not only in HANDOFF.

## 7. Where knowledge lives

| What | Where |
|---|---|
| Instructions for all agents | `AGENTS.md` (this file) |
| Architecture map + known issues | `docs/CODEMAP.md` → `docs/codemap/*.md` |
| Specs, APIs, runbooks | `docs/*.md`, `docs/runbooks/` |
| Live unfinished work | `.notes/HANDOFF.md` (local only) |
| Scratch reports, raw scans | `.notes/` (local only, never committed) |

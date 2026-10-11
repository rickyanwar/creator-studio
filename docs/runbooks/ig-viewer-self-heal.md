# IG viewer self-heal — Hermes VPS runbook

## Purpose and hard rules

Detect sustained Mode 1 viewer tier failures, prove our scraper broke against the owner's working reference, request Telegram approval, then fix and open a PR. Run as root on VPS; cron notepad persists between runs. Telegram chat sessions must read that notepad **and** the incident JSON before acting.

- NEVER edit or restart anything in `/opt/studio` or production containers. Reading files and `docker exec` for the health command are allowed. NEVER deploy, merge PRs, or push to `main`.
- NEVER print secrets, tokens, or credential-bearing URLs in Telegram, PRs, test evidence, or logs. Keep credentials outside repo.
- Only one open incident across tiers; no new fix/approval while one is `awaiting_approval`, `fixing`, or `pr_opened`. At most **3 fix attempts per incident**; then report failure with evidence and stop.
- Dedupe script-broken alerts **per tier for 24 hours** using cron notepad timestamps. Dedupe health-command failure notes for 6 hours. Record site/reference failures there too. Do not repeat Telegram messages on every run.
- NEVER execute agent-edited candidate code on the host. Run candidate pytest and live checks only in a throwaway container created from the deployed worker image, with code mounted read-only and no Docker socket, credentials, or unrelated host paths.
- Fix only after the owner replies `YA PERBAIKI <incident-id>` for the matching `awaiting_approval` incident. Accept it only from the Telegram user ID in Hermes `TELEGRAM_ALLOWED_USERS`, in that owner's private chat. `TIDAK <incident-id>` closes it; silence is not approval.

## Detection — every 30 minutes

Run `docker exec studio-api-1 python -m app.scripts.ig_viewer_health`. Success prints sanitized JSON and exits 0. Parse top-level `generated_at`, `tiers`, `all_tiers_down`. Each `tiers.<tier>` (`gramsnap`, `anonyig`, `igstoryviewer`) has `last_success_at`, `last_failure_at`, `streak_started_at`, `consecutive_failures`, `distinct_users`, `last_error`, `last_error_kind`, `unhealthy`. Times are UTC ISO 8601; `last_error` is capped at 300 characters. `all_tiers_down` is informational, not a repair trigger.

An `unhealthy` tier has at least 5 consecutive failures on 2 distinct users over at least 60 minutes, with `last_error_kind` `script` or `blocked`. `site_down` alone never triggers repair. If no tier has `unhealthy=true`, exit silently (except recovery check below). Unavailable data prints `{"error":"health_unavailable"}` and exits 2. If that occurs, the container is down, output is not valid JSON, or the command otherwise fails, send one short note at most once per 6 hours and stop; do not infer scraper health from missing data. Health and live-check errors are sanitized, but never paste raw logs or stack traces into Telegram or PRs; summarize them.

> `Cek IG tertunda: data kesehatan tidak tersedia (<alasan singkat>). Saya cek lagi pada jadwal berikutnya.`

## Confirm before alerting

For each unhealthy tier when no other incident is open, select 2 valid public usernames from this fixed allowlist: `marcmarquez93`, `f.1interviews`, `crossesup`, `effettogp`, `calfkicks`. If another username is needed, first validate it against `^[A-Za-z0-9._]{1,30}$`. Always pass usernames as separate argv tokens; never interpolate Telegram/text input into a shell command string. Under `/root/ig_test/venv_ig`, run owner's proven reference for **that tier and both users**, then run our deployed code against the **same two users**:

| Tier | Owner's reference in `/root/ig_test` |
| --- | --- |
| `gramsnap` | `fast_test.py` using `anonyig_hybrid_monitor.process_username`, or explicitly `fetch_via_dom` from `anonyig_hybrid_monitor.py`; inspect output to ensure GramSnap path actually ran. |
| `anonyig` | `fast_test.py` using `anonyig_hybrid_monitor.process_username`; explicitly verify AnonyIG path ran, or call the matching function in `anonyig_hybrid_monitor.py`. |
| `igstoryviewer` | `test_only_igstory.py`, or `fetch_via_igstoryviewer` from `anonyig_hybrid_monitor.py`. |

Use `/root/ig_test/venv_ig/bin/python` to execute the owner's host-provided references; inspect their entry points for user arguments instead of guessing flags. Confirm reference returned at least 1 post **from the named tier**, not merely a successful fallback. Confirmation may also run deployed, reviewed code on the host under `/root/ig_test/venv_ig`, read-only against `/opt/studio/backend`:

```sh
/root/ig_test/venv_ig/bin/python /opt/studio/backend/scripts/ig_viewer_live_check.py --backend-dir /opt/studio/backend --tiers <tier> --users <user1>,<user2> --json-out /root/hermes-work/<tier>-confirm.json
```

Standalone live check accepts `--backend-dir`, `--tiers` (one comma-separated value, e.g. `gramsnap,anonyig`, or `all`), `--users` (one comma-separated value), `--rounds`, `--sleep`, `--json-out`, `--dry-run`. Exit 0 only if **every** tier/user/round run returned at least 1 post. JSON summary fields: `runs` (each with `round`, `tier`, `user`, `ok`, `posts`, `albums`, `kind`, `error`, `seconds`), `passed`, `failed`, `all_passed`. `--tiers all` exercises the **fallback flow**, not each tier independently; explicit tier names call `fetch_with_tier`. Read per-user failures; never mistake a successful fallback tier for success on a requested tier. Avoid rapid retries against viewer sites.

| Evidence | Action |
| --- | --- |
| Reference OK on both users; ours FAIL on either | `SCRIPT BROKEN`: alert with evidence; open one incident. |
| Reference and ours both FAIL on same users | Likely site/Cloudflare issue: note first seen, tier, users, outcomes in cron notepad; no repair alert. Re-check next run. If still failing **more than 24 hours** after first confirmation, send one informational Telegram note (no fix offer), deduped per tier; reset timer after recovery. |
| Ours OK now | Transient: no alert; clear pending site-failure observation. |
| Reference inconclusive, mixed results, or only fallback succeeded | Do not label script broken. Record uncertainty in notepad; retry next run. |

> `Info IG <tier>: referensi dan skrip kita sama-sama gagal >24 jam pada <jumlah> akun. Kemungkinan situs/Cloudflare bermasalah; saya pantau, tanpa perubahan kode.`

## Script-broken alert and incident record

Before sending alert, check per-tier 24-hour dedupe timestamp and the one-open-incident rule. Create `/root/hermes-work/incidents/<id>.json` with `id`, `tier`, `started` (UTC streak start), `status: "awaiting_approval"`, 2 tested usernames, health counters/error, reference and ours results, proposed approach, alert timestamp, and `attempts: 0`. Create directory root-only (`chmod 700`); incident file `chmod 600`. Put incident ID/path, tier, started, status, alert timestamp, and per-tier dedupe timestamps in cron notepad so chat sessions can locate it. Never include tokens in incident files. If same tier is still pending, update evidence without re-alerting. Approval expires 24 hours after the alert timestamp; send a fresh alert before accepting a later reply.

> `IG <tier> bermasalah sejak <waktu UTC>: <gagal beruntun> gagal, <jumlah akun> akun (uji: <akun1>, <akun2>). Error <jenis>: <pesan singkat>. Referensi: <hasil 2 akun>; skrip kita: <hasil 2 akun>. Dugaan: <penyebab>. Usulan: <perbaikan singkat>. Boleh saya perbaiki? Balas: YA PERBAIKI <incident-id> / TIDAK <incident-id>`

## After Telegram approval — fix and verify

Chat session: read cron notepad and `/root/hermes-work/incidents/<id>.json`; verify status is `awaiting_approval`, incident ID matches exactly, reply is no more than 24 hours after the latest alert, sender ID is in `TELEGRAM_ALLOWED_USERS`, chat is that owner's private chat, and no competing incident exists. Record approving Telegram message ID and timestamp in incident JSON, then atomically change status to `fixing` in JSON and notepad. That transition consumes approval; never reuse it for another attempt or incident. Keep all edits under `/root/hermes-work/creator-studio`, a separate clone created with `gh repo clone rickyanwar/creator-studio /root/hermes-work/creator-studio` once. Do not work in `/opt/studio`.

```sh
git fetch origin && git checkout -B hermes/fix-ig-viewer-<tier>-<YYYYMMDD> origin/main
```

Run from `/root/hermes-work/creator-studio` only after confirming no uncommitted work or branch with an existing PR would be overwritten. Compare failure with `/root/ig_test/anonyig_hybrid_monitor.py` and `.ref`-style `master_fetch` behavior; make minimal change in `backend/app/services/ig_viewer_scraper.py` or `backend/app/services/ig_media.py`. Count each edit-and-test cycle as one attempt, maximum 3. Record test output and attempt count in incident JSON and notepad.

**All gates must pass before pushing or opening PR. Candidate code must never run on the host.** Resolve the deployed image exactly once:

```sh
IMAGE="$(docker inspect studio-worker-1 --format '{{.Config.Image}}')"
```

Never mount `/var/run/docker.sock`, `/root` itself, any other `/root` path, `/opt/studio`, or the `gh` token into candidate containers. The sole allowed `/root` source is `/root/hermes-work/creator-studio/backend`, mounted read-only at `/src`. Containers run non-root, drop all capabilities, and disallow privilege escalation. If container user cannot write HOME or temporary files, add `-e HOME=/tmp`.

1. Pytest in throwaway container, without network. The worker image has no pytest, so build a test image once (rebuild whenever the worker image changes; needs network only during this build):
   ```sh
   printf 'FROM %s\nRUN pip install --no-cache-dir pytest==9.1.1\n' "$IMAGE" | docker build -q -t studio-worker-test -
   ```
   Then run tests on `studio-worker-test` with no network:
   ```sh
   docker run --rm --network none --user 1000:1000 --cap-drop ALL --security-opt no-new-privileges -e HOME=/tmp -e PYTHONDONTWRITEBYTECODE=1 -v /root/hermes-work/creator-studio/backend:/src:ro -w /src studio-worker-test python -m pytest tests -q -p no:cacheprovider
   ```
   (verified: 319 passed, 1 skipped)
2. Live check failing tier, **3 allowlisted public users, 2 rounds, 3/3 each round**, in a fresh networked throwaway container. `--users` takes ONE comma-separated value. Run the script inside a `sh -c` wrapper so that valid JSON goes to stdout; redirect docker run stdout to the host file:
   ```sh
   docker run --rm --user 1000:1000 --cap-drop ALL --security-opt no-new-privileges --shm-size=1g -e HOME=/tmp -v /root/hermes-work/creator-studio/backend:/src:ro "$IMAGE" \
     sh -c "python /src/scripts/ig_viewer_live_check.py --backend-dir /src --tiers <tier> --users <user1>,<user2>,<user3> --rounds 2 --json-out /tmp/r.json >/dev/null 2>&1; cat /tmp/r.json" \
     > /root/hermes-work/<tier>-fixed.json
   ```
   (verified: sandboxed run returned valid JSON, all 3 tiers 12 posts)

   **Exit code note:** the exit code of `docker run` here reflects `cat`, not the live-check script. Judge success from the JSON fields `all_passed` and per-run `ok`, not the exit code.

3. Regression, fallback flow, 1 round; every user passes. Use another fresh container. Apply the same `sh -c` / `cat` form and comma-separated `--users`:
   ```sh
   docker run --rm --user 1000:1000 --cap-drop ALL --security-opt no-new-privileges --shm-size=1g -e HOME=/tmp -v /root/hermes-work/creator-studio/backend:/src:ro "$IMAGE" \
     sh -c "python /src/scripts/ig_viewer_live_check.py --backend-dir /src --tiers all --users <user1>,<user2>,<user3> --rounds 1 --json-out /tmp/r.json >/dev/null 2>&1; cat /tmp/r.json" \
     > /root/hermes-work/all-tiers-regression.json
   ```
   Also run the per-tier form (`--tiers gramsnap,anonyig,igstoryviewer` — comma-separated) and redirect stdout to a different host JSON path to prove each tier independently; `all` alone can hide a broken tier through fallback. Each non-failing tier must pass its one-round checks.
   ```sh
   docker run --rm --user 1000:1000 --cap-drop ALL --security-opt no-new-privileges --shm-size=1g -e HOME=/tmp -v /root/hermes-work/creator-studio/backend:/src:ro "$IMAGE" \
     sh -c "python /src/scripts/ig_viewer_live_check.py --backend-dir /src --tiers gramsnap,anonyig,igstoryviewer --users <user1>,<user2>,<user3> --rounds 1 --json-out /tmp/r.json >/dev/null 2>&1; cat /tmp/r.json" \
     > /root/hermes-work/all-tiers-explicit-regression.json
   ```
   Exit code note applies here too: judge from JSON, not exit code.

Inspect exit codes **and** JSON summary; tests must represent the intended tiers, not fallbacks. Failed gate: diagnose and retry within 3 attempts; on third failure mark incident `failed`, report which gates failed and evidence to owner, then stop. No PR from failing tests.

> `Perbaikan IG <tier> belum berhasil setelah 3 percobaan. Gagal: <uji + hasil singkat>. Bukti: <ringkasan>. Tidak ada PR atau deploy. Mohon tinjau manual.`

On green gates, commit only intended code/tests, push **feature branch only**, and create PR with `gh pr create --base main --head hermes/fix-ig-viewer-<tier>-<YYYYMMDD> --title "Fix IG viewer <tier> scraper" --body "<body>"`. Body template (fill actual results, not claims):

```text
Cause: <root cause; reference comparison>
Change: <minimal code change>
Tests: pytest <passed count>; live <tier> 3/3 users x 2 rounds; regression fallback and each tier x 1 round
Live-check JSON summary: <paste sanitized per-tier/user/round totals and errors from JSON outputs>
```

Save PR URL, `head_sha` of the branch, and `status: "pr_opened"` in incident JSON and cron notepad. Send:

> `Perbaikan IG <tier> siap: <tautan PR> (SHA: <head-sha>). Penyebab: <singkat>. Uji: pytest <hasil>; live 3/3 akun x 2 putaran; regresi tiap tier lulus. Boleh saya merge dan deploy? Balas: YA MERGE <incident-id>`

## After PR opened — Telegram approval to merge

Chat session: read cron notepad and `/root/hermes-work/incidents/<id>.json`; verify status is `pr_opened`, incident ID matches exactly, reply is no more than 24 hours after the alert, sender ID is in `TELEGRAM_ALLOWED_USERS`, and chat is private. Verify that the PR's CI is green and the current head SHA of the PR matches the `head_sha` saved in the incident JSON exactly (any new commit invalidates the approval). Record approving Telegram message ID and timestamp in incident JSON, then atomically change status to `merging`.

Once approved, merge the PR using `gh pr merge --squash` (do not use admin bypass). Wait and watch the CI deploy, then check the health command. If the health command confirms recovery (new `last_success_at` after merge, `unhealthy=false`, `consecutive_failures=0`), send a recovery message and set incident `closed` in JSON and notepad.

> `IG <tier> pulih. PR <tautan PR> sudah digabung dan di-deploy; cek terbaru berhasil pada <waktu UTC>. Insiden ditutup.`

If still unhealthy or deploy fails, keep incident open, record evidence, and propose a new fix (or PR revert, which also needs a new `YA MERGE <incident-id>` approval process). Do not start a new fix without fresh owner approval.

## One-time VPS setup — owner/Claude, not Hermes

Steps 1–4 (install `gh`, configure token, clone, and fix Hermes config) were completed on **2026-10-05**. Create/enable cron only after Phase 2 is deployed.

1. Install `gh` from GitHub CLI apt repository as root (verify current commands at https://github.com/cli/cli/blob/trunk/docs/install_linux.md):
   ```sh
   apt update && apt install -y curl gpg
   curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg -o /usr/share/keyrings/githubcli-archive-keyring.gpg
   chmod go+r /usr/share/keyrings/githubcli-archive-keyring.gpg
   printf 'deb [arch=%s signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main\n' "$(dpkg --print-architecture)" > /etc/apt/sources.list.d/github-cli.list
   apt update && apt install -y gh
   ```
2. Create fine-grained GitHub PAT scoped **only** to `rickyanwar/creator-studio`, with repository permissions **Contents: Read and write** and **Pull requests: Read and write**. Store token in root-only file outside repo (`chmod 600`; parent directory root-only), then run `gh auth login --with-token < /root/<token-file>` as root; check `gh auth status` without sending output to Telegram. Do not commit token or credential-bearing clone URLs. Configure root git identity for Hermes commits: `git config --global user.name "Hermes Agent"` and `git config --global user.email "<owner-approved-email>"`.
3. Create `/root/hermes-work` and `/root/hermes-work/incidents` with mode 700; clone once: `gh repo clone rickyanwar/creator-studio /root/hermes-work/creator-studio`. Ensure this runbook is present in clone and `venv_ig` plus references work before scheduling.
4. Fix duplicate `api_key` near line ~283 in Hermes `config.yaml` via `hermes config edit` (remove duplicate only), then `hermes config check`. Do not show key contents in output or chat.
5. **After Phase 2 deploy only:** cron command template, adapted to actual `hermes cron create --help` flags before running:
   ```sh
   hermes cron create <schedule-option> '*/30 * * * *' <prompt-option> 'Read /root/hermes-work/creator-studio/docs/runbooks/ig-viewer-self-heal.md in full. Follow Detection and Confirmation each run; use persistent per-job notepad and /root/hermes-work/incidents for dedupe, incident state, and approval handoff. Never fix without matching Telegram approval. Never edit or restart /opt/studio, deploy, merge, or push main.' <name-option> ig-viewer-self-heal
   ```
    Replace placeholder options and shell quoting with syntax from local help; cron must run every 30 minutes with persistent per-job notepad. Configure Telegram owner as recipient through Hermes settings; test one manual run before enabling schedule.

## Residual risks for owner

- Fine-grained PAT can technically push to `main`. Add GitHub branch protection/ruleset requiring PRs. Owner bypass preserves the owner's deploy pushes, but cannot stop a PAT acting as that same user. Better: replace it with a dedicated machine-user or GitHub App token limited to PR-only rights.
- Hermes runs as root with terminal access in the existing setup; these controls are procedural. Later run Hermes as an unprivileged user.

## Known environment facts

- **Scraper browser timezone:** the browser must report the host timezone (`IG_VIEWER_TIMEZONE`, default `Asia/Jakarta`). With a UTC browser timezone, Cloudflare blocks GramSnap and AnonyIG: the posts API stays 422 because `/api/cf` never fires. Always verify this env var is set before diagnosing mysterious 422 errors on those tiers.
- **Worker `/dev/shm`:** production worker containers have 64 MB `/dev/shm`. That is sufficient for normal operation; do not treat it as a bug.
- **Candidate gate containers:** candidate (non-production) containers use `--shm-size=1g` to give the browser more shared memory during testing. Do not apply this to production worker restarts.

## Troubleshooting and pause

- Missing JSON or command exit 2: check `docker inspect studio-api-1` and health CLI/Redis from the allowed read-only surface; send deduped note, stop. Do not restart services.
- Reference tests use wrong tier or no posts: inspect `/root/ig_test` entry points and tier-specific output; do not classify inconclusive comparison as script broken.
- Live check fails despite pytest green: read saved JSON per run and compare reference behavior; retry within attempt cap. Confirm three known working users and installed reference venv before judging code.
- Lost chat context: read cron notepad plus `/root/hermes-work/incidents/<id>.json` before replying or editing. Stop if state/approval cannot be verified.
- Pause job with `hermes cron pause <job-id-or-name>` (confirm accepted argument with `hermes cron pause --help`); inspect cron listing to verify paused. Resume only after owner decides.

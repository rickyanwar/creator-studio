# Phone group composer exploration

Live-checked 2026-11-10 through the deployed VPS runner against the real phone. Status: **partial accessibility verification; publishing is not verified or authorized by this runbook**.

## Mandatory boundaries

Never post into a group during testing. Never tap Post, Share, Publish, or Send. Stop at the composer and discard every draft. Do not use `Add groups` / `Crosspost to more groups`. Do not change phone, app, account, keyboard, or story settings. Never open Software update. Never install or restart services for exploration.

Before touching the phone and at least every five minutes, run a read-only delivery check. Stop if a phone delivery is active/claimed or due within twenty minutes. The live table uses `intended_method` / `actual_method`, not `method`:

```sql
BEGIN READ ONLY;
SELECT id,state,scheduled_for,claimed_at
FROM publish_deliveries
WHERE (intended_method='phone' OR actual_method='phone')
  AND (state IN ('claimed','publishing')
    OR (claimed_at IS NOT NULL
        AND state NOT IN ('published','failed','skipped','uncertain','needs_human'))
    OR (state IN ('scheduled','waiting_for_slot','waiting_for_device')
        AND scheduled_for < now() + interval '20 minutes'));
COMMIT;
```

Use deployed `phone_runner.adb` with `PYTHONPATH=/opt/phone_runner` on the VPS host. Never invoke bare adb in SSH. Do not call publishing/proof helpers blindly: some change keyboard settings or execute submission. Resolve current labels from fresh hierarchy captures; coordinates below are evidence, not reusable selectors.

## 1. Choose the actor before opening the group

1. Open own profile. `Profile, tab 6 of 6` returned the observed profile to its header even when `Go to profile` restored an old Reels scroll position.
2. Tap clickable `android.widget.Button`, content-desc `Open profile switcher`.
3. Choose the unique exact actor row. Observed rows were clickable `android.view.ViewGroup` with content-desc `Agung, 2 notifications` and `GP Weekend, 8 notifications`. Notification counts are dynamic. Child text identifies the actor; GP Weekend also has child text `Page`.
4. Verify the current actor in Menu (`Agung, see your profile` / `GP Weekend, see your profile`) and again inside the group composer. Agung's own profile also exposed `Dashboard` and owner edit controls.

Visible names establish observed UI identity only; numeric actor IDs were not independently recovered during this exploration.

## 2. Find joined groups

Runner requests accept a missing or null group URL and use the existing Your groups name search; a supplied URL must still be valid, and backend catalog URLs remain required.

Agung:

- Menu → `See more` exposed text `Groups` in a nonclickable ViewGroup (`Groups, button 12 of 18`); its text bounds can be used only as a last-resort observed tap target.
- A directly verified alternative is clickable `Groups, tab 4 of 6`.
- Select `Your groups, 2 of 4` (`android.widget.TabWidget`). Search input: text `Search your groups by name`, class `android.widget.EditText`.
- Joined-list group names are text/content-desc on nonclickable ViewGroups. Tapping the observed name bounds opened the group. Avoid adjacent `Pin group` / `Unpin group` controls.
- `Max Verstappen🏆RedBull Racing` and `F1` both showed `Joined` and a working group composer under Agung.

GP Weekend:

- Menu → text `Groups` (`Groups, button 2 of 4`) opened Groups.
- Tabs are `For you, 1 of 3`, `Your groups, 2 of 3`, `Discover, 3 of 3`; no Your activity tab was observed here.
- Joined list showed `FORMULA 1 MEMES`, `F1`, `Formula 1 Fans Group ✅`.
- Do not conflate this last name with Agung's separately observed `Formula 1 Fans Group - Formula1FansGroup.com`.

Group IDs/URLs remain **UNVERIFIED**. The hierarchy did not expose them, the filtered activity dump returned none, and the live group catalog was empty. Do not construct numeric URLs from names. The local exploration report contains the partial Agung inventory and timestamped evidence.

## 3. Open a new group composer

1. On the joined group screen, tap text/content-desc `Write something...`, class `android.widget.Button`.
2. Verify exact group title at the top and `Public group` beneath it.
3. Verify author text/content-desc `Agung` or `GP Weekend` (nonclickable `android.view.ViewGroup`). The author label in the composer is not an actor-switch button.
4. Empty input is `android.widget.EditText`, content-desc **`Post Title`**, text **`Submit a public post for admin approval…`**. This was observed in both tested groups, including the Page F1 composer.
5. Bottom submission control is content-desc **`Post`**, `android.widget.Button`, observed bounds `[786,2108][1044,2240]`. It was nonclickable when empty and clickable with attached media. **Never tap it in testing.**

Most relevant composer controls have empty resource IDs. The toolbar `Menu` and several navigation controls expose only `com.facebook.katana:id/(name removed)`; that is not a unique selector.

This is different from the existing feed share-intent composer: no verified `Next` → Post settings → `SHARE` sequence. Direct group media previews retain a bottom `Post` control.

### Author chip on the group screen

In Max Verstappen's group, the clickable `Action chip profile picture` before the input opened a sheet with radio button `Agung` and button `Create a nickname, This hides your main profile`. No Page choice was observed in that sheet. It was dismissed without selecting anything. Choose Page identity through the global profile switcher instead; GP Weekend's F1 author was verified after that switch.

## 4. Media differences

### Video through composer Gallery

1. Empty group composer → button `Gallery`.
2. Picker header text `New post`; controls `Cancel`, `Select album, Gallery`, `Select multiple`, `Privacy Settings`, `Camera`. Do not open privacy settings.
3. Example item desc: `Video, item 1, taken on Nov 09, 2026 04:54, 11 seconds`.
4. Selecting it returned directly to the group composer, author `Agung`.
5. Input hint changed to `Say something about this video…`, desc still `Post Title`.
6. Preview exposed `Video`, `Edit video`, `Remove video`, `Add more photos/videos from gallery.` and bottom `Post`.

No Reel Settings screen was observed on this path. Separate Reel creation and behavior after submission are **UNVERIFIED**.

### Photo through group Photo/Video

1. Group screen → button `Photo/Video` opens a different picker layout, with top `Cancel`, `Select album, Gallery`, `Privacy Settings`.
2. Select a photo. Example: `Photo, item 12, taken on Nov 06, 2026 22:52`.
3. Selected strip exposed `Selected media, item 1 of 1. Double tap to deselect.` and button `Next`.
4. `Next` returned to the group composer with `Photo`, `Edit photo`, `Remove photo`, `Open menu`, and `Add more photos/videos from gallery.`.
5. Input hint: `Say something about this photo…`; author `Agung`; bottom `Post` remains untouched.

### Two-photo album / multi-photo post

1. From the single-photo composer, `Add more photos/videos from gallery.` reopened the `New post` picker with the original photo selected.
2. Scroll to a second photo and select it. Duplicated accessible nodes may include both CompoundButton and Button for the same item; use the matching `android.widget.Button`, never tap both.
3. Selected strip exposed `Selected media, item 2 of 2. Double tap to deselect.`.
4. `Next` returned to the group composer with `Photo 1 of 2, expand photo` and `Photo 2 of 2, expand photo`.
5. Input hint: `Say something about these photos…`.
6. `Album. 4 of 4. Press to add an album to your post` was also present, partly clipped at the right edge. Named album creation was **not tested**.

Caption typing was not exercised. No text was entered in a group or elsewhere.

## 5. Discard and verify exit

- With media attached, Back opened **`Want to finish your post later?`**.
- Choices: `Save as draft`, **`Discard post`**, `Continue editing`.
- `Discard post` is text/content-desc on a nonclickable ViewGroup, inside a clickable parent with generic desc `button`. Resolve the containing row or observed text bounds; do not select a generic `button` without checking its child.
- Video discard returned to the group feed.
- Photo/album discard returned to the original Photo/Video picker, with its previous one-photo selection still visible. Tap **`Cancel`** to leave that picker and verify `Write something...` on the group feed.
- Empty composers exited directly with Back; no save/discard sheet appeared.
- Never use `Save as draft` as cleanup.

At completion restore the initial actor, force-stop `com.facebook.katana`, send HOME, verify launcher, and rerun the read-only delivery check. The session restored the original Formula Weekend actor and verified launcher after force-stop. All safety gates were clear and no phone delivery row's `updated_at` changed during the session.

## Timing, popups, and unverified link retrieval

- Three-second settling waits followed each navigation; each tap used a fresh hierarchy capture. These are observed working waits, not measured minimums.
- One fresh hierarchy dump failed before an Agung switch tap; no tap happened. A later capture succeeded, then navigation resumed.
- `Go to profile` can restore deeply scrolled content. Do not infer the current identity from feed/reel layout.
- The media discard sheet above was the only encountered draft popup. No participation answers or nickname were entered.
- Group `You` opened Agung's member profile, showing `Member of Max Verstappen🏆RedBull Racing since October 4, 2026`, `Posts, 1 of 2`, `Comments, 2 of 2`. It did not verify a pending-post queue.
- `Member tools more options` did not visibly navigate in the recorded captures; no further path is claimed.
- Pending/approved group post links and Copy link availability remain **UNVERIFIED**. No post was created to test them; no Share control was used. Existing feed-profile resolver behavior must not be assumed to work for group posts.

Local evidence: `.notes/PHONE-GROUP-FLOW.md` and `.notes/scratch-phone/` (PNG and accessibility JSON). Screenshots could not be visually inspected by the model; findings above are grounded in accessibility nodes.

## S3b rehearsal — 2026-11-10

Implemented exact-label navigation in `phone_runner/group_publish.py`, including REVIEW-B outcome preservation,
typed membership/restriction/identity failures, direct rehearsal submission guard, safe exception logging, and
verified discard. Actor selection now precedes group navigation. All taps resolve a fresh hierarchy; the
nonclickable joined-group/discard text bounds are the documented last-resort fallback. No fixed coordinates.

Observed device: Facebook `580.0.0.51.74`, version code `475019391`, Android `14`, screen `1080x2408`.
The initial actor was Formula Weekend and was restored after each completed variant. Facebook was force-stopped
and HOME captured after each run. No Post/Share/Publish/Send control was tapped. The deployed runner directory
and service were not modified. The isolated `/tmp/gs-rehearse` copy was removed after evidence retrieval.
Corrected read-only delivery SQL ran before every device command; the final query returned zero rows.

### Live results (existing gallery media)

| Step | One photo | Two photos | One video |
|---|---|---|---|
| Choose Agung; verify Menu | OK | OK | OK |
| Your groups; search exact F1; verify Joined | OK | OK | OK |
| Open composer; verify F1 / Public group / Agung | OK | OK | OK |
| Attach media | OK: Photo/Video → photo → Next | OK: second picker selection → Next | OK: Gallery → video |
| Paste caption | OK | OK | OK |
| Verify author, group, caption and media count | OK | OK | OK |
| Discard; cancel residual picker; verify Write something... | OK | OK | OK |
| Restore Formula Weekend; close Facebook; HOME | OK | OK | OK |

These are **step-level UI rehearsals**, not successful end-to-end `/group-publish` or calibration runs.
The live catalog query `SELECT name,url FROM fb_groups WHERE name='F1'` returned zero rows. The contract
requires a valid group URL. No URL was invented and validation was not bypassed in either execution entrypoint.
The isolated driver called the UI steps with a name-only group and existing gallery media. Therefore URL VIEW
intent, media download/staging correlation, numeric actor-ID binding, and end-to-end calibration remain
**UNVERIFIED**. Production readiness is not established by this rehearsal.

The actual `execute_group_calibrate` call returned:

```json
{"ok":false,"fb_version":"","fb_version_code":"","android":"","screen":"","steps":[],"detail":"invalid_group_url"}
```

### Additional verified details

- Menu is an exact `android.widget.Button` label `Menu`, not necessarily a `Menu, tab ...` node.
  Read the current actor before toggling an already-open Menu. A profile intent may restore a separate profile
  view; return through the feed before verifying the actor in Menu.
- Your groups preserves previous search text. `Clear text` restores `Search your groups by name` before pasting.
  Match the joined-group `android.view.ViewGroup`, not the EditText whose current value is also `F1`.
  Group-header title text can end in nonbreaking spaces; strip surrounding whitespace only.
- Tapping `Post Title` with media can open an `Add text` editor. After clipboard paste and keyboard dismissal,
  exact button `Done` returns to the composer. Verify author/group again there.
- A fresh hierarchy can fail transiently during navigation. A bounded read-only recapture is allowed; no tap
  occurs without a successful current capture. Initial failed attempts were discarded/closed before retries.
- The album picker required a scroll to expose a second photo. The rehearsal driver computed swipe bounds
  from the current scrollable node; no saved coordinates were reused.
- Caption tested: `Rehearsal only. This draft will be discarded.` All filled drafts were explicitly discarded.
  No draft was saved. Success/pending-admin UI after submission and group permalinks remain unverified;
  unknown post-submit state stays `uncertain` rather than claiming publication.

Evidence: `.notes/scratch-rehearse/{photo,album,video}-*.json` and matching PNGs, plus calibration JSON.
Screenshots are saved but could not be visually inspected by this model; results use accessibility evidence.

Verification: focused runner tests passed twice (58 each); full runner suite passed (836).
Full backend: 3955 passed, 5 skipped, 344 setup errors because hostname `db` is unavailable locally;
no FAILED test lines. Celery include import check: `ok`.

## S3c end-to-end rehearsal — 2026-11-10

The isolated real `execute_group_calibrate` passed with one publicly served photo and a null group URL;
real `execute_group_publish` returned `rehearsed` for one photo, two photos, and one video, in that order.
All calibration steps passed; metadata was Facebook `580.0.0.51.74` / `475019391`, Android `14`, `1080x2408`.
This verifies name-search navigation, real media download/staging/picker correlation, caption entry on
publish rehearsals, draft verification/discard, actor restoration, and cleanup through the real entrypoints.
URL VIEW navigation, numeric actor-ID binding, and actual submission remain unverified.

Each run ended with no Facebook process and launcher accessibility nodes. Before/after listings matched
exactly: `/sdcard/Pictures/staging` retained its five existing files and `/sdcard/Movies/staging` stayed empty.
SQL gated every ADB command, including staging commands; submission controls and keyevents 66/160 were blocked.
Final safety SQL returned zero rows; zero phone delivery rows were updated since session start
`2026-11-10 03:54:14.561487+00`. The isolated `/tmp/gs-rehearse` directory was removed after evidence retrieval.
Evidence: `.notes/scratch-rehearse/e2e/` (final-screen JSON/PNG, results and staging listings).
Full runner suite: `845 passed` twice. No Post/Share/Publish/Send control was tapped.

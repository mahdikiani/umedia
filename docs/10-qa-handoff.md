# QA Handoff — for an independent testing agent (e.g. cursor-agent)

This doc exists for one specific loop: **Mahdi hands this file to another
coding/testing agent** (not this session), that agent works through the
checklist below against the live preview, Mahdi relays its findings back
to *this* Claude Code session, and Claude confirms/fixes/updates
`docs/09-tasks.md` accordingly. It is not a replacement for `09-tasks.md`
(the resumable build log) — it's a point-in-time verification pass over
what that log claims is done.

## Read this first

- **`docs/08-implementation-plan.md`** — the why: reframing from
  "filesystem" to "resource abstraction", the plugin architecture, the
  reuse table. Read this before anything else if the *shape* of the
  system is unclear.
- **`docs/09-tasks.md`** — the authoritative, resumable build log. Every
  phase, what's actually been verified and how, every real bug found and
  how it was fixed. **This is ground truth for "what should already
  work"** — if something in this checklist contradicts it, `09-tasks.md`
  wins; say so when reporting back rather than silently trusting this doc.
- **`docs/02-architecture.md`** through **`docs/05-api-design.md`** —
  concrete architecture/contract reference if you need to understand
  *why* something behaves a certain way before deciding it's a bug.
- **`docs/07-agent-instructions.md`** — the process rules this whole
  project has been built under (don't couple core to providers, write
  tests for provider contracts, explain tradeoffs before large changes).
  If you're going to *fix* anything (not just report), follow these, and
  match the existing code's style — small, well-commented, TDD where the
  codebase already has test coverage for the area you're touching.

## Environment

- **Live URL**: https://umedia.uln.me — a real `docker compose` deployment
  (`compose.media-preview.yml` at the repo root), not a local dev server.
  Frontend and backend are two containers behind Traefik on one domain
  (`/api/v1/*` → backend, everything else → frontend).
- **Admin login**: an admin account already exists on this deployment.
  Neither this Claude session nor you (the testing agent) has that
  password — Mahdi logs in himself, or resets it (see "Resetting the
  preview" below) if a clean first-run state is needed for onboarding
  tests.
- **Source**: `/home/mahdi/Projects/umedia` (`apps/media` = backend,
  `apps/web` = frontend). If you have filesystem/terminal access, the
  backend test suite is `cd apps/media/app && uv run pytest -o addopts=""
  -q` (180 tests as of this doc) and the frontend is `cd apps/web && npx
  tsc --noEmit && npx eslint . && npx vitest run && npx next build`.
- **Resetting the preview** (only if you need a genuine first-run state,
  and only with Mahdi's OK — this deletes the admin account and all
  resources in the preview): `docker compose -f compose.media-preview.yml
  down`, then `sudo rm -rf volumes/data volumes/storage` at the repo
  root, then `docker compose -f compose.media-preview.yml up -d --build`.
  Do **not** touch the *other* `umedia` compose project
  (`~/Projects/ai/umedia`, serving `drive.uln.me`) — different checkout,
  different deployment, explicitly not this project.

## Known gaps — do not report these as new bugs

Everything below is already tracked in `docs/09-tasks.md`'s Backlog.
Mention them only if you find something *additionally* broken about them,
not just that they're incomplete:

- Google Drive OAuth and Telegram session connect flows: the
  `connect_flow` field exists (`token`/`oauth`/`session`) but the actual
  `oauth/start`, `oauth/callback`, `session/start`, `session/verify`
  routes are **not built yet**. Both providers can currently only be
  connected by manually pasting an already-obtained token/session string
  into the generic form.
- `rclone`'s `s3` remote type: write path (`rcat`) unverified against
  real AWS/MinIO (blocked by a moto/AWS-SDK-v2 incompatibility in this
  environment, not our code — `list`/`stat`/`mkdir`/read all confirmed
  working). `onedrive`/`dropbox`/`webdav`/`nextcloud`/`ftp`/`sftp` remote
  types are mechanically supported but not individually tested.
- `telegram` plugin: never verified against a real bot/channel, only a
  hand-written fake `TelegramClient`. If you have real Telegram
  credentials and want to try it, that's genuinely useful new signal —
  otherwise don't file "telegram doesn't work" without a real attempt.
- No full-text search, no WebDAV mount server, no viewer plugins, no
  dual-pane GUI, no HATEOAS links. All explicitly backlog, not oversights.
- `usso` (auth library) is a vendored wheel, not a published PyPI
  package — irrelevant to black-box testing, mentioned only so a
  dependency-audit tool doesn't flag it as broken.

## How to report back

For each task below: **PASS**, **FAIL** (with exact repro steps, the
exact error text/screenshot, and which browser/viewport if relevant), or
**BLOCKED** (couldn't test it — say why). Don't summarize as "mostly
works" — list every FAIL individually, even minor ones (a misaligned RTL
button is worth one line, not silence). If you fixed something yourself,
say what you changed and point to the diff/commit rather than just
"fixed" — this session will review it, not blindly trust it.

---

## Checklist

### Auth
- [ ] Fresh install shows the **setup** screen ("Secure your drive"), not
      login — requires either a reset (above) or a not-yet-configured
      deployment.
- [ ] Setup with a <12-char password is rejected client-side.
- [ ] After setup, session persists across a page reload (no re-login).
- [ ] Logout actually clears the session — `/files` bounces to `/login`
      afterward, and reloading `/files` directly (typed URL) also bounces.
- [ ] Wrong password on login shows a real, readable error message — not
      `[object Object]` or blank (regression check, this was a real bug).
- [ ] Change password (Settings): old sessions elsewhere are invalidated
      (or at least this session's cookies are cleared and re-login works
      with the new password).

### Onboarding
- [ ] A fresh admin with **zero** storage connections is redirected to
      `/onboarding` after login/setup, not shown an empty `/files`.
- [ ] Connecting the first storage from `/onboarding` redirects to
      `/files` afterward, now populated (empty-but-browsable, not stuck).
- [ ] Deleting your *only* connection (from Settings) and then navigating
      to `/files` sends you back to `/onboarding` — this is a repeatable
      gate, not a one-time post-setup redirect.

### Storage (Settings page)
- [ ] Add a `local` connection with a valid `root_path` — succeeds.
- [ ] Add one with a `root_path` outside the allowed root — rejected with
      a specific, readable reason (not a generic "could not connect").
- [ ] Add an S3-compatible connection if you have real credentials for
      one (MinIO, Backblaze, AWS, etc.) — otherwise skip, don't fabricate.
- [ ] Remove a connection — disappears from the list; resources that were
      under it should no longer be reachable via `/files` for that
      connection (they don't get deleted, just orphaned from the UI same
      as before — confirm this matches your expectation, flag if not).
- [ ] `GET /api/v1/provider-types` (via browser devtools/network tab or
      `/api/v1/docs`) includes a `connect_flow` field per provider type.

### Files — browsing & organization
- [ ] Root listing shows only resources for the currently selected
      connection (if you have more than one connection, switching the
      selector changes the list).
- [ ] Create a folder, navigate into it (breadcrumb updates), navigate
      back out via a breadcrumb click.
- [ ] Create a **nested** folder (folder inside a folder) and upload a
      file into the innermost one — this exercises a real bug fixed
      earlier this session (wrong parent id sent to the plugin); confirm
      it actually lands in the right place, not silently misplaced.
- [ ] Rename a file and a folder.
- [ ] Not a bug to report: **there is no move-between-folders UI**
      (confirmed — no drag-drop, no "move to" action in the row menu).
      The backend supports it (`PUT /resources/{id}` accepts a new
      `parent_id`); only the frontend affordance is missing. Known gap.

### Upload (tus, resumable)
- [ ] Upload a small file — appears in the list as `completed` shortly
      after.
- [ ] Upload a **large** file (several hundred MB+, ideally over a min or
      two of transfer time) — a real progress bar moves, the page stays
      responsive (scroll, click elsewhere) while it uploads.
- [ ] **Pause** an in-progress upload, then **Resume** it — completes
      correctly, file integrity intact (compare size/or open it after).
- [ ] **Cancel** an in-progress upload — row disappears, and the partial
      upload doesn't show up later as a resource.
- [ ] Upload the exact same file twice (identical bytes) into the same
      folder — second one dedupes (returns the existing resource, doesn't
      create a duplicate row) — check via the file list, still just one
      entry.
- [ ] Genuinely drop the network mid-upload (disable wifi, or kill the
      tab's network in devtools) on a large file, then restore
      connectivity and reload the page — does it offer to resume, or does
      tus-js-client's `findPreviousUploads` recovery not currently wire
      up to anything in the UI? (This is a real open question, not a
      known-answer check — report what actually happens either way.)

### Download / streaming
- [ ] Click a video file — it plays, and **seeking** (jumping to a later
      point) works smoothly, not just from the start (`Range` support).
- [ ] Direct-download a large file — starts immediately, doesn't hang
      waiting to buffer the whole thing first.

### Sharing
- [ ] Toggle a file's share link on (row menu → Share link) — a
      `/f/{uid}` URL gets copied to clipboard.
- [ ] Open that link in an incognito/private window (no login) — the file
      loads/downloads without authentication.
- [ ] Toggle it back off — the same incognito window now gets a 404 for
      the same URL, not the file.
- [ ] `/f/{uid}?details=true` in the incognito window (once shared)
      returns JSON metadata, not the raw file.

### Delete / restore (Trash)
Soft-delete from Files; restore and permanent delete live on `/trash`.
Header buttons: **Restore all**, **Delete all**. Per-row: **Restore**,
**Delete forever**. Retention banner: 30 days.

- [ ] Soft-delete a file from Files — it leaves the Files list and
      appears on `/trash` with name, deleted date, and days left.
- [ ] Soft-delete a **folder** that has files inside — the folder is the
      only trash row (children stay hidden behind it, not listed as
      their own trash roots). Those children are gone from Files too.
- [ ] `/trash` shows the 30-day warning. **Restore all** and **Delete
      all** are enabled when the trash has items, and both disabled when
      it is empty ("The trash is empty.").
- [ ] Per-row **Restore** on a file — it leaves Trash and shows up again
      in Files (same folder it came from).
- [ ] Per-row **Restore** on a deleted folder — the folder *and* its
      children come back together.
- [ ] Per-row **Delete forever** opens a confirm dialog. **Cancel**
      leaves the item in Trash. **Yes, delete forever** removes it from
      Trash and it does **not** come back in Files.
- [ ] **Restore all** restores every trash root, including items that
      were still behind **Load more** (not only the first page). Files
      listing has them back; Trash is empty afterward.
- [ ] **Delete all** opens "Delete everything in the trash forever?".
      **Cancel** changes nothing. **Yes, delete all forever** empties
      Trash; those items do not reappear in Files.
- [ ] After Trash is empty, **Restore all** and **Delete all** are
      disabled.

### Internationalization & theming
- [ ] Toggle فارسی — every screen you've already tested (login, files,
      settings, onboarding) re-renders in Persian, and the layout
      actually mirrors (sidebar, dropdown menus, breadcrumbs) — not just
      the text flipping while the layout stays LTR.
- [ ] Light mode page chrome is milky `#f8f8f8`, not pure white and not
      the earlier beige `#F6F4F0`. Cards/tables may stay white on top of
      that.
- [ ] Toggle dark mode — check contrast/legibility on the busiest screens
      (Files table, Settings, Trash, the upload progress panel), not just
      that it "turns dark". Background is `#141414`.
- [ ] Both settings (locale, theme) persist across a page reload.

### Disabled connections (recent addition, backend-only so far)
There is **no UI toggle for this yet** (confirmed: `settings/page.tsx` has
no `enabled` control) — the `PATCH /api/v1/providers/{uid}` endpoint and
the actual enforcement exist, but only reachable directly via the API for
now. Test it that way:
- [ ] `PATCH /api/v1/providers/{uid}` with body `{"enabled": false}`
      (e.g. via `/api/v1/docs`'s "Try it out", logged in) — succeeds, and
      the connection still shows up in `GET /providers` with
      `"enabled": false`.
- [ ] Uploading to that connection afterward (from the normal UI) fails
      with a clear "disabled" error, not a generic one.
- [ ] `PATCH` it back to `{"enabled": true}` — uploads work again.
- [ ] Not a bug to report: the *lack* of a UI toggle itself. That's a
      known, tracked gap (`docs/09-tasks.md`), not something this pass
      claims to have finished.

---

## Things worth trying that aren't a checklist item

Anything that seems like a natural next thing a real user would do that
isn't listed above. This project has repeatedly found real bugs exactly
this way (a live click-through catching what `pytest`/`tsc`/`eslint`
structurally can't) — don't feel confined to the checklist if something
looks off while you're in there.

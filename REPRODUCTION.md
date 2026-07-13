# Demo meeting form — reproduction spec

This document lets a coding agent rebuild this product from scratch on a **different
stack and platform**. It deliberately describes *behavior*, not implementation: the
platform this repo was packaged for has changed significantly, so nothing here names
config files, container/image formats, or deployment mechanics. Where the original
requirements cite API paths (e.g. `PUT /internal/meeting/{id}/my-entry`), treat them
as illustrative of the API *shape* — reproduce the behavior, not the exact routes.

The normative core is the requirements backbone in §4, carried over from this repo's
`demo/testable-requirements.toml` (a format that worked well here — see §7).

---

## 1. Product overview

One automation, three deployable units sharing one backend:

- **Backend** — an authenticated JSON API (the "internal" surface) plus a small
  unauthenticated "public" surface, backed by a relational database and an
  object store for images. Runs a daily scheduler.
- **Internal frontend** — the **"Demo meeting form"**: the main product. A
  single-page app behind organization SSO (OIDC). Employees record what they
  will demo at the weekly Demo meeting; admins manage attendance, schedule,
  people, and history.
- **External frontend** — a small **public page**: a read-only image gallery
  (title, uploader, date per image), an empty-state message when there are no
  images, and a "Powered by BitSwan" footer linking to bitswan.ai. No login.

Both frontends share the same polish conventions (§6): English/Czech language
switcher and a light/dark theme toggle.

### Identity and roles

- Users authenticate via the organization's OIDC provider; the backend validates
  bearer tokens against the provider's published keys and reads email, display
  name, and group claims.
- Two roles: **user** (any member of the allowed group) and **admin** (member of
  an admin subgroup at the identity provider). Role enforcement must exist at
  **both** layers: the UI hides admin controls from non-admins, *and* the API
  rejects non-admin requests with 403 — the API is the source of truth.
- The public surface requires no authentication and exposes only read-only data
  (gallery listing/images, the current schedule, minimal config).

## 2. Domain model (concepts, not schema)

- **Meeting schedule** — a single meeting type, "Demo", with one global
  schedule: a weekday + start time. Default **Monday 15:00**. Admin-editable;
  persists across restarts.
- **Meeting instance** — one concrete Demo meeting on a specific date.
  Auto-created at **00:00 local time** on the configured weekday (§5 has the
  pitfalls). Has attendees and demos; past instances form the History.
- **Person** — anyone known to the system. Two flavors:
  - *Resolved*: has a unique email; created automatically the first time
    someone signs in via OIDC ("the roster auto-grows"), or promoted from a
    placeholder.
  - *Placeholder*: display name only, no email — created when someone types a
    name that isn't in the roster yet (e.g. naming a project leader who has
    never logged in). When a matching person later logs in, the oldest
    unpaired placeholder with that display name is auto-paired to the real
    account (best-effort; display names are intentionally non-unique).
- **Project** — a global catalog entry: name (unique) + leader (a Person
  reference). Not scoped to any meeting; usable in any current or future
  meeting. Anyone signed in can create, rename, or delete projects.
- **Demo / project entry** — "user U will demo something for project P at
  meeting M", with a free-text description and a server-persisted order index
  (reading order for the meeting host). One row per (user, project) pair per
  meeting. User-facing terminology is **"demo"**, not "note" or "entry".
- **Attendance** — per user per meeting, boolean, **forward-looking** ("will be
  there", not "was there"). Admin-only to set; users see their own status
  read-only.
- **Subscription ("my projects")** — a personal project list per user.
  Auto-grows: writing a demo for a project silently subscribes the author (or
  the target user, when an admin writes it). Also explicitly manageable
  (subscribe/unsubscribe, idempotent subscribe).
- **Gallery image** — stored in object storage; metadata (title, uploader,
  content type, size, created-at) in the database. Uploaded/deleted from the
  internal surface, listed/served read-only on the public surface.

## 3. Internal app structure

Navigation tabs, all visible to signed-in users:

1. **Meeting form** (default) — for the *current* meeting: an **Attendance
   section** on top (read-only status for users; full roster with editable
   checkboxes for admins) and a **Demo list** below — a flat ordered list of
   every demo registered for the meeting, one row per (user, project), showing
   project name, presenter display name, and description. Users add ("+ Add
   demo"), edit, and delete their own demos inline with autosave; admins edit
   anyone's and can drag rows to reorder (order persists server-side, visible
   to everyone). When adding a demo, the project is picked via search over the
   global catalog or created on the spot; a new project's leader is chosen via
   a combobox over the roster that also accepts a free-typed name (which
   creates a placeholder Person).
2. **History** — past Demo meetings, most-recent-first, labeled by date.
   Opening one shows its attendees and demos: read-only for users, editable
   (and persisted) for admins.
3. **People** (admin) — roster management: list people, add placeholders,
   rename.
4. **Settings** (admin) — change the demo weekday and start time; manually
   create/recreate today's meeting instance (useful for testing and demos).

The app title/header is **"Demo meeting form"**.

## 4. Requirements backbone

Carried from `demo/testable-requirements.toml`. `REQ-*` are root product
requirements; `AI-*` are derived acceptance criteria (each `parent`ed to the
requirement it verifies). Statuses reflect where this repo stopped: `pass` =
implemented and verified; `proposed` = acceptance criterion written, never
verified; `pending` = the newest product direction, implemented in code but
never verified. **A reproducing agent should treat `pending`/`proposed` items
as first-class requirements** — they describe the intended final state.

### Naming & schedule

| ID | Status | Requirement |
|---|---|---|
| REQ-001 | pass | The automation is named 'Demo meeting form'. |
| AI-009 | pass | The internal frontend displays 'Demo meeting form' as the visible application title/header. |
| REQ-002 | pass | There is a single meeting type, 'Demo', with a configurable schedule. Default is Monday 15:00. An admin can change the weekday and start time. |
| AI-010 | pass | A freshly-deployed automation has its Demo meeting schedule set to Monday 15:00 by default. |
| AI-011 | pass | An admin can change the weekday of future demos via the admin settings UI, and the change persists across reloads and backend restarts. |
| AI-012 | pass | An admin can change the start time of future demos via the admin settings UI, and the change persists across reloads and backend restarts. |
| AI-013 | pass | A non-admin user is rejected (UI hidden and API returns 403) when attempting to change the schedule. |

### Auto-creation of meeting instances

| ID | Status | Requirement |
|---|---|---|
| REQ-003 | pass | On the demo day a new Demo meeting instance is automatically created at 00:00 local time (midnight at the start of that day). |
| AI-014 | pass | At 00:00 local time on the configured demo weekday, a new Demo meeting instance is created with that date as its meeting date. |
| AI-015 | pass | The auto-create job is idempotent: running it twice on the same demo day does not produce duplicate meeting instances. |
| AI-016 | pass | If the admin changes the demo weekday, the next auto-created instance occurs at 00:00 on the new weekday (not the old one). |

### The meeting form

| ID | Status | Requirement |
|---|---|---|
| REQ-004 | pass | The internal frontend uses OIDC to identify the signed-in user by email and presents a form for the current Demo meeting where they record one or more projects they worked on with a free-text description each. Attendance is admin-set and shown to the user read-only. |
| AI-017 | pass | When a signed-in user opens the form, their email comes from the OIDC session and is not editable as free text. |
| AI-019 | pass | The form lets the user select one or more projects from the current meeting's project list. |
| AI-020 | pass | For each selected project the user writes a free-text description; descriptions are stored per project per user. |
| AI-021 | pass | Submitting persists the entry; reopening the form for the same meeting shows the previously submitted values. |
| AI-045 | proposed | A non-admin user opening the form sees their attendance status as a read-only label (no checkbox), sourced from the roster. |

### Roster

| ID | Status | Requirement |
|---|---|---|
| REQ-005 | pass | The attendee roster auto-grows: any OIDC user who has ever signed in appears in the attendee list for current and future meetings. |
| AI-022 | pass | A first-time OIDC login adds the user to the roster and the attendee list of the current and all future Demo meetings. |
| AI-023 | pass | A user appears at most once in the roster regardless of login count. |
| AI-024 | pass | A user whose email is referenced elsewhere but who has never authenticated does NOT appear in the roster automatically. |

### Project catalog

| ID | Status | Requirement |
|---|---|---|
| REQ-006 | pass | Any signed-in user can add a new project (with a leader name) to the global catalog; it becomes available for entries on any current or future meeting. |
| AI-025 | pass | Any signed-in user can submit a new project (name + leader) to the global catalog. |
| AI-026 | pass | A project added by one user is immediately visible to all others. |
| AI-027 | pass | Projects live in a single global catalog, not scoped to the meeting they were created from; referenceable from any meeting. |
| REQ-036 | pass | Users have a personal 'my projects' subscription list and a search bar to find any project. Subscriptions auto-grow when a user first writes a note for a project. Anyone signed in can rename or delete any project. |
| REQ-037 | pass | The API returns the signed-in user's subscribed projects with id/name/leader. |
| REQ-038 | pass | Explicit subscribe/unsubscribe endpoints exist; subscribe is idempotent. |
| REQ-039 | pass | Writing a note for a project the target user isn't subscribed to silently auto-subscribes them (also when an admin writes on their behalf). |
| REQ-040 | pass | Project search matches name or leader substring, case-insensitive, ordered by name. |
| REQ-041 | pass | Any signed-in user can rename a project or change its leader; a duplicate name yields 409/conflict. |
| REQ-042 | pass | Any signed-in user can delete a project; deletion cascades to all its demo entries and subscriptions. |

### Permissions

| ID | Status | Requirement |
|---|---|---|
| REQ-007 | pass | Attendance is admin-only. Users create/edit their own demo entries; an admin can edit anyone's entries and change schedule/settings. |
| AI-018 | pass | A non-admin submitting an attendance value through the self-service entry endpoint is rejected with 403; the form exposes no attendance control to non-admins. |
| AI-028 | pass | A non-admin is rejected (403 + hidden UI) when editing another user's attendance or entries, or their own attendance. |
| AI-029 | pass | An admin can edit any user's attendance for the current meeting; the change persists. |
| AI-030 | pass | An admin can edit any user's demo entries (description and project association); changes persist. |
| AI-031 | pass | Schedule and other admin settings endpoints require the admin role; non-admins get 403. |
| AI-043 | proposed | A non-admin can still update their own entry with only demo entries (no attendance key) and it succeeds; attendance stays at whatever the admin last set (default false). |
| AI-044 | proposed | After an admin sets a user to attending=true, that user's subsequent self-service entry update (without an attendance key) preserves attending=true. |

### History

| ID | Status | Requirement |
|---|---|---|
| REQ-008 | pass | A separate 'History' tab lists past Demo meetings; opening one shows attendees and per-project notes read-only (layout may differ from the live form). |
| AI-032 | pass | The 'History' tab is visible to all signed-in users. |
| AI-033 | pass | History lists past meetings most-recent-first, meeting date as primary label. |
| AI-034 | pass | Selecting a past meeting shows its attendees and per-project notes. |
| AI-035 | pass | Historical view is read-only for users; admins can edit attendees and notes from it, and edits persist. |

### Demo list (newest direction — implemented, never verified)

| ID | Status | Requirement |
|---|---|---|
| REQ-046 | pending | The main meeting page is an Attendance section (top) and a Demo list below: a flat ordered list of every demo registered for the current meeting (one row per user-project pair) showing project name, presenter display name, and description. Users edit/delete their own demos inline (autosaved); admins edit anyone's and drag rows to reorder. Order persists server-side and is visible to everyone. |
| REQ-047 | pending | User-facing terminology is 'demo', not 'note': description placeholder "Describe what you are going to demo" (Czech: "Popiš, co budeš na demu předvádět"); list titled "Demo list" ("Seznam dem"); a "+ Add demo" control replaces the prior project search/create UI for the current user. Internal storage naming may stay as-is. |

### Public surface (not covered by the original requirements file)

Reconstructed from the code — treat as `pass`-equivalent:

- The public page shows a read-only gallery of uploaded images with title,
  uploader, and upload date; an empty-state message when no images exist; a
  fetch error is shown as a message, not a blank page.
- Gallery images are served through the backend from object storage (no
  direct object-store URLs leak to the client).
- Upload and delete are internal-only operations (signed-in users).
- The public API also exposes the current Demo schedule and minimal
  client configuration, unauthenticated.
- Footer: "Powered by BitSwan" linking to https://bitswan.ai.

## 5. Design decisions & pitfalls (paid for in this repo's history)

1. **Attendance is forward-looking.** It means "will be there", not "was
   there". This inverted an earlier design; keep the wording and semantics
   forward-looking from the start.
2. **Attendance became admin-only *after* users could set it.** The migration
   trap: the self-service "save my entry" call must accept payloads *without*
   an attendance field and must **not** reset admin-set attendance to a
   default when it does (AI-043/044 exist precisely because this regressed).
3. **Distinguish "fetch failed" from "no meeting today".** An API error and an
   empty result must render differently — a network failure once looked like
   "no meeting scheduled", which is a confusing lie.
4. **Cron-style schedulers don't backfill missed firings.** If the backend
   restarts after midnight on a demo day, the daily-at-00:00 job never fires
   for that day. Run a **startup catch-up** that ensures today's instance
   exists, and make creation **idempotent** (safe to call any number of times
   per day). Log both the tick and the backfill visibly.
5. **Placeholder people.** Admins name project leaders who haven't logged in
   yet. Model this as a Person without an email; on that person's first real
   login, auto-pair the oldest unpaired placeholder with the matching display
   name. Accept that display names are non-unique and pairing is best-effort.
6. **Leader picker = combobox over the roster.** Free-text leader names
   fragment the data; source the picker from known people, but still allow
   typing a new name (which creates a placeholder).
7. **Dev-only roster seeding.** With SSO, a dev environment has no users, so
   admin screens have nobody to act on. Seed a handful of clearly-fake people
   (e.g. `*@dummy.dev`) **strictly gated to dev environments** and idempotent
   across restarts, so dummies never leak to staging/production.
8. **Enforce permissions at the API, mirror in the UI.** Every admin-only
   ability needs both the hidden control and the 403; tests should check the
   API side.
9. **Admin "recreate today's meeting" button.** Manual create/recreate of the
   current instance turned out essential for testing and live demos.
10. **Deleting a project cascades** to its demo entries and subscriptions —
    decide this explicitly rather than leaving orphans or FK errors.

## 6. Polish features (user-visible, easy to forget)

- **Bilingual UI**: English and Czech, full parity, runtime language switcher
  on both frontends. Keep all strings in locale files from day one.
- **Theme**: light/dark toggle; initial value follows the system preference;
  choice persists locally.
- **Visual direction**: "calm minimal" — quiet typography, generous spacing,
  no dashboard chrome; the meeting form should read like a simple document,
  not an admin panel.
- **Autosave** on demo edits (no explicit save button on the demo list).
- Public page footer: "Powered by BitSwan".

## 7. Verification protocol (recommended working method)

This repo kept itself honest with a `testable-requirements.toml` file — a flat
list of `[[requirement]]` entries: `id`, `parent`, `description`, `status`.
The loop that worked:

1. A human writes root requirements (`REQ-*`) in plain language.
2. The agent derives concrete, individually testable acceptance criteria
   (`AI-*`), each `parent`ed to its root requirement, status `proposed`.
3. Implement; write an automated backend test per criterion (this repo ended
   with ~18 test files, roughly one per feature area).
4. Only when the test passes and the behavior is demonstrated does status
   flip to `pass`. `pending` marks agreed-but-unverified product direction.

Reproduce the *method*, not necessarily the file format: the value is having
one canonical, statused requirement list that both humans and agents edit.

## 8. Demo walkthrough (what "done" looks like on stage)

1. **User signs in** via SSO → lands on the Demo meeting form for the current
   meeting; header reads "Demo meeting form"; their attendance shows as a
   read-only status.
2. **User adds a demo**: "+ Add demo" → searches the project catalog, picks a
   project (or creates one, choosing a leader from the roster combobox) →
   types "what I'm going to demo" → it autosaves and appears in the shared
   demo list with their display name.
3. **Second user signs in for the first time** → appears in the attendance
   roster automatically; the first user's project is already available to
   them.
4. **Admin marks attendance** for both users; changes are visible to everyone.
5. **Admin drags demo rows** to set the reading order; a non-admin reloads and
   sees the new order but cannot drag.
6. **Admin opens Settings**, moves the demo to another weekday, and uses
   "recreate today's meeting" to show instance creation on demand.
7. **History tab**: open last week's meeting → attendees and demos, read-only
   for users; the admin edits a note and it persists.
8. **Public page** (no login): the image gallery renders with titles and
   uploaders; switch language to Czech and toggle dark mode.

## 9. Deliberately unspecified

Choose freely: programming languages, web framework, SPA framework, database
engine, object-store client, scheduler library, auth library, packaging, and
deployment. The original choices are visible in this repo's history but are
explicitly **not** part of this spec — the platform they targeted has moved on.

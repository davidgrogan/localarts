# Local Music POC (Northampton, MA)

A small Flask app: a calendar of local shows, a venues list with a
pluggable scraper framework, and an artists roster for highlighting
local musicians. Built as a first proof of concept before deploying to
DigitalOcean.

## What's here

- **Calendar** (`/`) -- upcoming approved shows, filterable by venue, a
  "show only events with local artists" toggle, and a row of category
  toggle pills (Music, Comedy, Theater, Spoken Word, Lectures, Art
  Exhibits, Film, Dance, ...) that default to just "Music" selected but
  let a visitor show as many at once as they want (see "Public category
  filter and Manage Categories" below); also has a "Local Artists Playing
  This Week!" gallery -- one card per local artist actually playing a show
  in the next 7 days in one of the currently-selected categories (see
  "Local artist Genre/Category Tags" below). Public -- this and the
  artist roster below are the only things anonymous visitors see.
- **Artists** (`/artists`) -- roster of local artists, alphabetical by name,
  filterable by Genre Tag, with a random-local-artist spotlight at the top,
  a live name-search box, and an A-Z jump bar (see "Local Artists index
  layout" below). Linked to the shows they're playing. Public. The add/edit
  forms (admin-only) have an "Import from Bandcamp" bookmarklet + paste box
  that prefills name/location/bio/photo/embed/tags for review before saving
  -- see "Import from Bandcamp" below for why it's a bookmarklet rather than
  a plain "paste a URL" fetch.
- **Venues** (`/venues`) -- add a venue, tell it how to pull in events (manual,
  Squarespace JSON trick, iCal feed, generic HTML selectors, or headless-browser
  selectors), preview a scrape before importing anything, and see a log of
  recent scrape runs. Public to browse; adding/editing/scraping is admin-only.
- **Submit a Show** (`/gigs/submit`) -- public form for artists/promoters to
  propose their own show (including DIY one-off shows with no formal venue),
  reviewed by an admin before it hits the calendar. See "Submit your show"
  below.
- **Event Details** (`/show/<id>`) -- a single show's own page: full-size
  image and full description, neither of which fit on the calendar's
  card-per-show list (there, the image is a small cropped thumbnail and the
  description only shows as a hover tooltip). Linked from every calendar
  card's thumbnail and a new "Event Details" link. Public for approved
  shows; 404s for anyone who isn't an admin if the show isn't approved yet.
  Has an "Add to calendar" button (`/show/<id>.ics`) that downloads the
  show as a single-event .ics file for Google/Apple/Outlook -- see "Add to
  calendar" below.
- **About** (`/about`) -- the site's admin-editable "About this site" copy,
  linked from the main nav. Used to show inline (collapsed by default) at
  the top of the calendar page; now lives on its own page instead, so the
  calendar stays focused on the actual show listings. See "About page and
  the fixed venue-caution line" below.
- **Scan** (`/venues/scan`) -- rescrapes every active, non-manual venue on
  demand (the same thing `scrape_all.py`/the `scrape.timer` schedule does).
  The page's JS calls a one-venue-at-a-time JSON endpoint
  (`POST /venues/<id>/scan-one`) in sequence rather than looping through all
  of them in one request, so there's a real progress bar and a live log line
  per venue instead of one long silent wait -- one venue failing to fetch
  doesn't stop the rest. If JS is off, a fallback form on the same page
  (`POST /venues/scan/run-all`) runs everything in a single request with no
  progress bar. Since there's no background job queue in this POC, either
  path still ties up one request for the whole scan; if scanning ever gets
  slow enough to matter (several headless-browser venues back to back),
  scheduling more frequent `scrape.timer` runs is the fallback. Admin-only.
- **Add show** (`/events/new`) -- manual entry, with a quick-add box for a new artist and
  either a flyer image upload or a pasted image URL (see "Uploading a flyer on the Add/Edit
  Show form" below). Admin-only.
- **Review** (`/events/review`) -- one queue for everything a scrape needs a human
  to look at, in four sections: **New** (`is_approved=False`, never seen before --
  approve or discard), **Changed** (already approved and still live, but the venue's
  site reported a different time/title since -- confirm it's real or unpublish),
  **Possibly cancelled** (was approved, hasn't shown up in the venue's listing for two
  scrapes in a row, so it's already been auto-hidden -- restore it if it's actually
  still happening, or confirm/discard), and **Rejected** (discarded from New --
  restore it or delete it for good). Discarding from New doesn't delete the row; it
  marks `is_rejected` instead, so a future scrape recognizes the same event (matched
  by venue + external id) and leaves it alone rather than re-adding it as if it were
  brand new. See "Keeping scraped data honest" below for how that bookkeeping works.
  Admin-only.
- **Contact** (`/contact`) -- public form for visitors to request an artist/show/venue
  be added, or flag something wrong. Emails `CONTACT_EMAIL` via Resend's HTTPS API (see below).

## Contact form email

`app/routes/contact.py` sends via [Resend](https://resend.com), a
transactional email API over HTTPS -- see `.env.example` for the exact
steps to get an API key (sign up, then generate one at
https://resend.com/api-keys). Set `RESEND_API_KEY`; `CONTACT_EMAIL`
defaults to davidbgrogan@gmail.com. Without `RESEND_API_KEY` set, the
form still renders but shows an error instead of quietly failing to
send. (This used to be direct Gmail SMTP -- see "Hardened against a
hung mail server" below for why that was abandoned.)

## Admin login

Everything above marked admin-only (venue/event management, the review
queue, adding/editing/deleting artists) sits behind a single-admin login
(`app/auth.py`) -- a username/password from env vars plus Flask's signed
session cookie, no separate user system needed since there's exactly one
admin. Log in at `/login` (linked from the footer); the nav and every
edit/delete/scrape control only render once you're logged in.

Set real credentials via `ADMIN_USERNAME` / `ADMIN_PASSWORD_HASH` (see
`.env.example` for how to generate the hash) -- with nothing set, it
falls back to `admin`/`admin`, which is fine for poking around on your
own laptop and not fine for anything actually deployed.

## Data model

- `Venue` -- name, address, website, the specific events URL to pull from, a
  `source_type` (see below), a free-form `scrape_config` JSON field for
  per-venue tuning, a `default_event_type` tag (see "Event type tags" below),
  and an `image_url` -- a photo of the venue itself, settable via pasted URL
  or file upload on the Add/Edit Venue form, shown on the venue's detail page
  and used as a fallback image for any of its events with no image of their
  own (see "Venue photos, and the event-image fallback" below).
- `Artist` -- name, hometown, bio, an `image_url` (a photo/promo image --
  just a link to an image hosted elsewhere, same as `Event.image_url`; no
  file upload in this POC), website/Bandcamp/YouTube link, an embed
  code, an `is_local` flag (the whole point of the site is surfacing local
  acts, so this drives every "local spotlight" view), plus `genre_tags` and
  `category_tags` (both many-to-many -- see "Local artist Genre/Category
  Tags" below). Also still has the original `genre` column, a single
  free-text string from before Genre Tags existed -- kept only so any
  already-seeded artist's old value isn't silently dropped; no longer
  editable via the artist form.
- `Event` -- a single show: title, start/end time, venue, artists (many-to-many),
  event type tags (many-to-many, see below), `source` (`manual` vs `scraped`),
  `external_id` (dedupe key on re-scrape), and `is_approved` (the review gate
  described above).
- `EventType` -- a reusable category tag (e.g. "Music", "Exhibition", "Lecture").
  Distinct from `Event.genre` (a finer-grained music genre like "Jazz"/"Folk"
  pulled straight from a venue's own feed, one value per event): this is the
  broader, curated, multi-valued category an admin picks -- the thing you'd
  filter a mixed calendar like Smith College's down to "just the concerts" by.
  Also doubles as an artist's Category Tags -- see below. Carries an
  `is_public_category` boolean, off by default, that decides whether a tag
  shows up as a selectable pill on the public calendar's filter bar at all --
  see "Public category filter and Manage Categories" below.
- `GenreTag` -- a reusable music-genre tag for artists (e.g. "Electronica",
  "Americana"). See "Local artist Genre/Category Tags" below.
- `ScrapeRun` -- a log row per scrape attempt: status, counts, and a truncated
  raw-response sample, so a bad scrape is debuggable without re-hitting the venue site.

## Event type tags

An event can carry more than one tag (a benefit show might be both "Music"
and "Benefit"), and tags are created on the fly -- there's no dedicated
"manage tags" screen. Both the event form (`/events/new`, `/events/<id>/edit`)
and the venue form (`/venues/new`, `/venues/<id>/edit`) let you either check
existing tags or type a new one into a "quick add" field (comma-separated on
the event form, for adding several new tags at once); `get_or_create_event_type()`
in `app/utils.py` dedupes by name case-insensitively so typing "music" after
"Music" already exists never creates a duplicate.

A venue can have one **default tag**, applied automatically to a brand-new
event at that venue -- both a manual add that doesn't pick a tag itself, and
every scraped import (see `run_scrape()` in `app/scrapers/base.py`) -- always
overridable per event, and never re-applied once an event already has tags
(so a later re-scrape can't silently strip an admin's own tagging choice).
Iron Horse, The Parlor Room, Academy of Music, Haze, Luthier's Co-Op, and
The Heavy Culture Cooperative are seeded with "Music" as their default,
since every show at those is one (Amherst Cinema, the first non-music
venue, is seeded the same way but with "Film" instead -- every listing
there is a screening); Smith College and Quonk
intentionally have no default, since their calendars mix things like
exhibitions, lectures, comedy, dance parties, and tabletop game nights with
actual performances, and each scraped item needs its own call. Whether a
tag shows up as a visitor-facing filter at all is a separate decision --
see the next section.

## Public category filter and Manage Categories

Not every `EventType` tag is meant for visitors -- a venue might get
tagged internally with something one-off or messy while it's being sorted
out in the Review queue, and that shouldn't clutter the public filter bar.
Each `EventType` has an `is_public_category` boolean (default off) that
decides whether it appears as a toggle pill on the calendar's filter bar;
everything else stays an internal-only admin tag, usable on the event/venue
forms exactly as before, just invisible to visitors.

**Manage Categories** (`/events/categories`, admin-only, linked from the nav
next to Scan) is a small CRUD page for that flag: an "Add a category" box to
create a brand-new public category in one step, and a table of every
existing `EventType` showing how many future events it's tagged on, a
Public/Internal toggle button, and three more per-row actions: **Rename**
(changes the name and slug in place -- same id, same events, same
public/internal status; refuses if the new name collides with a
*different* existing category, pointing at Move instead rather than
silently merging the two), **Move events to** (a dropdown of every other
category -- moves every event off this one onto the picked category, then
deletes this one, since nothing's left tagged with it), and **Delete**
(works as soon as nothing *upcoming* is tagged with it -- a category
whose only events are already in the past doesn't block deletion, only a
future one does; refuses and points at Move otherwise, so a cleanup click
can't silently pull a still-relevant tag out from under a show that
hasn't happened yet). These three exist because the earlier one-off tag
collisions this feature
surfaced (see the near-miss discussion above) kept needing a code change
and an app restart to fix -- now David can do the same kind of cleanup
himself, on demand, from the page. `app/routes/events.py`'s
`rename_category()` / `move_category_events()` / `delete_category()` are
the manual, click-driven counterparts to `_migrate_renamed_categories()`'s
automatic startup migrations in `app/__init__.py` -- same two operations
(merge into an existing tag, or rename in place), just triggered by an
admin instead of baked into every install. The page as a whole exists
because David wanted to be able to add (and now clean up) curated public
categories over time -- past the initial set of Music, Comedy, Theater,
Spoken Word, Lectures, Art Exhibits, Film, and Dance -- without needing a
code change each time.

The calendar's filter bar renders one pill per public `EventType` -- but
only the ones that actually have something coming up. `_public_category_choices()`
in `app/routes/main.py` filters to categories carrying at least one
still-upcoming, *approved* event (`local_now()`, matching every other
"future" check in this app, and `is_approved=True`, matching exactly what
the calendar itself ever shows); a public category with nothing on the
books -- its last show already happened, or its only upcoming show is
still sitting in the Review queue -- used to still render as a checkable
pill that just handed a visitor an empty calendar for their trouble.
Each pill is a checkbox styled to look pressed/selected via CSS's
`:has()` selector rather than a native checkbox look. A fresh,
never-touched visit defaults to just "Music" selected -- not
"everything," since a mixed-use venue like Smith College or Quonk means
an unfiltered calendar would bury real shows under exhibitions and
lectures. Visitors can check as many pills as they want, including none,
or none at all if they explicitly submit the form that way; a hidden
`categories_submitted` field (the same trick already used for the "hide
recurring" view toggle) is how the backend tells "never touched, apply
the Music default" apart from "explicitly submitted with everything
unchecked, show nothing." Selections round-trip through every other link
on the page (view toggles, month prev/next, the "+N more" overflow link)
as repeated `category=<id>` query params, and an event tagged with more
than one selected category is never shown twice -- `_base_query()` in
`app/routes/main.py` matches with `Event.event_types.any(...)` (an EXISTS
subquery) rather than a join, the same pattern already used for the
local-artist toggle.

Checking a pill (or a combination of them) can easily turn up nothing in
the default "Next 7 Days" view even though real shows exist further out
-- e.g. the only upcoming Comedy night is three weeks away, not this
week. Rather than showing an empty week and expecting the visitor to
notice and click "List All" themselves, `calendar()` redirects straight
to the List All view when the category filter was actually submitted
this request (`categories_submitted` present) and the resulting week view
comes back empty. A plain, filter-untouched quiet week -- nothing on at
all for the default Music view -- still shows the normal empty state
rather than auto-jumping, since that's not the visitor having picked a
combination that turned up nothing; it's just a genuinely quiet week.

Because `is_public_category` defaults to `False` and `get_or_create_event_type()`
never changes an existing tag's flag (a deliberate rule -- re-seeding or
quick-adding a tag name that already exists should never silently flip an
admin's own Manage Categories choice), upgrading an install that predates
this feature would otherwise leave the public calendar showing zero events
-- no tag would be public yet. `_bootstrap_default_public_category()` in
`app/__init__.py` runs once at startup, checks whether *any* `EventType` is
currently public, and if none are, promotes whichever of the eight curated
category names already exist by that exact name (case-insensitive) to
public. It only ever fires while no category is public at all, so it won't
re-run and silently override a deliberate "turn every category off" choice
made later via Manage Categories. It also won't merge or guess at
near-miss existing tags on its own -- a pre-existing singular "Lecture"
tag, for instance, is left as its own separate, still-internal tag rather
than silently folded into the new plural "Lectures" category. Reconciling
a near-miss like that is an editorial call, made explicitly rather than
guessed at: `_CATEGORY_RENAMES` (right above `_migrate_renamed_categories()`
in `app/__init__.py`) is a short, hand-maintained list of `(old name, new
name)` pairs for exactly that, and each pair can mean one of two
different things depending on whether the new name is already in use:

- **Merge into an existing tag** -- "Art Exhibition" -> "Art Exhibits" is
  this case: a pre-existing internal tag, created by hand well before
  "Art Exhibits" existed as a curated category, that held 14 real events
  invisible on the public filter pill as a result. Every event on the old
  name moves onto the already-existing new tag, and the old tag is
  deleted once nothing carries it anymore. The new name has to already
  exist as its own `EventType` for this branch to run at all (checked by
  a real lookup, not `get_or_create_event_type()`) -- otherwise it skips
  the rename entirely rather than creating a stray, accidentally
  non-public row under that name.
- **Plain rename in place** -- "Celebration" -> "Misc." is this case:
  nothing named "Misc." existed yet, so there's nothing to merge into --
  the old tag's own row just gets a new name and slug, keeping its id,
  its events, and whatever `is_public_category` value it already had,
  completely untouched otherwise.

Both run on every app start, same as `_bootstrap_default_public_category()`,
and are a no-op the moment the old name doesn't exist. "Lecture" ->
"Lectures" is the other known near-miss, not yet added here since folding
it in is still David's call to make.

**A real deploy-ordering bug this surfaced:** both of these startup
migrations query the `EventType` table, and a plain `EventType.query...`
implicitly SELECTs every mapped column -- including `is_public_category`
-- not just the ones a filter mentions. That's harmless on SQLite, where
`_run_sqlite_column_migrations()` above always adds a newly-declared
column immediately, before either function runs. On Postgres it isn't:
`sync_schema.py` (the droplet's own column-migration step, since Postgres
has no automatic one) calls `create_app()` itself first, just to get at
`app`/`db` -- which means these two functions can run against a database
that's missing the very column they depend on, on a fresh deploy where
that column was just added to `models.py` but `sync_schema.py --apply`
hasn't actually run against this database yet. This crashed a real
`deploy_all.sh` run with `psycopg2.errors.UndefinedColumn: column
event_type.is_public_category does not exist`, thrown from inside
`sync_schema.py`'s own `create_app()` call -- before it ever reached its
own `ADD COLUMN` step. `_column_exists()` in `app/__init__.py` guards both
functions against exactly this: if the column they depend on doesn't
exist yet on this database, they just skip cleanly instead of crashing,
deferring the self-heal to the next app start (right after
`sync_schema.py --apply` adds the column in the same deploy run). Worth
remembering for any *future* startup migration added here that reads a
column from a model still mid-rollout on Postgres: guard it with
`_column_exists()` the same way, since `sync_schema.py` will always hit
`create_app()` before its own `ADD COLUMN` step runs.

**A second real deploy bug, right after fixing the one above:** once
`sync_schema.py` got past the `UndefinedColumn` crash and actually tried
to run its generated `ALTER TABLE event_type ADD COLUMN is_public_category
BOOLEAN NOT NULL;` against the droplet's Postgres database, it failed
again with `psycopg2.errors.NotNullViolation: column "is_public_category"
of relation "event_type" contains null values`. The column was declared
in `models.py` with `default=False` but no `server_default=` -- and
`default=` is a Python-side value SQLAlchemy applies through the ORM on
INSERT, invisible to raw DDL. `sync_schema.py`'s `find_missing_columns()`
generates its `ADD COLUMN` statement via SQLAlchemy's `CreateColumn`
compiler, which only ever emits a real `DEFAULT` clause from
`server_default=`. With no DEFAULT to fall back on, Postgres had no value
to backfill the `event_type` table's existing rows with, so the NOT NULL
constraint failed outright. The fix was adding `server_default=false()`
(from `sqlalchemy.false`) alongside the existing `default=False` on
`EventType.is_public_category` in `models.py` -- now `CreateColumn`
compiles to `... BOOLEAN DEFAULT false NOT NULL`, so every existing row
gets backfilled with `false` and the `ALTER TABLE` succeeds. Worth
remembering for any *future* `nullable=False` column added to an existing
table: it needs both `default=` (for the ORM) and `server_default=` (for
raw DDL like this) to survive a real Postgres deploy -- a nullable column
only needs `default=`, since existing rows can just take NULL. This is
also documented directly in `sync_schema.py`'s own docstring.

## Local artist Genre/Category Tags, filtering, and the featured-artist spotlight

An artist's **Genre Tags** (e.g. "Electronica", "New Wave", "Americana") and
**Category Tags** (e.g. "Music", "Comedy", "Art") both work like event type
tags above -- multi-valued, created on the fly via checkboxes plus a
comma-separated "quick add" text field on the artist form
(`/artists/new`, `/artists/<id>/edit`) -- but they're two deliberately
different tables under the hood:

- **Category Tags reuse the `EventType` table** shows are already tagged
  with, rather than a separate artist-only category list. That keeps
  "Comedy" meaning one single thing site-wide instead of two tag lists that
  could drift apart, and means an artist's categories and a show's
  categories are always directly comparable. `get_or_create_event_type()` in
  `app/utils.py` (already used by the event/venue forms) handles the
  case-insensitive dedupe for these too.
- **Genre Tags get their own new `GenreTag` table** (`get_or_create_genre_tag()`
  in `app/utils.py`), since genre is a finer-grained, mostly-music-specific
  axis -- the same Event.genre-vs-EventType distinction drawn above, just at
  the artist level.

The old single `Artist.genre` free-text column is left in place but no longer
written to by the form -- new/edited artists use `genre_tags` instead.

The Local Artists page (`/artists`) filters by Genre Tag only -- it used to
also have a Category Tag dropdown and an "only artists with upcoming shows"
toggle, both removed since they added more filter UI than the roster's size
actually justified. Always sorted alphabetically by name regardless of
whether a filter's active. See "Local Artists index layout" below for the
spotlight/grid/search/A-Z-jump built around that same alphabetical sort.

The calendar's "show only events with local artists" toggle
(`?only_local_artists=1`) works the same way one level up: `Event.artists.any(Artist.is_local.is_(True))`,
added to `_base_query()` in `app/routes/main.py` alongside the existing
venue/type filters -- distinct from the single-artist dropdown
(`?artist=<id>`, still wired up server-side but not currently shown on the
calendar UI).

The homepage used to spotlight one random `is_local` artist with an
upcoming show; `_local_artists_playing_this_week()` in `app/routes/main.py`
replaced that single-pick spotlight with a "Local Artists Playing This
Week!" gallery -- a card for every local artist actually playing an
approved show, tagged with one of the visitor's currently-selected public
categories (Music by default -- see "Public category filter and Manage
Categories" above), in the next 7 days (same window as the calendar's own
"week" view), since that's more useful to a visitor
deciding what to go see than one random pick. Renders nothing if no local
artist has a show landing in that window.

The gallery is wrapped in a native `<details>`/`<summary>` element rather
than a custom JS toggle -- clicking the header collapses/expands the
section with no JavaScript needed. It defaults open (the `open` attribute
in `calendar.html`), so nothing changes for a first-time visitor; it just
lets a returning visitor tuck it away. (The "About this site" intro used
to be a second collapsible section here too -- see "About page and the
fixed venue-caution line" below for where it moved. The Local Artists
index's own, separate random-artist spotlight -- see "Local Artists index
layout" below -- is unrelated to this gallery; it always picks one artist
regardless of whether they have a show this week.)

`seed.py` includes two artists exercising this (Comet & the Roadrunners --
Electronica/New Wave; Ruth & the Backroads -- Americana), each with a
placeholder Bandcamp-style `embed_code` and linked to an upcoming sample
show, so all of the above is visible before any real artist data is entered.
Comet & the Roadrunners also has a placeholder `image_url` set, and Ruth &
the Backroads deliberately doesn't, so both states -- an artist photo, and
the fallback to the site logo used everywhere an artist has none -- are
visible in the demo (Local Artists list/index spotlight, artist detail
page, and the calendar's "Local Artists Playing This Week!" gallery).

David reported these cards were "sometimes not long enough
height-wise." `.artist-week-card` in `style.css` originally used a
hard-coded `height: 480px` + `overflow: hidden`, specifically to keep
every card the same size despite content varying a lot (photo/no photo,
genre tags/no genre tags, embed/no embed) -- but a real artist's actual
embed code (a "large" Bandcamp album player, a YouTube embed, etc.) or
several wrapped genre tags can easily need more than 480px, and
`overflow: hidden` was silently cropping that content instead of ever
growing the card. Fixed by dropping the fixed height and overflow
clipping entirely -- uniform card height still happens automatically,
since `.artist-week-gallery` is a flex row and flex's default
`align-items: stretch` already stretches every card to match its
tallest sibling's *natural* content height; the fix just lets that
natural height grow instead of clipping to a fixed number. The "View
artist profile" button still stays pinned to the bottom of every card
via `margin-top: auto`, same as before.

These four placeholder shows ("Sample Show -- Iron Horse", "-- Parlor Room",
"-- Academy of Music", "-- Haze") get their dates refreshed on every
`seed.py` run so they don't quietly roll into the past over time (see the
comment above `_upsert_sample_show` in `seed.py`), but that used to mean
deleting one for real (e.g. once a venue has actual scraped/added shows and
the placeholder is just noise) never stuck -- the next `seed.py` run saw no
row with that title and recreated it, indistinguishable from a genuinely
fresh install. A marker file (`instance/.sample_shows_seeded`, next to the
sqlite db) now records "these have been placed at least once on this
install"; once it exists, a missing placeholder title means it was deleted
on purpose, and `seed.py` leaves it gone instead of bringing it back.

## Local Artists index layout (`/artists`)

Replaced the old category-dropdown + "only upcoming shows" toggle (both
removed -- see the "Local artist Genre/Category Tags" section above) with a
layout meant to hold up as the roster grows past a single scrollable list:

- **Spotlight**: a random `is_local` artist, re-picked on every page load
  (`list_artists()` in `app/routes/artists.py`) -- always drawn from the
  *full* local roster, not whatever the genre filter narrows the grid
  below down to, so applying a filter doesn't make the spotlight
  disappear or feel tied to it. Falls back to picking from literally any
  artist if an install somehow has zero local ones yet, rather than
  showing nothing. Unrelated to the calendar's "Local Artists Playing
  This Week!" gallery above -- this always shows one artist regardless of
  whether they have a show this week.
- **Photo grid, not a list**: each artist is a square photo tile (falls
  back to the site logo, same as everywhere else) with their name, genre
  tags, and next upcoming show (or "No upcoming shows") -- a `touring`
  badge still marks non-local artists, same as the old list view.
- **Live search**: a plain-JS, no-reload text input that filters the grid
  by name as you type (matches each tile's `data-name` attribute) --
  entirely client-side, doesn't touch the genre filter or reload the page.
- **A-Z jump bar**: every letter renders, even ones with no artist yet
  (as an unclickable placeholder) so the bar's width doesn't shift as the
  roster grows; a letter with at least one artist links to a big-letter
  marker (`.artist-letter-marker`) that sits in its own grid cell right
  before that letter's first tile -- both the jump bar's scroll target
  and a visible "you're now in the G's" divider, rather than an invisible
  zero-height anchor that used to just leave that cell looking blank.

`_upcoming_events_for()` (moved into `app/routes/artists.py`, alongside
`list_artists()`) computes each tile's "next show" -- the same
is_approved/`start_datetime >= now` filtering the artist detail page's own
"Upcoming shows" list already used, pulled out into one shared helper so
both stay in sync rather than maintaining two copies of the same logic.

**Alphabetizing ignores a leading "The "** -- `app/utils.py`'s
`artist_sort_key()`/`artist_display_letter()` strip a case-insensitive
"The " prefix before comparing, so "The Mountain Movers" sorts and groups
under M (right where a visitor looking for that band would actually
look), not off on its own under T. `artist_display_letter()` is also
registered as the `artist_letter` Jinja filter so list.html's per-tile
"did the letter change" grouping and `list_artists()`'s own
`available_letters` computation both apply the exact same rule rather
than each re-deriving it. Sorting is done in Python (`sorted(..., key=
artist_sort_key)`), not a SQL `ORDER BY` -- portably stripping a
case-insensitive prefix in SQL across both SQLite and Postgres is
fiddlier than it's worth at this site's artist-count scale. The same key
is used for every other artist listing that used to do a plain
`ORDER BY Artist.name` too (the calendar page's artist dropdown, the
Add/Edit Show form's "Featured local artists" checkboxes), so a "The ..."
band lines up the same way everywhere its name is listed.

## Import from Bandcamp (bookmarklet -- `app/bandcamp_bookmarklet.py`, `app/static/bandcamp_bookmarklet.js`)

Both the "Add artist" and "Edit artist" forms (`artists/form.html`) have an
"Import from Bandcamp" box above the main artist fields: a bookmarklet to
install once, plus a "Paste from Bandcamp bookmarklet" textarea and a "Fill
in fields" button. There is **no server-side route for this at all** --
the admin clicks the bookmarklet while looking at a band's own Bandcamp
page in their own browser, it copies a block of JSON to the clipboard, and
a small client-side `<script>` on the form parses that paste and fills in
name/hometown/photo/website/embed/bio/suggested-tags. That's a deliberately
unusual design for this project (its one and only bit of client-side JS,
everything else here is plain server-rendered Flask), and the road to it
is worth recording in full, because it went through three earlier
approaches that all failed against a live Bandcamp URL despite each one
looking sound at the time:

1. **Plain `requests` + BeautifulSoup.** Feasibility research (inspecting
   a real band's page, hushpuppy-mass.bandcamp.com, by hand through an
   actual Chrome tab) showed every needed field -- name, location, bio,
   tags, cover art, embed player URL -- present in the initial
   server-rendered HTML with no JS execution required. Looked sufficient.
   Run for real, David got back "couldn't find a band name... couldn't
   find any albums" -- everything blank, no error at all.

2. **Headless Chromium via Playwright**, with the same
   `--disable-blink-features=AutomationControlled` launch flag +
   `navigator.webdriver` override that fixed an identical-looking symptom
   for Quonk's Ticket Tailor listing (see `app/scrapers/rendered_html.py`
   and this README's Quonk write-up below). Same live retest, same
   completely empty result.

3. **A real Chrome User-Agent string**, not this project's usual honest
   venue-scraper one (`"Mozilla/5.0 (compatible; LocalMusicSitePOC/0.1)"`)
   -- Quonk's own config overrides `user_agent` too, and that detail had
   been missed when porting the pattern over. Third live retest, same
   result again, byte-for-byte.

Three fixes in, still identical failures, was the point to stop guessing
and get hard evidence instead (the same lesson the Quonk debugging arc
itself had already taught, further back in this file) -- a small
stand-alone diagnostic script driving the exact same Chromium/UA/flags as
the real code, printing the response status, final URL, and a screenshot.
That revealed the real cause: Bandcamp was serving a genuine **CAPTCHA**
("Enter the characters seen in the image below... Answer Submit"),
`Client Challenge` in the page title -- not a fingerprint check any
UA/flag tweak could quietly get past, an actual human-verification wall.
No amount of disguising an automated request defeats that, and building
something that tries to solve or bypass a CAPTCHA isn't something this
project does.

The retrospective on *why* research had looked so clean despite this:
research was done by driving a real Chrome browser through the Claude in
Chrome extension -- an already-established, human-driven browsing session,
nothing about which looks automated to Bandcamp. The actual feature, by
contrast, necessarily runs as an unattended, scripted sequence of
requests (root page, then `/music`, then potentially a dozen-plus release
pages within a couple of seconds) -- exactly the traffic pattern
bot-detection is built to catch, regardless of which HTTP client or
browser drives it. "I can browse it and see the data by hand" and "a
script can fetch it unattended" turned out to be two different claims,
and conflating them was the actual mistake, not any particular technical
detail of the fetch itself.

Given that, the fix couldn't be "make the automated request sneakier" --
it had to stop being an automated request in the first place. The
bookmarklet (`app/static/bandcamp_bookmarklet.js`, readable source; minified
with `terser` and embedded as `BOOKMARKLET_JS` in
`app/bandcamp_bookmarklet.py`, which turns it into a `javascript:` href via
`urllib.parse.quote()` -- percent-encoding leaves no characters Jinja's
autoescaping needs to touch, so it drops straight into an `<a href="...">`
safely) runs the same logic the abandoned server-side module did (same
selectors, same earliest-album-by-real-release-date logic, same
`<meta property="og:video">`-based embed construction), but as `fetch()`
calls made from *inside* the admin's own already-open Bandcamp tab --
genuinely no different from the browsing Bandcamp already trusts, because
it is that. `app/bandcamp_bookmarklet.py`'s docstring has the full
"why", `app/static/bandcamp_bookmarklet.js`'s comments mirror the
per-field extraction rationale the old Python module's did (bio
`<script>`/`more`-`less` toggle-link stripping, `/music` grid walking with
standalone `/track/...` links excluded, `data-tralbum` JSON parsing, etc.)
-- keep the two in sync if either changes, there's no shared code between
them (a bookmarklet can't `import` a Flask app module).

The one meaningful capability loss from dropping the server-side version:
it can no longer flash a friendly warning through Flask (e.g. "couldn't
find an album") -- the bookmarklet's own `alert()`/clipboard-copy message
and the paste box's status line cover the same ground client-side instead.

Verified two ways before shipping: (1) a jsdom-based Node harness
(`npm install jsdom`) running the actual bookmarklet source -- both the
readable file and the exact minified string embedded in
`bandcamp_bookmarklet.py` -- against the same fixture HTML used to verify
the original Python module, with `fetch`/`DOMParser`/`clipboard.writeText`
stubbed, confirming identical output (right band info, right earliest
album picked over the featured/newest one, standalone tracks skipped,
bio toggle links stripped) to what the old server-side module produced;
and (2) a Flask test-client smoke test confirming the bookmarklet link and
paste box render on both the add and edit forms, that no dead references
to the removed server-side routes remain anywhere, and that saving an
artist through the ordinary form still works untouched by any of this.

## Recurring event grouping (`app/recurrence.py`)

Real users testing the site pointed out the same problem two ways: a daily
art exhibit (Holley Flagg Exhibit) and a weekly open mic (Tue/Wed/Thu at
Luthier's Co-Op) each showed up as their own full card every single day,
which is a lot of repeated noise for "one thing that keeps happening."

`group_recurring_events()` groups same-venue events by normalized title,
splits each group into "runs" of occurrences close enough together in time
(within `MAX_GAP_DAYS`, currently 10 days) to plausibly be the same booking,
and collapses any run of at least `MIN_OCCURRENCES` (currently 3) into a
single row with an inferred weekday-pattern badge ("Daily", "Weekdays",
"Every Tue/Wed/Thu", or just a date range if the pattern's too irregular to
name). This is purely a display-time computation over already-scraped
`Event` rows -- no schema changes, no scraper changes, and no stored
"series" concept. It's deliberately *not* based on iCal RRULEs: only
ical-sourced venues could ever carry one, `icalendar` already expands them
into individual occurrences before this code sees them anyway (see
`app/scrapers/ical_feed.py`), and Elfsight/html-sourced venues never have
structured recurrence data at all -- reverse-engineering the pattern from
the occurrences we already have is the only approach that works uniformly.

`main.calendar()` always queries the full unbounded future set first (not
just the week view's 7-day window) so a series' badge/date-range stays
accurate -- e.g. "Every Tue/Wed/Thu &middot; thru Aug 15" -- even when only
its next occurrence or two falls inside the current 7 days; the week view
then just filters which *rows* are shown, without recomputing the grouping
on a truncated dataset.

To turn this off entirely (e.g. if it doesn't look/feel right in practice),
flip `GROUP_RECURRING_EVENTS = False` at the top of `app/routes/main.py` --
that's the only place it's wired in, so this one-line change (no other code
changes, no migration to undo) reverts to the old flat one-row-per-
occurrence behavior.

Admins should note that editing/deleting a collapsed series only affects
the one representative occurrence shown, not the whole run -- the calendar
page says as much next to those controls when a row is a series.

## Scraper framework (`app/scrapers/`)

Adding a venue means picking one of these `source_type`s:

- **`manual`** -- no scraping, you add shows by hand.
- **`squarespace_json`** -- for venues on Squarespace (a lot of small venues are).
  Squarespace event calendars are rendered client-side by JS, so a plain HTML
  fetch comes back empty. The workaround: appending `?format=json` to any
  Squarespace page URL returns the page's underlying JSON, including the
  events collection -- *if* the events actually live in Squarespace's own
  collection (see the Iron Horse note below for a case where they don't).
- **`ical`** -- for venues that publish a `.ics` feed (WordPress event
  calendar plugins, Google Calendar, etc.) -- the most robust option when
  available. Optional `scrape_config` key: `title_exclude` (list of
  strings, case-insensitive substring match) drops any event whose title
  contains one of them -- for feeds that mix real events in with
  non-event calendar entries (see the Luthier's Co-Op note below).
- **`html`** -- generic BeautifulSoup + CSS selectors against the raw server
  response, driven by the venue's `scrape_config` JSON (`item_selector`,
  `title_selector`, `date_selector`, optional `date_format`/`link_selector`/
  `description_selector`). Only works on server-rendered pages with no JS
  involved. When no `date_format` is given, the fuzzy-date path also handles
  free-typed, multi-day date text (see the Academy of Music note below).
- **`rendered_html`** -- same selector-driven config as `html`, but fetches the
  page with a headless Chromium browser (Playwright) first and waits for it to
  finish rendering before scraping. Needed for any venue whose calendar is
  actually a client-side widget rather than content baked into the page's own
  HTML/JSON. Two extra optional `scrape_config` keys: `wait_for_selector` (a
  CSS selector to wait for before capturing the page) and `wait_ms` (fallback
  fixed wait, default 3000ms).
- **`elfsight_jsonld`** -- purpose-built for Elfsight's "Event Calendar"
  widget. Fetches the page the same way `rendered_html` does, but instead of
  CSS-selector-scraping the widget's visible DOM, it reads the schema.org
  Event `<script type="application/ld+json">` block Elfsight embeds inside
  every event card. That JSON-LD has a full ISO `startDate`/`endDate`
  (year included), `description`, and `location.name` -- all more reliable
  than the visible markup, which uses hashed, version-specific class names
  and never shows a year in its date text at all. Same `wait_for_selector` /
  `wait_ms` keys as `rendered_html`, plus `location_match` (list of
  substrings matched case-insensitively against each event's JSON-LD
  location name -- defaults to `[venue.name]`), `include_all_locations`
  (bool, skips that filtering), and `category_include` (list of substrings,
  case-insensitive, matched against the same visible category text read
  for `genre` -- only matching events survive; see the 33 Hawley note
  below for why).
- **`haze_calendar`** -- purpose-built for Haze's (hazenorthampton.org)
  built-in calendar widget. Plain HTTP fetch (server-rendered, no headless
  browser needed), but events are grouped inside a day cell rather than
  each carrying their own date, which doesn't fit the generic selector
  model. Reads `<time datetime="YYYY-MM-DD">` per day cell (a real ISO
  date) and `aria-label="Event details: <title>"` per event -- the only
  two things in an otherwise all-Tailwind-utility-classes page that are
  stable regardless of styling changes. No `scrape_config` needed.
- **`ludus`** -- purpose-built for venues running [Ludus](https://ludus.com)
  as their ticketing platform (e.g. `bombyx.ludus.com`, a separate site
  from a venue's own marketing page). Fetches the page the same
  headless-Chromium way `rendered_html` does (a plain fetch got blocked
  with a 403 -- see the Bombyx note below), but with its own bespoke
  parser instead of the generic selector config: each event (`.show_item`)
  can contain more than one date/session (`.showtimes_item`, e.g. a
  recurring weekly class), each of which becomes its own event sharing
  the same title; `data-showtime-id` is used directly as the stable
  `external_id`. Accepts every `scrape_config` key `rendered_html` does
  (`wait_for_selector`, `user_agent`, etc.), plus its own
  `category_include` (list of substrings, case-insensitive, matched
  against the show's visible category pill text -- see the Bombyx note
  below for a real tradeoff with this).
- **`venuepilot`** -- purpose-built for venues running
  [VenuePilot](https://venuepilot.co) as their event listing widget (first
  and so far only case: CitySpace -- see write-up below). Fetches the page
  the same headless-Chromium way `rendered_html` does (`fetch_raw` is
  reused directly from that module, same reasoning as `ludus.py`), since
  VenuePilot's widget is a client-side React app behind a hash route with
  no server-rendered content and no usable public API (GraphQL
  introspection is disabled). Its own bespoke `parse()` reads
  `.vp-event-row` markup directly. One `scrape_config` key of its own,
  `title_exclude` (same convention as `ical_feed.py`'s), plus every key
  `rendered_html` accepts since it shares that module's `fetch_raw`.

Each source type is one small module (`squarespace_json.py`, `ical_feed.py`,
`html_generic.py`, `rendered_html.py`, `elfsight_jsonld.py`, `haze_calendar.py`,
`ludus.py`, `venuepilot.py`) exposing `fetch_raw(venue)` and `parse(raw, venue)`.
Adding a venue whose site doesn't fit any of these means writing one new
module and registering it in `app/scrapers/base.py`.

**On the Iron Horse specifically:** its calendar turned out to be an
[Elfsight "Event Calendar"](https://elfsight.com/event-calendar-widget/)
widget with no public API -- confirmed by checking the scrape preview screen
against `squarespace_json`, which showed the venue's own Squarespace
collection reporting `itemCount: 0` (i.e. the events aren't Squarespace
content at all, just an embedded third-party widget on top of it). A real
rendered-page sample then showed each event card embeds a full schema.org
Event JSON-LD block, so it's seeded with `source_type = "elfsight_jsonld"` and
`scrape_config = {"location_match": ["Iron Horse"]}` -- no manual selector
work needed. That `location_match` filter matters because the Iron Horse's
Elfsight feed is shared across several sibling "Parlor Room Collective"
venues (Black Birch Vineyard, Musician's Workshop, The Parlor Room, etc.);
without it, every show on the shared feed would get misattributed to the
Iron Horse. The Parlor Room is seeded the same way, filtered to
`["Parlor Room"]`, on the same feed URL. To finish wiring either one up:

1. `playwright install chromium` once (see below).
2. Run **Venues → (venue) → Test scrape** and confirm the parsed events look
   right and are attributed to the correct physical venue.
3. If a venue's `location_match` needs tweaking (e.g. the JSON-LD location
   name doesn't contain the substring you expect), edit it via
   **Venues → edit → scrape config** and re-test.
4. Once it looks right, **Run scrape now** to import.

Note: if you already ran `seed.py` before this scraper existed, re-running it
won't retroactively update your existing Iron Horse / Parlor Room rows (the
seed script only creates venues that don't already exist by slug) -- edit
those two venues' source type and scrape config directly via the UI instead.

**On the Academy of Music (aomtheatre.com):** a plain WordPress site with no
JS widget involved, so it uses `source_type = "html"` directly against
`https://aomtheatre.com/event-calendar` -- no headless browser needed.
Selectors (confirmed from a real "Copy outerHTML" of one event card) are
`.event_card` / `.event_card_title h5` / `.event_card_date_times` /
`.event_card_details_button a` / `.event_card_presents` (title/date/link/
description respectively). Its date field turned out to be free-typed rather
than machine-readable, and some entries span multiple nights with the year
omitted entirely (e.g. "Friday, March 12th and Saturday, March 13th"). That's
what motivated the fuzzy-date improvements in `html_generic.py`: multi-day
text is truncated to just its first date, and a missing year falls back to
the last year seen earlier on the page (the listing is chronological, so
that's a safe assumption) -- verified against all the actual edge cases seen
on the page before wiring it up for real.

**On Smith College's events calendar (smith.edu/news-events/events):** a
Drupal 10 site, server-rendered (confirmed via a direct fetch), so this is
also `source_type = "html"`. Selectors (confirmed from a real raw-HTML
sample of one event "teaser") are `article.teaser` / `.heading__link` /
`.teaser__subheading` / `.teaser__text` / `.teaser__media img`. Two things
made this venue different from the others:

- This is the *whole college's* calendar (exhibitions, lectures, religious
  life, performances, everything), not a single music venue -- deliberately
  seeded to pull all of it rather than filter down to just the site's own
  "Performances" event type, so expect real non-music noise here that the
  Review queue needs to sort through.
- Its date field is `"Wednesday, July 22, 2026 | 9 a.m.-4 p.m."` -- a date
  and a start-end time range joined by `|`. Handing that whole string to
  dateutil's fuzzy parser picks up the *end* time instead of the start
  time. `html_generic.py` now has a small `_clean_time_range()` heuristic
  (understands "Noon"/"Midnight" and a first time missing am/pm because it
  shares the second time's) that strips it down to just the start time
  before parsing -- it's a no-op on any date string without a `|`, so it's
  safe for every other venue.

Also new in `html_generic.py`, needed because Smith's listing is paginated
(`?page=N`): `scrape_config` now accepts `"page_param"` and `"max_pages"`
to fetch and concatenate several pages per scrape (Smith is seeded with
`max_pages: 6`) -- both are no-ops for every existing venue's single-page
config.

**On Haze (hazenorthampton.org):** a custom Next.js site with its own
built-in calendar widget, not a third-party embed. A plain fetch returns
the full month-grid HTML already rendered, so no headless browser is
needed. Its styling is entirely Tailwind utility classes with no
semantic hooks (unlike Elfsight's `eapp-` prefixed helpers or WordPress's
`event_card` class), so scraping it needed a dedicated module
(`haze_calendar.py`) built around the two things that *are* stable: a
real `<time datetime="...">` per day cell (full ISO date, year included
-- no DOM-badge/JSON-LD cross-referencing needed the way Elfsight's
venues required), and `aria-label="Event details: <title>"` per event,
which doubles as a clean title. Time of day is regex-matched from the
event's own text; a few recurring entries (e.g. weekly "Bar Night!")
show "Tonight" / "Til 2am!" instead of a clock time and fall back to
midnight -- verified against the real markup for a timed/imaged event,
an empty day cell, a no-clock-time entry, and a day with multiple events
before wiring it up. Worth noting: the event flyer images are hosted on
an S3 bucket (`calendar-wish...`) keyed by what look like Google Calendar
event IDs, suggesting the whole thing might be backed by a synced public
Google Calendar -- if so, that calendar's own `.ics` feed (via the
`ical` source type) would be more robust than this widget-scraping
approach, but the actual calendar ID wasn't discoverable from the pages
fetched so far.

**On Luthier's Co-Op (luthiers-coop.com, Easthampton):** the first venue
to actually use that `ical` source type for real. Runs WordPress's "The
Events Calendar" plugin (confirmed via `tec-api-*` meta tags), which
publishes a genuine `.ics` export at `/events/?ical=1` -- the page's own
"+ Export Events" link. Real per-event UIDs and zoned start/end times, no
HTML scraping or headless browser needed at all. The one wrinkle: this
venue's calendar feed also contains non-event entries -- "CLOSED" /
"CLOSED FOR SUMMER VACATION" days, and "BackStage Bar Open 4-11pm" (the
bar's own daily hours, posted as a calendar entry every day it's open) --
mixed in right alongside real shows, open mics, and karaoke nights.
`title_exclude: ["CLOSED", "BackStage Bar Open"]` filters those two out
before they ever reach the review queue, while still keeping recurring
Open Mic/Karaoke nights, which are real weekly entertainment.

**On 33 Hawley (33hawley.org, home of the Northampton Center for the
Arts):** another Elfsight "Event Calendar" widget, same product as Iron
Horse/Parlor Room, embedded right on the homepage rather than a separate
events page (there isn't one -- confirmed via `sitemap.xml`, which lists
no events/calendar URL at all). Identified without being able to render
the page myself (this sandbox can't launch a headless browser to check
JS-rendered content): David opened the page's DevTools Network tab and
found a request to `universe-static.elfsightcdn.com/.../event-calendar/...`,
which is the same Elfsight product Iron Horse's scraper already handles.
This building hosts multiple resident arts organizations running dance,
theatre, classes, and workshops through the same shared calendar --
David wants only live performances pulled in automatically, the opposite
tradeoff from Smith College's "pull everything" choice above. New
`category_include: ["Performance"]` config (see the `elfsight_jsonld`
bullet) filters to just events whose visible Elfsight category tag
contains "Performance". No default tag, same reasoning as Smith College:
even filtered to "Performance," this could still mean theatre or dance,
not only music.

**Caveat:** this venue's scrape_config hasn't been verified against its
actual rendered markup the way every other venue here has (again, no
headless browser available in this environment) -- confirmed via a
synthetic test that the filtering *logic* works correctly, but not that
the real widget's category text says exactly "Performance". If a real
scrape comes back empty or with the wrong events, check what the visible
category text actually says and adjust `category_include` to match.

**On The Heavy Culture Cooperative (theheavyculture.coop, Easthampton):**
a Wix site running the native "Wix Events & Tickets" app. Confirmed
server-rendered via a real view-source of `/shows` -- unlike Squarespace's
client-rendered calendars, Wix bakes the event markup (and a big
`wix-warmup-data` JSON blob with the same data again) right into the raw
HTML. The one wrinkle worth remembering here: this page embeds the *same*
events widget three separate times -- an Upcoming Events list, a Calendar
view, and a Past Events list -- and all three render identical
`data-hook="event-list-item"` `<li>` markup. A bare `item_selector` would
triple-count every event and pull in past shows besides, so
`item_selector` is scoped to `#comp-lk7y5t1j`, the Wix-assigned component
id of just the Upcoming Events widget. Selectors target Wix's own
`data-hook` attributes rather than its CSS classes -- the classes are
per-build hashes (`FwdPeD`, `WFgzOI`, etc.), the same problem seen with
Elfsight's widget markup elsewhere, while `data-hook` is Wix's stable
automation-hook convention and shows up consistently across events.
Some events' date text is a range ("Jul 24, 2026, 7:00 PM &ndash; 11:00
PM") rather than a single time -- same underlying problem as Smith
College's "|" ranges (dateutil's fuzzy parser grabs the *end* time), just
a different separator, so `html_generic.py` got a third heuristic,
`_strip_dash_time_range()`, alongside `_clean_time_range()`.

The Upcoming Events widget only server-renders its first 7 shows and has
a "Load More" button (`<button data-hook="load-more-button">`, confirmed
via a real view-source) for the rest. That button has no `href` and
there's no `?page=N`-style URL anywhere -- it's a bare client-side control
that fetches more events from Wix's own internal Events API, and even the
page's `wix-warmup-data` blob only caches those same first 7 (with
`"hasMore": true`), so there's genuinely nothing more to find in the
static HTML. That means plain `html`/`html_generic` can't reach the rest
of the events, so this venue uses `rendered_html` instead -- the same
headless-browser approach already built for 33 Hawley's Elfsight "Next
Events" button. `next_button_selector` is scoped to `#comp-lk7y5t1j`
(both the Upcoming and Past Events widgets have their own Load More
button) and `next_button_clicks: 2` grabs a couple of extra batches
beyond the initial page load.

**On Quonk (quonkhampton.com, an immersive-arts venue in downtown
Northampton):** the one venue here whose *own* site is genuinely
client-rendered start to finish -- a plain fetch of quonkhampton.com
returns nothing but an empty shell and `<meta>` tags. `events_url` skips
quonkhampton.com entirely: every event card's "Learn More" link on that
homepage goes straight out to a Ticket Tailor listing
(`tickettailor.com/events/quonkhampton/<id>`) -- a third-party ticketing
platform, not another page on Quonk's own site -- so `events_url` points
at Ticket Tailor's own listing page (`tickettailor.com/events/quonkhampton`)
instead, which has every upcoming event's title/date/link/image in its
markup:

```
<li class="events-listing__item">
  <div class="event__content__titles">
    <h3 class="event__title">
      <a class="event__link" href="/events/quonkhampton/2307456">Punchline! Stand Up Comedy @ Quonk</a>
    </h3>
    <div class="event-meta event__meta">
      <span class="event-meta__date">
        <span isolate="">Fri</span><span isolate="">Jul</span>
        <var>24</var>, <var>2026</var><var>7:30 PM</var>-<var>9:15 PM</var>
      </span>
    </div>
  </div>
</li>
```

That listing page never shows a description, though -- David asked for
this venue specifically because you have to click through to each event
to read one, and each event's own Ticket Tailor detail page has it, in a
`section.detail-content__description` block.

Getting this venue actually working took three separate real bugs to
chase down, worth recording since each one *looked* like it explained the
symptom until the next scrape attempt proved otherwise:

1. **A plain `requests.get()` on the listing page got a flat 403,
   regardless of `User-Agent`.** Ticket Tailor's own `robots.txt`
   explicitly allows crawling these exact pages for any user-agent, so
   this wasn't really "keep bots out" -- almost certainly a TLS/header
   fingerprint check a plain `requests` call can't fake no matter what
   headers it sends, which a real browser passes automatically. Fixed by
   switching `source_type` to `rendered_html` (a real headless Chromium
   via Playwright) for both the listing fetch *and* the per-event
   description fetch -- the latter needed its own new mechanism,
   `description_from_link`/`description_detail_selector` in
   `rendered_html.py`, which reuses the same already-past-the-block
   Playwright page for each event's detail page and writes the result
   into the captured listing HTML as a `<div class="__prefetched_description">`
   right inside its matching item, so `html_generic.py`'s ordinary
   `description_selector` handling (pointed at that class) picks it up
   with no separate network call at parse time at all.
2. **Even with a real headless browser, the scrape came back with only 1
   of 5 events, and a garbled date on that one.** A direct side-by-side
   check -- the identical URL, loaded in an ordinary human-driven Chrome
   tab -- came back with all 5 events correctly immediately, no waiting
   needed. Same URL, different content, only when automated: the
   likely cause is `navigator.webdriver` (`true` by default in an
   unmodified Playwright session, `false` in a real browser), a
   well-known signal bot-management products check for, causing a
   stale/fallback snapshot to be served to automated sessions. Fixed with
   the standard mitigation -- `rendered_html.py` now launches Chromium
   with `--disable-blink-features=AutomationControlled` and overrides
   `navigator.webdriver` via an init script before any page script runs.
   (Also added `_wait_for_stable_item_count()`, which polls the matching
   item count until it stops changing rather than capturing the instant
   the first one appears -- a reasonable fix for a *different*, more
   common failure mode of progressively-hydrating widgets, and worth
   keeping even though it turned out not to be this specific symptom's
   actual cause.)
3. **Even after fixing the above, the real captured HTML -- confirmed via
   a raw-sample dump, which needed `_RAW_SAMPLE_SIGNALS` in
   `app/scrapers/base.py` extended with `"events-listing__item"` since
   the default `<body>`-anchored sample never reached that far past
   Quonk's header/hero markup -- showed all 5 events with correct dates
   right there in the markup, yet parsing still produced 1 wrong event.**
   The real culprit: `date_selector`'s match isn't one flat text node,
   it's several (`<span isolate>Fri</span><span isolate>Jul</span>
   <var>24</var>...`, see the sample above). `BeautifulSoup.get_text(strip=True)`
   with no separator glues adjacent fragments together with nothing
   between them ("FriJul24, 2026..."), and dateutil's fuzzy parser
   doesn't recognize "FriJul" as a month at all -- it either fails to
   parse the string outright (event silently skipped) or falls back to
   January. Fixed by adding `separator=" "` to every `get_text()` call in
   `html_generic.py`'s `parse()` (title, date, inline description, genre)
   -- a no-op for every other venue here, whose selectors all match a
   single plain-text node with no internal tag boundaries to separate.

This is also the first venue where a relative href/src needs resolving
against a *different* domain than `website_url` -- Ticket Tailor's own
domain, not quonkhampton.com. `html_generic.py`'s `_resolve_url()` calls
now anchor against `events_url`'s own scheme+host instead (see that
file's module docstring), which is a strictly more correct fix for every
other venue here too, since it happens their `events_url` and
`website_url` share a domain already.

**Caveat:** one event ("The Number Goes Up! Gameshow") lists multiple
showtimes as just "Fri Jul 31, 2026, Multiple times" instead of a real
time -- the fuzzy date parser can't extract a time from that and falls
back to midnight. Rare enough (one event, currently) not to be worth a
dedicated heuristic; if it bothers you, edit that show's time by hand
after it's imported.

**On BOMBYX Center for Arts & Equity (130 Pine St., Florence village,
Northampton):** its own marketing site is `bombyx.live`, but that's not
where the actual event listing lives -- ticketing runs on a separate
[Ludus](https://ludus.com) install at `bombyx.ludus.com/index.php`
instead, so `events_url` points there, not at `website_url`. Confirmed
via live browser DOM inspection (a real Network-tab log showed no
separate XHR/JSON request fetches the event list -- only a POST to
`/v1/shows/seats-left` for seat-count numbers *after* the page has
already rendered -- and the page is served from `index.php`, a plain
PHP page, not a JS framework/SPA):

```
<div class="show_item" data-show-id="200526944" data-event-categories="1823;">
  <div class="show_item_category_pills">
    <span class="event-category-pill show_item_category_pill">Concert</span>
  </div>
  <div class="show_item_cover_photo" style="background-image:url('https://ludus.../cg_....');"></div>
  <h2 class="show_item_title"><span class="patron_heading_label">Sufi-Buddhist Fusion Soundbath</span></h2>
  <div class="showtimes_item" id="showtimes_item311574" data-showtime-id="311574">
    <div class="admin_showtimes_item_title">
      <div class="desktop_copy">
        <span class="span_link">Sunday, August 9, 2026 <span>12:00 PM</span></span>
      </div>
      <!-- .mobile_copy sibling duplicates the same text for responsive layout -- not selected, to avoid double-counting -->
    </div>
  </div>
</div>
```

One `.show_item` (a titled listing) can contain more than one
`.showtimes_item` (a date/session) -- e.g. a recurring weekly class with
five separate Saturday dates -- and each one becomes its own event
sharing the same title, using its own `data-showtime-id` directly as a
stable `external_id`.

Two things worth flagging:

1. **A plain `requests.get()` got a flat `403 Forbidden`.** The original
   version of this scraper used a plain fetch, on the theory that a
   server-rendered PHP page with no separate list-fetching XHR almost
   certainly has this markup in the *initial* response too (the same
   reasoning `squarespace_json.py` uses for Iron Horse) -- but a real
   scrape attempt came back blocked outright, with no distinguishing
   body to go on. That's the same shape of failure Quonk's Ticket Tailor
   listing hit (see that write-up above): almost certainly a TLS/header
   fingerprint check a plain `requests` call can't fake, not a
   UA-string check a real browser passes automatically. Rather than
   spend another live-test round-trip on a smaller fix that already
   failed to be enough for that identical-shaped Quonk problem,
   `fetch_raw()` now reuses `rendered_html.py`'s Playwright-based fetch
   directly (the same headless-Chromium-with-automation-hiding-flags
   approach already proven against Quonk/Heavy Culture/33 Hawley) --
   it's a fully generic `fetch_raw(venue)` with no dependency on
   `html_generic`'s parsing, so it's safe to import and pair with this
   module's own bespoke `parse()` (which understands Ludus's nested
   `show_item`/`showtimes_item` shape, not the generic selector config
   `rendered_html.py`'s own `parse()` expects). `source_type` stays
   `"ludus"`, not `"rendered_html"`, for exactly that reason.
   `wait_for_selector: ".show_item"` and a realistic Chrome `user_agent`
   are set in `scrape_config` even though this isn't `source_type =
   "rendered_html"` -- every one of that module's config keys still
   applies, since `ludus.py`'s `fetch_raw` *is* that same function.
2. **`category_include: ["Concert"]` has a real tradeoff.** BOMBYX is a
   genuine multi-use community arts space -- dance classes, grant-writing
   workshops, speed networking, theater -- sharing the same Ludus listing
   as its actual concerts, so `category_include` filters to just the
   "Concert" pill, the same call made for 33 Hawley above. But a couple of
   obviously-musical listings seen live ("Noho Music Presents: Summer Jam
   '26", "Choro Camp 2026") had **no category pill at all** and would be
   silently excluded by this filter. Worth a look at the scrape preview
   once this runs for real -- loosen or drop `category_include` if that's
   happening more than rarely.

There's no reliable direct "buy tickets" link per show/date in the
DOM either -- the "Get Tickets" control is a `<div>` (not a link) driving
an in-page radio-button + form flow rather than navigating anywhere, so
`ticket_url` falls back to the venue's own Ludus listing page, the same
fallback `elfsight_jsonld.py` uses when a venue's own widget doesn't
expose one.

**On CitySpace (43 Main St., Easthampton -- the town's shared community
space inside Old Town Hall):** events are listed through
[VenuePilot](https://venuepilot.co), a third-party widget embedded via a
WPBakery "raw HTML/JS" block on an otherwise ordinary WordPress page, with
all real content living behind a client-side hash route
(`cityspaceeasthampton.org/all-events/#/events`). A plain fetch only ever
sees the empty page shell -- the widget's own React app renders everything
after the fact, pulling from a GraphQL API (`www.venuepilot.co/graphql`)
that has introspection disabled and isn't otherwise documented, so this
follows the same path Quonk/Heavy Culture/Bombyx already took: load the
real page in a headless browser and parse the *rendered* DOM (`app/scrapers/
venuepilot.py`) instead of reverse-engineering the API. Structure,
confirmed via live DOM inspection in a real browser:

```
.vp-event-row
  a.vp-event-link[href="#/events/188338"]   -- this venue's own stable
                                                per-event page; used
                                                directly as external_id
  .vp-month-n-day  "Aug 22"                 -- no year at all, see below
  .vp-time  "8:00 PM"
  .vp-promoter  "The Blue Room at CitySpace" -- the specific room/space
                                                this show is actually in
                                                (see custom_venue_name
                                                below), or just plain
                                                "CitySpace" for anything
                                                not tied to a sub-space
  .vp-event-name  "..."                     -- the title
  .vp-support  "..."                        -- optional subtitle/series
                                                line, appended to the
                                                description
  .vp-main-img / .vp-cover-img              -- cover image, a `style=
                                                "background-image:url(...)"`
                                                div, same pattern as
                                                Bombyx/Ludus above
```

Two things worth flagging:

1. **No year is shown anywhere in the date text.** `_resolve_year()` tries
   the current year first, then next year, keeping whichever lands within
   a small grace window of "today" -- e.g. scraping in August 2026 and
   seeing "Jan 8" correctly resolves to January 2027, not the already-past
   January 2026. This is a different heuristic than `html_generic.py`'s
   "carry forward the last year seen on the page" approach (used for
   Academy of Music above), since VenuePilot's page never shows a year at
   all, even once, to carry forward.
2. **CitySpace is genuinely mixed-use**, not a dedicated music venue -- the
   same widget lists Blue Room concerts alongside ECA Gallery art
   openings, a monthly building tour, a pop-up market, and a volunteer
   day, with no visible category field to tell them apart on the listing
   page. Same tradeoff as Quonk/Smith College above: `seed.py` leaves
   `default_event_type` unset, so every newly scraped event lands untagged
   and off the public calendar (whichever categories a visitor has
   selected) until an admin tags the real shows by hand via the Review
   queue. `title_exclude` is there to
   permanently drop the recurring non-music filler (the Tour/Market combo
   repeats close to monthly) once it's clear which titles are never going
   to be shows.

This is also the first scraper to make use of `Event.custom_venue_name`
(the "one-off location" field originally built for hand-entered festival
sets/street fairs -- see the Data model section): CitySpace's `.vp-promoter`
text names the actual room a show is in (e.g. "The Blue Room at CitySpace",
"ECA Gallery"), which is more specific and more useful to a visitor than
just "CitySpace." `venuepilot.py` sets `custom_venue_name` to that text
*except* when it's just the venue's own plain name verbatim, in which case
it's left unset so `display_venue_name`/`display_venue_link` fall back to
the real Venue row (with a working website link) instead of a redundant,
link-suppressing override.

**Caveat:** this venue's scraper hasn't been run against the real live
site end to end in this project's own dev environment -- Playwright's
Chromium binary couldn't be installed here (the download itself is
blocked by the same sandbox network restriction that blocks most direct
`curl`/`pip`-style fetches in this environment), so `fetch_raw()` itself
is unverified. `parse()` is verified against a synthetic HTML fixture
built from real, individually-confirmed class names and example values
(gathered via live browser DOM inspection) -- covering the year-rollover
date logic, the room-name override, image extraction, and de-duplication
-- but the very first real scrape should still be spot-checked on the
**Venues → CitySpace → Test scrape** preview page before running **Run
scrape now** for real, the same as any newly added venue.

**On Amherst Cinema (28 Amity St., Amherst -- a nonprofit independent
movie theater, David's first non-music venue):** its homepage's own
showtimes calendar is a hover-to-preview widget, but that's purely a UI
skin -- confirmed via live browser DOM inspection that every day cell
links to its own real URL, `amherstcinema.org/calendar/month/<YYYY-MM-DD>`,
a plain server-rendered Drupal Views page showing the exact same markup
the hover popup does. That means `app/scrapers/amherst_cinema.py` needs
no headless browser at all -- unlike Quonk/Heavy Culture/Bombyx/CitySpace
above, a plain `requests.get()` of each day-page is enough.

Confirmed structure (live DOM inspection), one `.views-row` per film
playing that day:

    <div class="series">Big Screen Classics</div>   <!-- often empty -->
    <div class="title"><a href="…/films-and-events/<slug>">Late Fame</a></div>
    <div class="times">
      <div class="views-view-fields"><a href="<agileticketing url>"><span class="date-display-single">2:00 pm</span></a></div>
      <!-- one .views-view-fields per showtime that day -->
    </div>

**One Event row per film per *day*, not per showtime** -- a deliberate
simplification David asked for explicitly, since a single film running
3-4 showtimes a day for a week or more would otherwise flood the
calendar with 15-20 rows a day for this one venue alone. All of a day's
times get folded into `Event.description` as a "Showtimes: 2:00 pm |
4:55 pm | …" line; `start_datetime` is set to that day's *first* showing
so date sorting and "is this upcoming" checks still behave, but the
calendar card itself only reflects that first time -- the full list only
shows up via the description tooltip/detail page. `ticket_url` points at
the film's own detail page (which lists every remaining date/time with
its own buy button) rather than one specific showtime's AgileTicketing
link, since a single row can't represent several different showtimes'
separate purchase links.

Each film also has its own detail page (the same URL its title links to)
with metadata that would be wasteful to re-fetch on every single day/
showtime row: a poster image, runtime, director, MPAA rating, release
year, and a synopsis (confirmed classes: `.field-name-field-image-front
img`, `.field-name-field-duration`, `.field-name-field-director`,
`.field-name-field-rating`, `.field-name-field-year`,
`.field-name-body`). `parse()` fetches each *unique* film's detail page
exactly once per scrape run (cached by slug in a plain dict), no matter
how many different days that film shows up on within the scraped window
-- the same one-extra-request-per-item spirit as `html_generic.py`'s
`description_from_link`, just applied at the film level since here the
"item" legitimately spans many day-page rows. `Event.genre` is set from
the day-page's own "series" label when present (e.g. "Big Screen
Classics"), distinguishing a revival screening from a regular new
release.

`scrape_config`'s `days_ahead` (default 21, matching `base.py`'s own
`MISSING_CHECK_WINDOW_DAYS`) controls how many consecutive days forward
from today get fetched as separate day-pages. Every day checked live
(through the end of the current month and into the next) already had a
full schedule, so this is a real, useful window in practice -- but
there's no guarantee that holds forever, hence configurable.

A real bug David found after the first live scrape: the poster image
never showed up on the calendar, even though `Event.image_url` held a
real, correct, publicly-loadable URL. Root cause, confirmed via a live
cross-origin test (loading the exact same poster URL as an `<img>` from
a genuinely different origin failed with `onerror`, while a plain
navigation to that same URL, or a `fetch()` with no Referer, succeeded):
amherstcinema.org hotlink-protects its poster images -- a direct
navigation loads fine, but an embed on any *other* site gets refused.
`_download_poster()` in `amherst_cinema.py` fixes this the same way this
project already handles admin-uploaded flyers/venue photos: download the
image once (a plain server-to-server `requests.get()`, not a browser
embedding a cross-origin `<img>`, so it isn't subject to that same
check) and save it under `/static/uploads/flyers/` (`FLYER_UPLOAD_DIR`),
then store *that* local URL on the Event instead of Amherst Cinema's
own. Filenames are deterministic (`amherst-<slugify(slug)>.<ext>`, not a
random uuid like a one-off admin upload gets) specifically so a
re-scrape of the same film overwrites its existing local copy instead of
piling up an ever-growing set of orphaned files on every single scrape
run, forever. Best-effort like every other helper in this module: a
failed download just leaves `image_url` as `None` (falling back to the
venue photo or site logo) rather than sinking the whole scrape.

David asked, after this venue's first real scrape, for a film's full
list of that day's showtimes to be visible directly under its title on
the calendar card, not just in the hover tooltip/truncated popup (the
only place a plain `description` normally surfaces -- see calendar.html's
`data-description`/`title="..."` uses). `app/utils.py`'s `showtimes_line()`
(registered as the `showtimes_line` Jinja filter in `app/__init__.py`)
pulls just that leading "2:00 pm | 4:55 pm | ..." text back out of the
description's first `<p>Showtimes: ...</p>` paragraph -- the one
`_build_description()` above always puts first -- and calendar.html
renders it as its own `.event-showtimes` line right under the title, only
when present. Deliberately scoped to that one exact leading-paragraph
shape rather than showing a preview of *any* event's description under
its title: Quonk's `description_from_link` pull-through in particular is
a full paragraph-length blurb that would blow up every card's height if
shown inline by default. Every other venue's events (nothing else
currently produces a description starting with "Showtimes:") render
exactly as before.

**Caveat:** `parse()` and `fetch_raw()`'s day-window/marker logic are
both verified end to end -- including a full `run_scrape()` smoke test
against a real seeded `Venue` row with a faked network layer -- but
against *synthetic* HTML built from real, individually-confirmed classes
and example values (gathered via live browser DOM inspection, including
actually clicking through to a film's detail page). The scraper's own
`requests.get()` has not been run against the live site from this
project's own dev environment -- outbound requests to arbitrary domains
are blocked by this sandbox's network restrictions, the same limitation
noted in CitySpace's caveat above. The very first real scrape should
still be spot-checked on the **Venues → Amherst Cinema → Test scrape**
preview page before running **Run scrape now** for real.

**On One Amber Lane (1 Amber Ln, Northampton):** confirmed Squarespace
(body class fingerprint) via live browser inspection, so this venue
reuses `app/scrapers/squarespace_json.py` completely unmodified -- the
same module already handling the Iron Horse. A live `?format=json` fetch
of `oneamberlane.com/events` returned 36 real event candidates in the
exact shape `_find_event_candidates()` already expects (`title`,
`startDate`/`endDate`, `fullUrl`, `assetUrl`, etc.), all live-music
listings (e.g. "Andie Arel Live Music", "B-Town Trio Live Music"). No
`title_exclude` or other config tweak was needed -- unlike CitySpace/
Quonk/Smith College, nothing non-music showed up in the sample.

The site's own nav never prints a street address -- its "location" link
points straight to a Google Maps place, and Maps currently has that
location filed under an older business name, "Iconica Social Club" (same
building/coordinates, confirmed via the map listing's own displayed
name/address: "Amber Lane Café & Pub" / "1 Amber Ln, Northampton, MA
01060"). That's what's seeded as the venue's address.

This venue also motivated a small, pure-addition enhancement to
`squarespace_json.py` itself: `parse()` now pulls `item['assetUrl']`
(when present) into `Event.image_url`. Squarespace's own image CDN
(`images.squarespace-cdn.com`) serves these already-absolute with no
hotlink/referrer protection -- unlike Amherst Cinema's own site-hosted
poster images (see that venue's Caveat above) -- so no re-hosting step
is needed here; the URL is used as-is. This also benefits the existing
Iron Horse venue, which previously got no event images at all. An item
with no `assetUrl` simply leaves `image_url` as `None`, same as before.

David reported One Amber Lane's showtimes were wrong after its first
live scrape. Root cause: Squarespace's `startDate`/`endDate` fields are
Unix-epoch milliseconds -- a real, unambiguous UTC instant -- but
`squarespace_json.py`'s original code converted them with
`datetime.utcfromtimestamp()`, which produces a naive datetime holding
that *raw UTC* value. Every other part of this app stores and compares
`Event.start_datetime` as naive **America/New_York local wall-clock**
time (see `app/utils.py`'s `SITE_TIMEZONE`/`local_now()`), so the scraper
was quietly storing a UTC clock-reading under a column everything else
treats as local -- a 4-5 hour error that, for an evening show, rolls it
onto the wrong calendar day entirely (e.g. a real 10:00pm Dec 28th show
came out stored/displayed as 3:00am Dec 29th). This is the same bug
class already hit and fixed for the Elfsight scraper (Iron Horse/Parlor
Room). Fixed with a new `_ms_to_local()` helper in `squarespace_json.py`
that converts to timezone-aware UTC first, then `.astimezone()`s to
America/New_York and drops the tzinfo -- mirroring
`elfsight_jsonld.py`'s own `_to_local()`. Covered by
`test_squarespace_json.py`, which asserts the exact wrong-calendar-day
case against a real epoch-ms value.

## Submit your show (`app/routes/gigs.py`, `GigSubmission` model)

A public `/gigs/submit` form -- linked from the main nav as "Submit a Show"
-- lets an artist or promoter propose a show without needing an admin
account, including DIY one-off shows (house shows, backyard sets, basement
gigs) that don't belong to any formal venue. Required fields: date &amp;
time, a free-text location/venue field (not a dropdown -- see "DIY show
handling" below for why), a free-text box for the bands on the bill and
their websites (not a structured per-band list -- there's no reliable way
to auto-parse that into individual Artist records, so an admin reads it
by hand during conversion), a flyer image upload, and the submitter's own
name/email (so David can follow up with questions or let them know once
it's live). An optional free-text "Genre(s)" field (`GigSubmission.genres_text`)
lets a submitter list the show's genre(s) (e.g. "Punk, Folk, Jazz") --
optional since not every submitter will think to fill it in, and not tied
to any pick-list, since there's no manual-add-show form field for
`Event.genre` to prefill from anyway (see the conversion prefill note
below).

Every submission lands as a **pending `GigSubmission` row**, not an Event
-- same "keep unvetted input off the public site until a human looks at
it" idea as a scraped Event's `is_approved=False`, just for a different
intake path (anyone can fill in this form, with no scrape/venue-feed
behind it at all). Submitting immediately:

1. Sends the submitter a "flagged for review" confirmation message (a
   flash message on the same page, not a separate email -- see "No
   confirmation email" below).
2. Emails David via `send_admin_email()` (see "Shared admin email" below)
   with the full submission -- date, location, lineup, submitter contact
   -- **with the submitted flyer image attached directly to the email**
   (not just a link to it), so it's visible right in the notification
   without opening the review queue first -- and a direct link to the
   review queue, so nothing sits unnoticed waiting to be checked.

A honeypot field (`.hp-field` in style.css -- hidden from real visitors,
tripped only by a bot that fills in every input including hidden ones)
guards against spam the same way the contact form already did; a caught
submission gets the same success message with nothing actually saved,
so a bot has no signal it was caught.

**Admin review (`/gigs/review`, linked from the nav as "Gig Submissions"
with a pending-count badge, same pattern as the existing "Review" link):**
a "Pending" table (flyer thumbnail, date, submitted location, lineup,
submitter contact, actions) plus a capped "Recent history" table of the
last 50 converted/dismissed submissions (mainly so a dismiss made by
mistake is easy to find and undo via "Restore to pending").

**Conversion is deliberately not a bespoke form.** "Convert to show" just
links straight into the existing `events.new_event(from_gig=<id>)` --
same Add Show form every manually-added show already uses, just
pre-filled from the submission:

- **Venue** defaults to a shared **"DIY" venue** (seeded in `seed.py`,
  `slug="diy"`, deliberately no address/city/state of its own) if one
  exists on the install. Per David's call, the specific address/location
  text a submitter enters (e.g. "123 Elm St, back porch") is **not**
  given its own Event column -- there's nothing sensible to put it in
  that survives every show at a *different* one-off address while still
  reading as "DIY" for site navigation/filtering, so it's copied into the
  **description** field instead (see below), and the admin can freely
  pick a different, real Venue instead if the submitted location
  actually matches one already in the system.
- **Description** is pre-filled with the submitter's name/email, the
  submitted location text verbatim, the submitted genre(s) (if any were
  given), and the full lineup text -- so nothing from the original
  submission is lost once the row itself gets marked converted, even
  though none of that has its own Event column.
- **Image / flyer URL** -- a brand-new field on the Add/Edit Show form
  (previously `Event.image_url` existed on the model but had no form
  field at all, only ever set by scrapers) -- is pre-filled with the
  uploaded flyer's own on-site URL. This "just works" with zero new
  upload-serving code: the flyer file already lives in `app/static/`
  (see "Flyer uploads" below), so its URL is exactly as valid an
  `image_url` value as any scraper-sourced image URL already was.
- **Title** is left blank (deliberately -- there's no reliable way to
  turn a free-text lineup into a good show title automatically) with a
  placeholder hint suggesting one.

The submission is marked `status = "converted"` and `converted_event_id`
is linked **only once the form is actually saved** (a `POST` with a
hidden `from_gig_id` field), not just opened -- abandoning a half-started
conversion leaves the submission sitting in "Pending" as if nothing
happened, which is the right behavior.

**Flyer uploads (`app/utils.py`'s `save_flyer_upload()`/`flyer_url()`):**
the first real file-upload feature in this app -- every other image
anywhere on the site (`Artist.image_url`, `Event.image_url`) is just a
URL string typed/pasted in, with zero upload pipeline. A flyer, though,
genuinely starts as a photo on someone's phone, so this needed real
`request.files` handling: saved under `app/static/uploads/flyers/` with a
fresh random (`uuid4`) filename -- never the visitor's own filename, both
to dodge path-traversal tricks and so two submitters' same-named
"flyer.jpg" can't collide -- restricted to `png`/`jpg`/`jpeg`/`gif`/`webp`
extensions. Saved straight into `app/static/` means Flask's own
static-file serving handles it, no separate download/serve route needed.
The app's `MAX_CONTENT_LENGTH` config (10MB, set in `app/__init__.py`)
caps the whole incoming request before the route even runs, so a
deliberately huge upload gets rejected by Flask/Werkzeug outright rather
than by any code in this app. Uploaded files aren't tracked in git (see
`.gitignore`) -- the upload directory is created on demand
(`os.makedirs(..., exist_ok=True)`), so a fresh clone works fine before
anyone's ever submitted anything.

**Shared admin email (`app/utils.py`'s `send_admin_email()`):** the
email-sending code used to live only in `app/routes/contact.py` as a
module-private `_send_email()`. Pulled out into `app/utils.py` once this
feature needed the exact same mechanism for its own notification, rather
than duplicating it in a second blueprint -- the contact form's behavior
is unchanged, it just calls the shared function now. Sends via
[Resend](https://resend.com)'s HTTPS API (`RESEND_API_KEY`/
`RESEND_FROM_EMAIL`/`CONTACT_EMAIL`, see `.env.example`) -- see
"Hardened against a hung mail server" below for why this isn't direct
SMTP.

`send_admin_email()` also takes optional `attachment_path`/
`attachment_filename` arguments -- `gigs.py`'s notification passes the
submitted flyer's on-disk path (built from `FLYER_UPLOAD_DIR` +
`flyer_filename`) so it lands as a real attachment on the email itself,
not just a link the admin has to click through for. The attachment gets
a human-readable filename (`flyer-<slugified-venue-name>.<ext>`) rather
than exposing the internal uuid-based filename `save_flyer_upload()`
actually saves it under. A missing/unreadable flyer file is treated as
"send with no attachment" rather than failing the whole notification --
the submission itself is already safely saved in the DB by the time this
runs, so a flyer-attachment hiccup shouldn't also take down the email.
The contact form doesn't pass these -- there's no file involved there --
so its behavior is unchanged.

**Hardened against a hung mail server taking down the whole site
(2026-08-17 incident):** a real production outage traced back to
`send_admin_email()` -- back when it used direct Gmail SMTP, the
droplet's connection attempt to `smtp.gmail.com:587` hung for longer
than gunicorn's own request timeout (30s, the default --
`local-music.service` sets no `--timeout` override). Gunicorn responded
by SIGABRT-ing the *entire worker process* mid-request (a
`WORKER TIMEOUT` in the systemd journal), which took down every other
request that worker happened to be handling too -- not just the
"Submit a Show" one that triggered it.

The first fix was a background-thread timeout: run the actual send on a
daemon thread and only wait up to `EMAIL_SEND_TIMEOUT` (15s) via
`thread.join()`, raising a plain `TimeoutError` if it's not done by then
rather than blocking indefinitely. That's still just a normal, catchable
exception, so `gigs.py`/`contact.py`'s existing `try/except` around
every call needed zero changes -- a submitter now got the same "your
show was submitted, but the notification email didn't go out" flash
they'd get for any other send failure, in under a second, instead of the
whole site hanging for 30 seconds and then 500ing.

That fix stopped the crash, but a real submission afterward still hit
the timeout every time, which meant the underlying connection was still
broken, not just occasionally slow. Diagnosing directly on the droplet
(`getent hosts smtp.gmail.com`, `nc -4`/`nc -6 -zv smtp.gmail.com
587`/`465`, `ufw status`, checking DigitalOcean's Cloud Firewall
dashboard) ruled out DNS (fast), general outbound HTTPS (works --
`nc`'d 443 against google.com instantly), any local firewall (`ufw`
inactive), and any DigitalOcean Cloud Firewall (none attached) -- but
direct TCP connections to `smtp.gmail.com` on both port 587 and port 465
hung indefinitely with no response at all, on both IPv4 and IPv6. The
exact cause was never pinned down (suspected datacenter- or Gmail-side
blocking of the droplet's IP range for anti-spam reasons), but every
locally-fixable explanation was conclusively ruled out.

Rather than keep chasing an opaque network block outside this app's
control, `send_admin_email()` was rewritten to stop using SMTP
altogether: it now POSTs to [Resend](https://resend.com)'s HTTPS API
(`_send_admin_email_now()`), reusing port 443, which was already proven
to work. The background-thread/`EMAIL_SEND_TIMEOUT` safety net from the
first fix was kept in place as cheap insurance even though an HTTPS call
is far less likely to hang the way that SMTP connection did. The
external signature of `send_admin_email()` didn't change, so `gigs.py`/
`contact.py` needed no further changes beyond the one-time env var swap
(`MAIL_USERNAME`/`MAIL_PASSWORD` &rarr; `RESEND_API_KEY`, see
`.env.example`). The old Gmail App Password should be revoked from the
Google account once Resend is confirmed working in production, since
it'll otherwise remain a valid, unused credential.

## Add to calendar (`/show/<id>.ics`)

Every show's Event Details page has an "Add to calendar" button next to
"Tickets / more info" (or on its own if the show has no ticket link) --
downloads that one show as a standard `.ics` file that Google Calendar,
Apple Calendar, and Outlook all understand natively, no plugin or
account-linking needed. The same link also sits on each show's card on
the calendar page itself (`calendar.html`'s `.event-link` row, alongside
"Event Details" and "View on venue site"), so a visitor never has to open
the Event Details page just to add a show to their own calendar -- for a
grouped recurring series, it links that one specific occurrence's date,
not the whole series' date range. Built by `app/utils.py`'s
`build_event_ics()`
using the `icalendar` package (already a dependency -- `ical_feed.py`'s
scraper already used it to *read* other venues' feeds; this is the same
library used the other direction, to *write* one) and served by
`main.py`'s `event_ics()` route.

The file is generated fresh on every request straight from the live
`Event` row, not cached or written to disk anywhere -- same principle as
`resolve_image_url()` above: nothing about it (its URL, its UID) should
depend on whatever host or mount-prefix happened to be in play when
someone downloaded it. `Event.start_datetime` is stored as naive local
wall-clock time (see `SITE_TIMEZONE`'s docstring), so it's tagged with
that timezone and converted to a real UTC instant before being handed to
`icalendar` -- a bare UTC timestamp needs no accompanying `VTIMEZONE`
block to be unambiguous, which is one less thing to get wrong than
emitting a raw `America/New_York` `TZID`. A show with no `end_datetime`
set (the Add/Edit Show form has no end-time field, and most scraped venue
feeds don't publish one either) falls back to a 3-hour default
(`DEFAULT_EVENT_DURATION`) rather than leaving `DTEND` unset.

The calendar entry's description includes the show's own description,
price, and ticket link (whichever are set) plus a link back to the show's
own page on the site, so anyone who imported it can always get back to
the full listing. Same visibility rule as the Event Details page itself:
a not-yet-approved show's `.ics` 404s for anyone who isn't logged in as
admin, so a guessable URL can't leak an unvetted show onto someone's
calendar before it's been reviewed.

## Uploading a flyer on the Add/Edit Show form

The manual Add/Edit Show form (`/events/new`, `/events/<id>/edit`) can
now take a flyer image upload directly, not just a pasted URL -- reusing
`save_flyer_upload()`/`flyer_url()`, the exact same upload pipeline
already built for the public "Submit a Show" form (uuid-renamed, saved
under `app/static/uploads/flyers/`, no separate serving route needed).
The form's `enctype="multipart/form-data"` and a new `flyer_image` file
input sit right below the existing "Image / flyer URL" text field, which
is `type="text"` rather than `type="url"` -- a value prefilled from an
upload is a relative path (`/static/uploads/flyers/<uuid>.ext`, see
`flyer_url()`'s docstring below), and `type="url"`'s native browser
validation would reject that outright.

`flyer_url()` deliberately returns a relative URL, not an absolute one --
it used to pass `_external=True` (looked harmless: an absolute URL "just
works" wherever it's rendered), but that baked in whatever host happened
to be serving the request at *upload* time. Uploading locally stored
`http://127.0.0.1:5050/static/uploads/flyers/x.jpg` straight into
`Event.image_url`/`Venue.image_url` -- permanent, and meaningless once
that row got copied to the droplet by `migrate_to_postgres.py`, so the
image silently 404'd there even once the file itself existed (see
"Moving locally-accumulated data to the droplet" below for the matching
fix on that side -- `push_to_droplet.sh`/`deploy_all.sh` now rsync
`app/static/uploads/flyers/` itself, since a relative URL alone doesn't
help if the file was never copied over). A relative URL resolves
correctly against whichever domain actually serves the page either way,
matching how `GigSubmission.flyer_filename` was already designed to work
(see that column's docstring in `models.py`).

Dropping `_external=True` fixed the *host*, but not fully: the droplet
serves this site under a URL prefix (`waveyvibe.dev/localarts`, not the
domain root -- see "Deploying behind Caddy at a sub-path" below), and a
plain `/static/uploads/flyers/x.jpg` computed once at upload time on local
dev (which has no prefix) doesn't carry that prefix when copied to the
droplet's database and rendered there later. `app/utils.py`'s
`resolve_image_url()` -- wired up as the `resolve_image_url` Jinja filter
in `app/__init__.py`, applied everywhere `Event.image_url`/
`Venue.image_url` is rendered as an `<img src>` or prefilled form value
(never `Artist.image_url`, which is always a pasted external URL with no
local-upload path) -- fixes this by re-deriving the URL fresh, via
`flyer_url()`, at *render* time instead of trusting the stored string. An
already-absolute pasted URL (`http://`/`https://`) passes through
unchanged either way.

`events.py`'s `_resolve_image_url()` decides what `Event.image_url` ends
up as: an uploaded file always wins over whatever's in the URL text field
(on the theory that if an admin bothered to pick a file, that's the one
they actually meant to use); the text field is only a fallback for when
no file is chosen this time -- including a blank URL field with no file
selected, which is still how an image gets cleared from a show, exactly
as it worked before this feature existed. Choosing a file in the browser
shows an instant local preview (`URL.createObjectURL()`, no server
round-trip needed just to see it) and clears the text field so there's no
ambiguity about which one will actually be used once saved. A file that
isn't a supported image type (JPG/PNG/GIF/WEBP) doesn't block the save --
unlike the public submission form, where a flyer is required, one here is
always optional, so a bad file just flashes a warning and falls back to
the URL field instead of losing the rest of what was filled in.

**No confirmation email to the submitter.** The submission form validates
the email address is present and looks like an email
(`name@domain.tld`-shaped -- a loose regex check, not a real
deliverability check), and it's stored so David can reach out, but no
automated email is sent back to the submitter on success -- just the
on-page "flagged for review" message. Worth adding later if it turns out
people want a receipt, but wasn't part of the initial ask.

## Venue photos, and the event-image fallback

Venues can now have their own photo too (`Venue.image_url`, added the same
way as `Event.image_url`/`Artist.image_url` -- a plain URL column, plus
the `_COLUMN_MIGRATIONS` entry in `app/__init__.py` for existing SQLite
installs). It's set from the Add/Edit Venue form (`/venues/new`,
`/venues/<id>/edit`), which offers the same pasted-URL-or-file-upload pair
of fields as the Add/Edit Show form -- an uploaded file (`venue_image`)
wins over a pasted URL, a blank URL field with no file clears it, and a
bad file type flashes a warning and falls back to the URL field instead of
losing the rest of the save. The upload logic itself was pulled out of
`events.py`'s `_resolve_image_url()` into a shared
`resolve_uploaded_image_url()` in `app/utils.py` so both forms use the
exact same precedence rules rather than duplicating them; each caller
still gets to customize the file-input name (`flyer_image` vs
`venue_image`) and the "unsupported file type" flash wording.

Once set, a venue's photo shows on its own detail page (`/venues/<id>`),
and -- more usefully -- becomes the fallback image for any of that
venue's shows that don't have their own flyer/image_url: both the
calendar's event cards and a show's own Event Details page check
`event.image_url` first, then `event.venue.image_url`, and only fall all
the way back to the bare site logo (calendar cards) or no image at all
(Event Details) if neither is set. So a venue that bothers to add one
photo of its room gets that photo on every one of its shows that would
otherwise show nothing.

## About page and the fixed venue-caution line

The admin-editable "About this site" copy (`SiteSetting.about_html`, edited
via `/about/edit`) used to show inline on the calendar page, in a
collapsed-by-default `<details>` panel. It now has its own page (`/about`,
linked from the main nav) instead, so the calendar stays focused on the
actual show listings; saving an edit redirects back to `/about` rather than
the calendar.

Separately, one specific line -- "Venues sometimes change set times,
lineups, or cancel shows on short notice, so before you head out, it's
always worth double-checking the details on the venue's own website." --
always shows on both the calendar page and every show's Event Details page,
regardless of what the About page's own content currently says. It's a
fixed Python constant (`VENUE_CAUTION_NOTE` in `app/utils.py`), not part of
the admin-editable `about_html`, specifically so it can't be accidentally
edited away or lost if someone rewrites the About copy from scratch --
`edit_about.html`'s form page says as much, pointing back at
`app/utils.py` if the wording itself ever needs to change.

David edited the About text locally and ran `deploy_all.sh`, but the
live site still showed the old copy afterward. Root cause: `about_html`
lives in the `site_setting` table, and `migrate_to_postgres.py` (which
`deploy_all.sh` calls to carry local data over) only copies the tables
listed in its own `TABLES_IN_ORDER` -- `site_setting` had never been
added to that list, so `git pull`/`sync_schema.py` correctly brought the
droplet's *code and table structure* up to date (which is why the
Contact-menu removal deployed fine -- that was a template change, not a
data edit), but the actual row with the new About text was silently
never copied. Fixed by adding `site_setting` to `TABLES_IN_ORDER` --
see that script's own comments for why this whole class of miss (a new
row-data table quietly not making it into a migration/deploy step) has
happened before, with the tag/association tables added for the local-
artist feature. The immediate fix for David: re-run `deploy_all.sh` (or
just `migrate_to_postgres.py` directly) once his local About edit is in
place again, or simplest of all, just re-enter the text directly via the
droplet's own `/about/edit` page instead of relying on local->droplet
sync for a single-row change.

## Keeping scraped data honest

Scraping isn't just "find new shows" -- venues change times, and sometimes
cancel shows outright, and a re-scrape needs to surface both without either
silently corrupting something a visitor already saw or nuking a real show
because of a one-off scraper hiccup. `run_scrape()` in
`app/scrapers/base.py` handles this with two extra behaviors on top of the
basic create/update logic, both driven by four columns on `Event`:
`last_seen_at`, `missing_streak`, `needs_review`, `review_note`.

- **Changed events:** if an already-*approved* event's scraped time or
  title differs from what's stored, the record is updated immediately (the
  public site should never show stale info) but also flagged
  (`needs_review = True`, with a note like "Time changed from 8:00 PM to
  9:00 PM") so it shows up in the Review page's **Changed** section instead
  of silently mutating something already public. New/still-pending events
  just update quietly -- they haven't been reviewed either way yet.
- **Possibly-cancelled events:** any approved event starting within the
  next 21 days that a scrape's results *don't* include gets its
  `missing_streak` bumped. Two misses in a row (not one, to tolerate a
  single bad page load or a paginated feed's cutoff) and it's auto-hidden
  (`is_approved = False`) and flagged, landing in Review's **Possibly
  cancelled** section already off the public calendar rather than just
  quietly disappearing. Reappearing on a later scrape resets the streak to
  0 with no flag. This check is skipped entirely if a scrape came back with
  *zero* events, since that far more likely means a broken scrape (site
  redesign, selector no longer matching) than every show at that venue
  getting cancelled at once.

Both together mean the daily "scrape all venues" run (`scrape_all.py`, on
the `scrape.timer` schedule) can run unattended and the Review page
(`/events/review`) is the one place to check each morning: **New** (never
seen before), **Changed** (still live, but flagged), and **Possibly
cancelled** (auto-hidden, needs a confirm/restore decision).

## Adding a venue (workflow so far)

The pattern that's worked for both venues added so far:

1. Try fetching the events page. If the content shows up without executing
   JS, it's a `squarespace_json` or `html` candidate; if a plain fetch comes
   back empty/shell-like, it's client-rendered and needs `rendered_html` (or
   a JSON-LD-style module like `elfsight_jsonld.py` if the widget embeds one).
2. Get one real event card's raw markup (via the browser's Inspect -> Copy
   outerHTML) to find the actual repeating item's selector and its
   title/date/link/description sub-selectors -- guessing class names from a
   screenshot or the rendered text alone isn't reliable.
3. Check the date text specifically: a single clean format may need nothing
   more than `date_format`; anything free-typed, multi-day, or year-optional
   may need the kind of parsing logic added for the Academy of Music.
4. Add the venue via the UI (or `seed.py`) with the source type and
   `scrape_config`, then **Test scrape** and iterate before **Run scrape now**.

## Running locally

```bash
cd local-music-poc
python3 -m venv .venv
source .venv/bin/activate        # .venv\Scripts\activate on Windows
pip install -r requirements.txt
playwright install chromium      # one-time browser download, needed for rendered_html venues

python seed.py                   # adds Iron Horse, Parlor Room, Academy of Music, Haze, Luthier's Co-Op, 33 Hawley, The Heavy Culture Cooperative, Quonk, BOMBYX Center for Arts & Equity, CitySpace, Amherst Cinema, One Amber Lane, DIY (catch-all for submitted one-off shows), sample artists/shows
python run.py                    # http://127.0.0.1:5000
```

The SQLite database is created automatically at `instance/local_music.sqlite3`
on first run. Delete that file (and re-run `seed.py`) to start over.

## Deploying

See `DEPLOY.md` for the full DigitalOcean Droplet walkthrough (droplet +
managed Postgres + gunicorn/systemd + nginx/certbot + a systemd timer that
runs `scrape_all.py` four times a day). The app now reads `DATABASE_URL`
and `SECRET_KEY` from the environment (falling back to the local SQLite
file when they're unset), so nothing about local dev above changes.

## Next steps

A few things still worth doing once this is live:

1. Add basic auth in front of the admin routes (`/venues/*`, `/events/new`,
   `/events/review`, etc.) -- right now anyone who finds the URL can
   add/edit/delete. Flagged in `DEPLOY.md` too since it matters more once
   this is actually public.
2. Real error alerting on failed scrapes (the `ScrapeRun` log is there;
   nothing currently surfaces a failure besides looking at the venue page).
3. Once a few more venues are in, decide whether `html_generic.py`'s selector
   config needs a nicer editing UI, or whether most venues turn out to be
   Squarespace/iCal anyway.
4. A real migration tool (Alembic) once the schema needs to change after
   there's real production data in Postgres -- the hand-rolled column
   migration in `app/__init__.py` only patches SQLite.

# Local Music POC (Northampton, MA)

A small Flask app: a calendar of local shows, a venues list with a
pluggable scraper framework, and an artists roster for highlighting
local musicians. Built as a first proof of concept before deploying to
DigitalOcean.

## What's here

- **Calendar** (`/`) -- upcoming approved shows, filterable by venue, event
  type, or a "show only events with local artists" toggle; also spotlights one
  randomly-picked local artist with an upcoming show at the top of the page
  (see "Local artist Genre/Category Tags" below). Public -- this and the
  artist roster below are the only things anonymous visitors see.
- **Artists** (`/artists`) -- roster of local artists, alphabetical by name,
  filterable by Genre Tag, Category Tag, or an "only artists with upcoming
  shows" toggle. Linked to the shows they're playing. Public. The add/edit
  forms (admin-only) have an "Import from Bandcamp" bookmarklet + paste box
  that prefills name/location/bio/photo/embed/tags for review before saving
  -- see "Import from Bandcamp" below for why it's a bookmarklet rather than
  a plain "paste a URL" fetch.
- **Venues** (`/venues`) -- add a venue, tell it how to pull in events (manual,
  Squarespace JSON trick, iCal feed, generic HTML selectors, or headless-browser
  selectors), preview a scrape before importing anything, and see a log of
  recent scrape runs. Admin-only.
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
- **Add show** (`/events/new`) -- manual entry, with a quick-add box for a new artist. Admin-only.
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
  be added, or flag something wrong. Emails `CONTACT_EMAIL` via Gmail SMTP (see below).

## Contact form email

`app/routes/contact.py` sends via Gmail SMTP using an account **App
Password** (not the real Gmail password) -- see `.env.example` for the
exact steps to generate one (turn on 2-Step Verification, then
generate an App Password at https://myaccount.google.com/apppasswords).
Set `MAIL_USERNAME` (the Gmail address) and `MAIL_PASSWORD` (the App
Password); `CONTACT_EMAIL` defaults to davidbgrogan@gmail.com. Without
those set, the form still renders but shows an error instead of quietly
failing to send.

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
  per-venue tuning, and a `default_event_type` tag (see "Event type tags" below).
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
  Also doubles as an artist's Category Tags -- see below.
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
since every show at those is one; Smith College and Quonk
intentionally have no default, since their calendars mix things like
exhibitions, lectures, comedy, dance parties, and tabletop game nights with
actual performances, and each scraped item needs its own call. Visitors
filter the public calendar by tag the same way they already filter by
venue/artist (`?type=<id>`).

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

The Local Artists page (`/artists`) filters by Genre Tag, Category Tag, and an
"only artists with upcoming shows" toggle (`Artist.events.any(Event.start_datetime
>= now)`, via an `EXISTS` subquery rather than a join so an artist with
several upcoming shows isn't listed more than once), always sorted
alphabetically by name regardless of which filters are active.

The calendar's "show only events with local artists" toggle
(`?only_local_artists=1`) works the same way one level up: `Event.artists.any(Artist.is_local.is_(True))`,
added to `_base_query()` in `app/routes/main.py` alongside the existing
venue/type filters -- distinct from the single-artist dropdown
(`?artist=<id>`, still wired up server-side but not currently shown on the
calendar UI).

The homepage's featured-artist spotlight (`_pick_featured_artist()` in
`app/routes/main.py`) picks one random `is_local` artist with at least one
upcoming, approved show, recomputed on every page load rather than a
scheduled rotation -- per David's ask, this keeps it to one query with no
extra scheduling/state to maintain. Renders nothing if there's no eligible
artist yet (e.g. a fresh install with no shows linked to a local artist).
The artist's own upcoming shows (`_upcoming_events_for()`) are listed right
there in the spotlight, not just linked to, so a visitor doesn't have to
click through to see when/where to catch them.

Both the homepage's "About this site" intro and the featured-artist
spotlight are wrapped in a native `<details>`/`<summary>` element rather
than a custom JS toggle -- clicking the header collapses/expands the
section with no JavaScript needed. Both default open (the `open` attribute
in `calendar.html`), so nothing changes for a first-time visitor; it just
lets a returning visitor tuck either one away.

`seed.py` includes two artists exercising this (Comet & the Roadrunners --
Electronica/New Wave; Ruth & the Backroads -- Americana), each with a
placeholder Bandcamp-style `embed_code` and linked to an upcoming sample
show, so all of the above is visible before any real artist data is entered.
Comet & the Roadrunners also has a placeholder `image_url` set, and Ruth &
the Backroads deliberately doesn't, so both states -- an artist photo, and
the fallback to the site logo used everywhere an artist has none -- are
visible in the demo (Local Artists list, artist detail page, and the
homepage's featured-artist spotlight).

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

Each source type is one small module (`squarespace_json.py`, `ical_feed.py`,
`html_generic.py`, `rendered_html.py`, `elfsight_jsonld.py`, `haze_calendar.py`)
exposing `fetch_raw(venue)` and `parse(raw, venue)`. Adding a venue whose site
doesn't fit any of these means writing one new module and registering it in
`app/scrapers/base.py`.

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

python seed.py                   # adds Iron Horse, Parlor Room, Academy of Music, Haze, Luthier's Co-Op, 33 Hawley, The Heavy Culture Cooperative, Quonk, sample artists/shows
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

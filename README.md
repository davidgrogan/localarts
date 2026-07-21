# Local Music POC (Northampton, MA)

A small Flask app: a calendar of local shows, a venues list with a
pluggable scraper framework, and an artists roster for highlighting
local musicians. Built as a first proof of concept before deploying to
DigitalOcean.

## What's here

- **Calendar** (`/`) -- upcoming approved shows, filterable by venue or artist.
  Public -- this and the artist roster below are the only things anonymous
  visitors see.
- **Artists** (`/artists`) -- roster of local artists, linked to the shows they're playing. Public.
- **Venues** (`/venues`) -- add a venue, tell it how to pull in events (manual,
  Squarespace JSON trick, iCal feed, generic HTML selectors, or headless-browser
  selectors), preview a scrape before importing anything, and see a log of
  recent scrape runs. Admin-only.
- **Add show** (`/events/new`) -- manual entry, with a quick-add box for a new artist. Admin-only.
- **Review** (`/events/review`) -- one queue for everything a scrape needs a human
  to look at, in three sections: **New** (`is_approved=False`, never seen before --
  approve or discard), **Changed** (already approved and still live, but the venue's
  site reported a different time/title since -- confirm it's real or unpublish), and
  **Possibly cancelled** (was approved, hasn't shown up in the venue's listing for two
  scrapes in a row, so it's already been auto-hidden -- restore it if it's actually
  still happening, or confirm/discard). See "Keeping scraped data honest" below for
  how that bookkeeping works. Admin-only.
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
  `source_type` (see below), and a free-form `scrape_config` JSON field for
  per-venue tuning.
- `Artist` -- name, genre, hometown, bio, an `is_local` flag (the whole point
  of the site is surfacing local acts, so this drives any "local spotlight" view later).
- `Event` -- a single show: title, start/end time, venue, artists (many-to-many),
  `source` (`manual` vs `scraped`), `external_id` (dedupe key on re-scrape), and
  `is_approved` (the review gate described above).
- `ScrapeRun` -- a log row per scrape attempt: status, counts, and a truncated
  raw-response sample, so a bad scrape is debuggable without re-hitting the venue site.

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
  calendar plugins, Google Calendar, etc.) -- the most robust option when available.
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
  location name -- defaults to `[venue.name]`) and `include_all_locations`
  (bool, skips that filtering).
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

python seed.py                   # adds Iron Horse, Parlor Room, Academy of Music, Haze, sample artists/shows
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

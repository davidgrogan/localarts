# Paradise City Music — Project Brief

A fast-orientation summary of this codebase, written as a companion to
`README.md` (the detailed, ~2,000-line build log) rather than a
replacement for it. If you're pointing a fresh AI coding agent at this
project, read this file first for the shape of the thing, then let the
agent read `README.md` and the actual source for the how-and-why of any
specific piece.

## What this is

A Flask web app for the Northampton/Easthampton, MA area: a public
live-music show calendar plus a venue and local-artist directory, backed
by an admin-only system for managing venues, artists, and shows by hand
*and* a scraper framework that automatically pulls new shows from ~13
different venues' own websites/ticketing widgets. Deployed at
`paradisecitymusic.org` and also mounted at `waveyvibe.dev/localarts` on
the same DigitalOcean droplet.

## Who uses it, and how

- **Public visitors** browse the calendar (week / list-all / month views,
  filterable by genre, "local artists only," and venue), read event
  detail pages, download `.ics` calendar files, browse the venue and
  artist directories, and submit their own show via a public form.
- **The single admin** (session-based login, one username/password pair
  from env vars — this was never built for multiple admin accounts) adds
  and edits venues/artists/events by hand, configures and runs scrapers
  per venue, reviews a queue of scraper-flagged changes/likely
  cancellations, and reviews/approves public show submissions.

## Tech stack

Flask + SQLAlchemy + Jinja2 (server-rendered, minimal client JS — the
Month view hover/tap popup and a couple of localStorage-remembered UI
toggles are the only real client-side logic). SQLite in local dev,
Postgres in production. Playwright (headless Chromium) for the venues
whose listings are JS-rendered. Resend's HTTP API for outbound email
(contact form, gig-submission notifications). No frontend build step —
`app/static/style.css` is hand-written, no bundler.

## Data model (`app/models.py`)

- **Venue** — name/address/website, plus scraping config: `source_type`
  (a string key into the scraper plugin registry — see below),
  `scrape_config` (freeform JSON, shape defined by whichever module
  `source_type` selects), `default_event_type` (auto-tag newly scraped
  events, left unset for mixed-use venues), `is_active`.
- **Event** — belongs to a Venue; `custom_venue_name` overrides the
  displayed location for one-off bookings (festival stages, a venue's
  named sub-room) without needing a fake Venue row; `source` is
  `manual` | `scraped` | `gig_submission`; `is_approved` gates public
  visibility; `needs_review`/`review_note`/`missing_streak` back the
  admin Review queue (see Scraper framework below); many-to-many with
  Artist and EventType.
- **Artist** — local-artist directory entries; many-to-many `genre_tags`
  and `category_tags` (both reuse `GenreTag`/`EventType` respectively);
  `embed_code` for a pasted Bandcamp embed; `is_local` flag.
- **EventType** — the tag vocabulary shared by Event ("Music," used to
  restrict the public calendar to real shows — see
  `MUSIC_ONLY_CATEGORY_NAME` in `app/routes/main.py`) and Artist
  ("category" tags, same table, different relationship).
- **GenreTag** — Artist-only genre vocabulary (separate from Event.genre,
  which is a free-text string set by scrapers).
- **ScrapeRun** — one row per scrape attempt, log of what happened.
- **GigSubmission** — the public "Submit your show" queue; `status`
  pending/approved/dismissed; `converted_event_id` once turned into a
  real Event.
- **SiteSetting** — singleton row holding the admin-editable About page
  HTML.

## Scraper framework (`app/scrapers/`) — the load-bearing subsystem

Every venue picks a `source_type`; each source_type is one small module
exposing `fetch_raw(venue) -> str` and `parse(raw, venue) -> list[ScrapedEvent]`,
orchestrated by `run_scrape()` in `base.py`. This plugin shape is the
main piece of architecture worth preserving in any rebuild — venue
websites are wildly inconsistent (plain HTML, Squarespace JSON, iCal
feeds, three different JS-widget products, two bespoke ticketing
platforms), and every one of them needed either a different fetch
strategy, a different parser, or both.

**If you're rebuilding this with an AI agent, don't hand it a clean-room
spec for this part — hand it `README.md`'s "Scraper framework" section
and the actual module code.** That section is a debugging diary of real,
non-obvious problems (a plain `requests.get()` silently blocked by
TLS/header fingerprinting; a headless browser serving stale content until
`navigator.webdriver` was hidden; multi-fragment date text getting glued
into unparseable strings; a listing with no year in its dates at all;
introspection-disabled GraphQL APIs) that would very likely get
rediscovered one at a time, expensively, by any fresh attempt that
started from an abstract description instead of this history.

The **Review queue** (admin-facing) is the other piece worth calling out:
`run_scrape()` never silently overwrites an already-public event's time
or title, and never silently deletes an already-public event that
stopped appearing in a feed — both get flagged for a human to confirm
first. This is a deliberate trust boundary between "the scraper found
something new" (auto-published or not, per `approve_new`) and "the
scraper wants to change something a visitor may have already seen"
(always held for review).

## Integrations & required credentials

- `SECRET_KEY`, `ADMIN_USERNAME`, `ADMIN_PASSWORD_HASH` — app auth.
- `DATABASE_URL` — Postgres in production, defaults to local SQLite.
- `RESEND_API_KEY` — outbound email (contact form, gig submissions).
- Playwright's Chromium binary (`playwright install chromium`) — required
  for any venue using `rendered_html`/`elfsight_jsonld`/`ludus`/`venuepilot`
  source types.
- Droplet SSH + Postgres password (`deploy/push_to_droplet.env`) — used by
  `push_to_droplet.sh`/`deploy_all.sh`, not read by the app itself.

Full list with descriptions: `.env.example`.

## Deployment shape

Single DigitalOcean droplet, gunicorn + systemd, Caddy reverse-proxying
this app at a sub-path (`/`) alongside an unrelated static site on the
same domain — which is why `wsgi.py` wraps the app in `ProxyFix` honoring
`X-Forwarded-Prefix`, and why `SESSION_COOKIE_PATH`/`NAME` are explicitly
set (default cookie behavior would collide with the sibling site's own
session). `deploy_all.sh` is the one-command path: commit+push code,
`git pull` + `pip install` + `sync_schema.py --apply` (additive-only
schema sync, never drops/narrows a column) + service restart on the
droplet, then rsync uploaded flyer images and migrate local data over.
Full walkthrough: `DEPLOY.md`.

## Recurring gotchas already solved (worth not re-discovering)

- **Timezone**: always compare against `app/utils.py`'s `local_now()`,
  never `datetime.utcnow()` — the app stores naive local wall-clock
  times throughout, not UTC.
- **Bot detection**: several venues' sites/ticketing platforms silently
  block or tarpit requests that look automated. Fixes applied, in order
  of how much force they need: a realistic browser `User-Agent` first
  (cheapest, works surprisingly often); if that's not enough, switch that
  venue to the Playwright-based `rendered_html` fetch with
  `--disable-blink-features=AutomationControlled` and a `navigator.webdriver`
  override.
- **Free-typed/inconsistent date text**: `html_generic.py` has fuzzy-date
  fallbacks for missing years (carry the last year seen on the page
  forward), multi-day ranges ("Fri Jul 24 and Sat Jul 25"), and
  dash/pipe-separated start-end time ranges (a naive fuzzy parser grabs
  the *end* time otherwise). `venuepilot.py` has a separate heuristic
  for pages that never show a year at all.
- **Image/link portability**: the app can be mounted at different
  sub-paths on different domains (see Deployment above), so stored
  image URLs are kept relative and re-prefixed at render time
  (`resolve_image_url` filter) rather than baked absolute at scrape/
  upload time.
- **HTML entities**: some scraped sources double-encode entities
  (`&amp;amp;`) — decoded explicitly rather than trusted as-is.

## Where the real spec already lives

This project's `README.md` is not a changelog to skim past — it's the
actual specification, written incrementally as each feature and bug fix
landed, including the *reasoning* (not just the result) behind
non-obvious calls: which venues deliberately get no default event-type
tag and why, why tags are never touched by re-scrapes once an admin sets
them, why the Review queue requires two missed scrapes in a row before
treating a show as cancelled, and so on. The single highest-value thing
you can hand a new AI coding agent working on this project is: this
brief for orientation, then `README.md` + the actual source for anything
it's about to touch.

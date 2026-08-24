"""Scraper for Amherst Cinema (amherstcinema.org), a Drupal site running
what looks like the "Show Event" content type against the Views module.

The homepage's own showtimes calendar is presented as a hover-to-preview
widget, but that's purely a UI skin -- confirmed via live DOM inspection
that every day cell links to its own real, plain server-rendered URL:

    https://amherstcinema.org/calendar/month/<YYYY-MM-DD>

No headless browser needed at all (unlike Quonk/Heavy Culture/CitySpace/
Bombyx) -- a plain requests.get() of that URL returns the exact same
markup the hover popup shows, one <div class="views-row"> per film
playing that day:

    <div class="views-row">
      <div class="series">Big Screen Classics</div>          <!-- often empty -->
      <div class="title"><a href="https://amherstcinema.org/films-and-events/<slug>">Late Fame</a></div>
      <div class="times">
        <div class="views-view-fields">
          <a href="<agileticketing ticket url>"><span class="date-display-single">2:00 pm</span></a>
        </div>
        <!-- one .views-view-fields per showtime that day -->
      </div>
    </div>

Since a single film can run 3-4 showtimes a day for a week or more, this
follows David's explicit call (see the conversation this was built from)
to keep the data model simple: **one Event row per film per day**, not
one row per showtime. All of that day's times get folded into
Event.description as a bullet-free "Showtimes: 2:00 pm | 4:55 pm | ..."
line, and Event.start_datetime is set to the *first* showing (so day
sorting and "is this upcoming" checks behave correctly) -- the tradeoff
being that the calendar card itself only shows that first time; the full
list only surfaces via the description tooltip/detail page. That's a
known, deliberate simplification, not an oversight.

Each film also has its own detail page (the same URL the title link
above points at) with richer, mostly-static metadata that's wasteful to
re-fetch on every single day/showtime row: a poster image, runtime,
director, MPAA rating, release year, and a synopsis. Confirmed classes,
via live DOM inspection of https://amherstcinema.org/films-and-events/late-fame:

    .field-name-field-image-front img   -- poster (already an absolute URL)
    .field-name-field-duration          -- "96 mins."
    .field-name-field-director          -- "Directed by Kent Jones"
    .field-name-field-rating            -- "NR"
    .field-name-field-year              -- "2026"
    .field-name-body                    -- synopsis (Drupal's standard body field)

parse() fetches each unique film's detail page exactly once per scrape
run (cached in a plain dict keyed by slug), no matter how many different
days that film shows up on within the scraped window -- same
one-extra-request-per-*item*, not per-occurrence, spirit as
html_generic.py's description_from_link, just applied at a coarser grain
since here the "item" (a film) legitimately spans many day-page rows.

Venue scrape_config keys:
    days_ahead -- optional int (default 21, matching base.py's own
                  MISSING_CHECK_WINDOW_DAYS) -- how many consecutive days
                  forward from today to fetch day-pages for. Confirmed via
                  live browsing that the cinema publishes at least this far
                  out in practice (every day through the end of the current
                  month, and into the first days of the next, already had
                  a full schedule when this was written), but there's no
                  guarantee of that holding forever, hence configurable.
    user_agent -- optional UA override, same convention as html_generic.py.

Event.genre is set from the day-page's own "series" label when present
(e.g. "Big Screen Classics") -- distinguishing a revival/series screening
from a regular new release, the same way other scrapers use genre for a
music/comedy sub-category. Event.ticket_url points at the film's own
detail page (not one specific showtime's AgileTicketing link), since a
single Event row can't represent several different showtimes' separate
purchase links -- the detail page itself shows every remaining date/time
with its own "buy" button.
"""
import json
import os
import re
from datetime import timedelta
from urllib.parse import urljoin, urlsplit

import requests
from bs4 import BeautifulSoup
from dateutil import parser as dateparser

from app.scrapers.base import ScrapedEvent, ScrapeError
from app.utils import ALLOWED_FLYER_EXTENSIONS, FLYER_UPLOAD_DIR, local_now, slugify

USER_AGENT = "Mozilla/5.0 (compatible; LocalMusicSitePOC/0.1)"

DEFAULT_DAYS_AHEAD = 21

# Inserted between each fetched day-page's raw HTML by fetch_raw() so
# parse() can recover which calendar date each chunk belongs to without
# re-parsing the page's own "MM/DD/YYYY" <h1> text (ambiguous month/day
# order) -- a plain, unambiguous ISO date travels with the markup instead.
_PAGE_MARKER_RE = re.compile(r"<!--AMHERST_PAGE:(\d{4}-\d{2}-\d{2})-->")


def _config(venue):
    try:
        return json.loads(venue.scrape_config or "{}")
    except json.JSONDecodeError:
        return {}


def fetch_raw(venue):
    if not venue.events_url:
        raise ScrapeError("Venue has no events_url configured.")

    config = _config(venue)
    days_ahead = int(config.get("days_ahead") or DEFAULT_DAYS_AHEAD)
    user_agent = config.get("user_agent") or USER_AGENT

    base = venue.events_url.rstrip("/")
    today = local_now().date()

    chunks = []
    for offset in range(days_ahead):
        day = today + timedelta(days=offset)
        url = f"{base}/{day.isoformat()}"
        try:
            resp = requests.get(url, headers={"User-Agent": user_agent}, timeout=15)
            resp.raise_for_status()
        except requests.RequestException as exc:
            if offset == 0:
                # The very first day (today) failing outright means
                # something's genuinely wrong (wrong URL, site down) --
                # any later day is independent, so one bad day shouldn't
                # sink the whole window.
                raise ScrapeError(f"Failed to fetch {url}: {exc}") from exc
            continue
        chunks.append(f"<!--AMHERST_PAGE:{day.isoformat()}-->{resp.text}")

    return "\n".join(chunks)


_CONTENT_TYPE_EXTENSIONS = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/gif": "gif",
    "image/webp": "webp",
}


def _download_poster(image_url, slug, user_agent):
    """Downloads a film's poster and re-hosts it under our own
    /static/uploads/flyers/ (the same on-disk storage app/utils.py's
    FLYER_UPLOAD_DIR/save_flyer_upload() already use for admin-uploaded
    flyers and venue photos), returning that local URL instead of
    Amherst Cinema's own.

    This exists because amherstcinema.org hotlink-protects these images:
    confirmed via a real cross-origin test that the exact same poster URL
    loads fine as a direct browser navigation (no Referer header at all)
    but fails to load as an <img> embedded on any *other* site (any
    Referer naming a foreign origin) -- exactly David's real symptom,
    reproduced from this project's own calendar page. A plain
    requests.get() here is a server-to-server fetch, not a browser
    rendering an embedded cross-origin image, so it isn't subject to that
    same check -- downloading the bytes once and serving them from our
    own domain sidesteps the problem rather than fighting it.

    filename is deterministic (slugify(slug) + extension), not a random
    uuid like save_flyer_upload() uses for a one-off admin upload --
    deliberately so a re-scrape of the same film *overwrites* its
    existing local copy instead of leaving an ever-growing pile of
    orphaned files behind on every single scrape run (this function runs
    once per unique film per scrape, indefinitely, unlike a one-time
    admin upload).

    Best-effort, like every other helper in this module: returns None on
    any failure (network error, unrecognized image type) rather than
    raising -- a missing poster shouldn't sink the whole scrape, and
    Event.image_url simply falling back to the venue photo/site logo (see
    calendar.html) is a fine degraded outcome.
    """
    try:
        resp = requests.get(image_url, headers={"User-Agent": user_agent}, timeout=15)
        resp.raise_for_status()
    except requests.RequestException:
        return None

    ext = urlsplit(image_url).path.rsplit(".", 1)[-1].lower()
    if ext not in ALLOWED_FLYER_EXTENSIONS:
        content_type = resp.headers.get("Content-Type", "").split(";")[0].strip()
        ext = _CONTENT_TYPE_EXTENSIONS.get(content_type)
    if ext not in ALLOWED_FLYER_EXTENSIONS:
        return None

    os.makedirs(FLYER_UPLOAD_DIR, exist_ok=True)
    filename = f"amherst-{slugify(slug)}.{ext}"
    with open(os.path.join(FLYER_UPLOAD_DIR, filename), "wb") as f:
        f.write(resp.content)

    # Matches _FLYER_URL_MARKER's exact shape (app/utils.py) so
    # resolve_image_url() recognizes this as a locally-uploaded image and
    # re-derives the correct URL at *render* time -- portable across
    # installs with different mount prefixes, same as any other upload.
    return f"/static/uploads/flyers/{filename}"


def _fetch_film_detail(url, slug, user_agent):
    """Best-effort fetch of a film's own detail page for the metadata
    that isn't on the day-page listing -- poster, runtime, director,
    rating, year, synopsis. Never raises: a failure here (network error,
    404, unexpected markup) just means that film's Event rows go out with
    less enrichment, not that the whole scrape fails over it."""
    try:
        resp = requests.get(url, headers={"User-Agent": user_agent}, timeout=15)
        resp.raise_for_status()
    except requests.RequestException:
        return {}

    soup = BeautifulSoup(resp.text, "html.parser")

    def field_text(css_class):
        el = soup.select_one(f".{css_class}")
        return el.get_text(strip=True) if el else None

    image_url = None
    image_el = soup.select_one(".field-name-field-image-front img")
    if image_el and image_el.has_attr("src"):
        remote_image_url = urljoin(url, image_el["src"])
        image_url = _download_poster(remote_image_url, slug, user_agent)

    synopsis_el = soup.select_one(".field-name-body")
    synopsis = synopsis_el.get_text(separator=" ", strip=True) if synopsis_el else None

    return {
        "image_url": image_url,
        "duration": field_text("field-name-field-duration"),
        "director": field_text("field-name-field-director"),
        "rating": field_text("field-name-field-rating"),
        "year": field_text("field-name-field-year"),
        "synopsis": synopsis,
    }


def _build_description(times, detail):
    parts = [f"<p>Showtimes: {' | '.join(times)}</p>"]
    meta_bits = [
        b for b in (detail.get("duration"), detail.get("director"), detail.get("rating"), detail.get("year"))
        if b
    ]
    if meta_bits:
        parts.append(f"<p>{' | '.join(meta_bits)}</p>")
    if detail.get("synopsis"):
        parts.append(f"<p>{detail['synopsis']}</p>")
    return "".join(parts)


def parse(raw, venue):
    config = _config(venue)
    user_agent = config.get("user_agent") or USER_AGENT

    # Split on the markers fetch_raw() inserted -- re.split with a
    # capturing group interleaves [junk_before_first_marker, date1,
    # html1, date2, html2, ...], so pairing tokens[1::2] with
    # tokens[2::2] recovers each (date, html chunk).
    tokens = _PAGE_MARKER_RE.split(raw)
    day_chunks = list(zip(tokens[1::2], tokens[2::2]))

    film_cache = {}
    events = []
    seen_external_ids = set()

    for date_str, html_chunk in day_chunks:
        soup = BeautifulSoup(html_chunk, "html.parser")

        for row in soup.select(".views-row"):
            title_el = row.select_one(".title a")
            if title_el is None or not title_el.has_attr("href"):
                continue
            title = title_el.get_text(separator=" ", strip=True)
            if not title:
                continue
            detail_url = urljoin(venue.events_url, title_el["href"])
            # The film's own detail-page slug (last non-empty path
            # segment) is a stable per-film key -- used both to dedupe
            # the detail-page fetch across every day it plays, and as
            # part of this Event row's external_id.
            slug = [seg for seg in detail_url.rstrip("/").split("/") if seg][-1]

            time_els = row.select(".times .date-display-single")
            times = [t.get_text(strip=True) for t in time_els]
            if not times:
                continue

            try:
                start_dt = dateparser.parse(f"{date_str} {times[0]}")
            except (ValueError, OverflowError):
                continue

            series_el = row.select_one(".series")
            series_text = series_el.get_text(strip=True) if series_el else ""
            genre = series_text or None

            if slug not in film_cache:
                film_cache[slug] = _fetch_film_detail(detail_url, slug, user_agent)
            detail = film_cache[slug]

            external_id = f"{slug}-{date_str}"
            if external_id in seen_external_ids:
                continue
            seen_external_ids.add(external_id)

            events.append(
                ScrapedEvent(
                    title=title,
                    start_datetime=start_dt,
                    description=_build_description(times, detail),
                    ticket_url=detail_url,
                    genre=genre,
                    image_url=detail.get("image_url"),
                    external_id=external_id,
                )
            )

    events.sort(key=lambda e: e.start_datetime)
    return events

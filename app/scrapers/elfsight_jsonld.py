"""Headless-browser scraper for Elfsight "Event Calendar" widgets that
reads each event's embedded schema.org JSON-LD instead of scraping the
widget's visible, CSS-selector-driven DOM.

Why this exists (discovered on the Iron Horse's real rendered page):
Elfsight's visible event cards use long hashed styled-components class
names (e.g. `Grid__GridItemContainer-sc-36c876de-2`) that can change
between widget versions, and -- more importantly -- the visible date
elements only ever show a month/day and a day-of-week ("Jul 17", "Fri")
with **no year anywhere in the visible markup**. But each event card also
embeds a `<script type="application/ld+json">` tag with a complete
schema.org Event object: name, startDate/endDate (full ISO dates,
year included), description, image, and a location name/address. That's
strictly better data, reached without hunting for brittle selectors, so
this module fetches the page the same way rendered_html.py does (a real
headless Chromium load, since the widget is entirely client-rendered)
and reads the JSON-LD for everything it can.

Genre/category isn't in the JSON-LD at all, confirmed absent from real
samples -- it's read from the visible DOM instead, using a "find the
nearest match inside this event's own card" approach:
    <div aria-label="Event category" class="Category__CategoryItem-...
         eapp-events-calendar-grid-item-category">Folk Rock</div>
That class is one of Elfsight's stable, non-hashed helper classes (same
family as `eapp-events-calendar-grid-item-name`), so it survives widget
version bumps that reshuffle the hashed styled-components classes
around it. Some events carry more than one category tag inside that
same element (e.g. "Jazz" + "Tribute" + "Soul" as separate child nodes);
those are joined with ", " rather than left to run together.

Date handling turned out to need the same "don't trust the JSON-LD"
treatment as genre, just for a subtler reason. Some events' `startDate` is
a real UTC timestamp ("2026-07-22T00:00:00Z") that needs converting back
to Eastern time; that part's straightforward. But other events carry a
*naive* value with no timezone marker at all (e.g. "2026-07-25T00:00:00")
that is nonetheless already wrong by a day at the source -- there's no
offset to detect or convert, the date component itself just doesn't match
what the venue's own page shows (confirmed directly: the visible date
badge says "Jul 24" for an event whose JSON-LD parses to the 25th). A
syntactically date-only value and a mis-shifted one are indistinguishable
once parsed, so trusting the JSON-LD's date at all is a dead end.

The fix: never take the calendar date from the JSON-LD. Instead, read the
actual month/day straight from the visible date badge --
    <div class="DateElement__Month-... eapp-events-calendar-date-element-month">Jul</div>
    <div class="DateElement__Day-... eapp-events-calendar-date-element-day">24</div>
-- which is exactly what real visitors see on the venue's own page, and
combine it with the *year* from the JSON-LD (the only piece that field is
still trusted for -- a UTC/local day-boundary shift essentially never
changes the year, barring a Dec 31/Jan 1 show). Time of day comes from the
visible time element the same way:
    <div aria-label="Event time: 7:00 PM"
         class="Time__TimeComponent-... eapp-events-calendar-time-time">
      7:00 PM
    </div>
-- except that this element doesn't always contain *just* that text.
Confirmed on the droplet (Iron Horse, all events scraping to midnight):
the widget sometimes renders a nested UTC-offset annotation inside the
same element, e.g. `7:00 PM<span> UTC-4</span>`, seemingly whenever it
detects the rendering browser's system timezone doesn't match the venue's
-- your Mac's system clock is already America/New_York so the widget
never bothered clarifying, but a fresh Ubuntu droplet defaults to UTC, so
it started showing up there. Reading the element's full text naively
picked up "7:00 PM UTC-4", which doesn't match a plain time format at
all -- _find_time_of_day now regex-extracts just the "7:00 PM" portion
instead of assuming the element's text is exactly that, so it's tolerant
of this annotation appearing, changing, or not appearing at all.

Also handles a quirk specific to the Iron Horse: its Elfsight feed is
shared across several physically distinct "Parlor Room Collective"
venues (Black Birch Vineyard, Musician's Workshop, etc. in addition to
the Iron Horse itself and The Parlor Room). Each event's JSON-LD
`location.name` says which physical venue it's actually at, so this
scraper filters events down to the ones matching *this* Venue row
instead of importing every show on the shared feed under one venue.

Venue scrape_config keys:
    wait_for_selector, wait_ms  -- same meaning as rendered_html.py, used
                                    while fetching the page.
    location_match  -- optional list of substrings (case-insensitive)
                        matched against each event's JSON-LD
                        `location.name`. Defaults to [venue.name] if
                        omitted, e.g. "Iron Horse Music Hall" will match
                        a location name of "The Iron Horse Music Hall".
    include_all_locations  -- optional bool; if true, skips location
                               filtering entirely and imports every event
                               found on the page (useful for a venue
                               whose feed genuinely only lists its own
                               shows).
"""
import json
import re
from datetime import datetime as dt
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup
from dateutil import parser as dateparser

# Both Iron Horse and The Parlor Room are in Northampton, MA -- used to
# convert any timezone-aware JSON-LD timestamp back to local wall-clock
# time (see module docstring for why that matters).
_VENUE_TZ = ZoneInfo("America/New_York")

from app.scrapers.base import ScrapedEvent
from app.scrapers.rendered_html import fetch_raw as _fetch_rendered  # noqa: F401 (re-exported)

fetch_raw = _fetch_rendered


def _config(venue):
    try:
        return json.loads(venue.scrape_config or "{}")
    except json.JSONDecodeError:
        return {}


def _location_name(raw_location):
    if isinstance(raw_location, dict):
        return raw_location.get("name", "") or ""
    if isinstance(raw_location, str):
        return raw_location
    return ""


def _image_url(raw_image):
    # schema.org allows "image" to be a plain URL string, an ImageObject
    # dict with its own "url", or a list of either -- Elfsight's cards use
    # a plain string in practice, but handle the other shapes too.
    if isinstance(raw_image, list):
        raw_image = raw_image[0] if raw_image else None
    if isinstance(raw_image, dict):
        return raw_image.get("url")
    if isinstance(raw_image, str):
        return raw_image
    return None


_CATEGORY_SELECTOR = ".eapp-events-calendar-grid-item-category"
_TIME_SELECTOR = ".eapp-events-calendar-time-time"
_TIME_FORMAT = "%I:%M %p"  # e.g. "7:00 PM"
# Matches just the "7:00 PM" portion of the time element's text. Needed
# because that element sometimes contains a nested <span> with a UTC
# offset annotation (e.g. "7:00 PM<span> UTC-4</span>", observed on the
# droplet -- see module docstring), which get_text() would otherwise
# concatenate into "7:00 PM UTC-4" and fail strptime entirely. Extracting
# just the time substring is robust to that suffix appearing, changing,
# or not appearing at all.
_TIME_RE = re.compile(r"\d{1,2}:\d{2}\s*[AaPp][Mm]")
_MONTH_SELECTOR = ".eapp-events-calendar-date-element-month"
_DAY_SELECTOR = ".eapp-events-calendar-date-element-day"
# Stable (non-hashed) class marking the boundary of a single event's card --
# bounding these lookups to this container, rather than walking an
# arbitrary number of ancestors up, matters: past this point the DOM
# contains *sibling* event cards too, and an unbounded walk-up will happily
# (and wrongly) pick up another event's category/time once it reaches a
# shared ancestor like the widget's outer grid.
_CARD_CONTAINER_CLASS = "eapp-events-calendar-grid-item-container"


def _iter_event_objects(soup):
    """Yield (event_dict, script_tag) for every schema.org Event embedded
    anywhere on the page, whether a script tag holds a single Event
    object, a bare list of them, or a wrapping @graph. The script tag is
    returned alongside the parsed data so callers can also pull genre/
    category text out of the surrounding visible DOM (see module
    docstring -- that field isn't in the JSON-LD)."""
    for script in soup.find_all("script", type="application/ld+json"):
        text = script.string or script.get_text()
        if not text or not text.strip():
            continue
        try:
            data = json.loads(text)
        except (json.JSONDecodeError, TypeError):
            continue

        candidates = []
        if isinstance(data, list):
            candidates = data
        elif isinstance(data, dict) and isinstance(data.get("@graph"), list):
            candidates = data["@graph"]
        elif isinstance(data, dict):
            candidates = [data]

        for item in candidates:
            if isinstance(item, dict) and item.get("@type") == "Event":
                yield item, script


def _find_in_card(script, selector, separator=""):
    """Find an element matching `selector` belonging to *this* event's
    card -- for fields that live in the visible DOM alongside the
    <script> tag but aren't reachable from the JSON-LD itself. Bounded to
    the nearest ancestor that marks a single event card's boundary, so it
    can't pick up a sibling event's data instead."""
    card = script.find_parent(
        class_=lambda c: bool(c) and _CARD_CONTAINER_CLASS in c.split()
    )
    search_root = card if card is not None else script.parent
    if search_root is None:
        return None
    found = search_root.select_one(selector)
    if not found:
        return None
    text = found.get_text(separator=separator, strip=True)
    return text or None


def _find_category(script):
    # separator=", " matters: some events carry multiple category tags as
    # separate child nodes inside the one matched element (e.g. "Jazz",
    # "Tribute", "Soul"), and BeautifulSoup's default get_text() would
    # otherwise run them together as "JazzTributeSoul".
    return _find_in_card(script, _CATEGORY_SELECTOR, separator=", ")


def _to_local(parsed_dt):
    """Convert a timezone-aware datetime to America/New_York wall-clock
    time and drop the tzinfo (the DB stores naive datetimes). Naive
    values -- a genuinely date-only JSON-LD string with no offset -- are
    returned unchanged, since there's nothing to convert."""
    if parsed_dt.tzinfo is not None:
        return parsed_dt.astimezone(_VENUE_TZ).replace(tzinfo=None)
    return parsed_dt


def _find_time_of_day(script):
    """Return a `datetime.time` parsed from the card's visible time text
    (e.g. "7:00 PM"), or None if there's no such element or no time-like
    substring can be found in it. Extracts the time via regex rather than
    parsing the element's full text directly, since that text sometimes
    carries a trailing UTC-offset annotation (see _TIME_RE above)."""
    text = _find_in_card(script, _TIME_SELECTOR)
    if not text:
        return None
    match = _TIME_RE.search(text)
    if not match:
        return None
    try:
        return dt.strptime(match.group(0), _TIME_FORMAT).time()
    except ValueError:
        return None


def _find_month_day(script):
    """Return a (month, day) tuple parsed from the card's visible date
    badge (e.g. month="Jul", day="24"), or None if either piece is
    missing/unparseable. This is the authoritative source for the
    calendar date -- see module docstring for why the JSON-LD's date
    can't be trusted for this."""
    month_text = _find_in_card(script, _MONTH_SELECTOR)
    day_text = _find_in_card(script, _DAY_SELECTOR)
    if not month_text or not day_text:
        return None
    try:
        month = dt.strptime(month_text, "%b").month
        day = int(day_text)
    except ValueError:
        return None
    return month, day


def parse(raw, venue):
    soup = BeautifulSoup(raw, "html.parser")
    config = _config(venue)

    include_all = bool(config.get("include_all_locations"))
    match_terms = config.get("location_match") or [venue.name]
    match_terms = [t.lower() for t in match_terms if t]

    events = []
    seen_external_ids = set()

    for item, script in _iter_event_objects(soup):
        name = (item.get("name") or "").strip()
        start_raw = item.get("startDate")
        if not name or not start_raw:
            continue

        location_name = _location_name(item.get("location"))
        if not include_all and match_terms:
            if not any(term in location_name.lower() for term in match_terms):
                continue

        try:
            raw_start = dateparser.parse(start_raw)
        except (ValueError, OverflowError):
            continue

        # The JSON-LD's date component isn't trustworthy (see module
        # docstring) -- only its year is used. Month/day come from the
        # visible date badge, which is exactly what the venue's own page
        # shows. Fall back to the (timezone-converted) JSON-LD date only
        # if the badge is missing for some reason.
        month_day = _find_month_day(script)
        if month_day is not None:
            month, day = month_day
        else:
            local_raw = _to_local(raw_start)
            month, day = local_raw.month, local_raw.day

        time_of_day = _find_time_of_day(script)
        hour = time_of_day.hour if time_of_day is not None else 0
        minute = time_of_day.minute if time_of_day is not None else 0

        try:
            start_dt = dt(raw_start.year, month, day, hour, minute)
        except ValueError:
            continue

        end_dt = None
        end_raw = item.get("endDate")
        if end_raw:
            try:
                end_raw_dt = dateparser.parse(end_raw)
                end_dt = dt(end_raw_dt.year, month, day, hour, minute)
            except (ValueError, OverflowError):
                end_dt = None

        external_id = f"{name}-{start_dt.isoformat()}"
        if external_id in seen_external_ids:
            # Elfsight widgets sometimes render a duplicate (desktop +
            # mobile) copy of the same card in the DOM.
            continue
        seen_external_ids.add(external_id)

        # Elfsight's JSON-LD rarely includes a per-event "url" -- its cards
        # open an in-widget lightbox rather than linking to a distinct page.
        # Fall back to the venue's own events page so there's always
        # somewhere for a visitor to click through to on the venue's site.
        ticket_url = item.get("url") or venue.events_url or None

        events.append(
            ScrapedEvent(
                title=name,
                start_datetime=start_dt,
                end_datetime=end_dt,
                description=item.get("description") or "",
                ticket_url=ticket_url,
                # Genre/category isn't in the JSON-LD -- it's read from the
                # visible "Category" element near this event's <script>
                # tag instead (see module docstring and _find_category).
                genre=item.get("genre") or _find_category(script),
                image_url=_image_url(item.get("image")),
                external_id=external_id,
            )
        )

    events.sort(key=lambda e: e.start_datetime)
    return events

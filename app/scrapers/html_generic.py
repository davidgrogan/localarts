"""Generic CSS-selector-based HTML scraper -- the fallback for venue
sites that aren't Squarespace and don't publish an iCal feed.

Since every venue's markup is different, this one is driven entirely
by `venue.scrape_config`, a JSON object with:

    {
      "item_selector": "CSS selector matching each event block",
      "title_selector": "CSS selector *within* an item, for the title",
      "date_selector": "CSS selector *within* an item, for the date/time text",
      "date_format": "optional strptime format, e.g. '%B %d, %Y %I:%M %p'
                       -- if omitted, dateutil's fuzzy parser is used",
      "link_selector": "optional CSS selector *within* an item, for the
                         ticket/detail link (defaults to the title
                         selector's own <a> if present)",
      "description_selector": "optional CSS selector *within* an item,
                                for a short description/presenter line",
      "genre_selector": "optional CSS selector *within* an item, for a
                          genre/category tag's text",
      "image_selector": "optional CSS selector *within* an item, for an
                          <img> to use as the event's image (reads its
                          src attribute; relative URLs are resolved
                          against the venue's website_url)",
      "page_param": "optional query-string param name for pagination
                      (e.g. 'page') -- if set, fetch_raw fetches pages
                      0..max_pages-1 by appending ?<page_param>=<n> to
                      events_url and concatenates the raw HTML",
      "max_pages": "how many pages to fetch when page_param is set
                    (default 1 -- i.e. no pagination)"
    }

This only works well on server-rendered (non-JS) pages. If a venue's
listing is client-rendered like Squarespace/Wix/React sites often are,
plain requests+BeautifulSoup will see an empty shell -- that's exactly
the case squarespace_json.py exists to handle differently. Use the
scrape preview page to check the raw HTML sample before assuming this
source type will work for a given venue.

Fuzzy date parsing (no `date_format` given) does two extra things beyond
a plain dateutil call, both driven by a real-world case (the Academy of
Music's event calendar, aomtheatre.com) where the date text is a free-typed
field rather than a single machine-readable date:

  - Multi-day text like "Thursday, October 29th and Friday, October 30th"
    or "Friday, April 16th, Saturday, April 17th, and Sunday, April 18th"
    gets truncated right before the *second* weekday name, so only the
    first date is parsed (a reasonable simplification for a POC -- the
    event is imported as a single show on its first date rather than one
    row per night).
  - Many of those same multi-day entries omit the year entirely (it's
    implied by the surrounding events on the page). Since events are
    processed in document order and this kind of listing is chronological,
    each parsed event's year is carried forward as the fallback default
    for the next item that doesn't specify one.

A second heuristic, added for Smith College's events calendar
(smith.edu/news-events/events, a Drupal "teaser" listing), handles a
different real-world date shape: "Wednesday, July 22, 2026 | 9 a.m.-4
p.m." -- a date and a start-end time range joined by "|". Handing that
whole string to dateutil's fuzzy parser directly picks up the *second*
(end) time instead of the start time. `_clean_time_range()` below strips
it down to "Wednesday, July 22, 2026 9 a.m." before parsing, understanding
"Noon"/"Midnight" and a first time that omits am/pm because it shares the
second time's (e.g. "5-7 p.m." -> "5 p.m."). It's a no-op on any date
string that doesn't contain "|", so it's safe for every other venue's
date_selector text.
"""
import json
import re

import requests
from bs4 import BeautifulSoup
from dateutil import parser as dateparser

from app.scrapers.base import ScrapedEvent, ScrapeError

USER_AGENT = "Mozilla/5.0 (compatible; LocalMusicSitePOC/0.1)"

_WEEKDAY_RE = re.compile(
    r"\b(Sunday|Monday|Tuesday|Wednesday|Thursday|Friday|Saturday)\b"
)

# Matches a "<date> | <start>[-<end>]" tail, e.g. "| 9 a.m.-4 p.m.",
# "| Noon-1 p.m.", "| 5-7 p.m." -- see module docstring.
_TIME_RANGE_RE = re.compile(
    r"^(?P<date>[^|]+)\|\s*"
    r"(?P<t1>noon|midnight|\d{1,2}(?::\d{2})?)\s*(?P<ap1>[ap]\.?m\.?)?"
    r"(?:\s*-\s*(?P<t2>noon|midnight|\d{1,2}(?::\d{2})?)\s*(?P<ap2>[ap]\.?m\.?)?)?",
    re.IGNORECASE,
)


def _clean_time_range(text):
    match = _TIME_RANGE_RE.match(text.strip())
    if not match or not match.group("t1"):
        return text

    date_part = match.group("date").strip()
    t1 = match.group("t1").lower()
    if t1 == "noon":
        time_part = "12:00 pm"
    elif t1 == "midnight":
        time_part = "12:00 am"
    else:
        ap = match.group("ap1") or match.group("ap2") or ""
        time_part = f"{t1} {ap}".strip()

    return f"{date_part} {time_part}"


def fetch_raw(venue):
    if not venue.events_url:
        raise ScrapeError("Venue has no events_url configured.")

    config = _config(venue)
    page_param = config.get("page_param")
    max_pages = int(config.get("max_pages") or 1) if page_param else 1

    pages = []
    for page_num in range(max_pages):
        url = venue.events_url
        if page_param and page_num > 0:
            sep = "&" if "?" in url else "?"
            url = f"{url}{sep}{page_param}={page_num}"
        try:
            resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=15)
            resp.raise_for_status()
        except requests.RequestException as exc:
            if page_num == 0:
                raise ScrapeError(f"Failed to fetch {url}: {exc}") from exc
            break  # ran out of pages (or a transient error) -- use what we have
        pages.append(resp.text)

    return "\n".join(pages)


def _config(venue):
    try:
        return json.loads(venue.scrape_config or "{}")
    except json.JSONDecodeError:
        return {}


def _resolve_url(href, base_url):
    if not href:
        return None
    if href.startswith("http") or href.startswith("//"):
        return href
    return f"{(base_url or '').rstrip('/')}{href}"


def _first_date_phrase(text):
    """If a date string mentions more than one weekday name, keep only
    the text up to (not including) the second one -- see module docstring."""
    matches = list(_WEEKDAY_RE.finditer(text))
    if len(matches) >= 2:
        text = text[: matches[1].start()]
    return text.strip().rstrip(", ").strip()


def parse(raw, venue):
    from datetime import datetime as dt

    config = _config(venue)
    item_sel = config.get("item_selector")
    if not item_sel:
        raise ScrapeError(
            "No 'item_selector' set in this venue's scrape_config. Edit the "
            "venue and add selectors, using the raw HTML sample below as a "
            "guide, then re-run the preview."
        )

    soup = BeautifulSoup(raw, "html.parser")
    items = soup.select(item_sel)
    events = []
    seen_external_ids = set()

    title_sel = config.get("title_selector")
    date_sel = config.get("date_selector")
    link_sel = config.get("link_selector")
    description_sel = config.get("description_selector")
    genre_sel = config.get("genre_selector")
    image_sel = config.get("image_selector")
    date_format = config.get("date_format")

    last_known_year = None

    for item in items:
        title_el = item.select_one(title_sel) if title_sel else None
        date_el = item.select_one(date_sel) if date_sel else None

        title = title_el.get_text(strip=True) if title_el else None
        date_text = date_el.get_text(strip=True) if date_el else None
        if not title or not date_text:
            # Skip rather than fail outright -- one malformed item on the
            # page shouldn't sink the whole scrape.
            continue

        try:
            if date_format:
                start_dt = dt.strptime(date_text, date_format)
            else:
                phrase = _first_date_phrase(_clean_time_range(date_text))
                default_year = last_known_year or dt.utcnow().year
                start_dt = dateparser.parse(
                    phrase, fuzzy=True, default=dt(default_year, 1, 1)
                )
                last_known_year = start_dt.year
        except (ValueError, OverflowError):
            continue

        if link_sel:
            link_el = item.select_one(link_sel)
        elif title_el is not None:
            # The title element itself is often the <a> (e.g.
            # title_selector matches an <a class="event-title">); fall
            # back to a descendant <a> if not.
            link_el = title_el if title_el.name == "a" else title_el.find("a")
        else:
            link_el = None
        ticket_url = None
        if link_el and link_el.has_attr("href"):
            ticket_url = _resolve_url(link_el["href"], venue.website_url)

        description = ""
        if description_sel:
            description_el = item.select_one(description_sel)
            if description_el:
                description = description_el.get_text(strip=True)

        genre = None
        if genre_sel:
            genre_el = item.select_one(genre_sel)
            if genre_el:
                genre = genre_el.get_text(strip=True) or None

        image_url = None
        if image_sel:
            image_el = item.select_one(image_sel)
            if image_el and image_el.has_attr("src"):
                image_url = _resolve_url(image_el["src"], venue.website_url)

        external_id = f"{title}-{start_dt.isoformat()}"
        if external_id in seen_external_ids:
            continue
        seen_external_ids.add(external_id)

        events.append(
            ScrapedEvent(
                title=title,
                start_datetime=start_dt,
                description=description,
                ticket_url=ticket_url,
                genre=genre,
                image_url=image_url,
                external_id=external_id,
            )
        )

    events.sort(key=lambda e: e.start_datetime)
    return events

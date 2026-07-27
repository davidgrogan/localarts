"""Scraper for venues running Ludus (ludus.com) as their ticketing/event
platform -- confirmed live on BOMBYX Center for Arts & Equity
(bombyx.ludus.com), which is a separate site from a venue's own marketing
site (e.g. bombyx.live) and where the actual event listing lives.

Structure, confirmed via live DOM inspection in a real browser (network
log showed no separate XHR/JSON request fetches the event list itself --
only a POST to /v1/shows/seats-left for seat-count numbers *after* the
listing has already rendered -- and the page is served from index.php,
a plain PHP page, not a JS framework/SPA):

    .show_item                        -- one per titled event/listing
        [data-show-id]                -- stable ID for the listing itself
        [data-event-categories]       -- semicolon-separated internal
                                          category IDs (not human-readable
                                          -- the visible pill text below is
                                          used instead so this scraper
                                          doesn't need a separate ID->name
                                          mapping)
        .show_item_category_pill      -- 0+ visible category badges, e.g.
                                          "Workshop", "Concert", "Community
                                          Event" -- some real listings
                                          (e.g. "Noho Music Presents:
                                          Summer Jam '26") have NONE at all
        .show_item_cover_photo        -- style="background-image:url(...)"
        .show_item_title .patron_heading_label -- the event title text
        .showtimes_item               -- 1+ per show_item: one per date/
                                          session. Some listings (e.g. a
                                          recurring weekly class) have
                                          several -- each becomes its own
                                          Event below, all sharing the
                                          same title.
            [data-showtime-id]        -- stable per-date/session ID, used
                                          directly as external_id
            .admin_showtimes_item_title .desktop_copy .span_link
                                       -- the date text as a direct text
                                          node ("Sunday, August 9, 2026"),
                                          with the time nested inside as
                                          its own <span> ("12:00 PM"). A
                                          near-identical .mobile_copy
                                          sibling duplicates the same
                                          text for a responsive layout --
                                          deliberately not selected here to
                                          avoid double-counting.

This module was originally written with a plain `requests.get()` fetch,
on the theory that a server-rendered PHP page with no separate
list-fetching XHR almost certainly has this markup in the *initial*
response too (the same reasoning squarespace_json.py uses for Iron
Horse) -- but a real scrape attempt against the live site came back
`403 Forbidden`, with no distinguishing response body to go on. That's
the same shape of failure Quonk's Ticket Tailor listing hit (see this
project's README): Ticket Tailor's own robots.txt explicitly allowed
crawling those exact pages, so the block there wasn't a UA-string
check, it was almost certainly a TLS/header fingerprint a plain
`requests` call can't fake -- a real browser passes it automatically.
Rather than spend another live-test round-trip on a smaller fix
(swapping in a realistic `User-Agent` alone) that already failed to be
enough for that identical-shaped Quonk problem, fetch_raw() below skips
straight to reusing rendered_html.py's Playwright-based fetch (the same
headless-Chromium-with-automation-hiding-flags approach already proven
against Quonk/Heavy Culture/33 Hawley) -- it's a fully generic
`fetch_raw(venue)` with no dependency on html_generic's parsing, so it's
safe to import and reuse directly here, keeping this module's own
parse() (which understands Ludus's nested show_item/showtimes_item
shape, not the generic selector config rendered_html.parse() expects).

There is no reliable direct "buy tickets" URL per show/date visible in
the DOM -- the "Get Tickets" control is a <div> (not a link) that drives
an in-page radio-button + form flow rather than navigating anywhere.
ticket_url falls back to the venue's own Ludus listing page, same
fallback idea as elfsight_jsonld.py's ticket_url handling.

Venue scrape_config keys:
    category_include -- optional list of substrings (case-insensitive)
                          matched against the show's visible category
                          pill text (joined with "; " if more than one).
                          A show with NO category pill at all never
                          matches any non-empty category_include list --
                          see the module docstring above for real
                          examples of that happening.

    Since fetch_raw() is borrowed directly from rendered_html.py (see
    above), every one of *that* module's own scrape_config keys also
    applies here -- most usefully wait_for_selector (set to ".show_item"
    so the fetch doesn't capture the page before real events render) and
    user_agent (a realistic browser UA string, in case the automation-
    hiding flags alone aren't enough to get past this venue's block --
    see rendered_html.py's own module docstring for the full list of
    keys: wait_ms, next_button_selector/next_button_clicks,
    description_from_link/description_detail_selector).
"""
import json
import re
from datetime import datetime

from bs4 import BeautifulSoup, NavigableString

from app.scrapers.base import ScrapedEvent
# Reused as-is -- see module docstring for why this venue needs the same
# headless-Chromium fetch already built for Quonk/Heavy Culture/33 Hawley
# rather than a plain requests.get(). This function only reads
# venue.events_url/venue.scrape_config; it has no dependency on
# html_generic's parsing, so it's safe to borrow here and pair with this
# module's own parse() below instead of rendered_html.py's generic one.
from app.scrapers.rendered_html import fetch_raw

DATE_TIME_FMT = "%A, %B %d, %Y %I:%M %p"

_BG_IMAGE_RE = re.compile(r"""url\((['"]?)(.*?)\1\)""")


def _config(venue):
    try:
        return json.loads(venue.scrape_config or "{}")
    except json.JSONDecodeError:
        return {}


def _direct_text(el):
    """The date text sits as a direct text-node child of `el`, alongside
    (not inside) a nested <span> for the time -- see module docstring.
    Joining every direct NavigableString child (rather than assuming
    there's exactly one) is defensive against incidental whitespace text
    nodes without relying on a fragile [0]-index."""
    return "".join(t for t in el.contents if isinstance(t, NavigableString)).strip()


def _image_url(cover_el):
    if cover_el is None or not cover_el.has_attr("style"):
        return None
    match = _BG_IMAGE_RE.search(cover_el["style"])
    return match.group(2) if match else None


def parse(raw, venue):
    soup = BeautifulSoup(raw, "html.parser")
    config = _config(venue)
    category_include = [c.lower() for c in config.get("category_include", []) if c]

    events = []
    seen_external_ids = set()

    for show in soup.select(".show_item"):
        title_el = show.select_one(".show_item_title .patron_heading_label")
        title = title_el.get_text(strip=True) if title_el else ""
        if not title:
            continue

        pills = [p.get_text(strip=True) for p in show.select(".show_item_category_pill")]
        category_text = "; ".join(p for p in pills if p)
        if category_include:
            haystack = category_text.lower()
            if not any(term in haystack for term in category_include):
                continue

        image_url = _image_url(show.select_one(".show_item_cover_photo"))
        ticket_url = venue.events_url or venue.website_url or None

        for showtime in show.select(".showtimes_item"):
            showtime_id = showtime.get("data-showtime-id")
            if not showtime_id:
                continue

            date_el = showtime.select_one(".admin_showtimes_item_title .desktop_copy .span_link")
            if date_el is None:
                continue
            date_text = _direct_text(date_el)
            time_el = date_el.select_one("span")
            time_text = time_el.get_text(strip=True) if time_el else ""
            if not date_text or not time_text:
                continue

            try:
                start_dt = datetime.strptime(f"{date_text} {time_text}", DATE_TIME_FMT)
            except ValueError:
                continue

            external_id = showtime_id
            if external_id in seen_external_ids:
                continue
            seen_external_ids.add(external_id)

            events.append(
                ScrapedEvent(
                    title=title,
                    start_datetime=start_dt,
                    description="",
                    ticket_url=ticket_url,
                    genre=category_text or None,
                    image_url=image_url,
                    external_id=external_id,
                )
            )

    events.sort(key=lambda e: e.start_datetime)
    return events

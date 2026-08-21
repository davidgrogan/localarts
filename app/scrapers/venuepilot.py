"""Scraper for venues running VenuePilot (venuepilot.co) as their event
listing widget -- confirmed live on CitySpace (cityspaceeasthampton.org),
embedded via a WPBakery "raw HTML/JS" block on a WordPress page whose
actual events live behind a client-side hash route
(cityspaceeasthampton.org/all-events/#/events). A plain requests fetch
only ever sees the empty page shell; the widget's own React app renders
everything after the fact, fetching from a GraphQL API
(www.venuepilot.co/graphql) that has introspection disabled and isn't
otherwise documented, so this follows the same path Quonk/Heavy
Culture/Bombyx took: load the real page in a headless browser and parse
the *rendered* DOM instead of reverse-engineering the API.

Structure, confirmed via live DOM inspection in a real browser:

    .vp-event-row                 -- one per listed event
        a.vp-event-link           -- href="#/events/<id>" -- this
                                      venue's own stable per-event page
                                      (a hash route on the same domain,
                                      not a separate ticketing site), used
                                      directly as both external_id source
                                      and the fallback "more info" link
        .vp-month-n-day           -- e.g. "Aug 22" -- note: NO YEAR. See
                                      _resolve_year() below for how that's
                                      handled.
        .vp-time                  -- e.g. "8:00 PM"
        .vp-promoter               -- the specific room/space within the
                                      venue this show is actually in, e.g.
                                      "The Blue Room at CitySpace", "ECA
                                      Gallery", "Local arts and gifts" --
                                      or just the venue's own plain name
                                      ("CitySpace") for anything not tied
                                      to a particular sub-space. Fed into
                                      ScrapedEvent.custom_venue_name
                                      (see that field's docstring) *except*
                                      when it's just the venue's own name
                                      verbatim, in which case leaving it
                                      unset lets display_venue_name/
                                      display_venue_link fall back to the
                                      real Venue row (name + a working
                                      website link) instead of a
                                      redundant, link-suppressing override.
        .vp-event-name             -- the event title
        .vp-support (optional)     -- a subtitle/series line, e.g. "Pay it
                                      Forward 2026", "Art Walk Easthampton"
                                      -- appended to the description rather
                                      than dropped, since it's often the
                                      only clue distinguishing e.g. a
                                      ticketed concert series from a free
                                      community event of the same shape.
        .vp-main-img (or           -- the cover image, NOT an <img src>
         .vp-cover-img)               -- a `style="background-image:
                                      url(...)"` div, same pattern as
                                      ludus.py's .show_item_cover_photo.

CitySpace itself is a genuinely mixed-use community space, not a
dedicated music venue -- the same widget lists Blue Room concerts
alongside ECA Gallery art openings, a monthly building tour, a pop-up
market, and a volunteer day, with no visible category/tag field to
distinguish them on the listing page. Because of that, seed.py
deliberately does *not* set a default_event_type for this venue -- every
newly scraped event lands untagged and off the public (Music-only)
calendar until an admin reviews and tags the real shows by hand (see
Event.event_types / main.py's MUSIC_ONLY_CATEGORY_NAME). title_exclude
below is the escape valve for permanently filtering out the recurring
non-music filler (the Tour/Market/etc. combo repeats close to monthly)
once it's clear which titles are never going to be shows.

Venue scrape_config keys:
    title_exclude -- optional list[str] (case-insensitive substring
                      match against the event title) -- same convention
                      as ical_feed.py's title_exclude, for permanently
                      dropping recurring non-music listings (e.g. "Tour
                      of Old Town Hall", "Tiny Pop-Up Market") once
                      confirmed they're never going to be shows, rather
                      than reviewing/discarding the same ones every scrape.

    Since fetch_raw() is borrowed directly from rendered_html.py (same
    reasoning as ludus.py -- a plain fetch of a React/hash-routed page
    would only ever see an empty shell), every one of *that* module's own
    scrape_config keys also applies here -- most usefully wait_for_selector
    (set to ".vp-event-row" so the fetch doesn't capture the page before
    the widget's async GraphQL call resolves).
"""
import json
import re
from datetime import timedelta

from bs4 import BeautifulSoup

from app.scrapers.base import ScrapedEvent
# Reused as-is -- see module docstring for why this hash-routed React
# widget needs the same headless-Chromium fetch already built for
# Quonk/Heavy Culture/Bombyx rather than a plain requests.get(). This
# function only reads venue.events_url/venue.scrape_config; it has no
# dependency on html_generic's parsing, so it's safe to borrow here and
# pair with this module's own parse() below instead of
# rendered_html.py's generic selector-driven one.
from app.scrapers.rendered_html import fetch_raw
from app.utils import local_now

_BG_IMAGE_RE = re.compile(r"""url\((['"]?)(.*?)\1\)""")

# "Aug 22" has no year at all -- every event on this page is upcoming, so
# any parse that lands more than this many days in the past almost
# certainly means the *next* occurrence of that month/day, a year ahead
# (e.g. scraping in August 2026 and seeing "Jan 8" clearly means January
# 2027, not a January that already passed seven months ago). A small
# grace window (rather than exactly 0) tolerates a show earlier *today*
# still showing up if the scrape happens to run partway through the day.
_PAST_GRACE_DAYS = 3


def _config(venue):
    try:
        return json.loads(venue.scrape_config or "{}")
    except json.JSONDecodeError:
        return {}


def _resolve_year(month_day_text, time_text, today):
    """Parses a "<Mon> <day>" + "<h>:<mm> <AM/PM>" pair with no year in
    either string (see module docstring) by trying the current year first,
    then next year if that lands too far in the past. Returns None if the
    combined text doesn't parse as a date/time at all (a malformed or
    unexpected listing -- the caller skips that item rather than guessing)."""
    from datetime import datetime as dt

    for year in (today.year, today.year + 1):
        try:
            candidate = dt.strptime(f"{month_day_text} {year} {time_text}", "%b %d %Y %I:%M %p")
        except ValueError:
            continue
        if candidate >= today - timedelta(days=_PAST_GRACE_DAYS):
            return candidate
    return None


def _image_url(el):
    if el is None or not el.has_attr("style"):
        return None
    match = _BG_IMAGE_RE.search(el["style"])
    return match.group(2) if match else None


def parse(raw, venue):
    soup = BeautifulSoup(raw, "html.parser")
    config = _config(venue)
    title_exclude = [s.lower() for s in config.get("title_exclude", [])]

    today = local_now().replace(hour=0, minute=0, second=0, microsecond=0)
    venue_name_lower = (venue.name or "").strip().lower()

    events = []
    seen_external_ids = set()

    for row in soup.select(".vp-event-row"):
        title_el = row.select_one(".vp-event-name")
        title = title_el.get_text(strip=True) if title_el else ""
        if not title:
            continue
        if title_exclude and any(pat in title.lower() for pat in title_exclude):
            continue

        month_day_el = row.select_one(".vp-month-n-day")
        time_el = row.select_one(".vp-time")
        if month_day_el is None or time_el is None:
            continue
        start_dt = _resolve_year(
            month_day_el.get_text(strip=True), time_el.get_text(strip=True), today
        )
        if start_dt is None:
            continue

        link_el = row.select_one("a.vp-event-link, a[href^='#/events/']")
        ticket_url = None
        if link_el and link_el.has_attr("href"):
            base = (venue.events_url or "").split("#")[0]
            ticket_url = f"{base}{link_el['href']}" if base else None

        promoter_el = row.select_one(".vp-promoter")
        promoter_text = promoter_el.get_text(strip=True) if promoter_el else ""
        # Only an override when it actually names something other than
        # the venue itself -- see module docstring for why a plain
        # "CitySpace" (or missing) promoter line should NOT set this,
        # unlike "The Blue Room at CitySpace"/"ECA Gallery"/etc.
        custom_venue_name = (
            promoter_text if promoter_text and promoter_text.lower() != venue_name_lower else None
        )

        support_el = row.select_one(".vp-support")
        support_text = support_el.get_text(strip=True) if support_el else ""
        description = support_text

        image_url = _image_url(row.select_one(".vp-main-img")) or _image_url(
            row.select_one(".vp-cover-img")
        )

        # The hash fragment itself ("#/events/188338") is this event's
        # own stable id on this venue's site -- reused directly rather
        # than a title+date hash, so it survives a title edit/typo fix
        # on VenuePilot's side without creating a duplicate Event here.
        external_id = None
        if link_el and link_el.has_attr("href"):
            external_id = link_el["href"].split("/")[-1]
        if not external_id:
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
                image_url=image_url,
                custom_venue_name=custom_venue_name,
                external_id=external_id,
            )
        )

    events.sort(key=lambda e: e.start_datetime)
    return events

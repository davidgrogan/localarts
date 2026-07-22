"""Scraper for venues that publish a plain iCal (.ics) feed.

This is the easiest and most robust source type when a venue has one
(common for WordPress "The Events Calendar" sites, Google Calendar
based listings, etc.) -- no HTML/JS guesswork involved.

Optional `venue.scrape_config` (JSON):
    title_exclude: list[str] -- events whose title contains any of these
        strings (case-insensitive) are dropped entirely. Some venues'
        calendar feeds mix real events in with operational notices (e.g.
        "CLOSED", "Bar Open 4-11pm") that aren't shows -- this filters
        that noise out before it ever reaches the review queue.
"""
import json
from datetime import datetime, date

import requests
from icalendar import Calendar

from app.scrapers.base import ScrapedEvent, ScrapeError

USER_AGENT = "Mozilla/5.0 (compatible; LocalMusicSitePOC/0.1)"


def fetch_raw(venue):
    if not venue.events_url:
        raise ScrapeError("Venue has no events_url (.ics feed) configured.")
    try:
        resp = requests.get(venue.events_url, headers={"User-Agent": USER_AGENT}, timeout=15)
        resp.raise_for_status()
    except requests.RequestException as exc:
        raise ScrapeError(f"Failed to fetch {venue.events_url}: {exc}") from exc
    return resp.text


def _to_datetime(value):
    """icalendar hands back either a date or a datetime depending on
    whether the event is all-day; normalize to a plain datetime."""
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)
    return None


def parse(raw, venue):
    try:
        config = json.loads(venue.scrape_config or "{}")
    except json.JSONDecodeError as exc:
        raise ScrapeError(f"scrape_config isn't valid JSON: {exc}") from exc
    title_exclude = [s.lower() for s in config.get("title_exclude", [])]

    try:
        cal = Calendar.from_ical(raw)
    except ValueError as exc:
        raise ScrapeError(f"Response wasn't valid iCal data: {exc}") from exc

    events = []
    for component in cal.walk("VEVENT"):
        dtstart = component.get("dtstart")
        if dtstart is None:
            continue
        start_dt = _to_datetime(dtstart.dt)
        if start_dt is None:
            continue

        dtend = component.get("dtend")
        end_dt = _to_datetime(dtend.dt) if dtend else None

        uid = str(component.get("uid") or "")
        title = str(component.get("summary") or "Untitled event")
        if title_exclude and any(pat in title.lower() for pat in title_exclude):
            continue
        external_id = uid or f"{title}-{start_dt.isoformat()}"

        events.append(
            ScrapedEvent(
                title=title.strip(),
                start_datetime=start_dt,
                end_datetime=end_dt,
                description=str(component.get("description") or ""),
                ticket_url=str(component.get("url") or "") or None,
                external_id=external_id,
            )
        )

    events.sort(key=lambda e: e.start_datetime)
    return events

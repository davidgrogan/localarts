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


def _split_vtimezone_block(block_lines):
    """block_lines is a VTIMEZONE block's contents, starting with the
    'BEGIN:VTIMEZONE' line itself and with no matching 'END:VTIMEZONE'
    yet. Splits on each top-level 'TZID:' line so a block that wrongly
    packed more than one timezone definition together becomes one
    well-formed VTIMEZONE per TZID; a no-op (just re-closes the block)
    when there's only one."""
    tzid_indices = [i for i, line in enumerate(block_lines) if line.startswith("TZID:")]
    if len(tzid_indices) <= 1:
        return block_lines + ["END:VTIMEZONE"]
    result = []
    for i, start in enumerate(tzid_indices):
        end = tzid_indices[i + 1] if i + 1 < len(tzid_indices) else len(block_lines)
        result.append("BEGIN:VTIMEZONE")
        result.extend(block_lines[start:end])
        result.append("END:VTIMEZONE")
    return result


def _repair_malformed_vtimezones(raw):
    """Seen from a real WordPress "The Events Calendar" feed (a venue
    with events in both US Eastern and Atlantic time): it emits a
    single VTIMEZONE block containing two TZID definitions back to
    back, e.g.

        BEGIN:VTIMEZONE
        TZID:America/New_York
        ...(DAYLIGHT/STANDARD sub-blocks)...
        TZID:America/Halifax
        ...(DAYLIGHT/STANDARD sub-blocks)...
        END:VTIMEZONE

    which is invalid per RFC 5545 (one VTIMEZONE = one TZID) and makes
    icalendar's Calendar.from_ical() raise `TypeError: unhashable type:
    'list'` trying to key its timezone cache off a list of TZIDs
    instead of a single one. Splitting each such block into one
    well-formed VTIMEZONE per TZID before parsing fixes it; a no-op on
    any feed that's already well-formed (the overwhelmingly common
    case), so safe for every other ical venue."""
    lines = raw.splitlines()
    out = []
    in_vtimezone = False
    block_lines = []
    for line in lines:
        if line.strip() == "BEGIN:VTIMEZONE":
            in_vtimezone = True
            block_lines = [line]
            continue
        if in_vtimezone:
            if line.strip() == "END:VTIMEZONE":
                out.extend(_split_vtimezone_block(block_lines))
                in_vtimezone = False
                block_lines = []
                continue
            block_lines.append(line)
            continue
        out.append(line)
    return "\n".join(out)


def parse(raw, venue):
    try:
        config = json.loads(venue.scrape_config or "{}")
    except json.JSONDecodeError as exc:
        raise ScrapeError(f"scrape_config isn't valid JSON: {exc}") from exc
    title_exclude = [s.lower() for s in config.get("title_exclude", [])]

    raw = _repair_malformed_vtimezones(raw)
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

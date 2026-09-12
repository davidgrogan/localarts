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
    months_ahead: int -- WordPress "The Events Calendar"'s own `?ical=1`
        export is scoped to whatever month is currently on screen: hit
        plain ".../events/?ical=1" and you only get the events visible in
        *this* month's grid (plus that grid's leading/trailing days from
        the adjacent months) -- nothing further out, no matter how much
        the venue has already published. That's easy to miss because nothing
        ever looks "broken": every scheduled run succeeds and the feed's
        far edge just quietly creeps forward one day at a time as "today"
        does. Confirmed on Luthier's Co-Op (venue slug luthiers-co-op):
        their own site lets you page forward to see months of listed
        shows, but our feed was topping out a few weeks out. Setting
        months_ahead=N also fetches N additional months' exports (via
        ".../events/YYYY-MM/?ical=1", the same URL pattern "The Events
        Calendar" itself uses for its "« prev / next »" month links) and
        merges their VEVENTs into the base month's calendar before
        handing off to parse() below, which is otherwise unchanged.
        Left unset (default 0), behavior is exactly what it always was --
        this only kicks in for a venue that opts in.
"""
import calendar
import json
import re
import time
from datetime import datetime, date
from urllib.parse import urlsplit, urlunsplit

import requests
from icalendar import Calendar

from app.scrapers.base import ScrapedEvent, ScrapeError
from app.utils import local_now

USER_AGENT = "Mozilla/5.0 (compatible; LocalMusicSitePOC/0.1)"

# The extra monthly requests months_ahead adds make this feed hit a
# flaky venue server more often per scrape; a real WordPress "The Events
# Calendar" install (Luthier's Co-Op) was observed intermittently
# returning 503s on this exact endpoint even though it was reachable
# moments before/after. One quick retry absorbs that without giving up
# on an otherwise-working month.
_RETRY_ATTEMPTS = 2
_RETRY_DELAY_SECONDS = 2

_VEVENT_RE = re.compile(r"BEGIN:VEVENT.*?END:VEVENT", re.DOTALL)
_UID_RE = re.compile(r"^UID:(.*)$", re.MULTILINE)


def _get(url):
    last_exc = None
    for attempt in range(_RETRY_ATTEMPTS):
        try:
            resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=15)
            resp.raise_for_status()
            return resp.text
        except requests.RequestException as exc:
            last_exc = exc
            if attempt + 1 < _RETRY_ATTEMPTS:
                time.sleep(_RETRY_DELAY_SECONDS)
    raise ScrapeError(f"Failed to fetch {url}: {last_exc}") from last_exc


def _month_url(base_url, year, month):
    """Build "the same URL, but for a different month" the way The Events
    Calendar's own month-view "« prev / next »" links do: a /YYYY-MM/
    path segment inserted right before the page's own query string (here,
    always just "?ical=1")."""
    parts = urlsplit(base_url)
    path = parts.path if parts.path.endswith("/") else parts.path + "/"
    new_path = f"{path}{year:04d}-{month:02d}/"
    return urlunsplit((parts.scheme, parts.netloc, new_path, parts.query, parts.fragment))


def _add_months(year, month, delta):
    total = (year * 12 + (month - 1)) + delta
    return total // 12, total % 12 + 1


def _merge_extra_months(primary_raw, venue, months_ahead):
    """Fetch `months_ahead` additional monthly exports beyond whatever
    `primary_raw` already covers, and splice their VEVENT blocks into it.
    Reuses primary_raw's own VTIMEZONE/VCALENDAR wrapper rather than
    building a fresh one -- same venue, same timezone, every month --
    and dedupes on UID in case a month's leading/trailing grid days
    already showed up in a neighboring month's export.

    A month that fails to fetch (after _get's own retry) is silently
    skipped rather than failing the whole scrape -- partial forward
    coverage beats none, and it'll very likely succeed on next
    scheduled run anyway. Nothing here can go in the merged raw ics
    text itself to note a skip: icalendar's from_ical() walks every
    content line in the string, including any after END:VCALENDAR, so
    even a trailing HTML/ICS comment there is enough to make parse()
    reject the whole feed as invalid.
    """
    seen_uids = set(_UID_RE.findall(primary_raw))
    new_blocks = []

    today = local_now().date()
    for i in range(1, months_ahead + 1):
        year, month = _add_months(today.year, today.month, i)
        url = _month_url(venue.events_url, year, month)
        try:
            month_raw = _get(url)
        except ScrapeError:
            continue
        for block in _VEVENT_RE.findall(month_raw):
            uid_match = _UID_RE.search(block)
            uid = uid_match.group(1).strip() if uid_match else None
            if uid and uid in seen_uids:
                continue
            if uid:
                seen_uids.add(uid)
            new_blocks.append(block)

    if not new_blocks:
        return primary_raw

    idx = primary_raw.rfind("END:VCALENDAR")
    if idx == -1:
        # Malformed/unexpected wrapper -- fall back to the unmerged
        # primary feed rather than silently dropping it.
        return primary_raw
    insertion = "\n" + "\n".join(new_blocks) + "\n"
    return primary_raw[:idx] + insertion + primary_raw[idx:]


def fetch_raw(venue):
    if not venue.events_url:
        raise ScrapeError("Venue has no events_url (.ics feed) configured.")
    raw = _get(venue.events_url)

    try:
        config = json.loads(venue.scrape_config or "{}")
    except json.JSONDecodeError:
        config = {}
    months_ahead = int(config.get("months_ahead", 0) or 0)
    if months_ahead > 0:
        raw = _merge_extra_months(raw, venue, months_ahead)

    return raw


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

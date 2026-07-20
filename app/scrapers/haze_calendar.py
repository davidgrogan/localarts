"""Scraper for Haze's (hazenorthampton.org) built-in calendar widget.

Confirmed via a real "Copy outerHTML" sample that this widget is entirely
server-rendered -- a plain HTTP fetch returns the full month-grid HTML, no
headless browser needed (unlike the Iron Horse's Elfsight widget). But its
structure is different from every other venue scraped so far: events are
grouped inside a day cell rather than each carrying their own date --

    <div class="min-h-36 border-b border-r ...">                 <!-- one day cell -->
      <time datetime="2026-07-30" class="...">30</time>
      <div class="mt-2 space-y-2">
        <div class="... rounded-md border ...">
          <div tabindex="0" aria-label="Event details: Reggae nite with DJ live &amp; DJ cancer" class="...">
            <div class="flex flex-col gap-1.5">
              <div class="..."><img alt="Flyer: ..." src="..."></div>
              <div class="...">
                <span class="...">9:00 PM</span>
                <span class="...">Reggae nite with DJ live &amp; DJ cancer</span>
              </div>
            </div>
          </div>
        </div>
        <!-- more event cards here if the day has more than one -->
      </div>
    </div>

The site's own styling is all Tailwind utility classes (e.g.
`text-fuchsia-200/90`, `rounded-md`) -- there's nothing like Elfsight's
`eapp-` prefixed helpers or WordPress's `event_card` class to hook a
selector to, and utility classes are liable to shift with any styling
tweak. Two pieces of markup are semantic and stable regardless of that,
so this scraper is built entirely around them:
  - `<time datetime="YYYY-MM-DD">` on each day cell -- a real ISO date,
    year included, no guessing or DOM-badge/JSON-LD cross-referencing
    needed the way Elfsight's venues required.
  - `aria-label="Event details: <title>"` on each event's clickable
    card, which doubles as a clean, ready-to-use title.

Time of day is pulled by regex from the event card's own visible text
(whatever looks like "9:00 PM"). Some recurring entries (e.g. a weekly
"Bar Night!") show "Tonight" / "Til 2am!" instead of a clock time; those
fall back to midnight, the same graceful-degradation approach used
elsewhere in this framework (e.g. Elfsight's date-only fallback) when a
real time genuinely isn't available from the source.

Real-world gotcha, confirmed against two actual events: this month-grid
view appears to compute its own day-cell placement and displayed clock
time in UTC rather than America/New_York, while Haze's own "This week"
teaser section (elsewhere on the same page) shows the correct local
time -- i.e. this looks like a bug in Haze's own site, not something we
introduced. "Game Day" showed here as Sunday 7:00 PM but is actually
Sunday 3:00 PM (same-day 4-hour EDT shift); "Dead Night, Hosted by Kade
Parkin" showed as Wednesday 12:00 AM but is actually Tuesday 8:00 PM (a
shift that also rolls it back a calendar day). Treating the day cell's
date + parsed clock time as a UTC instant and converting to Eastern
reproduces both correct values exactly, so that conversion is applied
whenever a real time was found. When no time is found at all (the
"Tonight" / "Til 2am!" cases), there's nothing to convert, so the day
cell's own date is kept as-is with a midnight placeholder -- we can't
know whether those are similarly shifted without a real hour to correct.

Not every venue's page will look like this, but if another venue turns
out to use the same "calendar-wish"-style widget (its event flyer images
are hosted on a calendar-wish S3 bucket, keyed by what look like Google
Calendar event IDs -- possibly a small SaaS product syncing a Google
Calendar), this module should work for it too with just an events_url
change.
"""
import re
from datetime import datetime as dt, timezone
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

from app.scrapers.base import ScrapedEvent, ScrapeError

USER_AGENT = "Mozilla/5.0 (compatible; LocalMusicSitePOC/0.1)"

_TIME_RE = re.compile(r"\b(\d{1,2}):(\d{2})\s?([AaPp][Mm])\b")
_EVENT_SELECTOR = "[aria-label^='Event details:']"
_VENUE_TZ = ZoneInfo("America/New_York")


def fetch_raw(venue):
    if not venue.events_url:
        raise ScrapeError("Venue has no events_url configured.")
    try:
        resp = requests.get(venue.events_url, headers={"User-Agent": USER_AGENT}, timeout=15)
        resp.raise_for_status()
    except requests.RequestException as exc:
        raise ScrapeError(f"Failed to fetch {venue.events_url}: {exc}") from exc
    return resp.text


def _parse_time_of_day(text):
    match = _TIME_RE.search(text)
    if not match:
        return None
    hour, minute, meridiem = match.groups()
    hour = int(hour) % 12
    if meridiem.lower() == "pm":
        hour += 12
    return hour, int(minute)


def parse(raw, venue):
    soup = BeautifulSoup(raw, "html.parser")
    events = []
    seen_external_ids = set()

    for time_el in soup.find_all("time", attrs={"datetime": True}):
        date_str = time_el.get("datetime", "")
        try:
            year, month, day = (int(part) for part in date_str.split("-")[:3])
        except (ValueError, AttributeError):
            continue

        # The day cell is this <time>'s own immediate parent (confirmed
        # from the real markup) -- deliberately *not* walking further up
        # looking for events, since most day cells legitimately have zero
        # events, and walking up would risk picking up a neighboring
        # day's events instead (the same class of bug hit and fixed for
        # Elfsight's category matching).
        day_cell = time_el.parent
        if day_cell is None:
            continue

        for event_el in day_cell.select(_EVENT_SELECTOR):
            aria = event_el.get("aria-label", "")
            title = aria.split(":", 1)[1].strip() if ":" in aria else aria.strip()
            if not title:
                continue

            parsed_time = _parse_time_of_day(event_el.get_text(" ", strip=True))

            try:
                if parsed_time is not None:
                    hour, minute = parsed_time
                    # See module docstring: this grid appears to compute its
                    # own date/time in UTC -- treat it as such and convert
                    # back to Eastern rather than taking it at face value.
                    utc_dt = dt(year, month, day, hour, minute, tzinfo=timezone.utc)
                    start_dt = utc_dt.astimezone(_VENUE_TZ).replace(tzinfo=None)
                else:
                    # No real clock time to correct with -- keep the day
                    # cell's own date and fall back to midnight.
                    start_dt = dt(year, month, day, 0, 0)
            except ValueError:
                continue

            img = event_el.find("img")
            image_url = img["src"] if img and img.has_attr("src") else None

            external_id = f"{title}-{start_dt.isoformat()}"
            if external_id in seen_external_ids:
                # The widget occasionally renders more than one copy of a
                # day cell (e.g. a "this week" teaser plus the full grid).
                continue
            seen_external_ids.add(external_id)

            events.append(
                ScrapedEvent(
                    title=title,
                    start_datetime=start_dt,
                    description="",
                    # No distinct per-event page -- these cards open an
                    # in-page details view rather than linking out, so
                    # fall back to the venue's own calendar page.
                    ticket_url=venue.events_url,
                    image_url=image_url,
                    external_id=external_id,
                )
            )

    events.sort(key=lambda e: e.start_datetime)
    return events

"""Scraper for Squarespace-based venue sites.

Many small venues (including the Iron Horse -- ironhorse.org) run on
Squarespace, whose event listing pages are rendered client-side by JS,
which makes a plain requests+BeautifulSoup scrape of the HTML come back
empty. Squarespace has a long-standing (undocumented but widely relied
upon) convenience: appending ``?format=json`` to any page URL returns
a JSON dump of that page's content, including the underlying events
collection -- no headless browser required.

The exact JSON shape varies a bit by template version, so rather than
hard-coding one path like data['collection']['items'], `_find_event_
candidates` walks the whole tree looking for dict shapes that look
like an event (a 'title' plus a start-date-ish field). Verify with the
in-app scrape preview screen (Venues -> a venue -> "Test scrape") after
deploying somewhere with real internet access, since this sandbox
can't reach ironhorse.org to test live.
"""
import html
import json
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

from app.scrapers.base import ScrapedEvent, ScrapeError

USER_AGENT = "Mozilla/5.0 (compatible; LocalMusicSitePOC/0.1)"

# Both the Iron Horse and One Amber Lane are in Northampton, MA -- Squarespace's
# startDate/endDate are Unix-epoch milliseconds (a real UTC instant), but
# Event.start_datetime is stored as naive *local* wall-clock time (see
# app/utils.py's SITE_TIMEZONE/local_now() docstrings). Same conversion
# elfsight_jsonld.py's _to_local() already does for its own JSON-LD
# timestamps -- without it, every event lands 4-5 hours off (and can roll
# onto the wrong calendar day entirely), the exact bug class task #52/#148
# already fixed for Elfsight.
_VENUE_TZ = ZoneInfo("America/New_York")


def _ms_to_local(ms):
    """Unix-epoch milliseconds -> naive America/New_York wall-clock time."""
    return (
        datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc)
        .astimezone(_VENUE_TZ)
        .replace(tzinfo=None)
    )


def fetch_raw(venue):
    url = venue.events_url
    if not url:
        raise ScrapeError("Venue has no events_url configured.")
    sep = "&" if "?" in url else "?"
    json_url = f"{url}{sep}format=json"
    try:
        resp = requests.get(json_url, headers={"User-Agent": USER_AGENT}, timeout=15)
        resp.raise_for_status()
    except requests.RequestException as exc:
        raise ScrapeError(f"Failed to fetch {json_url}: {exc}") from exc
    return resp.text


def _strip_html(html):
    if not html:
        return ""
    return BeautifulSoup(html, "html.parser").get_text(" ", strip=True)


def _find_event_candidates(node, found=None):
    if found is None:
        found = []
    if isinstance(node, dict):
        if "title" in node and (
            "startDate" in node or "startDateAllDay" in node or "start" in node
        ):
            found.append(node)
        else:
            for value in node.values():
                _find_event_candidates(value, found)
    elif isinstance(node, list):
        for item in node:
            _find_event_candidates(item, found)
    return found


def parse(raw, venue):
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ScrapeError(
            "Response wasn't JSON -- the ?format=json trick may not apply to "
            "this venue's page (or it isn't Squarespace). Check the raw "
            "sample and consider the 'html' source type instead."
        ) from exc

    candidates = _find_event_candidates(data)
    events = []
    seen_ids = set()

    for item in candidates:
        start_ms = item.get("startDate")
        if start_ms is None:
            continue
        try:
            start_dt = _ms_to_local(start_ms)
        except (TypeError, ValueError, OSError, OverflowError):
            continue

        end_dt = None
        end_ms = item.get("endDate")
        if end_ms:
            try:
                end_dt = _ms_to_local(end_ms)
            except (TypeError, ValueError, OSError, OverflowError):
                end_dt = None

        # unescape() -- same "literal &apos;/&amp; embedded straight into a
        # JSON string" issue found (and fixed) in elfsight_jsonld.py's
        # equivalent name field; json.loads() has no concept of HTML
        # entities, unlike _strip_html()'s BeautifulSoup-based description
        # below, which decodes them as a side effect of HTML parsing.
        title = html.unescape((item.get("title") or "Untitled event").strip())
        item_id = item.get("id")
        external_id = str(item_id) if item_id else f"{title}-{start_ms}"
        if external_id in seen_ids:
            continue
        seen_ids.add(external_id)

        full_url = item.get("fullUrl")
        ticket_url = None
        if full_url:
            base = (venue.website_url or "").rstrip("/")
            ticket_url = full_url if full_url.startswith("http") else f"{base}{full_url}"

        description = _strip_html(item.get("excerpt") or item.get("body") or "")

        # Squarespace's own image CDN (images.squarespace-cdn.com) serves
        # these asset URLs already absolute and with no hotlink/referrer
        # protection -- unlike Amherst Cinema's site-hosted poster images
        # (see amherst_cinema.py's _download_poster() docstring), so these
        # can be used directly with no re-hosting step.
        image_url = item.get("assetUrl") or None

        events.append(
            ScrapedEvent(
                title=title,
                start_datetime=start_dt,
                end_datetime=end_dt,
                description=description,
                ticket_url=ticket_url,
                external_id=external_id,
                image_url=image_url,
            )
        )

    events.sort(key=lambda e: e.start_datetime)
    return events

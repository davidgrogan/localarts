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
import json
from datetime import datetime

import requests
from bs4 import BeautifulSoup

from app.scrapers.base import ScrapedEvent, ScrapeError

USER_AGENT = "Mozilla/5.0 (compatible; LocalMusicSitePOC/0.1)"


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
            start_dt = datetime.utcfromtimestamp(int(start_ms) / 1000)
        except (TypeError, ValueError):
            continue

        end_dt = None
        end_ms = item.get("endDate")
        if end_ms:
            try:
                end_dt = datetime.utcfromtimestamp(int(end_ms) / 1000)
            except (TypeError, ValueError):
                end_dt = None

        title = (item.get("title") or "Untitled event").strip()
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

        events.append(
            ScrapedEvent(
                title=title,
                start_datetime=start_dt,
                end_datetime=end_dt,
                description=description,
                ticket_url=ticket_url,
                external_id=external_id,
            )
        )

    events.sort(key=lambda e: e.start_datetime)
    return events

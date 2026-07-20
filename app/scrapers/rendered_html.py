"""Headless-browser scraper for venues whose event listings are rendered
entirely by client-side JS -- common for embedded third-party widgets
(Elfsight and similar "Event Calendar" plugins) that Squarespace/Wix/etc.
sites drop in via a Code Block. A plain requests fetch only ever sees the
empty container div these widgets mount into; this module actually loads
the page in a headless Chromium browser, waits for the widget's JS to run,
and hands the *rendered* DOM to the same selector-driven parser used by
html_generic.py.

This is the right tool specifically for the Iron Horse (ironhorse.org):
its calendar turned out to be an Elfsight "Event Calendar" widget with no
public API, rather than anything native to the page's own HTML or
Squarespace's content JSON (confirmed via the scrape preview screen --
the Squarespace JSON's own events collection reported itemCount: 0).

Requires Playwright's browser binaries to be installed once, separately
from the pip install:
    playwright install chromium

Venue scrape_config keys -- the same selector keys as html_generic.py,
plus two more specific to this module:
    item_selector, title_selector, date_selector, date_format, link_selector
    wait_for_selector -- optional CSS selector to wait for before capturing
                          the page (use the event item's own selector so we
                          know the widget has actually finished rendering)
    wait_ms            -- optional extra fixed wait in milliseconds after
                          the page reports "networkidle" (default 3000);
                          bump this if a venue's widget is slow to render
"""
import json

from app.scrapers.base import ScrapeError
from app.scrapers.html_generic import parse as _parse_rendered_dom

DEFAULT_WAIT_MS = 3000
USER_AGENT = "Mozilla/5.0 (compatible; LocalMusicSitePOC/0.1)"


def _config(venue):
    try:
        return json.loads(venue.scrape_config or "{}")
    except json.JSONDecodeError:
        return {}


def fetch_raw(venue):
    if not venue.events_url:
        raise ScrapeError("Venue has no events_url configured.")

    try:
        from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
    except ImportError as exc:
        raise ScrapeError(
            "Playwright isn't installed. Run `pip install -r requirements.txt`, "
            "then `playwright install chromium` once, and try again."
        ) from exc

    config = _config(venue)
    wait_for_selector = config.get("wait_for_selector")
    wait_ms = int(config.get("wait_ms", DEFAULT_WAIT_MS))

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            try:
                page = browser.new_page(user_agent=USER_AGENT)
                page.goto(venue.events_url, wait_until="networkidle", timeout=30000)
                if wait_for_selector:
                    try:
                        page.wait_for_selector(wait_for_selector, timeout=15000)
                    except PlaywrightTimeoutError:
                        # Fall through and capture whatever rendered -- the
                        # scrape preview page will show 0 parsed events plus
                        # the raw HTML for debugging rather than hard-failing.
                        pass
                else:
                    page.wait_for_timeout(wait_ms)
                html = page.content()
            finally:
                browser.close()
    except ScrapeError:
        raise
    except Exception as exc:  # noqa: BLE001 -- surface any Playwright failure to the UI
        raise ScrapeError(f"Headless browser fetch of {venue.events_url} failed: {exc}") from exc

    return html


def parse(raw, venue):
    # Same selector-driven parsing as the generic HTML scraper -- the only
    # difference this module makes is *what* HTML it hands over (rendered
    # vs. raw server response).
    return _parse_rendered_dom(raw, venue)

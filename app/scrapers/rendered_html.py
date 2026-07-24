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
plus a few more specific to this module:
    item_selector, title_selector, date_selector, date_format, link_selector
    wait_for_selector -- optional CSS selector to wait for before capturing
                          the page (use the event item's own selector so we
                          know the widget has actually finished rendering)
    wait_ms            -- optional fixed wait in milliseconds after the DOM
                          is ready, used only when wait_for_selector isn't
                          set (default 3000); bump this if a venue's widget
                          is slow to render. Deliberately does *not* wait
                          for Playwright's "networkidle" state -- plenty of
                          real sites (ad/analytics scripts, chat widgets,
                          background polling) never fully go idle, which
                          timed out real scrapes even after the actual
                          content had long since rendered.
    next_button_selector -- optional Playwright selector (CSS, or "text=..."
                          for a text match) for a "next page/month" control
                          that advances the widget's own internal state
                          without changing the page's URL at all -- some
                          Elfsight Event Calendar widgets (confirmed: 33
                          Hawley) only ever show the *current* month's events
                          on first load, with a "Next Events" button that
                          just re-renders the same DOM in place. Without
                          clicking it ourselves, a venue whose upcoming shows
                          in a wanted category (e.g. category_include:
                          ["Performance"]) haven't started yet this month
                          would always come back with 0 events, even though
                          the fetch/render/parse pipeline is otherwise
                          working correctly. A plain text selector like
                          "text=Next Events" is preferred over the widget's
                          own hashed styled-components class names (which
                          churn across widget versions -- see
                          elfsight_jsonld.py's module docstring) since the
                          visible button label is a much more stable target.
    next_button_clicks -- how many times to click next_button_selector
                          before capturing the page (default 0, i.e. don't
                          click at all). Best-effort: if the click ever
                          fails (button missing/disabled once there's
                          nothing further to page through), just stops
                          clicking and captures whatever's there rather
                          than failing the whole scrape.
"""
import json
import os

from app.scrapers.base import ScrapeError
from app.scrapers.html_generic import parse as _parse_rendered_dom

DEFAULT_WAIT_MS = 3000
USER_AGENT = "Mozilla/5.0 (compatible; LocalMusicSitePOC/0.1)"

# Where to save a debug screenshot when wait_for_selector never finds its
# target in any frame -- a visual dump of exactly what the headless
# browser rendered is a much faster way to diagnose "0 events parsed"
# than guessing from raw HTML text samples (which text-only tools like a
# preview page's truncated raw_sample field can't show at all: a blank
# widget area, a cookie/consent overlay blocking it, a bot-check page,
# etc. all look different in a screenshot but can look identical in text).
DEBUG_SCREENSHOT_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "instance", "debug_screenshots",
)


def _config(venue):
    try:
        return json.loads(venue.scrape_config or "{}")
    except json.JSONDecodeError:
        return {}


def _auto_scroll(page, step=1000, pause_ms=350, max_steps=25):
    """Scroll down the page in increments, pausing briefly at each step.
    Needed for widgets embedded partway down a long page (e.g. 33 Hawley's
    Elfsight calendar sits on the homepage rather than its own dedicated
    /events page, unlike Iron Horse) that lazy-render only once scrolled
    into view -- a plain domcontentloaded wait never triggers those, since
    a headless page's viewport never actually reaches them without this.
    Stops once the page stops growing (or after max_steps), so this is a
    safe no-op (a couple hundred ms) on pages that don't need it at all."""
    previous_height = -1
    for _ in range(max_steps):
        page.evaluate(f"window.scrollBy(0, {step})")
        page.wait_for_timeout(pause_ms)
        current_height = page.evaluate("document.body.scrollHeight")
        if current_height == previous_height:
            break
        previous_height = current_height


def _capture_paged_html(frame, selector, times):
    """Click a "next page/month" control up to `times` times, capturing
    the frame's content once *before* the first click and again after
    every successful click, then gluing every capture together -- rather
    than only capturing once at the end (see next_button_selector in the
    module docstring). That single-capture approach silently lost every
    event shown on an earlier page: confirmed on a real venue whose
    widget *replaces* its displayed events with each click rather than
    accumulating them (clicking Next 3 times to reach a wanted event
    meant the two events shown after clicks 1 and 2 were gone from the
    DOM by the time of the single final capture, even though the fetch
    otherwise worked correctly). Concatenating multiple full HTML
    documents into one string is a bit unusual, but harmless here:
    parse() (in elfsight_jsonld.py) just scans the whole document for
    every <script type="application/ld+json"> tag with BeautifulSoup's
    lenient parser, and already dedupes candidates by (name, start
    datetime) -- so it picks up every page's events with no changes
    needed on the parsing side. Best-effort on the clicking itself: stops
    as soon as a click fails (e.g. the button became disabled/disappeared
    because there's nothing further to page through) rather than treating
    that as a scrape failure."""
    captures = [frame.content()]
    for _ in range(times):
        try:
            frame.click(selector, timeout=5000)
        except Exception:  # noqa: BLE001 -- widget-specific control, many ways to not be there
            break
        frame.wait_for_timeout(800)
        captures.append(frame.content())
    return "\n".join(captures)


def _save_debug_screenshot(page, venue):
    """Best-effort -- a screenshot is a debugging aid, never something
    that should turn a completed scrape into a hard failure."""
    try:
        os.makedirs(DEBUG_SCREENSHOT_DIR, exist_ok=True)
        path = os.path.join(DEBUG_SCREENSHOT_DIR, f"{venue.slug}.png")
        page.screenshot(path=path, full_page=True)
    except Exception:  # noqa: BLE001
        pass


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
    next_button_selector = config.get("next_button_selector")
    next_button_clicks = int(config.get("next_button_clicks", 0))

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            try:
                page = browser.new_page(user_agent=USER_AGENT)
                # "networkidle" (no network activity for 500ms) sounds
                # like the right thing to wait for, but plenty of real
                # sites never actually go fully idle -- ads, analytics
                # beacons, chat widgets, or a Squarespace site's own
                # background polling can keep some request in flight
                # indefinitely, timing this out even though the content
                # we actually care about rendered ages ago. Wait only for
                # the DOM itself to be ready, then rely on
                # wait_for_selector/wait_ms below (which target this
                # venue's specific widget) to know when it's safe to
                # capture the page.
                page.goto(venue.events_url, wait_until="domcontentloaded", timeout=30000)
                _auto_scroll(page)
                if wait_for_selector:
                    # Some widget embeds (confirmed: 33 Hawley's Elfsight
                    # widget) render inside their own <iframe> rather than
                    # directly in the page's own document -- an iframe has a
                    # completely separate DOM, so a selector search scoped to
                    # the main frame never finds it, silently times out, and
                    # we'd capture the empty page shell forever. Try the main
                    # frame first (the common case, e.g. Iron Horse), then
                    # fall back to checking every iframe on the page and use
                    # whichever frame's document actually contains the
                    # selector.
                    matched_frame = None
                    for frame in page.frames:
                        try:
                            frame.wait_for_selector(wait_for_selector, timeout=15000)
                            matched_frame = frame
                            break
                        except PlaywrightTimeoutError:
                            continue
                    if matched_frame is None:
                        _save_debug_screenshot(page, venue)
                        html = page.content()
                    elif next_button_selector and next_button_clicks > 0:
                        html = _capture_paged_html(matched_frame, next_button_selector, next_button_clicks)
                    else:
                        html = matched_frame.content()
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

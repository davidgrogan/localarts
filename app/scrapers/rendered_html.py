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
    description_from_link / description_detail_selector -- same idea as
                          html_generic.py's option of the same name (follow
                          each item's own link out to its detail page for a
                          description that isn't on the listing page), but
                          done differently here: html_generic.py's version
                          does that follow-through fetch with a plain
                          `requests.get()` at parse time, which is fine for
                          a server that only blocks non-browser requests on
                          its *listing* page but not elsewhere. Confirmed
                          on Quonk's Ticket Tailor listing (see README.md):
                          even after fixing the listing fetch's 403 with a
                          browser-like `user_agent`, the *same* plain
                          requests.get() from html_generic.py's follow-
                          through step got blocked too -- Ticket Tailor's
                          bot-management is filtering on more than the
                          User-Agent header alone (likely a TLS/header
                          fingerprint a plain `requests` call can't fake,
                          the same class of check a real browser -- which
                          is exactly what this module already drives --
                          passes automatically). So when this module sees
                          these two keys, it does the follow-through
                          fetches itself, one per item, reusing the same
                          Playwright page/browser that already got past
                          whatever blocked the plain-requests version, and
                          writes each result into the captured listing HTML
                          as a `<div class="__prefetched_description">`
                          right inside its matching item -- at which point
                          it's just another inline description as far as
                          html_generic.py's parser is concerned, so set
                          `description_selector` to
                          `.__prefetched_description` too and no further
                          plain-requests fetch happens at parse time at
                          all. Best-effort per item: a single detail-page
                          fetch failing just leaves that one event's
                          description empty rather than failing the scrape.
"""
import json
import os

from bs4 import BeautifulSoup

from app.scrapers.base import ScrapeError
from app.scrapers.html_generic import parse as _parse_rendered_dom
from app.scrapers.html_generic import _page_origin, _resolve_url

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


def _wait_for_stable_item_count(frame, selector, poll_ms=500, max_polls=20):
    """`wait_for_selector` only waits for the *first* match to appear --
    fine for a widget that mounts its whole event list in one shot, but
    not for one that hydrates items in progressively (one API response
    at a time, or one-by-one via JS) rather than all at once. Confirmed
    on Quonk's Ticket Tailor listing: a scrape captured right after the
    first item appeared came back with only 1 of 5 real events, and a
    garbled date on that one besides -- consistent with grabbing the DOM
    mid-hydration rather than once it's actually settled. This polls the
    matching element count every `poll_ms` until two consecutive checks
    agree (or `max_polls` is hit), so the page gets a chance to finish
    filling in before anything captures it. A safe no-op (a few hundred
    ms) on any widget that was already fully mounted by the time
    wait_for_selector first succeeded."""
    previous_count = -1
    for _ in range(max_polls):
        current_count = len(frame.query_selector_all(selector))
        if current_count == previous_count:
            break
        previous_count = current_count
        frame.wait_for_timeout(poll_ms)


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


PREFETCHED_DESCRIPTION_CLASS = "__prefetched_description"


def _prefetch_descriptions(page, html, venue, config):
    """See description_from_link/description_detail_selector in the
    module docstring -- follows each listing item's own link out to its
    detail page for a description, using the *same* Playwright page that
    already got past this venue's bot-blocking rather than a plain
    requests.get() (which failed here even with a browser-like
    User-Agent -- see README.md's Quonk write-up). Writes each result
    back into the listing HTML as a `<div class="__prefetched_description">`
    inside its matching item, so html_generic.py's ordinary
    description_selector handling picks it up with no changes needed
    there -- set description_selector to match this class too.

    Best-effort per item: if a single detail-page fetch or parse fails,
    that one event is just left with no description rather than failing
    the whole scrape."""
    item_sel = config.get("item_selector")
    link_sel = config.get("link_selector")
    title_sel = config.get("title_selector")
    detail_sel = config.get("description_detail_selector")
    if not item_sel or not detail_sel:
        return html

    soup = BeautifulSoup(html, "html.parser")
    page_origin = _page_origin(venue.events_url)

    for item in soup.select(item_sel):
        if link_sel:
            link_el = item.select_one(link_sel)
        else:
            title_el = item.select_one(title_sel) if title_sel else None
            link_el = None
            if title_el is not None:
                link_el = title_el if title_el.name == "a" else title_el.find("a")
        if not link_el or not link_el.has_attr("href"):
            continue

        detail_url = _resolve_url(link_el["href"], page_origin)
        if not detail_url:
            continue

        try:
            page.goto(detail_url, wait_until="domcontentloaded", timeout=30000)
            detail_html = page.content()
        except Exception:  # noqa: BLE001 -- one bad detail page shouldn't sink the scrape
            continue

        detail_soup = BeautifulSoup(detail_html, "html.parser")
        detail_el = detail_soup.select_one(detail_sel)
        if not detail_el:
            continue

        marker = soup.new_tag("div")
        marker["class"] = PREFETCHED_DESCRIPTION_CLASS
        marker.string = detail_el.get_text(separator=" ", strip=True)
        item.append(marker)

    return str(soup)


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
    user_agent = config.get("user_agent") or USER_AGENT

    try:
        with sync_playwright() as p:
            # --disable-blink-features=AutomationControlled + the
            # navigator.webdriver override just below exist because of
            # Quonk's Ticket Tailor listing specifically: confirmed via a
            # direct side-by-side check that the *identical* URL returns
            # all 5 real upcoming events (correct dates) in an ordinary,
            # human-driven Chrome tab, but consistently returned just one
            # stale/wrong-dated event through this module's plain
            # Playwright session, even after the fetch itself stopped
            # 403'ing and after waiting for the item count to stabilize
            # (see this file's earlier revisions). That pattern -- same
            # URL, different content, only when automated -- points at
            # `navigator.webdriver` (true by default in an unmodified
            # Playwright/Selenium session, false in a real browser), a
            # well-known signal bot-management products key off of to
            # serve automated visitors a cached/fallback snapshot instead
            # of the live page. Ticket Tailor's own robots.txt explicitly
            # allows crawling these exact pages for any user-agent, so
            # this is closing a false-positive rather than getting around
            # a real access restriction.
            browser = p.chromium.launch(args=["--disable-blink-features=AutomationControlled"])
            try:
                page = browser.new_page(user_agent=user_agent)
                page.add_init_script(
                    "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
                )
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
                    else:
                        # wait_for_selector above only confirms the *first*
                        # match appeared -- some widgets hydrate their item
                        # list progressively rather than all at once, so
                        # give it a chance to finish filling in before
                        # capturing (see _wait_for_stable_item_count's
                        # docstring for the real-world case this fixes).
                        _wait_for_stable_item_count(matched_frame, wait_for_selector)
                        if next_button_selector and next_button_clicks > 0:
                            html = _capture_paged_html(matched_frame, next_button_selector, next_button_clicks)
                        else:
                            html = matched_frame.content()
                else:
                    page.wait_for_timeout(wait_ms)
                    html = page.content()

                if config.get("description_from_link"):
                    html = _prefetch_descriptions(page, html, venue, config)
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

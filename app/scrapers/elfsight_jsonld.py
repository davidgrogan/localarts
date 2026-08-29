"""Headless-browser scraper for the Iron Horse / Parlor Room's Elfsight
"Event Calendar" widget.

Despite the module name (kept for continuity with existing venue rows'
source_type -- renaming it would mean an extra migration step for no
real benefit), this module no longer reads any JSON-LD at all. It used
to: earlier revisions of this same widget embedded a schema.org Event
`<script type="application/ld+json">` tag inside each card, which was
the only source for the one field the widget's visible date badge never
shows (the year). That's gone now -- see below -- so everything this
module extracts comes straight out of the widget's own visible DOM.

What changed (confirmed directly against ironhorse.org in August 2026,
after David reported the site "changed the way it lists its events"):
the Iron Horse's whole site got redesigned, folding its own listing plus
every sibling "Parlor Room Collective" venue's listing (The Parlor Room,
Musician's Workshop, Black Birch Vineyard, etc.) into one unified,
venue-filterable calendar on /shows. Two things about the underlying
Elfsight widget changed along with it, confirmed via a real
JS-executing browser (a plain `requests`/WebFetch-style fetch can't see
any of this -- it only ever returns the empty mount div, since the
widget is entirely client-rendered):

1. The widget's event cards now render inside an *open shadow root*
   (`document.querySelector(".es-embed-root").shadowRoot`), confirmed by
   inspecting the live DOM directly. Shadow DOM content is deliberately
   excluded when a page's HTML is serialized (`outerHTML`,
   `page.content()`, etc.) -- it's invisible to *any* approach that
   fetches rendered HTML as a string and hands it to BeautifulSoup,
   which is exactly what the old version of this module (and
   rendered_html.py generally) does. There's no selector fix for that;
   the only way to reach this content is to query the live page directly
   -- which, happily, Playwright's own selector engine already does:
   unlike a plain CSS `querySelectorAll` call from outside, Playwright's
   `page.query_selector_all()` / `page.eval_on_selector_all()` pierce
   open shadow roots automatically. So this module's fetch_raw() talks
   to the live Playwright `page` object directly (via
   `eval_on_selector_all`) instead of capturing a page.content() string
   for a separate parse() step to work through -- there's no HTML for
   parse() to receive here, just the structured field values already
   pulled out of the shadow-rooted DOM, serialized as JSON.

2. The widget no longer embeds a schema.org Event JSON-LD script tag
   anywhere in the page at all (confirmed: the only JSON-LD present
   is generic WebSite/Organization/LocalBusiness, nothing per-event).
   That means there's no year source left to trust for the calendar
   date -- see _infer_year()'s docstring for how this module now infers
   it instead, and _find_month_day_time()'s docstring for what field
   the location filtering below reads instead of JSON-LD's
   `location.name` (short version: the same visible "location" text the
   widget already shows on each card, which was sitting right there in
   the DOM the whole time).

The good news, confirmed directly: none of the widget's own event-card
class names changed (`.eapp-events-calendar-grid-item-container`,
`-name`, `-time`, `-location`, `-category`, the date badge's `-month`/
`-day`, etc. are all exactly the same stable, non-hashed classes as
before). This rewrite only had to change *how* those selectors get
reached (via Playwright's shadow-piercing queries instead of
BeautifulSoup over captured HTML) and *what* supplies the year (an
inference instead of JSON-LD), not which fields exist or what they're
named.

Venue scrape_config keys (unchanged from before):
    wait_for_selector  -- optional CSS selector to wait for before
                           extracting; defaults to this module's own
                           card-container selector, which is virtually
                           always the right thing to wait for. Kept
                           configurable for parity with rendered_html.py
                           (33 Hawley's venue row already sets this
                           explicitly to the same default).
    wait_ms            -- unused by this module (kept accepted, but
                           ignored) now that extraction polls for a
                           stable card count instead of a fixed delay --
                           see fetch_raw()'s docstring for why a fixed
                           wait_ms turned out not to be reliable here.
    location_match  -- optional list of substrings (case-insensitive)
                        matched against each event's visible "location"
                        text (e.g. "THE IRON HORSE"). Defaults to
                        [venue.name] if omitted.
    include_all_locations  -- optional bool; if true, skips location
                               filtering entirely.
    category_include  -- optional list of substrings (case-insensitive)
                          matched against each event's visible category
                          text. Omit to import every category.
"""
import json
import os
import re
from datetime import date, datetime as dt, timedelta

from app.scrapers.base import ScrapedEvent, ScrapeError
from app.scrapers.rendered_html import _auto_scroll

# Card-level selectors -- all confirmed stable across the summer 2026
# redesign (see module docstring). Bounding every other lookup to a
# single `.eapp-events-calendar-grid-item-container` element (rather
# than searching the whole page) is what the JS extractor below relies
# on to keep each event's fields from bleeding into its neighbors'.
_CARD_CONTAINER_SELECTOR = ".eapp-events-calendar-grid-item-container"

# Matches just a "7:00 PM" (or "7:00 PM - 9:00 PM") portion of the
# card's time text -- kept from the pre-redesign version of this module
# since the widget has, in the past, rendered a nested UTC-offset
# annotation in this same element (e.g. "7:00 PM UTC-4") that a plain
# strptime on the whole string would choke on. Not confirmed to still
# happen post-redesign, but harmless to stay tolerant of either way.
_TIME_RE = re.compile(r"\d{1,2}:\d{2}\s*[AaPp][Mm]")
_TIME_FORMAT = "%I:%M %p"

DEBUG_SCREENSHOT_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "instance", "debug_screenshots",
)

# The JS run once per matched card (via eval_on_selector_all) to pull
# every field this module needs directly out of the shadow-rooted DOM.
# Written as a single function so one round trip into the page extracts
# every card at once, rather than one round trip per field per card.
_EXTRACT_JS = """
(cards) => cards.map(card => {
  function text(sel) {
    const el = card.querySelector(sel);
    return el ? el.textContent.trim() : null;
  }
  // Category is its own small quirk: some events carry more than one
  // category tag (e.g. "Blues" + "Jazz") as separate child elements
  // that all *also* carry the outer selector's class name, so
  // card.querySelector(...) alone only ever finds the first one and a
  // plain .textContent on it would miss the rest. Look for nested
  // ".eapp-events-calendar-category-item" children first (there can be
  // several); fall back to the outer element's own text if there
  // aren't any (single-category cards don't always nest one).
  let category = null;
  const catEl = card.querySelector('.eapp-events-calendar-grid-item-category');
  if (catEl) {
    const items = Array.from(catEl.querySelectorAll('.eapp-events-calendar-category-item'));
    category = items.length
      ? items.map(i => i.textContent.trim()).join(', ')
      : catEl.textContent.trim();
  }
  const img = card.querySelector('.eapp-events-calendar-grid-item-imageContainer img');
  return {
    month: text('.eapp-events-calendar-date-element-month'),
    day: text('.eapp-events-calendar-date-element-day'),
    name: text('.eapp-events-calendar-grid-item-name'),
    time: text('.eapp-events-calendar-time-time'),
    location: text('.eapp-events-calendar-grid-item-location'),
    category: category,
    image: img ? img.src : null,
  };
})
"""


def _config(venue):
    try:
        return json.loads(venue.scrape_config or "{}")
    except json.JSONDecodeError:
        return {}


def _save_debug_screenshot(page, venue):
    try:
        os.makedirs(DEBUG_SCREENSHOT_DIR, exist_ok=True)
        path = os.path.join(DEBUG_SCREENSHOT_DIR, f"{venue.slug}.png")
        page.screenshot(path=path, full_page=True)
    except Exception:  # noqa: BLE001 -- a screenshot is a debugging aid, never a hard failure
        pass


def _wait_for_stable_card_count(page, selector, poll_ms=750, max_polls=15):
    """Poll the (shadow-piercing) match count for `selector` until two
    consecutive checks agree, or `max_polls` is hit. Needed because this
    widget doesn't render all at once: confirmed directly (repeated live
    navigations) that its cards can take anywhere from ~2 to ~15+ seconds
    to appear after the widget's own root element shows up, so waiting
    only for wait_for_selector's first match -- which fires the instant
    the *first* card mounts -- risks capturing a small, incomplete subset
    of a widget that's still hydrating the rest of a 100+ event feed."""
    previous_count = -1
    for _ in range(max_polls):
        current_count = len(page.query_selector_all(selector))
        if current_count == previous_count and current_count > 0:
            break
        previous_count = current_count
        page.wait_for_timeout(poll_ms)


def fetch_raw(venue):
    """Returns a JSON string (a list of per-event field dicts), not HTML
    -- see module docstring for why there's no HTML for a separate
    parse() step to work through here. `raw` is still the name used by
    the rest of the scraper framework (ScrapeRun.raw_sample, the scrape
    preview page, etc.), it just isn't markup this time."""
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
    wait_for_selector = config.get("wait_for_selector") or _CARD_CONTAINER_SELECTOR
    user_agent = config.get("user_agent") or "Mozilla/5.0 (compatible; LocalMusicSitePOC/0.1)"

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(args=["--disable-blink-features=AutomationControlled"])
            try:
                page = browser.new_page(user_agent=user_agent)
                page.add_init_script(
                    "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
                )
                page.goto(venue.events_url, wait_until="domcontentloaded", timeout=30000)
                # The widget only starts rendering once it's actually
                # scrolled into view -- confirmed live: a fresh page load
                # left the whole calendar section blank indefinitely (a
                # real scrape came back "[]" after the full wait_for_selector
                # timeout, with a debug screenshot showing nothing below the
                # page's header/hero at all), no matter how long the
                # timeout was, because the widget sits well below the
                # viewport on first load and its own lazy-render never got
                # triggered. rendered_html.py's _auto_scroll() (reused here
                # rather than duplicated) exists for exactly this pattern --
                # see its own docstring.
                _auto_scroll(page)
                try:
                    # A generous timeout: confirmed live, this widget has
                    # taken over 10 seconds to mount its first card on a
                    # cold load, well past rendered_html.py's 3-second
                    # default wait_ms (which is why this module doesn't
                    # use a fixed wait_ms at all anymore -- see module
                    # docstring's wait_ms entry).
                    page.wait_for_selector(wait_for_selector, timeout=30000)
                except PlaywrightTimeoutError:
                    # Never found a single card -- either the widget is
                    # down, this venue's page no longer has it at all, or
                    # it's just unusually slow today. Save a screenshot
                    # for whoever's debugging a "0 events" run and return
                    # an empty result rather than failing the scrape
                    # outright (see base.py: parse() should never raise
                    # just for "no events found").
                    _save_debug_screenshot(page, venue)
                    return "[]"

                _wait_for_stable_card_count(page, _CARD_CONTAINER_SELECTOR)
                cards = page.eval_on_selector_all(_CARD_CONTAINER_SELECTOR, _EXTRACT_JS)
            finally:
                browser.close()
    except ScrapeError:
        raise
    except Exception as exc:  # noqa: BLE001 -- surface any Playwright failure to the UI
        raise ScrapeError(f"Headless browser fetch of {venue.events_url} failed: {exc}") from exc

    return json.dumps(cards)


def _parse_time_text(text):
    try:
        return dt.strptime(text, _TIME_FORMAT).time()
    except ValueError:
        return None


def _find_time_range(time_text):
    if not time_text:
        return None, None
    matches = _TIME_RE.findall(time_text)
    if not matches:
        return None, None
    start_time = _parse_time_text(matches[0])
    end_time = _parse_time_text(matches[1]) if len(matches) > 1 else None
    return start_time, end_time


def _infer_year(month, day, today):
    """The widget's visible date badge (e.g. "Aug 29") never shows a
    year, and -- unlike before the redesign -- there's no JSON-LD left
    to borrow one from either (see module docstring). But a venue's
    "upcoming shows" widget only ever lists events at or after today, so
    the year can be inferred from that alone: build the date assuming
    this calendar year, and if that lands before today (a Dec-listed
    widget showing a Jan event, the one case this matters for), it must
    actually mean next year instead. Handles Feb 29 on a non-leap
    "this year" by falling through to next year the same way any other
    already-past date would (ValueError there means the same thing a
    date being in the past means: not this year)."""
    try:
        candidate = date(today.year, month, day)
    except ValueError:
        return today.year + 1
    if candidate < today:
        return today.year + 1
    return today.year


def parse(raw, venue):
    try:
        cards = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return []

    config = _config(venue)
    include_all = bool(config.get("include_all_locations"))
    match_terms = config.get("location_match") or [venue.name]
    match_terms = [t.lower() for t in match_terms if t]
    category_include = [t.lower() for t in config.get("category_include", []) if t]

    today = dt.now().date()
    events = []
    seen_external_ids = set()

    for card in cards:
        name = (card.get("name") or "").strip()
        month_text = card.get("month")
        day_text = card.get("day")
        if not name or not month_text or not day_text:
            continue

        location_text = (card.get("location") or "")
        if not include_all and match_terms:
            if not any(term in location_text.lower() for term in match_terms):
                continue

        category_text = card.get("category")
        if category_include:
            haystack = (category_text or "").lower()
            if not any(term in haystack for term in category_include):
                continue

        try:
            month = dt.strptime(month_text, "%b").month
            day = int(day_text)
        except ValueError:
            continue

        year = _infer_year(month, day, today)

        start_time, end_time = _find_time_range(card.get("time"))
        hour = start_time.hour if start_time is not None else 0
        minute = start_time.minute if start_time is not None else 0

        try:
            start_dt = dt(year, month, day, hour, minute)
        except ValueError:
            continue

        end_dt = None
        if end_time is not None:
            end_dt = dt(start_dt.year, month, day, end_time.hour, end_time.minute)
            if end_dt <= start_dt:
                # An end time earlier than the start time means the show
                # runs past midnight (e.g. "11:00 PM - 1:00 AM").
                end_dt += timedelta(days=1)

        external_id = f"{name}-{start_dt.isoformat()}"
        if external_id in seen_external_ids:
            continue
        seen_external_ids.add(external_id)

        events.append(
            ScrapedEvent(
                title=name,
                start_datetime=start_dt,
                end_datetime=end_dt,
                description="",
                # The widget's cards open an in-widget lightbox rather
                # than linking out to a distinct page (confirmed: no
                # <a href> anywhere inside a card), same as before the
                # redesign -- fall back to the venue's own events page.
                ticket_url=venue.events_url or None,
                genre=category_text,
                image_url=card.get("image"),
                external_id=external_id,
            )
        )

    events.sort(key=lambda e: e.start_datetime)
    return events

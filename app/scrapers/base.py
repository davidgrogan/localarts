"""Scraper framework.

Each "source_type" a Venue can have maps to a small module in this
package exposing two functions:

    fetch_raw(venue) -> str
        Hit the network and return the raw payload (JSON text, ICS
        text, or HTML) as a string. No parsing here.

    parse(raw, venue) -> list[ScrapedEvent]
        Turn the raw payload into a list of ScrapedEvent candidates.
        Should never raise on "no events found" -- return [] instead.
        Only raise for genuine fetch/parse failures.

`run_scrape()` below is the orchestrator used by both the web UI and
any future scheduled job: it fetches, parses, logs a ScrapeRun, and
(unless dry_run) upserts Event rows.

Adding a new venue whose site doesn't fit an existing source_type means
writing one new module here and registering it in SOURCE_TYPES below --
that's the extension point the "help me work through scraping" workflow
is built around.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta

from app.utils import local_now
from typing import Optional

from app.models import db, Event, ScrapeRun

RAW_SAMPLE_MAX_CHARS = 12000

# How the daily "scrape all venues" run keeps already-approved (public)
# events honest, beyond just creating new ones -- see run_scrape()'s
# docstring for the full behavior. Kept as module constants rather than
# per-venue config since these are policy calls (how cautious to be
# about a listing changing), not something that varies venue to venue.
DATE_FMT = "%b %d, %Y %I:%M %p"
# Only events starting within this many days are checked for having
# disappeared from a venue's current listing -- a show three months out
# not appearing yet doesn't mean anything (it may just be outside a
# paginated feed's window), but one that was on the list yesterday and
# is due next week and is gone today is worth a second look.
MISSING_CHECK_WINDOW_DAYS = 21
# Require this many consecutive scrapes to come back without the event
# before treating it as a likely cancellation -- one missed page load or
# a venue site hiccup shouldn't be enough to pull a real show down.
MISSING_STREAK_THRESHOLD = 2


@dataclass
class ScrapedEvent:
    title: str
    start_datetime: datetime
    external_id: str
    end_datetime: Optional[datetime] = None
    description: str = ""
    ticket_url: Optional[str] = None
    # Not every venue's feed publishes these -- leave as None when absent.
    genre: Optional[str] = None
    image_url: Optional[str] = None


class ScrapeError(Exception):
    """Raised when fetching or parsing a venue's feed fails outright."""


def _load_source_types():
    # Imported lazily / locally to avoid circular imports at module load.
    from app.scrapers import (
        squarespace_json,
        ical_feed,
        html_generic,
        rendered_html,
        elfsight_jsonld,
        haze_calendar,
    )

    return {
        "squarespace_json": squarespace_json,
        "ical": ical_feed,
        "html": html_generic,
        "rendered_html": rendered_html,
        "elfsight_jsonld": elfsight_jsonld,
        "haze_calendar": haze_calendar,
    }


def get_scraper(source_type: str):
    modules = _load_source_types()
    if source_type not in modules:
        raise ScrapeError(
            f"Unknown source_type '{source_type}'. Available: {', '.join(modules)}"
        )
    return modules[source_type]


def preview_scrape(venue):
    """Fetch + parse only -- no DB writes. Used by the scrape preview page
    so a venue's config can be iterated on safely before importing.
    """
    if venue.source_type == "manual":
        return {"raw_sample": "(manual venue -- no scraping configured)", "events": []}

    scraper = get_scraper(venue.source_type)
    raw = scraper.fetch_raw(venue)
    events = scraper.parse(raw, venue)
    return {
        "raw_sample": _raw_sample(raw),
        "events": events,
    }


_RAW_SAMPLE_SIGNALS = (
    "elfsightcdn",
    "eapp-events-calendar",
    "tribe-events",
    # Quonk's Ticket Tailor listing (see rendered_html.py/README.md) --
    # its header/hero markup alone ran past RAW_SAMPLE_MAX_CHARS, the
    # same problem as the Squarespace case below, so a preview centered
    # on <body> never reached the actual event list either.
    "events-listing__item",
)


def _raw_sample(raw):
    """A fixed raw[:N] slice sounds like a reasonable preview, but a real
    page's <head> (fonts, meta tags, inlined CSS) or its header/nav markup
    can each individually run well past RAW_SAMPLE_MAX_CHARS on their own
    -- confirmed on a real Squarespace site, where even starting the slice
    at <body> only got as far as the nav bar, nowhere near the actual
    calendar widget. Prefer to center the sample on whichever known
    widget-related string shows up first in the raw HTML (a real hit
    there is the most direct evidence of whether the venue's calendar
    widget rendered at all), falling back to <body>, then the very start
    of the string, so this stays useful for diagnosing "0 events parsed"
    regardless of how much unrelated markup precedes the interesting
    part.

    Deliberately excludes generic strings like "fullcalendar" or plain
    "elfsight" -- a real Squarespace page was seen shipping an empty
    `<style data-fullcalendar="">` boilerplate tag (and separately, plain
    "elfsight" can appear in unrelated analytics/meta noise) near the very
    top of <head>, which matched every single time regardless of what the
    actual widget did, silently anchoring the sample on the same early,
    uninformative slice on every attempt. Only match strings confirmed to
    appear exclusively as part of the real rendered widget content."""
    lowered = raw.lower()
    signal_index = -1
    for signal in _RAW_SAMPLE_SIGNALS:
        idx = lowered.find(signal)
        if idx != -1 and (signal_index == -1 or idx < signal_index):
            signal_index = idx
    if signal_index != -1:
        start = max(signal_index - 500, 0)
    else:
        body_index = lowered.find("<body")
        start = body_index if body_index != -1 else 0
    return raw[start:start + RAW_SAMPLE_MAX_CHARS]


def run_scrape(venue, approve_new=False):
    """Fetch, parse, log a ScrapeRun, and upsert Event rows.

    New events are created with is_approved=approve_new so they can be
    reviewed on the scrape preview page before appearing on the public
    calendar (unless the caller explicitly approves on import).

    Existing events (matched on venue_id + external_id) are always
    updated in place with the latest scraped fields -- the public site
    should never show stale info. But if an event that's already
    *approved* (live on the public calendar) comes back with a different
    start time or title, that update is also flagged: `needs_review` is
    set and `review_note` records what changed, so it surfaces in the
    admin Review queue's "Changed" section instead of silently mutating
    something a visitor may have already seen. New/still-pending events
    are simply updated with no flag -- they haven't been reviewed yet
    either way.

    Separately, any *approved* scraped event starting within the next
    MISSING_CHECK_WINDOW_DAYS that this run's results *don't* include
    gets its `missing_streak` bumped. Once that streak reaches
    MISSING_STREAK_THRESHOLD (two scrapes in a row without it), the
    event is treated as a likely cancellation: unpublished
    (is_approved=False) and flagged, landing in the Review queue's
    "Possibly cancelled" section rather than just vanishing. This check
    is skipped entirely if the scrape came back with zero events, since
    an empty result is far more likely to mean the venue's page changed
    or the fetch broke than that everything got cancelled at once.
    """
    run = ScrapeRun(venue_id=venue.id, status="success")

    try:
        result = preview_scrape(venue)
    except Exception as exc:  # noqa: BLE001 -- want to log any failure
        run.status = "error"
        run.message = str(exc)
        run.events_found = 0
        db.session.add(run)
        venue.last_scraped_at = local_now()
        db.session.commit()
        raise

    raw_sample = result["raw_sample"]
    events = result["events"]
    # local_now(), not datetime.utcnow() -- see app/utils.py's SITE_TIMEZONE
    # docstring. This gets compared against Event.start_datetime below (the
    # "did an approved show quietly disappear from the feed" check), which
    # is naive local wall-clock, not true UTC.
    now = local_now()

    created = 0
    updated = 0
    flagged_changed = 0
    scraped_external_ids = set()

    for scraped in events:
        scraped_external_ids.add(scraped.external_id)
        existing = Event.query.filter_by(
            venue_id=venue.id, external_id=scraped.external_id
        ).first()
        if existing:
            if existing.is_approved and (
                existing.start_datetime != scraped.start_datetime
                or existing.title != scraped.title
            ):
                notes = []
                if existing.title != scraped.title:
                    notes.append(f"Title changed from “{existing.title}” to “{scraped.title}”")
                if existing.start_datetime != scraped.start_datetime:
                    notes.append(
                        f"Time changed from {existing.start_datetime.strftime(DATE_FMT)} "
                        f"to {scraped.start_datetime.strftime(DATE_FMT)}"
                    )
                existing.needs_review = True
                existing.review_note = (
                    "; ".join(notes) + f" (seen on scrape run {now.strftime('%b %d, %Y')})"
                )
                flagged_changed += 1

            existing.title = scraped.title
            existing.start_datetime = scraped.start_datetime
            existing.end_datetime = scraped.end_datetime
            existing.description = scraped.description
            existing.ticket_url = scraped.ticket_url
            existing.genre = scraped.genre
            existing.image_url = scraped.image_url
            existing.source = "scraped"
            existing.last_seen_at = now
            existing.missing_streak = 0
            updated += 1
        else:
            new_event = Event(
                venue_id=venue.id,
                title=scraped.title,
                start_datetime=scraped.start_datetime,
                end_datetime=scraped.end_datetime,
                description=scraped.description,
                ticket_url=scraped.ticket_url,
                genre=scraped.genre,
                image_url=scraped.image_url,
                source="scraped",
                external_id=scraped.external_id,
                is_approved=approve_new,
                last_seen_at=now,
            )
            # Brand-new events only -- an existing event's tags are never
            # touched here, so an admin's own tagging (or override of the
            # venue default) survives every future re-scrape.
            if venue.default_event_type:
                new_event.event_types = [venue.default_event_type]
            db.session.add(new_event)
            created += 1

    flagged_cancelled = 0
    if events:  # an empty result almost certainly means a broken scrape, not mass cancellations
        horizon = now + timedelta(days=MISSING_CHECK_WINDOW_DAYS)
        missing_candidates = Event.query.filter(
            Event.venue_id == venue.id,
            Event.source == "scraped",
            Event.is_approved.is_(True),
            Event.start_datetime >= now,
            Event.start_datetime <= horizon,
        ).all()
        for candidate in missing_candidates:
            if candidate.external_id in scraped_external_ids:
                continue
            candidate.missing_streak = (candidate.missing_streak or 0) + 1
            if candidate.missing_streak >= MISSING_STREAK_THRESHOLD:
                candidate.is_approved = False
                candidate.needs_review = True
                candidate.review_note = (
                    f"Not found in the last {candidate.missing_streak} scrapes as of "
                    f"{now.strftime('%b %d, %Y')} -- likely cancelled. Restore it if it's "
                    "actually still happening."
                )
                flagged_cancelled += 1

    run.events_found = len(events)
    run.events_created = created
    run.events_updated = updated
    run.raw_sample = raw_sample
    if flagged_changed or flagged_cancelled:
        run.message = (
            f"{flagged_changed} changed event(s) flagged for review, "
            f"{flagged_cancelled} event(s) auto-hidden as likely cancelled."
        )
    venue.last_scraped_at = now

    db.session.add(run)
    db.session.commit()

    return run

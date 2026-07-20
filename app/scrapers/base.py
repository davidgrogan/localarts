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
from datetime import datetime
from typing import Optional

from app.models import db, Event, ScrapeRun

RAW_SAMPLE_MAX_CHARS = 8000


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
        "raw_sample": raw[:RAW_SAMPLE_MAX_CHARS],
        "events": events,
    }


def run_scrape(venue, approve_new=False):
    """Fetch, parse, log a ScrapeRun, and upsert Event rows.

    New events are created with is_approved=approve_new so they can be
    reviewed on the scrape preview page before appearing on the public
    calendar (unless the caller explicitly approves on import).
    Existing events (matched on venue_id + external_id) are updated in
    place without touching their current approval state.
    """
    run = ScrapeRun(venue_id=venue.id, status="success")

    try:
        result = preview_scrape(venue)
    except Exception as exc:  # noqa: BLE001 -- want to log any failure
        run.status = "error"
        run.message = str(exc)
        run.events_found = 0
        db.session.add(run)
        venue.last_scraped_at = datetime.utcnow()
        db.session.commit()
        raise

    raw_sample = result["raw_sample"]
    events = result["events"]

    created = 0
    updated = 0
    for scraped in events:
        existing = Event.query.filter_by(
            venue_id=venue.id, external_id=scraped.external_id
        ).first()
        if existing:
            existing.title = scraped.title
            existing.start_datetime = scraped.start_datetime
            existing.end_datetime = scraped.end_datetime
            existing.description = scraped.description
            existing.ticket_url = scraped.ticket_url
            existing.genre = scraped.genre
            existing.image_url = scraped.image_url
            existing.source = "scraped"
            updated += 1
        else:
            db.session.add(
                Event(
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
                )
            )
            created += 1

    run.events_found = len(events)
    run.events_created = created
    run.events_updated = updated
    run.raw_sample = raw_sample
    venue.last_scraped_at = datetime.utcnow()

    db.session.add(run)
    db.session.commit()

    return run

"""SQLAlchemy models for the local music site POC.

Core tables: Venue, Artist, Event (a show), plus a many-to-many
Event<->Artist association, and ScrapeRun which logs every attempt to
pull events from a venue's feed/website so the aggregation workflow is
observable and debuggable from within the app.
"""
from datetime import datetime

from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()


# Many-to-many: a show can feature multiple artists; an artist can play
# many shows across many venues.
event_artists = db.Table(
    "event_artists",
    db.Column("event_id", db.Integer, db.ForeignKey("event.id"), primary_key=True),
    db.Column("artist_id", db.Integer, db.ForeignKey("artist.id"), primary_key=True),
)

# Many-to-many: an event can carry more than one category tag (e.g. a
# show that's also a fundraiser might be both "Music" and "Benefit").
event_event_types = db.Table(
    "event_event_types",
    db.Column("event_id", db.Integer, db.ForeignKey("event.id"), primary_key=True),
    db.Column("event_type_id", db.Integer, db.ForeignKey("event_type.id"), primary_key=True),
)


class EventType(db.Model):
    """A reusable category tag for events (e.g. "Music", "Exhibition",
    "Lecture", "Performance"). Deliberately separate from Event.genre
    (a finer-grained music genre like "Jazz"/"Folk" pulled straight from
    a venue's own feed): this is the broader, curated category an admin
    picks so a mixed calendar -- like Smith College's, which covers
    everything from concerts to art exhibitions -- can be filtered down
    to just what a visitor cares about. Created ad hoc from the event
    form or a venue's "default tag" field rather than a dedicated admin
    screen; see app/utils.py's get_or_create_event_type().
    """

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), unique=True, nullable=False)
    slug = db.Column(db.String(80), unique=True, nullable=False)

    events = db.relationship("Event", secondary=event_event_types, back_populates="event_types")

    def __repr__(self):
        return f"<EventType {self.name}>"


class Venue(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    slug = db.Column(db.String(200), unique=True, nullable=False)

    address = db.Column(db.String(300))
    city = db.Column(db.String(120))
    state = db.Column(db.String(20))

    website_url = db.Column(db.String(500))
    # The specific page/feed the scraper should pull from (may differ
    # from the homepage, e.g. a venue's /shows or /events sub-page, or
    # an .ics feed URL).
    events_url = db.Column(db.String(500))

    # How to interpret events_url. One of: "manual", "ical",
    # "squarespace_json", "html".
    source_type = db.Column(db.String(50), default="manual", nullable=False)

    # Free-form JSON (stored as text) holding scraper-specific settings,
    # e.g. CSS selectors for an "html" source, or notes on quirks of a
    # particular venue's feed. Edited/iterated on via the scrape preview
    # tool in the UI.
    scrape_config = db.Column(db.Text, default="{}")

    # Applied automatically to brand-new events at this venue (manual adds
    # that don't pick a tag themselves, and scraped imports -- see
    # run_scrape()'s docstring) -- e.g. Iron Horse/Parlor Room default to
    # "Music" so scraped shows don't all need tagging by hand. Always
    # overridable per event; never re-applied to an event that already has
    # tags.
    default_event_type_id = db.Column(db.Integer, db.ForeignKey("event_type.id"))
    default_event_type = db.relationship("EventType")

    last_scraped_at = db.Column(db.DateTime)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    events = db.relationship("Event", back_populates="venue", cascade="all, delete-orphan")
    scrape_runs = db.relationship(
        "ScrapeRun", back_populates="venue", cascade="all, delete-orphan",
        order_by="desc(ScrapeRun.run_at)",
    )

    def __repr__(self):
        return f"<Venue {self.name}>"


class Artist(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    slug = db.Column(db.String(200), unique=True, nullable=False)

    hometown = db.Column(db.String(120))
    genre = db.Column(db.String(120))
    bio = db.Column(db.Text)
    # Artist's own site or Bandcamp page -- whichever they use most.
    website_url = db.Column(db.String(500))
    # Raw embed HTML from Bandcamp's or YouTube's own "Embed" snippet
    # generator (an <iframe>, typically), rendered as-is on the artist's
    # page. Trusted input -- only the site admin enters this, never a
    # visitor -- so it's fine to render unescaped.
    embed_code = db.Column(db.Text)

    # This site's whole angle is highlighting artists who live/work
    # locally, so this flag drives any "local spotlight" views.
    is_local = db.Column(db.Boolean, default=True, nullable=False)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    events = db.relationship("Event", secondary=event_artists, back_populates="artists")

    def __repr__(self):
        return f"<Artist {self.name}>"


class Event(db.Model):
    """A single show/performance at a venue."""

    id = db.Column(db.Integer, primary_key=True)
    venue_id = db.Column(db.Integer, db.ForeignKey("venue.id"), nullable=False)

    title = db.Column(db.String(300), nullable=False)
    start_datetime = db.Column(db.DateTime, nullable=False)
    end_datetime = db.Column(db.DateTime)

    description = db.Column(db.Text)
    ticket_url = db.Column(db.String(500))
    price_info = db.Column(db.String(200))

    # Optional, venue-feed-dependent extras: a genre/category tag (e.g.
    # "Jazz", "Comedy") and a promo/artist image URL, when the source
    # actually publishes them (not every venue's feed does).
    genre = db.Column(db.String(120))
    image_url = db.Column(db.String(500))

    # "manual" (added by hand in the admin UI) or "scraped".
    source = db.Column(db.String(20), default="manual", nullable=False)

    # For scraped events: an identifier from the source feed (e.g. a
    # Squarespace item id, or an ICS UID) used to detect duplicates on
    # re-scrape and to know whether to update vs. create.
    external_id = db.Column(db.String(300))

    # Scraped events land here as False ("needs review") until someone
    # approves them from the scrape preview screen; manual events are
    # approved immediately. Keeps a bad scrape from silently polluting
    # the public calendar.
    is_approved = db.Column(db.Boolean, default=True, nullable=False)

    # Bookkeeping for the "keep already-reviewed events honest" scrape
    # behavior (see app/scrapers/base.py's run_scrape docstring):
    # last_seen_at is stamped every time this event's external_id still
    # shows up in its venue's scrape results; missing_streak counts
    # consecutive scrapes in a row where it *didn't*, reset to 0 the
    # moment it reappears. needs_review + review_note flag an approved
    # event whose time/title changed, or one that's been auto-hidden as
    # a likely cancellation, for a human to look at on the Review page
    # -- kept separate from is_approved so "new, never reviewed" and
    # "was approved, now flagged" are distinguishable in that view.
    last_seen_at = db.Column(db.DateTime)
    missing_streak = db.Column(db.Integer, default=0, nullable=False)
    needs_review = db.Column(db.Boolean, default=False, nullable=False)
    review_note = db.Column(db.Text)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    venue = db.relationship("Venue", back_populates="events")
    artists = db.relationship("Artist", secondary=event_artists, back_populates="events")
    event_types = db.relationship("EventType", secondary=event_event_types, back_populates="events")

    __table_args__ = (
        db.UniqueConstraint("venue_id", "external_id", name="uq_event_venue_external_id"),
    )

    def __repr__(self):
        return f"<Event {self.title} @ {self.start_datetime}>"


class ScrapeRun(db.Model):
    """Log of a single scrape/import attempt for a venue.

    Keeping this in the DB (rather than just printing to a console) is
    what makes the "add a venue and work through the feed/scraping"
    workflow usable: the admin screen can show exactly what was fetched,
    what was parsed out of it, and what happened on the last few runs.
    """

    id = db.Column(db.Integer, primary_key=True)
    venue_id = db.Column(db.Integer, db.ForeignKey("venue.id"), nullable=False)

    run_at = db.Column(db.DateTime, default=datetime.utcnow)
    status = db.Column(db.String(20), default="success")  # success | error
    events_found = db.Column(db.Integer, default=0)
    events_created = db.Column(db.Integer, default=0)
    events_updated = db.Column(db.Integer, default=0)
    message = db.Column(db.Text)

    # Truncated raw payload (JSON/HTML snippet) captured for debugging,
    # so a bad parse can be diagnosed without re-hitting the venue site.
    raw_sample = db.Column(db.Text)

    venue = db.relationship("Venue", back_populates="scrape_runs")

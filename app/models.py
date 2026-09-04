"""SQLAlchemy models for the local music site POC.

Core tables: Venue, Artist, Event (a show), plus a many-to-many
Event<->Artist association, and ScrapeRun which logs every attempt to
pull events from a venue's feed/website so the aggregation workflow is
observable and debuggable from within the app.
"""
from datetime import datetime

from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import false

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

# Many-to-many: an artist's "Category Tags" (Music, Comedy, Art, etc.) --
# deliberately the *same* EventType table events use for their own category
# tags, rather than a separate artist-only category system. That keeps
# "Comedy" meaning one single, shared thing across the whole site instead of
# two parallel tag lists that could drift apart, and means an artist's
# categories and a show's categories are always comparable later (e.g. a
# future "artists who do Comedy" page reusing the exact tags already used to
# filter the calendar).
artist_event_types = db.Table(
    "artist_event_types",
    db.Column("artist_id", db.Integer, db.ForeignKey("artist.id"), primary_key=True),
    db.Column("event_type_id", db.Integer, db.ForeignKey("event_type.id"), primary_key=True),
)

# Many-to-many: an artist's "Genre Tags" (Electronica, New Wave, Americana,
# etc.) -- kept as its own separate tag table rather than reusing EventType,
# since genre is a finer-grained, mostly-music-specific classification (the
# same distinction Event.genre vs. EventType already draws at the event
# level -- see EventType's docstring below).
artist_genre_tags = db.Table(
    "artist_genre_tags",
    db.Column("artist_id", db.Integer, db.ForeignKey("artist.id"), primary_key=True),
    db.Column("genre_tag_id", db.Integer, db.ForeignKey("genre_tag.id"), primary_key=True),
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

    Also doubles as an Artist's "Category Tags" (see artist_event_types
    above) -- the same tag set, shared between events and artists.
    """

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), unique=True, nullable=False)
    slug = db.Column(db.String(80), unique=True, nullable=False)
    # Not every tag belongs on the public calendar's filter bar -- an
    # admin might quick-add something narrow/internal (e.g. "karaoke")
    # while tagging one event, without meaning to promote it to a
    # site-wide category. This flag is what actually decides which tags
    # show up as a toggleable pill on the public calendar (see
    # app/routes/main.py's category filter) versus staying an
    # admin-only label -- flipped via the "Manage categories" admin page
    # (app/routes/events.py's manage_categories()), so David can add a
    # brand-new public category later without needing a code change.
    # False by default: get_or_create_event_type() only ever sets this
    # True on a row it's creating for the first time (see that
    # function's docstring), never on one that already exists, so an
    # admin's own choice here always survives a re-seed or another
    # quick-add of the same tag name.
    #
    # Both default= (a Python-side default SQLAlchemy applies on INSERT)
    # AND server_default= (a real database-level DEFAULT clause) are set
    # here on purpose, not just the first one -- server_default is what
    # actually let sync_schema.py's generic `ADD COLUMN ... NOT NULL`
    # backfill this column on every existing row when it first shipped
    # to the droplet's Postgres database. Without it, CreateColumn's DDL
    # compiler has no DEFAULT to include at all (default= is invisible
    # to raw DDL, only server_default= is), so the ALTER TABLE tried to
    # add a NOT NULL column with no way to fill in a value for rows that
    # already existed -- a real deploy failure this caused:
    # `psycopg2.errors.NotNullViolation: column "is_public_category" of
    # relation "event_type" contains null values`. Any *future* NOT NULL
    # column added to an existing table needs the same treatment for
    # sync_schema.py to be able to add it -- see that script's own
    # docstring for the fuller explanation.
    is_public_category = db.Column(db.Boolean, default=False, server_default=false(), nullable=False)

    events = db.relationship("Event", secondary=event_event_types, back_populates="event_types")
    artists = db.relationship("Artist", secondary=artist_event_types, back_populates="category_tags")

    def __repr__(self):
        return f"<EventType {self.name}>"


class GenreTag(db.Model):
    """A reusable music-genre tag for artists (e.g. "Electronica", "New
    Wave", "Americana") -- an artist can carry several. Separate from the
    legacy Artist.genre free-text column (kept only so already-seeded data
    isn't silently lost -- new/edited artists use genre_tags instead) and
    separate from EventType/Category Tags (see artist_event_types above),
    since genre is a different, finer-grained axis than the broad
    Music/Comedy/Art category an artist or show falls under. Created ad hoc
    from the artist form's "quick add a new tag" input, same pattern as
    get_or_create_event_type(); see get_or_create_genre_tag() in
    app/utils.py.
    """

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), unique=True, nullable=False)
    slug = db.Column(db.String(80), unique=True, nullable=False)

    artists = db.relationship("Artist", secondary=artist_genre_tags, back_populates="genre_tags")

    def __repr__(self):
        return f"<GenreTag {self.name}>"


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

    # A photo of the venue itself (its room, marquee, whatever an admin
    # picks) -- shown on the venue's own detail page, and used as a
    # fallback image on any of its events that don't have their own
    # image_url (e.g. a scraped event with no flyer, or a manually-added
    # show nobody bothered to attach a flyer to). Same upload pipeline as
    # Event.image_url/the gig-submission flyer -- see
    # app/utils.py's resolve_uploaded_image_url()/save_flyer_upload().
    #
    # Text, not a length-capped String -- originally String(500), widened
    # after pasting a real Instagram/Facebook photo URL for "Haze" (519
    # chars, mostly a long signed query string) broke the Postgres sync
    # with "value too long for type character varying(500)" on the
    # droplet (SQLite never enforces a VARCHAR(n) length at all, so this
    # only ever bit Postgres). Event.image_url/Artist.image_url widened
    # the same way below, on the same reasoning -- any of them could hit
    # the same wall from a pasted CDN URL just as easily.
    image_url = db.Column(db.Text)

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
    # Legacy free-text genre, from before Genre Tags existed. No longer
    # editable via the artist form (genre_tags below replaced it), kept
    # only so any already-seeded artist's old value isn't silently
    # dropped -- safe to ignore going forward.
    genre = db.Column(db.String(120))
    bio = db.Column(db.Text)
    # A photo/promo image, same idea as Event.image_url -- just a URL (no
    # upload/storage pipeline in this POC), shown on the artist's own page,
    # the Local Artists list, and the homepage's featured-artist spotlight.
    # Falls back to the site logo wherever it's missing, same pattern as
    # an event with no image_url. Text, not a length-capped String -- see
    # Venue.image_url's docstring for why (a pasted social-media photo URL
    # can easily run past 500 characters).
    image_url = db.Column(db.Text)
    # Artist's own site or Bandcamp page -- whichever they use most.
    website_url = db.Column(db.String(500))
    # LEGACY -- superseded by the general-purpose ArtistLink table below.
    # These two columns briefly held one single-purpose (title, URL) pair
    # for a Freak Scene newsletter write-up; the very next ask was "let me
    # add *multiple* links, with a title and a link each," so that became
    # its own real one-to-many table instead of a second bespoke column
    # pair. Left in place (nullable, no longer read or written by the
    # artist form/routes) purely so app/__init__.py's
    # _migrate_freak_scene_links() has somewhere to read any
    # already-entered Freak Scene link from and carry it into a real
    # ArtistLink row the first time this runs on an install that has one
    # set -- see that function's own docstring.
    freak_scene_url = db.Column(db.String(500))
    freak_scene_title = db.Column(db.String(300))
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
    genre_tags = db.relationship("GenreTag", secondary=artist_genre_tags, back_populates="artists")
    category_tags = db.relationship("EventType", secondary=artist_event_types, back_populates="artists")
    # An artist's "Artist Links" -- an admin-managed, arbitrary-length list
    # of (title, URL) pairs shown in their own section on the artist page
    # (press write-ups, social links, merch, whatever). See ArtistLink
    # below. cascade="all, delete-orphan" so deleting an artist cleans up
    # its links too, same pattern as GigSubmission's converted_event
    # *isn't* cascaded (a deliberate exception noted there) -- here there's
    # no reason a link should ever outlive the artist it belongs to.
    # order_by keeps them in the order an admin arranged them via the
    # form's sort_order field, falling back to insertion order (id) for
    # any tie.
    links = db.relationship(
        "ArtistLink", back_populates="artist", cascade="all, delete-orphan",
        order_by="ArtistLink.sort_order, ArtistLink.id",
    )

    def __repr__(self):
        return f"<Artist {self.name}>"


class ArtistLink(db.Model):
    """One (title, URL) link in an artist's "Artist Links" section --
    press write-ups (e.g. a Freak Scene newsletter feature), social media,
    merch, whatever an admin wants to point visitors at. Deliberately a
    real one-to-many table rather than another single-purpose column pair
    on Artist (like the old Artist.freak_scene_url/freak_scene_title it
    replaces -- see those columns' own "LEGACY" docstring in this file),
    since the whole point is supporting more than one link per artist.

    Managed entirely through the artist form's repeatable "Artist Links"
    row list (see artists/form.html and _resolve_artist_links() in
    app/routes/artists.py), which replaces an artist's whole link list on
    every save rather than diffing individual rows -- same "just replace
    the collection" pattern already used for genre_tags/category_tags on
    Artist, simpler than reconciling adds/edits/removes/reorders
    individually for a list this small.
    """

    id = db.Column(db.Integer, primary_key=True)
    artist_id = db.Column(db.Integer, db.ForeignKey("artist.id"), nullable=False)

    title = db.Column(db.String(300), nullable=False)
    url = db.Column(db.String(500), nullable=False)
    # Admin-controlled ordering (set to each row's position in the form
    # when saved) rather than free-form drag-and-drop -- simple to
    # implement and plenty for a handful of links per artist.
    sort_order = db.Column(db.Integer, default=0, nullable=False)

    artist = db.relationship("Artist", back_populates="links")

    def __repr__(self):
        return f"<ArtistLink {self.title!r} -> {self.url}>"


class Event(db.Model):
    """A single show/performance at a venue."""

    id = db.Column(db.Integer, primary_key=True)
    venue_id = db.Column(db.Integer, db.ForeignKey("venue.id"), nullable=False)

    # An optional free-text override for one-off locations that aren't a
    # real, reusable Venue -- a festival with several differently-named
    # stages (e.g. "Florence Fest -- Main Stage" vs "...-- Second Stage"),
    # a street fair, a pop-up show at a spot that'll likely never host
    # another one. venue_id is still required (see above) and is meant to
    # just point at the shared "DIY" Venue (seeded in seed.py, same one
    # GigSubmission conversions default to for house shows -- see
    # events.py's _diy_venue_id()) purely so this event has *something*
    # to file under for site navigation/filtering; it is *not* meant to
    # be shown to visitors once custom_venue_name is set. Wherever a
    # show's venue is displayed, use display_venue_name/display_venue_link
    # below instead of venue.name/venue.website_url directly -- those
    # prefer this field and deliberately suppress the (irrelevant, since
    # it belongs to the placeholder Venue, not this specific one-off spot)
    # venue link once it's set.
    custom_venue_name = db.Column(db.String(300))

    title = db.Column(db.String(300), nullable=False)
    start_datetime = db.Column(db.DateTime, nullable=False)
    end_datetime = db.Column(db.DateTime)

    description = db.Column(db.Text)
    ticket_url = db.Column(db.String(500))
    price_info = db.Column(db.String(200))

    # Optional, venue-feed-dependent extras: a genre/category tag (e.g.
    # "Jazz", "Comedy") and a promo/artist image URL, when the source
    # actually publishes them (not every venue's feed does). image_url is
    # Text, not a length-capped String -- see Venue.image_url's docstring
    # for why (a pasted social-media photo URL can easily run past 500
    # characters).
    genre = db.Column(db.String(120))
    image_url = db.Column(db.Text)

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

    # Set when a still-pending ("New") scraped event is discarded from the
    # Review page -- rather than deleting the row outright (which would
    # make it look brand-new again on the very next scrape, since matching
    # is done by venue_id + external_id), it's kept and hidden instead, so
    # a future scrape recognizes it and leaves it alone. Its other fields
    # still get refreshed on re-scrape like any other matched event (see
    # run_scrape()), so it stays up to date in case it's ever restored.
    is_rejected = db.Column(db.Boolean, default=False, nullable=False)

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

    @property
    def display_venue_name(self):
        """What to actually show as this event's venue -- custom_venue_name
        when it's a one-off location (see that column's docstring above),
        otherwise the linked Venue's own name. Use this everywhere a show's
        venue is displayed, instead of venue.name directly."""
        return self.custom_venue_name or self.venue.name

    @property
    def display_venue_link(self):
        """The URL display_venue_name above should link to, or None for
        plain (unlinked) text. Always None once custom_venue_name is set --
        venue.website_url belongs to the placeholder Venue (e.g. "DIY")
        this event is filed under for navigation purposes, not to the
        actual one-off spot, so linking it would point visitors somewhere
        unrelated to what they just clicked on."""
        if self.custom_venue_name:
            return None
        return self.venue.website_url


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


class GigSubmission(db.Model):
    """A show submitted by an artist/promoter through the public "Submit
    your show" form (app/routes/gigs.py) -- not an Event yet, deliberately:
    these are unvetted, free-text submissions (anyone can fill in the
    form, no login), so they land here for an admin to look over and
    convert into a real Event (creating/linking a Venue and Artist
    records as needed) rather than publishing straight to the calendar.
    Same "keep unvetted input out of the public site until a human looks
    at it" idea as a scraped Event's is_approved=False, just for a
    different intake path.

    venue_name is deliberately a free-text field, not a dropdown tied to
    the Venue table -- lots of real submissions are expected to be
    one-off DIY shows at someone's house/backyard/basement rather than a
    listed venue, so there's nothing to pick from. Per David's call, the
    specific address/location text isn't given its own Event column --
    it's meant to be manually copied into the converted Event's own
    description field during conversion (see gigs.py's prefill), while
    those DIY shows all get grouped under one shared "DIY" Venue record
    (seeded in seed.py) for site navigation/filtering purposes.

    lineup_text is one free-text box covering both the bands on the bill
    and their websites together (not a structured per-band list) --
    there's no reliable way to auto-parse that into individual Artist
    records, so an admin reads it during conversion and uses the
    existing "+ Add as local artist" flow (artists.new_artist) per band
    if/when they want a real Artist page for one.

    genres_text is similarly one free-text box, not tied to Event.genre
    or a GenreTag/EventType pick-list -- a submitter typing "punk, noise
    rock" shouldn't be blocked on matching this install's exact existing
    tag spellings, and there's no manual-add-show form field for
    Event.genre to prefill anyway (see events.py's _gig_prefill(), which
    folds this into the converted Event's description alongside the
    location and lineup, same as those). Optional: not every submitter
    will think to fill it in, and it's a nice-to-have for the admin
    during conversion, not a blocker on getting the show submitted.
    """

    id = db.Column(db.Integer, primary_key=True)

    submitted_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    # The show's own proposed date/time -- naive local wall-clock, same
    # storage convention as Event.start_datetime (see app/utils.py's
    # SITE_TIMEZONE docstring), since it becomes an Event's start_datetime
    # as-is once converted.
    start_datetime = db.Column(db.DateTime, nullable=False)

    venue_name = db.Column(db.String(300), nullable=False)
    lineup_text = db.Column(db.Text, nullable=False)
    genres_text = db.Column(db.String(300))

    # Filename only (not a full path/URL) -- e.g. "3f2a...9c.jpg" -- under
    # app/static/uploads/flyers/. Kept as just a filename (like every
    # other *_filename-style field would be) rather than a full URL so it
    # isn't tied to whatever domain the site happens to be served from;
    # app/utils.py's flyer_url() builds the actual URL via url_for at
    # render time. Required by the submission form, but nullable at the
    # DB level (defensive: a bad/failed upload shouldn't take down an
    # otherwise-valid submission -- see gigs.py's submit_gig()).
    flyer_filename = db.Column(db.String(300))

    submitter_name = db.Column(db.String(200), nullable=False)
    submitter_email = db.Column(db.String(200), nullable=False)

    # "pending" (awaiting review) -> "converted" (became a real Event) or
    # "dismissed" (not a real/duplicate/spam submission, kept for the
    # record rather than deleted outright -- same "keep it, just hide it"
    # reasoning as Event.is_rejected).
    status = db.Column(db.String(20), default="pending", nullable=False)
    reviewed_at = db.Column(db.DateTime)

    # Set once this becomes a real Event (see events.new_event()'s
    # from_gig handling) -- kept even if that Event is later edited or
    # deleted, as a "this is where it went" audit trail. Nullable, and
    # deliberately not a hard foreign-key-cascade situation: if the Event
    # is deleted, this just becomes a dangling id rather than deleting
    # the submission record too (ondelete not set to CASCADE).
    converted_event_id = db.Column(db.Integer, db.ForeignKey("event.id"))
    converted_event = db.relationship("Event")

    def __repr__(self):
        return f"<GigSubmission {self.venue_name} @ {self.start_datetime} ({self.status})>"


class SiteSetting(db.Model):
    """A single-row table for small pieces of sitewide content an admin
    can edit through the UI instead of needing a code change -- right now
    just the "About this site" block on the calendar page. Deliberately
    not a general key/value settings table (nothing else needs one yet);
    always exactly one row, fixed at id=1, created on first access by
    app.utils.get_site_setting() if it's missing (e.g. a fresh install
    with no admin edit yet).

    about_html is stored and rendered as raw HTML (see calendar.html's
    `| safe` filter) rather than escaped/plain text, per David's ask --
    same trust model already used for Artist.embed_code: only an
    already-authenticated admin can ever write to this field (see
    main.py's edit_about(), which is @login_required), so allowing HTML
    here doesn't open an XSS hole to the public the way accepting raw
    HTML from a visitor-facing form would.
    """

    __tablename__ = "site_setting"

    id = db.Column(db.Integer, primary_key=True)
    about_html = db.Column(db.Text, nullable=False, default="")

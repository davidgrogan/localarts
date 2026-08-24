import os

from dotenv import load_dotenv
from flask import Flask, session
from sqlalchemy import inspect, text

from app.models import db

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Loads a local ".env" file if one exists (e.g. `DATABASE_URL=...` for
# testing against a real Postgres locally); on the droplet, systemd's
# EnvironmentFile= sets these instead and this is a harmless no-op since
# there's no .env file deployed there.
load_dotenv()

# db.create_all() only creates *missing tables* -- it never alters an
# existing table to add a newly-declared column. For a SQLite-backed POC
# where the model keeps growing (e.g. adding Event.genre/image_url after
# people already have a populated local DB), this tiny hand-rolled
# migration keeps existing installs working without a real migration
# tool (Alembic) or telling people to delete their database and re-seed.
_COLUMN_MIGRATIONS = {
    "event": [
        ("genre", "VARCHAR(120)"),
        ("image_url", "TEXT"),
        ("last_seen_at", "DATETIME"),
        ("missing_streak", "INTEGER DEFAULT 0 NOT NULL"),
        ("needs_review", "BOOLEAN DEFAULT 0 NOT NULL"),
        ("review_note", "TEXT"),
        ("is_rejected", "BOOLEAN DEFAULT 0 NOT NULL"),
        ("custom_venue_name", "VARCHAR(300)"),
    ],
    "artist": [
        ("embed_code", "TEXT"),
        ("image_url", "TEXT"),
    ],
    "venue": [
        ("default_event_type_id", "INTEGER"),
        ("image_url", "TEXT"),
    ],
    "gig_submission": [
        ("genres_text", "VARCHAR(300)"),
    ],
    "event_type": [
        ("is_public_category", "BOOLEAN DEFAULT 0 NOT NULL"),
    ],
}


def _run_sqlite_column_migrations():
    if not db.engine.url.get_backend_name().startswith("sqlite"):
        return  # Postgres/etc: use a real migration tool instead.
    with db.engine.connect() as conn:
        for table, columns in _COLUMN_MIGRATIONS.items():
            existing = {row[1] for row in conn.execute(text(f"PRAGMA table_info({table})"))}
            for column_name, column_type in columns:
                if column_name not in existing:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column_name} {column_type}"))
                    conn.commit()


def _column_exists(table_name, column_name):
    """True if the live database's table already has this column.

    Exists to protect every EventType-querying startup migration below
    (currently _bootstrap_default_public_category() and
    _migrate_renamed_categories()) against a real, previously-uncaught
    ordering bug on Postgres: sync_schema.py's whole job is adding a
    newly-declared column like `is_public_category` via its own ADD
    COLUMN step (Postgres has no automatic column migration -- see
    _run_sqlite_column_migrations() above, which explicitly skips any
    non-SQLite backend), but sync_schema.py's main() calls create_app()
    itself first, purely to get at `app`/`db` -- which means create_app()'s
    own startup migrations here can run against a table that's missing
    the very column they depend on, on a fresh deploy where that column
    was just added to models.py but sync_schema.py --apply hasn't run
    yet on this database. Any plain `EventType.query...` implicitly
    SELECTs every mapped column (not just the ones a filter mentions),
    so even _migrate_renamed_categories() -- which never reads
    is_public_category directly -- still errors out the same way.
    Confirmed against a real deploy_all.sh run: this crashed
    sync_schema.py itself with `psycopg2.errors.UndefinedColumn:
    column event_type.is_public_category does not exist` before it ever
    reached its own ADD COLUMN step.

    Skipping cleanly here just defers the self-healing migration to the
    *next* app start -- right after sync_schema.py --apply adds the
    column in this same run -- rather than crashing the deploy outright."""
    inspector = inspect(db.engine)
    if not inspector.has_table(table_name):
        return False
    return column_name in {col["name"] for col in inspector.get_columns(table_name)}


# Same eight names seed.py flags is_public_category=True on (kept as a
# separate list here, not imported from there, since the two lists serve
# different purposes and can legitimately diverge later: seed.py's list
# is "what a *fresh* install seeds," this one is "which *pre-existing*
# tag names, from before this feature existed, should be auto-promoted
# on a one-time upgrade" -- see _bootstrap_default_public_category()).
_UPGRADE_PROMOTE_CATEGORY_NAMES = (
    "music", "comedy", "theater", "spoken word", "lectures", "art exhibits", "film", "dance",
)


def _bootstrap_default_public_category():
    """Self-healing, backend-agnostic follow-up to the is_public_category
    column migration above (needed on Postgres too, which the SQLite-only
    function above skips -- there, the equivalent ADD COLUMN happens via
    `sync_schema.py --apply` on the droplet, a separate manual step this
    can't hook into directly, so it just runs here on every app start
    instead and checks its own precondition).

    Every EventType row that already existed before is_public_category
    existed at all -- on any install that predates this feature,
    including a real production database -- got the new column's False
    default. Left alone, that would mean the public calendar's category
    filter defaults to an empty selection and shows nothing at all until
    someone happens to find the new "Manage categories" admin page and
    turns categories back on by hand.

    Confirmed against this project's own real, years-accumulated dev
    database: an admin had already created plain tags named exactly
    "Comedy" and "Dance" by hand at various points (quick-added while
    tagging individual shows, long before "public category" was a
    concept) -- get_or_create_event_type()'s own "never touch an
    existing tag's flag" rule means seed.py's
    get_or_create_event_type("Comedy", is_public_category=True) call
    silently no-ops against that already-existing row, same as it's
    supposed to for a *deliberate* later admin choice. The difference is
    this isn't a deliberate choice about *being public* at all -- the
    concept didn't exist yet -- so this promotes every exact
    (case-insensitive) name match in _UPGRADE_PROMOTE_CATEGORY_NAMES,
    not just "Music", the first time this runs. It deliberately does NOT
    try to reconcile a near-miss like a pre-existing singular "Lecture"
    tag against the new plural "Lectures" category -- that's an
    editorial call (which existing shows' tags to move where) for a
    human to make via Manage categories, not something to guess at
    automatically.

    Runs on every app start, but is a no-op the moment *any* EventType is
    flagged public -- which happens the very first time either seed.py
    runs (fresh install, no pre-existing rows to collide with) or this
    function itself promotes one -- so it never overrides a deliberate
    later choice. One edge case worth knowing about: if every public
    category is ever turned off at once, this treats that identically to
    an unmigrated install and promotes the same names again at the next
    restart, rather than leaving the filter bar with nothing selectable
    at all -- a deliberate tradeoff (a self-healing default beats a
    calendar that silently shows zero shows forever).

    Guarded by _column_exists() -- see that function's docstring above
    for the real Postgres deploy-ordering bug this fixes."""
    from app.models import EventType

    if not _column_exists("event_type", "is_public_category"):
        return
    if EventType.query.filter_by(is_public_category=True).first() is not None:
        return
    promoted_any = False
    for name in _UPGRADE_PROMOTE_CATEGORY_NAMES:
        match = EventType.query.filter(db.func.lower(EventType.name) == name).first()
        if match:
            match.is_public_category = True
            promoted_any = True
    if promoted_any:
        db.session.commit()


# Editorial tag renames, decided by David via Manage categories case by
# case (not guessed at automatically -- see
# _bootstrap_default_public_category()'s docstring above for why a
# near-miss name is normally left alone on its own). Each entry is (old
# tag name, new tag name), matched case-insensitively. Two different
# things can happen per entry, depending on whether new_name is already
# in use -- see _migrate_renamed_categories()'s docstring below:
#   - new_name already exists as its own tag (e.g. "art exhibits", a
#     curated public category): every event on old_name moves onto that
#     existing tag, and old_name is deleted -- a real merge.
#   - new_name doesn't exist yet (e.g. "misc."): old_name's own row is
#     just renamed in place -- same tag, same id, same events, same
#     public/internal status, new name/slug.
# "Lecture" -> "Lectures" is the other near-miss flagged when this
# feature shipped but deliberately not included here yet -- add it the
# same way if/when David decides those two should merge.
_CATEGORY_RENAMES = (
    ("art exhibition", "art exhibits"),
    ("celebration", "misc."),
)


def _migrate_renamed_categories():
    """Applies each (old_name, new_name) pair in _CATEGORY_RENAMES above.

    Unlike _bootstrap_default_public_category() above, this isn't about
    is_public_category at all -- these are internal tags an admin had
    quick-added by hand at some point (same story as the pre-existing
    "Comedy"/"Dance" tags), each one landing on a name David later
    decided should read differently ("Art Exhibition" colliding with the
    new curated "Art Exhibits" category; "Celebration" just not being the
    label David wants going forward). Run as a real data migration (not a
    template-level rename) so this actually takes effect wherever the
    tag's name is read from -- the Manage Categories table, the
    Add/Edit Show form's tag checkboxes, and (for a public category) the
    calendar's own filter pills.

    If new_name already exists as its own EventType, this is a genuine
    merge: every event on old_name moves onto that existing row, and
    old_name is deleted once nothing carries it anymore. get_or_create_event_type()
    is deliberately not used for that lookup -- if new_name is meant to
    land on an existing curated public category (like "Art Exhibits")
    that, for whatever reason, doesn't exist yet on this install, this
    skips the merge entirely rather than creating a fresh, non-public row
    under that name that would silently defeat the whole point.

    If new_name does NOT already exist, there's nothing to merge into --
    old_name's own row is just renamed in place (name + slug only), so it
    keeps the same id, the same events, and whatever is_public_category
    value it already had. This is the right behavior for a rename that
    isn't reconciling a near-miss with something else already curated
    (e.g. "Celebration" -> "Misc.").

    Runs on every app start; each entry is a no-op the moment its
    old_name doesn't exist (already migrated, or a fresh install that
    never had it) -- cheap enough to just always check.

    Guarded by _column_exists(), same as _bootstrap_default_public_category()
    above -- every EventType.query below implicitly SELECTs the
    is_public_category column too (a plain ORM query selects every
    mapped column, not just the ones a filter mentions), even though
    this function's own logic never reads it, so it's just as vulnerable
    to the Postgres deploy-ordering bug described in that function's
    docstring."""
    from app.models import EventType
    from app.utils import slugify

    if not _column_exists("event_type", "is_public_category"):
        return
    for old_name, new_name in _CATEGORY_RENAMES:
        old = EventType.query.filter(db.func.lower(EventType.name) == old_name).first()
        if old is None:
            continue
        new = EventType.query.filter(db.func.lower(EventType.name) == new_name).first()
        if new is None:
            # Nothing to merge into -- just rename old_name's own row in
            # place, keeping its id/events/is_public_category untouched.
            old.name = new_name
            old.slug = slugify(new_name)
            db.session.commit()
            continue
        for event in list(old.events):
            if new not in event.event_types:
                event.event_types.append(new)
            event.event_types.remove(old)
        db.session.flush()
        if not old.events:
            db.session.delete(old)
        db.session.commit()


def create_app(test_config=None):
    app = Flask(__name__, instance_relative_config=True)

    # DATABASE_URL is what DigitalOcean's managed Postgres (and most other
    # hosts) hand you. When it's not set -- local dev, or this sandbox --
    # fall back to the same SQLite file used all along. Some providers
    # still hand back the old "postgres://" scheme, which SQLAlchemy 1.4+
    # rejects; normalize it to "postgresql://".
    database_url = os.environ.get("DATABASE_URL")
    if database_url and database_url.startswith("postgres://"):
        database_url = database_url.replace("postgres://", "postgresql://", 1)
    if not database_url:
        db_path = os.path.join(BASE_DIR, "instance", "local_music.sqlite3")
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        database_url = f"sqlite:///{db_path}"

    app.config.from_mapping(
        # `or` rather than `.get(key, default)` -- a blank SECRET_KEY= line
        # in .env (e.g. from copying .env.example without filling it in)
        # still counts as "set" to os.environ.get, which would otherwise
        # silently defeat this fallback and break session cookies entirely.
        SECRET_KEY=os.environ.get("SECRET_KEY") or "dev-secret-change-me",
        SQLALCHEMY_DATABASE_URI=database_url,
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        # Only matters when this app shares a domain with other apps behind
        # a reverse proxy (e.g. mounted at waveyvibe.dev/localarts alongside
        # sibling apps) -- scopes the session cookie to this app's own path
        # and gives it its own name so it can't collide with a same-named
        # cookie set by something else on the same domain. Defaults are the
        # normal Flask behavior for a single-app-per-domain deployment.
        SESSION_COOKIE_PATH=os.environ.get("SESSION_COOKIE_PATH") or "/",
        SESSION_COOKIE_NAME=os.environ.get("SESSION_COOKIE_NAME") or "session",
        # Caps the *whole* incoming request (form fields + the uploaded
        # flyer file together), not just the file -- Flask/Werkzeug reject
        # anything over this with a 413 before app/routes/gigs.py's
        # submit_gig() even runs. 10MB comfortably covers a real phone-
        # camera photo of a flyer without leaving the public submission
        # form open to someone deliberately uploading huge files.
        MAX_CONTENT_LENGTH=10 * 1024 * 1024,
    )
    if test_config:
        app.config.update(test_config)

    db.init_app(app)

    from app.routes.main import bp as main_bp
    from app.routes.venues import bp as venues_bp
    from app.routes.artists import bp as artists_bp
    from app.routes.events import bp as events_bp
    from app.routes.contact import bp as contact_bp
    from app.routes.gigs import bp as gigs_bp
    from app.auth import bp as auth_bp

    app.register_blueprint(main_bp)
    app.register_blueprint(venues_bp)
    app.register_blueprint(artists_bp)
    app.register_blueprint(events_bp)
    app.register_blueprint(contact_bp)
    app.register_blueprint(gigs_bp)
    app.register_blueprint(auth_bp)

    @app.context_processor
    def inject_is_admin():
        # Available in every template as `is_admin` so nav links and
        # edit/delete/scrape controls can hide themselves from anonymous
        # visitors without every route needing to pass it explicitly.
        return {"is_admin": bool(session.get("is_admin"))}

    @app.context_processor
    def inject_bandcamp_bookmarklet():
        # Available in every template as `bandcamp_bookmarklet_href` --
        # only actually used on artists/form.html, but a context processor
        # keeps it out of every render_template() call in
        # app/routes/artists.py rather than threading it through each one.
        # See app/bandcamp_bookmarklet.py for what this is and why it
        # exists (short version: Bandcamp CAPTCHA-walls automated fetches,
        # so this data has to come from the admin's own real browser
        # session via a bookmarklet instead of a server-side fetch).
        from app.bandcamp_bookmarklet import bookmarklet_href

        return {"bandcamp_bookmarklet_href": bookmarklet_href()}

    @app.context_processor
    def inject_review_count():
        # Badge count on the nav's "Review" link -- everything waiting on
        # an admin decision: brand-new scraped events, approved events
        # flagged for a changed time/title, and events auto-hidden as
        # likely cancellations. Computed globally (not just on the
        # calendar route) so it's accurate on every admin page; skipped
        # entirely for anonymous visitors to avoid the extra query.
        if not session.get("is_admin"):
            return {}
        from app.models import Event

        count = Event.query.filter(
            Event.is_rejected.is_(False),
            db.or_(Event.is_approved.is_(False), Event.needs_review.is_(True)),
        ).count()
        return {"pending_count": count}

    @app.context_processor
    def inject_pending_gigs_count():
        # Badge count on the nav's "Gig Submissions" link, same pattern
        # (and same "skip the query for anonymous visitors" reasoning) as
        # inject_review_count() above.
        if not session.get("is_admin"):
            return {}
        from app.models import GigSubmission

        count = GigSubmission.query.filter_by(status="pending").count()
        return {"pending_gigs_count": count}

    with app.app_context():
        db.create_all()
        _run_sqlite_column_migrations()
        _bootstrap_default_public_category()
        _migrate_renamed_categories()

    @app.template_filter("dtfmt")
    def dtfmt(value, fmt="%a %b %-d, %Y  %-I:%M %p"):
        if value is None:
            return ""
        try:
            return value.strftime(fmt)
        except ValueError:
            # %-d / %-I aren't supported on all platforms (e.g. Windows)
            return value.strftime("%a %b %d, %Y  %I:%M %p")

    @app.template_filter("flyer_url")
    def flyer_url_filter(flyer_filename):
        # A thin Jinja-filter wrapper around app.utils.flyer_url() so
        # gigs/review.html can write `{{ g.flyer_filename | flyer_url }}`
        # instead of importing/calling it explicitly per template.
        from app.utils import flyer_url

        return flyer_url(flyer_filename)

    @app.template_filter("resolve_image_url")
    def resolve_image_url_filter(value):
        # A thin Jinja-filter wrapper around app.utils.resolve_image_url()
        # -- apply this to every Event.image_url/Venue.image_url render
        # site (never Artist.image_url, which is always a pasted external
        # URL with no local-upload path -- see that column's docstring in
        # models.py). Re-derives the URL for a locally-uploaded image
        # fresh, in the current request, instead of trusting whatever got
        # stored -- see resolve_image_url()'s own docstring for why that
        # matters when local dev and the droplet are mounted under
        # different URL prefixes.
        from app.utils import resolve_image_url

        return resolve_image_url(value)

    @app.template_filter("artist_letter")
    def artist_letter_filter(name):
        # A thin Jinja-filter wrapper around app.utils.artist_display_letter()
        # so artists/list.html's per-tile "did the letter change" grouping
        # uses the exact same "The " stripped alphabetizing rule as
        # list_artists()'s own available_letters computation, instead of
        # each re-deriving it slightly differently.
        from app.utils import artist_display_letter

        return artist_display_letter(name)

    return app

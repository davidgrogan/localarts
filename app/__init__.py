import os

from dotenv import load_dotenv
from flask import Flask, session
from sqlalchemy import text

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

    return app

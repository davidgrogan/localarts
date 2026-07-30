import os
import re
import smtplib
import uuid
from datetime import datetime
from email.message import EmailMessage
from zoneinfo import ZoneInfo

# Every venue feed and every manually-entered show stores Event.start_datetime
# as a plain, naive "wall clock" value copied straight from whatever the
# venue's own site/feed says (e.g. "7:00 PM" becomes 19:00) -- there is no
# timezone attached and no conversion to true UTC anywhere in this codebase.
# That's exactly right for *display* (dtfmt just prints the raw value, so a
# show always shows the time it's actually at) but it means any code that
# needs to know "is this event upcoming" has to compare against local
# wall-clock "now", not real UTC "now" (datetime.utcnow()) -- otherwise,
# for the several hours a day US/Eastern trails UTC, an event that hasn't
# happened yet in reality gets treated as already in the past (see the
# "Featured Artist not showing up for a same-day show" bug this was written
# to fix). Northampton, MA is always America/New_York -- there's only ever
# one site instance with one timezone, so this is hardcoded rather than
# configurable.
SITE_TIMEZONE = ZoneInfo("America/New_York")


def local_now():
    """"Now," in the same naive-local-wall-clock terms Event.start_datetime
    is stored in -- see SITE_TIMEZONE's docstring above. Use this (not
    datetime.utcnow()) anywhere "now" is compared against a start_datetime,
    used as an "upcoming events" cutoff, or otherwise needs to line up with
    what a show's stored time actually means."""
    return datetime.now(SITE_TIMEZONE).replace(tzinfo=None)


def slugify(text):
    text = (text or "").lower().strip()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return text.strip("-") or "item"


def get_or_create_event_type(name):
    """Look up an EventType by name, case-insensitively, creating it if
    it doesn't exist yet. Shared by the event form's and venue form's
    "quick add a new tag" inputs so typing "music" after "Music" already
    exists doesn't create a duplicate. Import of app.models is local to
    avoid a circular import (models.py doesn't import this module, but
    keeping it lazy here matches the pattern used elsewhere, e.g.
    app/scrapers/base.py's _load_source_types()).
    """
    from app.models import db, EventType

    name = (name or "").strip()
    if not name:
        return None
    existing = EventType.query.filter(db.func.lower(EventType.name) == name.lower()).first()
    if existing:
        return existing
    event_type = EventType(name=name, slug=slugify(name))
    db.session.add(event_type)
    db.session.flush()
    return event_type


def get_or_create_genre_tag(name):
    """Same idea as get_or_create_event_type() above, for an artist's Genre
    Tags (e.g. "Electronica", "Americana") instead of Category Tags --
    look up a GenreTag by name case-insensitively, creating it if it
    doesn't exist yet, so the artist form's "quick add a new tag" input
    doesn't create "electronica" and "Electronica" as two separate tags.
    """
    from app.models import db, GenreTag

    name = (name or "").strip()
    if not name:
        return None
    existing = GenreTag.query.filter(db.func.lower(GenreTag.name) == name.lower()).first()
    if existing:
        return existing
    genre_tag = GenreTag(name=name, slug=slugify(name))
    db.session.add(genre_tag)
    db.session.flush()
    return genre_tag


# Seeded into a brand-new site_setting row the first time get_site_setting()
# runs and finds none -- written as HTML (not plain text) since about_html
# is rendered with `| safe`, matching whatever an admin would type in via
# the edit-about form. Kept here rather than inline in get_site_setting()
# so it's easy to find/update if the default copy needs to change later.
DEFAULT_ABOUT_HTML = """\
<h1>Live Music in the Northampton Area</h1>
<p class="hero-lede">
  One calendar, pulled together from venues all over Northampton, MA
  &mdash; so you don't have to check a dozen different sites to see who's
  playing this week.
</p>
<p class="hero-note">
  Venues sometimes change set times, lineups, or cancel shows on short
  notice, so before you head out, it's always worth double-checking the
  details on the venue's own website.
</p>
"""

# Shown on both the calendar (calendar.html) and every show's own Event
# Details page (events/detail.html) -- kept as a fixed constant rather than
# part of the admin-editable about_html above, on purpose: the full "About
# this site" content moved to its own /about page (see main.py's
# about_page()) and is no longer shown inline on the calendar at all, but
# David asked for this specific line to keep showing up wherever a visitor
# is actually looking at show listings, regardless of whatever the About
# page's own content later gets edited to say. Plain text (not HTML) since
# it's only ever inserted as-is, not merged into admin-editable markup.
VENUE_CAUTION_NOTE = (
    "Venues sometimes change set times, lineups, or cancel shows on short "
    "notice, so before you head out, it's always worth double-checking the "
    "details on the venue's own website."
)


def get_site_setting():
    """The single SiteSetting row (see models.py), creating it with the
    default "About this site" copy if it doesn't exist yet -- e.g. a
    fresh install, or an existing install's very first request after
    this feature shipped. Import of app.models is local, same
    circular-import-avoidance reason as get_or_create_event_type() above.
    """
    from app.models import db, SiteSetting

    setting = SiteSetting.query.get(1)
    if not setting:
        setting = SiteSetting(id=1, about_html=DEFAULT_ABOUT_HTML)
        db.session.add(setting)
        db.session.commit()
    return setting


# ---------------------------------------------------------------------------
# Admin email notifications (Gmail SMTP)
# ---------------------------------------------------------------------------
# Originally lived only in app/routes/contact.py (the contact form). Pulled
# out here once the gig-submission notifier (app/routes/gigs.py) needed to
# send its own admin email through the exact same mechanism -- rather than
# duplicating the SMTP boilerplate in a second blueprint, both now share this
# one function. Env vars read the same way contact.py always read them
# (plain os.environ.get at import time, not app.config), so behavior/config
# is unchanged for anyone who already has these set.
#
# `os.environ.get(KEY, default)`'s default only kicks in when KEY is
# completely absent from the environment -- but .env.example (and any .env
# copied from it) ships these as blank-but-present lines (e.g.
# "CONTACT_EMAIL="), which python-dotenv loads as the empty string, not as
# "unset." That silently defeated the default below (CONTACT_EMAIL always
# came out "" for anyone who left it blank, same as they're told to for "use
# the default"). SECRET_KEY just below already sidesteps this with
# `os.environ.get(...) or default`; MAIL_SERVER/MAIL_PORT/CONTACT_EMAIL now
# use the same guard against an empty string.
CONTACT_EMAIL = os.environ.get("CONTACT_EMAIL") or "davidbgrogan@gmail.com"
MAIL_SERVER = os.environ.get("MAIL_SERVER") or "smtp.gmail.com"
MAIL_PORT = int(os.environ.get("MAIL_PORT") or "587")
# The Gmail address the message is sent *from* (usually the same address as
# CONTACT_EMAIL, but kept separate in case that's ever not true), and its App
# Password -- see README.md for how to generate one.
MAIL_USERNAME = os.environ.get("MAIL_USERNAME")
MAIL_PASSWORD = os.environ.get("MAIL_PASSWORD")


def send_admin_email(subject, body, reply_to=None):
    """Send a single plain-text email to the site's admin inbox
    (CONTACT_EMAIL) via Gmail SMTP. Raises RuntimeError if MAIL_USERNAME/
    MAIL_PASSWORD aren't configured, and lets any smtplib exception
    propagate -- every caller is expected to catch it and flash a friendly
    error rather than let it 500, same as the contact form always did.
    """
    if not MAIL_USERNAME or not MAIL_PASSWORD:
        raise RuntimeError(
            "Email isn't configured on this server yet (MAIL_USERNAME/"
            "MAIL_PASSWORD aren't set)."
        )

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = MAIL_USERNAME
    msg["To"] = CONTACT_EMAIL
    if reply_to:
        msg["Reply-To"] = reply_to
    msg.set_content(body)

    with smtplib.SMTP(MAIL_SERVER, MAIL_PORT, timeout=10) as smtp:
        smtp.starttls()
        smtp.login(MAIL_USERNAME, MAIL_PASSWORD)
        smtp.send_message(msg)


# ---------------------------------------------------------------------------
# Flyer uploads (app/routes/gigs.py's "Submit your show" form)
# ---------------------------------------------------------------------------
# The first real file-upload feature in this app -- every other image
# anywhere on the site (Artist.image_url, Event.image_url) is just a URL
# string typed/pasted in, with no upload pipeline at all. A flyer, though,
# genuinely starts as a photo/file on someone's phone, so this needed real
# `request.files` handling. Saved straight into app/static/ so Flask's own
# static-file serving handles it with no new route -- a flyer's URL is just
# flyer_url() below, no separate download/serve endpoint needed.
FLYER_UPLOAD_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "static", "uploads", "flyers"
)
ALLOWED_FLYER_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp"}


def save_flyer_upload(file_storage):
    """Save an uploaded flyer image (a werkzeug FileStorage, i.e.
    request.files.get('flyer')) into FLYER_UPLOAD_DIR under a fresh random
    filename -- never the visitor-supplied one, both to dodge path-
    traversal tricks (a uuid rename sidesteps that entirely, on top of
    only ever reading the extension off the original name) and so two
    different submitters' same-named "flyer.jpg" can't collide.

    Returns the saved filename (just the filename -- see flyer_url() below
    for turning it into a URL) on success, or None if there's no file, an
    empty filename (the normal "no file chosen" case), or an extension not
    in ALLOWED_FLYER_EXTENSIONS. Never raises on a bad file -- gigs.py's
    submit_gig() treats a missing/invalid flyer as an ordinary validation
    error with a flash message, not a 500. Overall upload size is capped
    separately by the app's MAX_CONTENT_LENGTH config (see app/__init__.py)
    -- Flask rejects an oversized request outright, before this function
    (or the route) ever runs.
    """
    if file_storage is None or not file_storage.filename:
        return None
    original_name = file_storage.filename
    ext = original_name.rsplit(".", 1)[-1].lower() if "." in original_name else ""
    if ext not in ALLOWED_FLYER_EXTENSIONS:
        return None
    os.makedirs(FLYER_UPLOAD_DIR, exist_ok=True)
    filename = f"{uuid.uuid4().hex}.{ext}"
    file_storage.save(os.path.join(FLYER_UPLOAD_DIR, filename))
    return filename


def flyer_url(flyer_filename):
    """The public URL for a saved flyer filename (see save_flyer_upload()
    above), or None if there isn't one. Needs an active Flask app/request
    context for url_for() -- every caller (routes, templates) already has
    one, so the import is kept local here rather than at module level to
    avoid pulling Flask into every other function in this file that
    doesn't need it.

    _external=True matters here, not just cosmetically: this value gets
    dropped straight into the Add Show form's Image/flyer URL field
    (events.py's new_event() from_gig prefill), which is an
    `<input type="url">` -- a browser's built-in validation for that input
    type requires a fully-qualified absolute URL (scheme + host), and
    rejects a bare site-relative path like "/static/uploads/flyers/x.jpg"
    outright with "Please enter a URL", blocking the whole form from
    submitting. Every other Event.image_url value already in this app
    (scraper-sourced) is a full absolute URL too, so this also just
    matches that existing convention rather than being a special case.
    """
    if not flyer_filename:
        return None
    from flask import url_for

    return url_for("static", filename=f"uploads/flyers/{flyer_filename}", _external=True)

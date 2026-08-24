import base64
import os
import re
import threading
import uuid
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import requests
from icalendar import Calendar, Event as ICSEvent

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


# Matches the leading "<p>Showtimes: 2:00 pm | 4:55 pm | ...</p>" paragraph
# app/scrapers/amherst_cinema.py's _build_description() always puts first
# in a film's description -- see showtimes_line() below for why this
# exists at all.
_SHOWTIMES_LINE_RE = re.compile(r"^\s*<p>\s*Showtimes:\s*(.*?)\s*</p>", re.IGNORECASE)


def showtimes_line(description):
    """Pulls just the "2:00 pm | 4:55 pm | 7:30 pm | 9:40 pm" showtimes text
    back out of an Event's description, for calendar.html to render as its
    own short line directly under the title -- rather than only being
    visible in the card's hover tooltip/truncated popup, which is easy to
    miss for a listing whose whole point is "here are the times today."

    Deliberately scoped to exactly this one leading-paragraph shape instead
    of showing an arbitrary preview of *any* event's description under its
    title: some venues' descriptions (Quonk's Ticket Tailor detail-page
    pull-through, in particular) are full paragraph-length blurbs that
    would blow up every card's height if shown inline by default. Amherst
    Cinema is currently the only scraper that ever produces a description
    starting with "Showtimes:", so this is a no-op (returns None) for
    every other venue's events -- and for a manually-entered show, or a
    scraped one whose description happens to start differently.

    Returns None if description is empty or doesn't start with that exact
    shape, rather than raising -- this always runs against whatever
    arbitrary text/HTML is already in the description column.
    """
    if not description:
        return None
    match = _SHOWTIMES_LINE_RE.match(description)
    return match.group(1) if match else None


def artist_sort_key(name):
    """Sort key for alphabetizing artist names: a leading "The " (any
    case -- "the", "The", "THE" all match) is ignored, so "The Mountain
    Movers" sorts and groups under M right alongside a hypothetical
    "Mountain Movers", not off by itself under T -- matching how a real
    venue marquee or record-store bin would alphabetize band names.
    Sorting is done here in Python (see list_artists()/events.py's form
    routes, which now do `sorted(..., key=artist_sort_key)` instead of an
    ORDER BY) rather than as a SQL expression -- stripping a
    case-insensitive prefix portably across SQLite and Postgres in SQL
    is fiddlier than it's worth at this site's artist-count scale.

    Deliberately requires the trailing space ("the " with 4 characters,
    not just "the") so a band actually named exactly "The" isn't stripped
    down to an empty-string sort key.
    """
    if not name:
        return ""
    stripped = name.strip()
    lowered = stripped.lower()
    if lowered.startswith("the "):
        return stripped[4:].lower()
    return lowered


def artist_display_letter(name):
    """The letter the /artists index's A-Z jump bar and its per-letter
    section markers group an artist under -- the first letter of
    artist_sort_key(), so "The Mountain Movers" shows up under M (right
    where a visitor looking for that band would actually look) rather
    than every "The ..." act clustering under T. Registered as the
    `artist_letter` Jinja filter in app/__init__.py so list.html's
    per-tile grouping and list_artists()'s available_letters computation
    share this exact same rule instead of each re-deriving it slightly
    differently.
    """
    key = artist_sort_key(name)
    return key[0].upper() if key else ""


def get_or_create_event_type(name, is_public_category=False):
    """Look up an EventType by its computed slug, creating it if it
    doesn't exist yet. Shared by the event form's and venue form's
    "quick add a new tag" inputs so typing "music" after "Music" already
    exists doesn't create a duplicate. Import of app.models is local to
    avoid a circular import (models.py doesn't import this module, but
    keeping it lazy here matches the pattern used elsewhere, e.g.
    app/scrapers/base.py's _load_source_types()).

    is_public_category only ever applies to a row this call actually
    creates -- an already-existing tag's flag is left exactly as it was,
    even if this call passes a different value. That matters because
    the event/venue forms' own "quick add a new tag" inputs call this
    with the default (False, an internal-only tag) on every save, and a
    re-run of seed.py calls it with True for the curated public
    categories (see that file) -- neither should ever silently flip a
    flag an admin already set deliberately via the "Manage categories"
    page (app/routes/events.py's manage_categories()).

    Matches on slug, not a case-insensitive comparison of the raw name --
    confirmed via a real IntegrityError (UNIQUE constraint failed:
    genre_tag.slug, the sibling function below, same bug) that those
    aren't equivalent: an existing tag named "folk rock" and a new
    submission of "folk-rock" don't match as strings (space vs hyphen)
    even though slugify() collapses both to the identical "folk-rock",
    which is the column this would actually collide on. Comparing name
    case-insensitively missed that, fell through to inserting a second
    row, and crashed on the UNIQUE constraint slugify() was specifically
    supposed to prevent. Matching on the slug instead catches every case
    the name comparison did (identical names case-insensitively always
    produce identical slugs) plus this one, and is exactly the same
    canonicalization slugify() already does everywhere else.
    """
    from app.models import db, EventType

    name = (name or "").strip()
    if not name:
        return None
    slug = slugify(name)
    existing = EventType.query.filter_by(slug=slug).first()
    if existing:
        return existing
    event_type = EventType(name=name, slug=slug, is_public_category=is_public_category)
    db.session.add(event_type)
    db.session.flush()
    return event_type


def get_or_create_genre_tag(name):
    """Same idea as get_or_create_event_type() above, for an artist's Genre
    Tags (e.g. "Electronica", "Americana") instead of Category Tags --
    look up a GenreTag by its computed slug, creating it if it doesn't
    exist yet, so the artist form's "quick add a new tag" input doesn't
    create "electronica" and "Electronica" as two separate tags. See that
    function's docstring for why this matches on slug rather than a
    case-insensitive name comparison -- confirmed on this exact function
    via a real IntegrityError from an existing "folk rock" tag colliding
    with a new "folk-rock" submission.
    """
    from app.models import db, GenreTag

    name = (name or "").strip()
    if not name:
        return None
    slug = slugify(name)
    existing = GenreTag.query.filter_by(slug=slug).first()
    if existing:
        return existing
    genre_tag = GenreTag(name=name, slug=slug)
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
# Admin email notifications (Resend HTTP API)
# ---------------------------------------------------------------------------
# Originally lived only in app/routes/contact.py (the contact form). Pulled
# out here once the gig-submission notifier (app/routes/gigs.py) needed to
# send its own admin email through the exact same mechanism -- rather than
# duplicating the sending boilerplate in a second blueprint, both now share
# this one function.
#
# This used to talk Gmail SMTP directly via smtplib -- switched to Resend's
# plain HTTPS API after a 2026-08-17 production incident (see
# send_admin_email()'s own docstring, and the README's "Hardened against a
# hung mail server" section) where the droplet's outbound connections to
# smtp.gmail.com on port 587 turned out to be silently blocked/dropped by
# something upstream of the box itself -- confirmed by direct `nc` tests: an
# ordinary HTTPS connection (port 443, to an unrelated host) succeeded
# instantly, while smtp.gmail.com on both 587 and 465 just hung with no
# response at all, and neither `ufw` nor a DigitalOcean Cloud Firewall was
# actually configured to explain it. Root cause never fully identified (some
# combination of the datacenter's network and/or Gmail treating that IP range
# as a likely spam source seems most likely) -- rather than keep chasing an
# opaque, upstream-of-the-droplet network block, moving to an HTTPS-based
# provider sidesteps the entire failure class: it uses the exact same port
# (443) that was already confirmed to work fine.
#
# `os.environ.get(KEY, default)`'s default only kicks in when KEY is
# completely absent from the environment -- but .env.example (and any .env
# copied from it) ships these as blank-but-present lines (e.g.
# "CONTACT_EMAIL="), which python-dotenv loads as the empty string, not as
# "unset." That silently defeated a plain `os.environ.get(KEY, default)`
# (CONTACT_EMAIL always came out "" for anyone who left it blank, same as
# they're told to for "use the default"). SECRET_KEY above already sidesteps
# this with `os.environ.get(...) or default`; CONTACT_EMAIL uses the same
# guard against an empty string.
CONTACT_EMAIL = os.environ.get("CONTACT_EMAIL") or "davidbgrogan@gmail.com"
# See README.md / .env.example for how to get one -- resend.com's free tier
# (a few thousand emails/month) is comfortably more than this site needs.
RESEND_API_KEY = os.environ.get("RESEND_API_KEY")
# Resend requires sending "from" an address on a domain you've verified with
# them -- or, with zero setup at all, their own onboarding@resend.dev sender,
# which is what this defaults to so email sending works immediately without
# first walking through domain verification. Switch this once
# waveyvibe.dev (or a subdomain of it) is verified in Resend's dashboard, for
# a from-address visitors will actually recognize.
RESEND_FROM_EMAIL = os.environ.get("RESEND_FROM_EMAIL") or "Paradise City Music <onboarding@resend.dev>"


# Hard wall-clock ceiling on how long send_admin_email() itself is ever
# allowed to block a request for -- see that function's docstring for the
# real incident this exists to prevent (a WORKER TIMEOUT on 2026-08-17 that
# killed an entire gunicorn worker, not just the one request, because this
# function hung far longer than the network layer's own timeout should have
# allowed). Kept in place even after switching off SMTP (see the module
# comment above) as cheap insurance -- an HTTPS call to Resend is far less
# likely to hang the way that SMTP connection did, but "far less likely"
# isn't "impossible," and this costs nothing to leave in place. Chosen to
# stay comfortably under gunicorn's own request timeout (deploy/
# local-music.service sets no explicit --timeout flag, so it's gunicorn's
# default of 30 seconds) -- if this were set *equal to or above* that, the
# worker could still get killed before this function ever got the chance to
# give up cleanly.
EMAIL_SEND_TIMEOUT = 15


def _send_admin_email_now(payload):
    """The actual blocking HTTPS call to Resend's API -- split out of
    send_admin_email() below purely so it can be run on a background
    thread with a hard deadline (see EMAIL_SEND_TIMEOUT's docstring).

    Deliberately doesn't use resp.raise_for_status() -- that only puts
    the bare status code in the exception message (e.g. "403 Client
    Error: Forbidden for url: ..."), discarding the JSON body Resend
    actually sends explaining *why* (e.g. "You can only send testing
    emails to your own email address... please verify a domain"). That
    body is the whole story for diagnosing a send failure, so it's
    folded into the raised message instead of thrown away."""
    resp = requests.post(
        "https://api.resend.com/emails",
        headers={"Authorization": f"Bearer {RESEND_API_KEY}"},
        json=payload,
        timeout=10,
    )
    if not resp.ok:
        try:
            detail = resp.json().get("message") or resp.text
        except ValueError:  # response body wasn't JSON
            detail = resp.text
        raise RuntimeError(f"Resend API returned {resp.status_code}: {detail}")


def send_admin_email(subject, body, reply_to=None, attachment_path=None, attachment_filename=None):
    """Send a single plain-text email to the site's admin inbox
    (CONTACT_EMAIL) via Resend's HTTP API. Raises RuntimeError if
    RESEND_API_KEY isn't configured, and re-raises any request failure (a
    non-2xx response, e.g. a bad/revoked API key) or TimeoutError -- see
    below -- every caller is expected to catch it and flash a friendly
    error rather than let it 500, same as the contact form always did.

    attachment_path (optional) attaches one file, read straight off disk --
    used by gigs.py's submit_gig() to include the submitted flyer image
    inline in the notification, so David can see it without opening the
    review queue first. attachment_filename controls the filename the
    recipient's mail client shows for it (defaults to attachment_path's own
    basename, which would otherwise be an opaque uuid-based name -- see
    save_flyer_upload()). A missing/unreadable file is treated as "no
    attachment" rather than failing the whole email -- the submission
    itself is already safely saved by the time this runs (see gigs.py), so
    a flyer-attachment hiccup shouldn't also take down the notification.
    Resend's API takes attachment bytes base64-encoded inside the JSON
    payload itself (no multipart upload), so that's all this does with the
    file's contents -- no separate request or storage involved.

    The actual HTTPS call (_send_admin_email_now()) runs on a background
    thread rather than directly on the caller's -- this function only ever
    waits up to EMAIL_SEND_TIMEOUT seconds for it via thread.join(), then
    raises TimeoutError if it's still not done, rather than blocking
    indefinitely. See EMAIL_SEND_TIMEOUT's own docstring for why this is
    still worth keeping even after moving off the SMTP connection that
    actually caused the 2026-08-17 incident. The background thread is
    daemon=True and left running if it times out rather than being
    forcibly killed (Python has no clean way to kill a thread stuck in a
    C-level network call anyway) -- if the request does eventually
    complete a few seconds later, the email still goes out, just late; if
    it never does, it quietly leaks one thread, which is a far better
    failure mode than losing a whole worker process over one slow network
    call.
    """
    if not RESEND_API_KEY:
        raise RuntimeError("Email isn't configured on this server yet (RESEND_API_KEY isn't set).")

    payload = {
        "from": RESEND_FROM_EMAIL,
        "to": [CONTACT_EMAIL],
        "subject": subject,
        "text": body,
    }
    if reply_to:
        payload["reply_to"] = reply_to

    if attachment_path:
        try:
            with open(attachment_path, "rb") as f:
                data = f.read()
        except OSError:
            data = None
        if data is not None:
            payload["attachments"] = [{
                "filename": attachment_filename or os.path.basename(attachment_path),
                "content": base64.b64encode(data).decode("ascii"),
            }]

    caught = []

    def _run():
        try:
            _send_admin_email_now(payload)
        except Exception as exc:  # noqa: BLE001 -- handed back to the caller's own thread below
            caught.append(exc)

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    thread.join(EMAIL_SEND_TIMEOUT)

    if thread.is_alive():
        raise TimeoutError(
            f"Timed out after {EMAIL_SEND_TIMEOUT}s trying to reach Resend's API -- "
            "it may still go out in the background."
        )
    if caught:
        raise caught[0]


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


def resolve_uploaded_image_url(form, files, file_field="flyer_image", url_field="image_url", bad_file_message=None):
    """Shared precedence logic for any admin form that offers both a
    pasted Image URL text field and a direct file upload for the same
    image -- originally written for events.py's Add/Edit Show form (see
    _resolve_image_url() there, now a thin wrapper around this), and
    reused as-is by venues.py's Add/Edit Venue form for a venue's own
    photo.

    An uploaded file always wins over whatever's in the URL text field,
    on the theory that if an admin bothered to pick a file, that's the
    one they actually want used -- the text field is only a fallback for
    when no file was chosen this time (or a URL was pasted in directly,
    e.g. one already hosted elsewhere). Reuses save_flyer_upload()/
    flyer_url() -- the same upload pipeline built for the public "Submit
    a Show" form (uuid-renamed, saved under app/static/uploads/flyers/,
    no separate serving route needed) -- rather than a second one; the
    "flyer" naming there predates this being reused for venue photos too,
    but the mechanics (save an image file, get back a URL) are identical.

    When no file is uploaded, this returns exactly what's in the URL text
    field -- including blank, which is how every caller has always
    cleared an image back to None. A file *was* chosen but isn't a
    supported image type: flashes a warning (bad_file_message, or a
    generic default) and falls back to the URL field instead of blocking
    the whole save.
    """
    from flask import flash

    upload = files.get(file_field)
    if upload is not None and upload.filename:
        filename = save_flyer_upload(upload)
        if filename:
            return flyer_url(filename)
        flash(
            bad_file_message or (
                "That image file type isn't supported (JPG, PNG, GIF, or WEBP only) "
                "-- this was saved without changing its image."
            ),
            "error",
        )
    return form.get(url_field, "").strip() or None


def flyer_url(flyer_filename):
    """The public URL for a saved flyer filename (see save_flyer_upload()
    above), or None if there isn't one. Needs an active Flask app/request
    context for url_for() -- every caller (routes, templates) already has
    one, so the import is kept local here rather than at module level to
    avoid pulling Flask into every other function in this file that
    doesn't need it.

    Deliberately relative (no scheme+host) -- this used to pass
    _external=True, which seemed harmless (an absolute URL "just works"
    wherever it's rendered) but actually baked in whatever host happened
    to be serving the request at *upload* time: uploading on local dev
    stored "http://127.0.0.1:5050/static/uploads/flyers/x.jpg" straight
    into Event.image_url/Venue.image_url, meaningless once that row got
    copied to the droplet by migrate_to_postgres.py.

    Dropping _external=True fixes the host, but on its own isn't enough
    to make this value *portable* between local dev (served at the
    domain root) and the droplet (served under a URL prefix, e.g.
    waveyvibe.dev/localarts -- see DEPLOY.md's Caddy/ProxyFix setup): a
    plain "/static/uploads/flyers/x.jpg" is only correct for whichever
    install has no mount prefix. url_for() *does* account for the
    current request's prefix automatically (that's the whole point of
    ProxyFix reading X-Forwarded-Prefix) -- but only at the moment this
    function actually runs. The bug was calling it once at *upload* time
    and then storing the resulting string as a permanent, frozen value:
    that value is only ever correct for whatever install did the
    uploading, and stays wrong forever once copied anywhere else. See
    resolve_image_url() below, which re-derives this fresh at *render*
    time instead of trusting whatever got stored -- that's what actually
    makes an uploaded image portable between installs with different
    mount prefixes, not this function alone.

    This does mean the "Image / flyer URL" text fields it prefills
    (events/form.html, venues/form.html) can no longer be
    `<input type="url">` -- that input type's native browser validation
    requires a fully-qualified absolute URL and would reject this
    relative value outright, blocking the form from submitting even
    without the admin touching the field. Both are `type="text"` now.
    """
    if not flyer_filename:
        return None
    from flask import url_for

    return url_for("static", filename=f"uploads/flyers/{flyer_filename}")


# The literal path fragment every locally-uploaded flyer/photo's URL
# contains, no matter which install (or which URL prefix) it was uploaded
# on -- see save_flyer_upload()'s FLYER_UPLOAD_DIR. Shared between
# flyer_url() (builds it) and resolve_image_url() below (recognizes it).
_FLYER_URL_MARKER = "/static/uploads/flyers/"


def resolve_image_url(value):
    """Turn a stored Event.image_url/Venue.image_url value into the URL
    to actually put in an `<img src="...">` (or a form field's prefilled
    value) *right now*, in *this* request -- rather than trusting the
    stored string as-is.

    Why this needs to exist at all: those two columns hold two genuinely
    different kinds of value, and only one of them is safe to render
    directly --

    - A pasted external URL (Facebook/Instagram CDN, a venue's own
      hosted flyer image, etc.) is already a complete, portable, correct
      URL no matter where it's rendered. Passed through unchanged.
    - A locally-uploaded flyer/photo's URL (computed once by flyer_url()
      at upload time, then saved as-is) is only correct on whichever
      install did the uploading -- see flyer_url()'s docstring. Detected
      here by the shared /static/uploads/flyers/ path fragment
      (_FLYER_URL_MARKER) every such value contains regardless of
      install/prefix, then *recomputed* via a fresh flyer_url() call --
      which, running now, correctly picks up wherever *this* request is
      actually being served from (local dev at the domain root, or the
      droplet under /localarts -- see ProxyFix/X-Forwarded-Prefix in
      DEPLOY.md) instead of wherever the upload happened to occur.

    None/empty values pass through unchanged (nothing to resolve).
    """
    if not value or _FLYER_URL_MARKER not in value:
        return value
    filename = value.rsplit(_FLYER_URL_MARKER, 1)[-1]
    return flyer_url(filename)


# How long a show is assumed to run when Event.end_datetime was never set
# (the Add/Edit Show form has no end-time field -- most scraped venue
# feeds don't publish one either) -- long enough to cover a typical
# multi-band bill without the calendar entry looking like it ends in the
# middle of the show, short enough that it doesn't visibly bleed into the
# next morning on a calendar app's day view.
DEFAULT_EVENT_DURATION = timedelta(hours=3)


def build_event_ics(event):
    """A single-event .ics file (RFC 5545) for the "Add to calendar"
    button on events/detail.html -- built fresh on every request, from
    the live Event row, the same "recompute now, don't trust anything
    baked in earlier" principle as resolve_image_url() above (see that
    docstring): the file's URL/UID never depend on whatever host or
    mount-prefix happened to be in play when someone else downloaded it
    first, only on *this* request's.

    DTSTART/DTEND are converted to real UTC before handing them to
    icalendar, rather than attaching SITE_TIMEZONE's "America/New_York"
    Olson id directly and emitting a TZID parameter -- a bare UTC
    timestamp needs no accompanying VTIMEZONE block to be unambiguous,
    where a TZID one technically does (some calendar apps tolerate a
    missing VTIMEZONE for a well-known id, but it's not guaranteed by the
    spec). Event.start_datetime is stored as naive local wall-clock time
    (see SITE_TIMEZONE's docstring) so it has to be told what timezone it
    actually is (`.replace(tzinfo=SITE_TIMEZONE)`, not a real conversion,
    since it carries no tzinfo of its own yet) before it can be converted
    to UTC.

    UID is `event-<id>@paradisecitymusic` -- stable across re-downloads
    and independent of the serving domain (unlike the DESCRIPTION/URL
    fields below, which *should* reflect wherever this request is being
    served from) -- so a calendar app that re-imports the same show later
    recognizes it as the same event rather than creating a duplicate.
    """
    from flask import url_for

    start_local = event.start_datetime.replace(tzinfo=SITE_TIMEZONE)
    start_utc = start_local.astimezone(timezone.utc)
    if event.end_datetime:
        end_utc = event.end_datetime.replace(tzinfo=SITE_TIMEZONE).astimezone(timezone.utc)
    else:
        end_utc = start_utc + DEFAULT_EVENT_DURATION

    detail_url = url_for("main.event_detail", event_id=event.id, _external=True)

    # display_venue_name, not event.venue.name -- a one-off location (see
    # Event.custom_venue_name's docstring in models.py) has its own name
    # that should show up here instead. Its address/city/state belong to
    # the placeholder Venue (e.g. "DIY") this event is filed under, not the
    # actual one-off spot, so they'd be actively wrong/misleading here --
    # skipped entirely once custom_venue_name is set, same reasoning as
    # the calendar/detail templates suppressing city/state in that case.
    location_parts = [event.display_venue_name]
    if not event.custom_venue_name:
        if event.venue.address:
            location_parts.append(event.venue.address)
        city_state = ", ".join(p for p in (event.venue.city, event.venue.state) if p)
        if city_state:
            location_parts.append(city_state)

    description_lines = []
    if event.description:
        description_lines.append(event.description)
    if event.price_info:
        description_lines.append(f"Price: {event.price_info}")
    if event.ticket_url:
        description_lines.append(f"Tickets: {event.ticket_url}")
    description_lines.append(f"Full listing: {detail_url}")

    vevent = ICSEvent()
    vevent.add("uid", f"event-{event.id}@paradisecitymusic")
    vevent.add("dtstamp", datetime.now(timezone.utc))
    vevent.add("dtstart", start_utc)
    vevent.add("dtend", end_utc)
    vevent.add("summary", event.title)
    vevent.add("location", ", ".join(location_parts))
    vevent.add("description", "\n".join(description_lines))
    vevent.add("url", detail_url)

    cal = Calendar()
    cal.add("prodid", "-//Paradise City Music//paradisecitymusic//EN")
    cal.add("version", "2.0")
    cal.add("calscale", "GREGORIAN")
    cal.add_component(vevent)
    return cal.to_ical()

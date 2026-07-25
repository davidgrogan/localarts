import re
from datetime import datetime
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

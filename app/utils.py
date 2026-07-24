import re


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

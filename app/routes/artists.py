from datetime import datetime

from flask import Blueprint, render_template, request, redirect, url_for, flash

from app.models import db, Artist, Event, GenreTag, EventType
from app.utils import slugify, get_or_create_genre_tag, get_or_create_event_type
from app.auth import login_required

bp = Blueprint("artists", __name__, url_prefix="/artists")
# Unlike venues/events, this blueprint is a mix -- browsing the local
# artist roster (list_artists, detail) is core public content (the whole
# point of the site), so only the add/edit/delete routes below are
# individually gated rather than protecting the whole blueprint.


def _resolve_genre_tags(form):
    """Turn a submitted artist form's checked Genre Tag ids + quick-add
    text into a list of GenreTag objects, creating any brand-new ones.
    Mirrors app/routes/events.py's _resolve_event_types()."""
    selected_ids = form.getlist("genre_tag_ids")
    tags = GenreTag.query.filter(GenreTag.id.in_(selected_ids)).all() if selected_ids else []

    new_names = form.get("new_genre_tag_names", "").strip()
    if new_names:
        for raw_name in new_names.split(","):
            tag = get_or_create_genre_tag(raw_name)
            if tag and tag not in tags:
                tags.append(tag)
    return tags


def _resolve_category_tags(form):
    """Same idea as _resolve_genre_tags() above, for an artist's Category
    Tags -- which reuse the same EventType tags events are categorized
    with (see artist_event_types in models.py)."""
    selected_ids = form.getlist("category_tag_ids")
    tags = EventType.query.filter(EventType.id.in_(selected_ids)).all() if selected_ids else []

    new_names = form.get("new_category_tag_names", "").strip()
    if new_names:
        for raw_name in new_names.split(","):
            tag = get_or_create_event_type(raw_name)
            if tag and tag not in tags:
                tags.append(tag)
    return tags


def _find_artist_matching_title(title):
    """Best-effort match of an existing artist against a show's title, for
    pre-filling the quick-add form with data that already exists rather
    than a blank one. An exact slug match on the whole title rarely hits,
    since a show's title is often messier than an artist's cleaned-up name
    (e.g. "The Olllam w/ Kathleen Parks" vs. the artist "The Olllam") --
    so this also checks whether any existing artist's name shows up as a
    substring of the title. Fine for a POC-scale artist list; would need
    something smarter at real scale."""
    if not title:
        return None
    exact = Artist.query.filter_by(slug=slugify(title)).first()
    if exact:
        return exact
    title_lower = title.lower()
    for artist in Artist.query.all():
        if artist.name and artist.name.lower() in title_lower:
            return artist
    return None


@bp.route("/")
def list_artists():
    genre_tag_id = request.args.get("genre", type=int)
    category_tag_id = request.args.get("category", type=int)
    # Toggle rather than a tri-state filter -- visitors either want "just
    # the ones with something coming up" or everyone; there's no real use
    # case for the inverse ("only artists with nothing booked").
    upcoming_only = request.args.get("upcoming") == "1"

    query = Artist.query.order_by(Artist.name)
    if genre_tag_id:
        query = query.filter(Artist.genre_tags.any(GenreTag.id == genre_tag_id))
    if category_tag_id:
        query = query.filter(Artist.category_tags.any(EventType.id == category_tag_id))
    if upcoming_only:
        # .any() (an EXISTS subquery) rather than a join -- a join here
        # would duplicate an artist once per upcoming show they have.
        query = query.filter(Artist.events.any(Event.start_datetime >= datetime.utcnow()))
    artists = query.all()

    return render_template(
        "artists/list.html",
        artists=artists,
        genre_tags=GenreTag.query.order_by(GenreTag.name).all(),
        category_tags=EventType.query.order_by(EventType.name).all(),
        selected_genre=genre_tag_id,
        selected_category=category_tag_id,
        upcoming_only=upcoming_only,
    )


@bp.route("/new", methods=["GET", "POST"])
@login_required
def new_artist():
    # Supports two entry points: Artists -> Add artist (a blank form), and
    # a "+ Add as local artist" link on an event listing that pre-fills
    # the name from that show's title and links the result back to it --
    # see calendar.html and the from_event_id handling below.
    from_event_id = request.values.get("from_event", type=int)
    source_event = Event.query.get(from_event_id) if from_event_id else None

    if request.method == "POST":
        name = request.form["name"].strip()
        slug = slugify(name)

        # Match on slug first rather than always inserting: quick-adding
        # the same touring act from two different show listings shouldn't
        # crash on the unique slug constraint or create a duplicate --
        # it should just link the existing artist to this show too.
        artist = Artist.query.filter_by(slug=slug).first()
        is_new = artist is None
        if is_new:
            artist = Artist(name=name, slug=slug)
            db.session.add(artist)
        else:
            artist.name = name

        artist.hometown = request.form.get("hometown", "").strip()
        artist.bio = request.form.get("bio", "").strip()
        artist.image_url = request.form.get("image_url", "").strip()
        artist.website_url = request.form.get("website_url", "").strip()
        artist.embed_code = request.form.get("embed_code", "").strip()
        artist.is_local = bool(request.form.get("is_local"))
        artist.genre_tags = _resolve_genre_tags(request.form)
        artist.category_tags = _resolve_category_tags(request.form)

        linked_event_id = request.form.get("from_event_id", type=int)
        linked_event = Event.query.get(linked_event_id) if linked_event_id else None
        if linked_event and linked_event not in artist.events:
            artist.events.append(linked_event)

        db.session.commit()

        verb = "Added" if is_new else "Updated"
        message = f"{verb} artist “{artist.name}”."
        if linked_event:
            message += f" Linked to “{linked_event.title}”."
        flash(message, "success")

        if linked_event:
            return redirect(url_for("main.calendar"))
        return redirect(url_for("artists.detail", artist_id=artist.id))

    # If an artist matching the show's title already exists, pre-fill the
    # form with *their* current data (not a blank form) -- otherwise
    # submitting without re-typing genre/bandcamp/bio/embed code would
    # blank out everything already saved for them.
    existing_artist = _find_artist_matching_title(source_event.title) if source_event else None
    return render_template(
        "artists/form.html",
        artist=existing_artist,
        source_event=source_event,
        genre_tags=GenreTag.query.order_by(GenreTag.name).all(),
        category_tags=EventType.query.order_by(EventType.name).all(),
    )


@bp.route("/<int:artist_id>")
def detail(artist_id):
    artist = Artist.query.get_or_404(artist_id)
    return render_template("artists/detail.html", artist=artist)


@bp.route("/<int:artist_id>/edit", methods=["GET", "POST"])
@login_required
def edit_artist(artist_id):
    artist = Artist.query.get_or_404(artist_id)
    if request.method == "POST":
        artist.name = request.form["name"].strip()
        artist.hometown = request.form.get("hometown", "").strip()
        artist.bio = request.form.get("bio", "").strip()
        artist.image_url = request.form.get("image_url", "").strip()
        artist.website_url = request.form.get("website_url", "").strip()
        artist.embed_code = request.form.get("embed_code", "").strip()
        artist.is_local = bool(request.form.get("is_local"))
        artist.genre_tags = _resolve_genre_tags(request.form)
        artist.category_tags = _resolve_category_tags(request.form)
        db.session.commit()
        flash(f"Updated “{artist.name}”.", "success")
        return redirect(url_for("artists.detail", artist_id=artist.id))
    return render_template(
        "artists/form.html",
        artist=artist,
        source_event=None,
        genre_tags=GenreTag.query.order_by(GenreTag.name).all(),
        category_tags=EventType.query.order_by(EventType.name).all(),
    )


@bp.route("/<int:artist_id>/delete", methods=["POST"])
@login_required
def delete_artist(artist_id):
    artist = Artist.query.get_or_404(artist_id)
    db.session.delete(artist)
    db.session.commit()
    flash(f"Deleted “{artist.name}”.", "success")
    return redirect(url_for("artists.list_artists"))

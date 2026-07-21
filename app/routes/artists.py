from flask import Blueprint, render_template, request, redirect, url_for, flash

from app.models import db, Artist, Event
from app.utils import slugify
from app.auth import login_required

bp = Blueprint("artists", __name__, url_prefix="/artists")
# Unlike venues/events, this blueprint is a mix -- browsing the local
# artist roster (list_artists, detail) is core public content (the whole
# point of the site), so only the add/edit/delete routes below are
# individually gated rather than protecting the whole blueprint.


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
    artists = Artist.query.order_by(Artist.name).all()
    return render_template("artists/list.html", artists=artists)


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
        artist.genre = request.form.get("genre", "").strip()
        artist.bio = request.form.get("bio", "").strip()
        artist.website_url = request.form.get("website_url", "").strip()
        artist.embed_code = request.form.get("embed_code", "").strip()
        artist.is_local = bool(request.form.get("is_local"))

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
    return render_template("artists/form.html", artist=existing_artist, source_event=source_event)


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
        artist.genre = request.form.get("genre", "").strip()
        artist.bio = request.form.get("bio", "").strip()
        artist.website_url = request.form.get("website_url", "").strip()
        artist.embed_code = request.form.get("embed_code", "").strip()
        artist.is_local = bool(request.form.get("is_local"))
        db.session.commit()
        flash(f"Updated “{artist.name}”.", "success")
        return redirect(url_for("artists.detail", artist_id=artist.id))
    return render_template("artists/form.html", artist=artist, source_event=None)


@bp.route("/<int:artist_id>/delete", methods=["POST"])
@login_required
def delete_artist(artist_id):
    artist = Artist.query.get_or_404(artist_id)
    db.session.delete(artist)
    db.session.commit()
    flash(f"Deleted “{artist.name}”.", "success")
    return redirect(url_for("artists.list_artists"))

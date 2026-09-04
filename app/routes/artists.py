import random

from flask import Blueprint, render_template, request, redirect, url_for, flash

from app.models import db, Artist, ArtistLink, Event, GenreTag, EventType
from app.utils import (
    slugify,
    get_or_create_genre_tag,
    get_or_create_event_type,
    local_now,
    artist_sort_key,
    artist_display_letter,
)
from app.auth import login_required

bp = Blueprint("artists", __name__, url_prefix="/artists")
# Unlike venues/events, this blueprint is a mix -- browsing the local
# artist roster (list_artists, detail) is core public content (the whole
# point of the site), so only the add/edit/delete routes below are
# individually gated rather than protecting the whole blueprint.
#
# Note on "Import from Bandcamp": there's deliberately no server-side
# route for this. An earlier version fetched the Bandcamp URL directly
# (first with plain requests, then with a headless Playwright browser
# once that got served stripped content) -- both got a real CAPTCHA from
# Bandcamp once actually run against a live URL, not just a fingerprint
# check any amount of disguising the request could get past. The feature
# now runs entirely client-side: a bookmarklet (see
# app/bandcamp_bookmarklet.py and app/static/bandcamp_bookmarklet.js) does
# the fetching from the admin's own real browser session while they're
# already on the band's Bandcamp page, and artists/form.html has a paste
# box + inline <script> that fills in the fields from what it copies to
# the clipboard -- no server round-trip, so no route needed here at all.


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


def _resolve_artist_links(form):
    """Turns the artist form's repeatable "Artist Links" rows into a list
    of brand-new ArtistLink objects (not yet attached to any artist -- the
    caller assigns them via `artist.links = ...`, which -- combined with
    that relationship's cascade="all, delete-orphan" in models.py --
    replaces the artist's whole link list on every save, same "just
    replace the collection" pattern _resolve_genre_tags()/
    _resolve_category_tags() above already use for Artist's other
    list-valued fields. Simpler than diffing individual row
    adds/edits/removes/reorders for a list this small.

    link_titles/link_urls are two same-length parallel lists (one entry
    per row the form rendered -- see artists/form.html's JS for how rows
    are added/removed), zipped back together here. A row with a title but
    no URL is dropped entirely (nothing to link to); a URL with no title
    falls back to using the URL itself as the link text, same idea as
    _migrate_freak_scene_links()'s generic-title fallback in
    app/__init__.py. sort_order is just each row's position in the
    submitted form -- good enough since the form itself is the only way
    to reorder them."""
    titles = form.getlist("link_titles")
    urls = form.getlist("link_urls")
    links = []
    for i, (title, url) in enumerate(zip(titles, urls)):
        title = title.strip()
        url = url.strip()
        if not url:
            continue
        links.append(ArtistLink(title=title or url, url=url, sort_order=i))
    return links


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


def _upcoming_events_for(artist, now):
    """Every future, approved show linked to this artist, soonest first --
    shared by the artist detail page's "Upcoming shows" list and the
    artist index's per-card/hero "next show" line, so both use the exact
    same is_approved/start_datetime>=now filtering (previously duplicated
    inline on the detail page only). See detail()'s original comment for
    why is_approved and local_now() (not datetime.utcnow(); see
    app/utils.py's SITE_TIMEZONE docstring) both matter here."""
    return sorted(
        (e for e in artist.events if e.is_approved and e.start_datetime >= now),
        key=lambda e: e.start_datetime,
    )


@bp.route("/")
def list_artists():
    genre_tag_id = request.args.get("genre", type=int)

    query = Artist.query
    if genre_tag_id:
        query = query.filter(Artist.genre_tags.any(GenreTag.id == genre_tag_id))
    # Sorted in Python via artist_sort_key(), not a SQL ORDER BY -- that
    # key both lowercases (a plain ORDER BY sorts by byte value, which
    # puts every capitalized name before any lowercase one regardless of
    # letter, e.g. "Zeta" before "alice") and ignores a leading "The " so
    # "The Mountain Movers" sorts under M, not T. Either inconsistency
    # used to (or would) scatter a single letter's artists across more
    # than one place in the list, which breaks the A-Z jump bar's
    # assumption that each letter's artists are contiguous (see
    # list.html's single "current letter changed" anchor per letter).
    artists = sorted(query.all(), key=lambda a: artist_sort_key(a.name))

    now = local_now()
    next_shows = {}
    for a in artists:
        upcoming = _upcoming_events_for(a, now)
        next_shows[a.id] = upcoming[0] if upcoming else None

    # A-Z jump bar only needs to know which letters actually have an
    # artist -- everything else renders as an unclickable placeholder so
    # the bar's width/spacing stays constant regardless of the roster.
    # artist_display_letter(), not a.name[0], so a "The ..." artist counts
    # toward the letter it's actually alphabetized/grouped under.
    available_letters = {artist_display_letter(a.name) for a in artists if a.name}

    # Spotlight pick: always drawn from the *full* local roster, not
    # whatever the genre filter above narrowed "artists" down to -- so
    # applying a filter doesn't make the spotlight disappear or feel
    # arbitrarily tied to it. Restricted to is_local artists (this page's
    # whole point is surfacing local acts; touring artists only end up
    # listed here because they played a show with a local act on the
    # bill), falling back to literally anyone if an install somehow has
    # zero local artists yet, rather than showing no spotlight at all.
    local_roster = Artist.query.filter_by(is_local=True).all()
    hero_pool = local_roster or Artist.query.all()
    hero_artist = random.choice(hero_pool) if hero_pool else None
    hero_upcoming = _upcoming_events_for(hero_artist, now) if hero_artist else []

    return render_template(
        "artists/list.html",
        artists=artists,
        genre_tags=GenreTag.query.order_by(GenreTag.name).all(),
        selected_genre=genre_tag_id,
        next_shows=next_shows,
        available_letters=available_letters,
        hero_artist=hero_artist,
        hero_next_show=hero_upcoming[0] if hero_upcoming else None,
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
        artist.links = _resolve_artist_links(request.form)
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
    # "Upcoming shows" on this public page used to just list *every*
    # Event ever linked to this artist (artist.events, unfiltered) sorted
    # by date -- so a show from months ago sorted right in alongside real
    # upcoming ones, still under an "Upcoming shows" heading. Filtering
    # to is_approved (same as every other public listing on this site --
    # see _base_query() in main.py) and start_datetime >= now actually
    # makes the heading true -- see _upcoming_events_for()'s docstring.
    upcoming_events = _upcoming_events_for(artist, local_now())
    return render_template("artists/detail.html", artist=artist, upcoming_events=upcoming_events)


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
        artist.links = _resolve_artist_links(request.form)
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

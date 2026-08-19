from datetime import datetime

from flask import Blueprint, render_template, request, redirect, url_for, flash

from app.models import db, Event, Venue, Artist, EventType, GigSubmission
from app.utils import (
    slugify,
    get_or_create_event_type,
    local_now,
    flyer_url,
    resolve_uploaded_image_url,
    artist_sort_key,
)
from app.auth import require_admin

bp = Blueprint("events", __name__, url_prefix="/events")
# Entirely an admin surface -- adding/editing/approving/deleting shows and
# the review queue. Visitors only ever see events rendered on the public
# calendar, never through this blueprint.
bp.before_request(require_admin)


def _resolve_event_types(form):
    """Turn a submitted event form's checked tag ids + quick-add text
    into a list of EventType objects, creating any brand-new ones."""
    selected_ids = form.getlist("event_type_ids")
    tags = EventType.query.filter(EventType.id.in_(selected_ids)).all() if selected_ids else []

    new_names = form.get("new_event_type_names", "").strip()
    if new_names:
        for raw_name in new_names.split(","):
            tag = get_or_create_event_type(raw_name)
            if tag and tag not in tags:
                tags.append(tag)
    return tags


def _resolve_image_url(form, files):
    """Figures out what Event.image_url should end up as after a manual
    Add/Edit Show form submission -- either a flyer file uploaded right on
    this form (see form.html's new "Upload a flyer" field), or the
    existing "Image / flyer URL" text field (still there for pasting in a
    URL directly, or because it was pre-filled from a gig-submission
    conversion's own uploaded flyer -- see _gig_prefill()/new_event()'s
    from_gig handling).

    An uploaded file always wins over whatever's in the text field, on the
    theory that if an admin bothered to pick a file, that's the one they
    actually want used -- so the text field is only a fallback for when no
    file was chosen this time. Reuses gigs.py's save_flyer_upload(), the
    same upload pipeline already built for the public "Submit a Show" form
    (uuid-renamed, saved under app/static/uploads/flyers/, no separate
    serving route needed) rather than a second one.

    When no file is uploaded, this returns exactly what's in the text
    field -- including blank, which is how both new_event()/edit_event()
    have always cleared image_url to None. (On Edit, the text field is
    pre-filled with the event's current image_url, so leaving it alone in
    practice preserves it; deliberately clearing that field is still the
    one way to remove an image, same as before this feature existed.)

    A file *was* chosen but isn't a supported image type: flashes a
    warning and falls back to the text field instead of blocking the whole
    save -- unlike the public submission form (where a flyer is required),
    a flyer here is always optional, so a bad file shouldn't cost the
    admin the rest of what they just filled in.

    Just a thin wrapper around app/utils.py's resolve_uploaded_image_url()
    now -- pulled out there once venues.py's Add/Edit Venue form needed
    the exact same upload-vs-URL precedence logic for a venue's own
    photo, rather than duplicating it a second time.
    """
    return resolve_uploaded_image_url(
        form, files,
        bad_file_message=(
            "That flyer file type isn't supported (JPG, PNG, GIF, or WEBP only) "
            "-- the show was saved without changing its image."
        ),
    )


def _diy_venue_id():
    """The shared catch-all "DIY" venue's id (seeded in seed.py), used to
    default the venue picker when converting a gig submission -- most
    submissions are expected to be one-off DIY shows with no formal venue
    of their own. Returns None (leaving the picker unselected) if a "DIY"
    venue hasn't been created on this install yet, rather than erroring."""
    diy = Venue.query.filter_by(slug="diy").first()
    return diy.id if diy else None


def _gig_prefill(gig):
    """Build the description text + placeholder venue/date values used to
    pre-fill the Add Show form when converting a GigSubmission (see
    events.new_event()'s from_gig handling). The submitted location text
    isn't given its own Event column (per David's call -- DIY shows all
    get grouped under the shared "DIY" venue instead); it's preserved here
    in the description instead, along with the submitter's contact info,
    so nothing from the original submission is lost once the row itself
    gets marked converted. Same reasoning for genres_text -- there's no
    manual-add-show form field for Event.genre to prefill instead."""
    genre_line = f"Genre(s) as submitted: {gig.genres_text}\n" if gig.genres_text else ""
    return (
        f"Submitted by {gig.submitter_name} ({gig.submitter_email}).\n"
        f"Location as submitted: {gig.venue_name}\n"
        f"{genre_line}\n"
        f"Lineup:\n{gig.lineup_text}"
    )


@bp.route("/new", methods=["GET", "POST"])
def new_event():
    venues = Venue.query.order_by(Venue.name).all()
    # artist_sort_key() (not a plain ORDER BY) so a "The ..." band's
    # checkbox lines up alphabetically here the same way it does on the
    # /artists index -- see that key's own docstring in app/utils.py.
    artists = sorted(Artist.query.all(), key=lambda a: artist_sort_key(a.name))
    event_types = EventType.query.order_by(EventType.name).all()

    # Converting a pending gig submission (see app/routes/gigs.py) into a
    # real show -- request.values covers both the query string (GET, the
    # "Convert to show" link's ?from_gig=<id>) and the hidden form field
    # below (POST, so the submission can be looked up again after submit
    # without trusting a resubmitted query string).
    from_gig_id = request.values.get("from_gig", type=int)
    gig = GigSubmission.query.get(from_gig_id) if from_gig_id else None

    if request.method == "POST":
        venue = Venue.query.get_or_404(int(request.form["venue_id"]))
        start_dt = datetime.strptime(request.form["start_datetime"], "%Y-%m-%dT%H:%M")
        event = Event(
            venue_id=venue.id,
            # One-off location override (festival stage, street fair,
            # etc.) -- see Event.custom_venue_name's docstring. Blank is
            # the overwhelmingly common case (a real, listed venue was
            # picked above), so this stays None rather than "" then.
            custom_venue_name=request.form.get("custom_venue_name", "").strip() or None,
            title=request.form["title"].strip(),
            start_datetime=start_dt,
            description=request.form.get("description", "").strip(),
            ticket_url=request.form.get("ticket_url", "").strip() or None,
            price_info=request.form.get("price_info", "").strip() or None,
            image_url=_resolve_image_url(request.form, request.files),
            source="manual",
            is_approved=True,
        )

        artist_ids = request.form.getlist("artist_ids")
        if artist_ids:
            event.artists = Artist.query.filter(Artist.id.in_(artist_ids)).all()

        # Quick-add a brand new local artist right from the show form,
        # rather than forcing a trip to Artists -> New first.
        new_artist_name = request.form.get("new_artist_name", "").strip()
        if new_artist_name:
            artist = Artist(name=new_artist_name, slug=slugify(new_artist_name))
            db.session.add(artist)
            event.artists.append(artist)

        # Tags: whatever was picked/quick-added, or -- if nothing was --
        # the venue's own default tag (e.g. Iron Horse shows default to
        # "Music" so this doesn't need setting by hand every time).
        tags = _resolve_event_types(request.form)
        event.event_types = tags if tags else ([venue.default_event_type] if venue.default_event_type else [])

        db.session.add(event)
        db.session.flush()  # assigns event.id before linking the submission below

        # Only reached once the form is actually saved (not just opened via
        # the "Convert to show" link) -- see gigs.py's review()/module
        # docstring for why conversion isn't a separate bespoke route.
        converted_gig_id = request.form.get("from_gig_id", type=int)
        if converted_gig_id:
            converted_gig = GigSubmission.query.get(converted_gig_id)
            if converted_gig:
                converted_gig.status = "converted"
                converted_gig.reviewed_at = local_now()
                converted_gig.converted_event_id = event.id

        db.session.commit()
        flash(f"Added show “{event.title}”.", "success")
        return redirect(url_for("main.calendar"))

    return render_template(
        "events/form.html",
        event=None,
        venues=venues,
        artists=artists,
        event_types=event_types,
        gig=gig,
        gig_description=_gig_prefill(gig) if gig else None,
        gig_default_venue_id=_diy_venue_id() if gig else None,
        gig_image_url=flyer_url(gig.flyer_filename) if gig else None,
    )


@bp.route("/<int:event_id>/edit", methods=["GET", "POST"])
def edit_event(event_id):
    event = Event.query.get_or_404(event_id)
    venues = Venue.query.order_by(Venue.name).all()
    # artist_sort_key() (not a plain ORDER BY) so a "The ..." band's
    # checkbox lines up alphabetically here the same way it does on the
    # /artists index -- see that key's own docstring in app/utils.py.
    artists = sorted(Artist.query.all(), key=lambda a: artist_sort_key(a.name))
    event_types = EventType.query.order_by(EventType.name).all()
    # Where "Save changes" should return to -- e.g. the Review page's Edit
    # links pass ?next=/events/review so editing a pending show returns
    # there instead of the public calendar. request.values covers both the
    # query string (GET, first load) and the hidden field below (POST).
    next_url = request.values.get("next") or url_for("main.calendar")

    if request.method == "POST":
        event.venue_id = int(request.form["venue_id"])
        event.custom_venue_name = request.form.get("custom_venue_name", "").strip() or None
        event.title = request.form["title"].strip()
        event.start_datetime = datetime.strptime(request.form["start_datetime"], "%Y-%m-%dT%H:%M")
        event.description = request.form.get("description", "").strip()
        event.ticket_url = request.form.get("ticket_url", "").strip() or None
        event.price_info = request.form.get("price_info", "").strip() or None
        event.image_url = _resolve_image_url(request.form, request.files)

        artist_ids = request.form.getlist("artist_ids")
        event.artists = Artist.query.filter(Artist.id.in_(artist_ids)).all() if artist_ids else []

        event.event_types = _resolve_event_types(request.form)

        db.session.commit()
        flash("Updated show.", "success")
        return redirect(next_url)

    return render_template(
        "events/form.html",
        event=event,
        venues=venues,
        artists=artists,
        event_types=event_types,
        next_url=next_url,
    )


@bp.route("/<int:event_id>/approve", methods=["POST"])
def approve_event(event_id):
    event = Event.query.get_or_404(event_id)
    event.is_approved = True
    db.session.commit()
    flash(f"Approved “{event.title}” -- it'll now show on the public calendar.", "success")
    # A "next" field (set by the edit form's Approve button, so approving
    # from there returns to wherever Edit was reached from -- e.g. Review)
    # takes priority; otherwise fall back to the referring page (how the
    # Review page's own Approve button has always worked) or the calendar.
    return redirect(request.form.get("next") or request.referrer or url_for("main.calendar"))


@bp.route("/<int:event_id>/delete", methods=["POST"])
def delete_event(event_id):
    event = Event.query.get_or_404(event_id)
    title = event.title
    db.session.delete(event)
    db.session.commit()
    flash(f"Deleted “{title}”.", "success")
    return redirect(request.referrer or url_for("main.calendar"))


@bp.route("/<int:event_id>/dismiss-flag", methods=["POST"])
def dismiss_flag(event_id):
    """Clear a "changed" flag on an already-approved event -- the admin
    looked at the new scraped time/title and it's fine as-is."""
    event = Event.query.get_or_404(event_id)
    event.needs_review = False
    event.review_note = None
    db.session.commit()
    flash(f"Dismissed the flag on “{event.title}”.", "success")
    return redirect(request.referrer or url_for("events.review"))


@bp.route("/<int:event_id>/unpublish", methods=["POST"])
def unpublish_event(event_id):
    """Pull an approved event back off the public calendar (e.g. a
    flagged change turned out to be wrong) without deleting its data."""
    event = Event.query.get_or_404(event_id)
    event.is_approved = False
    event.needs_review = False
    event.review_note = None
    db.session.commit()
    flash(f"Unpublished “{event.title}”.", "success")
    return redirect(request.referrer or url_for("events.review"))


@bp.route("/<int:event_id>/restore", methods=["POST"])
def restore_event(event_id):
    """Undo an auto-hide from a "possibly cancelled" flag -- the show is
    actually still happening (it just fell out of the venue's feed)."""
    event = Event.query.get_or_404(event_id)
    event.is_approved = True
    event.needs_review = False
    event.review_note = None
    event.missing_streak = 0
    db.session.commit()
    flash(f"Restored “{event.title}” -- it's back on the public calendar.", "success")
    return redirect(request.referrer or url_for("events.review"))


@bp.route("/<int:event_id>/reject", methods=["POST"])
def reject_event(event_id):
    """Discard a still-pending ("New") scraped event without deleting its
    row outright -- deleting it would make it look brand-new again on the
    very next scrape (matching is by venue_id + external_id, and a
    deleted row leaves nothing to match against). Keeping it, hidden and
    marked is_rejected, means a future scrape recognizes it and leaves it
    alone; its other fields still get refreshed on re-scrape like any
    matched event, so it's current if it's ever restored."""
    event = Event.query.get_or_404(event_id)
    event.is_rejected = True
    db.session.commit()
    flash(f"Discarded “{event.title}” -- it won't be re-added by future scrapes.", "success")
    return redirect(request.referrer or url_for("events.review"))


@bp.route("/<int:event_id>/unreject", methods=["POST"])
def unreject_event(event_id):
    """Undo a reject -- puts it back in the "New" bucket for review
    (not straight onto the public calendar; use Approve for that)."""
    event = Event.query.get_or_404(event_id)
    event.is_rejected = False
    db.session.commit()
    flash(f"Restored “{event.title}” to the New list for review.", "success")
    return redirect(request.referrer or url_for("events.review"))


@bp.route("/review")
def review():
    # Four mutually-exclusive buckets driven by is_approved / needs_review /
    # is_rejected (see Event model / run_scrape() docstrings for how they
    # get set):
    #   New             -- is_approved=False, needs_review=False, is_rejected=False (never seen before)
    #   Changed         -- is_approved=True,  needs_review=True                     (still live, flagged)
    #   Poss. cancelled -- is_approved=False, needs_review=True                      (auto-hidden)
    #   Rejected        -- is_rejected=True                                         (discarded, remembered)
    new_events = (
        Event.query.filter_by(is_approved=False, needs_review=False, is_rejected=False)
        .order_by(Event.start_datetime.asc())
        .all()
    )
    changed_events = (
        Event.query.filter_by(is_approved=True, needs_review=True)
        .order_by(Event.start_datetime.asc())
        .all()
    )
    cancelled_events = (
        Event.query.filter_by(is_approved=False, needs_review=True, is_rejected=False)
        .order_by(Event.start_datetime.asc())
        .all()
    )
    rejected_events = (
        Event.query.filter_by(is_rejected=True).order_by(Event.start_datetime.asc()).all()
    )
    return render_template(
        "events/review.html",
        new_events=new_events,
        changed_events=changed_events,
        cancelled_events=cancelled_events,
        rejected_events=rejected_events,
    )

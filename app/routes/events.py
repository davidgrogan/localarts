from datetime import datetime

from flask import Blueprint, render_template, request, redirect, url_for, flash

from app.models import db, Event, Venue, Artist, EventType
from app.utils import slugify, get_or_create_event_type
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


@bp.route("/new", methods=["GET", "POST"])
def new_event():
    venues = Venue.query.order_by(Venue.name).all()
    artists = Artist.query.order_by(Artist.name).all()
    event_types = EventType.query.order_by(EventType.name).all()

    if request.method == "POST":
        venue = Venue.query.get_or_404(int(request.form["venue_id"]))
        start_dt = datetime.strptime(request.form["start_datetime"], "%Y-%m-%dT%H:%M")
        event = Event(
            venue_id=venue.id,
            title=request.form["title"].strip(),
            start_datetime=start_dt,
            description=request.form.get("description", "").strip(),
            ticket_url=request.form.get("ticket_url", "").strip() or None,
            price_info=request.form.get("price_info", "").strip() or None,
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
        db.session.commit()
        flash(f"Added show “{event.title}”.", "success")
        return redirect(url_for("main.calendar"))

    return render_template(
        "events/form.html", event=None, venues=venues, artists=artists, event_types=event_types
    )


@bp.route("/<int:event_id>/edit", methods=["GET", "POST"])
def edit_event(event_id):
    event = Event.query.get_or_404(event_id)
    venues = Venue.query.order_by(Venue.name).all()
    artists = Artist.query.order_by(Artist.name).all()
    event_types = EventType.query.order_by(EventType.name).all()

    if request.method == "POST":
        event.venue_id = int(request.form["venue_id"])
        event.title = request.form["title"].strip()
        event.start_datetime = datetime.strptime(request.form["start_datetime"], "%Y-%m-%dT%H:%M")
        event.description = request.form.get("description", "").strip()
        event.ticket_url = request.form.get("ticket_url", "").strip() or None
        event.price_info = request.form.get("price_info", "").strip() or None

        artist_ids = request.form.getlist("artist_ids")
        event.artists = Artist.query.filter(Artist.id.in_(artist_ids)).all() if artist_ids else []

        event.event_types = _resolve_event_types(request.form)

        db.session.commit()
        flash("Updated show.", "success")
        return redirect(url_for("main.calendar"))

    return render_template(
        "events/form.html", event=event, venues=venues, artists=artists, event_types=event_types
    )


@bp.route("/<int:event_id>/approve", methods=["POST"])
def approve_event(event_id):
    event = Event.query.get_or_404(event_id)
    event.is_approved = True
    db.session.commit()
    flash(f"Approved “{event.title}” -- it'll now show on the public calendar.", "success")
    return redirect(request.referrer or url_for("main.calendar"))


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


@bp.route("/review")
def review():
    # Three mutually-exclusive buckets driven by is_approved + needs_review
    # (see Event model / run_scrape() docstrings for how they get set):
    #   New          -- is_approved=False, needs_review=False  (never seen before)
    #   Changed       -- is_approved=True,  needs_review=True   (still live, flagged)
    #   Poss. cancelled -- is_approved=False, needs_review=True (auto-hidden)
    new_events = (
        Event.query.filter_by(is_approved=False, needs_review=False)
        .order_by(Event.start_datetime.asc())
        .all()
    )
    changed_events = (
        Event.query.filter_by(is_approved=True, needs_review=True)
        .order_by(Event.start_datetime.asc())
        .all()
    )
    cancelled_events = (
        Event.query.filter_by(is_approved=False, needs_review=True)
        .order_by(Event.start_datetime.asc())
        .all()
    )
    return render_template(
        "events/review.html",
        new_events=new_events,
        changed_events=changed_events,
        cancelled_events=cancelled_events,
    )

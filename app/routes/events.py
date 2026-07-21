from datetime import datetime

from flask import Blueprint, render_template, request, redirect, url_for, flash

from app.models import db, Event, Venue, Artist
from app.utils import slugify
from app.auth import require_admin

bp = Blueprint("events", __name__, url_prefix="/events")
# Entirely an admin surface -- adding/editing/approving/deleting shows and
# the review queue. Visitors only ever see events rendered on the public
# calendar, never through this blueprint.
bp.before_request(require_admin)


@bp.route("/new", methods=["GET", "POST"])
def new_event():
    venues = Venue.query.order_by(Venue.name).all()
    artists = Artist.query.order_by(Artist.name).all()

    if request.method == "POST":
        start_dt = datetime.strptime(request.form["start_datetime"], "%Y-%m-%dT%H:%M")
        event = Event(
            venue_id=int(request.form["venue_id"]),
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

        db.session.add(event)
        db.session.commit()
        flash(f"Added show “{event.title}”.", "success")
        return redirect(url_for("main.calendar"))

    return render_template("events/form.html", event=None, venues=venues, artists=artists)


@bp.route("/<int:event_id>/edit", methods=["GET", "POST"])
def edit_event(event_id):
    event = Event.query.get_or_404(event_id)
    venues = Venue.query.order_by(Venue.name).all()
    artists = Artist.query.order_by(Artist.name).all()

    if request.method == "POST":
        event.venue_id = int(request.form["venue_id"])
        event.title = request.form["title"].strip()
        event.start_datetime = datetime.strptime(request.form["start_datetime"], "%Y-%m-%dT%H:%M")
        event.description = request.form.get("description", "").strip()
        event.ticket_url = request.form.get("ticket_url", "").strip() or None
        event.price_info = request.form.get("price_info", "").strip() or None

        artist_ids = request.form.getlist("artist_ids")
        event.artists = Artist.query.filter(Artist.id.in_(artist_ids)).all() if artist_ids else []

        db.session.commit()
        flash("Updated show.", "success")
        return redirect(url_for("main.calendar"))

    return render_template("events/form.html", event=event, venues=venues, artists=artists)


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


@bp.route("/review")
def review():
    pending = Event.query.filter_by(is_approved=False).order_by(Event.start_datetime.asc()).all()
    return render_template("events/review.html", events=pending)

from datetime import datetime, timedelta

from flask import Blueprint, render_template, request

from app.models import Event, Venue, Artist

bp = Blueprint("main", __name__)


def _base_query(venue_id, artist_id):
    query = Event.query.filter(Event.is_approved.is_(True))
    if venue_id:
        query = query.filter(Event.venue_id == venue_id)
    if artist_id:
        query = query.join(Event.artists).filter(Artist.id == artist_id)
    return query


@bp.route("/")
def calendar():
    venue_id = request.args.get("venue", type=int)
    artist_id = request.args.get("artist", type=int)
    # "week" (the next 7 days) is the default landing view; "list" is the
    # full unbounded upcoming-shows list. The month-grid view was removed
    # -- it was hard to read with more than a couple of shows in a day.
    view = request.args.get("view", "week")
    if view not in ("week", "list"):
        view = "week"

    today = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    week_end = today + timedelta(days=7)

    if view == "week":
        events = (
            _base_query(venue_id, artist_id)
            .filter(Event.start_datetime >= today, Event.start_datetime < week_end)
            .order_by(Event.start_datetime.asc())
            .all()
        )
    else:
        events = (
            _base_query(venue_id, artist_id)
            .filter(Event.start_datetime >= today)
            .order_by(Event.start_datetime.asc())
            .all()
        )

    venues = Venue.query.order_by(Venue.name).all()
    artists = Artist.query.filter_by(is_local=True).order_by(Artist.name).all()

    pending_count = Event.query.filter_by(is_approved=False).count()

    return render_template(
        "calendar.html",
        events=events,
        view=view,
        today=today,
        tomorrow=(today + timedelta(days=1)).date(),
        week_start=today,
        week_end=week_end - timedelta(days=1),
        venues=venues,
        artists=artists,
        selected_venue=venue_id,
        selected_artist=artist_id,
        pending_count=pending_count,
    )

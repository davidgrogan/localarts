import calendar as calendar_module
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


def _parse_month_param(month_param, today):
    """Parse a "YYYY-MM" query param, falling back to the current month
    on anything missing or malformed."""
    if month_param:
        try:
            year_str, month_str = month_param.split("-")
            return int(year_str), int(month_str)
        except (ValueError, AttributeError):
            pass
    return today.year, today.month


def _build_month_grid(year, month, venue_id, artist_id, today):
    """Fetch this month's approved events (respecting the venue/artist
    filters) and lay them out into a Sunday-first week grid for the
    calendar-view template."""
    month_start = datetime(year, month, 1)
    if month == 12:
        next_month_start = datetime(year + 1, 1, 1)
        next_month_param = f"{year + 1}-01"
    else:
        next_month_start = datetime(year, month + 1, 1)
        next_month_param = f"{year}-{month + 1:02d}"
    if month == 1:
        prev_month_param = f"{year - 1}-12"
    else:
        prev_month_param = f"{year}-{month - 1:02d}"

    month_events = (
        _base_query(venue_id, artist_id)
        .filter(Event.start_datetime >= month_start, Event.start_datetime < next_month_start)
        .order_by(Event.start_datetime.asc())
        .all()
    )

    events_by_day = {}
    for event in month_events:
        events_by_day.setdefault(event.start_datetime.day, []).append(event)

    is_current_month = (today.year, today.month) == (year, month)

    return {
        "month_name": month_start.strftime("%B %Y"),
        "weeks": calendar_module.Calendar(firstweekday=6).monthdayscalendar(year, month),
        "events_by_day": events_by_day,
        "prev_month": prev_month_param,
        "next_month": next_month_param,
        "today_day": today.day if is_current_month else None,
    }


@bp.route("/")
def calendar():
    venue_id = request.args.get("venue", type=int)
    artist_id = request.args.get("artist", type=int)
    # "week" (the next 7 days) is the default landing view; "month" is the
    # grid view; "list" is the full unbounded upcoming-shows list.
    view = request.args.get("view", "week")
    if view not in ("week", "month", "list"):
        view = "week"

    today = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    week_end = today + timedelta(days=7)

    events = []
    grid = None
    if view == "month":
        year, month = _parse_month_param(request.args.get("month"), today)
        grid = _build_month_grid(year, month, venue_id, artist_id, today)
    elif view == "week":
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
        grid=grid,
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

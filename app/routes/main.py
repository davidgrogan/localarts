from datetime import datetime, timedelta

from flask import Blueprint, render_template, request

from app.models import Event, Venue, Artist, EventType
from app.recurrence import DisplayItem, group_recurring_events

bp = Blueprint("main", __name__)

# Real users testing the site asked for recurring events (a daily exhibit,
# a weekly open mic) to collapse into one row with a badge instead of
# repeating in full every single day -- see app/recurrence.py. Flip this
# to False to instantly revert to the old flat one-row-per-occurrence
# behavior with no other code changes, in case the grouping doesn't look
# or feel right in practice.
GROUP_RECURRING_EVENTS = True


def _base_query(venue_id, artist_id, selected_type):
    query = Event.query.filter(Event.is_approved.is_(True))
    if venue_id:
        query = query.filter(Event.venue_id == venue_id)
    if artist_id:
        query = query.join(Event.artists).filter(Artist.id == artist_id)
    if selected_type == "untagged":
        query = query.filter(~Event.event_types.any())
    elif selected_type:
        query = query.join(Event.event_types).filter(EventType.id == selected_type)
    return query


@bp.route("/")
def calendar():
    venue_id = request.args.get("venue", type=int)
    artist_id = request.args.get("artist", type=int)
    # The type filter is normally a tag id, but also accepts the special
    # value "untagged" to find events with no tags at all -- see
    # _base_query() above.
    type_param = request.args.get("type", "").strip()
    if type_param == "untagged":
        event_type_id = "untagged"
    elif type_param.isdigit():
        event_type_id = int(type_param)
    else:
        event_type_id = None
    # "week" (the next 7 days) is the default landing view; "list" is the
    # full unbounded upcoming-shows list. The month-grid view was removed
    # -- it was hard to read with more than a couple of shows in a day.
    view = request.args.get("view", "week")
    if view not in ("week", "list"):
        view = "week"

    today = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    week_end = today + timedelta(days=7)

    # Always query the full unbounded future set (not just the week-view's
    # 7-day window) so a recurring series' badge/date-range is accurate --
    # e.g. "Every Tue/Wed/Thu, thru Aug 15" -- even when only its next
    # occurrence or two actually falls within the current 7-day window.
    # The week view still only *displays* items whose next occurrence
    # falls in that window; it just computes the grouping from complete
    # data first.
    all_upcoming = (
        _base_query(venue_id, artist_id, event_type_id)
        .filter(Event.start_datetime >= today)
        .order_by(Event.start_datetime.asc())
        .all()
    )

    if GROUP_RECURRING_EVENTS:
        items = group_recurring_events(all_upcoming)
    else:
        items = [DisplayItem(event=e) for e in all_upcoming]

    if view == "week":
        items = [item for item in items if item.event.start_datetime < week_end]

    venues = Venue.query.order_by(Venue.name).all()
    artists = Artist.query.filter_by(is_local=True).order_by(Artist.name).all()
    event_types = EventType.query.order_by(EventType.name).all()

    return render_template(
        "calendar.html",
        items=items,
        view=view,
        today=today,
        tomorrow=(today + timedelta(days=1)).date(),
        week_start=today,
        week_end=week_end - timedelta(days=1),
        venues=venues,
        artists=artists,
        event_types=event_types,
        selected_venue=venue_id,
        selected_artist=artist_id,
        selected_type=event_type_id,
    )

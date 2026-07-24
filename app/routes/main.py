import random
from datetime import datetime, timedelta

from flask import Blueprint, render_template, request

from app.models import Event, Venue, Artist, EventType
from app.recurrence import DisplayItem, group_recurring_events

bp = Blueprint("main", __name__)

# Real users testing the site asked for recurring events (a daily exhibit,
# a weekly open mic) to collapse into one row with a badge instead of
# repeating in full every single day -- see app/recurrence.py. This first
# shipped as always-on, but that wasn't landing well in practice -- it's
# now an opt-in visitor toggle instead (the "hide_recurring" query param /
# calendar.html checkbox), defaulting to the original flat behavior.
# Setting this to False is still a hard server-side kill switch that
# overrides the toggle entirely, for a one-line rollback if needed.
GROUP_RECURRING_EVENTS = True


def _base_query(venue_id, artist_id, selected_type, only_local_artists=False):
    query = Event.query.filter(Event.is_approved.is_(True))
    if venue_id:
        query = query.filter(Event.venue_id == venue_id)
    if artist_id:
        query = query.join(Event.artists).filter(Artist.id == artist_id)
    if selected_type == "untagged":
        query = query.filter(~Event.event_types.any())
    elif selected_type:
        query = query.join(Event.event_types).filter(EventType.id == selected_type)
    if only_local_artists:
        # .any() (an EXISTS subquery) rather than a join -- a join here
        # would duplicate a show once per local artist it features.
        query = query.filter(Event.artists.any(Artist.is_local.is_(True)))
    return query


def _pick_featured_artist():
    """Pick one random is_local artist that has at least one upcoming
    show, for the homepage spotlight. Random on every homepage load
    (rather than a curated rotation) per David's ask -- keeps this to a
    single query with no scheduling/state to maintain. Returns None if
    there's no eligible artist yet (e.g. a fresh install with no shows
    linked to any local artist)."""
    from sqlalchemy import and_

    candidates = Artist.query.filter(
        Artist.is_local.is_(True),
        Artist.events.any(and_(
            Event.start_datetime >= datetime.utcnow(),
            Event.is_approved.is_(True),
        )),
    ).all()
    return random.choice(candidates) if candidates else None


def _upcoming_events_for(artist):
    """The featured artist's own upcoming, approved shows, soonest first --
    shown directly in the homepage spotlight (rather than making a visitor
    click through to the artist's page to see them) per David's ask.
    Filtered/sorted in Python rather than a query since artist.events is
    already loaded and typically tiny (a handful of shows at most)."""
    if not artist:
        return []
    now = datetime.utcnow()
    upcoming = [e for e in artist.events if e.start_datetime >= now and e.is_approved]
    return sorted(upcoming, key=lambda e: e.start_datetime)


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
    # Defaults to the plain flat list (every occurrence shown) -- the
    # collapsed/badged view is opt-in via this checkbox, not automatic.
    hide_recurring = request.args.get("hide_recurring") == "1"
    # Sitewide "only shows featuring a local artist" toggle -- distinct
    # from the single-artist dropdown (artist_id above, currently unused
    # by the template but left wired up): this shows every local artist's
    # shows at once rather than one artist at a time.
    only_local_artists = request.args.get("only_local_artists") == "1"

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
        _base_query(venue_id, artist_id, event_type_id, only_local_artists)
        .filter(Event.start_datetime >= today)
        .order_by(Event.start_datetime.asc())
        .all()
    )

    if GROUP_RECURRING_EVENTS and hide_recurring:
        items = group_recurring_events(all_upcoming)
    else:
        items = [DisplayItem(event=e) for e in all_upcoming]

    if view == "week":
        items = [item for item in items if item.event.start_datetime < week_end]

    venues = Venue.query.order_by(Venue.name).all()
    artists = Artist.query.filter_by(is_local=True).order_by(Artist.name).all()
    event_types = EventType.query.order_by(EventType.name).all()
    featured_artist = _pick_featured_artist()

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
        hide_recurring=hide_recurring,
        only_local_artists=only_local_artists,
        featured_artist=featured_artist,
        featured_artist_events=_upcoming_events_for(featured_artist),
    )

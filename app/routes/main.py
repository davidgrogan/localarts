from dataclasses import dataclass
from datetime import timedelta

from flask import Blueprint, flash, redirect, render_template, request, url_for

from app.auth import login_required
from app.models import db, Event, Venue, Artist, EventType
from app.recurrence import DisplayItem, group_recurring_events
from app.utils import get_site_setting, local_now

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

# The site pivoted to a music-only focus -- the public calendar now always
# restricts to events carrying this EventType category tag, regardless of
# any filter selection (there's no way to opt back into seeing Comedy/Art/
# Lecture/etc. from the calendar UI at all anymore). The underlying
# Category Tags infrastructure is untouched otherwise -- a venue can still
# scrape/tag non-music events, an admin can still see and manage them via
# the Review queue and each venue's own detail page -- they just never
# reach the public calendar. Matched case-insensitively (this dataset has
# inconsistently-cased tags, e.g. "Music" alongside "karaoke") rather than
# hardcoding a specific EventType id, which could differ per install.
MUSIC_ONLY_CATEGORY_NAME = "music"


def _base_query(venue_id, artist_id, selected_genre, only_local_artists=False):
    query = Event.query.filter(Event.is_approved.is_(True))
    query = query.join(Event.event_types).filter(
        db.func.lower(EventType.name) == MUSIC_ONLY_CATEGORY_NAME
    )
    if venue_id:
        query = query.filter(Event.venue_id == venue_id)
    if artist_id:
        query = query.filter(Event.artists.any(Artist.id == artist_id))
    if selected_genre:
        # Event.genre is a freeform, comma-separated string straight from
        # whatever a venue's own feed happens to publish (see models.py) --
        # not a real tag table like EventType/GenreTag. A plain
        # case-insensitive substring match is the simplest thing that
        # works against that: good enough at this site's scale (confirmed
        # against real scraped data), would need a proper normalized
        # genre-tag table if the venue list grew a lot and this started
        # producing noisy false matches.
        query = query.filter(Event.genre.ilike(f"%{selected_genre}%"))
    if only_local_artists:
        # .any() (an EXISTS subquery) rather than a join -- a join here
        # would duplicate a show once per local artist it features.
        query = query.filter(Event.artists.any(Artist.is_local.is_(True)))
    return query


def _distinct_genres():
    """Every individual genre word/phrase currently in play across
    approved, Music-tagged events' freeform Event.genre field -- powers
    the Genre filter dropdown (replacing the old Event Type filter, which
    stopped being useful once the calendar hard-restricts to Music
    anyway). A venue's feed often lists a show's genre(s) as one
    comma-separated string (e.g. "Alternative, Americana,
    Singer-Songwriter"); this splits those apart and de-duplicates
    case-insensitively (keeping whichever casing was seen first) so the
    dropdown offers one option per genre rather than one per unique
    combination of genres."""
    seen = {}
    rows = (
        _base_query(None, None, None)
        .filter(Event.genre.isnot(None), Event.genre != "")
        .with_entities(Event.genre)
        .distinct()
    )
    for (raw,) in rows:
        for piece in raw.split(","):
            piece = piece.strip()
            if not piece:
                continue
            key = piece.lower()
            if key not in seen:
                seen[key] = piece
    return sorted(seen.values(), key=str.lower)


@dataclass
class WeeklySpot:
    """One card in the "Local Artists Playing This Week!" gallery: a single
    local artist's appearance at a single show. Deliberately one entry per
    (artist, show) pair rather than one per artist or one per show -- an
    artist playing twice this week gets two cards (one per date/venue), and
    a bill with more than one local act (not unusual -- e.g. a show billing
    three local bands together) gets one card per artist rather than
    picking just one of them to represent the whole bill."""
    artist: Artist
    event: Event


def _local_artists_playing_this_week(week_start, week_end):
    """Every local artist's appearance at an approved, Music-tagged show
    landing in the [week_start, week_end) window, soonest first -- powers
    the homepage's "Local Artists Playing This Week!" gallery. Replaces the
    old single-random-artist spotlight with everything actually happening
    this week, since that's more useful to a visitor deciding what to go
    see than one random pick. Reuses whatever week_start/week_end the
    calendar's own "week" view is built from (see calendar() below), so
    "this week" means the same 7-day window everywhere on the page --
    that's already computed with local_now(), not datetime.utcnow() (see
    app/utils.py's SITE_TIMEZONE docstring), so today's shows correctly
    still count as upcoming."""
    events = (
        _base_query(None, None, None, only_local_artists=True)
        .filter(Event.start_datetime >= week_start, Event.start_datetime < week_end)
        .order_by(Event.start_datetime.asc())
        .all()
    )
    spots = []
    for event in events:
        for artist in event.artists:
            if artist.is_local:
                spots.append(WeeklySpot(artist=artist, event=event))
    return spots


@bp.route("/")
def calendar():
    venue_id = request.args.get("venue", type=int)
    artist_id = request.args.get("artist", type=int)
    genre_param = request.args.get("genre", "").strip() or None
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

    # local_now(), not datetime.utcnow() -- see app/utils.py's SITE_TIMEZONE
    # docstring. Using true UTC here made "today" roll over hours before
    # local midnight actually arrived.
    today = local_now().replace(hour=0, minute=0, second=0, microsecond=0)
    week_end = today + timedelta(days=7)

    # Always query the full unbounded future set (not just the week-view's
    # 7-day window) so a recurring series' badge/date-range is accurate --
    # e.g. "Every Tue/Wed/Thu, thru Aug 15" -- even when only its next
    # occurrence or two actually falls within the current 7-day window.
    # The week view still only *displays* items whose next occurrence
    # falls in that window; it just computes the grouping from complete
    # data first.
    all_upcoming = (
        _base_query(venue_id, artist_id, genre_param, only_local_artists)
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
    genres = _distinct_genres()
    artists_this_week = _local_artists_playing_this_week(today, week_end)
    site_setting = get_site_setting()

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
        genres=genres,
        selected_venue=venue_id,
        selected_artist=artist_id,
        selected_genre=genre_param,
        hide_recurring=hide_recurring,
        only_local_artists=only_local_artists,
        artists_this_week=artists_this_week,
        about_html=site_setting.about_html,
    )


@bp.route("/about/edit", methods=["GET", "POST"])
@login_required
def edit_about():
    """Admin-only editor for the "About this site" block on the calendar
    page. Deliberately a single big HTML textarea (not a rich-text/WYSIWYG
    editor -- no such dependency exists in this project) since David asked
    for it to "allow HTML markup" directly, same trust model as an
    artist's embed_code field. See models.py's SiteSetting docstring for
    why this is safe (only an authenticated admin can ever reach this
    route)."""
    setting = get_site_setting()
    if request.method == "POST":
        setting.about_html = request.form.get("about_html", "")
        db.session.commit()
        flash("Updated “About this site.”", "success")
        return redirect(url_for("main.calendar"))
    return render_template("edit_about.html", setting=setting)

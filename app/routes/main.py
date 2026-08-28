import calendar as calendar_module
from dataclasses import dataclass
from datetime import datetime, timedelta

from flask import Blueprint, Response, abort, flash, redirect, render_template, request, session, url_for
from sqlalchemy import and_

from app.auth import login_required
from app.models import db, Event, Venue, Artist, EventType
from app.recurrence import DisplayItem, group_recurring_events
from app.utils import (
    get_site_setting,
    local_now,
    VENUE_CAUTION_NOTE,
    artist_sort_key,
    build_event_ics,
    build_event_jsonld,
    slugify,
)

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

# The public calendar's category filter is a live, admin-managed set,
# not a fixed enum: any EventType with is_public_category=True (see that
# column's docstring in models.py) shows up as a toggleable pill on the
# filter bar, added/removed from the "Manage categories" admin page
# (app/routes/events.py's manage_categories()) with no code change
# needed. Category tags an admin hasn't promoted that way (e.g. the
# stray "karaoke" tag) still exist and can still be applied to a show
# from the Add/Edit Show form -- they just never appear on the public
# filter bar or affect what visitors see.
#
# DEFAULT_PUBLIC_CATEGORY_NAME is only about what a *fresh, never-touched-
# the-filter-bar* visit shows: this is a music venue site first, so
# landing on a plain "/" with no filter interaction at all still shows
# just Music, matching this site's original (and only, until now)
# behavior. Matched case-insensitively (this dataset has inconsistently
# cased tags) rather than hardcoding a specific EventType id, which could
# differ per install.
DEFAULT_PUBLIC_CATEGORY_NAME = "music"


def _public_category_choices():
    """Every EventType currently flagged as a public filter category AND
    carrying at least one still-upcoming, approved event -- powers the
    calendar's row of toggle pills, and is also the *only* set of ids
    _resolve_selected_category_ids() below will ever honor from the query
    string. That second part matters: without it, hand-editing the URL to
    add an arbitrary EventType id would leak an internal-only tag's
    events onto the public calendar.

    The "has a future event" half of this filter is David's ask -- a
    public category with nothing coming up (its last show already
    happened, or it was just created and hasn't been used on anything
    yet) used to still render as a pill a visitor could check and get an
    empty calendar back for their trouble. now() here is local_now(), not
    datetime.utcnow() (see app/utils.py's SITE_TIMEZONE docstring), and
    is_approved=True matches exactly what the calendar itself ever shows
    -- a pill for a category whose only upcoming events are still sitting
    in the Review queue would be just as much of a dead end.

    Recomputed on every request rather than cached -- this is a low-traffic
    site with a handful of categories, so the extra EXISTS subquery per
    pageview isn't worth the complexity of invalidating a cache every time
    an event's date, approval, or tags change."""
    now = local_now()
    return (
        EventType.query.filter_by(is_public_category=True)
        .filter(
            EventType.events.any(
                and_(Event.is_approved.is_(True), Event.start_datetime >= now)
            )
        )
        .order_by(EventType.name)
        .all()
    )


def _resolve_selected_category_ids(args, public_choices, venue_selected):
    """Figures out which public categories are actually selected for this
    request.

    request.args.getlist("category") alone can't tell "the filter form
    was submitted with every pill unchecked" apart from "this is a fresh
    visit that never touched the filter bar at all" -- both produce a
    URL with zero "category" params. Those two cases need different
    answers (the first should show nothing, honoring exactly what the
    visitor's checkboxes said; the second should fall back to a default,
    preserving today's landing experience), so the filter form also
    submits a "categories_submitted" hidden field on every submission
    (same idea as the existing "view-field" hidden input) purely to
    disambiguate the two.

    The "fresh, never touched the filter bar" default itself branches on
    venue_selected: picking a venue off the dropdown (see calendar.html --
    its onchange handler disables the categories_submitted hidden field
    before submitting, specifically so this counts as "fresh" even though
    a request just fired) defaults to *every* public category rather than
    just Music, since a visitor asking "what's on at this venue" almost
    certainly wants everything approved there, not just its music-tagged
    shows -- David's ask, after noticing picking a mixed-use venue like
    Smith College or Quonk silently hid its non-music events. A plain
    site-wide fresh visit (no venue picked) keeps defaulting to Music,
    unchanged.

    Any requested id that isn't currently a public category (stale
    bookmark from before it was hidden, or a hand-edited URL) is silently
    dropped rather than erroring -- same "don't blow up on a weird query
    string" posture as _parse_month_param() above.
    """
    valid_ids = {c.id for c in public_choices}
    if "categories_submitted" not in args:
        if venue_selected:
            return [c.id for c in public_choices]
        default = next(
            (c for c in public_choices if c.name.lower() == DEFAULT_PUBLIC_CATEGORY_NAME), None
        )
        return [default.id] if default else []
    requested = args.getlist("category", type=int)
    return [cid for cid in requested if cid in valid_ids]


def _base_query(venue_id, artist_id, selected_genre, category_ids, only_local_artists=False, venue_ids=None):
    query = Event.query.filter(Event.is_approved.is_(True))
    if not category_ids:
        # No public category selected at all (an explicit "everything
        # unchecked," not a fresh visit -- see
        # _resolve_selected_category_ids()) -- short-circuit to an empty
        # result rather than a query with no category filter at all,
        # which would show *every* category including internal-only
        # ones no visitor ever opted into.
        return query.filter(Event.id.in_([]))
    # .any() (an EXISTS subquery) rather than a join -- a join here would
    # duplicate a show once per selected category it carries (e.g. a show
    # tagged both Music and Comedy, with both selected), same reasoning
    # as the only_local_artists .any() below.
    query = query.filter(Event.event_types.any(EventType.id.in_(category_ids)))
    if venue_id:
        query = query.filter(Event.venue_id == venue_id)
    # venue_ids -- a *list* of venues, distinct from the single venue_id
    # above (the calendar page's own single-select dropdown). Added for
    # the RSS feed builder (build_feed_route() below), which lets a
    # visitor pick more than one venue at once -- kept as its own
    # parameter rather than teaching venue_id to accept either an int or
    # a list, so every existing venue_id caller (the calendar view, the
    # month grid, the weekly local-artist spotlight) is untouched. The
    # two are independent filters that would AND together if a caller
    # somehow passed both, but no current caller does.
    if venue_ids:
        query = query.filter(Event.venue_id.in_(venue_ids))
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


def _distinct_genres(category_ids):
    """Every individual genre word/phrase currently in play across
    approved events in the currently-selected categories' freeform
    Event.genre field -- powers the Genre filter dropdown. A venue's feed
    often lists a show's genre(s) as one comma-separated string (e.g.
    "Alternative, Americana, Singer-Songwriter"); this splits those apart
    and de-duplicates case-insensitively (keeping whichever casing was
    seen first) so the dropdown offers one option per genre rather than
    one per unique combination of genres."""
    seen = {}
    rows = (
        _base_query(None, None, None, category_ids)
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


def _local_artists_playing_this_week(week_start, week_end, category_ids):
    """Every local artist's appearance at an approved show -- in one of the
    currently-selected categories -- landing in the [week_start, week_end)
    window, soonest first -- powers the homepage's "Local Artists Playing
    This Week!" gallery. Replaces the old single-random-artist spotlight
    with everything actually happening this week, since that's more useful
    to a visitor deciding what to go see than one random pick. Reuses
    whatever week_start/week_end the calendar's own "week" view is built
    from (see calendar() below), so "this week" means the same 7-day
    window everywhere on the page -- that's already computed with
    local_now(), not datetime.utcnow() (see app/utils.py's SITE_TIMEZONE
    docstring), so today's shows correctly still count as upcoming."""
    events = (
        _base_query(None, None, None, category_ids, only_local_artists=True)
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


def _parse_month_param(month_param, today):
    """Parse a "YYYY-MM" query param (the month grid's own prev/next links,
    see _build_month_grid()'s month_name/prev_month/next_month below),
    falling back to the current month on anything missing or malformed --
    e.g. a first-ever visit to ?view=month with no month= yet, or someone
    hand-editing the URL into something nonsensical."""
    if month_param:
        try:
            year_str, month_str = month_param.split("-")
            return int(year_str), int(month_str)
        except (ValueError, AttributeError):
            pass
    return today.year, today.month


def _build_month_grid(year, month, venue_id, artist_id, selected_genre, category_ids, only_local_artists, today):
    """Fetch this month's approved events in the currently-selected
    categories (respecting whichever venue/genre/local-artist filters are
    active -- same _base_query() every other view uses) and lay them out
    into a Sunday-first week grid for calendar.html's month table.

    This exact feature existed once before and was deliberately removed
    (see git history: "Remove Month View ... it was hard to read with more
    than a couple shows on one day") -- reintroduced here at David's
    request, but each day cell now caps how many events render inline
    (see MAX_EVENTS_PER_DAY_CELL below) with a "+N more" overflow count
    rather than however many happen to have been scraped that day, so a
    day with a dozen shows across every venue in town doesn't blow out the
    whole grid's row height the way the original version did.
    """
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
        _base_query(venue_id, artist_id, selected_genre, category_ids, only_local_artists)
        .filter(Event.start_datetime >= month_start, Event.start_datetime < next_month_start)
        .order_by(Event.start_datetime.asc())
        .all()
    )

    MAX_EVENTS_PER_DAY_CELL = 4
    events_by_day = {}
    overflow_by_day = {}
    for event in month_events:
        day = event.start_datetime.day
        shown = events_by_day.setdefault(day, [])
        if len(shown) < MAX_EVENTS_PER_DAY_CELL:
            shown.append(event)
        else:
            overflow_by_day[day] = overflow_by_day.get(day, 0) + 1

    is_current_month = (today.year, today.month) == (year, month)

    return {
        "month_name": month_start.strftime("%B %Y"),
        # firstweekday=6 -> Sunday-first weeks, matching the day-of-week
        # header row below (Sun/Mon/.../Sat). Days outside this month pad
        # each edge week as 0, which the template renders as a blank cell.
        "weeks": calendar_module.Calendar(firstweekday=6).monthdayscalendar(year, month),
        "events_by_day": events_by_day,
        "overflow_by_day": overflow_by_day,
        "prev_month": prev_month_param,
        "next_month": next_month_param,
        "today_day": today.day if is_current_month else None,
    }


@bp.route("/")
def calendar():
    venue_id = request.args.get("venue", type=int)
    artist_id = request.args.get("artist", type=int)
    genre_param = request.args.get("genre", "").strip() or None
    # "week" (the next 7 days) is the default landing view; "list" is the
    # full unbounded upcoming-shows list; "month" is the grid view (see
    # _build_month_grid() above -- brought back at David's request after
    # an earlier removal, now with a per-day cap so a busy day doesn't
    # blow out the whole grid).
    view = request.args.get("view", "week")
    if view not in ("week", "list", "month"):
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

    public_categories = _public_category_choices()
    selected_category_ids = _resolve_selected_category_ids(request.args, public_categories, bool(venue_id))
    # Whether *this request* explicitly submitted the category filter --
    # not just whether the resolved selection happens to differ from the
    # default -- so "Clear filters" shows up as soon as the filter bar's
    # been touched at all, same trigger _resolve_selected_category_ids()
    # itself uses.
    categories_customized = "categories_submitted" in request.args

    # Always query the full unbounded future set (not just the week-view's
    # 7-day window) so a recurring series' badge/date-range is accurate --
    # e.g. "Every Tue/Wed/Thu, thru Aug 15" -- even when only its next
    # occurrence or two actually falls within the current 7-day window.
    # The week view still only *displays* items whose next occurrence
    # falls in that window; it just computes the grouping from complete
    # data first.
    all_upcoming = (
        _base_query(venue_id, artist_id, genre_param, selected_category_ids, only_local_artists)
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
        # A category pill that just got checked (or unchecked down to a
        # combination) can easily have nothing landing in the next 7 days
        # even though real shows exist further out -- e.g. the only
        # upcoming Comedy night is in three weeks. Rather than showing an
        # empty "Next 7 Days" view and making the visitor notice and click
        # "List All" themselves, redirect straight there. Scoped to
        # "the category filter was actually touched this request"
        # (categories_customized) so a plain, filter-untouched empty week
        # -- which just means it's a genuinely quiet week -- still shows
        # the normal empty state instead of silently jumping to List All
        # every time.
        if categories_customized and not items:
            return redirect(url_for(
                "main.calendar",
                view="list",
                venue=venue_id,
                genre=genre_param,
                hide_recurring=("1" if hide_recurring else None),
                only_local_artists=("1" if only_local_artists else None),
                category=selected_category_ids,
                categories_submitted=1,
            ))

    grid = None
    if view == "month":
        year, month = _parse_month_param(request.args.get("month"), today)
        grid = _build_month_grid(
            year, month, venue_id, artist_id, genre_param, selected_category_ids, only_local_artists, today
        )

    venues = Venue.query.order_by(Venue.name).all()
    # artist_sort_key() (not a plain ORDER BY) so a "The ..." band lines up
    # alphabetically here the same way it does on the /artists index --
    # see that key's own docstring in app/utils.py.
    artists = sorted(
        Artist.query.filter_by(is_local=True).all(), key=lambda a: artist_sort_key(a.name)
    )
    genres = _distinct_genres(selected_category_ids)
    artists_this_week = _local_artists_playing_this_week(today, week_end, selected_category_ids)

    return render_template(
        "calendar.html",
        items=items,
        grid=grid,
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
        venue_caution_note=VENUE_CAUTION_NOTE,
        public_categories=public_categories,
        selected_category_ids=selected_category_ids,
        categories_customized=categories_customized,
    )


@bp.route("/show/<int:event_id>")
def event_detail(event_id):
    """A single show's own page -- the full-size image and full
    description, neither of which fit on the calendar's card-per-show
    list (there, an image is a small cropped thumbnail and the
    description is hidden in a hover tooltip). Deliberately at
    `/show/<id>` rather than `/events/<id>` -- the `events` blueprint
    (app/routes/events.py) is an admin-only surface end to end (its own
    module docstring says so explicitly: "Visitors only ever see events
    rendered on the public calendar, never through this blueprint"), so a
    public route belongs on `main`'s own URL space instead of blurring
    that line, even though Flask itself would technically allow both to
    coexist without colliding.

    A not-yet-approved event 404s for anyone who isn't logged in as
    admin -- same "don't leak unvetted content via a guessable URL"
    reasoning as everywhere else an is_approved check gates public
    visibility. An admin can still open this page to preview one before
    approving it (e.g. from the Review queue).
    """
    event = Event.query.get_or_404(event_id)
    if not event.is_approved and not session.get("is_admin"):
        abort(404)
    return render_template(
        "events/detail.html",
        event=event,
        venue_caution_note=VENUE_CAUTION_NOTE,
        event_jsonld=build_event_jsonld(event),
    )


@bp.route("/show/<int:event_id>.ics")
def event_ics(event_id):
    """The event detail page's "Add to calendar" button -- a downloadable
    single-event .ics file, built fresh on every request by
    build_event_ics() (see that function's own docstring in app/utils.py
    for why it's computed per-request rather than cached/stored). Same
    visibility rule as event_detail() just above: a not-yet-approved
    event's calendar file 404s for anyone who isn't logged in as admin,
    rather than letting a guessable URL leak an unvetted show.
    """
    event = Event.query.get_or_404(event_id)
    if not event.is_approved and not session.get("is_admin"):
        abort(404)
    ics_bytes = build_event_ics(event)
    return Response(
        ics_bytes,
        mimetype="text/calendar",
        headers={"Content-Disposition": f'attachment; filename="{slugify(event.title)}.ics"'},
    )


# How far forward an RSS feed reaches, regardless of filters -- an
# unbounded feed would just grow forever as more venues/scrapes get added,
# with no real benefit to a subscriber (nobody needs a heads-up about a
# show 8 months out yet). 90 days was David's call.
FEED_MAX_DAYS_AHEAD = 90


def _resolve_feed_category_ids(args, public_choices):
    """Which public categories a feed URL wants, from repeated
    `?category=<id>` params -- same query-string shape as the calendar's
    own filter bar (see _resolve_selected_category_ids() above), but with
    different empty-selection semantics on purpose: the calendar
    distinguishes "fresh, filter bar never touched" from "explicitly
    submitted with everything unchecked" using a categories_submitted
    marker, because a fresh calendar visit should default to just Music,
    not silently show everything. A feed URL has no such "fresh visit" to
    default -- it's either built with specific categories checked (feed_builder()
    below), in which case honor exactly those, or it's the plain "every
    upcoming event" global feed with no category= at all, which should
    mean *every* public category, not zero. Same invalid-id handling as
    _resolve_selected_category_ids(): a stale/hand-edited id that isn't
    currently a public category is silently dropped."""
    valid_ids = [c.id for c in public_choices]
    requested = args.getlist("category", type=int)
    if not requested:
        return valid_ids
    valid_id_set = set(valid_ids)
    return [cid for cid in requested if cid in valid_id_set]


def _feed_title(selected_venues, selected_categories):
    """The RSS <title>/<h1> for a given filter combination -- e.g.
    "Paradise City Music — Iron Horse Music Hall, Music" for a feed
    scoped to one venue and one category, or a plain site-wide title for
    the unfiltered global feed."""
    parts = []
    if selected_venues:
        parts.append(", ".join(v.name for v in selected_venues))
    if selected_categories:
        parts.append(", ".join(c.name for c in selected_categories))
    if not parts:
        return "Paradise City Music — All Upcoming Events"
    return "Paradise City Music — " + " · ".join(parts)


@bp.route("/feed")
def feed_builder():
    """A small helper page: check off whichever venues and/or public
    categories you want, and it hands you the resulting /feed.rss URL --
    rather than a from-scratch filter UI, this deliberately mirrors the
    calendar's own venue list and public-category pills (see
    _public_category_choices() above), since those are the exact same
    two axes the feed itself filters on. The "everything, no filters"
    global feed is front and center above the picker, per David's ask to
    lean into that as the headline option rather than making a visitor
    build even the simplest possible feed by hand.

    The actual URL-building happens client-side (see feed_builder.html's
    own <script>) purely so the result updates live as checkboxes are
    toggled, with no page reload/round-trip needed -- there's no
    server-side state here at all, just the same venue/category lists
    the calendar page already renders.
    """
    venues = Venue.query.order_by(Venue.name).all()
    public_categories = _public_category_choices()
    return render_template(
        "feed_builder.html",
        venues=venues,
        public_categories=public_categories,
        feed_max_days_ahead=FEED_MAX_DAYS_AHEAD,
    )


@bp.route("/feed.rss")
def feed_rss():
    """RSS 2.0 feed of upcoming approved events, filterable by one or
    more venues and/or one or more public categories via repeated
    `?venue=<id>` / `?category=<id>` query params -- built by
    feed_builder() above, but just as valid hand-constructed or bookmarked
    directly. No params at all (the plain /feed.rss link) is the global
    "everything" feed.

    <pubDate> is each item's own Event.created_at (when it was first
    added to this calendar), not its start_datetime. That's the
    deliberate, RSS-spec-correct choice, not an oversight: <pubDate> is
    "when this item was published" per the RSS spec, and most feed
    readers sort/flag "new" items by it -- a feed that used
    start_datetime instead would have every item's <pubDate> sitting in
    the *future* (a show's start date is always later than when it's
    added), which readers generally don't handle well for "here's what's
    new" notifications; some hide a future-dated item until that date
    arrives, defeating the whole point of a heads-up feed. The actual
    show date is instead the *first* thing in every item's own
    description, and the whole feed is sorted by start_datetime (not
    pubDate) so it still reads in calendar order.

    FEED_MAX_DAYS_AHEAD caps how far forward the feed reaches (David's
    call: 90 days), same is_approved-only visibility as the calendar
    itself, and the same shared _base_query()/_resolve_feed_category_ids()
    plumbing every other filtered view on this site already uses.
    """
    public_categories = _public_category_choices()
    category_ids = _resolve_feed_category_ids(request.args, public_categories)
    venue_ids = request.args.getlist("venue", type=int)

    now = local_now()
    cutoff = now + timedelta(days=FEED_MAX_DAYS_AHEAD)
    events = (
        _base_query(None, None, None, category_ids, venue_ids=venue_ids)
        .filter(Event.start_datetime >= now, Event.start_datetime < cutoff)
        .order_by(Event.start_datetime.asc())
        .all()
    )

    selected_venues = (
        Venue.query.filter(Venue.id.in_(venue_ids)).order_by(Venue.name).all() if venue_ids else []
    )
    valid_category_id_set = {c.id for c in public_categories}
    selected_categories = (
        [c for c in public_categories if c.id in category_ids]
        if set(category_ids) != valid_category_id_set
        else []
    )

    body = render_template(
        "feed.xml",
        events=events,
        feed_title=_feed_title(selected_venues, selected_categories),
        feed_self_url=request.url,
        # datetime.utcnow(), NOT local_now() -- <lastBuildDate> goes
        # through the same `| rfc822` filter as every item's <pubDate>
        # (rfc822_utc() in app/utils.py), which assumes whatever naive
        # datetime it's given is already genuine UTC and just labels it
        # "+0000". local_now() is naive *Eastern* wall-clock time (see
        # SITE_TIMEZONE's docstring) -- feeding that in directly would
        # mislabel it as UTC and put <lastBuildDate> 4-5 hours off from
        # the real moment, even though every event's own <pubDate>
        # (built from the genuinely-UTC Event.created_at) would still be
        # correct.
        build_time_utc=datetime.utcnow(),
    )
    return Response(body, mimetype="application/rss+xml")


# Hand-curated from a live scan of every local artist's Bandcamp page (via
# the "bandcamp-new-releases" skill, run interactively through Claude in
# Chrome -- Bandcamp blocks scripted/headless scraping, see
# app/bandcamp_import.py's own docstring for the earlier feature that hit
# the same wall) for releases from July-August 2026. NOT re-scanned live on
# every page load -- this is a snapshot from that one scan, hardcoded here
# the same deliberate way seed.py's placeholder_embed comment describes:
# a real embed snippet swapped in by hand, not fetched at request time.
# Re-run the skill and update this list by hand for a future edition.
#
# IMPORTANT, learned the hard way (David caught "Christmas Pig Song"
# showing a July 2026 date when its own track page says December 1,
# 2001): a track's *own* individual Bandcamp page is the only reliable
# source for its release date. The initial scan instead trusted the
# *album* page's "released <date>" line for whichever track link it found
# first on that album -- fine for a genuinely new album, but wrong for any
# album that's really a compilation/anthology/live-set bundling older
# songs, which keep their own original release date on their own page even
# once repackaged into something newly uploaded. Three of the original
# twelve were wrong for exactly this reason (Angry Johnny And The
# Killbillies' "Christmas Pig Song" -- 2001, bundled into a new "Welcome To
# The Doomsday" collection; Dome Lettuce's "Stuck on Earth" -- Dec 2025,
# before the cutoff; Fred Cracklin's "Head Meet Concrete" -- 2023, off a
# "Live" album) and have been removed. The bandcamp-new-releases skill
# itself has been corrected to verify every candidate against its own
# track page before treating it as qualifying.
NEW_RELEASES = [
    {"artist": "Cloudbelly", "title": "Oh, Antarctica!", "released": "August 21, 2026", "track_id": "4050548037", "bandcamp_url": "https://cloudbelly.bandcamp.com/track/oh-antarctica"},
    {"artist": "Gentle Hen", "title": "Comfort Zone", "released": "August 21, 2026", "track_id": "235197250", "bandcamp_url": "https://gentlehen.bandcamp.com/track/comfort-zone-2"},
    {"artist": "Alyssa Kai", "title": "chronic illness power fantasy", "released": "August 14, 2026", "track_id": "2456862956", "bandcamp_url": "https://lyskoi.bandcamp.com/track/chronic-illness-power-fantasy"},
    {"artist": "The Suitcase Junket", "title": "Put Your Phone Down", "released": "August 11, 2026", "track_id": "1959259213", "bandcamp_url": "https://thesuitcasejunket.bandcamp.com/track/put-your-phone-down"},
    {"artist": "Brokestring & the Empty Promises", "title": "Good News", "released": "August 7, 2026", "track_id": "3416702175", "bandcamp_url": "https://brokestring.bandcamp.com/track/good-news"},
    {"artist": "NEONACH", "title": "Eye in the Sky", "released": "August 4, 2026", "track_id": "1216733992", "bandcamp_url": "https://neonach.bandcamp.com/track/eye-in-the-sky"},
    {"artist": "Tommy Twilite", "title": "House of Cards", "released": "July 27, 2026", "track_id": "1554002702", "bandcamp_url": "https://tommytwilite.bandcamp.com/track/house-of-cards"},
    {"artist": "The Colony Motel", "title": "Almah", "released": "July 19, 2026", "track_id": "3799119158", "bandcamp_url": "https://thecolonymotel.bandcamp.com/track/almah"},
    {"artist": "mibble", "title": "Morning Dew Is Almost Over", "released": "July 19, 2026", "track_id": "3602496743", "bandcamp_url": "https://mibble.bandcamp.com/track/morning-dew-is-almost-over"},
    {"artist": "Wishbone Zoe", "title": "Psyche's Romp", "released": "July 16, 2026", "track_id": "3070579675", "bandcamp_url": "https://wishbonezoe.bandcamp.com/track/psyches-romp"},
]


@bp.route("/new-releases")
def new_releases():
    """A hand-curated "what's new" page -- every local artist's most recent
    Bandcamp release from a given window (right now: July-August 2026, see
    NEW_RELEASES above), each with its own embedded player. Public, no
    login needed, same as every other content page on this site (about,
    venues, artists).

    Looks up each entry's local Artist row by name so the artist's own name
    can link back to their profile page here (Artist.slug isn't stored on
    NEW_RELEASES, so this is a name match, not an id/slug lookup -- fine at
    this scale, and it degrades gracefully to plain unlinked text below if
    a name doesn't match, e.g. after a rename).
    """
    releases = []
    for entry in NEW_RELEASES:
        artist_row = Artist.query.filter_by(name=entry["artist"]).first()
        releases.append({**entry, "artist_row": artist_row})
    return render_template(
        "new_releases.html",
        releases=releases,
        subtitle="July/August 2026: New tracks from local artists.",
    )


@bp.route("/about")
def about_page():
    """The full "About this site" content -- its own page, linked from the
    main nav, rather than shown inline (collapsed by default) on the
    calendar like it used to be. Public: no login needed to *read* it,
    same as everything else on the public side of the site; editing it
    still goes through edit_about() below, gated by @login_required."""
    site_setting = get_site_setting()
    return render_template("about.html", about_html=site_setting.about_html)


@bp.route("/about/edit", methods=["GET", "POST"])
@login_required
def edit_about():
    """Admin-only editor for the "About this site" page's (main.about_page())
    content. Deliberately a single big HTML textarea (not a rich-text/WYSIWYG
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
        return redirect(url_for("main.about_page"))
    return render_template("edit_about.html", setting=setting)

"""Detect recurring event series for display purposes only.

This is deliberately a pure, display-time grouping over already-scraped
Event rows -- no schema changes, no scraper changes. Real users testing
the site flagged the same pain point two different ways: a daily art
exhibit (Holley Flagg Exhibit) showing up as its own row every single
day, and a weekly open mic (Tue/Wed/Thu at Luthier's Co-Op) doing the
same thing weekly. Both are really "one thing that keeps happening,"
not N distinct events, and repeating the full card N times is exactly
the noise they complained about.

Approach: group same-venue events by a normalized title, then split
each group into "runs" -- consecutive occurrences close enough together
in time (see MAX_GAP_DAYS) to plausibly be the same recurring booking,
as opposed to, say, an annual event that happens to reuse the same
title a year apart. A run with enough occurrences (MIN_OCCURRENCES) gets
collapsed into a single display item with an inferred weekday pattern
badge ("Daily", "Weekdays", "Every Tue/Wed/Thu") and a date range;
anything short of that threshold, or that doesn't fit a clean pattern,
either stays as individual events (too few occurrences) or gets a
generic "recurring, no specific pattern" badge (occurs on scattered
days, but still frequently enough to be worth collapsing).

Deliberately NOT using an iCal RRULE or similar structured recurrence
rule as the source of truth: only the ical-sourced venues (e.g.
Luthier's Co-Op) could ever carry one, and even then icalendar already
expands recurring VEVENTs into individual occurrences before this code
ever sees them (see app/scrapers/ical_feed.py) -- Elfsight/html-sourced
venues never have structured recurrence data at all. Reverse-engineering
the pattern from the actual occurrences we already have is the only
approach that works uniformly across every source type.

This module intentionally has zero Flask/SQLAlchemy imports -- it only
ever touches plain attributes (`venue_id`, `title`, `start_datetime`) on
whatever Event-like objects it's given, so it's fully testable with
plain fixtures and easy to rip out later if the grouping doesn't look
or feel right in practice (see group_recurring_events's docstring).
"""
from dataclasses import dataclass, field
from datetime import timedelta

# Below this many occurrences, don't call it "recurring" at all -- most
# likely just a coincidental same-title rebooking (e.g. a touring artist
# who happens to play the same room twice a year), not worth a badge or
# collapsing away.
MIN_OCCURRENCES = 3

# Occurrences at the same venue with the same title but further apart
# than this are treated as unrelated bookings, not the same series --
# covers daily and weekly (up to Tue/Wed/Thu-style, worst gap 5 days)
# patterns comfortably without accidentally merging two genuinely
# separate bookings that happen to share a title months apart. Doesn't
# catch biweekly/monthly recurring patterns -- not needed for any real
# venue seen so far, and erring toward *not* merging is the safer
# default (a missed grouping is just a bit more noise; a wrong grouping
# actively misleads about when something's actually happening).
MAX_GAP_DAYS = 10

_WEEKDAY_ABBR = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


@dataclass
class DisplayItem:
    """One row to render on the calendar/list page -- either a single
    ungrouped Event, or a collapsed recurring series. `event` is always
    the representative occurrence to actually display (the earliest one
    in the series that's still in the queried/filtered set, i.e. the
    next upcoming occurrence) so existing template fields (title, venue,
    ticket_url, image, etc.) keep working unchanged for both cases."""

    event: object
    is_series: bool = False
    badge: str = None
    range_start: object = None  # date
    range_end: object = None  # date
    count: int = 1
    occurrences: list = field(default_factory=list)


def _normalized_title(event):
    return event.title.strip().lower()


def _infer_pattern(dates):
    """dates: a sorted list of unique `date` objects. Returns a human
    label ("Daily", "Weekdays", "Every Tue/Wed/Thu") if the occurrences
    exactly match every instance of some fixed set of weekdays within
    their own span, or None if the pattern's too irregular to name (the
    series is still collapsed with a date range -- just without a
    specific "every X" claim that wouldn't actually be true)."""
    if not dates:
        return None
    start, end = dates[0], dates[-1]
    span_days = (end - start).days + 1
    all_days_in_span = [start + timedelta(days=i) for i in range(span_days)]
    weekdays_present = {d.weekday() for d in dates}
    expected = {d for d in all_days_in_span if d.weekday() in weekdays_present}
    if expected != set(dates):
        return None

    if weekdays_present == set(range(7)):
        return "Daily"
    if weekdays_present == {0, 1, 2, 3, 4}:
        return "Weekdays"
    ordered = [w for w in range(7) if w in weekdays_present]
    return "Every " + "/".join(_WEEKDAY_ABBR[w] for w in ordered)


def group_recurring_events(events):
    """events must already be sorted ascending by start_datetime (the
    existing calendar queries already do this). Returns a list of
    DisplayItem, sorted the same way, ready to hand to the template in
    place of the flat event list.

    To roll this back: skip calling this function and pass the flat
    `events` list straight to the template wrapped in single-event
    DisplayItems (or revert the one commit that wired this in) -- this
    module and its call site are the only things that changed."""
    groups = {}
    for e in events:
        key = (e.venue_id, _normalized_title(e))
        groups.setdefault(key, []).append(e)

    display_items = []
    for occurrences in groups.values():
        # occurrences is already time-sorted (it's a subsequence of the
        # already-sorted `events`). Split into runs by gap.
        runs = [[occurrences[0]]]
        for prev, curr in zip(occurrences, occurrences[1:]):
            gap = (curr.start_datetime.date() - prev.start_datetime.date()).days
            if gap <= MAX_GAP_DAYS:
                runs[-1].append(curr)
            else:
                runs.append([curr])

        for run in runs:
            if len(run) < MIN_OCCURRENCES:
                display_items.extend(DisplayItem(event=e) for e in run)
                continue
            dates = sorted({e.start_datetime.date() for e in run})
            display_items.append(
                DisplayItem(
                    event=run[0],
                    is_series=True,
                    badge=_infer_pattern(dates),
                    range_start=dates[0],
                    range_end=dates[-1],
                    count=len(run),
                    occurrences=run,
                )
            )

    display_items.sort(key=lambda item: item.event.start_datetime)
    return display_items

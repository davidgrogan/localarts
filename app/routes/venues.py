from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify

from app.models import db, Venue, Event, EventType
from app.utils import slugify, get_or_create_event_type, resolve_uploaded_image_url
from app.scrapers.base import preview_scrape, run_scrape, ScrapeError
from app.auth import login_required

bp = Blueprint("venues", __name__, url_prefix="/venues")
# Like artists.py, this is a mix rather than an all-admin surface: browsing
# the venue directory (list_venues, detail) is public -- visitors can see
# what venues participate and what's coming up at each one -- while
# managing venues (add/edit/delete, scrape config/preview/run, scan) stays
# admin-only via @login_required on each of those routes individually.

SOURCE_TYPES = [
    ("manual", "Manual only (no scraping)"),
    ("squarespace_json", "Squarespace (?format=json trick)"),
    ("ical", "iCal / .ics feed"),
    ("html", "Generic HTML (CSS selectors, static pages)"),
    ("rendered_html", "Headless browser (CSS selectors, JS-rendered pages / widgets)"),
    ("elfsight_jsonld", "Elfsight widget (JSON-LD -- recommended for Elfsight Event Calendar embeds)"),
    ("haze_calendar", "Haze-style calendar widget (time[datetime] + aria-label based)"),
]


@bp.route("/")
def list_venues():
    venues = Venue.query.order_by(Venue.name).all()
    return render_template("venues/list.html", venues=venues)


def _scannable_venues():
    return (
        Venue.query.filter(Venue.is_active.is_(True), Venue.source_type != "manual")
        .order_by(Venue.name)
        .all()
    )


@bp.route("/scan")
@login_required
def scan_page():
    """One-click rescrape of every active, non-manual venue -- the same
    thing scrape_all.py/the scrape.timer does on a schedule, triggered on
    demand from the admin UI. Renders a page that runs the scan one venue
    at a time via JS (hitting scan_one below for each), so there's a real
    progress bar instead of one long silent request -- new events still
    land pending review as usual (never auto-approved), and one venue
    failing to fetch doesn't stop the rest."""
    venues = _scannable_venues()
    return render_template("venues/scan.html", venues=venues)


@bp.route("/<int:venue_id>/scan-one", methods=["POST"])
@login_required
def scan_one(venue_id):
    """JSON endpoint the Scan page's JS calls once per venue in sequence.
    Deliberately one venue per request (rather than looping server-side)
    so the browser can update a progress bar after each result comes
    back instead of just waiting on one big request."""
    venue = Venue.query.get_or_404(venue_id)
    try:
        run = run_scrape(venue, approve_new=False)
        return jsonify(
            ok=True, venue=venue.name, created=run.events_created, updated=run.events_updated
        )
    except ScrapeError as exc:
        return jsonify(ok=False, venue=venue.name, error=str(exc))
    except Exception as exc:  # noqa: BLE001 -- report it, don't 500 the whole scan
        return jsonify(ok=False, venue=venue.name, error=f"unexpected error ({exc})")


@bp.route("/scan/run-all", methods=["POST"])
@login_required
def scan_all():
    """No-JS fallback for the Scan page: runs every venue in one request,
    same as scan_page's JS loop does client-side, just without a
    progress bar."""
    venues = _scannable_venues()
    if not venues:
        flash("No active, scrapable venues to scan.", "error")
        return redirect(url_for("events.review"))

    scanned = 0
    created = 0
    updated = 0
    failed = []
    for venue in venues:
        try:
            run = run_scrape(venue, approve_new=False)
            scanned += 1
            created += run.events_created
            updated += run.events_updated
        except ScrapeError as exc:
            failed.append(f"{venue.name}: {exc}")
        except Exception as exc:  # noqa: BLE001 -- keep going on one bad venue
            failed.append(f"{venue.name}: unexpected error ({exc})")

    summary = f"Scanned {scanned}/{len(venues)} venue(s): {created} new show(s), {updated} updated."
    if failed:
        flash(f"{summary} Failed: {'; '.join(failed)}", "error")
    else:
        flash(summary, "success")
    return redirect(url_for("events.review"))


def _resolve_venue_image_url(form, files):
    """A venue's own photo -- shown on its detail page, and used as a
    fallback image on any of its events that don't have their own
    image_url (see calendar.html/events/detail.html). Same upload-vs-URL
    precedence as events.py's Add/Edit Show form; see
    app/utils.py's resolve_uploaded_image_url() for the shared logic."""
    return resolve_uploaded_image_url(
        form, files,
        file_field="venue_image",
        bad_file_message=(
            "That photo file type isn't supported (JPG, PNG, GIF, or WEBP only) "
            "-- the venue was saved without changing its photo."
        ),
    )


def _resolve_default_event_type(form):
    """Turn a submitted venue form's default-tag select + quick-add text
    into an EventType (or None), creating a brand-new one if needed."""
    new_name = form.get("new_default_event_type_name", "").strip()
    if new_name:
        return get_or_create_event_type(new_name)
    selected_id = form.get("default_event_type_id", "").strip()
    return EventType.query.get(int(selected_id)) if selected_id else None


@bp.route("/new", methods=["GET", "POST"])
@login_required
def new_venue():
    event_types = EventType.query.order_by(EventType.name).all()
    if request.method == "POST":
        name = request.form["name"].strip()
        venue = Venue(
            name=name,
            slug=slugify(name),
            address=request.form.get("address", "").strip(),
            city=request.form.get("city", "").strip(),
            state=request.form.get("state", "").strip(),
            website_url=request.form.get("website_url", "").strip(),
            events_url=request.form.get("events_url", "").strip(),
            source_type=request.form.get("source_type", "manual"),
            scrape_config=request.form.get("scrape_config", "").strip() or "{}",
            image_url=_resolve_venue_image_url(request.form, request.files),
        )
        default_tag = _resolve_default_event_type(request.form)
        venue.default_event_type = default_tag
        db.session.add(venue)
        db.session.commit()
        flash(f"Added venue “{venue.name}”.", "success")
        return redirect(url_for("venues.detail", venue_id=venue.id))
    return render_template(
        "venues/form.html", venue=None, source_types=SOURCE_TYPES, event_types=event_types
    )


@bp.route("/<int:venue_id>")
def detail(venue_id):
    venue = Venue.query.get_or_404(venue_id)
    events = Event.query.filter_by(venue_id=venue.id).order_by(Event.start_datetime.asc()).all()
    approved = [e for e in events if e.is_approved]
    # Rejected events are also is_approved=False, but they're not
    # "awaiting review" anymore -- they're deliberately discarded and
    # live in Review's Rejected section instead.
    pending = [e for e in events if not e.is_approved and not e.is_rejected]
    return render_template("venues/detail.html", venue=venue, approved=approved, pending=pending)


@bp.route("/<int:venue_id>/edit", methods=["GET", "POST"])
@login_required
def edit_venue(venue_id):
    venue = Venue.query.get_or_404(venue_id)
    event_types = EventType.query.order_by(EventType.name).all()
    if request.method == "POST":
        venue.name = request.form["name"].strip()
        venue.address = request.form.get("address", "").strip()
        venue.city = request.form.get("city", "").strip()
        venue.state = request.form.get("state", "").strip()
        venue.website_url = request.form.get("website_url", "").strip()
        venue.events_url = request.form.get("events_url", "").strip()
        venue.source_type = request.form.get("source_type", "manual")
        venue.scrape_config = request.form.get("scrape_config", "").strip() or "{}"
        venue.image_url = _resolve_venue_image_url(request.form, request.files)
        venue.is_active = bool(request.form.get("is_active"))
        venue.default_event_type = _resolve_default_event_type(request.form)
        db.session.commit()
        flash(f"Updated “{venue.name}”.", "success")
        return redirect(url_for("venues.detail", venue_id=venue.id))
    return render_template(
        "venues/form.html", venue=venue, source_types=SOURCE_TYPES, event_types=event_types
    )


@bp.route("/<int:venue_id>/delete", methods=["POST"])
@login_required
def delete_venue(venue_id):
    venue = Venue.query.get_or_404(venue_id)
    db.session.delete(venue)
    db.session.commit()
    flash(f"Deleted “{venue.name}”.", "success")
    return redirect(url_for("venues.list_venues"))


@bp.route("/<int:venue_id>/scrape/preview")
@login_required
def scrape_preview(venue_id):
    venue = Venue.query.get_or_404(venue_id)
    error = None
    result = {"raw_sample": "", "events": []}
    try:
        result = preview_scrape(venue)
    except ScrapeError as exc:
        error = str(exc)
    except Exception as exc:  # noqa: BLE001 -- surface any failure to the UI
        error = f"Unexpected error: {exc}"
    return render_template("venues/scrape_preview.html", venue=venue, result=result, error=error)


@bp.route("/<int:venue_id>/scrape/run", methods=["POST"])
@login_required
def scrape_run(venue_id):
    venue = Venue.query.get_or_404(venue_id)
    approve_new = bool(request.form.get("approve_new"))
    try:
        run = run_scrape(venue, approve_new=approve_new)
        flash(
            f"Scrape complete: {run.events_found} found, "
            f"{run.events_created} new, {run.events_updated} updated.",
            "success",
        )
    except ScrapeError as exc:
        flash(f"Scrape failed: {exc}", "error")
    except Exception as exc:  # noqa: BLE001
        flash(f"Scrape failed unexpectedly: {exc}", "error")
    return redirect(url_for("venues.detail", venue_id=venue.id))

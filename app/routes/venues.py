from flask import Blueprint, render_template, request, redirect, url_for, flash

from app.models import db, Venue, Event
from app.utils import slugify
from app.scrapers.base import preview_scrape, run_scrape, ScrapeError

bp = Blueprint("venues", __name__, url_prefix="/venues")

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


@bp.route("/new", methods=["GET", "POST"])
def new_venue():
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
        )
        db.session.add(venue)
        db.session.commit()
        flash(f"Added venue “{venue.name}”.", "success")
        return redirect(url_for("venues.detail", venue_id=venue.id))
    return render_template("venues/form.html", venue=None, source_types=SOURCE_TYPES)


@bp.route("/<int:venue_id>")
def detail(venue_id):
    venue = Venue.query.get_or_404(venue_id)
    events = Event.query.filter_by(venue_id=venue.id).order_by(Event.start_datetime.asc()).all()
    approved = [e for e in events if e.is_approved]
    pending = [e for e in events if not e.is_approved]
    return render_template("venues/detail.html", venue=venue, approved=approved, pending=pending)


@bp.route("/<int:venue_id>/edit", methods=["GET", "POST"])
def edit_venue(venue_id):
    venue = Venue.query.get_or_404(venue_id)
    if request.method == "POST":
        venue.name = request.form["name"].strip()
        venue.address = request.form.get("address", "").strip()
        venue.city = request.form.get("city", "").strip()
        venue.state = request.form.get("state", "").strip()
        venue.website_url = request.form.get("website_url", "").strip()
        venue.events_url = request.form.get("events_url", "").strip()
        venue.source_type = request.form.get("source_type", "manual")
        venue.scrape_config = request.form.get("scrape_config", "").strip() or "{}"
        venue.is_active = bool(request.form.get("is_active"))
        db.session.commit()
        flash(f"Updated “{venue.name}”.", "success")
        return redirect(url_for("venues.detail", venue_id=venue.id))
    return render_template("venues/form.html", venue=venue, source_types=SOURCE_TYPES)


@bp.route("/<int:venue_id>/delete", methods=["POST"])
def delete_venue(venue_id):
    venue = Venue.query.get_or_404(venue_id)
    db.session.delete(venue)
    db.session.commit()
    flash(f"Deleted “{venue.name}”.", "success")
    return redirect(url_for("venues.list_venues"))


@bp.route("/<int:venue_id>/scrape/preview")
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

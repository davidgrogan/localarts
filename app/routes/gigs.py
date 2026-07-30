"""Public "Submit your show" form -- lets artists/promoters propose a
show (including DIY one-off shows with no formal venue, e.g. a house
show or a backyard set) without needing an admin account.

Every submission lands as a pending GigSubmission row (see models.py's
docstring for why this is a separate table rather than creating an Event
directly -- short version: it's unvetted, anyone-can-submit input, same
"keep it out of the public calendar until a human looks at it" idea as a
scraped Event's is_approved=False) and immediately emails David via
send_admin_email() so nothing sits unnoticed in a queue he'd otherwise
have to remember to check.

Conversion (turning a submission into a real Event) is deliberately
*not* a bespoke form in this blueprint -- review() below just links each
pending submission's "Convert to show" button straight into
events.new_event(from_gig=<id>), which pre-fills the normal Add Show
form from the submission (see that route for the prefill logic) and
marks this row "converted" + links the resulting Event back here once
it's actually saved. Reusing the existing show form means artist-linking,
tagging, and venue selection all come for free instead of needing their
own bespoke conversion UI.
"""
import re
from datetime import datetime

from flask import Blueprint, flash, redirect, render_template, request, url_for

from app.auth import login_required
from app.models import GigSubmission, db
from app.utils import local_now, save_flyer_upload, send_admin_email

bp = Blueprint("gigs", __name__, url_prefix="/gigs")

# Deliberately loose (not a full RFC 5322 validator) -- just enough to catch
# an empty/obviously-mistyped address before it's saved and a reply to it
# would bounce; a real confirmation email would be the stronger check, but
# this form doesn't send one (see submit_gig()'s docstring below).
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


@bp.route("/submit", methods=["GET", "POST"])
def submit_gig():
    if request.method == "POST":
        # Honeypot -- same pattern as contact.py's form: a field hidden from
        # real visitors via CSS (.hp-field in style.css), tripped only by a
        # bot that fills in every input on the page including hidden ones.
        # Pretend to succeed rather than revealing it was caught.
        if request.form.get("website", "").strip():
            flash("Thanks! Your show has been flagged for review.", "success")
            return redirect(url_for("gigs.submit_gig"))

        submitter_name = request.form.get("submitter_name", "").strip()
        submitter_email = request.form.get("submitter_email", "").strip()
        venue_name = request.form.get("venue_name", "").strip()
        lineup_text = request.form.get("lineup_text", "").strip()
        # Optional -- see models.py's GigSubmission.genres_text docstring
        # for why this isn't required.
        genres_text = request.form.get("genres_text", "").strip()
        start_raw = request.form.get("start_datetime", "").strip()
        flyer_file = request.files.get("flyer")

        errors = []
        if not submitter_name:
            errors.append("Your name is required.")
        if not submitter_email or not _EMAIL_RE.match(submitter_email):
            errors.append("A valid email address is required.")
        if not venue_name:
            errors.append("Location/venue is required.")
        if not lineup_text:
            errors.append("Please list the bands on the bill.")

        start_dt = None
        if not start_raw:
            errors.append("Date and time are required.")
        else:
            try:
                start_dt = datetime.strptime(start_raw, "%Y-%m-%dT%H:%M")
            except ValueError:
                errors.append("That date/time didn't parse -- please use the date/time picker.")

        flyer_filename = None
        if flyer_file is None or not flyer_file.filename:
            errors.append("Please upload a flyer image.")
        else:
            flyer_filename = save_flyer_upload(flyer_file)
            if flyer_filename is None:
                errors.append(
                    "That flyer file type isn't supported -- please upload a JPG, PNG, GIF, or WEBP image."
                )

        if errors:
            for error in errors:
                flash(error, "error")
            return render_template("gigs/submit.html", form=request.form)

        submission = GigSubmission(
            start_datetime=start_dt,
            venue_name=venue_name,
            lineup_text=lineup_text,
            genres_text=genres_text or None,
            flyer_filename=flyer_filename,
            submitter_name=submitter_name,
            submitter_email=submitter_email,
        )
        db.session.add(submission)
        db.session.commit()

        # Best-effort: the submission is already safely saved above
        # regardless of whether this email goes out, so a broken/unconfigured
        # mail server shouldn't make the submitter think their show vanished
        # -- but it's still worth telling them the notification part failed,
        # since otherwise "we'll take a look soon" is a promise that quietly
        # depends on David actually noticing the review queue on his own.
        try:
            send_admin_email(
                f"New show submitted: {venue_name}",
                (
                    f"Submitted by: {submitter_name} ({submitter_email})\n"
                    f"Date/time: {start_dt.strftime('%b %-d, %Y %-I:%M %p')}\n"
                    f"Location/venue (as submitted): {venue_name}\n"
                    + (f"Genre(s): {genres_text}\n" if genres_text else "")
                    + "\n"
                    f"Lineup:\n{lineup_text}\n\n"
                    f"Review it here: {url_for('gigs.review', _external=True)}"
                ),
                reply_to=submitter_email,
            )
        except Exception as exc:  # noqa: BLE001 -- notification failing shouldn't lose the submission
            flash(
                "Your show was submitted, but the notification email didn't go out "
                f"({exc}) -- it's still safely waiting in the review queue.",
                "error",
            )
            return redirect(url_for("gigs.submit_gig"))

        flash(
            "Thanks! Your show has been flagged for review -- we'll take a look and get it up on the calendar soon.",
            "success",
        )
        return redirect(url_for("gigs.submit_gig"))

    return render_template("gigs/submit.html", form=None)


@bp.route("/review")
@login_required
def review():
    pending = (
        GigSubmission.query.filter_by(status="pending")
        .order_by(GigSubmission.start_datetime.asc())
        .all()
    )
    # A short recent history (converted + dismissed) below the pending
    # queue -- mainly so a dismiss made by mistake is easy to find and
    # undo, and so "did I already handle this one" has an answer without
    # needing to remember. Capped rather than showing every submission
    # ever, same "this is a working queue, not a permanent archive" idea
    # as events/review.html's buckets.
    history = (
        GigSubmission.query.filter(GigSubmission.status != "pending")
        .order_by(GigSubmission.reviewed_at.desc())
        .limit(50)
        .all()
    )
    return render_template("gigs/review.html", pending=pending, history=history)


@bp.route("/<int:submission_id>/dismiss", methods=["POST"])
@login_required
def dismiss(submission_id):
    """Not a real/duplicate/spam submission -- kept (not deleted) so it's
    recognizable if the same thing gets submitted again, and so it isn't
    just gone forever if dismissed by mistake (see restore() below)."""
    submission = GigSubmission.query.get_or_404(submission_id)
    submission.status = "dismissed"
    submission.reviewed_at = local_now()
    db.session.commit()
    flash("Dismissed that submission.", "success")
    return redirect(request.referrer or url_for("gigs.review"))


@bp.route("/<int:submission_id>/restore", methods=["POST"])
@login_required
def restore(submission_id):
    """Undo a dismiss -- puts a submission back in the pending queue."""
    submission = GigSubmission.query.get_or_404(submission_id)
    submission.status = "pending"
    submission.reviewed_at = None
    db.session.commit()
    flash("Restored to the pending queue.", "success")
    return redirect(request.referrer or url_for("gigs.review"))

"""Public contact form -- lets visitors ask for an artist, show, or venue
to be added (or flag something wrong) without needing an admin account.

Sends a plain email via Gmail SMTP using an account App Password (not
the real Gmail password) rather than pulling in a whole mail library --
Python's stdlib smtplib/email modules are enough for one outgoing message
at low volume. See README.md / .env.example for how to generate one.

The actual SMTP-sending code used to live here as a module-private
_send_email() -- it's been pulled out to app.utils.send_admin_email()
since app/routes/gigs.py's "Submit your show" notifier needed the exact
same mechanism and there was no reason to duplicate it in a second
blueprint. Behavior here is unchanged.
"""
from flask import Blueprint, flash, redirect, render_template, request, url_for

from app.utils import send_admin_email

bp = Blueprint("contact", __name__, url_prefix="/contact")

CATEGORIES = [
    ("artist", "Add a local artist"),
    ("event", "Add or correct a show"),
    ("venue", "Add a venue"),
    ("other", "Something else"),
]


def _send_email(name, reply_to, category_label, message):
    body = (
        f"Category: {category_label}\n"
        f"Name: {name or '(not provided)'}\n"
        f"Email: {reply_to or '(not provided)'}\n\n"
        f"{message}"
    )
    send_admin_email(f"Paradise City Music contact form: {category_label}", body, reply_to=reply_to)


@bp.route("/", methods=["GET", "POST"])
def contact():
    if request.method == "POST":
        # Honeypot: a field hidden from real visitors via CSS, but a
        # simple bot that fills in every input on the page will trip it.
        # Pretend to succeed rather than revealing it was caught.
        if request.form.get("website", "").strip():
            flash("Thanks! We'll take a look.", "success")
            return redirect(url_for("contact.contact"))

        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip()
        category = request.form.get("category", "other")
        message = request.form.get("message", "").strip()
        category_label = dict(CATEGORIES).get(category, "Something else")

        if not message:
            flash("Please enter a message before submitting.", "error")
            return render_template("contact.html", categories=CATEGORIES, form=request.form)

        try:
            _send_email(name, email, category_label, message)
        except Exception as exc:  # noqa: BLE001 -- show it, don't 500
            flash(f"Sorry, something went wrong sending that: {exc}", "error")
            return render_template("contact.html", categories=CATEGORIES, form=request.form)

        flash("Thanks for reaching out -- we'll take a look soon.", "success")
        return redirect(url_for("contact.contact"))

    return render_template("contact.html", categories=CATEGORIES, form=None)

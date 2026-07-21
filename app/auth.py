"""Single-admin session login for gating the site's management screens
(venue/event/artist editing, scraping, the review queue) behind a login,
now that the public calendar and artist pages are meant for real visitors
rather than just David.

Deliberately not a full user system -- there's exactly one admin (David),
so this is a single username/password pair (from env vars, password
stored as a hash) plus Flask's signed session cookie (already backed by
SECRET_KEY, which every deployment already sets). No new dependencies:
werkzeug's password hashing ships with Flask itself.

Two ways routes get protected:
  - `login_required` -- decorate an individual view (used for the few
    artist routes that are admin-only while their siblings stay public,
    e.g. artists.edit_artist but not artists.detail).
  - `require_admin` -- register as a blueprint's `before_request` hook
    to protect every route in that blueprint at once (used for venues.py
    and events.py, which are admin-only end to end).
"""
import os
from functools import wraps

from flask import Blueprint, flash, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

bp = Blueprint("auth", __name__)

ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "admin")
# Dev-only fallback (password: "admin") so the admin screens work out of
# the box locally with zero setup. ALWAYS set a real ADMIN_PASSWORD_HASH
# in any deployment that isn't just your own laptop. Generate one with:
#   python3 -c "from werkzeug.security import generate_password_hash as g; print(g('your-real-password'))"
ADMIN_PASSWORD_HASH = os.environ.get("ADMIN_PASSWORD_HASH") or generate_password_hash("admin")


def _is_logged_in():
    return bool(session.get("is_admin"))


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not _is_logged_in():
            return redirect(url_for("auth.login", next=request.path))
        return view(*args, **kwargs)

    return wrapped


def require_admin():
    """For use as `bp.before_request(require_admin)` -- protects every
    route on a blueprint without decorating each one individually."""
    if not _is_logged_in():
        return redirect(url_for("auth.login", next=request.path))


@bp.route("/login", methods=["GET", "POST"])
def login():
    if _is_logged_in():
        return redirect(url_for("main.calendar"))

    next_url = request.values.get("next") or url_for("main.calendar")

    if request.method == "POST":
        username = request.form.get("username", "")
        password = request.form.get("password", "")
        if username == ADMIN_USERNAME and check_password_hash(ADMIN_PASSWORD_HASH, password):
            session["is_admin"] = True
            flash("Logged in.", "success")
            # Only ever redirect to a local path from this form field --
            # never trust it enough to send someone off-site.
            dest = request.form.get("next") or url_for("main.calendar")
            if not dest.startswith("/"):
                dest = url_for("main.calendar")
            return redirect(dest)
        flash("Incorrect username or password.", "error")

    return render_template("auth/login.html", next_url=next_url)


@bp.route("/logout", methods=["POST"])
def logout():
    session.pop("is_admin", None)
    flash("Logged out.", "success")
    return redirect(url_for("main.calendar"))

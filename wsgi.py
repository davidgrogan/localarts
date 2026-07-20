"""Entry point for gunicorn in production.

    gunicorn --workers 3 --bind 127.0.0.1:8000 wsgi:app

run.py stays as the plain `python run.py` entry point for local dev
(Flask's built-in dev server); this is the one systemd/gunicorn points at.

Wrapped in ProxyFix so it works correctly behind a reverse proxy -- in
particular x_prefix, which reads an X-Forwarded-Prefix header set by the
proxy and uses it as this app's SCRIPT_NAME. That's only needed when the
app is mounted under a path on a shared domain (e.g. Caddy proxying
waveyvibe.dev/localarts/* here) rather than owning a whole (sub)domain to
itself -- without it, url_for()-generated links and static asset URLs
would come out root-relative ("/events/new") instead of prefixed
("/localarts/events/new"), and the browser would request the wrong path.
x_proto/x_host are the usual pair for a TLS-terminating proxy so Flask
knows the original request was https and knows its real hostname.
"""
from werkzeug.middleware.proxy_fix import ProxyFix

from app import create_app

app = create_app()
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)

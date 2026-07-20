"""Entry point for gunicorn in production.

    gunicorn --workers 3 --bind 127.0.0.1:8000 wsgi:app

run.py stays as the plain `python run.py` entry point for local dev
(Flask's built-in dev server); this is the one systemd/gunicorn points at.
"""
from app import create_app

app = create_app()

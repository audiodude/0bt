"""Gunicorn entry point. Use `gunicorn wsgi:app`."""
from app.main import create_app

app = create_app()

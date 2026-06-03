# Deployment Guide

Server deployment notes for the media_monitor crawler. The Docker/compose files
live on the server (not in this repo); everything else needed for a clean clone
is committed here.

## Architecture — what must run

The app is **not** a single process. A working deployment needs:

| Process            | Command                                                                 | Notes |
|--------------------|-------------------------------------------------------------------------|-------|
| Web (WSGI)         | `gunicorn media_monitor.wsgi:application --bind 0.0.0.0:8000`            | Serves dashboard + API. |
| Celery worker      | `celery -A media_monitor worker -l info`                                | Does the actual discovery + fetching + parsing. |
| Celery beat        | `celery -A media_monitor beat -l info --scheduler django_celery_beat.schedulers:DatabaseScheduler` | Triggers periodic discovery. |
| PostgreSQL         | managed service or container                                            | Set `DATABASE_URL`. |
| Redis              | managed service or container                                            | Celery broker — set `REDIS_URL`. |
| Elasticsearch      | optional                                                                | Search/indexing degrade gracefully if absent. |

The web server alone crawls **nothing** — the worker and beat are mandatory for
the pipeline to do anything.

## First-time setup on the server

```bash
git clone <repo-url> media-monitor
cd media-monitor

# 1. Environment
cp .env.example .env
# …edit .env: real SECRET_KEY, ALLOWED_HOSTS, DATABASE_URL, REDIS_URL, etc.

# 2. Dependencies (inside your venv / image)
pip install -r requirements.txt

# 3. One-time asset downloads (also handled by entrypoint.sh)
python -c "import nltk; nltk.download('vader_lexicon')"
python -m playwright install chromium          # only if any seed uses Playwright
#   (Playwright also needs OS libs: `python -m playwright install-deps`)

# 4. Database + scheduler + static
export DJANGO_SETTINGS_MODULE=media_monitor.settings.production
python manage.py migrate
python manage.py setup_beat
python manage.py collectstatic --noinput

# 5. Admin user (for /admin and dashboard access)
python manage.py createsuperuser
```

`entrypoint.sh` automates steps 3–4 and is meant to be wired into the Dockerfile
`ENTRYPOINT`. See the header comment in that file.

## Settings modules

Selected via `DJANGO_SETTINGS_MODULE`:

- `media_monitor.settings.production` — **use this in production.** Forces
  `DEBUG=False`, requires `SECRET_KEY`, enables HTTPS/HSTS. Requires TLS in front.
- `media_monitor.settings.development` — local only.
- `media_monitor.settings.testing` — CI / `manage.py test`.

`wsgi.py` / `asgi.py` default to production; `manage.py` defaults to development.

## Loading seeds

The crawler does nothing until `SeedSource` rows exist. Add them via the Django
admin, the REST API (`POST /api/seeds/`), or `python manage.py crawl_seeds`.

## Known gaps to address before exposing publicly

- **No authentication** on the dashboard or the write API endpoints
  (`POST/PUT/DELETE /api/seeds/`, `/api/extraction-rules/`). Anyone who can reach
  the host can edit seeds. Put it behind auth or a private network until this is
  added.
- **HTTPS is required** by production settings (`SECURE_SSL_REDIRECT=True`) —
  terminate TLS at a reverse proxy / load balancer.

## Manual operation (without beat)

```bash
python manage.py crawl_seeds --all     # run discovery once
python manage.py fetch_pending         # fetch pending URLs once
python manage.py index_articles --reset  # rebuild the Elasticsearch index
```

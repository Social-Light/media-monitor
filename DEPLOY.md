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
| PostgreSQL (crawler) | managed service or container                                          | Crawler's own data. Set `DATABASE_URL`. |
| PostgreSQL (platform) | the shared platform database                                         | Only when `PLATFORM_INTEGRATED=True`. Set `PLATFORM_DB_URL`. See "Platform integration". |
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
# For platform integration also set PLATFORM_INTEGRATED=True and PLATFORM_DB_URL
# (production.py forces PLATFORM_INTEGRATED=True). See "Platform integration" below.

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

## Platform integration

The crawler can push parsed coverage into the external media-monitoring platform
(the `socialmonitor` project). This is controlled by `PLATFORM_INTEGRATED`:

- **Dev/CI (`PLATFORM_INTEGRATED=False`)** — the `platform_sync` models are
  `managed=True` and live in the crawler's own DB. `migrate` builds local
  `monitor_*` tables; no second database is needed.
- **Production (`PLATFORM_INTEGRATED=True`, forced by production.py)** — the
  `platform_sync` models are `managed=False` and are routed to a **second
  database** (`PLATFORM_DB_URL`) that is the *shared platform PostgreSQL*. The
  crawler reads `Organization`/`Keyword`/`Competitor` and writes
  `OnlineArticle`/`CompetitorArticle` there.

Critical operational rules for production:

1. **The crawler never migrates the platform DB.** Its router refuses to migrate
   `platform_sync` anywhere and keeps crawler/built-in tables out of the platform
   DB. `python manage.py migrate` only touches the crawler's `default` DB — run
   it exactly as in standalone mode.
2. **The `monitor_*` tables must already exist** in the shared DB (created by the
   platform's own migrations) before you enable integration, or writes will fail.
3. **Organisations, keywords and competitors are owned by the platform.** Any
   such records created in local dev SQLite are dev-only and do NOT transfer; in
   production the bridge matches against whatever the platform DB contains.
4. The bridge is wrapped in try/except in `parse_page()`, so a platform outage
   degrades gracefully — parsing continues, coverage is simply not pushed.

Verify routing on the server without writing anything:

```bash
python manage.py shell -c "from django.db import router; from platform_sync.models import OnlineArticle; print(router.db_for_write(OnlineArticle), OnlineArticle._meta.managed)"
# expect: platform False
```

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

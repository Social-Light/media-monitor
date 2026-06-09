# Integrating the Crawler with the Social Light Platform (server)

This guide adds the **media_monitor crawler** to the existing `socialmonitor`
docker-compose stack on the server, sharing the running PostgreSQL instance.

## Architecture

```
                    sociallight_db  (PostgreSQL : one server, two databases)
                    ├── database "sociallight"  ← platform (sociallight_django) owns this
                    │     monitor_organization, monitor_keyword, monitor_competitor,
                    │     monitor_onlinearticle, monitor_competitorarticle, monitor_alert, …
                    └── database "crawler"      ← crawler owns this (NEW)
                          discovery_*, fetcher_*, matching_*, alerts_*, auth_*, …

  crawler containers
  ├── crawler_web     (optional: dashboard + REST API)
  ├── crawler_worker  (Celery: discovery → fetch → parse → push)
  └── crawler_beat    (Celery beat: periodic discovery/fetch)
        │  default DB  → postgres .../crawler        (its own tables, managed=True)
        │  platform DB → postgres .../sociallight    (monitor_* tables, managed=False)
        └  broker      → redis://redis:6379/1        (separate Redis DB from platform)
```

**Why two databases, not one:** the platform uses a custom user model
(`AUTH_USER_MODEL = monitor.User`) and has its own migration history. The crawler
uses Django's default `auth.User`. Two Django projects cannot share one database's
`auth`/`contenttypes`/`django_migrations` tables without colliding. So the crawler
keeps its **own** database on the same Postgres server and reaches into the
platform's `sociallight` database *only* through the `platform_sync` models
(`managed=False`) to read orgs/keywords/competitors and write articles.

The crawler **never migrates** the platform database — its router forbids it.

---

## Prerequisites

- The crawler repo cloned next to the platform, e.g. `/opt/sociallight/media-monitor`
  (the platform is `/opt/sociallight/socialmonitor`).
- The platform already running on Postgres in `sociallight_db` (database `sociallight`).
- The platform's `monitor_*` tables exist in `sociallight` (created by the
  platform's own migrations) — the crawler's `platform_sync` models mirror them
  exactly and rely on them being present.

---

## Step 1 — Create the crawler's database

The crawler needs its own DB on the same Postgres. Create it using the existing
`sociallight` superuser/role:

```bash
docker exec -it sociallight_db psql -U sociallight -c "CREATE DATABASE crawler OWNER sociallight;"
# verify
docker exec -it sociallight_db psql -U sociallight -c "\l" | grep -E "sociallight|crawler"
```

(Nothing else is created by hand — the crawler's `migrate` builds its tables.)

---

## Step 2 — Crawler environment file

Create `/opt/sociallight/media-monitor/.env` (do NOT commit it):

```ini
# Django
SECRET_KEY=<generate a 50-char random key>
DEBUG=False
ALLOWED_HOSTS=<server-domain>,localhost,127.0.0.1

# Crawler's own database (separate DB, same Postgres server)
DATABASE_URL=postgres://sociallight:sociallight_password@db:5432/crawler

# Platform integration → shared platform database
PLATFORM_INTEGRATED=True
PLATFORM_DB_URL=postgres://sociallight:sociallight_password@db:5432/sociallight

# Celery broker — use Redis DB index 1 so crawler keys don't collide with the platform (0)
REDIS_URL=redis://redis:6379/1

# Email — Resend via Anymail (same sender as the platform)
DEFAULT_FROM_EMAIL=Social Light <noreply@sociallight.africa>
EMAIL_BACKEND=anymail.backends.resend.EmailBackend
RESEND_API_KEY=<resend api key>
# (SMTP fallback instead of Anymail: EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend
#  with EMAIL_HOST=smtp.resend.com, EMAIL_PORT=587, EMAIL_HOST_USER=resend,
#  EMAIL_HOST_PASSWORD=<resend api key>, EMAIL_USE_TLS=True)

# Optional
SENTRY_DSN=
ELASTICSEARCH_URL=
```

Generate a secret key:
```bash
docker run --rm python:3.12-slim python -c "import secrets; print(secrets.token_urlsafe(50))"
```

> Note: `production.py` forces `PLATFORM_INTEGRATED=True` regardless, but set it in
> `.env` too for clarity. `EMAIL_HOST_*` (used for alerts in phase 2) are read by
> the crawler's settings if present — see phase 2.

---

## Step 3 — Add the crawler services to docker-compose

Append these services to the platform's `docker-compose.yml` (so they share the
same default network and can reach `db` and `redis` by name). Adjust the build
path if the crawler lives elsewhere.

```yaml
  # ── Crawler: dashboard + REST API (optional) ──────────────────────────
  crawler_web:
    build: ../media-monitor
    image: media_monitor:latest
    container_name: crawler_web
    command: gunicorn media_monitor.wsgi:application --bind 0.0.0.0:8002 --workers 3 --timeout 300
    env_file:
      - ../media-monitor/.env
    environment:
      RUN_SETUP: "1"          # this container runs migrate/collectstatic/setup_beat
    depends_on:
      - db
      - redis
    expose:
      - "8002"

  # ── Crawler: Celery worker (does the crawling) ────────────────────────
  crawler_worker:
    image: media_monitor:latest      # reuses the image built by crawler_web
    container_name: crawler_worker
    command: celery -A media_monitor worker -l info --concurrency 4
    env_file:
      - ../media-monitor/.env
    environment:
      RUN_SETUP: "0"          # skip setup; crawler_web already did it
    depends_on:
      - crawler_web
      - db
      - redis

  # ── Crawler: Celery beat (periodic discovery/fetch) ───────────────────
  crawler_beat:
    image: media_monitor:latest
    container_name: crawler_beat
    command: celery -A media_monitor beat -l info --scheduler django_celery_beat.schedulers:DatabaseScheduler
    env_file:
      - ../media-monitor/.env
    environment:
      RUN_SETUP: "0"
    depends_on:
      - crawler_web
      - db
      - redis
```

If you don't need the dashboard, drop `crawler_web` and instead run the one-time
setup manually (Step 4) and set `RUN_SETUP: "1"` on `crawler_worker`.

---

## Step 4 — Build, migrate, register beat schedule

```bash
cd /opt/sociallight/socialmonitor          # where the compose file lives

# Build the crawler image
docker compose build crawler_web

# One-time: migrate the crawler's OWN database + register periodic tasks.
# (crawler_web does this automatically on start via entrypoint RUN_SETUP=1,
#  but you can also run it explicitly:)
docker compose run --rm crawler_web python manage.py migrate
docker compose run --rm crawler_web python manage.py setup_beat
```

`migrate` only touches the `crawler` database. It does **not** create or alter any
`monitor_*` tables — those belong to the platform.

---

## Step 5 — Verify the wiring (writes nothing)

```bash
docker compose run --rm crawler_web python manage.py shell -c \
"from django.db import router; from platform_sync.models import OnlineArticle, Organization; \
print('OnlineArticle DB :', router.db_for_write(OnlineArticle)); \
print('managed          :', OnlineArticle._meta.managed); \
print('platform orgs    :', Organization.objects.count())"
```

Expected:
```
OnlineArticle DB : platform
managed          : False
platform orgs    : <number of orgs already in the platform>
```

If `platform orgs` errors or is 0, the platform DB connection or its `monitor_*`
tables aren't reachable — fix before going further.

---

## Step 6 — Load sources and confirm organisations

- Organisations / keywords / competitors are **owned by the platform** — create
  them in the platform UI (or they already exist). The crawler reads them live.
- Load crawl sources into the crawler:

```bash
# CSV bulk import (seeds only — see import_sources docs)
docker compose run --rm crawler_web python manage.py import_sources sources.csv
# or add seeds via the dashboard / API, then:
docker compose run --rm crawler_web python manage.py crawl_seeds --all
```

---

## Step 7 — Start everything and confirm captures land in the platform

```bash
docker compose up -d crawler_web crawler_worker crawler_beat
docker compose logs -f crawler_worker      # watch discovery → fetch → parse → push
```

After a crawl cycle, confirm the crawler wrote into the platform DB:

```bash
docker exec -it sociallight_db psql -U sociallight -d sociallight -c \
"SELECT count(*) FROM monitor_onlinearticle; SELECT count(*) FROM monitor_competitorarticle;"
```

Those counts should rise as the crawler captures matching coverage. They'll appear
in the platform UI under each organisation's online coverage.

---

## Operational notes

- **Redis isolation**: the crawler uses `redis://redis:6379/1` (DB index 1) so its
  Celery keys don't mix with the platform's (index 0).
- **Migrations**: run crawler migrations only against its own DB (default). Never
  point `migrate` at `sociallight`.
- **Schema parity**: `platform_sync/models.py` mirrors the platform's `monitor_*`
  columns exactly. If the platform changes those tables, update `platform_sync`
  models to match (they are `managed=False`, so Django won't migrate them).
- **Dashboard (optional)**: to expose `crawler_web` (port 8002), add an nginx
  location block; it has **no authentication** yet, so keep it internal/VPN-only.
- **Secrets**: rotate the Anthropic key and Resend password currently in
  `socialmonitor/.env`; keep both `.env` files out of version control.

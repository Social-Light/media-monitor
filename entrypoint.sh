#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# entrypoint.sh — one-time-per-boot setup for the media_monitor web/worker image.
#
# Idempotent: safe to run on every container start. Call it from your Dockerfile
# ENTRYPOINT (or run it manually after `git clone` on the server). It does NOT
# start a server itself — pass the process to run as arguments, e.g.:
#
#   ./entrypoint.sh gunicorn media_monitor.wsgi:application --bind 0.0.0.0:8000
#   ./entrypoint.sh celery -A media_monitor worker -l info
#   ./entrypoint.sh celery -A media_monitor beat -l info \
#                          --scheduler django_celery_beat.schedulers:DatabaseScheduler
#
# Set RUN_SETUP=0 to skip the migrate/collectstatic/beat steps (useful for the
# worker/beat containers so only the web container runs them).
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

: "${DJANGO_SETTINGS_MODULE:=media_monitor.settings.production}"
export DJANGO_SETTINGS_MODULE

if [ "${RUN_SETUP:-1}" = "1" ]; then
  echo "[entrypoint] Applying database migrations…"
  python manage.py migrate --noinput

  echo "[entrypoint] Collecting static files…"
  python manage.py collectstatic --noinput

  echo "[entrypoint] Registering Celery beat periodic tasks…"
  python manage.py setup_beat
fi

# One-time NLP/browser assets. These are no-ops if already present, but doing
# them at build time (in the Dockerfile) is faster — see DEPLOY.md.
echo "[entrypoint] Ensuring NLTK VADER lexicon…"
python -c "import nltk; nltk.download('vader_lexicon', quiet=True)" || true

echo "[entrypoint] Ensuring Playwright Chromium (only needed for JS-heavy seeds)…"
python -m playwright install chromium || true

echo "[entrypoint] Setup complete. Exec: $*"
exec "$@"

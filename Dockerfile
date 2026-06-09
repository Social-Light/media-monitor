# Crawler image — media_monitor (Django + Celery)
# Matches the platform's base (python:3.12-slim). requirements.txt pins Django 5.2.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DJANGO_SETTINGS_MODULE=media_monitor.settings.production

WORKDIR /app

# System deps:
#  - libpq-dev / gcc / build-essential : psycopg (PostgreSQL) + building wheels
#  - libxml2-dev / libxslt1-dev        : lxml (HTML parsing)
#  - libjpeg-dev / zlib1g-dev          : pillow
#  - curl                              : healthchecks / debugging
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential gcc libpq-dev \
    libxml2-dev libxslt1-dev \
    libjpeg-dev zlib1g-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/
RUN pip install --upgrade pip && pip install -r requirements.txt

# Optional: install the Playwright Chromium browser + its OS libs at build time.
# Only needed for seeds with use_playwright=True. Set --build-arg WITH_PLAYWRIGHT=1
# to include it (adds ~400MB). Default off to keep the image lean; entrypoint.sh
# will also attempt `playwright install chromium` at runtime as a fallback.
ARG WITH_PLAYWRIGHT=0
RUN if [ "$WITH_PLAYWRIGHT" = "1" ]; then python -m playwright install --with-deps chromium; fi

COPY . /app/
RUN chmod +x entrypoint.sh

# entrypoint.sh runs migrate/collectstatic/setup_beat + NLTK/Playwright asset
# fetches when RUN_SETUP=1 (default), then exec's the passed command. Worker/beat
# containers should pass RUN_SETUP=0 so only one container performs setup.
ENTRYPOINT ["bash", "entrypoint.sh"]
CMD ["gunicorn", "media_monitor.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "3", "--timeout", "300"]

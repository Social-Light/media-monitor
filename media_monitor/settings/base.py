"""
media_monitor/settings/base.py
────────────────────────────────
Shared settings for all environments.

Environment-specific files (development, production, testing) import this
module AFTER loading their env source (os.environ / .env file), so all
env() calls here resolve correctly at import time.
"""
import environ
from pathlib import Path
import sentry_sdk
from sentry_sdk.integrations.django import DjangoIntegration
from sentry_sdk.integrations.celery import CeleryIntegration

BASE_DIR = Path(__file__).resolve().parent.parent.parent

env = environ.Env(
    DEBUG=(bool, False),
    ALLOWED_HOSTS=(list, []),
    SENTRY_DSN=(str, ""),
    MEDIA_MONITOR_WEBHOOK_SECRET=(str, ""),
)

# ── Core ──────────────────────────────────────────────────────────────────────
SECRET_KEY    = env("SECRET_KEY", default="django-insecure-local-dev-key-change-me")
DEBUG         = env("DEBUG")
ALLOWED_HOSTS = env("ALLOWED_HOSTS")

# ── Sentry ────────────────────────────────────────────────────────────────────
_SENTRY_DSN = env("SENTRY_DSN")
if _SENTRY_DSN:
    sentry_sdk.init(
        dsn=_SENTRY_DSN,
        integrations=[DjangoIntegration(), CeleryIntegration()],
        traces_sample_rate=0.2,
        send_default_pii=False,
    )

# ── Apps ──────────────────────────────────────────────────────────────────────
DJANGO_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
]

THIRD_PARTY_APPS: list[str] = [
    "django_celery_beat",
    "django_celery_results",
    "rest_framework",
]

PROJECT_APPS: list[str] = [
    "core",
    "discovery",
    "fetcher",
    "api",
    "dashboard",
    "alerts",
    "matching",
    "platform_sync",
]

INSTALLED_APPS = DJANGO_APPS + THIRD_PARTY_APPS + PROJECT_APPS

# ── Middleware ────────────────────────────────────────────────────────────────
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "media_monitor.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "media_monitor.wsgi.application"

# ── Database ──────────────────────────────────────────────────────────────────
DATABASES = {
    "default": env.db(
        "DATABASE_URL",
        default=f"sqlite:///{BASE_DIR / 'db.sqlite3'}",
    )
}

# ── Platform integration ────────────────────────────────────────────────────────
# False → standalone: platform_sync models are managed locally in the default DB.
# True  → integrated: platform_sync models are unmanaged and routed to the shared
#         'platform' database (configured in settings/production.py).
PLATFORM_INTEGRATED = env.bool("PLATFORM_INTEGRATED", default=False)
DATABASE_ROUTERS = ["platform_sync.routers.PlatformRouter"]

# ── Password validation ───────────────────────────────────────────────────────
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# ── Internationalisation ──────────────────────────────────────────────────────
LANGUAGE_CODE = "en-us"
TIME_ZONE     = "UTC"
USE_I18N      = True
USE_TZ        = True

# ── Static files ──────────────────────────────────────────────────────────────
STATIC_URL  = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# ── Logging ───────────────────────────────────────────────────────────────────
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {
            "format": "[{levelname}] {asctime} {name}: {message}",
            "style": "{",
        },
        "simple": {
            "format": "[{levelname}] {message}",
            "style": "{",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "verbose",
        },
    },
    "root": {"handlers": ["console"], "level": "INFO"},
}

# ── Crawler ───────────────────────────────────────────────────────────────────
CRAWLER = {
    "REQUEST_TIMEOUT":    env.int("CRAWLER_TIMEOUT",     default=15),
    "USER_AGENT":         env("CRAWLER_USER_AGENT",      default="MediaMonitorBot/1.0 (+https://yoursite.com/bot)"),
    "MAX_LINKS_PER_PAGE": env.int("MAX_LINKS_PER_PAGE",  default=50),
    "POLITENESS_DELAY":   env.float("POLITENESS_DELAY",  default=1.0),
    "BING_API_KEY":       env("BING_API_KEY",            default=""),
    "GOOGLE_NEWS_API_KEY":env("GOOGLE_NEWS_API_KEY",     default=""),
    "GOOGLE_CSE_ID":      env("GOOGLE_CSE_ID",           default=""),
}

# ── Elasticsearch ─────────────────────────────────────────────────────────────
ELASTICSEARCH = {
    "HOSTS":   [env("ELASTICSEARCH_URL",      default="http://localhost:9200")],
    "INDEX":   env("ELASTICSEARCH_INDEX",     default="articles"),
    "TIMEOUT": env.int("ELASTICSEARCH_TIMEOUT", default=10),
}

# ── Celery ────────────────────────────────────────────────────────────────────
CELERY_BROKER_URL           = env("REDIS_URL", default="redis://localhost:6379/0")
CELERY_RESULT_BACKEND       = "django-db"
CELERY_ACCEPT_CONTENT       = ["json"]
CELERY_TASK_SERIALIZER      = "json"
CELERY_RESULT_SERIALIZER    = "json"
CELERY_TIMEZONE             = "UTC"
CELERY_BEAT_SCHEDULER       = "django_celery_beat.schedulers:DatabaseScheduler"
CELERY_TASK_TRACK_STARTED   = True
CELERY_TASK_TIME_LIMIT      = 300
CELERY_TASK_SOFT_TIME_LIMIT = 240

# ── Email ─────────────────────────────────────────────────────────────────────
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", default="alerts@mediamonitor.local")
EMAIL_BACKEND      = env(
    "EMAIL_BACKEND",
    default="django.core.mail.backends.console.EmailBackend",
)

# ── Integration ───────────────────────────────────────────────────────────────
MEDIA_MONITOR_WEBHOOK_SECRET = env("MEDIA_MONITOR_WEBHOOK_SECRET")

# ── Django REST Framework ─────────────────────────────────────────────────────
REST_FRAMEWORK = {
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 20,
}

import environ
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
environ.Env.read_env(BASE_DIR / ".env")

from .base import *  # noqa: F401, F403

DEBUG = False

SECRET_KEY = env("SECRET_KEY")  # required — no default in production

SECURE_SSL_REDIRECT        = True
SESSION_COOKIE_SECURE      = True
CSRF_COOKIE_SECURE         = True
SECURE_HSTS_SECONDS        = 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD        = True

CONN_MAX_AGE = 60

# ── Platform integration (shared PostgreSQL) ────────────────────────────────────
# platform_sync models become unmanaged and are routed to the 'platform' database
# (the shared platform DB). The crawler keeps its own data in 'default'.
PLATFORM_INTEGRATED = True
DATABASES = {
    "default":  env.db("DATABASE_URL"),      # crawler database
    "platform": env.db("PLATFORM_DB_URL"),   # shared platform database
}

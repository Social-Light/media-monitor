import environ
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
environ.Env.read_env(BASE_DIR / ".env")

from .base import *  # noqa: F401, F403

DEBUG = True
ALLOWED_HOSTS = env.list("ALLOWED_HOSTS", default=["localhost", "127.0.0.1"])
EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"

"""
media_monitor/celery.py

Celery application entry point.

Workers are started with:
    celery -A media_monitor worker -l info

Beat scheduler (periodic tasks) is started with:
    celery -A media_monitor beat -l info --scheduler django_celery_beat.schedulers:DatabaseScheduler

Monitor tasks in real-time with Flower:
    celery -A media_monitor flower
"""
import os
from celery import Celery

# Tell Celery which Django settings module to use
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "media_monitor.settings.development")

app = Celery("media_monitor")

# Read all CELERY_* keys from Django settings
app.config_from_object("django.conf:settings", namespace="CELERY")

# Auto-discover tasks.py in every INSTALLED_APP
app.autodiscover_tasks()
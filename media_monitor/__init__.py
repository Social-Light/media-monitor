# Load Celery when Django starts so @shared_task decorators register correctly
from .celery import app as celery_app
 
__all__ = ("celery_app",)
 
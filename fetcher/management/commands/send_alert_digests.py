"""
fetcher/management/commands/send_alert_digests.py

Flush pending platform-alert notifications as per-alert email digests. Immediate
alerts are sent at capture time; daily/weekly/monthly ones queue as unsent
PlatformAlertNotification rows and are emailed by this command.

Schedule it (Celery beat / cron), e.g.:
  python manage.py send_alert_digests --frequency daily     # run daily
  python manage.py send_alert_digests --frequency weekly    # run weekly
  python manage.py send_alert_digests                       # flush all pending
"""
from django.core.management.base import BaseCommand

from fetcher.platform_alerts import send_pending_digests


class Command(BaseCommand):
    help = "Email pending platform-alert notifications as per-alert digests."

    def add_arguments(self, parser):
        parser.add_argument(
            "--frequency",
            choices=["immediate", "daily", "weekly", "monthly"],
            help="Only flush notifications for alerts of this frequency. Default: all pending.",
        )

    def handle(self, *args, **options):
        frequency = options.get("frequency")
        flushed = send_pending_digests(frequency=frequency)
        scope = f" ({frequency})" if frequency else ""
        self.stdout.write(self.style.SUCCESS(
            f"Flushed {flushed} pending alert notification(s){scope}."
        ))

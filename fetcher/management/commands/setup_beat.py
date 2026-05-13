"""
management/commands/setup_beat.py
 
Registers all periodic Celery Beat tasks in the database.
Safe to run multiple times — uses update_or_create so re-running
never creates duplicates.
 
Usage:
    python manage.py setup_beat
    python manage.py setup_beat --disable   # registers everything but disabled
 
Run this once after every fresh database setup, or whenever you
want to reset the schedule back to defaults.
 
Tasks registered:
    discovery.run_all_seeds       every 5 minutes
    fetcher.fetch_pending_urls    every 2 minutes
"""
from django.core.management.base import BaseCommand
 
 
# ── Schedule definitions ──────────────────────────────────────────────────────
# Each entry: (task_name, human_name, every_N, period, description)
SCHEDULES = [
    (
        "discovery.run_all_seeds",
        "Discovery — run all due seeds",
        5,
        "minutes",
        "Scans all active SeedSources and dispatches discovery tasks for any that are due.",
    ),
    (
        "fetcher.fetch_pending_urls",
        "Fetcher — fetch pending URLs",
        2,
        "minutes",
        "Picks up PENDING DiscoveredURLs and dispatches fetch+parse tasks for each.",
    ),
]
 
 
class Command(BaseCommand):
    help = "Register all periodic Celery Beat tasks in the database."
 
    def add_arguments(self, parser):
        parser.add_argument(
            "--disable",
            action="store_true",
            help="Register tasks but set them as disabled.",
        )
        parser.add_argument(
            "--list",
            action="store_true",
            dest="list_only",
            help="Print current periodic tasks without making changes.",
        )
 
    def handle(self, *args, **options):
        from django_celery_beat.models import IntervalSchedule, PeriodicTask
 
        if options["list_only"]:
            self._list_tasks()
            return
 
        enabled = not options["disable"]
        created_count = 0
        updated_count = 0
 
        for task_name, human_name, every, period, description in SCHEDULES:
            # ── Get or create the interval ─────────────────────────────────
            period_choice = {
                "minutes": IntervalSchedule.MINUTES,
                "seconds": IntervalSchedule.SECONDS,
                "hours":   IntervalSchedule.HOURS,
                "days":    IntervalSchedule.DAYS,
            }[period]
 
            schedule, _ = IntervalSchedule.objects.get_or_create(
                every=every,
                period=period_choice,
            )
 
            # ── Register the task ──────────────────────────────────────────
            task, created = PeriodicTask.objects.update_or_create(
                name=human_name,
                defaults={
                    "task":        task_name,
                    "interval":    schedule,
                    "enabled":     enabled,
                    "description": description,
                },
            )
 
            if created:
                created_count += 1
                self.stdout.write(
                    self.style.SUCCESS(f"  ✓ Created  [{every} {period}]  {human_name}")
                )
            else:
                updated_count += 1
                self.stdout.write(
                    self.style.WARNING(f"  ↺ Updated  [{every} {period}]  {human_name}")
                )
 
        status = "DISABLED" if not enabled else "ENABLED"
        self.stdout.write(
            self.style.SUCCESS(
                f"\nDone. {created_count} created, {updated_count} updated. "
                f"Tasks are {status}."
            )
        )
        if not enabled:
            self.stdout.write(
                "  Run without --disable to enable them."
            )
 
    def _list_tasks(self):
        from django_celery_beat.models import PeriodicTask
        tasks = PeriodicTask.objects.exclude(name="celery.backend_cleanup").order_by("name")
        if not tasks.exists():
            self.stdout.write(self.style.WARNING("No periodic tasks registered."))
            return
 
        self.stdout.write(f"\n{'Name':<45} {'Task':<40} {'Schedule':<20} {'Enabled'}")
        self.stdout.write("-" * 115)
        for t in tasks:
            schedule = str(t.interval) if t.interval else str(t.crontab)
            self.stdout.write(
                f"{t.name:<45} {t.task:<40} {schedule:<20} {'✓' if t.enabled else '✗'}"
            )
 
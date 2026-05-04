"""
discovery/management/commands/crawl_seeds.py

Run discovery synchronously from the command line — no Celery worker needed.
Useful for first-time setup, debugging, and CI pipelines.

Usage examples:

  # Run all seeds that are due (respects crawl_interval)
  python manage.py crawl_seeds

  # Force-run ALL active seeds regardless of schedule
  python manage.py crawl_seeds --all

  # Run specific seeds by ID
  python manage.py crawl_seeds --seed-ids 1 2 3

  # Only run seeds of a certain type
  python manage.py crawl_seeds --source-type rss

  # Dispatch to Celery instead of running in-process
  python manage.py crawl_seeds --async
"""
import logging

from django.core.management.base import BaseCommand
from django.utils import timezone

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Run the discovery layer for seed sources (synchronous by default)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--seed-ids",
            nargs="+",
            type=int,
            metavar="ID",
            help="Run only these seed IDs.",
        )
        parser.add_argument(
            "--all",
            action="store_true",
            help="Force-run ALL active seeds, ignoring crawl_interval.",
        )
        parser.add_argument(
            "--source-type",
            choices=["rss", "sitemap", "seed_url", "search_api", "manual"],
            help="Only run seeds of this source type.",
        )
        parser.add_argument(
            "--async",
            action="store_true",
            dest="use_async",
            help="Dispatch tasks to Celery instead of running in-process.",
        )

    def handle(self, *args, **options):
        from discovery.models import SeedSource
        from discovery.tasks import run_seed_discovery

        seed_ids    = options["seed_ids"]
        run_all     = options["all"]
        source_type = options["source_type"]
        use_async   = options["use_async"]

        # ── Build queryset ─────────────────────────────────────────────────
        qs = SeedSource.objects.filter(is_active=True)

        if source_type:
            qs = qs.filter(source_type=source_type)

        if seed_ids:
            qs = qs.filter(pk__in=seed_ids)
        elif not run_all:
            # Default: only seeds whose crawl_interval has elapsed
            due_pks = [s.pk for s in qs.exclude(crawl_interval=0) if s.is_due]
            qs = qs.filter(pk__in=due_pks)

        seeds = list(qs)

        if not seeds:
            self.stdout.write(self.style.WARNING("No seeds to run."))
            return

        mode = "async (Celery)" if use_async else "sync"
        self.stdout.write(
            self.style.SUCCESS(f"Running {len(seeds)} seed(s) [{mode}]...")
        )

        # ── Dispatch or run ────────────────────────────────────────────────
        for seed in seeds:
            self.stdout.write(f"\n  → [{seed.pk}] {seed.name}  ({seed.get_source_type_display()})")

            if use_async:
                task = run_seed_discovery.delay(seed.pk)
                self.stdout.write(f"    Dispatched task {task.id}")
            else:
                result = run_seed_discovery(seed.pk)
                status = result.get("status", "?")
                if status == "ok":
                    self.stdout.write(
                        self.style.SUCCESS(
                            f"    ✓ found={result['attempted']}  "
                            f"new={result['new']}  "
                            f"dupes={result['skipped_duplicate']}  "
                            f"filtered={result['skipped_keyword']}"
                        )
                    )
                else:
                    self.stdout.write(self.style.WARNING(f"    ⚠ status={status}"))

        self.stdout.write(self.style.SUCCESS("\nDone."))
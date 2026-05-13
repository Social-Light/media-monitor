"""
fetcher/management/commands/fetch_pending.py
 
Fetch and parse pending URLs from the command line.
No Celery worker required — runs synchronously in-process.
 
Usage examples:
 
  # Fetch all PENDING urls (default batch of 50)
  python manage.py fetch_pending
 
  # Fetch a specific number of URLs
  python manage.py fetch_pending --limit 10
 
  # Fetch a specific set of DiscoveredURL IDs
  python manage.py fetch_pending --ids 1 2 3
 
  # Re-fetch URLs regardless of current status (e.g. to re-parse)
  python manage.py fetch_pending --ids 5 --force
 
  # Dispatch to Celery instead of running in-process
  python manage.py fetch_pending --async
"""
import logging
 
from django.core.management.base import BaseCommand
 
from core.models import URLStatusChoices
from discovery.models import DiscoveredURL
from fetcher.services import fetch_and_parse
from fetcher.tasks import fetch_single_url
 
logger = logging.getLogger(__name__)
 
 
class Command(BaseCommand):
    help = "Fetch and parse pending DiscoveredURLs (synchronous by default)."
 
    def add_arguments(self, parser):
        parser.add_argument(
            "--limit",
            type=int,
            default=50,
            metavar="N",
            help="Maximum number of URLs to fetch (default: 50).",
        )
        parser.add_argument(
            "--ids",
            nargs="+",
            type=int,
            metavar="ID",
            help="Fetch specific DiscoveredURL IDs regardless of status.",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="Re-fetch URLs even if they are already FETCHED or PARSED.",
        )
        parser.add_argument(
            "--async",
            action="store_true",
            dest="use_async",
            help="Dispatch tasks to Celery instead of running in-process.",
        )
 
    def handle(self, *args, **options):
 
        limit     = options["limit"]
        ids       = options["ids"]
        force     = options["force"]
        use_async = options["use_async"]
 
        # ── Build queryset ─────────────────────────────────────────────────
        if ids:
            qs = DiscoveredURL.objects.filter(pk__in=ids)
            if not force:
                qs = qs.filter(status=URLStatusChoices.PENDING)
        else:
            qs = DiscoveredURL.objects.filter(
                status=URLStatusChoices.PENDING
            )[:limit]
 
        urls = list(qs)
 
        if not urls:
            self.stdout.write(self.style.WARNING("No URLs to fetch."))
            return
 
        mode = "async (Celery)" if use_async else "sync"
        self.stdout.write(
            self.style.SUCCESS(f"Fetching {len(urls)} URL(s) [{mode}]...\n")
        )
 
        ok = failed = skipped = 0
 
        for du in urls:
            short_url = du.url if len(du.url) <= 70 else du.url[:67] + "..."
            self.stdout.write(f"  → [{du.pk}] {short_url}")
 
            if use_async:
                fetch_single_url.delay(du.pk)
                self.stdout.write("    Dispatched")
                continue
 
            # ── Run synchronously ──────────────────────────────────────────
            result = fetch_and_parse(du)
            status = result.get("status")
 
            if status == "ok":
                ok += 1
                self.stdout.write(
                    self.style.SUCCESS(
                        f"    ✓ {result.get('title', '')[:60]}  "
                        f"({result.get('words', 0)} words)"
                    )
                )
            elif status == "fetch_failed":
                failed += 1
                du.refresh_from_db()
                self.stdout.write(
                    self.style.ERROR(
                        f"    ✗ fetch failed — {du.error_message[:80]}"
                    )
                )
            elif status == "parse_failed":
                failed += 1
                du.refresh_from_db()
                self.stdout.write(
                    self.style.ERROR(
                        f"    ✗ parse failed — {du.error_message[:80]}"
                    )
                )
            else:
                skipped += 1
                self.stdout.write(self.style.WARNING(f"    ⚠ {status}"))
 
        if not use_async:
            self.stdout.write(
                self.style.SUCCESS(
                    f"\nDone.  ok={ok}  failed={failed}  skipped={skipped}"
                )
            )
 
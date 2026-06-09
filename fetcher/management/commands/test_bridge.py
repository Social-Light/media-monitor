"""
fetcher/management/commands/test_bridge.py

Manually exercise the platform bridge: create (or reuse) a test organisation,
attach keywords, then run push_to_platform / push_competitor_to_platform over the
ParsedArticles already in the database and report what was created.

Usage:
  python manage.py test_bridge --org 'Test Org' --keyword 'test'
  python manage.py test_bridge --org 'Debswana' --keyword diamond --keyword mining
  python manage.py test_bridge --org 'Test Org' --keyword test --limit 50

Safe to re-run: organisation, keywords and platform articles are all created via
get_or_create / duplicate checks, so nothing is duplicated.
"""
from django.core.management.base import BaseCommand

from fetcher.bridge import push_competitor_to_platform, push_to_platform
from fetcher.models import ParsedArticle
from platform_sync.models import Keyword, Organization


class Command(BaseCommand):
    help = "Run the platform bridge against existing ParsedArticles for a test org."

    def add_arguments(self, parser):
        parser.add_argument("--org", default="Test Org", help="Organisation name (created if absent).")
        parser.add_argument(
            "--keyword", dest="keywords", action="append", metavar="KEYWORD",
            help="Keyword to attach to the org. Repeatable. Defaults to 'test'.",
        )
        parser.add_argument(
            "--limit", type=int, default=None,
            help="Only process the most recent N ParsedArticles.",
        )

    def handle(self, *args, **options):
        org_name = options["org"]
        keywords = options["keywords"] or ["test"]
        limit = options["limit"]

        # ── Organisation ────────────────────────────────────────────────────
        org, created = Organization.objects.get_or_create(
            name=org_name, defaults={"status": "active"},
        )
        if org.status != "active":
            org.status = "active"
            org.save(update_fields=["status"])
        self.stdout.write(self.style.SUCCESS(
            f"{'Created' if created else 'Using'} organisation '{org.name}' (id={org.id})"
        ))

        # ── Keywords ────────────────────────────────────────────────────────
        for kw in keywords:
            _, kw_created = Keyword.objects.get_or_create(organization=org, keyword=kw)
            self.stdout.write(f"  keyword '{kw}' {'added' if kw_created else 'already present'}")

        # ── Run the bridge over existing parsed articles ────────────────────
        qs = ParsedArticle.objects.filter(is_duplicate=False)
        if limit:
            qs = qs[:limit]

        total = qs.count() if limit is None else len(qs)
        if not total:
            self.stdout.write(self.style.WARNING(
                "No ParsedArticles found. Crawl some pages first (crawl_seeds / fetch_pending)."
            ))
            return

        self.stdout.write(f"\nRunning bridge over {total} parsed article(s)...\n")

        online_created, competitor_created, matched_articles = 0, 0, 0
        for art in qs:
            online = push_to_platform(art)
            competitor = push_competitor_to_platform(art)
            if online or competitor:
                matched_articles += 1
                self.stdout.write(
                    f"  ✓ {art.title[:70]}  "
                    f"(online={len(online)}, competitor={len(competitor)})"
                )
            online_created += len(online)
            competitor_created += len(competitor)

        self.stdout.write(self.style.SUCCESS("\nSummary:"))
        self.stdout.write(f"  Parsed articles scanned : {total}")
        self.stdout.write(f"  Articles that matched   : {matched_articles}")
        self.stdout.write(f"  OnlineArticles created  : {online_created}")
        self.stdout.write(f"  CompetitorArticles created: {competitor_created}")
        self.stdout.write(self.style.SUCCESS("Done."))

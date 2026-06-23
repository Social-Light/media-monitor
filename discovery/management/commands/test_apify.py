"""
discovery/management/commands/test_apify.py

Manually exercise the Apify social-discovery adapter: run a live keyword search
against one platform and print the normalised posts. Optionally ingest them
(create ParsedArticles + run the platform bridge) the same way a SOCIAL seed
would during a scheduled crawl.

Usage:
  python manage.py test_apify --platform x --terms "Debswana" --terms "diamonds"
  python manage.py test_apify --platform facebook --terms "Botswana mining" --max 20
  python manage.py test_apify --platform x --terms "Debswana" --seed 3 --ingest

Requires APIFY_API_TOKEN in the environment. Without it, fetch_mentions returns
an empty list (and this command says so) rather than erroring.
"""
from django.core.management.base import BaseCommand, CommandError

from discovery.models import SeedSource, SourceType
from discovery.services.apify import fetch_mentions
from fetcher.social_ingest import ingest_social_post


class Command(BaseCommand):
    help = "Run an Apify social search for keywords and optionally ingest the posts."

    def add_arguments(self, parser):
        parser.add_argument(
            "--platform", default="x",
            help="x | facebook | instagram | linkedin (default: x).",
        )
        parser.add_argument(
            "--terms", dest="terms", action="append", metavar="TERM",
            help="Keyword to search for. Repeatable. Required.",
        )
        parser.add_argument(
            "--max", type=int, default=10, help="Max posts to fetch (default: 10).",
        )
        parser.add_argument(
            "--ingest", action="store_true",
            help="Also ingest the posts (create ParsedArticles + run the bridge).",
        )
        parser.add_argument(
            "--seed", type=int, default=None,
            help="SeedSource id to attribute ingested posts to. Required with --ingest.",
        )

    def handle(self, *args, **options):
        # Social posts routinely contain emoji; the Windows console is cp1252 and
        # would crash on them. Degrade unprintable characters instead of dying.
        import sys
        try:
            sys.stdout.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass

        platform = options["platform"]
        terms = options["terms"]
        if not terms:
            raise CommandError("At least one --terms value is required.")

        self.stdout.write(f"Searching {platform} for {terms} (max {options['max']})...")
        posts = fetch_mentions(platform, terms, max_items=options["max"])

        if not posts:
            self.stdout.write(self.style.WARNING(
                "No posts returned. Check APIFY_API_TOKEN, the actor for this "
                "platform, and that the search terms have recent activity."
            ))
            return

        self.stdout.write(self.style.SUCCESS(f"\n{len(posts)} post(s):\n"))
        for p in posts:
            who = p.get("author") or p.get("author_handle") or "?"
            self.stdout.write(f"  - [{who}] {p.get('title', '')[:80]}")
            self.stdout.write(f"    {p.get('url', '')}")

        if not options["ingest"]:
            self.stdout.write("\n(Use --ingest --seed <id> to materialise these as ParsedArticles.)")
            return

        seed_id = options["seed"]
        if seed_id is None:
            raise CommandError("--ingest requires --seed <SeedSource id>.")
        try:
            seed = SeedSource.objects.get(pk=seed_id)
        except SeedSource.DoesNotExist:
            raise CommandError(f"SeedSource {seed_id} does not exist.")
        if seed.source_type != SourceType.SOCIAL:
            self.stdout.write(self.style.WARNING(
                f"Seed {seed_id} is '{seed.source_type}', not 'social' — ingesting anyway."
            ))

        self.stdout.write(f"\nIngesting {len(posts)} post(s) under seed '{seed.name}'...")
        new = skipped = 0
        for p in posts:
            article = ingest_social_post(seed, p)
            if article is None:
                skipped += 1
            else:
                new += 1

        self.stdout.write(self.style.SUCCESS(
            f"\nDone. Ingested {new} new, skipped {skipped} (already seen / no URL)."
        ))

"""
fetcher/management/commands/ingest_urls.py

Ingest a curated list of article URLs and attach them to ONE organisation as
platform coverage. This is the "Claude finds, server ingests" back-fill path:
when external search APIs aren't available, the article URLs are gathered
out-of-band (e.g. by a human or an assistant) and handed to this command, which
runs each through the normal fetch → parse pipeline and then force-writes an
OnlineArticle for the named org — regardless of the org's keyword state.

    python manage.py ingest_urls --org "Decode Afrika" \
        --url https://example.com/story-1 \
        --url https://example.com/story-2

    python manage.py ingest_urls --org "Decode Afrika" --urls-file urls.txt

    python manage.py ingest_urls --org "Decode Afrika" --urls-file urls.txt --dry-run

--urls-file is a plain text file, one URL per line (blank lines and lines
starting with '#' are ignored).

Why it force-writes (bypasses keyword matching): the URLs are already curated to
be about the org, so capture must not depend on the org's keywords appearing in
the body — a body-only or contaminated-keyword org would otherwise be silently
skipped. Relevancy is still scored normally. Dedup is on (organization, url), so
re-running never double-captures.
"""
from django.core.management.base import BaseCommand, CommandError

from core.models import URLStatusChoices
from discovery.models import DiscoveredURL, SeedSource, SourceType
from fetcher.bridge import push_article_for_org
from fetcher.models import ParsedArticle
from fetcher.services import fetch_and_parse
from platform_sync.models import Organization


class Command(BaseCommand):
    help = "Fetch a curated list of URLs and attach them to one organisation as OnlineArticles."

    def add_arguments(self, parser):
        parser.add_argument("--org", required=True, help="Platform organisation name (exact, case-insensitive).")
        parser.add_argument("--url", action="append", dest="urls", default=None, metavar="URL",
                            help="A URL to ingest. Repeatable.")
        parser.add_argument("--urls-file", default=None,
                            help="Path to a text file of URLs, one per line (# comments allowed).")
        parser.add_argument("--coverage", default="Earned",
                            choices=["Earned", "Incidental", "Advocated", "Not Set"],
                            help="Coverage type for the created OnlineArticles (default: Earned).")
        parser.add_argument("--dry-run", action="store_true",
                            help="List the URLs and resolved org, but fetch/write nothing.")

    def handle(self, *args, **options):
        org = self._resolve_org(options["org"])
        urls = self._collect_urls(options)
        if not urls:
            raise CommandError("No URLs given. Use --url (repeatable) and/or --urls-file.")

        self.stdout.write(
            f"Ingesting {len(urls)} URL(s) for org '{org.name}'"
            + (" [DRY RUN]" if options["dry_run"] else "")
        )

        if options["dry_run"]:
            for u in urls:
                self.stdout.write(f"  [would ingest] {u}")
            self.stdout.write(self.style.SUCCESS(f"\nDry run — nothing written. {len(urls)} URL(s) listed."))
            return

        seed = self._get_seed(org)
        captured = existing = failed = 0
        for url in urls:
            self.stdout.write(f"  {url}")
            self.stdout.flush()
            outcome = self._ingest_one(seed, org, url, options["coverage"])
            if outcome == "captured":
                captured += 1
            elif outcome == "existing":
                existing += 1
            else:
                failed += 1

        self.stdout.write(self.style.SUCCESS(
            f"\nDone. captured={captured} already_present={existing} failed={failed}"
        ))

    # ── Helpers ────────────────────────────────────────────────────────────────

    def _resolve_org(self, name: str) -> Organization:
        matches = list(Organization.objects.filter(name__iexact=name.strip()))
        if not matches:
            raise CommandError(f"No platform organisation named '{name}'.")
        if len(matches) > 1:
            raise CommandError(f"'{name}' is ambiguous — {len(matches)} organisations match.")
        return matches[0]

    def _collect_urls(self, options) -> list[str]:
        urls: list[str] = []
        seen = set()

        def add(raw):
            u = (raw or "").strip()
            if not u or u.startswith("#") or u in seen:
                return
            seen.add(u)
            urls.append(u)

        for u in (options["urls"] or []):
            add(u)
        if options["urls_file"]:
            try:
                with open(options["urls_file"], encoding="utf-8") as fh:
                    for line in fh:
                        add(line)
            except OSError as exc:
                raise CommandError(f"Could not read --urls-file: {exc}")
        return urls

    def _get_seed(self, org: Organization) -> SeedSource:
        """A dedicated inactive seed the curated URLs are attributed to."""
        seed, _ = SeedSource.objects.get_or_create(
            url=f"backfill://org/{org.id}",
            defaults={
                "name":           f"Backfill: {org.name}",
                "source_type":    SourceType.MANUAL,
                "is_active":      False,
                "crawl_interval": 0,
            },
        )
        return seed

    def _ingest_one(self, seed, org, url, coverage) -> str:
        """
        Returns "captured" | "existing" | "failed".

        Fetches + parses the URL (reusing an existing parse if the URL was already
        crawled), then force-writes an OnlineArticle for `org`.
        """
        url_hash = DiscoveredURL.hash_url(url)
        discovered = DiscoveredURL.objects.filter(url_hash=url_hash).first()

        if discovered is None:
            discovered = DiscoveredURL.objects.create(
                seed=seed, url=url, status=URLStatusChoices.PENDING,
            )

        article = ParsedArticle.objects.filter(fetched_page__discovered_url=discovered).first()
        if article is None:
            # Not parsed yet (new, or a prior fetch failed) — run the pipeline.
            result = fetch_and_parse(discovered)
            if result.get("status") != "ok":
                self.stdout.write(self.style.WARNING(f"      fetch/parse failed: {result.get('status')}"))
                return "failed"
            article = ParsedArticle.objects.filter(fetched_page__discovered_url=discovered).first()
            if article is None:
                self.stdout.write(self.style.WARNING("      no parsed article produced"))
                return "failed"

        try:
            created = push_article_for_org(article, org, coverage=coverage)
        except Exception as exc:  # bridge must never abort the batch
            self.stdout.write(self.style.WARNING(f"      bridge error: {exc}"))
            return "failed"

        if created is None:
            self.stdout.write("      already present for this org")
            return "existing"
        self.stdout.write(self.style.SUCCESS(f"      captured: {article.title[:70]}"))
        return "captured"

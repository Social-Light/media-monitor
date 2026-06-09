"""
discovery/management/commands/import_sources.py

Bulk-load SeedSource and Organisation records from a CSV file.

Two CSV shapes are supported, resolved by header name:

  1. The documented import schema:
       name, url, source_type, organisation, keywords, crawl_interval

  2. The media-source export schema (sociallight_export_mediasource):
       name, type, domain, reach, country, _id, handle, logo_url, ...
     Here `domain` holds the URL and `type` is the media type. Only rows
     whose type is "online" are imported — social/print/etc. are skipped.

Column resolution per row:
  name           required
  url            falls back to the `domain` column
  organisation   falls back to `name` (each source is its own org)
  source_type    validated against SourceType; defaults to seed_url
  keywords       optional, comma-separated
  crawl_interval optional minutes; defaults to 60
  type           optional media-type filter — when present only "online" passes

For each accepted row the command:
  a. get_or_create Organisation by name
  b. get_or_create SeedSource by url
  c. sets SeedSource.keyword_filter from the keywords column
  d. get_or_create an OrganisationKeyword for every keyword
  e. get_or_create an OrganisationSource for the URL's domain

Usage:
  python manage.py import_sources sources.csv
  python manage.py import_sources sources.csv --dry-run
  python manage.py import_sources sources.csv --clear-existing
"""
import csv
from urllib.parse import urlparse

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from discovery.models import SeedSource, SourceType
from matching.models import Organisation, OrganisationKeyword, OrganisationSource

VALID_SOURCE_TYPES = {value for value, _ in SourceType.choices}
DEFAULT_SOURCE_TYPE = SourceType.SEED_URL  # export rows are landing pages → link extraction
DEFAULT_INTERVAL = 60


def extract_domain(url: str) -> str:
    """Return the bare host of a URL, tolerating a missing scheme."""
    netloc = urlparse(url).netloc
    if not netloc:  # e.g. "www.example.com/news" with no scheme
        netloc = urlparse("//" + url).netloc
    return netloc.strip().lower()


def split_keywords(raw: str) -> list[str]:
    """Split a comma-separated string into cleaned, case-insensitively unique terms."""
    seen, result = set(), []
    for kw in (raw or "").split(","):
        kw = kw.strip()
        key = kw.lower()
        if kw and key not in seen:
            seen.add(key)
            result.append(kw)
    return result


class Command(BaseCommand):
    help = "Import SeedSource + Organisation records from a CSV file."

    def add_arguments(self, parser):
        parser.add_argument("csv_path", help="Path to the CSV file to import.")
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Parse and report what would happen without writing to the database.",
        )
        parser.add_argument(
            "--clear-existing",
            action="store_true",
            help="Delete ALL SeedSource and Organisation records before importing "
                 "(cascades to discovered URLs, pages, articles, keywords, sources, matches).",
        )

    def handle(self, *args, **options):
        csv_path = options["csv_path"]
        dry_run = options["dry_run"]
        clear_existing = options["clear_existing"]

        # ── Read the file ──────────────────────────────────────────────────────
        try:
            with open(csv_path, newline="", encoding="utf-8-sig") as fh:
                reader = csv.DictReader(fh)
                fieldnames = [(f or "").strip() for f in (reader.fieldnames or [])]
                rows = list(reader)
        except FileNotFoundError:
            raise CommandError(f"CSV file not found: {csv_path}")

        if "name" not in fieldnames:
            raise CommandError("CSV must have a header row including a 'name' column.")
        if "url" not in fieldnames and "domain" not in fieldnames:
            raise CommandError("CSV must have either a 'url' or a 'domain' column.")

        stats = {
            "rows": len(rows),
            "orgs_created": 0,
            "seeds_created": 0,
            "seeds_updated": 0,
            "keywords_created": 0,
            "sources_created": 0,
            "skipped_not_online": 0,
            "skipped_invalid": 0,
        }
        warnings = []

        if dry_run:
            self.stdout.write(self.style.WARNING("DRY RUN — no changes will be saved.\n"))

        # One transaction: a mid-file error rolls everything back. In --dry-run we
        # do the real work for accurate counts, then roll the whole thing back.
        try:
            with transaction.atomic():
                if clear_existing:
                    seed_n = SeedSource.objects.count()
                    org_n = Organisation.objects.count()
                    SeedSource.objects.all().delete()
                    Organisation.objects.all().delete()  # cascades keywords + sources
                    verb = "Would clear" if dry_run else "Cleared"
                    self.stdout.write(self.style.WARNING(
                        f"{verb} {seed_n} seed source(s) and {org_n} organisation(s).\n"
                    ))

                for i, row in enumerate(rows, start=2):  # row 1 is the header
                    self._process_row(row, i, stats, warnings)

                if dry_run:
                    transaction.set_rollback(True)
        except CommandError:
            raise
        except Exception as exc:  # pragma: no cover - defensive
            raise CommandError(f"Import failed, rolled back: {exc}")

        # ── Report ─────────────────────────────────────────────────────────────
        for w in warnings:
            self.stdout.write(self.style.WARNING(w))

        verb = "Would import" if dry_run else "Imported"
        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(f"{verb} from {stats['rows']} data row(s):"))
        self.stdout.write(f"  Organisations created: {stats['orgs_created']}")
        self.stdout.write(f"  Seed sources created : {stats['seeds_created']}")
        self.stdout.write(f"  Seed sources updated : {stats['seeds_updated']}")
        self.stdout.write(f"  Keywords created     : {stats['keywords_created']}")
        self.stdout.write(f"  Org sources created  : {stats['sources_created']}")
        self.stdout.write(f"  Skipped (not online) : {stats['skipped_not_online']}")
        self.stdout.write(f"  Skipped (invalid)    : {stats['skipped_invalid']}")
        self.stdout.write(self.style.SUCCESS("Dry run complete." if dry_run else "Done."))

    # ── Per-row processing ──────────────────────────────────────────────────────

    def _process_row(self, row: dict, line_no: int, stats: dict, warnings: list):
        name = (row.get("name") or "").strip()
        url = (row.get("url") or row.get("domain") or "").strip()

        # Media-type filter: when a `type` column exists, only "online" passes.
        media_type = (row.get("type") or "").strip().lower()
        if media_type and media_type != "online":
            stats["skipped_not_online"] += 1
            return

        if not name or not url:
            stats["skipped_invalid"] += 1
            warnings.append(f"Row {line_no}: missing name or url — skipped.")
            return

        org_name = (row.get("organisation") or "").strip() or name

        source_type = (row.get("source_type") or "").strip().lower()
        if source_type not in VALID_SOURCE_TYPES:
            if source_type:
                warnings.append(
                    f"Row {line_no}: unknown source_type '{source_type}' — "
                    f"using '{DEFAULT_SOURCE_TYPE}'."
                )
            source_type = DEFAULT_SOURCE_TYPE

        interval_raw = (row.get("crawl_interval") or "").strip()
        try:
            crawl_interval = int(interval_raw) if interval_raw else DEFAULT_INTERVAL
            if crawl_interval < 0:
                raise ValueError
        except ValueError:
            warnings.append(
                f"Row {line_no}: invalid crawl_interval '{interval_raw}' — using {DEFAULT_INTERVAL}."
            )
            crawl_interval = DEFAULT_INTERVAL

        keywords = split_keywords(row.get("keywords", ""))
        keyword_filter = ", ".join(keywords)

        # a. Organisation
        org, org_created = Organisation.objects.get_or_create(name=org_name)
        stats["orgs_created"] += int(org_created)

        # b. SeedSource (by unique url) + c. keyword_filter
        seed, seed_created = SeedSource.objects.get_or_create(
            url=url,
            defaults={
                "name": name,
                "source_type": source_type,
                "crawl_interval": crawl_interval,
                "keyword_filter": keyword_filter,
            },
        )
        if seed_created:
            stats["seeds_created"] += 1
        else:
            changed = []
            if seed.name != name:
                seed.name = name
                changed.append("name")
            if keyword_filter and seed.keyword_filter != keyword_filter:
                seed.keyword_filter = keyword_filter
                changed.append("keyword_filter")
            if changed:
                seed.save(update_fields=changed)
                stats["seeds_updated"] += 1

        # d. OrganisationKeyword per keyword
        for kw in keywords:
            _, kw_created = OrganisationKeyword.objects.get_or_create(
                organisation=org, keyword=kw,
            )
            stats["keywords_created"] += int(kw_created)

        # e. OrganisationSource for the URL's domain
        domain = extract_domain(url)
        if domain:
            _, src_created = OrganisationSource.objects.get_or_create(
                organisation=org, domain=domain,
            )
            stats["sources_created"] += int(src_created)

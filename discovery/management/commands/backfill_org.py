"""
discovery/management/commands/backfill_org.py

Historical back-search for a single organisation. Given a start date, an end
date, and an organisation name, this searches the org's own keywords across news
and/or social sources over that window and ingests every hit through the SAME
pipeline a scheduled crawl uses — so back-filled coverage is deduped, scored, and
pushed to the platform exactly like live captures.

    python manage.py backfill_org --org "Debswana" --start 2026-01-01 --end 2026-03-31
    python manage.py backfill_org --org "Debswana" --start 2026-01-01 --source news
    python manage.py backfill_org --org "Debswana" --start 2026-01-01 --source social \
        --platforms x --platforms linkedin
    python manage.py backfill_org --org "Debswana" --start 2026-01-01 --dry-run

The organisation is a platform organisation (platform_sync.Organization); its
keywords drive the search, so the search set matches what the bridge captures.

Date-range support differs by source (best-effort, then always client-side
filtered by published date):
  • news / google  — TRUE absolute range (Google CSE sort=date:r:START:END).
  • news / bing    — relative freshness only; can't reach far back. Warns.
  • social / x     — absolute start/end passed to the tweet-scraper actor.
  • social / other — no absolute filter in the actor; relies on client-side
                     date filtering of returned posts. Warns.

Requires the relevant API keys (GOOGLE_NEWS_API_KEY + GOOGLE_CSE_ID / BING_API_KEY
for news, APIFY_API_TOKEN for social). Missing keys yield empty results, not
errors — the same graceful degradation as the discovery services.
"""
from datetime import datetime, date as date_cls

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from core.models import URLStatusChoices
from discovery.models import DiscoveredURL, SeedSource, SourceType
from discovery.services import search
from discovery.services.apify import fetch_mentions
from fetcher.services import fetch_and_parse
from fetcher.social_ingest import ingest_social_post
from platform_sync.models import Organization

SOCIAL_PLATFORMS = ("x", "facebook", "instagram", "linkedin")


# ── Module-level helpers (unit-tested) ─────────────────────────────────────────

def parse_date_arg(raw: str) -> date_cls:
    """Parse a YYYY-MM-DD CLI argument into a date, or raise CommandError."""
    try:
        return datetime.strptime(raw.strip(), "%Y-%m-%d").date()
    except (ValueError, AttributeError):
        raise CommandError(f"Invalid date '{raw}'. Use YYYY-MM-DD.")


def within_range(published_at, start: date_cls, end: date_cls) -> bool:
    """
    True if `published_at` falls within [start, end] inclusive.

    Undated items (published_at is None) are KEPT — many search/social results
    carry no reliable publish date, and dropping them would silently lose
    coverage. The provider-side date filter is the primary bound; this is a
    best-effort secondary guard for items that do carry a date.
    """
    if published_at is None:
        return True
    d = published_at.date() if hasattr(published_at, "date") else published_at
    return start <= d <= end


def social_date_input(platform: str, start: date_cls, end: date_cls) -> dict:
    """
    Actor-input overrides that constrain a social search to [start, end], for the
    actors that support an absolute range. Returns {} for actors that don't (the
    caller still client-side filters by published date).
    """
    if platform == "x":
        # apidojo/tweet-scraper accepts absolute start/end (YYYY-MM-DD).
        return {"start": f"{start:%Y-%m-%d}", "end": f"{end:%Y-%m-%d}"}
    return {}


# ── Command ────────────────────────────────────────────────────────────────────

class Command(BaseCommand):
    help = "Back-search a single organisation's keywords over a date range and ingest the hits."

    def add_arguments(self, parser):
        parser.add_argument("--org", required=True, help="Platform organisation name (exact, case-insensitive).")
        parser.add_argument("--start", required=True, help="Start date, YYYY-MM-DD.")
        parser.add_argument("--end", default=None, help="End date, YYYY-MM-DD (default: today).")
        parser.add_argument(
            "--source", choices=["news", "social", "both"], default="both",
            help="Which sources to back-search (default: both).",
        )
        parser.add_argument(
            "--provider", choices=["google", "bing"], default="google",
            help="News search provider. google supports absolute date ranges; bing does not (default: google).",
        )
        parser.add_argument(
            "--platforms", action="append", choices=list(SOCIAL_PLATFORMS), default=None,
            help="Social platform(s) to search. Repeatable. Default: all four.",
        )
        parser.add_argument(
            "--category", action="append", default=None,
            help="Keyword category to include (brand | personnel | campaign). Repeatable. Default: all.",
        )
        parser.add_argument("--max", type=int, default=20, help="Max results per keyword search (default: 20).")
        parser.add_argument(
            "--dry-run", action="store_true",
            help="Search and report counts only — ingest nothing.",
        )

    def handle(self, *args, **options):
        # Social posts routinely contain emoji; the Windows console is cp1252 and
        # would crash on them. Degrade unprintable characters instead of dying.
        import sys
        try:
            sys.stdout.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass

        start = parse_date_arg(options["start"])
        end   = parse_date_arg(options["end"]) if options["end"] else timezone.now().date()
        if end < start:
            raise CommandError(f"--end ({end}) is before --start ({start}).")

        org = self._resolve_org(options["org"])
        keywords = self._org_keywords(org, options["category"])
        if not keywords:
            raise CommandError(f"Organisation '{org.name}' has no matching keywords to search.")

        self.stdout.write(
            f"Back-searching '{org.name}' — {len(keywords)} keyword(s), "
            f"{start} → {end}, source={options['source']}"
            + (" [DRY RUN]" if options["dry_run"] else "")
        )
        self.stdout.write(f"  keywords: {', '.join(keywords)}")

        seed = self._get_backfill_seed(org)
        source = options["source"]
        totals = {"found": 0, "new": 0, "skipped_existing": 0, "skipped_out_of_range": 0}

        if source in ("news", "both"):
            self._backfill_news(seed, keywords, start, end, options, totals)
        if source in ("social", "both"):
            self._backfill_social(seed, keywords, start, end, options, totals)

        self.stdout.write(self.style.SUCCESS(
            f"\nDone. found={totals['found']} new={totals['new']} "
            f"existing={totals['skipped_existing']} out_of_range={totals['skipped_out_of_range']}"
            + (" (nothing ingested — dry run)" if options["dry_run"] else "")
        ))

    # ── Resolution helpers ────────────────────────────────────────────────────

    def _resolve_org(self, name: str) -> Organization:
        matches = list(Organization.objects.filter(name__iexact=name.strip()))
        if not matches:
            raise CommandError(f"No platform organisation named '{name}'.")
        if len(matches) > 1:
            raise CommandError(f"'{name}' is ambiguous — {len(matches)} organisations match.")
        return matches[0]

    def _org_keywords(self, org: Organization, categories) -> list[str]:
        """Distinct keywords of the org, de-duplicated case-insensitively."""
        cats = {c.strip().lower() for c in categories} if categories else None
        terms, seen = [], set()
        for kw in org.keywords.all():
            if cats is not None and (kw.category or "").lower() not in cats:
                continue
            term = (kw.keyword or "").strip()
            if term and term.lower() not in seen:
                seen.add(term.lower())
                terms.append(term)
        return terms

    def _get_backfill_seed(self, org: Organization) -> SeedSource:
        """
        A dedicated, inactive seed that back-filled items are attributed to. It is
        is_active=False and crawl_interval=0 so the scheduler never touches it —
        it exists only as the FK parent for back-fill DiscoveredURLs / posts.
        """
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

    # ── News ──────────────────────────────────────────────────────────────────

    def _backfill_news(self, seed, keywords, start, end, options, totals):
        provider = options["provider"]
        if provider == "bing":
            self.stdout.write(self.style.WARNING(
                "  news/bing: Bing News supports relative freshness only — it cannot "
                "reach an arbitrary historical window. Use --provider google for real back-fills."
            ))

        self.stdout.write(f"\nNews back-search via {provider}...")
        seen_hashes = set()
        for kw in keywords:
            kwargs = {"count": options["max"]}
            if provider == "google":
                kwargs["date_range"] = (start, end)
            items = search(query=kw, provider=provider, **kwargs)
            for item in items:
                url = (item.get("url") or "").strip()
                if not url:
                    continue
                if not within_range(item.get("published_at"), start, end):
                    totals["skipped_out_of_range"] += 1
                    continue
                url_hash = DiscoveredURL.hash_url(url)
                if url_hash in seen_hashes:
                    continue
                seen_hashes.add(url_hash)
                totals["found"] += 1
                self._ingest_news_url(seed, item, url_hash, options["dry_run"], totals)

    def _ingest_news_url(self, seed, item, url_hash, dry_run, totals):
        url = item["url"].strip()
        if DiscoveredURL.objects.filter(url_hash=url_hash).exists():
            totals["skipped_existing"] += 1
            return
        if dry_run:
            self.stdout.write(f"  [would fetch] {url}")
            return
        discovered = DiscoveredURL.objects.create(
            seed         = seed,
            url          = url,
            title        = (item.get("title") or "")[:512],
            snippet      = item.get("snippet") or "",
            published_at = item.get("published_at"),
            status       = URLStatusChoices.PENDING,
        )
        result = fetch_and_parse(discovered)
        if result.get("status") == "ok":
            totals["new"] += 1
            self.stdout.write(f"  [ok] {result.get('title', '')[:70]}")
        else:
            self.stdout.write(self.style.WARNING(f"  [{result.get('status')}] {url}"))

    # ── Social ─────────────────────────────────────────────────────────────────

    def _backfill_social(self, seed, keywords, start, end, options, totals):
        platforms = options["platforms"] or list(SOCIAL_PLATFORMS)
        self.stdout.write(f"\nSocial back-search: {', '.join(platforms)}...")
        for platform in platforms:
            actor_input = social_date_input(platform, start, end)
            if not actor_input:
                self.stdout.write(self.style.WARNING(
                    f"  {platform}: actor has no absolute date filter — "
                    "results are client-side filtered by publish date (older posts may be unavailable)."
                ))
            for kw in keywords:
                posts = fetch_mentions(
                    platform, [kw], max_items=options["max"],
                    actor_input=actor_input or None,
                )
                for post in posts:
                    if not within_range(post.get("published_at"), start, end):
                        totals["skipped_out_of_range"] += 1
                        continue
                    totals["found"] += 1
                    if options["dry_run"]:
                        self.stdout.write(f"  [would ingest] [{platform}] {post.get('title', '')[:60]}")
                        continue
                    article = ingest_social_post(seed, post)
                    if article is None:
                        totals["skipped_existing"] += 1
                    else:
                        totals["new"] += 1
                        self.stdout.write(f"  [ok] [{platform}] {post.get('title', '')[:60]}")

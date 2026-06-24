"""
discovery/tasks.py
───────────────────
Celery tasks for the Discovery layer.

Task hierarchy:
    run_all_seeds          ← Periodic beat task. Scans all active seeds,
        │                    dispatches one run_seed_discovery per due seed.
        │
        └─ run_seed_discovery  ← Processes a single SeedSource:
                                  1. Calls run_discovery(seed)
                                  2. Applies keyword filtering
                                  3. Bulk-inserts new DiscoveredURLs
                                  4. Stamps seed.last_crawled_at

Schedule (set up via Django admin → Periodic Tasks):
    Task name                    Suggested interval
    discovery.run_all_seeds      Every 5 minutes
"""
import logging
from datetime import timedelta

from celery import shared_task
from django.utils import timezone

from core.models import URLStatusChoices
from discovery.models import DiscoveredURL, SeedSource, SourceType
from discovery.services import run_discovery
from fetcher.social_ingest import ingest_social_post

logger = logging.getLogger(__name__)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _matches_keywords(item: dict, keywords: list[str]) -> bool:
    """
    Return True when no keyword filter is set, or when at least one
    keyword appears in the URL, title, or snippet.
    """
    if not keywords:
        return True
    haystack = " ".join([
        item.get("url",     ""),
        item.get("title",   ""),
        item.get("snippet", ""),
    ]).lower()
    return any(kw in haystack for kw in keywords)


def _store_discovered_urls(seed: SeedSource, items: list[dict]) -> dict:
    """
    Bulk-insert DiscoveredURL records for a list of raw article dicts.

    Uses bulk_create(ignore_conflicts=True) so re-running the same seed
    never creates duplicates — the unique constraint on url_hash handles it
    silently at the database level.

    Returns a summary dict: {attempted, new, skipped_duplicate, skipped_keyword}
    """
    keywords           = seed.keywords
    to_create          = []
    seen_hashes        = set()
    skipped_keyword    = 0
    skipped_duplicate  = 0

    for item in items:
        url = item.get("url", "").strip()
        if not url:
            continue

        # Keyword filter
        if not _matches_keywords(item, keywords):
            skipped_keyword += 1
            continue

        url_hash = DiscoveredURL.hash_url(url)

        # In-batch dedup (before hitting the DB)
        if url_hash in seen_hashes:
            skipped_duplicate += 1
            continue
        seen_hashes.add(url_hash)

        to_create.append(DiscoveredURL(
            seed         = seed,
            url          = url,
            url_hash     = url_hash,
            title        = item.get("title",        "")[:512],
            snippet      = item.get("snippet",      ""),
            published_at = item.get("published_at"),
            status       = URLStatusChoices.PENDING,
        ))

    # bulk_create with ignore_conflicts silently skips rows whose url_hash
    # already exists in the DB (i.e. previously discovered URLs).
    # Note: SQLite does not return only newly inserted rows, so we count
    # records before and after to get an accurate new-insertion count.
    count_before = DiscoveredURL.objects.filter(
        url_hash__in=[d.url_hash for d in to_create]
    ).count() if to_create else 0

    if to_create:
        DiscoveredURL.objects.bulk_create(to_create, ignore_conflicts=True)

    count_after = DiscoveredURL.objects.filter(
        url_hash__in=[d.url_hash for d in to_create]
    ).count() if to_create else 0

    actually_new = count_after - count_before

    return {
        "attempted":          len(items),
        "new":                actually_new,
        "skipped_duplicate":  skipped_duplicate + (len(to_create) - actually_new),
        "skipped_keyword":    skipped_keyword,
    }


def _ingest_social_posts(seed: SeedSource, items: list[dict]) -> dict:
    """
    Ingest social posts (from a SOCIAL seed) directly into ParsedArticles.

    Social posts arrive fully-formed from Apify, so they skip the fetch/parse
    pipeline entirely — each is materialised via fetcher.social_ingest, which
    also runs dedup, indexing, alerts, matching, and the platform bridge.

    The seed's keyword_filter narrows a broad platform search — EXCEPT for
    org-driven seeds (meta {"from_orgs": true}), where the search already targets
    the active orgs' keywords and capture is gated by the bridge; applying the
    seed filter there would wrongly drop posts found via other org keywords.
    Returns the same summary shape as _store_discovered_urls so the task result
    is uniform across source types.
    """
    from_orgs       = bool((seed.meta or {}).get("from_orgs"))
    keywords        = [] if from_orgs else seed.keywords
    new             = 0
    skipped_keyword = 0
    skipped_existing = 0

    for item in items:
        if not _matches_keywords(item, keywords):
            skipped_keyword += 1
            continue

        article = ingest_social_post(seed, item)
        if article is None:
            skipped_existing += 1
        else:
            new += 1

    return {
        "attempted":         len(items),
        "new":               new,
        "skipped_duplicate": skipped_existing,
        "skipped_keyword":   skipped_keyword,
    }


# ── Task 1 — single seed ──────────────────────────────────────────────────────

@shared_task(
    bind=True,
    name="discovery.run_seed_discovery",
    max_retries=3,
    default_retry_delay=60,   # seconds between retries
)
def run_seed_discovery(self, seed_id: int) -> dict:
    """
    Run the full discovery pipeline for one SeedSource.

    Steps:
      1. Load the SeedSource (bail early if missing or inactive)
      2. Call run_discovery(seed) → list of raw article dicts
      3. Filter by keywords, dedup, bulk-insert DiscoveredURLs
      4. Stamp seed.last_crawled_at

    Args:
        seed_id: Primary key of the SeedSource to crawl.

    Returns:
        Summary dict: {seed_id, seed_name, attempted, new,
                       skipped_duplicate, skipped_keyword, status}
    """

    # ── Load seed ──────────────────────────────────────────────────────────
    try:
        seed = SeedSource.objects.get(pk=seed_id, is_active=True)
    except SeedSource.DoesNotExist:
        logger.warning("run_seed_discovery: SeedSource %d not found or inactive", seed_id)
        return {"seed_id": seed_id, "status": "skipped_inactive"}

    logger.info("run_seed_discovery START seed=%d (%s)", seed_id, seed.name)

    # ── Discover ───────────────────────────────────────────────────────────
    try:
        items = run_discovery(seed)
    except Exception as exc:
        logger.exception("run_seed_discovery: discovery failed for seed %d", seed_id)
        raise self.retry(exc=exc)

    # ── Store ──────────────────────────────────────────────────────────────
    # Social posts arrive fully-formed and are materialised directly into
    # ParsedArticles; every other source type stores PENDING DiscoveredURLs for
    # the fetcher to pick up.
    if seed.source_type == SourceType.SOCIAL:
        summary = _ingest_social_posts(seed, items)
    else:
        summary = _store_discovered_urls(seed, items)

    # ── Stamp ──────────────────────────────────────────────────────────────
    seed.mark_crawled()

    result = {
        "seed_id":           seed_id,
        "seed_name":         seed.name,
        "status":            "ok",
        **summary,
    }
    logger.info("run_seed_discovery END seed=%d — %s", seed_id, result)
    return result


# ── Task 2 — all seeds ────────────────────────────────────────────────────────

@shared_task(name="discovery.run_all_seeds")
def run_all_seeds() -> dict:
    """
    Periodic task that fans out run_seed_discovery for every active seed
    that is due for a crawl.

    Meant to run every 5 minutes via django-celery-beat.
    Each individual seed controls its own cadence via crawl_interval.

    Returns:
        {"dispatched": [seed_ids ...], "total": N}
    """
    dispatched = []

    for seed in SeedSource.objects.filter(is_active=True).exclude(crawl_interval=0):
        if seed.is_due:
            run_seed_discovery.delay(seed.pk)
            dispatched.append(seed.pk)
            logger.info("run_all_seeds: dispatched seed=%d (%s)", seed.pk, seed.name)

    logger.info("run_all_seeds: %d task(s) dispatched", len(dispatched))
    return {"dispatched": dispatched, "total": len(dispatched)}
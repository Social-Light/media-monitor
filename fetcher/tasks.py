"""
fetcher/tasks.py
─────────────────
Two Celery tasks for the Fetcher layer.

Task hierarchy:
    fetch_pending_urls          ← Periodic beat task.
        │                         Scans for PENDING DiscoveredURLs,
        │                         dispatches one fetch_single_url per URL.
        │
        └─ fetch_single_url     ← Fetches and parses one DiscoveredURL.
                                  Calls fetch_and_parse() from services.py.
                                  Retries up to 3× on unexpected exceptions.

Schedule (set via Django admin → Periodic Tasks):
    Task name                    Suggested interval
    fetcher.fetch_pending_urls   Every 2 minutes

Flow in context of the full pipeline:
    discovery.run_all_seeds
        └─ DiscoveredURL (status=PENDING)
               └─ fetcher.fetch_pending_urls   ← picks these up
                      └─ fetch_single_url
                             └─ FetchedPage + ParsedArticle (status=PARSED)
"""
import logging

from celery import shared_task

from core.models import URLStatusChoices
from discovery.models import DiscoveredURL
from fetcher.services import fetch_and_parse

logger = logging.getLogger(__name__)

# Maximum number of URLs to queue per beat tick.
# Keeps each beat tick fast — the worker pool handles the actual parallelism.
DEFAULT_BATCH_SIZE = 50


# ── Task 1 — single URL ───────────────────────────────────────────────────────

@shared_task(
    bind=True,
    name="fetcher.fetch_single_url",
    max_retries=3,
    default_retry_delay=30,    # seconds between retries
)
def fetch_single_url(self, discovered_url_id: int) -> dict:
    """
    Fetch and parse a single DiscoveredURL by primary key.

    Steps:
      1. Load the DiscoveredURL — bail if it no longer exists
      2. Call fetch_and_parse() which handles the HTTP request,
         stores FetchedPage, parses with newspaper3k, stores ParsedArticle
      3. Return a result dict for logging / task history

    Args:
        discovered_url_id: Primary key of the DiscoveredURL to process.

    Returns:
        Result dict: {id, url, status, title?, words?}
    """
    # ── Load ──────────────────────────────────────────────────────────────────
    try:
        du = DiscoveredURL.objects.get(pk=discovered_url_id)
    except DiscoveredURL.DoesNotExist:
        logger.warning("fetch_single_url: DiscoveredURL %d not found", discovered_url_id)
        return {"id": discovered_url_id, "status": "not_found"}

    logger.info("fetch_single_url START id=%d url=%s", du.pk, du.url)

    # ── Fetch + Parse ─────────────────────────────────────────────────────────
    try:
        result = fetch_and_parse(du)
    except Exception as exc:
        # Unexpected error — retry up to max_retries times before giving up
        logger.exception(
            "fetch_single_url: unexpected error for id=%d url=%s",
            du.pk, du.url,
        )
        raise self.retry(exc=exc)

    result["id"] = du.pk
    logger.info("fetch_single_url END id=%d status=%s", du.pk, result.get("status"))
    return result


# ── Task 2 — batch dispatcher ─────────────────────────────────────────────────

@shared_task(name="fetcher.fetch_pending_urls")
def fetch_pending_urls(batch_size: int = DEFAULT_BATCH_SIZE) -> dict:
    """
    Periodic task that picks up PENDING DiscoveredURLs and dispatches
    a fetch_single_url task for each one.

    Only PENDING URLs are targeted — FETCHING, FETCHED, PARSED, FAILED,
    and SKIPPED are all intentionally ignored.

    Args:
        batch_size: Max number of URLs to dispatch per run. Defaults to 50.
                    Pass a smaller value in tests or on resource-limited hosts.

    Returns:
        {"dispatched": [id, ...], "total": N}
    """
    pending_ids = list(
        DiscoveredURL.objects
        .filter(status=URLStatusChoices.PENDING)
        .values_list("pk", flat=True)[:batch_size]
    )

    for pk in pending_ids:
        fetch_single_url.delay(pk)

    logger.info(
        "fetch_pending_urls: dispatched %d task(s) (batch_size=%d)",
        len(pending_ids), batch_size,
    )
    return {"dispatched": pending_ids, "total": len(pending_ids)}
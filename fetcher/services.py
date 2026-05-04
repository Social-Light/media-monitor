"""
fetcher/services.py
────────────────────
Two responsibilities:

  fetch_page(discovered_url)
      → Makes the HTTP GET request
      → Stores a FetchedPage
      → Updates DiscoveredURL.status to FETCHED or FAILED

  parse_page(fetched_page)
      → Runs newspaper3k on the stored raw HTML
      → Extracts title, body, author, date, language, tags
      → Stores a ParsedArticle
      → Updates DiscoveredURL.status to PARSED or FAILED

  fetch_and_parse(discovered_url)
      → Runs both steps in sequence
      → Returns a result dict the Celery task uses for logging

Design notes:
  - fetch_page and parse_page are deliberately separate so we can:
      a) Re-parse stored HTML without a new network request
      b) Test each step independently with mocks
  - Neither function raises — errors are caught, logged, and written
    to the model so the pipeline keeps running for other URLs.
"""
import logging
import time
from urllib.parse import urlparse

import newspaper
from django.utils import timezone

from core.models import URLStatusChoices
from core.utils import get_session
from discovery.models import DiscoveredURL

logger = logging.getLogger(__name__)


# ── Step 1: Fetch ─────────────────────────────────────────────────────────────

def fetch_page(discovered_url: DiscoveredURL):
    """
    Perform the HTTP GET for a DiscoveredURL and persist a FetchedPage.

    The DiscoveredURL status is updated to FETCHING while in progress,
    then to FETCHED on success or FAILED on any error.

    Args:
        discovered_url: The DiscoveredURL instance to fetch.

    Returns:
        FetchedPage instance on success, None on failure.
    """
    from fetcher.models import FetchedPage
    from django.conf import settings

    url     = discovered_url.url
    timeout = settings.CRAWLER["REQUEST_TIMEOUT"]
    session = get_session()

    # Mark as in-progress
    discovered_url.status = URLStatusChoices.FETCHING
    discovered_url.save(update_fields=["status"])

    start = time.monotonic()

    try:
        response     = session.get(url, timeout=timeout)
        duration_ms  = int((time.monotonic() - start) * 1000)

        fetched_page = FetchedPage.objects.create(
            discovered_url    = discovered_url,
            status_code       = response.status_code,
            content_type      = response.headers.get("Content-Type", ""),
            encoding          = response.encoding or "",
            raw_html          = response.text,
            fetch_duration_ms = duration_ms,
        )

        if response.ok:
            discovered_url.status = URLStatusChoices.FETCHED
            discovered_url.error_message = ""
        else:
            discovered_url.status = URLStatusChoices.FAILED
            discovered_url.error_message = f"HTTP {response.status_code}"

        discovered_url.save(update_fields=["status", "error_message"])
        logger.info("Fetched [%d] %s in %dms", response.status_code, url, duration_ms)
        return fetched_page

    except Exception as exc:
        duration_ms = int((time.monotonic() - start) * 1000)
        logger.warning("Fetch failed for %s after %dms: %s", url, duration_ms, exc)
        discovered_url.status        = URLStatusChoices.FAILED
        discovered_url.error_message = str(exc)[:500]
        discovered_url.save(update_fields=["status", "error_message"])
        return None


# ── Step 2: Parse ─────────────────────────────────────────────────────────────

def parse_page(fetched_page):
    """
    Extract clean article content from a FetchedPage using newspaper3k.

    Stores a ParsedArticle and updates the DiscoveredURL status to
    PARSED on success or FAILED if the page yields no usable content.

    Args:
        fetched_page: The FetchedPage instance to parse.

    Returns:
        ParsedArticle instance on success, None on failure.
    """
    from fetcher.models import ParsedArticle

    url = fetched_page.discovered_url.url
    logger.info("Parsing: %s", url)

    try:
        article = newspaper.Article(url)
        article.set_html(fetched_page.raw_html)
        article.parse()
    except Exception as exc:
        logger.warning("Parse failed for %s: %s", url, exc)
        _mark_failed(fetched_page.discovered_url, f"Parse error: {exc}")
        return None

    # Extract all available signals from newspaper3k
    title       = (article.title or "").strip()
    body_text   = (article.text  or "").strip()
    author      = ", ".join(article.authors)[:255]
    published_at = article.publish_date
    language    = (article.meta_lang or "").strip()[:10]
    source_domain = urlparse(url).netloc

    # Build tags from meta_keywords + tags set
    tags = _collect_tags(article)

    # Build signals dict — the JSON field used later for alerting
    signals = _collect_signals(article)

    # Use the discovery-time title as fallback if newspaper couldn't find one
    if not title:
        title = fetched_page.discovered_url.title or ""

    # Use meta description as summary fallback
    summary = (article.meta_description or "").strip()

    parsed_article = ParsedArticle.objects.create(
        fetched_page  = fetched_page,
        title         = title[:512],
        body_text     = body_text,
        summary       = summary,
        author        = author,
        published_at  = published_at,
        source_domain = source_domain,
        language      = language,
        tags          = tags,
        signals       = signals,
    )

    fetched_page.discovered_url.status = URLStatusChoices.PARSED
    fetched_page.discovered_url.error_message = ""
    fetched_page.discovered_url.save(update_fields=["status", "error_message"])

    logger.info(
        "Parsed '%s' — %d words, domain=%s",
        title[:60], len(body_text.split()), source_domain,
    )
    return parsed_article


# ── Step 3: Combined pipeline ─────────────────────────────────────────────────

def fetch_and_parse(discovered_url: DiscoveredURL) -> dict:
    """
    Run the full fetch → parse pipeline for one DiscoveredURL.

    Called directly by the Celery task.

    Returns:
        A result dict the task uses for logging and monitoring:
        {
            "url":    str,
            "status": "ok" | "fetch_failed" | "parse_failed",
            "title":  str  (only on "ok"),
            "words":  int  (only on "ok"),
        }
    """
    url = discovered_url.url

    fetched_page = fetch_page(discovered_url)
    if fetched_page is None:
        return {"url": url, "status": "fetch_failed"}

    parsed_article = parse_page(fetched_page)
    if parsed_article is None:
        return {"url": url, "status": "parse_failed"}

    return {
        "url":    url,
        "status": "ok",
        "title":  parsed_article.title,
        "words":  parsed_article.word_count,
    }


# ── Helpers ───────────────────────────────────────────────────────────────────

def _mark_failed(discovered_url: DiscoveredURL, message: str):
    discovered_url.status        = URLStatusChoices.FAILED
    discovered_url.error_message = message[:500]
    discovered_url.save(update_fields=["status", "error_message"])


def _collect_tags(article: newspaper.Article) -> list:
    """
    Merge tags from meta_keywords and newspaper's tag set into a
    deduplicated, lowercased list.
    """
    tags = set()

    # meta_keywords is a string like "gold, mining, Africa"
    meta_kw = article.meta_keywords or ""
    if isinstance(meta_kw, str):
        for kw in meta_kw.split(","):
            kw = kw.strip().lower()
            if kw:
                tags.add(kw)
    elif isinstance(meta_kw, list):
        tags.update(kw.strip().lower() for kw in meta_kw if kw.strip())

    # newspaper.tags is a set of strings
    for tag in (article.tags or set()):
        tags.add(tag.strip().lower())

    return sorted(tags)


def _collect_signals(article: newspaper.Article) -> dict:
    """
    Build a signals dict from everything newspaper3k extracted.
    Stored as JSON on ParsedArticle.signals for later alerting.

    Structure:
        {
            "keywords": [...],    # from meta_keywords
            "summary":  "...",    # newspaper's NLP summary (if available)
        }
    """
    signals: dict = {}

    # Keywords from meta
    meta_kw = article.meta_keywords or ""
    if isinstance(meta_kw, str):
        kws = [k.strip() for k in meta_kw.split(",") if k.strip()]
    else:
        kws = [k.strip() for k in meta_kw if k.strip()]
    if kws:
        signals["keywords"] = kws

    return signals
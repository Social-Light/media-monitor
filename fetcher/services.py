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

from core.models import URLStatusChoices
from core.throttle import wait_for_domain
from core.utils import get_session
from discovery.models import DiscoveredURL
from fetcher.country import detect_country
from fetcher.dedup import check_and_mark_duplicate
from fetcher.extractor import apply_rule, get_rule_for_page
from fetcher.news_filter import is_news_article
from fetcher.nlp import run_nlp
from fetcher.playwright_fetcher import render_page
from fetcher.search import index_article
from alerts.matching import check_alerts
from alerts.notifications import dispatch_notifications
from matching.matcher import match_article
from fetcher.bridge import push_competitor_to_platform, push_to_platform

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

    # Mark as in-progress
    discovered_url.status = URLStatusChoices.FETCHING
    discovered_url.save(update_fields=["status"])

    wait_for_domain(url)
    start = time.monotonic()

    try:
        if discovered_url.seed.use_playwright:
            raw_html     = render_page(url, timeout_ms=timeout * 1000)
            duration_ms  = int((time.monotonic() - start) * 1000)
            status_code  = 200
            content_type = "text/html; charset=utf-8"
            encoding     = "utf-8"
            fetch_ok     = True
        else:
            response     = get_session().get(url, timeout=timeout)
            duration_ms  = int((time.monotonic() - start) * 1000)
            raw_html     = response.text
            status_code  = response.status_code
            content_type = response.headers.get("Content-Type", "")
            encoding     = response.encoding or ""
            fetch_ok     = response.ok

        fetched_page = FetchedPage.objects.create(
            discovered_url    = discovered_url,
            status_code       = status_code,
            content_type      = content_type,
            encoding          = encoding,
            raw_html          = raw_html,
            fetch_duration_ms = duration_ms,
        )

        if fetch_ok:
            discovered_url.status = URLStatusChoices.FETCHED
            discovered_url.error_message = ""
        else:
            discovered_url.status = URLStatusChoices.FAILED
            discovered_url.error_message = f"HTTP {status_code}"

        discovered_url.save(update_fields=["status", "error_message"])
        logger.info("Fetched [%d] %s in %dms", status_code, url, duration_ms)
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

    # Apply per-site extraction rule overrides (takes priority over newspaper3k)
    rule = get_rule_for_page(url)
    if rule:
        overrides    = apply_rule(rule, fetched_page.raw_html, url)
        title        = overrides.get('title',        title)
        body_text    = overrides.get('body_text',    body_text)
        author       = overrides.get('author',       author)
        published_at = overrides.get('published_at', published_at)

    # Build tags from meta_keywords + tags set
    tags = _collect_tags(article)

    # Build signals dict — the JSON field used later for alerting
    signals = _collect_signals(article)

    # Enrich signals with NLP (named entities + sentiment)
    try:
        signals.update(run_nlp(body_text))
    except Exception as exc:
        logger.warning("NLP failed for %s: %s", url, exc)

    # Determine publication country — never empty
    country = detect_country(source_domain, signals)

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
        country       = country,
        language      = language,
        tags          = tags,
        signals       = signals,
    )

    try:
        check_and_mark_duplicate(parsed_article)
    except Exception as exc:
        logger.warning("Dedup check failed for %s: %s", url, exc)

    try:
        if not parsed_article.is_duplicate:
            index_article(parsed_article)
    except Exception as exc:
        logger.warning("Elasticsearch indexing failed for %s: %s", url, exc)

    try:
        if not parsed_article.is_duplicate:
            for match in check_alerts(parsed_article):
                dispatch_notifications(match)
    except Exception as exc:
        logger.warning("Alert matching failed for %s: %s", url, exc)

    try:
        if not parsed_article.is_duplicate:
            match_article(parsed_article)
    except Exception as exc:
        logger.warning("Organisation matching failed for %s: %s", url, exc)

    try:
        if not parsed_article.is_duplicate and is_news_article(parsed_article):
            push_to_platform(parsed_article)
            push_competitor_to_platform(parsed_article)
    except Exception as exc:
        logger.warning("Platform bridge failed for %s: %s", url, exc)

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
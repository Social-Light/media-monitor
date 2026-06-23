"""
fetcher/social_ingest.py
─────────────────────────
Turn a normalised social-media post (from discovery.services.apify) into a
ParsedArticle and run it through the same downstream enrichment as a parsed
news article.

Why this bypasses fetch + parse:
  A social post arrives from Apify already complete — full text, author, date,
  permalink. There is no HTML to fetch (the permalinks sit behind login walls
  and newspaper3k cannot read them). So instead of DiscoveredURL → fetch → parse,
  a social post is materialised directly:

      DiscoveredURL (status=PARSED)
          └─ FetchedPage   (synthesised; raw_html = the post text)
                 └─ ParsedArticle

  and then the exact same post-parse pipeline runs:
      dedup → Elasticsearch index → alerts → organisation matching → platform bridge

The only deliberate difference from parse_page() is the bridge gate: news goes
through is_news_article() (to drop corporate product pages), but a social post
that already matched a tracked keyword *is* the mention we want — so it is pushed
to the platform unconditionally.

Idempotency: each post's permalink is hashed into DiscoveredURL.url_hash (unique),
so re-running a social seed never re-ingests a post already seen.

ingest_social_post(seed, post) -> ParsedArticle | None
    Returns the new ParsedArticle, or None if skipped (no URL / already seen)
    or on any failure. Never raises — one bad post must not abort the run.
"""
import logging

from core.models import URLStatusChoices
from discovery.models import DiscoveredURL
from fetcher.models import FetchedPage, ParsedArticle
from fetcher.dedup import check_and_mark_duplicate
from fetcher.nlp import run_nlp
from fetcher.search import index_article
from alerts.matching import check_alerts
from alerts.notifications import dispatch_notifications
from matching.matcher import match_article
from fetcher.bridge import push_social_to_platform
from discovery.services.apify import PLATFORM_DOMAINS

logger = logging.getLogger(__name__)


def _build_signals(post: dict) -> dict:
    """
    Compose the ParsedArticle.signals dict: NLP-derived sentiment/entities over
    the post text, plus the social metadata the platform/UI may surface later.
    """
    text = post.get("text", "") or ""
    signals: dict = {}
    try:
        signals.update(run_nlp(text))
    except Exception as exc:  # pragma: no cover - defensive, run_nlp rarely raises
        logger.warning("NLP failed for social post %s: %s", post.get("url"), exc)

    signals["social"] = True
    signals["platform"] = post.get("platform", "")
    if post.get("author_handle"):
        signals["author_handle"] = post["author_handle"]
    if post.get("engagement"):
        signals["engagement"] = post["engagement"]
    if post.get("followers"):
        signals["followers"] = post["followers"]
    return signals


def ingest_social_post(seed, post: dict) -> ParsedArticle | None:
    """
    Materialise one normalised social post (see apify.fetch_mentions) as a
    ParsedArticle for `seed` and run the downstream enrichment pipeline.
    """
    url = (post.get("url") or "").strip()
    if not url:
        logger.debug("Skipping social post with no URL (platform=%s)", post.get("platform"))
        return None

    try:
        url_hash = DiscoveredURL.hash_url(url)
        discovered, created = DiscoveredURL.objects.get_or_create(
            url_hash=url_hash,
            defaults={
                "seed":         seed,
                "url":          url,
                "title":        post.get("title", "")[:512],
                "snippet":      post.get("text", ""),
                "published_at": post.get("published_at"),
                "status":       URLStatusChoices.PARSED,
            },
        )
        if not created:
            # Already ingested on a previous run — idempotent skip.
            return None

        platform = post.get("platform", "")
        source_domain = PLATFORM_DOMAINS.get(platform, platform)[:255]
        text = post.get("text", "") or ""

        # A synthesised "fetch" — no network happened, but the pipeline (and the
        # ParsedArticle FK) needs a FetchedPage row, and storing the post text as
        # raw_html keeps re-processing possible.
        fetched_page = FetchedPage.objects.create(
            discovered_url    = discovered,
            status_code       = 200,
            content_type      = "application/json",
            encoding          = "utf-8",
            raw_html          = text,
            fetch_duration_ms = 0,
        )

        author = (post.get("author") or "")
        if post.get("author_handle"):
            handle = post["author_handle"]
            author = f"{author} (@{handle})".strip() if author else f"@{handle}"

        parsed_article = ParsedArticle.objects.create(
            fetched_page  = fetched_page,
            title         = (post.get("title") or text[:200] or url)[:512],
            body_text     = text,
            summary       = text,
            author        = author[:255],
            published_at  = post.get("published_at"),
            source_domain = source_domain,
            country       = "",
            language      = "",
            tags          = [],
            signals       = _build_signals(post),
        )
    except Exception as exc:
        logger.warning("Failed to ingest social post %s: %s", url, exc)
        return None

    # ── Downstream enrichment — each step isolated so one failure never aborts ──
    try:
        check_and_mark_duplicate(parsed_article)
    except Exception as exc:
        logger.warning("Dedup check failed for social post %s: %s", url, exc)

    try:
        if not parsed_article.is_duplicate:
            index_article(parsed_article)
    except Exception as exc:
        logger.warning("Elasticsearch indexing failed for social post %s: %s", url, exc)

    try:
        if not parsed_article.is_duplicate:
            for match in check_alerts(parsed_article):
                dispatch_notifications(match)
    except Exception as exc:
        logger.warning("Alert matching failed for social post %s: %s", url, exc)

    try:
        if not parsed_article.is_duplicate:
            match_article(parsed_article)
    except Exception as exc:
        logger.warning("Organisation matching failed for social post %s: %s", url, exc)

    # Bridge: a keyword-matched social post is already the mention we want, so —
    # unlike news — it is not filtered through is_news_article(). It is written as
    # a SocialMediaPost (the platform's 'Social Media Posts' table), NOT an
    # OnlineArticle.
    try:
        if not parsed_article.is_duplicate:
            push_social_to_platform(parsed_article)
    except Exception as exc:
        logger.warning("Platform bridge failed for social post %s: %s", url, exc)

    logger.info("Ingested %s post: %s", parsed_article.source_domain, url)
    return parsed_article

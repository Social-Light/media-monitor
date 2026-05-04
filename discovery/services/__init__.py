"""
discovery/services/__init__.py
────────────────────────────────
Single entry-point for the entire Discovery layer.

run_discovery(seed) looks at the SeedSource.source_type and delegates
to the correct adapter — callers never import the adapters directly.

Returned dict shape (identical from all four adapters):
    {
        "url":          str,
        "title":        str,
        "snippet":      str,
        "published_at": datetime | None,
        "source_type":  str,   # "rss" | "sitemap" | "seed_url" | "search_api"
    }
"""
import logging

from .rss            import fetch_feed
from .sitemap        import fetch_sitemap, discover_sitemap_url
from .link_extractor import extract_links
from .search_api     import search

logger = logging.getLogger(__name__)

__all__ = [
    "fetch_feed",
    "fetch_sitemap",
    "discover_sitemap_url",
    "extract_links",
    "search",
    "run_discovery",
]


def run_discovery(seed) -> list[dict]:
    """
    Dispatch discovery for a single SeedSource instance.

    Reads seed.source_type to pick the right adapter, then passes
    seed.meta (a JSON dict) as kwargs for adapter-specific config.

    Args:
        seed: A SeedSource model instance.

    Returns:
        List of raw article dicts ready to be stored as DiscoveredURLs.

    Meta keys per source type:
        rss        — (no extra config needed)
        sitemap    — max_depth (int, default 3)
        seed_url   — allowed_domains (list[str]),
                     article_links_only (bool, default True),
                     max_links (int, default 50)
        search_api — provider ("bing" | "google", default "bing"),
                     query (str, defaults to seed.name),
                     count (int, default 20)
    """
    from discovery.models import SourceType

    source_type = seed.source_type
    meta        = seed.meta or {}

    logger.info(
        "run_discovery → seed=%d (%s) source_type=%s",
        seed.pk, seed.name, source_type,
    )

    # ── RSS / Atom ─────────────────────────────────────────────────────────────
    if source_type == SourceType.RSS:
        return fetch_feed(seed.url)

    # ── Sitemap ────────────────────────────────────────────────────────────────
    elif source_type == SourceType.SITEMAP:
        return fetch_sitemap(
            seed.url,
            max_depth=meta.get("max_depth", 3),
        )

    # ── Link extraction ────────────────────────────────────────────────────────
    elif source_type == SourceType.SEED_URL:
        return extract_links(
            seed.url,
            allowed_domains=meta.get("allowed_domains") or None,
            keywords=seed.keywords or None,
            max_links=meta.get("max_links", 50),
            article_links_only=meta.get("article_links_only", True),
        )

    # ── Search API ─────────────────────────────────────────────────────────────
    elif source_type == SourceType.SEARCH_API:
        # Query defaults to the seed's keyword_filter joined together,
        # falling back to the seed name if no keywords are set.
        default_query = " ".join(seed.keywords) if seed.keywords else seed.name
        return search(
            query=meta.get("query", default_query),
            provider=meta.get("provider", "bing"),
            count=meta.get("count", 20),
        )

    # ── Manual / unknown ──────────────────────────────────────────────────────
    else:
        logger.warning(
            "run_discovery: no adapter for source_type=%s (seed=%d)",
            source_type, seed.pk,
        )
        return []
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
from .apify          import fetch_mentions

from platform_sync.models import Organization

logger = logging.getLogger(__name__)

__all__ = [
    "fetch_feed",
    "fetch_sitemap",
    "discover_sitemap_url",
    "extract_links",
    "search",
    "fetch_mentions",
    "active_organisation_keywords",
    "run_discovery",
]


def active_organisation_keywords(categories=("brand",)) -> list[str]:
    """
    Distinct keywords across all active platform organisations — original
    casing, de-duplicated case-insensitively.

    Used by org-driven social seeds (meta {"from_orgs": true}) so the LinkedIn /
    X / etc. search stays in sync with the platform's configured organisations:
    add a keyword in the platform and the next run searches for it, with no seed
    edit. Capture is already gated on these same keywords by the bridge.

    categories filters by Keyword.category (brand | personnel | campaign). It
    DEFAULTS to brand-only — the main cost lever, since one Apify search runs per
    keyword and personnel/campaign terms multiply that. Pass categories=None (or
    the seed's meta["keyword_categories"]=null) to include every category.
    """
    cats = set(categories) if categories else None
    terms: list[str] = []
    seen: set[str] = set()
    for org in Organization.objects.filter(status="active"):
        for kw in org.keywords.all():
            if cats is not None and kw.category not in cats:
                continue
            term = (kw.keyword or "").strip()
            if term and term.lower() not in seen:
                seen.add(term.lower())
                terms.append(term)
    return terms


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
        social     — platform ("x" | "facebook" | "instagram" | "linkedin"),
                     from_orgs (bool — if true, search terms come from the active
                       platform organisations' keywords, one search per keyword;
                       ignores query),
                     keyword_categories (list, only with from_orgs; which keyword
                       categories to search — defaults to ["brand"] for cost
                       control; null = all categories),
                     query (str | list, defaults to seed keywords then name),
                     max_items (int, default settings APIFY_MAX_ITEMS),
                     actor_input (dict, optional Actor-input overrides)
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

    # ── Social media (Apify) ────────────────────────────────────────────────────
    elif source_type == SourceType.SOCIAL:
        platform    = meta.get("platform", "x")
        max_items   = meta.get("max_items")
        actor_input = meta.get("actor_input")

        # Org-driven: pull search terms from the active platform organisations and
        # run one search per keyword, so the search set always matches what the
        # bridge will capture. New org keywords are picked up automatically.
        # Defaults to BRAND keywords only (cost control — one Apify search per
        # keyword); a seed can widen with meta["keyword_categories"] (list, or
        # null for every category).
        if meta.get("from_orgs"):
            results: list[dict] = []
            categories = meta.get("keyword_categories", ["brand"])
            terms = active_organisation_keywords(categories=categories)
            logger.info(
                "run_discovery: org-driven social search over %d keyword(s) (categories=%s)",
                len(terms), categories,
            )
            for term in terms:
                results.extend(fetch_mentions(
                    platform, [term], max_items=max_items, actor_input=actor_input,
                ))
            return results

        # Otherwise search the seed's own configured terms (meta.query → keywords → name).
        terms = meta.get("query") or seed.keywords or [seed.name]
        return fetch_mentions(
            platform=platform, terms=terms,
            max_items=max_items, actor_input=actor_input,
        )

    # ── Manual / unknown ──────────────────────────────────────────────────────
    else:
        logger.warning(
            "run_discovery: no adapter for source_type=%s (seed=%d)",
            source_type, seed.pk,
        )
        return []
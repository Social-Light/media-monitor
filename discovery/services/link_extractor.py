"""
discovery/services/link_extractor.py
──────────────────────────────────────
Fetches a seed page and extracts outbound <a href> links.

This is the most general-purpose strategy — useful for sites that
publish no RSS feed and no structured sitemap.

Filtering applied in order:
  1. Domain filter   — only keep links that stay within allowed_domains
  2. Scheme filter   — http/https only (drops mailto:, tel:, javascript:, etc.)
  3. Deduplication   — each normalised URL appears at most once
  4. Article hint    — optional heuristic that scores URLs likely to be articles
  5. Keyword filter  — optional; drops URLs/anchor text with no keyword match
  6. Cap             — at most max_links results returned
"""
import logging
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from core.utils import safe_get, normalize_url, get_domain, is_valid_url

logger = logging.getLogger(__name__)


# ── Article URL heuristic ─────────────────────────────────────────────────────

# Path segments that strongly suggest an article rather than nav/utility page
_ARTICLE_SIGNALS = frozenset([
    "/news/", "/article/", "/articles/", "/story/", "/stories/",
    "/post/", "/posts/", "/blog/", "/press-release/", "/press/",
    "/media/", "/publication/", "/publications/", "/report/",
])


def _looks_like_article(url: str) -> bool:
    """
    Heuristic: return True if the URL path suggests it leads to an article.
    Uses path segment matching — fast and requires no network call.
    """
    path = urlparse(url).path.lower()
    if any(sig in path for sig in _ARTICLE_SIGNALS):
        return True
    # Also accept URLs with a long, slug-like path (at least 2 segments, >15 chars)
    segments = [s for s in path.strip("/").split("/") if s]
    return len(segments) >= 2 and len(path) > 15


# ── Public API ────────────────────────────────────────────────────────────────

def extract_links(
    url: str,
    allowed_domains: list[str] | None = None,
    keywords: list[str] | None = None,
    max_links: int = 50,
    article_links_only: bool = False,
) -> list[dict]:
    """
    Fetch `url` and extract filtered outbound links.

    Args:
        url:                The page to scrape.
        allowed_domains:    Hosts to keep. Defaults to the same domain as `url`.
        keywords:           If given, only keep links where anchor text or href
                            contains at least one keyword (case-insensitive).
        max_links:          Hard cap on returned results.
        article_links_only: If True, apply _looks_like_article heuristic to
                            filter out navigation/utility links.

    Returns:
        List of dicts with: url, title, snippet, published_at, source_type.
    """
    if allowed_domains is None:
        allowed_domains = [get_domain(url)]

    logger.info("Extracting links from: %s", url)

    response = safe_get(url)
    if response is None:
        logger.warning("Could not fetch page for link extraction: %s", url)
        return []

    soup  = BeautifulSoup(response.text, "lxml")
    found: list[dict] = []
    seen:  set[str]   = set()

    for a_tag in soup.find_all("a", href=True):
        href = a_tag["href"].strip()

        # Resolve and normalise
        full_url = normalize_url(href, url)

        # Basic validity (http/https, has netloc)
        if not is_valid_url(full_url):
            continue

        # Domain filter
        if get_domain(full_url) not in allowed_domains:
            continue

        # Dedup
        if full_url in seen:
            continue
        seen.add(full_url)

        # Article heuristic (optional)
        if article_links_only and not _looks_like_article(full_url):
            continue

        anchor_text = a_tag.get_text(separator=" ", strip=True)[:512]

        # Keyword filter (optional)
        if keywords:
            haystack = (anchor_text + " " + full_url).lower()
            if not any(kw in haystack for kw in keywords):
                continue

        found.append({
            "url":          full_url,
            "title":        anchor_text,
            "snippet":      "",
            "published_at": None,
            "source_type":  "seed_url",
        })

        if len(found) >= max_links:
            logger.debug("max_links cap (%d) reached for %s", max_links, url)
            break

    logger.info("Extracted %d links from %s", len(found), url)
    return found
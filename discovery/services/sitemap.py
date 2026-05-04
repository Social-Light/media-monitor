"""
discovery/services/sitemap.py
──────────────────────────────
Crawls XML sitemaps and returns article URL dicts.

Handles:
  • Standard URL sets      <urlset> … <url><loc>…
  • Sitemap index files    <sitemapindex> → recursively fetches children
  • Google News sitemaps   <news:news> extension for titles and pub dates
  • Gzipped sitemaps       .xml.gz (decompressed transparently)
"""
import gzip
import io
import logging
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from dateutil import parser as date_parser

from core.utils import safe_get, normalize_url

logger = logging.getLogger(__name__)


# ── Decompression ─────────────────────────────────────────────────────────────

def _decompress(response) -> bytes:
    """Transparently decompress gzipped sitemaps."""
    content_type = response.headers.get("Content-Type", "")
    is_gz = response.url.endswith(".gz") or "gzip" in content_type
    if is_gz:
        with gzip.open(io.BytesIO(response.content)) as f:
            return f.read()
    return response.content


# ── Parsing ───────────────────────────────────────────────────────────────────

def _parse_sitemap_bytes(content: bytes, source_url: str) -> list[dict]:
    """
    Parse raw XML bytes from a single sitemap file.

    Returns either:
      - A list of URL dicts (for a urlset)
      - A list of {"__index__": True, "url": child_url} sentinels (for sitemapindex)
    """
    soup = BeautifulSoup(content, "lxml-xml")

    # ── Sitemap Index ──────────────────────────────────────────────────────
    if soup.find("sitemapindex"):
        children = []
        for sm_tag in soup.find_all("sitemap"):
            loc = sm_tag.find("loc")
            if loc and loc.get_text(strip=True):
                children.append({
                    "__index__": True,
                    "url": loc.get_text(strip=True),
                })
        logger.debug("Sitemap index at %s → %d children", source_url, len(children))
        return children

    # ── URL Set ────────────────────────────────────────────────────────────
    results = []
    for url_tag in soup.find_all("url"):
        loc = url_tag.find("loc")
        if not loc or not loc.get_text(strip=True):
            continue

        link = normalize_url(loc.get_text(strip=True), source_url)

        # ── Google News extension ──────────────────────────────────────────
        title    = ""
        pub_date = None

        news_title = url_tag.find("news:title")
        if news_title:
            title = news_title.get_text(strip=True)[:512]

        pub_tag = url_tag.find("news:publication_date") or url_tag.find("lastmod")
        if pub_tag:
            try:
                pub_date = date_parser.parse(pub_tag.get_text(strip=True))
            except Exception:
                pass

        results.append({
            "url":          link,
            "title":        title,
            "snippet":      "",
            "published_at": pub_date,
            "source_type":  "sitemap",
        })

    return results


# ── Public API ────────────────────────────────────────────────────────────────

def fetch_sitemap(url: str, max_depth: int = 3) -> list[dict]:
    """
    Fetch and parse a sitemap URL, recursively following index files.

    Args:
        url:       Root sitemap URL.
        max_depth: How many levels of sitemap index nesting to follow.
                   Prevents infinite loops on badly configured sites.

    Returns:
        Flat list of article URL dicts.
    """
    logger.info("Fetching sitemap: %s (max_depth=%d)", url, max_depth)

    response = safe_get(url)
    if response is None:
        logger.warning("Could not fetch sitemap: %s", url)
        return []

    try:
        content = _decompress(response)
        items   = _parse_sitemap_bytes(content, url)
    except Exception as exc:
        logger.error("Failed to parse sitemap %s: %s", url, exc)
        return []

    results = []
    for item in items:
        if item.get("__index__"):
            if max_depth > 0:
                # Recurse into child sitemap
                results.extend(fetch_sitemap(item["url"], max_depth=max_depth - 1))
            else:
                logger.debug("max_depth reached, skipping child sitemap: %s", item["url"])
        else:
            results.append(item)

    logger.info("Sitemap %s → %d URLs", url, len(results))
    return results


def discover_sitemap_url(base_url: str) -> str | None:
    """
    Auto-discover a sitemap URL for a given website root.

    Strategy:
      1. Parse robots.txt for a Sitemap: directive
      2. Probe common well-known paths

    Args:
        base_url: Website root, e.g. "https://example.com"

    Returns:
        First sitemap URL found, or None.
    """
    # 1. robots.txt
    robots_url = urljoin(base_url, "/robots.txt")
    resp = safe_get(robots_url)
    if resp:
        for line in resp.text.splitlines():
            if line.lower().startswith("sitemap:"):
                sm_url = line.split(":", 1)[1].strip()
                logger.info("Found sitemap in robots.txt: %s", sm_url)
                return sm_url

    # 2. Common paths
    for path in ["/sitemap.xml", "/sitemap_index.xml", "/sitemap.xml.gz"]:
        candidate = urljoin(base_url, path)
        resp = safe_get(candidate)
        if resp and resp.status_code == 200:
            logger.info("Found sitemap at: %s", candidate)
            return candidate

    logger.warning("No sitemap discovered for %s", base_url)
    return None
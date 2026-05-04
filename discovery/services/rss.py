"""
discovery/services/rss.py
─────────────────────────
Parses an RSS or Atom feed and returns a normalised list of article dicts.

feedparser handles all flavours transparently:
  RSS 0.9x, RSS 1.0, RSS 2.0, Atom 0.3, Atom 1.0, MediaRSS, iTunes podcasts

Returned dict shape (same for all four services):
    {
        "url":          str,
        "title":        str,
        "snippet":      str,
        "published_at": datetime | None,
        "source_type":  "rss",
    }
"""
import logging
from datetime import datetime, timezone
from html.parser import HTMLParser

import feedparser
from dateutil import parser as date_parser

from core.utils import normalize_url

logger = logging.getLogger(__name__)


# ── HTML stripper ─────────────────────────────────────────────────────────────

class _StripHTML(HTMLParser):
    """Minimal HTML-to-plaintext converter used for feed summaries."""
    def __init__(self):
        super().__init__()
        self._parts: list[str] = []

    def handle_data(self, data: str):
        self._parts.append(data)

    def get_text(self) -> str:
        import re
        raw = " ".join(self._parts)
        return re.sub(r"\s+", " ", raw).strip()


def _strip_html(raw: str) -> str:
    p = _StripHTML()
    p.feed(raw)
    return p.get_text()


# ── Date parsing ──────────────────────────────────────────────────────────────

def _parse_date(entry: feedparser.FeedParserDict) -> datetime | None:
    """
    Try feedparser's pre-parsed time structs first (most reliable),
    then fall back to the raw string fields.
    Returns a timezone-aware datetime or None.
    """
    # feedparser pre-parses dates into time.struct_time tuples
    for attr in ("published_parsed", "updated_parsed", "created_parsed"):
        t = getattr(entry, attr, None)
        if t:
            try:
                return datetime(*t[:6], tzinfo=timezone.utc)
            except Exception:
                pass

    # Fall back to raw string fields
    for attr in ("published", "updated"):
        raw = getattr(entry, attr, None)
        if raw:
            try:
                return date_parser.parse(raw)
            except Exception:
                pass

    return None


# ── Summary extraction ────────────────────────────────────────────────────────

def _get_summary(entry: feedparser.FeedParserDict) -> str:
    """
    Pull a plain-text snippet from whichever field the feed provides.
    Tries: summary → description → content (list).
    """
    for attr in ("summary", "description"):
        raw = getattr(entry, attr, None)
        if raw:
            return _strip_html(str(raw))[:1000]

    # Atom feeds sometimes use a content list
    content = getattr(entry, "content", None)
    if content and isinstance(content, list):
        raw = content[0].get("value", "")
        if raw:
            return _strip_html(str(raw))[:1000]

    return ""


# ── Public API ────────────────────────────────────────────────────────────────

def fetch_feed(url: str) -> list[dict]:
    """
    Fetch and parse a feed URL.

    Returns a list of article dicts.  An empty list means either the feed
    could not be fetched or it contained no entries — both are logged.

    Args:
        url: The RSS or Atom feed URL.
    """
    logger.info("Fetching RSS/Atom feed: %s", url)

    feed = feedparser.parse(url)

    # bozo=True means the feed is malformed, but may still have entries
    if feed.bozo:
        logger.warning(
            "Feed %s has parse warnings (%s) — continuing with %d entries",
            url, feed.bozo_exception, len(feed.entries),
        )

    if not feed.entries:
        logger.warning("Feed %s returned 0 entries", url)
        return []

    results: list[dict] = []

    for entry in feed.entries:
        link = getattr(entry, "link", None)
        if not link:
            continue  # Entry without a URL is useless

        results.append({
            "url":          normalize_url(link),
            "title":        getattr(entry, "title", "")[:512],
            "snippet":      _get_summary(entry),
            "published_at": _parse_date(entry),
            "source_type":  "rss",
        })

    logger.info("RSS feed %s → %d entries", url, len(results))
    return results
"""
discovery/services/search_api.py
──────────────────────────────────
Discovers new articles by querying external search APIs with keywords.

Supported providers:
  • Bing News Search API  — requires BING_API_KEY in settings.CRAWLER
  • Google Custom Search  — requires GOOGLE_NEWS_API_KEY + GOOGLE_CSE_ID

Both adapters return the same dict shape as every other service,
so the caller (run_discovery) is provider-agnostic.

API key setup:
  Bing:   https://www.microsoft.com/en-us/bing/apis/bing-news-search-api
  Google: https://programmablesearchengine.google.com  (free: 100 req/day)
"""
import logging

from dateutil import parser as date_parser
from django.conf import settings

from core.utils import safe_get, get_session

logger = logging.getLogger(__name__)


# ── Shared helper ─────────────────────────────────────────────────────────────

def _parse_iso_date(raw: str | None):
    if not raw:
        return None
    try:
        return date_parser.parse(raw)
    except Exception:
        return None


def _article_dict(url: str, title: str, snippet: str, published_at) -> dict:
    return {
        "url":          url,
        "title":        title[:512],
        "snippet":      snippet[:1000],
        "published_at": published_at,
        "source_type":  "search_api",
    }


# ── Bing News Search ──────────────────────────────────────────────────────────

BING_ENDPOINT = "https://api.bing.microsoft.com/v7.0/news/search"


def search_bing(query: str, count: int = 20, market: str = "en-ZA") -> list[dict]:
    """
    Query the Bing News Search API.

    Args:
        query:  Search string, e.g. "gold mining Botswana".
        count:  Number of results (max 100 per request).
        market: Bing market code. "en-ZA" = English, South Africa.

    Returns:
        List of article dicts.  Empty list if the API key is not configured
        or the request fails.
    """
    api_key = settings.CRAWLER.get("BING_API_KEY", "")
    if not api_key:
        logger.warning("BING_API_KEY not set — skipping Bing search for '%s'", query)
        return []

    session = get_session()
    session.headers["Ocp-Apim-Subscription-Key"] = api_key

    params = {
        "q":         query,
        "count":     min(count, 100),
        "mkt":       market,
        "freshness": "Week",
        "sortBy":    "Date",
    }

    logger.info("Bing News search: '%s' (count=%d, market=%s)", query, count, market)
    response = safe_get(BING_ENDPOINT, session=session, params=params)
    if response is None:
        return []

    results = []
    for article in response.json().get("value", []):
        url = article.get("url", "")
        if not url:
            continue
        results.append(_article_dict(
            url=url,
            title=article.get("name", ""),
            snippet=article.get("description", ""),
            published_at=_parse_iso_date(article.get("datePublished")),
        ))

    logger.info("Bing returned %d articles for '%s'", len(results), query)
    return results


# ── Google Custom Search ──────────────────────────────────────────────────────

GOOGLE_CSE_ENDPOINT = "https://www.googleapis.com/customsearch/v1"


def search_google(
    query:        str,
    count:        int = 10,
    date_restrict: str = "d7",
    cx:           str = "",
    date_range:   tuple | None = None,
) -> list[dict]:
    """
    Query the Google Programmable Search Engine (Custom Search).

    Args:
        query:         Search string.
        count:         Results per page (max 10 — Google API hard limit).
        date_restrict: Restrict to recent results. "d7" = last 7 days.
        cx:            Search Engine ID. Falls back to settings.CRAWLER["GOOGLE_CSE_ID"].
        date_range:    Optional (start_date, end_date) of datetime.date objects.
                       When given, an ABSOLUTE range is requested via
                       sort=date:r:YYYYMMDD:YYYYMMDD (used for historical
                       back-fills) and date_restrict is ignored. This is the only
                       provider that supports arbitrary date ranges — Bing News
                       offers relative freshness (Day/Week/Month) only.

    Returns:
        List of article dicts.
    """
    api_key = settings.CRAWLER.get("GOOGLE_NEWS_API_KEY", "")
    cse_id  = cx or settings.CRAWLER.get("GOOGLE_CSE_ID", "")

    if not api_key:
        logger.warning("GOOGLE_NEWS_API_KEY not set — skipping Google search for '%s'", query)
        return []
    if not cse_id:
        logger.warning("GOOGLE_CSE_ID not set — skipping Google search for '%s'", query)
        return []

    if date_range:
        start, end = date_range
        sort_param = f"date:r:{start:%Y%m%d}:{end:%Y%m%d}"
    else:
        sort_param = "date"

    params = {
        "key":          api_key,
        "cx":           cse_id,
        "q":            query,
        "num":          min(count, 10),
        "sort":         sort_param,
    }
    # A relative window only makes sense when no absolute range was requested.
    if not date_range:
        params["dateRestrict"] = date_restrict

    logger.info("Google CSE search: '%s' (count=%d)", query, count)
    response = safe_get(GOOGLE_CSE_ENDPOINT, params=params)
    if response is None:
        return []

    results = []
    for item in response.json().get("items", []):
        url = item.get("link", "")
        if not url:
            continue
        results.append(_article_dict(
            url=url,
            title=item.get("title", ""),
            snippet=item.get("snippet", ""),
            published_at=None,  # CSE doesn't reliably return pub dates
        ))

    logger.info("Google CSE returned %d articles for '%s'", len(results), query)
    return results


# ── Unified dispatcher ────────────────────────────────────────────────────────

def search(query: str, provider: str = "bing", **kwargs) -> list[dict]:
    """
    Single entry-point used by run_discovery().

    Args:
        query:    The search string.
        provider: "bing" | "google"
        **kwargs: Forwarded to the underlying adapter.

    Raises:
        ValueError: If an unsupported provider is specified.
    """
    if provider == "bing":
        return search_bing(query, **kwargs)
    elif provider == "google":
        return search_google(query, **kwargs)
    else:
        raise ValueError(f"Unknown search provider '{provider}'. Use 'bing' or 'google'.")
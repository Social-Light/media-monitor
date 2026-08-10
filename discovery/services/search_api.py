"""
discovery/services/search_api.py
──────────────────────────────────
Discovers new articles by querying external search APIs with keywords.

Supported providers:
  • Bing News Search API  — requires BING_API_KEY in settings.CRAWLER.
    NOTE: Bing Search API v7 (all tiers, including News) was retired by
    Microsoft on 2025-08-11 and stopped accepting new signups from Feb 2025.
    This adapter is kept only in case a pre-retirement key still works for an
    existing resource — do not build new integrations against it.
  • Google Custom Search  — requires GOOGLE_NEWS_API_KEY + GOOGLE_CSE_ID
  • Serper (Google News)  — requires SERPER_API_KEY. Used in preference to
    Google CSE for time-sensitive orgs — CSE doesn't reliably return publish
    dates, Serper's News endpoint does. Paid (small free trial).
  • Tavily (news topic)   — requires TAVILY_API_KEY. Free tier: 1,000
    credits/month, renews monthly, no card required — the only provider here
    with an ongoing free allowance rather than a one-time trial.

All adapters return the same dict shape as every other service,
so the caller (run_discovery) is provider-agnostic.

API key setup:
  Bing:   retired — see note above, do not sign up
  Google: https://programmablesearchengine.google.com  (free: 100 req/day)
  Serper: https://serper.dev
  Tavily: https://tavily.com  (free: 1,000 credits/month, no card)
"""
import logging
import re
from datetime import timedelta, timezone as dt_timezone

from dateutil import parser as date_parser
from django.conf import settings
from django.utils import timezone

from core.utils import safe_get, safe_post, get_session

_GOOGLE_GOTO_HREF_RE = re.compile(r'HREF="([^"]+)"', re.IGNORECASE)

logger = logging.getLogger(__name__)


# ── Shared helper ─────────────────────────────────────────────────────────────

def _parse_iso_date(raw: str | None):
    if not raw:
        return None
    try:
        return date_parser.parse(raw)
    except Exception:
        return None


_RELATIVE_UNITS = {
    "second": "seconds", "minute": "minutes", "hour": "hours",
    "day": "days", "week": "weeks", "month": "months", "year": "years",
}


def _parse_serper_date(raw: str | None):
    """
    Serper's News endpoint returns "date" as a relative string like
    "2 hours ago" / "3 days ago" most of the time, but occasionally an
    absolute date for older results. Handle both.
    """
    if not raw:
        return None

    match = re.match(r"(\d+)\s+(second|minute|hour|day|week|month|year)s?\s+ago", raw.strip().lower())
    if match:
        amount, unit = int(match.group(1)), _RELATIVE_UNITS[match.group(2)]
        # timedelta has no months/years — approximate (good enough for freshness sorting).
        if unit == "months":
            return timezone.now() - timedelta(days=30 * amount)
        if unit == "years":
            return timezone.now() - timedelta(days=365 * amount)
        return timezone.now() - timedelta(**{unit: amount})

    parsed = _parse_iso_date(raw)
    if parsed is not None and timezone.is_naive(parsed):
        # Absolute fallback dates (e.g. "Aug 3, 2026") carry no timezone —
        # assume UTC rather than leaving Django to warn/mishandle a naive dt.
        parsed = timezone.make_aware(parsed, dt_timezone.utc)
    return parsed


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


# ── Serper (Google News) ──────────────────────────────────────────────────────

SERPER_NEWS_ENDPOINT = "https://google.serper.dev/news"


def search_serper(query: str, count: int = 20, gl: str = "za", tbs: str | None = "qdr:m") -> list[dict]:
    """
    Query Serper's Google News endpoint.

    Preferred over Google CSE for time-sensitive orgs: unlike CSE, Serper's
    News results carry a genuine (if often relative, e.g. "2 hours ago")
    publish date — see _parse_serper_date.

    Args:
        query: Search string.
        count: Number of results (Serper News returns ~10-100 per page).
        gl:    Country code for localised results. "za" = South Africa.
               (Botswana has no dedicated Google News edition; "za" is the
               closest regional match and is what Google itself falls back to.)
        tbs:   Google's time-based-search filter — "qdr:h"/"d"/"w"/"m"/"y"
               (past hour/day/week/month/year). Defaults to "qdr:m" (past
               month) since the bridge's is_news_article() gate only accepts
               current-calendar-month publish dates anyway — an unrestricted
               query mostly wastes results on older coverage that gets
               filtered out downstream. Pass None to disable.

    Returns:
        List of article dicts. Empty list if the API key is not configured
        or the request fails.
    """
    api_key = settings.CRAWLER.get("SERPER_API_KEY", "")
    if not api_key:
        logger.warning("SERPER_API_KEY not set — skipping Serper search for '%s'", query)
        return []

    session = get_session()
    session.headers["X-API-KEY"] = api_key
    session.headers["Content-Type"] = "application/json"

    payload = {"q": query, "gl": gl, "num": count}
    if tbs:
        payload["tbs"] = tbs

    logger.info("Serper News search: '%s' (count=%d, gl=%s, tbs=%s)", query, count, gl, tbs)
    response = safe_post(SERPER_NEWS_ENDPOINT, session=session, json=payload)
    if response is None:
        return []

    results = []
    for item in response.json().get("news", []):
        url = item.get("link", "")
        if not url:
            continue
        results.append(_article_dict(
            url=url,
            title=item.get("title", ""),
            snippet=item.get("snippet", ""),
            published_at=_parse_serper_date(item.get("date")),
        ))

    logger.info("Serper returned %d articles for '%s'", len(results), query)
    return results


# ── Tavily ─────────────────────────────────────────────────────────────────────

TAVILY_ENDPOINT = "https://api.tavily.com/search"


def _resolve_tavily_url(url: str, session) -> str:
    """
    Tavily's topic="news" results are frequently Google click-tracking
    wrappers (https://www.google.com/goto?url=...) rather than the article's
    real URL — confirmed by hand: the wrapper returns HTTP 200 (no Location
    header, so plain requests/redirect-following never unwraps it) with an
    HTML body of the form:

        <TITLE>302 Moved</TITLE> ... <A HREF="https://real-article-url">here</A>

    Without unwrapping this, every field downstream that depends on the real
    URL breaks: dedup keys, domain-based allow/deny lists, and
    is_news_article()'s host checks would all see "google.com" instead of
    the actual publisher.

    Returns the resolved URL, or the original url unchanged if it isn't a
    Google wrapper or the wrapper page can't be parsed (fail open — better to
    push a wrapper link occasionally than silently drop a real article).
    """
    if "google.com/goto" not in url:
        return url

    response = safe_get(url, session=session)
    if response is None:
        return url

    match = _GOOGLE_GOTO_HREF_RE.search(response.text)
    return match.group(1) if match else url


def search_tavily(query: str, count: int = 20, days: int = 7) -> list[dict]:
    """
    Query the Tavily Search API with topic="news".

    Free tier: 1,000 credits/month, renews every month, no card required — a
    basic news search costs 1 credit, so this covers roughly 1,000 discovery
    queries/month shared across every org/seed pointed at this provider.

    Args:
        query: Search string.
        count: Max results (Tavily caps "basic" depth at 20 per request).
        days:  How many days back to search. Tavily's "news" topic takes an
               explicit lookback window in days (unlike Google CSE's
               dateRestrict codes), so this maps directly.

    Returns:
        List of article dicts. Empty list if the API key is not configured
        or the request fails.
    """
    api_key = settings.CRAWLER.get("TAVILY_API_KEY", "")
    if not api_key:
        logger.warning("TAVILY_API_KEY not set — skipping Tavily search for '%s'", query)
        return []

    session = get_session()
    session.headers["Authorization"] = f"Bearer {api_key}"
    session.headers["Content-Type"] = "application/json"

    payload = {
        "query":        query,
        "topic":        "news",
        "days":         days,
        "max_results":  min(count, 20),
        "search_depth": "basic",
    }

    logger.info("Tavily news search: '%s' (count=%d, days=%d)", query, count, days)
    response = safe_post(TAVILY_ENDPOINT, session=session, json=payload)
    if response is None:
        return []

    results = []
    for item in response.json().get("results", []):
        url = item.get("url", "")
        if not url:
            continue
        url = _resolve_tavily_url(url, session)
        results.append(_article_dict(
            url=url,
            title=item.get("title", ""),
            snippet=item.get("content", ""),
            published_at=_parse_iso_date(item.get("published_date")),
        ))

    logger.info("Tavily returned %d articles for '%s'", len(results), query)
    return results


# ── Unified dispatcher ────────────────────────────────────────────────────────

def search(query: str, provider: str = "bing", **kwargs) -> list[dict]:
    """
    Single entry-point used by run_discovery().

    Args:
        query:    The search string.
        provider: "bing" | "google" | "serper" | "tavily"
        **kwargs: Forwarded to the underlying adapter.

    Raises:
        ValueError: If an unsupported provider is specified.
    """
    if provider == "bing":
        return search_bing(query, **kwargs)
    elif provider == "google":
        return search_google(query, **kwargs)
    elif provider == "serper":
        return search_serper(query, **kwargs)
    elif provider == "tavily":
        return search_tavily(query, **kwargs)
    else:
        raise ValueError(f"Unknown search provider '{provider}'. Use 'bing', 'google', 'serper', or 'tavily'.")
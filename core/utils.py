"""
core/utils.py

Shared HTTP and URL utilities used by every crawler layer.
Import these instead of using requests directly so all requests
go through the same session, timeout, and user-agent config.
"""
import logging
from urllib.parse import urlparse, urljoin

import requests
from django.conf import settings

logger = logging.getLogger(__name__)


# ── Session ────────────────────────────────────────────────────────────────────

def get_session() -> requests.Session:
    """
    Return a requests Session pre-configured with the crawler's
    User-Agent header.

    Always use this instead of requests.get() directly so every
    outbound request identifies itself consistently.
    """
    session = requests.Session()
    session.headers.update({
        "User-Agent": settings.CRAWLER["USER_AGENT"],
    })
    return session


# ── Safe HTTP ──────────────────────────────────────────────────────────────────

def safe_get(url: str, session: requests.Session | None = None, **kwargs):
    """
    Perform a GET request and return the Response, or None on any error.

    Never raises — callers check for None instead of try/catching.
    Respects settings.CRAWLER["REQUEST_TIMEOUT"] unless overridden.

    Args:
        url:     The URL to fetch.
        session: Optional existing session to reuse (avoids creating
                 a new TCP connection pool on every call).
        **kwargs: Passed through to requests.get / session.get.

    Returns:
        requests.Response on success, None on any error.

    Example:
        response = safe_get("https://example.com/rss")
        if response is None:
            return []   # network error already logged
        data = response.text
    """
    _session = session or get_session()
    timeout  = kwargs.pop("timeout", settings.CRAWLER["REQUEST_TIMEOUT"])

    try:
        response = _session.get(url, timeout=timeout, **kwargs)
        response.raise_for_status()
        return response
    except requests.HTTPError as exc:
        logger.warning("HTTP error fetching %s: %s", url, exc)
    except requests.ConnectionError as exc:
        logger.warning("Connection error fetching %s: %s", url, exc)
    except requests.Timeout:
        logger.warning("Timeout fetching %s (limit: %ss)", url, timeout)
    except requests.RequestException as exc:
        logger.warning("Request failed for %s: %s", url, exc)

    return None


# ── URL helpers ────────────────────────────────────────────────────────────────

def normalize_url(url: str, base: str = "") -> str:
    """
    Normalise a URL so that duplicates from the same page are caught.

    Steps applied:
    1. Resolve relative URLs against `base`  (e.g. /article/123 → https://example.com/article/123)
    2. Lower-case the scheme and host        (http://Example.COM → http://example.com)
    3. Strip URL fragments                   (url#section → url)

    Args:
        url:  The URL to normalise (may be relative).
        base: The page URL the link was found on (used for relative resolution).

    Returns:
        A normalised absolute URL string.

    Example:
        normalize_url("/news/story", "https://Example.COM/home#top")
        # → "https://example.com/news/story"
    """
    if base:
        url = urljoin(base, url)

    parsed = urlparse(url)
    normalised = parsed._replace(
        scheme=parsed.scheme.lower(),
        netloc=parsed.netloc.lower(),
        fragment="",           # strip #anchors
    )
    return normalised.geturl()


def get_domain(url: str) -> str:
    """
    Extract just the netloc (host) from a URL.

    Example:
        get_domain("https://www.miningweekly.com/article/123")
        # → "www.miningweekly.com"
    """
    return urlparse(url).netloc.lower()


def same_domain(url_a: str, url_b: str) -> bool:
    """
    Return True if both URLs share the same host.

    Used by the link extractor to avoid following links off-domain.

    Example:
        same_domain("https://example.com/a", "https://example.com/b")  # True
        same_domain("https://example.com/a", "https://other.com/b")    # False
    """
    return get_domain(url_a) == get_domain(url_b)


def is_valid_url(url: str) -> bool:
    """
    Quick sanity check — does this string look like a fetchable URL?
    Rejects empty strings, mailto: links, javascript:, etc.
    """
    try:
        parsed = urlparse(url)
        return parsed.scheme in ("http", "https") and bool(parsed.netloc)
    except Exception:
        return False
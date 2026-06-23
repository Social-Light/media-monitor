"""
fetcher/news_filter.py
───────────────────────
Decide whether a parsed page is genuine news/editorial coverage worth pushing
to the platform — as opposed to a corporate product/marketing/utility page.

Motivation: several seed sources are companies' own corporate websites
(e.g. www.sc.com, botswana.accessbankplc.com). Plain link extraction pulls in
every page — product pages ("Personal Loan", "Property Insurance"), branch
finders, contact pages, etc. Those are not media coverage, yet they mention the
company name everywhere, so the bridge used to capture them as CompetitorArticle
/ OnlineArticle rows.

Policy (applied at the bridge capture step only — discovery is unchanged):

  1. Reject clear product / marketing / utility pages by URL path
     (e.g. /personal, /products, /loans, /accounts, /contact, /about …),
     unless the path also carries a news/blog/press segment.

  2. Require a real publication date in the **current calendar month**. This
     keeps newspaper coverage recent and, as a side effect, drops the dateless
     product pages (which only ever had the crawl-date fallback).

  3. Require an editorial signal: a news/blog/press URL segment OR a body of at
     least MIN_BODY_WORDS words — so a thin landing page that happens to carry a
     date is still rejected.

is_news_article(parsed_article) -> bool   (never raises)
"""
import logging
import re
from urllib.parse import urlparse

from django.utils import timezone

logger = logging.getLogger(__name__)

# URL path segments that indicate editorial / news content.
NEWS_PATH_SIGNALS = (
    "/news", "/article", "/story", "/stories", "/post", "/posts",
    "/blog", "/blogs", "/press", "/press-release", "/press-releases",
    "/media", "/newsroom", "/publication", "/publications", "/insight",
    "/insights", "/opinion", "/opinions", "/feature", "/features",
    "/report", "/reports",
)

# URL path segments that indicate product / marketing / utility pages.
NON_NEWS_PATH_SIGNALS = (
    "/personal", "/products", "/product", "/loan", "/loans", "/insurance",
    "/account", "/accounts", "/banking", "/business", "/corporate",
    "/cards", "/card", "/rates", "/rate", "/tools", "/tool", "/calculator",
    "/contact", "/about", "/about-us", "/careers", "/career", "/branch",
    "/branches", "/find-a-branch", "/atm", "/atms", "/help", "/faq", "/faqs",
    "/login", "/register", "/apply", "/terms", "/privacy", "/legal",
    "/services", "/service", "/wealth", "/investment", "/investments",
    "/savings", "/mortgage", "/sitemap", "/search", "/cookie", "/cookies",
)

# Minimum words of body text for a page with no news/blog URL segment to still
# count as editorial content.
MIN_BODY_WORDS = 80


def _path_segments(path: str) -> list[str]:
    """Return the URL path as leading-slash segments, e.g. '/news/foo' → ['/news', '/foo']."""
    return ["/" + seg for seg in path.lower().strip("/").split("/") if seg]


def _has_signal(segments: list[str], signals: tuple) -> bool:
    """True if any path segment exactly matches one of the signal segments."""
    return any(seg in signals for seg in segments)


def _is_current_month(dt) -> bool:
    """True if dt (date or datetime) falls in the current calendar month."""
    if dt is None:
        return False
    now = timezone.localtime(timezone.now())
    return (dt.year, dt.month) == (now.year, now.month)


def is_news_article(parsed_article) -> bool:
    """
    Return True if this parsed page should be treated as news/editorial coverage
    and pushed to the platform. Never raises — on any unexpected error it returns
    False (fail closed: better to skip a borderline page than flood the platform).
    """
    try:
        url = parsed_article.url or ""
        path = urlparse(url).path
        segments = _path_segments(path)

        has_news_path = _has_signal(segments, NEWS_PATH_SIGNALS)
        has_non_news_path = _has_signal(segments, NON_NEWS_PATH_SIGNALS)

        # 1. Clear product / marketing / utility page — reject (unless it also
        #    lives under a news/blog/press section).
        if has_non_news_path and not has_news_path:
            logger.debug("news_filter: skip (product/utility path) %s", url)
            return False

        # 2. Require a real publication date in the current calendar month.
        if not _is_current_month(parsed_article.published_at):
            logger.debug("news_filter: skip (no current-month date) %s", url)
            return False

        # 3. Editorial signal: a news/blog/press path OR a substantial body.
        body_words = len((parsed_article.body_text or "").split())
        if not has_news_path and body_words < MIN_BODY_WORDS:
            logger.debug("news_filter: skip (no news path, thin body %d words) %s",
                         body_words, url)
            return False

        return True
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("news_filter: error classifying article, skipping: %s", exc)
        return False

"""
discovery/services/apify.py
────────────────────────────
Discover social-media mentions of keywords via Apify Actors.

Unlike news sites, Facebook / Instagram / LinkedIn / X cannot be crawled
directly (login walls + anti-scraping + ToS). Apify runs maintained scrapers
("Actors") and returns structured posts over a single REST API, so this adapter
is the social equivalent of search_api.py — same role, different upstream.

Per platform we run one Actor (configurable, see settings.CRAWLER["APIFY_ACTORS"])
through the synchronous run endpoint:

    POST https://api.apify.com/v2/acts/{actor}/run-sync-get-dataset-items?token=…

which starts the run, waits for it, and returns the dataset items in one call.

Every Actor has its own input/output schema, and the marketplace's preferred
scraper for a platform changes over time. Two design choices keep us resilient:

  • Input  — _build_input() produces a sensible default body per platform, but a
             seed's meta["actor_input"] is merged on top, so any field can be
             overridden (or a brand-new Actor supported) without a code change.
  • Output — _normalise() reads each field through a list of candidate keys, so
             differing field names across Actors still map onto our one shape.

fetch_mentions() returns a list of dicts in the shape every discovery adapter
uses, plus the extra social fields the ingest layer needs:

    {
        "url":           str,            # permalink to the post
        "platform":      str,            # "x" | "facebook" | "instagram" | "linkedin"
        "author":        str,            # display name
        "author_handle": str,            # @handle / username
        "text":          str,            # full post text
        "title":         str,            # synthesised short headline
        "snippet":       str,            # == text (keyword filtering / DiscoveredURL)
        "published_at":  datetime | None,
        "engagement":    dict,           # {"likes", "shares", "comments"}
        "source_type":   "social",
    }

Never raises — a missing token, unknown platform, or any HTTP/JSON failure is
logged and yields an empty list, exactly like the other discovery services.
"""
import logging
import re
from datetime import datetime, timezone as dt_timezone

from dateutil import parser as date_parser
from django.conf import settings

from core.utils import get_session

logger = logging.getLogger(__name__)

APIFY_RUN_SYNC_URL = "https://api.apify.com/v2/acts/{actor}/run-sync-get-dataset-items"

# Hostname recorded as the article's source_domain per platform. Drives both
# attribution and the platform-side "source" column.
PLATFORM_DOMAINS = {
    "x":         "x.com",
    "facebook":  "facebook.com",
    "instagram": "instagram.com",
    "linkedin":  "linkedin.com",
}

# Human labels used when synthesising a headline for a post with no text.
PLATFORM_LABELS = {
    "x":         "X",
    "facebook":  "Facebook",
    "instagram": "Instagram",
    "linkedin":  "LinkedIn",
}

# Candidate keys per field, in priority order. The first key present and
# non-empty on an Actor item wins — this absorbs naming differences between
# Actors without per-Actor parsing code.
_URL_KEYS    = ("url", "postUrl", "post_url", "tweetUrl", "link", "permalink", "postLink")
_TEXT_KEYS   = ("text", "fullText", "content", "caption", "postText", "message", "description")
_NAME_KEYS   = ("authorName", "ownerFullName", "fullName", "name", "displayName")
_HANDLE_KEYS = ("authorUsername", "ownerUsername", "username", "userName", "handle", "screenName")
_DATE_KEYS   = ("timestamp", "createdAt", "date", "publishedAt", "time", "postedAtISO", "datePosted")
_LIKE_KEYS   = ("likes", "likesCount", "likeCount", "favoriteCount", "favouriteCount",
                "reactionsCount", "numLikes", "total_reactions")
_SHARE_KEYS  = ("shares", "sharesCount", "retweetCount", "reshareCount", "numShares")
_COMMENT_KEYS = ("comments", "commentsCount", "replyCount", "numComments")
_FOLLOWER_KEYS = ("followers", "followersCount", "followerCount", "subscribers",
                  "subscriberCount", "fans", "fanCount")

# Objects some Actors nest date/engagement fields inside (LinkedIn: posted_at,
# stats; others: metrics). Searched after the top level by _deep_first.
_NESTED_CONTAINERS = ("posted_at", "postedAt", "stats", "metrics", "engagement", "statistics")


def _first(item: dict, keys: tuple, default=None):
    """Return item[k] for the first key whose value is truthy, else `default`."""
    for key in keys:
        value = item.get(key)
        if value not in (None, "", [], {}):
            return value
    return default


def _deep_first(item: dict, keys: tuple, default=None):
    """
    Like _first, but if no top-level key matches, also look one level down inside
    the known nested containers (posted_at, stats, …). Lets one normaliser handle
    Actors that group date/engagement fields under a sub-object.
    """
    top = _first(item, keys)
    if top not in (None, "", [], {}):
        return top
    for container in _NESTED_CONTAINERS:
        obj = item.get(container)
        if isinstance(obj, dict):
            nested = _first(obj, keys)
            if nested not in (None, "", [], {}):
                return nested
    return default


def _nested_author(item: dict, keys: tuple) -> str:
    """
    Resolve an author field that may sit at the top level or nested under
    an "author"/"user"/"owner" object (Actors disagree on which).
    """
    top = _first(item, keys)
    if top:
        return str(top)
    for container in ("author", "user", "owner"):
        obj = item.get(container)
        if isinstance(obj, dict):
            nested = _first(obj, keys)
            if nested:
                return str(nested)
    return ""


def _extract_followers(item: dict) -> int:
    """
    Best-effort follower/subscriber count for the post's author/page.
    Tries numeric follower fields (top-level or nested under author/user/owner),
    then falls back to parsing "29,828 followers" out of a headline string
    (LinkedIn company pages put the count there). Returns 0 when unknown.
    """
    val = _first(item, _FOLLOWER_KEYS)
    if val is None:
        for container in ("author", "user", "owner"):
            obj = item.get(container)
            if isinstance(obj, dict):
                val = _first(obj, _FOLLOWER_KEYS)
                if val is not None:
                    break
    if isinstance(val, (int, float)):
        return int(val)
    if isinstance(val, str) and val.replace(",", "").strip().isdigit():
        return int(val.replace(",", ""))

    for headline in (_nested_author(item, ("headline",)), str(item.get("headline") or "")):
        match = re.search(r"([\d,]+)\s+followers", headline, re.IGNORECASE)
        if match:
            return int(match.group(1).replace(",", ""))
    return 0


def _parse_date(raw):
    if not raw:
        return None
    # Epoch timestamps (seconds or milliseconds) arrive as ints/numeric strings.
    if isinstance(raw, (int, float)) or (isinstance(raw, str) and raw.isdigit()):
        try:
            ts = float(raw)
            if ts > 1e12:      # milliseconds → seconds
                ts /= 1000.0
            return datetime.fromtimestamp(ts, tz=dt_timezone.utc)
        except (ValueError, OverflowError, OSError):
            return None
    try:
        return date_parser.parse(str(raw))
    except (ValueError, OverflowError, TypeError):
        return None


def _build_input(platform: str, terms: list[str], max_items: int) -> dict:
    """
    Default request body per platform. Best-effort against each Actor's current
    input schema; override via seed meta["actor_input"] when an Actor differs.
    """
    query = " ".join(terms)
    if platform == "x":
        # apidojo/tweet-scraper — real keyword search.
        return {"searchTerms": terms, "maxItems": max_items, "sort": "Latest"}
    if platform == "facebook":
        # apify/facebook-posts-scraper is PAGE-based (no open keyword search on
        # Facebook). The Page URLs must be supplied per seed via
        # meta["actor_input"] = {"startUrls": [{"url": "https://facebook.com/<page>"}]};
        # posts are then keyword-filtered downstream / matched by the bridge.
        return {"resultsLimit": max_items}
    if platform == "instagram":
        # Instagram has no working keyword/hashtag *search* endpoint; scrape the
        # hashtag page directly. Each term becomes an explore/tags/<tag>/ URL
        # (lower-cased, non-alphanumerics stripped, since hashtags have none).
        tags = []
        for term in terms:
            tag = re.sub(r"[^a-z0-9]", "", term.lower())
            if tag:
                tags.append(f"https://www.instagram.com/explore/tags/{tag}/")
        return {"directUrls": tags, "resultsType": "posts", "resultsLimit": max_items}
    if platform == "linkedin":
        # apimaestro/linkedin-posts-search-scraper-no-cookies — singular "keyword".
        # Default to the most recent posts from the past month (current-month
        # monitoring). To back-date / widen, override via the seed's
        # meta["actor_input"], e.g. {"date_filter": ""} (all time) or
        # {"date_filter": "past-week"}; date_filter ∈ {"", past-1h, past-24h,
        # past-week, past-month}, sort_type ∈ {relevance, date_posted}.
        return {"keyword": query, "limit": max_items,
                "sort_type": "date_posted", "date_filter": "past-month"}
    # Unknown platform — generic body; the caller already validated, so this is
    # only reached for a custom platform key the operator wired up themselves.
    return {"query": query, "maxItems": max_items}


def _normalise(platform: str, item: dict) -> dict | None:
    """
    Map one raw Actor dataset item onto our standard social dict.
    Returns None if the item has neither a URL nor any text (unusable).
    """
    if not isinstance(item, dict):
        return None

    url  = _first(item, _URL_KEYS, "")
    text = _first(item, _TEXT_KEYS, "")
    if not url and not text:
        return None

    author = _nested_author(item, _NAME_KEYS)
    handle = _nested_author(item, _HANDLE_KEYS)
    text   = str(text).strip()

    # A short, human headline — the post's first line, else an attribution.
    label = PLATFORM_LABELS.get(platform, platform.title())
    if text:
        first_line = text.splitlines()[0].strip()
        title = first_line[:200] or text[:200]
    else:
        who = author or (f"@{handle}" if handle else "Unknown")
        title = f"{who} on {label}"

    return {
        "url":           str(url),
        "platform":      platform,
        "author":        author,
        "author_handle": handle,
        "text":          text,
        "title":         title,
        "snippet":       text,
        "published_at":  _parse_date(_deep_first(item, _DATE_KEYS)),
        "followers":     _extract_followers(item),
        "engagement": {
            "likes":    _deep_first(item, _LIKE_KEYS, 0),
            "shares":   _deep_first(item, _SHARE_KEYS, 0),
            "comments": _deep_first(item, _COMMENT_KEYS, 0),
        },
        "source_type":   "social",
    }


def _run_actor(actor: str, payload: dict, token: str, timeout: int) -> list[dict]:
    """
    POST to the run-sync-get-dataset-items endpoint and return the dataset
    items. Returns [] on any error (never raises).

    The actor id is given as "username/actor-name" everywhere else, but the REST
    API path requires the "username~actor-name" form. The token is sent as a
    Bearer header (not a query param) so it never lands in logs / error URLs.
    """
    url = APIFY_RUN_SYNC_URL.format(actor=actor.replace("/", "~"))
    session = get_session()
    try:
        response = session.post(
            url,
            headers={"Authorization": f"Bearer {token}"},
            json=payload,
            timeout=timeout,
        )
        response.raise_for_status()
        data = response.json()
    except Exception as exc:  # network, HTTP, or JSON decode
        logger.warning("Apify run failed for actor '%s': %s", actor, exc)
        return []

    if not isinstance(data, list):
        logger.warning("Apify actor '%s' returned non-list payload (%s)", actor, type(data).__name__)
        return []
    return data


def fetch_mentions(platform: str, terms, *, max_items: int | None = None,
                   token: str | None = None, actor_input: dict | None = None) -> list[dict]:
    """
    Fetch recent posts mentioning `terms` from one social platform via Apify.

    Args:
        platform:    "x" | "facebook" | "instagram" | "linkedin".
        terms:       Keyword string or list of keyword strings to search for.
        max_items:   Cap on posts to fetch. Defaults to settings APIFY_MAX_ITEMS.
        token:       Apify API token. Defaults to settings APIFY_API_TOKEN.
        actor_input: Optional dict merged onto the default Actor input body,
                     for per-seed tuning or supporting a non-default Actor.

    Returns:
        List of normalised social post dicts. Empty list if the token is unset,
        the platform is unknown, or the Actor run fails.
    """
    platform = (platform or "").lower().strip()
    if isinstance(terms, str):
        terms = [terms]
    terms = [t.strip() for t in (terms or []) if t and t.strip()]

    token = token or settings.CRAWLER.get("APIFY_API_TOKEN", "")
    if not token:
        logger.warning("APIFY_API_TOKEN not set — skipping social discovery for %s", platform)
        return []

    actor = settings.CRAWLER.get("APIFY_ACTORS", {}).get(platform)
    if not actor:
        logger.warning("No Apify actor configured for platform '%s' — skipping", platform)
        return []

    # URL-driven actors (Facebook page posts, Instagram direct profile/hashtag
    # URLs supplied via actor_input) don't need search terms — the startUrls /
    # directUrls drive them. Only keyword-search actors require terms.
    url_driven = bool(actor_input and (actor_input.get("startUrls")
                                       or actor_input.get("directUrls")))
    if not terms and not url_driven:
        logger.warning("No search terms for %s social discovery — skipping", platform)
        return []

    max_items = max_items or settings.CRAWLER.get("APIFY_MAX_ITEMS", 50)
    timeout   = settings.CRAWLER.get("APIFY_TIMEOUT", 120)

    payload = _build_input(platform, terms, max_items)
    if actor_input:
        payload.update(actor_input)

    logger.info("Apify %s search via '%s': terms=%s max=%d", platform, actor, terms, max_items)
    items = _run_actor(actor, payload, token, timeout)

    results = []
    for raw in items:
        post = _normalise(platform, raw)
        if post:
            results.append(post)

    logger.info("Apify %s returned %d usable post(s) for %s", platform, len(results), terms)
    return results

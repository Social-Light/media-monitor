"""
fetcher/bridge.py
──────────────────
Push parsed crawler articles into the media-monitoring **platform**.

Two entry points, both called at the end of fetcher.services.parse_page():

  push_to_platform(parsed_article) -> list[OnlineArticle]
      For each active Organization, match the article against the org's keywords
      and, on a hit, create an OnlineArticle (the org's own coverage).

  push_competitor_to_platform(parsed_article) -> list[CompetitorArticle]
      For each Competitor of each active Organization, match the article against
      Competitor.match_terms() and, on a hit, create a CompetitorArticle.

Routing/DB selection is transparent: in production these models are routed to the
shared 'platform' database; in dev/test they live in the default DB. See
platform_sync.routers.PlatformRouter.

Capture decision: an article is linked to an org when one of its tracked terms
appears in the title or body (case-insensitive). The stored relevancy, however,
is computed with the platform's own 0–100 scorer (fetcher/relevancy.py, vendored
from monitor/relevancy.py) over the headline + summary — so crawler-written
coverage ranks identically to coverage the platform ingests itself.

Existing coverage is never duplicated — (organization, url) is checked first.
"""
import logging

from django.utils import timezone

from fetcher.relevancy import compute_relevancy
from platform_sync.models import (
    CompetitorArticle,
    OnlineArticle,
    Organization,
)

logger = logging.getLogger(__name__)

VALID_SENTIMENTS = {"positive", "negative", "neutral", "mixed"}


def _resolve_sentiment(parsed_article) -> str:
    """Pull sentiment from the article's NLP signals, defaulting to 'neutral'."""
    sentiment = (parsed_article.signals or {}).get("sentiment", "neutral")
    return sentiment if sentiment in VALID_SENTIMENTS else "neutral"


def _published_date(parsed_article):
    """OnlineArticle.date_published is required; fall back to today when unknown."""
    published = parsed_article.published_at
    return published.date() if published else timezone.now().date()


def _matched_terms(terms, title: str, body: str) -> list:
    """Return the original-cased terms found (case-insensitively) in title or body."""
    matched = []
    for term in terms:
        needle = term.strip().lower()
        if needle and (needle in title or needle in body):
            matched.append(term)
    return matched


def push_to_platform(parsed_article) -> list:
    """
    Create OnlineArticle rows for every active organisation whose keywords match
    this article. Returns the list of newly created OnlineArticle instances.
    """
    created: list = []

    title = (parsed_article.title or "").lower()
    body = (parsed_article.body_text or "").lower()
    if not title and not body:
        return created

    url = parsed_article.url
    sentiment = _resolve_sentiment(parsed_article)
    date_published = _published_date(parsed_article)
    source = (parsed_article.source_domain or "")[:200]
    country = (parsed_article.country or "")[:100]

    for org in Organization.objects.filter(status="active"):
        keywords = list(org.keywords.all())
        matched = _matched_terms([kw.keyword for kw in keywords], title, body)
        if not matched:
            continue

        if OnlineArticle.objects.filter(organization=org, url=url).exists():
            continue

        # Score on headline + summary with the platform's own 0–100 scorer so
        # crawler coverage ranks like platform-ingested coverage. Competitors
        # count toward relevancy too (a competitor mention is relevant coverage).
        competitors = list(org.competitors.all())
        relevancy = compute_relevancy(
            parsed_article.title, parsed_article.summary,
            keywords=keywords, competitors=competitors,
        )

        article = OnlineArticle.objects.create(
            organization=org,
            source=source,
            headline=parsed_article.title or "",
            summary=parsed_article.summary or "",
            url=url[:2000],
            date_published=date_published,
            country=country,
            sentiment=sentiment,
            coverage="Earned",
            relevancy=relevancy,
        )
        logger.info(
            "Bridge: OnlineArticle for org '%s' from %s (relevancy=%.2f, terms=%s)",
            org.name, url, relevancy, matched,
        )
        created.append(article)

    return created


def push_competitor_to_platform(parsed_article) -> list:
    """
    Create CompetitorArticle rows for every competitor (of an active org) whose
    match_terms() appear in this article. Returns the new CompetitorArticles.
    """
    created: list = []

    title = (parsed_article.title or "").lower()
    body = (parsed_article.body_text or "").lower()
    if not title and not body:
        return created

    url = parsed_article.url
    sentiment = _resolve_sentiment(parsed_article)
    date_published = _published_date(parsed_article)
    source = (parsed_article.source_domain or "")[:200]
    country = (parsed_article.country or "")[:100]

    for org in Organization.objects.filter(status="active"):
        for competitor in org.competitors.all():
            matched = _matched_terms(competitor.match_terms(), title, body)
            if not matched:
                continue

            if CompetitorArticle.objects.filter(
                organization=org, competitor=competitor, url=url
            ).exists():
                continue

            article = CompetitorArticle.objects.create(
                organization=org,
                competitor=competitor,
                company_name=competitor.name[:200],
                headline=parsed_article.title or "",
                summary=parsed_article.summary or "",
                url=url[:2000],
                source=source,
                date_published=date_published,
                country=country,
                sentiment=sentiment,
                matched_keywords=", ".join(matched)[:300],
            )
            logger.info(
                "Bridge: CompetitorArticle for competitor '%s' (org '%s') from %s (terms=%s)",
                competitor.name, org.name, url, matched,
            )
            created.append(article)

    return created

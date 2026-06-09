"""
matching/matcher.py
────────────────────
match_article(parsed_article) -> list[ArticleMatch]

Checks a parsed article's title and body against every active keyword of
every active organisation, recording an ArticleMatch for each organisation
whose keyword is found.

A keyword found in the title ranks above one found only in the body, so each
(article, organisation, keyword) hit is recorded once with the strongest
location. Re-running on the same article is idempotent (get_or_create on the
unique constraint), so a re-parse never produces duplicate matches.

Like the other pipeline services, this never raises — failures are logged and
the function returns whatever it managed to create.
"""
import logging

from matching.models import ArticleMatch, Organisation

logger = logging.getLogger(__name__)

# A title mention is a stronger signal than a passing reference in the body.
CONFIDENCE_TITLE = 1.0
CONFIDENCE_BODY  = 0.5


def match_article(parsed_article) -> list[ArticleMatch]:
    """
    Match a ParsedArticle against all active organisation keywords.

    For each active organisation keyword found in the article title or body,
    a single ArticleMatch is created (title hits take precedence over body
    hits). Returns the list of newly created ArticleMatch instances.
    """
    new_matches: list[ArticleMatch] = []

    title = (parsed_article.title or "").lower()
    body  = (parsed_article.body_text or "").lower()
    if not title and not body:
        return new_matches

    for org in Organisation.objects.filter(is_active=True).prefetch_related("keywords"):
        for kw in org.keywords.all():
            if not kw.is_active:
                continue

            needle = kw.keyword.strip().lower()
            if not needle:
                continue

            if needle in title:
                matched_in, confidence = ArticleMatch.MatchedIn.TITLE, CONFIDENCE_TITLE
            elif needle in body:
                matched_in, confidence = ArticleMatch.MatchedIn.BODY, CONFIDENCE_BODY
            else:
                continue

            match, created = ArticleMatch.objects.get_or_create(
                parsed_article=parsed_article,
                organisation=org,
                matched_keyword=kw.keyword,
                defaults={"matched_in": matched_in, "confidence": confidence},
            )
            if created:
                logger.info(
                    "Article %d matched organisation '%s' on '%s' (%s)",
                    parsed_article.pk, org.name, kw.keyword, matched_in,
                )
                new_matches.append(match)

    return new_matches

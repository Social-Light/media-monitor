import logging

from alerts.models import Alert, AlertMatch

logger = logging.getLogger(__name__)


def matches_alert(alert: Alert, article) -> list[str]:
    """
    Check whether an article satisfies an alert's filters and keywords.

    Returns the list of matched keywords (subset of alert.keywords_list).
    An empty list means no match.
    """
    if alert.source_domain and article.source_domain != alert.source_domain:
        return []
    if alert.language and article.language != alert.language:
        return []
    if alert.sentiment:
        if article.signals.get("sentiment", "") != alert.sentiment:
            return []

    text = f"{article.title} {article.body_text}".lower()
    return [kw for kw in alert.keywords_list if kw in text]


def check_alerts(article) -> list[AlertMatch]:
    """
    Test all active alerts against a ParsedArticle.

    For each alert that matches, creates an AlertMatch (skipping duplicates).
    Returns the list of newly created AlertMatch instances.
    """
    new_matches: list[AlertMatch] = []

    for alert in Alert.objects.filter(is_active=True):
        matched_keywords = matches_alert(alert, article)
        if not matched_keywords:
            continue

        match, created = AlertMatch.objects.get_or_create(
            alert=alert,
            article=article,
            defaults={"matched_keywords": matched_keywords},
        )
        if created:
            logger.info(
                "Alert '%s' matched article %d on: %s",
                alert.name, article.pk, matched_keywords,
            )
            new_matches.append(match)

    return new_matches

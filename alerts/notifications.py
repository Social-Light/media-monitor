import logging

import requests as _requests
from django.conf import settings
from django.core.mail import send_mail
from django.utils import timezone

from alerts.models import AlertMatch

logger = logging.getLogger(__name__)


def send_email_alert(match: AlertMatch) -> None:
    """Send an email notification for an AlertMatch. Raises on failure."""
    alert   = match.alert
    article = match.article

    subject = f"[Alert: {alert.name}] {article.title[:80]}"
    body = (
        f"Alert: {alert.name}\n"
        f"Matched keywords: {', '.join(match.matched_keywords)}\n\n"
        f"Title: {article.title}\n"
        f"URL: {article.url}\n"
        f"Domain: {article.source_domain}\n"
        f"Published: {article.published_at or '—'}\n\n"
        f"Summary: {article.summary or '(none)'}\n"
    )

    send_mail(
        subject=subject,
        message=body,
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[alert.email],
        fail_silently=False,
    )
    logger.debug("Email alert sent for match %d to %s", match.pk, alert.email)


def send_webhook_alert(match: AlertMatch) -> None:
    """POST article data as JSON to the alert's webhook URL. Raises on failure."""
    alert   = match.alert
    article = match.article

    payload = {
        "alert_name":       alert.name,
        "matched_keywords": match.matched_keywords,
        "article": {
            "id":            article.pk,
            "title":         article.title,
            "url":           article.url,
            "source_domain": article.source_domain,
            "language":      article.language,
            "published_at":  article.published_at.isoformat() if article.published_at else None,
            "summary":       article.summary,
            "sentiment":     article.signals.get("sentiment", ""),
        },
    }

    response = _requests.post(alert.webhook_url, json=payload, timeout=10)
    response.raise_for_status()
    logger.debug("Webhook alert sent for match %d to %s", match.pk, alert.webhook_url)


def dispatch_notifications(match: AlertMatch) -> None:
    """
    Send all configured notifications for a match.

    Marks the match as notified if at least one channel succeeded.
    Logs individual delivery failures but never raises.
    """
    notified = False

    if match.alert.email:
        try:
            send_email_alert(match)
            notified = True
        except Exception as exc:
            logger.warning("Email alert failed for match %d: %s", match.pk, exc)

    if match.alert.webhook_url:
        try:
            send_webhook_alert(match)
            notified = True
        except Exception as exc:
            logger.warning("Webhook alert failed for match %d: %s", match.pk, exc)

    if notified:
        AlertMatch.objects.filter(pk=match.pk).update(
            notified=True,
            notified_at=timezone.now(),
        )

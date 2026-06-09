"""
fetcher/platform_alerts.py
───────────────────────────
Fire notifications off the *platform's* Alert model (platform_sync.Alert →
monitor_alert) when the crawler captures matching coverage.

Flow:
  parse_page() → push_to_platform() creates OnlineArticle rows → notify_for_article()
  checks each active Alert of that article's organisation. A match creates a
  PlatformAlertNotification (crawler-side dedup record). For `immediate` alerts the
  email is sent right away; daily/weekly/monthly matches are left pending and
  flushed later by send_pending_digests() (run via the send_alert_digests command
  on a schedule).

Matching: an Alert's comma-separated keywords are checked (case-insensitive)
against the article's headline + summary. An Alert with no keywords matches every
capture for its organisation (the org-level keyword match already qualified it).

Nothing here raises out of the notification path for email failures — those are
logged so a parse is never lost to an SMTP hiccup.
"""
import logging
from collections import defaultdict

from django.conf import settings
from django.core.mail import send_mail
from django.utils import timezone

from alerts.models import PlatformAlertNotification
from platform_sync.models import Alert, OnlineArticle

logger = logging.getLogger(__name__)


def _matches(alert, text_lower: str):
    """Return (is_match, matched_keywords). Empty alert keywords → match all."""
    kws = alert.keyword_list()
    if not kws:
        return True, []
    matched = [k for k in kws if k in text_lower]
    return bool(matched), matched


def _article_lines(article) -> str:
    bits = [f"• {article.headline}"]
    if article.source:
        bits.append(f"  Source: {article.source}")
    if article.url:
        bits.append(f"  {article.url}")
    if article.date_published:
        bits.append(f"  Published: {article.date_published}")
    return "\n".join(bits)


def _send_email(subject: str, body: str, recipient: str):
    send_mail(
        subject=subject,
        message=body,
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[recipient],
        fail_silently=False,
    )


def notify_for_article(online_article) -> list:
    """
    Check every active platform Alert for this article's organisation. Create a
    PlatformAlertNotification per new (alert, article) match; send immediately for
    `immediate` alerts. Returns the list of newly created notification records.
    """
    new_notifs: list = []

    text = f"{online_article.headline} {online_article.summary}".lower()
    org_id = online_article.organization_id

    for alert in Alert.objects.filter(organization_id=org_id, is_active=True):
        is_match, matched = _matches(alert, text)
        if not is_match:
            continue

        notif, created = PlatformAlertNotification.objects.get_or_create(
            alert_id=alert.id,
            article_id=online_article.id,
            defaults={
                "organization_id": str(org_id),
                "alert_name": alert.name,
                "recipient": alert.email,
                "matched_keywords": matched,
                "frequency": alert.frequency,
            },
        )
        if not created:
            continue
        new_notifs.append(notif)

        if alert.frequency == "immediate" and alert.email:
            subject = alert.email_subject or f"[Social Light] {alert.name}"
            body = (
                f"New coverage matched your alert '{alert.name}':\n\n"
                f"{_article_lines(online_article)}\n"
            )
            if matched:
                body += f"\nMatched keywords: {', '.join(matched)}\n"
            try:
                _send_email(subject, body, alert.email)
                notif.sent = True
                notif.sent_at = timezone.now()
                notif.save(update_fields=["sent", "sent_at"])
                logger.info("Immediate platform alert '%s' emailed to %s", alert.name, alert.email)
            except Exception as exc:
                logger.warning("Immediate alert email failed (alert %s): %s", alert.id, exc)

    return new_notifs


def send_pending_digests(frequency: str | None = None) -> int:
    """
    Email a digest for all unsent PlatformAlertNotifications, grouped per alert,
    and mark them sent. Optionally restrict to one frequency (e.g. 'daily').
    Returns the number of notifications flushed. Run from the send_alert_digests
    command on a schedule (Celery beat / cron).
    """
    qs = PlatformAlertNotification.objects.filter(sent=False)
    if frequency:
        qs = qs.filter(frequency=frequency)

    groups = defaultdict(list)
    for notif in qs:
        groups[notif.alert_id].append(notif)

    flushed = 0
    for alert_id, notifs in groups.items():
        try:
            alert = Alert.objects.get(id=alert_id)
        except Alert.DoesNotExist:
            continue
        if not alert.email:
            continue

        article_ids = [n.article_id for n in notifs]
        articles = list(OnlineArticle.objects.filter(id__in=article_ids))
        if not articles:
            continue

        subject = alert.email_subject or f"[Social Light] {alert.name} — {len(articles)} update(s)"
        body = (
            f"{len(articles)} new item(s) matched your alert '{alert.name}':\n\n"
            + "\n\n".join(_article_lines(a) for a in articles)
            + "\n"
        )
        try:
            _send_email(subject, body, alert.email)
        except Exception as exc:
            logger.warning("Digest email failed (alert %s): %s", alert_id, exc)
            continue

        now = timezone.now()
        for notif in notifs:
            notif.sent = True
            notif.sent_at = now
            notif.save(update_fields=["sent", "sent_at"])
        flushed += len(notifs)
        logger.info("Digest for alert '%s' emailed to %s (%d items)", alert.name, alert.email, len(articles))

    return flushed

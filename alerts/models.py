from django.db import models

from core.models import TimeStampedModel


class Alert(TimeStampedModel):
    """
    A named rule that fires when a parsed article contains one or more keywords.

    Optional filters (source_domain, language, sentiment) narrow the match.
    At least one of email or webhook_url should be set for the alert to be useful.
    """

    class SentimentFilter(models.TextChoices):
        POSITIVE = "positive", "Positive"
        NEGATIVE = "negative", "Negative"
        NEUTRAL  = "neutral",  "Neutral"

    name = models.CharField(
        max_length=255,
        help_text="Human-readable label, e.g. 'Botswana Mining Alerts'.",
    )
    keywords = models.TextField(
        help_text=(
            "Comma-separated keywords. An article matches if any keyword "
            "appears (case-insensitive) in the title or body."
        ),
    )

    # ── Optional filters ──────────────────────────────────────────────────────
    source_domain = models.CharField(
        max_length=255,
        blank=True,
        help_text="Restrict to this domain only, e.g. 'miningweekly.com'. Leave blank for all.",
    )
    language = models.CharField(
        max_length=10,
        blank=True,
        help_text="ISO 639-1 code, e.g. 'en'. Leave blank for all languages.",
    )
    sentiment = models.CharField(
        max_length=20,
        blank=True,
        choices=SentimentFilter.choices,
        help_text="Restrict to articles with this sentiment. Leave blank for all.",
    )

    # ── Notification channels ─────────────────────────────────────────────────
    email = models.EmailField(
        blank=True,
        help_text="Email address to notify on match.",
    )
    webhook_url = models.URLField(
        blank=True,
        help_text="HTTP endpoint to POST a JSON payload to on match.",
    )

    is_active = models.BooleanField(default=True, db_index=True)

    class Meta:
        ordering = ["name"]
        verbose_name = "Alert"
        verbose_name_plural = "Alerts"

    def __str__(self):
        return self.name

    @property
    def keywords_list(self) -> list[str]:
        """Return keywords as a cleaned, lowercased list."""
        return [k.strip().lower() for k in self.keywords.split(",") if k.strip()]


class AlertMatch(TimeStampedModel):
    """
    Records that a specific article triggered a specific alert.

    Created by check_alerts(); notified is set to True after notifications
    are dispatched successfully.
    """

    alert = models.ForeignKey(
        Alert,
        on_delete=models.CASCADE,
        related_name="matches",
    )
    article = models.ForeignKey(
        "fetcher.ParsedArticle",
        on_delete=models.CASCADE,
        related_name="alert_matches",
    )
    matched_keywords = models.JSONField(
        default=list,
        help_text="Which keywords from the alert were found in this article.",
    )
    notified = models.BooleanField(
        default=False,
        db_index=True,
        help_text="True once at least one notification was sent successfully.",
    )
    notified_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Timestamp of the last successful notification dispatch.",
    )

    class Meta:
        ordering = ["-created_at"]
        unique_together = [["alert", "article"]]
        verbose_name = "Alert Match"
        verbose_name_plural = "Alert Matches"

    def __str__(self):
        return f"{self.alert.name} → {self.article.title[:60]}"


class PlatformAlertNotification(TimeStampedModel):
    """
    Dedup + audit record for notifications fired off the *platform's* Alert model
    (platform_sync.Alert / monitor_alert) when the crawler captures matching
    coverage.

    This lives in the crawler's own database (NOT the platform DB) because the
    platform's schema is read-only to us — we can't add a "sent" table there.
    It references the platform Alert and OnlineArticle by their integer PKs
    (no cross-database ForeignKey).
    """

    alert_id = models.IntegerField(
        db_index=True, help_text="PK of the platform_sync.Alert (monitor_alert)."
    )
    article_id = models.IntegerField(
        help_text="PK of the platform_sync.OnlineArticle that triggered this."
    )
    organization_id = models.CharField(
        max_length=64, blank=True, help_text="Platform Organization UUID (for reference)."
    )
    alert_name = models.CharField(max_length=200, blank=True)
    recipient = models.EmailField(blank=True)
    matched_keywords = models.JSONField(default=list)
    frequency = models.CharField(max_length=20, blank=True)
    sent = models.BooleanField(default=False, db_index=True)
    sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        unique_together = [["alert_id", "article_id"]]
        verbose_name = "Platform Alert Notification"
        verbose_name_plural = "Platform Alert Notifications"

    def __str__(self):
        state = "sent" if self.sent else "pending"
        return f"alert#{self.alert_id} article#{self.article_id} ({state})"

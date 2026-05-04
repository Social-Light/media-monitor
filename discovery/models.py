"""
discovery/models.py

Three models that represent the Discovery layer:

  SeedSource      — a root source the crawler should watch
      └─ DiscoveredURL  — a single URL found from a seed
             └─ CrawlJob    — one attempt to fetch/process that URL
"""
import hashlib

from django.db import models
from django.utils import timezone

from core.models import TimeStampedModel, URLStatusChoices


# ─────────────────────────────────────────────────────────────────────────────
# Choices
# ─────────────────────────────────────────────────────────────────────────────

class SourceType(models.TextChoices):
    """
    How this seed source is crawled.
    Determines which discovery service is used at runtime.
    """
    RSS        = "rss",        "RSS / Atom Feed"
    SITEMAP    = "sitemap",    "XML Sitemap"
    SEED_URL   = "seed_url",   "Seed URL (link extraction)"
    SEARCH_API = "search_api", "Search API (Bing / Google)"
    MANUAL     = "manual",     "Manually Added"


# ─────────────────────────────────────────────────────────────────────────────
# Model 1 — SeedSource
# ─────────────────────────────────────────────────────────────────────────────

class SeedSource(TimeStampedModel):
    """
    A root source the crawler should monitor.

    Examples:
      - https://miningweekly.com/feed          (RSS)
      - https://gov.bw/sitemap.xml             (Sitemap)
      - https://reuters.com/business/energy/   (Seed URL)
      - <configured via meta JSON>             (Search API)
    """

    # ── Identity ──────────────────────────────────────────────────────────────
    name = models.CharField(
        max_length=255,
        help_text="Human-readable label, e.g. 'Mining Weekly RSS'.",
    )
    url = models.URLField(
        unique=True,
        help_text="The feed URL, sitemap URL, or page to crawl.",
    )
    source_type = models.CharField(
        max_length=20,
        choices=SourceType.choices,
        default=SourceType.RSS,
    )

    # ── Scheduling ────────────────────────────────────────────────────────────
    is_active = models.BooleanField(
        default=True,
        help_text="Inactive seeds are skipped by the scheduler.",
    )
    crawl_interval = models.PositiveIntegerField(
        default=60,
        help_text="How often to re-crawl this source, in minutes. 0 = manual only.",
    )
    last_crawled_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Set automatically each time discovery runs.",
    )

    # ── Filtering ─────────────────────────────────────────────────────────────
    keyword_filter = models.TextField(
        blank=True,
        help_text=(
            "Comma-separated keywords. When set, only URLs/articles whose "
            "title, snippet, or URL contain at least one keyword are stored. "
            "Leave empty to capture everything."
        ),
    )

    # ── Extra config ──────────────────────────────────────────────────────────
    meta = models.JSONField(
        default=dict,
        blank=True,
        help_text=(
            "Source-type-specific config as JSON. Examples: "
            '{"max_depth": 2} for sitemaps, '
            '{"provider": "bing", "query": "gold mining"} for search API.'
        ),
    )

    class Meta:
        ordering     = ["name"]
        verbose_name = "Seed Source"
        verbose_name_plural = "Seed Sources"

    def __str__(self):
        return f"{self.name} ({self.get_source_type_display()})"

    # ── Helpers ───────────────────────────────────────────────────────────────

    @property
    def keywords(self) -> list[str]:
        """Return keyword_filter as a cleaned lowercase list."""
        return [k.strip().lower() for k in self.keyword_filter.split(",") if k.strip()]

    @property
    def is_due(self) -> bool:
        """Return True if this source has never been crawled or its interval has elapsed."""
        if self.crawl_interval == 0:
            return False
        if self.last_crawled_at is None:
            return True
        from datetime import timedelta
        return timezone.now() >= self.last_crawled_at + timedelta(minutes=self.crawl_interval)

    def mark_crawled(self):
        """Stamp last_crawled_at to now. Call after a successful discovery run."""
        self.last_crawled_at = timezone.now()
        self.save(update_fields=["last_crawled_at"])


# ─────────────────────────────────────────────────────────────────────────────
# Model 2 — DiscoveredURL
# ─────────────────────────────────────────────────────────────────────────────

class DiscoveredURL(TimeStampedModel):
    """
    A single URL found during discovery — not yet fetched or parsed.

    The url_hash field is a SHA-256 of the URL and is used for fast,
    database-level deduplication. bulk_create(..., ignore_conflicts=True)
    relies on the unique constraint on url_hash to silently skip duplicates
    without needing a SELECT first.
    """

    seed = models.ForeignKey(
        SeedSource,
        on_delete=models.CASCADE,
        related_name="discovered_urls",
    )
    url = models.URLField(
        max_length=2048,
        help_text="The discovered article or page URL.",
    )
    url_hash = models.CharField(
        max_length=64,
        unique=True,
        db_index=True,
        editable=False,
        help_text="SHA-256 of the URL. Set automatically. Used for deduplication.",
    )

    # ── Metadata from discovery source ────────────────────────────────────────
    title = models.CharField(
        max_length=512,
        blank=True,
        help_text="Title from the RSS entry or link anchor text.",
    )
    snippet = models.TextField(
        blank=True,
        help_text="Short summary from the feed or search result.",
    )
    published_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Publication date if available from the discovery source.",
    )

    # ── Pipeline status ───────────────────────────────────────────────────────
    status = models.CharField(
        max_length=20,
        choices=URLStatusChoices.choices,
        default=URLStatusChoices.PENDING,
        db_index=True,
    )
    error_message = models.TextField(
        blank=True,
        help_text="Populated when status=failed.",
    )

    class Meta:
        ordering     = ["-created_at"]
        verbose_name = "Discovered URL"
        verbose_name_plural = "Discovered URLs"
        indexes = [
            models.Index(fields=["status"]),
            models.Index(fields=["seed", "status"]),
            models.Index(fields=["published_at"]),
        ]

    def __str__(self):
        return self.url

    def save(self, *args, **kwargs):
        # Always compute url_hash from the URL before saving
        if not self.url_hash:
            self.url_hash = hashlib.sha256(self.url.encode()).hexdigest()
        super().save(*args, **kwargs)

    # ── Helpers ───────────────────────────────────────────────────────────────

    @classmethod
    def hash_url(cls, url: str) -> str:
        """Return the SHA-256 hash for a given URL string."""
        return hashlib.sha256(url.encode()).hexdigest()


# ─────────────────────────────────────────────────────────────────────────────
# Model 3 — CrawlJob
# ─────────────────────────────────────────────────────────────────────────────

class CrawlJob(TimeStampedModel):
    """
    An audit record for a single attempt to fetch and process a DiscoveredURL.

    One URL may accumulate many CrawlJobs over time (retries, periodic re-crawls).
    This gives us a full history of every attempt and its outcome.
    """

    class TriggerType(models.TextChoices):
        SCHEDULED = "scheduled", "Scheduled"
        MANUAL    = "manual",    "Manual"
        RETRY     = "retry",     "Retry"

    discovered_url = models.ForeignKey(
        DiscoveredURL,
        on_delete=models.CASCADE,
        related_name="crawl_jobs",
    )
    trigger = models.CharField(
        max_length=20,
        choices=TriggerType.choices,
        default=TriggerType.SCHEDULED,
    )
    status = models.CharField(
        max_length=20,
        choices=URLStatusChoices.choices,
        default=URLStatusChoices.PENDING,
    )

    # ── Celery linkage ────────────────────────────────────────────────────────
    celery_task_id = models.CharField(
        max_length=255,
        blank=True,
        help_text="The Celery task ID, for tracking in Flower.",
    )

    # ── Timing ────────────────────────────────────────────────────────────────
    started_at  = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    # ── Outcome ───────────────────────────────────────────────────────────────
    http_status = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        help_text="HTTP response code from the fetch attempt.",
    )
    error_message = models.TextField(blank=True)

    class Meta:
        ordering     = ["-created_at"]
        verbose_name = "Crawl Job"
        verbose_name_plural = "Crawl Jobs"

    def __str__(self):
        return f"Job #{self.pk} [{self.status}] — {self.discovered_url.url[:60]}"

    @property
    def duration_seconds(self) -> float | None:
        """Return elapsed time in seconds, or None if job hasn't finished."""
        if self.started_at and self.finished_at:
            return (self.finished_at - self.started_at).total_seconds()
        return None
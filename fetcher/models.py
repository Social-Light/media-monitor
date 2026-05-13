"""
fetcher/models.py

Two models that represent the Fetcher layer:

  FetchedPage     — the raw HTTP response for a DiscoveredURL
      └─ ParsedArticle  — cleaned article content extracted from the page

Design decisions:
  - FetchedPage is OneToOne with DiscoveredURL. One URL, one raw page.
    If a URL needs re-fetching, the existing FetchedPage is replaced.

  - ParsedArticle is OneToOne with FetchedPage. Parsing always comes
    from a specific fetch — so results are traceable back to the exact
    raw HTML they came from.

  - raw_html is stored on FetchedPage so we can re-parse without
    re-fetching. Useful when we improve the parser or add new fields.
"""
from django.db import models

from core.models import TimeStampedModel
from discovery.models import DiscoveredURL, SeedSource


# ─────────────────────────────────────────────────────────────────────────────
# Model 1 — FetchedPage
# ─────────────────────────────────────────────────────────────────────────────

class FetchedPage(TimeStampedModel):
    """
    The raw HTTP response for a single DiscoveredURL.

    Stored immediately after a successful fetch, before any parsing.
    Keeping the raw HTML separate from the parsed content means we can
    re-parse at any time without making another network request.
    """

    discovered_url = models.OneToOneField(
        DiscoveredURL,
        on_delete=models.CASCADE,
        related_name="fetched_page",
        help_text="The URL this page was fetched from.",
    )

    # ── HTTP response metadata ─────────────────────────────────────────────
    status_code = models.PositiveSmallIntegerField(
        help_text="HTTP response status code, e.g. 200, 404, 500.",
    )
    content_type = models.CharField(
        max_length=255,
        blank=True,
        help_text="Value of the Content-Type response header.",
    )
    encoding = models.CharField(
        max_length=50,
        blank=True,
        help_text="Character encoding detected or declared by the server.",
    )

    # ── Raw content ────────────────────────────────────────────────────────
    raw_html = models.TextField(
        help_text="Full HTML body of the response.",
    )

    # ── Timing ────────────────────────────────────────────────────────────
    fetch_duration_ms = models.PositiveIntegerField(
        default=0,
        help_text="How long the HTTP request took, in milliseconds.",
    )
    fetched_at = models.DateTimeField(
        auto_now_add=True,
        help_text="Timestamp of when this page was fetched.",
    )

    class Meta:
        verbose_name = "Fetched Page"
        verbose_name_plural = "Fetched Pages"
        ordering = ["-fetched_at"]

    def __str__(self):
        return f"[{self.status_code}] {self.discovered_url.url[:80]}"

    @property
    def url(self) -> str:
        """Shortcut to the source URL."""
        return self.discovered_url.url

    @property
    def was_successful(self) -> bool:
        """True if the HTTP response was a 2xx status."""
        return 200 <= self.status_code < 300


# ─────────────────────────────────────────────────────────────────────────────
# Model 2 — ParsedArticle
# ─────────────────────────────────────────────────────────────────────────────

class ParsedArticle(TimeStampedModel):
    """
    Cleaned, structured article content extracted from a FetchedPage.

    This is the final output of the pipeline — the data that gets indexed,
    searched, and used to trigger alerts.
    """

    fetched_page = models.OneToOneField(
        FetchedPage,
        on_delete=models.CASCADE,
        related_name="parsed_article",
        help_text="The raw page this article was parsed from.",
    )

    # ── Core content ───────────────────────────────────────────────────────
    title = models.CharField(
        max_length=512,
        blank=True,
        help_text="Article headline.",
    )
    body_text = models.TextField(
        blank=True,
        help_text="Full plain-text body of the article, stripped of HTML.",
    )
    summary = models.TextField(
        blank=True,
        help_text="Auto-generated summary (from NLP or meta description).",
    )

    # ── Authorship & publication ───────────────────────────────────────────
    author = models.CharField(
        max_length=255,
        blank=True,
        help_text="Author name(s), comma-separated if multiple.",
    )
    published_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Article publication date extracted from the page.",
    )
    source_domain = models.CharField(
        max_length=255,
        blank=True,
        db_index=True,
        help_text="Hostname of the source URL, e.g. 'www.miningweekly.com'.",
    )

    # ── Language & tags ────────────────────────────────────────────────────
    language = models.CharField(
        max_length=10,
        blank=True,
        help_text="ISO 639-1 language code, e.g. 'en', 'fr'.",
    )
    tags = models.JSONField(
        default=list,
        blank=True,
        help_text="Keywords or tags extracted from the page metadata.",
    )

    # ── NLP signals ────────────────────────────────────────────────────────
    # Populated during parsing; used later for alerting and search ranking.
    # Structure is flexible — stored as JSON so we can extend it without
    # a new migration every time we add a signal type.
    #
    # Example:
    #   {
    #     "keywords": ["gold", "mining", "Botswana"],
    #     "entities": {"ORG": ["Anglo American"], "GPE": ["Botswana"]},
    #     "sentiment": "positive"
    #   }
    signals = models.JSONField(
        default=dict,
        blank=True,
        help_text="NLP-derived signals: keywords, named entities, sentiment, etc.",
    )

    # ── Deduplication ──────────────────────────────────────────────────────────
    content_hash = models.CharField(
        max_length=64,
        blank=True,
        db_index=True,
        help_text="SHA-256 of normalised body text.",
    )
    canonical_url = models.URLField(
        blank=True,
        help_text='<link rel="canonical"> href extracted from the page.',
    )
    duplicate_of = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="duplicates",
        help_text="Original article this is a duplicate of.",
    )
    is_duplicate = models.BooleanField(
        default=False,
        db_index=True,
        help_text="True if this article is a duplicate of another.",
    )

    class Meta:
        verbose_name = "Parsed Article"
        verbose_name_plural = "Parsed Articles"
        ordering = ["-published_at", "-created_at"]
        indexes = [
            models.Index(fields=["source_domain"]),
            models.Index(fields=["published_at"]),
            models.Index(fields=["language"]),
        ]

    def __str__(self):
        return self.title[:80] or self.fetched_page.url[:80]

    @property
    def url(self) -> str:
        """Shortcut to the original source URL."""
        return self.fetched_page.discovered_url.url

    @property
    def word_count(self) -> int:
        """Approximate word count of the article body."""
        return len(self.body_text.split()) if self.body_text else 0

    @property
    def has_body(self) -> bool:
        """True if meaningful body text was extracted."""
        return self.word_count > 50


# ─────────────────────────────────────────────────────────────────────────────
# Model 3 — ExtractionRule
# ─────────────────────────────────────────────────────────────────────────────

class ExtractionRule(TimeStampedModel):
    """
    Per-domain CSS selector overrides for article field extraction.

    When a FetchedPage's URL matches the rule's domain, the selectors here
    are applied after newspaper3k and their results take priority.  Any
    selector left blank means "fall back to newspaper3k for that field."
    """

    seed = models.ForeignKey(
        SeedSource,
        on_delete=models.CASCADE,
        related_name="rules",
        help_text="Seed source that owns this rule.",
    )
    domain = models.CharField(
        max_length=255,
        db_index=True,
        help_text="Exact hostname to match, e.g. 'www.miningweekly.com'.",
    )

    # ── CSS selectors ──────────────────────────────────────────────────────
    title_selector = models.CharField(
        max_length=500,
        blank=True,
        help_text="CSS selector for the article title element.",
    )
    body_selector = models.CharField(
        max_length=500,
        blank=True,
        help_text="CSS selector for the article body element.",
    )
    author_selector = models.CharField(
        max_length=500,
        blank=True,
        help_text="CSS selector for the author element.",
    )
    date_selector = models.CharField(
        max_length=500,
        blank=True,
        help_text="CSS selector for the publication date element.",
    )
    date_format = models.CharField(
        max_length=100,
        blank=True,
        help_text=(
            "strptime format string for date_selector text, "
            "e.g. '%%d %%B %%Y'. Leave blank to auto-parse ISO 8601."
        ),
    )

    is_active = models.BooleanField(
        default=True,
        help_text="Inactive rules are ignored during extraction.",
    )

    class Meta:
        verbose_name = "Extraction Rule"
        verbose_name_plural = "Extraction Rules"
        ordering = ["domain"]

    def __str__(self):
        return f"ExtractionRule({self.domain})"
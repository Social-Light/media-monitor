"""
matching/models.py
───────────────────
The organisation matching layer.

Organisations are the entities a client wants to monitor (companies,
agencies, brands). Each organisation owns a set of keywords and, optionally,
a set of source domains. After an article is parsed, match_article() checks
its title and body against every active organisation keyword and records an
ArticleMatch for each hit.
"""
from django.db import models
from django.utils.text import slugify

from core.models import TimeStampedModel


class Organisation(TimeStampedModel):
    """An entity being monitored — e.g. a company, agency, or brand."""

    name = models.CharField(
        max_length=255,
        help_text="Display name, e.g. 'Debswana Diamond Company'.",
    )
    slug = models.SlugField(
        max_length=255,
        unique=True,
        help_text="URL-safe identifier. Auto-generated from the name if left blank.",
    )
    is_active = models.BooleanField(default=True, db_index=True)

    class Meta:
        ordering = ["name"]
        verbose_name = "Organisation"
        verbose_name_plural = "Organisations"

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)
        super().save(*args, **kwargs)


class OrganisationKeyword(TimeStampedModel):
    """A single keyword that, when found in an article, links it to the organisation."""

    organisation = models.ForeignKey(
        Organisation,
        on_delete=models.CASCADE,
        related_name="keywords",
    )
    keyword = models.CharField(
        max_length=255,
        help_text="Matched case-insensitively against the article title and body.",
    )
    is_active = models.BooleanField(default=True, db_index=True)

    class Meta:
        ordering = ["keyword"]
        unique_together = [["organisation", "keyword"]]
        verbose_name = "Organisation Keyword"
        verbose_name_plural = "Organisation Keywords"

    def __str__(self):
        return f"{self.organisation.name}: {self.keyword}"


class OrganisationSource(TimeStampedModel):
    """A source domain associated with an organisation (e.g. its own newsroom)."""

    organisation = models.ForeignKey(
        Organisation,
        on_delete=models.CASCADE,
        related_name="sources",
    )
    domain = models.CharField(
        max_length=255,
        db_index=True,
        help_text="Bare host, e.g. 'www.debswana.com'.",
    )

    class Meta:
        ordering = ["domain"]
        unique_together = [["organisation", "domain"]]
        verbose_name = "Organisation Source"
        verbose_name_plural = "Organisation Sources"

    def __str__(self):
        return f"{self.organisation.name}: {self.domain}"


class ArticleMatch(TimeStampedModel):
    """Records that a parsed article matched one of an organisation's keywords."""

    class MatchedIn(models.TextChoices):
        TITLE = "title", "Title"
        BODY  = "body",  "Body"

    parsed_article = models.ForeignKey(
        "fetcher.ParsedArticle",
        on_delete=models.CASCADE,
        related_name="organisation_matches",
    )
    organisation = models.ForeignKey(
        Organisation,
        on_delete=models.CASCADE,
        related_name="article_matches",
    )
    matched_keyword = models.CharField(max_length=255)
    matched_in = models.CharField(
        max_length=10,
        choices=MatchedIn.choices,
        help_text="Where the keyword was found — a title hit ranks above a body hit.",
    )
    confidence = models.FloatField(
        default=1.0,
        help_text="Match strength in [0, 1]; title hits score higher than body hits.",
    )

    class Meta:
        ordering = ["-created_at"]
        unique_together = [["parsed_article", "organisation", "matched_keyword"]]
        verbose_name = "Article Match"
        verbose_name_plural = "Article Matches"

    def __str__(self):
        return f"{self.organisation.name} ← {self.matched_keyword} ({self.matched_in})"

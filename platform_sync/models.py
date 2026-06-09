"""
platform_sync/models.py
─────────────────────────
Mirror of the five media-monitoring **platform** models the crawler writes into
(the platform itself lives in the `socialmonitor` project / `monitor` app).

These are an exact copy of the relevant fields from monitor/models.py so that,
in production, `managed = False` models line up byte-for-byte with the real
`monitor_*` tables in the shared PostgreSQL database. `db_table` is pinned to the
`monitor_*` names regardless of this app's label.

Managed flag:
  - PLATFORM_INTEGRATED = False (dev/test) → managed = True
        migrations create local `monitor_*` tables in the crawler's own DB so
        tests and local runs work normally.
  - PLATFORM_INTEGRATED = True (production) → managed = False
        the platform owns its tables; Django never migrates them. Routing to the
        separate `platform` database is handled by platform_sync.routers.

Only Organization, Keyword, Competitor, OnlineArticle and CompetitorArticle are
mirrored — the crawler only ever reads orgs/keywords/competitors and writes the
two article tables.
"""
import re
import uuid

from django.conf import settings
from django.db import models

# True in dev/test (build + migrate locally), False in production (platform-owned).
PLATFORM_MANAGED = not getattr(settings, "PLATFORM_INTEGRATED", False)


SENTIMENT_CHOICES = [
    ("positive", "Positive"),
    ("neutral", "Neutral"),
    ("negative", "Negative"),
    ("mixed", "Mixed"),
]

COVERAGE_CHOICES = [
    ("Earned", "Earned"),
    ("Incidental", "Incidental"),
    ("Advocated", "Advocated"),
    ("Not Set", "Not Set"),
]

INDUSTRY_CHOICES = [
    ("Banking & Financial Services", "Banking & Financial Services"),
    ("Mining & Metals", "Mining & Metals"),
    ("Telecommunications", "Telecommunications"),
    ("Retail", "Retail"),
    ("Education & Research", "Education & Research"),
    ("Healthcare", "Healthcare"),
    ("Government", "Government"),
    ("Energy", "Energy"),
    ("Technology", "Technology"),
    ("Other", "Other"),
]

KEYWORD_CATEGORY_CHOICES = [
    ("brand", "Brand Keywords"),
    ("personnel", "Personnel"),
    ("campaign", "Campaigns"),
]


class Organization(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=200)
    email = models.EmailField(blank=True)
    industry = models.CharField(max_length=100, choices=INDUSTRY_CHOICES, blank=True)
    country = models.CharField(max_length=100, default="Botswana")
    status = models.CharField(
        max_length=20,
        choices=[("active", "Active"), ("inactive", "Inactive")],
        default="active",
    )
    address = models.CharField(max_length=300, blank=True)
    phone = models.CharField(max_length=50, blank=True)
    website = models.URLField(blank=True, max_length=500)
    facebook_url = models.URLField(blank=True, max_length=500)
    linkedin_url = models.URLField(blank=True, max_length=500)
    x_handle = models.CharField(max_length=100, blank=True)
    logo = models.FileField(upload_to="logos/", blank=True, null=True)
    gradient_color1 = models.CharField(max_length=7, default="#1d4ed8")
    gradient_color2 = models.CharField(max_length=7, default="#0f172a")
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name

    class Meta:
        managed = PLATFORM_MANAGED
        db_table = "monitor_organization"
        ordering = ["name"]


class Keyword(models.Model):
    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="keywords"
    )
    keyword = models.CharField(max_length=200)
    category = models.CharField(max_length=100, default="brand")
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.keyword

    class Meta:
        managed = PLATFORM_MANAGED
        db_table = "monitor_keyword"
        ordering = ["category", "keyword"]
        unique_together = ["organization", "keyword", "category"]


class Competitor(models.Model):
    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="competitors"
    )
    name = models.CharField(max_length=200)
    aliases = models.TextField(
        blank=True,
        default="",
        help_text="Comma-separated alternative names/keywords used to match coverage",
    )
    website = models.URLField(blank=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name

    def match_terms(self):
        """All terms that should match this competitor in coverage: the name plus
        any comma/newline-separated aliases. De-duplicated (case-insensitive),
        empties dropped, original casing preserved."""
        terms = [self.name] + re.split(r"[,\n]", self.aliases or "")
        seen, out = set(), []
        for t in terms:
            t = t.strip()
            if t and t.lower() not in seen:
                seen.add(t.lower())
                out.append(t)
        return out

    class Meta:
        managed = PLATFORM_MANAGED
        db_table = "monitor_competitor"
        ordering = ["name"]


class CompetitorArticle(models.Model):
    """Online coverage *about* a competitor. Distinct from OnlineArticle, which is
    the organisation's own coverage."""

    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="competitor_articles"
    )
    competitor = models.ForeignKey(
        Competitor,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="articles",
    )
    company_name = models.CharField(max_length=200)
    headline = models.TextField()
    url = models.URLField(blank=True, max_length=2000)
    summary = models.TextField(blank=True)
    source = models.CharField(max_length=200, blank=True)
    date_published = models.DateField(null=True, blank=True)
    country = models.CharField(max_length=100, blank=True)
    matched_keywords = models.CharField(max_length=300, blank=True)
    sentiment_score = models.FloatField(default=0)
    sentiment = models.CharField(max_length=20, choices=SENTIMENT_CHOICES, default="neutral")
    reach = models.IntegerField(default=0)
    cpm = models.FloatField(default=0)
    ave = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    rank = models.FloatField(default=0)
    coverage_type = models.CharField(max_length=50, blank=True, default="Not Set")
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.company_name}: {self.headline[:50]}"

    class Meta:
        managed = PLATFORM_MANAGED
        db_table = "monitor_competitorarticle"
        ordering = ["-date_published", "-created_at"]


class OnlineArticle(models.Model):
    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="online_articles"
    )
    source = models.CharField(max_length=200)
    source_logo = models.URLField(blank=True)
    headline = models.TextField()
    summary = models.TextField(blank=True)
    url = models.URLField(blank=True, max_length=2000)
    date_published = models.DateField()
    country = models.CharField(max_length=100, blank=True)
    sentiment = models.CharField(max_length=20, choices=SENTIMENT_CHOICES, default="neutral")
    ave = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    coverage = models.CharField(
        max_length=50, choices=COVERAGE_CHOICES, blank=True, default="Not Set"
    )
    reach = models.IntegerField(default=0)
    relevancy = models.FloatField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.headline[:60]

    class Meta:
        managed = PLATFORM_MANAGED
        db_table = "monitor_onlinearticle"
        ordering = ["-date_published", "-created_at"]

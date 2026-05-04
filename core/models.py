from django.db import models


class TimeStampedModel(models.Model):
    """
    Abstract base class that adds created_at and updated_at
    timestamp fields to every model that inherits from it.

    Usage:
        class MyModel(TimeStampedModel):
            name = models.CharField(max_length=100)
    """
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class URLStatusChoices(models.TextChoices):
    """
    Shared status lifecycle for a discovered URL as it moves
    through the crawler pipeline.

    PENDING  → ready to be fetched
    FETCHING → a worker has claimed it and is in progress
    FETCHED  → raw HTML stored, waiting to be parsed
    PARSED   → article text extracted and stored
    FAILED   → an error occurred at some stage
    SKIPPED  → intentionally excluded (duplicate, out of scope, etc.)
    """
    PENDING  = "pending",  "Pending"
    FETCHING = "fetching", "Fetching"
    FETCHED  = "fetched",  "Fetched"
    PARSED   = "parsed",   "Parsed"
    FAILED   = "failed",   "Failed"
    SKIPPED  = "skipped",  "Skipped"
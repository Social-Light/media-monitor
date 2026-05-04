"""
Tests for discovery models.
Run with: python manage.py test discovery
"""
import hashlib
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from core.models import URLStatusChoices
from discovery.models import SeedSource, DiscoveredURL, CrawlJob, SourceType


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def make_seed(**kwargs) -> SeedSource:
    defaults = dict(name="Test Seed", url="https://example.com/rss", source_type=SourceType.RSS)
    defaults.update(kwargs)
    return SeedSource.objects.create(**defaults)


def make_discovered(seed, url="https://example.com/article/1", **kwargs) -> DiscoveredURL:
    return DiscoveredURL.objects.create(seed=seed, url=url, **kwargs)


# ─────────────────────────────────────────────────────────────────────────────
# SeedSource tests
# ─────────────────────────────────────────────────────────────────────────────

class SeedSourceTests(TestCase):

    def test_str_includes_name_and_type(self):
        seed = make_seed()
        self.assertIn("Test Seed", str(seed))
        self.assertIn("RSS", str(seed))

    def test_keywords_parses_comma_list(self):
        seed = make_seed(keyword_filter="Gold, Mining , diamonds")
        self.assertEqual(seed.keywords, ["gold", "mining", "diamonds"])

    def test_keywords_empty_returns_empty_list(self):
        seed = make_seed(keyword_filter="")
        self.assertEqual(seed.keywords, [])

    def test_is_due_true_when_never_crawled(self):
        seed = make_seed(crawl_interval=60, last_crawled_at=None)
        self.assertTrue(seed.is_due)

    def test_is_due_true_when_interval_elapsed(self):
        seed = make_seed(
            crawl_interval=60,
            last_crawled_at=timezone.now() - timedelta(minutes=61),
        )
        self.assertTrue(seed.is_due)

    def test_is_due_false_when_recently_crawled(self):
        seed = make_seed(
            crawl_interval=60,
            last_crawled_at=timezone.now() - timedelta(minutes=10),
        )
        self.assertFalse(seed.is_due)

    def test_is_due_false_when_interval_zero(self):
        seed = make_seed(crawl_interval=0)
        self.assertFalse(seed.is_due)

    def test_mark_crawled_sets_timestamp(self):
        seed = make_seed()
        self.assertIsNone(seed.last_crawled_at)
        seed.mark_crawled()
        seed.refresh_from_db()
        self.assertIsNotNone(seed.last_crawled_at)


# ─────────────────────────────────────────────────────────────────────────────
# DiscoveredURL tests
# ─────────────────────────────────────────────────────────────────────────────

class DiscoveredURLTests(TestCase):

    def setUp(self):
        self.seed = make_seed()

    def test_url_hash_auto_populated(self):
        du = make_discovered(self.seed)
        expected = hashlib.sha256("https://example.com/article/1".encode()).hexdigest()
        self.assertEqual(du.url_hash, expected)

    def test_default_status_is_pending(self):
        du = make_discovered(self.seed)
        self.assertEqual(du.status, URLStatusChoices.PENDING)

    def test_str_returns_url(self):
        du = make_discovered(self.seed)
        self.assertEqual(str(du), "https://example.com/article/1")

    def test_hash_url_classmethod(self):
        url = "https://example.com/article/99"
        expected = hashlib.sha256(url.encode()).hexdigest()
        self.assertEqual(DiscoveredURL.hash_url(url), expected)

    def test_duplicate_url_raises_on_create(self):
        """
        The unique constraint on url_hash means creating the same URL twice
        raises an IntegrityError.
        """
        from django.db import IntegrityError
        make_discovered(self.seed, url="https://example.com/dupe")
        with self.assertRaises(IntegrityError):
            make_discovered(self.seed, url="https://example.com/dupe")

    def test_bulk_create_ignores_duplicate(self):
        """
        bulk_create with ignore_conflicts=True silently skips duplicates —
        this is the dedup strategy used by the discovery tasks.
        """
        url = "https://example.com/bulk-dupe"
        url_hash = DiscoveredURL.hash_url(url)

        items = [
            DiscoveredURL(seed=self.seed, url=url, url_hash=url_hash, title="First"),
            DiscoveredURL(seed=self.seed, url=url, url_hash=url_hash, title="Second"),
        ]
        created = DiscoveredURL.objects.bulk_create(items, ignore_conflicts=True)

        # Only one record should exist in the database
        self.assertEqual(DiscoveredURL.objects.filter(url=url).count(), 1)


# ─────────────────────────────────────────────────────────────────────────────
# CrawlJob tests
# ─────────────────────────────────────────────────────────────────────────────

class CrawlJobTests(TestCase):

    def setUp(self):
        self.seed = make_seed()
        self.du   = make_discovered(self.seed)

    def test_str_includes_status_and_url(self):
        job = CrawlJob.objects.create(discovered_url=self.du)
        self.assertIn("pending", str(job).lower())

    def test_duration_seconds_none_when_not_finished(self):
        job = CrawlJob.objects.create(discovered_url=self.du, started_at=timezone.now())
        self.assertIsNone(job.duration_seconds)

    def test_duration_seconds_calculated_correctly(self):
        start = timezone.now()
        end   = start + timedelta(seconds=4)
        job = CrawlJob.objects.create(
            discovered_url=self.du,
            started_at=start,
            finished_at=end,
        )
        self.assertAlmostEqual(job.duration_seconds, 4.0, places=1)

    def test_default_trigger_is_scheduled(self):
        job = CrawlJob.objects.create(discovered_url=self.du)
        self.assertEqual(job.trigger, CrawlJob.TriggerType.SCHEDULED)
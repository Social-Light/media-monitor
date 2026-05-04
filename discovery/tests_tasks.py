"""
Tests for discovery/tasks.py

Tasks are run synchronously with .apply() so no broker is needed.
All service calls are patched so no network access is required.
"""
from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from core.models import URLStatusChoices
from discovery.models import SeedSource, DiscoveredURL, SourceType
from discovery.tasks import run_seed_discovery, run_all_seeds, _matches_keywords, _store_discovered_urls


# ── Fixtures ──────────────────────────────────────────────────────────────────

def make_seed(**kwargs) -> SeedSource:
    defaults = dict(
        name="Test Feed",
        url="https://example.com/feed",
        source_type=SourceType.RSS,
        is_active=True,
        crawl_interval=60,
    )
    defaults.update(kwargs)
    return SeedSource.objects.create(**defaults)


def fake_articles(n=3, base_url="https://example.com/article/") -> list[dict]:
    return [
        {
            "url":          f"{base_url}{i}",
            "title":        f"Article {i}",
            "snippet":      f"Snippet for article {i}",
            "published_at": None,
            "source_type":  "rss",
        }
        for i in range(1, n + 1)
    ]


# ── _matches_keywords ─────────────────────────────────────────────────────────

class MatchesKeywordsTests(TestCase):

    def test_no_keywords_always_matches(self):
        item = {"url": "https://example.com/article", "title": "Anything", "snippet": ""}
        self.assertTrue(_matches_keywords(item, []))

    def test_keyword_in_title_matches(self):
        item = {"url": "https://example.com/a", "title": "Gold prices surge", "snippet": ""}
        self.assertTrue(_matches_keywords(item, ["gold"]))

    def test_keyword_in_url_matches(self):
        item = {"url": "https://example.com/gold-strike", "title": "Story", "snippet": ""}
        self.assertTrue(_matches_keywords(item, ["gold"]))

    def test_keyword_in_snippet_matches(self):
        item = {"url": "https://example.com/a", "title": "Story", "snippet": "Mining output rises"}
        self.assertTrue(_matches_keywords(item, ["mining"]))

    def test_no_keyword_match_returns_false(self):
        item = {"url": "https://example.com/politics", "title": "Election results", "snippet": ""}
        self.assertFalse(_matches_keywords(item, ["gold", "mining"]))

    def test_case_insensitive(self):
        item = {"url": "https://example.com/a", "title": "GOLD MINING", "snippet": ""}
        self.assertTrue(_matches_keywords(item, ["gold"]))


# ── _store_discovered_urls ────────────────────────────────────────────────────

class StoreDiscoveredURLsTests(TestCase):

    def setUp(self):
        self.seed = make_seed()

    def test_stores_new_articles(self):
        articles = fake_articles(3)
        result = _store_discovered_urls(self.seed, articles)
        self.assertEqual(result["new"], 3)
        self.assertEqual(DiscoveredURL.objects.count(), 3)

    def test_stored_urls_have_pending_status(self):
        _store_discovered_urls(self.seed, fake_articles(1))
        du = DiscoveredURL.objects.first()
        self.assertEqual(du.status, URLStatusChoices.PENDING)

    def test_stored_urls_linked_to_seed(self):
        _store_discovered_urls(self.seed, fake_articles(1))
        du = DiscoveredURL.objects.first()
        self.assertEqual(du.seed, self.seed)

    def test_deduplicates_within_batch(self):
        # Same URL twice in one batch — only one should be stored
        articles = [
            {"url": "https://example.com/dupe", "title": "A", "snippet": "", "published_at": None, "source_type": "rss"},
            {"url": "https://example.com/dupe", "title": "B", "snippet": "", "published_at": None, "source_type": "rss"},
        ]
        result = _store_discovered_urls(self.seed, articles)
        self.assertEqual(DiscoveredURL.objects.count(), 1)
        self.assertEqual(result["skipped_duplicate"], 1)

    def test_ignores_already_stored_urls(self):
        # Store once, then try to store the same URLs again
        articles = fake_articles(2)
        _store_discovered_urls(self.seed, articles)
        result = _store_discovered_urls(self.seed, articles)
        # Second call should add 0 new records
        self.assertEqual(result["new"], 0)
        self.assertEqual(DiscoveredURL.objects.count(), 2)

    def test_keyword_filter_skips_non_matching(self):
        self.seed.keyword_filter = "gold"
        self.seed.save()
        articles = [
            {"url": "https://example.com/gold-strike",  "title": "Gold strike!", "snippet": "", "published_at": None, "source_type": "rss"},
            {"url": "https://example.com/politics-news","title": "Politics",     "snippet": "", "published_at": None, "source_type": "rss"},
        ]
        result = _store_discovered_urls(self.seed, articles)
        self.assertEqual(result["new"], 1)
        self.assertEqual(result["skipped_keyword"], 1)
        self.assertEqual(DiscoveredURL.objects.first().url, "https://example.com/gold-strike")

    def test_skips_items_with_no_url(self):
        articles = [{"url": "", "title": "No URL", "snippet": "", "published_at": None, "source_type": "rss"}]
        result = _store_discovered_urls(self.seed, articles)
        self.assertEqual(result["new"], 0)
        self.assertEqual(DiscoveredURL.objects.count(), 0)


# ── run_seed_discovery task ───────────────────────────────────────────────────

class RunSeedDiscoveryTaskTests(TestCase):

    def setUp(self):
        self.seed = make_seed()

    def _run(self, articles=None):
        """Run the task synchronously, patching run_discovery."""
        if articles is None:
            articles = fake_articles(3)
        with patch("discovery.tasks.run_discovery", return_value=articles):
            return run_seed_discovery.apply(args=[self.seed.pk])

    def test_task_succeeds(self):
        result = self._run()
        self.assertEqual(result.status, "SUCCESS")

    def test_returns_summary_dict(self):
        result = self._run(fake_articles(3))
        self.assertEqual(result.result["status"],    "ok")
        self.assertEqual(result.result["seed_id"],   self.seed.pk)
        self.assertEqual(result.result["seed_name"], self.seed.name)
        self.assertEqual(result.result["new"],       3)

    def test_creates_discovered_url_records(self):
        self._run(fake_articles(4))
        self.assertEqual(DiscoveredURL.objects.count(), 4)

    def test_stamps_last_crawled_at(self):
        self.assertIsNone(self.seed.last_crawled_at)
        self._run()
        self.seed.refresh_from_db()
        self.assertIsNotNone(self.seed.last_crawled_at)

    def test_skips_inactive_seed(self):
        self.seed.is_active = False
        self.seed.save()
        result = run_seed_discovery.apply(args=[self.seed.pk])
        self.assertEqual(result.result["status"], "skipped_inactive")
        self.assertEqual(DiscoveredURL.objects.count(), 0)

    def test_skips_missing_seed(self):
        result = run_seed_discovery.apply(args=[99999])
        self.assertEqual(result.result["status"], "skipped_inactive")

    def test_deduplicates_across_runs(self):
        articles = fake_articles(3)
        with patch("discovery.tasks.run_discovery", return_value=articles):
            run_seed_discovery.apply(args=[self.seed.pk])
            result2 = run_seed_discovery.apply(args=[self.seed.pk])
        # Second run should find 0 new URLs
        self.assertEqual(result2.result["new"], 0)
        self.assertEqual(DiscoveredURL.objects.count(), 3)


# ── run_all_seeds task ────────────────────────────────────────────────────────

class RunAllSeedsTaskTests(TestCase):

    def test_dispatches_due_seeds(self):
        # Two seeds: both never crawled (is_due=True)
        seed1 = make_seed(name="Feed A", url="https://a.com/feed")
        seed2 = make_seed(name="Feed B", url="https://b.com/feed")

        with patch("discovery.tasks.run_seed_discovery.delay") as mock_delay:
            result = run_all_seeds.apply()

        self.assertEqual(result.result["total"], 2)
        dispatched_ids = result.result["dispatched"]
        self.assertIn(seed1.pk, dispatched_ids)
        self.assertIn(seed2.pk, dispatched_ids)
        self.assertEqual(mock_delay.call_count, 2)

    def test_skips_recently_crawled_seed(self):
        make_seed(
            name="Fresh Feed",
            url="https://fresh.com/feed",
            last_crawled_at=timezone.now() - timedelta(minutes=5),
            crawl_interval=60,   # not due for another 55 minutes
        )
        with patch("discovery.tasks.run_seed_discovery.delay") as mock_delay:
            result = run_all_seeds.apply()

        self.assertEqual(result.result["total"], 0)
        mock_delay.assert_not_called()

    def test_skips_inactive_seeds(self):
        make_seed(name="Inactive", url="https://off.com/feed", is_active=False)
        with patch("discovery.tasks.run_seed_discovery.delay") as mock_delay:
            result = run_all_seeds.apply()

        self.assertEqual(result.result["total"], 0)
        mock_delay.assert_not_called()

    def test_skips_zero_interval_seeds(self):
        make_seed(name="Manual", url="https://manual.com/feed", crawl_interval=0)
        with patch("discovery.tasks.run_seed_discovery.delay") as mock_delay:
            result = run_all_seeds.apply()

        self.assertEqual(result.result["total"], 0)
        mock_delay.assert_not_called()

    def test_empty_db_returns_zero(self):
        result = run_all_seeds.apply()
        self.assertEqual(result.result["total"], 0)

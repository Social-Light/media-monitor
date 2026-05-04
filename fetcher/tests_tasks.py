"""
fetcher/tests_tasks.py

Tests for fetcher/tasks.py

Tasks run synchronously with .apply() — no broker needed.
All service calls are patched — no network access needed.
"""
from unittest.mock import patch, call

from django.test import TestCase

from core.models import URLStatusChoices
from discovery.models import DiscoveredURL, SeedSource, SourceType
from fetcher.models import FetchedPage, ParsedArticle
from fetcher.tasks import fetch_single_url, fetch_pending_urls

# ── Fixtures ──────────────────────────────────────────────────────────────────

_counter = 0

def _url():
    global _counter
    _counter += 1
    return f"https://example.com/article/{_counter}"


def make_seed() -> SeedSource:
    seed, _ = SeedSource.objects.get_or_create(
        url="https://example.com/feed",
        defaults={"name": "Test Seed", "source_type": SourceType.RSS},
    )
    return seed


def make_discovered_url(status=URLStatusChoices.PENDING) -> DiscoveredURL:
    return DiscoveredURL.objects.create(
        seed=make_seed(),
        url=_url(),
        status=status,
    )


# ── fetch_single_url ──────────────────────────────────────────────────────────

class FetchSingleURLTaskTests(TestCase):

    def _run(self, du, result=None):
        """Run fetch_single_url synchronously with a patched service call."""
        if result is None:
            result = {"url": du.url, "status": "ok", "title": "Test", "words": 120}
        with patch("fetcher.tasks.fetch_and_parse", return_value=result):
            return fetch_single_url.apply(args=[du.pk])

    def test_task_succeeds(self):
        du     = make_discovered_url()
        result = self._run(du)
        self.assertEqual(result.status, "SUCCESS")

    def test_returns_result_dict(self):
        du     = make_discovered_url()
        result = self._run(du)
        self.assertIn("status", result.result)
        self.assertIn("id",     result.result)
        self.assertIn("url",    result.result)

    def test_result_id_matches_discovered_url(self):
        du     = make_discovered_url()
        result = self._run(du)
        self.assertEqual(result.result["id"], du.pk)

    def test_result_status_ok_on_success(self):
        du     = make_discovered_url()
        result = self._run(du)
        self.assertEqual(result.result["status"], "ok")

    def test_calls_fetch_and_parse_with_correct_object(self):
        du = make_discovered_url()
        with patch("fetcher.tasks.fetch_and_parse") as mock_fap:
            mock_fap.return_value = {"url": du.url, "status": "ok", "title": "X", "words": 5}
            fetch_single_url.apply(args=[du.pk])
        # fetch_and_parse should be called once with the DiscoveredURL instance
        mock_fap.assert_called_once()
        called_du = mock_fap.call_args[0][0]
        self.assertEqual(called_du.pk, du.pk)

    def test_returns_not_found_for_missing_id(self):
        result = fetch_single_url.apply(args=[99999])
        self.assertEqual(result.result["status"], "not_found")

    def test_propagates_fetch_failed_status(self):
        du = make_discovered_url()
        result = self._run(du, result={"url": du.url, "status": "fetch_failed"})
        self.assertEqual(result.result["status"], "fetch_failed")

    def test_propagates_parse_failed_status(self):
        du = make_discovered_url()
        result = self._run(du, result={"url": du.url, "status": "parse_failed"})
        self.assertEqual(result.result["status"], "parse_failed")


# ── fetch_pending_urls ────────────────────────────────────────────────────────

class FetchPendingURLsTaskTests(TestCase):

    def test_dispatches_pending_urls(self):
        du1 = make_discovered_url(status=URLStatusChoices.PENDING)
        du2 = make_discovered_url(status=URLStatusChoices.PENDING)

        with patch("fetcher.tasks.fetch_single_url.delay") as mock_delay:
            result = fetch_pending_urls.apply()

        self.assertEqual(result.result["total"], 2)
        dispatched = result.result["dispatched"]
        self.assertIn(du1.pk, dispatched)
        self.assertIn(du2.pk, dispatched)
        self.assertEqual(mock_delay.call_count, 2)

    def test_ignores_non_pending_statuses(self):
        make_discovered_url(status=URLStatusChoices.FETCHED)
        make_discovered_url(status=URLStatusChoices.PARSED)
        make_discovered_url(status=URLStatusChoices.FAILED)
        make_discovered_url(status=URLStatusChoices.SKIPPED)
        make_discovered_url(status=URLStatusChoices.FETCHING)

        with patch("fetcher.tasks.fetch_single_url.delay") as mock_delay:
            result = fetch_pending_urls.apply()

        self.assertEqual(result.result["total"], 0)
        mock_delay.assert_not_called()

    def test_only_dispatches_pending(self):
        pending = make_discovered_url(status=URLStatusChoices.PENDING)
        make_discovered_url(status=URLStatusChoices.PARSED)

        with patch("fetcher.tasks.fetch_single_url.delay") as mock_delay:
            result = fetch_pending_urls.apply()

        self.assertEqual(result.result["total"], 1)
        self.assertIn(pending.pk, result.result["dispatched"])

    def test_respects_batch_size(self):
        for _ in range(10):
            make_discovered_url(status=URLStatusChoices.PENDING)

        with patch("fetcher.tasks.fetch_single_url.delay") as mock_delay:
            result = fetch_pending_urls.apply(kwargs={"batch_size": 3})

        self.assertEqual(result.result["total"], 3)
        self.assertEqual(mock_delay.call_count, 3)

    def test_returns_zero_when_nothing_pending(self):
        result = fetch_pending_urls.apply()
        self.assertEqual(result.result["total"], 0)
        self.assertEqual(result.result["dispatched"], [])

    def test_task_succeeds(self):
        result = fetch_pending_urls.apply()
        self.assertEqual(result.status, "SUCCESS")

    def test_dispatches_correct_ids(self):
        du = make_discovered_url(status=URLStatusChoices.PENDING)

        with patch("fetcher.tasks.fetch_single_url.delay") as mock_delay:
            fetch_pending_urls.apply()

        mock_delay.assert_called_once_with(du.pk)
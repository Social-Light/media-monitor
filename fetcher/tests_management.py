"""
fetcher/tests_management.py
 
Tests for:
  - fetcher/management/commands/setup_beat.py
  - fetcher/management/commands/fetch_pending.py
"""
from io import StringIO
from unittest.mock import patch
 
from django.core.management import call_command
from django.test import TestCase
 
from core.models import URLStatusChoices
from discovery.models import DiscoveredURL, SeedSource, SourceType
from fetcher.models import FetchedPage, ParsedArticle
 
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
 
 
def make_du(status=URLStatusChoices.PENDING) -> DiscoveredURL:
    return DiscoveredURL.objects.create(
        seed=make_seed(),
        url=_url(),
        title="Test Article",
        status=status,
    )
 
 
def ok_result(du):
    return {"url": du.url, "status": "ok", "title": "Gold strike", "words": 120}
 
def fetch_failed_result(du):
    return {"url": du.url, "status": "fetch_failed"}
 
def parse_failed_result(du):
    return {"url": du.url, "status": "parse_failed"}
 
 
# ── setup_beat ────────────────────────────────────────────────────────────────
 
class SetupBeatCommandTests(TestCase):
 
    def _call(self, *args, **kwargs):
        out = StringIO()
        call_command("setup_beat", *args, stdout=out, **kwargs)
        return out.getvalue()
 
    def test_creates_both_tasks(self):
        from django_celery_beat.models import PeriodicTask
        self._call()
        self.assertEqual(
            PeriodicTask.objects.exclude(name="celery.backend_cleanup").count(), 2
        )
 
    def test_creates_discovery_task(self):
        from django_celery_beat.models import PeriodicTask
        self._call()
        self.assertTrue(
            PeriodicTask.objects.filter(task="discovery.run_all_seeds").exists()
        )
 
    def test_creates_fetcher_task(self):
        from django_celery_beat.models import PeriodicTask
        self._call()
        self.assertTrue(
            PeriodicTask.objects.filter(task="fetcher.fetch_pending_urls").exists()
        )
 
    def test_tasks_enabled_by_default(self):
        from django_celery_beat.models import PeriodicTask
        self._call()
        for task in PeriodicTask.objects.exclude(name="celery.backend_cleanup"):
            self.assertTrue(task.enabled, f"{task.name} should be enabled")
 
    def test_disable_flag_creates_disabled_tasks(self):
        from django_celery_beat.models import PeriodicTask
        self._call(disable=True)
        for task in PeriodicTask.objects.exclude(name="celery.backend_cleanup"):
            self.assertFalse(task.enabled, f"{task.name} should be disabled")
 
    def test_idempotent_no_duplicates_on_rerun(self):
        from django_celery_beat.models import PeriodicTask
        self._call()
        self._call()
        self.assertEqual(
            PeriodicTask.objects.exclude(name="celery.backend_cleanup").count(), 2
        )
 
    def test_output_shows_created_on_first_run(self):
        output = self._call()
        self.assertIn("Created", output)
 
    def test_output_shows_updated_on_second_run(self):
        self._call()
        output = self._call()
        self.assertIn("Updated", output)
 
    def test_list_flag_shows_tasks(self):
        self._call()
        output = self._call(list_only=True)
        self.assertIn("discovery.run_all_seeds",    output)
        self.assertIn("fetcher.fetch_pending_urls", output)
 
    def test_discovery_task_interval_is_5_minutes(self):
        from django_celery_beat.models import PeriodicTask
        self._call()
        task = PeriodicTask.objects.get(task="discovery.run_all_seeds")
        self.assertEqual(task.interval.every, 5)
 
    def test_fetcher_task_interval_is_2_minutes(self):
        from django_celery_beat.models import PeriodicTask
        self._call()
        task = PeriodicTask.objects.get(task="fetcher.fetch_pending_urls")
        self.assertEqual(task.interval.every, 2)
 
 
# ── fetch_pending ─────────────────────────────────────────────────────────────
 
class FetchPendingCommandTests(TestCase):
 
    def _call(self, *args, **kwargs):
        out = StringIO()
        call_command("fetch_pending", *args, stdout=out, stderr=StringIO(), **kwargs)
        return out.getvalue()
 
    def _patch(self, result_fn):
        """Return a context manager that patches fetch_and_parse."""
        def side_effect(du):
            return result_fn(du)
        return patch("fetcher.management.commands.fetch_pending.fetch_and_parse",
                     side_effect=side_effect)
 
    # ── No pending URLs ────────────────────────────────────────────────────
 
    def test_no_urls_prints_warning(self):
        output = self._call()
        self.assertIn("No URLs", output)
 
    # ── Default behaviour: fetch PENDING ──────────────────────────────────
 
    def test_fetches_pending_urls(self):
        du = make_du(URLStatusChoices.PENDING)
        with self._patch(ok_result):
            output = self._call()
        self.assertIn("ok=1", output)
 
    def test_skips_non_pending_by_default(self):
        make_du(URLStatusChoices.PARSED)
        make_du(URLStatusChoices.FAILED)
        output = self._call()
        self.assertIn("No URLs", output)
 
    def test_shows_title_on_success(self):
        make_du()
        with self._patch(ok_result):
            output = self._call()
        self.assertIn("Gold strike", output)
 
    def test_shows_word_count_on_success(self):
        make_du()
        with self._patch(ok_result):
            output = self._call()
        self.assertIn("120 words", output)
 
    def test_counts_failures(self):
        make_du()
        with self._patch(fetch_failed_result):
            output = self._call()
        self.assertIn("failed=1", output)
 
    def test_counts_parse_failures(self):
        make_du()
        with self._patch(parse_failed_result):
            output = self._call()
        self.assertIn("failed=1", output)
 
    # ── --limit ────────────────────────────────────────────────────────────
 
    def test_limit_caps_results(self):
        for _ in range(5):
            make_du()
        fetched = []
        def capture(du):
            fetched.append(du.pk)
            return ok_result(du)
        with patch("fetcher.management.commands.fetch_pending.fetch_and_parse",
                   side_effect=capture):
            self._call(limit=2)
        self.assertEqual(len(fetched), 2)
 
    # ── --ids ──────────────────────────────────────────────────────────────
 
    def test_ids_fetches_specific_urls(self):
        du1 = make_du()
        du2 = make_du()
        fetched_ids = []
        def capture(du):
            fetched_ids.append(du.pk)
            return ok_result(du)
        with patch("fetcher.management.commands.fetch_pending.fetch_and_parse",
                   side_effect=capture):
            self._call(ids=[du1.pk])
        self.assertIn(du1.pk, fetched_ids)
        self.assertNotIn(du2.pk, fetched_ids)
 
    def test_ids_with_no_match_prints_warning(self):
        output = self._call(ids=[99999])
        self.assertIn("No URLs", output)
 
    # ── --force ────────────────────────────────────────────────────────────
 
    def test_force_refetches_parsed_url(self):
        du = make_du(URLStatusChoices.PARSED)
        fetched = []
        def capture(du):
            fetched.append(du.pk)
            return ok_result(du)
        with patch("fetcher.management.commands.fetch_pending.fetch_and_parse",
                   side_effect=capture):
            self._call(ids=[du.pk], force=True)
        self.assertIn(du.pk, fetched)
 
    # ── --async ────────────────────────────────────────────────────────────
 
    def test_async_dispatches_celery_task(self):
        make_du()
        with patch("fetcher.management.commands.fetch_pending.fetch_single_url") as mock_task:
            mock_task.delay = patch("fetcher.management.commands.fetch_pending.fetch_single_url.delay").start()
            with patch("fetcher.management.commands.fetch_pending.fetch_single_url.delay") as mock_delay:
                self._call(use_async=True)
            mock_delay.assert_called_once()
 
    def test_async_does_not_call_fetch_and_parse_directly(self):
        make_du()
        with patch("fetcher.management.commands.fetch_pending.fetch_and_parse") as mock_fap, \
             patch("fetcher.management.commands.fetch_pending.fetch_single_url.delay"):
            self._call(use_async=True)
        mock_fap.assert_not_called()
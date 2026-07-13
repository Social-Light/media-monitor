"""
discovery/tests_backfill.py

Tests for the backfill_org management command (historical org back-search).

External I/O is mocked at the command module level:
  - search()          — news provider adapter
  - fetch_mentions()  — Apify social adapter
  - fetch_and_parse() — news fetch/parse pipeline
  - ingest_social_post() — social ingest pipeline
so nothing hits the network and no real fetch runs. Real Organization / Keyword
/ SeedSource / DiscoveredURL rows are created against the local test DB.
"""
from datetime import date, datetime, timezone as dt_timezone
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from discovery.models import DiscoveredURL, SeedSource, SourceType
from discovery.management.commands.backfill_org import (
    parse_date_arg,
    social_date_input,
    within_range,
)
from platform_sync.models import Organization, Keyword

CMD = "discovery.management.commands.backfill_org"


def make_org(name="Debswana", keywords=(("Debswana", "brand"),), status="active"):
    org = Organization.objects.create(name=name, status=status)
    for kw, cat in keywords:
        Keyword.objects.create(organization=org, keyword=kw, category=cat)
    return org


def news_item(url, published_at=None, title="A story"):
    return {"url": url, "title": title, "snippet": "snippet", "published_at": published_at}


# ── Pure helpers ──────────────────────────────────────────────────────────────

class HelperTests(TestCase):
    def test_parse_date_arg_ok(self):
        self.assertEqual(parse_date_arg("2026-01-15"), date(2026, 1, 15))

    def test_parse_date_arg_bad(self):
        with self.assertRaises(CommandError):
            parse_date_arg("15/01/2026")

    def test_within_range_inside(self):
        d = datetime(2026, 2, 1, tzinfo=dt_timezone.utc)
        self.assertTrue(within_range(d, date(2026, 1, 1), date(2026, 3, 1)))

    def test_within_range_outside(self):
        d = datetime(2025, 12, 1, tzinfo=dt_timezone.utc)
        self.assertFalse(within_range(d, date(2026, 1, 1), date(2026, 3, 1)))

    def test_within_range_none_is_kept(self):
        self.assertTrue(within_range(None, date(2026, 1, 1), date(2026, 3, 1)))

    def test_social_date_input_x_absolute(self):
        self.assertEqual(
            social_date_input("x", date(2026, 1, 1), date(2026, 3, 31)),
            {"start": "2026-01-01", "end": "2026-03-31"},
        )

    def test_social_date_input_other_empty(self):
        self.assertEqual(social_date_input("linkedin", date(2026, 1, 1), date(2026, 3, 31)), {})


# ── Validation / resolution ────────────────────────────────────────────────────

class ValidationTests(TestCase):
    def test_unknown_org_errors(self):
        with self.assertRaises(CommandError):
            call_command("backfill_org", "--org", "Nope", "--start", "2026-01-01")

    def test_end_before_start_errors(self):
        make_org()
        with self.assertRaises(CommandError):
            call_command("backfill_org", "--org", "Debswana",
                         "--start", "2026-03-01", "--end", "2026-01-01")

    def test_org_with_no_matching_keywords_errors(self):
        make_org(keywords=(("Debswana", "brand"),))
        with self.assertRaises(CommandError):
            call_command("backfill_org", "--org", "Debswana", "--start", "2026-01-01",
                         "--source", "news", "--category", "personnel")


# ── News back-search ────────────────────────────────────────────────────────────

class NewsBackfillTests(TestCase):
    def setUp(self):
        make_org(keywords=(("Debswana", "brand"),))

    @patch(f"{CMD}.fetch_and_parse", return_value={"status": "ok", "title": "T", "words": 100})
    @patch(f"{CMD}.search")
    def test_news_ingests_and_passes_date_range(self, mock_search, mock_fp):
        mock_search.return_value = [news_item("https://ex.com/a",
                                              datetime(2026, 2, 1, tzinfo=dt_timezone.utc))]
        call_command("backfill_org", "--org", "Debswana",
                     "--start", "2026-01-01", "--end", "2026-03-31",
                     "--source", "news", stdout=StringIO())

        # Google provider must receive an absolute date_range.
        _, kwargs = mock_search.call_args
        self.assertEqual(kwargs["provider"], "google")
        self.assertEqual(kwargs["date_range"], (date(2026, 1, 1), date(2026, 3, 31)))
        # A DiscoveredURL was created and fetch_and_parse ran.
        self.assertTrue(DiscoveredURL.objects.filter(url="https://ex.com/a").exists())
        mock_fp.assert_called_once()

    @patch(f"{CMD}.fetch_and_parse")
    @patch(f"{CMD}.search")
    def test_out_of_range_item_skipped(self, mock_search, mock_fp):
        mock_search.return_value = [news_item("https://ex.com/old",
                                              datetime(2025, 1, 1, tzinfo=dt_timezone.utc))]
        call_command("backfill_org", "--org", "Debswana",
                     "--start", "2026-01-01", "--end", "2026-03-31",
                     "--source", "news", stdout=StringIO())
        self.assertFalse(DiscoveredURL.objects.filter(url="https://ex.com/old").exists())
        mock_fp.assert_not_called()

    @patch(f"{CMD}.fetch_and_parse")
    @patch(f"{CMD}.search")
    def test_dry_run_creates_nothing(self, mock_search, mock_fp):
        mock_search.return_value = [news_item("https://ex.com/a")]
        call_command("backfill_org", "--org", "Debswana", "--start", "2026-01-01",
                     "--source", "news", "--dry-run", stdout=StringIO())
        self.assertFalse(DiscoveredURL.objects.exists())
        mock_fp.assert_not_called()

    @patch(f"{CMD}.fetch_and_parse", return_value={"status": "ok", "title": "T"})
    @patch(f"{CMD}.search")
    def test_existing_url_not_refetched(self, mock_search, mock_fp):
        seed = SeedSource.objects.create(
            name="pre", url="https://pre.example/seed", source_type=SourceType.RSS,
        )
        DiscoveredURL.objects.create(seed=seed, url="https://ex.com/a", status="parsed")
        mock_search.return_value = [news_item("https://ex.com/a")]
        call_command("backfill_org", "--org", "Debswana", "--start", "2026-01-01",
                     "--source", "news", stdout=StringIO())
        mock_fp.assert_not_called()

    @patch(f"{CMD}.fetch_and_parse", return_value={"status": "ok", "title": "T"})
    @patch(f"{CMD}.search")
    def test_bing_provider_used_without_date_range(self, mock_search, mock_fp):
        mock_search.return_value = []
        call_command("backfill_org", "--org", "Debswana", "--start", "2026-01-01",
                     "--source", "news", "--provider", "bing", stdout=StringIO())
        _, kwargs = mock_search.call_args
        self.assertEqual(kwargs["provider"], "bing")
        self.assertNotIn("date_range", kwargs)


# ── Social back-search ──────────────────────────────────────────────────────────

class SocialBackfillTests(TestCase):
    def setUp(self):
        make_org(keywords=(("Debswana", "brand"),))

    @patch(f"{CMD}.ingest_social_post")
    @patch(f"{CMD}.fetch_mentions")
    def test_x_gets_absolute_date_actor_input(self, mock_fetch, mock_ingest):
        mock_fetch.return_value = [{"url": "https://x.com/1", "title": "post",
                                    "published_at": datetime(2026, 2, 1, tzinfo=dt_timezone.utc)}]
        mock_ingest.return_value = object()
        call_command("backfill_org", "--org", "Debswana",
                     "--start", "2026-01-01", "--end", "2026-03-31",
                     "--source", "social", "--platforms", "x", stdout=StringIO())
        _, kwargs = mock_fetch.call_args
        self.assertEqual(kwargs["actor_input"], {"start": "2026-01-01", "end": "2026-03-31"})
        mock_ingest.assert_called_once()

    @patch(f"{CMD}.ingest_social_post")
    @patch(f"{CMD}.fetch_mentions")
    def test_social_out_of_range_skipped(self, mock_fetch, mock_ingest):
        mock_fetch.return_value = [{"url": "https://x.com/old", "title": "post",
                                    "published_at": datetime(2020, 1, 1, tzinfo=dt_timezone.utc)}]
        call_command("backfill_org", "--org", "Debswana",
                     "--start", "2026-01-01", "--end", "2026-03-31",
                     "--source", "social", "--platforms", "x", stdout=StringIO())
        mock_ingest.assert_not_called()

    @patch(f"{CMD}.ingest_social_post")
    @patch(f"{CMD}.fetch_mentions")
    def test_dry_run_no_ingest(self, mock_fetch, mock_ingest):
        mock_fetch.return_value = [{"url": "https://x.com/1", "title": "post", "published_at": None}]
        call_command("backfill_org", "--org", "Debswana", "--start", "2026-01-01",
                     "--source", "social", "--platforms", "x", "--dry-run", stdout=StringIO())
        mock_ingest.assert_not_called()

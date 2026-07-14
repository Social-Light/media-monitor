"""
fetcher/tests_ingest_urls.py

Tests for the curated-URL back-fill path:
  - fetcher.bridge.push_article_for_org (force-write for one org, no keyword gate)
  - the ingest_urls management command

fetch_and_parse is mocked so nothing hits the network; the platform_sync models
run managed locally (PLATFORM_INTEGRATED=False), so OnlineArticle rows are real.
"""
import tempfile
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from discovery.models import DiscoveredURL, SeedSource, SourceType
from fetcher.bridge import push_article_for_org
from fetcher.models import FetchedPage, ParsedArticle
from platform_sync.models import Keyword, OnlineArticle, Organization

CMD = "fetcher.management.commands.ingest_urls"

_n = 0


def _uid():
    global _n
    _n += 1
    return _n


def make_org(name="Decode Afrika", keywords=()):
    org = Organization.objects.create(name=name, status="active")
    for kw in keywords:
        Keyword.objects.create(organization=org, keyword=kw)
    return org


def make_parsed(url, title="Decode Afrika hosts summit"):
    uid = _uid()
    seed = SeedSource.objects.create(
        name=f"s{uid}", url=f"https://seed.example/{uid}", source_type=SourceType.RSS,
    )
    du = DiscoveredURL.objects.create(seed=seed, url=url, status="parsed")
    fp = FetchedPage.objects.create(discovered_url=du, status_code=200, raw_html="<html></html>")
    return ParsedArticle.objects.create(
        fetched_page=fp, title=title, body_text="body text", summary="summary",
        source_domain="example.com",
    )


# ── Bridge force-write ──────────────────────────────────────────────────────────

class PushArticleForOrgTests(TestCase):
    def test_forces_capture_without_keyword_match(self):
        org = make_org(keywords=())  # no keywords at all
        art = make_parsed("https://ex.com/a", title="Totally unrelated headline")
        created = push_article_for_org(art, org)
        self.assertIsNotNone(created)
        self.assertTrue(OnlineArticle.objects.filter(organization=org, url="https://ex.com/a").exists())

    def test_dedup_returns_none_second_time(self):
        org = make_org()
        art = make_parsed("https://ex.com/b")
        self.assertIsNotNone(push_article_for_org(art, org))
        self.assertIsNone(push_article_for_org(art, org))
        self.assertEqual(OnlineArticle.objects.filter(organization=org, url="https://ex.com/b").count(), 1)

    def test_coverage_passed_through(self):
        org = make_org()
        art = make_parsed("https://ex.com/c")
        created = push_article_for_org(art, org, coverage="Advocated")
        self.assertEqual(created.coverage, "Advocated")


# ── ingest_urls command ─────────────────────────────────────────────────────────

class IngestUrlsCommandTests(TestCase):
    def setUp(self):
        self.org = make_org()

    def test_unknown_org_errors(self):
        with self.assertRaises(CommandError):
            call_command("ingest_urls", "--org", "Nope", "--url", "https://ex.com/x")

    def test_no_urls_errors(self):
        with self.assertRaises(CommandError):
            call_command("ingest_urls", "--org", "Decode Afrika")

    @patch(f"{CMD}.fetch_and_parse")
    def test_reuses_existing_parse_and_captures(self, mock_fp):
        make_parsed("https://ex.com/known")
        call_command("ingest_urls", "--org", "Decode Afrika", "--url", "https://ex.com/known")
        mock_fp.assert_not_called()  # already parsed → no fetch
        self.assertTrue(OnlineArticle.objects.filter(organization=self.org, url="https://ex.com/known").exists())

    @patch(f"{CMD}.fetch_and_parse")
    def test_fetch_path_creates_and_captures(self, mock_fp):
        def fake(discovered_url):
            page = FetchedPage.objects.create(
                discovered_url=discovered_url, status_code=200, raw_html="x",
            )
            ParsedArticle.objects.create(
                fetched_page=page, title="Decode Afrika summit", body_text="b",
                summary="s", source_domain="ex.com",
            )
            return {"status": "ok", "title": "Decode Afrika summit", "words": 2}

        mock_fp.side_effect = fake
        call_command("ingest_urls", "--org", "Decode Afrika", "--url", "https://ex.com/new")
        mock_fp.assert_called_once()
        self.assertTrue(OnlineArticle.objects.filter(organization=self.org, url="https://ex.com/new").exists())

    @patch(f"{CMD}.fetch_and_parse", return_value={"status": "fetch_failed"})
    def test_fetch_failure_reported_not_fatal(self, mock_fp):
        call_command("ingest_urls", "--org", "Decode Afrika",
                     "--url", "https://ex.com/bad", "--url", "https://ex.com/also-bad")
        self.assertEqual(mock_fp.call_count, 2)  # kept going after the first failure
        self.assertFalse(OnlineArticle.objects.filter(organization=self.org).exists())

    @patch(f"{CMD}.fetch_and_parse")
    def test_dry_run_writes_nothing(self, mock_fp):
        call_command("ingest_urls", "--org", "Decode Afrika",
                     "--url", "https://ex.com/a", "--dry-run")
        mock_fp.assert_not_called()
        self.assertFalse(OnlineArticle.objects.exists())
        self.assertFalse(DiscoveredURL.objects.exists())

    @patch(f"{CMD}.fetch_and_parse")
    def test_urls_file_parsed_with_comments_and_dedup(self, mock_fp):
        make_parsed("https://ex.com/1")
        make_parsed("https://ex.com/2")
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as fh:
            fh.write("# a comment\n\nhttps://ex.com/1\nhttps://ex.com/2\nhttps://ex.com/1\n")
            path = fh.name
        call_command("ingest_urls", "--org", "Decode Afrika", "--urls-file", path)
        # Two distinct URLs captured; the duplicate line ignored.
        self.assertEqual(OnlineArticle.objects.filter(organization=self.org).count(), 2)
        mock_fp.assert_not_called()

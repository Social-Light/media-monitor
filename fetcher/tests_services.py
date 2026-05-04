"""
fetcher/tests_services.py

Tests for fetcher/services.py

All HTTP requests and newspaper3k parsing are mocked so these tests
run fully offline and deterministically.
"""
from unittest.mock import MagicMock, patch

from django.test import TestCase

from core.models import URLStatusChoices
from discovery.models import DiscoveredURL, SeedSource, SourceType
from fetcher.models import FetchedPage, ParsedArticle
from fetcher.services import fetch_page, parse_page, fetch_and_parse, _collect_tags, _collect_signals


# ── Shared fixtures ───────────────────────────────────────────────────────────

# Counter so every test gets a unique URL — avoids url_hash conflicts
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


def make_discovered_url() -> DiscoveredURL:
    return DiscoveredURL.objects.create(
        seed=make_seed(),
        url=_url(),
        title="Test Article Title",
    )


def make_fetched_page(du=None, status_code=200, raw_html=None) -> FetchedPage:
    du = du or make_discovered_url()
    return FetchedPage.objects.create(
        discovered_url    = du,
        status_code       = status_code,
        content_type      = "text/html; charset=utf-8",
        encoding          = "utf-8",
        raw_html          = raw_html or SAMPLE_HTML,
        fetch_duration_ms = 250,
    )


# Realistic sample HTML with enough metadata for newspaper3k to work with
SAMPLE_HTML = """<!DOCTYPE html>
<html>
<head>
    <title>Gold prices hit record high</title>
    <meta name="author" content="Jane Smith">
    <meta property="article:published_time" content="2025-04-22T08:00:00Z">
    <meta name="description" content="Gold reached an all-time high today.">
    <meta name="keywords" content="gold, mining, commodities">
</head>
<body>
    <article>
        <h1>Gold prices hit record high</h1>
        <p>Gold prices surged to a record high of 3500 dollars per ounce today,
        driven by global uncertainty and strong demand from central banks.
        Analysts say the rally could continue into next quarter.
        Mining companies in southern Africa are reporting strong earnings.
        The Botswana government has announced increased royalty collections.
        Investors are watching the market closely for signs of correction.</p>
    </article>
</body>
</html>"""


# ── _collect_tags ─────────────────────────────────────────────────────────────

class CollectTagsTests(TestCase):

    def _make_article(self, meta_keywords="", tags=None):
        mock = MagicMock()
        mock.meta_keywords = meta_keywords
        mock.tags          = tags or set()
        return mock

    def test_parses_comma_separated_keywords(self):
        article = self._make_article(meta_keywords="Gold, Mining, Africa")
        result  = _collect_tags(article)
        self.assertIn("gold",   result)
        self.assertIn("mining", result)
        self.assertIn("africa", result)

    def test_merges_newspaper_tags(self):
        article = self._make_article(meta_keywords="gold", tags={"diamonds"})
        result  = _collect_tags(article)
        self.assertIn("gold",     result)
        self.assertIn("diamonds", result)

    def test_deduplicates(self):
        article = self._make_article(meta_keywords="gold", tags={"gold"})
        result  = _collect_tags(article)
        self.assertEqual(result.count("gold"), 1)

    def test_lowercases_all_tags(self):
        article = self._make_article(meta_keywords="GOLD, MINING")
        result  = _collect_tags(article)
        self.assertIn("gold",   result)
        self.assertIn("mining", result)
        self.assertNotIn("GOLD", result)

    def test_empty_returns_empty_list(self):
        article = self._make_article()
        self.assertEqual(_collect_tags(article), [])

    def test_accepts_list_meta_keywords(self):
        article = self._make_article(meta_keywords=["Gold", "Mining"])
        result  = _collect_tags(article)
        self.assertIn("gold",   result)
        self.assertIn("mining", result)


# ── _collect_signals ──────────────────────────────────────────────────────────

class CollectSignalsTests(TestCase):

    def _make_article(self, meta_keywords=""):
        mock = MagicMock()
        mock.meta_keywords = meta_keywords
        return mock

    def test_keywords_extracted(self):
        article = self._make_article(meta_keywords="gold, mining")
        signals = _collect_signals(article)
        self.assertIn("keywords", signals)
        self.assertIn("gold",   signals["keywords"])
        self.assertIn("mining", signals["keywords"])

    def test_empty_keywords_not_included(self):
        article = self._make_article(meta_keywords="")
        signals = _collect_signals(article)
        self.assertNotIn("keywords", signals)


# ── fetch_page ────────────────────────────────────────────────────────────────

class FetchPageTests(TestCase):

    def _mock_response(self, status_code=200, text=SAMPLE_HTML, headers=None):
        mock = MagicMock()
        mock.status_code = status_code
        mock.text        = text
        mock.encoding    = "utf-8"
        mock.headers     = headers or {"Content-Type": "text/html; charset=utf-8"}
        mock.ok          = (200 <= status_code < 300)
        return mock

    def test_creates_fetched_page_on_success(self):
        du = make_discovered_url()
        with patch("fetcher.services.get_session") as mock_session:
            mock_session.return_value.get.return_value = self._mock_response()
            result = fetch_page(du)

        self.assertIsInstance(result, FetchedPage)
        self.assertEqual(result.status_code, 200)

    def test_stores_raw_html(self):
        du = make_discovered_url()
        with patch("fetcher.services.get_session") as mock_session:
            mock_session.return_value.get.return_value = self._mock_response()
            page = fetch_page(du)

        self.assertEqual(page.raw_html, SAMPLE_HTML)

    def test_marks_discovered_url_as_fetched(self):
        du = make_discovered_url()
        with patch("fetcher.services.get_session") as mock_session:
            mock_session.return_value.get.return_value = self._mock_response()
            fetch_page(du)

        du.refresh_from_db()
        self.assertEqual(du.status, URLStatusChoices.FETCHED)

    def test_marks_failed_on_http_error(self):
        du = make_discovered_url()
        with patch("fetcher.services.get_session") as mock_session:
            mock_session.return_value.get.return_value = self._mock_response(status_code=404)
            result = fetch_page(du)

        du.refresh_from_db()
        self.assertEqual(du.status, URLStatusChoices.FAILED)
        self.assertIn("404", du.error_message)
        # Still returns a FetchedPage so we have the response recorded
        self.assertIsInstance(result, FetchedPage)
        self.assertEqual(result.status_code, 404)

    def test_returns_none_on_network_exception(self):
        du = make_discovered_url()
        with patch("fetcher.services.get_session") as mock_session:
            mock_session.return_value.get.side_effect = Exception("Connection refused")
            result = fetch_page(du)

        self.assertIsNone(result)
        du.refresh_from_db()
        self.assertEqual(du.status, URLStatusChoices.FAILED)
        self.assertIn("Connection refused", du.error_message)

    def test_records_fetch_duration(self):
        du = make_discovered_url()
        with patch("fetcher.services.get_session") as mock_session:
            mock_session.return_value.get.return_value = self._mock_response()
            page = fetch_page(du)

        self.assertGreaterEqual(page.fetch_duration_ms, 0)

    def test_stores_content_type(self):
        du = make_discovered_url()
        with patch("fetcher.services.get_session") as mock_session:
            mock_session.return_value.get.return_value = self._mock_response(
                headers={"Content-Type": "text/html; charset=utf-8"}
            )
            page = fetch_page(du)

        self.assertIn("text/html", page.content_type)

    def test_marks_fetching_status_during_request(self):
        """Status must be FETCHING while the request is in-flight."""
        du = make_discovered_url()
        observed = []

        def fake_get(url, timeout):
            du.refresh_from_db()
            observed.append(du.status)
            mock = MagicMock()
            mock.status_code = 200
            mock.text        = SAMPLE_HTML
            mock.encoding    = "utf-8"
            mock.headers     = {}
            mock.ok          = True
            return mock

        with patch("fetcher.services.get_session") as mock_session:
            mock_session.return_value.get.side_effect = fake_get
            fetch_page(du)

        self.assertIn(URLStatusChoices.FETCHING, observed)


# ── parse_page ────────────────────────────────────────────────────────────────

class ParsePageTests(TestCase):

    def _mock_article(self, **kwargs):
        """Build a mock newspaper.Article with sensible defaults."""
        mock = MagicMock()
        mock.title           = kwargs.get("title",        "Gold prices hit record high")
        mock.text            = kwargs.get("text",         "Gold surged to a record high. " * 20)
        mock.authors         = kwargs.get("authors",      ["Jane Smith"])
        mock.publish_date    = kwargs.get("publish_date", None)
        mock.meta_lang       = kwargs.get("meta_lang",    "en")
        mock.tags            = kwargs.get("tags",         set())
        mock.meta_keywords   = kwargs.get("meta_keywords","gold, mining")
        mock.meta_description = kwargs.get("meta_description", "Gold surged today.")
        return mock

    def _call(self, fp=None, article_mock=None):
        fp   = fp or make_fetched_page()
        mock = article_mock or self._mock_article()
        with patch("fetcher.services.newspaper.Article", return_value=mock):
            return parse_page(fp)

    def test_creates_parsed_article(self):
        result = self._call()
        self.assertIsInstance(result, ParsedArticle)

    def test_title_extracted(self):
        result = self._call()
        self.assertEqual(result.title, "Gold prices hit record high")

    def test_author_extracted(self):
        result = self._call()
        self.assertEqual(result.author, "Jane Smith")

    def test_multiple_authors_joined(self):
        mock   = self._mock_article(authors=["Alice", "Bob"])
        result = self._call(article_mock=mock)
        self.assertEqual(result.author, "Alice, Bob")

    def test_source_domain_extracted_from_url(self):
        result = self._call()
        self.assertEqual(result.source_domain, "example.com")

    def test_language_extracted(self):
        result = self._call()
        self.assertEqual(result.language, "en")

    def test_tags_collected(self):
        mock   = self._mock_article(meta_keywords="gold, mining")
        result = self._call(article_mock=mock)
        self.assertIn("gold",   result.tags)
        self.assertIn("mining", result.tags)

    def test_signals_has_keywords(self):
        mock   = self._mock_article(meta_keywords="gold, mining")
        result = self._call(article_mock=mock)
        self.assertIn("keywords", result.signals)

    def test_marks_discovered_url_as_parsed(self):
        fp = make_fetched_page()
        self._call(fp=fp)
        fp.discovered_url.refresh_from_db()
        self.assertEqual(fp.discovered_url.status, URLStatusChoices.PARSED)

    def test_falls_back_to_discovery_title_when_no_title(self):
        fp   = make_fetched_page()
        mock = self._mock_article(title="")
        result = self._call(fp=fp, article_mock=mock)
        # Should use the DiscoveredURL's title set by make_discovered_url()
        self.assertEqual(result.title, "Test Article Title")

    def test_summary_from_meta_description(self):
        mock   = self._mock_article(meta_description="A great summary.")
        result = self._call(article_mock=mock)
        self.assertEqual(result.summary, "A great summary.")

    def test_returns_none_on_parse_exception(self):
        fp   = make_fetched_page()
        mock = MagicMock()
        mock.set_html.side_effect = Exception("Parse exploded")
        with patch("fetcher.services.newspaper.Article", return_value=mock):
            result = parse_page(fp)
        self.assertIsNone(result)

    def test_marks_failed_on_parse_exception(self):
        fp   = make_fetched_page()
        mock = MagicMock()
        mock.set_html.side_effect = Exception("Parse exploded")
        with patch("fetcher.services.newspaper.Article", return_value=mock):
            parse_page(fp)
        fp.discovered_url.refresh_from_db()
        self.assertEqual(fp.discovered_url.status, URLStatusChoices.FAILED)


# ── fetch_and_parse ───────────────────────────────────────────────────────────

class FetchAndParseTests(TestCase):

    def _mock_response(self, status_code=200):
        mock = MagicMock()
        mock.status_code = status_code
        mock.text        = SAMPLE_HTML
        mock.encoding    = "utf-8"
        mock.headers     = {"Content-Type": "text/html"}
        mock.ok          = (200 <= status_code < 300)
        return mock

    def _mock_article(self):
        mock = MagicMock()
        mock.title            = "Gold prices hit record high"
        mock.text             = "Gold surged. " * 30
        mock.authors          = ["Jane Smith"]
        mock.publish_date     = None
        mock.meta_lang        = "en"
        mock.tags             = set()
        mock.meta_keywords    = "gold"
        mock.meta_description = ""
        return mock

    def test_returns_ok_on_success(self):
        du = make_discovered_url()
        with patch("fetcher.services.get_session") as ms, \
             patch("fetcher.services.newspaper.Article", return_value=self._mock_article()):
            ms.return_value.get.return_value = self._mock_response()
            result = fetch_and_parse(du)

        self.assertEqual(result["status"], "ok")
        self.assertIn("title", result)
        self.assertIn("words", result)

    def test_returns_fetch_failed_on_network_error(self):
        du = make_discovered_url()
        with patch("fetcher.services.get_session") as ms:
            ms.return_value.get.side_effect = Exception("Timeout")
            result = fetch_and_parse(du)

        self.assertEqual(result["status"], "fetch_failed")

    def test_returns_parse_failed_when_parse_raises(self):
        du = make_discovered_url()
        broken_article = MagicMock()
        broken_article.set_html.side_effect = Exception("Bad HTML")
        with patch("fetcher.services.get_session") as ms, \
             patch("fetcher.services.newspaper.Article", return_value=broken_article):
            ms.return_value.get.return_value = self._mock_response()
            result = fetch_and_parse(du)

        self.assertEqual(result["status"], "parse_failed")

    def test_result_contains_url(self):
        du = make_discovered_url()
        with patch("fetcher.services.get_session") as ms, \
             patch("fetcher.services.newspaper.Article", return_value=self._mock_article()):
            ms.return_value.get.return_value = self._mock_response()
            result = fetch_and_parse(du)

        self.assertEqual(result["url"], du.url)

    def test_end_to_end_creates_both_models(self):
        """Both FetchedPage and ParsedArticle should exist after a successful run."""
        du = make_discovered_url()
        with patch("fetcher.services.get_session") as ms, \
             patch("fetcher.services.newspaper.Article", return_value=self._mock_article()):
            ms.return_value.get.return_value = self._mock_response()
            fetch_and_parse(du)

        self.assertTrue(FetchedPage.objects.filter(discovered_url=du).exists())
        self.assertTrue(
            ParsedArticle.objects.filter(fetched_page__discovered_url=du).exists()
        )

    def test_discovered_url_status_is_parsed_on_success(self):
        du = make_discovered_url()
        with patch("fetcher.services.get_session") as ms, \
             patch("fetcher.services.newspaper.Article", return_value=self._mock_article()):
            ms.return_value.get.return_value = self._mock_response()
            fetch_and_parse(du)

        du.refresh_from_db()
        self.assertEqual(du.status, URLStatusChoices.PARSED)
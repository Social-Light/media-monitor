"""
fetcher/tests_search.py

Tests for:
  fetcher/search.py        — article_to_doc, index_article, delete_article,
                             ensure_index, drop_index, bulk_index_articles,
                             search_articles
  management/commands/index_articles.py

All Elasticsearch calls are mocked; no live ES required.
"""
from io import StringIO
from unittest.mock import MagicMock, call, patch

from django.core.management import call_command
from django.test import TestCase

from core.models import URLStatusChoices
from discovery.models import DiscoveredURL, SeedSource, SourceType
from fetcher.models import FetchedPage, ParsedArticle
from fetcher.search import (
    INDEX_MAPPING,
    article_to_doc,
    bulk_index_articles,
    delete_article,
    drop_index,
    ensure_index,
    index_article,
    search_articles,
)


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


def make_article(**kwargs) -> ParsedArticle:
    du = DiscoveredURL.objects.create(seed=make_seed(), url=_url(), title="Test")
    fp = FetchedPage.objects.create(
        discovered_url=du,
        status_code=200,
        content_type="text/html",
        encoding="utf-8",
        raw_html="<html><body><p>body</p></body></html>",
        fetch_duration_ms=100,
    )
    defaults = dict(
        title="Gold miners report record profits",
        body_text="Gold surged today. " * 20,
        summary="Gold surged.",
        author="Jane Smith",
        source_domain="example.com",
        language="en",
        tags=["gold", "mining"],
        signals={"sentiment": "positive", "keywords": ["gold"]},
    )
    defaults.update(kwargs)
    return ParsedArticle.objects.create(fetched_page=fp, **defaults)


def _mock_client():
    client = MagicMock()
    client.indices.exists.return_value = False
    client.search.return_value = {"hits": {"hits": [], "total": {"value": 0}}}
    return client


# ── article_to_doc ────────────────────────────────────────────────────────────

class ArticleToDocTests(TestCase):

    def setUp(self):
        self.article = make_article()

    def test_contains_title(self):
        doc = article_to_doc(self.article)
        self.assertEqual(doc["title"], self.article.title)

    def test_contains_body_text(self):
        doc = article_to_doc(self.article)
        self.assertEqual(doc["body_text"], self.article.body_text)

    def test_contains_source_domain(self):
        doc = article_to_doc(self.article)
        self.assertEqual(doc["source_domain"], "example.com")

    def test_contains_language(self):
        doc = article_to_doc(self.article)
        self.assertEqual(doc["language"], "en")

    def test_contains_tags(self):
        doc = article_to_doc(self.article)
        self.assertEqual(doc["tags"], ["gold", "mining"])

    def test_sentiment_from_signals(self):
        doc = article_to_doc(self.article)
        self.assertEqual(doc["sentiment"], "positive")

    def test_sentiment_empty_when_missing(self):
        self.article.signals = {}
        doc = article_to_doc(self.article)
        self.assertEqual(doc["sentiment"], "")

    def test_published_at_none_when_not_set(self):
        doc = article_to_doc(self.article)
        self.assertIsNone(doc["published_at"])

    def test_published_at_iso_when_set(self):
        from datetime import datetime, timezone as dt_tz
        self.article.published_at = datetime(2025, 4, 22, 8, 0, 0,
                                             tzinfo=dt_tz.utc)
        doc = article_to_doc(self.article)
        self.assertIn("2025-04-22", doc["published_at"])

    def test_url_is_article_url(self):
        doc = article_to_doc(self.article)
        self.assertEqual(doc["url"], self.article.url)

    def test_is_duplicate_field(self):
        doc = article_to_doc(self.article)
        self.assertFalse(doc["is_duplicate"])

    def test_created_at_is_iso_string(self):
        doc = article_to_doc(self.article)
        self.assertIsInstance(doc["created_at"], str)
        self.assertRegex(doc["created_at"], r'\d{4}-\d{2}-\d{2}')


# ── index_article ─────────────────────────────────────────────────────────────

class IndexArticleTests(TestCase):

    def test_calls_client_index(self):
        article = make_article()
        client  = _mock_client()
        with patch("fetcher.search.get_client", return_value=client):
            index_article(article)
        client.index.assert_called_once()

    def test_uses_article_pk_as_document_id(self):
        article = make_article()
        client  = _mock_client()
        with patch("fetcher.search.get_client", return_value=client):
            index_article(article)
        _, kwargs = client.index.call_args
        self.assertEqual(kwargs["id"], str(article.pk))

    def test_document_matches_article_to_doc(self):
        article = make_article()
        client  = _mock_client()
        with patch("fetcher.search.get_client", return_value=client):
            index_article(article)
        _, kwargs = client.index.call_args
        self.assertEqual(kwargs["document"]["title"], article.title)

    def test_uses_configured_index_name(self):
        article = make_article()
        client  = _mock_client()
        with patch("fetcher.search.get_client", return_value=client):
            index_article(article)
        _, kwargs = client.index.call_args
        self.assertEqual(kwargs["index"], "articles")


# ── delete_article ────────────────────────────────────────────────────────────

class DeleteArticleTests(TestCase):

    def test_calls_client_delete(self):
        client = _mock_client()
        with patch("fetcher.search.get_client", return_value=client):
            delete_article(42)
        client.delete.assert_called_once_with(index="articles", id="42")

    def test_silently_ignores_not_found(self):
        from elasticsearch import NotFoundError
        client = _mock_client()
        client.delete.side_effect = NotFoundError(
            message="not found", meta=MagicMock(status=404), body={}
        )
        with patch("fetcher.search.get_client", return_value=client):
            delete_article(99)  # should not raise


# ── ensure_index ──────────────────────────────────────────────────────────────

class EnsureIndexTests(TestCase):

    def test_creates_index_when_absent(self):
        client = _mock_client()
        client.indices.exists.return_value = False
        with patch("fetcher.search.get_client", return_value=client):
            ensure_index()
        client.indices.create.assert_called_once()

    def test_passes_mapping_on_create(self):
        client = _mock_client()
        client.indices.exists.return_value = False
        with patch("fetcher.search.get_client", return_value=client):
            ensure_index()
        _, kwargs = client.indices.create.call_args
        self.assertEqual(kwargs["mappings"], INDEX_MAPPING)

    def test_skips_create_when_index_exists(self):
        client = _mock_client()
        client.indices.exists.return_value = True
        with patch("fetcher.search.get_client", return_value=client):
            ensure_index()
        client.indices.create.assert_not_called()


# ── drop_index ────────────────────────────────────────────────────────────────

class DropIndexTests(TestCase):

    def test_deletes_existing_index(self):
        client = _mock_client()
        client.indices.exists.return_value = True
        with patch("fetcher.search.get_client", return_value=client):
            drop_index()
        client.indices.delete.assert_called_once_with(index="articles")

    def test_skips_delete_when_index_absent(self):
        client = _mock_client()
        client.indices.exists.return_value = False
        with patch("fetcher.search.get_client", return_value=client):
            drop_index()
        client.indices.delete.assert_not_called()


# ── bulk_index_articles ───────────────────────────────────────────────────────

class BulkIndexArticlesTests(TestCase):

    def test_returns_success_and_error_counts(self):
        articles = [make_article(), make_article()]
        with patch("fetcher.search.get_client", return_value=_mock_client()), \
             patch("fetcher.search.es_bulk", return_value=(2, 0)) as mock_bulk:
            successes, errors = bulk_index_articles(articles)
        self.assertEqual(successes, 2)
        self.assertEqual(errors, 0)

    def test_passes_correct_index_in_actions(self):
        articles = [make_article()]
        captured_actions = []

        def fake_bulk(client, actions, **kwargs):
            captured_actions.extend(list(actions))
            return (1, 0)

        with patch("fetcher.search.get_client", return_value=_mock_client()), \
             patch("fetcher.search.es_bulk", side_effect=fake_bulk):
            bulk_index_articles(articles)

        self.assertEqual(len(captured_actions), 1)
        self.assertEqual(captured_actions[0]["_index"], "articles")

    def test_uses_article_pk_as_action_id(self):
        article = make_article()
        captured = []

        def fake_bulk(client, actions, **kwargs):
            captured.extend(list(actions))
            return (1, 0)

        with patch("fetcher.search.get_client", return_value=_mock_client()), \
             patch("fetcher.search.es_bulk", side_effect=fake_bulk):
            bulk_index_articles([article])

        self.assertEqual(captured[0]["_id"], str(article.pk))


# ── search_articles ───────────────────────────────────────────────────────────

class SearchArticlesTests(TestCase):

    def _search(self, query="gold", client=None, **kwargs):
        client = client or _mock_client()
        with patch("fetcher.search.get_client", return_value=client):
            return search_articles(query, **kwargs), client

    def test_calls_client_search(self):
        _, client = self._search()
        client.search.assert_called_once()

    def test_always_filters_out_duplicates(self):
        _, client = self._search()
        _, kwargs = client.search.call_args
        filters = kwargs["query"]["bool"]["filter"]
        self.assertIn({"term": {"is_duplicate": False}}, filters)

    def test_uses_multi_match_for_non_empty_query(self):
        _, client = self._search(query="gold mining")
        _, kwargs = client.search.call_args
        must = kwargs["query"]["bool"]["must"]
        self.assertEqual(must[0]["multi_match"]["query"], "gold mining")

    def test_title_boosted_in_multi_match(self):
        _, client = self._search(query="gold")
        _, kwargs = client.search.call_args
        fields = kwargs["query"]["bool"]["must"][0]["multi_match"]["fields"]
        self.assertIn("title^3", fields)

    def test_empty_query_uses_match_all(self):
        _, client = self._search(query="")
        _, kwargs = client.search.call_args
        must = kwargs["query"]["bool"]["must"]
        self.assertIn("match_all", must[0])

    def test_source_domain_filter_applied(self):
        _, client = self._search(source_domain="miningweekly.com")
        _, kwargs = client.search.call_args
        filters = kwargs["query"]["bool"]["filter"]
        self.assertIn({"term": {"source_domain": "miningweekly.com"}}, filters)

    def test_language_filter_applied(self):
        _, client = self._search(language="en")
        _, kwargs = client.search.call_args
        filters = kwargs["query"]["bool"]["filter"]
        self.assertIn({"term": {"language": "en"}}, filters)

    def test_sentiment_filter_applied(self):
        _, client = self._search(sentiment="positive")
        _, kwargs = client.search.call_args
        filters = kwargs["query"]["bool"]["filter"]
        self.assertIn({"term": {"sentiment": "positive"}}, filters)

    def test_no_optional_filters_when_not_provided(self):
        _, client = self._search()
        _, kwargs = client.search.call_args
        filters = kwargs["query"]["bool"]["filter"]
        # Only the is_duplicate filter
        self.assertEqual(len(filters), 1)

    def test_from_and_size_passed(self):
        _, client = self._search(from_=10, size=5)
        _, kwargs = client.search.call_args
        self.assertEqual(kwargs["from_"], 10)
        self.assertEqual(kwargs["size"], 5)

    def test_returns_hits_list(self):
        client = _mock_client()
        client.search.return_value = {
            "hits": {"hits": [{"_id": "1", "_score": 1.0, "_source": {"title": "Gold"}}]}
        }
        results, _ = self._search(client=client)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["_source"]["title"], "Gold")


# ── Integration: parse_page indexes non-duplicates ────────────────────────────

class ParsePageIndexIntegrationTests(TestCase):

    def _mock_newspaper(self):
        mock = MagicMock()
        mock.title = "Gold article"
        mock.text  = "Gold surged today. " * 20
        mock.authors          = []
        mock.publish_date     = None
        mock.meta_lang        = "en"
        mock.tags             = set()
        mock.meta_keywords    = ""
        mock.meta_description = ""
        return mock

    def _parse(self, index_patch=None):
        du = DiscoveredURL.objects.create(
            seed=make_seed(), url=_url(), title="Test"
        )
        fp = FetchedPage.objects.create(
            discovered_url=du, status_code=200, content_type="text/html",
            encoding="utf-8", raw_html="<html><body></body></html>",
            fetch_duration_ms=100,
        )
        with patch("fetcher.services.newspaper.Article", return_value=self._mock_newspaper()), \
             patch("fetcher.services.run_nlp", return_value={"sentiment": "neutral"}), \
             (index_patch or patch("fetcher.services.index_article")) as mock_idx:
            from fetcher.services import parse_page
            result = parse_page(fp)
        return result, mock_idx

    def test_index_called_for_unique_article(self):
        _, mock_idx = self._parse()
        mock_idx.assert_called_once()

    def test_index_not_called_for_duplicate(self):
        with patch("fetcher.services.index_article") as mock_idx, \
             patch("fetcher.services.newspaper.Article", return_value=self._mock_newspaper()), \
             patch("fetcher.services.run_nlp", return_value={}), \
             patch("fetcher.services.check_and_mark_duplicate",
                   side_effect=lambda a: _mark_as_duplicate(a)):
            du = DiscoveredURL.objects.create(
                seed=make_seed(), url=_url(), title="Test"
            )
            fp = FetchedPage.objects.create(
                discovered_url=du, status_code=200, content_type="text/html",
                encoding="utf-8", raw_html="<html><body></body></html>",
                fetch_duration_ms=100,
            )
            from fetcher.services import parse_page
            parse_page(fp)
        mock_idx.assert_not_called()

    def test_parse_succeeds_when_index_raises(self):
        result, _ = self._parse(
            index_patch=patch("fetcher.services.index_article",
                              side_effect=Exception("ES down"))
        )
        self.assertIsNotNone(result)


def _mark_as_duplicate(article):
    article.is_duplicate = True
    article.save(update_fields=["is_duplicate"])


# ── Management command: index_articles ────────────────────────────────────────

class IndexArticlesCommandTests(TestCase):

    def _call(self, *args, **kwargs):
        out = StringIO()
        call_command("index_articles", *args, stdout=out, **kwargs)
        return out.getvalue()

    def _patch(self):
        return (
            patch("fetcher.management.commands.index_articles.ensure_index"),
            patch("fetcher.management.commands.index_articles.drop_index"),
            patch("fetcher.management.commands.index_articles.bulk_index_articles",
                  return_value=(0, 0)),
        )

    def test_no_articles_prints_message(self):
        p1, p2, p3 = self._patch()
        with p1, p2, p3:
            output = self._call()
        self.assertIn("No articles", output)

    def test_calls_ensure_index(self):
        p1, p2, p3 = self._patch()
        with p1 as mock_ensure, p2, p3:
            self._call()
        mock_ensure.assert_called_once()

    def test_indexes_non_duplicate_articles(self):
        make_article()
        make_article(is_duplicate=True)
        p1, p2, p3 = self._patch()
        with p1, p2, patch(
            "fetcher.management.commands.index_articles.bulk_index_articles",
            return_value=(1, 0),
        ) as mock_bulk:
            self._call()
        args, _ = mock_bulk.call_args
        qs = args[0]
        self.assertEqual(qs.count(), 1)
        self.assertFalse(qs.first().is_duplicate)

    def test_reset_calls_drop_index(self):
        p1, p2, p3 = self._patch()
        with p1, p2 as mock_drop, p3:
            self._call(reset=True)
        mock_drop.assert_called_once()

    def test_no_reset_does_not_drop(self):
        p1, p2, p3 = self._patch()
        with p1, p2 as mock_drop, p3:
            self._call()
        mock_drop.assert_not_called()

    def test_output_shows_counts(self):
        make_article()
        p1, p2, p3 = self._patch()
        with p1, p2, patch(
            "fetcher.management.commands.index_articles.bulk_index_articles",
            return_value=(1, 0),
        ):
            output = self._call()
        self.assertIn("indexed=1", output)
        self.assertIn("errors=0", output)

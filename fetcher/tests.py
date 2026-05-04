"""
Tests for fetcher models.
Run with: python manage.py test fetcher
"""
from django.test import TestCase

from discovery.models import SeedSource, DiscoveredURL, SourceType
from fetcher.models import FetchedPage, ParsedArticle

# Counter to generate unique URLs across test methods
_url_counter = 0

def _unique_url(base="https://example.com/article/"):
    global _url_counter
    _url_counter += 1
    return f"{base}{_url_counter}"


# ── Fixtures ──────────────────────────────────────────────────────────────────

def make_seed() -> SeedSource:
    # get_or_create avoids the unique constraint on url across tests
    seed, _ = SeedSource.objects.get_or_create(
        url="https://example.com/feed",
        defaults={"name": "Test Seed", "source_type": SourceType.RSS},
    )
    return seed


def make_discovered_url(seed=None, url=None) -> DiscoveredURL:
    return DiscoveredURL.objects.create(
        seed=seed or make_seed(),
        url=url or _unique_url(),
    )


def make_fetched_page(discovered_url=None, status_code=200, **kwargs) -> FetchedPage:
    # Resolve discovered_url once — never call make_discovered_url() twice
    du = discovered_url if discovered_url is not None else make_discovered_url()
    return FetchedPage.objects.create(
        discovered_url=du,
        status_code=status_code,
        content_type="text/html; charset=utf-8",
        encoding="utf-8",
        raw_html="<html><body><h1>Test Article</h1><p>Content here.</p></body></html>",
        fetch_duration_ms=342,
        **kwargs,
    )


def make_parsed_article(fetched_page=None, **kwargs) -> ParsedArticle:
    fp = fetched_page if fetched_page is not None else make_fetched_page()
    defaults = dict(
        title="Gold Strike in Kalahari",
        body_text=(
            "A major gold deposit was discovered in the Kalahari region today. "
            "Geologists confirmed the find after months of drilling and surveys. "
            "Mining operations are expected to begin next year pending approval. "
            "The deposit is estimated to contain over 50 tonnes of high-grade gold. "
            "Industry analysts say this is one of the largest finds in a decade."
        ),
        summary="A major gold deposit discovered in the Kalahari.",
        author="Jane Smith",
        source_domain="www.miningweekly.com",
        language="en",
        tags=["gold", "mining", "Kalahari"],
        signals={"keywords": ["gold", "deposit", "Kalahari"], "sentiment": "positive"},
    )
    defaults.update(kwargs)
    return ParsedArticle.objects.create(fetched_page=fp, **defaults)


# ── FetchedPage tests ─────────────────────────────────────────────────────────

class FetchedPageModelTests(TestCase):

    def setUp(self):
        self.du   = make_discovered_url()
        self.page = make_fetched_page(discovered_url=self.du)

    def test_str_includes_status_and_url(self):
        self.assertIn("200", str(self.page))
        self.assertIn("example.com", str(self.page))

    def test_url_property_returns_source_url(self):
        self.assertEqual(self.page.url, self.du.url)

    def test_was_successful_true_for_200(self):
        self.assertTrue(self.page.was_successful)

    def test_was_successful_true_for_201(self):
        page = make_fetched_page(status_code=201)
        self.assertTrue(page.was_successful)

    def test_was_successful_false_for_404(self):
        page = make_fetched_page(status_code=404)
        self.assertFalse(page.was_successful)

    def test_was_successful_false_for_500(self):
        page = make_fetched_page(status_code=500)
        self.assertFalse(page.was_successful)

    def test_one_to_one_with_discovered_url(self):
        from django.db import IntegrityError
        with self.assertRaises(IntegrityError):
            FetchedPage.objects.create(
                discovered_url=self.du,
                status_code=200,
                raw_html="<html></html>",
            )

    def test_reverse_relation_from_discovered_url(self):
        self.assertEqual(self.du.fetched_page, self.page)

    def test_fetched_at_auto_set(self):
        self.assertIsNotNone(self.page.fetched_at)

    def test_timestamps_set(self):
        self.assertIsNotNone(self.page.created_at)
        self.assertIsNotNone(self.page.updated_at)

    def test_cascade_delete_from_discovered_url(self):
        page_id = self.page.pk
        self.du.delete()
        self.assertFalse(FetchedPage.objects.filter(pk=page_id).exists())


# ── ParsedArticle tests ───────────────────────────────────────────────────────

class ParsedArticleModelTests(TestCase):

    def setUp(self):
        self.page    = make_fetched_page()
        self.article = make_parsed_article(fetched_page=self.page)

    def test_str_returns_title(self):
        self.assertEqual(str(self.article), "Gold Strike in Kalahari")

    def test_str_falls_back_to_url_when_no_title(self):
        article = make_parsed_article(fetched_page=make_fetched_page(), title="")
        self.assertIn("example.com", str(article))

    def test_url_property_traverses_relations(self):
        self.assertEqual(self.article.url, self.page.discovered_url.url)

    def test_word_count_counts_words(self):
        self.assertGreater(self.article.word_count, 10)

    def test_word_count_zero_for_empty_body(self):
        article = make_parsed_article(fetched_page=make_fetched_page(), body_text="")
        self.assertEqual(article.word_count, 0)

    def test_has_body_true_for_long_article(self):
        self.assertTrue(self.article.has_body)

    def test_has_body_false_for_short_content(self):
        article = make_parsed_article(fetched_page=make_fetched_page(), body_text="Short.")
        self.assertFalse(article.has_body)

    def test_tags_stored_as_list(self):
        self.assertIsInstance(self.article.tags, list)
        self.assertIn("gold", self.article.tags)

    def test_signals_stored_as_dict(self):
        self.assertIsInstance(self.article.signals, dict)
        self.assertIn("keywords", self.article.signals)
        self.assertIn("gold", self.article.signals["keywords"])

    def test_one_to_one_with_fetched_page(self):
        from django.db import IntegrityError
        with self.assertRaises(IntegrityError):
            ParsedArticle.objects.create(fetched_page=self.page, title="Duplicate")

    def test_reverse_relation_from_fetched_page(self):
        self.assertEqual(self.page.parsed_article, self.article)

    def test_cascade_delete_from_fetched_page(self):
        article_id = self.article.pk
        self.page.delete()
        self.assertFalse(ParsedArticle.objects.filter(pk=article_id).exists())

    def test_cascade_delete_from_discovered_url(self):
        article_id = self.article.pk
        self.page.discovered_url.delete()
        self.assertFalse(ParsedArticle.objects.filter(pk=article_id).exists())

    def test_timestamps_set(self):
        self.assertIsNotNone(self.article.created_at)
        self.assertIsNotNone(self.article.updated_at)

    def test_default_tags_is_empty_list(self):
        article = ParsedArticle.objects.create(fetched_page=make_fetched_page())
        self.assertEqual(article.tags, [])

    def test_default_signals_is_empty_dict(self):
        article = ParsedArticle.objects.create(fetched_page=make_fetched_page())
        self.assertEqual(article.signals, {})
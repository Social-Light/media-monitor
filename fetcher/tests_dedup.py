"""
fetcher/tests_dedup.py

Tests for fetcher/dedup.py — all five public functions:

  normalize_text
  compute_content_hash
  extract_canonical_url
  find_duplicate
  check_and_mark_duplicate

Also covers the integration point in parse_page() (services.py).
"""
from unittest.mock import MagicMock, patch

from django.test import TestCase

from core.models import URLStatusChoices
from discovery.models import DiscoveredURL, SeedSource, SourceType
from fetcher.dedup import (
    check_and_mark_duplicate,
    compute_content_hash,
    extract_canonical_url,
    find_duplicate,
    normalize_text,
)
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
        seed=make_seed(), url=_url(), title="Test Article"
    )


def make_fetched_page(du=None, raw_html=None) -> FetchedPage:
    du = du or make_du()
    return FetchedPage.objects.create(
        discovered_url=du,
        status_code=200,
        content_type="text/html",
        encoding="utf-8",
        raw_html=raw_html or "<html><body><p>body text</p></body></html>",
        fetch_duration_ms=100,
    )


_DEFAULT_BODY = "Gold miners across southern Africa reported record profits. " * 10


def make_article(title="Gold miners report record profits",
                 body_text=None,
                 is_duplicate=False,
                 raw_html=None) -> ParsedArticle:
    fp = make_fetched_page(raw_html=raw_html)
    body = _DEFAULT_BODY if body_text is None else body_text
    return ParsedArticle.objects.create(
        fetched_page=fp,
        title=title,
        body_text=body,
        source_domain="example.com",
        is_duplicate=is_duplicate,
    )


HTML_WITH_CANONICAL = """<!DOCTYPE html>
<html>
<head>
  <link rel="canonical" href="https://example.com/canonical-article">
</head>
<body><p>Article body.</p></body>
</html>"""

HTML_WITHOUT_CANONICAL = """<!DOCTYPE html>
<html><head><title>No canonical</title></head>
<body><p>Article body.</p></body>
</html>"""

HTML_RELATIVE_CANONICAL = """<!DOCTYPE html>
<html>
<head>
  <link rel="canonical" href="/relative/path">
</head>
<body><p>Article body.</p></body>
</html>"""


# ── normalize_text ────────────────────────────────────────────────────────────

class NormalizeTextTests(TestCase):

    def test_lowercases(self):
        self.assertEqual(normalize_text("GOLD MINING"), "gold mining")

    def test_collapses_multiple_spaces(self):
        self.assertEqual(normalize_text("gold   mining"), "gold mining")

    def test_collapses_newlines(self):
        self.assertEqual(normalize_text("gold\nmining\nreport"), "gold mining report")

    def test_strips_leading_trailing_whitespace(self):
        self.assertEqual(normalize_text("  gold  "), "gold")

    def test_empty_string_returns_empty(self):
        self.assertEqual(normalize_text(""), "")

    def test_tabs_collapsed(self):
        self.assertEqual(normalize_text("gold\t\tmining"), "gold mining")


# ── compute_content_hash ──────────────────────────────────────────────────────

class ComputeContentHashTests(TestCase):

    def test_returns_64_char_hex_string(self):
        h = compute_content_hash("some text")
        self.assertEqual(len(h), 64)
        self.assertRegex(h, r'^[0-9a-f]{64}$')

    def test_same_text_same_hash(self):
        self.assertEqual(
            compute_content_hash("gold miners report"),
            compute_content_hash("gold miners report"),
        )

    def test_different_text_different_hash(self):
        self.assertNotEqual(
            compute_content_hash("gold miners report"),
            compute_content_hash("diamond miners report"),
        )

    def test_normalisation_makes_hashes_equal(self):
        # Extra whitespace and case differences should hash identically
        self.assertEqual(
            compute_content_hash("Gold  Miners Report"),
            compute_content_hash("gold miners report"),
        )

    def test_empty_string_returns_hash(self):
        h = compute_content_hash("")
        self.assertEqual(len(h), 64)


# ── extract_canonical_url ─────────────────────────────────────────────────────

class ExtractCanonicalUrlTests(TestCase):

    def test_extracts_canonical_href(self):
        result = extract_canonical_url(HTML_WITH_CANONICAL, "https://example.com/page")
        self.assertEqual(result, "https://example.com/canonical-article")

    def test_returns_none_when_no_canonical_tag(self):
        result = extract_canonical_url(HTML_WITHOUT_CANONICAL, "https://example.com/page")
        self.assertIsNone(result)

    def test_resolves_relative_canonical(self):
        result = extract_canonical_url(
            HTML_RELATIVE_CANONICAL, "https://example.com/page"
        )
        self.assertEqual(result, "https://example.com/relative/path")

    def test_returns_none_on_empty_html(self):
        result = extract_canonical_url("", "https://example.com/page")
        self.assertIsNone(result)

    def test_does_not_raise_on_malformed_html(self):
        result = extract_canonical_url("<<not html>>", "https://example.com/page")
        # Should return None, never raise
        self.assertIsNone(result)

    def test_returns_none_when_href_is_empty(self):
        html = '<html><head><link rel="canonical" href=""></head></html>'
        result = extract_canonical_url(html, "https://example.com/page")
        self.assertIsNone(result)


# ── find_duplicate ────────────────────────────────────────────────────────────

class FindDuplicateTests(TestCase):

    def test_returns_none_when_no_articles_exist(self):
        result = find_duplicate("Some title", "abc123")
        self.assertIsNone(result)

    def test_returns_none_when_no_match(self):
        make_article(title="Completely different topic")
        result = find_duplicate("Unrelated article title", "deadbeef")
        self.assertIsNone(result)

    def test_matches_by_exact_content_hash(self):
        body = "Identical body text for both articles. " * 10
        original = make_article(body_text=body)
        original.content_hash = compute_content_hash(body)
        original.save(update_fields=["content_hash"])

        result = find_duplicate("Different title", original.content_hash)
        self.assertEqual(result, original)

    def test_matches_by_title_similarity(self):
        original = make_article(title="Gold miners report record profits in Q3")
        # Slightly different title — same subject, minor variation
        result = find_duplicate("Gold miners report record profits in Q4", "nonexistent_hash")
        self.assertEqual(result, original)

    def test_does_not_match_low_similarity_title(self):
        make_article(title="Platinum prices rise sharply")
        result = find_duplicate("Gold miners report profits", "nonexistent_hash")
        self.assertIsNone(result)

    def test_excludes_self_by_id(self):
        original = make_article(title="Gold miners report record profits in Q3")
        original.content_hash = compute_content_hash(original.body_text)
        original.save(update_fields=["content_hash"])

        result = find_duplicate(original.title, original.content_hash, exclude_id=original.pk)
        self.assertIsNone(result)

    def test_does_not_return_articles_marked_as_duplicate(self):
        body = "Shared body text. " * 20
        existing_dup = make_article(body_text=body, is_duplicate=True)
        existing_dup.content_hash = compute_content_hash(body)
        existing_dup.save(update_fields=["content_hash"])

        result = find_duplicate("Any title", existing_dup.content_hash)
        self.assertIsNone(result)

    def test_hash_match_takes_priority_over_title_match(self):
        same_body = "Exact same body content for hash test. " * 10
        title_similar = make_article(
            title="Gold miners report record profits in Q3",
            body_text="Completely different body. " * 10,
        )
        hash_match = make_article(
            title="Unrelated title here",
            body_text=same_body,
        )
        hash_match.content_hash = compute_content_hash(same_body)
        hash_match.save(update_fields=["content_hash"])

        result = find_duplicate("Gold miners report record profits in Q3",
                                compute_content_hash(same_body))
        self.assertEqual(result, hash_match)


# ── check_and_mark_duplicate ──────────────────────────────────────────────────

class CheckAndMarkDuplicateTests(TestCase):

    def test_sets_content_hash(self):
        article = make_article()
        check_and_mark_duplicate(article)
        article.refresh_from_db()
        self.assertEqual(len(article.content_hash), 64)

    def test_content_hash_is_correct(self):
        article = make_article()
        check_and_mark_duplicate(article)
        article.refresh_from_db()
        self.assertEqual(article.content_hash, compute_content_hash(article.body_text))

    def test_sets_canonical_url_when_present(self):
        article = make_article(raw_html=HTML_WITH_CANONICAL)
        check_and_mark_duplicate(article)
        article.refresh_from_db()
        self.assertEqual(article.canonical_url, "https://example.com/canonical-article")

    def test_does_not_set_canonical_url_when_absent(self):
        article = make_article(raw_html=HTML_WITHOUT_CANONICAL)
        check_and_mark_duplicate(article)
        article.refresh_from_db()
        self.assertEqual(article.canonical_url, "")

    def test_returns_false_for_unique_article(self):
        article = make_article()
        result = check_and_mark_duplicate(article)
        self.assertFalse(result)

    def test_returns_true_for_duplicate(self):
        body = "Exactly duplicated body content for this article. " * 10
        original = make_article(body_text=body)
        check_and_mark_duplicate(original)  # sets content_hash on original

        duplicate = make_article(body_text=body)
        result = check_and_mark_duplicate(duplicate)
        self.assertTrue(result)

    def test_marks_duplicate_flag(self):
        body = "Another repeated body for duplicate testing. " * 10
        original = make_article(body_text=body)
        check_and_mark_duplicate(original)

        duplicate = make_article(body_text=body)
        check_and_mark_duplicate(duplicate)
        duplicate.refresh_from_db()
        self.assertTrue(duplicate.is_duplicate)

    def test_sets_duplicate_of_fk(self):
        body = "Shared body text used to test FK linkage. " * 10
        original = make_article(body_text=body)
        check_and_mark_duplicate(original)

        duplicate = make_article(body_text=body)
        check_and_mark_duplicate(duplicate)
        duplicate.refresh_from_db()
        self.assertEqual(duplicate.duplicate_of, original)

    def test_original_article_not_marked_as_duplicate(self):
        body = "Original content that should stay clean. " * 10
        original = make_article(body_text=body)
        check_and_mark_duplicate(original)
        original.refresh_from_db()
        self.assertFalse(original.is_duplicate)
        self.assertIsNone(original.duplicate_of)

    def test_no_body_text_skips_hash(self):
        article = make_article(body_text="")
        check_and_mark_duplicate(article)
        article.refresh_from_db()
        self.assertEqual(article.content_hash, "")


# ── Integration: parse_page calls check_and_mark_duplicate ───────────────────

class ParsePageDedupIntegrationTests(TestCase):
    """
    Verify that parse_page() populates dedup fields on the resulting article.
    BeautifulSoup/lxml run for real; newspaper3k is mocked.
    """

    BODY = "Gold surged to record highs today. " * 20

    def _mock_article(self, title="Gold hits record high", body=None):
        mock = MagicMock()
        mock.title            = title
        mock.text             = body or self.BODY
        mock.authors          = ["Jane Smith"]
        mock.publish_date     = None
        mock.meta_lang        = "en"
        mock.tags             = set()
        mock.meta_keywords    = "gold, mining"
        mock.meta_description = "Gold surged today."
        return mock

    def _parse(self, raw_html=None, article_mock=None):
        fp = make_fetched_page(raw_html=raw_html or HTML_WITHOUT_CANONICAL)
        mock = article_mock or self._mock_article()
        with patch("fetcher.services.newspaper.Article", return_value=mock):
            from fetcher.services import parse_page
            return parse_page(fp)

    def test_parse_page_sets_content_hash(self):
        result = self._parse()
        self.assertNotEqual(result.content_hash, "")
        self.assertEqual(len(result.content_hash), 64)

    def test_parse_page_sets_canonical_url(self):
        result = self._parse(raw_html=HTML_WITH_CANONICAL)
        self.assertEqual(result.canonical_url, "https://example.com/canonical-article")

    def test_parse_page_marks_second_identical_article_as_duplicate(self):
        first = self._parse()
        second = self._parse()  # same body → same content_hash
        second.refresh_from_db()
        self.assertTrue(second.is_duplicate)
        self.assertEqual(second.duplicate_of, first)

    def test_parse_page_does_not_mark_first_article_as_duplicate(self):
        first = self._parse()
        first.refresh_from_db()
        self.assertFalse(first.is_duplicate)

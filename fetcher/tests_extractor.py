"""
fetcher/tests_extractor.py

Tests for fetcher/extractor.py:
  get_rule_for_page
  apply_rule  (title, body, author, date selectors)

Plus integration tests verifying that parse_page() applies rules and
falls back to newspaper3k for fields not covered by the rule.
"""
from datetime import datetime
from unittest.mock import MagicMock, patch

from django.test import TestCase

from discovery.models import SeedSource, SourceType
from fetcher.extractor import apply_rule, get_rule_for_page
from fetcher.models import ExtractionRule, FetchedPage, ParsedArticle
from core.models import URLStatusChoices
from discovery.models import DiscoveredURL


# ── Fixtures ──────────────────────────────────────────────────────────────────

_counter = 0


def _url(domain="example.com"):
    global _counter
    _counter += 1
    return f"https://{domain}/article/{_counter}"


def make_seed() -> SeedSource:
    seed, _ = SeedSource.objects.get_or_create(
        url="https://example.com/feed",
        defaults={"name": "Test Seed", "source_type": SourceType.RSS},
    )
    return seed


def make_rule(domain="example.com", **kwargs) -> ExtractionRule:
    return ExtractionRule.objects.create(
        seed=make_seed(),
        domain=domain,
        **kwargs,
    )


def make_fetched_page(url=None, raw_html="<html><body></body></html>") -> FetchedPage:
    from discovery.models import DiscoveredURL
    du = DiscoveredURL.objects.create(
        seed=make_seed(),
        url=url or _url(),
        title="Test Article",
    )
    return FetchedPage.objects.create(
        discovered_url=du,
        status_code=200,
        content_type="text/html",
        encoding="utf-8",
        raw_html=raw_html,
        fetch_duration_ms=100,
    )


# ── Sample HTML fixtures ──────────────────────────────────────────────────────

HTML_FULL = """<!DOCTYPE html>
<html>
<head><title>Page title</title></head>
<body>
  <h1 class="article-title">Gold miners report record profits</h1>
  <div class="article-body">
    <p>Gold miners across southern Africa reported record profits this quarter.</p>
    <p>Analysts attribute the gains to higher commodity prices and lower costs.</p>
  </div>
  <span class="author-name">Jane Smith</span>
  <time class="pub-date" datetime="2025-04-22T08:00:00Z">22 April 2025</time>
</body>
</html>"""

HTML_TEXT_DATE = """<!DOCTYPE html>
<html><body>
  <h1 class="article-title">Platinum prices rise</h1>
  <div class="article-body"><p>Platinum surged today.</p></div>
  <span class="author-name">Bob Jones</span>
  <span class="pub-date">22 April 2025</span>
</body></html>"""

HTML_MISSING_ELEMENTS = """<!DOCTYPE html>
<html><body>
  <h1 class="article-title">Only a title here</h1>
</body></html>"""

HTML_EMPTY_TEXT = """<!DOCTYPE html>
<html><body>
  <h1 class="article-title">   </h1>
  <div class="article-body"></div>
</body></html>"""


# ── get_rule_for_page ─────────────────────────────────────────────────────────

class GetRuleForPageTests(TestCase):

    def test_returns_none_when_no_rules(self):
        result = get_rule_for_page("https://example.com/article/1")
        self.assertIsNone(result)

    def test_returns_matching_rule(self):
        rule = make_rule(domain="example.com")
        result = get_rule_for_page("https://example.com/article/1")
        self.assertEqual(result, rule)

    def test_does_not_match_different_domain(self):
        make_rule(domain="other.com")
        result = get_rule_for_page("https://example.com/article/1")
        self.assertIsNone(result)

    def test_ignores_inactive_rule(self):
        make_rule(domain="example.com", is_active=False)
        result = get_rule_for_page("https://example.com/article/1")
        self.assertIsNone(result)

    def test_matches_subdomain_exactly(self):
        make_rule(domain="www.example.com")
        result = get_rule_for_page("https://example.com/article/1")
        self.assertIsNone(result)

    def test_returns_none_for_empty_url(self):
        result = get_rule_for_page("")
        self.assertIsNone(result)

    def test_active_rule_returned_when_inactive_also_exists(self):
        make_rule(domain="example.com", is_active=False)
        active = make_rule(domain="example.com", is_active=True)
        result = get_rule_for_page("https://example.com/article/1")
        self.assertEqual(result, active)


# ── apply_rule — title ────────────────────────────────────────────────────────

class ApplyRuleTitleTests(TestCase):

    def _rule(self, **kwargs):
        return make_rule(**kwargs)

    def test_extracts_title(self):
        rule = self._rule(title_selector="h1.article-title")
        out = apply_rule(rule, HTML_FULL, "https://example.com/article/1")
        self.assertEqual(out["title"], "Gold miners report record profits")

    def test_title_not_in_result_when_selector_blank(self):
        rule = self._rule()  # no title_selector
        out = apply_rule(rule, HTML_FULL, "https://example.com/article/1")
        self.assertNotIn("title", out)

    def test_title_not_in_result_when_element_missing(self):
        rule = self._rule(title_selector="h1.nonexistent")
        out = apply_rule(rule, HTML_FULL, "https://example.com/article/1")
        self.assertNotIn("title", out)

    def test_title_not_in_result_when_text_is_empty(self):
        rule = self._rule(title_selector="h1.article-title")
        out = apply_rule(rule, HTML_EMPTY_TEXT, "https://example.com/article/1")
        self.assertNotIn("title", out)

    def test_bad_selector_does_not_raise(self):
        rule = self._rule(title_selector="[[[invalid")
        out = apply_rule(rule, HTML_FULL, "https://example.com/article/1")
        self.assertNotIn("title", out)


# ── apply_rule — body ─────────────────────────────────────────────────────────

class ApplyRuleBodyTests(TestCase):

    def test_extracts_body(self):
        rule = make_rule(body_selector="div.article-body")
        out = apply_rule(rule, HTML_FULL, "https://example.com/article/1")
        self.assertIn("body_text", out)
        self.assertIn("Gold miners", out["body_text"])

    def test_body_not_in_result_when_selector_blank(self):
        rule = make_rule()
        out = apply_rule(rule, HTML_FULL, "https://example.com/article/1")
        self.assertNotIn("body_text", out)

    def test_body_not_in_result_when_element_missing(self):
        rule = make_rule(body_selector="div.nonexistent")
        out = apply_rule(rule, HTML_FULL, "https://example.com/article/1")
        self.assertNotIn("body_text", out)

    def test_body_uses_newline_separator(self):
        rule = make_rule(body_selector="div.article-body")
        out = apply_rule(rule, HTML_FULL, "https://example.com/article/1")
        self.assertIn("\n", out["body_text"])


# ── apply_rule — author ───────────────────────────────────────────────────────

class ApplyRuleAuthorTests(TestCase):

    def test_extracts_author(self):
        rule = make_rule(author_selector="span.author-name")
        out = apply_rule(rule, HTML_FULL, "https://example.com/article/1")
        self.assertEqual(out["author"], "Jane Smith")

    def test_author_truncated_to_255(self):
        long_name = "A" * 300
        html = f'<html><body><span class="author-name">{long_name}</span></body></html>'
        rule = make_rule(author_selector="span.author-name")
        out = apply_rule(rule, html, "https://example.com/article/1")
        self.assertEqual(len(out["author"]), 255)

    def test_author_not_in_result_when_selector_blank(self):
        rule = make_rule()
        out = apply_rule(rule, HTML_FULL, "https://example.com/article/1")
        self.assertNotIn("author", out)


# ── apply_rule — date ─────────────────────────────────────────────────────────

class ApplyRuleDateTests(TestCase):

    def test_extracts_date_from_datetime_attribute(self):
        rule = make_rule(date_selector="time.pub-date")
        out = apply_rule(rule, HTML_FULL, "https://example.com/article/1")
        self.assertIn("published_at", out)
        self.assertEqual(out["published_at"].year, 2025)
        self.assertEqual(out["published_at"].month, 4)
        self.assertEqual(out["published_at"].day, 22)

    def test_extracts_date_from_text_with_format(self):
        rule = make_rule(
            date_selector="span.pub-date",
            date_format="%d %B %Y",
        )
        out = apply_rule(rule, HTML_TEXT_DATE, "https://example.com/article/1")
        self.assertIn("published_at", out)
        self.assertEqual(out["published_at"], datetime(2025, 4, 22))

    def test_date_not_in_result_when_selector_blank(self):
        rule = make_rule()
        out = apply_rule(rule, HTML_FULL, "https://example.com/article/1")
        self.assertNotIn("published_at", out)

    def test_date_not_in_result_when_element_missing(self):
        rule = make_rule(date_selector="time.nonexistent")
        out = apply_rule(rule, HTML_FULL, "https://example.com/article/1")
        self.assertNotIn("published_at", out)

    def test_bad_date_format_does_not_raise(self):
        rule = make_rule(date_selector="time.pub-date", date_format="%WRONG")
        out = apply_rule(rule, HTML_FULL, "https://example.com/article/1")
        self.assertNotIn("published_at", out)

    def test_date_not_in_result_when_text_empty(self):
        html = '<html><body><time class="pub-date"></time></body></html>'
        rule = make_rule(date_selector="time.pub-date")
        out = apply_rule(rule, html, "https://example.com/article/1")
        self.assertNotIn("published_at", out)


# ── apply_rule — resilience ───────────────────────────────────────────────────

class ApplyRuleResilienceTests(TestCase):

    def test_returns_empty_dict_on_empty_html(self):
        rule = make_rule(
            title_selector="h1.article-title",
            body_selector="div.article-body",
        )
        out = apply_rule(rule, "", "https://example.com/article/1")
        self.assertEqual(out, {})

    def test_partial_results_when_some_selectors_miss(self):
        rule = make_rule(
            title_selector="h1.article-title",
            body_selector="div.nonexistent",
        )
        out = apply_rule(rule, HTML_FULL, "https://example.com/article/1")
        self.assertIn("title", out)
        self.assertNotIn("body_text", out)

    def test_multiple_fields_extracted_together(self):
        rule = make_rule(
            title_selector="h1.article-title",
            body_selector="div.article-body",
            author_selector="span.author-name",
            date_selector="time.pub-date",
        )
        out = apply_rule(rule, HTML_FULL, "https://example.com/article/1")
        self.assertIn("title", out)
        self.assertIn("body_text", out)
        self.assertIn("author", out)
        self.assertIn("published_at", out)


# ── Integration: parse_page applies / falls back to rule ─────────────────────

class ParsePageExtractionRuleIntegrationTests(TestCase):
    """
    Verifies that parse_page() overlays ExtractionRule results on top of
    newspaper3k output and falls back to newspaper3k when no rule is active.
    """

    NP_TITLE = "Newspaper title"
    NP_BODY  = "Newspaper body text. " * 20
    RULE_TITLE = "Gold miners report record profits"

    def _mock_article(self, title=None, body=None):
        mock = MagicMock()
        mock.title            = title or self.NP_TITLE
        mock.text             = body  or self.NP_BODY
        mock.authors          = ["Jane Smith"]
        mock.publish_date     = None
        mock.meta_lang        = "en"
        mock.tags             = set()
        mock.meta_keywords    = ""
        mock.meta_description = ""
        return mock

    def _parse(self, raw_html, article_mock=None):
        fp = make_fetched_page(
            url=_url("example.com"),
            raw_html=raw_html,
        )
        mock = article_mock or self._mock_article()
        with patch("fetcher.services.newspaper.Article", return_value=mock):
            from fetcher.services import parse_page
            return parse_page(fp)

    def test_rule_title_overrides_newspaper_title(self):
        make_rule(domain="example.com", title_selector="h1.article-title")
        result = self._parse(HTML_FULL)
        self.assertEqual(result.title, self.RULE_TITLE)

    def test_rule_body_overrides_newspaper_body(self):
        make_rule(domain="example.com", body_selector="div.article-body")
        result = self._parse(HTML_FULL)
        self.assertIn("Gold miners", result.body_text)

    def test_newspaper_title_used_when_no_rule(self):
        result = self._parse(HTML_FULL)
        self.assertEqual(result.title, self.NP_TITLE)

    def test_newspaper_field_kept_when_rule_selector_blank(self):
        # Rule has title selector but no body selector
        make_rule(domain="example.com", title_selector="h1.article-title")
        result = self._parse(HTML_FULL)
        # body_text should still come from newspaper3k
        self.assertIn("Newspaper body", result.body_text)

    def test_newspaper_field_kept_when_rule_selector_misses(self):
        make_rule(domain="example.com", title_selector="h1.nonexistent")
        result = self._parse(HTML_FULL)
        self.assertEqual(result.title, self.NP_TITLE)

    def test_inactive_rule_ignored(self):
        make_rule(domain="example.com", title_selector="h1.article-title", is_active=False)
        result = self._parse(HTML_FULL)
        self.assertEqual(result.title, self.NP_TITLE)

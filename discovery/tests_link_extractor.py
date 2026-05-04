"""
Tests for discovery/services/link_extractor.py
Uses mock HTML responses — no network required.
"""
from unittest.mock import patch, MagicMock

from django.test import TestCase

from discovery.services.link_extractor import extract_links, _looks_like_article


# ── HTML fixtures ─────────────────────────────────────────────────────────────

def _mock_response(html: str, url: str = "https://example.com/"):
    mock = MagicMock()
    mock.text = html
    mock.url  = url
    return mock


BASE_PAGE = """
<html>
<body>
  <a href="/news/gold-strike">Gold Strike in Kalahari</a>
  <a href="/news/diamond-report">Diamond Report 2025</a>
  <a href="https://external.com/article">External link</a>
  <a href="mailto:editor@example.com">Email us</a>
  <a href="javascript:void(0)">Click here</a>
  <a href="/about">About us</a>
</body>
</html>"""

KEYWORD_PAGE = """
<html>
<body>
  <a href="/news/lithium-surge">Lithium prices surge</a>
  <a href="/news/coal-report">Coal industry report</a>
  <a href="/news/gold-outlook">Gold outlook for 2025</a>
</body>
</html>"""

DUPE_PAGE = """
<html>
<body>
  <a href="/news/story-1">Story One</a>
  <a href="/news/story-1">Story One again</a>
  <a href="/news/story-1">Story One third time</a>
</body>
</html>"""


# ── _looks_like_article ───────────────────────────────────────────────────────

class LooksLikeArticleTests(TestCase):

    def test_news_path_is_article(self):
        self.assertTrue(_looks_like_article("https://example.com/news/gold-prices"))

    def test_article_path_is_article(self):
        self.assertTrue(_looks_like_article("https://example.com/article/mining-2025"))

    def test_blog_path_is_article(self):
        self.assertTrue(_looks_like_article("https://example.com/blog/copper-demand"))

    def test_short_root_path_is_not_article(self):
        self.assertFalse(_looks_like_article("https://example.com/about"))

    def test_home_page_is_not_article(self):
        self.assertFalse(_looks_like_article("https://example.com/"))

    def test_deep_slug_path_is_article(self):
        self.assertTrue(_looks_like_article("https://example.com/sector/mining/story-title"))


# ── extract_links ─────────────────────────────────────────────────────────────

class ExtractLinksTests(TestCase):

    def _call(self, html=BASE_PAGE, url="https://example.com/", **kwargs):
        with patch("discovery.services.link_extractor.safe_get",
                   return_value=_mock_response(html, url)):
            return extract_links(url, **kwargs)

    def test_returns_list_of_dicts(self):
        results = self._call()
        self.assertIsInstance(results, list)
        if results:
            self.assertIn("url", results[0])

    def test_all_keys_present(self):
        results = self._call()
        for r in results:
            for key in ("url", "title", "snippet", "published_at", "source_type"):
                self.assertIn(key, r)

    def test_source_type_is_seed_url(self):
        results = self._call()
        for r in results:
            self.assertEqual(r["source_type"], "seed_url")

    def test_external_links_excluded_by_default(self):
        results = self._call()
        urls = [r["url"] for r in results]
        self.assertNotIn("https://external.com/article", urls)

    def test_mailto_links_excluded(self):
        results = self._call()
        urls = [r["url"] for r in results]
        self.assertFalse(any("mailto" in u for u in urls))

    def test_javascript_links_excluded(self):
        results = self._call()
        urls = [r["url"] for r in results]
        self.assertFalse(any("javascript" in u for u in urls))

    def test_duplicate_links_deduplicated(self):
        results = self._call(html=DUPE_PAGE)
        urls = [r["url"] for r in results]
        self.assertEqual(len(urls), len(set(urls)))
        self.assertEqual(len(results), 1)

    def test_anchor_text_used_as_title(self):
        results = self._call()
        titles = [r["title"] for r in results]
        self.assertIn("Gold Strike in Kalahari", titles)

    def test_keyword_filter_keeps_matching(self):
        results = self._call(html=KEYWORD_PAGE, keywords=["lithium", "gold"])
        urls = [r["url"] for r in results]
        self.assertTrue(any("lithium" in u for u in urls))
        self.assertTrue(any("gold" in u for u in urls))

    def test_keyword_filter_drops_non_matching(self):
        results = self._call(html=KEYWORD_PAGE, keywords=["lithium", "gold"])
        urls = [r["url"] for r in results]
        self.assertFalse(any("coal" in u for u in urls))

    def test_max_links_cap_respected(self):
        results = self._call(html=KEYWORD_PAGE, max_links=1)
        self.assertLessEqual(len(results), 1)

    def test_returns_empty_on_network_failure(self):
        with patch("discovery.services.link_extractor.safe_get", return_value=None):
            results = extract_links("https://example.com/")
        self.assertEqual(results, [])

    def test_article_links_only_filters_nav(self):
        results = self._call(article_links_only=True)
        urls = [r["url"] for r in results]
        # /about should be filtered out; /news/* should pass
        self.assertFalse(any(u.endswith("/about") for u in urls))
        self.assertTrue(any("/news/" in u for u in urls))
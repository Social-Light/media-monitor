"""
Tests for discovery/services/sitemap.py
Uses mock XML strings — no network required.
"""
from unittest.mock import patch, MagicMock

from django.test import TestCase

from discovery.services.sitemap import fetch_sitemap, _parse_sitemap_bytes


# ── XML fixtures ──────────────────────────────────────────────────────────────

URLSET_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url>
    <loc>https://example.com/article/gold-strike</loc>
    <lastmod>2025-04-20</lastmod>
  </url>
  <url>
    <loc>https://example.com/article/diamond-report</loc>
    <lastmod>2025-04-19</lastmod>
  </url>
</urlset>"""

SITEMAP_INDEX_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap>
    <loc>https://example.com/sitemap-news.xml</loc>
  </sitemap>
  <sitemap>
    <loc>https://example.com/sitemap-archive.xml</loc>
  </sitemap>
</sitemapindex>"""

GOOGLE_NEWS_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"
        xmlns:news="http://www.google.com/schemas/sitemap-news/0.9">
  <url>
    <loc>https://example.com/news/copper-boom</loc>
    <news:news>
      <news:publication>
        <news:name>Example News</news:name>
      </news:publication>
      <news:publication_date>2025-04-21T08:00:00Z</news:publication_date>
      <news:title>Copper boom drives investment</news:title>
    </news:news>
  </url>
</urlset>"""

EMPTY_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
</urlset>"""


def _make_mock_response(content: bytes, url: str = "https://example.com/sitemap.xml"):
    mock = MagicMock()
    mock.content = content
    mock.url     = url
    mock.headers = {}
    mock.status_code = 200
    return mock


# ── _parse_sitemap_bytes ──────────────────────────────────────────────────────

class ParseSitemapBytesTests(TestCase):

    def test_urlset_returns_correct_count(self):
        results = _parse_sitemap_bytes(URLSET_XML, "https://example.com/sitemap.xml")
        self.assertEqual(len(results), 2)

    def test_urlset_url_extracted(self):
        results = _parse_sitemap_bytes(URLSET_XML, "https://example.com/sitemap.xml")
        self.assertEqual(results[0]["url"], "https://example.com/article/gold-strike")

    def test_urlset_source_type_is_sitemap(self):
        results = _parse_sitemap_bytes(URLSET_XML, "https://example.com/sitemap.xml")
        self.assertEqual(results[0]["source_type"], "sitemap")

    def test_urlset_lastmod_parsed_as_date(self):
        results = _parse_sitemap_bytes(URLSET_XML, "https://example.com/sitemap.xml")
        self.assertIsNotNone(results[0]["published_at"])
        self.assertEqual(results[0]["published_at"].year, 2025)

    def test_sitemap_index_returns_index_sentinels(self):
        results = _parse_sitemap_bytes(SITEMAP_INDEX_XML, "https://example.com/sitemap.xml")
        self.assertEqual(len(results), 2)
        self.assertTrue(results[0].get("__index__"))
        self.assertEqual(results[0]["url"], "https://example.com/sitemap-news.xml")

    def test_google_news_title_extracted(self):
        results = _parse_sitemap_bytes(GOOGLE_NEWS_XML, "https://example.com/sitemap.xml")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["title"], "Copper boom drives investment")

    def test_google_news_date_extracted(self):
        results = _parse_sitemap_bytes(GOOGLE_NEWS_XML, "https://example.com/sitemap.xml")
        self.assertEqual(results[0]["published_at"].year, 2025)

    def test_empty_urlset_returns_empty_list(self):
        results = _parse_sitemap_bytes(EMPTY_XML, "https://example.com/sitemap.xml")
        self.assertEqual(results, [])


# ── fetch_sitemap ─────────────────────────────────────────────────────────────

class FetchSitemapTests(TestCase):

    def _patch(self, content: bytes, url="https://example.com/sitemap.xml"):
        return patch(
            "discovery.services.sitemap.safe_get",
            return_value=_make_mock_response(content, url),
        )

    def test_fetches_urlset(self):
        with self._patch(URLSET_XML):
            results = fetch_sitemap("https://example.com/sitemap.xml")
        self.assertEqual(len(results), 2)

    def test_returns_empty_on_network_failure(self):
        with patch("discovery.services.sitemap.safe_get", return_value=None):
            results = fetch_sitemap("https://example.com/sitemap.xml")
        self.assertEqual(results, [])

    def test_index_recursion_fetches_children(self):
        """Index sitemap should trigger a second fetch for each child."""
        child_xml = URLSET_XML  # one URL set child

        responses = [
            _make_mock_response(SITEMAP_INDEX_XML, "https://example.com/sitemap.xml"),
            _make_mock_response(child_xml, "https://example.com/sitemap-news.xml"),
            _make_mock_response(child_xml, "https://example.com/sitemap-archive.xml"),
        ]

        with patch("discovery.services.sitemap.safe_get", side_effect=responses):
            results = fetch_sitemap("https://example.com/sitemap.xml", max_depth=2)

        # 2 child sitemaps × 2 URLs each = 4 total
        self.assertEqual(len(results), 4)

    def test_max_depth_zero_skips_children(self):
        """At max_depth=0, index children should not be fetched."""
        with self._patch(SITEMAP_INDEX_XML):
            results = fetch_sitemap("https://example.com/sitemap.xml", max_depth=0)
        self.assertEqual(results, [])
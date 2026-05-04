"""
Tests for discovery/services/rss.py

Uses inline XML strings so no network access is required.
"""
from datetime import timezone
from unittest.mock import patch, MagicMock

from django.test import TestCase

from discovery.services.rss import fetch_feed, _strip_html, _parse_date, _get_summary


# ── Sample feed XML fixtures ──────────────────────────────────────────────────

RSS_FEED = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Test Mining Feed</title>
    <link>https://example.com</link>
    <item>
      <title>Gold prices surge to record high</title>
      <link>https://example.com/article/gold-prices</link>
      <description>&lt;p&gt;Gold hit a &lt;b&gt;record high&lt;/b&gt; today.&lt;/p&gt;</description>
      <pubDate>Tue, 22 Apr 2025 08:00:00 +0000</pubDate>
    </item>
    <item>
      <title>Annual mining report 2025</title>
      <link>https://example.com/article/mining-report</link>
      <description>Annual mining statistics released by the department.</description>
      <pubDate>Mon, 21 Apr 2025 10:00:00 +0000</pubDate>
    </item>
  </channel>
</rss>"""

ATOM_FEED = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Atom Test Feed</title>
  <entry>
    <title>Lithium discovery in Botswana</title>
    <link href="https://example.com/lithium-discovery"/>
    <summary>Major lithium deposit found in the Kalahari region.</summary>
    <updated>2025-04-20T09:00:00Z</updated>
  </entry>
</feed>"""

EMPTY_FEED = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>Empty</title></channel></rss>"""

NO_LINK_FEED = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Bad Feed</title>
    <item>
      <title>No link here</title>
      <description>This entry has no link element.</description>
    </item>
  </channel>
</rss>"""


def _mock_feedparser(xml_string: str):
    """Patch feedparser.parse to return a parsed result from a local XML string."""
    import feedparser
    return feedparser.parse(xml_string)


# ── _strip_html ───────────────────────────────────────────────────────────────

class StripHTMLTests(TestCase):

    def test_removes_tags(self):
        self.assertEqual(_strip_html("<p>Hello <b>world</b></p>"), "Hello world")

    def test_collapses_whitespace(self):
        self.assertEqual(_strip_html("<p>foo</p><p>bar</p>"), "foo bar")

    def test_plain_text_unchanged(self):
        self.assertEqual(_strip_html("plain text"), "plain text")

    def test_empty_string(self):
        self.assertEqual(_strip_html(""), "")


# ── fetch_feed ────────────────────────────────────────────────────────────────

class FetchFeedTests(TestCase):

    def _call(self, xml: str) -> list[dict]:
        with patch("feedparser.parse", return_value=_mock_feedparser(xml)):
            return fetch_feed("https://example.com/feed")

    def test_rss_returns_correct_count(self):
        results = self._call(RSS_FEED)
        self.assertEqual(len(results), 2)

    def test_rss_entry_has_all_keys(self):
        result = self._call(RSS_FEED)[0]
        self.assertIn("url",          result)
        self.assertIn("title",        result)
        self.assertIn("snippet",      result)
        self.assertIn("published_at", result)
        self.assertIn("source_type",  result)

    def test_rss_source_type_is_rss(self):
        result = self._call(RSS_FEED)[0]
        self.assertEqual(result["source_type"], "rss")

    def test_rss_title_extracted(self):
        result = self._call(RSS_FEED)[0]
        self.assertEqual(result["title"], "Gold prices surge to record high")

    def test_rss_url_extracted(self):
        result = self._call(RSS_FEED)[0]
        self.assertEqual(result["url"], "https://example.com/article/gold-prices")

    def test_rss_snippet_strips_html(self):
        result = self._call(RSS_FEED)[0]
        self.assertNotIn("<p>",  result["snippet"])
        self.assertNotIn("<b>",  result["snippet"])
        self.assertIn("record high", result["snippet"])

    def test_rss_date_parsed_correctly(self):
        result = self._call(RSS_FEED)[0]
        self.assertIsNotNone(result["published_at"])
        self.assertEqual(result["published_at"].year,  2025)
        self.assertEqual(result["published_at"].month, 4)
        self.assertEqual(result["published_at"].day,   22)

    def test_atom_feed_parsed(self):
        results = self._call(ATOM_FEED)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["title"], "Lithium discovery in Botswana")
        self.assertEqual(results[0]["url"], "https://example.com/lithium-discovery")

    def test_empty_feed_returns_empty_list(self):
        results = self._call(EMPTY_FEED)
        self.assertEqual(results, [])

    def test_entry_without_link_is_skipped(self):
        results = self._call(NO_LINK_FEED)
        self.assertEqual(results, [])
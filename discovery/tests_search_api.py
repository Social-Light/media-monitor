"""
Tests for discovery/services/search_api.py
Mocks all HTTP calls and settings — no real API keys needed.
"""
from unittest.mock import patch, MagicMock

from django.test import TestCase, override_settings

from discovery.services.search_api import search_bing, search_google, search


# ── Fixtures ──────────────────────────────────────────────────────────────────

BING_RESPONSE = {
    "value": [
        {
            "url":           "https://news.example.com/gold-prices",
            "name":          "Gold prices hit record",
            "description":   "Gold reached an all-time high on Tuesday.",
            "datePublished": "2025-04-22T08:00:00Z",
        },
        {
            "url":           "https://news.example.com/mining-output",
            "name":          "Mining output rises 12%",
            "description":   "Southern African mining output increased sharply.",
            "datePublished": "2025-04-21T10:00:00Z",
        },
    ]
}

GOOGLE_RESPONSE = {
    "items": [
        {
            "link":    "https://news.example.com/lithium-supply",
            "title":   "Lithium supply chain report",
            "snippet": "Analysts warn of global lithium shortfall.",
        },
    ]
}

BING_SETTINGS = {
    "BING_API_KEY":        "fake-bing-key",
    "GOOGLE_NEWS_API_KEY": "",
    "GOOGLE_CSE_ID":       "",
    "USER_AGENT":          "TestBot/1.0",
    "REQUEST_TIMEOUT":     10,
    "MAX_LINKS_PER_PAGE":  50,
    "POLITENESS_DELAY":    0,
}

GOOGLE_SETTINGS = {
    "BING_API_KEY":        "",
    "GOOGLE_NEWS_API_KEY": "fake-google-key",
    "GOOGLE_CSE_ID":       "fake-cse-id",
    "USER_AGENT":          "TestBot/1.0",
    "REQUEST_TIMEOUT":     10,
    "MAX_LINKS_PER_PAGE":  50,
    "POLITENESS_DELAY":    0,
}


def _mock_response(data: dict) -> MagicMock:
    mock = MagicMock()
    mock.json.return_value = data
    return mock


# ── search_bing ───────────────────────────────────────────────────────────────

class SearchBingTests(TestCase):

    @override_settings(CRAWLER=BING_SETTINGS)
    def test_returns_articles(self):
        with patch("discovery.services.search_api.safe_get",
                   return_value=_mock_response(BING_RESPONSE)):
            results = search_bing("gold mining")
        self.assertEqual(len(results), 2)

    @override_settings(CRAWLER=BING_SETTINGS)
    def test_article_has_all_keys(self):
        with patch("discovery.services.search_api.safe_get",
                   return_value=_mock_response(BING_RESPONSE)):
            result = search_bing("gold mining")[0]
        for key in ("url", "title", "snippet", "published_at", "source_type"):
            self.assertIn(key, result)

    @override_settings(CRAWLER=BING_SETTINGS)
    def test_source_type_is_search_api(self):
        with patch("discovery.services.search_api.safe_get",
                   return_value=_mock_response(BING_RESPONSE)):
            result = search_bing("gold mining")[0]
        self.assertEqual(result["source_type"], "search_api")

    @override_settings(CRAWLER=BING_SETTINGS)
    def test_date_parsed_correctly(self):
        with patch("discovery.services.search_api.safe_get",
                   return_value=_mock_response(BING_RESPONSE)):
            result = search_bing("gold mining")[0]
        self.assertIsNotNone(result["published_at"])
        self.assertEqual(result["published_at"].year, 2025)

    @override_settings(CRAWLER={**BING_SETTINGS, "BING_API_KEY": ""})
    def test_returns_empty_when_no_api_key(self):
        results = search_bing("gold mining")
        self.assertEqual(results, [])

    @override_settings(CRAWLER=BING_SETTINGS)
    def test_returns_empty_on_network_failure(self):
        with patch("discovery.services.search_api.safe_get", return_value=None):
            results = search_bing("gold mining")
        self.assertEqual(results, [])

    @override_settings(CRAWLER=BING_SETTINGS)
    def test_entries_without_url_are_skipped(self):
        bad_response = {"value": [{"name": "No URL here", "description": "..."}]}
        with patch("discovery.services.search_api.safe_get",
                   return_value=_mock_response(bad_response)):
            results = search_bing("gold")
        self.assertEqual(results, [])


# ── search_google ─────────────────────────────────────────────────────────────

class SearchGoogleTests(TestCase):

    @override_settings(CRAWLER=GOOGLE_SETTINGS)
    def test_returns_articles(self):
        with patch("discovery.services.search_api.safe_get",
                   return_value=_mock_response(GOOGLE_RESPONSE)):
            results = search_google("lithium")
        self.assertEqual(len(results), 1)

    @override_settings(CRAWLER=GOOGLE_SETTINGS)
    def test_source_type_is_search_api(self):
        with patch("discovery.services.search_api.safe_get",
                   return_value=_mock_response(GOOGLE_RESPONSE)):
            result = search_google("lithium")[0]
        self.assertEqual(result["source_type"], "search_api")

    @override_settings(CRAWLER={**GOOGLE_SETTINGS, "GOOGLE_NEWS_API_KEY": ""})
    def test_returns_empty_when_no_api_key(self):
        results = search_google("lithium")
        self.assertEqual(results, [])

    @override_settings(CRAWLER={**GOOGLE_SETTINGS, "GOOGLE_CSE_ID": ""})
    def test_returns_empty_when_no_cse_id(self):
        results = search_google("lithium")
        self.assertEqual(results, [])


# ── search() dispatcher ───────────────────────────────────────────────────────

class SearchDispatcherTests(TestCase):

    @override_settings(CRAWLER=BING_SETTINGS)
    def test_dispatches_to_bing(self):
        with patch("discovery.services.search_api.search_bing",
                   return_value=[]) as mock_bing:
            search("gold", provider="bing")
        mock_bing.assert_called_once_with("gold")

    @override_settings(CRAWLER=GOOGLE_SETTINGS)
    def test_dispatches_to_google(self):
        with patch("discovery.services.search_api.search_google",
                   return_value=[]) as mock_google:
            search("gold", provider="google")
        mock_google.assert_called_once_with("gold")

    def test_raises_on_unknown_provider(self):
        with self.assertRaises(ValueError):
            search("gold", provider="yahoo")
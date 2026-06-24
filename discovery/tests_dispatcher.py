"""
Tests for discovery/services/__init__.py  (run_discovery dispatcher)

Each test creates a real SeedSource in the DB and patches the underlying
adapter so no network calls are made.
"""
from unittest.mock import patch

from django.test import TestCase

from discovery.models import SeedSource, SourceType
from discovery.services import run_discovery

# A minimal article dict every mock adapter returns
FAKE_ARTICLE = {
    "url":          "https://example.com/article/1",
    "title":        "Test Article",
    "snippet":      "A snippet.",
    "published_at": None,
    "source_type":  "rss",
}


def make_seed(source_type, url="https://example.com/feed", name=None, **kwargs) -> SeedSource:
    return SeedSource.objects.create(
        name=name or f"Test {source_type}",
        url=url,
        source_type=source_type,
        **kwargs,
    )


class RunDiscoveryDispatcherTests(TestCase):

    # ── RSS ───────────────────────────────────────────────────────────────────

    def test_rss_calls_fetch_feed(self):
        seed = make_seed(SourceType.RSS)
        with patch("discovery.services.fetch_feed", return_value=[FAKE_ARTICLE]) as mock:
            results = run_discovery(seed)
        mock.assert_called_once_with(seed.url)
        self.assertEqual(results, [FAKE_ARTICLE])

    # ── Sitemap ───────────────────────────────────────────────────────────────

    def test_sitemap_calls_fetch_sitemap_with_default_depth(self):
        seed = make_seed(SourceType.SITEMAP, url="https://example.com/sitemap.xml")
        with patch("discovery.services.fetch_sitemap", return_value=[FAKE_ARTICLE]) as mock:
            run_discovery(seed)
        mock.assert_called_once_with(seed.url, max_depth=3)

    def test_sitemap_passes_max_depth_from_meta(self):
        seed = make_seed(SourceType.SITEMAP,
                         url="https://example.com/sitemap.xml",
                         meta={"max_depth": 1})
        with patch("discovery.services.fetch_sitemap", return_value=[]) as mock:
            run_discovery(seed)
        mock.assert_called_once_with(seed.url, max_depth=1)

    # ── Link extraction ───────────────────────────────────────────────────────

    def test_seed_url_calls_extract_links(self):
        seed = make_seed(SourceType.SEED_URL, url="https://example.com/news/")
        with patch("discovery.services.extract_links", return_value=[FAKE_ARTICLE]) as mock:
            results = run_discovery(seed)
        mock.assert_called_once()
        self.assertEqual(results, [FAKE_ARTICLE])

    def test_seed_url_passes_keywords_from_seed(self):
        seed = make_seed(SourceType.SEED_URL,
                         url="https://example.com/news/",
                         keyword_filter="gold, mining")
        with patch("discovery.services.extract_links", return_value=[]) as mock:
            run_discovery(seed)
        _, kwargs = mock.call_args
        self.assertEqual(kwargs["keywords"], ["gold", "mining"])

    def test_seed_url_passes_max_links_from_meta(self):
        seed = make_seed(SourceType.SEED_URL,
                         url="https://example.com/news/",
                         meta={"max_links": 10})
        with patch("discovery.services.extract_links", return_value=[]) as mock:
            run_discovery(seed)
        _, kwargs = mock.call_args
        self.assertEqual(kwargs["max_links"], 10)

    def test_seed_url_passes_allowed_domains_from_meta(self):
        seed = make_seed(SourceType.SEED_URL,
                         url="https://example.com/news/",
                         meta={"allowed_domains": ["example.com", "news.example.com"]})
        with patch("discovery.services.extract_links", return_value=[]) as mock:
            run_discovery(seed)
        _, kwargs = mock.call_args
        self.assertEqual(kwargs["allowed_domains"], ["example.com", "news.example.com"])

    # ── Search API ────────────────────────────────────────────────────────────

    def test_search_api_calls_search_with_bing_default(self):
        seed = make_seed(SourceType.SEARCH_API,
                         url="https://api.bing.microsoft.com/",
                         meta={"query": "gold mining Africa"})
        with patch("discovery.services.search", return_value=[FAKE_ARTICLE]) as mock:
            results = run_discovery(seed)
        mock.assert_called_once_with(
            query="gold mining Africa",
            provider="bing",
            count=20,
        )
        self.assertEqual(results, [FAKE_ARTICLE])

    def test_search_api_uses_google_provider_from_meta(self):
        seed = make_seed(SourceType.SEARCH_API,
                         url="https://googleapis.com/",
                         meta={"provider": "google", "query": "lithium", "count": 5})
        with patch("discovery.services.search", return_value=[]) as mock:
            run_discovery(seed)
        mock.assert_called_once_with(query="lithium", provider="google", count=5)

    def test_search_api_defaults_query_to_keywords(self):
        seed = make_seed(SourceType.SEARCH_API,
                         url="https://api.bing.microsoft.com/",
                         keyword_filter="diamonds, botswana")
        with patch("discovery.services.search", return_value=[]) as mock:
            run_discovery(seed)
        call_kwargs = mock.call_args[1]
        self.assertEqual(call_kwargs["query"], "diamonds botswana")

    def test_search_api_falls_back_to_seed_name_when_no_keywords(self):
        seed = make_seed(SourceType.SEARCH_API,
                         name="My Mining Monitor",
                         url="https://api.bing.microsoft.com/")
        with patch("discovery.services.search", return_value=[]) as mock:
            run_discovery(seed)
        call_kwargs = mock.call_args[1]
        self.assertEqual(call_kwargs["query"], "My Mining Monitor")

    # ── Social (Apify) ────────────────────────────────────────────────────────

    def test_social_calls_fetch_mentions_with_seed_query(self):
        seed = make_seed(SourceType.SOCIAL, url="https://linkedin.x/1",
                         meta={"platform": "linkedin", "query": "Debswana"})
        with patch("discovery.services.fetch_mentions", return_value=[FAKE_ARTICLE]) as mock:
            results = run_discovery(seed)
        mock.assert_called_once_with(platform="linkedin", terms="Debswana",
                                     max_items=None, actor_input=None)
        self.assertEqual(results, [FAKE_ARTICLE])

    def test_social_from_orgs_searches_each_active_org_keyword(self):
        from platform_sync.models import Organization
        org = Organization.objects.create(name="Debswana", status="active")
        org.keywords.create(keyword="Debswana")
        org.keywords.create(keyword="Jwaneng")
        inactive = Organization.objects.create(name="Old Co", status="inactive")
        inactive.keywords.create(keyword="ignore-me")

        seed = make_seed(SourceType.SOCIAL, url="https://linkedin.x/orgs",
                         meta={"platform": "linkedin", "from_orgs": True})
        with patch("discovery.services.fetch_mentions", return_value=[FAKE_ARTICLE]) as mock:
            results = run_discovery(seed)

        # One search per active-org keyword; inactive org keyword excluded.
        called_terms = [c.args[1] for c in mock.call_args_list]
        self.assertCountEqual(called_terms, [["Debswana"], ["Jwaneng"]])
        self.assertEqual(len(results), 2)  # FAKE_ARTICLE per keyword search

    # ── Manual / unknown ──────────────────────────────────────────────────────

    def test_manual_source_type_returns_empty_list(self):
        seed = make_seed(SourceType.MANUAL, url="https://example.com/manual")
        results = run_discovery(seed)
        self.assertEqual(results, [])
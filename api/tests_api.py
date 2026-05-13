"""
api/tests_api.py

Tests for the REST API endpoints:
  GET/POST   /api/seeds/
  GET/PUT/PATCH/DELETE /api/seeds/{id}/
  GET        /api/discovered-urls/
  GET        /api/discovered-urls/{id}/
  GET        /api/articles/
  GET        /api/articles/{id}/
  GET        /api/articles/search/
  GET/POST   /api/extraction-rules/
  GET/PUT/PATCH/DELETE /api/extraction-rules/{id}/

All Elasticsearch calls are patched — no real ES cluster needed.
"""
from unittest.mock import patch

from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from discovery.models import DiscoveredURL, SeedSource
from fetcher.models import ExtractionRule, FetchedPage, ParsedArticle

# ── Fixture helpers ────────────────────────────────────────────────────────────

_counter = 0


def _uid():
    global _counter
    _counter += 1
    return _counter


def make_seed(**kwargs):
    return SeedSource.objects.create(
        name=kwargs.get("name", f"Seed {_uid()}"),
        url=kwargs.get("url", f"https://example.com/feed/{_uid()}"),
        source_type=kwargs.get("source_type", "rss"),
        is_active=kwargs.get("is_active", True),
    )


def make_discovered_url(seed, **kwargs):
    uid = _uid()
    return DiscoveredURL.objects.create(
        seed=seed,
        url=kwargs.get("url", f"https://example.com/article/{uid}"),
        title=kwargs.get("title", f"Article {uid}"),
        status=kwargs.get("status", "pending"),
    )


def make_fetched_page(discovered_url, **kwargs):
    return FetchedPage.objects.create(
        discovered_url=discovered_url,
        status_code=kwargs.get("status_code", 200),
        raw_html=kwargs.get("raw_html", "<html><body>Test</body></html>"),
        fetch_duration_ms=kwargs.get("fetch_duration_ms", 100),
    )


def make_article(fetched_page, **kwargs):
    return ParsedArticle.objects.create(
        fetched_page=fetched_page,
        title=kwargs.get("title", f"Article {_uid()}"),
        body_text=kwargs.get("body_text", "Body text here."),
        source_domain=kwargs.get("source_domain", "example.com"),
        language=kwargs.get("language", "en"),
        is_duplicate=kwargs.get("is_duplicate", False),
        signals=kwargs.get("signals", {}),
    )


def make_rule(seed, **kwargs):
    return ExtractionRule.objects.create(
        seed=seed,
        domain=kwargs.get("domain", f"site{_uid()}.com"),
        title_selector=kwargs.get("title_selector", ""),
        body_selector=kwargs.get("body_selector", ""),
        is_active=kwargs.get("is_active", True),
    )


def _article_fixture():
    """Return a (seed, discovered_url, fetched_page, article) tuple."""
    seed = make_seed()
    durl = make_discovered_url(seed)
    page = make_fetched_page(durl)
    article = make_article(page)
    return seed, durl, page, article


# ── SeedSource ────────────────────────────────────────────────────────────────

class SeedSourceListTests(APITestCase):
    def test_list_returns_seeds(self):
        make_seed(name="Alpha")
        make_seed(name="Beta")
        response = self.client.get("/api/seeds/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        names = [s["name"] for s in response.data["results"]]
        self.assertIn("Alpha", names)
        self.assertIn("Beta", names)

    def test_list_filter_is_active_true(self):
        make_seed(name="Active", is_active=True)
        make_seed(name="Inactive", is_active=False)
        response = self.client.get("/api/seeds/?is_active=true")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        names = [s["name"] for s in response.data["results"]]
        self.assertIn("Active", names)
        self.assertNotIn("Inactive", names)

    def test_list_filter_is_active_false(self):
        make_seed(name="Active2", is_active=True)
        make_seed(name="Inactive2", is_active=False)
        response = self.client.get("/api/seeds/?is_active=false")
        names = [s["name"] for s in response.data["results"]]
        self.assertNotIn("Active2", names)
        self.assertIn("Inactive2", names)

    def test_create_seed(self):
        payload = {
            "name": "New Seed",
            "url": "https://newseed.com/feed",
            "source_type": "rss",
            "is_active": True,
            "crawl_interval": 60,
            "keyword_filter": "",
            "use_playwright": False,
            "meta": {},
        }
        response = self.client.post("/api/seeds/", payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["name"], "New Seed")
        self.assertEqual(response.data["url"], "https://newseed.com/feed")

    def test_create_seed_returns_source_type_display(self):
        payload = {
            "name": "Display Test",
            "url": "https://display.com/feed",
            "source_type": "rss",
            "crawl_interval": 30,
            "keyword_filter": "",
            "use_playwright": False,
            "meta": {},
        }
        response = self.client.post("/api/seeds/", payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["source_type_display"], "RSS / Atom Feed")

    def test_create_seed_missing_required_field_returns_400(self):
        response = self.client.post("/api/seeds/", {"name": "No URL"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


class SeedSourceDetailTests(APITestCase):
    def setUp(self):
        self.seed = make_seed(name="Detail Seed", url="https://detail.com/feed")

    def test_retrieve_seed(self):
        response = self.client.get(f"/api/seeds/{self.seed.pk}/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["name"], "Detail Seed")

    def test_update_seed(self):
        payload = {
            "name": "Updated",
            "url": "https://detail.com/feed",
            "source_type": "rss",
            "is_active": False,
            "crawl_interval": 120,
            "keyword_filter": "",
            "use_playwright": False,
            "meta": {},
        }
        response = self.client.put(f"/api/seeds/{self.seed.pk}/", payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["name"], "Updated")
        self.assertFalse(response.data["is_active"])

    def test_partial_update_seed(self):
        response = self.client.patch(
            f"/api/seeds/{self.seed.pk}/",
            {"crawl_interval": 999},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["crawl_interval"], 999)

    def test_delete_seed(self):
        response = self.client.delete(f"/api/seeds/{self.seed.pk}/")
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(SeedSource.objects.filter(pk=self.seed.pk).exists())

    def test_retrieve_nonexistent_seed_returns_404(self):
        response = self.client.get("/api/seeds/99999/")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)


# ── DiscoveredURL ─────────────────────────────────────────────────────────────

class DiscoveredURLListTests(APITestCase):
    def setUp(self):
        self.seed_a = make_seed(name="Seed A")
        self.seed_b = make_seed(name="Seed B")
        self.url_a = make_discovered_url(self.seed_a, status="pending")
        self.url_b = make_discovered_url(self.seed_b, status="parsed")

    def test_list_returns_discovered_urls(self):
        response = self.client.get("/api/discovered-urls/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertGreaterEqual(response.data["count"], 2)

    def test_filter_by_seed(self):
        response = self.client.get(f"/api/discovered-urls/?seed={self.seed_a.pk}")
        ids = [r["id"] for r in response.data["results"]]
        self.assertIn(self.url_a.pk, ids)
        self.assertNotIn(self.url_b.pk, ids)

    def test_filter_by_status(self):
        response = self.client.get("/api/discovered-urls/?status=parsed")
        statuses = [r["status"] for r in response.data["results"]]
        self.assertTrue(all(s == "parsed" for s in statuses))

    def test_post_not_allowed(self):
        response = self.client.post("/api/discovered-urls/", {}, format="json")
        self.assertEqual(response.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)

    def test_response_includes_seed_name(self):
        response = self.client.get(f"/api/discovered-urls/?seed={self.seed_a.pk}")
        self.assertEqual(response.data["results"][0]["seed_name"], "Seed A")


class DiscoveredURLDetailTests(APITestCase):
    def setUp(self):
        seed = make_seed()
        self.durl = make_discovered_url(seed, title="My URL")

    def test_retrieve_discovered_url(self):
        response = self.client.get(f"/api/discovered-urls/{self.durl.pk}/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["title"], "My URL")

    def test_put_not_allowed(self):
        response = self.client.put(f"/api/discovered-urls/{self.durl.pk}/", {})
        self.assertEqual(response.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)

    def test_delete_not_allowed(self):
        response = self.client.delete(f"/api/discovered-urls/{self.durl.pk}/")
        self.assertEqual(response.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)


# ── ParsedArticle ─────────────────────────────────────────────────────────────

class ParsedArticleListTests(APITestCase):
    def setUp(self):
        _, _, _, self.article_en = _article_fixture()
        self.article_en.language = "en"
        self.article_en.source_domain = "mining.com"
        self.article_en.save()

        _, _, _, self.article_fr = _article_fixture()
        self.article_fr.language = "fr"
        self.article_fr.source_domain = "presse.fr"
        self.article_fr.save()

        _, _, _, self.dup = _article_fixture()
        self.dup.is_duplicate = True
        self.dup.save()

    def test_list_returns_articles(self):
        response = self.client.get("/api/articles/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertGreaterEqual(response.data["count"], 2)

    def test_filter_by_source_domain(self):
        response = self.client.get("/api/articles/?source_domain=mining.com")
        domains = [r["source_domain"] for r in response.data["results"]]
        self.assertTrue(all(d == "mining.com" for d in domains))

    def test_filter_by_language(self):
        response = self.client.get("/api/articles/?language=fr")
        languages = [r["language"] for r in response.data["results"]]
        self.assertTrue(all(lang == "fr" for lang in languages))

    def test_filter_by_is_duplicate_true(self):
        response = self.client.get("/api/articles/?is_duplicate=true")
        is_dups = [r["is_duplicate"] for r in response.data["results"]]
        self.assertTrue(all(is_dups))

    def test_filter_by_is_duplicate_false(self):
        response = self.client.get("/api/articles/?is_duplicate=false")
        is_dups = [r["is_duplicate"] for r in response.data["results"]]
        self.assertFalse(any(is_dups))

    def test_post_not_allowed(self):
        response = self.client.post("/api/articles/", {}, format="json")
        self.assertEqual(response.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)

    def test_response_includes_url_property(self):
        response = self.client.get("/api/articles/")
        for item in response.data["results"]:
            self.assertIn("url", item)
            self.assertTrue(item["url"].startswith("http"))

    def test_response_includes_word_count(self):
        response = self.client.get("/api/articles/")
        for item in response.data["results"]:
            self.assertIn("word_count", item)
            self.assertIsInstance(item["word_count"], int)


class ParsedArticleDetailTests(APITestCase):
    def setUp(self):
        _, _, _, self.article = _article_fixture()
        self.article.title = "Detail Article"
        self.article.save()

    def test_retrieve_article(self):
        response = self.client.get(f"/api/articles/{self.article.pk}/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["title"], "Detail Article")

    def test_retrieve_includes_body_text(self):
        response = self.client.get(f"/api/articles/{self.article.pk}/")
        self.assertIn("body_text", response.data)

    def test_put_not_allowed(self):
        response = self.client.put(f"/api/articles/{self.article.pk}/", {})
        self.assertEqual(response.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)

    def test_delete_not_allowed(self):
        response = self.client.delete(f"/api/articles/{self.article.pk}/")
        self.assertEqual(response.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)


# ── Article search ─────────────────────────────────────────────────────────────

FAKE_HIT = {
    "_id": "42",
    "_score": 1.5,
    "_source": {
        "title": "Gold Mining Report",
        "body_text": "Gold prices rose.",
        "summary": "",
        "author": "",
        "source_domain": "mining.com",
        "language": "en",
        "published_at": None,
        "tags": [],
        "sentiment": "positive",
        "url": "https://mining.com/report",
        "is_duplicate": False,
        "created_at": "2025-01-01T00:00:00",
    },
}


class ArticleSearchTests(APITestCase):
    def _search(self, **params):
        return self.client.get("/api/articles/search/", params)

    @patch("api.views.search_articles", return_value=[FAKE_HIT])
    def test_search_returns_results(self, mock_sa):
        response = self._search(q="gold")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(response.data["results"][0]["title"], "Gold Mining Report")

    @patch("api.views.search_articles", return_value=[FAKE_HIT])
    def test_search_passes_query_to_es(self, mock_sa):
        self._search(q="gold mining")
        mock_sa.assert_called_once()
        args, kwargs = mock_sa.call_args
        self.assertEqual(args[0], "gold mining")

    @patch("api.views.search_articles", return_value=[])
    def test_search_passes_optional_filters(self, mock_sa):
        self._search(q="test", source_domain="mining.com", language="en", sentiment="positive")
        _, kwargs = mock_sa.call_args
        self.assertEqual(kwargs["source_domain"], "mining.com")
        self.assertEqual(kwargs["language"], "en")
        self.assertEqual(kwargs["sentiment"], "positive")

    @patch("api.views.search_articles", return_value=[])
    def test_search_empty_query_passes_empty_string(self, mock_sa):
        self._search()
        args, _ = mock_sa.call_args
        self.assertEqual(args[0], "")

    @patch("api.views.search_articles", return_value=[])
    def test_search_passes_from_and_size(self, mock_sa):
        self._search(q="x", **{"from_": 20, "size": 5})
        _, kwargs = mock_sa.call_args
        self.assertEqual(kwargs["from_"], 20)
        self.assertEqual(kwargs["size"], 5)

    @patch("api.views.search_articles", return_value=[])
    def test_search_size_capped_at_100(self, mock_sa):
        self._search(q="x", size=9999)
        _, kwargs = mock_sa.call_args
        self.assertLessEqual(kwargs["size"], 100)

    @patch("api.views.search_articles", return_value=[])
    def test_search_bad_size_defaults_gracefully(self, mock_sa):
        response = self._search(q="x", size="notanumber")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        _, kwargs = mock_sa.call_args
        self.assertEqual(kwargs["size"], 10)

    @patch("api.views.search_articles", return_value=[FAKE_HIT])
    def test_search_result_includes_id_and_score(self, mock_sa):
        response = self._search(q="gold")
        result = response.data["results"][0]
        self.assertIn("id", result)
        self.assertIn("score", result)
        self.assertEqual(result["id"], "42")
        self.assertAlmostEqual(result["score"], 1.5)

    @patch("api.views.search_articles", return_value=[])
    def test_search_returns_empty_list_when_no_hits(self, mock_sa):
        response = self._search(q="nomatch")
        self.assertEqual(response.data["count"], 0)
        self.assertEqual(response.data["results"], [])


# ── ExtractionRule ────────────────────────────────────────────────────────────

class ExtractionRuleListCreateTests(APITestCase):
    def setUp(self):
        self.seed = make_seed()
        self.rule = make_rule(self.seed, domain="alpha.com")

    def test_list_returns_rules(self):
        response = self.client.get("/api/extraction-rules/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        domains = [r["domain"] for r in response.data["results"]]
        self.assertIn("alpha.com", domains)

    def test_filter_by_seed(self):
        other_seed = make_seed()
        make_rule(other_seed, domain="other.com")
        response = self.client.get(f"/api/extraction-rules/?seed={self.seed.pk}")
        domains = [r["domain"] for r in response.data["results"]]
        self.assertIn("alpha.com", domains)
        self.assertNotIn("other.com", domains)

    def test_filter_by_domain(self):
        make_rule(self.seed, domain="beta.com")
        response = self.client.get("/api/extraction-rules/?domain=alpha.com")
        domains = [r["domain"] for r in response.data["results"]]
        self.assertIn("alpha.com", domains)
        self.assertNotIn("beta.com", domains)

    def test_filter_by_is_active(self):
        make_rule(self.seed, domain="inactive.com", is_active=False)
        response = self.client.get("/api/extraction-rules/?is_active=false")
        domains = [r["domain"] for r in response.data["results"]]
        self.assertIn("inactive.com", domains)
        self.assertNotIn("alpha.com", domains)

    def test_create_rule(self):
        payload = {
            "seed": self.seed.pk,
            "domain": "newsite.com",
            "title_selector": "h1.title",
            "body_selector": "div.content",
            "author_selector": "",
            "date_selector": "",
            "date_format": "",
            "is_active": True,
        }
        response = self.client.post("/api/extraction-rules/", payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["domain"], "newsite.com")
        self.assertEqual(response.data["title_selector"], "h1.title")

    def test_create_rule_missing_domain_returns_400(self):
        response = self.client.post(
            "/api/extraction-rules/",
            {"seed": self.seed.pk},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


class ExtractionRuleDetailTests(APITestCase):
    def setUp(self):
        self.seed = make_seed()
        self.rule = make_rule(self.seed, domain="detail.com")

    def test_retrieve_rule(self):
        response = self.client.get(f"/api/extraction-rules/{self.rule.pk}/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["domain"], "detail.com")

    def test_partial_update_rule(self):
        response = self.client.patch(
            f"/api/extraction-rules/{self.rule.pk}/",
            {"title_selector": "h2.headline"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["title_selector"], "h2.headline")

    def test_update_rule(self):
        payload = {
            "seed": self.seed.pk,
            "domain": "updated.com",
            "title_selector": "",
            "body_selector": "article",
            "author_selector": "",
            "date_selector": "",
            "date_format": "",
            "is_active": False,
        }
        response = self.client.put(
            f"/api/extraction-rules/{self.rule.pk}/", payload, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["domain"], "updated.com")
        self.assertFalse(response.data["is_active"])

    def test_delete_rule(self):
        response = self.client.delete(f"/api/extraction-rules/{self.rule.pk}/")
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(ExtractionRule.objects.filter(pk=self.rule.pk).exists())

    def test_retrieve_nonexistent_rule_returns_404(self):
        response = self.client.get("/api/extraction-rules/99999/")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

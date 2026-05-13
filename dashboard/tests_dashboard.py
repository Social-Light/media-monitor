"""
dashboard/tests_dashboard.py

Tests for all dashboard views:
  /dashboard/                    — overview
  /dashboard/seeds/              — seed list
  /dashboard/seeds/create/       — create seed
  /dashboard/seeds/<pk>/edit/    — edit seed
  /dashboard/seeds/<pk>/delete/  — delete seed
  /dashboard/articles/           — article list (with HTMX partial)
  /dashboard/articles/<pk>/      — article detail

HTMX requests are simulated by passing HTTP_HX_REQUEST="true".
"""
from django.test import TestCase
from django.urls import reverse

from discovery.models import SeedSource
from fetcher.models import FetchedPage, ParsedArticle

# ── Fixture helpers ────────────────────────────────────────────────────────────

_counter = 0


def _uid():
    global _counter
    _counter += 1
    return _counter


def make_seed(**kwargs):
    uid = _uid()
    return SeedSource.objects.create(
        name=kwargs.get("name", f"Seed {uid}"),
        url=kwargs.get("url", f"https://example.com/feed/{uid}"),
        source_type=kwargs.get("source_type", "rss"),
        is_active=kwargs.get("is_active", True),
        crawl_interval=kwargs.get("crawl_interval", 60),
    )


def make_article(seed, **kwargs):
    from discovery.models import DiscoveredURL
    uid = _uid()
    durl = DiscoveredURL.objects.create(
        seed=seed,
        url=kwargs.get("url", f"https://example.com/article/{uid}"),
        status="parsed",
    )
    page = FetchedPage.objects.create(
        discovered_url=durl,
        status_code=200,
        raw_html="<html><body>Test</body></html>",
    )
    return ParsedArticle.objects.create(
        fetched_page=page,
        title=kwargs.get("title", f"Article {uid}"),
        body_text=kwargs.get("body_text", "Some body text here for testing."),
        summary=kwargs.get("summary", "A summary."),
        source_domain=kwargs.get("source_domain", "example.com"),
        language=kwargs.get("language", "en"),
        is_duplicate=kwargs.get("is_duplicate", False),
        signals=kwargs.get("signals", {}),
    )


# ── Overview ──────────────────────────────────────────────────────────────────

class OverviewTests(TestCase):
    def test_overview_renders(self):
        response = self.client.get("/dashboard/")
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "dashboard/overview.html")

    def test_overview_uses_named_url(self):
        url = reverse("dashboard:overview")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_overview_shows_seed_counts(self):
        make_seed(is_active=True)
        make_seed(is_active=False)
        response = self.client.get("/dashboard/")
        self.assertEqual(response.context["total_seeds"], 2)
        self.assertEqual(response.context["active_seeds"], 1)

    def test_overview_shows_article_counts(self):
        seed = make_seed()
        make_article(seed)
        make_article(seed, is_duplicate=True)
        response = self.client.get("/dashboard/")
        self.assertEqual(response.context["total_articles"], 2)
        self.assertEqual(response.context["unique_articles"], 1)

    def test_overview_shows_recent_articles(self):
        seed = make_seed()
        make_article(seed, title="Recent Article")
        response = self.client.get("/dashboard/")
        titles = [a.title for a in response.context["recent_articles"]]
        self.assertIn("Recent Article", titles)

    def test_overview_recent_articles_excludes_duplicates(self):
        seed = make_seed()
        make_article(seed, title="Original")
        make_article(seed, title="Dupe", is_duplicate=True)
        response = self.client.get("/dashboard/")
        titles = [a.title for a in response.context["recent_articles"]]
        self.assertIn("Original", titles)
        self.assertNotIn("Dupe", titles)

    def test_overview_url_by_status_in_context(self):
        response = self.client.get("/dashboard/")
        self.assertIn("url_by_status", response.context)


# ── Seed list ─────────────────────────────────────────────────────────────────

class SeedListTests(TestCase):
    def test_list_renders(self):
        response = self.client.get("/dashboard/seeds/")
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "dashboard/seeds/list.html")

    def test_list_shows_seeds(self):
        make_seed(name="Alpha")
        make_seed(name="Beta")
        response = self.client.get("/dashboard/seeds/")
        self.assertContains(response, "Alpha")
        self.assertContains(response, "Beta")

    def test_list_seeds_ordered_by_name(self):
        make_seed(name="Zebra")
        make_seed(name="Apple")
        response = self.client.get("/dashboard/seeds/")
        seeds = list(response.context["seeds"])
        names = [s.name for s in seeds]
        self.assertEqual(names, sorted(names))

    def test_list_includes_article_count(self):
        seed = make_seed()
        make_article(seed)
        make_article(seed)
        response = self.client.get("/dashboard/seeds/")
        seed_in_ctx = next(s for s in response.context["seeds"] if s.pk == seed.pk)
        self.assertEqual(seed_in_ctx.article_count, 2)

    def test_list_shows_add_seed_link(self):
        response = self.client.get("/dashboard/seeds/")
        self.assertContains(response, reverse("dashboard:seed-create"))

    def test_empty_list_shows_placeholder(self):
        response = self.client.get("/dashboard/seeds/")
        self.assertContains(response, "No seeds yet")


# ── Seed create ───────────────────────────────────────────────────────────────

class SeedCreateTests(TestCase):
    def test_get_renders_form(self):
        response = self.client.get("/dashboard/seeds/create/")
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "dashboard/seeds/form.html")
        self.assertIn("form", response.context)

    def test_get_shows_create_action(self):
        response = self.client.get("/dashboard/seeds/create/")
        self.assertEqual(response.context["action"], "Create")

    def test_post_creates_seed_and_redirects(self):
        response = self.client.post("/dashboard/seeds/create/", {
            "name": "New Seed",
            "url": "https://newsite.com/feed",
            "source_type": "rss",
            "is_active": "on",
            "crawl_interval": "60",
            "keyword_filter": "",
            "use_playwright": "",
        })
        self.assertRedirects(response, reverse("dashboard:seed-list"))
        self.assertTrue(SeedSource.objects.filter(name="New Seed").exists())

    def test_post_invalid_rerenders_form(self):
        response = self.client.post("/dashboard/seeds/create/", {"name": ""})
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "dashboard/seeds/form.html")
        self.assertFalse(response.context["form"].is_valid())

    def test_post_duplicate_url_rerenders_form(self):
        make_seed(url="https://taken.com/feed")
        response = self.client.post("/dashboard/seeds/create/", {
            "name": "Another",
            "url": "https://taken.com/feed",
            "source_type": "rss",
            "crawl_interval": "60",
        })
        self.assertEqual(response.status_code, 200)
        self.assertFalse(SeedSource.objects.filter(name="Another").exists())


# ── Seed edit ─────────────────────────────────────────────────────────────────

class SeedEditTests(TestCase):
    def setUp(self):
        self.seed = make_seed(name="Original", url="https://orig.com/feed")

    def test_get_renders_form_with_instance(self):
        response = self.client.get(f"/dashboard/seeds/{self.seed.pk}/edit/")
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "dashboard/seeds/form.html")
        self.assertEqual(response.context["action"], "Edit")
        self.assertEqual(response.context["form"].instance.pk, self.seed.pk)

    def test_get_nonexistent_returns_404(self):
        response = self.client.get("/dashboard/seeds/99999/edit/")
        self.assertEqual(response.status_code, 404)

    def test_post_updates_seed_and_redirects(self):
        response = self.client.post(f"/dashboard/seeds/{self.seed.pk}/edit/", {
            "name": "Updated Name",
            "url": "https://orig.com/feed",
            "source_type": "rss",
            "is_active": "",
            "crawl_interval": "120",
            "keyword_filter": "gold",
            "use_playwright": "",
        })
        self.assertRedirects(response, reverse("dashboard:seed-list"))
        self.seed.refresh_from_db()
        self.assertEqual(self.seed.name, "Updated Name")
        self.assertEqual(self.seed.crawl_interval, 120)

    def test_post_invalid_rerenders_form(self):
        response = self.client.post(f"/dashboard/seeds/{self.seed.pk}/edit/", {
            "name": "",
        })
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["form"].is_valid())


# ── Seed delete ───────────────────────────────────────────────────────────────

class SeedDeleteTests(TestCase):
    def setUp(self):
        self.seed = make_seed(name="To Delete")

    def test_get_renders_confirmation(self):
        response = self.client.get(f"/dashboard/seeds/{self.seed.pk}/delete/")
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "dashboard/seeds/confirm_delete.html")
        self.assertContains(response, "To Delete")

    def test_get_nonexistent_returns_404(self):
        response = self.client.get("/dashboard/seeds/99999/delete/")
        self.assertEqual(response.status_code, 404)

    def test_post_deletes_seed_and_redirects(self):
        pk = self.seed.pk
        response = self.client.post(f"/dashboard/seeds/{pk}/delete/")
        self.assertRedirects(response, reverse("dashboard:seed-list"))
        self.assertFalse(SeedSource.objects.filter(pk=pk).exists())


# ── Article list ──────────────────────────────────────────────────────────────

class ArticleListTests(TestCase):
    def setUp(self):
        self.seed = make_seed()
        self.art_en = make_article(
            self.seed, title="Gold Mining Update",
            source_domain="mining.com", language="en",
        )
        self.art_fr = make_article(
            self.seed, title="Actualités minières",
            source_domain="presse.fr", language="fr",
        )
        self.art_dup = make_article(
            self.seed, title="Duplicate Article",
            source_domain="mining.com", is_duplicate=True,
        )

    def test_list_renders(self):
        response = self.client.get("/dashboard/articles/")
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "dashboard/articles/list.html")
        self.assertTemplateUsed(response, "dashboard/articles/_table.html")

    def test_list_excludes_duplicates_by_default(self):
        response = self.client.get("/dashboard/articles/")
        titles = [a.title for a in response.context["articles"]]
        self.assertIn("Gold Mining Update", titles)
        self.assertNotIn("Duplicate Article", titles)

    def test_show_duplicates_includes_duplicates(self):
        response = self.client.get("/dashboard/articles/?show_duplicates=1")
        titles = [a.title for a in response.context["articles"]]
        self.assertIn("Duplicate Article", titles)

    def test_filter_by_source_domain(self):
        response = self.client.get("/dashboard/articles/?source_domain=mining.com")
        domains = [a.source_domain for a in response.context["articles"]]
        self.assertTrue(all(d == "mining.com" for d in domains))

    def test_filter_by_language(self):
        response = self.client.get("/dashboard/articles/?language=fr")
        langs = [a.language for a in response.context["articles"]]
        self.assertTrue(all(l == "fr" for l in langs))

    def test_filter_by_title_query(self):
        response = self.client.get("/dashboard/articles/?q=Gold")
        titles = [a.title for a in response.context["articles"]]
        self.assertIn("Gold Mining Update", titles)
        self.assertNotIn("Actualités minières", titles)

    def test_filter_by_summary_query(self):
        make_article(self.seed, title="Unrelated", summary="Contains copper info")
        response = self.client.get("/dashboard/articles/?q=copper")
        titles = [a.title for a in response.context["articles"]]
        self.assertIn("Unrelated", titles)

    def test_htmx_request_returns_table_partial_only(self):
        response = self.client.get(
            "/dashboard/articles/",
            HTTP_HX_REQUEST="true",
        )
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "dashboard/articles/_table.html")
        self.assertTemplateNotUsed(response, "dashboard/articles/list.html")

    def test_htmx_partial_respects_filters(self):
        response = self.client.get(
            "/dashboard/articles/?source_domain=presse.fr",
            HTTP_HX_REQUEST="true",
        )
        domains = [a.source_domain for a in response.context["articles"]]
        self.assertTrue(all(d == "presse.fr" for d in domains))

    def test_context_contains_domains_and_languages(self):
        response = self.client.get("/dashboard/articles/")
        self.assertIn("domains", response.context)
        self.assertIn("languages", response.context)

    def test_domains_dropdown_populated(self):
        response = self.client.get("/dashboard/articles/")
        self.assertIn("mining.com", list(response.context["domains"]))


# ── Article detail ────────────────────────────────────────────────────────────

class ArticleDetailTests(TestCase):
    def setUp(self):
        self.seed = make_seed()
        self.article = make_article(
            self.seed,
            title="Detailed Article",
            body_text="Full body text of the article.",
            summary="Short summary.",
            signals={"sentiment": "positive", "entities": {"ORG": ["Acme Corp"]}},
        )

    def test_detail_renders(self):
        response = self.client.get(f"/dashboard/articles/{self.article.pk}/")
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "dashboard/articles/detail.html")

    def test_detail_shows_title(self):
        response = self.client.get(f"/dashboard/articles/{self.article.pk}/")
        self.assertContains(response, "Detailed Article")

    def test_detail_shows_body_text(self):
        response = self.client.get(f"/dashboard/articles/{self.article.pk}/")
        self.assertContains(response, "Full body text")

    def test_detail_shows_summary(self):
        response = self.client.get(f"/dashboard/articles/{self.article.pk}/")
        self.assertContains(response, "Short summary")

    def test_detail_shows_sentiment(self):
        response = self.client.get(f"/dashboard/articles/{self.article.pk}/")
        self.assertContains(response, "positive")

    def test_detail_shows_entities(self):
        response = self.client.get(f"/dashboard/articles/{self.article.pk}/")
        self.assertContains(response, "Acme Corp")

    def test_detail_shows_source_url(self):
        response = self.client.get(f"/dashboard/articles/{self.article.pk}/")
        self.assertContains(response, self.article.url)

    def test_detail_nonexistent_returns_404(self):
        response = self.client.get("/dashboard/articles/99999/")
        self.assertEqual(response.status_code, 404)

    def test_article_in_context(self):
        response = self.client.get(f"/dashboard/articles/{self.article.pk}/")
        self.assertEqual(response.context["article"].pk, self.article.pk)

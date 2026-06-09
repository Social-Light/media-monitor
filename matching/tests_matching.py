"""
matching/tests_matching.py

Tests for:
  Organisation.save() slug auto-generation
  match_article()
  fetcher.services.parse_page() organisation-matching integration
"""
from unittest.mock import MagicMock, patch

from django.test import TestCase

import fetcher.services  # imported before any patch("fetcher.services.*") calls

from discovery.models import DiscoveredURL, SeedSource
from fetcher.models import FetchedPage, ParsedArticle
from matching.matcher import CONFIDENCE_BODY, CONFIDENCE_TITLE, match_article
from matching.models import (
    ArticleMatch,
    Organisation,
    OrganisationKeyword,
    OrganisationSource,
)

# ── Fixture helpers ────────────────────────────────────────────────────────────

_counter = 0


def _uid():
    global _counter
    _counter += 1
    return _counter


def make_seed():
    uid = _uid()
    return SeedSource.objects.create(
        name=f"Seed {uid}",
        url=f"https://example.com/feed/{uid}",
        source_type="rss",
    )


def make_article(seed=None, **kwargs):
    if seed is None:
        seed = make_seed()
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
        title=kwargs.get("title", "Debswana Diamond Report"),
        body_text=kwargs.get("body_text", "Diamond output rose at the mine this quarter."),
        source_domain=kwargs.get("source_domain", "mining.com"),
        language=kwargs.get("language", "en"),
        is_duplicate=kwargs.get("is_duplicate", False),
        signals=kwargs.get("signals", {}),
    )


def make_org(name=None, keywords=(), is_active=True, active_keywords=True):
    org = Organisation.objects.create(
        name=name or f"Org {_uid()}",
        is_active=is_active,
    )
    for kw in keywords:
        OrganisationKeyword.objects.create(
            organisation=org, keyword=kw, is_active=active_keywords,
        )
    return org


# ── Organisation model ──────────────────────────────────────────────────────────

class OrganisationModelTests(TestCase):
    def test_slug_auto_generated_from_name(self):
        org = Organisation.objects.create(name="Debswana Diamond Company")
        self.assertEqual(org.slug, "debswana-diamond-company")

    def test_explicit_slug_is_preserved(self):
        org = Organisation.objects.create(name="Debswana", slug="custom-slug")
        self.assertEqual(org.slug, "custom-slug")

    def test_slug_is_unique(self):
        Organisation.objects.create(name="Acme")
        from django.db import IntegrityError
        with self.assertRaises(IntegrityError):
            Organisation.objects.create(name="Acme Two", slug="acme")

    def test_keyword_unique_per_organisation(self):
        org = make_org(keywords=["gold"])
        from django.db import IntegrityError
        with self.assertRaises(IntegrityError):
            OrganisationKeyword.objects.create(organisation=org, keyword="gold")

    def test_source_str(self):
        org = make_org(name="Acme")
        src = OrganisationSource.objects.create(organisation=org, domain="acme.com")
        self.assertEqual(str(src), "Acme: acme.com")


# ── match_article() ──────────────────────────────────────────────────────────────

class MatchArticleTests(TestCase):
    def test_matches_keyword_in_title(self):
        org = make_org(name="Debswana", keywords=["Debswana"])
        article = make_article(title="Debswana posts record output", body_text="Unrelated body.")
        matches = match_article(article)
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].organisation, org)
        self.assertEqual(matches[0].matched_in, ArticleMatch.MatchedIn.TITLE)
        self.assertEqual(matches[0].confidence, CONFIDENCE_TITLE)

    def test_matches_keyword_in_body(self):
        make_org(name="Debswana", keywords=["Debswana"])
        article = make_article(title="Mining report", body_text="Debswana expanded the pit.")
        matches = match_article(article)
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].matched_in, ArticleMatch.MatchedIn.BODY)
        self.assertEqual(matches[0].confidence, CONFIDENCE_BODY)

    def test_title_takes_precedence_over_body(self):
        make_org(name="Debswana", keywords=["Debswana"])
        article = make_article(title="Debswana news", body_text="Debswana again.")
        matches = match_article(article)
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].matched_in, ArticleMatch.MatchedIn.TITLE)

    def test_matching_is_case_insensitive(self):
        make_org(name="Debswana", keywords=["DEBSWANA"])
        article = make_article(title="debswana lowercase", body_text="x")
        self.assertEqual(len(match_article(article)), 1)

    def test_no_match_creates_no_record(self):
        make_org(name="Debswana", keywords=["copper"])
        article = make_article(title="Gold report", body_text="No relevant terms.")
        self.assertEqual(match_article(article), [])
        self.assertEqual(ArticleMatch.objects.count(), 0)

    def test_inactive_organisation_skipped(self):
        make_org(name="Debswana", keywords=["Debswana"], is_active=False)
        article = make_article(title="Debswana report")
        self.assertEqual(match_article(article), [])

    def test_inactive_keyword_skipped(self):
        make_org(name="Debswana", keywords=["Debswana"], active_keywords=False)
        article = make_article(title="Debswana report")
        self.assertEqual(match_article(article), [])

    def test_multiple_organisations_each_match(self):
        make_org(name="Debswana", keywords=["Debswana"])
        make_org(name="BCL", keywords=["copper"])
        article = make_article(title="Debswana and copper", body_text="x")
        matches = match_article(article)
        self.assertEqual(len(matches), 2)

    def test_multiple_keywords_one_org_distinct_records(self):
        make_org(name="Debswana", keywords=["Debswana", "diamond"])
        article = make_article(title="Debswana diamond output", body_text="x")
        matches = match_article(article)
        self.assertEqual(len(matches), 2)
        self.assertEqual({m.matched_keyword for m in matches}, {"Debswana", "diamond"})

    def test_idempotent_no_duplicate_records(self):
        make_org(name="Debswana", keywords=["Debswana"])
        article = make_article(title="Debswana report")
        match_article(article)
        second = match_article(article)
        self.assertEqual(second, [])
        self.assertEqual(ArticleMatch.objects.count(), 1)

    def test_empty_article_returns_empty(self):
        make_org(name="Debswana", keywords=["Debswana"])
        article = make_article(title="", body_text="")
        self.assertEqual(match_article(article), [])

    def test_blank_keyword_ignored(self):
        make_org(name="Debswana", keywords=["   "])
        article = make_article(title="Debswana report")
        self.assertEqual(match_article(article), [])


# ── parse_page() integration ─────────────────────────────────────────────────────

class ParsePageMatchingIntegrationTests(TestCase):
    """parse_page() should run organisation matching after a successful parse."""

    def _run_parse(self, title="Debswana Rush", body="Debswana confirmed reserves.",
                   is_duplicate=False):
        import newspaper
        seed = make_seed()
        uid = _uid()
        durl = DiscoveredURL.objects.create(
            seed=seed,
            url=f"https://example.com/article/{uid}",
            status="fetched",
        )
        page = FetchedPage.objects.create(
            discovered_url=durl,
            status_code=200,
            raw_html=f"<html><body>{body}</body></html>",
        )

        mock_article = MagicMock(spec=newspaper.Article)
        mock_article.title = title
        mock_article.text = body
        mock_article.authors = []
        mock_article.publish_date = None
        mock_article.meta_lang = "en"
        mock_article.meta_keywords = ""
        mock_article.meta_description = ""
        mock_article.tags = set()

        with patch("fetcher.services.newspaper.Article", return_value=mock_article), \
             patch("fetcher.services.get_rule_for_page", return_value=None), \
             patch("fetcher.services.run_nlp", return_value={}), \
             patch("fetcher.services.check_and_mark_duplicate",
                   side_effect=lambda a: setattr(a, "is_duplicate", is_duplicate)), \
             patch("fetcher.services.index_article"):
            from fetcher.services import parse_page
            return parse_page(page)

    def test_matching_organisation_records_match_after_parse(self):
        org = make_org(name="Debswana", keywords=["Debswana"])
        article = self._run_parse(title="Debswana Rush")
        self.assertTrue(
            ArticleMatch.objects.filter(parsed_article=article, organisation=org).exists()
        )

    def test_non_matching_organisation_records_nothing(self):
        make_org(name="BCL", keywords=["copper"])
        self._run_parse(title="Debswana Rush", body="No relevant terms here.")
        self.assertEqual(ArticleMatch.objects.count(), 0)

    def test_duplicate_article_is_not_matched(self):
        make_org(name="Debswana", keywords=["Debswana"])
        self._run_parse(title="Debswana Rush", is_duplicate=True)
        self.assertEqual(ArticleMatch.objects.count(), 0)

    def test_parse_succeeds_when_match_article_raises(self):
        make_org(name="Debswana", keywords=["Debswana"])
        with patch("fetcher.services.match_article", side_effect=Exception("DB error")):
            result = self._run_parse(title="Debswana Rush")
        self.assertIsNotNone(result)
        self.assertEqual(result.title, "Debswana Rush")

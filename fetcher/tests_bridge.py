"""
fetcher/tests_bridge.py

Tests for the platform bridge (fetcher/bridge.py).

These run against the local SQLite test DB (PLATFORM_INTEGRATED=False → the
platform_sync models are managed locally), creating real Organization, Keyword
and Competitor rows, then asserting OnlineArticle / CompetitorArticle records.
"""
from unittest.mock import MagicMock, patch

from django.test import TestCase

import fetcher.services  # imported before any patch("fetcher.services.*") calls

from discovery.models import DiscoveredURL, SeedSource
from fetcher.bridge import (
    _matched_terms,
    _resolve_sentiment,
    push_competitor_to_platform,
    push_social_to_platform,
    push_to_platform,
)
from fetcher.models import FetchedPage, ParsedArticle
from platform_sync.models import (
    CompetitorArticle,
    Competitor,
    Keyword,
    OnlineArticle,
    Organization,
    SocialMediaPost,
)

_counter = 0


def _uid():
    global _counter
    _counter += 1
    return _counter


def make_article(**kwargs):
    uid = _uid()
    seed = SeedSource.objects.create(
        name=f"Seed {uid}", url=f"https://example.com/feed/{uid}", source_type="rss",
    )
    durl = DiscoveredURL.objects.create(
        seed=seed, url=kwargs.get("url", f"https://example.com/article/{uid}"), status="parsed",
    )
    page = FetchedPage.objects.create(
        discovered_url=durl, status_code=200, raw_html="<html></html>",
    )
    return ParsedArticle.objects.create(
        fetched_page=page,
        title=kwargs.get("title", "Debswana posts record diamond output"),
        body_text=kwargs.get("body_text", "The company expanded its operations this year."),
        summary=kwargs.get("summary", "A short summary."),
        source_domain=kwargs.get("source_domain", "mmegi.bw"),
        country=kwargs.get("country", "Botswana"),
        published_at=kwargs.get("published_at", None),
        signals=kwargs.get("signals", {"sentiment": "positive"}),
        is_duplicate=kwargs.get("is_duplicate", False),
    )


def make_org(name=None, status="active", keywords=(), competitors=()):
    org = Organization.objects.create(name=name or f"Org {_uid()}", status=status)
    for kw in keywords:
        Keyword.objects.create(organization=org, keyword=kw)
    for comp_name, aliases in competitors:
        Competitor.objects.create(organization=org, name=comp_name, aliases=aliases)
    return org


# ── Pure helpers ──────────────────────────────────────────────────────────────

class HelperTests(TestCase):
    def test_matched_terms_finds_in_title(self):
        self.assertEqual(_matched_terms(["Debswana"], "debswana wins award", "body"), ["Debswana"])

    def test_matched_terms_finds_in_body(self):
        self.assertEqual(_matched_terms(["Debswana"], "mining news", "debswana expands"), ["Debswana"])

    def test_matched_terms_no_match(self):
        self.assertEqual(_matched_terms(["copper"], "gold news", "gold body"), [])

    def test_resolve_sentiment_from_signals(self):
        art = MagicMock(signals={"sentiment": "negative"})
        self.assertEqual(_resolve_sentiment(art), "negative")

    def test_resolve_sentiment_defaults_neutral(self):
        self.assertEqual(_resolve_sentiment(MagicMock(signals={})), "neutral")
        self.assertEqual(_resolve_sentiment(MagicMock(signals={"sentiment": "bogus"})), "neutral")


# ── push_to_platform ──────────────────────────────────────────────────────────

class PushToPlatformTests(TestCase):

    def test_creates_online_article_on_title_match(self):
        org = make_org(name="Debswana", keywords=["Debswana"])
        article = make_article(title="Debswana posts record output")
        created = push_to_platform(article)

        self.assertEqual(len(created), 1)
        oa = OnlineArticle.objects.get(organization=org)
        self.assertEqual(oa.headline, "Debswana posts record output")
        self.assertEqual(oa.relevancy, 50.0)   # brand keyword in headline → 0–100 scorer
        self.assertEqual(oa.coverage, "Earned")
        self.assertEqual(oa.sentiment, "positive")
        self.assertEqual(oa.source, "mmegi.bw")
        self.assertEqual(oa.url, article.url)

    def test_body_only_match_is_captured_but_scores_zero(self):
        # Captured (keyword in body) but relevancy is scored on headline+summary
        # only — matching the platform — so a body-only mention scores 0.
        make_org(name="Debswana", keywords=["Debswana"])
        article = make_article(title="Mining sector update", body_text="Debswana expanded output.")
        push_to_platform(article)
        oa = OnlineArticle.objects.get()
        self.assertEqual(oa.relevancy, 0.0)

    def test_relevancy_uses_category_weight_and_repeat_bonus(self):
        # 'Debswana' (brand=50) appears twice in headline+summary → 50 + 0.1*50 = 55.
        make_org(name="Debswana", keywords=["Debswana"])
        article = make_article(title="Debswana wins", summary="Debswana again")
        push_to_platform(article)
        self.assertEqual(OnlineArticle.objects.get().relevancy, 55.0)

    def test_no_match_creates_nothing(self):
        make_org(name="BCL", keywords=["copper"])
        push_to_platform(make_article(title="Diamond news", body_text="no relevant terms"))
        self.assertEqual(OnlineArticle.objects.count(), 0)

    def test_inactive_org_skipped(self):
        make_org(name="Debswana", status="inactive", keywords=["Debswana"])
        push_to_platform(make_article(title="Debswana output"))
        self.assertEqual(OnlineArticle.objects.count(), 0)

    def test_sentiment_defaults_neutral_when_missing(self):
        make_org(name="Debswana", keywords=["Debswana"])
        push_to_platform(make_article(title="Debswana output", signals={}))
        self.assertEqual(OnlineArticle.objects.get().sentiment, "neutral")

    def test_published_date_falls_back_to_today(self):
        from django.utils import timezone
        make_org(name="Debswana", keywords=["Debswana"])
        push_to_platform(make_article(title="Debswana output", published_at=None))
        self.assertEqual(OnlineArticle.objects.get().date_published, timezone.now().date())

    def test_duplicate_not_created_for_same_org_and_url(self):
        make_org(name="Debswana", keywords=["Debswana"])
        article = make_article(title="Debswana output")
        push_to_platform(article)
        push_to_platform(article)  # second pass
        self.assertEqual(OnlineArticle.objects.count(), 1)

    def test_multiple_orgs_each_get_article(self):
        make_org(name="Debswana", keywords=["Debswana"])
        make_org(name="Diamond Co", keywords=["diamond"])
        push_to_platform(make_article(title="Debswana diamond output"))
        self.assertEqual(OnlineArticle.objects.count(), 2)


# ── push_competitor_to_platform ───────────────────────────────────────────────

class PushCompetitorTests(TestCase):

    def test_creates_competitor_article_on_name_match(self):
        org = make_org(name="Debswana", competitors=[("Lucara", "")])
        article = make_article(title="Lucara unveils large diamond")
        created = push_competitor_to_platform(article)

        self.assertEqual(len(created), 1)
        ca = CompetitorArticle.objects.get()
        self.assertEqual(ca.organization, org)
        self.assertEqual(ca.company_name, "Lucara")
        self.assertEqual(ca.matched_keywords, "Lucara")
        self.assertEqual(ca.sentiment, "positive")

    def test_matches_on_alias(self):
        make_org(name="Debswana", competitors=[("Lucara Diamond Corp", "Lucara, LDC")])
        push_competitor_to_platform(make_article(title="LDC reports strong quarter"))
        ca = CompetitorArticle.objects.get()
        self.assertIn("LDC", ca.matched_keywords)

    def test_no_match_creates_nothing(self):
        make_org(name="Debswana", competitors=[("Lucara", "")])
        push_competitor_to_platform(make_article(title="Unrelated headline", body_text="nothing"))
        self.assertEqual(CompetitorArticle.objects.count(), 0)

    def test_duplicate_not_created(self):
        make_org(name="Debswana", competitors=[("Lucara", "")])
        article = make_article(title="Lucara news")
        push_competitor_to_platform(article)
        push_competitor_to_platform(article)
        self.assertEqual(CompetitorArticle.objects.count(), 1)

    def test_inactive_org_competitor_skipped(self):
        make_org(name="Debswana", status="inactive", competitors=[("Lucara", "")])
        push_competitor_to_platform(make_article(title="Lucara news"))
        self.assertEqual(CompetitorArticle.objects.count(), 0)


# ── parse_page integration ─────────────────────────────────────────────────────

class ParsePageBridgeIntegrationTests(TestCase):
    """parse_page() should push to the platform after a successful parse."""

    def _run_parse(self, title="Debswana output", body="Debswana expanded.",
                   is_duplicate=False, url=None, publish_date="now"):
        import newspaper
        from django.utils import timezone
        if publish_date == "now":
            publish_date = timezone.now()
        seed = SeedSource.objects.create(
            name=f"Seed {_uid()}", url=f"https://example.com/feed/{_uid()}", source_type="rss",
        )
        durl = DiscoveredURL.objects.create(
            seed=seed, url=url or f"https://example.com/article/{_uid()}", status="fetched",
        )
        page = FetchedPage.objects.create(
            discovered_url=durl, status_code=200, raw_html=f"<html><body>{body}</body></html>",
        )

        from django.utils import timezone

        mock_article = MagicMock(spec=newspaper.Article)
        mock_article.title = title
        mock_article.text = body
        mock_article.authors = []
        # Current-month date + the '/article/' URL path → passes the news filter
        # (fetcher/news_filter.py) so the bridge actually fires.
        mock_article.publish_date = publish_date
        mock_article.meta_lang = "en"
        mock_article.meta_keywords = ""
        mock_article.meta_description = ""
        mock_article.tags = set()

        with patch("fetcher.services.newspaper.Article", return_value=mock_article), \
             patch("fetcher.services.get_rule_for_page", return_value=None), \
             patch("fetcher.services.run_nlp", return_value={"sentiment": "positive"}), \
             patch("fetcher.services.check_and_mark_duplicate",
                   side_effect=lambda a: setattr(a, "is_duplicate", is_duplicate)), \
             patch("fetcher.services.index_article"):
            from fetcher.services import parse_page
            return parse_page(page)

    def test_parse_creates_online_article(self):
        org = make_org(name="Debswana", keywords=["Debswana"])
        self._run_parse(title="Debswana output")
        self.assertTrue(OnlineArticle.objects.filter(organization=org).exists())

    def test_duplicate_article_not_pushed(self):
        make_org(name="Debswana", keywords=["Debswana"])
        self._run_parse(title="Debswana output", is_duplicate=True)
        self.assertEqual(OnlineArticle.objects.count(), 0)

    def test_product_page_not_pushed(self):
        # Corporate product page (no news path) → news filter skips the bridge.
        make_org(name="Debswana", keywords=["Debswana"])
        self._run_parse(
            title="Debswana Personal Loan",
            url="https://www.example.com/personal/loans/debswana-loan",
        )
        self.assertEqual(OnlineArticle.objects.count(), 0)

    def test_old_dated_article_not_pushed(self):
        # A news-path page but published outside the current month → skipped.
        from datetime import timedelta
        from django.utils import timezone
        make_org(name="Debswana", keywords=["Debswana"])
        self._run_parse(
            title="Debswana output",
            url="https://www.example.com/news/debswana-output",
            publish_date=timezone.now() - timedelta(days=70),
        )
        self.assertEqual(OnlineArticle.objects.count(), 0)

    def test_parse_survives_bridge_error(self):
        make_org(name="Debswana", keywords=["Debswana"])
        with patch("fetcher.services.push_to_platform", side_effect=Exception("platform down")):
            result = self._run_parse(title="Debswana output")
        self.assertIsNotNone(result)
        self.assertEqual(result.title, "Debswana output")


# ── push_social_to_platform ─────────────────────────────────────────────────────

class PushSocialToPlatformTests(TestCase):
    def test_creates_socialmediapost_on_keyword_match(self):
        make_org(name="Debswana", keywords=["Debswana"])
        art = make_article(
            title="Debswana wins award",
            signals={"sentiment": "positive", "platform": "linkedin",
                     "followers": 29828, "engagement": {"likes": 70, "shares": 6, "comments": 1}},
        )
        created = push_social_to_platform(art)
        self.assertEqual(len(created), 1)
        post = created[0]
        self.assertEqual(post.platform, "LinkedIn")
        self.assertEqual(post.sentiment, "positive")
        self.assertEqual(post.reach, 70)        # likes / reactions
        self.assertEqual(int(post.ave), 29828)  # author/page followers
        self.assertEqual(SocialMediaPost.objects.count(), 1)

    def test_country_defaults_to_org_country_when_post_has_none(self):
        org = make_org(name="Debswana", keywords=["Debswana"])
        org.country = "Botswana"
        org.save(update_fields=["country"])
        art = make_article(title="Debswana news", country="", signals={"platform": "x"})
        post = push_social_to_platform(art)[0]
        self.assertEqual(post.country, "Botswana")

    def test_x_platform_stored_as_twitter(self):
        make_org(name="Debswana", keywords=["Debswana"])
        art = make_article(title="Debswana news", signals={"platform": "x"})
        created = push_social_to_platform(art)
        self.assertEqual(created[0].platform, "Twitter")

    def test_unknown_platform_falls_back_to_other(self):
        make_org(name="Debswana", keywords=["Debswana"])
        art = make_article(title="Debswana news", signals={"platform": "myspace"})
        self.assertEqual(push_social_to_platform(art)[0].platform, "Other")

    def test_no_match_creates_nothing(self):
        make_org(name="Copper", keywords=["copper"])
        art = make_article(title="Debswana news", signals={"platform": "x"})
        self.assertEqual(push_social_to_platform(art), [])
        self.assertEqual(SocialMediaPost.objects.count(), 0)

    def test_dedup_on_org_and_url(self):
        make_org(name="Debswana", keywords=["Debswana"])
        art = make_article(title="Debswana news", signals={"platform": "x"})
        push_social_to_platform(art)
        push_social_to_platform(art)
        self.assertEqual(SocialMediaPost.objects.count(), 1)

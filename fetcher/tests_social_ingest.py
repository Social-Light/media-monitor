"""
fetcher/tests_social_ingest.py

Tests for fetcher/social_ingest.py — materialising a normalised Apify social
post into a ParsedArticle and running the downstream pipeline.

run_nlp and the Elasticsearch index call are patched (no model load / no ES).
The platform bridge runs for real against the locally-managed platform_sync
models (PLATFORM_INTEGRATED=False in tests), exactly like tests_bridge.
"""
from datetime import datetime, timezone as dt_timezone
from unittest.mock import patch

from django.test import TestCase

from core.models import URLStatusChoices
from discovery.models import DiscoveredURL, SeedSource, SourceType
from fetcher.models import FetchedPage, ParsedArticle
from fetcher.social_ingest import ingest_social_post
from platform_sync.models import Keyword, Organization, SocialMediaPost

_counter = 0


def _uid():
    global _counter
    _counter += 1
    return _counter


def make_social_seed(**kwargs):
    uid = _uid()
    defaults = dict(
        name=f"Social Seed {uid}",
        url=f"https://apify.example/social/{uid}",
        source_type=SourceType.SOCIAL,
    )
    defaults.update(kwargs)
    return SeedSource.objects.create(**defaults)


def make_post(**kwargs):
    uid = _uid()
    post = {
        "url":           f"https://x.com/u/status/{uid}",
        "platform":      "x",
        "author":        "Jane Doe",
        "author_handle": "jane",
        "text":          "Debswana posts record diamond output this quarter",
        "title":         "Debswana posts record diamond output this quarter",
        "snippet":       "Debswana posts record diamond output this quarter",
        "published_at":  datetime(2025, 4, 22, 8, 0, tzinfo=dt_timezone.utc),
        "followers":     5000,
        "engagement":    {"likes": 10, "shares": 2, "comments": 1},
        "source_type":   "social",
    }
    post.update(kwargs)
    return post


class IngestSocialPostTests(TestCase):
    def setUp(self):
        # No spaCy/NLTK load, no Elasticsearch connection in tests.
        nlp = patch("fetcher.social_ingest.run_nlp", return_value={"sentiment": "positive"})
        idx = patch("fetcher.social_ingest.index_article", return_value=None)
        self.mock_nlp = nlp.start()
        self.mock_index = idx.start()
        self.addCleanup(nlp.stop)
        self.addCleanup(idx.stop)
        self.seed = make_social_seed()

    # ── Materialisation ─────────────────────────────────────────────────────

    def test_creates_full_chain(self):
        post = make_post()
        article = ingest_social_post(self.seed, post)

        self.assertIsNotNone(article)
        self.assertEqual(article.body_text, post["text"])
        self.assertEqual(article.summary, post["text"])
        self.assertEqual(article.source_domain, "x.com")

        durl = DiscoveredURL.objects.get(url=post["url"])
        self.assertEqual(durl.status, URLStatusChoices.PARSED)
        self.assertEqual(durl.seed, self.seed)
        self.assertTrue(FetchedPage.objects.filter(discovered_url=durl).exists())

    def test_author_handle_formatting(self):
        article = ingest_social_post(self.seed, make_post(author="Jane Doe", author_handle="jane"))
        self.assertEqual(article.author, "Jane Doe (@jane)")

    def test_author_handle_only(self):
        article = ingest_social_post(self.seed, make_post(author="", author_handle="jane"))
        self.assertEqual(article.author, "@jane")

    def test_signals_carry_social_metadata(self):
        article = ingest_social_post(self.seed, make_post())
        self.assertTrue(article.signals["social"])
        self.assertEqual(article.signals["platform"], "x")
        self.assertEqual(article.signals["author_handle"], "jane")
        self.assertEqual(article.signals["sentiment"], "positive")
        self.assertEqual(article.signals["engagement"], {"likes": 10, "shares": 2, "comments": 1})
        self.assertEqual(article.signals["followers"], 5000)

    # ── Guard rails ─────────────────────────────────────────────────────────

    def test_no_url_skipped(self):
        self.assertIsNone(ingest_social_post(self.seed, make_post(url="")))
        self.assertEqual(ParsedArticle.objects.count(), 0)

    def test_idempotent_on_repeat(self):
        post = make_post()
        first = ingest_social_post(self.seed, post)
        second = ingest_social_post(self.seed, post)
        self.assertIsNotNone(first)
        self.assertIsNone(second)
        self.assertEqual(ParsedArticle.objects.count(), 1)

    # ── Pipeline integration ──────────────────────────────────────────────────

    def test_bridge_pushes_socialmediapost_when_org_keyword_matches(self):
        Organization.objects.create(name="Debswana Co", status="active").keywords.create(keyword="Debswana")
        article = ingest_social_post(self.seed, make_post())
        posts = SocialMediaPost.objects.filter(url=article.url)
        self.assertEqual(posts.count(), 1)
        post = posts.first()
        # platform "x" is stored as "Twitter"; author becomes the page_name.
        self.assertEqual(post.platform, "Twitter")
        self.assertEqual(post.page_name, "Jane Doe (@jane)")
        self.assertEqual(post.reach, 5000)         # reach = follower count
        self.assertEqual(float(post.ave), 1750.0)  # 5000 * 0.35

    def test_bridge_no_push_without_keyword_match(self):
        Organization.objects.create(name="Copper Co", status="active").keywords.create(keyword="copper")
        article = ingest_social_post(self.seed, make_post())
        self.assertIsNotNone(article)
        self.assertEqual(SocialMediaPost.objects.count(), 0)

    def test_indexing_called_for_non_duplicate(self):
        ingest_social_post(self.seed, make_post())
        self.mock_index.assert_called_once()

    def test_bridge_failure_does_not_abort(self):
        with patch("fetcher.social_ingest.push_social_to_platform", side_effect=Exception("platform down")):
            article = ingest_social_post(self.seed, make_post())
        # Article is still created and returned despite the bridge blowing up.
        self.assertIsNotNone(article)
        self.assertTrue(ParsedArticle.objects.filter(pk=article.pk).exists())

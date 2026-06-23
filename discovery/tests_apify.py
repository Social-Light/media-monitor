"""
Tests for discovery/services/apify.py

All HTTP is mocked — no Apify token or network access needed. Covers the field
normalisers (which absorb differing Actor output schemas), the per-platform
input builder, and fetch_mentions' guard rails (missing token, unknown platform,
no terms) plus its happy path and actor_input override.
"""
from datetime import datetime, timezone as dt_timezone
from unittest.mock import MagicMock, patch

from django.test import TestCase, override_settings

from discovery.services.apify import (
    _build_input,
    _deep_first,
    _extract_followers,
    _first,
    _nested_author,
    _normalise,
    _parse_date,
    _run_actor,
    fetch_mentions,
)

APIFY_SETTINGS = {
    "APIFY_API_TOKEN": "fake-token",
    "APIFY_MAX_ITEMS": 50,
    "APIFY_TIMEOUT":   120,
    "APIFY_ACTORS": {
        "x":         "apidojo/tweet-scraper",
        "facebook":  "apify/facebook-posts-scraper",
        "instagram": "apify/instagram-scraper",
        "linkedin":  "apimaestro/linkedin-posts-search-scraper-no-cookies",
    },
    "USER_AGENT":      "TestBot/1.0",
    "REQUEST_TIMEOUT": 10,
}

NO_TOKEN_SETTINGS = {**APIFY_SETTINGS, "APIFY_API_TOKEN": ""}


# ── Field helpers ───────────────────────────────────────────────────────────────

class FieldHelperTests(TestCase):
    def test_first_returns_first_truthy(self):
        self.assertEqual(_first({"a": "", "b": "x", "c": "y"}, ("a", "b", "c")), "x")

    def test_first_skips_empty_collections(self):
        self.assertEqual(_first({"a": [], "b": {}, "c": "v"}, ("a", "b", "c")), "v")

    def test_first_default_when_absent(self):
        self.assertEqual(_first({}, ("a", "b"), default=0), 0)

    def test_nested_author_top_level(self):
        self.assertEqual(_nested_author({"authorName": "Jane"}, ("authorName",)), "Jane")

    def test_nested_author_inside_author_object(self):
        item = {"author": {"userName": "jane_x"}}
        self.assertEqual(_nested_author(item, ("userName",)), "jane_x")

    def test_nested_author_missing(self):
        self.assertEqual(_nested_author({}, ("name",)), "")

    def test_deep_first_top_level(self):
        self.assertEqual(_deep_first({"likes": 5}, ("likes",)), 5)

    def test_deep_first_inside_container(self):
        item = {"stats": {"total_reactions": 9}}
        self.assertEqual(_deep_first(item, ("total_reactions",)), 9)

    def test_deep_first_default(self):
        self.assertEqual(_deep_first({}, ("likes",), default=0), 0)

    def test_parse_date_iso(self):
        self.assertEqual(_parse_date("2025-04-22T08:00:00Z").year, 2025)

    def test_parse_date_epoch_millis(self):
        # 1782112881700 ms ≈ 2026-06-22 UTC
        dt = _parse_date(1782112881700)
        self.assertEqual(dt.year, 2026)
        self.assertEqual(dt.tzinfo, dt_timezone.utc)

    def test_parse_date_epoch_string(self):
        self.assertEqual(_parse_date("1782112881700").year, 2026)

    def test_parse_date_none(self):
        self.assertIsNone(_parse_date(None))

    def test_parse_date_garbage(self):
        self.assertIsNone(_parse_date("not a date"))

    def test_followers_numeric_field(self):
        self.assertEqual(_extract_followers({"followersCount": 1200}), 1200)

    def test_followers_from_headline_with_commas(self):
        item = {"author": {"name": "The Projects Magazine", "headline": "29,828 followers"}}
        self.assertEqual(_extract_followers(item), 29828)

    def test_followers_personal_profile_is_zero(self):
        # Personal profiles put a job title / name in headline, not a follower count.
        item = {"author": {"name": "Juan Reina", "headline": "Juan Reina"}}
        self.assertEqual(_extract_followers(item), 0)

    def test_followers_absent_is_zero(self):
        self.assertEqual(_extract_followers({}), 0)


# ── _build_input ────────────────────────────────────────────────────────────────

class BuildInputTests(TestCase):
    def test_x_uses_search_terms_list(self):
        payload = _build_input("x", ["gold", "mining"], 30)
        self.assertEqual(payload["searchTerms"], ["gold", "mining"])
        self.assertEqual(payload["maxItems"], 30)

    def test_facebook_is_page_based(self):
        # Facebook has no keyword search — body carries only resultsLimit; the
        # Page startUrls come from the seed's meta.actor_input.
        payload = _build_input("facebook", ["gold", "mining"], 10)
        self.assertEqual(payload, {"resultsLimit": 10})

    def test_instagram_hashtag_search(self):
        payload = _build_input("instagram", ["gold"], 10)
        self.assertEqual(payload["resultsType"], "posts")
        self.assertEqual(payload["searchType"], "hashtag")
        self.assertEqual(payload["search"], "gold")

    def test_linkedin_keyword_singular(self):
        payload = _build_input("linkedin", ["gold", "mining"], 10)
        self.assertEqual(payload["keyword"], "gold mining")
        self.assertEqual(payload["limit"], 10)
        # Defaults to recent posts from the past month (overridable via actor_input).
        self.assertEqual(payload["sort_type"], "date_posted")
        self.assertEqual(payload["date_filter"], "past-month")

    def test_actor_input_can_widen_linkedin_date_filter(self):
        # Back-dating: actor_input overrides the past-month default.
        from unittest.mock import patch
        with patch("discovery.services.apify._run_actor", return_value=[]) as run:
            with override_settings(CRAWLER=APIFY_SETTINGS):
                fetch_mentions("linkedin", ["gold"], actor_input={"date_filter": ""})
        payload = run.call_args[0][1]
        self.assertEqual(payload["date_filter"], "")


# ── _normalise ──────────────────────────────────────────────────────────────────

class NormaliseTests(TestCase):
    def test_x_style_item(self):
        item = {
            "url": "https://x.com/u/status/1",
            "text": "Big news about Debswana today",
            "author": {"name": "Jane Doe", "userName": "jane"},
            "createdAt": "2025-04-22T08:00:00Z",
            "likes": 12, "retweetCount": 3, "replyCount": 1,
        }
        post = _normalise("x", item)
        self.assertEqual(post["url"], "https://x.com/u/status/1")
        self.assertEqual(post["platform"], "x")
        self.assertEqual(post["author"], "Jane Doe")
        self.assertEqual(post["author_handle"], "jane")
        self.assertEqual(post["text"], "Big news about Debswana today")
        self.assertEqual(post["title"], "Big news about Debswana today")
        self.assertEqual(post["snippet"], post["text"])
        self.assertEqual(post["engagement"], {"likes": 12, "shares": 3, "comments": 1})
        self.assertIsInstance(post["published_at"], datetime)
        self.assertEqual(post["source_type"], "social")

    def test_instagram_style_item(self):
        item = {
            "postUrl": "https://instagram.com/p/abc",
            "caption": "Loving the new mine tour",
            "ownerFullName": "Mining Fan",
            "ownerUsername": "mining_fan",
        }
        post = _normalise("instagram", item)
        self.assertEqual(post["url"], "https://instagram.com/p/abc")
        self.assertEqual(post["text"], "Loving the new mine tour")
        self.assertEqual(post["author"], "Mining Fan")
        self.assertEqual(post["author_handle"], "mining_fan")

    def test_title_falls_back_to_attribution_when_no_text(self):
        post = _normalise("x", {"url": "https://x.com/u/status/9", "authorName": "Jane"})
        self.assertEqual(post["text"], "")
        self.assertEqual(post["title"], "Jane on X")

    def test_title_uses_first_line_only(self):
        post = _normalise("x", {"url": "u", "text": "Headline line\nrest of body"})
        self.assertEqual(post["title"], "Headline line")

    def test_linkedin_style_nested_item(self):
        # Real apimaestro/linkedin-posts-search-scraper-no-cookies shape:
        # post_url for the URL, date+timestamp nested under posted_at, engagement
        # nested under stats, author under author.name.
        item = {
            "post_url": "https://www.linkedin.com/posts/foo-activity-123",
            "text": "Exciting opportunity in the mining industry",
            "author": {"name": "Viking Ultra", "headline": "29,828 followers"},
            "posted_at": {"date": "2026-06-22 09:21:21", "timestamp": 1782112881700},
            "stats": {"total_reactions": 4, "comments": 2, "shares": 1},
        }
        post = _normalise("linkedin", item)
        self.assertEqual(post["url"], "https://www.linkedin.com/posts/foo-activity-123")
        self.assertEqual(post["author"], "Viking Ultra")
        self.assertEqual(post["text"], "Exciting opportunity in the mining industry")
        self.assertEqual(post["published_at"].year, 2026)
        # reach comes from total_reactions; ave-source followers parsed from headline.
        self.assertEqual(post["engagement"], {"likes": 4, "shares": 1, "comments": 2})
        self.assertEqual(post["followers"], 29828)

    def test_unusable_item_returns_none(self):
        self.assertIsNone(_normalise("x", {"likes": 5}))

    def test_non_dict_returns_none(self):
        self.assertIsNone(_normalise("x", "not a dict"))


# ── _run_actor ──────────────────────────────────────────────────────────────────

@override_settings(CRAWLER=APIFY_SETTINGS)
class RunActorTests(TestCase):
    @patch("discovery.services.apify.get_session")
    def test_returns_dataset_items(self, mock_get_session):
        response = MagicMock()
        response.json.return_value = [{"url": "u", "text": "t"}]
        session = MagicMock()
        session.post.return_value = response
        mock_get_session.return_value = session

        items = _run_actor("apidojo/tweet-scraper", {"q": 1}, "tok", 30)
        self.assertEqual(items, [{"url": "u", "text": "t"}])
        # token rides in the Authorization header (never the URL), input in body
        args, kwargs = session.post.call_args
        self.assertEqual(args[0], "https://api.apify.com/v2/acts/apidojo~tweet-scraper/run-sync-get-dataset-items")
        self.assertEqual(kwargs["headers"], {"Authorization": "Bearer tok"})
        self.assertEqual(kwargs["json"], {"q": 1})

    @patch("discovery.services.apify.get_session")
    def test_http_error_returns_empty(self, mock_get_session):
        session = MagicMock()
        session.post.side_effect = Exception("boom")
        mock_get_session.return_value = session
        self.assertEqual(_run_actor("actor/x", {}, "tok", 30), [])

    @patch("discovery.services.apify.get_session")
    def test_non_list_payload_returns_empty(self, mock_get_session):
        response = MagicMock()
        response.json.return_value = {"error": "rate limited"}
        session = MagicMock()
        session.post.return_value = response
        mock_get_session.return_value = session
        self.assertEqual(_run_actor("actor/x", {}, "tok", 30), [])


# ── fetch_mentions ──────────────────────────────────────────────────────────────

class FetchMentionsTests(TestCase):
    @override_settings(CRAWLER=NO_TOKEN_SETTINGS)
    def test_no_token_returns_empty(self):
        self.assertEqual(fetch_mentions("x", ["gold"]), [])

    @override_settings(CRAWLER=APIFY_SETTINGS)
    def test_unknown_platform_returns_empty(self):
        self.assertEqual(fetch_mentions("myspace", ["gold"]), [])

    @override_settings(CRAWLER=APIFY_SETTINGS)
    def test_no_terms_returns_empty(self):
        self.assertEqual(fetch_mentions("x", []), [])
        self.assertEqual(fetch_mentions("x", ["", "  "]), [])

    @override_settings(CRAWLER=APIFY_SETTINGS)
    @patch("discovery.services.apify._run_actor")
    def test_happy_path_normalises_items(self, mock_run):
        mock_run.return_value = [
            {"url": "https://x.com/1", "text": "Debswana news", "authorName": "A"},
            {"likes": 1},  # unusable → dropped
        ]
        posts = fetch_mentions("x", "Debswana")
        self.assertEqual(len(posts), 1)
        self.assertEqual(posts[0]["url"], "https://x.com/1")

    @override_settings(CRAWLER=APIFY_SETTINGS)
    @patch("discovery.services.apify._run_actor")
    def test_string_term_is_wrapped(self, mock_run):
        mock_run.return_value = []
        fetch_mentions("x", "Debswana")
        actor, payload, token, timeout = mock_run.call_args[0]
        self.assertEqual(actor, "apidojo/tweet-scraper")
        self.assertEqual(payload["searchTerms"], ["Debswana"])
        self.assertEqual(token, "fake-token")

    @override_settings(CRAWLER=APIFY_SETTINGS)
    @patch("discovery.services.apify._run_actor")
    def test_actor_input_overrides_default(self, mock_run):
        mock_run.return_value = []
        fetch_mentions("x", ["gold"], actor_input={"sort": "Top", "extra": 1})
        payload = mock_run.call_args[0][1]
        self.assertEqual(payload["sort"], "Top")
        self.assertEqual(payload["extra"], 1)
        self.assertEqual(payload["searchTerms"], ["gold"])

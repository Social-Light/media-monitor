"""
alerts/tests_alerts.py

Tests for:
  Alert.keywords_list property
  matches_alert()
  check_alerts()
  send_email_alert()
  send_webhook_alert()
  dispatch_notifications()
  fetcher.services.parse_page() alert integration
"""
from unittest.mock import MagicMock, patch

from django.core import mail
from django.test import TestCase, override_settings

import fetcher.services  # must be imported before any patch("fetcher.services.*") calls

from alerts.matching import check_alerts, matches_alert
from alerts.models import Alert, AlertMatch
from alerts.notifications import dispatch_notifications, send_email_alert, send_webhook_alert
from discovery.models import SeedSource
from fetcher.models import FetchedPage, ParsedArticle

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
        title=kwargs.get("title", "Gold Mining Report"),
        body_text=kwargs.get("body_text", "Gold prices in southern Africa rose sharply."),
        source_domain=kwargs.get("source_domain", "mining.com"),
        language=kwargs.get("language", "en"),
        is_duplicate=kwargs.get("is_duplicate", False),
        signals=kwargs.get("signals", {"sentiment": "positive"}),
    )


def make_alert(**kwargs):
    return Alert.objects.create(
        name=kwargs.get("name", f"Alert {_uid()}"),
        keywords=kwargs.get("keywords", "gold, mining"),
        email=kwargs.get("email", ""),
        webhook_url=kwargs.get("webhook_url", ""),
        source_domain=kwargs.get("source_domain", ""),
        language=kwargs.get("language", ""),
        sentiment=kwargs.get("sentiment", ""),
        is_active=kwargs.get("is_active", True),
    )


def make_match(alert, article, **kwargs):
    return AlertMatch.objects.create(
        alert=alert,
        article=article,
        matched_keywords=kwargs.get("matched_keywords", ["gold"]),
        notified=kwargs.get("notified", False),
    )


# ── Alert.keywords_list ────────────────────────────────────────────────────────

class AlertKeywordsListTests(TestCase):
    def test_splits_on_comma(self):
        alert = make_alert(keywords="gold, mining, Africa")
        self.assertEqual(alert.keywords_list, ["gold", "mining", "africa"])

    def test_strips_whitespace(self):
        alert = make_alert(keywords="  gold  ,  mining  ")
        self.assertEqual(alert.keywords_list, ["gold", "mining"])

    def test_lowercases_keywords(self):
        alert = make_alert(keywords="Gold,MINING,Africa")
        self.assertEqual(alert.keywords_list, ["gold", "mining", "africa"])

    def test_ignores_empty_segments(self):
        alert = make_alert(keywords="gold,,mining,")
        self.assertEqual(alert.keywords_list, ["gold", "mining"])

    def test_single_keyword(self):
        alert = make_alert(keywords="platinum")
        self.assertEqual(alert.keywords_list, ["platinum"])


# ── matches_alert() ────────────────────────────────────────────────────────────

class MatchesAlertTests(TestCase):
    def setUp(self):
        self.article = make_article(
            title="Gold Mining in Botswana",
            body_text="Platinum and diamond reserves are also significant.",
            source_domain="mining.com",
            language="en",
            signals={"sentiment": "positive"},
        )

    def test_matches_keyword_in_title(self):
        alert = make_alert(keywords="gold")
        result = matches_alert(alert, self.article)
        self.assertIn("gold", result)

    def test_matches_keyword_in_body(self):
        alert = make_alert(keywords="platinum")
        result = matches_alert(alert, self.article)
        self.assertIn("platinum", result)

    def test_no_match_when_keyword_absent(self):
        alert = make_alert(keywords="copper")
        result = matches_alert(alert, self.article)
        self.assertEqual(result, [])

    def test_matching_is_case_insensitive(self):
        alert = make_alert(keywords="GOLD")
        result = matches_alert(alert, self.article)
        self.assertIn("gold", result)

    def test_returns_only_matched_keywords(self):
        alert = make_alert(keywords="gold, copper, platinum")
        result = matches_alert(alert, self.article)
        self.assertIn("gold", result)
        self.assertIn("platinum", result)
        self.assertNotIn("copper", result)

    def test_domain_filter_blocks_wrong_domain(self):
        alert = make_alert(keywords="gold", source_domain="other.com")
        result = matches_alert(alert, self.article)
        self.assertEqual(result, [])

    def test_domain_filter_passes_correct_domain(self):
        alert = make_alert(keywords="gold", source_domain="mining.com")
        result = matches_alert(alert, self.article)
        self.assertIn("gold", result)

    def test_language_filter_blocks_wrong_language(self):
        alert = make_alert(keywords="gold", language="fr")
        result = matches_alert(alert, self.article)
        self.assertEqual(result, [])

    def test_language_filter_passes_correct_language(self):
        alert = make_alert(keywords="gold", language="en")
        result = matches_alert(alert, self.article)
        self.assertIn("gold", result)

    def test_sentiment_filter_blocks_wrong_sentiment(self):
        alert = make_alert(keywords="gold", sentiment="negative")
        result = matches_alert(alert, self.article)
        self.assertEqual(result, [])

    def test_sentiment_filter_passes_correct_sentiment(self):
        alert = make_alert(keywords="gold", sentiment="positive")
        result = matches_alert(alert, self.article)
        self.assertIn("gold", result)

    def test_no_filters_matches_any_domain_and_language(self):
        alert = make_alert(keywords="gold", source_domain="", language="", sentiment="")
        result = matches_alert(alert, self.article)
        self.assertIn("gold", result)

    def test_empty_keywords_list_returns_empty(self):
        alert = make_alert(keywords="  ,  ")
        result = matches_alert(alert, self.article)
        self.assertEqual(result, [])


# ── check_alerts() ─────────────────────────────────────────────────────────────

class CheckAlertsTests(TestCase):
    def setUp(self):
        self.article = make_article(title="Gold Rush in Africa")

    def test_creates_match_for_matching_alert(self):
        alert = make_alert(keywords="gold")
        check_alerts(self.article)
        self.assertTrue(AlertMatch.objects.filter(alert=alert, article=self.article).exists())

    def test_no_match_creates_no_record(self):
        make_alert(keywords="copper")
        check_alerts(self.article)
        self.assertEqual(AlertMatch.objects.count(), 0)

    def test_inactive_alerts_are_skipped(self):
        make_alert(keywords="gold", is_active=False)
        check_alerts(self.article)
        self.assertEqual(AlertMatch.objects.count(), 0)

    def test_returns_new_match_instances(self):
        make_alert(keywords="gold")
        matches = check_alerts(self.article)
        self.assertEqual(len(matches), 1)
        self.assertIsInstance(matches[0], AlertMatch)

    def test_does_not_create_duplicate_match(self):
        alert = make_alert(keywords="gold")
        check_alerts(self.article)
        check_alerts(self.article)  # second call
        self.assertEqual(AlertMatch.objects.filter(alert=alert, article=self.article).count(), 1)

    def test_second_call_returns_empty_list(self):
        make_alert(keywords="gold")
        check_alerts(self.article)
        matches = check_alerts(self.article)
        self.assertEqual(matches, [])

    def test_matched_keywords_stored_on_match(self):
        make_alert(keywords="gold, copper")
        matches = check_alerts(self.article)
        self.assertIn("gold", matches[0].matched_keywords)


# ── send_email_alert() ────────────────────────────────────────────────────────

@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class SendEmailAlertTests(TestCase):
    def setUp(self):
        self.alert   = make_alert(keywords="gold", email="ops@example.com")
        self.article = make_article(title="Gold in Botswana")
        self.match   = make_match(self.alert, self.article, matched_keywords=["gold"])

    def test_sends_email_to_alert_address(self):
        send_email_alert(self.match)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["ops@example.com"])

    def test_email_subject_contains_alert_name(self):
        send_email_alert(self.match)
        self.assertIn(self.alert.name, mail.outbox[0].subject)

    def test_email_subject_contains_article_title(self):
        send_email_alert(self.match)
        self.assertIn("Gold in Botswana", mail.outbox[0].subject)

    def test_email_body_contains_url(self):
        send_email_alert(self.match)
        self.assertIn(self.article.url, mail.outbox[0].body)

    def test_email_body_contains_matched_keywords(self):
        send_email_alert(self.match)
        self.assertIn("gold", mail.outbox[0].body)


# ── send_webhook_alert() ──────────────────────────────────────────────────────

class SendWebhookAlertTests(TestCase):
    def setUp(self):
        self.alert   = make_alert(keywords="gold", webhook_url="https://hook.example.com/notify")
        self.article = make_article(title="Gold Discovery")
        self.match   = make_match(self.alert, self.article, matched_keywords=["gold"])

    @patch("alerts.notifications._requests")
    def test_posts_to_webhook_url(self, mock_req):
        mock_req.post.return_value = MagicMock(status_code=200)
        mock_req.post.return_value.raise_for_status = MagicMock()
        send_webhook_alert(self.match)
        mock_req.post.assert_called_once()
        url_called = mock_req.post.call_args[0][0]
        self.assertEqual(url_called, "https://hook.example.com/notify")

    @patch("alerts.notifications._requests")
    def test_payload_contains_alert_name(self, mock_req):
        mock_req.post.return_value = MagicMock(status_code=200)
        mock_req.post.return_value.raise_for_status = MagicMock()
        send_webhook_alert(self.match)
        payload = mock_req.post.call_args[1]["json"]
        self.assertEqual(payload["alert_name"], self.alert.name)

    @patch("alerts.notifications._requests")
    def test_payload_contains_article_data(self, mock_req):
        mock_req.post.return_value = MagicMock(status_code=200)
        mock_req.post.return_value.raise_for_status = MagicMock()
        send_webhook_alert(self.match)
        payload = mock_req.post.call_args[1]["json"]
        self.assertEqual(payload["article"]["title"], "Gold Discovery")
        self.assertEqual(payload["article"]["url"], self.article.url)

    @patch("alerts.notifications._requests")
    def test_payload_contains_matched_keywords(self, mock_req):
        mock_req.post.return_value = MagicMock(status_code=200)
        mock_req.post.return_value.raise_for_status = MagicMock()
        send_webhook_alert(self.match)
        payload = mock_req.post.call_args[1]["json"]
        self.assertIn("gold", payload["matched_keywords"])

    @patch("alerts.notifications._requests")
    def test_raises_on_http_error(self, mock_req):
        import requests as real_requests
        mock_req.post.return_value = MagicMock(status_code=500)
        mock_req.post.return_value.raise_for_status.side_effect = (
            real_requests.HTTPError("500 Server Error")
        )
        with self.assertRaises(real_requests.HTTPError):
            send_webhook_alert(self.match)


# ── dispatch_notifications() ──────────────────────────────────────────────────

@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class DispatchNotificationsTests(TestCase):
    def setUp(self):
        self.article = make_article(title="Platinum report")

    def test_sends_email_when_email_set(self):
        alert = make_alert(keywords="platinum", email="watch@example.com")
        match = make_match(alert, self.article, matched_keywords=["platinum"])
        dispatch_notifications(match)
        self.assertEqual(len(mail.outbox), 1)

    @patch("alerts.notifications._requests")
    def test_sends_webhook_when_url_set(self, mock_req):
        mock_req.post.return_value = MagicMock(status_code=200)
        mock_req.post.return_value.raise_for_status = MagicMock()
        alert = make_alert(keywords="platinum", webhook_url="https://hook.example.com/x")
        match = make_match(alert, self.article, matched_keywords=["platinum"])
        dispatch_notifications(match)
        mock_req.post.assert_called_once()

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    def test_marks_notified_after_email_success(self):
        alert = make_alert(keywords="platinum", email="watch@example.com")
        match = make_match(alert, self.article, matched_keywords=["platinum"])
        dispatch_notifications(match)
        match.refresh_from_db()
        self.assertTrue(match.notified)
        self.assertIsNotNone(match.notified_at)

    def test_not_notified_when_no_channels(self):
        alert = make_alert(keywords="platinum", email="", webhook_url="")
        match = make_match(alert, self.article, matched_keywords=["platinum"])
        dispatch_notifications(match)
        match.refresh_from_db()
        self.assertFalse(match.notified)

    @patch("alerts.notifications.send_email_alert", side_effect=Exception("SMTP down"))
    @patch("alerts.notifications._requests")
    def test_webhook_still_sent_when_email_fails(self, mock_req, mock_email):
        mock_req.post.return_value = MagicMock(status_code=200)
        mock_req.post.return_value.raise_for_status = MagicMock()
        alert = make_alert(
            keywords="platinum",
            email="watch@example.com",
            webhook_url="https://hook.example.com/x",
        )
        match = make_match(alert, self.article, matched_keywords=["platinum"])
        dispatch_notifications(match)
        mock_req.post.assert_called_once()

    @patch("alerts.notifications.send_webhook_alert", side_effect=Exception("timeout"))
    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    def test_email_still_sent_when_webhook_fails(self, mock_webhook):
        alert = make_alert(
            keywords="platinum",
            email="watch@example.com",
            webhook_url="https://hook.example.com/x",
        )
        match = make_match(alert, self.article, matched_keywords=["platinum"])
        dispatch_notifications(match)
        self.assertEqual(len(mail.outbox), 1)


# ── services.py integration ───────────────────────────────────────────────────

class ParsePageAlertIntegrationTests(TestCase):
    """
    Test that parse_page() triggers alert matching and notification dispatch.
    All external I/O (newspaper3k, ES, NLP, dedup, requests) is mocked.
    """

    def _run_parse(self, title="Gold Rush", body="Gold reserves confirmed."):
        import newspaper
        import fetcher.services  # ensure module is imported before patching
        seed = make_seed()
        from discovery.models import DiscoveredURL
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
        mock_article.text  = body
        mock_article.authors = []
        mock_article.publish_date = None
        mock_article.meta_lang = "en"
        mock_article.meta_keywords = ""
        mock_article.meta_description = ""
        mock_article.tags = set()

        with patch("fetcher.services.newspaper.Article", return_value=mock_article), \
             patch("fetcher.services.get_rule_for_page", return_value=None), \
             patch("fetcher.services.run_nlp", return_value={"sentiment": "positive"}), \
             patch("fetcher.services.check_and_mark_duplicate", return_value=False), \
             patch("fetcher.services.index_article"):
            from fetcher.services import parse_page
            return parse_page(page)

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    def test_matching_alert_receives_email_after_parse(self):
        make_alert(keywords="gold", email="team@example.com")
        self._run_parse(title="Gold Rush")
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("team@example.com", mail.outbox[0].to)

    def test_non_matching_alert_sends_no_notification(self):
        make_alert(keywords="copper", email="team@example.com")
        with patch("alerts.notifications.send_email_alert") as mock_email:
            self._run_parse(title="Gold Rush")
            mock_email.assert_not_called()

    def test_parse_succeeds_when_check_alerts_raises(self):
        with patch("fetcher.services.check_alerts", side_effect=Exception("DB error")):
            result = self._run_parse(title="Gold Rush")
        self.assertIsNotNone(result)
        self.assertEqual(result.title, "Gold Rush")

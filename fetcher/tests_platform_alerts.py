"""
fetcher/tests_platform_alerts.py

Tests for platform-alert notifications (fetcher/platform_alerts.py): notifying off
the platform's Alert model when the crawler captures OnlineArticle coverage.

Run against the local SQLite test DB (PLATFORM_INTEGRATED=False → platform_sync
models managed locally). Email uses the locmem backend.
"""
from datetime import date

from django.core import mail
from django.test import TestCase, override_settings

from alerts.models import PlatformAlertNotification
from fetcher.platform_alerts import notify_for_article, send_pending_digests
from platform_sync.models import Alert, OnlineArticle, Organization

_counter = 0


def _uid():
    global _counter
    _counter += 1
    return _counter


def make_org(name=None, status="active"):
    return Organization.objects.create(name=name or f"Org {_uid()}", status=status)


def make_article(org, headline="Debswana posts record output", summary="diamond output up"):
    uid = _uid()
    return OnlineArticle.objects.create(
        organization=org,
        source="mmegi.bw",
        headline=headline,
        summary=summary,
        url=f"https://mmegi.bw/article/{uid}",
        date_published=date(2026, 6, 1),
        sentiment="positive",
        coverage="Earned",
    )


def make_alert(org, **kwargs):
    return Alert.objects.create(
        organization=org,
        name=kwargs.get("name", f"Alert {_uid()}"),
        keywords=kwargs.get("keywords", ""),
        email=kwargs.get("email", "watch@example.com"),
        frequency=kwargs.get("frequency", "immediate"),
        email_subject=kwargs.get("email_subject", ""),
        is_active=kwargs.get("is_active", True),
    )


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class NotifyForArticleTests(TestCase):

    def test_immediate_alert_sends_email_and_records(self):
        org = make_org()
        make_alert(org, keywords="debswana", frequency="immediate", email="ops@example.com")
        article = make_article(org, headline="Debswana output climbs")

        notifs = notify_for_article(article)

        self.assertEqual(len(notifs), 1)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["ops@example.com"])
        n = PlatformAlertNotification.objects.get()
        self.assertTrue(n.sent)
        self.assertIsNotNone(n.sent_at)
        self.assertEqual(n.matched_keywords, ["debswana"])

    def test_empty_keywords_matches_all(self):
        org = make_org()
        make_alert(org, keywords="", frequency="immediate")
        notify_for_article(make_article(org))
        self.assertEqual(PlatformAlertNotification.objects.count(), 1)
        self.assertEqual(len(mail.outbox), 1)

    def test_no_keyword_match_no_notification(self):
        org = make_org()
        make_alert(org, keywords="copper", frequency="immediate")
        notify_for_article(make_article(org, headline="Diamond news", summary="gems"))
        self.assertEqual(PlatformAlertNotification.objects.count(), 0)
        self.assertEqual(len(mail.outbox), 0)

    def test_inactive_alert_skipped(self):
        org = make_org()
        make_alert(org, keywords="", is_active=False)
        notify_for_article(make_article(org))
        self.assertEqual(PlatformAlertNotification.objects.count(), 0)

    def test_alert_for_other_org_not_triggered(self):
        org_a = make_org(name="A")
        org_b = make_org(name="B")
        make_alert(org_b, keywords="")
        notify_for_article(make_article(org_a))
        self.assertEqual(PlatformAlertNotification.objects.count(), 0)

    def test_duplicate_not_resent(self):
        org = make_org()
        make_alert(org, keywords="", frequency="immediate")
        article = make_article(org)
        notify_for_article(article)
        notify_for_article(article)  # second pass
        self.assertEqual(PlatformAlertNotification.objects.count(), 1)
        self.assertEqual(len(mail.outbox), 1)

    def test_daily_alert_queues_without_sending(self):
        org = make_org()
        make_alert(org, keywords="", frequency="daily")
        notify_for_article(make_article(org))
        n = PlatformAlertNotification.objects.get()
        self.assertFalse(n.sent)
        self.assertEqual(len(mail.outbox), 0)

    def test_immediate_without_email_queues(self):
        org = make_org()
        make_alert(org, keywords="", frequency="immediate", email="")
        notify_for_article(make_article(org))
        n = PlatformAlertNotification.objects.get()
        self.assertFalse(n.sent)  # no recipient → not sent
        self.assertEqual(len(mail.outbox), 0)


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class SendPendingDigestsTests(TestCase):

    def test_flushes_pending_daily_as_digest(self):
        org = make_org()
        make_alert(org, keywords="", frequency="daily", email="digest@example.com")
        notify_for_article(make_article(org, headline="Item one"))
        notify_for_article(make_article(org, headline="Item two"))
        self.assertEqual(len(mail.outbox), 0)  # nothing sent yet

        flushed = send_pending_digests(frequency="daily")

        self.assertEqual(flushed, 2)
        self.assertEqual(len(mail.outbox), 1)  # one digest email
        self.assertIn("Item one", mail.outbox[0].body)
        self.assertIn("Item two", mail.outbox[0].body)
        self.assertEqual(PlatformAlertNotification.objects.filter(sent=False).count(), 0)

    def test_frequency_filter_leaves_others_pending(self):
        org = make_org()
        make_alert(org, keywords="", frequency="weekly", email="w@example.com")
        notify_for_article(make_article(org))
        flushed = send_pending_digests(frequency="daily")  # wrong frequency
        self.assertEqual(flushed, 0)
        self.assertEqual(len(mail.outbox), 0)

    def test_digest_idempotent_second_run_sends_nothing(self):
        org = make_org()
        make_alert(org, keywords="", frequency="daily", email="d@example.com")
        notify_for_article(make_article(org))
        send_pending_digests()
        mail.outbox.clear()
        flushed = send_pending_digests()
        self.assertEqual(flushed, 0)
        self.assertEqual(len(mail.outbox), 0)

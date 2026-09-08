"""
core/tests_email_transport.py

Alert delivery moved off a vendor HTTP API onto plain SMTP. What the crawler
sends did not change, so these tests are about the transport: that Django's SMTP
backend is what the EMAIL_* settings build, that no vendor package or vendor
environment variable is needed any longer, and that a mail server outage is
logged rather than swallowed.

Nothing here opens a socket and no credential is real.
"""
from pathlib import Path
from smtplib import SMTPAuthenticationError
from unittest import mock

from django.conf import settings
from django.core import mail
from django.core.mail import get_connection, send_mail
from django.core.mail.backends.smtp import EmailBackend as DjangoSMTPBackend
from django.test import TestCase, override_settings

SMTP = "django.core.mail.backends.smtp.EmailBackend"
LOCMEM = "django.core.mail.backends.locmem.EmailBackend"

# Values that exist only in this file, chosen so a copy-paste out of it cannot
# configure anything real.
FAKE_SMTP = {
    "EMAIL_BACKEND":      SMTP,
    "EMAIL_HOST":         "smtp.invalid.test",
    "EMAIL_PORT":         587,
    "EMAIL_HOST_USER":    "not-a-real-user",
    "EMAIL_HOST_PASSWORD": "not-a-real-password",
    "EMAIL_USE_TLS":      True,
    "EMAIL_USE_SSL":      False,
}

PROJECT_ROOT = Path(settings.BASE_DIR)


class SmtpTransportTests(TestCase):
    def test_the_smtp_settings_build_djangos_smtp_backend(self):
        with override_settings(**FAKE_SMTP):
            connection = get_connection()
        self.assertIsInstance(connection, DjangoSMTPBackend)
        self.assertEqual(connection.host, "smtp.invalid.test")
        self.assertEqual(connection.port, 587)
        self.assertTrue(connection.use_tls)
        self.assertFalse(connection.use_ssl)

    def test_no_vendor_mail_package_is_installed_or_configured(self):
        self.assertNotIn("anymail", settings.INSTALLED_APPS)
        self.assertFalse(hasattr(settings, "ANYMAIL"))

    def test_no_vendor_environment_variable_is_needed_to_send(self):
        with mock.patch.dict("os.environ", {}, clear=True), override_settings(EMAIL_BACKEND=LOCMEM):
            send_mail("Subject", "Body", settings.DEFAULT_FROM_EMAIL, ["ops@example.com"])
        self.assertEqual(len(mail.outbox), 1)

    def test_no_vendor_reference_survives_in_the_source(self):
        patterns = ("anymail", "api.resend.com", "RESEND_API_KEY")
        searched = [PROJECT_ROOT / "requirements.txt", PROJECT_ROOT / ".env.example"]
        for folder in ("alerts", "api", "core", "dashboard", "discovery",
                       "fetcher", "matching", "media_monitor", "platform_sync"):
            searched += [p for p in (PROJECT_ROOT / folder).rglob("*")
                         if p.suffix in (".py", ".html", ".txt") and "__pycache__" not in p.parts]

        offenders = []
        for path in searched:
            # This file names the patterns in order to look for them.
            if not path.is_file() or path.name == "tests_email_transport.py":
                continue
            body = path.read_text(encoding="utf-8", errors="ignore").lower()
            offenders += [f"{path.relative_to(PROJECT_ROOT)}: {p}"
                          for p in patterns if p.lower() in body]
        self.assertEqual(offenders, [])


class SmtpFailureTests(TestCase):
    """An outage must be reported, not swallowed. ``send_email_alert`` sends with
    fail_silently=False precisely so that ``dispatch_notifications`` can see the
    failure and leave the match unnotified for a later retry."""

    FAILURE = SMTPAuthenticationError(535, b"5.7.8 authentication failed")

    @staticmethod
    def smtp_down(failure):
        """Fail at the connection, where a real mail server fails, rather than by
        replacing the backend method — that would also defeat fail_silently for
        any caller that legitimately asks for it."""
        return mock.patch("django.core.mail.backends.smtp.smtplib.SMTP", side_effect=failure)

    @override_settings(**FAKE_SMTP)
    def test_a_failed_send_raises_rather_than_reporting_success(self):
        with self.smtp_down(self.FAILURE):
            with self.assertRaises(SMTPAuthenticationError):
                send_mail("Subject", "Body", settings.DEFAULT_FROM_EMAIL,
                          ["ops@example.com"], fail_silently=False)

    @override_settings(**FAKE_SMTP)
    def test_fail_silently_still_suppresses_where_a_caller_asked_for_it(self):
        """Proof that the failure above is the backend refusing, not the patch
        bypassing the backend's own error handling."""
        with self.smtp_down(self.FAILURE):
            sent = send_mail("Subject", "Body", settings.DEFAULT_FROM_EMAIL,
                             ["ops@example.com"], fail_silently=True)
        self.assertEqual(sent, 0)

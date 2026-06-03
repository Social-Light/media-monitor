"""
core/tests_throttle.py

Tests for the per-domain rate limiter.
"""
import time
from unittest.mock import patch

from django.test import TestCase, override_settings

from core.throttle import wait_for_domain, _state, _domain_locks


def _clear_state():
    _state.clear()
    _domain_locks.clear()


@override_settings(CRAWLER={"POLITENESS_DELAY": 0.1})
class WaitForDomainTests(TestCase):

    def setUp(self):
        _clear_state()

    def test_first_call_does_not_sleep(self):
        """First request to a domain should not block."""
        start = time.monotonic()
        wait_for_domain("https://example.com/page1")
        elapsed = time.monotonic() - start
        self.assertLess(elapsed, 0.05)

    def test_rapid_second_call_sleeps(self):
        """A second call immediately after the first should wait ~delay seconds."""
        wait_for_domain("https://example.com/page1")
        start = time.monotonic()
        wait_for_domain("https://example.com/page2")
        elapsed = time.monotonic() - start
        self.assertGreaterEqual(elapsed, 0.08)

    def test_different_domains_do_not_interfere(self):
        """Calls to different domains should each be independent."""
        wait_for_domain("https://alpha.com/page")
        start = time.monotonic()
        wait_for_domain("https://beta.com/page")
        elapsed = time.monotonic() - start
        self.assertLess(elapsed, 0.05)

    @override_settings(CRAWLER={"POLITENESS_DELAY": 0})
    def test_zero_delay_skips_sleep(self):
        """A politeness delay of 0 disables throttling entirely."""
        wait_for_domain("https://example.com/page1")
        start = time.monotonic()
        wait_for_domain("https://example.com/page2")
        elapsed = time.monotonic() - start
        self.assertLess(elapsed, 0.05)

    def test_domain_extracted_from_url(self):
        """Paths and query strings are ignored; only the netloc is tracked."""
        wait_for_domain("https://example.com/a?x=1")
        self.assertIn("example.com", _state)

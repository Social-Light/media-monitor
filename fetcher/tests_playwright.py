"""
fetcher/tests_playwright.py

Tests for:
  fetcher/playwright_fetcher.py — render_page()
  fetcher/services.py fetch_page() — Playwright branch

Playwright and all browser I/O are mocked throughout; no real browser
is launched during the test run.
"""
from unittest.mock import MagicMock, call, patch

from django.test import TestCase

from core.models import URLStatusChoices
from discovery.models import DiscoveredURL, SeedSource, SourceType
from fetcher.models import FetchedPage
from fetcher.playwright_fetcher import render_page


# ── Fixtures ──────────────────────────────────────────────────────────────────

_counter = 0


def _url():
    global _counter
    _counter += 1
    return f"https://example.com/article/{_counter}"


def make_seed(use_playwright=False) -> SeedSource:
    seed, _ = SeedSource.objects.get_or_create(
        url="https://example.com/feed",
        defaults={
            "name": "Test Seed",
            "source_type": SourceType.RSS,
            "use_playwright": use_playwright,
        },
    )
    if seed.use_playwright != use_playwright:
        seed.use_playwright = use_playwright
        seed.save(update_fields=["use_playwright"])
    return seed


def make_du(use_playwright=False) -> DiscoveredURL:
    return DiscoveredURL.objects.create(
        seed=make_seed(use_playwright=use_playwright),
        url=_url(),
        title="Test Article",
    )


RENDERED_HTML = "<html><body><h1>JS-rendered content</h1></body></html>"


def _mock_sync_playwright(html=RENDERED_HTML, raise_on_goto=None):
    """
    Build a mock for sync_playwright() context manager that returns html
    from page.content(), or raises raise_on_goto from page.goto().
    """
    mock_page = MagicMock()
    mock_page.content.return_value = html
    if raise_on_goto:
        mock_page.goto.side_effect = raise_on_goto

    mock_browser = MagicMock()
    mock_browser.new_page.return_value = mock_page

    mock_playwright = MagicMock()
    mock_playwright.chromium.launch.return_value = mock_browser

    mock_ctx = MagicMock()
    mock_ctx.__enter__ = MagicMock(return_value=mock_playwright)
    mock_ctx.__exit__ = MagicMock(return_value=False)

    return mock_ctx, mock_page, mock_browser


# ── render_page ───────────────────────────────────────────────────────────────

class RenderPageTests(TestCase):

    def _call(self, url="https://example.com/article/1",
              timeout_ms=30000, html=RENDERED_HTML, raise_on_goto=None):
        mock_ctx, mock_page, mock_browser = _mock_sync_playwright(
            html=html, raise_on_goto=raise_on_goto
        )
        with patch("fetcher.playwright_fetcher.sync_playwright", return_value=mock_ctx):
            return render_page(url, timeout_ms=timeout_ms)

    def test_returns_rendered_html(self):
        result = self._call()
        self.assertEqual(result, RENDERED_HTML)

    def test_passes_url_to_goto(self):
        mock_ctx, mock_page, _ = _mock_sync_playwright()
        url = "https://example.com/js-page"
        with patch("fetcher.playwright_fetcher.sync_playwright", return_value=mock_ctx):
            render_page(url)
        mock_page.goto.assert_called_once()
        args, kwargs = mock_page.goto.call_args
        self.assertEqual(args[0], url)

    def test_passes_timeout_to_goto(self):
        mock_ctx, mock_page, _ = _mock_sync_playwright()
        with patch("fetcher.playwright_fetcher.sync_playwright", return_value=mock_ctx):
            render_page("https://example.com/", timeout_ms=15000)
        _, kwargs = mock_page.goto.call_args
        self.assertEqual(kwargs["timeout"], 15000)

    def test_uses_networkidle_wait(self):
        mock_ctx, mock_page, _ = _mock_sync_playwright()
        with patch("fetcher.playwright_fetcher.sync_playwright", return_value=mock_ctx):
            render_page("https://example.com/")
        _, kwargs = mock_page.goto.call_args
        self.assertEqual(kwargs["wait_until"], "networkidle")

    def test_launches_chromium_headless(self):
        mock_ctx, _, mock_browser = _mock_sync_playwright()
        mock_playwright = mock_ctx.__enter__.return_value
        with patch("fetcher.playwright_fetcher.sync_playwright", return_value=mock_ctx):
            render_page("https://example.com/")
        mock_playwright.chromium.launch.assert_called_once_with(headless=True)

    def test_browser_closed_on_success(self):
        mock_ctx, _, mock_browser = _mock_sync_playwright()
        with patch("fetcher.playwright_fetcher.sync_playwright", return_value=mock_ctx):
            render_page("https://example.com/")
        mock_browser.close.assert_called_once()

    def test_browser_closed_on_goto_failure(self):
        mock_ctx, _, mock_browser = _mock_sync_playwright(
            raise_on_goto=Exception("Navigation failed")
        )
        with patch("fetcher.playwright_fetcher.sync_playwright", return_value=mock_ctx):
            with self.assertRaises(Exception):
                render_page("https://example.com/")
        mock_browser.close.assert_called_once()

    def test_raises_on_navigation_error(self):
        mock_ctx, _, _ = _mock_sync_playwright(
            raise_on_goto=Exception("net::ERR_NAME_NOT_RESOLVED")
        )
        with patch("fetcher.playwright_fetcher.sync_playwright", return_value=mock_ctx):
            with self.assertRaises(Exception):
                render_page("https://nonexistent.invalid/")


# ── fetch_page — Playwright branch ───────────────────────────────────────────

class FetchPagePlaywrightTests(TestCase):
    """
    Verify that fetch_page() uses render_page() when seed.use_playwright=True
    and falls back to requests when False. All Playwright I/O is mocked.
    """

    def _fetch(self, use_playwright=True, rendered_html=RENDERED_HTML,
               render_raises=None):
        du = make_du(use_playwright=use_playwright)
        if render_raises:
            render_mock = patch(
                "fetcher.services.render_page", side_effect=render_raises
            )
        else:
            render_mock = patch(
                "fetcher.services.render_page", return_value=rendered_html
            )
        with render_mock:
            from fetcher.services import fetch_page
            return fetch_page(du), du

    def test_playwright_seed_calls_render_page(self):
        with patch("fetcher.services.render_page", return_value=RENDERED_HTML) as mock_rp:
            du = make_du(use_playwright=True)
            from fetcher.services import fetch_page
            fetch_page(du)
        mock_rp.assert_called_once()

    def test_playwright_seed_does_not_call_get_session(self):
        with patch("fetcher.services.render_page", return_value=RENDERED_HTML), \
             patch("fetcher.services.get_session") as mock_session:
            du = make_du(use_playwright=True)
            from fetcher.services import fetch_page
            fetch_page(du)
        mock_session.assert_not_called()

    def test_playwright_result_stored_as_fetched_page(self):
        result, du = self._fetch()
        self.assertIsInstance(result, FetchedPage)
        self.assertEqual(result.raw_html, RENDERED_HTML)

    def test_playwright_status_code_is_200(self):
        result, _ = self._fetch()
        self.assertEqual(result.status_code, 200)

    def test_playwright_encoding_is_utf8(self):
        result, _ = self._fetch()
        self.assertEqual(result.encoding, "utf-8")

    def test_playwright_marks_url_as_fetched(self):
        _, du = self._fetch()
        du.refresh_from_db()
        self.assertEqual(du.status, URLStatusChoices.FETCHED)

    def test_playwright_failure_returns_none(self):
        result, _ = self._fetch(render_raises=Exception("Navigation failed"))
        self.assertIsNone(result)

    def test_playwright_failure_marks_url_as_failed(self):
        _, du = self._fetch(render_raises=Exception("Navigation failed"))
        du.refresh_from_db()
        self.assertEqual(du.status, URLStatusChoices.FAILED)

    def test_playwright_failure_stores_error_message(self):
        _, du = self._fetch(render_raises=Exception("net::ERR_NAME_NOT_RESOLVED"))
        du.refresh_from_db()
        self.assertIn("ERR_NAME_NOT_RESOLVED", du.error_message)

    def test_non_playwright_seed_uses_requests(self):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text        = "<html>static</html>"
        mock_response.encoding    = "utf-8"
        mock_response.headers     = {"Content-Type": "text/html"}
        mock_response.ok          = True

        with patch("fetcher.services.get_session") as mock_session, \
             patch("fetcher.services.render_page") as mock_rp:
            mock_session.return_value.get.return_value = mock_response
            du = make_du(use_playwright=False)
            from fetcher.services import fetch_page
            fetch_page(du)

        mock_rp.assert_not_called()
        mock_session.assert_called_once()

    def test_timeout_passed_to_render_page(self):
        from django.test import override_settings
        du = make_du(use_playwright=True)
        with patch("fetcher.services.render_page", return_value=RENDERED_HTML) as mock_rp, \
             override_settings(CRAWLER={"REQUEST_TIMEOUT": 20}):
            from fetcher.services import fetch_page
            fetch_page(du)
        args, kwargs = mock_rp.call_args
        timeout_ms = kwargs.get("timeout_ms", args[1] if len(args) > 1 else None)
        self.assertEqual(timeout_ms, 20 * 1000)


# ── SeedSource.use_playwright field ──────────────────────────────────────────

class SeedSourcePlaywrightFieldTests(TestCase):

    def test_default_is_false(self):
        seed = SeedSource.objects.create(
            name="Test", url="https://test.com/feed", source_type=SourceType.RSS
        )
        self.assertFalse(seed.use_playwright)

    def test_can_set_to_true(self):
        seed = SeedSource.objects.create(
            name="JS Site", url="https://jssite.com/feed",
            source_type=SourceType.RSS, use_playwright=True,
        )
        seed.refresh_from_db()
        self.assertTrue(seed.use_playwright)

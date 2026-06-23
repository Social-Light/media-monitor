"""
fetcher/tests_news_filter.py

Tests for the news/editorial classifier (fetcher/news_filter.py) used to gate
the platform bridge: only genuine, current-month news/blog/press coverage is
pushed; corporate product/marketing/utility pages are skipped.
"""
from datetime import timedelta
from types import SimpleNamespace

from django.test import TestCase
from django.utils import timezone

from fetcher.news_filter import is_news_article


def _article(url="https://news.example.com/news/story-1", published_at="now",
             body_text="word " * 200):
    """Lightweight stub exposing only the attributes is_news_article reads."""
    if published_at == "now":
        published_at = timezone.now()
    return SimpleNamespace(url=url, published_at=published_at, body_text=body_text)


class IsNewsArticleTests(TestCase):

    # ── Accepted ────────────────────────────────────────────────────────────
    def test_news_path_current_month_accepted(self):
        self.assertTrue(is_news_article(_article(
            url="https://www.mmegi.bw/news/debswana-record-output")))

    def test_blog_path_accepted(self):
        self.assertTrue(is_news_article(_article(
            url="https://www.sc.com/bw/blog/market-outlook")))

    def test_press_release_accepted(self):
        self.assertTrue(is_news_article(_article(
            url="https://www.sc.com/bw/press-releases/q2-results")))

    def test_newspaper_article_no_news_path_but_long_body_accepted(self):
        # Newspaper article URL with a long body and a current-month date.
        self.assertTrue(is_news_article(_article(
            url="https://www.thevoicebw.com/2026/06/18/banking-shake-up",
            body_text="word " * 300)))

    def test_news_path_overrides_product_segment(self):
        # A loan story published under /news/ is still news.
        self.assertTrue(is_news_article(_article(
            url="https://www.sc.com/bw/news/new-loan-launch")))

    # ── Rejected: product / utility pages ───────────────────────────────────
    def test_product_page_rejected(self):
        self.assertFalse(is_news_article(_article(
            url="https://www.sc.com/bw/personal/loans/personal-loan",
            published_at=None)))

    def test_contact_page_rejected(self):
        self.assertFalse(is_news_article(_article(
            url="https://botswana.accessbankplc.com/contact", published_at=None)))

    def test_accounts_page_rejected_even_with_date(self):
        # Even if a product page somehow carries a current date, it's rejected.
        self.assertFalse(is_news_article(_article(
            url="https://www.sc.com/bw/accounts/savings")))

    # ── Rejected: date requirements ─────────────────────────────────────────
    def test_no_date_rejected(self):
        self.assertFalse(is_news_article(_article(
            url="https://www.mmegi.bw/news/some-story", published_at=None)))

    def test_old_date_rejected(self):
        old = timezone.now() - timedelta(days=70)
        self.assertFalse(is_news_article(_article(
            url="https://www.mmegi.bw/news/old-story", published_at=old)))

    # ── Rejected: thin non-news pages ───────────────────────────────────────
    def test_thin_body_no_news_path_rejected(self):
        self.assertFalse(is_news_article(_article(
            url="https://www.sc.com/bw/landing", body_text="Welcome to our site")))

    def test_error_returns_false(self):
        # A stub missing attributes triggers the defensive except → False.
        self.assertFalse(is_news_article(object()))

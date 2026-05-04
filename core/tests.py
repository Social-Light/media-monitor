"""
Tests for core models and utilities.
Run with: python manage.py test core
"""
from django.test import TestCase
from core.utils import normalize_url, get_domain, same_domain, is_valid_url


class NormalizeURLTests(TestCase):

    def test_lowercases_scheme_and_host(self):
        result = normalize_url("HTTPS://Example.COM/Path")
        self.assertEqual(result, "https://example.com/Path")

    def test_strips_fragment(self):
        result = normalize_url("https://example.com/page#section")
        self.assertEqual(result, "https://example.com/page")

    def test_resolves_relative_url(self):
        result = normalize_url("/news/story", "https://example.com/home")
        self.assertEqual(result, "https://example.com/news/story")

    def test_absolute_url_unchanged(self):
        url = "https://example.com/article/123"
        self.assertEqual(normalize_url(url), url)


class GetDomainTests(TestCase):

    def test_extracts_domain(self):
        self.assertEqual(get_domain("https://www.miningweekly.com/article/1"), "www.miningweekly.com")

    def test_lowercases_domain(self):
        self.assertEqual(get_domain("https://Example.COM/"), "example.com")
class SameDomainTests(TestCase):

    def test_same_domain_returns_true(self):
        self.assertTrue(same_domain("https://example.com/a", "https://example.com/b"))

    def test_different_domain_returns_false(self):
        self.assertFalse(same_domain("https://example.com/a", "https://other.com/b"))


class IsValidURLTests(TestCase):

    def test_valid_http_url(self):
        self.assertTrue(is_valid_url("http://example.com/page"))

    def test_valid_https_url(self):
        self.assertTrue(is_valid_url("https://example.com/page"))

    def test_rejects_empty_string(self):
        self.assertFalse(is_valid_url(""))

    def test_rejects_mailto(self):
        self.assertFalse(is_valid_url("mailto:user@example.com"))

    def test_rejects_javascript(self):
        self.assertFalse(is_valid_url("javascript:void(0)"))

    def test_rejects_relative_path(self):
        self.assertFalse(is_valid_url("/relative/path"))

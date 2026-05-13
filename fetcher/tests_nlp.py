"""
fetcher/tests_nlp.py

Tests for fetcher/nlp.py:
  extract_entities   — spaCy NER (model mocked)
  analyse_sentiment  — VADER (analyzer mocked)
  run_nlp            — combined (both mocked)

Plus integration tests verifying that parse_page() merges NLP results
into ParsedArticle.signals and survives NLP failures.
"""
from unittest.mock import MagicMock, patch

from django.test import TestCase

from core.models import URLStatusChoices
from discovery.models import DiscoveredURL, SeedSource, SourceType
from fetcher.models import FetchedPage, ParsedArticle
from fetcher.nlp import analyse_sentiment, extract_entities, run_nlp


# ── Helpers ───────────────────────────────────────────────────────────────────

_counter = 0


def _url():
    global _counter
    _counter += 1
    return f"https://example.com/article/{_counter}"


def make_seed() -> SeedSource:
    seed, _ = SeedSource.objects.get_or_create(
        url="https://example.com/feed",
        defaults={"name": "Test Seed", "source_type": SourceType.RSS},
    )
    return seed


def make_fetched_page() -> FetchedPage:
    du = DiscoveredURL.objects.create(seed=make_seed(), url=_url(), title="Test")
    return FetchedPage.objects.create(
        discovered_url=du,
        status_code=200,
        content_type="text/html",
        encoding="utf-8",
        raw_html="<html><body><p>body</p></body></html>",
        fetch_duration_ms=100,
    )


def _mock_spacy_doc(entity_tuples):
    """Build a mock spaCy doc with the given (text, label_) entity tuples."""
    ents = [MagicMock(text=text, label_=label) for text, label in entity_tuples]
    doc = MagicMock()
    doc.ents = ents
    nlp = MagicMock(return_value=doc)
    return nlp


def _mock_vader(compound: float):
    vader = MagicMock()
    vader.polarity_scores.return_value = {'compound': compound}
    return vader


# ── extract_entities ──────────────────────────────────────────────────────────

class ExtractEntitiesTests(TestCase):

    def _call(self, text, entity_tuples):
        with patch('fetcher.nlp._get_nlp', return_value=_mock_spacy_doc(entity_tuples)):
            return extract_entities(text)

    def test_empty_text_returns_empty_without_calling_nlp(self):
        with patch('fetcher.nlp._get_nlp') as mock_get:
            result = extract_entities("")
        mock_get.assert_not_called()
        self.assertEqual(result, {})

    def test_groups_entities_by_label(self):
        result = self._call("text", [("Anglo American", "ORG"), ("Botswana", "GPE")])
        self.assertIn("ORG", result)
        self.assertIn("GPE", result)
        self.assertIn("Anglo American", result["ORG"])
        self.assertIn("Botswana", result["GPE"])

    def test_deduplicates_within_label(self):
        result = self._call("text", [("Anglo American", "ORG"), ("Anglo American", "ORG")])
        self.assertEqual(result["ORG"].count("Anglo American"), 1)

    def test_multiple_entities_under_same_label(self):
        result = self._call("text", [("Anglo American", "ORG"), ("De Beers", "ORG")])
        self.assertEqual(len(result["ORG"]), 2)
        self.assertIn("De Beers", result["ORG"])

    def test_strips_whitespace_from_entity_text(self):
        result = self._call("text", [("  Anglo American  ", "ORG")])
        self.assertIn("Anglo American", result["ORG"])

    def test_excludes_blank_entity_text(self):
        result = self._call("text", [("   ", "ORG")])
        self.assertNotIn("ORG", result)

    def test_returns_empty_when_doc_has_no_entities(self):
        result = self._call("plain text", [])
        self.assertEqual(result, {})

    def test_multiple_labels_returned(self):
        result = self._call("text", [
            ("Anglo American", "ORG"),
            ("Botswana", "GPE"),
            ("John Smith", "PERSON"),
        ])
        self.assertEqual(set(result.keys()), {"ORG", "GPE", "PERSON"})


# ── analyse_sentiment ─────────────────────────────────────────────────────────

class AnalyseSentimentTests(TestCase):

    def _call(self, compound: float) -> str:
        with patch('fetcher.nlp._get_vader', return_value=_mock_vader(compound)):
            return analyse_sentiment("any text")

    def test_empty_text_returns_neutral_without_calling_vader(self):
        with patch('fetcher.nlp._get_vader') as mock_get:
            result = analyse_sentiment("")
        mock_get.assert_not_called()
        self.assertEqual(result, "neutral")

    def test_high_compound_is_positive(self):
        self.assertEqual(self._call(0.8), "positive")

    def test_low_compound_is_negative(self):
        self.assertEqual(self._call(-0.8), "negative")

    def test_zero_compound_is_neutral(self):
        self.assertEqual(self._call(0.0), "neutral")

    def test_boundary_positive_threshold(self):
        self.assertEqual(self._call(0.05), "positive")

    def test_boundary_negative_threshold(self):
        self.assertEqual(self._call(-0.05), "negative")

    def test_just_below_positive_threshold_is_neutral(self):
        self.assertEqual(self._call(0.04), "neutral")

    def test_just_above_negative_threshold_is_neutral(self):
        self.assertEqual(self._call(-0.04), "neutral")


# ── run_nlp ───────────────────────────────────────────────────────────────────

class RunNlpTests(TestCase):

    def _call(self, entities=None, sentiment="neutral"):
        with patch('fetcher.nlp.extract_entities', return_value=entities or {}), \
             patch('fetcher.nlp.analyse_sentiment', return_value=sentiment):
            return run_nlp("some text")

    def test_includes_entities_when_found(self):
        result = self._call(entities={"ORG": ["Anglo American"]})
        self.assertEqual(result["entities"], {"ORG": ["Anglo American"]})

    def test_omits_entities_key_when_empty(self):
        result = self._call(entities={})
        self.assertNotIn("entities", result)

    def test_always_includes_sentiment(self):
        result = self._call(sentiment="positive")
        self.assertEqual(result["sentiment"], "positive")

    def test_sentiment_negative(self):
        result = self._call(sentiment="negative")
        self.assertEqual(result["sentiment"], "negative")

    def test_returns_dict(self):
        result = self._call()
        self.assertIsInstance(result, dict)

    def test_survives_extract_entities_exception(self):
        with patch('fetcher.nlp.extract_entities', side_effect=RuntimeError("NER crash")), \
             patch('fetcher.nlp.analyse_sentiment', return_value="neutral"):
            result = run_nlp("text")
        # sentiment still populated; no exception raised
        self.assertEqual(result.get("sentiment"), "neutral")
        self.assertNotIn("entities", result)

    def test_survives_analyse_sentiment_exception(self):
        with patch('fetcher.nlp.extract_entities', return_value={"ORG": ["X"]}), \
             patch('fetcher.nlp.analyse_sentiment', side_effect=RuntimeError("VADER crash")):
            result = run_nlp("text")
        self.assertIsInstance(result, dict)
        self.assertNotIn("sentiment", result)

    def test_empty_text_returns_neutral_sentiment(self):
        # run_nlp delegates empty-text handling to its sub-functions
        result = run_nlp("")
        self.assertEqual(result.get("sentiment"), "neutral")
        self.assertNotIn("entities", result)


# ── Integration: parse_page merges NLP into signals ───────────────────────────

class ParsePageNlpIntegrationTests(TestCase):

    NLP_DATA = {"entities": {"ORG": ["Anglo American"], "GPE": ["Botswana"]},
                "sentiment": "positive"}

    def _mock_newspaper_article(self):
        mock = MagicMock()
        mock.title            = "Gold miners report record profits"
        mock.text             = "Gold surged today. " * 20
        mock.authors          = ["Jane Smith"]
        mock.publish_date     = None
        mock.meta_lang        = "en"
        mock.tags             = set()
        mock.meta_keywords    = "gold, mining"
        mock.meta_description = "Gold surged."
        return mock

    def _parse(self, nlp_side_effect=None):
        fp = make_fetched_page()
        mock_article = self._mock_newspaper_article()
        nlp_patch = (
            patch('fetcher.services.run_nlp', side_effect=nlp_side_effect)
            if nlp_side_effect
            else patch('fetcher.services.run_nlp', return_value=self.NLP_DATA)
        )
        with patch('fetcher.services.newspaper.Article', return_value=mock_article), \
             nlp_patch:
            from fetcher.services import parse_page
            return parse_page(fp)

    def test_signals_contain_sentiment(self):
        result = self._parse()
        self.assertEqual(result.signals["sentiment"], "positive")

    def test_signals_contain_entities(self):
        result = self._parse()
        self.assertIn("entities", result.signals)
        self.assertIn("ORG", result.signals["entities"])

    def test_signals_retain_existing_keywords(self):
        result = self._parse()
        self.assertIn("keywords", result.signals)

    def test_parse_succeeds_when_run_nlp_raises(self):
        result = self._parse(nlp_side_effect=RuntimeError("NLP exploded"))
        self.assertIsNotNone(result)
        self.assertIsInstance(result, ParsedArticle)

    def test_signals_still_have_keywords_when_nlp_fails(self):
        result = self._parse(nlp_side_effect=RuntimeError("NLP exploded"))
        # keywords come from _collect_signals, which runs before run_nlp
        self.assertIn("keywords", result.signals)

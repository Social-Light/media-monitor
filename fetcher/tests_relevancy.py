"""
fetcher/tests_relevancy.py

Unit tests for the vendored relevancy scorer (fetcher/relevancy.py), mirroring the
platform's 0–100 scoring so crawler coverage ranks like platform-ingested coverage.
No DB needed — uses lightweight keyword/competitor stand-ins.
"""
from django.test import SimpleTestCase

from fetcher.relevancy import compute_relevancy


class _KW:
    def __init__(self, keyword, category="brand"):
        self.keyword = keyword
        self.category = category


class _Comp:
    def __init__(self, *terms):
        self._terms = list(terms)

    def match_terms(self):
        return self._terms


class ComputeRelevancyTests(SimpleTestCase):

    def test_brand_keyword_scores_50(self):
        self.assertEqual(compute_relevancy("Debswana wins", "", keywords=[_KW("Debswana")]), 50.0)

    def test_personnel_keyword_scores_30(self):
        score = compute_relevancy("News about Steven Bogatsu", "",
                                  keywords=[_KW("Steven Bogatsu", "personnel")])
        self.assertEqual(score, 30.0)

    def test_unknown_category_uses_default_weight_20(self):
        self.assertEqual(compute_relevancy("foo bar", "", keywords=[_KW("foo", "misc")]), 20.0)

    def test_repeat_bonus(self):
        # brand=50, twice → 50 + 0.1*50 = 55
        self.assertEqual(compute_relevancy("Debswana and Debswana", "", keywords=[_KW("Debswana")]), 55.0)

    def test_scores_headline_and_summary(self):
        self.assertEqual(compute_relevancy("Mining update", "Debswana grows", keywords=[_KW("Debswana")]), 50.0)

    def test_competitor_term_scores_30(self):
        self.assertEqual(compute_relevancy("Lucara news", "", competitors=[_Comp("Lucara")]), 30.0)

    def test_competitor_alias_matches(self):
        self.assertEqual(compute_relevancy("LDC reports", "", competitors=[_Comp("Lucara Diamond Corp", "LDC")]), 30.0)

    def test_word_boundary_no_partial_match(self):
        # "BCL" should not match inside "BCLs"
        self.assertEqual(compute_relevancy("BCLs gold report", "", keywords=[_KW("BCL")]), 0.0)

    def test_keyword_wins_over_duplicate_competitor_term(self):
        # 'Acme' tracked as both brand keyword (50) and competitor (30) → counted once at 50
        score = compute_relevancy("Acme update", "", keywords=[_KW("Acme")], competitors=[_Comp("Acme")])
        self.assertEqual(score, 50.0)

    def test_capped_at_100(self):
        kws = [_KW("alpha"), _KW("beta"), _KW("gamma")]  # 3 × 50 = 150 → capped
        self.assertEqual(compute_relevancy("alpha beta gamma", "", keywords=kws), 100.0)

    def test_no_tracked_terms_scores_zero(self):
        self.assertEqual(compute_relevancy("anything", "here"), 0.0)

    def test_no_match_scores_zero(self):
        self.assertEqual(compute_relevancy("gold news", "", keywords=[_KW("copper")]), 0.0)

    def test_empty_text_scores_zero(self):
        self.assertEqual(compute_relevancy("", "", keywords=[_KW("Debswana")]), 0.0)

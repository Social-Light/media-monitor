"""
fetcher/relevancy.py
─────────────────────
Relevancy scoring for captured coverage — a VENDORED COPY of the platform's
`monitor/relevancy.py` (socialmonitor). Kept identical so coverage the crawler
writes into monitor_onlinearticle scores on the same 0–100 scale as coverage the
platform ingests itself (CSV / webhook / manual). If the platform changes its
scoring, update this file to match.

Score = sum of per-term weights over the headline + summary (word-boundary,
case-insensitive), with a small repeat bonus, capped at 100:
  brand keyword      = 50
  personnel/campaign = 30   (DEFAULT_WEIGHT = 20 for unknown categories)
  tracked competitor = 30   (name + aliases, via Competitor.match_terms())
"""
import re

CATEGORY_WEIGHTS = {
    "brand": 50,
    "personnel": 30,
    "campaign": 30,
}
DEFAULT_WEIGHT = 20
COMPETITOR_WEIGHT = 30
REPEAT_BONUS = 0.1
MAX_SCORE = 100.0


def _scoring_terms(keywords, competitors):
    """Yield (lowercased term, weight) for every tracked keyword and competitor.

    Competitors contribute their name plus aliases (Competitor.match_terms()).
    De-duplicated case-insensitively; a keyword's weight wins over a competitor's.
    """
    seen = set()
    for kw in keywords or []:
        term = (kw.keyword or "").strip().lower()
        if term and term not in seen:
            seen.add(term)
            yield term, CATEGORY_WEIGHTS.get(kw.category, DEFAULT_WEIGHT)
    for comp in competitors or []:
        for raw in comp.match_terms():
            term = raw.strip().lower()
            if term and term not in seen:
                seen.add(term)
                yield term, COMPETITOR_WEIGHT


def compute_relevancy(headline, summary="", keywords=None, org=None, competitors=None):
    """Return a 0–100 relevancy score for the given text.

    Pass ``keywords`` (Keyword instances) and/or ``competitors`` (Competitor
    instances), or ``org`` to load both. With nothing tracked the score is 0.
    """
    if keywords is None:
        keywords = list(org.keywords.all()) if org is not None else []
    if competitors is None:
        competitors = list(org.competitors.all()) if org is not None else []
    if not keywords and not competitors:
        return 0.0

    text = f"{headline or ''} {summary or ''}".lower()
    if not text.strip():
        return 0.0

    score = 0.0
    for term, weight in _scoring_terms(keywords, competitors):
        occurrences = len(re.findall(r"\b" + re.escape(term) + r"\b", text))
        if occurrences:
            score += weight + (occurrences - 1) * weight * REPEAT_BONUS

    return round(min(score, MAX_SCORE), 2)

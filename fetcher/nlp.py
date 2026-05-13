import logging

import spacy
from nltk.sentiment.vader import SentimentIntensityAnalyzer

logger = logging.getLogger(__name__)

# Module-level cache — each worker process loads the models once.
_nlp = None
_vader = None


def _get_nlp() -> spacy.Language:
    global _nlp
    if _nlp is None:
        _nlp = spacy.load('en_core_web_sm')
    return _nlp


def _get_vader() -> SentimentIntensityAnalyzer:
    global _vader
    if _vader is None:
        _vader = SentimentIntensityAnalyzer()
    return _vader


def extract_entities(text: str) -> dict:
    """
    Run spaCy NER on text.

    Returns a dict mapping entity label → deduplicated list of entity strings,
    e.g. {"ORG": ["Anglo American"], "GPE": ["Botswana"]}.
    Returns {} on empty input. Never raises.
    """
    if not text:
        return {}

    nlp = _get_nlp()
    doc = nlp(text)

    entities: dict = {}
    for ent in doc.ents:
        value = ent.text.strip()
        if not value:
            continue
        label = ent.label_
        bucket = entities.setdefault(label, [])
        if value not in bucket:
            bucket.append(value)

    return entities


def analyse_sentiment(text: str) -> str:
    """
    Return "positive", "negative", or "neutral" using VADER compound score.

    Thresholds: compound >= 0.05 → positive, <= -0.05 → negative.
    Returns "neutral" on empty input. Never raises.
    """
    if not text:
        return "neutral"

    vader = _get_vader()
    compound = vader.polarity_scores(text)['compound']

    if compound >= 0.05:
        return "positive"
    if compound <= -0.05:
        return "negative"
    return "neutral"


def run_nlp(text: str) -> dict:
    """
    Run NER and sentiment analysis, returning a signals-compatible dict:
      {
        "entities":  {"ORG": [...], "GPE": [...]},   # omitted when empty
        "sentiment": "positive" | "negative" | "neutral",
      }
    Never raises — individual failures are logged and skipped.
    """
    result: dict = {}

    try:
        entities = extract_entities(text)
        if entities:
            result['entities'] = entities
    except Exception as exc:
        logger.warning("Entity extraction failed: %s", exc)

    try:
        result['sentiment'] = analyse_sentiment(text)
    except Exception as exc:
        logger.warning("Sentiment analysis failed: %s", exc)

    return result

import logging

from django.conf import settings as django_settings
from elasticsearch import Elasticsearch, NotFoundError
from elasticsearch.helpers import bulk as es_bulk

from fetcher.models import ParsedArticle

logger = logging.getLogger(__name__)

# Lazy singleton — one client per worker process.
_client: Elasticsearch | None = None

INDEX_MAPPING = {
    "properties": {
        "title":         {"type": "text",    "analyzer": "english"},
        "body_text":     {"type": "text",    "analyzer": "english"},
        "summary":       {"type": "text",    "analyzer": "english"},
        "author":        {"type": "keyword"},
        "source_domain": {"type": "keyword"},
        "language":      {"type": "keyword"},
        "published_at":  {"type": "date"},
        "tags":          {"type": "keyword"},
        "sentiment":     {"type": "keyword"},
        "url":           {"type": "keyword"},
        "is_duplicate":  {"type": "boolean"},
        "created_at":    {"type": "date"},
    }
}


def get_client() -> Elasticsearch:
    global _client
    if _client is None:
        cfg = django_settings.ELASTICSEARCH
        _client = Elasticsearch(
            cfg["HOSTS"],
            request_timeout=cfg.get("TIMEOUT", 10),
        )
    return _client


def _index() -> str:
    return django_settings.ELASTICSEARCH["INDEX"]


# ── Document serialisation ─────────────────────────────────────────────────────

def article_to_doc(article: ParsedArticle) -> dict:
    """Serialise a ParsedArticle to an Elasticsearch document dict."""
    return {
        "title":         article.title,
        "body_text":     article.body_text,
        "summary":       article.summary,
        "author":        article.author,
        "source_domain": article.source_domain,
        "language":      article.language,
        "published_at":  article.published_at.isoformat() if article.published_at else None,
        "tags":          article.tags,
        "sentiment":     article.signals.get("sentiment", ""),
        "url":           article.url,
        "is_duplicate":  article.is_duplicate,
        "created_at":    article.created_at.isoformat(),
    }


# ── Index operations ──────────────────────────────────────────────────────────

def index_article(article: ParsedArticle) -> None:
    """Index a single ParsedArticle. Document ID = str(article.pk)."""
    get_client().index(
        index=_index(),
        id=str(article.pk),
        document=article_to_doc(article),
    )
    logger.debug("Indexed article %d: %s", article.pk, article.title[:60])


def delete_article(article_id: int) -> None:
    """Remove an article from the index. Silently ignores 404."""
    try:
        get_client().delete(index=_index(), id=str(article_id))
    except NotFoundError:
        pass
    logger.debug("Deleted article %d from index", article_id)


def ensure_index() -> None:
    """Create the articles index with its mapping if it does not already exist."""
    client = get_client()
    idx    = _index()
    if not client.indices.exists(index=idx):
        client.indices.create(index=idx, mappings=INDEX_MAPPING)
        logger.info("Created Elasticsearch index: %s", idx)
    else:
        logger.debug("Index already exists: %s", idx)


def drop_index() -> None:
    """Delete the articles index if it exists. Used for --reset."""
    client = get_client()
    idx    = _index()
    if client.indices.exists(index=idx):
        client.indices.delete(index=idx)
        logger.info("Deleted Elasticsearch index: %s", idx)


def bulk_index_articles(articles, chunk_size: int = 100) -> tuple[int, int]:
    """
    Bulk-index an iterable of ParsedArticle instances.
    Returns (successes, errors) as integer counts.
    """
    idx = _index()

    def _actions():
        for article in articles:
            yield {
                "_index":  idx,
                "_id":     str(article.pk),
                "_source": article_to_doc(article),
            }

    successes, errors = es_bulk(
        get_client(),
        _actions(),
        chunk_size=chunk_size,
        raise_on_error=False,
        stats_only=True,
    )
    if errors:
        logger.warning("Bulk index completed with %d errors", errors)
    return successes, errors


# ── Search ────────────────────────────────────────────────────────────────────

def search_articles(
    query: str,
    *,
    source_domain: str | None = None,
    language: str | None = None,
    sentiment: str | None = None,
    from_: int = 0,
    size: int = 10,
) -> list[dict]:
    """
    Full-text search over title (^3), body_text, and summary.

    Optional keyword filters: source_domain, language, sentiment.
    Duplicates are always excluded.

    Returns a list of ES hit dicts, each with _id, _score, and _source.
    """
    must: list = [
        {"multi_match": {"query": query, "fields": ["title^3", "body_text", "summary"]}}
        if query else
        {"match_all": {}}
    ]
    filters: list = [{"term": {"is_duplicate": False}}]

    if source_domain:
        filters.append({"term": {"source_domain": source_domain}})
    if language:
        filters.append({"term": {"language": language}})
    if sentiment:
        filters.append({"term": {"sentiment": sentiment}})

    response = get_client().search(
        index=_index(),
        query={"bool": {"must": must, "filter": filters}},
        from_=from_,
        size=size,
    )
    return response["hits"]["hits"]

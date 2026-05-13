import difflib
import hashlib
import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from fetcher.models import ParsedArticle


def normalize_text(text: str) -> str:
    """Lowercase and collapse all whitespace to a single space."""
    return re.sub(r'\s+', ' ', text.lower()).strip()


def compute_content_hash(text: str) -> str:
    """SHA-256 of normalised body text, returned as a 64-char hex string."""
    return hashlib.sha256(normalize_text(text).encode('utf-8')).hexdigest()


def extract_canonical_url(html: str, page_url: str) -> str | None:
    """
    Return the href of <link rel="canonical"> resolved against page_url, or None.
    Never raises — returns None on any parsing error.
    """
    try:
        soup = BeautifulSoup(html, 'lxml')
        tag = soup.find('link', rel='canonical')
        if tag and tag.get('href'):
            href = tag['href'].strip()
            if href:
                return urljoin(page_url, href)
    except Exception:
        pass
    return None


def find_duplicate(
    title: str,
    content_hash: str,
    exclude_id: int | None = None,
) -> ParsedArticle | None:
    """
    Return an existing non-duplicate ParsedArticle that matches, or None.

    Checks in order:
      1. Exact content_hash match (fast index lookup)
      2. Title similarity >= 0.85 via SequenceMatcher (up to 500 candidates)
    """
    qs = ParsedArticle.objects.filter(is_duplicate=False)
    if exclude_id is not None:
        qs = qs.exclude(pk=exclude_id)

    if content_hash:
        match = qs.filter(content_hash=content_hash).first()
        if match:
            return match

    if title:
        title_lower = title.lower()
        candidates = qs.exclude(title='').only('pk', 'title')[:500]
        for candidate in candidates:
            ratio = difflib.SequenceMatcher(
                None, title_lower, candidate.title.lower()
            ).ratio()
            if ratio >= 0.85:
                return candidate

    return None


def check_and_mark_duplicate(article: ParsedArticle) -> bool:
    """
    Populate content_hash and canonical_url on article, then check for a duplicate.

    Saves only the fields that changed. Returns True if a duplicate was found.
    """
    update_fields: list[str] = []

    if article.body_text:
        article.content_hash = compute_content_hash(article.body_text)
        update_fields.append('content_hash')

    try:
        canonical = extract_canonical_url(article.fetched_page.raw_html, article.url)
        if canonical:
            article.canonical_url = canonical
            update_fields.append('canonical_url')
    except Exception:
        pass

    original = find_duplicate(
        article.title, article.content_hash, exclude_id=article.pk
    )
    if original:
        article.is_duplicate = True
        article.duplicate_of = original
        update_fields.extend(['is_duplicate', 'duplicate_of'])

    if update_fields:
        article.save(update_fields=update_fields)

    return original is not None

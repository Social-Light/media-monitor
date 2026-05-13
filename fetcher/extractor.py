from datetime import datetime
from urllib.parse import urlparse

from bs4 import BeautifulSoup
from django.utils.dateparse import parse_datetime

from fetcher.models import ExtractionRule


def get_rule_for_page(page_url: str) -> ExtractionRule | None:
    """Return the first active ExtractionRule matching the URL's domain, or None."""
    domain = urlparse(page_url).netloc
    if not domain:
        return None
    return ExtractionRule.objects.filter(domain=domain, is_active=True).first()


def apply_rule(rule: ExtractionRule, html: str, page_url: str) -> dict:
    """
    Run each non-blank CSS selector from rule against html.

    Returns a dict with a subset of: title, body_text, author, published_at.
    Only keys where a selector matched and yielded non-empty content are included.
    Never raises — selector errors and parse failures are silently skipped.
    """
    out: dict = {}
    try:
        soup = BeautifulSoup(html, 'lxml')
    except Exception:
        return out

    _extract_text(soup, rule.title_selector, 'title', out)
    _extract_body(soup, rule.body_selector, out)
    _extract_author(soup, rule.author_selector, out)
    _extract_date(soup, rule.date_selector, rule.date_format, out)

    return out


# ── Private helpers ───────────────────────────────────────────────────────────

def _extract_text(soup: BeautifulSoup, selector: str, key: str, out: dict) -> None:
    if not selector:
        return
    try:
        el = soup.select_one(selector)
        if el:
            text = el.get_text(strip=True)
            if text:
                out[key] = text
    except Exception:
        pass


def _extract_body(soup: BeautifulSoup, selector: str, out: dict) -> None:
    if not selector:
        return
    try:
        el = soup.select_one(selector)
        if el:
            text = el.get_text(separator='\n', strip=True)
            if text:
                out['body_text'] = text
    except Exception:
        pass


def _extract_author(soup: BeautifulSoup, selector: str, out: dict) -> None:
    if not selector:
        return
    try:
        el = soup.select_one(selector)
        if el:
            text = el.get_text(strip=True)
            if text:
                out['author'] = text[:255]
    except Exception:
        pass


def _extract_date(soup: BeautifulSoup, selector: str, date_format: str, out: dict) -> None:
    if not selector:
        return
    try:
        el = soup.select_one(selector)
        if not el:
            return
        # Prefer machine-readable datetime attribute (e.g. <time datetime="...">)
        raw = el.get('datetime', '').strip() or el.get_text(strip=True)
        if not raw:
            return
        if date_format:
            out['published_at'] = datetime.strptime(raw, date_format)
        else:
            dt = parse_datetime(raw)
            if dt:
                out['published_at'] = dt
    except Exception:
        pass

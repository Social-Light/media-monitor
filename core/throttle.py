"""
core/throttle.py

Per-domain rate limiter used by fetch_page() to honour
settings.CRAWLER["POLITENESS_DELAY"] between requests to the same host.

Thread-safe via a per-domain Lock so concurrent Celery workers on the
same machine don't hammer a single domain simultaneously.
"""
import threading
import time
from urllib.parse import urlparse

from django.conf import settings

_lock  = threading.Lock()
_state: dict[str, float] = {}   # domain → time of last request
_domain_locks: dict[str, threading.Lock] = {}


def _get_domain_lock(domain: str) -> threading.Lock:
    with _lock:
        if domain not in _domain_locks:
            _domain_locks[domain] = threading.Lock()
        return _domain_locks[domain]


def wait_for_domain(url: str) -> None:
    """
    Block until the politeness delay has elapsed since the last request
    to the same domain, then record the current time as the new
    last-request timestamp.

    The delay is read from settings.CRAWLER["POLITENESS_DELAY"] (seconds).
    A delay of 0 disables throttling entirely.
    """
    delay = settings.CRAWLER.get("POLITENESS_DELAY", 1.0)
    if delay <= 0:
        return

    domain = urlparse(url).netloc.lower()
    domain_lock = _get_domain_lock(domain)

    with domain_lock:
        now  = time.monotonic()
        last = _state.get(domain, 0.0)
        gap  = now - last
        if gap < delay:
            time.sleep(delay - gap)
        _state[domain] = time.monotonic()

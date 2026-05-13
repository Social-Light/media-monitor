import logging

from playwright.sync_api import sync_playwright

logger = logging.getLogger(__name__)


def render_page(url: str, timeout_ms: int = 30000) -> str:
    """
    Load url in a headless Chromium browser and return the fully-rendered HTML.

    Waits for 'networkidle' so JS-rendered content is present before capture.
    Raises playwright.sync_api.Error (or subclass) on navigation failure —
    the caller (fetch_page) catches this and marks the URL as FAILED.

    Args:
        url:        The page to render.
        timeout_ms: Navigation timeout in milliseconds (default 30 s).

    Returns:
        Full HTML string of the rendered page, equivalent to document.documentElement.outerHTML.
    """
    logger.info("Playwright rendering: %s", url)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.goto(url, timeout=timeout_ms, wait_until="networkidle")
            html = page.content()
        finally:
            browser.close()

    logger.info("Playwright done: %s (%d chars)", url, len(html))
    return html

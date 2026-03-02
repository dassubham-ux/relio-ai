from __future__ import annotations

import asyncio
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

PATHS_TO_FETCH = ["", "/pricing", "/about", "/blog", "/docs"]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; RelioBot/1.0; +https://github.com/relio-ai)"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}

STRIP_TAGS = {"script", "style", "nav", "footer", "header", "noscript", "aside"}


@dataclass
class FetchedPage:
    url: str
    title: str
    meta_description: str
    text: str


def _extract_text(html: str) -> tuple[str, str, str]:
    """Return (title, meta_description, visible_text) from raw HTML."""
    soup = BeautifulSoup(html, "html.parser")

    title = soup.title.get_text(strip=True) if soup.title else ""

    meta_desc = ""
    meta_tag = soup.find("meta", attrs={"name": "description"})
    if meta_tag and meta_tag.get("content"):
        meta_desc = meta_tag["content"].strip()

    for tag in soup(STRIP_TAGS):
        tag.decompose()

    visible_text = soup.get_text(separator=" ", strip=True)
    # Collapse excessive whitespace
    import re
    visible_text = re.sub(r"\s{3,}", "  ", visible_text)

    return title, meta_desc, visible_text


async def _fetch_one(client: httpx.AsyncClient, url: str) -> FetchedPage | None:
    try:
        resp = await client.get(url, follow_redirects=True, timeout=15)
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        title, meta_desc, text = _extract_text(resp.text)
        return FetchedPage(url=str(resp.url), title=title, meta_description=meta_desc, text=text)
    except Exception:
        return None


async def fetch_website(base_url: str) -> list[FetchedPage]:
    """Fetch homepage + common paths, returning successfully scraped pages."""
    parsed = urlparse(base_url)
    origin = f"{parsed.scheme}://{parsed.netloc}"

    urls = [urljoin(origin, path) for path in PATHS_TO_FETCH]

    async with httpx.AsyncClient(headers=HEADERS) as client:
        tasks = [_fetch_one(client, url) for url in urls]
        results = await asyncio.gather(*tasks)

    pages = [p for p in results if p is not None]
    return pages


def fetch_website_sync(base_url: str) -> list[FetchedPage]:
    """Synchronous wrapper around fetch_website."""
    return asyncio.run(fetch_website(base_url))


def pages_to_text(pages: list[FetchedPage], max_chars: int = 20_000) -> str:
    """Combine all fetched pages into a single trimmed text block."""
    chunks = []
    total = 0
    for page in pages:
        header = f"=== {page.url} ===\nTitle: {page.title}\nMeta: {page.meta_description}\n"
        body = page.text
        chunk = header + body
        if total + len(chunk) > max_chars:
            remaining = max_chars - total
            chunk = chunk[:remaining]
            chunks.append(chunk)
            break
        chunks.append(chunk)
        total += len(chunk)
    return "\n\n".join(chunks)

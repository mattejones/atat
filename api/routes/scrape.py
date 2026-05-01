"""
scrape.py — Scrape a job description from a URL.

Uses httpx for fetching and basic HTML parsing to extract the main text content.
Returns cleaned plain text suitable for the LLM tailoring pipeline.
"""

import re

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/scrape", tags=["scrape"])


class ScrapeResponse(BaseModel):
    url:   str
    text:  str
    title: str = ""


@router.get("")
async def scrape_jd(url: str) -> ScrapeResponse:
    """
    Fetch a URL and extract the main text content.
    Returns cleaned plain text suitable for pasting into the JD field.
    """
    try:
        import httpx
    except ImportError:
        raise HTTPException(
            status_code=500,
            detail="httpx is required for scraping. Run: pip install httpx"
        )

    try:
        from html.parser import HTMLParser
    except ImportError:
        pass

    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    try:
        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=15.0,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                )
            },
        ) as client:
            response = await client.get(url)
            response.raise_for_status()
    except httpx.HTTPStatusError as e:
        raise HTTPException(
            status_code=422,
            detail=f"Could not fetch URL (HTTP {e.response.status_code}): {url}"
        )
    except httpx.RequestError as e:
        raise HTTPException(
            status_code=422,
            detail=f"Could not reach URL: {url}. Error: {str(e)}"
        )

    html = response.text
    title, text = _extract_text(html)

    if len(text.strip()) < 100:
        raise HTTPException(
            status_code=422,
            detail="Could not extract meaningful text from this URL. Try pasting the JD manually."
        )

    return ScrapeResponse(url=url, text=text.strip(), title=title)


def _extract_text(html: str) -> tuple[str, str]:
    """
    Extract clean text from HTML.
    Removes script/style/nav/footer/header tags, collapses whitespace.
    """
    # Extract title
    title_match = re.search(r'<title[^>]*>(.*?)</title>', html, re.IGNORECASE | re.DOTALL)
    title = title_match.group(1).strip() if title_match else ""

    # Remove unwanted tags and their content entirely
    for tag in ["script", "style", "nav", "header", "footer", "noscript",
                "svg", "iframe", "form", "button", "input", "select"]:
        html = re.sub(
            rf'<{tag}[^>]*>.*?</{tag}>',
            ' ', html,
            flags=re.IGNORECASE | re.DOTALL
        )

    # Replace block-level elements with newlines
    html = re.sub(
        r'<(br|p|div|li|h[1-6]|section|article|main)[^>]*>',
        '\n', html,
        flags=re.IGNORECASE
    )

    # Strip remaining tags
    html = re.sub(r'<[^>]+>', ' ', html)

    # Decode common HTML entities
    for entity, char in [
        ('&amp;', '&'), ('&lt;', '<'), ('&gt;', '>'),
        ('&nbsp;', ' '), ('&quot;', '"'), ('&#39;', "'"),
        ('&ndash;', '–'), ('&mdash;', '—'),
    ]:
        html = html.replace(entity, char)

    # Collapse whitespace — preserve paragraph breaks
    lines = [line.strip() for line in html.splitlines()]
    lines = [l for l in lines if l]

    # Remove very short lines (likely navigation artifacts)
    lines = [l for l in lines if len(l) > 20 or l.endswith(('.', ':', '?', '!'))]

    text = '\n'.join(lines)

    # Collapse 3+ consecutive newlines to 2
    text = re.sub(r'\n{3,}', '\n\n', text)

    return title, text

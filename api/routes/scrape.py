"""
scrape.py — Scrape a job description from a URL.

Uses httpx for fetching and basic HTML parsing to extract the main text content.
Returns cleaned plain text suitable for the LLM tailoring pipeline.
"""

import asyncio
import ipaddress
import re
import socket
from urllib.parse import urljoin, urlparse

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/scrape", tags=["scrape"])


async def assert_public_url(url: str) -> None:
    """
    Refuse URLs that resolve to a private, loopback, link-local or otherwise non-public
    address (which includes Tailscale's 100.64.0.0/10). Without this, "fetch this job ad"
    would let anyone signed in read other services on the home network through this server.
    Not a defence against DNS rebinding between this check and the fetch.
    """
    host = urlparse(url).hostname
    if not host:
        raise HTTPException(status_code=422, detail="That doesn't look like a web address.")
    try:
        infos = await asyncio.to_thread(socket.getaddrinfo, host, None)
    except socket.gaierror:
        raise HTTPException(status_code=422, detail=f"Couldn't find {host}. Check the address.")
    for info in infos:
        ip = ipaddress.ip_address(info[4][0].split("%")[0])
        if not ip.is_global:
            raise HTTPException(
                status_code=422,
                detail="That address is on a private network, so ATAT won't fetch it.",
            )


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
        # Redirects are followed by hand so every hop is checked: a public page must not be
        # able to bounce this server onto an address inside the home network.
        async with httpx.AsyncClient(
            follow_redirects=False,
            timeout=15.0,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                )
            },
        ) as client:
            for _hop in range(6):
                await assert_public_url(url)
                response = await client.get(url)
                if response.is_redirect and response.headers.get("location"):
                    url = urljoin(url, response.headers["location"])
                    continue
                break
            else:
                raise HTTPException(status_code=422, detail="Too many redirects.")
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

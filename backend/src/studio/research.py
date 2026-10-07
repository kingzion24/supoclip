"""Fact lookup for Studio scripts.

Searches Wikipedia (free, no key) for the queries the director planned and
returns the lead section of the best matching articles. The director may only
state facts it can ground in these notes or in widely known general knowledge.
Every failure is soft: a video can still be made from the user's brief.
"""

import logging
from typing import Any, Dict, List

import httpx

logger = logging.getLogger(__name__)

WIKIPEDIA_API = "https://{lang}.wikipedia.org/w/api.php"
USER_AGENT = "Katakata-Studio/1.0 (https://github.com/kingzion24/supoclip)"
RESULTS_PER_QUERY = 2
MAX_SOURCES = 8
MAX_EXTRACT_CHARS = 1800
REQUEST_TIMEOUT = 15.0


def parse_wikipedia_response(payload: Dict[str, Any], lang: str = "en") -> List[Dict[str, str]]:
    """Turn a generator=search + extracts response into source notes."""
    pages = ((payload or {}).get("query") or {}).get("pages") or {}
    if isinstance(pages, dict):
        pages = list(pages.values())
    pages = sorted(pages, key=lambda page: page.get("index", 0))
    sources = []
    for page in pages:
        extract = " ".join((page.get("extract") or "").split())
        title = page.get("title") or ""
        if not extract or not title:
            continue
        if len(extract) > MAX_EXTRACT_CHARS:
            extract = extract[:MAX_EXTRACT_CHARS].rsplit(" ", 1)[0] + "…"
        url = page.get("fullurl") or (
            f"https://{lang}.wikipedia.org/wiki/{title.replace(' ', '_')}"
        )
        sources.append({"title": title, "url": url, "extract": extract})
    return sources


def search_wikipedia(query: str, lang: str = "en", limit: int = RESULTS_PER_QUERY) -> List[Dict[str, str]]:
    params = {
        "action": "query",
        "format": "json",
        "generator": "search",
        "gsrsearch": query,
        "gsrlimit": limit,
        "prop": "extracts|info",
        "inprop": "url",
        "explaintext": 1,
        "exintro": 1,
        "exlimit": "max",
        "redirects": 1,
    }
    try:
        response = httpx.get(
            WIKIPEDIA_API.format(lang=lang),
            params=params,
            headers={"User-Agent": USER_AGENT},
            timeout=REQUEST_TIMEOUT,
            follow_redirects=True,
        )
        response.raise_for_status()
        return parse_wikipedia_response(response.json(), lang)
    except Exception as exc:
        logger.warning("Wikipedia search failed for %r: %s", query, exc)
        return []


def gather_sources(queries: List[str]) -> List[Dict[str, str]]:
    """Look up each query and return de-duplicated sources, best first."""
    sources: List[Dict[str, str]] = []
    seen = set()
    for query in queries[:5]:
        query = " ".join(str(query).split())[:120]
        if not query:
            continue
        for source in search_wikipedia(query):
            if source["url"] in seen:
                continue
            seen.add(source["url"])
            sources.append(source)
            if len(sources) >= MAX_SOURCES:
                return sources
    return sources


def format_research_notes(sources: List[Dict[str, str]]) -> str:
    if not sources:
        return ""
    lines = ["RESEARCH NOTES (the only facts you may state beyond general knowledge):"]
    for number, source in enumerate(sources, start=1):
        lines.append(f"[{number}] {source['title']} ({source['url']}): {source['extract']}")
    return "\n".join(lines)

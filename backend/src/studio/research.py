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


# --- Claude web search ----------------------------------------------------
# With an Anthropic key, Claude searches the whole web (current prices, recent
# tech and local Tanzanian sources) instead of only Wikipedia. Web search is
# billed by Anthropic per search on top of tokens; max_uses caps it per video.

WEB_SEARCH_TOOL = {"type": "web_search_20260209", "name": "web_search", "max_uses": 6}
WEB_RESEARCH_SYSTEM = (
    "You research facts for a short educational video narrated in Kiswahili for a "
    "Tanzanian and East African audience. Search the web, prefer primary and reputable "
    "sources (official statistics, central banks, universities, major news outlets, "
    "encyclopedias), and prefer Tanzanian or East African sources for local facts. "
    "Then write 6-12 short numbered research notes in English. Each note states one "
    "concrete, checkable fact (a number, date, name, mechanism or finding) with its "
    "year when it matters. Only include facts the sources support, and say plainly "
    "when sources disagree. No introduction and no conclusion."
)
MAX_WEB_CONTINUATIONS = 3


def _collect_web_sources(content: List[Any], sources: Dict[str, Dict[str, str]]) -> None:
    for block in content:
        block_type = getattr(block, "type", None)
        if block_type == "web_search_tool_result":
            results = getattr(block, "content", None)
            if not isinstance(results, list):  # an error object, not results
                continue
            for result in results:
                url = getattr(result, "url", None)
                if url and url not in sources:
                    sources[url] = {"title": getattr(result, "title", "") or url, "url": url, "extract": ""}
        elif block_type == "text":
            for citation in getattr(block, "citations", None) or []:
                url = getattr(citation, "url", None)
                if not url:
                    continue
                source = sources.setdefault(
                    url, {"title": getattr(citation, "title", "") or url, "url": url, "extract": ""}
                )
                cited = " ".join((getattr(citation, "cited_text", "") or "").split())
                if cited and cited not in source["extract"] and len(source["extract"]) < MAX_EXTRACT_CHARS:
                    source["extract"] = (source["extract"] + " … " + cited).strip(" …")


async def research_with_claude(idea: str, model: str, api_key: str) -> Dict[str, Any]:
    """Research the idea with Claude's web search. Returns notes and the
    sources Claude cited (cited sources first)."""
    import anthropic

    client = anthropic.AsyncAnthropic(api_key=api_key, timeout=300, max_retries=2)
    messages: List[Dict[str, Any]] = [
        {"role": "user", "content": f"Research this video idea (it may be in Kiswahili or English):\n\n{idea}"}
    ]
    sources: Dict[str, Dict[str, str]] = {}
    notes: List[str] = []
    for _ in range(MAX_WEB_CONTINUATIONS + 1):
        response = await client.beta.messages.create(
            model=model,
            max_tokens=16000,
            system=WEB_RESEARCH_SYSTEM,
            messages=messages,
            tools=[WEB_SEARCH_TOOL],
            output_config={"effort": "medium"},
            betas=["server-side-fallback-2026-07-01"],
            extra_body={"fallbacks": "default"},
        )
        if response.stop_reason == "refusal":
            raise RuntimeError("Claude declined to research this topic")
        _collect_web_sources(response.content, sources)
        notes += [block.text for block in response.content if getattr(block, "type", None) == "text"]
        if response.stop_reason != "pause_turn":
            break
        messages = messages[:1] + [{"role": "assistant", "content": response.content}]
    text = "".join(notes).strip()
    if not text:
        raise RuntimeError("Claude's web research returned no notes")
    ordered = sorted(sources.values(), key=lambda source: not source["extract"])
    return {"method": "web", "notes": text, "sources": ordered[:12]}


def format_web_notes(research: Dict[str, Any]) -> str:
    sources = research.get("sources") or []
    lines = [
        "RESEARCH NOTES from a web search (the only facts you may state beyond general knowledge):",
        research.get("notes", ""),
    ]
    if sources:
        lines.append("Sources: " + "; ".join(f"{source['title']} ({source['url']})" for source in sources))
    return "\n".join(lines)

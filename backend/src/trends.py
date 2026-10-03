"""What is trending right now, for trend-aware clip picking and the Discover page.

Follows the Agent Reach approach (https://github.com/Panniantong/Agent-Reach):
read public sources directly, no scraping logins:

- Google Trends daily trending searches RSS for a country (no API key)
- YouTube's "most popular" chart for a country, when a YouTube Data API key
  is configured (YOUTUBE_DATA_API_KEY, or GOOGLE_API_KEY with the YouTube
  Data API enabled)

Results are cached in memory per region for an hour. Every failure is soft:
callers get whatever sources answered, possibly nothing.
"""

import logging
import time
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional

import httpx

from .config import get_config

logger = logging.getLogger(__name__)

GOOGLE_TRENDS_RSS_URL = "https://trends.google.com/trending/rss"
YOUTUBE_VIDEOS_URL = "https://www.googleapis.com/youtube/v3/videos"
CACHE_SECONDS = 3600
# Remember an empty answer briefly so an offline machine does not wait out the
# request timeouts on every video.
EMPTY_CACHE_SECONDS = 600
REQUEST_TIMEOUT = 10.0
MAX_SEARCHES = 15
MAX_VIDEOS = 15

REGION_NAMES = {
    "TZ": "Tanzania", "KE": "Kenya", "UG": "Uganda", "RW": "Rwanda", "NG": "Nigeria",
    "ZA": "South Africa", "GH": "Ghana", "US": "the United States", "GB": "the United Kingdom",
    "IN": "India",
}

_cache: Dict[str, Dict[str, Any]] = {}


def region_name(region: str) -> str:
    return REGION_NAMES.get(region.upper(), region.upper())


def _local(tag: str) -> str:
    """Element name without its XML namespace."""
    return tag.rsplit("}", 1)[-1]


def parse_google_trends_rss(xml_text: str) -> List[Dict[str, Any]]:
    """Parse trending searches from the Google Trends RSS feed.

    Namespace-agnostic, so both the current ``trending/rss`` feed and the older
    daily-trends feed parse.
    """
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []
    searches = []
    for item in root.iter():
        if _local(item.tag) != "item":
            continue
        title, traffic, news = "", "", []
        for child in item:
            name = _local(child.tag)
            if name == "title":
                title = (child.text or "").strip()
            elif name == "approx_traffic":
                traffic = (child.text or "").strip()
            elif name == "news_item":
                headline = next(
                    (
                        (sub.text or "").strip()
                        for sub in child
                        if _local(sub.tag) == "news_item_title"
                    ),
                    "",
                )
                if headline:
                    news.append(headline)
        if title:
            searches.append({"title": title, "traffic": traffic, "news": news[:2]})
        if len(searches) >= MAX_SEARCHES:
            break
    return searches


def _fetch_google_trends(region: str) -> List[Dict[str, Any]]:
    try:
        response = httpx.get(
            GOOGLE_TRENDS_RSS_URL,
            params={"geo": region},
            timeout=REQUEST_TIMEOUT,
            follow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0 (Katakata trend reader)"},
        )
        response.raise_for_status()
        return parse_google_trends_rss(response.text)
    except Exception as exc:
        logger.warning("Google Trends unavailable for %s: %s", region, exc)
        return []


def _fetch_youtube_popular(region: str) -> List[Dict[str, Any]]:
    api_key = get_config().resolve_youtube_data_api_key()
    if not api_key:
        return []
    try:
        response = httpx.get(
            YOUTUBE_VIDEOS_URL,
            params={
                "part": "snippet,statistics",
                "chart": "mostPopular",
                "regionCode": region,
                "maxResults": MAX_VIDEOS,
                "key": api_key,
            },
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
    except Exception as exc:
        logger.warning("YouTube most-popular chart unavailable for %s: %s", region, exc)
        return []
    videos = []
    for item in response.json().get("items", []):
        snippet = item.get("snippet") or {}
        stats = item.get("statistics") or {}
        videos.append(
            {
                "id": item.get("id"),
                "title": snippet.get("title", ""),
                "channel": snippet.get("channelTitle", ""),
                "tags": (snippet.get("tags") or [])[:5],
                "views": int(stats.get("viewCount", 0) or 0),
                "url": f"https://www.youtube.com/watch?v={item.get('id')}",
            }
        )
    return videos


def get_trends(region: Optional[str] = None, use_cache: bool = True) -> Dict[str, Any]:
    """Trending searches and videos for ``region`` (default TRENDS_REGION)."""
    config = get_config()
    region = (region or config.trends_region or "").strip().upper()
    if not region:
        return {"region": "", "region_name": "", "searches": [], "videos": []}
    cached = _cache.get(region)
    if use_cache and cached and time.time() - cached["fetched_at"] < cached["ttl"]:
        return cached["data"]
    data = {
        "region": region,
        "region_name": region_name(region),
        "searches": _fetch_google_trends(region),
        "videos": _fetch_youtube_popular(region),
    }
    ttl = CACHE_SECONDS if (data["searches"] or data["videos"]) else EMPTY_CACHE_SECONDS
    _cache[region] = {"fetched_at": time.time(), "ttl": ttl, "data": data}
    return data


def format_trend_signals(trends: Dict[str, Any]) -> str:
    """Describe current trends for the clip-picking prompt."""
    searches = trends.get("searches") or []
    videos = trends.get("videos") or []
    if not searches and not videos:
        return ""
    lines = [
        f"Trending right now in {trends.get('region_name') or trends.get('region')} "
        "(use only when a segment genuinely connects to one of these; never force a "
        "trend onto unrelated content):",
    ]
    for search in searches[:10]:
        news = f" ({search['news'][0]})" if search.get("news") else ""
        traffic = f" [{search['traffic']} searches]" if search.get("traffic") else ""
        lines.append(f"- search: {search['title']}{traffic}{news}")
    for video in videos[:8]:
        lines.append(f"- popular video: {video['title']} ({video['channel']})")
    lines.append(
        "When a segment does connect to a trend, rank it higher, let the hook_title "
        "and post_caption reference the trend naturally, and include its hashtag."
    )
    return "\n".join(lines)

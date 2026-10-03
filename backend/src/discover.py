"""Find videos worth clipping: YouTube search for the Discover page.

Uses yt-dlp's search (the same tool Agent Reach uses for YouTube), so no API key
is needed. Podcasts are searched as YouTube podcast episodes, since a clip
needs video.
"""

import logging
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

MAX_RESULTS = 25


def _thumbnail(video_id: str) -> str:
    return f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"


def normalize_search_entry(entry: Dict[str, Any]) -> Dict[str, Any] | None:
    """Map a yt-dlp flat search entry to what the Discover page shows."""
    video_id = entry.get("id")
    if not video_id or entry.get("ie_key") not in (None, "Youtube"):
        return None
    duration = entry.get("duration")
    return {
        "id": video_id,
        "title": entry.get("title") or "",
        "channel": entry.get("channel") or entry.get("uploader") or "",
        "duration": int(duration) if isinstance(duration, (int, float)) else None,
        "views": entry.get("view_count"),
        "thumbnail": _thumbnail(video_id),
        "url": f"https://www.youtube.com/watch?v={video_id}",
        "is_live": entry.get("live_status") == "is_live",
    }


def search_youtube(
    query: str,
    limit: int = 12,
    podcasts: bool = False,
    min_minutes: int = 0,
) -> List[Dict[str, Any]]:
    """Search YouTube and return clippable videos (no livestreams)."""
    import yt_dlp

    query = " ".join(query.split())[:120]
    if not query:
        return []
    limit = max(1, min(limit, MAX_RESULTS))
    search_terms = f"{query} podcast" if podcasts else query
    # Ask for extra results because livestreams and short videos are dropped.
    fetch = min(MAX_RESULTS * 2, limit * 2 if min_minutes else limit + 5)
    options = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "extract_flat": "in_playlist",
    }
    with yt_dlp.YoutubeDL(options) as ydl:
        info = ydl.extract_info(f"ytsearch{fetch}:{search_terms}", download=False) or {}

    results = []
    for entry in info.get("entries") or []:
        video = normalize_search_entry(entry or {})
        if not video or video["is_live"]:
            continue
        if min_minutes and (video["duration"] or 0) < min_minutes * 60:
            continue
        results.append(video)
        if len(results) >= limit:
            break
    return results

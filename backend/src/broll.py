"""
B-Roll functionality for video enhancement using the Pexels API, with Pixabay
as a fallback stock source.
"""

import httpx
import logging
import asyncio
from pathlib import Path
from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field

from .config import get_config

logger = logging.getLogger(__name__)

PEXELS_API_URL = "https://api.pexels.com/videos/search"
PEXELS_VIDEO_URL = "https://api.pexels.com/videos/videos"
PIXABAY_VIDEO_API_URL = "https://pixabay.com/api/videos/"


class BRollVideo(BaseModel):
    """Represents a B-roll video from Pexels."""
    id: int
    width: int
    height: int
    duration: int
    url: str
    image: str  # Thumbnail
    video_files: List[Dict[str, Any]]
    user: Dict[str, Any]


class BRollSuggestion(BaseModel):
    """A suggestion for B-roll insertion."""
    keyword: str = Field(description="Search keyword for B-roll")
    timestamp: float = Field(description="When to insert B-roll (seconds from clip start)")
    duration: float = Field(description="How long to show B-roll (2-5 seconds)", ge=2.0, le=5.0)
    context: str = Field(description="What's being discussed at this point")
    video_url: Optional[str] = Field(default=None, description="URL of selected B-roll video")
    video_id: Optional[int] = Field(default=None, description="Pexels video ID")
    local_path: Optional[str] = Field(default=None, description="Local path after download")


async def search_broll_videos(
    keyword: str,
    orientation: str = "portrait",
    size: str = "medium",
    per_page: int = 5
) -> List[Dict[str, Any]]:
    """
    Search Pexels for relevant B-roll videos.

    Args:
        keyword: Search term for B-roll
        orientation: Video orientation (portrait, landscape, square)
        size: Video size (large, medium, small)
        per_page: Number of results to return

    Returns:
        List of video results in the Pexels response shape
    """
    videos = await _search_pexels_videos(keyword, orientation, size, per_page)
    if not videos:
        videos = await _search_pixabay_videos(keyword, per_page)
    return videos


def is_broll_configured() -> bool:
    runtime_config = get_config()
    return bool(runtime_config.pexels_api_key or runtime_config.pixabay_api_key)


async def _search_pexels_videos(
    keyword: str, orientation: str, size: str, per_page: int
) -> List[Dict[str, Any]]:
    runtime_config = get_config()
    if not runtime_config.pexels_api_key:
        return []

    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                PEXELS_API_URL,
                params={
                    "query": keyword,
                    "orientation": orientation,
                    "size": size,
                    "per_page": per_page
                },
                headers={
                    "Authorization": runtime_config.pexels_api_key
                },
                timeout=30.0
            )

            if response.status_code != 200:
                logger.error(f"Pexels API error: {response.status_code} - {response.text}")
                return []

            data = response.json()
            videos = data.get("videos", [])

            logger.info(f"Found {len(videos)} B-roll videos for '{keyword}'")
            return videos

    except Exception as e:
        logger.error(f"Error searching Pexels: {e}")
        return []


def _pixabay_hit_to_pexels_shape(hit: Dict[str, Any]) -> Dict[str, Any]:
    """Map a Pixabay video hit onto the Pexels video fields used here."""
    video_files = []
    largest: Dict[str, Any] = {}
    for rendition in ("large", "medium", "small", "tiny"):
        file_info = (hit.get("videos") or {}).get(rendition) or {}
        if not file_info.get("url"):
            continue
        width = int(file_info.get("width") or 0)
        height = int(file_info.get("height") or 0)
        largest = largest or file_info
        video_files.append(
            {
                "quality": "hd" if min(width, height) >= 720 else "sd",
                "width": width,
                "height": height,
                "link": file_info["url"],
            }
        )
    return {
        "id": hit.get("id"),
        "width": int(largest.get("width") or 0),
        "height": int(largest.get("height") or 0),
        "duration": int(hit.get("duration") or 0),
        "url": hit.get("pageURL", ""),
        "image": largest.get("thumbnail", ""),
        "video_files": video_files,
        "user": {"name": hit.get("user", "")},
        "provider": "pixabay",
    }


async def _search_pixabay_videos(keyword: str, per_page: int) -> List[Dict[str, Any]]:
    runtime_config = get_config()
    if not runtime_config.pixabay_api_key:
        return []

    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                PIXABAY_VIDEO_API_URL,
                params={
                    "key": runtime_config.pixabay_api_key,
                    "q": keyword,
                    # Pixabay rejects per_page below 3.
                    "per_page": max(3, min(per_page, 200)),
                    "safesearch": "true",
                },
                timeout=30.0,
            )
            if response.status_code != 200:
                logger.error(f"Pixabay API error: {response.status_code}")
                return []

            videos = [
                _pixabay_hit_to_pexels_shape(hit)
                for hit in response.json().get("hits", [])
            ]
            logger.info(f"Found {len(videos)} Pixabay B-roll videos for '{keyword}'")
            return videos

    except Exception as e:
        logger.error(f"Error searching Pixabay: {e}")
        return []


async def get_best_broll_video(
    keyword: str,
    target_duration: float = 3.0,
    orientation: str = "portrait"
) -> Optional[Dict[str, Any]]:
    """
    Get the best matching B-roll video for a keyword.

    Args:
        keyword: Search term
        target_duration: Desired video duration
        orientation: Video orientation

    Returns:
        Best matching video or None
    """
    videos = await search_broll_videos(keyword, orientation=orientation)

    if not videos:
        return None

    # Score videos based on duration match and quality
    def score_video(video: Dict) -> float:
        duration = video.get("duration", 0)
        # Prefer videos close to target duration
        duration_diff = abs(duration - target_duration)
        duration_score = max(0, 10 - duration_diff)

        # Prefer higher quality (HD videos)
        quality_score = 0
        for vf in video.get("video_files", []):
            if vf.get("quality") == "hd":
                quality_score = 5
                break
            elif vf.get("quality") == "sd":
                quality_score = 2

        return duration_score + quality_score

    # Sort by score and return best match
    scored_videos = [(v, score_video(v)) for v in videos]
    scored_videos.sort(key=lambda x: x[1], reverse=True)

    return scored_videos[0][0] if scored_videos else None


def get_video_download_url(video: Dict[str, Any], quality: str = "hd", orientation: str = "portrait") -> Optional[str]:
    """
    Get the download URL for a specific video quality.

    Args:
        video: Pexels video object
        quality: Desired quality (hd, sd)
        orientation: Desired orientation

    Returns:
        Download URL or None
    """
    video_files = video.get("video_files", [])

    # Filter by quality and find best match
    for vf in video_files:
        if vf.get("quality") == quality:
            # Check orientation by dimensions
            width = vf.get("width", 0)
            height = vf.get("height", 0)

            is_portrait = height > width
            if orientation == "portrait" and is_portrait:
                return vf.get("link")
            elif orientation == "landscape" and not is_portrait:
                return vf.get("link")

    # Fallback: return any HD file
    for vf in video_files:
        if vf.get("quality") == quality:
            return vf.get("link")

    # Last resort: return first available
    if video_files:
        return video_files[0].get("link")

    return None


async def download_broll_video(
    video_url: str,
    output_path: Path,
    timeout: float = 60.0
) -> bool:
    """
    Download a B-roll video from Pexels.

    Args:
        video_url: URL to download
        output_path: Where to save the video
        timeout: Download timeout in seconds

    Returns:
        True if successful
    """
    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)

        async with httpx.AsyncClient() as client:
            response = await client.get(video_url, timeout=timeout, follow_redirects=True)

            if response.status_code != 200:
                logger.error(f"Failed to download B-roll: {response.status_code}")
                return False

            with open(output_path, "wb") as f:
                f.write(response.content)

            logger.info(f"Downloaded B-roll to: {output_path}")
            return True

    except Exception as e:
        logger.error(f"Error downloading B-roll: {e}")
        return False


async def fetch_broll_for_opportunities(
    opportunities: List[Dict[str, Any]],
    output_dir: Path,
    orientation: str = "portrait"
) -> List[BRollSuggestion]:
    """
    Fetch B-roll videos for a list of opportunities from AI analysis.

    Args:
        opportunities: List of B-roll opportunities from AI
        output_dir: Directory to save downloaded videos
        orientation: Video orientation

    Returns:
        List of B-roll suggestions with download paths
    """
    if not is_broll_configured():
        logger.warning("No B-roll API key configured (Pexels or Pixabay), skipping B-roll fetch")
        return []

    broll_dir = output_dir / "broll"
    broll_dir.mkdir(parents=True, exist_ok=True)

    suggestions = []

    for i, opp in enumerate(opportunities):
        keyword = opp.get("search_term", "")
        if not keyword:
            continue

        # Parse timestamp (MM:SS format)
        timestamp_str = opp.get("timestamp", "00:00")
        try:
            parts = timestamp_str.split(":")
            timestamp = int(parts[0]) * 60 + int(parts[1])
        except (ValueError, IndexError):
            timestamp = 0

        duration = opp.get("duration", 3.0)
        context = opp.get("context", "")

        # Search for B-roll
        video = await get_best_broll_video(keyword, target_duration=duration, orientation=orientation)

        if video:
            video_id = video.get("id")
            download_url = get_video_download_url(video, quality="hd", orientation=orientation)

            if download_url:
                # Download the video
                local_path = broll_dir / f"broll_{i+1}_{video_id}.mp4"
                success = await download_broll_video(download_url, local_path)

                if success:
                    suggestion = BRollSuggestion(
                        keyword=keyword,
                        timestamp=float(timestamp),
                        duration=duration,
                        context=context,
                        video_url=download_url,
                        video_id=video_id,
                        local_path=str(local_path)
                    )
                    suggestions.append(suggestion)
                    logger.info(f"B-roll {i+1}: '{keyword}' -> {local_path}")

        # Small delay to avoid rate limiting
        await asyncio.sleep(0.5)

    logger.info(f"Fetched {len(suggestions)}/{len(opportunities)} B-roll videos")
    return suggestions


async def get_broll_suggestions_for_clip(
    transcript_text: str,
    clip_duration: float
) -> List[Dict[str, Any]]:
    """
    Generate B-roll keyword suggestions based on transcript text.
    This is a simple keyword extraction - the main AI analysis handles this.

    Args:
        transcript_text: Text from the clip
        clip_duration: Duration of the clip in seconds

    Returns:
        List of suggested keywords
    """
    # This is a fallback - the main AI analysis in ai.py handles B-roll detection
    # This function can be used for additional keyword extraction if needed

    # Simple keyword extraction based on common visual concepts
    visual_keywords = [
        "money", "cash", "success", "business", "computer", "phone", "office",
        "meeting", "team", "workout", "gym", "food", "cooking", "travel",
        "city", "nature", "people", "technology", "social media", "coffee",
        "book", "reading", "writing", "car", "driving", "walking", "running"
    ]

    text_lower = transcript_text.lower()
    found_keywords = []

    for keyword in visual_keywords:
        if keyword in text_lower:
            found_keywords.append(keyword)

    return found_keywords[:3]  # Return top 3 matches

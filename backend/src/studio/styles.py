"""Inspiration channels: learn a style guide from YouTube videos the creator admires.

Katakata reads public metadata and subtitles with yt-dlp (no download of the
videos themselves), then the director's LLM writes a style guide: how the
channel hooks viewers, paces and structures a story, narrates, and uses
visuals. Studio scripts follow the guide's techniques, never the channels'
wording or stories.
"""

import logging
import re
import tempfile
from pathlib import Path
from typing import Any, Dict, List

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

MAX_VIDEOS = 6
VIDEOS_PER_CHANNEL = 3
OPENING_WORDS = 700
CLOSING_WORDS = 200
_VIDEO_ID = re.compile(r"(?:v=|youtu\.be/|/shorts/|/live/)([A-Za-z0-9_-]{11})")

STYLE_SYSTEM_PROMPT = (
    "You are a story editor who studies successful YouTube channels so a creator can learn "
    "their craft. From the titles, descriptions and transcript excerpts you are given, write "
    "a practical style guide another writer could follow for faceless narrated stickman "
    "videos in Kiswahili. Describe techniques, not content: never quote more than a few words, "
    "and never suggest reusing their stories, titles or catchphrases."
)


class StyleGuide(BaseModel):
    summary: str = Field(description="One sentence describing the channel's style (English)")
    guide: str = Field(
        description=(
            "A practical style guide (English, 200-450 words) with these headed sections: "
            "Hooks (how the first 15 seconds grab attention), Structure and pacing (how the "
            "story is built, how long sections run, how tension is kept), Narration voice "
            "(sentence length, tone, use of questions, humour, direct address), Visual ideas "
            "(recurring metaphors, scene types, transitions, on-screen text), Endings (how "
            "videos close and invite comments), Do and Don't (5 short rules)."
        )
    )


def is_channel_url(url: str) -> bool:
    return bool(re.search(r"youtube\.com/(@[^/?#]+|channel/|c/|user/)", url)) and not _VIDEO_ID.search(url)


def parse_vtt(text: str) -> str:
    """Plain words from a WebVTT subtitle file, without the repeated lines
    auto-captions produce."""
    lines: List[str] = []
    for line in text.splitlines():
        line = line.strip()
        if (
            not line
            or line == "WEBVTT"
            or "-->" in line
            or line.startswith(("Kind:", "Language:", "NOTE", "STYLE"))
            or line.isdigit()
        ):
            continue
        line = re.sub(r"<[^>]+>", "", line)
        line = " ".join(line.split())
        # Auto-captions roll: each cue repeats the line before it.
        if line and line not in lines[-2:]:
            lines.append(line)
    return " ".join(lines)


def excerpt(transcript: str) -> str:
    words = transcript.split()
    if len(words) <= OPENING_WORDS + CLOSING_WORDS:
        return transcript
    return " ".join(words[:OPENING_WORDS]) + " […] " + " ".join(words[-CLOSING_WORDS:])


def _ydl(options: Dict[str, Any]):
    import yt_dlp

    base = {"quiet": True, "no_warnings": True, "skip_download": True}
    base.update(options)
    return yt_dlp.YoutubeDL(base)


def _channel_videos(url: str) -> List[str]:
    target = url.rstrip("/")
    if not target.endswith("/videos"):
        target += "/videos"
    with _ydl({"extract_flat": "in_playlist", "playlistend": VIDEOS_PER_CHANNEL * 3}) as ydl:
        info = ydl.extract_info(target, download=False) or {}
    videos = []
    for entry in info.get("entries") or []:
        video_id = (entry or {}).get("id")
        if video_id and len(video_id) == 11:
            videos.append(f"https://www.youtube.com/watch?v={video_id}")
        if len(videos) >= VIDEOS_PER_CHANNEL:
            break
    return videos


def _video_material(url: str, work: Path) -> Dict[str, Any]:
    options = {
        "writesubtitles": True,
        "writeautomaticsub": True,
        "subtitleslangs": ["en", "en-orig", "en-US", "en-GB", "sw"],
        "subtitlesformat": "vtt",
        "outtmpl": str(work / "%(id)s.%(ext)s"),
    }
    with _ydl(options) as ydl:
        info = ydl.extract_info(url, download=True) or {}
    video_id = info.get("id", "")
    transcript = ""
    for subtitle in sorted(work.glob(f"{video_id}*.vtt")):
        transcript = parse_vtt(subtitle.read_text(errors="ignore"))
        if transcript:
            break
    return {
        "title": info.get("title") or "",
        "channel": info.get("channel") or info.get("uploader") or "",
        "url": info.get("webpage_url") or url,
        "duration": info.get("duration"),
        "views": info.get("view_count"),
        "description": " ".join((info.get("description") or "").split())[:600],
        "transcript": excerpt(transcript),
    }


def gather_material(urls: List[str]) -> List[Dict[str, Any]]:
    """Metadata and transcript excerpts for up to MAX_VIDEOS videos."""
    video_urls: List[str] = []
    for url in urls:
        url = url.strip()
        if not url:
            continue
        try:
            found = _channel_videos(url) if is_channel_url(url) else [url]
        except Exception as error:
            logger.warning("Could not list videos for %s: %s", url, error)
            continue
        for video in found:
            if video not in video_urls:
                video_urls.append(video)
    material = []
    with tempfile.TemporaryDirectory(prefix="studio_style_") as temp:
        for video in video_urls[:MAX_VIDEOS]:
            try:
                material.append(_video_material(video, Path(temp)))
            except Exception as error:
                logger.warning("Could not read %s: %s", video, error)
    return material


def build_style_prompt(name: str, material: List[Dict[str, Any]]) -> str:
    parts = [f"Write the style guide for: {name}"]
    for index, video in enumerate(material, start=1):
        minutes = f"{round(video['duration'] / 60)} min" if video.get("duration") else "unknown length"
        parts.append(
            f"VIDEO {index}: \"{video['title']}\" by {video['channel']} ({minutes})\n"
            f"Description: {video['description']}\n"
            f"Transcript excerpt: {video['transcript'] or '(no subtitles available)'}"
        )
    return "\n\n".join(parts)


async def write_style_guide(name: str, material: List[Dict[str, Any]]) -> Dict[str, str]:
    from .director import _run

    result = await _run(StyleGuide, STYLE_SYSTEM_PROMPT, build_style_prompt(name, material))
    return {"summary": result.summary.strip(), "guide": result.guide.strip()}

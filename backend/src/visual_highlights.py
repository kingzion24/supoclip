"""Visual highlight detection with TwelveLabs Pegasus (optional).

The transcript-based ranking in ``ai.py`` cannot see what happens on screen,
so dance, stunts, reactions and other visual moments with little speech are
missed. When ``TWELVELABS_API_KEY`` is set, the source video is uploaded (as a
small 360p proxy) to TwelveLabs, Pegasus returns timestamped highlights, and
the pipeline uses them twice: as extra hints for the transcript ranking and
as visual-only clips for moments the transcript ranking did not pick.

Everything here fails soft: any error returns no highlights and the pipeline
continues exactly as it would without TwelveLabs.
"""

import json
import logging
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx

from .config import get_config

logger = logging.getLogger(__name__)

TWELVELABS_API_URL = "https://api.twelvelabs.io/v1.3"
# Pegasus analyzes at most one hour; local uploads are capped at 200 MB.
MAX_ANALYZED_SECONDS = 3600
ASSET_READY_TIMEOUT_SECONDS = 900
ASSET_POLL_INTERVAL_SECONDS = 5
MIN_VISUAL_CLIP_SECONDS = 15
MAX_VISUAL_CLIP_SECONDS = 60
# A visual highlight is skipped when this much of it is already covered by a
# clip the transcript ranking selected.
MAX_OVERLAP_RATIO = 0.3

HIGHLIGHT_PROMPT = """You are choosing moments from this video to post as TikTok, Instagram Reels and YouTube Shorts clips.

Find the moments that are the most compelling to WATCH: dance moves and dance challenges, stunts and skills, big reactions, physical comedy, dramatic reveals, crowd energy, striking or beautiful scenes, and moments where the music or laughter peaks. Judge what is seen and heard, not only what is said.

Return up to 6 highlights, best first. For each one give:
- start_sec and end_sec: where the moment starts and ends, 15 to 60 seconds apart
- score: 0-100, how likely this moment is to perform well as a short-form clip
- description: one sentence describing what happens on screen
- hook_title: a 3-9 word on-screen headline for the clip, written in the language spoken in the video (English if nobody speaks), with no hashtags, emojis or quotes"""

HIGHLIGHT_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "highlights": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "start_sec": {"type": "timestamp", "format": "seconds"},
                    "end_sec": {"type": "timestamp", "format": "seconds"},
                    "score": {"type": "integer", "minimum": 0, "maximum": 100},
                    "description": {"type": "string"},
                    "hook_title": {"type": "string"},
                },
                "required": ["start_sec", "end_sec", "score", "description", "hook_title"],
            },
        }
    },
    "required": ["highlights"],
}


def is_enabled() -> bool:
    return bool(get_config().twelvelabs_api_key)


def _cache_path(video_path: Path) -> Path:
    return video_path.with_suffix(".visual_highlights.json")


def _make_proxy(video_path: Path, output_path: Path) -> bool:
    """Encode a small 360p copy (short side 360px) of at most one hour."""
    command = [
        "ffmpeg",
        "-y",
        "-i",
        str(video_path),
        "-t",
        str(MAX_ANALYZED_SECONDS),
        "-vf",
        "scale='if(gt(iw,ih),-2,360)':'if(gt(iw,ih),360,-2)'",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "34",
        "-maxrate",
        "300k",
        "-bufsize",
        "600k",
        "-c:a",
        "aac",
        "-b:a",
        "48k",
        "-ac",
        "1",
        str(output_path),
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=1800)
    if result.returncode != 0:
        logger.warning("TwelveLabs proxy encode failed: %s", result.stderr[-1000:])
        return False
    return output_path.exists() and output_path.stat().st_size > 0


def _upload_asset(client: httpx.Client, proxy_path: Path) -> str:
    with open(proxy_path, "rb") as handle:
        response = client.post(
            "/assets",
            data={"method": "direct", "filename": proxy_path.name},
            files={"file": (proxy_path.name, handle, "video/mp4")},
        )
    response.raise_for_status()
    asset_id = response.json().get("_id") or response.json().get("id")
    if not asset_id:
        raise RuntimeError("TwelveLabs did not return an asset id")
    return str(asset_id)


def _wait_for_asset(client: httpx.Client, asset_id: str) -> None:
    deadline = time.monotonic() + ASSET_READY_TIMEOUT_SECONDS
    while True:
        response = client.get(f"/assets/{asset_id}")
        response.raise_for_status()
        status = response.json().get("status")
        if status == "ready":
            return
        if status == "failed":
            raise RuntimeError(f"TwelveLabs asset processing failed: {response.json().get('error')}")
        if time.monotonic() >= deadline:
            raise TimeoutError("TwelveLabs asset was not ready in time")
        time.sleep(ASSET_POLL_INTERVAL_SECONDS)


def _analyze(client: httpx.Client, asset_id: str, model_name: str) -> str:
    response = client.post(
        "/analyze",
        json={
            "model_name": model_name,
            "video": {"type": "asset_id", "asset_id": asset_id},
            "prompt": HIGHLIGHT_PROMPT,
            "temperature": 0.2,
            "response_format": {"type": "json_schema", "json_schema": HIGHLIGHT_SCHEMA},
            "max_tokens": 2048,
            "stream": False,
        },
    )
    response.raise_for_status()
    return str(response.json().get("data") or "")


def parse_highlights(raw: str, video_duration: Optional[float] = None) -> List[Dict[str, Any]]:
    """Validate Pegasus' JSON answer into sorted highlight dicts."""
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError):
        logger.warning("TwelveLabs returned non-JSON highlights")
        return []
    items = payload.get("highlights") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        return []

    highlights: List[Dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        try:
            start = max(0.0, float(item.get("start_sec")))
            end = float(item.get("end_sec"))
            score = int(item.get("score"))
        except (TypeError, ValueError):
            continue
        if video_duration:
            end = min(end, video_duration)
        if end - start < 1:
            continue
        highlights.append(
            {
                "start": round(start, 2),
                "end": round(end, 2),
                "score": max(0, min(100, score)),
                "description": str(item.get("description") or "").strip(),
                "hook_title": str(item.get("hook_title") or "").strip() or None,
            }
        )
    highlights.sort(key=lambda h: h["score"], reverse=True)
    return highlights


def detect_visual_highlights(
    video_path: Path, video_duration: Optional[float] = None
) -> List[Dict[str, Any]]:
    """Return TwelveLabs visual highlights for ``video_path`` (cached per video)."""
    config = get_config()
    if not config.twelvelabs_api_key:
        return []

    cache_path = _cache_path(video_path)
    if cache_path.exists():
        try:
            return json.loads(cache_path.read_text())
        except (OSError, ValueError):
            pass

    asset_id: Optional[str] = None
    with tempfile.TemporaryDirectory(prefix="supoclip_twelvelabs_") as temp_dir, httpx.Client(
        base_url=TWELVELABS_API_URL,
        headers={"x-api-key": config.twelvelabs_api_key},
        timeout=httpx.Timeout(300.0, connect=30.0),
    ) as client:
        try:
            proxy_path = Path(temp_dir) / "proxy.mp4"
            if not _make_proxy(video_path, proxy_path):
                return []
            logger.info("Uploading %s to TwelveLabs for visual highlights", video_path.name)
            asset_id = _upload_asset(client, proxy_path)
            _wait_for_asset(client, asset_id)
            raw = _analyze(client, asset_id, config.twelvelabs_pegasus_model)
            highlights = parse_highlights(raw, video_duration)
        except Exception as exc:
            logger.warning("TwelveLabs visual highlight detection failed: %s", exc)
            return []
        finally:
            if asset_id:
                try:
                    client.delete(f"/assets/{asset_id}")
                except Exception:
                    logger.debug("Could not delete TwelveLabs asset %s", asset_id)

    logger.info("TwelveLabs found %d visual highlights", len(highlights))
    try:
        cache_path.write_text(json.dumps(highlights))
    except OSError:
        pass
    return highlights


def _format_timestamp(seconds: float) -> str:
    seconds = max(0, int(seconds))
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def format_visual_signals(highlights: List[Dict[str, Any]]) -> str:
    """Describe highlights as hints for the transcript-ranking prompt."""
    if not highlights:
        return ""
    lines = [
        "Visual highlights seen in the video frames (TwelveLabs). Prefer transcript "
        "spans that overlap these when they also work as standalone clips:",
    ]
    for highlight in highlights:
        lines.append(
            f"- [{_format_timestamp(highlight['start'])} - {_format_timestamp(highlight['end'])}] "
            f"visual score {highlight['score']}: {highlight['description']}"
        )
    return "\n".join(lines)


def _overlap(a: Tuple[float, float], b: Tuple[float, float]) -> float:
    return max(0.0, min(a[1], b[1]) - max(a[0], b[0]))


def _fit_window(start: float, end: float, video_duration: Optional[float]) -> Tuple[float, float]:
    """Grow or shrink a highlight to a 15-60s clip window inside the video."""
    length = end - start
    if length < MIN_VISUAL_CLIP_SECONDS:
        pad = (MIN_VISUAL_CLIP_SECONDS - length) / 2
        start, end = start - pad, end + pad
    elif length > MAX_VISUAL_CLIP_SECONDS:
        end = start + MAX_VISUAL_CLIP_SECONDS
    if start < 0:
        end, start = end - start, 0.0
    if video_duration and end > video_duration:
        start = max(0.0, start - (end - video_duration))
        end = video_duration
    return start, end


def build_visual_segments(
    highlights: List[Dict[str, Any]],
    selected_ranges: List[Tuple[float, float]],
    video_duration: Optional[float],
    max_clips: int,
    min_score: int,
) -> List[Dict[str, Any]]:
    """Turn highlights the transcript ranking missed into clip segments."""
    taken = list(selected_ranges)
    segments: List[Dict[str, Any]] = []
    for highlight in highlights:
        if len(segments) >= max_clips:
            break
        if highlight["score"] < min_score:
            continue
        window = _fit_window(highlight["start"], highlight["end"], video_duration)
        if window[1] - window[0] < 4:
            continue
        length = window[1] - window[0]
        if any(_overlap(window, other) > length * MAX_OVERLAP_RATIO for other in taken):
            continue
        taken.append(window)
        score = highlight["score"]
        quarter = round(score / 4)
        segments.append(
            {
                "start_time": _format_timestamp(window[0]),
                "end_time": _format_timestamp(window[1]),
                "text": "",
                "relevance_score": round(score / 100, 2),
                "reasoning": f"Visual highlight (TwelveLabs): {highlight['description']}",
                "virality_score": score,
                "hook_score": quarter,
                "engagement_score": quarter,
                "value_score": quarter,
                "shareability_score": quarter,
                "hook_type": "visual",
                "hook_title": highlight.get("hook_title"),
            }
        )
    return segments

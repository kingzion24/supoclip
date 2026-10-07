"""Automatic scene clips with Gemini Omni Flash (Gemini API Interactions).

One synchronous request per scene: the API returns the finished clip, inline
as base64 or as a file URI to download. Needs a GOOGLE_API_KEY on a project
with billing (the model has no free tier). Audio cannot be switched off by
a flag; the scene prompts ask for no voice and no music, and Katakata keeps
the clip's sound effects quietly under the narration.
"""

import base64
import logging
from pathlib import Path
from typing import Any, Dict, Iterator, Optional

import httpx

logger = logging.getLogger(__name__)

API_BASE = "https://generativelanguage.googleapis.com"
INTERACTIONS_URL = f"{API_BASE}/v1beta/interactions"
API_REVISION = "2026-05-20"
MODEL = "gemini-omni-1.1-flash"
CLIP_SECONDS = 10
RESOLUTION = "720p"
REQUEST_TIMEOUT = 900.0


def build_request(prompt: str, aspect_ratio: str) -> Dict[str, Any]:
    return {
        "model": MODEL,
        "input": prompt,
        "response_format": {
            "type": "video",
            "aspect_ratio": aspect_ratio if aspect_ratio in ("9:16", "16:9") else "9:16",
            "resolution": RESOLUTION,
            "duration": f"{CLIP_SECONDS}s",
            "delivery": "inline",
        },
    }


def _video_parts(interaction: Dict[str, Any]) -> Iterator[Dict[str, Any]]:
    for step in interaction.get("steps") or []:
        if step.get("type") != "model_output":
            continue
        for part in step.get("content") or []:
            if part.get("type") == "video":
                yield part


def extract_video(interaction: Dict[str, Any]) -> Dict[str, Optional[str]]:
    """The last video in the model's output: ``{"data": b64}`` or ``{"uri": ...}``."""
    parts = list(_video_parts(interaction))
    if not parts:
        status = interaction.get("status")
        raise RuntimeError(
            f"Gemini returned no video (status: {status or 'unknown'}). "
            "The prompt may have been blocked; try editing the scene."
        )
    part = parts[-1]
    return {"data": part.get("data"), "uri": part.get("uri")}


def _download_uri(client: httpx.Client, uri: str, headers: Dict[str, str]) -> bytes:
    if uri.startswith("http") and ":download" in uri:
        url = uri
    else:
        name = uri.split("/v1beta/", 1)[-1] if "/v1beta/" in uri else uri
        url = f"{API_BASE}/v1beta/{name}:download"
    response = client.get(url, params={"alt": "media"}, headers=headers, follow_redirects=True)
    response.raise_for_status()
    return response.content


def _error_message(response: httpx.Response) -> str:
    try:
        detail = response.json().get("error", {})
        message = detail.get("message") or response.text
    except Exception:
        message = response.text
    message = " ".join(str(message).split())[:300]
    if response.status_code in (401, 403):
        return f"Google rejected the API key ({message}). Check GOOGLE_API_KEY and that billing is on."
    if response.status_code == 429:
        return "Gemini rate limit or quota reached. Wait a few minutes and try again."
    return f"Gemini video generation failed (HTTP {response.status_code}): {message}"


def generate_clip(prompt: str, aspect_ratio: str, api_key: str, output_path: Path) -> float:
    """Generate one scene clip into ``output_path``. Returns its size in MB."""
    headers = {"x-goog-api-key": api_key, "Api-Revision": API_REVISION}
    with httpx.Client(timeout=REQUEST_TIMEOUT) as client:
        response = client.post(INTERACTIONS_URL, json=build_request(prompt, aspect_ratio), headers=headers)
        if response.status_code >= 400:
            raise RuntimeError(_error_message(response))
        video = extract_video(response.json())
        if video.get("data"):
            content = base64.b64decode(video["data"])
        elif video.get("uri"):
            content = _download_uri(client, video["uri"], headers)
        else:
            raise RuntimeError("Gemini returned a video without data")
    if len(content) < 1000:
        raise RuntimeError("Gemini returned an empty video")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    partial = output_path.with_suffix(".part")
    partial.write_bytes(content)
    partial.replace(output_path)
    return len(content) / (1024 * 1024)

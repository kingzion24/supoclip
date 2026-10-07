"""Background jobs for Studio productions (run by the ARQ worker).

Jobs never re-raise: a failed step records the error on the production so
the user can retry it, instead of the queue retrying paid AI calls.
"""

import hashlib
import json
import logging
import re
import shutil
import unicodedata
from pathlib import Path
from typing import Any, Dict, Optional

from ..media.audio_enhancements import resolve_music_track
from ..utils.async_helpers import run_in_thread
from . import store
from .assemble import assemble_video
from .director import plan_research, write_proposal, write_scene_prompts
from .research import format_research_notes, gather_sources
from .voice import voice_scene

logger = logging.getLogger(__name__)


def _fail(production_id: str, stage: str, error: Exception, fallback_status: str) -> Dict[str, Any]:
    logger.error("Studio %s failed for %s: %s", stage, production_id, error, exc_info=True)
    store.update(
        production_id,
        status="error",
        error=str(error) or type(error).__name__,
        error_stage=stage,
        retry_status=fallback_status,
        progress_message=None,
    )
    return {"status": "error"}


def _progress(production_id: str, status: str, message: str) -> None:
    store.update(production_id, status=status, progress_message=message, error=None, error_stage=None)


async def studio_direct(ctx: Dict[str, Any], production_id: str, feedback: Optional[str] = None) -> Dict[str, Any]:
    """Research the topic (once) and write or revise the director's proposal."""
    production = store.load(production_id)
    if not production:
        return {"status": "missing"}
    brief = production["brief"]
    previous = production.get("proposal")
    try:
        research = production.get("research")
        if brief.get("research") and research is None:
            _progress(production_id, "researching", "Looking up facts…")
            queries = await plan_research(brief)
            sources = await run_in_thread(gather_sources, queries)
            research = {"queries": queries, "sources": sources}
            store.update(production_id, research=research)
        _progress(
            production_id,
            "directing",
            "Revising the script…" if feedback else "Writing the script and scenes…",
        )
        notes = format_research_notes((research or {}).get("sources") or [])
        proposal = await write_proposal(brief, notes, feedback, previous if feedback else None)
        store.update(
            production_id,
            status="proposal",
            proposal=proposal,
            revision=int(production.get("revision", 0)) + 1,
            approved_at=None,
            prompts=None,
            progress_message=None,
            error=None,
            error_stage=None,
        )
        return {"status": "proposal"}
    except Exception as error:
        return _fail(production_id, "direct", error, "proposal" if previous else "new")


async def studio_prompts(ctx: Dict[str, Any], production_id: str) -> Dict[str, Any]:
    """Write one standalone video prompt per approved scene."""
    production = store.load(production_id)
    if not production or not production.get("proposal"):
        return {"status": "missing"}
    try:
        _progress(production_id, "prompting", "Writing the scene prompts…")
        prompts = await write_scene_prompts(production["brief"], production["proposal"])
        store.update(production_id, status="shooting", prompts=prompts, progress_message=None)
        return {"status": "shooting"}
    except Exception as error:
        return _fail(production_id, "prompts", error, "proposal")


def _voice_key(scene: Dict[str, Any], voice: str, speed: int) -> str:
    raw = json.dumps([scene.get("narration"), scene.get("stage"), voice, speed], ensure_ascii=False)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def slugify(value: str) -> str:
    ascii_value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", ascii_value).strip("-").lower()
    return slug[:50] or "studio-video"


async def studio_render(ctx: Dict[str, Any], production_id: str) -> Dict[str, Any]:
    """Voice the script and assemble the final video from the uploaded clips."""
    production = store.load(production_id)
    if not production or not production.get("proposal"):
        return {"status": "missing"}
    brief = production["brief"]
    proposal = production["proposal"]
    directory = store.production_dir(production_id)
    try:
        voice = brief.get("voice") or "sw-TZ-DaudiNeural"
        speed = int(brief.get("voice_speed", 0))
        scenes = []
        voice_cache: Dict[str, Any] = dict(production.get("voice_cache") or {})
        total = len(proposal["scenes"])
        for scene in proposal["scenes"]:
            number = scene["number"]
            clip = directory / store.scene_clip_name(number)
            if not clip.exists():
                raise RuntimeError(f"Scene {number} has no clip yet. Upload it first.")
            _progress(production_id, "rendering", f"Recording the voice: scene {number} of {total}…")
            key = _voice_key(scene, voice, speed)
            voice_path = directory / "voice" / f"{key}.wav"
            cached = voice_cache.get(key)
            if not cached or not voice_path.exists():
                cached = await voice_scene(scene["narration"], scene["stage"], voice, voice_path, speed)
                voice_cache[key] = cached
                store.update(production_id, voice_cache=voice_cache)
            scenes.append(
                {
                    "clip": clip,
                    "voice": voice_path,
                    "voice_duration": cached["duration"],
                    "words": cached["words"],
                    "overlay_text": scene.get("overlay_text") if brief.get("overlays", True) else "",
                }
            )

        _progress(production_id, "rendering", "Putting the video together…")
        filename = f"{slugify(proposal.get('title_english') or proposal.get('title', ''))}-{production_id[:6]}.mp4"
        output_path = store.output_dir() / filename
        music = resolve_music_track(brief.get("music")) if brief.get("music") else None
        result = await run_in_thread(
            assemble_video,
            scenes,
            brief["aspect_ratio"],
            output_path,
            proposal.get("hook_title") if brief.get("hook_title", True) else None,
            bool(brief.get("captions", True)),
            brief.get("caption_template") or "default",
            music,
            float(brief.get("music_volume", 0.12)),
        )
        previous = (production.get("final") or {}).get("filename")
        if previous and previous != filename:
            (store.output_dir() / Path(previous).name).unlink(missing_ok=True)
        store.update(
            production_id,
            status="done",
            final={"filename": filename, "duration": result["duration"], "rendered_at": store.now_iso()},
            progress_message=None,
        )
        return {"status": "done"}
    except Exception as error:
        return _fail(production_id, "render", error, "shooting")


def delete_production_files(production: Dict[str, Any]) -> None:
    final = (production.get("final") or {}).get("filename")
    if final:
        (store.output_dir() / Path(final).name).unlink(missing_ok=True)
    shutil.rmtree(store.production_dir(production["id"]), ignore_errors=True)

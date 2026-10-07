"""Studio API: create, review, approve, upload scene clips, and render."""

import logging
from pathlib import Path
from typing import Any, Dict, Optional

import aiofiles
from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ...auth_headers import resolve_authenticated_user_id
from ...caption_templates import get_template_names
from ...config import get_config
from ...database import get_db
from ...media.audio_enhancements import list_music_tracks
from ...media.ffmpeg import ffprobe_duration
from ...studio import store
from ...studio.director import SCENE_SECONDS, scene_count
from ...studio.jobs import delete_production_files
from ...studio.voice import DEFAULT_VOICE, VOICES
from ...utils.async_helpers import run_in_thread
from ...workers.job_queue import JobQueue

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/studio", tags=["studio"])

MAX_CLIP_BYTES = 300 * 1024 * 1024
MIN_DURATION = 20
MAX_DURATION = 180


class CreateProduction(BaseModel):
    idea: str = Field(min_length=10, max_length=4000)
    aspect_ratio: str = Field(default="9:16", pattern=r"^(9:16|16:9)$")
    duration_seconds: int = Field(default=60, ge=MIN_DURATION, le=MAX_DURATION)
    voice: str = DEFAULT_VOICE
    voice_speed: int = Field(default=0, ge=-20, le=20)
    research: bool = True
    captions: bool = True
    caption_template: str = "default"
    hook_title: bool = True
    overlays: bool = True
    music: Optional[str] = None
    music_volume: float = Field(default=0.12, ge=0.0, le=0.5)


class SceneEdit(BaseModel):
    narration: Optional[str] = Field(default=None, max_length=600)
    overlay_text: Optional[str] = Field(default=None, max_length=60)


class Revision(BaseModel):
    feedback: str = Field(min_length=3, max_length=2000)


class RenderSettings(BaseModel):
    voice: Optional[str] = None
    voice_speed: Optional[int] = Field(default=None, ge=-20, le=20)
    captions: Optional[bool] = None
    caption_template: Optional[str] = None
    hook_title: Optional[bool] = None
    overlays: Optional[bool] = None
    music: Optional[str] = None
    music_volume: Optional[float] = Field(default=None, ge=0.0, le=0.5)


def _clean_brief(data: Dict[str, Any]) -> Dict[str, Any]:
    if data.get("voice") is not None and data["voice"] not in VOICES:
        raise HTTPException(400, "Unknown voice")
    if data.get("caption_template") is not None and data["caption_template"] not in get_template_names():
        raise HTTPException(400, "Unknown caption template")
    music = data.get("music")
    if music is not None and music not in ("", "random") and music not in list_music_tracks():
        raise HTTPException(400, "Unknown music track")
    if "music" in data and not music:
        data["music"] = None
    if "duration_seconds" in data:
        data["duration_seconds"] = scene_count(data["duration_seconds"]) * SCENE_SECONDS
    return data


async def _user(request: Request, db: AsyncSession) -> str:
    return await resolve_authenticated_user_id(request, db, get_config())


def _owned(production_id: str, user_id: str) -> Dict[str, Any]:
    production = store.load(production_id)
    if not production or production.get("user_id") != user_id:
        raise HTTPException(404, "Production not found")
    return production


def _require_idle(production: Dict[str, Any]) -> None:
    if store.is_busy(production):
        raise HTTPException(409, "Katakata is still working on this video. Wait for it to finish.")


def _public(production: Dict[str, Any]) -> Dict[str, Any]:
    data = {key: value for key, value in production.items() if key not in {"voice_cache", "user_id"}}
    data["stuck"] = production.get("status") in store.BUSY_STATUSES and not store.is_busy(production)
    directory = store.production_dir(production["id"])
    scenes = (production.get("proposal") or {}).get("scenes") or []
    data["clips"] = {
        str(scene["number"]): (directory / store.scene_clip_name(scene["number"])).exists()
        for scene in scenes
    }
    return data


async def _enqueue(function: str, *args: Any) -> None:
    try:
        await JobQueue.enqueue_job(function, *args)
    except Exception as error:
        logger.error("Could not enqueue %s: %s", function, error)
        raise HTTPException(503, "The background worker is not reachable. Is Katakata fully started?")


@router.get("/options")
async def options():
    return {
        "voices": [{"id": key, "label": label} for key, label in VOICES.items()],
        "default_voice": DEFAULT_VOICE,
        "caption_templates": get_template_names(),
        "music": list_music_tracks(),
        "durations": [30, 60, 90, 120, 180],
        "max_duration": MAX_DURATION,
    }


@router.get("/")
async def list_productions(request: Request, db: AsyncSession = Depends(get_db)):
    user_id = await _user(request, db)
    productions = await run_in_thread(store.list_for_user, user_id)
    return {
        "productions": [
            {
                "id": item["id"],
                "title": (item.get("proposal") or {}).get("title") or item["brief"]["idea"][:80],
                "status": item.get("status"),
                "created_at": item.get("created_at"),
                "duration_seconds": item["brief"]["duration_seconds"],
                "aspect_ratio": item["brief"]["aspect_ratio"],
                "final": item.get("final"),
            }
            for item in productions
        ]
    }


@router.post("/", status_code=201)
async def create_production(body: CreateProduction, request: Request, db: AsyncSession = Depends(get_db)):
    user_id = await _user(request, db)
    brief = _clean_brief(body.model_dump())
    brief["idea"] = brief["idea"].strip()
    production_id = store.new_production_id()
    production = {
        "id": production_id,
        "user_id": user_id,
        "created_at": store.now_iso(),
        "status": "researching" if brief["research"] else "directing",
        "progress_message": "Starting…",
        "brief": brief,
        "research": None,
        "proposal": None,
        "prompts": None,
        "final": None,
        "revision": 0,
    }
    with store.production_lock(production_id):
        store.save(production)
    await _enqueue("studio_direct", production_id)
    return _public(production)


@router.get("/{production_id}")
async def get_production(production_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    user_id = await _user(request, db)
    return _public(_owned(production_id, user_id))


@router.delete("/{production_id}")
async def delete_production(production_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    user_id = await _user(request, db)
    production = _owned(production_id, user_id)
    await run_in_thread(delete_production_files, production)
    return {"deleted": True}


@router.post("/{production_id}/revise", status_code=202)
async def revise(production_id: str, body: Revision, request: Request, db: AsyncSession = Depends(get_db)):
    """Ask the director to rewrite the proposal with the user's feedback."""
    user_id = await _user(request, db)
    production = _owned(production_id, user_id)
    _require_idle(production)
    store.update(production_id, status="directing", progress_message="Revising the script…", error=None)
    await _enqueue("studio_direct", production_id, body.feedback.strip() if production.get("proposal") else None)
    return {"status": "directing"}


@router.post("/{production_id}/retry", status_code=202)
async def retry(production_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    """Re-run the step that failed."""
    user_id = await _user(request, db)
    production = _owned(production_id, user_id)
    status = production.get("status")
    stuck = status in store.BUSY_STATUSES and not store.is_busy(production)
    if status != "error" and not stuck:
        raise HTTPException(409, "Nothing to retry")
    stage = production.get("error_stage") or {
        "prompting": "prompts", "rendering": "render",
    }.get(status, "direct")
    if stage == "prompts":
        store.update(production_id, status="prompting", error=None)
        await _enqueue("studio_prompts", production_id)
    elif stage == "render":
        store.update(production_id, status="rendering", error=None)
        await _enqueue("studio_render", production_id)
    else:
        store.update(production_id, status="directing", error=None)
        await _enqueue("studio_direct", production_id)
    return {"status": "retrying"}


@router.patch("/{production_id}/scenes/{number}")
async def edit_scene(
    production_id: str, number: int, body: SceneEdit, request: Request, db: AsyncSession = Depends(get_db)
):
    """Edit a scene's narration or overlay. Narration edits keep the approval:
    prompts do not contain the narration."""
    user_id = await _user(request, db)
    production = _owned(production_id, user_id)
    _require_idle(production)
    with store.production_lock(production_id):
        production = store.load(production_id)
        scenes = (production.get("proposal") or {}).get("scenes") or []
        scene = next((item for item in scenes if item["number"] == number), None)
        if not scene:
            raise HTTPException(404, "Scene not found")
        if body.narration is not None:
            narration = " ".join(body.narration.split())
            if not narration:
                raise HTTPException(400, "Narration cannot be empty")
            scene["narration"] = narration
        if body.overlay_text is not None:
            scene["overlay_text"] = body.overlay_text.strip()
        store.save(production)
    return _public(production)


@router.post("/{production_id}/approve", status_code=202)
async def approve(production_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    user_id = await _user(request, db)
    production = _owned(production_id, user_id)
    _require_idle(production)
    if not production.get("proposal"):
        raise HTTPException(409, "There is no proposal to approve yet")
    store.update(production_id, status="prompting", approved_at=store.now_iso(), progress_message="Writing the scene prompts…")
    await _enqueue("studio_prompts", production_id)
    return {"status": "prompting"}


@router.post("/{production_id}/scenes/{number}/clip")
async def upload_clip(
    production_id: str,
    number: int,
    request: Request,
    clip: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
):
    user_id = await _user(request, db)
    production = _owned(production_id, user_id)
    if production.get("status") == "rendering":
        raise HTTPException(409, "Wait for the current render to finish")
    scenes = (production.get("proposal") or {}).get("scenes") or []
    if not any(scene["number"] == number for scene in scenes):
        raise HTTPException(404, "Scene not found")
    directory = store.production_dir(production_id)
    target = directory / store.scene_clip_name(number)
    partial = target.with_suffix(".part")
    written = 0
    async with aiofiles.open(partial, "wb") as destination:
        while chunk := await clip.read(1024 * 1024):
            written += len(chunk)
            if written > MAX_CLIP_BYTES:
                await destination.close()
                partial.unlink(missing_ok=True)
                raise HTTPException(413, "That clip is too large (300 MB maximum)")
            await destination.write(chunk)
    try:
        duration = await run_in_thread(ffprobe_duration, partial)
    except Exception:
        duration = 0
    if duration <= 0:
        partial.unlink(missing_ok=True)
        raise HTTPException(400, "That file is not a video Katakata can read")
    partial.replace(target)
    return {"number": number, "duration": round(duration, 2)}


@router.delete("/{production_id}/scenes/{number}/clip")
async def delete_clip(production_id: str, number: int, request: Request, db: AsyncSession = Depends(get_db)):
    user_id = await _user(request, db)
    _owned(production_id, user_id)
    (store.production_dir(production_id) / store.scene_clip_name(number)).unlink(missing_ok=True)
    return {"deleted": True}


@router.post("/{production_id}/render", status_code=202)
async def render(
    production_id: str,
    request: Request,
    body: Optional[RenderSettings] = None,
    db: AsyncSession = Depends(get_db),
):
    user_id = await _user(request, db)
    production = _owned(production_id, user_id)
    _require_idle(production)
    scenes = (production.get("proposal") or {}).get("scenes") or []
    if not scenes:
        raise HTTPException(409, "There is no approved script yet")
    directory = store.production_dir(production_id)
    missing = [scene["number"] for scene in scenes if not (directory / store.scene_clip_name(scene["number"])).exists()]
    if missing:
        raise HTTPException(409, f"Upload clips for scenes {', '.join(map(str, missing))} first")
    changes = _clean_brief({key: value for key, value in (body.model_dump() if body else {}).items() if value is not None})
    with store.production_lock(production_id):
        production = store.load(production_id)
        production["brief"].update(changes)
        production["status"] = "rendering"
        production["progress_message"] = "Starting the render…"
        production["error"] = None
        store.save(production)
    await _enqueue("studio_render", production_id)
    return {"status": "rendering"}


@router.get("/{production_id}/files/{kind}")
async def get_file(production_id: str, kind: str, request: Request, db: AsyncSession = Depends(get_db)):
    """Serve the final video (``final``) or a scene clip (``scene-03``)."""
    user_id = await _user(request, db)
    production = _owned(production_id, user_id)
    if kind == "final":
        filename = (production.get("final") or {}).get("filename")
        path = store.output_dir() / Path(filename).name if filename else None
        download_name = filename
    elif kind.startswith("scene-") and kind[6:].isdigit():
        path = store.production_dir(production_id) / store.scene_clip_name(int(kind[6:]))
        download_name = path.name
    else:
        raise HTTPException(404, "File not found")
    if not path or not path.exists():
        raise HTTPException(404, "File not found")
    return FileResponse(path, media_type="video/mp4", filename=download_name)

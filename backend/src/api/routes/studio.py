"""Studio API: create, review, approve, upload scene clips, and render."""

import logging
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

import aiofiles
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
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
MAX_DURATION = 600
GENRE_DURATIONS = {
    "explainer": {"min": 20, "max": 180, "choices": [30, 60, 90, 120, 180]},
    "documentary": {"min": 60, "max": 600, "choices": [120, 180, 300, 420, 600]},
}
MAX_VOICE_SAMPLE_BYTES = 60 * 1024 * 1024


class CreateProduction(BaseModel):
    idea: str = Field(min_length=10, max_length=4000)
    genre: Literal["explainer", "documentary"] = "explainer"
    style_id: Optional[str] = None
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


class GenerateRequest(BaseModel):
    scenes: Optional[list[int]] = None
    replace: bool = False


class RenderSettings(BaseModel):
    voice: Optional[str] = Field(default=None, max_length=60)
    voice_speed: Optional[int] = Field(default=None, ge=-20, le=20)
    captions: Optional[bool] = None
    caption_template: Optional[str] = None
    hook_title: Optional[bool] = None
    overlays: Optional[bool] = None
    music: Optional[str] = None
    music_volume: Optional[float] = Field(default=None, ge=0.0, le=0.5)


class StyleCreate(BaseModel):
    name: str = Field(min_length=2, max_length=80)
    urls: List[str] = Field(min_length=1, max_length=6)


def _owned_asset(kind: str, asset_id: str, user_id: str) -> Dict[str, Any]:
    record = store.load_asset(kind, asset_id)
    if not record or record.get("user_id") != user_id:
        raise HTTPException(404, "Not found")
    return record


def _clean_brief(data: Dict[str, Any], user_id: str) -> Dict[str, Any]:
    voice = data.get("voice")
    if voice is not None and voice not in VOICES:
        record = store.load_asset("voices", voice.split(":", 1)[1]) if voice.startswith("custom:") else None
        if not record or record.get("user_id") != user_id:
            raise HTTPException(400, "Unknown voice")
        if record.get("status") != "ready":
            raise HTTPException(409, "That cloned voice is still being learned")
    if data.get("style_id"):
        style = store.load_asset("styles", data["style_id"])
        if not style or style.get("user_id") != user_id:
            raise HTTPException(400, "Unknown inspiration style")
        if style.get("status") != "ready":
            raise HTTPException(409, "That inspiration style is still being analysed")
    if data.get("caption_template") is not None and data["caption_template"] not in get_template_names():
        raise HTTPException(400, "Unknown caption template")
    music = data.get("music")
    if music is not None and music not in ("", "random") and music not in list_music_tracks():
        raise HTTPException(400, "Unknown music track")
    if "music" in data and not music:
        data["music"] = None
    if "duration_seconds" in data:
        limits = GENRE_DURATIONS[data.get("genre") or "explainer"]
        seconds = scene_count(data["duration_seconds"]) * SCENE_SECONDS
        if not limits["min"] <= seconds <= limits["max"]:
            raise HTTPException(
                400, f"{(data.get('genre') or 'explainer').title()} videos can be {limits['min']}-{limits['max']} seconds long"
            )
        data["duration_seconds"] = seconds
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
        "durations": GENRE_DURATIONS["explainer"]["choices"],
        "genres": {name: limits["choices"] for name, limits in GENRE_DURATIONS.items()},
        "max_duration": MAX_DURATION,
        "auto_generate": bool(get_config().google_api_key),
    }


# --- Inspiration styles ----------------------------------------------------


def _public_asset(record: Dict[str, Any]) -> Dict[str, Any]:
    return {key: value for key, value in record.items() if key not in {"user_id", "sample"}}


@router.get("/styles")
async def list_styles(request: Request, db: AsyncSession = Depends(get_db)):
    user_id = await _user(request, db)
    return {"styles": [_public_asset(item) for item in store.list_assets("styles", user_id)]}


@router.post("/styles", status_code=201)
async def create_style(body: StyleCreate, request: Request, db: AsyncSession = Depends(get_db)):
    user_id = await _user(request, db)
    urls = [url.strip() for url in body.urls if url.strip()]
    if not urls or not all("youtube.com" in url or "youtu.be" in url for url in urls):
        raise HTTPException(400, "Add YouTube channel or video links")
    record = store.save_asset("styles", {
        "id": store.new_production_id(), "user_id": user_id, "created_at": store.now_iso(),
        "name": body.name.strip(), "urls": urls, "status": "queued", "summary": "", "guide": "", "videos": [],
    })
    await _enqueue("studio_style", record["id"])
    return _public_asset(record)


@router.post("/styles/{style_id}/refresh", status_code=202)
async def refresh_style(style_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    user_id = await _user(request, db)
    _owned_asset("styles", style_id, user_id)
    store.update_asset("styles", style_id, status="queued", error=None)
    await _enqueue("studio_style", style_id)
    return {"status": "queued"}


@router.delete("/styles/{style_id}")
async def delete_style(style_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    user_id = await _user(request, db)
    _owned_asset("styles", style_id, user_id)
    import shutil

    shutil.rmtree(store.asset_dir("styles", style_id), ignore_errors=True)
    return {"deleted": True}


# --- Cloned voices ---------------------------------------------------------


@router.get("/voices")
async def list_voices(request: Request, db: AsyncSession = Depends(get_db)):
    user_id = await _user(request, db)
    return {"voices": [_public_asset(item) for item in store.list_assets("voices", user_id)]}


@router.post("/voices", status_code=201)
async def create_voice(
    request: Request,
    name: str = Form(..., min_length=2, max_length=60),
    base_voice: str = Form(DEFAULT_VOICE),
    consent: bool = Form(False),
    sample: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
):
    """Upload a recording to clone. Only your own voice, or one you have permission to use."""
    user_id = await _user(request, db)
    if not consent:
        raise HTTPException(400, "Confirm that this is your voice or that you have permission to clone it")
    if base_voice not in VOICES:
        raise HTTPException(400, "Unknown base voice")
    voice_id = store.new_production_id()
    directory = store.asset_dir("voices", voice_id)
    directory.mkdir(parents=True, exist_ok=True)
    suffix = Path(sample.filename or "sample.wav").suffix.lower()[:6] or ".wav"
    target = directory / f"sample{suffix}"
    written = 0
    async with aiofiles.open(target, "wb") as destination:
        while chunk := await sample.read(1024 * 1024):
            written += len(chunk)
            if written > MAX_VOICE_SAMPLE_BYTES:
                await destination.close()
                import shutil

                shutil.rmtree(directory, ignore_errors=True)
                raise HTTPException(413, "That recording is too large (60 MB maximum)")
            await destination.write(chunk)
    try:
        seconds = await run_in_thread(ffprobe_duration, target)
    except Exception:
        seconds = 0
    if seconds < 20:
        import shutil

        shutil.rmtree(directory, ignore_errors=True)
        raise HTTPException(400, "Upload at least 30 seconds of clear speech (a quiet room, no music)")
    record = store.save_asset("voices", {
        "id": voice_id, "user_id": user_id, "created_at": store.now_iso(), "name": name.strip(),
        "base_voice": base_voice, "sample": target.name, "duration": round(seconds, 1),
        "status": "queued", "consent_at": store.now_iso(),
    })
    await _enqueue("studio_voice", voice_id)
    return _public_asset(record)


@router.delete("/voices/{voice_id}")
async def delete_voice(voice_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    user_id = await _user(request, db)
    _owned_asset("voices", voice_id, user_id)
    import shutil

    shutil.rmtree(store.asset_dir("voices", voice_id), ignore_errors=True)
    return {"deleted": True}


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
    brief = _clean_brief(body.model_dump(), user_id)
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
        "prompting": "prompts", "rendering": "render", "generating": "generate",
    }.get(status, "direct")
    if stage == "prompts":
        store.update(production_id, status="prompting", error=None)
        await _enqueue("studio_prompts", production_id)
    elif stage == "generate":
        store.update(production_id, status="generating", error=None)
        await _enqueue("studio_generate", production_id, _missing_clips(production))
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


def _missing_clips(production: Dict[str, Any]) -> list[int]:
    directory = store.production_dir(production["id"])
    return [
        item["number"]
        for item in ((production.get("prompts") or {}).get("prompts") or [])
        if not (directory / store.scene_clip_name(item["number"])).exists()
    ]


@router.post("/{production_id}/generate", status_code=202)
async def generate(
    production_id: str,
    request: Request,
    body: Optional[GenerateRequest] = None,
    db: AsyncSession = Depends(get_db),
):
    """Generate scene clips with Gemini Omni Flash (paid, needs GOOGLE_API_KEY)."""
    user_id = await _user(request, db)
    production = _owned(production_id, user_id)
    _require_idle(production)
    if not production.get("prompts"):
        raise HTTPException(409, "Approve the script first so the scene prompts exist")
    if not get_config().google_api_key:
        raise HTTPException(400, "Add GOOGLE_API_KEY (with billing on) to .env to generate clips automatically")
    body = body or GenerateRequest()
    available = {item["number"] for item in production["prompts"]["prompts"]}
    if body.scenes:
        numbers = sorted({number for number in body.scenes if number in available})
        if not body.replace:
            missing = set(_missing_clips(production))
            numbers = [number for number in numbers if number in missing] or numbers
    else:
        numbers = _missing_clips(production)
    if not numbers:
        raise HTTPException(409, "Every scene already has a clip")
    store.update(production_id, status="generating", progress_message="Starting Gemini…", error=None)
    await _enqueue("studio_generate", production_id, numbers)
    return {"status": "generating", "scenes": numbers}


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
    changes = _clean_brief(
        {key: value for key, value in (body.model_dump() if body else {}).items() if value is not None}, user_id
    )
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

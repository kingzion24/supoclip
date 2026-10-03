"""Optional audio layers for rendered clips: background music and a spoken hook.

Both run as one ffmpeg pass over a finished clip. The video stream is copied,
so the audio mix never re-encodes or degrades the picture. Any failure leaves
the original clip untouched.
"""

import random
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from .common import logger
from .ffmpeg import ffprobe_duration, ffprobe_has_audio, run_ffmpeg_command

MUSIC_DIR = Path(__file__).resolve().parent.parent.parent / "music"
SUPPORTED_MUSIC_EXTENSIONS = (".mp3", ".m4a", ".aac", ".wav", ".ogg")

DEFAULT_MUSIC_VOLUME = 0.15
# The spoken hook starts shortly after the clip does; the original audio is
# turned down to this level while it plays so the voice stays intelligible.
VOICEOVER_DELAY_SECONDS = 0.3
VOICEOVER_DUCKED_VOLUME = 0.3
MUSIC_FADE_OUT_SECONDS = 1.5

# Microsoft Edge neural voices used for the spoken hook (free, no API key).
VOICE_BY_LANGUAGE = {
    "sw": "sw-TZ-RehemaNeural",
    "en": "en-US-AriaNeural",
    "fr": "fr-FR-DeniseNeural",
    "es": "es-ES-ElviraNeural",
    "pt": "pt-BR-FranciscaNeural",
    "de": "de-DE-KatjaNeural",
    "it": "it-IT-ElsaNeural",
    "ar": "ar-EG-SalmaNeural",
    "hi": "hi-IN-SwaraNeural",
}
FALLBACK_VOICE = "en-US-EmmaMultilingualNeural"
_VOICE_PATTERN = re.compile(r"^[a-z]{2,3}-[A-Z]{2}-[A-Za-z]+Neural$")


def list_music_tracks() -> List[str]:
    """Return the music files available in ``backend/music/``."""
    if not MUSIC_DIR.exists():
        return []
    return sorted(
        path.name
        for path in MUSIC_DIR.iterdir()
        if path.is_file() and path.suffix.lower() in SUPPORTED_MUSIC_EXTENSIONS
    )


def normalize_audio_settings(
    background_music: Any = None,
    music_volume: Any = None,
    hook_voiceover: Any = None,
    voiceover_voice: Any = None,
) -> Dict[str, Any]:
    """Validate the per-task audio options sent by clients."""
    music: Optional[str] = None
    if isinstance(background_music, str):
        choice = background_music.strip()
        if choice == "random":
            music = "random"
        elif choice and Path(choice).name == choice and choice in list_music_tracks():
            music = choice

    try:
        volume = float(music_volume)
    except (TypeError, ValueError):
        volume = DEFAULT_MUSIC_VOLUME
    if volume != volume:  # NaN
        volume = DEFAULT_MUSIC_VOLUME
    volume = round(max(0.0, min(1.0, volume)), 3)

    voice = voiceover_voice.strip() if isinstance(voiceover_voice, str) else ""
    return {
        "background_music": music,
        "music_volume": volume,
        "hook_voiceover": hook_voiceover is True,
        "voiceover_voice": voice if _VOICE_PATTERN.match(voice) else None,
    }


def audio_settings_from_metadata(metadata: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Re-validate audio settings stored with a task (tracks may have been removed)."""
    stored = (metadata or {}).get("audio_settings")
    if not isinstance(stored, dict):
        stored = {}
    return normalize_audio_settings(
        stored.get("background_music"),
        stored.get("music_volume"),
        stored.get("hook_voiceover"),
        stored.get("voiceover_voice"),
    )


def has_audio_enhancements(settings: Optional[Dict[str, Any]]) -> bool:
    if not settings:
        return False
    return bool(settings.get("background_music") or settings.get("hook_voiceover"))


def choose_voice(language: Optional[str], voice: Optional[str] = None) -> str:
    """Pick the voiceover voice: an explicit choice, else one for the language."""
    if voice:
        return voice
    base = (language or "").split("_", 1)[0].lower()
    return VOICE_BY_LANGUAGE.get(base, FALLBACK_VOICE)


def resolve_music_track(choice: Optional[str]) -> Optional[Path]:
    if not choice:
        return None
    tracks = list_music_tracks()
    if not tracks:
        return None
    name = random.choice(tracks) if choice == "random" else choice
    path = MUSIC_DIR / name
    return path if path.exists() else None


def synthesize_voiceover(text: str, voice: str, output_path: Path) -> bool:
    """Speak ``text`` with an Edge neural voice into an mp3 file."""
    try:
        import edge_tts
    except ImportError:
        logger.warning("edge-tts is not installed; skipping hook voiceover")
        return False
    try:
        edge_tts.Communicate(text, voice).save_sync(str(output_path))
    except Exception as exc:
        logger.warning("Hook voiceover synthesis failed (%s): %s", voice, exc)
        return False
    return output_path.exists() and output_path.stat().st_size > 0


def build_audio_mix_command(
    clip_path: Path,
    output_path: Path,
    clip_duration: float,
    clip_has_audio: bool,
    music_path: Optional[Path] = None,
    music_volume: float = DEFAULT_MUSIC_VOLUME,
    voiceover_path: Optional[Path] = None,
    voiceover_duration: float = 0.0,
) -> List[str]:
    """Build the ffmpeg command that layers music and a voiceover on a clip."""
    command = ["ffmpeg", "-y", "-i", str(clip_path)]
    filters: List[str] = []
    labels: List[str] = []
    next_input = 1

    if clip_has_audio:
        if voiceover_path:
            duck_start = VOICEOVER_DELAY_SECONDS
            duck_end = VOICEOVER_DELAY_SECONDS + voiceover_duration
            filters.append(
                f"[0:a]aresample=48000,volume='if(between(t,{duck_start:.3f},{duck_end:.3f}),"
                f"{VOICEOVER_DUCKED_VOLUME},1)':eval=frame[orig]"
            )
        else:
            filters.append("[0:a]aresample=48000[orig]")
        labels.append("[orig]")

    if music_path:
        command += ["-stream_loop", "-1", "-i", str(music_path)]
        fade_start = max(0.0, clip_duration - MUSIC_FADE_OUT_SECONDS)
        filters.append(
            f"[{next_input}:a]aresample=48000,atrim=0:{clip_duration:.3f},"
            f"volume={music_volume},"
            f"afade=t=out:st={fade_start:.3f}:d={MUSIC_FADE_OUT_SECONDS}[music]"
        )
        labels.append("[music]")
        next_input += 1

    if voiceover_path:
        delay_ms = int(VOICEOVER_DELAY_SECONDS * 1000)
        command += ["-i", str(voiceover_path)]
        filters.append(
            f"[{next_input}:a]aresample=48000,adelay={delay_ms}:all=1,volume=1.4[voice]"
        )
        labels.append("[voice]")

    filters.append(
        f"{''.join(labels)}amix=inputs={len(labels)}:duration=longest:normalize=0,"
        f"apad,atrim=0:{clip_duration:.3f}[out]"
    )
    command += [
        "-filter_complex",
        ";".join(filters),
        "-map",
        "0:v",
        "-map",
        "[out]",
        "-c:v",
        "copy",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-movflags",
        "+faststart",
        str(output_path),
    ]
    return command


def apply_audio_enhancements(
    clip_path: Path,
    settings: Optional[Dict[str, Any]],
    hook_title: Optional[str] = None,
    language: Optional[str] = None,
) -> bool:
    """Mix background music and/or a spoken hook into ``clip_path`` in place.

    Returns True when the clip was rewritten. Failures are logged and leave
    the clip as it was.
    """
    if not has_audio_enhancements(settings):
        return False
    assert settings is not None

    with tempfile.TemporaryDirectory(prefix="supoclip_audio_") as temp_dir:
        work_dir = Path(temp_dir)
        try:
            clip_duration = ffprobe_duration(clip_path)
            if clip_duration <= 0:
                return False

            music_path = resolve_music_track(settings.get("background_music"))
            if settings.get("background_music") and not music_path:
                logger.warning("Background music requested but no track found in %s", MUSIC_DIR)

            voiceover_path: Optional[Path] = None
            voiceover_duration = 0.0
            if settings.get("hook_voiceover") and hook_title:
                voice = choose_voice(language, settings.get("voiceover_voice"))
                candidate = work_dir / "hook_voiceover.mp3"
                if synthesize_voiceover(hook_title, voice, candidate):
                    voiceover_path = candidate
                    voiceover_duration = ffprobe_duration(candidate)

            if not music_path and not voiceover_path:
                return False

            mixed_path = work_dir / f"mixed{clip_path.suffix or '.mp4'}"
            command = build_audio_mix_command(
                clip_path,
                mixed_path,
                clip_duration,
                ffprobe_has_audio(clip_path),
                music_path=music_path,
                music_volume=float(settings.get("music_volume", DEFAULT_MUSIC_VOLUME)),
                voiceover_path=voiceover_path,
                voiceover_duration=voiceover_duration,
            )
            result = run_ffmpeg_command(command)
            if result.returncode != 0 or not mixed_path.exists():
                return False

            shutil.move(str(mixed_path), str(clip_path))
            logger.info(
                "Added audio layers to %s (music=%s, voiceover=%s)",
                clip_path.name,
                music_path.name if music_path else None,
                bool(voiceover_path),
            )
            return True
        except Exception as exc:
            logger.warning("Audio enhancement failed for %s: %s", clip_path.name, exc)
            return False

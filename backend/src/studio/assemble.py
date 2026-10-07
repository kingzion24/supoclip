"""Assemble a Studio video from uploaded scene clips and the voiced script.

Each scene lasts as long as its narration. A clip that is longer is trimmed;
a shorter one is slowed down a little (at most 1.3x) and then holds its last
frame. Scenes are joined with hard cuts (the director matches each ending to
the next opening), the clips' own sound effects stay quietly underneath, and
the voice-over, music, captions and overlay phrases are added in one final
encode.
"""

import shutil
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..media.captions import (
    ass_fonts_dir,
    ass_timestamp,
    build_assemblyai_ass_subtitles,
    escape_ass_text,
)
from ..media.common import LOUDNORM_FILTER, OUTPUT_FPS, logger
from ..media.ffmpeg import (
    build_final_video_encode_args,
    ffprobe_duration,
    ffprobe_has_audio,
    run_ffmpeg_command,
    subtitles_filter_fragment,
)
from ..caption_templates import get_template

FRAME_SIZES = {"9:16": (1080, 1920), "16:9": (1920, 1080)}
MIN_SCENE_SECONDS = 3.0
MAX_SLOWDOWN = 1.3
SFX_VOLUME = 0.35
DEFAULT_MUSIC_VOLUME = 0.12
FADE_SECONDS = 0.4
HOOK_SECONDS = 4.0


def fit_plan(clip_duration: float, target: float) -> Tuple[float, float]:
    """Return (slowdown factor, seconds of held last frame) to fill ``target``."""
    if clip_duration <= 0:
        return 1.0, target
    if clip_duration >= target:
        return 1.0, 0.0
    factor = min(MAX_SLOWDOWN, target / clip_duration)
    hold = max(0.0, target - clip_duration * factor)
    return factor, hold


def build_scene_command(
    clip_path: Path,
    output_path: Path,
    target: float,
    clip_duration: float,
    has_audio: bool,
    size: Tuple[int, int],
) -> List[str]:
    """Scale/crop a clip to the frame, fit it to ``target`` seconds, and give
    it a uniform audio track so scenes can be joined without re-encoding."""
    width, height = size
    factor, hold = fit_plan(clip_duration, target)
    video = (
        f"[0:v]setpts={factor:.4f}*PTS,"
        f"scale={width}:{height}:force_original_aspect_ratio=increase,"
        f"crop={width}:{height},setsar=1,fps={OUTPUT_FPS}"
    )
    if hold > 0:
        video += f",tpad=stop_mode=clone:stop_duration={hold:.3f}"
    video += f",trim=duration={target:.3f},format=yuv420p[v]"
    command = ["ffmpeg", "-y", "-i", str(clip_path)]
    if has_audio:
        audio = f"[0:a]aresample=48000,atempo={1 / factor:.4f},"
    else:
        command += ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo"]
        audio = "[1:a]"
    audio += f"apad,atrim=duration={target:.3f},aformat=sample_rates=48000:channel_layouts=stereo[a]"
    return command + [
        "-filter_complex", f"{video};{audio}",
        "-map", "[v]", "-map", "[a]",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "17", "-pix_fmt", "yuv420p",
        "-r", str(OUTPUT_FPS),
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
        "-t", f"{target:.3f}",
        str(output_path),
    ]


def build_mix_command(
    body_path: Path,
    voice_path: Path,
    output_path: Path,
    total: float,
    subtitle_filters: List[str],
    music_path: Optional[Path] = None,
    music_volume: float = DEFAULT_MUSIC_VOLUME,
) -> List[str]:
    """Final pass: burn captions, lay the voice over quiet SFX, and duck the
    music under the voice."""
    command = ["ffmpeg", "-y", "-i", str(body_path), "-i", str(voice_path)]
    fade_out = max(0.0, total - 1.5)
    filters = [
        f"[0:a]volume={SFX_VOLUME}[sfx]",
        "[1:a]aresample=48000,aformat=channel_layouts=stereo,apad,"
        f"atrim=duration={total:.3f},asplit=2[voice][key]",
    ]
    mix_inputs = "[sfx][voice]"
    count = 2
    if music_path:
        command += ["-stream_loop", "-1", "-i", str(music_path)]
        filters.append(
            f"[2:a]aresample=48000,aformat=channel_layouts=stereo,"
            f"atrim=duration={total:.3f},volume={music_volume:.3f},"
            f"afade=t=in:d=1,afade=t=out:st={fade_out:.3f}:d=1.5[music]"
        )
        filters.append("[music][key]sidechaincompress=threshold=0.04:ratio=6:attack=30:release=500[ducked]")
        mix_inputs += "[ducked]"
        count = 3
    else:
        filters.append("[key]anullsink")
    filters.append(f"{mix_inputs}amix=inputs={count}:duration=first:normalize=0,{LOUDNORM_FILTER}[aout]")

    video = f"[0:v]fade=t=in:d={FADE_SECONDS},fade=t=out:st={max(0.0, total - FADE_SECONDS):.3f}:d={FADE_SECONDS}"
    for fragment in subtitle_filters:
        video += f",{fragment}"
    filters.append(video + ",setsar=1[vout]")
    return command + [
        "-filter_complex", ";".join(filters),
        "-map", "[vout]", "-map", "[aout]",
        *build_final_video_encode_args(),
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
        "-t", f"{total:.3f}",
        "-movflags", "+faststart",
        str(output_path),
    ]


def build_overlay_ass(
    overlays: List[Tuple[float, float, str]], size: Tuple[int, int]
) -> Optional[str]:
    """ASS file content for short phrases shown near the top of a scene."""
    events = [(start, end, text.strip()) for start, end, text in overlays if text.strip()]
    if not events:
        return None
    width, height = size
    font_px = round(width * (0.062 if height > width else 0.04))
    margin_v = round(height * 0.12)
    lines = [
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {width}",
        f"PlayResY: {height}",
        "WrapStyle: 0",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, "
        "Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: Overlay,THEBOLDFONT,{font_px},&H00FFFFFF,&H00FFFFFF,&H00000000,&H99000000,"
        f"1,0,0,0,100,100,0,0,3,{max(6, font_px // 5)},0,8,60,60,{margin_v},1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    for start, end, text in events:
        lines.append(
            f"Dialogue: 1,{ass_timestamp(start)},{ass_timestamp(end)},Overlay,,0,0,0,,"
            f"{{\\fad(200,200)}}{escape_ass_text(text.upper())}"
        )
    return "\n".join(lines) + "\n"


def assemble_video(
    scenes: List[Dict[str, Any]],
    aspect_ratio: str,
    output_path: Path,
    hook_title: Optional[str] = None,
    captions: bool = True,
    caption_template: str = "default",
    music_path: Optional[Path] = None,
    music_volume: float = DEFAULT_MUSIC_VOLUME,
) -> Dict[str, Any]:
    """Build the final video.

    ``scenes`` items need ``clip`` (Path), ``voice`` (Path to wav),
    ``voice_duration`` (seconds), ``words`` (times relative to the scene) and
    optionally ``overlay_text``.
    """
    size = FRAME_SIZES.get(aspect_ratio, FRAME_SIZES["9:16"])
    with tempfile.TemporaryDirectory(prefix="studio_render_") as temp:
        work = Path(temp)
        scene_files: List[Path] = []
        voice_files: List[Path] = []
        words: List[Dict[str, Any]] = []
        overlays: List[Tuple[float, float, str]] = []
        cursor = 0.0
        for index, scene in enumerate(scenes):
            target = max(MIN_SCENE_SECONDS, float(scene["voice_duration"]))
            clip_path = Path(scene["clip"])
            clip_duration = ffprobe_duration(clip_path)
            scene_file = work / f"scene{index:02d}.mp4"
            result = run_ffmpeg_command(
                build_scene_command(
                    clip_path, scene_file, target, clip_duration,
                    ffprobe_has_audio(clip_path), size,
                )
            )
            if result.returncode != 0 or not scene_file.exists():
                raise RuntimeError(f"Could not prepare the clip for scene {index + 1}")
            scene_files.append(scene_file)

            voice_file = work / f"voice{index:02d}.wav"
            result = run_ffmpeg_command(
                [
                    "ffmpeg", "-y", "-i", str(scene["voice"]),
                    "-af", f"apad=whole_dur={target:.3f},atrim=duration={target:.3f}",
                    "-ac", "1", "-ar", "48000", str(voice_file),
                ]
            )
            if result.returncode != 0:
                raise RuntimeError(f"Could not prepare the voice for scene {index + 1}")
            voice_files.append(voice_file)

            for word in scene.get("words", []):
                words.append(
                    {"text": word["text"], "start": cursor + word["start"], "end": cursor + word["end"]}
                )
            overlay = (scene.get("overlay_text") or "").strip()
            if overlay and not (index == 0 and hook_title):
                overlays.append((cursor + 0.3, cursor + min(target, 3.5), overlay))
            cursor += target

        total = cursor
        body = work / "body.mp4"
        _concat(scene_files, body, copy=True)
        voice = work / "voice.wav"
        _concat(voice_files, voice, copy=False)

        subtitle_filters: List[str] = []
        template = get_template(caption_template)
        if captions or hook_title:
            ass_path = work / "captions.ass"
            built = build_assemblyai_ass_subtitles(
                body, 0.0, total, size[0], size[1], ass_path,
                caption_template=caption_template,
                hook_title=hook_title or None,
                include_captions=captions,
                caption_words=words if captions else None,
            )
            if built and ass_path.exists():
                subtitle_filters.append(
                    subtitles_filter_fragment(ass_path, ass_fonts_dir(template.get("font_family")))
                )
        overlay_ass = build_overlay_ass(overlays, size)
        if overlay_ass:
            overlay_path = work / "overlays.ass"
            overlay_path.write_text(overlay_ass)
            subtitle_filters.append(
                subtitles_filter_fragment(overlay_path, ass_fonts_dir("THEBOLDFONT"))
            )

        final = work / "final.mp4"
        result = run_ffmpeg_command(
            build_mix_command(body, voice, final, total, subtitle_filters, music_path, music_volume),
            timeout=1800,
        )
        if result.returncode != 0 or not final.exists():
            logger.error("Studio final mix failed: %s", (result.stderr or "")[-2000:])
            raise RuntimeError("Could not render the final video")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(final), str(output_path))
    return {"duration": round(total, 2), "words": len(words)}


def _concat(parts: List[Path], target: Path, copy: bool) -> None:
    list_file = target.with_suffix(".txt")
    list_file.write_text("".join(f"file '{part.as_posix()}'\n" for part in parts))
    command = ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(list_file)]
    command += ["-c", "copy"] if copy else ["-ac", "1", "-ar", "48000"]
    result = run_ffmpeg_command(command + [str(target)], timeout=1800)
    if result.returncode != 0 or not target.exists():
        raise RuntimeError("Could not join the scenes")

"""Kiswahili voice-over for Studio videos with Edge neural voices.

Edge TTS reads a whole paragraph in one flat rhythm. To sound more like a
narrator, each sentence is synthesized on its own with a small pace and pitch
shift for its story stage, sentences are joined with natural pauses (longer
after questions and at the end of a scene), and the result goes through a
light voice polish (rumble cut, warmth, presence, gentle compression).

Edge reports when every word starts, so captions come straight from the
script with exact timing and no transcription.
"""

import asyncio
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Tuple

from ..media.common import logger
from ..media.ffmpeg import ffprobe_duration, run_ffmpeg_command

VOICES = {
    "sw-TZ-DaudiNeural": "Daudi (Tanzania, male)",
    "sw-TZ-RehemaNeural": "Rehema (Tanzania, female)",
    "sw-KE-RafikiNeural": "Rafiki (Kenya, male)",
    "sw-KE-ZuriNeural": "Zuri (Kenya, female)",
}
DEFAULT_VOICE = "sw-TZ-DaudiNeural"

# Small per-stage delivery changes: an energetic hook, a slower, lower "truth".
STAGE_PROSODY = {
    "hook": (5, 2),
    "disrupt": (2, 0),
    "secrets": (0, -1),
    "truth": (-6, -2),
    "elevation": (-2, 1),
}
SAMPLE_RATE = 48000
PAUSE_AFTER_SENTENCE = 0.28
PAUSE_AFTER_QUESTION = 0.5
PAUSE_AFTER_EXCLAMATION = 0.38
SCENE_TAIL_PAUSE = 0.45
VOICE_POLISH_FILTER = (
    "highpass=f=70,"
    "equalizer=f=180:t=q:w=1.2:g=2,"
    "equalizer=f=3200:t=q:w=1.5:g=1.5,"
    "acompressor=threshold=-20dB:ratio=2.5:attack=8:release=120:makeup=2"
)
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?…])\s+")
TICKS_PER_SECOND = 10_000_000


def split_sentences(text: str) -> List[str]:
    sentences = [part.strip() for part in _SENTENCE_SPLIT.split(" ".join(text.split()))]
    return [sentence for sentence in sentences if sentence]


def pause_after(sentence: str, last_in_scene: bool) -> float:
    if last_in_scene:
        return SCENE_TAIL_PAUSE
    if sentence.endswith("?"):
        return PAUSE_AFTER_QUESTION
    if sentence.endswith("!"):
        return PAUSE_AFTER_EXCLAMATION
    return PAUSE_AFTER_SENTENCE


def prosody_for(stage: str, sentence: str, speed: int = 0) -> Tuple[str, str]:
    """Edge rate/pitch strings for a sentence of the given story stage."""
    rate, pitch = STAGE_PROSODY.get(stage, (0, 0))
    if sentence.endswith("?"):
        pitch += 2
    rate = max(-30, min(30, rate + speed))
    return f"{rate:+d}%", f"{pitch:+d}Hz"


def words_from_boundaries(boundaries: List[Dict[str, Any]], offset: float) -> List[Dict[str, Any]]:
    """Convert Edge WordBoundary events to caption words on the video timeline."""
    words = []
    for event in boundaries:
        text = (event.get("text") or "").strip()
        if not text:
            continue
        start = offset + event["offset"] / TICKS_PER_SECOND
        end = start + max(event.get("duration", 0), 1) / TICKS_PER_SECOND
        words.append({"text": text, "start": round(start, 3), "end": round(end, 3)})
    return words


def estimate_words(sentence: str, offset: float, duration: float) -> List[Dict[str, Any]]:
    """Spread a sentence's words over its audio by length, for voices that do
    not report word timings."""
    tokens = sentence.split()
    if not tokens or duration <= 0:
        return []
    weights = [len(token) + 2 for token in tokens]
    scale = duration / sum(weights)
    words, cursor = [], offset
    for token, weight in zip(tokens, weights):
        span = weight * scale
        words.append({"text": token, "start": round(cursor, 3), "end": round(cursor + span * 0.9, 3)})
        cursor += span
    return words


async def _synthesize_sentence(text: str, voice: str, rate: str, pitch: str, path: Path) -> List[Dict[str, Any]]:
    import edge_tts

    communicate = edge_tts.Communicate(text, voice, rate=rate, pitch=pitch, boundary="WordBoundary")
    boundaries = []
    with path.open("wb") as audio:
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                audio.write(chunk["data"])
            elif chunk["type"] == "WordBoundary":
                boundaries.append(chunk)
    if path.stat().st_size == 0:
        raise RuntimeError(f"The voice service returned no audio for: {text[:60]}")
    return boundaries


def _decode_to_wav(source: Path, target: Path) -> None:
    result = run_ffmpeg_command(
        ["ffmpeg", "-y", "-i", str(source), "-ac", "1", "-ar", str(SAMPLE_RATE), str(target)]
    )
    if result.returncode != 0:
        raise RuntimeError("Could not decode the synthesized voice")


def _silence(seconds: float, target: Path) -> None:
    result = run_ffmpeg_command(
        [
            "ffmpeg", "-y", "-f", "lavfi", "-i", f"anullsrc=r={SAMPLE_RATE}:cl=mono",
            "-t", f"{seconds:.3f}", str(target),
        ]
    )
    if result.returncode != 0:
        raise RuntimeError("Could not create silence for the voice-over")


def _concat_wavs(parts: List[Path], target: Path, polish: bool) -> None:
    list_file = target.with_suffix(".txt")
    list_file.write_text("".join(f"file '{part.as_posix()}'\n" for part in parts))
    command = ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(list_file)]
    if polish:
        command += ["-af", VOICE_POLISH_FILTER]
    command += ["-ac", "1", "-ar", str(SAMPLE_RATE), str(target)]
    result = run_ffmpeg_command(command)
    list_file.unlink(missing_ok=True)
    if result.returncode != 0:
        raise RuntimeError("Could not join the voice-over")


async def voice_scene(
    narration: str,
    stage: str,
    voice: str,
    output_path: Path,
    speed: int = 0,
) -> Dict[str, Any]:
    """Voice one scene into ``output_path`` (wav). Returns duration and words
    with times relative to the start of the scene audio."""
    sentences = split_sentences(narration)
    if not sentences:
        raise RuntimeError("A scene has no narration to voice")
    with tempfile.TemporaryDirectory(prefix="studio_voice_") as temp:
        work = Path(temp)
        parts: List[Path] = []
        words: List[Dict[str, Any]] = []
        cursor = 0.0
        for index, sentence in enumerate(sentences):
            rate, pitch = prosody_for(stage, sentence, speed)
            mp3 = work / f"s{index}.mp3"
            boundaries = await _synthesize_sentence(sentence, voice, rate, pitch, mp3)
            wav = work / f"s{index}.wav"
            await asyncio.to_thread(_decode_to_wav, mp3, wav)
            spoken = ffprobe_duration(wav)
            sentence_words = words_from_boundaries(boundaries, cursor)
            words += sentence_words or estimate_words(sentence, cursor, spoken)
            cursor += spoken
            parts.append(wav)
            gap = pause_after(sentence, index == len(sentences) - 1)
            silence = work / f"p{index}.wav"
            await asyncio.to_thread(_silence, gap, silence)
            parts.append(silence)
            cursor += gap
        joined = work / "scene.wav"
        await asyncio.to_thread(_concat_wavs, parts, joined, True)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(joined), str(output_path))
    duration = ffprobe_duration(output_path)
    logger.info("Voiced scene: %.2fs, %s words", duration, len(words))
    return {"duration": duration, "words": words}

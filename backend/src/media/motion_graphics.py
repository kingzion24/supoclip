"""Kinetic typography and motion graphics for caption templates with ``motion``.

Everything here is drawn by libass in the same pass that burns the captions,
plus one ffmpeg zoompan stage for punch-zooms, so motion adds no extra encode:

- kinetic hook title: the headline slams in (scale + tilt) and an accent bar
  wipes in underneath it
- keyword callouts: power words and numbers pop up large mid-frame on the beat
  they are spoken
- punch-zooms: a quick camera push-in on each callout beat
- progress bar: a thin bar that fills along the bottom over the clip
"""

import re
from typing import Any, Dict, List, Optional, Set, Tuple

from ..emoji_captions import POWER_WORDS, normalize_token

# Callouts: at most one every CALLOUT_MIN_GAP seconds, one per
# CALLOUT_SECONDS_PER_BEAT seconds of clip, never more than CALLOUT_MAX.
CALLOUT_MIN_GAP = 3.5
CALLOUT_SECONDS_PER_BEAT = 6.0
CALLOUT_MAX = 6
CALLOUT_HOLD_SECONDS = 0.75
CALLOUT_SCALE = 2.1
CALLOUT_Y_FRAC = 0.40
# Callouts wait until the hook title has had the screen for a moment.
CALLOUT_START_AFTER = 1.2

PUNCH_ZOOM = 0.08
PUNCH_IN_SECONDS = 0.08
PUNCH_OUT_SECONDS = 0.42

PROGRESS_BAR_FRAC = 0.006

_CALLOUT_STRIP = re.compile(r"^[^\w$%#]+|[^\w$%]+$", re.UNICODE)


MOTION_LEVELS = ("none", "subtle", "full")
# "subtle" clips get at most this many callouts and no punch-zooms.
SUBTLE_MAX_CALLOUTS = 2


def motion_enabled(template: Dict[str, Any]) -> bool:
    return bool(template.get("motion"))


def resolve_motion_level(
    template: Dict[str, Any], motion_plan: Optional[Dict[str, Any]] = None
) -> Optional[str]:
    """Return "subtle"/"full" for the motion to draw, or None for no motion.

    ``motion: True`` always means full motion. ``motion: "auto"`` follows the
    AI's per-clip ``motion_level`` (subtle when the clip has no plan, e.g.
    after a regenerate).
    """
    motion = template.get("motion")
    if motion is True:
        return "full"
    if motion != "auto":
        return None
    level = (motion_plan or {}).get("level")
    if level not in MOTION_LEVELS:
        level = "subtle"
    return None if level == "none" else level


def match_callout_indices(
    words: List[Dict[str, Any]], callout_words: List[str]
) -> Set[int]:
    """Map the AI's callout words onto the first matching spoken word."""
    wanted = [normalize_token(word) for word in callout_words if normalize_token(word)]
    found: Set[int] = set()
    for target in wanted:
        for index, word in enumerate(words):
            if index in found or float(word.get("start", 0)) < CALLOUT_START_AFTER:
                continue
            if normalize_token(str(word.get("text", ""))) == target:
                found.add(index)
                break
    return found


def _ass_time(seconds: float) -> str:
    seconds = max(0.0, seconds)
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = seconds % 60
    return f"{hours}:{minutes:02d}:{secs:05.2f}"


def _callout_text(raw: str, uppercase: bool = True) -> str:
    text = _CALLOUT_STRIP.sub("", raw.strip())
    text = text.replace("{", "").replace("}", "").replace("\\", "")
    return text.upper() if uppercase else text


def _beat_strength(word_text: str) -> int:
    """Numbers beat power words; longer words beat short ones."""
    token = normalize_token(word_text)
    score = len(token)
    if any(char.isdigit() for char in token):
        score += 20
    if token in POWER_WORDS:
        score += 10
    return score


def select_motion_beats(
    words: List[Dict[str, Any]],
    emphasis_idx: Set[int],
    output_duration: float,
    max_beats: Optional[int] = None,
) -> List[Tuple[float, str]]:
    """Pick the (time, text) moments that get a callout and a punch-zoom."""
    budget = min(CALLOUT_MAX, max(1, int(output_duration // CALLOUT_SECONDS_PER_BEAT)))
    if max_beats is not None:
        budget = min(budget, max_beats)
    candidates = []
    for index in emphasis_idx:
        if index >= len(words):
            continue
        word = words[index]
        start = float(word.get("start", 0))
        text = _callout_text(str(word.get("text", "")))
        if (
            not text
            or start < CALLOUT_START_AFTER
            or start > output_duration - CALLOUT_HOLD_SECONDS
        ):
            continue
        candidates.append((_beat_strength(text), start, text))

    beats: List[Tuple[float, str]] = []
    for _, start, text in sorted(candidates, key=lambda c: (-c[0], c[1])):
        if len(beats) >= budget:
            break
        if all(abs(start - other) >= CALLOUT_MIN_GAP for other, _ in beats):
            beats.append((start, text))
    return sorted(beats)


def kinetic_hook_entrance() -> str:
    """Slam-in for the hook title: big, tilted and transparent to resting."""
    return (
        "\\fad(0,240)\\fscx165\\fscy165\\frz-5\\alpha&HFF&"
        "\\t(0,200,\\fscx100\\fscy100\\frz0\\alpha&H00&)"
        "\\t(200,3800,\\fscx104\\fscy104)"
    )


def kinetic_hook_bar_event(
    start: float,
    end: float,
    video_width: int,
    top_y: int,
    line_count: int,
    hook_px: int,
    longest_chars: int,
    color: str,
) -> str:
    """An accent bar that wipes in under the hook title."""
    bar_w = max(120, min(int(video_width * 0.7), int(longest_chars * hook_px * 0.5)))
    bar_h = max(8, hook_px // 7)
    y = top_y + int(line_count * hook_px * 1.18) + bar_h
    shape = f"m 0 0 l {bar_w} 0 l {bar_w} {bar_h} l 0 {bar_h}"
    tags = (
        f"\\an8\\pos({video_width // 2},{y})\\bord0\\shad0\\1c{color}\\fad(0,240)"
        "\\fscx0\\t(120,340,\\fscx100)\\p1"
    )
    return f"Dialogue: 2,{_ass_time(start)},{_ass_time(end)},Hook,,0,0,0,,{{{tags}}}{shape}{{\\p0}}"


def build_motion_ass(
    template: Dict[str, Any],
    words: List[Dict[str, Any]],
    emphasis_idx: Set[int],
    video_width: int,
    video_height: int,
    output_duration: float,
    font_name: str,
    caption_font_px: int,
    highlight_color: str,
    outline_color: str,
    level: str = "full",
    callout_words: Optional[List[str]] = None,
) -> Tuple[List[str], List[str], List[float]]:
    """Return (style lines, dialogue events, punch-zoom beat times).

    ``level`` "subtle" draws at most two callouts and no punch-zooms.
    ``callout_words`` (chosen by the AI) replace the rule-based keyword pick.
    """
    styles: List[str] = []
    events: List[str] = []
    beat_times: List[float] = []

    callout_px = int(caption_font_px * CALLOUT_SCALE)
    outline_px = max(4, callout_px // 12)
    shadow_px = max(3, callout_px // 22)
    styles.append(
        f"Style: Callout,{font_name},{callout_px},{highlight_color},&H000000FF,"
        f"{outline_color},&H80000000,1,0,0,0,100,100,0,0,1,{outline_px},{shadow_px},5,40,40,40,1"
    )
    callout_y = int(video_height * CALLOUT_Y_FRAC)
    uppercase = template.get("uppercase", True) is not False

    candidates = emphasis_idx
    if callout_words:
        candidates = match_callout_indices(words, callout_words) or emphasis_idx
    beats = select_motion_beats(
        words,
        candidates,
        output_duration,
        max_beats=SUBTLE_MAX_CALLOUTS if level == "subtle" else None,
    )
    for number, (start, text) in enumerate(beats):
        label = text if uppercase else text.lower()
        tilt = -6 if number % 2 == 0 else 6
        end = min(output_duration, start + CALLOUT_HOLD_SECONDS)
        hold_ms = int((end - start) * 1000)
        tags = (
            f"\\an5\\pos({video_width // 2},{callout_y})"
            f"\\fscx230\\fscy230\\frz{tilt}\\alpha&HFF&"
            f"\\t(0,120,\\fscx100\\fscy100\\frz{tilt // 3}\\alpha&H00&)"
            f"\\t({max(120, hold_ms - 180)},{hold_ms},\\fscx118\\fscy118\\alpha&HFF&)"
        )
        events.append(
            f"Dialogue: 3,{_ass_time(start)},{_ass_time(end)},Callout,,0,0,0,,{{{tags}}}{label}"
        )
        if level == "full":
            beat_times.append(start)

    if template.get("progress_bar", True) and output_duration > 1:
        bar_h = max(6, int(video_height * PROGRESS_BAR_FRAC))
        styles.append(
            f"Style: Progress,{font_name},10,{highlight_color},&H000000FF,"
            "&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1"
        )
        shape = f"m 0 0 l {video_width} 0 l {video_width} {bar_h} l 0 {bar_h}"
        end_time = _ass_time(output_duration)
        duration_ms = int(output_duration * 1000)
        events.append(
            f"Dialogue: 0,{_ass_time(0)},{end_time},Progress,,0,0,0,,"
            f"{{\\an7\\pos(0,{video_height - bar_h})\\1c&HFFFFFF&\\alpha&HB0&\\p1}}{shape}{{\\p0}}"
        )
        events.append(
            f"Dialogue: 1,{_ass_time(0)},{end_time},Progress,,0,0,0,,"
            f"{{\\an7\\pos(0,{video_height - bar_h})\\fscx0\\t(0,{duration_ms},\\fscx100)\\p1}}"
            f"{shape}{{\\p0}}"
        )

    return styles, events, beat_times


def punch_zoom_fragment(
    beat_times: List[float], out_width: int, out_height: int
) -> Optional[str]:
    """Scale-and-crop stage that pushes the camera in briefly on each beat.

    The zoom is a per-frame ``scale`` (eval=frame) followed by a centre crop
    back to the output size, so no frames are re-timed or dropped and audio
    stays in sync on every framing path.
    """
    if not beat_times:
        return None
    pulses = []
    for beat in beat_times:
        peak = beat + PUNCH_IN_SECONDS
        release = peak + PUNCH_OUT_SECONDS
        pulses.append(
            f"if(between(t\\,{beat:.3f}\\,{peak:.3f})\\,(t-{beat:.3f})/{PUNCH_IN_SECONDS}\\,0)"
            f"+if(between(t\\,{peak:.3f}\\,{release:.3f})\\,1-(t-{peak:.3f})/{PUNCH_OUT_SECONDS}\\,0)"
        )
    zoom = f"(1+{PUNCH_ZOOM}*({'+'.join(pulses)}))"
    return (
        f"scale=w='trunc({out_width}*{zoom}/2)*2':h='trunc({out_height}*{zoom}/2)*2'"
        ":eval=frame:flags=bicubic,"
        f"crop={out_width}:{out_height}:(iw-{out_width})/2:(ih-{out_height})/2,setsar=1"
    )

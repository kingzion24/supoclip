"""Podcast-style speaker cuts: the camera follows the voice.

Modelled on how The Diary of a CEO edits clips from a wide two-person shot:
every cut lands where the speaking turn changes, short interjections ("yeah",
"mm") do not trigger a cut, and the frame jumps (no pan) to the new speaker.

Who is speaking comes from the transcript's speaker labels (AssemblyAI
diarization), which follow the audio exactly. Which face belongs to which
label is decided from mouth/face motion: over all of a speaker's turns, the
face region that moves more is theirs.
"""

from typing import Any, Dict, List, Optional, Tuple

# A turn shorter than this is treated as an interjection and gets no cut.
MIN_SHOT_SECONDS = 1.2
# Cut a beat before the new speaker's first word so the face is on screen
# when they start talking.
CUT_LEAD_SECONDS = 0.12


def build_speaker_turns(
    words: List[Dict[str, Any]],
    duration: float,
    min_shot: float = MIN_SHOT_SECONDS,
    lead: float = CUT_LEAD_SECONDS,
) -> List[Dict[str, Any]]:
    """Group labelled words into shots covering [0, duration], one per turn."""
    raw: List[List[Any]] = []  # [first_word_start, last_word_end, speaker]
    for word in words:
        speaker = word.get("speaker")
        if not speaker:
            continue
        start, end = float(word["start"]), float(word["end"])
        if raw and raw[-1][2] == speaker:
            raw[-1][1] = end
        else:
            raw.append([start, end, speaker])
    if not raw:
        return []

    shots: List[Dict[str, Any]] = []
    for start, end, speaker in raw:
        if shots and shots[-1]["speaker"] == speaker:
            continue
        if shots and end - start < min_shot:
            continue  # interjection: stay on the current speaker
        cut = 0.0 if not shots else max(shots[-1]["start"] + 0.01, start - lead)
        shots.append({"start": cut, "speaker": speaker})

    for index, shot in enumerate(shots):
        shot["end"] = shots[index + 1]["start"] if index + 1 < len(shots) else duration
    return [shot for shot in shots if shot["end"] > shot["start"]]


def assign_speakers_to_regions(
    turns: List[Dict[str, Any]],
    times: List[float],
    left_values: List[float],
    right_values: List[float],
) -> Optional[Dict[str, str]]:
    """Map speaker labels to the "left"/"right" face by who moves while talking."""
    if not turns or not times or len(left_values) != len(times) or len(right_values) != len(times):
        return None

    def normalize(values: List[float]) -> List[float]:
        mean = sum(values) / max(len(values), 1)
        return [value / mean if mean > 0 else 0.0 for value in values]

    left, right = normalize(left_values), normalize(right_values)
    diff_sum: Dict[str, float] = {}
    talk_time: Dict[str, float] = {}
    turn_index = 0
    for time, left_value, right_value in zip(times, left, right):
        while turn_index < len(turns) and time >= turns[turn_index]["end"]:
            turn_index += 1
        if turn_index >= len(turns):
            break
        turn = turns[turn_index]
        if time < turn["start"]:
            continue
        label = turn["speaker"]
        diff_sum[label] = diff_sum.get(label, 0.0) + (left_value - right_value)
        talk_time[label] = talk_time.get(label, 0.0) + 1

    main = sorted(talk_time, key=talk_time.get, reverse=True)[:2]
    if len(main) < 2:
        return None
    mean_diff = {label: diff_sum[label] / talk_time[label] for label in talk_time}
    left_label, right_label = sorted(main, key=lambda label: mean_diff[label], reverse=True)
    mapping = {left_label: "left", right_label: "right"}
    for label in talk_time:
        mapping.setdefault(label, "left" if mean_diff[label] >= 0 else "right")
    return mapping


def speaker_cut_timeline(
    turns: List[Dict[str, Any]], mapping: Dict[str, str]
) -> List[Dict[str, Any]]:
    """Turn labelled shots into left/right shots, merging repeats."""
    timeline: List[Dict[str, Any]] = []
    for turn in turns:
        side = mapping.get(turn["speaker"])
        if side is None:
            continue
        if timeline and timeline[-1]["speaker"] == side:
            timeline[-1]["end"] = turn["end"]
        else:
            timeline.append({"start": turn["start"], "end": turn["end"], "speaker": side})
    if timeline:
        timeline[0]["start"] = 0.0
    return timeline


def build_cut_expression(timeline: List[Dict[str, Any]], left_x: int, right_x: int) -> str:
    """Crop-x expression that jumps between the two framings at each shot.

    Commas are escaped for use inside a quoted filtergraph expression.
    """
    if not timeline:
        return str(left_x)

    def x_for(side: str) -> int:
        return left_x if side == "left" else right_x

    current = x_for(timeline[0]["speaker"])
    terms = [str(current)]
    for shot in timeline[1:]:
        target = x_for(shot["speaker"])
        if target != current:
            terms.append(f"({target - current})*gte(t\\,{shot['start']:.3f})")
            current = target
    return "+".join(terms)


def count_cuts(timeline: List[Dict[str, Any]]) -> int:
    return sum(
        1 for previous, shot in zip(timeline, timeline[1:]) if previous["speaker"] != shot["speaker"]
    )


def plan_speaker_cuts(
    words: List[Dict[str, Any]],
    duration: float,
    times: List[float],
    left_values: List[float],
    right_values: List[float],
) -> Optional[Tuple[List[Dict[str, Any]], Dict[str, str]]]:
    """Full plan from labelled words and per-face motion, or None if unusable."""
    turns = build_speaker_turns(words, duration)
    if len(turns) < 2:
        return None
    mapping = assign_speakers_to_regions(turns, times, left_values, right_values)
    if not mapping:
        return None
    timeline = speaker_cut_timeline(turns, mapping)
    if count_cuts(timeline) < 1:
        return None
    return timeline, mapping

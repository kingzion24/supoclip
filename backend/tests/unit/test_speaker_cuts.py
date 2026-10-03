from src.media import speaker_cuts as sc


def _words(speaker, start, end, step=0.35):
    words, t = [], start
    while t + 0.3 <= end:
        words.append({"text": "neno", "start": t, "end": t + 0.3, "speaker": speaker})
        t += step
    return words


def test_turns_cut_on_speaker_change_and_ignore_interjections():
    words = (
        _words("A", 0.3, 3.8)
        + [{"text": "yeah", "start": 4.0, "end": 4.3, "speaker": "B"}]
        + _words("A", 4.4, 6.0)
        + _words("B", 6.5, 10.0)
    )
    turns = sc.build_speaker_turns(words, duration=10.0)
    assert [(t["speaker"], round(t["start"], 2), round(t["end"], 2)) for t in turns] == [
        ("A", 0.0, 6.38),
        ("B", 6.38, 10.0),
    ]


def test_words_without_speaker_labels_give_no_turns():
    assert sc.build_speaker_turns([{"text": "hi", "start": 0, "end": 1}], 5.0) == []


def test_speakers_map_to_the_face_that_moves_while_they_talk():
    turns = [
        {"start": 0.0, "end": 4.0, "speaker": "B"},
        {"start": 4.0, "end": 8.0, "speaker": "A"},
    ]
    times = [i * 0.5 for i in range(16)]
    left = [5.0 if t < 4 else 1.0 for t in times]
    right = [1.0 if t < 4 else 5.0 for t in times]
    assert sc.assign_speakers_to_regions(turns, times, left, right) == {"B": "left", "A": "right"}
    assert sc.assign_speakers_to_regions(turns[:1], times, left, right) is None


def test_cut_expression_jumps_between_framings():
    timeline = [
        {"start": 0.0, "end": 4.0, "speaker": "left"},
        {"start": 4.0, "end": 8.0, "speaker": "right"},
        {"start": 8.0, "end": 12.0, "speaker": "left"},
    ]
    assert sc.build_cut_expression(timeline, 100, 900) == (
        "100+(800)*gte(t\\,4.000)+(-800)*gte(t\\,8.000)"
    )
    assert sc.count_cuts(timeline) == 2


def test_plan_speaker_cuts_end_to_end():
    words = _words("B", 0.3, 3.8) + _words("A", 4.1, 7.8)
    times = [i * 0.5 for i in range(16)]
    left = [5.0 if t < 4 else 1.0 for t in times]
    right = [1.0 if t < 4 else 5.0 for t in times]
    timeline, mapping = sc.plan_speaker_cuts(words, 8.0, times, left, right)
    assert mapping == {"B": "left", "A": "right"}
    assert [shot["speaker"] for shot in timeline] == ["left", "right"]
    assert timeline[0]["start"] == 0.0 and timeline[-1]["end"] == 8.0

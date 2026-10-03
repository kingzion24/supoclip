from src.caption_templates import get_template
from src.media import motion_graphics as mg


def _words(*items):
    return [{"text": text, "start": start, "end": start + 0.4} for text, start in items]


def test_select_motion_beats_prefers_numbers_and_spaces_them_out():
    words = _words(("secret", 0.5), ("500", 2.0), ("every", 2.5), ("nobody", 7.0), ("huge", 12.5))
    beats = mg.select_motion_beats(words, {0, 1, 2, 3, 4}, output_duration=13.0)
    # Too early for the hook (0.5s) and too close to "500" (2.5s) are skipped;
    # a 13s clip gets two callouts.
    assert beats == [(2.0, "500"), (7.0, "NOBODY")]


def test_callout_text_strips_punctuation_but_keeps_money_and_percent():
    assert mg._callout_text("$500,") == "$500"
    assert mg._callout_text('"kweli!"') == "KWELI"
    assert mg._callout_text("90%.") == "90%"


def test_build_motion_ass_draws_callouts_and_progress_bar():
    template = get_template("kinetic")
    words = _words(("watch", 0.3), ("500", 3.0), ("dollars", 3.5))
    styles, events, beats = mg.build_motion_ass(
        template, words, {1}, 1080, 1920, 12.0, "THE BOLD FONT", 60, "&H0000E0FF&", "&H00000000&"
    )
    assert any(line.startswith("Style: Callout,") for line in styles)
    assert any(line.startswith("Style: Progress,") for line in styles)
    callouts = [event for event in events if ",Callout," in event]
    assert len(callouts) == 1 and callouts[0].endswith("500")
    assert sum(",Progress," in event for event in events) == 2
    assert beats == [3.0]


def test_progress_bar_can_be_disabled():
    template = dict(get_template("kinetic"), progress_bar=False)
    _, events, _ = mg.build_motion_ass(
        template, [], set(), 1080, 1920, 12.0, "F", 60, "&H00FFFFFF&", "&H00000000&"
    )
    assert events == []


def test_punch_zoom_fragment_keeps_output_size():
    assert mg.punch_zoom_fragment([], 1080, 1920) is None
    fragment = mg.punch_zoom_fragment([3.0, 9.5], 1080, 1920)
    assert "eval=frame" in fragment
    # Two pulses per beat, in both the width and height expressions.
    assert fragment.count("between(t") == 8
    assert "crop=1080:1920:(iw-1080)/2:(ih-1920)/2" in fragment


def test_only_kinetic_templates_enable_motion():
    assert mg.motion_enabled(get_template("kinetic"))
    assert mg.motion_enabled(get_template("kinetic_green"))
    assert not mg.motion_enabled(get_template("default"))


def test_resolve_motion_level_follows_ai_only_for_auto_templates():
    auto = get_template("podcast_pro")
    assert mg.resolve_motion_level(auto, {"level": "full"}) == "full"
    assert mg.resolve_motion_level(auto, {"level": "none"}) is None
    assert mg.resolve_motion_level(auto, None) == "subtle"
    assert mg.resolve_motion_level(auto, {"level": "bogus"}) == "subtle"
    assert mg.resolve_motion_level(get_template("kinetic"), {"level": "none"}) == "full"
    assert mg.resolve_motion_level(get_template("default"), {"level": "full"}) is None


def test_ai_callout_words_replace_rule_based_pick():
    words = _words(("money", 2.0), ("500", 6.0), ("family", 10.0))
    _, events, beats = mg.build_motion_ass(
        get_template("podcast_pro"), words, {1}, 1080, 1920, 14.0, "F", 60,
        "&H0000D4FF&", "&H00000000&", level="full", callout_words=["Family"],
    )
    callouts = [event for event in events if ",Callout," in event]
    assert len(callouts) == 1 and callouts[0].endswith("FAMILY")
    assert beats == [10.0]


def test_subtle_level_caps_callouts_and_skips_punch_zoom():
    words = _words(("one", 2.0), ("two", 6.0), ("three", 10.0), ("four", 14.0))
    _, events, beats = mg.build_motion_ass(
        get_template("podcast_pro"), words, {0, 1, 2, 3}, 1080, 1920, 20.0, "F", 60,
        "&H0000D4FF&", "&H00000000&", level="subtle",
    )
    assert sum(",Callout," in event for event in events) == 2
    assert beats == []

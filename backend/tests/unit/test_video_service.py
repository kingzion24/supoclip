import json
from types import SimpleNamespace

import pytest

from src.services import video_service as video_service_module
from src.services.video_service import VideoService


class _EmptyAnalysis:
    summary = "No strong standalone segment found"
    key_topics = []
    most_relevant_segments = []


@pytest.mark.asyncio
async def test_process_video_complete_uses_fallback_when_ai_selects_no_segments(
    monkeypatch, tmp_path
):
    video_path = tmp_path / "source.mp4"
    video_path.write_bytes(b"placeholder")

    config = SimpleNamespace(
        max_video_duration=5400,
        clip_duration=30,
        fast_mode_max_clips=4,
    )
    monkeypatch.setattr(video_service_module, "get_config", lambda: config)
    monkeypatch.setattr(
        VideoService,
        "resolve_local_video_path",
        staticmethod(lambda _url: video_path),
    )
    monkeypatch.setattr(
        VideoService,
        "_get_file_duration",
        staticmethod(lambda _path: 42.0),
    )

    async def fake_generate_transcript(
        _video_path, processing_mode="balanced", source_url=None
    ):
        return "[00:00 - 00:01] hello"

    async def fake_analyze_transcript(_transcript, clip_signals=None):
        return _EmptyAnalysis()

    async def fake_run_in_thread(_func, *_args, **_kwargs):
        return None

    monkeypatch.setattr(
        VideoService,
        "generate_transcript",
        staticmethod(fake_generate_transcript),
    )
    monkeypatch.setattr(
        VideoService,
        "analyze_transcript",
        staticmethod(fake_analyze_transcript),
    )
    monkeypatch.setattr(video_service_module, "run_in_thread", fake_run_in_thread)

    result = await VideoService.process_video_complete(
        url="upload://source.mp4",
        source_type="video_url",
        processing_mode="fast",
    )

    assert result["segments_to_render"] == [
        {
            "start_time": "00:00",
            "end_time": "00:30",
            "text": "[00:00 - 00:01] hello",
            "relevance_score": 0.25,
            "reasoning": (
                "AI analysis did not identify a strong standalone segment, "
                "so Katakata generated the first available portion of the video."
            ),
            "virality_score": 0,
            "hook_score": 0,
            "engagement_score": 0,
            "value_score": 0,
            "shareability_score": 0,
            "hook_type": "fallback",
            "hook_title": None,
        }
    ]
    analysis = json.loads(result["analysis_json"])
    assert analysis["most_relevant_segments"] == result["segments_to_render"]


def test_fallback_segment_caps_to_video_duration():
    segment = VideoService._build_fallback_segment(
        video_duration=12.0,
        transcript="short transcript",
        target_duration=30,
    )

    assert segment["start_time"] == "00:00"
    assert segment["end_time"] == "00:12"
    assert segment["hook_type"] == "fallback"


@pytest.mark.asyncio
async def test_cached_text_regenerates_missing_word_timings(monkeypatch, tmp_path):
    from unittest.mock import AsyncMock
    from src.config import Config
    source = tmp_path / "source.mp4"
    source.touch()
    monkeypatch.setattr(video_service_module, "get_config", Config)
    monkeypatch.setattr(VideoService, "resolve_local_video_path", lambda _: source)
    monkeypatch.setattr(VideoService, "_get_file_duration", lambda _: 19)
    monkeypatch.setattr(video_service_module, "load_cached_transcript_data", lambda _: None)
    transcribe = AsyncMock(return_value="Restored transcript")
    monkeypatch.setattr(VideoService, "generate_transcript", transcribe)
    analysis = json.dumps({"most_relevant_segments": [{"start_time": "00:00", "end_time": "00:17", "text": "Cached segment"}], "summary": "Test", "key_topics": []})
    await VideoService.process_video_complete(url="upload://source.mp4", source_type="video_url", cached_transcript="Cached transcript", cached_analysis_json=analysis)
    transcribe.assert_awaited_once()


@pytest.mark.asyncio
async def test_process_video_complete_adds_visual_highlight_clips(monkeypatch, tmp_path):
    video_path = tmp_path / "source.mp4"
    video_path.write_bytes(b"placeholder")
    config = SimpleNamespace(
        max_video_duration=5400,
        clip_duration=30,
        fast_mode_max_clips=4,
        twelvelabs_max_visual_clips=2,
        twelvelabs_min_highlight_score=60,
    )
    monkeypatch.setattr(video_service_module, "get_config", lambda: config)
    monkeypatch.setattr(
        VideoService, "resolve_local_video_path", staticmethod(lambda _url: video_path)
    )
    monkeypatch.setattr(VideoService, "_get_file_duration", staticmethod(lambda _path: 600.0))

    async def fake_generate_transcript(_video_path, processing_mode="balanced", source_url=None):
        return "[00:00 - 00:40] Habari za leo marafiki"

    spoken_segments = [
        {
            "start_time": f"0{minute}:00",
            "end_time": f"0{minute}:30",
            "text": "Habari za leo marafiki",
            "virality": {"total_score": 80},
            "hook_title": "Siri ya leo",
        }
        for minute in range(4)
    ]
    received_signals = []

    async def fake_analyze_transcript(_transcript, clip_signals=None):
        received_signals.append(clip_signals)
        return SimpleNamespace(
            summary="s", key_topics=[], most_relevant_segments=spoken_segments
        )

    highlights = [
        {"start": 300, "end": 320, "score": 95, "description": "Singeli dance", "hook_title": "Ngoma kali"},
        {"start": 400, "end": 420, "score": 90, "description": "Crowd cheers", "hook_title": None},
        {"start": 500, "end": 520, "score": 85, "description": "Third", "hook_title": None},
    ]

    async def fake_run_in_thread(func, *_args, **_kwargs):
        if func is video_service_module.detect_visual_highlights:
            return highlights
        return None

    monkeypatch.setattr(VideoService, "generate_transcript", staticmethod(fake_generate_transcript))
    monkeypatch.setattr(VideoService, "analyze_transcript", staticmethod(fake_analyze_transcript))
    monkeypatch.setattr(video_service_module, "run_in_thread", fake_run_in_thread)
    monkeypatch.setattr(video_service_module, "visual_highlights_enabled", lambda: True)

    result = await VideoService.process_video_complete(
        url="upload://source.mp4", source_type="video_url", processing_mode="fast"
    )

    assert "Singeli dance" in received_signals[0]
    segments = result["segments_to_render"]
    # Fast mode keeps 4 clips: the top 2 spoken ones plus 2 visual-only ones.
    assert len(segments) == 4
    assert [s["hook_type"] for s in segments][-2:] == ["visual", "visual"]
    assert segments[2]["start_time"] == "05:00"
    assert segments[2]["hook_title"] == "Ngoma kali"
    assert "visual" in json.loads(result["analysis_json"])["most_relevant_segments"][2]["hook_type"]

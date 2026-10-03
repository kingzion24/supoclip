import json
from types import SimpleNamespace

import httpx

from src import visual_highlights as vh


def _config(**overrides):
    values = {
        "twelvelabs_api_key": "tlk_test",
        "twelvelabs_pegasus_model": "pegasus1.5",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_parse_highlights_validates_and_sorts():
    raw = json.dumps(
        {
            "highlights": [
                {"start_sec": 10, "end_sec": 30, "score": 70, "description": "Dance", "hook_title": "Watch this move"},
                {"start_sec": 50, "end_sec": 80, "score": 140, "description": "Crowd", "hook_title": ""},
                {"start_sec": 5, "end_sec": 5.5, "score": 90, "description": "Too short", "hook_title": "x"},
                {"start_sec": "bad", "end_sec": 9, "score": 50},
            ]
        }
    )
    highlights = vh.parse_highlights(raw, video_duration=70)
    assert [h["description"] for h in highlights] == ["Crowd", "Dance"]
    assert highlights[0]["score"] == 100
    assert highlights[0]["end"] == 70
    assert highlights[0]["hook_title"] is None
    assert vh.parse_highlights("not json") == []


def test_build_visual_segments_skips_covered_moments_and_fits_window():
    highlights = [
        {"start": 100, "end": 105, "score": 90, "description": "Backflip", "hook_title": "Hii ni hatari"},
        {"start": 12, "end": 30, "score": 85, "description": "Already clipped", "hook_title": None},
        {"start": 200, "end": 230, "score": 40, "description": "Weak", "hook_title": None},
    ]
    segments = vh.build_visual_segments(
        highlights,
        selected_ranges=[(10.0, 40.0)],
        video_duration=600,
        max_clips=2,
        min_score=60,
    )
    assert len(segments) == 1
    segment = segments[0]
    # A 5s highlight grows to the 15s minimum, centred on the moment.
    assert (segment["start_time"], segment["end_time"]) == ("01:35", "01:50")
    assert segment["hook_type"] == "visual"
    assert segment["virality_score"] == 90
    assert segment["hook_title"] == "Hii ni hatari"


def test_format_visual_signals():
    assert vh.format_visual_signals([]) == ""
    text = vh.format_visual_signals(
        [{"start": 65, "end": 80, "score": 77, "description": "Singeli dance"}]
    )
    assert "[01:05 - 01:20] visual score 77: Singeli dance" in text


def test_detect_visual_highlights_disabled_without_key(tmp_path, monkeypatch):
    monkeypatch.setattr(vh, "get_config", lambda: _config(twelvelabs_api_key=None))
    assert vh.detect_visual_highlights(tmp_path / "video.mp4") == []


def test_detect_visual_highlights_calls_twelvelabs_and_caches(tmp_path, monkeypatch):
    video_path = tmp_path / "video.mp4"
    video_path.write_bytes(b"source")
    requests = []
    answer = {
        "highlights": [
            {"start_sec": 20, "end_sec": 40, "score": 88, "description": "Dance battle", "hook_title": "Nani ameshinda?"}
        ]
    }

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append((request.method, request.url.path))
        assert request.headers["x-api-key"] == "tlk_test"
        if request.method == "POST" and request.url.path.endswith("/assets"):
            return httpx.Response(201, json={"_id": "asset-1", "status": "processing"})
        if request.method == "GET" and request.url.path.endswith("/assets/asset-1"):
            return httpx.Response(200, json={"_id": "asset-1", "status": "ready"})
        if request.method == "POST" and request.url.path.endswith("/analyze"):
            body = json.loads(request.content)
            assert body["video"] == {"type": "asset_id", "asset_id": "asset-1"}
            assert body["response_format"]["type"] == "json_schema"
            return httpx.Response(200, json={"id": "a", "data": json.dumps(answer)})
        if request.method == "DELETE":
            return httpx.Response(204)
        return httpx.Response(404)

    real_client = httpx.Client
    monkeypatch.setattr(vh, "get_config", lambda: _config())
    monkeypatch.setattr(
        vh.httpx, "Client", lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs)
    )

    def fake_proxy(source, output):
        output.write_bytes(b"proxy")
        return True

    monkeypatch.setattr(vh, "_make_proxy", fake_proxy)

    highlights = vh.detect_visual_highlights(video_path, video_duration=120)
    assert highlights == [
        {"start": 20.0, "end": 40.0, "score": 88, "description": "Dance battle", "hook_title": "Nani ameshinda?"}
    ]
    assert ("DELETE", "/v1.3/assets/asset-1") in requests

    # A second call reads the per-video cache instead of paying for analysis again.
    requests.clear()
    assert vh.detect_visual_highlights(video_path) == highlights
    assert requests == []


def test_detect_visual_highlights_fails_soft(tmp_path, monkeypatch):
    video_path = tmp_path / "video.mp4"
    video_path.write_bytes(b"source")
    real_client = httpx.Client
    monkeypatch.setattr(vh, "get_config", lambda: _config())
    monkeypatch.setattr(
        vh.httpx,
        "Client",
        lambda **kwargs: real_client(
            transport=httpx.MockTransport(lambda request: httpx.Response(500)), **kwargs
        ),
    )
    monkeypatch.setattr(vh, "_make_proxy", lambda source, output: output.write_bytes(b"p") or True)
    assert vh.detect_visual_highlights(video_path) == []
    assert not video_path.with_suffix(".visual_highlights.json").exists()

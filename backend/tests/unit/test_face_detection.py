import sys
from types import SimpleNamespace

import numpy as np
import pytest

from src.media import face_detection as fd


def test_square_tiles_cover_wide_frames():
    assert fd.square_tiles(1080, 1920) == [(0, 1080)]
    assert fd.square_tiles(1920, 1080) == [(0, 1920), (0, 1080), (420, 1500), (840, 1920)]


def test_merge_boxes_keeps_best_of_overlapping_duplicates():
    boxes = [
        (100, 100, 80, 80, 0.7),
        (104, 98, 82, 80, 0.95),  # same face seen from another tile
        (900, 120, 90, 90, 0.8),
    ]
    assert fd.merge_boxes(boxes) == [(104, 98, 82, 80, 0.95), (900, 120, 90, 90, 0.8)]


def test_legacy_mediapipe_api_is_used_when_present(monkeypatch):
    calls = {}

    def legacy_detector(**kwargs):
        calls.update(kwargs)
        return "legacy"

    fake_mp = SimpleNamespace(
        solutions=SimpleNamespace(face_detection=SimpleNamespace(FaceDetection=legacy_detector))
    )
    monkeypatch.setitem(sys.modules, "mediapipe", fake_mp)
    assert fd.create_face_detector(model_selection=0) == "legacy"
    assert calls == {"model_selection": 0, "min_detection_confidence": 0.5}


def test_falls_back_to_opencv_when_tasks_detector_cannot_start(monkeypatch):
    monkeypatch.setitem(sys.modules, "mediapipe", SimpleNamespace())

    def broken(*args, **kwargs):
        raise OSError("libGLESv2.so.2: cannot open shared object file")

    monkeypatch.setattr(fd, "TasksFaceDetector", broken)
    assert fd.create_face_detector() is None


def test_bundled_models_exist():
    assert fd.FULL_RANGE_MODEL.exists()
    assert fd.FULL_RANGE_MODEL.stat().st_size > 500_000


def _tasks_detector_or_skip():
    try:
        return fd.TasksFaceDetector()
    except Exception as exc:  # no MediaPipe Tasks or no system GL library
        pytest.skip(f"MediaPipe Tasks face detector unavailable: {exc}")


def test_tasks_detector_returns_legacy_shape_on_blank_frame():
    detector = _tasks_detector_or_skip()
    try:
        results = detector.process(np.full((1080, 1920, 3), 60, dtype=np.uint8))
        assert results.detections is None
    finally:
        detector.close()

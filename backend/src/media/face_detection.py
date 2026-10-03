"""MediaPipe face detection that works on current and legacy MediaPipe.

MediaPipe 0.10.3x removed the legacy ``mp.solutions.face_detection`` API that
the reframing code was written against, which silently dropped every clip to
the much weaker Haar cascade. This module returns a detector with the legacy
``process(rgb) -> results.detections`` interface, backed by the MediaPipe
Tasks ``FaceDetector`` when the legacy API is gone.

The Tasks detector runs on a square 128/192px input, so small faces in a wide
two-person podcast shot get lost when the whole 16:9 frame is squeezed in. Wide
frames are therefore also scanned in overlapping height-sized square tiles and
the detections merged.

The models in ``media/models/`` are Google's BlazeFace models (Apache-2.0),
from https://storage.googleapis.com/mediapipe-models/face_detector/.
"""

from pathlib import Path
from types import SimpleNamespace
from typing import Any, List, Optional, Tuple

from .common import logger

MODELS_DIR = Path(__file__).resolve().parent / "models"
FULL_RANGE_MODEL = MODELS_DIR / "blaze_face_full_range.tflite"
WIDE_FRAME_RATIO = 1.2
MERGE_IOU = 0.3


def _iou(a: Tuple[float, float, float, float], b: Tuple[float, float, float, float]) -> float:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    ix = max(0.0, min(ax + aw, bx + bw) - max(ax, bx))
    iy = max(0.0, min(ay + ah, by + bh) - max(ay, by))
    inter = ix * iy
    union = aw * ah + bw * bh - inter
    return inter / union if union > 0 else 0.0


def square_tiles(width: int, height: int) -> List[Tuple[int, int]]:
    """x-ranges of the whole frame plus overlapping square tiles for wide frames."""
    tiles = [(0, width)]
    if height <= 0 or width / height <= WIDE_FRAME_RATIO:
        return tiles
    step = max(1, (width - height) // 2)
    x = 0
    while x + height <= width:
        tiles.append((x, x + height))
        x += step
    if tiles[-1][1] < width:
        tiles.append((width - height, width))
    return tiles


def merge_boxes(
    boxes: List[Tuple[float, float, float, float, float]]
) -> List[Tuple[float, float, float, float, float]]:
    """Keep the highest-scoring box among overlapping duplicates."""
    kept: List[Tuple[float, float, float, float, float]] = []
    for box in sorted(boxes, key=lambda b: b[4], reverse=True):
        if all(_iou(box[:4], other[:4]) < MERGE_IOU for other in kept):
            kept.append(box)
    return kept


class TasksFaceDetector:
    """MediaPipe Tasks FaceDetector behind the legacy ``process`` interface."""

    def __init__(self, min_detection_confidence: float = 0.5):
        import mediapipe as mp
        from mediapipe.tasks.python import BaseOptions, vision

        self._mp = mp
        self._detector = vision.FaceDetector.create_from_options(
            vision.FaceDetectorOptions(
                base_options=BaseOptions(model_asset_path=str(FULL_RANGE_MODEL)),
                min_detection_confidence=min_detection_confidence,
            )
        )

    def _detect(self, rgb) -> List[Tuple[float, float, float, float, float]]:
        image = self._mp.Image(image_format=self._mp.ImageFormat.SRGB, data=rgb)
        boxes = []
        for detection in self._detector.detect(image).detections:
            box = detection.bounding_box
            score = detection.categories[0].score if detection.categories else 0.5
            boxes.append(
                (float(box.origin_x), float(box.origin_y), float(box.width), float(box.height), float(score))
            )
        return boxes

    def process(self, rgb) -> Any:
        height, width = rgb.shape[:2]
        boxes: List[Tuple[float, float, float, float, float]] = []
        for x0, x1 in square_tiles(width, height):
            tile = rgb if (x0, x1) == (0, width) else rgb[:, x0:x1].copy()
            boxes.extend((x + x0, y, w, h, s) for x, y, w, h, s in self._detect(tile))
        detections = [
            SimpleNamespace(
                location_data=SimpleNamespace(
                    relative_bounding_box=SimpleNamespace(
                        xmin=x / width, ymin=y / height, width=w / width, height=h / height
                    )
                ),
                score=[score],
            )
            for x, y, w, h, score in merge_boxes(boxes)
        ]
        return SimpleNamespace(detections=detections or None)

    def close(self) -> None:
        try:
            self._detector.close()
        except Exception:
            pass


def create_face_detector(
    model_selection: int = 1, min_detection_confidence: float = 0.5
) -> Optional[Any]:
    """Return a MediaPipe face detector with ``process(rgb)``, or None."""
    try:
        import mediapipe as mp
    except ImportError:
        logger.info("MediaPipe not installed; using OpenCV face detection")
        return None

    legacy = getattr(getattr(mp, "solutions", None), "face_detection", None)
    if legacy is not None:
        return legacy.FaceDetection(
            model_selection=model_selection,
            min_detection_confidence=min_detection_confidence,
        )
    try:
        return TasksFaceDetector(min_detection_confidence)
    except Exception as exc:
        # Usually a missing system GL library (libGLESv2 / libEGL).
        logger.warning("MediaPipe face detector unavailable (%s); using OpenCV", exc)
        return None

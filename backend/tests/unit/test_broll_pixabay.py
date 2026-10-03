from src.broll import _pixabay_hit_to_pexels_shape
from src.clip_editor import EXPORT_PRESETS, build_export_video_filter


def test_pixabay_hit_maps_to_pexels_shape():
    hit = {
        "id": 42,
        "pageURL": "https://pixabay.com/videos/id-42/",
        "duration": 12,
        "user": "dar_creator",
        "videos": {
            "large": {"url": "https://cdn/large.mp4", "width": 1920, "height": 1080, "thumbnail": "https://cdn/l.jpg"},
            "small": {"url": "https://cdn/small.mp4", "width": 640, "height": 360},
            "tiny": {"url": "", "width": 0, "height": 0},
        },
    }
    video = _pixabay_hit_to_pexels_shape(hit)
    assert video["id"] == 42
    assert (video["width"], video["height"], video["duration"]) == (1920, 1080, 12)
    assert video["image"] == "https://cdn/l.jpg"
    assert video["video_files"] == [
        {"quality": "hd", "width": 1920, "height": 1080, "link": "https://cdn/large.mp4"},
        {"quality": "sd", "width": 640, "height": 360, "link": "https://cdn/small.mp4"},
    ]


def test_square_and_landscape_exports_blur_fill():
    assert (EXPORT_PRESETS["square"].width, EXPORT_PRESETS["square"].height) == (1080, 1080)
    assert (EXPORT_PRESETS["landscape"].width, EXPORT_PRESETS["landscape"].height) == (1920, 1080)
    assert "boxblur" in build_export_video_filter(EXPORT_PRESETS["landscape"])
    assert "pad=1080:1920" in build_export_video_filter(EXPORT_PRESETS["tiktok"])

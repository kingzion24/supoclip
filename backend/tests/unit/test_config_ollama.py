from src import config
from src.config import Config


def test_default_ollama_base_url_uses_localhost_outside_docker(monkeypatch):
    monkeypatch.setattr(config.os.path, "exists", lambda path: False)

    assert Config._default_ollama_base_url() == "http://localhost:11434/v1"


def test_default_ollama_base_url_uses_host_gateway_in_docker(monkeypatch):
    monkeypatch.setattr(config.os.path, "exists", lambda path: path == "/.dockerenv")

    assert Config._default_ollama_base_url() == "http://host.docker.internal:11434/v1"


def test_youtube_duration_limit_uses_active_subscription_plan(monkeypatch):
    monkeypatch.setenv("MAX_VIDEO_DURATION", "5400")
    monkeypatch.setenv("PRO_YOUTUBE_MAX_VIDEO_DURATION", "7200")
    monkeypatch.setenv("SCALE_YOUTUBE_MAX_VIDEO_DURATION", "10800")
    config = Config()

    assert config.max_youtube_video_duration_for_plan("free", "active") == 5400
    assert config.max_youtube_video_duration_for_plan("pro", "active") == 7200
    assert config.max_youtube_video_duration_for_plan("scale", "trialing") == 10800
    assert config.max_youtube_video_duration_for_plan("scale", "inactive") == 5400


def test_whisper_accepts_documented_model_size_setting(monkeypatch):
    monkeypatch.delenv("WHISPER_MODEL", raising=False)
    monkeypatch.setenv("WHISPER_MODEL_SIZE", "tiny")
    assert Config().whisper_model == "tiny"
    monkeypatch.setenv("WHISPER_MODEL", "base")
    assert Config().whisper_model == "base"


def test_transcription_language_defaults_to_auto_detect(monkeypatch):
    monkeypatch.delenv("TRANSCRIPTION_LANGUAGE", raising=False)
    assert Config().transcription_language is None

    monkeypatch.setenv("TRANSCRIPTION_LANGUAGE", "auto")
    assert Config().transcription_language is None

    monkeypatch.setenv("TRANSCRIPTION_LANGUAGE", " SW ")
    assert Config().transcription_language == "sw"

    monkeypatch.setenv("TRANSCRIPTION_LANGUAGE", "en-US")
    assert Config().transcription_language == "en_us"


def test_worker_job_timeout_scales_with_longest_video(monkeypatch):
    monkeypatch.delenv("WORKER_JOB_TIMEOUT_SECONDS", raising=False)
    monkeypatch.delenv("SCALE_YOUTUBE_MAX_VIDEO_DURATION", raising=False)
    monkeypatch.setenv("MAX_VIDEO_DURATION", "5400")
    assert Config().worker_job_timeout_seconds == 21600

    monkeypatch.setenv("MAX_VIDEO_DURATION", "18000")  # 5-hour videos
    assert Config().worker_job_timeout_seconds == 36000

    monkeypatch.setenv("WORKER_JOB_TIMEOUT_SECONDS", "")  # empty from Compose
    assert Config().worker_job_timeout_seconds == 36000
    monkeypatch.setenv("WORKER_JOB_TIMEOUT_SECONDS", "7200")
    assert Config().worker_job_timeout_seconds == 7200


def test_assemblyai_wait_grows_for_long_recordings(monkeypatch, tmp_path):
    from src.media import transcription

    monkeypatch.setattr(transcription, "ffprobe_duration", lambda path: 5 * 3600)
    assert transcription._assemblyai_wait_seconds(tmp_path / "a.mp3", 900) == 9000
    monkeypatch.setattr(transcription, "ffprobe_duration", lambda path: 600)
    assert transcription._assemblyai_wait_seconds(tmp_path / "a.mp3", 900) == 900

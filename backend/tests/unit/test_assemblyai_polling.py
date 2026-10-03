from types import SimpleNamespace
from unittest.mock import Mock
from pathlib import Path

import httpx
import pytest

from src.media import transcription


def polling_job(monkeypatch, handler):
    client = httpx.Client(base_url="https://api.assemblyai.com", transport=httpx.MockTransport(handler))
    sdk_client = SimpleNamespace(http_client=client)
    submitted = SimpleNamespace(id="existing-job", _client=sdk_client)
    transcriber = SimpleNamespace(
        upload_file=Mock(return_value="https://cdn.assemblyai.com/audio"),
        submit=Mock(return_value=submitted),
    )
    # The real SDK response parser runs; only the final cache-rich Transcript is stubbed.
    monkeypatch.setattr(transcription.aai.Transcript, "from_response", lambda **kwargs: kwargs["response"])
    monkeypatch.setattr(transcription.time, "sleep", lambda _: None)
    return transcriber, client


def response(status):
    return httpx.Response(200, json={"id": "existing-job", "status": status, "audio_url": "https://example.com/audio.mp3"})


def test_poll_timeout_retries_same_job_and_uses_short_request_timeout(monkeypatch):
    requests = []
    def handler(request):
        requests.append(request)
        if len(requests) == 1:
            raise httpx.ReadTimeout("temporary timeout", request=request)
        return response("completed")
    transcriber, client = polling_job(monkeypatch, handler)
    try:
        result = transcription._submit_and_wait_for_assemblyai_transcript(transcriber, Path("audio.mp3"), None, 900)
    finally:
        client.close()
    assert result.status == "completed"
    transcriber.submit.assert_called_once()
    assert all(str(r.url).endswith("/transcript/existing-job") for r in requests)
    assert all(r.extensions["timeout"]["read"] == 30 for r in requests)


def test_expired_budget_stops_before_another_poll(monkeypatch):
    requests = []
    def handler(request):
        requests.append(request)
        return response("processing")
    transcriber, client = polling_job(monkeypatch, handler)
    clock = iter([0, 0, 1, 2])
    monkeypatch.setattr(transcription.time, "monotonic", lambda: next(clock))
    try:
        with pytest.raises(TimeoutError, match="within 2s"):
            transcription._submit_and_wait_for_assemblyai_transcript(transcriber, Path("audio.mp3"), None, 2)
    finally:
        client.close()
    assert len(requests) == 1
    assert requests[0].extensions["timeout"]["read"] == 2
    transcriber.submit.assert_called_once()


def test_transcription_timeout_does_not_resubmit_paid_job(monkeypatch, tmp_path):
    runtime_config = SimpleNamespace(
        assembly_ai_api_key="test",
        assembly_ai_http_timeout_seconds=2,
        transcription_language=None,
    )
    helper = Mock(side_effect=TimeoutError("deadline exceeded"))
    monkeypatch.setattr(transcription, "_submit_and_wait_for_assemblyai_transcript", helper)
    monkeypatch.setattr(transcription, "_prepare_audio_for_transcription", lambda path: path)
    monkeypatch.setattr(transcription, "_upload_for_assemblyai", lambda transcriber, path: "https://cdn/audio")
    with pytest.raises(TimeoutError):
        transcription._get_transcript_with_assemblyai(tmp_path / "video.mp4", "universal", runtime_config)
    helper.assert_called_once()


def test_upload_timeout_retries_before_single_paid_submission(monkeypatch):
    transcriber, client = polling_job(monkeypatch, lambda _: response("completed"))
    audio_url = "https://cdn.assemblyai.com/audio"
    transcriber.upload_file.side_effect = [httpx.ReadTimeout("upload timeout"), audio_url]
    try:
        transcription._submit_and_wait_for_assemblyai_transcript(transcriber, Path("audio.mp3"), None, 900)
    finally:
        client.close()
    assert transcriber.upload_file.call_count == 2
    transcriber.submit.assert_called_once_with(audio_url, config=None)


def test_exhausted_upload_retries_do_not_submit_transcript(monkeypatch):
    transcriber, client = polling_job(monkeypatch, lambda _: response("completed"))
    transcriber.upload_file.side_effect = httpx.ConnectError("upload unavailable")
    try:
        with pytest.raises(httpx.ConnectError):
            transcription._submit_and_wait_for_assemblyai_transcript(transcriber, Path("audio.mp3"), None, 900)
    finally:
        client.close()
    assert transcriber.upload_file.call_count == 3
    transcriber.submit.assert_not_called()


def test_submission_timeout_is_not_retried(monkeypatch):
    transcriber, client = polling_job(monkeypatch, lambda _: response("completed"))
    transcriber.submit.side_effect = httpx.ReadTimeout("submission response lost")
    try:
        with pytest.raises(httpx.ReadTimeout):
            transcription._submit_and_wait_for_assemblyai_transcript(transcriber, Path("audio.mp3"), None, 900)
    finally:
        client.close()
    transcriber.upload_file.assert_called_once()
    transcriber.submit.assert_called_once()


def _fake_transcript(status, error=None):
    return SimpleNamespace(
        status=status,
        error=error,
        utterances=[SimpleNamespace(start=0, end=2000, speaker="A", text="Habari za leo", words=[])],
        words=[],
        text="Habari za leo",
        json_response={"language_code": "sw"},
    )


def test_rejected_request_retries_with_safer_models(monkeypatch, tmp_path):
    runtime_config = SimpleNamespace(
        assembly_ai_api_key="test",
        assembly_ai_http_timeout_seconds=2,
        transcription_language=None,
    )
    seen = []

    def submit(transcriber, path, config, timeout, audio_url=None):
        seen.append((list(config.speech_models), config.speaker_labels, audio_url))
        if len(seen) == 1:
            raise transcription.aai.types.TranscriptError("speech_models: unknown model", 400)
        if len(seen) == 2:
            return _fake_transcript(transcription.aai.TranscriptStatus.error, "language not supported")
        return _fake_transcript(transcription.aai.TranscriptStatus.completed)

    uploads = []
    monkeypatch.setattr(transcription, "_submit_and_wait_for_assemblyai_transcript", submit)
    monkeypatch.setattr(transcription, "_prepare_audio_for_transcription", lambda path: path)
    monkeypatch.setattr(
        transcription, "_upload_for_assemblyai", lambda transcriber, path: uploads.append(path) or "https://cdn/audio"
    )
    monkeypatch.setattr(transcription, "cache_transcript_data", lambda *args: None)
    video = tmp_path / "video.mp4"
    result = transcription._get_transcript_with_assemblyai(video, "universal", runtime_config)

    assert "Habari za leo" in result
    assert seen == [
        (["universal-3-5-pro", "universal-2"], True, "https://cdn/audio"),
        (["universal-3-pro", "universal-2"], True, "https://cdn/audio"),
        (["universal-2"], True, "https://cdn/audio"),
    ]
    assert len(uploads) == 1  # audio uploaded once, reused for every attempt


def test_auth_errors_are_not_retried(monkeypatch, tmp_path):
    runtime_config = SimpleNamespace(
        assembly_ai_api_key="bad", assembly_ai_http_timeout_seconds=2, transcription_language=None
    )
    calls = []

    def submit(*args, **kwargs):
        calls.append(1)
        raise transcription.aai.types.TranscriptError("Invalid API key", 401)

    monkeypatch.setattr(transcription, "_submit_and_wait_for_assemblyai_transcript", submit)
    monkeypatch.setattr(transcription, "_prepare_audio_for_transcription", lambda path: path)
    monkeypatch.setattr(transcription, "_upload_for_assemblyai", lambda transcriber, path: "u")
    with pytest.raises(RuntimeError, match="Invalid API key"):
        transcription._get_transcript_with_assemblyai(tmp_path / "v.mp4", "universal", runtime_config)
    assert len(calls) == 1


def test_all_attempts_failing_reports_assemblyai_reason(monkeypatch, tmp_path):
    runtime_config = SimpleNamespace(
        assembly_ai_api_key="test", assembly_ai_http_timeout_seconds=2, transcription_language=None
    )
    monkeypatch.setattr(
        transcription,
        "_submit_and_wait_for_assemblyai_transcript",
        lambda *a, **k: _fake_transcript(transcription.aai.TranscriptStatus.error, "audio has no speech"),
    )
    monkeypatch.setattr(transcription, "_prepare_audio_for_transcription", lambda path: path)
    monkeypatch.setattr(transcription, "_upload_for_assemblyai", lambda transcriber, path: "u")
    with pytest.raises(RuntimeError, match="Transcription failed: audio has no speech"):
        transcription._get_transcript_with_assemblyai(tmp_path / "v.mp4", "universal", runtime_config)

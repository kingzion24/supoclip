import shutil
import subprocess
from pathlib import Path

import pytest

from src.media import audio_enhancements as ae


@pytest.fixture
def music_dir(tmp_path, monkeypatch):
    (tmp_path / "beat.mp3").write_bytes(b"x")
    (tmp_path / "README.md").write_text("not a track")
    monkeypatch.setattr(ae, "MUSIC_DIR", tmp_path)
    return tmp_path


def test_normalize_audio_settings_validates_inputs(music_dir):
    assert ae.normalize_audio_settings() == {
        "background_music": None,
        "music_volume": ae.DEFAULT_MUSIC_VOLUME,
        "hook_voiceover": False,
        "voiceover_voice": None,
    }
    settings = ae.normalize_audio_settings("beat.mp3", "0.4", True, "sw-TZ-DaudiNeural")
    assert settings == {
        "background_music": "beat.mp3",
        "music_volume": 0.4,
        "hook_voiceover": True,
        "voiceover_voice": "sw-TZ-DaudiNeural",
    }
    # Unknown tracks, path tricks, out-of-range volume and bad voices are dropped.
    assert ae.normalize_audio_settings("../secret.mp3")["background_music"] is None
    assert ae.normalize_audio_settings("README.md")["background_music"] is None
    assert ae.normalize_audio_settings("random")["background_music"] == "random"
    assert ae.normalize_audio_settings(music_volume=7)["music_volume"] == 1.0
    assert ae.normalize_audio_settings(music_volume="nan")["music_volume"] == ae.DEFAULT_MUSIC_VOLUME
    assert ae.normalize_audio_settings(hook_voiceover="yes")["hook_voiceover"] is False
    assert ae.normalize_audio_settings(voiceover_voice="rm -rf")["voiceover_voice"] is None


def test_audio_settings_from_metadata_revalidates(music_dir):
    metadata = {"audio_settings": {"background_music": "gone.mp3", "hook_voiceover": True}}
    settings = ae.audio_settings_from_metadata(metadata)
    assert settings["background_music"] is None
    assert settings["hook_voiceover"] is True
    assert ae.audio_settings_from_metadata(None)["hook_voiceover"] is False


def test_choose_voice_prefers_explicit_then_language():
    assert ae.choose_voice("sw") == "sw-TZ-RehemaNeural"
    assert ae.choose_voice("en_us") == "en-US-AriaNeural"
    assert ae.choose_voice(None) == ae.FALLBACK_VOICE
    assert ae.choose_voice("sw", "sw-KE-ZuriNeural") == "sw-KE-ZuriNeural"


def test_build_audio_mix_command_ducks_original_under_voiceover():
    command = ae.build_audio_mix_command(
        Path("clip.mp4"),
        Path("out.mp4"),
        clip_duration=20.0,
        clip_has_audio=True,
        music_path=Path("beat.mp3"),
        music_volume=0.2,
        voiceover_path=Path("vo.mp3"),
        voiceover_duration=2.0,
    )
    graph = command[command.index("-filter_complex") + 1]
    assert "between(t,0.300,2.300),0.3,1" in graph
    assert "volume=0.2" in graph
    assert "amix=inputs=3" in graph
    assert command[command.index("-c:v") + 1] == "copy"
    assert command.count("-i") == 3
    assert "-stream_loop" in command


def test_build_audio_mix_command_handles_silent_clip():
    command = ae.build_audio_mix_command(
        Path("clip.mp4"), Path("out.mp4"), 10.0, clip_has_audio=False, music_path=Path("beat.mp3")
    )
    graph = command[command.index("-filter_complex") + 1]
    assert "[0:a]" not in graph
    assert "amix=inputs=1" in graph


def test_apply_audio_enhancements_skips_when_disabled(tmp_path):
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"video")
    assert ae.apply_audio_enhancements(clip, ae.normalize_audio_settings()) is False
    assert clip.read_bytes() == b"video"


def test_apply_audio_enhancements_keeps_clip_when_tts_fails(tmp_path, monkeypatch):
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"video")
    monkeypatch.setattr(ae, "ffprobe_duration", lambda path: 12.0)
    monkeypatch.setattr(ae, "synthesize_voiceover", lambda *args: False)
    settings = {"background_music": None, "hook_voiceover": True, "music_volume": 0.1}
    assert ae.apply_audio_enhancements(clip, settings, "Habari za leo", "sw") is False
    assert clip.read_bytes() == b"video"


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_apply_audio_enhancements_mixes_with_ffmpeg(tmp_path, monkeypatch):
    def lavfi(args, output):
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", *args, str(output)], check=True)

    clip = tmp_path / "clip.mp4"
    lavfi(
        ["-f", "lavfi", "-i", "testsrc=size=180x320:rate=15", "-f", "lavfi",
         "-i", "sine=frequency=300", "-t", "4", "-c:v", "libx264", "-c:a", "aac", "-shortest"],
        clip,
    )
    lavfi(["-f", "lavfi", "-i", "sine=frequency=800", "-t", "1"], tmp_path / "beat.mp3")
    voice = tmp_path / "voice.mp3"
    lavfi(["-f", "lavfi", "-i", "sine=frequency=1200", "-t", "1"], voice)
    monkeypatch.setattr(ae, "MUSIC_DIR", tmp_path)
    spoken = []

    def fake_tts(text, chosen_voice, output_path):
        spoken.append((text, chosen_voice))
        shutil.copy(voice, output_path)
        return True

    monkeypatch.setattr(ae, "synthesize_voiceover", fake_tts)
    settings = ae.normalize_audio_settings("random", 0.2, True)

    assert ae.apply_audio_enhancements(clip, settings, "Siri ya mafanikio", "sw") is True
    assert spoken == [("Siri ya mafanikio", "sw-TZ-RehemaNeural")]
    assert abs(ae.ffprobe_duration(clip) - 4.0) < 0.2
    assert ae.ffprobe_has_audio(clip)

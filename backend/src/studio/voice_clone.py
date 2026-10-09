"""Make the Edge narration sound like the creator's own voice.

Uses OpenVoice V2's tone-colour converter (MIT, https://github.com/myshell-ai/OpenVoice,
vendored in ``studio/openvoice``). The Edge voice still decides the words,
rhythm and pauses; the converter swaps its timbre for the creator's, so the
audio keeps its length and the word timings used for captions stay exact.

The converter checkpoint (~130 MB) is downloaded from Hugging Face on first
use into ``{TEMP_DIR}/models/openvoice_v2`` (or ``OPENVOICE_DIR``). Only clone
a voice you own or have permission to use.
"""

import json
import logging
import os
import subprocess
import tempfile
import threading
import wave
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np

from ..config import get_config
from ..media.ffmpeg import run_ffmpeg_command

logger = logging.getLogger(__name__)

HF_REPO = "myshell-ai/OpenVoiceV2"
CONVERTER_FILES = ("converter/config.json", "converter/checkpoint.pth")
DEFAULT_TAU = 0.3
REFERENCE_CHUNK_SECONDS = 8
MAX_REFERENCE_CHUNKS = 24
MIN_SAMPLE_SECONDS = 15

# Read by the base Edge voice to learn its timbre (about 30 seconds of speech).
BASE_REFERENCE_SENTENCES = [
    "Habari za leo, rafiki yangu. Karibu tena kwenye simulizi yetu ya kila wiki.",
    "Leo tutazungumza kuhusu maisha, pesa, historia na mambo yanayobadilisha dunia yetu.",
    "Je, umewahi kujiuliza kwa nini watu wengine hufanikiwa haraka kuliko wengine?",
    "Jibu si rahisi kama unavyofikiri, lakini linaanza na tabia ndogo za kila siku.",
    "Kutoka Dar es Salaam hadi Arusha, kutoka Mwanza hadi Zanzibar, hadithi hizi zinatuhusu sote.",
    "Kaa nami hadi mwisho, kwa sababu siri kubwa iko kwenye sehemu ya mwisho kabisa.",
]

_lock = threading.Lock()
_converter: Optional["ToneConverter"] = None


def model_dir() -> Path:
    override = os.getenv("OPENVOICE_DIR", "").strip()
    return Path(override) if override else Path(get_config().temp_dir) / "models" / "openvoice_v2"


def ensure_checkpoint() -> Path:
    """Download the converter checkpoint once and return its folder."""
    directory = model_dir()
    if all((directory / name).exists() for name in CONVERTER_FILES):
        return directory / "converter"
    from huggingface_hub import hf_hub_download

    logger.info("Downloading the OpenVoice V2 converter to %s", directory)
    for name in CONVERTER_FILES:
        hf_hub_download(HF_REPO, name, local_dir=str(directory))
    return directory / "converter"


def load_audio(path: Path, sample_rate: int) -> np.ndarray:
    """Decode any audio/video file to mono float32 at ``sample_rate``."""
    result = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-ac", "1", "-ar", str(sample_rate), "-f", "f32le", "-"],
        capture_output=True,
        timeout=600,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Could not read audio from {path.name}")
    return np.frombuffer(result.stdout, dtype=np.float32).copy()


def write_wav(path: Path, audio: np.ndarray, sample_rate: int) -> None:
    pcm = (np.clip(audio, -1.0, 1.0) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(pcm.tobytes())


def spectrogram(y, n_fft: int, hop: int, win: int):
    """Linear spectrogram exactly as OpenVoice computes it (mel_processing.spectrogram_torch)."""
    import torch

    window = torch.hann_window(win).to(dtype=y.dtype, device=y.device)
    pad = int((n_fft - hop) / 2)
    y = torch.nn.functional.pad(y.unsqueeze(1), (pad, pad), mode="reflect").squeeze(1)
    spec = torch.stft(
        y, n_fft, hop_length=hop, win_length=win, window=window, center=False,
        pad_mode="reflect", normalized=False, onesided=True, return_complex=True,
    )
    return torch.sqrt(spec.real.pow(2) + spec.imag.pow(2) + 1e-6)


class ToneConverter:
    def __init__(self, config: Dict[str, Any], checkpoint: Optional[Path] = None, device: str = "cpu"):
        import torch

        from .openvoice.models import SynthesizerTrn

        self.config = config
        data = config["data"]
        self.sample_rate = int(data["sampling_rate"])
        self.n_fft = int(data["filter_length"])
        self.hop = int(data["hop_length"])
        self.win = int(data["win_length"])
        self.device = device
        self.model = SynthesizerTrn(
            len(config.get("symbols", [])),
            self.n_fft // 2 + 1,
            n_speakers=data.get("n_speakers", 0),
            **config["model"],
        ).to(device)
        self.model.eval()
        if checkpoint is not None:
            state = torch.load(str(checkpoint), map_location=torch.device(device))
            missing, unexpected = self.model.load_state_dict(state["model"], strict=False)
            if missing:
                logger.warning("OpenVoice checkpoint is missing %s weights", len(missing))

    @classmethod
    def from_dir(cls, directory: Path) -> "ToneConverter":
        config = json.loads((directory / "config.json").read_text())
        return cls(config, directory / "checkpoint.pth")

    def _spec(self, audio: np.ndarray):
        import torch

        y = torch.from_numpy(audio).float().unsqueeze(0).to(self.device)
        return spectrogram(y, self.n_fft, self.hop, self.win)

    def extract_se(self, paths: List[Path]):
        """Average tone-colour embedding over several reference clips."""
        import torch

        embeddings = []
        for path in paths:
            audio = load_audio(path, self.sample_rate)
            if len(audio) < self.n_fft * 2:
                continue
            with torch.no_grad():
                spec = self._spec(audio)
                embeddings.append(self.model.ref_enc(spec.transpose(1, 2)).unsqueeze(-1))
        if not embeddings:
            raise RuntimeError("The recording is too short or silent to learn a voice from")
        return torch.stack(embeddings).mean(0)

    def convert(self, source: Path, src_se, tgt_se, output: Path, tau: float = DEFAULT_TAU) -> None:
        import torch

        audio = load_audio(source, self.sample_rate)
        with torch.no_grad():
            spec = self._spec(audio)
            lengths = torch.LongTensor([spec.size(-1)]).to(self.device)
            converted = self.model.voice_conversion(spec, lengths, sid_src=src_se, sid_tgt=tgt_se, tau=tau)[0][0, 0]
        write_wav(output, converted.cpu().float().numpy(), self.sample_rate)


def get_converter() -> ToneConverter:
    global _converter
    with _lock:
        if _converter is None:
            _converter = ToneConverter.from_dir(ensure_checkpoint())
        return _converter


def split_reference(sample: Path, work: Path) -> List[Path]:
    """Trim silences and cut the creator's recording into short clips."""
    cleaned = work / "clean.wav"
    result = run_ffmpeg_command(
        [
            "ffmpeg", "-y", "-i", str(sample), "-ac", "1", "-ar", "22050",
            "-af", "highpass=f=70,silenceremove=stop_periods=-1:stop_duration=0.4:stop_threshold=-40dB",
            str(cleaned),
        ]
    )
    if result.returncode != 0:
        raise RuntimeError("Could not read the voice recording")
    pattern = work / "chunk%03d.wav"
    run_ffmpeg_command(
        ["ffmpeg", "-y", "-i", str(cleaned), "-f", "segment", "-segment_time", str(REFERENCE_CHUNK_SECONDS), str(pattern)]
    )
    chunks = sorted(work.glob("chunk*.wav"))[:MAX_REFERENCE_CHUNKS]
    return [chunk for chunk in chunks if chunk.stat().st_size > 22050] or [cleaned]


def save_se(se, path: Path) -> None:
    import torch

    path.parent.mkdir(parents=True, exist_ok=True)
    torch.save(se.cpu(), str(path))


def load_se(path: Path):
    import torch

    return torch.load(str(path), map_location="cpu")


async def base_voice_se(voice: str):
    """Embedding of an Edge voice, learned once from a short reference read."""
    from .voice import _synthesize_sentence

    path = model_dir() / "base_se" / f"{voice}.pt"
    if path.exists():
        return load_se(path)
    converter = get_converter()
    with tempfile.TemporaryDirectory(prefix="openvoice_base_") as temp:
        clips = []
        for index, sentence in enumerate(BASE_REFERENCE_SENTENCES):
            clip = Path(temp) / f"base{index}.mp3"
            await _synthesize_sentence(sentence, voice, "+0%", "+0Hz", clip)
            clips.append(clip)
        se = converter.extract_se(clips)
    save_se(se, path)
    return se


def learn_voice(sample: Path, embedding_path: Path) -> Dict[str, Any]:
    """Learn the creator's voice from ``sample`` and save its embedding."""
    converter = get_converter()
    with tempfile.TemporaryDirectory(prefix="openvoice_ref_") as temp:
        chunks = split_reference(sample, Path(temp))
        seconds = sum(len(load_audio(chunk, 16000)) for chunk in chunks) / 16000
        if seconds < MIN_SAMPLE_SECONDS:
            raise RuntimeError(
                f"Only {seconds:.0f} seconds of speech found. Record at least 30 seconds of clear talking."
            )
        se = converter.extract_se(chunks)
    save_se(se, embedding_path)
    return {"speech_seconds": round(seconds, 1), "chunks": len(chunks)}


def convert_to_voice(source: Path, src_se, tgt_se, output: Path, tau: float = DEFAULT_TAU) -> None:
    """Re-voice ``source`` (any audio) and write a 48 kHz mono wav to ``output``."""
    converter = get_converter()
    with tempfile.TemporaryDirectory(prefix="openvoice_convert_") as temp:
        raw = Path(temp) / "converted.wav"
        converter.convert(source, src_se, tgt_se, raw, tau)
        result = run_ffmpeg_command(
            ["ffmpeg", "-y", "-i", str(raw), "-af", "loudnorm=I=-16:TP=-1.5:LRA=11", "-ac", "1", "-ar", "48000", str(output)]
        )
        if result.returncode != 0:
            raise RuntimeError("Could not finish the cloned voice")

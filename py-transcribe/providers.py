#!/usr/bin/env python3
"""
providers.py — Transcription backend abstraction for the warm-model service.

Supports two Whisper backends behind a common interface, chosen in config.yaml
via `transcribe_provider`:
  - faster-whisper (CTranslate2): ~4x faster, lower memory; recommended on GPU.
  - openai-whisper (PyTorch reference): identical output to the batch pipeline.

Both implementations return a normalised list of segment dicts
``[{"start": float, "end": float, "text": str}, ...]`` with timestamps relative
to the start of the given audio file. The end time is the *tightest* available
(the last word's end when word timestamps exist) so a segment isn't stretched
past when the speaker actually stopped — matching py-process/transcribe.py.

The heavy backend libraries (``whisper`` / ``faster_whisper``) are imported
lazily inside each implementation's constructor, so importing this module is
cheap and does not require both backends to be installed — only the one that is
actually configured.
"""

import sys
from abc import ABC, abstractmethod


class TranscriptionProvider(ABC):
    """Abstract base class for Whisper transcription backends."""

    backend: str = "unknown"
    model_name: str = ""
    device: str = ""

    @abstractmethod
    def transcribe(self, wav_path: str, language: str | None) -> list[dict]:
        """Transcribe one audio file.

        Returns a list of ``{"start", "end", "text"}`` segments with timestamps
        relative to the start of the file (the caller adds the snippet offset).
        """


class OpenAIWhisperTranscriber(TranscriptionProvider):
    """Transcription via the OpenAI Whisper (PyTorch) reference implementation."""

    backend = "openai-whisper"

    def __init__(self, model_name: str, device: str | None):
        import whisper  # lazy import — only when this backend is selected

        self.model_name = model_name
        self.device = device or "cpu"
        # device=None lets Whisper auto-select (CUDA if available).
        self._model = whisper.load_model(model_name, device=device or None)

    def transcribe(self, wav_path: str, language: str | None) -> list[dict]:
        options: dict = {"word_timestamps": True}
        if language:
            options["language"] = language

        result = self._model.transcribe(wav_path, **options)
        segments: list[dict] = []
        for seg in result.get("segments", []):
            text = (seg.get("text") or "").strip()
            if not text:
                continue
            words = seg.get("words")
            end = words[-1]["end"] if words else seg["end"]
            segments.append({"start": float(seg["start"]), "end": float(end), "text": text})
        return segments


class FasterWhisperTranscriber(TranscriptionProvider):
    """Transcription via faster-whisper (CTranslate2 backend)."""

    backend = "faster-whisper"

    def __init__(self, model_name: str, device: str | None, compute_type: str):
        from faster_whisper import WhisperModel  # lazy import

        self.model_name = model_name
        self.device = device or "auto"
        self.compute_type = compute_type
        self._model = WhisperModel(
            model_name, device=self.device, compute_type=compute_type
        )

    def transcribe(self, wav_path: str, language: str | None) -> list[dict]:
        segments_iter, _info = self._model.transcribe(
            wav_path,
            language=language,
            word_timestamps=True,
        )
        segments: list[dict] = []
        for seg in segments_iter:  # generator — consuming it runs the transcription
            text = (seg.text or "").strip()
            if not text:
                continue
            words = getattr(seg, "words", None)
            end = words[-1].end if words else seg.end
            segments.append({"start": float(seg.start), "end": float(end), "text": text})
        return segments


def get_transcription_provider(config: dict) -> TranscriptionProvider:
    """
    Instantiate and return the configured transcription provider.

    Reads from config:
      transcribe_provider     — 'faster-whisper' (default) | 'openai-whisper'
      transcribe_model        — model size; falls back to whisper_model, then 'base'
      transcribe_device       — 'cuda' | 'cpu' | 'auto' (default 'cuda')
      transcribe_compute_type — faster-whisper compute type (default 'float16')
    """
    provider = str(config.get("transcribe_provider", "faster-whisper")).lower()
    model_name = config.get("transcribe_model") or config.get("whisper_model", "base")
    device = config.get("transcribe_device", "cuda")

    if provider in ("faster-whisper", "faster_whisper", "fasterwhisper"):
        compute_type = config.get("transcribe_compute_type", "float16")
        try:
            return FasterWhisperTranscriber(model_name, device, compute_type)
        except ImportError:
            print(
                "Error: faster-whisper is not installed. "
                "Run: pip install faster-whisper",
                file=sys.stderr,
            )
            sys.exit(1)

    if provider in ("openai-whisper", "openai_whisper", "whisper", "openai"):
        try:
            return OpenAIWhisperTranscriber(model_name, device)
        except ImportError:
            print(
                "Error: openai-whisper is not installed. "
                "Run: pip install openai-whisper",
                file=sys.stderr,
            )
            sys.exit(1)

    print(
        f"Error: Unknown transcribe_provider '{provider}'. "
        "Supported values: faster-whisper, openai-whisper",
        file=sys.stderr,
    )
    sys.exit(1)

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


def _opt_float(value) -> float | None:
    """Coerce a config value to float, treating None/empty as 'disabled' (None)."""
    if value is None or value == "":
        return None
    return float(value)


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
        # openai-whisper does not understand "auto" — let Whisper auto-select
        # (CUDA if available) by passing None in that case.
        resolved_device = None if (not device or device == "auto") else device
        self.device = resolved_device or "cpu"
        self._model = whisper.load_model(model_name, device=resolved_device)

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
    """Transcription via faster-whisper (CTranslate2 backend).

    Large/distil models transcribe real speech well but hallucinate boilerplate
    ("Thank you.", "I don't know.") on noise-only clips. To suppress that without
    hurting real speech, ``transcribe()`` applies a layered, config-driven pruning
    pass: Silero VAD strips non-speech *before* decoding, faster-whisper's own
    silence/garbage gates run during decoding, and a ``no_speech_prob`` post-filter
    is a final backstop. The output dict shape is unchanged.
    """

    backend = "faster-whisper"

    def __init__(
        self,
        model_name: str,
        device: str | None,
        compute_type: str,
        *,
        vad_filter: bool = True,
        vad_min_silence_ms: int = 500,
        condition_on_previous_text: bool = False,
        no_speech_threshold: float = 0.6,
        log_prob_threshold: float = -1.0,
        compression_ratio_threshold: float = 2.4,
        hallucination_silence_threshold: float | None = 2.0,
        max_no_speech_prob: float | None = 0.8,
    ):
        from faster_whisper import WhisperModel  # lazy import

        self.model_name = model_name
        self.device = device or "auto"
        self.compute_type = compute_type
        self.vad_filter = vad_filter
        self.vad_min_silence_ms = vad_min_silence_ms
        self.condition_on_previous_text = condition_on_previous_text
        self.no_speech_threshold = no_speech_threshold
        self.log_prob_threshold = log_prob_threshold
        self.compression_ratio_threshold = compression_ratio_threshold
        self.hallucination_silence_threshold = hallucination_silence_threshold
        self.max_no_speech_prob = max_no_speech_prob
        self._model = WhisperModel(
            model_name, device=self.device, compute_type=compute_type
        )

    def transcribe(self, wav_path: str, language: str | None) -> list[dict]:
        options: dict = {
            "language": language,
            "word_timestamps": True,
            "vad_filter": self.vad_filter,
            "condition_on_previous_text": self.condition_on_previous_text,
            "no_speech_threshold": self.no_speech_threshold,
            "log_prob_threshold": self.log_prob_threshold,
            "compression_ratio_threshold": self.compression_ratio_threshold,
            "hallucination_silence_threshold": self.hallucination_silence_threshold,
        }
        if self.vad_filter:
            options["vad_parameters"] = {"min_silence_duration_ms": self.vad_min_silence_ms}

        segments_iter, _info = self._model.transcribe(wav_path, **options)
        segments: list[dict] = []
        for seg in segments_iter:  # generator — consuming it runs the transcription
            text = (seg.text or "").strip()
            if not text:
                continue
            # Backstop after VAD: drop segments the model itself flags as likely
            # non-speech (a common source of "Thank you." on noise-only clips).
            if self.max_no_speech_prob is not None:
                if getattr(seg, "no_speech_prob", 0.0) > self.max_no_speech_prob:
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

    faster-whisper noise/hallucination pruning (all optional, safe defaults):
      transcribe_vad_filter                       — Silero VAD pre-filter (default true)
      transcribe_vad_min_silence_ms               — VAD min_silence_duration_ms (default 500)
      transcribe_condition_on_previous_text       — default false (stops hallucination carry-over)
      transcribe_no_speech_threshold              — default 0.6
      transcribe_log_prob_threshold               — default -1.0
      transcribe_compression_ratio_threshold      — default 2.4
      transcribe_hallucination_silence_threshold  — seconds, null to disable (default 2.0)
      transcribe_max_no_speech_prob               — post-filter cutoff, null to disable (default 0.8)
    """
    provider = str(config.get("transcribe_provider", "faster-whisper")).lower()
    model_name = config.get("transcribe_model") or config.get("whisper_model", "base")
    device = config.get("transcribe_device", "cuda")

    if provider in ("faster-whisper", "faster_whisper", "fasterwhisper"):
        compute_type = config.get("transcribe_compute_type", "float16")
        try:
            return FasterWhisperTranscriber(
                model_name,
                device,
                compute_type,
                vad_filter=bool(config.get("transcribe_vad_filter", True)),
                vad_min_silence_ms=int(config.get("transcribe_vad_min_silence_ms", 500)),
                condition_on_previous_text=bool(
                    config.get("transcribe_condition_on_previous_text", False)
                ),
                no_speech_threshold=float(config.get("transcribe_no_speech_threshold", 0.6)),
                log_prob_threshold=float(config.get("transcribe_log_prob_threshold", -1.0)),
                compression_ratio_threshold=float(
                    config.get("transcribe_compression_ratio_threshold", 2.4)
                ),
                hallucination_silence_threshold=_opt_float(
                    config.get("transcribe_hallucination_silence_threshold", 2.0)
                ),
                max_no_speech_prob=_opt_float(
                    config.get("transcribe_max_no_speech_prob", 0.8)
                ),
            )
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

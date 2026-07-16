#!/usr/bin/env python3
"""
transcribe.py — Assemble session transcripts from per-user utterance snippets.

Each participant has a sub-folder of timestamped snippet WAVs:

    <session>/<username>/<offset_ms>.wav

The snippet filename is the zero-padded session-relative start offset in
milliseconds (e.g. ``0000012840.wav`` => 12.84 s into the session).

Two sources of per-snippet text are supported:

  1. **Sidecars (fast path).** If the live transcription service
     (py-transcribe) already transcribed a snippet, a ``<offset_ms>.json``
     sidecar sits next to the WAV with session-relative segments. We load those
     directly — no Whisper needed. When *every* snippet has a sidecar, the
     Whisper model is never loaded at all and assembly is near-instant. With
     ``delete_wav_after_transcribe`` on, the WAV is gone and only the sidecar
     remains — assembly works from sidecars alone, so snippets are enumerated by
     the union of WAV and sidecar filenames (see ``iter_snippets``).

  2. **Batch fallback.** Snippets without a sidecar are transcribed here with
     OpenAI Whisper (loaded once), then their clip-relative timestamps are
     shifted by the filename offset to become session-relative.

Either way this script remains the single assembler of the per-user ``.txt``
files and ``_combined_transcript.txt`` (unchanged format), so vectorize.py and
summarize.py need no changes.

Usage:
    python transcribe.py <session_name> [--model <size>] [--language <lang>]
"""

# Annotations are lazy strings so we can reference whisper types in signatures
# without importing the (heavy) whisper package at module load — it is imported
# only when a snippet actually needs batch transcription (see main()).
from __future__ import annotations

import argparse
import json
import sys
import time
import wave
from pathlib import Path

import yaml


def load_config() -> dict:
    """Load config.yaml from the repo root (one level up from py-process/)."""
    config_path = Path(__file__).resolve().parent.parent / "config.yaml"
    if not config_path.exists():
        print(f"Error: config.yaml not found at {config_path}", file=sys.stderr)
        sys.exit(1)
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def format_timestamp(seconds: float) -> str:
    """Format seconds into HH:MM:SS.mmm for transcript timestamps."""
    hrs = int(seconds // 3600)
    mins = int((seconds % 3600) // 60)
    secs = seconds % 60
    return f"{hrs:02d}:{mins:02d}:{secs:06.3f}"


def transcribe_wav(model: whisper.Whisper, wav_path: Path, language: str | None) -> dict:
    """Run Whisper transcription on a single snippet WAV file (batch fallback).

    word_timestamps=True is always enabled so that each segment's end time
    reflects the last word spoken rather than the next silence boundary.
    """
    options: dict = {"word_timestamps": True}
    if language:
        options["language"] = language
    return model.transcribe(str(wav_path), **options)


def _seg_end(seg: dict) -> float:
    """Tightest available end timestamp for a Whisper segment (last word end)."""
    words = seg.get("words")
    if words:
        return words[-1]["end"]
    return seg["end"]


def parse_offset_seconds(wav_path: Path) -> float:
    """Session-relative start offset (seconds) encoded in the snippet filename."""
    try:
        return int(wav_path.stem) / 1000.0
    except ValueError:
        print(
            f"Warning: snippet {wav_path.name} has a non-numeric name; using offset 0.",
            file=sys.stderr,
        )
        return 0.0


def wav_duration_ms(wav_path: Path) -> float:
    """Duration of a WAV file in milliseconds (0.0 if it can't be read)."""
    try:
        with wave.open(str(wav_path), "rb") as w:
            rate = w.getframerate()
            frames = w.getnframes()
        return (frames / float(rate)) * 1000.0 if rate else 0.0
    except (wave.Error, OSError, EOFError):
        return 0.0


def iter_snippets(user_dir: Path) -> list[Path]:
    """Sorted snippet base paths (``<offset_ms>.wav``) in a participant folder.

    A snippet may exist as a WAV, a JSON sidecar, or both — ``delete_wav_after_
    transcribe`` leaves only the sidecar once a snippet is transcribed. We
    therefore enumerate the union of WAV and sidecar stems and return one
    ``.wav``-suffixed Path per snippet (the WAV may not exist; callers check
    before reading it). Filenames are zero-padded offsets, so the stems sort
    chronologically.
    """
    stems = {p.stem for p in user_dir.glob("*.wav")}
    stems.update(p.stem for p in user_dir.glob("*.json"))
    return [user_dir / f"{stem}.wav" for stem in sorted(stems)]


def load_sidecar(wav_path: Path) -> dict | None:
    """Load a snippet's ``<offset_ms>.json`` sidecar if present.

    Returns ``{"language": str, "segments": [{start, end, text}, ...]}`` with
    timestamps already session-relative, or None when there is no (valid) sidecar.
    """
    sidecar = wav_path.with_suffix(".json")
    if not sidecar.exists():
        return None
    try:
        with open(sidecar, "r", encoding="utf-8") as f:
            data = json.load(f)
        segments = [
            {
                "start": float(s["start"]),
                "end": float(s["end"]),
                "text": str(s["text"]).strip(),
            }
            for s in data.get("segments", [])
            if str(s.get("text", "")).strip()
        ]
        return {"language": data.get("language", "unknown"), "segments": segments}
    except (json.JSONDecodeError, OSError, KeyError, ValueError) as exc:
        print(f"Warning: ignoring bad sidecar {sidecar}: {exc}", file=sys.stderr)
        return None


def transcribe_user(
    model: whisper.Whisper | None,
    user_dir: Path,
    language: str | None,
    min_ms: float,
) -> tuple[list[dict], str]:
    """Collect a participant's snippet segments (session-relative), in order.

    Uses each snippet's sidecar when present; otherwise transcribes the WAV with
    ``model`` (which is non-None whenever any sidecar-less snippet needs it).
    Snippets shorter than ``min_ms`` that lack a sidecar are skipped as blips.
    """
    segments: list[dict] = []
    detected_lang = "unknown"

    for wav_path in iter_snippets(user_dir):
        sidecar = load_sidecar(wav_path)
        if sidecar is not None:
            if sidecar["language"] and sidecar["language"] != "unknown":
                detected_lang = sidecar["language"]
            segments.extend(sidecar["segments"])  # already session-relative
            continue

        # No sidecar — the snippet must be batch-transcribed from its WAV. A
        # sidecar-only snippet (WAV removed by delete_wav_after_transcribe) is
        # always handled above, so reaching here normally means the WAV exists;
        # guard anyway so a vanished file never aborts the run.
        if not wav_path.exists():
            print(
                f"Warning: snippet {wav_path.name} has neither WAV nor sidecar; skipping.",
                file=sys.stderr,
            )
            continue
        if wav_duration_ms(wav_path) < min_ms:
            continue  # sub-threshold blip, no sidecar — skip
        if model is None:
            print(
                f"Warning: no model loaded but {wav_path} needs transcription; skipping.",
                file=sys.stderr,
            )
            continue

        offset_s = parse_offset_seconds(wav_path)
        result = transcribe_wav(model, wav_path, language)
        detected_lang = result.get("language", detected_lang)
        for seg in result.get("segments", []):
            text = seg["text"].strip()
            if not text:
                continue
            segments.append(
                {
                    "start": offset_s + seg["start"],
                    "end": offset_s + _seg_end(seg),
                    "text": text,
                }
            )

    segments.sort(key=lambda s: (s["start"], s["end"]))
    return segments, detected_lang


def write_transcript(
    username: str,
    segments: list[dict],
    detected_lang: str,
    output_path: Path,
    join_offset_s: float,
) -> None:
    """Write a per-user transcript .txt file with session-relative timestamps."""
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(f"# Transcript for {username}\n")
        f.write(f"# Detected language: {detected_lang}\n")
        if join_offset_s > 0.0:
            f.write(f"# First spoke at: {format_timestamp(join_offset_s)}\n")
        f.write("\n")

        for seg in segments:
            start = format_timestamp(seg["start"])
            end = format_timestamp(seg["end"])
            f.write(f"[{start} --> {end}]  {seg['text']}\n")


def main():
    parser = argparse.ArgumentParser(
        description="Assemble session transcripts from per-user utterance snippets."
    )
    parser.add_argument(
        "session",
        help="Session folder name (e.g. 20260330_033020_test)",
    )
    parser.add_argument(
        "--model",
        default=None,
        choices=["tiny", "base", "small", "medium", "large"],
        help="Whisper model size for the batch fallback (default: from config.yaml, or 'base')",
    )
    parser.add_argument(
        "--language",
        default=None,
        help="Language code (e.g. 'en'). If omitted, uses config.yaml default or auto-detects.",
    )
    args = parser.parse_args()

    config = load_config()

    config_model = config.get("whisper_model", "base")
    config_language = config.get("whisper_language", None)
    if config_language == "auto":
        config_language = None

    model_size = args.model or config_model
    language = args.language or config_language
    min_ms = float(config.get("transcribe_min_ms", 400))

    output_dir = config.get("output_directory", "./recordings")
    session_dir = Path(output_dir) / args.session

    if not session_dir.exists():
        print(f"Error: Session directory not found: {session_dir}", file=sys.stderr)
        sys.exit(1)

    # Each participant is a sub-folder of snippet WAVs. Skip reserved
    # underscore/dot-prefixed entries (metadata, mix outputs, etc.).
    user_dirs = sorted(
        p
        for p in session_dir.iterdir()
        if p.is_dir() and not p.name.startswith(("_", "."))
    )
    if not user_dirs:
        print(
            f"Error: No participant sub-folders found in {session_dir}", file=sys.stderr
        )
        sys.exit(1)

    # Decide whether the Whisper model is needed at all: only when at least one
    # snippet lacks a sidecar and is long enough to be worth transcribing.
    needs_model = any(
        (not wav.with_suffix(".json").exists()) and wav_duration_ms(wav) >= min_ms
        for ud in user_dirs
        for wav in ud.glob("*.wav")
    )

    print(f"Session:  {args.session}")
    print(f"Path:     {session_dir}")
    print(f"Users:    {len(user_dirs)} participant(s)")
    print(f"Language: {language or 'auto-detect'}")
    print()

    model = None
    if needs_model:
        try:
            import whisper  # heavy (pulls torch) — only imported when truly needed
        except ImportError:
            print(
                "Warning: openai-whisper is not installed; snippets without a "
                "transcription sidecar will be skipped. Live transcription via "
                "py-transcribe normally produces a sidecar for every snippet.\n",
                file=sys.stderr,
            )
        else:
            print(f"Loading Whisper model '{model_size}' (snippets without sidecars)...")
            t0 = time.time()
            model = whisper.load_model(model_size)
            print(f"Model loaded in {time.time() - t0:.1f}s\n")
    else:
        print("All snippets already transcribed (sidecars present) — assembling without Whisper.\n")

    results_by_user: dict[str, list[dict]] = {}
    for user_dir in user_dirs:
        username = user_dir.name
        snippet_count = len(iter_snippets(user_dir))
        if snippet_count == 0:
            print(f"Skipping {username} (no snippets).")
            continue

        print(f"Assembling {username} ({snippet_count} snippet(s))...", end=" ", flush=True)
        t0 = time.time()
        segments, detected_lang = transcribe_user(model, user_dir, language, min_ms)
        elapsed = time.time() - t0

        results_by_user[username] = segments
        join_offset_s = segments[0]["start"] if segments else 0.0
        txt_path = session_dir / f"{username}.txt"
        write_transcript(username, segments, detected_lang, txt_path, join_offset_s)

        print(f"done ({elapsed:.1f}s, {len(segments)} segments, lang={detected_lang})")
        print(f"  -> {txt_path}")

    # Combined transcript — all users merged by timestamp. Snippet offsets are
    # authoritative, so timestamps are session-relative with no drift clamping.
    all_segments = []
    for username, segments in results_by_user.items():
        if not segments:
            continue
        join_offset_s = segments[0]["start"]

        all_segments.append(
            {
                "username": username,
                "start": join_offset_s,
                "end": join_offset_s,
                "text": "*** joined the session ***",
            }
        )
        for seg in segments:
            all_segments.append(
                {
                    "username": username,
                    "start": seg["start"],
                    "end": seg["end"],
                    "text": seg["text"],
                }
            )

    # Sort chronologically: primary key start time, secondary end time. Join
    # markers (end == start) sort before same-moment speech (end == start + N).
    all_segments.sort(key=lambda s: (s["start"], s["end"]))

    combined_path = session_dir / "_combined_transcript.txt"
    with open(combined_path, "w", encoding="utf-8") as f:
        f.write(f"# Combined Transcript — Session: {args.session}\n\n")
        for seg in all_segments:
            start = format_timestamp(seg["start"])
            end = format_timestamp(seg["end"])
            f.write(f"[{start} --> {end}]  {seg['username']}: {seg['text']}\n")

    print(f"\nCombined transcript -> {combined_path}")
    print("Done.")


if __name__ == "__main__":
    main()

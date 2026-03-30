#!/usr/bin/env python3
"""
transcribe.py — Transcribe per-user WAV recordings from a session folder using OpenAI Whisper.

Usage:
    python transcribe.py <session_name> [--model <size>] [--language <lang>]

Examples:
    python transcribe.py 20260330_033020_test
    python transcribe.py 20260330_033020_test --model medium
    python transcribe.py 20260330_033020_test --model large --language en
"""

import argparse
import os
import sys
import time
from pathlib import Path

import yaml
import whisper


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
    """Run Whisper transcription on a single WAV file."""
    options = {}
    if language:
        options["language"] = language

    result = model.transcribe(str(wav_path), **options)
    return result


def write_transcript(username: str, result: dict, output_path: Path) -> None:
    """Write a per-user transcript .txt file with timestamps."""
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(f"# Transcript for {username}\n")
        f.write(f"# Detected language: {result.get('language', 'unknown')}\n\n")

        for segment in result.get("segments", []):
            start = format_timestamp(segment["start"])
            end = format_timestamp(segment["end"])
            text = segment["text"].strip()
            f.write(f"[{start} --> {end}]  {text}\n")


def main():
    parser = argparse.ArgumentParser(
        description="Transcribe per-user WAV recordings from a session folder using Whisper."
    )
    parser.add_argument(
        "session",
        help="Session folder name (e.g. 20260330_033020_test)",
    )
    parser.add_argument(
        "--model",
        default=None,
        choices=["tiny", "base", "small", "medium", "large"],
        help="Whisper model size (default: from config.yaml, or 'base')",
    )
    parser.add_argument(
        "--language",
        default=None,
        help="Language code (e.g. 'en'). If omitted, uses config.yaml default or auto-detects.",
    )
    args = parser.parse_args()

    # Load config and resolve session path
    config = load_config()

    # Apply config defaults if CLI flags weren't provided
    config_model = config.get("whisper_model", "base")
    config_language = config.get("whisper_language", None)
    if config_language == "auto":
        config_language = None

    model_size = args.model or config_model
    language = args.language or config_language

    output_dir = config.get("output_directory", "./recordings")
    session_dir = Path(output_dir) / args.session

    if not session_dir.exists():
        print(f"Error: Session directory not found: {session_dir}", file=sys.stderr)
        sys.exit(1)

    # Find all WAV files in the session
    wav_files = sorted(session_dir.glob("*.wav"))
    if not wav_files:
        print(f"Error: No .wav files found in {session_dir}", file=sys.stderr)
        sys.exit(1)

    print(f"Session:  {args.session}")
    print(f"Path:     {session_dir}")
    print(f"Model:    {model_size}")
    print(f"Language: {language or 'auto-detect'}")
    print(f"Files:    {len(wav_files)} WAV file(s)")
    print()

    # Load Whisper model
    print(f"Loading Whisper model '{model_size}'...")
    t0 = time.time()
    model = whisper.load_model(model_size)
    print(f"Model loaded in {time.time() - t0:.1f}s\n")

    # Transcribe each user's WAV (cache results for reuse in combined transcript)
    results_by_user: dict[str, dict] = {}
    for wav_path in wav_files:
        username = wav_path.stem  # filename without extension = sanitised username
        print(f"Transcribing {username}...", end=" ", flush=True)

        t0 = time.time()
        result = transcribe_wav(model, wav_path, language)
        elapsed = time.time() - t0

        results_by_user[username] = result

        # Write per-user transcript
        txt_path = session_dir / f"{username}.txt"
        write_transcript(username, result, txt_path)

        segment_count = len(result.get("segments", []))
        detected_lang = result.get("language", "?")
        print(f"done ({elapsed:.1f}s, {segment_count} segments, lang={detected_lang})")
        print(f"  -> {txt_path}")

    # Write a combined transcript with all users merged by timestamp
    all_segments = []
    for username, result in results_by_user.items():
        for seg in result.get("segments", []):
            all_segments.append(
                {
                    "username": username,
                    "start": seg["start"],
                    "end": seg["end"],
                    "text": seg["text"].strip(),
                }
            )

    # Sort by start time for a chronological combined transcript
    all_segments.sort(key=lambda s: s["start"])

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

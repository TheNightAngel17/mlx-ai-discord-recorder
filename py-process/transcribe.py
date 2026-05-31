#!/usr/bin/env python3
"""
transcribe.py — Transcribe per-user utterance snippets from a session folder using OpenAI Whisper.

Each participant has a sub-folder of timestamped snippet WAVs:

    <session>/<username>/<offset_ms>.wav

The snippet filename is the zero-padded session-relative start offset in
milliseconds (e.g. ``0000012840.wav`` => 12.84 s into the session). Whisper
times each snippet from zero, so we add the snippet's offset to every segment to
make all timestamps session-relative. This is both more accurate than relying on
Whisper to stay aligned across long silences and far faster, because Whisper no
longer has to process hours of padded silence.

Usage:
    python transcribe.py <session_name> [--model <size>] [--language <lang>]

Examples:
    python transcribe.py 20260330_033020_test
    python transcribe.py 20260330_033020_test --model medium
    python transcribe.py 20260330_033020_test --model large --language en
"""

import argparse
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
    """Run Whisper transcription on a single snippet WAV file.

    word_timestamps=True is always enabled so that each segment's end time
    reflects the last word spoken rather than the next silence boundary.  This
    prevents Whisper's VAD padding from stretching a segment well past when the
    speaker actually stopped, which otherwise corrupts conversational ordering
    in the combined transcript.
    """
    options: dict = {"word_timestamps": True}
    if language:
        options["language"] = language

    result = model.transcribe(str(wav_path), **options)
    return result


def _seg_end(seg: dict) -> float:
    """Return the tightest available end timestamp for a Whisper segment.

    When word_timestamps=True the segment carries a 'words' list; the last
    word's end time is used because it marks when speech actually stopped.
    Falls back to seg['end'] (the VAD-padded boundary) when words are absent.
    """
    words = seg.get("words")
    if words:
        return words[-1]["end"]
    return seg["end"]


def parse_offset_seconds(wav_path: Path) -> float:
    """Session-relative start offset (seconds) encoded in the snippet filename.

    Snippet files are named with their zero-padded start offset in
    milliseconds (e.g. ``0000012840.wav`` => 12.84 s).  Returns 0.0 when the
    stem isn't numeric so a stray file never aborts the run.
    """
    try:
        return int(wav_path.stem) / 1000.0
    except ValueError:
        print(
            f"Warning: snippet {wav_path.name} has a non-numeric name; using offset 0.",
            file=sys.stderr,
        )
        return 0.0


def transcribe_user(
    model: whisper.Whisper, user_dir: Path, language: str | None
) -> tuple[list[dict], str]:
    """Transcribe every snippet in a participant's sub-folder.

    Returns ``(segments, detected_language)`` where each segment is a normalised
    dict ``{start, end, text}`` whose timestamps are session-relative (the
    snippet's filename offset has already been added).  Snippets are processed in
    chronological (filename) order.
    """
    segments: list[dict] = []
    detected_lang = "unknown"

    for wav_path in sorted(user_dir.glob("*.wav")):
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

    # Snippets are already ordered, but a segment's end can run past the next
    # snippet's start, so sort by (start, end) to keep ordering deterministic.
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
        description="Transcribe per-user utterance snippets from a session folder using Whisper."
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

    # Each participant is a sub-folder of timestamped snippet WAVs. Skip the
    # reserved underscore/dot-prefixed entries (metadata, mix outputs, etc.).
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

    print(f"Session:  {args.session}")
    print(f"Path:     {session_dir}")
    print(f"Model:    {model_size}")
    print(f"Language: {language or 'auto-detect'}")
    print(f"Users:    {len(user_dirs)} participant(s)")
    print()

    # Load Whisper model once and reuse it across every snippet.
    print(f"Loading Whisper model '{model_size}'...")
    t0 = time.time()
    model = whisper.load_model(model_size)
    print(f"Model loaded in {time.time() - t0:.1f}s\n")

    # Transcribe each participant's snippets (cache results for the combined file)
    results_by_user: dict[str, list[dict]] = {}
    lang_by_user: dict[str, str] = {}
    for user_dir in user_dirs:
        username = user_dir.name  # sub-folder name = sanitised username
        snippet_count = len(list(user_dir.glob("*.wav")))
        if snippet_count == 0:
            print(f"Skipping {username} (no snippets).")
            continue

        print(f"Transcribing {username} ({snippet_count} snippet(s))...", end=" ", flush=True)
        t0 = time.time()
        segments, detected_lang = transcribe_user(model, user_dir, language)
        elapsed = time.time() - t0

        results_by_user[username] = segments
        lang_by_user[username] = detected_lang

        join_offset_s = segments[0]["start"] if segments else 0.0
        txt_path = session_dir / f"{username}.txt"
        write_transcript(username, segments, detected_lang, txt_path, join_offset_s)

        print(f"done ({elapsed:.1f}s, {len(segments)} segments, lang={detected_lang})")
        print(f"  -> {txt_path}")

    # Write a combined transcript with all users merged by timestamp. Each
    # snippet's offset has already been applied, so timestamps are session-
    # relative and require no drift clamping — the offset is authoritative.
    all_segments = []
    for username, segments in results_by_user.items():
        if not segments:
            continue
        join_offset_s = segments[0]["start"]

        # Insert a marker recording when each participant first spoke.
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

    # Sort chronologically: primary key is start time, secondary key is end time.
    # Using start as the primary key preserves conversational order — whoever
    # began speaking first appears first, even if their segment overlaps and
    # ends later than a following segment.
    # Join markers have end == start, so their sort key is (t, t); any speech
    # starting at the same moment has (t, t+N) which sorts after, keeping the
    # join marker pinned before the user's first line.
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

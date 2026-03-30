#!/usr/bin/env python3
"""
merge_audio.py — Mix and compress per-user WAV recordings from a session folder.

Overlays all per-user WAV files into a single combined session recording, then
exports compressed MP3s for both the combined mix and each individual user.

Usage:
    python merge_audio.py <session_name>

Examples:
    python merge_audio.py 20260330_033020_test
"""

import argparse
import sys
from pathlib import Path

import yaml
from pydub import AudioSegment


def load_config() -> dict:
    """Load config.yaml from the repo root (one level up from py-process/)."""
    config_path = Path(__file__).resolve().parent.parent / "config.yaml"
    if not config_path.exists():
        print(f"Error: config.yaml not found at {config_path}", file=sys.stderr)
        sys.exit(1)
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_wav(wav_path: Path) -> AudioSegment:
    """Load a WAV file as an AudioSegment."""
    return AudioSegment.from_wav(str(wav_path))


def mix_segments(segments: list[AudioSegment]) -> AudioSegment:
    """Overlay all audio segments on top of each other (time zero = start of recording)."""
    if not segments:
        raise ValueError("No audio segments to mix.")
    mixed = segments[0]
    for seg in segments[1:]:
        mixed = mixed.overlay(seg)
    return mixed


def export_mp3(segment: AudioSegment, output_path: Path, bitrate: str) -> None:
    """Export an AudioSegment as an MP3 file at the given bitrate."""
    segment.export(str(output_path), format="mp3", bitrate=bitrate)


def main():
    parser = argparse.ArgumentParser(
        description="Mix per-user WAV recordings and export compressed MP3s."
    )
    parser.add_argument(
        "session",
        help="Session folder name (e.g. 20260330_033020_test)",
    )
    args = parser.parse_args()

    # Load config and resolve session path
    config = load_config()

    output_dir = config.get("output_directory", "./recordings")
    session_dir = Path(output_dir) / args.session

    if not session_dir.exists():
        print(f"Error: Session directory not found: {session_dir}", file=sys.stderr)
        sys.exit(1)

    # Find all per-user WAV files (exclude any previously generated mix files)
    wav_files = sorted(
        f for f in session_dir.glob("*.wav") if not f.stem.startswith("_")
    )
    if not wav_files:
        print(f"Error: No .wav files found in {session_dir}", file=sys.stderr)
        sys.exit(1)

    mp3_bitrate = config.get("mp3_bitrate", "128k")
    keep_wav = config.get("keep_wav", True)

    print(f"Session:  {args.session}")
    print(f"Path:     {session_dir}")
    print(f"Bitrate:  {mp3_bitrate}")
    print(f"Keep WAV: {keep_wav}")
    print(f"Files:    {len(wav_files)} WAV file(s)")
    print()

    # Load and compress each per-user WAV → MP3
    segments: list[AudioSegment] = []
    for wav_path in wav_files:
        username = wav_path.stem
        print(f"Loading {username}.wav...", end=" ", flush=True)
        seg = load_wav(wav_path)
        segments.append(seg)
        print(f"done ({len(seg) / 1000:.1f}s)")

        mp3_path = session_dir / f"{username}.mp3"
        print(f"  Compressing -> {mp3_path.name}...", end=" ", flush=True)
        export_mp3(seg, mp3_path, mp3_bitrate)
        print("done")

        if not keep_wav:
            wav_path.unlink()
            print(f"  Deleted {wav_path.name}")

    print()

    # Mix all user segments together (all start at time zero)
    print(f"Mixing {len(segments)} track(s)...", end=" ", flush=True)
    mix = mix_segments(segments)
    print(f"done ({len(mix) / 1000:.1f}s)")

    # Export combined WAV
    mix_wav_path = session_dir / "_session_mix.wav"
    print(f"Exporting combined WAV -> {mix_wav_path.name}...", end=" ", flush=True)
    mix.export(str(mix_wav_path), format="wav")
    print("done")

    # Export combined MP3
    mix_mp3_path = session_dir / "_session_mix.mp3"
    print(f"Exporting combined MP3 -> {mix_mp3_path.name}...", end=" ", flush=True)
    export_mp3(mix, mix_mp3_path, mp3_bitrate)
    print("done")

    print("\nDone.")


if __name__ == "__main__":
    main()

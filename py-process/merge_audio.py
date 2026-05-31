#!/usr/bin/env python3
"""
merge_audio.py — Reconstruct a session mix from per-user utterance snippets and export it.

Each participant is a sub-folder of timestamped snippet WAVs:

    <session>/<username>/<offset_ms>.wav

The filename is the snippet's session-relative start offset in milliseconds.
This script rebuilds the session timeline by placing every snippet at its offset:
for each user it copies the snippet PCM into a full-length silence buffer (a
user's own utterances never overlap), then overlays the few per-user tracks into
a single combined recording exported as both WAV and MP3.

Honours the ``keep_wav`` config flag: when false, snippet WAVs (and their now-
empty sub-folders) are deleted after the mix is exported.

Usage:
    python merge_audio.py <session_name>

Examples:
    python merge_audio.py 20260330_033020_test
"""

import argparse
import math
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


def parse_offset_ms(wav_path: Path) -> int:
    """Session-relative start offset (ms) encoded in the snippet filename.

    Returns 0 when the stem isn't numeric so a stray file never aborts the run.
    """
    try:
        return int(wav_path.stem)
    except ValueError:
        print(
            f"Warning: snippet {wav_path.name} has a non-numeric name; placing at 0.",
            file=sys.stderr,
        )
        return 0


def mix_segments(segments: list[AudioSegment]) -> AudioSegment:
    """Overlay all per-user tracks on top of each other (all start at time zero)."""
    if not segments:
        raise ValueError("No audio segments to mix.")
    mixed = segments[0]
    for seg in segments[1:]:
        mixed = mixed.overlay(seg)
    return mixed


def build_user_track(
    clips: list[tuple[AudioSegment, int]],
    total_ms: int,
    frame_rate: int,
    sample_width: int,
    channels: int,
) -> AudioSegment:
    """Place one participant's snippets onto a full-length silent track.

    A user's own utterances are captured one at a time and separated by silence,
    so they never overlap — each snippet's PCM is copied straight into the
    buffer at its byte offset (no mixing needed within a user).
    """
    frame_width = sample_width * channels
    total_bytes = int(math.ceil(total_ms / 1000.0 * frame_rate)) * frame_width
    buf = bytearray(total_bytes)  # zero-filled == silence

    for seg, offset_ms in clips:
        if (seg.frame_rate, seg.sample_width, seg.channels) != (
            frame_rate,
            sample_width,
            channels,
        ):
            seg = (
                seg.set_frame_rate(frame_rate)
                .set_sample_width(sample_width)
                .set_channels(channels)
            )
        raw = seg.raw_data
        start = int(round(offset_ms / 1000.0 * frame_rate)) * frame_width
        end = min(start + len(raw), total_bytes)
        buf[start:end] = raw[: end - start]

    return AudioSegment(
        data=bytes(buf),
        sample_width=sample_width,
        frame_rate=frame_rate,
        channels=channels,
    )


def export_mp3(segment: AudioSegment, output_path: Path, bitrate: str) -> None:
    """Export an AudioSegment as an MP3 file at the given bitrate."""
    segment.export(str(output_path), format="mp3", bitrate=bitrate)


def main():
    parser = argparse.ArgumentParser(
        description="Reconstruct a session mix from per-user snippets and export WAV + MP3."
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

    # Each participant is a sub-folder of snippet WAVs. Skip reserved
    # underscore/dot-prefixed entries (metadata, previously generated mixes).
    user_dirs = sorted(
        p
        for p in session_dir.iterdir()
        if p.is_dir() and not p.name.startswith(("_", "."))
    )

    # Gather snippets per user (path + offset)
    users: list[tuple[str, list[tuple[Path, int]]]] = []
    total_snippets = 0
    for user_dir in user_dirs:
        snips = [(w, parse_offset_ms(w)) for w in sorted(user_dir.glob("*.wav"))]
        if snips:
            users.append((user_dir.name, snips))
            total_snippets += len(snips)

    if not users:
        print(f"Error: No snippet .wav files found in {session_dir}", file=sys.stderr)
        sys.exit(1)

    mp3_bitrate = config.get("mp3_bitrate", "128k")
    keep_wav = config.get("keep_wav", True)

    print(f"Session:  {args.session}")
    print(f"Path:     {session_dir}")
    print(f"Bitrate:  {mp3_bitrate}")
    print(f"Keep WAV: {keep_wav}")
    print(f"Users:    {len(users)} participant(s), {total_snippets} snippet(s)")
    print()

    # Load every snippet, learn the audio format, and find the session length.
    loaded: list[tuple[str, list[tuple[AudioSegment, int]]]] = []
    total_ms = 0
    frame_rate = sample_width = channels = None
    for username, snips in users:
        clips: list[tuple[AudioSegment, int]] = []
        for wav_path, offset_ms in snips:
            seg = AudioSegment.from_wav(str(wav_path))
            clips.append((seg, offset_ms))
            total_ms = max(total_ms, offset_ms + len(seg))
            if frame_rate is None:
                frame_rate, sample_width, channels = (
                    seg.frame_rate,
                    seg.sample_width,
                    seg.channels,
                )
        loaded.append((username, clips))
        print(f"Loaded {username}: {len(clips)} snippet(s)")

    print()

    # Reconstruct one full-length track per user, then overlay them.
    print(f"Reconstructing {len(loaded)} track(s) over {total_ms / 1000:.1f}s...", end=" ", flush=True)
    user_tracks = [
        build_user_track(clips, total_ms, frame_rate, sample_width, channels)
        for _username, clips in loaded
    ]
    mix = mix_segments(user_tracks)
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

    # Honour keep_wav: clean up snippet WAVs (and empty sub-folders) when off.
    if not keep_wav:
        removed = 0
        for username, snips in users:
            for wav_path, _offset in snips:
                try:
                    wav_path.unlink()
                    removed += 1
                except OSError as exc:
                    print(f"  Could not delete {wav_path}: {exc}", file=sys.stderr)
            try:
                (session_dir / username).rmdir()
            except OSError:
                pass  # non-empty or already gone
        print(f"\nDeleted {removed} snippet WAV(s) (keep_wav: false).")

    print("\nDone.")


if __name__ == "__main__":
    main()

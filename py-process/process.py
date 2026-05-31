#!/usr/bin/env python3
"""
process.py — Post-processing orchestrator for recorded Discord sessions.

This is the main entry point called by the JS bot after a recording ends.
It coordinates all post-processing steps in order:
    1. Transcription (via transcribe.py)
    2. Merge audio (via merge_audio.py)
    3. Vectorize transcript (via vectorize.py)
    4. Summarize session (via summarize.py) — only when --summarize is passed

Usage:
    python process.py <session_name> [--model <size>] [--language <lang>] [--no-summarize]

Examples:
    python process.py 20260330_033020_test
    python process.py 20260330_033020_test --model medium --language en
    python process.py 20260330_033020_test --no-summarize
"""

import argparse
import subprocess
import sys
from pathlib import Path

import requests
import yaml


def load_config() -> dict:
    """Load config.yaml from the repo root (one level up from py-process/)."""
    config_path = Path(__file__).resolve().parent.parent / "config.yaml"
    if not config_path.exists():
        print(f"Error: config.yaml not found at {config_path}", file=sys.stderr)
        sys.exit(1)
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def finalize_live_transcription(session: str, config: dict) -> None:
    """Drain the live transcription service's queue for this session.

    When `auto_transcribe` is on, snippets were transcribed during the session
    by py-transcribe; this waits until that service has written every sidecar so
    the transcribe step can assemble instantly. If the service is unreachable we
    log and continue — transcribe.py then batch-transcribes any missing snippets.
    """
    if not config.get("auto_transcribe"):
        return

    port = config.get("transcribe_api_port", 8200)
    url = f"http://localhost:{port}/api/session/finalize"
    try:
        resp = requests.post(url, json={"session": session}, timeout=900)
        resp.raise_for_status()
        data = resp.json()
        if data.get("drained"):
            print(f"Live transcription drained for session '{session}'.")
        else:
            print(
                f"Warning: live transcription did not fully drain "
                f"({data.get('remaining')} snippet(s) left); transcribe.py will fill gaps.",
                file=sys.stderr,
            )
    except requests.exceptions.RequestException as exc:
        print(
            f"Warning: could not reach the transcription service ({exc}). "
            "transcribe.py will transcribe snippets in batch instead.",
            file=sys.stderr,
        )


def run_step(label: str, command: list[str]) -> None:
    """Run a post-processing step as a subprocess. Exit on failure."""
    print(f"\n{'=' * 60}")
    print(f"  Step: {label}")
    print(f"{'=' * 60}\n")

    result = subprocess.run(command)

    if result.returncode != 0:
        print(f"\nError: '{label}' failed with exit code {result.returncode}", file=sys.stderr)
        sys.exit(result.returncode)


def main():
    parser = argparse.ArgumentParser(
        description="Post-processing orchestrator for recorded Discord sessions."
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
    parser.add_argument(
        "--summarize",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Generate a session summary after vectorization (default: on). "
             "Pass --no-summarize to skip.",
    )
    args = parser.parse_args()

    config = load_config()

    # Resolve paths to sibling scripts (same directory as this script)
    script_dir = Path(__file__).resolve().parent
    transcribe_script = script_dir / "transcribe.py"
    merge_audio_script = script_dir / "merge_audio.py"
    vectorize_script = script_dir / "vectorize.py"
    summarize_script = script_dir / "summarize.py"

    # -------------------------------------------------------------------
    # Step 1: Transcription
    # -------------------------------------------------------------------
    # If snippets were transcribed live, wait for that queue to drain so every
    # sidecar exists before transcribe.py assembles the combined transcript.
    finalize_live_transcription(args.session, config)

    transcribe_cmd = [sys.executable, str(transcribe_script), args.session]
    if args.model:
        transcribe_cmd += ["--model", args.model]
    if args.language:
        transcribe_cmd += ["--language", args.language]

    run_step("Transcribe recordings", transcribe_cmd)

    # -------------------------------------------------------------------
    # Step 2: Merge audio (mix per-user WAVs → _session_mix.wav + MP3)
    # -------------------------------------------------------------------
    merge_cmd = [sys.executable, str(merge_audio_script), args.session]
    run_step("Merge audio tracks", merge_cmd)

    # -------------------------------------------------------------------
    # Step 3: Vectorize transcript (chunk, embed, store in ChromaDB)
    # -------------------------------------------------------------------
    vectorize_cmd = [sys.executable, str(vectorize_script), args.session]
    run_step("Vectorize transcript", vectorize_cmd)

    # -------------------------------------------------------------------
    # Step 4: Summarize session (optional — controlled by --summarize flag)
    # -------------------------------------------------------------------
    if args.summarize:
        summarize_cmd = [sys.executable, str(summarize_script), args.session]
        run_step("Generate session summary", summarize_cmd)
    else:
        print(f"\n{'=' * 60}")
        print("  Step: Generate session summary — SKIPPED (--no-summarize)")
        print(f"{'=' * 60}\n")

    print(f"\n{'=' * 60}")
    print("  Post-processing complete.")
    print(f"{'=' * 60}\n")


if __name__ == "__main__":
    main()

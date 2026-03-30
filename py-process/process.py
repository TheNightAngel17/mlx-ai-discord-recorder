#!/usr/bin/env python3
"""
process.py — Post-processing orchestrator for recorded Discord sessions.

This is the main entry point called by the JS bot after a recording ends.
It coordinates all post-processing steps in order:
    1. Transcription (via transcribe.py)
    (future steps will be added here)

Usage:
    python process.py <session_name> [--model <size>] [--language <lang>]

Examples:
    python process.py 20260330_033020_test
    python process.py 20260330_033020_test --model medium --language en
"""

import argparse
import subprocess
import sys
from pathlib import Path


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
    args = parser.parse_args()

    # Resolve the path to transcribe.py (same directory as this script)
    script_dir = Path(__file__).resolve().parent
    transcribe_script = script_dir / "transcribe.py"

    # -------------------------------------------------------------------
    # Step 1: Transcription
    # -------------------------------------------------------------------
    transcribe_cmd = [sys.executable, str(transcribe_script), args.session]
    if args.model:
        transcribe_cmd += ["--model", args.model]
    if args.language:
        transcribe_cmd += ["--language", args.language]

    run_step("Transcribe recordings", transcribe_cmd)

    # -------------------------------------------------------------------
    # Future steps go here, e.g.:
    #   run_step("Diarization", [...])
    #   run_step("Summarisation", [...])
    # -------------------------------------------------------------------

    print(f"\n{'=' * 60}")
    print("  Post-processing complete.")
    print(f"{'=' * 60}\n")


if __name__ == "__main__":
    main()

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
    python process.py <session_name> [--model <size>] [--language <lang>]
                       [--no-summarize] [--retranscribe] [--force-vectorize]

Examples:
    python process.py 20260330_033020_test
    python process.py 20260330_033020_test --model medium --language en
    python process.py 20260330_033020_test --no-summarize
    # Re-process an already-processed session, re-transcribing from audio and
    # re-indexing the (now-changed) transcript:
    python process.py 20260330_033020_test --retranscribe --force-vectorize
"""

import argparse
import subprocess
import sys
import wave
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


def transcribe_base_url(config: dict) -> str:
    """Base URL of the live transcription service (py-transcribe)."""
    base = config.get("transcribe_api_url") or f"http://localhost:{config.get('transcribe_api_port', 8200)}"
    return base.rstrip("/")


def iter_snippet_wavs(session_dir: Path):
    """Yield (user_dir, wav_path) for every per-user snippet WAV in a session.

    Mirrors the layout the recorder writes: ``<session>/<username>/<offset_ms>.wav``.
    Reserved underscore/dot-prefixed entries (metadata, mix outputs) are skipped.
    """
    for user_dir in sorted(session_dir.iterdir()):
        if not user_dir.is_dir() or user_dir.name.startswith(("_", ".")):
            continue
        for wav in sorted(user_dir.glob("*.wav")):
            yield user_dir, wav


def wav_duration_ms(wav_path: Path) -> float:
    """Duration of a WAV file in milliseconds (0.0 if it can't be read)."""
    try:
        with wave.open(str(wav_path), "rb") as w:
            rate = w.getframerate()
            frames = w.getnframes()
        return (frames / float(rate)) * 1000.0 if rate else 0.0
    except (wave.Error, OSError, EOFError):
        return 0.0


def reset_transcription(session: str, config: dict) -> None:
    """Force a fresh transcription pass for an already-processed session.

    Transcription is sidecar-driven: ``transcribe.py`` reuses any existing
    ``<offset_ms>.json`` sidecar instead of re-running the model. To genuinely
    re-transcribe we therefore (1) delete every sidecar, then (2) re-enqueue each
    snippet to the warm-model py-transcribe service — the component that actually
    owns the Whisper model (the lean py-process image has none). The subsequent
    ``finalize_live_transcription`` step waits for that queue to drain.

    If py-transcribe is unreachable (or auto_transcribe is off), the sidecars are
    still gone, so transcribe.py's local-Whisper batch fallback re-transcribes the
    snippets instead — the bare-metal path.
    """
    output_dir = config.get("output_directory", "./recordings")
    session_dir = Path(output_dir) / session
    if not session_dir.exists():
        print(f"Error: Session directory not found: {session_dir}", file=sys.stderr)
        sys.exit(1)

    snippets = list(iter_snippet_wavs(session_dir))

    # 1) Drop existing sidecars so nothing stale is reused.
    removed = 0
    for _user_dir, wav in snippets:
        sidecar = wav.with_suffix(".json")
        if sidecar.exists():
            try:
                sidecar.unlink()
                removed += 1
            except OSError as exc:
                print(f"Warning: could not delete sidecar {sidecar}: {exc}", file=sys.stderr)
    print(f"Re-transcribe: cleared {removed} sidecar(s) across {len(snippets)} snippet(s).")

    # 2) Re-enqueue to the live transcription service (the model owner).
    if not config.get("auto_transcribe"):
        print(
            "Re-transcribe: auto_transcribe is off — skipping service re-enqueue; "
            "transcribe.py will batch-transcribe with local Whisper."
        )
        return

    url = f"{transcribe_base_url(config)}/api/transcribe"
    min_ms = float(config.get("transcribe_min_ms", 400))
    language = config.get("whisper_language") or None
    if language == "auto":
        language = None

    enqueued = 0
    for user_dir, wav in snippets:
        # Skip sub-threshold blips, matching the recorder and the batch fallback.
        if wav_duration_ms(wav) < min_ms:
            continue
        try:
            offset_ms = int(wav.stem)
        except ValueError:
            print(f"Warning: snippet {wav.name} has a non-numeric name; skipping.", file=sys.stderr)
            continue
        try:
            resp = requests.post(
                url,
                json={
                    "session": session,
                    "username": user_dir.name,
                    "offset_ms": offset_ms,
                    "wav_path": str(wav.resolve()),
                    "language": language,
                },
                timeout=10,
            )
            resp.raise_for_status()
            enqueued += 1
        except requests.exceptions.RequestException as exc:
            print(
                f"Warning: could not enqueue {user_dir.name}/{wav.name} for re-transcription "
                f"({exc}); transcribe.py will batch-transcribe it if Whisper is installed.",
                file=sys.stderr,
            )
    print(f"Re-transcribe: re-enqueued {enqueued} snippet(s) to {url}.")


def finalize_live_transcription(session: str, config: dict, timeout_s: float = 600.0) -> None:
    """Drain the live transcription service's queue for this session.

    When `auto_transcribe` is on, snippets were transcribed during the session
    by py-transcribe; this waits until that service has written every sidecar so
    the transcribe step can assemble instantly. If the service is unreachable we
    log and continue — transcribe.py then batch-transcribes any missing snippets.

    `timeout_s` bounds how long to wait for the drain. The default suits a normal
    session (most snippets are already done at stop time); a re-transcribe pass
    enqueues every snippet at once, so it passes a larger budget.
    """
    if not config.get("auto_transcribe"):
        return

    url = f"{transcribe_base_url(config)}/api/session/finalize"
    try:
        resp = requests.post(
            url, json={"session": session, "timeout_s": timeout_s}, timeout=timeout_s + 60
        )
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


def run_step(label: str, command: list[str] | None = None, func=None) -> None:
    """Run one post-processing step and announce it. Exit on failure.

    Pass `command` to run a sibling script as a subprocess, or `func` to run an
    in-process callable (used by the re-transcribe step, which only orchestrates
    the py-transcribe service). The "Step:" banner is what the py-process job
    runner scrapes to report live progress to Discord.
    """
    print(f"\n{'=' * 60}")
    print(f"  Step: {label}")
    print(f"{'=' * 60}\n")

    if func is not None:
        func()
        return

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
    parser.add_argument(
        "--retranscribe",
        action="store_true",
        help="Re-transcribe from audio: clear existing per-snippet sidecars and "
             "regenerate them (via py-transcribe, or local Whisper) before assembling. "
             "Use when re-processing a session whose transcript you want rebuilt.",
    )
    parser.add_argument(
        "--force-vectorize",
        action="store_true",
        help="Pass --force to vectorize.py so an already-indexed session is "
             "re-indexed. Required when re-processing, since vectorize.py otherwise "
             "skips sessions already present in the vector DB.",
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
    # Step 0 (optional): Re-transcribe — clear sidecars and re-enqueue audio.
    # -------------------------------------------------------------------
    if args.retranscribe:
        run_step(
            "Re-transcribe (reset sidecars)",
            func=lambda: reset_transcription(args.session, config),
        )

    # -------------------------------------------------------------------
    # Step 1: Transcription
    # -------------------------------------------------------------------
    # If snippets were transcribed live, wait for that queue to drain so every
    # sidecar exists before transcribe.py assembles the combined transcript. A
    # re-transcribe pass enqueues every snippet at once, so allow much longer.
    finalize_live_transcription(
        args.session, config, timeout_s=3600.0 if args.retranscribe else 600.0
    )

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
    # vectorize.py skips sessions already in the vector DB; --force re-indexes
    # them, which is required when re-processing an existing session.
    vectorize_cmd = [sys.executable, str(vectorize_script), args.session]
    if args.force_vectorize:
        vectorize_cmd += ["--force"]
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

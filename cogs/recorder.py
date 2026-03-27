"""
cogs/recorder.py — All /mlx-ai recording logic for the MLX AI Discord Recorder bot.

Commands:
    /mlx-ai record start  — Join a voice channel and begin recording mixed audio
    /mlx-ai record stop   — Stop recording, save mixed.wav, run WhisperX transcription
    /mlx-ai record status — Show the current recording session info
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import struct
import wave
from datetime import datetime, timezone
from typing import Any

import discord
from discord.ext import commands

logger = logging.getLogger(__name__)

# Audio format constants matching Discord's decoded PCM output
SAMPLE_RATE = 48000
CHANNELS = 2
SAMPLE_WIDTH = 2  # bytes per sample (16-bit signed PCM)


class MixedAudioSink(discord.sinks.Sink):
    """Captures audio from all users and mixes into a single PCM buffer.

    Overrides ``write`` so that all received PCM frames are accumulated
    per-user and then summed (with int16 clipping) when the recording ends.
    """

    def __init__(self) -> None:
        super().__init__()
        self._user_buffers: dict[int, bytearray] = {}

    # ------------------------------------------------------------------ #
    # Sink interface                                                        #
    # ------------------------------------------------------------------ #

    def write(self, data: bytes, user: int) -> None:
        if user not in self._user_buffers:
            self._user_buffers[user] = bytearray()
        self._user_buffers[user].extend(data)

    def format_audio(self, audio: Any) -> None:
        # We don't use the per-user AudioData objects from the base class.
        pass

    def cleanup(self) -> None:
        self._user_buffers.clear()

    # ------------------------------------------------------------------ #
    # Mixing helper                                                         #
    # ------------------------------------------------------------------ #

    def get_mixed_pcm(self) -> bytes:
        """Return all captured audio streams mixed into a single PCM buffer."""
        if not self._user_buffers:
            return b""

        max_len = max(len(d) for d in self._user_buffers.values())
        # Ensure even byte length (each 16-bit sample occupies 2 bytes).
        if max_len % 2:
            max_len += 1

        streams: list[bytes] = []
        for data in self._user_buffers.values():
            if len(data) < max_len:
                padded = bytearray(data)
                padded.extend(b"\x00" * (max_len - len(data)))
                streams.append(bytes(padded))
            else:
                streams.append(bytes(data))

        sample_count = max_len // SAMPLE_WIDTH
        mixed = bytearray(max_len)
        for i in range(sample_count):
            offset = i * SAMPLE_WIDTH
            total = 0
            for stream in streams:
                if offset + SAMPLE_WIDTH <= len(stream):
                    total += struct.unpack_from("<h", stream, offset)[0]
            total = max(-32768, min(32767, total))
            struct.pack_into("<h", mixed, offset, total)

        return bytes(mixed)


# --------------------------------------------------------------------------- #
# WAV writing                                                                  #
# --------------------------------------------------------------------------- #

def _save_mixed_wav(pcm_data: bytes, filepath: str) -> None:
    """Write raw stereo 48 kHz 16-bit PCM data to a WAV file."""
    with wave.open(filepath, "wb") as wf:
        wf.setnchannels(CHANNELS)
        wf.setsampwidth(SAMPLE_WIDTH)
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(pcm_data)


# --------------------------------------------------------------------------- #
# WhisperX transcription (runs in a thread pool to stay non-blocking)          #
# --------------------------------------------------------------------------- #

def _fmt_time(seconds: float) -> str:
    """Format a duration in seconds as ``HH:MM:SS``."""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def _transcribe_blocking(
    audio_path: str,
    session_dir: str,
    participants: list[dict],
    config: dict,
) -> None:
    """Run WhisperX transcription + diarization synchronously.

    This is intended to be called via ``asyncio.get_event_loop().run_in_executor``
    so that it does not block the event loop.
    """
    import whisperx  # imported here so the bot starts even if whisperx is missing

    hf_token: str = config.get("huggingface_token") or os.environ.get("HF_TOKEN", "")
    model_size: str = config.get("whisper_model", "base")

    try:
        import torch
        device = "cuda" if torch.cuda.is_available() else "cpu"
    except ImportError:
        device = "cpu"

    compute_type = "float16" if device == "cuda" else "int8"

    logger.info("Loading WhisperX model '%s' on device '%s'", model_size, device)
    model = whisperx.load_model(model_size, device, compute_type=compute_type)

    logger.info("Transcribing %s", audio_path)
    audio = whisperx.load_audio(audio_path)
    result = model.transcribe(audio, batch_size=16)

    # Word-level alignment
    try:
        align_model, metadata = whisperx.load_align_model(
            language_code=result["language"], device=device
        )
        result = whisperx.align(
            result["segments"], align_model, metadata, audio, device,
            return_char_alignments=False,
        )
    except Exception as exc:
        logger.warning("Alignment failed (continuing without it): %s", exc)

    # Speaker diarization
    if hf_token:
        try:
            diarize_model = whisperx.DiarizationPipeline(
                use_auth_token=hf_token, device=device
            )
            diarize_segments = diarize_model(audio)
            result = whisperx.assign_word_speakers(diarize_segments, result)
        except Exception as exc:
            logger.warning("Diarization failed (continuing without it): %s", exc)
    else:
        logger.warning("No HuggingFace token — skipping speaker diarization")

    # Save transcript.json
    transcript_json_path = os.path.join(session_dir, "transcript.json")
    with open(transcript_json_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    logger.info("Saved transcript.json to %s", transcript_json_path)

    # Build speaker map: SPEAKER_00, SPEAKER_01, … → display names in order of
    # first appearance in the transcript.
    speakers_seen: list[str] = []
    for seg in result.get("segments", []):
        spk = seg.get("speaker", "")
        if spk and spk not in speakers_seen:
            speakers_seen.append(spk)

    speaker_map: dict[str, str] = {}
    for i, spk in enumerate(speakers_seen):
        if i < len(participants):
            speaker_map[spk] = participants[i]["display_name"]
        else:
            speaker_map[spk] = spk

    speaker_map_path = os.path.join(session_dir, "speaker_map.json")
    with open(speaker_map_path, "w", encoding="utf-8") as f:
        json.dump(speaker_map, f, ensure_ascii=False, indent=2)
    logger.info("Saved speaker_map.json to %s", speaker_map_path)

    # Save transcript.txt
    transcript_txt_path = os.path.join(session_dir, "transcript.txt")
    with open(transcript_txt_path, "w", encoding="utf-8") as f:
        f.write("Speaker Map:\n")
        for spk, name in speaker_map.items():
            f.write(f"  {spk} -> {name}\n")
        f.write("\nTranscript:\n")
        for seg in result.get("segments", []):
            spk = seg.get("speaker", "UNKNOWN")
            name = speaker_map.get(spk, spk)
            start = _fmt_time(seg.get("start", 0.0))
            end = _fmt_time(seg.get("end", 0.0))
            text = seg.get("text", "").strip()
            f.write(f'[{spk} / {name}] {start} -> {end}: "{text}"\n')
    logger.info("Saved transcript.txt to %s", transcript_txt_path)


async def _run_whisperx(
    audio_path: str,
    session_dir: str,
    participants: list[dict],
    config: dict,
    announce_ch: discord.TextChannel | None,
) -> None:
    """Dispatch WhisperX transcription to a thread pool and send announcements."""
    loop = asyncio.get_event_loop()
    try:
        await loop.run_in_executor(
            None,
            _transcribe_blocking,
            audio_path,
            session_dir,
            participants,
            config,
        )
        if announce_ch:
            await announce_ch.send(
                f"Transcription complete — files saved to `{session_dir}`"
            )
    except Exception as exc:
        logger.exception("WhisperX transcription failed: %s", exc)
        if announce_ch:
            await announce_ch.send(f"Transcription failed: {exc}")


# --------------------------------------------------------------------------- #
# Cog                                                                          #
# --------------------------------------------------------------------------- #

class RecorderCog(commands.Cog):
    """Cog that provides the /mlx-ai record command group and all recording logic."""

    mlx_ai = discord.SlashCommandGroup(
        "mlx-ai",
        "MLX AI Discord Recorder commands",
    )
    record = mlx_ai.create_subgroup("record", "Voice recording commands")

    def __init__(self, bot: commands.Bot, config: dict) -> None:
        self.bot = bot
        self.config = config
        self.mlx_ai.guild_ids = [bot.guild_id]

        self.is_recording: bool = False
        self.is_transcribing: bool = False
        self.voice_client: discord.VoiceClient | None = None
        self.recording_channel: discord.VoiceChannel | None = None
        self.session_name: str | None = None
        self.session_dir: str | None = None
        self.start_time: datetime | None = None
        self.participants: list[dict] = []

        self._stop_guild: discord.Guild | None = None
        self._stop_auto: bool = False

        super().__init__()

    # ------------------------------------------------------------------ #
    # Helpers                                                               #
    # ------------------------------------------------------------------ #

    async def _get_announce_channel(
        self, guild: discord.Guild
    ) -> discord.TextChannel | None:
        channel_name: str = self.config.get("announce_channel", "bot-commands")
        return discord.utils.get(guild.text_channels, name=channel_name)

    async def _stop_recording(
        self, guild: discord.Guild, *, auto: bool = False
    ) -> None:
        if not self.is_recording:
            return

        self.is_recording = False
        self._stop_guild = guild
        self._stop_auto = auto

        if self.voice_client and self.voice_client.is_connected():
            self.voice_client.stop_recording()

    async def _recording_finished(
        self, sink: MixedAudioSink, session_dir: str
    ) -> None:
        """Callback invoked by py-cord when stop_recording() completes."""
        pcm_data = sink.get_mixed_pcm()
        wav_path = os.path.join(session_dir, "mixed.wav")

        if pcm_data:
            _save_mixed_wav(pcm_data, wav_path)
            logger.info("Saved mixed.wav (%d bytes) to %s", len(pcm_data), wav_path)
        else:
            logger.warning("No audio captured — mixed.wav was not saved")

        if self.voice_client and self.voice_client.is_connected():
            await self.voice_client.disconnect()

        guild = self._stop_guild
        session_name = self.session_name
        output_dir = self.config.get("output_directory", "./recordings")
        rel_path = os.path.join(output_dir, session_name or "")
        participants = list(self.participants)
        session_dir_final = self.session_dir

        # Resolve announce channel once and reuse for both stop and transcription messages.
        announce_ch: discord.TextChannel | None = None
        if guild:
            announce_ch = await self._get_announce_channel(guild)

        if announce_ch:
            if self._stop_auto:
                await announce_ch.send(
                    f"Recording automatically stopped (channel empty). "
                    f"Files saved to `{rel_path}`"
                )
            else:
                await announce_ch.send(
                    f"Recording stopped — files saved to `{rel_path}`"
                )

        logger.info("Recording session finished: %s", session_name)

        # Clear session state before starting background task
        self.voice_client = None
        self.recording_channel = None
        self.session_name = None
        self.session_dir = None
        self.start_time = None
        self.participants = []
        self._stop_guild = None
        self._stop_auto = False

        # Launch WhisperX transcription as a background task
        if pcm_data and session_dir_final:
            self.is_transcribing = True
            if announce_ch:
                await announce_ch.send(
                    "Transcription started — processing audio with WhisperX..."
                )

            async def _transcribe_and_done() -> None:
                try:
                    await _run_whisperx(
                        wav_path,
                        session_dir_final,
                        participants,
                        self.config,
                        announce_ch,
                    )
                finally:
                    self.is_transcribing = False

            asyncio.create_task(_transcribe_and_done())

    # ------------------------------------------------------------------ #
    # Slash commands                                                        #
    # ------------------------------------------------------------------ #

    @record.command(
        name="start", description="Join a voice channel and start recording"
    )
    @discord.option(
        "voice_channel",
        discord.VoiceChannel,
        description="The voice channel to record",
    )
    @discord.option(
        "session_name",
        str,
        description='A label for this session, e.g. "Campaign1_Session4"',
    )
    async def record_start(
        self,
        ctx: discord.ApplicationContext,
        voice_channel: discord.VoiceChannel,
        session_name: str,
    ) -> None:
        if self.is_recording:
            await ctx.respond(
                "Already recording! Use `/mlx-ai record stop` first.",
                ephemeral=True,
            )
            return

        try:
            vc: discord.VoiceClient = await voice_channel.connect()
        except discord.ClientException as exc:
            await ctx.respond(f"Failed to join voice channel: {exc}", ephemeral=True)
            return
        except discord.Forbidden:
            await ctx.respond(
                "I don't have permission to join that voice channel.", ephemeral=True
            )
            return

        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        safe_session = re.sub(r"[^\w\-]", "_", session_name)
        folder_name = f"{timestamp}_{safe_session}"
        output_dir = self.config.get("output_directory", "./recordings")
        session_dir = os.path.join(output_dir, folder_name)
        os.makedirs(session_dir, exist_ok=True)

        # Snapshot participants present at session start
        participants = [
            {"user_id": m.id, "display_name": m.display_name}
            for m in voice_channel.members
            if not m.bot
        ]
        participants_path = os.path.join(session_dir, "participants.json")
        with open(participants_path, "w", encoding="utf-8") as f:
            json.dump(participants, f, ensure_ascii=False, indent=2)

        self.is_recording = True
        self.voice_client = vc
        self.recording_channel = voice_channel
        self.session_name = folder_name
        self.session_dir = session_dir
        self.start_time = datetime.now(timezone.utc)
        self.participants = participants

        vc.start_recording(
            MixedAudioSink(),
            self._recording_finished,
            session_dir,
        )

        logger.info(
            "Recording started: %s in voice channel '%s'", folder_name, voice_channel.name
        )

        announce_ch = await self._get_announce_channel(ctx.guild)
        if announce_ch:
            await announce_ch.send(
                f"Recording started in `{voice_channel.name}` — Session: `{folder_name}`"
            )

        await ctx.respond(
            f"Recording started in `{voice_channel.name}` — Session: `{folder_name}`",
            ephemeral=True,
        )

    @record.command(name="stop", description="Stop the current recording session")
    async def record_stop(self, ctx: discord.ApplicationContext) -> None:
        if not self.is_recording:
            await ctx.respond("No active recording session.", ephemeral=True)
            return

        await ctx.respond("Stopping recording...", ephemeral=True)
        await self._stop_recording(ctx.guild)

    @record.command(
        name="status", description="Show the status of the current recording session"
    )
    async def record_status(self, ctx: discord.ApplicationContext) -> None:
        if not self.is_recording and not self.is_transcribing:
            await ctx.respond("No active recording session.", ephemeral=True)
            return

        if self.is_transcribing and not self.is_recording:
            await ctx.respond(
                "**Transcription in Progress** — WhisperX is processing the recorded audio.",
                ephemeral=True,
            )
            return

        assert self.start_time is not None
        elapsed = datetime.now(timezone.utc) - self.start_time
        elapsed_str = str(elapsed).split(".")[0]

        human_count = (
            sum(1 for m in self.recording_channel.members if not m.bot)
            if self.recording_channel
            else 0
        )

        await ctx.respond(
            (
                "**Recording Active**\n"
                f"Session: `{self.session_name}`\n"
                f"Voice Channel: `{self.recording_channel.name if self.recording_channel else 'unknown'}`\n"
                f"Duration: `{elapsed_str}`\n"
                f"Users being recorded: `{human_count}`\n"
                f"Transcription in progress: {'Yes' if self.is_transcribing else 'No'}"
            ),
            ephemeral=True,
        )

    # ------------------------------------------------------------------ #
    # Voice state listener                                                  #
    # ------------------------------------------------------------------ #

    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ) -> None:
        if not self.is_recording or member.bot:
            return

        if (
            after.channel is not None
            and after.channel == self.recording_channel
            and before.channel != after.channel
        ):
            logger.info("%s joined mid-session.", member.name)
            announce_ch = await self._get_announce_channel(member.guild)
            if announce_ch:
                await announce_ch.send(
                    f"Now recording `{member.name}` who joined mid-session"
                )

        if (
            before.channel is not None
            and before.channel == self.recording_channel
        ):
            human_members = [m for m in self.recording_channel.members if not m.bot]
            if not human_members:
                logger.info(
                    "Voice channel '%s' is empty — auto-stopping recording.",
                    self.recording_channel.name,
                )
                await self._stop_recording(member.guild, auto=True)


def setup(bot: commands.Bot) -> None:
    """Entry point called by bot.load_extension('cogs.recorder').

    py-cord 2.6 calls setup() synchronously — do NOT make this async.
    """
    bot.add_cog(RecorderCog(bot, bot.config))
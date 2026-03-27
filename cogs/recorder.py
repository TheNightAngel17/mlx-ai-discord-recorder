"""
cogs/recorder.py — All /mlx-ai recording logic for the MLX AI Discord Recorder bot.

Commands:
    /mlx-ai record start  — Join a voice channel and begin per-user WAV recording
    /mlx-ai record stop   — Stop the active recording and save all audio files
    /mlx-ai record status — Show the current recording session info
"""

from __future__ import annotations

import io
import logging
import os
import re
import wave
from datetime import datetime, timezone

import discord
from discord.ext import commands
from discord.sinks import AudioData, SinkException

logger = logging.getLogger(__name__)


class PerUserWaveSink(discord.sinks.Sink):
    """A sink that correctly records each Discord user's audio to a separate WAV file."""

    encoding = "wav"

    def format_audio(self, audio: AudioData) -> None:
        if self.vc.recording:
            raise SinkException(
                "Audio may only be formatted after recording is finished."
            )

        audio.file.seek(0)
        pcm_data = audio.file.read()

        wav_buffer = io.BytesIO()
        with wave.open(wav_buffer, "wb") as wf:
            wf.setnchannels(self.vc.decoder.CHANNELS)
            wf.setsampwidth(self.vc.decoder.SAMPLE_SIZE // self.vc.decoder.CHANNELS)
            wf.setframerate(self.vc.decoder.SAMPLING_RATE)
            wf.writeframes(pcm_data)

        wav_buffer.seek(0)
        audio.file = wav_buffer
        audio.on_format(self.encoding)


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
        self.voice_client: discord.VoiceClient | None = None
        self.recording_channel: discord.VoiceChannel | None = None
        self.session_name: str | None = None
        self.session_dir: str | None = None
        self.start_time: datetime | None = None

        self._stop_guild: discord.Guild | None = None
        self._stop_auto: bool = False

        super().__init__()

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
        self, sink: PerUserWaveSink, session_dir: str
    ) -> None:
        for user_id, audio in sink.audio_data.items():
            user = self.bot.get_user(user_id)
            if user is None:
                try:
                    user = await self.bot.fetch_user(user_id)
                except discord.NotFound:
                    logger.warning(
                        "Could not resolve user ID %s — skipping their audio.",
                        user_id,
                    )
                    continue

            if user.bot:
                continue

            audio.file.seek(0)
            wav_bytes = audio.file.read()
            if not wav_bytes:
                logger.warning("No audio captured for %s — skipping.", user.name)
                continue

            safe_name = re.sub(r"[^
\w\-]", "_", user.name)
            filepath = os.path.join(session_dir, f"{safe_name}.wav")
            with open(filepath, "wb") as f:
                f.write(wav_bytes)
            logger.info("Saved recording for %s → %s", user.name, filepath)

        if self.voice_client and self.voice_client.is_connected():
            await self.voice_client.disconnect()

        guild = self._stop_guild
        session_name = self.session_name
        output_dir = self.config.get("output_directory", "./recordings")
        rel_path = os.path.join(output_dir, session_name or "")

        if guild:
            announce_ch = await self._get_announce_channel(guild)
            if announce_ch:
                if self._stop_auto:
                    await announce_ch.send(
                        f"⏹️ Channel empty — recording automatically stopped. "
                        f"Files saved to `{rel_path}`"
                    )
                else:
                    await announce_ch.send(
                        f"⏹️ Recording stopped — files saved to `{rel_path}`"
                    )

        logger.info("Recording session finished: %s", session_name)

        self.voice_client = None
        self.recording_channel = None
        self.session_name = None
        self.session_dir = None
        self.start_time = None
        self._stop_guild = None
        self._stop_auto = False

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
                "❌ Already recording! Use `/mlx-ai record stop` first.",
                ephemeral=True,
            )
            return

        try:
            vc: discord.VoiceClient = await voice_channel.connect()
        except discord.ClientException as exc:
            await ctx.respond(f"❌ Failed to join voice channel: {exc}", ephemeral=True)
            return
        except discord.Forbidden:
            await ctx.respond("❌ I don't have permission to join that voice channel.", ephemeral=True)
            return

        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        folder_name = f"{timestamp}_{session_name}"
        output_dir = self.config.get("output_directory", "./recordings")
        session_dir = os.path.join(output_dir, folder_name)
        os.makedirs(session_dir, exist_ok=True)

        self.is_recording = True
        self.voice_client = vc
        self.recording_channel = voice_channel
        self.session_name = folder_name
        self.session_dir = session_dir
        self.start_time = datetime.now(timezone.utc)

        vc.start_recording(
            PerUserWaveSink(),
            self._recording_finished,
            session_dir,
        )

        logger.info("Recording started: %s in voice channel '%s'", folder_name, voice_channel.name)

        announce_ch = await self._get_announce_channel(ctx.guild)
        if announce_ch:
            await announce_ch.send(
                f"🔴 Recording started in `{voice_channel.name}` — Session: `{folder_name}`"
            )

        await ctx.respond(
            f"✅ Recording started in `{voice_channel.name}` — Session: `{folder_name}`",
            ephemeral=True,
        )

    @record.command(name="stop", description="Stop the current recording session")
    async def record_stop(self, ctx: discord.ApplicationContext) -> None:
        if not self.is_recording:
            await ctx.respond("❌ No active recording session.", ephemeral=True)
            return

        await ctx.respond("⏹️ Stopping recording…", ephemeral=True)
        await self._stop_recording(ctx.guild)

    @record.command(
        name="status", description="Show the status of the current recording session"
    )
    async def record_status(self, ctx: discord.ApplicationContext) -> None:
        if not self.is_recording:
            await ctx.respond("ℹ️ No active recording session.", ephemeral=True)
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
                "🔴 **Recording Active**\n"
                f"Session: `{self.session_name}`\n"
                f"Voice Channel: `{self.recording_channel.name if self.recording_channel else 'unknown'}`\n"
                f"Duration: `{elapsed_str}`\n"
                f"Users being recorded: `{human_count}`"
            ),
            ephemeral=True,
        )

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
            logger.info("%s joined mid-session — now recording them.", member.name)
            announce_ch = await self._get_announce_channel(member.guild)
            if announce_ch:
                await announce_ch.send(f"🎙️ Now recording `{member.name}` who joined mid-session")

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

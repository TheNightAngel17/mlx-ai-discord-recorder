"""
bot.py — Entry point for the MLX AI Discord Recorder bot.

Responsibilities:
  - Load configuration from config.yaml and secrets from .env
  - Load libopus for Discord voice support (Linux)
  - Initialise the discord.py (py-cord) Bot with the required intents
  - Load the RecorderCog extension
  - Sync slash commands to the configured guild on startup
"""

from __future__ import annotations

import asyncio
import ctypes.util
import logging
import os

import discord
import yaml
from discord.ext import commands
from dotenv import load_dotenv

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Opus auto-load (required for Discord voice on Linux)
# ---------------------------------------------------------------------------
def _load_opus() -> None:
    """Attempt to load libopus so py-cord voice recording works on Linux."""
    if discord.opus.is_loaded():
        return

    candidates = [
        "libopus.so.0",                                      # short name (most Linux)
        ctypes.util.find_library("opus"),                    # dynamic lookup
        "/usr/lib/x86_64-linux-gnu/libopus.so.0",           # Debian/Ubuntu fallback
        "/usr/lib/aarch64-linux-gnu/libopus.so.0",          # ARM (Raspberry Pi etc.)
    ]

    for name in candidates:
        if not name:
            continue
        try:
            discord.opus.load_opus(name)
            logger.info("Opus loaded from: %s", name)
            return
        except OSError:
            continue

    logger.warning(
        "Could not load libopus — voice recording will not work. "
        "On Debian/Ubuntu run: sudo apt install libopus0"
    )

_load_opus()

def load_config() -> dict:
    """Load and return the contents of config.yaml."""
    config_path = os.path.join(os.path.dirname(__file__), "config.yaml")
    with open(config_path, "r") as f:
        return yaml.safe_load(f)

async def main() -> None:
    load_dotenv()

    token = os.environ.get("DISCORD_TOKEN")
    guild_id_str = os.environ.get("GUILD_ID")

    if not token:
        logger.error("DISCORD_TOKEN is not set. Add it to your .env file.")
        raise SystemExit(1)

    if not guild_id_str:
        logger.error("GUILD_ID is not set. Add it to your .env file.")
        raise SystemExit(1)

    try:
        guild_id = int(guild_id_str)
    except ValueError:
        logger.error("GUILD_ID must be a numeric Discord guild (server) ID.")
        raise SystemExit(1)

    config = load_config()

    intents = discord.Intents.default()
    intents.members = True
    intents.message_content = True
    intents.voice_states = True

    bot = commands.Bot(
        command_prefix="!",
        intents=intents,
        auto_sync_commands=False,
    )

    bot.config = config       # type: ignore[attr-defined]
    bot.guild_id = guild_id   # type: ignore[attr-defined]

    # load_extension is synchronous in py-cord 2.6 — do NOT await it
    bot.load_extension("cogs.recorder")

    @bot.event
    async def on_ready() -> None:
        logger.info("Logged in as %s (ID: %s)", bot.user, bot.user.id)
        await bot.sync_commands(guild_ids=[guild_id])
        logger.info("Slash commands synced to guild %s", guild_id)

    await bot.start(token)

if __name__ == "__main__":
    asyncio.run(main())
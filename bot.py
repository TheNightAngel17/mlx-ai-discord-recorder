"""
bot.py — Entry point for the MLX AI Discord Recorder bot.

Responsibilities:
  - Load configuration from config.yaml and secrets from .env
  - Initialise the discord.py (py-cord) Bot with the required intents
  - Load the RecorderCog extension
  - Sync slash commands to the configured guild on startup
"""

from __future__ import annotations

import asyncio
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
    intents.members = True          # Required for on_voice_state_update member info
    intents.message_content = True  # Privileged intent — required by Discord for verified bots
    intents.voice_states = True     # Required for voice channel state tracking

    bot = commands.Bot(
        command_prefix="!",
        intents=intents,
        auto_sync_commands=False,   # We sync manually in on_ready for guild-scope
    )

    # Attach shared state to the bot so cogs can access it
    bot.config = config       # type: ignore[attr-defined]
    bot.guild_id = guild_id   # type: ignore[attr-defined]

    @bot.event
    async def on_ready() -> None:
        logger.info("Logged in as %s (ID: %s)", bot.user, bot.user.id)
        # load_extension is synchronous in py-cord 2.6; must be called after
        # the bot is ready so guild_id is available on the bot instance
        bot.load_extension("cogs.recorder")
        # Sync commands scoped to the configured guild — takes effect immediately
        await bot.sync_commands(guild_ids=[guild_id])
        logger.info("Slash commands synced to guild %s", guild_id)

    await bot.start(token)

if __name__ == "__main__":
    asyncio.run(main())

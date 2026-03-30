/**
 * bot.js — Entry point for the MLX AI Discord Recorder JS bot.
 *
 * Responsibilities:
 *   - Load secrets from ../.env and config from ../config.yaml
 *   - Create a discord.js Client with the required intents
 *   - Register slash commands as guild commands via REST on startup
 *   - Route /mlx-ai record start|stop|status interactions to the Recorder
 *   - Forward voiceStateUpdate events to the Recorder
 */

"use strict";

const path = require("path");
require("dotenv").config({ path: path.join(__dirname, "../.env") });

const fs = require("fs");
const yaml = require("js-yaml");
const {
  Client,
  GatewayIntentBits,
  REST,
  Routes,
  SlashCommandBuilder,
  SlashCommandSubcommandGroupBuilder,
  SlashCommandSubcommandBuilder,
} = require("discord.js");
const { Recorder } = require("./recorder");

// ---------------------------------------------------------------------------
// Logging helper — matches Python bot format: YYYY-MM-DD HH:MM:SS [LEVEL] module: message
// ---------------------------------------------------------------------------
function makeLogger(module) {
  function fmt(level, msg) {
    const now = new Date()
      .toISOString()
      .replace("T", " ")
      .replace(/\.\d{3}Z$/, "");
    process.stdout.write(`${now} [${level}] ${module}: ${msg}\n`);
  }
  return {
    info: (msg) => fmt("INFO", msg),
    warn: (msg) => fmt("WARNING", msg),
    error: (msg) => fmt("ERROR", msg),
    debug: (msg) => fmt("DEBUG", msg),
  };
}

const logger = makeLogger("bot");

// ---------------------------------------------------------------------------
// Load config
// ---------------------------------------------------------------------------
const configPath = path.join(__dirname, "../config.yaml");
let config;
try {
  config = yaml.load(fs.readFileSync(configPath, "utf8"));
} catch (err) {
  logger.error(`Failed to load config.yaml: ${err.message}`);
  process.exit(1);
}

// ---------------------------------------------------------------------------
// Validate required environment variables
// ---------------------------------------------------------------------------
const token = process.env.DISCORD_TOKEN;
const guildId = process.env.GUILD_ID;

if (!token) {
  logger.error("DISCORD_TOKEN is not set. Add it to your .env file.");
  process.exit(1);
}
if (!guildId) {
  logger.error("GUILD_ID is not set. Add it to your .env file.");
  process.exit(1);
}

// ---------------------------------------------------------------------------
// Slash command definitions
// ---------------------------------------------------------------------------
const commands = [
  new SlashCommandBuilder()
    .setName("mlx-ai")
    .setDescription("MLX AI Discord Recorder commands")
    .addSubcommandGroup(
      new SlashCommandSubcommandGroupBuilder()
        .setName("record")
        .setDescription("Voice recording commands")
        .addSubcommand(
          new SlashCommandSubcommandBuilder()
            .setName("start")
            .setDescription("Join a voice channel and start recording")
            .addChannelOption((opt) =>
              opt
                .setName("voice_channel")
                .setDescription("The voice channel to record")
                .setRequired(true)
            )
            .addStringOption((opt) =>
              opt
                .setName("session_name")
                .setDescription(
                  'A label for this session, e.g. "Campaign1_Session4"'
                )
                .setRequired(true)
            )
        )
        .addSubcommand(
          new SlashCommandSubcommandBuilder()
            .setName("stop")
            .setDescription("Stop the current recording session")
        )
        .addSubcommand(
          new SlashCommandSubcommandBuilder()
            .setName("status")
            .setDescription("Show the status of the current recording session")
        )
    )
    .toJSON(),
];

// ---------------------------------------------------------------------------
// Discord client
// ---------------------------------------------------------------------------
const client = new Client({
  intents: [
    GatewayIntentBits.Guilds,
    GatewayIntentBits.GuildVoiceStates,
    GatewayIntentBits.GuildMembers,
    GatewayIntentBits.GuildMessages,
    GatewayIntentBits.MessageContent,
  ],
});

const recorder = new Recorder(config, makeLogger("recorder"));

// ---------------------------------------------------------------------------
// Register guild commands on startup
// ---------------------------------------------------------------------------
client.once("ready", async () => {
  logger.info(`Logged in as ${client.user.tag}`);

  const rest = new REST({ version: "10" }).setToken(token);
  try {
    await rest.put(Routes.applicationGuildCommands(client.user.id, guildId), {
      body: commands,
    });
    logger.info(`Slash commands registered to guild ${guildId}`);
  } catch (err) {
    logger.error(`Failed to register slash commands: ${err.message}`);
  }
});

// ---------------------------------------------------------------------------
// Interaction routing
// ---------------------------------------------------------------------------
client.on("interactionCreate", async (interaction) => {
  if (!interaction.isChatInputCommand()) return;
  if (interaction.commandName !== "mlx-ai") return;

  const group = interaction.options.getSubcommandGroup(false);
  const sub = interaction.options.getSubcommand(false);

  if (group !== "record") return;

  if (sub === "start") {
    const voiceChannel = interaction.options.getChannel("voice_channel");
    const sessionName = interaction.options.getString("session_name");
    await recorder.start(interaction, voiceChannel, sessionName);
  } else if (sub === "stop") {
    await recorder.stop(interaction);
  } else if (sub === "status") {
    await recorder.status(interaction);
  }
});

// ---------------------------------------------------------------------------
// Voice state forwarding
// ---------------------------------------------------------------------------
client.on("voiceStateUpdate", (oldState, newState) => {
  recorder.onVoiceStateUpdate(oldState, newState);
});

// ---------------------------------------------------------------------------
// Start
// ---------------------------------------------------------------------------
client.login(token).catch((err) => {
  logger.error(`Failed to login: ${err.message}`);
  process.exit(1);
});

/**
 * recorder.js — All recording logic for the MLX AI Discord Recorder JS bot.
 *
 * Exports a Recorder class with:
 *   start(interaction, voiceChannel, sessionName) — join channel, begin recording
 *   stop(interaction)                             — stop recording, save WAVs, disconnect
 *   status(interaction)                           — reply with current session info
 *   onVoiceStateUpdate(oldState, newState)        — auto-stop / mid-join announcements
 */

"use strict";

const fs = require("fs");
const path = require("path");
const { joinVoiceChannel, EndBehaviorType } = require("@discordjs/voice");
const prism = require("prism-media");

// WAV parameters — must match the Opus decoder settings
const SAMPLE_RATE = 48000;
const CHANNELS = 2;
const BIT_DEPTH = 16; // 16-bit PCM
const BYTES_PER_SAMPLE = BIT_DEPTH / 8;

/**
 * Build a WAV file Buffer from raw PCM data.
 * Format: PCM, 48 kHz, stereo, 16-bit little-endian.
 *
 * @param {Buffer} pcmData
 * @returns {Buffer}
 */
function buildWav(pcmData) {
  const dataSize = pcmData.length;
  const header = Buffer.alloc(44);

  // RIFF chunk
  header.write("RIFF", 0);
  header.writeUInt32LE(36 + dataSize, 4); // ChunkSize
  header.write("WAVE", 8);

  // fmt sub-chunk
  header.write("fmt ", 12);
  header.writeUInt32LE(16, 16); // Subchunk1Size (PCM)
  header.writeUInt16LE(1, 20); // AudioFormat (PCM = 1)
  header.writeUInt16LE(CHANNELS, 22);
  header.writeUInt32LE(SAMPLE_RATE, 24);
  header.writeUInt32LE(SAMPLE_RATE * CHANNELS * BYTES_PER_SAMPLE, 28); // ByteRate
  header.writeUInt16LE(CHANNELS * BYTES_PER_SAMPLE, 32); // BlockAlign
  header.writeUInt16LE(BIT_DEPTH, 34);

  // data sub-chunk
  header.write("data", 36);
  header.writeUInt32LE(dataSize, 40);

  return Buffer.concat([header, pcmData]);
}

/**
 * Sanitise a Discord username for use as a filename.
 * Mirrors the Python bot: replace [^\w\-] with _.
 *
 * @param {string} name
 * @returns {string}
 */
function sanitiseName(name) {
  return name.replace(/[^\w\-]/g, "_");
}

class Recorder {
  /**
   * @param {object} config   Parsed config.yaml object
   * @param {object} logger   Logger with .info / .warn / .error methods
   */
  constructor(config, logger) {
    this.config = config;
    this.logger = logger;

    this.isRecording = false;
    /** @type {import('@discordjs/voice').VoiceConnection|null} */
    this.connection = null;
    /** @type {import('discord.js').VoiceChannel|null} */
    this.recordingChannel = null;
    this.sessionName = null;
    this.sessionDir = null;
    /** @type {Date|null} */
    this.startTime = null;

    // userId -> { chunks: Buffer[], username: string, stream: ReadableStream, decoder: prism.opus.Decoder }
    this.audioBuffers = new Map();
  }

  // -------------------------------------------------------------------------
  // Public API
  // -------------------------------------------------------------------------

  /**
   * Join a voice channel and start recording each user to a separate WAV file.
   *
   * @param {import('discord.js').ChatInputCommandInteraction} interaction
   * @param {import('discord.js').VoiceChannel} voiceChannel
   * @param {string} sessionName
   */
  async start(interaction, voiceChannel, sessionName) {
    if (this.isRecording) {
      await interaction.reply({
        content: "Already recording! Use `/mlx-ai record stop` first.",
        ephemeral: true,
      });
      return;
    }

    // Verify the selected channel is actually a voice channel
    const { ChannelType } = require("discord.js");
    if (
      voiceChannel.type !== ChannelType.GuildVoice &&
      voiceChannel.type !== ChannelType.GuildStageVoice
    ) {
      await interaction.reply({
        content: "Please select a voice channel.",
        ephemeral: true,
      });
      return;
    }

    // Create session directory
    const timestamp = new Date()
      .toISOString()
      .replace(/[-:]/g, "")
      .replace("T", "_")
      .slice(0, 15); // YYYYMMDD_HHMMSS
    const folderName = `${timestamp}_${sessionName}`;
    const outputDir = this.config.output_directory || "./recordings";
    const sessionDir = path.join(outputDir, folderName);

    try {
      fs.mkdirSync(sessionDir, { recursive: true });
    } catch (err) {
      await interaction.reply({
        content: `Failed to create session directory: ${err.message}`,
        ephemeral: true,
      });
      return;
    }

    // Join the voice channel
    let connection;
    try {
      connection = joinVoiceChannel({
        channelId: voiceChannel.id,
        guildId: voiceChannel.guild.id,
        adapterCreator: voiceChannel.guild.voiceAdapterCreator,
        selfDeaf: false,
        selfMute: true,
      });
    } catch (err) {
      await interaction.reply({
        content: `Failed to join voice channel: ${err.message}`,
        ephemeral: true,
      });
      return;
    }

    this.isRecording = true;
    this.connection = connection;
    this.recordingChannel = voiceChannel;
    this.sessionName = folderName;
    this.sessionDir = sessionDir;
    this.startTime = new Date();
    this.audioBuffers = new Map();

    // Subscribe to all members currently in the channel
    for (const member of voiceChannel.members.values()) {
      if (!member.user.bot) {
        this._subscribeUser(member.user.id, member.user.username);
      }
    }

    // Also subscribe to any user who starts speaking (handles mid-session joins
    // at the audio level, before voiceStateUpdate fires)
    const receiver = connection.receiver;
    receiver.speaking.on("start", (userId) => {
      if (!this.isRecording) return;
      if (!this.audioBuffers.has(userId)) {
        const member = voiceChannel.guild.members.cache.get(userId);
        const username = member ? member.user.username : userId;
        this._subscribeUser(userId, username);
      }
    });

    this.logger.info(
      `Recording started: ${folderName} in voice channel '${voiceChannel.name}'`
    );

    // Announce in text channel
    const announceChannel = await this._getAnnounceChannel(voiceChannel.guild);
    if (announceChannel) {
      await announceChannel.send(
        `Recording started in \`${voiceChannel.name}\` — Session: \`${folderName}\``
      );
    }

    await interaction.reply({
      content: `Recording started in \`${voiceChannel.name}\` — Session: \`${folderName}\``,
      ephemeral: true,
    });
  }

  /**
   * Stop the current recording, save WAV files, disconnect, and announce.
   *
   * @param {import('discord.js').ChatInputCommandInteraction|import('discord.js').Guild} interactionOrGuild
   * @param {boolean} [auto=false]  true when auto-stopped due to empty channel
   */
  async stop(interactionOrGuild, auto = false) {
    // interactionOrGuild can be a slash command Interaction or a Guild object
    // (when called internally from onVoiceStateUpdate)
    const isInteraction =
      interactionOrGuild && typeof interactionOrGuild.reply === "function";

    if (!this.isRecording) {
      if (isInteraction) {
        await interactionOrGuild.reply({
          content: "No active recording session.",
          ephemeral: true,
        });
      }
      return;
    }

    if (isInteraction) {
      await interactionOrGuild.reply({
        content: "Stopping recording…",
        ephemeral: true,
      });
    }

    this.isRecording = false;

    const guild = isInteraction
      ? interactionOrGuild.guild
      : interactionOrGuild;
    const sessionName = this.sessionName;
    const sessionDir = this.sessionDir;
    const outputDir = this.config.output_directory || "./recordings";

    // Save WAV files for all captured users
    for (const [userId, entry] of this.audioBuffers.entries()) {
      const { chunks, username } = entry;

      // Stop the decoder stream
      if (entry.stream && !entry.stream.destroyed) {
        entry.stream.destroy();
      }
      if (entry.decoder && !entry.decoder.destroyed) {
        entry.decoder.destroy();
      }

      const pcmData = Buffer.concat(chunks);
      if (pcmData.length === 0) {
        this.logger.warn(`No audio captured for ${username} — skipping.`);
        continue;
      }

      const safeName = sanitiseName(username);
      const filePath = path.join(sessionDir, `${safeName}.wav`);
      try {
        fs.writeFileSync(filePath, buildWav(pcmData));
        this.logger.info(`Saved recording for ${username} -> ${filePath}`);
      } catch (err) {
        this.logger.error(`Failed to save WAV for ${username}: ${err.message}`);
      }
    }

    // Disconnect
    if (this.connection) {
      this.connection.destroy();
      this.connection = null;
    }

    // Announce
    const relPath = path.join(outputDir, sessionName || "");
    if (guild) {
      const announceChannel = await this._getAnnounceChannel(guild);
      if (announceChannel) {
        if (auto) {
          await announceChannel.send(
            `Recording automatically stopped (channel empty). Files saved to \`${relPath}\``
          );
        } else {
          await announceChannel.send(
            `Recording stopped — files saved to \`${relPath}\``
          );
        }
      }
    }

    this.logger.info(`Recording session finished: ${sessionName}`);

    // Reset state
    this.audioBuffers = new Map();
    this.recordingChannel = null;
    this.sessionName = null;
    this.sessionDir = null;
    this.startTime = null;
  }

  /**
   * Reply with current session info.
   *
   * @param {import('discord.js').ChatInputCommandInteraction} interaction
   */
  async status(interaction) {
    if (!this.isRecording) {
      await interaction.reply({
        content: "No active recording session.",
        ephemeral: true,
      });
      return;
    }

    const elapsed = Math.floor((Date.now() - this.startTime.getTime()) / 1000);
    const hours = Math.floor(elapsed / 3600)
      .toString()
      .padStart(2, "0");
    const minutes = Math.floor((elapsed % 3600) / 60)
      .toString()
      .padStart(2, "0");
    const seconds = (elapsed % 60).toString().padStart(2, "0");
    const elapsedStr = `${hours}:${minutes}:${seconds}`;

    const humanCount = this.recordingChannel
      ? [...this.recordingChannel.members.values()].filter((m) => !m.user.bot)
          .length
      : 0;

    await interaction.reply({
      content: [
        "**Recording Active**",
        `Session: \`${this.sessionName}\``,
        `Voice Channel: \`${this.recordingChannel ? this.recordingChannel.name : "unknown"}\``,
        `Duration: \`${elapsedStr}\``,
        `Users being recorded: \`${humanCount}\``,
      ].join("\n"),
      ephemeral: true,
    });
  }

  /**
   * Handle voice state changes — auto-stop when channel empties,
   * and announce mid-session joins.
   *
   * @param {import('discord.js').VoiceState} oldState
   * @param {import('discord.js').VoiceState} newState
   */
  onVoiceStateUpdate(oldState, newState) {
    if (!this.isRecording) return;
    if (newState.member && newState.member.user.bot) return;

    const member = newState.member || oldState.member;
    if (!member) return;

    // Mid-session join announcement
    if (
      newState.channelId === this.recordingChannel?.id &&
      oldState.channelId !== newState.channelId
    ) {
      this.logger.info(
        `${member.user.username} joined mid-session — now recording them.`
      );
      this._getAnnounceChannel(newState.guild).then((ch) => {
        if (ch) {
          ch.send(
            `Now recording \`${member.user.username}\` who joined mid-session`
          );
        }
      });
    }

    // Auto-stop when all humans leave
    if (
      oldState.channelId === this.recordingChannel?.id &&
      oldState.channelId !== newState.channelId
    ) {
      const humanMembers = [
        ...this.recordingChannel.members.values(),
      ].filter((m) => !m.user.bot);

      if (humanMembers.length === 0) {
        this.logger.info(
          `Voice channel '${this.recordingChannel.name}' is empty — auto-stopping recording.`
        );
        this.stop(newState.guild, true).catch((err) => {
          this.logger.error(`Auto-stop failed: ${err.message}`);
        });
      }
    }
  }

  // -------------------------------------------------------------------------
  // Internal helpers
  // -------------------------------------------------------------------------

  /**
   * Subscribe to a user's audio stream and collect PCM chunks.
   *
   * @param {string} userId
   * @param {string} username
   */
  _subscribeUser(userId, username) {
    if (!this.connection || !this.isRecording) return;
    if (this.audioBuffers.has(userId)) return; // already subscribed

    const receiver = this.connection.receiver;
    const opusStream = receiver.subscribe(userId, {
      end: { behavior: EndBehaviorType.Manual },
    });

    const decoder = new prism.opus.Decoder({
      rate: SAMPLE_RATE,
      channels: CHANNELS,
      frameSize: 960,
    });

    const chunks = [];
    const entry = { chunks, username, stream: opusStream, decoder };
    this.audioBuffers.set(userId, entry);

    opusStream.pipe(decoder);

    decoder.on("data", (chunk) => {
      chunks.push(chunk);
    });

    decoder.on("error", (err) => {
      this.logger.warn(
        `Opus decoder error for ${username}: ${err.message}`
      );
    });

    this.logger.info(`Subscribed to audio for ${username} (${userId})`);
  }

  /**
   * Find the configured announce text channel in the given guild.
   *
   * @param {import('discord.js').Guild} guild
   * @returns {Promise<import('discord.js').TextChannel|null>}
   */
  async _getAnnounceChannel(guild) {
    const channelName = this.config.announce_channel || "bot-commands";
    const channel = guild.channels.cache.find(
      (c) => c.name === channelName && c.isTextBased()
    );
    return channel || null;
  }
}

module.exports = { Recorder };

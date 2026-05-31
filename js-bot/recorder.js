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
const {
  joinVoiceChannel,
  entersState,
  VoiceConnectionStatus,
  EndBehaviorType,
} = require("@discordjs/voice");
const {
  ActionRowBuilder,
  ButtonBuilder,
  ButtonStyle,
  ChannelType,
  StringSelectMenuBuilder,
} = require("discord.js");
const prism = require("prism-media");

// WAV parameters — must match the Opus decoder settings
const SAMPLE_RATE = 48000;
const CHANNELS = 2;
const BIT_DEPTH = 16; // 16-bit PCM
const BYTES_PER_SAMPLE = BIT_DEPTH / 8;

// Discord sends 20ms Opus frames: 960 samples per channel at 48 kHz
const OPUS_FRAME_SIZE = 960;

// Bytes in one full PCM sample frame (all channels). Used to align any appended
// silence padding to a clean sample boundary.
const BLOCK_ALIGN = CHANNELS * BYTES_PER_SAMPLE;

// Timeouts
const CONNECTION_READY_TIMEOUT_MS = 20_000; // max wait for VoiceConnectionStatus.Ready
const STREAM_FINISH_TIMEOUT_MS = 3_000; // max wait for an in-flight snippet to flush on stop

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

    // How long a user must be silent before the current utterance snippet is
    // closed. This window doubles as the "back-pad": the receiver keeps the
    // stream open until the user is genuinely silent this long, so trailing
    // words spoken right before Discord's early "stopped talking" signal are
    // still captured. Too low fragments sentences; too high merges utterances.
    this._silenceMs = Number(config.snippet_silence_ms) || 1000;
    // Extra trailing silence (ms) appended to each snippet so Whisper has a
    // little lead-out and does not clip the final word. Set 0 to disable.
    this._tailPadMs =
      config.snippet_tail_pad_ms != null ? Number(config.snippet_tail_pad_ms) : 200;

    // Live transcription: when enabled, each finished snippet is POSTed to the
    // warm-model transcription service (py-transcribe) as it is recorded.
    this._autoTranscribe = Boolean(config.auto_transcribe);
    this._transcribePort = Number(config.transcribe_api_port) || 8200;
    this._transcribeMinMs =
      config.transcribe_min_ms != null ? Number(config.transcribe_min_ms) : 400;

    this.isRecording = false;
    /** @type {import('@discordjs/voice').VoiceConnection|null} */
    this.connection = null;
    /** @type {import('discord.js').VoiceChannel|null} */
    this.recordingChannel = null;
    this.sessionName = null;
    this.sessionDir = null;
    /** @type {Date|null} */
    this.startTime = null;

    // userId -> { username, dir, snippetCount, active: Set<utterance> }
    // One entry per participant who has spoken; `active` holds in-flight
    // utterance captures so stop() can flush them.
    this.users = new Map();

    /**
     * Optional callback invoked when a user joins mid-session.
     * Set by SessionPanel to surface the event in the panel log.
     * @type {((username: string) => void) | null}
     */
    this.onMidSessionJoin = null;
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
   * @param {boolean} [silent=false]  When true, suppresses the announce-channel message.
   *   Pass true when called from the session panel to avoid duplicate chat messages.
   */
  async start(interaction, voiceChannel, sessionName, silent = false) {
    if (this.isRecording) {
      await interaction.reply({
        content: "Already recording! Use `/mlx-ai record stop` first.",
        ephemeral: true,
      });
      return;
    }

    // Defer immediately so Discord doesn't time out while we connect
    await interaction.deferReply({ ephemeral: true });

    // Verify the selected channel is actually a voice channel
    if (
      voiceChannel.type !== ChannelType.GuildVoice &&
      voiceChannel.type !== ChannelType.GuildStageVoice
    ) {
      await interaction.editReply({ content: "Please select a voice channel." });
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
      await interaction.editReply({
        content: `Failed to create session directory: ${err.message}`,
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
      await interaction.editReply({
        content: `Failed to join voice channel: ${err.message}`,
      });
      return;
    }

    this.isRecording = true;
    this.connection = connection;
    this.recordingChannel = voiceChannel;
    this.sessionName = folderName;
    this.sessionDir = sessionDir;
    this.startTime = new Date();
    this.users = new Map();

    // Persist session-level metadata so the Python pipeline can map snippet
    // offsets back to absolute wall-clock time if needed.
    try {
      fs.writeFileSync(
        path.join(sessionDir, "_session.metadata.json"),
        JSON.stringify(
          { session_start_ms: this.startTime.getTime(), session_name: folderName },
          null,
          2
        )
      );
    } catch (err) {
      this.logger.warn(`Could not write session metadata: ${err.message}`);
    }

    // Debug: log every state transition so we can see where it stalls
    connection.on("stateChange", (oldState, newState) => {
      this.logger.info(
        `Voice connection state: ${oldState.status} -> ${newState.status}`
      );
    });

    connection.on("error", (err) => {
      this.logger.error(`Voice connection error: ${err.message}`);
    });

    // Wait until the voice connection is fully ready before subscribing.
    // Audio packets are only delivered once the connection reaches Ready state.
    try {
      await entersState(connection, VoiceConnectionStatus.Ready, CONNECTION_READY_TIMEOUT_MS);
    } catch (err) {
      connection.destroy();
      this.isRecording = false;
      this.connection = null;
      this.recordingChannel = null;
      this.sessionName = null;
      this.sessionDir = null;
      this.startTime = null;
      this.users = new Map();
      await interaction.editReply({
        content: "Timed out waiting for voice connection to be ready.",
      });
      return;
    }

    // Capture begins when someone speaks. Each time a user starts talking we
    // open a fresh per-utterance stream that auto-closes after a silence gap
    // (see _startUtterance). Members present at start are not pre-subscribed —
    // their first utterance creates their sub-folder.
    const receiver = connection.receiver;
    receiver.speaking.on("start", (userId) => {
      if (!this.isRecording) return;
      const entry = this._ensureUser(userId, voiceChannel.guild);
      this._startUtterance(userId, entry);
    });

    this.logger.info(
      `Recording started: ${folderName} in voice channel '${voiceChannel.name}'`
    );

    // Announce in text channel (skipped when called silently from the session panel)
    if (!silent) {
      const announceChannel = await this._getAnnounceChannel(voiceChannel.guild);
      if (announceChannel) {
        await announceChannel.send(
          `Recording started in \`${voiceChannel.name}\` — Session: \`${folderName}\``
        );
      }
    }

    await interaction.editReply({
      content: `Recording started in \`${voiceChannel.name}\` — Session: \`${folderName}\``,
    });
  }

  /**
   * Stop the current recording, save WAV files, disconnect, and announce.
   *
   * @param {import('discord.js').ChatInputCommandInteraction|import('discord.js').Guild} interactionOrGuild
   * @param {boolean} [auto=false]    true when auto-stopped due to empty channel
   * @param {boolean} [silent=false]  When true, suppresses the announce-channel message.
   *   Pass true when called from the session panel to avoid duplicate chat messages.
   */
  async stop(interactionOrGuild, auto = false, silent = false) {
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
    const outputDir = this.config.output_directory || "./recordings";

    // Flush any in-flight utterance snippets. Snippets that already hit their
    // silence boundary were written to disk when their stream ended; here we
    // only need to close the ones still capturing at stop time.
    const flushPromises = [];
    for (const entry of this.users.values()) {
      for (const utterance of [...entry.active]) {
        flushPromises.push(utterance.close());
      }
    }
    await Promise.all(flushPromises);

    // Remove any sub-folders that ended up with no snippets (e.g. a user who
    // triggered a speaking event but produced no decodable audio).
    for (const entry of this.users.values()) {
      if (entry.snippetCount === 0) {
        try {
          fs.rmdirSync(entry.dir);
        } catch {
          /* non-empty or already gone — leave it */
        }
      }
    }

    // Disconnect
    if (this.connection) {
      this.connection.destroy();
      this.connection = null;
    }

    // Announce (skipped when called silently from the session panel)
    const relPath = path.join(outputDir, sessionName || "");
    if (guild && !silent) {
      const announceChannel = await this._getAnnounceChannel(guild);
      if (announceChannel) {
        const defaultModel = this.config.whisper_model || "base";

        const modelSelectRow = new ActionRowBuilder().addComponents(
          new StringSelectMenuBuilder()
            .setCustomId(`post_process_model:${sessionName}`)
            .setPlaceholder(`Model: ${defaultModel} (click to change)`)
            .addOptions([
              { label: "tiny",   description: "Fastest, lowest accuracy",  value: "tiny"   },
              { label: "base",   description: "Fast, decent accuracy",      value: "base"   },
              { label: "small",  description: "Balanced",                   value: "small"  },
              { label: "medium", description: "Slower, higher accuracy",    value: "medium" },
              { label: "large",  description: "Slowest, best accuracy",     value: "large"  },
            ])
        );

        const startButtonRow = new ActionRowBuilder().addComponents(
          new ButtonBuilder()
            .setCustomId(`post_process:${sessionName}:${defaultModel}`)
            .setLabel(`▶ Start Processing (${defaultModel})`)
            .setStyle(ButtonStyle.Primary)
        );

        const content = auto
          ? `Recording automatically stopped (channel empty). Files saved to \`${relPath}\``
          : `Recording stopped — files saved to \`${relPath}\``;

        await announceChannel.send({ content, components: [modelSelectRow, startButtonRow] });
      }
    }

    this.logger.info(`Recording session finished: ${sessionName}`);

    // Reset state
    this.users = new Map();
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

    // Mid-session join — log internally and notify panel if callback is registered
    if (
      newState.channelId === this.recordingChannel?.id &&
      oldState.channelId !== newState.channelId
    ) {
      this.logger.info(
        `${member.user.username} joined mid-session — now recording them.`
      );
      if (this.onMidSessionJoin) {
        this.onMidSessionJoin(member.user.username);
      }
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
   * Ensure a per-participant tracking entry (and sub-folder) exists for a user.
   * Called the first time a user speaks; their sub-folder is created lazily so
   * participants who never talk leave no empty folders behind.
   *
   * @param {string} userId
   * @param {import('discord.js').Guild} guild
   * @returns {{username: string, dir: string, snippetCount: number, active: Set}}
   */
  _ensureUser(userId, guild) {
    let entry = this.users.get(userId);
    if (entry) return entry;

    const member = guild.members.cache.get(userId);
    const username = member ? member.user.username : userId;
    const dir = path.join(this.sessionDir, sanitiseName(username));
    try {
      fs.mkdirSync(dir, { recursive: true });
    } catch (err) {
      this.logger.error(`Could not create folder for ${username}: ${err.message}`);
    }

    entry = { username, dir, snippetCount: 0, active: new Set() };
    this.users.set(userId, entry);
    this.logger.info(`Now recording ${username} (${userId}) -> ${dir}`);
    return entry;
  }

  /**
   * Open a fresh per-utterance capture for a user, if one isn't already running.
   *
   * Each utterance is subscribed with EndBehaviorType.AfterSilence: the stream
   * stays open until the user has been silent for `_silenceMs`, then closes and
   * the collected PCM is written as a timestamped snippet WAV. The snippet
   * filename is the zero-padded session-relative start offset in milliseconds,
   * so files sort chronologically and the offset is the single source of truth
   * for the snippet's place on the session timeline.
   *
   * @param {string} userId
   * @param {{dir: string, snippetCount: number, active: Set, username: string}} entry
   */
  _startUtterance(userId, entry) {
    if (!this.connection || !this.isRecording) return;

    const receiver = this.connection.receiver;
    // The receiver tracks one active subscription per user. While an utterance
    // is mid-capture, repeated `speaking start` events are ignored here so we
    // don't split a single utterance across multiple files.
    if (receiver.subscriptions.has(userId)) return;

    const offsetMs = Date.now() - this.startTime.getTime();

    const opusStream = receiver.subscribe(userId, {
      end: { behavior: EndBehaviorType.AfterSilence, duration: this._silenceMs },
    });
    const decoder = new prism.opus.Decoder({
      rate: SAMPLE_RATE,
      channels: CHANNELS,
      frameSize: OPUS_FRAME_SIZE,
    });

    const chunks = [];
    let finalized = false;

    const utterance = { opusStream, decoder, offsetMs };

    const finalize = () => {
      if (finalized) return;
      finalized = true;
      entry.active.delete(utterance);

      try {
        let pcm = Buffer.concat(chunks);
        if (pcm.length === 0) return; // nothing decodable — drop it

        // Duration of captured speech, measured before tail padding — used to
        // skip enqueuing sub-threshold blips for transcription.
        const speechMs =
          (pcm.length / (SAMPLE_RATE * CHANNELS * BYTES_PER_SAMPLE)) * 1000;

        // Append a short tail of silence so Whisper doesn't clip the last word.
        if (this._tailPadMs > 0) {
          const padBytes =
            Math.floor(
              ((this._tailPadMs / 1000) * SAMPLE_RATE * CHANNELS * BYTES_PER_SAMPLE) /
                BLOCK_ALIGN
            ) * BLOCK_ALIGN;
          if (padBytes > 0) pcm = Buffer.concat([pcm, Buffer.alloc(padBytes, 0)]);
        }

        const wavPath = path.join(
          entry.dir,
          `${String(offsetMs).padStart(10, "0")}.wav`
        );
        fs.writeFileSync(wavPath, buildWav(pcm));
        entry.snippetCount += 1;

        // Live transcription: hand the finished snippet to the warm-model
        // service (fire-and-forget — must not block capture).
        if (this._autoTranscribe && speechMs >= this._transcribeMinMs) {
          this._enqueueSnippet(offsetMs, wavPath, entry);
        }
      } catch (err) {
        this.logger.error(`Failed to save snippet for ${entry.username}: ${err.message}`);
      }
    };

    decoder.on("data", (chunk) => chunks.push(chunk));
    decoder.on("end", finalize);
    decoder.on("error", (err) => {
      this.logger.warn(`Opus decoder error for ${entry.username}: ${err.message}`);
    });
    opusStream.on("error", (err) => {
      this.logger.warn(`Opus stream error for ${entry.username}: ${err.message}`);
    });

    // Manual flush used by stop(): stop feeding the decoder, flush it so 'end'
    // fires promptly, and write whatever PCM we have. The timer is only a
    // safety net in case the decoder never emits 'end'.
    utterance.close = () =>
      new Promise((resolve) => {
        if (finalized) return resolve();
        const done = () => {
          finalize();
          resolve();
        };
        const timer = setTimeout(done, STREAM_FINISH_TIMEOUT_MS);
        decoder.once("end", () => {
          clearTimeout(timer);
          done();
        });
        decoder.once("close", () => {
          clearTimeout(timer);
          done();
        });
        opusStream.unpipe(decoder);
        if (!opusStream.destroyed) opusStream.destroy();
        decoder.end();
      });

    entry.active.add(utterance);
    opusStream.pipe(decoder);
  }

  /**
   * Fire-and-forget POST of a finished snippet to the live transcription service
   * (py-transcribe). Failures (e.g. the service isn't running) are logged and
   * ignored — the snippet WAV stays on disk and the batch fallback in
   * transcribe.py will transcribe it at post-process time.
   *
   * @param {number} offsetMs   Session-relative start offset of the snippet
   * @param {string} wavPath    Path to the written snippet WAV
   * @param {{dir: string, username: string}} entry
   */
  _enqueueSnippet(offsetMs, wavPath, entry) {
    const url = `http://localhost:${this._transcribePort}/api/transcribe`;
    const body = JSON.stringify({
      session: this.sessionName,
      username: path.basename(entry.dir),
      offset_ms: offsetMs,
      wav_path: path.resolve(wavPath),
    });
    fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body,
      signal: AbortSignal.timeout(5000),
    })
      .then((res) => {
        if (!res.ok) {
          this.logger.warn(
            `Transcription enqueue returned HTTP ${res.status} for ${path.basename(wavPath)}`
          );
        }
      })
      .catch((err) => {
        this.logger.warn(
          `Could not enqueue snippet for live transcription (is py-transcribe running?): ${err.message}`
        );
      });
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

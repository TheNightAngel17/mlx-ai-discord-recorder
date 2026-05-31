/**
 * postProcessor.js — Triggers the py-process job-runner service and reports results.
 *
 * The bot no longer spawns Python directly. It POSTs a job to the py-process
 * FastAPI service (transcribe → merge → vectorize → summarize), polls the job
 * for progress, and reads the result files (combined transcript, summary) from
 * the shared recordings volume to post back to Discord.
 *
 * Exports a PostProcessor class with:
 *   postProcess(interaction, sessionName, model, language, silent, generateSummary)
 *   postProcessFromButton(interaction, sessionName, model, language, generateSummary)
 *   mergeAudio(interaction, sessionName)
 *   vectorize(interaction, sessionName, force)
 *   status / mergeAudioStatus / vectorizeStatus
 */

"use strict";

const { AttachmentBuilder } = require("discord.js");
const path = require("path");
const fs = require("fs");

const POLL_INTERVAL_MS = 3000; // how often to poll job status
const JOB_TIMEOUT_MS = 60 * 60 * 1000; // 1h safety cap on a single job
const HTTP_TIMEOUT_MS = 15000; // per-request timeout for service calls

/**
 * Count per-user snippet WAVs across a session's participant sub-folders.
 *
 * Recordings live as timestamped snippets in <session>/<username>/*.wav, so a
 * flat readdir of the session folder no longer sees them. Reserved
 * underscore/dot-prefixed entries (session metadata, mix outputs) are ignored.
 *
 * @param {string} sessionDir
 * @returns {number}
 */
function countSnippetWavs(sessionDir) {
  let count = 0;
  let entries;
  try {
    entries = fs.readdirSync(sessionDir, { withFileTypes: true });
  } catch {
    return 0;
  }
  for (const entry of entries) {
    if (!entry.isDirectory()) continue;
    if (entry.name.startsWith("_") || entry.name.startsWith(".")) continue;
    try {
      for (const f of fs.readdirSync(path.join(sessionDir, entry.name))) {
        if (f.endsWith(".wav")) count += 1;
      }
    } catch {
      /* unreadable sub-folder — skip */
    }
  }
  return count;
}

class PostProcessor {
  /**
   * @param {object} config   Parsed config.yaml object
   * @param {object} logger   Logger with .info / .warn / .error methods
   */
  constructor(config, logger) {
    this.config = config;
    this.logger = logger;
    this.isProcessing = false;
    this.currentSession = null;
    this.isMerging = false;
    this.mergeSession = null;
    this.isVectorizing = false;
    this.vectorizeSession = null;

    const port = Number(config.process_api_port) || 8300;
    // Connect URL: localhost for bare-metal, service name in Docker (config-set).
    this._baseUrl = (config.process_api_url || `http://localhost:${port}`).replace(/\/$/, "");
  }

  // ---------------------------------------------------------------------------
  // Service client helpers
  // ---------------------------------------------------------------------------

  /**
   * Start a job on the py-process service. Resolves to the job id.
   * Throws if the service is unreachable or rejects the request.
   *
   * @param {"process"|"merge"|"vectorize"} kind
   * @param {object} body
   * @returns {Promise<string>}
   */
  async _startJob(kind, body) {
    const res = await fetch(`${this._baseUrl}/api/${kind}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      signal: AbortSignal.timeout(HTTP_TIMEOUT_MS),
    });
    if (!res.ok) {
      let detail = `HTTP ${res.status}`;
      try {
        const j = await res.json();
        detail = j.detail || detail;
      } catch {
        /* non-JSON error body */
      }
      throw new Error(detail);
    }
    const data = await res.json();
    return data.job_id;
  }

  /**
   * Poll a job until it reaches a terminal state. Calls onStep(job) whenever the
   * reported step changes, so the caller can surface progress in Discord.
   * Transient poll failures are tolerated; resolves with the final job object.
   *
   * @param {string} jobId
   * @param {(job: object) => Promise<void>} [onStep]
   * @returns {Promise<object>}
   */
  async _pollJob(jobId, onStep) {
    const deadline = Date.now() + JOB_TIMEOUT_MS;
    let lastStep = null;

    while (Date.now() < deadline) {
      let job = null;
      try {
        const res = await fetch(`${this._baseUrl}/api/jobs/${jobId}`, {
          signal: AbortSignal.timeout(HTTP_TIMEOUT_MS),
        });
        if (res.ok) job = await res.json();
      } catch {
        /* transient — keep polling */
      }

      if (job) {
        if (onStep && job.step && job.step !== lastStep) {
          lastStep = job.step;
          try {
            await onStep(job);
          } catch {
            /* ignore Discord edit hiccups */
          }
        }
        if (job.status === "done" || job.status === "failed") return job;
      }

      await new Promise((r) => setTimeout(r, POLL_INTERVAL_MS));
    }
    throw new Error("Timed out waiting for the job to finish.");
  }

  /** Extract a Discord-safe error blurb from a failed job. */
  _jobErrorText(job) {
    const tail = (job.log_tail || []).slice(-15).join("\n");
    return (tail || job.error || "Unknown error").slice(0, 1500);
  }

  /** Read a truncated preview of the combined transcript from the shared volume. */
  _readCombinedPreview(sessionDir) {
    const combinedPath = path.join(sessionDir, "_combined_transcript.txt");
    if (!fs.existsSync(combinedPath)) return "";
    const content = fs.readFileSync(combinedPath, "utf-8");
    return content.length > 1800 ? content.slice(0, 1800) + "\n… (truncated)" : content;
  }

  /** "py-process unreachable" hint shown when a job can't be started. */
  _serviceHint() {
    const port = this.config.process_api_port || 8300;
    return `> Is the py-process service running? \`cd py-process && uvicorn app:app --host 0.0.0.0 --port ${port}\``;
  }

  // ---------------------------------------------------------------------------
  // process (full pipeline)
  // ---------------------------------------------------------------------------

  /**
   * Run the full post-processing pipeline for a session.
   *
   * @param {import('discord.js').ChatInputCommandInteraction} interaction
   * @param {string} sessionName
   * @param {string} model
   * @param {string|null} language
   * @param {boolean} [silent=false]
   * @param {boolean} [generateSummary=true]
   */
  async postProcess(interaction, sessionName, model, language, silent = false, generateSummary = true) {
    if (this.isProcessing) {
      await interaction.reply({
        content: `Post-processing is already in progress for session \`${this.currentSession}\`. Please wait for it to finish.`,
        ephemeral: true,
      });
      return;
    }

    const outputDir = this.config.output_directory || "./recordings";
    const sessionDir = path.resolve(outputDir, sessionName);

    if (!fs.existsSync(sessionDir)) {
      await interaction.reply({
        content: `Session directory not found: \`${sessionName}\``,
        ephemeral: true,
      });
      return;
    }

    const snippetCount = countSnippetWavs(sessionDir);
    if (snippetCount === 0) {
      await interaction.reply({
        content: `No recordings found in session \`${sessionName}\``,
        ephemeral: true,
      });
      return;
    }

    await interaction.deferReply();

    this.isProcessing = true;
    this.currentSession = sessionName;
    this.logger.info(
      `Post-processing started: session=${sessionName}, model=${model}, language=${language || "auto"}, snippets=${snippetCount}`
    );

    const announceChannel = await this._getAnnounceChannel(interaction.guild);
    if (announceChannel && !silent) {
      await announceChannel.send(
        `Post-processing started for session \`${sessionName}\` (model: ${model}, language: ${language || "auto-detect"}, ${snippetCount} snippet(s))`
      );
    }

    try {
      const jobId = await this._startJob("process", {
        session: sessionName,
        model,
        language: language || null,
        summarize: generateSummary,
      });

      const job = await this._pollJob(jobId, async (j) => {
        await interaction.editReply({ content: `⏳ Post-processing \`${sessionName}\` — ${j.step}…` });
      });

      if (job.status === "done") {
        this.logger.info(`Post-processing finished successfully for session ${sessionName}`);

        const preview = this._readCombinedPreview(sessionDir);
        const replyContent = [
          `✅ Post-processing complete for session \`${sessionName}\``,
          `Files saved to \`${sessionDir}\``,
        ];
        if (preview) replyContent.push("", "```", preview, "```");
        await interaction.editReply({ content: replyContent.join("\n") });

        if (announceChannel && !silent) {
          await announceChannel.send(
            `✅ Post-processing complete for session \`${sessionName}\` — files saved to \`${sessionDir}\``
          );
          const summaryPath = path.join(sessionDir, "_session_summary.md");
          if (fs.existsSync(summaryPath)) {
            await announceChannel.send({
              files: [new AttachmentBuilder(summaryPath, { name: `${sessionName}_summary.md` })],
            });
          }
        }
      } else {
        this.logger.error(`Post-processing failed for session ${sessionName}: ${this._jobErrorText(job)}`);
        await interaction.editReply({
          content: `❌ Post-processing failed for session \`${sessionName}\`.\n\`\`\`\n${this._jobErrorText(job)}\n\`\`\``,
        });
        if (announceChannel && !silent) {
          await announceChannel.send(`❌ Post-processing failed for session \`${sessionName}\``);
        }
      }
    } catch (err) {
      this.logger.error(`Could not run post-processing for ${sessionName}: ${err.message}`);
      await interaction.editReply({
        content: `❌ Could not run post-processing: ${err.message}\n${this._serviceHint()}`,
      });
    } finally {
      this.isProcessing = false;
      this.currentSession = null;
    }
  }

  /**
   * Run post-processing triggered by a button click (already acknowledged).
   *
   * @param {import('discord.js').ButtonInteraction} interaction
   * @param {string} sessionName
   * @param {string} model
   * @param {string|null} language
   * @param {boolean} [generateSummary=true]
   */
  async postProcessFromButton(interaction, sessionName, model, language, generateSummary = true) {
    if (this.isProcessing) {
      await interaction.followUp({
        content: `⚠️ Post-processing is already in progress for session \`${this.currentSession}\`. Please wait for it to finish.`,
        ephemeral: true,
      });
      return;
    }

    const outputDir = this.config.output_directory || "./recordings";
    const sessionDir = path.resolve(outputDir, sessionName);

    if (!fs.existsSync(sessionDir)) {
      await interaction.followUp({
        content: `❌ Session directory not found: \`${sessionName}\``,
        ephemeral: true,
      });
      return;
    }

    const snippetCount = countSnippetWavs(sessionDir);
    if (snippetCount === 0) {
      await interaction.followUp({
        content: `❌ No recordings found in session \`${sessionName}\``,
        ephemeral: true,
      });
      return;
    }

    const startMsg = await interaction.followUp({
      content: `⏳ Post-processing started for session \`${sessionName}\` (model: ${model}, language: ${language || "auto-detect"}, ${snippetCount} snippet(s))…`,
    });

    this.isProcessing = true;
    this.currentSession = sessionName;
    this.logger.info(
      `Post-processing started (button): session=${sessionName}, model=${model}, language=${language || "auto"}, snippets=${snippetCount}`
    );

    const announceChannel = await this._getAnnounceChannel(interaction.guild);

    try {
      const jobId = await this._startJob("process", {
        session: sessionName,
        model,
        language: language || null,
        summarize: generateSummary,
      });

      const job = await this._pollJob(jobId, async (j) => {
        await startMsg.edit({ content: `⏳ Post-processing \`${sessionName}\` — ${j.step}…` });
      });

      if (job.status === "done") {
        this.logger.info(`Post-processing finished successfully for session ${sessionName}`);

        const preview = this._readCombinedPreview(sessionDir);
        const replyParts = [
          `✅ Post-processing complete for session \`${sessionName}\``,
          `Files saved to \`${sessionDir}\``,
        ];
        if (preview) replyParts.push("", "```", preview, "```");
        await startMsg.edit({ content: replyParts.join("\n") });

        if (announceChannel) {
          const summaryPath = path.join(sessionDir, "_session_summary.md");
          if (fs.existsSync(summaryPath)) {
            await announceChannel.send({
              files: [new AttachmentBuilder(summaryPath, { name: `${sessionName}_summary.md` })],
            });
          }
        }
      } else {
        this.logger.error(`Post-processing failed for session ${sessionName}: ${this._jobErrorText(job)}`);
        await startMsg.edit({
          content: `❌ Post-processing failed for session \`${sessionName}\`.\n\`\`\`\n${this._jobErrorText(job)}\n\`\`\``,
        });
      }
    } catch (err) {
      this.logger.error(`Could not run post-processing for ${sessionName}: ${err.message}`);
      await startMsg.edit({
        content: `❌ Could not run post-processing: ${err.message}\n${this._serviceHint()}`,
      });
    } finally {
      this.isProcessing = false;
      this.currentSession = null;
    }
  }

  /**
   * Reply with current post-processing status.
   *
   * @param {import('discord.js').ChatInputCommandInteraction} interaction
   */
  async status(interaction) {
    if (!this.isProcessing) {
      await interaction.reply({ content: "No post-processing in progress.", ephemeral: true });
      return;
    }
    await interaction.reply({
      content: `Post-processing in progress for session \`${this.currentSession}\`…`,
      ephemeral: true,
    });
  }

  // ---------------------------------------------------------------------------
  // merge-audio
  // ---------------------------------------------------------------------------

  /**
   * Trigger an audio merge job for a session.
   *
   * @param {import('discord.js').ChatInputCommandInteraction} interaction
   * @param {string} sessionName
   */
  async mergeAudio(interaction, sessionName) {
    if (this.isMerging) {
      await interaction.reply({
        content: `⚠️ Audio merge already in progress for session \`${this.mergeSession}\`. Please wait.`,
        ephemeral: true,
      });
      return;
    }

    const outputDir = this.config.output_directory || "./recordings";
    const sessionDir = path.resolve(outputDir, sessionName);

    if (!fs.existsSync(sessionDir)) {
      await interaction.reply({
        content: `❌ Session directory not found: \`${sessionName}\``,
        ephemeral: true,
      });
      return;
    }

    const snippetCount = countSnippetWavs(sessionDir);
    if (snippetCount === 0) {
      await interaction.reply({
        content: `❌ No per-user snippet recordings found in session \`${sessionName}\``,
        ephemeral: true,
      });
      return;
    }

    await interaction.deferReply();

    this.isMerging = true;
    this.mergeSession = sessionName;
    this.logger.info(`Audio merge started: session=${sessionName}, snippets=${snippetCount}`);

    try {
      const jobId = await this._startJob("merge", { session: sessionName });
      const job = await this._pollJob(jobId, async (j) => {
        await interaction.editReply({ content: `⏳ Merging audio for \`${sessionName}\` — ${j.step}…` });
      });

      if (job.status === "done") {
        this.logger.info(`Audio merge finished for session ${sessionName}`);
        const mp3Path = path.join(sessionDir, "_session_mix.mp3");
        await interaction.editReply({
          content: [`✅ Audio merge complete for session \`${sessionName}\``, `Output: \`${mp3Path}\``].join("\n"),
        });
      } else {
        this.logger.error(`Audio merge failed for session ${sessionName}: ${this._jobErrorText(job)}`);
        await interaction.editReply({
          content: `❌ Audio merge failed for session \`${sessionName}\`.\n\`\`\`\n${this._jobErrorText(job)}\n\`\`\``,
        });
      }
    } catch (err) {
      this.logger.error(`Could not run audio merge for ${sessionName}: ${err.message}`);
      await interaction.editReply({
        content: `❌ Could not run audio merge: ${err.message}\n${this._serviceHint()}`,
      });
    } finally {
      this.isMerging = false;
      this.mergeSession = null;
    }
  }

  /**
   * Report current merge-audio status.
   *
   * @param {import('discord.js').ChatInputCommandInteraction} interaction
   */
  async mergeAudioStatus(interaction) {
    if (!this.isMerging) {
      await interaction.reply({ content: "No audio merge in progress.", ephemeral: true });
      return;
    }
    await interaction.reply({
      content: `⏳ Audio merge in progress for session \`${this.mergeSession}\`…`,
      ephemeral: true,
    });
  }

  // ---------------------------------------------------------------------------
  // vectorize
  // ---------------------------------------------------------------------------

  /**
   * Trigger a vectorization job for a session (or all sessions).
   *
   * @param {import('discord.js').ChatInputCommandInteraction} interaction
   * @param {string} sessionName  Session folder name, or the literal string "all"
   * @param {boolean} force       Re-index already-vectorized sessions
   */
  async vectorize(interaction, sessionName, force) {
    if (this.isVectorizing) {
      await interaction.reply({
        content: `⚠️ Vectorization already in progress for \`${this.vectorizeSession}\`. Please wait.`,
        ephemeral: true,
      });
      return;
    }

    const isAll = sessionName.toLowerCase() === "all";

    if (!isAll) {
      const outputDir = this.config.output_directory || "./recordings";
      const sessionDir = path.resolve(outputDir, sessionName);
      if (!fs.existsSync(sessionDir)) {
        await interaction.reply({
          content: `❌ Session directory not found: \`${sessionName}\``,
          ephemeral: true,
        });
        return;
      }
      const combinedPath = path.join(sessionDir, "_combined_transcript.txt");
      if (!fs.existsSync(combinedPath)) {
        await interaction.reply({
          content: `❌ No \`_combined_transcript.txt\` found in \`${sessionName}\`. Run transcription first.`,
          ephemeral: true,
        });
        return;
      }
    }

    await interaction.deferReply();

    this.isVectorizing = true;
    this.vectorizeSession = isAll ? "(all sessions)" : sessionName;
    this.logger.info(`Vectorization started: target=${this.vectorizeSession}, force=${force}`);

    try {
      const jobId = await this._startJob("vectorize", { session: sessionName, force: Boolean(force) });
      const job = await this._pollJob(jobId, async (j) => {
        await interaction.editReply({ content: `⏳ Vectorizing ${this.vectorizeSession} — ${j.step}…` });
      });

      if (job.status === "done") {
        this.logger.info(`Vectorization finished for ${isAll ? "all sessions" : sessionName}`);
        const target = isAll ? "all sessions" : `session \`${sessionName}\``;
        await interaction.editReply({
          content: `✅ Vectorization complete for ${target}.\nData stored in \`${this.config.vector_db_directory || "./vectordb"}\``,
        });
      } else {
        this.logger.error(`Vectorization failed: ${this._jobErrorText(job)}`);
        await interaction.editReply({
          content: `❌ Vectorization failed.\n\`\`\`\n${this._jobErrorText(job)}\n\`\`\``,
        });
      }
    } catch (err) {
      this.logger.error(`Could not run vectorization: ${err.message}`);
      await interaction.editReply({
        content: `❌ Could not run vectorization: ${err.message}\n${this._serviceHint()}`,
      });
    } finally {
      this.isVectorizing = false;
      this.vectorizeSession = null;
    }
  }

  /**
   * Report current vectorization status.
   *
   * @param {import('discord.js').ChatInputCommandInteraction} interaction
   */
  async vectorizeStatus(interaction) {
    if (!this.isVectorizing) {
      await interaction.reply({ content: "No vectorization in progress.", ephemeral: true });
      return;
    }
    await interaction.reply({
      content: `⏳ Vectorization in progress for \`${this.vectorizeSession}\`…`,
      ephemeral: true,
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

module.exports = { PostProcessor };

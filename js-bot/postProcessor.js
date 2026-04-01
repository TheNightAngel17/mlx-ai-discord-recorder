/**
 * postProcessor.js — Spawns the Python post-processing orchestrator and reports results.
 *
 * Exports a PostProcessor class with:
 *   postProcess(interaction, sessionName, model, language) — run post-processing
 */

"use strict";

const { spawn } = require("child_process");
const path = require("path");
const fs = require("fs");

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
    this.currentProcess = null;
    this.isMerging = false;
    this.mergeSession = null;
    this.isVectorizing = false;
    this.vectorizeSession = null;
  }

  /**
   * Run the Python post-processing script for a given session.
   *
   * @param {import('discord.js').ChatInputCommandInteraction} interaction
   * @param {string} sessionName
   * @param {string} model        Whisper model size (tiny|base|small|medium|large)
   * @param {string|null} language Language code or null for auto-detect
   * @param {boolean} [silent=false]  When true, suppresses announce-channel messages.
   *   Pass true when called from the session panel to avoid duplicate chat messages.
   */
  async postProcess(interaction, sessionName, model, language, silent = false) {
    // Guard: only one post-processing run at a time
    if (this.isProcessing) {
      await interaction.reply({
        content: `Post-processing is already in progress for session \`${this.currentSession}\`. Please wait for it to finish.`,
        ephemeral: true,
      });
      return;
    }

    // Validate session directory exists
    const outputDir = this.config.output_directory || "./recordings";
    const sessionDir = path.resolve(outputDir, sessionName);

    if (!fs.existsSync(sessionDir)) {
      await interaction.reply({
        content: `Session directory not found: \`${sessionName}\``,
        ephemeral: true,
      });
      return;
    }

    // Check for WAV files
    const wavFiles = fs
      .readdirSync(sessionDir)
      .filter((f) => f.endsWith(".wav"));
    if (wavFiles.length === 0) {
      await interaction.reply({
        content: `No .wav files found in session \`${sessionName}\``,
        ephemeral: true,
      });
      return;
    }

    // Defer — post-processing can take a while
    await interaction.deferReply();

    this.isProcessing = true;
    this.currentSession = sessionName;

    this.logger.info(
      `Post-processing started: session=${sessionName}, model=${model}, language=${language || "auto"}, files=${wavFiles.length}`
    );

    // Resolve paths
    const repoRoot = path.resolve(__dirname, "..");
    const scriptPath = path.join(repoRoot, "py-process", "process.py");

    // Find the Python executable — prefer the venv, fall back to system python
    const venvPython = path.join(repoRoot, ".venv", "Scripts", "python.exe");
    const venvPythonUnix = path.join(repoRoot, ".venv", "bin", "python");
    let pythonExe = "python";
    if (fs.existsSync(venvPython)) {
      pythonExe = venvPython;
    } else if (fs.existsSync(venvPythonUnix)) {
      pythonExe = venvPythonUnix;
    }

    // Build args
    const args = [scriptPath, sessionName, "--model", model];
    if (language) {
      args.push("--language", language);
    }

    // Announce start (skipped when called silently from the session panel)
    const announceChannel = await this._getAnnounceChannel(interaction.guild);
    if (announceChannel && !silent) {
      await announceChannel.send(
        `Post-processing started for session \`${sessionName}\` (model: ${model}, language: ${language || "auto-detect"}, ${wavFiles.length} file(s))`
      );
    }

    return new Promise((resolve) => {
      const proc = spawn(pythonExe, args, { cwd: repoRoot });
      this.currentProcess = proc;

      let stdout = "";
      let stderr = "";

      proc.stdout.on("data", (data) => {
        const line = data.toString();
        stdout += line;
        // Log each line for real-time visibility in the terminal
        for (const l of line.split("\n").filter((s) => s.trim())) {
          this.logger.info(`[process] ${l}`);
        }
      });

      proc.stderr.on("data", (data) => {
        stderr += data.toString();
      });

      proc.on("close", async (code) => {
        this.isProcessing = false;
        this.currentSession = null;
        this.currentProcess = null;

        if (code === 0) {
          this.logger.info(
            `Post-processing finished successfully for session ${sessionName}`
          );

          // Read the combined transcript (if it exists) for the reply
          const combinedPath = path.join(
            sessionDir,
            "_combined_transcript.txt"
          );
          let preview = "";
          if (fs.existsSync(combinedPath)) {
            const content = fs.readFileSync(combinedPath, "utf-8");
            // Show first ~1800 chars to stay within Discord's 2000 char limit
            if (content.length > 1800) {
              preview = content.slice(0, 1800) + "\n… (truncated)";
            } else {
              preview = content;
            }
          }

          const replyContent = [
            `✅ Post-processing complete for session \`${sessionName}\``,
            `Files saved to \`${sessionDir}\``,
          ];

          if (preview) {
            replyContent.push("", "```", preview, "```");
          }

          await interaction.editReply({
            content: replyContent.join("\n"),
          });

          if (announceChannel && !silent) {
            await announceChannel.send(
              `✅ Post-processing complete for session \`${sessionName}\` — files saved to \`${sessionDir}\``
            );
          }
        } else {
          this.logger.error(
            `Post-processing failed for session ${sessionName} (exit code ${code}): ${stderr}`
          );

          await interaction.editReply({
            content: `❌ Post-processing failed for session \`${sessionName}\`.\n\`\`\`\n${stderr.slice(0, 1500) || "Unknown error"}\n\`\`\``,
          });

          if (announceChannel && !silent) {
            await announceChannel.send(
              `❌ Post-processing failed for session \`${sessionName}\``
            );
          }
        }

        resolve();
      });

      proc.on("error", async (err) => {
        this.isProcessing = false;
        this.currentSession = null;
        this.currentProcess = null;

        this.logger.error(
          `Failed to spawn post-processing process: ${err.message}`
        );

        await interaction.editReply({
          content: `❌ Failed to start post-processing: ${err.message}`,
        });

        resolve();
      });
    });
  }

  /**
   * Run post-processing triggered by a button click.
   * The button has already been acknowledged (update) by the caller.
   *
   * @param {import('discord.js').ButtonInteraction} interaction
   * @param {string} sessionName
   * @param {string} model
   * @param {string|null} language
   */
  async postProcessFromButton(interaction, sessionName, model, language) {
    // Guard: only one post-processing run at a time
    if (this.isProcessing) {
      await interaction.followUp({
        content: `⚠️ Post-processing is already in progress for session \`${this.currentSession}\`. Please wait for it to finish.`,
        ephemeral: true,
      });
      return;
    }

    // Validate session directory exists
    const outputDir = this.config.output_directory || "./recordings";
    const sessionDir = path.resolve(outputDir, sessionName);

    if (!fs.existsSync(sessionDir)) {
      await interaction.followUp({
        content: `❌ Session directory not found: \`${sessionName}\``,
        ephemeral: true,
      });
      return;
    }

    // Check for WAV files
    const wavFiles = fs
      .readdirSync(sessionDir)
      .filter((f) => f.endsWith(".wav"));
    if (wavFiles.length === 0) {
      await interaction.followUp({
        content: `❌ No .wav files found in session \`${sessionName}\``,
        ephemeral: true,
      });
      return;
    }

    // Send a "starting" message to the channel so progress is visible
    const startMsg = await interaction.followUp({
      content: `⏳ Post-processing started for session \`${sessionName}\` (model: ${model}, language: ${language || "auto-detect"}, ${wavFiles.length} file(s))…`,
    });

    this.isProcessing = true;
    this.currentSession = sessionName;

    this.logger.info(
      `Post-processing started (button): session=${sessionName}, model=${model}, language=${language || "auto"}, files=${wavFiles.length}`
    );

    // Resolve paths
    const repoRoot = path.resolve(__dirname, "..");
    const scriptPath = path.join(repoRoot, "py-process", "process.py");

    const venvPython = path.join(repoRoot, ".venv", "Scripts", "python.exe");
    const venvPythonUnix = path.join(repoRoot, ".venv", "bin", "python");
    let pythonExe = "python";
    if (fs.existsSync(venvPython)) {
      pythonExe = venvPython;
    } else if (fs.existsSync(venvPythonUnix)) {
      pythonExe = venvPythonUnix;
    }

    const args = [scriptPath, sessionName, "--model", model];
    if (language) {
      args.push("--language", language);
    }

    return new Promise((resolve) => {
      const proc = spawn(pythonExe, args, { cwd: repoRoot });
      this.currentProcess = proc;

      let stdout = "";
      let stderr = "";

      proc.stdout.on("data", (data) => {
        const line = data.toString();
        stdout += line;
        for (const l of line.split("\n").filter((s) => s.trim())) {
          this.logger.info(`[process] ${l}`);
        }
      });

      proc.stderr.on("data", (data) => {
        stderr += data.toString();
      });

      proc.on("close", async (code) => {
        this.isProcessing = false;
        this.currentSession = null;
        this.currentProcess = null;

        if (code === 0) {
          this.logger.info(
            `Post-processing finished successfully for session ${sessionName}`
          );

          const combinedPath = path.join(sessionDir, "_combined_transcript.txt");
          let preview = "";
          if (fs.existsSync(combinedPath)) {
            const content = fs.readFileSync(combinedPath, "utf-8");
            preview =
              content.length > 1800
                ? content.slice(0, 1800) + "\n… (truncated)"
                : content;
          }

          const replyParts = [
            `✅ Post-processing complete for session \`${sessionName}\``,
            `Files saved to \`${sessionDir}\``,
          ];
          if (preview) {
            replyParts.push("", "```", preview, "```");
          }

          await startMsg.edit({ content: replyParts.join("\n") });
        } else {
          this.logger.error(
            `Post-processing failed for session ${sessionName} (exit code ${code}): ${stderr}`
          );

          await startMsg.edit({
            content: `❌ Post-processing failed for session \`${sessionName}\`.\n\`\`\`\n${stderr.slice(0, 1500) || "Unknown error"}\n\`\`\``,
          });
        }

        resolve();
      });

      proc.on("error", async (err) => {
        this.isProcessing = false;
        this.currentSession = null;
        this.currentProcess = null;

        this.logger.error(
          `Failed to spawn post-processing process: ${err.message}`
        );

        await startMsg.edit({
          content: `❌ Failed to start post-processing: ${err.message}`,
        });

        resolve();
      });
    });
  }

  /**
   * Reply with current post-processing status.
   *
   * @param {import('discord.js').ChatInputCommandInteraction} interaction
   */
  async status(interaction) {
    if (!this.isProcessing) {
      await interaction.reply({
        content: "No post-processing in progress.",
        ephemeral: true,
      });
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
   * Spawn merge_audio.py for a given session.
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

    const wavFiles = fs
      .readdirSync(sessionDir)
      .filter((f) => f.endsWith(".wav") && !f.startsWith("_"));
    if (wavFiles.length === 0) {
      await interaction.reply({
        content: `❌ No per-user .wav files found in session \`${sessionName}\``,
        ephemeral: true,
      });
      return;
    }

    await interaction.deferReply();

    this.isMerging = true;
    this.mergeSession = sessionName;

    this.logger.info(`Audio merge started: session=${sessionName}, files=${wavFiles.length}`);

    const repoRoot = path.resolve(__dirname, "..");
    const scriptPath = path.join(repoRoot, "py-process", "merge_audio.py");

    const venvPython = path.join(repoRoot, ".venv", "Scripts", "python.exe");
    const venvPythonUnix = path.join(repoRoot, ".venv", "bin", "python");
    let pythonExe = "python";
    if (fs.existsSync(venvPython)) pythonExe = venvPython;
    else if (fs.existsSync(venvPythonUnix)) pythonExe = venvPythonUnix;

    return new Promise((resolve) => {
      const proc = spawn(pythonExe, [scriptPath, sessionName], { cwd: repoRoot });

      let stdout = "";
      let stderr = "";

      proc.stdout.on("data", (data) => {
        const line = data.toString();
        stdout += line;
        for (const l of line.split("\n").filter((s) => s.trim())) {
          this.logger.info(`[merge_audio] ${l}`);
        }
      });

      proc.stderr.on("data", (data) => {
        stderr += data.toString();
      });

      proc.on("close", async (code) => {
        this.isMerging = false;
        this.mergeSession = null;

        if (code === 0) {
          this.logger.info(`Audio merge finished for session ${sessionName}`);
          const mp3Path = path.join(sessionDir, "_session_mix.mp3");
          await interaction.editReply({
            content: [
              `✅ Audio merge complete for session \`${sessionName}\``,
              `Output: \`${mp3Path}\``,
            ].join("\n"),
          });
        } else {
          this.logger.error(`Audio merge failed for session ${sessionName} (exit ${code}): ${stderr}`);
          await interaction.editReply({
            content: `❌ Audio merge failed for session \`${sessionName}\`.\n\`\`\`\n${stderr.slice(0, 1500) || "Unknown error"}\n\`\`\``,
          });
        }
        resolve();
      });

      proc.on("error", async (err) => {
        this.isMerging = false;
        this.mergeSession = null;
        this.logger.error(`Failed to spawn merge_audio: ${err.message}`);
        await interaction.editReply({
          content: `❌ Failed to start audio merge: ${err.message}`,
        });
        resolve();
      });
    });
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
   * Spawn vectorize.py for a given session (or all sessions).
   *
   * @param {import('discord.js').ChatInputCommandInteraction} interaction
   * @param {string} sessionName  Session folder name, or the literal string "all"
   * @param {boolean} force       Pass --force to re-index already-vectorized sessions
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

    const repoRoot = path.resolve(__dirname, "..");
    const scriptPath = path.join(repoRoot, "py-process", "vectorize.py");

    const venvPython = path.join(repoRoot, ".venv", "Scripts", "python.exe");
    const venvPythonUnix = path.join(repoRoot, ".venv", "bin", "python");
    let pythonExe = "python";
    if (fs.existsSync(venvPython)) pythonExe = venvPython;
    else if (fs.existsSync(venvPythonUnix)) pythonExe = venvPythonUnix;

    const args = [scriptPath];
    if (isAll) {
      args.push("--all");
    } else {
      args.push(sessionName);
    }
    if (force) args.push("--force");

    return new Promise((resolve) => {
      const proc = spawn(pythonExe, args, { cwd: repoRoot });

      let stdout = "";
      let stderr = "";

      proc.stdout.on("data", (data) => {
        const line = data.toString();
        stdout += line;
        for (const l of line.split("\n").filter((s) => s.trim())) {
          this.logger.info(`[vectorize] ${l}`);
        }
      });

      proc.stderr.on("data", (data) => {
        stderr += data.toString();
      });

      proc.on("close", async (code) => {
        this.isVectorizing = false;
        this.vectorizeSession = null;

        if (code === 0) {
          this.logger.info(`Vectorization finished for ${isAll ? "all sessions" : sessionName}`);
          const target = isAll ? "all sessions" : `session \`${sessionName}\``;
          await interaction.editReply({
            content: `✅ Vectorization complete for ${target}.\nData stored in \`${this.config.vector_db_directory || "./vectordb"}\``,
          });
        } else {
          this.logger.error(`Vectorization failed (exit ${code}): ${stderr}`);
          await interaction.editReply({
            content: `❌ Vectorization failed.\n\`\`\`\n${stderr.slice(0, 1500) || "Unknown error"}\n\`\`\``,
          });
        }
        resolve();
      });

      proc.on("error", async (err) => {
        this.isVectorizing = false;
        this.vectorizeSession = null;
        this.logger.error(`Failed to spawn vectorize: ${err.message}`);
        await interaction.editReply({
          content: `❌ Failed to start vectorization: ${err.message}`,
        });
        resolve();
      });
    });
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

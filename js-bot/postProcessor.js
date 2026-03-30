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
  }

  /**
   * Run the Python post-processing script for a given session.
   *
   * @param {import('discord.js').ChatInputCommandInteraction} interaction
   * @param {string} sessionName
   * @param {string} model        Whisper model size (tiny|base|small|medium|large)
   * @param {string|null} language Language code or null for auto-detect
   */
  async postProcess(interaction, sessionName, model, language) {
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

    // Announce start
    const announceChannel = await this._getAnnounceChannel(interaction.guild);
    if (announceChannel) {
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

          if (announceChannel) {
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

          if (announceChannel) {
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

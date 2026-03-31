/**
 * queryHandler.js — Spawns py-query/query.py and returns the RAG answer to Discord.
 *
 * Exports a QueryHandler class with:
 *   query(interaction, question, sessionFilter, topK) — run a RAG query
 */

"use strict";

const { spawn } = require("child_process");
const path = require("path");
const fs = require("fs");

// Discord message character limit
const DISCORD_MAX_LENGTH = 2000;

class QueryHandler {
  /**
   * @param {object} config  Parsed config.yaml object
   * @param {object} logger  Logger with .info / .warn / .error methods
   */
  constructor(config, logger) {
    this.config = config;
    this.logger = logger;
  }

  /**
   * Run a RAG query via py-query/query.py and reply to the Discord interaction.
   *
   * @param {import('discord.js').ChatInputCommandInteraction} interaction
   * @param {string}      question      The question to ask
   * @param {string|null} sessionFilter Session name to restrict results to, or null for all
   * @param {number}      topK          Number of context chunks to retrieve
   * @param {boolean}     showSources   Whether to include source citations in the reply
   */
  async query(interaction, question, sessionFilter, topK, showSources) {
    // Defer immediately — LLM calls can take several seconds
    await interaction.deferReply();

    const repoRoot = path.resolve(__dirname, "..");
    const scriptPath = path.join(repoRoot, "py-query", "query.py");

    // Prefer the venv Python, fall back to system python
    const venvPython = path.join(repoRoot, ".venv", "Scripts", "python.exe");
    const venvPythonUnix = path.join(repoRoot, ".venv", "bin", "python");
    let pythonExe = "python";
    if (fs.existsSync(venvPython)) {
      pythonExe = venvPython;
    } else if (fs.existsSync(venvPythonUnix)) {
      pythonExe = venvPythonUnix;
    }

    // Build CLI args
    const args = [scriptPath, question, "--top-k", String(topK)];
    if (sessionFilter) {
      args.push("--session", sessionFilter);
    }
    if (showSources) {
      args.push("--show-sources");
    }

    this.logger.info(
      `RAG query started: question="${question}", session=${sessionFilter || "all"}, top_k=${topK}, show_sources=${showSources}`
    );

    return new Promise((resolve) => {
      const proc = spawn(pythonExe, args, {
        cwd: repoRoot,
        // Pass through the current environment so API keys from .env are available
        env: process.env,
      });

      let stdout = "";
      let stderr = "";

      proc.stdout.on("data", (data) => {
        stdout += data.toString();
      });

      proc.stderr.on("data", (data) => {
        stderr += data.toString();
        for (const line of data.toString().split("\n").filter((s) => s.trim())) {
          this.logger.error(`[query] ${line}`);
        }
      });

      proc.on("close", async (code) => {
        if (code === 0) {
          this.logger.info(`RAG query finished successfully: question="${question}"`);
          const reply = this._formatSuccess(stdout, question);
          await interaction.editReply({ content: reply });
        } else {
          this.logger.error(
            `RAG query failed (exit ${code}): ${stderr.trim()}`
          );
          const errText = stderr.trim() || stdout.trim() || "Unknown error.";
          await interaction.editReply({
            content: `❌ Query failed.\n\`\`\`\n${errText.slice(0, 1800)}\n\`\`\``,
          });
        }
        resolve();
      });

      proc.on("error", async (err) => {
        this.logger.error(`Failed to spawn query process: ${err.message}`);
        await interaction.editReply({
          content: `❌ Failed to start the query process: ${err.message}`,
        });
        resolve();
      });
    });
  }

  /**
   * Format the raw stdout from query.py into a Discord-safe reply.
   * query.py outputs a header block, a TIMINGS line, then the ANSWER section,
   * then optionally a SOURCES section. We parse those out so we can present
   * them cleanly in Discord.
   *
   * @param {string} raw      Full stdout from query.py
   * @param {string} question The original question
   * @returns {string}        Formatted reply string (≤ DISCORD_MAX_LENGTH chars)
   */
  _formatSuccess(raw, question) {
    const lines = raw.split("\n");

    // Extract the timings line (e.g. "Timings   : embed=0.21s  retrieval=0.04s  chat=3.87s  total=4.12s")
    const timingsLine = lines.find((l) => l.startsWith("Timings"));

    // Extract provider header lines (Embedding / Chat / Top-k / Session)
    const headerLines = lines.filter((l) =>
      /^(Embedding|Chat|Top-k|Session)\s*:/.test(l)
    );

    // Split on the separator lines ("====...====")
    const sections = raw.split(/={3,}/g).map((s) => s.trim()).filter(Boolean);

    // sections[0] = header block (before first ===)
    // sections[1] = "ANSWER"
    // sections[2] = answer text
    // sections[3] = "SOURCES (...)" (optional)
    // sections[4] = sources text (optional)

    let answer = "";
    let sources = "";

    for (let i = 0; i < sections.length; i++) {
      if (sections[i].trim() === "ANSWER") {
        answer = sections[i + 1]?.trim() || "";
      }
      if (/^SOURCES/.test(sections[i].trim())) {
        sources = sections[i + 1]?.trim() || "";
      }
    }

    const parts = [];

    // Question header
    parts.push(`**Q: ${question}**`);
    parts.push("");

    // Answer
    parts.push(answer || raw.trim());

    // Timings footer (if present)
    if (timingsLine) {
      parts.push("");
      parts.push(`-# ⏱ ${timingsLine.trim()}`);
    }

    // Sources (if present)
    if (sources) {
      parts.push("");
      parts.push("**Sources:**");
      parts.push(`\`\`\`\n${sources}\n\`\`\``);
    }

    let reply = parts.join("\n");

    // Truncate gracefully if still over Discord's limit
    if (reply.length > DISCORD_MAX_LENGTH) {
      reply = reply.slice(0, DISCORD_MAX_LENGTH - 20) + "\n… *(truncated)*";
    }

    return reply;
  }
}

module.exports = { QueryHandler };

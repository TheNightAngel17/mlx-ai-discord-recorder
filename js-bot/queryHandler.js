/**
 * queryHandler.js — Calls the py-query FastAPI service and returns the RAG answer to Discord.
 *
 * Exports a QueryHandler class with:
 *   query(interaction, question, sessionFilter, topK, showSources) — run a RAG query
 */

"use strict";

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
   * Run a RAG query via the py-query HTTP API and reply to the Discord interaction.
   *
   * The API must be running before queries are accepted. Start it with:
   *   cd py-query && uvicorn app:app --host 0.0.0.0 --port 8100
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

    const port = this.config.query_api_port || 8100;
    const url = `http://localhost:${port}/api/query`;

    const body = {
      question,
      top_k: topK,
      show_sources: showSources,
    };
    if (sessionFilter) {
      body.session = sessionFilter;
    }

    this.logger.info(
      `RAG query started: question="${question}", session=${sessionFilter || "all"}, top_k=${topK}, show_sources=${showSources}`
    );

    try {
      const response = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
        signal: AbortSignal.timeout(120_000), // 120s — allow time for slow LLMs
      });

      if (!response.ok) {
        let detail = `HTTP ${response.status}`;
        try {
          const errBody = await response.json();
          detail = errBody.detail || detail;
        } catch {
          // ignore JSON parse errors on error responses
        }
        this.logger.error(`RAG API returned ${response.status}: ${detail}`);
        await interaction.editReply({
          content: `❌ Query failed (${detail}).`,
        });
        return;
      }

      const data = await response.json();
      this.logger.info(`RAG query finished successfully: question="${question}"`);
      const reply = this._formatResponse(data, question);
      await interaction.editReply({ content: reply });
    } catch (err) {
      if (err.name === "TimeoutError" || err.name === "AbortError") {
        this.logger.error(`RAG API request timed out for question="${question}"`);
        await interaction.editReply({
          content: "❌ Query timed out. The LLM may be overloaded — try again shortly.",
        });
      } else {
        this.logger.error(`RAG API request failed: ${err.message}`);
        await interaction.editReply({
          content:
            "❌ Could not reach the RAG API. Is `py-query/app.py` running?\n" +
            `> Start it with: \`cd py-query && uvicorn app:app --host 0.0.0.0 --port ${port}\``,
        });
      }
    }
  }

  /**
   * Format the JSON response from the RAG API into a Discord-safe reply.
   *
   * @param {object} data     Parsed JSON response: { answer, sources, timings }
   * @param {string} question The original question
   * @returns {string}        Formatted reply string (≤ DISCORD_MAX_LENGTH chars)
   */
  _formatResponse(data, question) {
    const { answer, sources, timings } = data;
    const parts = [];

    // Question header
    parts.push(`**Q: ${question}**`);
    parts.push("");

    // Answer
    parts.push(answer || "No answer returned.");

    // Timings footer (if present)
    if (timings) {
      parts.push("");
      parts.push(
        `-# ⏱ embed=${timings.embed_s.toFixed(2)}s  ` +
          `retrieval=${timings.retrieval_s.toFixed(2)}s  ` +
          `chat=${timings.chat_s.toFixed(2)}s  ` +
          `total=${timings.total_s.toFixed(2)}s`
      );
    }

    // Sources (if requested and present)
    if (sources && sources.length > 0) {
      parts.push("");
      parts.push("**Sources:**");
      const srcLines = sources.map(
        (src, i) =>
          `[${i + 1}] ${src.session}  ${src.start} → ${src.end}` +
          `  (${src.speakers})  dist=${src.distance.toFixed(4)}`
      );
      parts.push("```\n" + srcLines.join("\n") + "\n```");
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

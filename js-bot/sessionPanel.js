/**
 * sessionPanel.js — Interactive "Session Control Panel" for the MLX AI Discord Recorder.
 *
 * Exports a SessionPanel class with:
 *   open(interaction)                  — /mlx-ai session: send the ephemeral panel
 *   handleChannelSelect(interaction)   — voice-channel select menu updated (IDLE guided step)
 *   handleSetNameButton(interaction)   — "Edit Session Details" button → show modal
 *   handleNameModal(interaction)       — modal submitted with session name
 *   handleRecordButton(interaction)    — "⏺ Start Recording" button pressed
 *   handleStopButton(interaction)      — "⏹ Stop Recording" button pressed
 *   handlePostProcessButton(interaction) — "⚙️ Post-Process" button pressed
 *   handleModelSelect(interaction)     — Whisper model dropdown changed (STOPPED state)
 *   handleSummaryToggle(interaction)   — "Generate Summary" toggle button pressed (STOPPED state)
 */

"use strict";

const fs = require("fs");
const path = require("path");

const {
  ActionRowBuilder,
  AttachmentBuilder,
  ButtonBuilder,
  ButtonStyle,
  ChannelSelectMenuBuilder,
  ChannelType,
  EmbedBuilder,
  ModalBuilder,
  StringSelectMenuBuilder,
  TextInputBuilder,
  TextInputStyle,
} = require("discord.js");

// Panel status constants
const STATUS = {
  IDLE: "idle",           // Panel just opened, nothing configured yet
  READY: "ready",         // Name + channel set, ready to record
  RECORDING: "recording", // Actively recording
  STOPPING: "stopping",   // Stop requested — saving WAV files
  STOPPED: "stopped",     // Recording saved, ready to post-process
  PROCESSING: "processing", // Post-processing running
  DONE: "done",           // Post-processing complete
};

/** Embed colour per status */
const COLORS = {
  [STATUS.IDLE]:       0x5865f2,
  [STATUS.READY]:      0xfee75c,
  [STATUS.RECORDING]:  0xed4245,
  [STATUS.STOPPING]:   0xe67e22,
  [STATUS.STOPPED]:    0x57f287,
  [STATUS.PROCESSING]: 0x5865f2,
  [STATUS.DONE]:       0x57f287,
};

/** Human-readable status description */
const STATUS_LABELS = {
  [STATUS.IDLE]:       "⚪ Set a session name and select a voice channel to begin.",
  [STATUS.READY]:      "🟡 Ready — press **⏺ Start Recording** to begin.",
  [STATUS.RECORDING]:  "🔴 Recording in progress…",
  [STATUS.STOPPING]:   "🟠 Stopping — saving WAV files…",
  [STATUS.STOPPED]:    "🟢 Recording saved — select a Whisper model and press **⚙️ Post-Process**.",
  [STATUS.PROCESSING]: "⏳ Post-processing in progress…",
  [STATUS.DONE]:       "✅ Post-processing complete.",
};

class SessionPanel {
  /**
   * @param {object} recorder       Recorder instance
   * @param {object} postProcessor  PostProcessor instance
   * @param {object} config         Parsed config.yaml object
   * @param {object} logger         Logger with .info / .warn / .error methods
   */
  constructor(recorder, postProcessor, config, logger) {
    this.recorder = recorder;
    this.postProcessor = postProcessor;
    this.config = config;
    this.logger = logger;
    /**
     * In-memory panel state map.
     * Key: panelId (string)
     * Value: { channelId, channelName, sessionName, sessionFolderName, status, log[] }
     * @type {Map<string, object>}
     */
    this.panels = new Map();
  }

  // ---------------------------------------------------------------------------
  // Internal helpers
  // ---------------------------------------------------------------------------

  /**
   * Generate a short unique panel ID from the current timestamp.
   * @returns {string}
   */
  _newPanelId() {
    return Date.now().toString(36);
  }

  /**
   * Append a line to the panel's status log (keeps the last 8 lines, ≤160 chars each).
   *
   * @param {object} state  Panel state object
   * @param {string} message  Message to append
   */
  _log(state, message) {
    const safe = `▸ ${message.replace(/```/g, "").trim()}`.slice(0, 160);
    state.log.push(safe);
    if (state.log.length > 8) {
      state.log.shift();
    }
  }

  /**
   * Build the Discord embed and component rows for the current panel state.
   *
   * Layout varies by status (progressive disclosure):
   *   IDLE                → channel dropdown  +  [✏️ Edit Session Details] (green if no name)
   *   READY               → channel dropdown  +  [✏️ Edit Session Details]  +  [⏺ Start Recording]
   *   RECORDING           → [⏹ Stop Recording]
   *   STOPPING            → [⏹ Stopping… (disabled)]
   *   STOPPED             → model dropdown  +  [☑ Generate Summary toggle]  +  [⚙️ Post-Process] (green)
   *   PROCESSING          → model dropdown (disabled)  +  [☑ Generate Summary (disabled)]  +  [⏳ Processing… (disabled)]
   *   DONE                → (no components — clean embed only)
   *
   * @param {string} panelId
   * @param {object} state
   * @returns {{ embed: EmbedBuilder, rows: ActionRowBuilder[] }}
   */
  _buildPanel(panelId, state) {
    const { channelId, channelName, sessionName, sessionFolderName, status, log, whisperModel, generateSummary } = state;

    const embed = new EmbedBuilder()
      .setTitle("🎙️ Session Control Panel")
      .setColor(COLORS[status] ?? 0x5865f2)
      .addFields(
        {
          name: "Session Name",
          value: sessionName ? `\`${sessionName}\`` : "_not set_",
          inline: true,
        },
        {
          name: "Voice Channel",
          value: channelName ? `\`#${channelName}\`` : "_not selected_",
          inline: true,
        },
        {
          name: "Status",
          value: STATUS_LABELS[status] ?? "⚪ Unknown",
        }
      );

    if (log.length > 0) {
      embed.addFields({
        name: "Log",
        value: log.join("\n").slice(0, 1024),
      });
    }

    const rows = [];

    if (status === STATUS.IDLE || status === STATUS.READY) {
      // Reusable channel dropdown (shown in IDLE and READY)
      const channelRow = new ActionRowBuilder().addComponents(
        new ChannelSelectMenuBuilder()
          .setCustomId(`panel_channel:${panelId}`)
          .setPlaceholder(channelName ? `Voice channel: #${channelName}` : "Select a voice channel…")
          .addChannelTypes(ChannelType.GuildVoice, ChannelType.GuildStageVoice)
      );

      // Edit button — green when no name set yet (draws attention), grey once a name is set
      const editRow = new ActionRowBuilder().addComponents(
        new ButtonBuilder()
          .setCustomId(`panel_set_name:${panelId}`)
          .setLabel("✏️ Edit Session Details")
          .setStyle(sessionName ? ButtonStyle.Secondary : ButtonStyle.Success)
      );

      rows.push(channelRow);
      rows.push(editRow);

      if (status === STATUS.READY) {
        rows.push(
          new ActionRowBuilder().addComponents(
            new ButtonBuilder()
              .setCustomId(`panel_record:${panelId}`)
              .setLabel("⏺ Start Recording")
              .setStyle(ButtonStyle.Success)
          )
        );
      }
    } else if (status === STATUS.RECORDING) {
      rows.push(
        new ActionRowBuilder().addComponents(
          new ButtonBuilder()
            .setCustomId(`panel_stop:${panelId}`)
            .setLabel("⏹ Stop Recording")
            .setStyle(ButtonStyle.Danger)
        )
      );
    } else if (status === STATUS.STOPPING) {
      rows.push(
        new ActionRowBuilder().addComponents(
          new ButtonBuilder()
            .setCustomId(`panel_stop:${panelId}`)
            .setLabel("⏹ Stopping…")
            .setStyle(ButtonStyle.Danger)
            .setDisabled(true)
        )
      );
    } else if (status === STATUS.STOPPED || status === STATUS.PROCESSING) {
      const modelValue = whisperModel || this.config.whisper_model || "base";
      const modelLocked = status === STATUS.PROCESSING;

      rows.push(
        new ActionRowBuilder().addComponents(
          new StringSelectMenuBuilder()
            .setCustomId(`panel_model:${panelId}`)
            .setPlaceholder(`Whisper model: ${modelValue}`)
            .addOptions([
              { label: "tiny",   description: "Fastest, lowest accuracy",  value: "tiny",   default: modelValue === "tiny"   },
              { label: "base",   description: "Fast, decent accuracy",      value: "base",   default: modelValue === "base"   },
              { label: "small",  description: "Balanced",                   value: "small",  default: modelValue === "small"  },
              { label: "medium", description: "Slower, higher accuracy",    value: "medium", default: modelValue === "medium" },
              { label: "large",  description: "Slowest, best accuracy",     value: "large",  default: modelValue === "large"  },
            ])
            .setDisabled(modelLocked)
        )
      );

      rows.push(
        new ActionRowBuilder().addComponents(
          new ButtonBuilder()
            .setCustomId(`panel_summary_toggle:${panelId}`)
            .setLabel(generateSummary ? "☑ Generate Summary" : "☐ Skip Summary")
            .setStyle(generateSummary ? ButtonStyle.Success : ButtonStyle.Secondary)
            .setDisabled(modelLocked)
        )
      );

      rows.push(
        new ActionRowBuilder().addComponents(
          new ButtonBuilder()
            .setCustomId(`panel_post_process:${panelId}`)
            .setLabel(status === STATUS.PROCESSING ? "⏳ Processing…" : "⚙️ Post-Process")
            .setStyle(status === STATUS.PROCESSING ? ButtonStyle.Secondary : ButtonStyle.Success)
            .setDisabled(status !== STATUS.STOPPED || !sessionFolderName)
        )
      );
    }
    // DONE: no components — clean embed only

    return { embed, rows };
  }

  /**
   * Re-render the panel embed in-place using editReply on the source interaction.
   *
   * @param {import('discord.js').Interaction} interaction  The interaction whose reply is the panel
   * @param {string} panelId
   * @param {object} state
   */
  async _refreshPanel(interaction, panelId, state) {
    const { embed, rows } = this._buildPanel(panelId, state);
    try {
      await interaction.editReply({ embeds: [embed], components: rows });
    } catch (err) {
      this.logger.warn(`Failed to refresh panel ${panelId}: ${err.message}`);
    }
  }

  /**
   * Build a fake interaction adapter that redirects recorder / postProcessor
   * reply calls into the panel log + embed refresh.
   *
   * The fake interaction has the minimum surface needed by Recorder.start() and
   * PostProcessor.postProcess() without touching the real Discord interaction:
   *   guild, deferReply(), reply(), editReply()
   *
   * @param {import('discord.js').ButtonInteraction} realInteraction  The acknowledged button interaction
   * @param {string} panelId
   * @param {object} state
   * @param {{ onSuccess?: () => void, onError?: () => void }} [hooks]
   * @returns {object}  Fake interaction object
   */
  _makeFakeInteraction(realInteraction, panelId, state, hooks = {}) {
    const self = this;

    const capture = async (content, isError) => {
      // Strip markdown noise, leading ✅ emoji, and grab the first line for the log
      const line = content
        .split("\n")[0]
        .replace(/^✅\s*/, "")
        .replace(/[`*]/g, "")
        .trim()
        .slice(0, 150);
      self._log(state, line);

      if (isError && hooks.onError) hooks.onError();
      if (!isError && content.startsWith("✅") && hooks.onSuccess) {
        hooks.onSuccess();
      }

      await self._refreshPanel(realInteraction, panelId, state);
    };

    return {
      guild: realInteraction.guild,
      deferReply: async () => {
        self.logger.debug(`Panel ${panelId}: fake interaction deferReply called (no-op — already acknowledged)`);
      },
      editReply: async ({ content }) => capture(content, content.startsWith("❌")),
      reply: async ({ content }) => capture(content, content.startsWith("❌")),
    };
  }

  // ---------------------------------------------------------------------------
  // Command handler
  // ---------------------------------------------------------------------------

  /**
   * Open the control panel in response to /mlx-ai session.
   *
   * @param {import('discord.js').ChatInputCommandInteraction} interaction
   */
  async open(interaction) {
    const panelId = this._newPanelId();

    const state = {
      channelId: null,
      channelName: null,
      sessionName: null,
      sessionFolderName: null,
      whisperModel: this.config.whisper_model || "base",
      generateSummary: this.config.auto_summarize !== false,
      status: STATUS.IDLE,
      log: [],
    };

    this.panels.set(panelId, state);

    const { embed, rows } = this._buildPanel(panelId, state);
    await interaction.reply({ embeds: [embed], components: rows, ephemeral: true });
    this.logger.info(`Session control panel opened (panelId=${panelId})`);
  }

  // ---------------------------------------------------------------------------
  // Interaction handlers — all called from bot.js interactionCreate
  // ---------------------------------------------------------------------------

  /**
   * Voice channel select menu updated.
   *
   * @param {import('discord.js').ChannelSelectMenuInteraction} interaction
   */
  async handleChannelSelect(interaction) {
    const panelId = interaction.customId.slice("panel_channel:".length);
    const state = this.panels.get(panelId);
    if (!state) {
      await interaction.reply({
        content: "⚠️ This panel has expired. Run `/mlx-ai session` to open a new one.",
        ephemeral: true,
      });
      return;
    }

    const channel = interaction.channels.first();
    state.channelId = channel.id;
    state.channelName = channel.name;

    // Advance to READY if the session name has already been entered
    if (state.sessionName && state.status === STATUS.IDLE) {
      state.status = STATUS.READY;
    }
    this._log(state, `Voice channel set to \`#${channel.name}\`.`);

    const { embed, rows } = this._buildPanel(panelId, state);
    await interaction.update({ embeds: [embed], components: rows });
  }

  /**
   * "Edit Session Details" button pressed — show a modal text input for the session name.
   * (Note: Discord modals only support text inputs, so the voice-channel selection
   * remains in the panel as a guided "step 2" that appears after the name is set.)
   *
   * @param {import('discord.js').ButtonInteraction} interaction
   */
  async handleSetNameButton(interaction) {
    const panelId = interaction.customId.slice("panel_set_name:".length);
    const state = this.panels.get(panelId);
    if (!state) {
      await interaction.reply({
        content: "⚠️ This panel has expired. Run `/mlx-ai session` to open a new one.",
        ephemeral: true,
      });
      return;
    }

    const nameInput = new TextInputBuilder()
      .setCustomId("session_name_input")
      .setLabel("Session Name")
      .setStyle(TextInputStyle.Short)
      .setPlaceholder("e.g. Campaign1_Session4")
      .setRequired(true)
      .setMinLength(1)
      .setMaxLength(80);

    // Only pre-fill the modal when a name is already set — Discord rejects
    // an empty-string value and throws DiscordAPIError[50035].
    if (state.sessionName) {
      nameInput.setValue(state.sessionName);
    }

    const modal = new ModalBuilder()
      .setCustomId(`panel_name:${panelId}`)
      .setTitle("Edit Session Details")
      .addComponents(
        new ActionRowBuilder().addComponents(nameInput)
      );

    await interaction.showModal(modal);
  }

  /**
   * Modal submitted with the session name.
   *
   * @param {import('discord.js').ModalSubmitInteraction} interaction
   */
  async handleNameModal(interaction) {
    const panelId = interaction.customId.slice("panel_name:".length);
    const state = this.panels.get(panelId);
    if (!state) {
      await interaction.reply({
        content: "⚠️ This panel has expired. Run `/mlx-ai session` to open a new one.",
        ephemeral: true,
      });
      return;
    }

    const raw = interaction.fields.getTextInputValue("session_name_input");
    // Sanitize: keep only word chars and hyphens, matching sanitiseName() in recorder.js.
    // Apply the fallback before slice() so an all-underscore input still produces "session".
    const sessionName =
      (raw.replace(/[^\w-]/g, "_").replace(/^_+|_+$/g, "") || "session").slice(0, 80);

    state.sessionName = sessionName;

    // Advance to READY if a channel is already selected
    if (state.channelId && state.status === STATUS.IDLE) {
      state.status = STATUS.READY;
    }
    this._log(state, `Session name set to \`${sessionName}\`.`);

    const { embed, rows } = this._buildPanel(panelId, state);
    await interaction.update({ embeds: [embed], components: rows });
  }

  /**
   * "⏺ Start Recording" button pressed.
   *
   * @param {import('discord.js').ButtonInteraction} interaction
   */
  async handleRecordButton(interaction) {
    const panelId = interaction.customId.slice("panel_record:".length);
    const state = this.panels.get(panelId);
    if (!state) {
      await interaction.reply({
        content: "⚠️ This panel has expired. Run `/mlx-ai session` to open a new one.",
        ephemeral: true,
      });
      return;
    }

    if (!state.channelId || !state.sessionName) {
      await interaction.reply({
        content: "⚠️ Set a session name and select a voice channel before recording.",
        ephemeral: true,
      });
      return;
    }

    const channel = interaction.guild.channels.cache.get(state.channelId);
    if (!channel) {
      await interaction.reply({
        content: "⚠️ The selected voice channel was not found. Select it again.",
        ephemeral: true,
      });
      return;
    }

    // Optimistically update the panel to recording state
    state.status = STATUS.RECORDING;
    this._log(state, "Starting recording…");
    const { embed, rows } = this._buildPanel(panelId, state);
    await interaction.update({ embeds: [embed], components: rows });

    // Build a fake interaction so recorder.start() feedback flows into the panel
    const fakeInteraction = this._makeFakeInteraction(
      interaction,
      panelId,
      state,
      {
        onError: () => {
          // recorder.start() reported failure — revert to READY so user can retry
          state.status = STATUS.READY;
        },
      }
    );

    try {
      await this.recorder.start(fakeInteraction, channel, state.sessionName, true);

      if (this.recorder.isRecording) {
        // Capture the full timestamped folder name that recorder created.
        // Fall back to the user-supplied name if the recorder state is unexpectedly absent.
        state.sessionFolderName = this.recorder.sessionName || state.sessionName;
        this._log(
          state,
          `Session folder: \`${state.sessionFolderName}\``
        );

        // Register a callback so mid-session voice joins appear in the panel log.
        this.recorder.onMidSessionJoin = (username) => {
          this._log(state, `\`${username}\` joined the channel.`);
          this._refreshPanel(interaction, panelId, state).catch((err) => {
            this.logger.warn(`Failed to refresh panel after mid-session join: ${err.message}`);
          });
        };
      } else {
        // recorder.start() completed but isRecording is false → something failed
        if (state.status === STATUS.RECORDING) {
          state.status = STATUS.READY;
        }
      }
    } catch (err) {
      this.logger.error(`Session panel recorder.start failed: ${err.message}`);
      state.status = STATUS.READY;
      this._log(state, `Error: ${err.message.slice(0, 100)}`);
    }

    await this._refreshPanel(interaction, panelId, state);
  }

  /**
   * "⏹ Stop Recording" button pressed.
   *
   * @param {import('discord.js').ButtonInteraction} interaction
   */
  async handleStopButton(interaction) {
    const panelId = interaction.customId.slice("panel_stop:".length);
    const state = this.panels.get(panelId);
    if (!state) {
      await interaction.reply({
        content: "⚠️ This panel has expired. Run `/mlx-ai session` to open a new one.",
        ephemeral: true,
      });
      return;
    }

    state.status = STATUS.STOPPING;
    this._log(state, "Stopping recording…");
    const { embed, rows } = this._buildPanel(panelId, state);
    await interaction.update({ embeds: [embed], components: rows });

    try {
      // Pass the guild (not the interaction) so recorder handles its own
      // announce-channel message; the panel reflects the final result below.
      await this.recorder.stop(interaction.guild, false, true);
      // Clear the mid-session join callback — recording is over.
      this.recorder.onMidSessionJoin = null;
      state.status = STATUS.STOPPED;
      this._log(state, `Files saved to \`${state.sessionFolderName}\`.`);
    } catch (err) {
      this.logger.error(`Session panel recorder.stop failed: ${err.message}`);
      state.status = STATUS.STOPPED; // treat as stopped even on error
      this._log(state, `Stop error: ${err.message.slice(0, 100)}`);
    }

    await this._refreshPanel(interaction, panelId, state);
  }

  /**
   * "⚙️ Post-Process" button pressed.
   *
   * @param {import('discord.js').ButtonInteraction} interaction
   */
  async handlePostProcessButton(interaction) {
    const panelId = interaction.customId.slice("panel_post_process:".length);
    const state = this.panels.get(panelId);
    if (!state) {
      await interaction.reply({
        content: "⚠️ This panel has expired. Run `/mlx-ai session` to open a new one.",
        ephemeral: true,
      });
      return;
    }

    if (!state.sessionFolderName) {
      await interaction.reply({
        content: "⚠️ No recorded session found. Record a session first.",
        ephemeral: true,
      });
      return;
    }

    const model = state.whisperModel || this.config.whisper_model || "base";
    const defaultLang =
      this.config.whisper_language === "auto"
        ? null
        : this.config.whisper_language || null;

    state.status = STATUS.PROCESSING;
    this._log(
      state,
      `Starting post-processing (model: ${model}, summary: ${state.generateSummary ? "on" : "off"})…`
    );
    const { embed, rows } = this._buildPanel(panelId, state);
    await interaction.update({ embeds: [embed], components: rows });

    // Build a fake interaction so postProcessor feedback flows into the panel
    const fakeInteraction = this._makeFakeInteraction(
      interaction,
      panelId,
      state,
      {
        onSuccess: () => {
          state.status = STATUS.DONE;
        },
        onError: () => {
          state.status = STATUS.STOPPED; // revert so user can retry
        },
      }
    );

    try {
      await this.postProcessor.postProcess(
        fakeInteraction,
        state.sessionFolderName,
        model,
        defaultLang,
        true,
        state.generateSummary
      );

      // If post-processing completed successfully, post the full DONE embed as a
      // public (non-ephemeral) message so the whole channel can see the session
      // summary.  Discord does not allow converting an ephemeral message to
      // public, so we post a new message containing the same embed content.
      if (state.status === STATUS.DONE) {
        try {
          const { embed: doneEmbed } = this._buildPanel(panelId, state);

          // Build the session summary attachment (if available)
          const outputDir = this.config.output_directory || "./recordings";
          const sessionDir = path.resolve(outputDir, state.sessionFolderName);
          const summaryPath = path.join(sessionDir, "_session_summary.md");

          const followUpPayload = {
            embeds: [doneEmbed],
            ephemeral: false,
          };

          if (fs.existsSync(summaryPath)) {
            // Attach the summary file so users can download it (no text preview in the message)
            followUpPayload.files = [
              new AttachmentBuilder(summaryPath, {
                name: `${state.sessionFolderName}_summary.md`,
              }),
            ];
          }

          await interaction.followUp(followUpPayload);
        } catch (followUpErr) {
          this.logger.warn(`Failed to post public completion notice: ${followUpErr.message}`);
        }
      }
    } catch (err) {
      this.logger.error(`Session panel postProcess failed: ${err.message}`);
      state.status = STATUS.STOPPED;
      this._log(state, `Error: ${err.message.slice(0, 100)}`);
      await this._refreshPanel(interaction, panelId, state);
    }
  }

  /**
   * Whisper model select menu updated (shown in the STOPPED state).
   *
   * @param {import('discord.js').StringSelectMenuInteraction} interaction
   */
  async handleModelSelect(interaction) {
    const panelId = interaction.customId.slice("panel_model:".length);
    const state = this.panels.get(panelId);
    if (!state) {
      await interaction.reply({
        content: "⚠️ This panel has expired. Run `/mlx-ai session` to open a new one.",
        ephemeral: true,
      });
      return;
    }

    state.whisperModel = interaction.values[0];
    const { embed, rows } = this._buildPanel(panelId, state);
    await interaction.update({ embeds: [embed], components: rows });
  }
  /**
   * "Generate Summary" toggle button pressed (shown in the STOPPED state).
   *
   * Flips state.generateSummary and re-renders the panel to reflect the new value.
   *
   * @param {import('discord.js').ButtonInteraction} interaction
   */
  async handleSummaryToggle(interaction) {
    const panelId = interaction.customId.slice("panel_summary_toggle:".length);
    const state = this.panels.get(panelId);
    if (!state) {
      await interaction.reply({
        content: "⚠️ This panel has expired. Run `/mlx-ai session` to open a new one.",
        ephemeral: true,
      });
      return;
    }

    state.generateSummary = !state.generateSummary;
    const { embed, rows } = this._buildPanel(panelId, state);
    await interaction.update({ embeds: [embed], components: rows });
  }
}

module.exports = { SessionPanel };

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
  ActionRowBuilder,
  ButtonBuilder,
  ButtonStyle,
  Client,
  GatewayIntentBits,
  REST,
  Routes,
  SlashCommandBuilder,
  SlashCommandSubcommandBuilder,
  SlashCommandSubcommandGroupBuilder,
  StringSelectMenuBuilder,
} = require("discord.js");
const { Recorder } = require("./recorder");
const { PostProcessor } = require("./postProcessor");
const { QueryHandler } = require("./queryHandler");
const { SessionPanel } = require("./sessionPanel");
const { CategoriesPanel } = require("./categoriesPanel");
const categories = require("./categories");

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
    .addSubcommand(
      new SlashCommandSubcommandBuilder()
        .setName("session")
        .setDescription(
          "Open an interactive control panel to record and post-process a new session"
        )
    )
    .addSubcommand(
      new SlashCommandSubcommandBuilder()
        .setName("ask")
        .setDescription("Ask a question about recorded sessions using RAG")
        .addStringOption((opt) =>
          opt
            .setName("question")
            .setDescription("The question to ask about recorded sessions")
            .setRequired(true)
        )
        .addStringOption((opt) =>
          opt
            .setName("category")
            .setDescription("Category to search — selects the collection (default: dnd)")
            .setRequired(false)
            .setAutocomplete(true)
        )
        .addStringOption((opt) =>
          opt
            .setName("subcategory")
            .setDescription("Restrict results to a sub-category within the category (optional)")
            .setRequired(false)
            .setAutocomplete(true)
        )
        .addStringOption((opt) =>
          opt
            .setName("session")
            .setDescription(
              "Restrict results to a specific session folder name (optional)"
            )
            .setRequired(false)
        )
        .addIntegerOption((opt) =>
          opt
            .setName("top_k")
            .setDescription("Number of transcript chunks to retrieve (default: 5)")
            .setRequired(false)
            .setMinValue(1)
            .setMaxValue(20)
        )
        .addBooleanOption((opt) =>
          opt
            .setName("show_sources")
            .setDescription("Include the retrieved source chunks in the reply (default: false)")
            .setRequired(false)
        )
    )
    .addSubcommand(
      new SlashCommandSubcommandBuilder()
        .setName("re-post-process")
        .setDescription(
          "Re-run the full post-processing pipeline (transcribe → merge → vectorize → summarize) on a session"
        )
        .addStringOption((opt) =>
          opt
            .setName("session")
            .setDescription("The session folder to re-process")
            .setRequired(true)
            .setAutocomplete(true)
        )
        .addBooleanOption((opt) =>
          opt
            .setName("re_transcribe")
            .setDescription(
              "Also re-transcribe the audio from scratch, rebuilding the transcript (default: false)"
            )
            .setRequired(false)
        )
    )
    .addSubcommandGroup(
      new SlashCommandSubcommandGroupBuilder()
        .setName("category")
        .setDescription("Manage session categories (D&D, meeting, event planning, …)")
        .addSubcommand(
          new SlashCommandSubcommandBuilder()
            .setName("add")
            .setDescription("Define a new session category")
            .addStringOption((opt) =>
              opt
                .setName("name")
                .setDescription("Short id/slug for the category (e.g. meeting)")
                .setRequired(true)
            )
        )
        .addSubcommand(
          new SlashCommandSubcommandBuilder()
            .setName("edit")
            .setDescription("Edit an existing session category")
            .addStringOption((opt) =>
              opt
                .setName("name")
                .setDescription("The category id/slug to edit")
                .setRequired(true)
            )
        )
        .addSubcommand(
          new SlashCommandSubcommandBuilder()
            .setName("delete")
            .setDescription("Delete a session category")
            .addStringOption((opt) =>
              opt
                .setName("name")
                .setDescription("The category id/slug to delete")
                .setRequired(true)
            )
        )
        .addSubcommand(
          new SlashCommandSubcommandBuilder()
            .setName("list")
            .setDescription("List all defined session categories")
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
const postProcessor = new PostProcessor(config, makeLogger("postProcessor"));
const queryHandler = new QueryHandler(config, makeLogger("queryHandler"));
const sessionPanel = new SessionPanel(recorder, postProcessor, config, makeLogger("sessionPanel"));
const categoriesPanel = new CategoriesPanel(config, makeLogger("categoriesPanel"));

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

  if (group === "category") {
    await categoriesPanel.handleCommand(interaction);
    return;
  }

  if (group === null && sub === "session") {
    await sessionPanel.open(interaction);
    return;
  }

  if (group === null && sub === "ask") {
    const question = interaction.options.getString("question");
    const sessionFilter = interaction.options.getString("session") ?? null;
    const category = interaction.options.getString("category") ?? null;
    const subcategory = interaction.options.getString("subcategory") ?? null;
    const topK = interaction.options.getInteger("top_k") ?? 5;
    const showSources = interaction.options.getBoolean("show_sources") ?? false;
    await queryHandler.query(
      interaction,
      question,
      sessionFilter,
      topK,
      showSources,
      category,
      subcategory
    );
    return;
  }

  if (group === null && sub === "re-post-process") {
    const sessionName = interaction.options.getString("session");
    const retranscribe = interaction.options.getBoolean("re_transcribe") ?? false;
    const model = config.whisper_model || "base";
    const language =
      config.whisper_language === "auto" ? null : config.whisper_language || null;
    const generateSummary = config.auto_summarize !== false;
    await postProcessor.rePostProcess(
      interaction,
      sessionName,
      model,
      language,
      retranscribe,
      generateSummary
    );
  }
});

// ---------------------------------------------------------------------------
// List recorded session folders (newest first) for the re-post-process picker.
// Folder names are timestamp-prefixed (YYYYMMDD_HHMMSS_name), so a reverse
// lexicographic sort puts the most recent sessions on top. Reserved
// underscore/dot-prefixed entries are skipped.
// ---------------------------------------------------------------------------
function listSessionFolders() {
  const outputDir = config.output_directory || "./recordings";
  let entries;
  try {
    entries = fs.readdirSync(outputDir, { withFileTypes: true });
  } catch {
    return [];
  }
  return entries
    .filter(
      (e) => e.isDirectory() && !e.name.startsWith("_") && !e.name.startsWith(".")
    )
    .map((e) => e.name)
    .sort()
    .reverse();
}

// ---------------------------------------------------------------------------
// Autocomplete for /mlx-ai ask — category + subcategory options
// (and the session picker for /mlx-ai re-post-process)
// ---------------------------------------------------------------------------
client.on("interactionCreate", async (interaction) => {
  if (!interaction.isAutocomplete()) return;
  if (interaction.commandName !== "mlx-ai") return;

  const focused = interaction.options.getFocused(true); // { name, value }
  const query = (focused.value || "").toLowerCase();

  try {
    if (focused.name === "session") {
      const choices = listSessionFolders()
        .filter((name) => name.toLowerCase().includes(query))
        .slice(0, 25)
        .map((name) => ({ name: name.slice(0, 100), value: name.slice(0, 100) }));
      await interaction.respond(choices);
      return;
    }

    if (focused.name === "category") {
      const choices = categories
        .listCategories(config)
        .filter(
          (c) =>
            c.display_name.toLowerCase().includes(query) ||
            c.category_name.toLowerCase().includes(query)
        )
        .slice(0, 25)
        .map((c) => ({ name: `${c.display_name} (${c.category_name})`, value: c.category_name }));
      await interaction.respond(choices);
      return;
    }

    if (focused.name === "subcategory") {
      // Sub-categories are scoped to the chosen category (slug-normalized).
      const catInput = interaction.options.getString("category");
      const catSlug = catInput ? categories.sanitizeSlug(catInput) : categories.DEFAULT_CATEGORY;
      const choices = categories
        .usedSubcategories(config, catSlug)
        .filter((s) => s.toLowerCase().includes(query))
        .slice(0, 25)
        .map((s) => ({ name: s, value: s }));
      await interaction.respond(choices);
      return;
    }

    await interaction.respond([]);
  } catch (err) {
    logger.warn(`Autocomplete failed for ${focused.name}: ${err.message}`);
    try {
      await interaction.respond([]);
    } catch {
      /* interaction already expired — ignore */
    }
  }
});

// ---------------------------------------------------------------------------
// Button + select menu routing for post-processing
// ---------------------------------------------------------------------------
client.on("interactionCreate", async (interaction) => {
  // ── Category management — add/edit modal submit ───────────────────────────
  if (
    interaction.isModalSubmit() &&
    interaction.customId.startsWith("category_modal:")
  ) {
    await categoriesPanel.handleModalSubmit(interaction);
    return;
  }

  // ── Category management — delete confirm button ───────────────────────────
  if (
    interaction.isButton() &&
    interaction.customId.startsWith("category_delete_confirm:")
  ) {
    await categoriesPanel.handleDeleteConfirm(interaction);
    return;
  }

  // ── Session panel — category select ───────────────────────────────────────
  if (
    interaction.isStringSelectMenu() &&
    interaction.customId.startsWith("panel_category:")
  ) {
    await sessionPanel.handleCategorySelect(interaction);
    return;
  }

  // ── Session panel — sub-category select ───────────────────────────────────
  if (
    interaction.isStringSelectMenu() &&
    interaction.customId.startsWith("panel_subcategory:")
  ) {
    await sessionPanel.handleSubcategorySelect(interaction);
    return;
  }

  // ── Session panel — new sub-category modal submit ─────────────────────────
  if (
    interaction.isModalSubmit() &&
    interaction.customId.startsWith("panel_subcategory_new:")
  ) {
    await sessionPanel.handleSubcategoryNewModal(interaction);
    return;
  }

  // ── Session panel — channel select ────────────────────────────────────────
  if (
    interaction.isChannelSelectMenu() &&
    interaction.customId.startsWith("panel_channel:")
  ) {
    await sessionPanel.handleChannelSelect(interaction);
    return;
  }

  // ── Session panel — Edit Session Details button ───────────────────────────
  if (
    interaction.isButton() &&
    interaction.customId.startsWith("panel_set_name:")
  ) {
    await sessionPanel.handleSetNameButton(interaction);
    return;
  }

  // ── Session panel — Start Recording button ────────────────────────────────
  if (
    interaction.isButton() &&
    interaction.customId.startsWith("panel_record:")
  ) {
    await sessionPanel.handleRecordButton(interaction);
    return;
  }

  // ── Session panel — Stop Recording button ─────────────────────────────────
  if (
    interaction.isButton() &&
    interaction.customId.startsWith("panel_stop:")
  ) {
    await sessionPanel.handleStopButton(interaction);
    return;
  }

  // ── Session panel — Post-Process button ───────────────────────────────────
  if (
    interaction.isButton() &&
    interaction.customId.startsWith("panel_post_process:")
  ) {
    await sessionPanel.handlePostProcessButton(interaction);
    return;
  }

  // ── Session panel — session name modal submit ─────────────────────────────
  if (
    interaction.isModalSubmit() &&
    interaction.customId.startsWith("panel_name:")
  ) {
    await sessionPanel.handleNameModal(interaction);
    return;
  }

  // ── Session panel — Whisper model select ──────────────────────────────────
  if (
    interaction.isStringSelectMenu() &&
    interaction.customId.startsWith("panel_model:")
  ) {
    await sessionPanel.handleModelSelect(interaction);
    return;
  }

  // ── Session panel — Generate Summary toggle ───────────────────────────────
  if (
    interaction.isButton() &&
    interaction.customId.startsWith("panel_summary_toggle:")
  ) {
    await sessionPanel.handleSummaryToggle(interaction);
    return;
  }

  // ── Model select menu: post_process_model:<sessionName> ──────────────────
  if (interaction.isStringSelectMenu() && interaction.customId.startsWith("post_process_model:")) {
    const sessionName = interaction.customId.slice("post_process_model:".length);
    const chosenModel = interaction.values[0];

    // Rebuild the select menu (keep it enabled so the user can change their mind)
    const modelSelectRow = new ActionRowBuilder().addComponents(
      new StringSelectMenuBuilder()
        .setCustomId(`post_process_model:${sessionName}`)
        .setPlaceholder(`Model: ${chosenModel}`)
        .addOptions([
          { label: "tiny",   description: "Fastest, lowest accuracy",  value: "tiny",   default: chosenModel === "tiny"   },
          { label: "base",   description: "Fast, decent accuracy",      value: "base",   default: chosenModel === "base"   },
          { label: "small",  description: "Balanced",                   value: "small",  default: chosenModel === "small"  },
          { label: "medium", description: "Slower, higher accuracy",    value: "medium", default: chosenModel === "medium" },
          { label: "large",  description: "Slowest, best accuracy",     value: "large",  default: chosenModel === "large"  },
        ])
    );

    // Update the button to reflect the newly chosen model
    const startButtonRow = new ActionRowBuilder().addComponents(
      new ButtonBuilder()
        .setCustomId(`post_process:${sessionName}:${chosenModel}`)
        .setLabel(`▶ Start Processing (${chosenModel})`)
        .setStyle(ButtonStyle.Primary)
    );

    await interaction.update({ components: [modelSelectRow, startButtonRow] });
    return;
  }

  // ── Start button: post_process:<sessionName>:<model> ─────────────────────
  if (interaction.isButton() && interaction.customId.startsWith("post_process:")) {
    const parts = interaction.customId.split(":");
    // customId format: post_process:<sessionName>:<model>
    // sessionName itself may contain colons, so everything between index 1 and
    // the last segment is the session name.
    const model = parts[parts.length - 1];
    const sessionName = parts.slice(1, parts.length - 1).join(":");

    const defaultLang =
      config.whisper_language === "auto"
        ? null
        : config.whisper_language || null;

    // Disable both rows so nothing can be clicked while processing runs
    const disabledSelectRow = new ActionRowBuilder().addComponents(
      new StringSelectMenuBuilder()
        .setCustomId(`post_process_model:${sessionName}`)
        .setPlaceholder(`Model: ${model}`)
        .addOptions([
          { label: "tiny",   value: "tiny"   },
          { label: "base",   value: "base"   },
          { label: "small",  value: "small"  },
          { label: "medium", value: "medium" },
          { label: "large",  value: "large"  },
        ])
        .setDisabled(true)
    );
    const disabledButtonRow = new ActionRowBuilder().addComponents(
      new ButtonBuilder()
        .setCustomId(`post_process:${sessionName}:${model}`)
        .setLabel(`▶ Start Processing (${model})`)
        .setStyle(ButtonStyle.Primary)
        .setDisabled(true)
    );
    await interaction.update({ components: [disabledSelectRow, disabledButtonRow] });

    await postProcessor.postProcessFromButton(
      interaction,
      sessionName,
      model,
      defaultLang,
      config.auto_summarize !== false
    );
  }
});

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

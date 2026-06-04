/**
 * categoriesPanel.js — Discord UI for managing session categories.
 *
 * Handles the /mlx-ai category subcommands:
 *   add <name>     — open a modal to define a new category (display name, collection, prompt)
 *   edit <name>    — open the same modal pre-filled with the existing definition
 *   delete <name>  — confirm, then remove the category's files (vectors are left intact)
 *   list           — show all defined categories
 *
 * The category_name slug comes from the command's `name` option (so it is fixed for the
 * lifetime of a category — renaming = delete + add). The modal therefore only collects
 * display_name, collection_name, and the summary prompt.
 */

"use strict";

const {
  ActionRowBuilder,
  ButtonBuilder,
  ButtonStyle,
  EmbedBuilder,
  ModalBuilder,
  TextInputBuilder,
  TextInputStyle,
} = require("discord.js");

const categories = require("./categories");

class CategoriesPanel {
  /**
   * @param {object} config  Parsed config.yaml object
   * @param {object} logger  Logger with .info / .warn / .error methods
   */
  constructor(config, logger) {
    this.config = config;
    this.logger = logger;
  }

  // ---------------------------------------------------------------------------
  // Command entry point — dispatched from bot.js for the `category` subcommand group
  // ---------------------------------------------------------------------------

  /**
   * Route a /mlx-ai category <sub> interaction.
   *
   * @param {import('discord.js').ChatInputCommandInteraction} interaction
   */
  async handleCommand(interaction) {
    const sub = interaction.options.getSubcommand();
    if (sub === "list") return this._list(interaction);

    const rawName = interaction.options.getString("name");
    const slug = categories.sanitizeSlug(rawName);

    if (sub === "add") return this._showModal(interaction, "add", slug);
    if (sub === "edit") return this._showModal(interaction, "edit", slug);
    if (sub === "delete") return this._confirmDelete(interaction, slug);
  }

  // ---------------------------------------------------------------------------
  // add / edit — modal
  // ---------------------------------------------------------------------------

  /**
   * Show the add/edit modal. On edit it is pre-filled from the existing definition;
   * on add the collection name defaults to `<slug>_sessions`.
   *
   * @param {import('discord.js').ChatInputCommandInteraction} interaction
   * @param {"add"|"edit"} action
   * @param {string} slug
   */
  async _showModal(interaction, action, slug) {
    const existing = categories.loadCategory(this.config, slug);

    if (action === "edit" && !existing) {
      await interaction.reply({
        content: `⚠️ Category \`${slug}\` does not exist. Use \`/mlx-ai category add\` to create it.`,
        ephemeral: true,
      });
      return;
    }
    if (action === "add" && existing) {
      await interaction.reply({
        content: `⚠️ Category \`${slug}\` already exists. Use \`/mlx-ai category edit\` to change it.`,
        ephemeral: true,
      });
      return;
    }

    const displayInput = new TextInputBuilder()
      .setCustomId("display_name")
      .setLabel("Display name")
      .setStyle(TextInputStyle.Short)
      .setPlaceholder("e.g. Work Meeting")
      .setRequired(true)
      .setMaxLength(80);

    const collectionInput = new TextInputBuilder()
      .setCustomId("collection_name")
      .setLabel("ChromaDB collection name (3–63 chars)")
      .setStyle(TextInputStyle.Short)
      .setPlaceholder("e.g. work_meetings")
      .setRequired(true)
      .setMinLength(3)
      .setMaxLength(63);

    const promptInput = new TextInputBuilder()
      .setCustomId("summary_prompt")
      .setLabel("Summary prompt (returns markdown)")
      .setStyle(TextInputStyle.Paragraph)
      .setPlaceholder("Instructions for how to summarize a session of this style…")
      .setRequired(true)
      .setMaxLength(4000);

    // Pre-fill. Discord rejects setValue("") (DiscordAPIError[50035]) — only set non-empty.
    const displayDefault = existing ? existing.display_name : "";
    const collectionDefault = existing ? existing.collection_name : `${slug}_sessions`;
    const promptDefault = (categories.loadPrompt(this.config, slug) || "").trim();

    if (displayDefault) displayInput.setValue(displayDefault);
    if (collectionDefault) collectionInput.setValue(collectionDefault);
    if (promptDefault) promptInput.setValue(promptDefault);

    const modal = new ModalBuilder()
      .setCustomId(`category_modal:${action}:${slug}`)
      .setTitle(`${action === "add" ? "Add" : "Edit"} category: ${slug}`)
      .addComponents(
        new ActionRowBuilder().addComponents(displayInput),
        new ActionRowBuilder().addComponents(collectionInput),
        new ActionRowBuilder().addComponents(promptInput)
      );

    await interaction.showModal(modal);
  }

  /**
   * Handle the add/edit modal submit (customId: category_modal:<action>:<slug>).
   *
   * @param {import('discord.js').ModalSubmitInteraction} interaction
   */
  async handleModalSubmit(interaction) {
    const rest = interaction.customId.slice("category_modal:".length);
    const sep = rest.indexOf(":");
    const action = rest.slice(0, sep);
    const slug = categories.sanitizeSlug(rest.slice(sep + 1));

    const displayName = interaction.fields.getTextInputValue("display_name").trim();
    const collectionRaw = interaction.fields.getTextInputValue("collection_name").trim();
    const prompt = interaction.fields.getTextInputValue("summary_prompt");

    const check = categories.validateCollectionName(collectionRaw);
    if (!check.ok) {
      await interaction.reply({
        content: `❌ Invalid collection name: ${check.error}`,
        ephemeral: true,
      });
      return;
    }

    try {
      const meta = categories.saveCategory(this.config, {
        category_name: slug,
        display_name: displayName,
        collection_name: check.value,
        prompt,
      });
      this.logger.info(
        `Category ${action === "add" ? "created" : "updated"}: ${meta.category_name} (collection=${meta.collection_name})`
      );
      await interaction.reply({
        content:
          `✅ Category \`${meta.category_name}\` ${action === "add" ? "created" : "updated"}.\n` +
          `> **${meta.display_name}** → collection \`${meta.collection_name}\``,
        ephemeral: true,
      });
    } catch (err) {
      this.logger.error(`Failed to save category ${slug}: ${err.message}`);
      await interaction.reply({
        content: `❌ Failed to save category \`${slug}\`: ${err.message}`,
        ephemeral: true,
      });
    }
  }

  // ---------------------------------------------------------------------------
  // delete
  // ---------------------------------------------------------------------------

  /**
   * Reply with a confirm button before deleting a category.
   *
   * @param {import('discord.js').ChatInputCommandInteraction} interaction
   * @param {string} slug
   */
  async _confirmDelete(interaction, slug) {
    if (slug === categories.DEFAULT_CATEGORY) {
      await interaction.reply({
        content: `⚠️ The built-in \`${categories.DEFAULT_CATEGORY}\` category cannot be deleted.`,
        ephemeral: true,
      });
      return;
    }

    const existing = categories.loadCategory(this.config, slug);
    if (!existing) {
      await interaction.reply({
        content: `⚠️ Category \`${slug}\` does not exist.`,
        ephemeral: true,
      });
      return;
    }

    const row = new ActionRowBuilder().addComponents(
      new ButtonBuilder()
        .setCustomId(`category_delete_confirm:${slug}`)
        .setLabel(`Delete "${existing.display_name}"`)
        .setStyle(ButtonStyle.Danger)
    );

    await interaction.reply({
      content:
        `Delete category \`${slug}\` (**${existing.display_name}**)?\n` +
        `> The definition is removed, but vectors already stored in collection ` +
        `\`${existing.collection_name}\` are left untouched.`,
      components: [row],
      ephemeral: true,
    });
  }

  /**
   * Handle the delete-confirm button (customId: category_delete_confirm:<slug>).
   *
   * @param {import('discord.js').ButtonInteraction} interaction
   */
  async handleDeleteConfirm(interaction) {
    const slug = categories.sanitizeSlug(
      interaction.customId.slice("category_delete_confirm:".length)
    );

    if (slug === categories.DEFAULT_CATEGORY) {
      await interaction.update({
        content: `⚠️ The built-in \`${categories.DEFAULT_CATEGORY}\` category cannot be deleted.`,
        components: [],
      });
      return;
    }

    const removed = categories.deleteCategory(this.config, slug);
    if (removed) {
      this.logger.info(`Category deleted: ${slug}`);
      await interaction.update({ content: `🗑️ Category \`${slug}\` deleted.`, components: [] });
    } else {
      await interaction.update({
        content: `⚠️ Category \`${slug}\` was already gone.`,
        components: [],
      });
    }
  }

  // ---------------------------------------------------------------------------
  // list
  // ---------------------------------------------------------------------------

  /**
   * Reply with an embed listing all defined categories.
   *
   * @param {import('discord.js').ChatInputCommandInteraction} interaction
   */
  async _list(interaction) {
    const all = categories.listCategories(this.config);
    const embed = new EmbedBuilder().setTitle("📚 Session Categories").setColor(0x5865f2);

    if (all.length === 0) {
      embed.setDescription("_No categories defined. Use `/mlx-ai category add` to create one._");
    } else {
      embed.setDescription(
        all
          .map(
            (c) =>
              `• **${c.display_name}** — \`${c.category_name}\` → collection \`${c.collection_name}\``
          )
          .join("\n")
      );
    }

    await interaction.reply({ embeds: [embed], ephemeral: true });
  }
}

module.exports = { CategoriesPanel };

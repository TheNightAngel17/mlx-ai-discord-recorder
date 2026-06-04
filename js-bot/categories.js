/**
 * categories.js — File-based store for session "categories".
 *
 * A category is a session "style" (D&D, work meeting, event planning, …). Each one
 * is a pair of files in the categories directory:
 *   <category_name>.json  — { category_name, display_name, collection_name }
 *   <category_name>.md     — the summary system prompt (markdown-output instructions)
 *
 * This module is the JS side of the store (the Discord bot reads it to populate the
 * session panel and the /mlx-ai category command, and writes it on add/edit/delete).
 * py-query/categories.py is the matching Python side used by the processing/query
 * services. Keep the two in sync (slug rules, default category, file layout).
 *
 * Exports:
 *   DEFAULT_CATEGORY                       — "dnd" (built-in, reproduces original behaviour)
 *   getCategoriesDir(config)               — absolute path to the store directory
 *   listCategories(config)                 — array of { category_name, display_name, collection_name }
 *   loadCategory(config, name)             — one category's metadata, or null if missing
 *   loadPrompt(config, name)               — the category's .md prompt text, or null
 *   saveCategory(config, fields)           — write JSON + MD (create/overwrite)
 *   deleteCategory(config, name)           — remove JSON + MD; returns true if anything was deleted
 *   sanitizeSlug(raw)                      — category_name slug rules
 *   validateCollectionName(raw)            — { ok, value, error } per ChromaDB rules
 */

"use strict";

const fs = require("fs");
const path = require("path");

/** Built-in category that reproduces the original D&D behaviour. */
const DEFAULT_CATEGORY = "dnd";

/**
 * Resolve the categories directory from config.
 * A relative `categories_directory` is resolved against the repo root (one level up
 * from js-bot/), so every service agrees regardless of the working directory.
 *
 * @param {object} config  Parsed config.yaml object
 * @returns {string} Absolute path to the categories directory
 */
function getCategoriesDir(config) {
  const configured = (config && config.categories_directory) || "./categories";
  if (path.isAbsolute(configured)) return configured;
  return path.resolve(path.join(__dirname, ".."), configured);
}

/**
 * Sanitize a raw category name into a filesystem-safe slug.
 * Mirrors the session-name rule in sessionPanel.handleNameModal: keep word chars and
 * hyphens, collapse everything else to underscore, trim edge underscores, lowercase.
 *
 * @param {string} raw
 * @returns {string} A non-empty slug (falls back to "category")
 */
function sanitizeSlug(raw) {
  const slug = String(raw || "")
    .trim()
    .toLowerCase()
    .replace(/[^\w-]/g, "_")
    .replace(/^_+|_+$/g, "");
  return (slug || "category").slice(0, 64);
}

/**
 * Validate (and lightly sanitize) a ChromaDB collection name.
 * ChromaDB requires: 3–63 chars, start and end alphanumeric, and only
 * [a-zA-Z0-9._-] in between (no spaces, no consecutive dots).
 *
 * @param {string} raw
 * @returns {{ ok: boolean, value: string, error: string|null }}
 */
function validateCollectionName(raw) {
  const value = String(raw || "").trim();
  if (value.length < 3 || value.length > 63) {
    return { ok: false, value, error: "Collection name must be 3–63 characters." };
  }
  if (!/^[a-zA-Z0-9][a-zA-Z0-9._-]*[a-zA-Z0-9]$/.test(value)) {
    return {
      ok: false,
      value,
      error:
        "Collection name must start and end with a letter or digit and contain only letters, digits, '.', '_', or '-'.",
    };
  }
  if (value.includes("..")) {
    return { ok: false, value, error: "Collection name may not contain consecutive dots." };
  }
  return { ok: true, value, error: null };
}

/**
 * List all defined categories (sorted by display name).
 *
 * @param {object} config
 * @returns {Array<{category_name: string, display_name: string, collection_name: string}>}
 */
function listCategories(config) {
  const dir = getCategoriesDir(config);
  let entries;
  try {
    entries = fs.readdirSync(dir, { withFileTypes: true });
  } catch {
    return [];
  }
  const categories = [];
  for (const entry of entries) {
    if (!entry.isFile() || !entry.name.endsWith(".json")) continue;
    const name = entry.name.slice(0, -".json".length);
    const cat = loadCategory(config, name);
    if (cat) categories.push(cat);
  }
  categories.sort((a, b) =>
    (a.display_name || a.category_name).localeCompare(b.display_name || b.category_name)
  );
  return categories;
}

/**
 * Load one category's metadata.
 *
 * @param {object} config
 * @param {string} name  category_name slug
 * @returns {{category_name: string, display_name: string, collection_name: string}|null}
 */
function loadCategory(config, name) {
  const slug = sanitizeSlug(name);
  const jsonPath = path.join(getCategoriesDir(config), `${slug}.json`);
  try {
    const data = JSON.parse(fs.readFileSync(jsonPath, "utf8"));
    return {
      category_name: data.category_name || slug,
      display_name: data.display_name || data.category_name || slug,
      collection_name: data.collection_name || `${slug}_sessions`,
    };
  } catch {
    return null;
  }
}

/**
 * Load a category's summary prompt (.md contents).
 *
 * @param {object} config
 * @param {string} name
 * @returns {string|null}
 */
function loadPrompt(config, name) {
  const slug = sanitizeSlug(name);
  const mdPath = path.join(getCategoriesDir(config), `${slug}.md`);
  try {
    return fs.readFileSync(mdPath, "utf8");
  } catch {
    return null;
  }
}

/**
 * Create or overwrite a category (writes both the JSON and the MD prompt).
 *
 * @param {object} config
 * @param {object} fields
 * @param {string} fields.category_name
 * @param {string} fields.display_name
 * @param {string} fields.collection_name
 * @param {string} fields.prompt
 * @returns {{category_name: string, display_name: string, collection_name: string}}
 */
function saveCategory(config, { category_name, display_name, collection_name, prompt }) {
  const dir = getCategoriesDir(config);
  fs.mkdirSync(dir, { recursive: true });

  const slug = sanitizeSlug(category_name);
  const meta = {
    category_name: slug,
    display_name: String(display_name || category_name || slug).trim() || slug,
    collection_name: String(collection_name || `${slug}_sessions`).trim(),
  };

  fs.writeFileSync(path.join(dir, `${slug}.json`), JSON.stringify(meta, null, 2) + "\n");
  fs.writeFileSync(path.join(dir, `${slug}.md`), String(prompt || "").trim() + "\n");
  return meta;
}

/**
 * Collect distinct sub-categories already used under a category by scanning each
 * session's _session.metadata.json under the output directory.
 *
 * @param {object} config    Parsed config.yaml object (uses output_directory)
 * @param {string} category  category_name slug to match
 * @returns {string[]}  Sorted unique sub-category labels
 */
function usedSubcategories(config, category) {
  if (!category) return [];
  const outputDir = (config && config.output_directory) || "./recordings";
  const subs = new Set();
  let entries;
  try {
    entries = fs.readdirSync(outputDir, { withFileTypes: true });
  } catch {
    return [];
  }
  for (const entry of entries) {
    if (!entry.isDirectory()) continue;
    try {
      const data = JSON.parse(
        fs.readFileSync(path.join(outputDir, entry.name, "_session.metadata.json"), "utf8")
      );
      const cat = data.category || DEFAULT_CATEGORY;
      if (cat === category && data.subcategory) subs.add(String(data.subcategory));
    } catch {
      /* missing/invalid metadata — skip */
    }
  }
  return [...subs].sort((a, b) => a.localeCompare(b));
}

/**
 * Delete a category (both files). Vectors already stored in its collection are left
 * untouched — only the definition is removed.
 *
 * @param {object} config
 * @param {string} name
 * @returns {boolean} true if at least one file was removed
 */
function deleteCategory(config, name) {
  const slug = sanitizeSlug(name);
  const dir = getCategoriesDir(config);
  let removed = false;
  for (const ext of [".json", ".md"]) {
    const p = path.join(dir, `${slug}${ext}`);
    try {
      fs.unlinkSync(p);
      removed = true;
    } catch {
      /* missing — ignore */
    }
  }
  return removed;
}

module.exports = {
  DEFAULT_CATEGORY,
  getCategoriesDir,
  listCategories,
  loadCategory,
  loadPrompt,
  saveCategory,
  deleteCategory,
  usedSubcategories,
  sanitizeSlug,
  validateCollectionName,
};

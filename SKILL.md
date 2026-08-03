---
name: show-current-model
description: Report the currently selected model in the AI agent IDE and output its complete parameters, always including the model's full name. Use when the user asks which model is currently active/selected, what model they are running, or for the current model's full name and parameter/spec details (context window, reasoning levels, input modalities, truncation policy, etc.). Also use when the user says "show current model", "当前是什么模型", "输出当前模型的完整参数", or asks for the model's full name.
---

# Show Current Model

Detect the active model in the agent IDE and report its complete parameters. Always include the model's full name.

## Workflow

1. Run the detector script (cross-platform, handles Windows paths):

   ```
   python <skill-dir>/scripts/show_current_model.py
   ```

   On Windows, fall back to `py <skill-dir>/scripts/show_current_model.py`. Add `--json` for machine-readable output.

   The script reads the agent IDE config (Codex: `$CODEX_HOME/config.toml`, default `~/.codex/config.toml`) to resolve `model`, `model_provider`, `model_reasoning_effort`, and the model catalog path (`model_catalog_json`, fallback `$CODEX_HOME/models.json`), then looks up the model's full parameter entry in the catalog.

2. If the script cannot resolve the model, detect manually:

   - Inspect `$CODEX_HOME/config.toml` for the top-level `model` key.
   - Read `model_catalog_json` (or `$CODEX_HOME/models.json`) and find the catalog entry whose `slug`/`id`/`model`/`name` matches.
   - For IDEs other than Codex, see `references/ides.md`.

3. Report to the user in this required format:

   - First line: the model's full name — `display_name` (and `slug`). This is mandatory even if nothing else is available.
   - Next: provider name (and `base_url`/`wire_api` when configured) plus the reasoning effort.
   - Then: the complete parameter list from the catalog as a table (context window, max context window, truncation policy, input modalities, supported reasoning levels, tool support, etc.).

## Rules

- Always output the model's full name (display name + slug), never only a short alias.
- Never print API keys, bearer tokens, or other secrets; the script redacts them.
- Do not include `model_messages`/instruction-template content in the report; it is the system prompt, not a model parameter.
- If the catalog has no entry for the active model, report the config-level facts (name, provider, reasoning effort), state that the local catalog lacks the entry, and list the available catalog slugs so the user can verify.
- If parameters come from an external source (provider API, docs), cite the source and mark unverifiable fields as unknown rather than guessing.
- The script is read-only; it never changes the IDE configuration.

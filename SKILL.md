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

   On Windows, fall back to `py <skill-dir>/scripts/show_current_model.py`. Add `--json` for machine-readable output. If the user is on a Codex profile (`codex --profile NAME`), add `--profile NAME`. That overlays `$CODEX_HOME/NAME.config.toml` on `config.toml`.

   The script reads `$CODEX_HOME/config.toml` (default `~/.codex/config.toml`) for the top-level `model`, provider, reasoning effort, and catalog path (`model_catalog_json`, fallback `$CODEX_HOME/models.json`). It then loads the matching catalog entry and applies the same metadata overrides Codex applies: `model_context_window` (clamped to `max_context_window`), `model_auto_compact_token_limit`, and `tool_output_token_limit`.

2. If the script cannot resolve a top-level `model`, say so. Do not treat `models.new_thread.model` or a legacy `[profiles.NAME]` table as the active model; current Codex does not either. For IDEs other than Codex, read `references/ides.md`.

3. Report to the user in this required format:

   - First line: the model's full name — `display_name` (and `slug`). This is mandatory even when the catalog is missing, unreadable, or has no matching entry.
   - Next: provider id and display name, safe provider fields (`base_url`, `wire_api`), and the reasoning effort.
   - Then: the effective parameter list (catalog values after config overrides), plus session settings such as reasoning summary, verbosity, and service tier when they are set.
   - Include the script's warnings. When the catalog has no entry, list the available slugs.

## Rules

- Always output the model's full name (display name + slug), never only a short alias.
- Never print API keys, bearer tokens, header values, or other secrets. Do not read `auth.json` or resolve `env_key`. The script already omits those fields and strips credentials embedded in URLs.
- Do not include `model_messages`, `base_instructions`, or other instruction-template content; it is the system prompt, not a model parameter.
- If the catalog has no entry for the active model, report the config-level facts (name, provider, reasoning effort), state that the local catalog lacks the entry, and list the available catalog slugs.
- A case-insensitive catalog match is valid only when exactly one entry matches. Do not guess among several.
- `codex --model` and the desktop model picker can override the file for one session. Name the config file the report came from, and say the value is the configured model when a session override may exist.
- If parameters come from an external source (provider API, docs), cite the source and mark unverifiable fields as unknown rather than guessing.
- The script is read-only; it never changes the IDE configuration.

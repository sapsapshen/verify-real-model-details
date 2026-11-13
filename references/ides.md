# Detecting the active model in agent IDEs

The bundled script implements the Codex section. Use this note when the user is on another IDE, or when a Codex session override is not written into `config.toml`.

## Codex (CLI / Desktop) — primary

- Config directory: `$CODEX_HOME`, default `~/.codex`.
- Base file: `$CODEX_HOME/config.toml`.
  - `model` — active model slug. This is the value the script reports.
  - `model_provider` — provider id. Built-in ids include `openai`, `ollama`, and `lmstudio`; custom providers live under `[model_providers.<id>]` with `name`, `base_url`, and `wire_api`.
  - `model_reasoning_effort` — configured effort, such as `low`, `medium`, `high`, `xhigh`, `max`, or `ultra`.
  - `model_reasoning_summary`, `model_verbosity`, `service_tier` — session settings. Report them separately from catalog metadata.
  - `model_catalog_json` — path to a JSON catalog loaded at startup. A relative path is resolved from the working directory. Fallback: `$CODEX_HOME/models.json`.
- Profiles: `codex --profile NAME` loads `$CODEX_HOME/NAME.config.toml` over the base file. Run `scripts/show_current_model.py --profile NAME` to match that. Legacy `[profiles.NAME]` tables and a top-level `profile = "NAME"` key are not applied.
- Effective metadata: Codex applies `model_context_window` (clamped to the catalog `max_context_window`), `model_auto_compact_token_limit`, and `tool_output_token_limit` on top of the catalog entry. The script applies those same overrides. Bytes truncation uses 4 bytes per token.
- Not the active model: `models.new_thread.model` is only a default for new threads. `review_model` is the `/review` model.
- Session overrides: `codex --model <slug>` and the desktop model picker can override the file without saving it. Say which file the value came from when it may be stale.
- Do not read `$CODEX_HOME/auth.json` or any provider `env_key` value.

## Claude Code

Full parameter lists are not stored in a local catalog. Resolve the selected model, then consult the provider's docs and mark anything you cannot verify as unknown.

Precedence for the selected model:

1. `/model` during the session, or `claude --model <alias-or-name>` at startup.
2. `ANTHROPIC_MODEL`.
3. `model` in the settings file (`~/.claude/settings.json`, or the project `.claude/settings.json` / `.claude/settings.local.json`).
4. `ANTHROPIC_DEFAULT_MODEL`, only when no settings file sets `model`.

Aliases such as `sonnet`, `opus`, `haiku`, and `fable` track the provider's current recommended model. Pin a full id when the exact version matters, for example `claude-sonnet-5-5`, `claude-opus-5-5`, or `claude-fable-5-2`.

## Cursor / Windsurf / Continue

Model selection is UI-based and is not exposed through a stable local config key. Ask the user which model is selected, or read it from the visible model picker. Do not invent parameters for that choice; cite the provider docs and mark unverifiable fields as unknown.

# Detecting the active model in agent IDEs

## Codex (CLI / Desktop) — primary

- Config: `$CODEX_HOME/config.toml` (default `~/.codex/config.toml`).
  - `model` — active model slug.
  - `model_provider` — provider name.
  - `model_reasoning_effort` — configured reasoning effort.
  - `model_catalog_json` — path to the model catalog (fallback: `$CODEX_HOME/models.json`).
- Env: `CODEX_HOME` overrides the config directory.
- Overrides: `codex --model <name>` (CLI flag) and the desktop model selector can override the config value for a session. If the config value looks stale, state the source of the value and note that an override may exist.
- The bundled `scripts/show_current_model.py` implements this automatically.

## Claude Code

- Env: `ANTHROPIC_MODEL` (e.g., `claude-sonnet-4-5`) overrides the default model.
- Config: `~/.claude/settings.json`, or `claude config get model`.
- Full parameter lists are not stored in a local catalog; consult the provider's docs/API and mark fields that cannot be verified as unknown.

## Cursor / Windsurf / Continue

- Model selection is UI-based and not exposed through a stable local config key. Ask the user for the selected model, or infer from visible settings where available.

#!/usr/bin/env python3
"""Report the currently selected model and its complete parameters.

Reads the agent IDE configuration (Codex: $CODEX_HOME/config.toml) to find the
active model, then looks up the model's full parameter entry in the model
catalog (model_catalog_json, fallback $CODEX_HOME/models.json).

Usage:
    python show_current_model.py         # human-readable report
    python show_current_model.py --json  # machine-readable JSON

Exit codes:
    0  resolved and reported
    2  model found in config but not in the catalog
    3  config or catalog missing / unreadable
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

try:
    import tomllib
except ImportError:  # Python < 3.11
    tomllib = None

# Windows consoles default to GBK/CP1252; force UTF-8 so Unicode output is safe.
for stream in (sys.stdout, sys.stderr):
    try:
        stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

# Top-level Codex config keys that identify the active model.
CODEX_CONFIG_KEYS = (
    "model",
    "model_provider",
    "model_reasoning_effort",
    "model_catalog_json",
)

# Catalog fields that are prompt templates, not model parameters.
NON_PARAMETER_FIELDS = {"model_messages", "instructions_template", "base_instructions"}

# Key fragments that may carry secrets and must never be printed.
SECRET_FRAGMENTS = ("token", "key", "secret", "password")


def codex_home() -> Path:
    return Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")).expanduser()


def read_codex_config() -> dict:
    """Load the top-level model settings from $CODEX_HOME/config.toml."""
    cfg_path = codex_home() / "config.toml"
    if not cfg_path.exists():
        return {}
    text = cfg_path.read_text(encoding="utf-8", errors="replace")

    if tomllib is not None:
        try:
            data = tomllib.loads(text)
            return {k: data.get(k) for k in CODEX_CONFIG_KEYS if k in data}
        except tomllib.TOMLDecodeError:
            pass

    # Fallback: parse top-level `key = "value"` pairs (before the first section).
    top = text.split("\n[", 1)[0]
    out = {}
    for key in CODEX_CONFIG_KEYS:
        m = re.search(rf'^{re.escape(key)}\s*=\s*"([^"]*)"', top, re.MULTILINE)
        if m:
            out[key] = m.group(1)
    return out


def provider_info(config: dict) -> dict:
    """Return safe, non-secret provider facts from the config."""
    provider_name = config.get("model_provider")
    info = {"name": provider_name}
    if provider_name and tomllib is not None:
        try:
            data = tomllib.loads(
                (codex_home() / "config.toml").read_text(encoding="utf-8", errors="replace")
            )
            block = data.get("model_providers", {}).get(provider_name, {})
            for key in ("name", "base_url", "wire_api"):
                if block.get(key):
                    info[key] = block[key]
        except Exception:
            pass
    return info


def find_catalog() -> Path | None:
    config = read_codex_config()
    candidate = config.get("model_catalog_json")
    if candidate:
        p = Path(candidate).expanduser()
        if p.exists():
            return p
    p = codex_home() / "models.json"
    return p if p.exists() else None


def load_catalog(path: Path) -> list:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        return [m for m in data if isinstance(m, dict)]
    models = data.get("models", []) if isinstance(data, dict) else []
    return [m for m in models if isinstance(m, dict)]


def find_model(catalog: list, slug: str):
    for entry in catalog:
        for key in ("slug", "id", "model", "name"):
            if entry.get(key) == slug:
                return entry
    return None


def is_secret_key(key: str) -> bool:
    lower = key.lower()
    return any(fragment in lower for fragment in SECRET_FRAGMENTS)


def sanitize(entry: dict) -> dict:
    return {
        key: value
        for key, value in entry.items()
        if key not in NON_PARAMETER_FIELDS and not is_secret_key(key)
    }


def build_report(config: dict, catalog_path: Path | None, entry) -> dict:
    slug = config.get("model")
    full_name = slug
    parameters = {}
    if entry:
        full_name = entry.get("display_name") or entry.get("name") or slug
        parameters = sanitize(entry)
    return {
        "full_name": full_name,
        "slug": slug,
        "provider": provider_info(config),
        "reasoning_effort": config.get("model_reasoning_effort"),
        "catalog": str(catalog_path) if catalog_path else None,
        "parameters": parameters,
    }


def print_human(report: dict) -> None:
    print(f"当前模型（全称）: {report['full_name']}")
    print(f"模型标识 (slug): {report['slug']}")
    provider = report["provider"]
    if provider.get("name"):
        detail = ", ".join(
            f"{k}={v}" for k, v in provider.items() if k != "name" and v
        )
        print(f"提供方: {provider['name']}" + (f" ({detail})" if detail else ""))
    if report.get("reasoning_effort"):
        print(f"推理强度 (reasoning effort): {report['reasoning_effort']}")
    print(f"参数来源: {report['catalog'] or '未找到本地模型目录'}")
    print()
    if report["parameters"]:
        print("完整参数:")
        for key, value in report["parameters"].items():
            if isinstance(value, (dict, list)):
                print(f"  {key}: {json.dumps(value, ensure_ascii=False)}")
            else:
                print(f"  {key}: {value}")
    else:
        print("本地模型目录中未找到该模型的参数条目。")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="output machine-readable JSON")
    args = parser.parse_args()

    config = read_codex_config()
    slug = config.get("model")
    if not slug:
        print("未找到当前模型：config.toml 中缺少顶层的 model 配置。", file=sys.stderr)
        return 3

    catalog_path = find_catalog()
    if catalog_path is None:
        print("未找到模型目录 (model_catalog_json 或 $CODEX_HOME/models.json)。", file=sys.stderr)
        return 3

    entry = find_model(load_catalog(catalog_path), slug)
    report = build_report(config, catalog_path, entry)

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print_human(report)

    if entry is None:
        print(
            f"警告：模型 {slug!r} 不在模型目录中。可用模型: "
            + ", ".join(
                m.get("slug") or m.get("id") or m.get("name") or "?"
                for m in load_catalog(catalog_path)
            ),
            file=sys.stderr,
        )
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())

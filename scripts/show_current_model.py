#!/usr/bin/env python3
"""Report the currently selected Codex model and its effective parameters.

Reads $CODEX_HOME/config.toml (default ~/.codex/config.toml). A Codex profile
file, $CODEX_HOME/<profile>.config.toml, is applied over the base config when
--profile is set. The model catalog comes from --catalog, model_catalog_json,
or $CODEX_HOME/models.json.

The script is read-only. It never opens auth.json and never prints API keys,
tokens, or prompt templates.

Usage:
    python show_current_model.py
    python show_current_model.py --json
    python show_current_model.py --profile deep-review

Exit codes:
    0  model resolved from config and catalog
    2  model found in config but not in the catalog
    3  config or catalog missing, unreadable, or not usable
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

try:
    import tomllib
except ImportError:  # Python < 3.11
    tomllib = None  # type: ignore[assignment]

EXIT_OK = 0
EXIT_NOT_IN_CATALOG = 2
EXIT_UNAVAILABLE = 3

TOP_LEVEL_KEYS = frozenset(
    {
        "model",
        "model_provider",
        "model_reasoning_effort",
        "model_reasoning_summary",
        "model_verbosity",
        "model_catalog_json",
        "model_context_window",
        "model_auto_compact_token_limit",
        "model_auto_compact_token_limit_scope",
        "model_post_turn_compact_threshold_percent",
        "tool_output_token_limit",
        "service_tier",
        "review_model",
        "profile",
    }
)

SESSION_KEYS = (
    "model_reasoning_summary",
    "model_verbosity",
    "service_tier",
    "model_auto_compact_token_limit_scope",
    "model_post_turn_compact_threshold_percent",
    "model_context_window",
    "model_auto_compact_token_limit",
    "tool_output_token_limit",
    "review_model",
)

SESSION_LABELS = {
    "model_reasoning_summary": "推理摘要 (model_reasoning_summary)",
    "model_verbosity": "详细度 (model_verbosity)",
    "service_tier": "服务档 (service_tier)",
    "model_auto_compact_token_limit_scope": "自动压缩范围 (model_auto_compact_token_limit_scope)",
    "model_post_turn_compact_threshold_percent": "回合后压缩阈值% (model_post_turn_compact_threshold_percent)",
    "review_model": "评审模型 (review_model，不是当前模型)",
    "model_context_window": "配置的上下文窗口 (model_context_window)",
    "model_auto_compact_token_limit": "配置的自动压缩阈值 (model_auto_compact_token_limit)",
    "tool_output_token_limit": "工具输出 token 上限 (tool_output_token_limit)",
}

PROVIDER_FIELDS = (
    "name",
    "base_url",
    "wire_api",
    "requires_openai_auth",
    "env_key",
    "supports_websockets",
    "request_max_retries",
    "stream_max_retries",
    "stream_idle_timeout_ms",
)

MATCH_KEYS = ("slug", "id", "model", "name")
NON_PARAMETER_FIELDS = frozenset(
    {
        "model_messages",
        "instructions_template",
        "base_instructions",
        "developer_instructions",
        "compact_prompt",
    }
)
PROFILE_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")
SECRET_KEY_RE = re.compile(
    r"(?:^|[_-])(?:api[_-]?key|access[_-]?key|secret|password|passwd|token|credential|"
    r"authorization|bearer|private[_-]?key|cookie)(?:$|[_-])",
    re.IGNORECASE,
)
SECRET_VALUE_RE = re.compile(
    r"(?:Bearer\s+)?(?:sk-[A-Za-z0-9_\-]{8,}|ghp_[A-Za-z0-9]{20,}|"
    r"github_pat_[A-Za-z0-9_]{20,}|xox[baprs]-[A-Za-z0-9-]{10,}|AKIA[0-9A-Z]{16})"
)
_SCALAR_LINE = re.compile(
    r"""^(?P<key>[A-Za-z0-9_-]+)[ \t]*=[ \t]*"""
    r"""(?P<value>"(?:\\.|[^"\\])*"|'(?:[^']|'')*'|-?\d(?:_?\d)*|true|false)"""
    r"""[ \t]*(?:#.*)?$"""
)
APPROX_BYTES_PER_TOKEN = 4

for stream in (sys.stdout, sys.stderr):
    try:
        stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    except (AttributeError, ValueError):
        pass


class ReportError(Exception):
    def __init__(self, message: str, exit_code: int = EXIT_UNAVAILABLE) -> None:
        super().__init__(message)
        self.exit_code = exit_code


def codex_home() -> Path:
    return Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")).expanduser()


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Report the active Codex model and its effective parameters."
    )
    parser.add_argument("--json", action="store_true", help="output machine-readable JSON")
    parser.add_argument(
        "--profile",
        help="overlay $CODEX_HOME/<profile>.config.toml, same as Codex --profile",
    )
    parser.add_argument(
        "--config",
        type=Path,
        help="read this config.toml instead of $CODEX_HOME/config.toml",
    )
    parser.add_argument(
        "--catalog",
        type=Path,
        help="read this model catalog instead of model_catalog_json",
    )
    return parser.parse_args(argv)


def decode_basic_string(body: str) -> str:
    escapes = {
        "b": "\b",
        "t": "\t",
        "n": "\n",
        "f": "\f",
        "r": "\r",
        '"': '"',
        "\\": "\\",
    }
    chars: list[str] = []
    index = 0
    while index < len(body):
        char = body[index]
        if char != "\\" or index + 1 >= len(body):
            chars.append(char)
            index += 1
            continue
        chars.append(escapes.get(body[index + 1], body[index + 1]))
        index += 2
    return "".join(chars)


def parse_scalar(token: str) -> Any:
    if token == "true":
        return True
    if token == "false":
        return False
    if token.startswith('"'):
        return decode_basic_string(token[1:-1])
    if token.startswith("'"):
        return token[1:-1].replace("''", "'")
    return int(token.replace("_", ""))


def parse_toml_fallback(text: str) -> dict[str, Any]:
    """Parse the top-level scalars this report needs when tomllib cannot."""
    top = re.split(r"(?m)^\[", text, maxsplit=1)[0]
    parsed: dict[str, Any] = {}
    for line in top.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = _SCALAR_LINE.match(stripped)
        if not match or match.group("key") not in TOP_LEVEL_KEYS:
            continue
        parsed[match.group("key")] = parse_scalar(match.group("value"))
    return parsed


def load_toml(path: Path) -> tuple[dict[str, Any], list[str]]:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise ReportError(f"无法读取配置文件 {path}: {exc.strerror or exc}") from exc

    warnings: list[str] = []
    if tomllib is None:
        warnings.append("当前 Python 没有 tomllib（需要 3.11+），已仅解析顶层标量。")
        return parse_toml_fallback(text), warnings
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        warnings.append(f"{path.name} 不是有效的 TOML，已仅解析顶层标量。")
        return parse_toml_fallback(text), warnings
    if not isinstance(data, dict):
        warnings.append(f"{path.name} 的 TOML 根节点不是表，已仅解析顶层标量。")
        return parse_toml_fallback(text), warnings
    return data, warnings


def merge_config(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in overlay.items():
        if key == "model_providers" and isinstance(value, dict):
            existing = merged.get(key)
            providers = dict(existing) if isinstance(existing, dict) else {}
            for provider_id, provider_value in value.items():
                current = providers.get(provider_id)
                if isinstance(current, dict) and isinstance(provider_value, dict):
                    updated = dict(current)
                    updated.update(provider_value)
                    providers[provider_id] = updated
                else:
                    providers[provider_id] = provider_value
            merged[key] = providers
            continue
        merged[key] = value
    return merged


def legacy_profile_warnings(config: dict[str, Any], profile: str | None) -> list[str]:
    warnings: list[str] = []
    selected = config.get("profile")
    if profile is None and isinstance(selected, str) and selected.strip():
        name = selected.strip()
        warnings.append(
            f"配置中的 profile = {name!r} 是旧式写法，Codex 已不再用它选择配置档。"
            f"请改用 {name}.config.toml，并运行脚本时加上 --profile {name}。"
        )
    profiles = config.get("profiles")
    if isinstance(profiles, dict):
        names = [
            str(name)
            for name, body in profiles.items()
            if isinstance(body, dict) and body.get("model")
        ]
        if names:
            listed = ", ".join(f"[profiles.{name}]" for name in names)
            warnings.append(
                f"检测到旧式 {listed}。Codex 不会套用这些表；"
                "请把配置放到 NAME.config.toml 并用 --profile NAME。"
            )
    return warnings


def load_config(
    home: Path, profile: str | None, config_path: Path | None
) -> tuple[dict[str, Any], list[str], Path, Path | None]:
    path = config_path.expanduser() if config_path is not None else home / "config.toml"
    if not path.is_file():
        raise ReportError(f"未找到配置文件: {path}")
    config, warnings = load_toml(path)
    profile_path: Path | None = None
    if profile:
        profile_path = home / f"{profile}.config.toml"
        if not profile_path.is_file():
            raise ReportError(f"未找到配置档: {profile_path}")
        overlay, overlay_warnings = load_toml(profile_path)
        warnings.extend(overlay_warnings)
        config = merge_config(config, overlay)
    warnings.extend(legacy_profile_warnings(config, profile))
    return config, warnings, path, profile_path


def missing_model_message(config: dict[str, Any]) -> str:
    message = "未找到当前模型：config.toml 中缺少顶层的 model 配置。"
    models = config.get("models")
    if not isinstance(models, dict):
        return message
    new_thread = models.get("new_thread")
    if not isinstance(new_thread, dict):
        return message
    hinted = new_thread.get("model")
    if isinstance(hinted, str) and hinted.strip():
        message += (
            f" 另发现 models.new_thread.model = {hinted.strip()!r}，"
            "它只是新会话默认值，不会当作当前模型。"
        )
    return message


def redact_query(query: str) -> str:
    if not query:
        return ""
    kept: list[str] = []
    for pair in query.split("&"):
        if not pair:
            continue
        if "=" not in pair:
            kept.append(pair)
            continue
        key, value = pair.split("=", 1)
        if SECRET_KEY_RE.search(key) or SECRET_VALUE_RE.fullmatch(value):
            kept.append(f"{key}=[REDACTED]")
        else:
            kept.append(pair)
    return "&".join(kept)


def redact_url(url: str) -> str:
    parts = urlsplit(url)
    if not parts.scheme or not parts.netloc:
        return url
    netloc = parts.netloc
    if "@" in netloc:
        netloc = "[REDACTED]@" + netloc.rsplit("@", 1)[1]
    return urlunsplit((parts.scheme, netloc, parts.path, redact_query(parts.query), ""))


def provider_info(config: dict[str, Any]) -> dict[str, Any]:
    provider_id = config.get("model_provider")
    if not isinstance(provider_id, str) or not provider_id.strip():
        return {"name": None}
    provider_id = provider_id.strip()
    info: dict[str, Any] = {"id": provider_id, "name": provider_id}
    providers = config.get("model_providers")
    block = providers.get(provider_id) if isinstance(providers, dict) else None
    if not isinstance(block, dict):
        return info
    for key in PROVIDER_FIELDS:
        if key not in block or block[key] in (None, ""):
            continue
        value = block[key]
        if isinstance(value, str) and key == "base_url":
            info[key] = redact_url(value)
            continue
        if isinstance(value, str) and SECRET_VALUE_RE.fullmatch(value.strip()):
            continue
        if isinstance(value, (str, int, float, bool)):
            info[key] = value
    if not info.get("name"):
        info["name"] = provider_id
    return info


def resolve_catalog_path(
    home: Path,
    config: dict[str, Any],
    explicit: Path | None,
    warnings: list[str],
) -> Path | None:
    if explicit is not None:
        path = explicit.expanduser()
        if not path.is_file():
            raise ReportError(f"指定的模型目录不存在: {path}")
        return path
    configured = config.get("model_catalog_json")
    if isinstance(configured, str) and configured.strip():
        path = Path(configured.strip()).expanduser()
        if path.is_file():
            return path
        warnings.append(f"model_catalog_json 指向的文件不存在: {path}")
    default = home / "models.json"
    if default.is_file():
        return default
    return None


def normalize_catalog(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict):
        models = data.get("models")
        if isinstance(models, list):
            return [item for item in models if isinstance(item, dict)]
        if isinstance(models, dict):
            return [item for item in models.values() if isinstance(item, dict)]
        if any(key in data for key in ("slug", "id", "model", "display_name", "name")):
            return [data]
        if data and all(isinstance(item, dict) for item in data.values()):
            return list(data.values())
    raise ReportError("模型目录格式无法识别：需要 JSON 数组、{\"models\": [...]}，或单个模型对象。")


def load_catalog(path: Path) -> list[dict[str, Any]]:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        detail = getattr(exc, "strerror", None) or exc
        raise ReportError(f"无法读取模型目录 {path}: {detail}") from exc
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ReportError(f"模型目录不是有效的 JSON ({path}): 第 {exc.lineno} 行") from exc
    return normalize_catalog(data)


def find_model(catalog: list[dict[str, Any]], slug: str) -> tuple[dict[str, Any] | None, str | None]:
    for entry in catalog:
        for key in MATCH_KEYS:
            if entry.get(key) == slug:
                return entry, None
    folded = slug.casefold()
    matches: list[dict[str, Any]] = []
    for entry in catalog:
        for key in MATCH_KEYS:
            value = entry.get(key)
            if isinstance(value, str) and value.casefold() == folded:
                matches.append(entry)
                break
    if len(matches) != 1:
        return None, None
    matched = matches[0]
    label = next((matched.get(key) for key in MATCH_KEYS if matched.get(key)), slug)
    return matched, f"配置中的模型 {slug!r} 与目录条目 {label!r} 仅大小写不同，已按该条目报告。"


def catalog_labels(catalog: list[dict[str, Any]]) -> list[str]:
    labels: list[str] = []
    for entry in catalog:
        label = next((entry.get(key) for key in MATCH_KEYS if entry.get(key)), "?")
        labels.append(str(label))
    return labels


def sanitize(value: Any) -> Any:
    if isinstance(value, dict):
        cleaned: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                continue
            if key in NON_PARAMETER_FIELDS or SECRET_KEY_RE.search(key):
                continue
            cleaned[key] = sanitize(item)
        return cleaned
    if isinstance(value, list):
        return [sanitize(item) for item in value]
    if isinstance(value, str) and SECRET_VALUE_RE.fullmatch(value.strip()):
        return "[REDACTED]"
    return value


def as_int(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        token = value.strip().replace("_", "")
        if re.fullmatch(r"-?\d+", token):
            return int(token)
    return None


def apply_tool_limit(policy: Any, token_limit: int) -> dict[str, Any] | None:
    if not isinstance(policy, dict):
        return None
    mode = policy.get("mode", policy.get("type"))
    if not isinstance(mode, str):
        return None
    normalized = mode.strip().lower()
    updated = dict(policy)
    if normalized in {"token", "tokens"}:
        updated["limit"] = token_limit
        return updated
    if normalized in {"byte", "bytes"}:
        updated["limit"] = token_limit * APPROX_BYTES_PER_TOKEN
        return updated
    return None


def apply_overrides(
    parameters: dict[str, Any], config: dict[str, Any], warnings: list[str]
) -> dict[str, Any]:
    """Apply the Codex config overrides that replace catalog metadata."""
    applied: dict[str, Any] = {}
    context = as_int(config.get("model_context_window"))
    if context is not None and context >= 0:
        effective = context
        maximum = as_int(parameters.get("max_context_window"))
        if maximum is not None and maximum >= 0:
            effective = min(context, maximum)
        parameters["context_window"] = effective
        applied["context_window"] = effective
        if effective != context:
            warnings.append(
                f"model_context_window={context} 超过 max_context_window={maximum}，"
                f"有效上下文窗口为 {effective}。"
            )
    compact = as_int(config.get("model_auto_compact_token_limit"))
    if compact is not None and compact >= 0:
        parameters["auto_compact_token_limit"] = compact
        applied["auto_compact_token_limit"] = compact
    tool_limit = as_int(config.get("tool_output_token_limit"))
    if tool_limit is not None and tool_limit >= 0:
        updated = apply_tool_limit(parameters.get("truncation_policy"), tool_limit)
        if updated is None:
            warnings.append(
                "tool_output_token_limit 未写入 truncation_policy："
                "目录中的截断策略不是 mode/limit 形式。"
            )
        else:
            parameters["truncation_policy"] = updated
            applied["truncation_policy"] = updated
    return applied


def json_scalar(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def session_settings(config: dict[str, Any]) -> dict[str, Any]:
    session: dict[str, Any] = {}
    for key in SESSION_KEYS:
        if key not in config or config[key] is None:
            continue
        value = json_scalar(config[key])
        if isinstance(value, str) and SECRET_VALUE_RE.fullmatch(value.strip()):
            continue
        session[key] = value
    return session


def collect(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    if args.profile is not None and not PROFILE_NAME_RE.fullmatch(args.profile):
        raise ReportError("profile 名称只能包含字母、数字、连字符和下划线。")

    home = codex_home()
    config, warnings, config_path, profile_path = load_config(home, args.profile, args.config)
    slug = config.get("model")
    if not isinstance(slug, str) or not slug.strip():
        raise ReportError(missing_model_message(config))
    slug = slug.strip()

    catalog_path = resolve_catalog_path(home, config, args.catalog, warnings)
    parameters: dict[str, Any] = {}
    overrides: dict[str, Any] = {}
    available: list[str] = []
    error: str | None = None
    exit_code = EXIT_OK
    if catalog_path is None:
        error = "未找到模型目录 (model_catalog_json 或 $CODEX_HOME/models.json)。"
        exit_code = EXIT_UNAVAILABLE
    else:
        try:
            catalog = load_catalog(catalog_path)
        except ReportError as exc:
            error = str(exc)
            exit_code = EXIT_UNAVAILABLE
        else:
            entry, match_warning = find_model(catalog, slug)
            if match_warning:
                warnings.append(match_warning)
            if entry is None:
                exit_code = EXIT_NOT_IN_CATALOG
                available = catalog_labels(catalog)
            else:
                parameters = sanitize(entry)
                overrides = apply_overrides(parameters, config, warnings)

    full_name = slug
    if parameters:
        named = parameters.get("display_name") or parameters.get("name")
        if isinstance(named, str) and named.strip():
            full_name = named.strip()

    report = {
        "full_name": full_name,
        "slug": slug,
        "provider": provider_info(config),
        "reasoning_effort": json_scalar(config.get("model_reasoning_effort")),
        "catalog": str(catalog_path) if catalog_path else None,
        "parameters": parameters,
        "profile": args.profile,
        "config_path": str(config_path),
        "profile_path": str(profile_path) if profile_path else None,
        "session": session_settings(config),
        "overrides_applied": overrides,
        "warnings": warnings,
        "error": error,
        "available_slugs": available,
    }
    return report, exit_code


def format_provider(provider: dict[str, Any]) -> str | None:
    name = provider.get("name")
    if not name:
        return None
    details: list[str] = []
    provider_id = provider.get("id")
    if provider_id and provider_id != name:
        details.append(f"id={provider_id}")
    for key, value in provider.items():
        if key in {"name", "id"} or value in (None, ""):
            continue
        details.append(f"{key}={value}")
    suffix = f" ({', '.join(details)})" if details else ""
    return f"提供方: {name}{suffix}"


def render_value(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return value


def human_session_lines(report: dict[str, Any]) -> list[str]:
    applied = report["overrides_applied"]
    hidden: set[str] = set()
    if "context_window" in applied:
        hidden.add("model_context_window")
    if "auto_compact_token_limit" in applied:
        hidden.add("model_auto_compact_token_limit")
    if "truncation_policy" in applied:
        hidden.add("tool_output_token_limit")
    lines: list[str] = []
    session = report["session"]
    for key, label in SESSION_LABELS.items():
        if key in hidden or key not in session:
            continue
        lines.append(f"{label}: {session[key]}")
    return lines


def print_human(report: dict[str, Any]) -> None:
    print(f"当前模型（全称）: {report['full_name']}")
    print(f"模型标识 (slug): {report['slug']}")
    provider_line = format_provider(report["provider"])
    if provider_line:
        print(provider_line)
    if report.get("reasoning_effort") not in (None, ""):
        print(f"推理强度 (reasoning effort): {report['reasoning_effort']}")
    if report.get("profile"):
        print(f"配置档 (profile): {report['profile']}")
    print(f"配置来源: {report['config_path']}")
    if report.get("profile_path"):
        print(f"配置档文件: {report['profile_path']}")
    print(f"参数来源: {report['catalog'] or '未找到本地模型目录'}")
    print()
    if report["parameters"]:
        print("完整参数:")
        for key, value in report["parameters"].items():
            print(f"  {key}: {render_value(value)}")
    elif report["catalog"] is None:
        print("未找到本地模型目录，因此没有参数条目。")
    elif report.get("error"):
        print("未能读取模型目录，因此没有参数条目。")
    else:
        print("本地模型目录中未找到该模型的参数条目。")

    session_lines = human_session_lines(report)
    if session_lines:
        print()
        print("会话设置:")
        for line in session_lines:
            print(f"  {line}")
    if report["overrides_applied"]:
        print()
        print("已应用的配置覆盖:")
        for key, value in report["overrides_applied"].items():
            print(f"  {key}: {render_value(value)}")
    if report["warnings"] or report["available_slugs"]:
        print()
        print("注意:")
        for warning in report["warnings"]:
            print(f"  {warning}")
        if report["available_slugs"]:
            print("  可用模型: " + ", ".join(report["available_slugs"]))


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        report, exit_code = collect(args)
    except ReportError as exc:
        print(exc, file=sys.stderr)
        return exc.exit_code

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print_human(report)

    if exit_code == EXIT_NOT_IN_CATALOG:
        print(
            f"警告：模型 {report['slug']!r} 不在模型目录中。可用模型: "
            + ", ".join(report["available_slugs"]),
            file=sys.stderr,
        )
    elif report.get("error"):
        print(report["error"], file=sys.stderr)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())

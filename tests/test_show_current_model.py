"""Regression tests for scripts/show_current_model.py."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "show_current_model.py"

SECRET = "sk-test-placeholder"
PROMPT = "SECRET PROMPT DO NOT PRINT"


def load_module():
    spec = importlib.util.spec_from_file_location("show_current_model", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


mod = load_module()


def write_home(tmp_path: Path, config: str, catalog: str | None, profile: str | None = None) -> Path:
    home = tmp_path / "codex"
    home.mkdir()
    (home / "config.toml").write_text(config, encoding="utf-8")
    (home / "auth.json").write_text(
        json.dumps({"OPENAI_API_KEY": SECRET}),
        encoding="utf-8",
    )
    if catalog is not None:
        (home / "models.json").write_text(catalog, encoding="utf-8")
    if profile is not None:
        (home / "work.config.toml").write_text(profile, encoding="utf-8")
    return home


def run(monkeypatch: pytest.MonkeyPatch, home: Path, *args: str, env: dict[str, str] | None = None):
    monkeypatch.setenv("CODEX_HOME", str(home))
    if env:
        for key, value in env.items():
            monkeypatch.setenv(key, value)
    monkeypatch.chdir(home)
    return mod.main(list(args))


def catalog_entry(**extra: object) -> str:
    entry = {
        "slug": "gpt-6.1-sol",
        "display_name": "GPT-6.1 Sol",
        "description": "Example catalog entry.",
        "context_window": 272000,
        "max_context_window": 272000,
        "truncation_policy": {"mode": "tokens", "limit": 10000},
        "input_modalities": ["text", "image"],
        "supports_parallel_tool_calls": True,
        "model_messages": {"instructions_template": PROMPT},
        "base_instructions": PROMPT,
        "api_key": SECRET,
        "headers": {"Authorization": f"Bearer {SECRET}"},
        "note": SECRET,
    }
    entry.update(extra)
    return json.dumps({"models": [entry]}, ensure_ascii=False)


BASE_CONFIG = """
model = "gpt-6.1-sol"
model_provider = "proxy"
model_reasoning_effort = "high"
model_reasoning_summary = "auto"
service_tier = "priority"

[model_providers.proxy]
name = "Proxy"
base_url = "https://user:supersecret@api.example.com/v1?api_key=abc&tenant=public"
wire_api = "responses"
requires_openai_auth = false
env_key = "OPENAI_API_KEY"
experimental_bearer_token = "sk-test-placeholder"
http_headers = { Authorization = "Bearer sk-test-placeholder" }
"""


def test_reports_full_name_and_hides_secrets(monkeypatch, tmp_path, capsys):
    home = write_home(tmp_path, BASE_CONFIG, catalog_entry())
    code = run(monkeypatch, home, env={"OPENAI_API_KEY": SECRET})
    captured = capsys.readouterr()
    combined = captured.out + captured.err

    assert code == 0
    assert captured.out.splitlines()[0] == "当前模型（全称）: GPT-6.1 Sol"
    assert "模型标识 (slug): gpt-6.1-sol" in captured.out
    assert "提供方: Proxy (id=proxy," in captured.out
    assert "wire_api=responses" in captured.out
    assert "env_key=OPENAI_API_KEY" in captured.out
    assert "requires_openai_auth=False" in captured.out
    assert "[REDACTED]@api.example.com" in captured.out
    assert "api_key=[REDACTED]" in captured.out
    assert "tenant=public" in captured.out
    assert "推理强度 (reasoning effort): high" in captured.out
    assert "服务档 (service_tier): priority" in captured.out
    assert SECRET not in combined
    assert PROMPT not in combined
    assert "experimental_bearer_token" not in combined
    assert "http_headers" not in combined
    assert "auth.json" not in combined
    assert "supersecret" not in combined


def test_json_includes_effective_overrides(monkeypatch, tmp_path, capsys):
    config = """
model = "gpt-6.1-sol"
model_provider = "proxy"
model_reasoning_effort = "high"
model_context_window = 1_000_000
model_auto_compact_token_limit = 700000
tool_output_token_limit = 30

[model_providers.proxy]
name = "Proxy"
base_url = "https://api.example.com/v1"
wire_api = "responses"
"""
    catalog = catalog_entry(
        context_window=272000,
        max_context_window=272000,
        truncation_policy={"mode": "bytes", "limit": 10},
    )
    home = write_home(tmp_path, config, catalog)
    code = run(monkeypatch, home, "--json")
    captured = capsys.readouterr()
    report = json.loads(captured.out)

    assert code == 0
    assert report["full_name"] == "GPT-6.1 Sol"
    assert report["parameters"]["context_window"] == 272000
    assert report["parameters"]["auto_compact_token_limit"] == 700000
    assert report["parameters"]["truncation_policy"]["limit"] == 120
    assert report["overrides_applied"]["context_window"] == 272000
    assert "超过 max_context_window" in " ".join(report["warnings"])
    assert "api_key" not in report["parameters"]
    assert "model_messages" not in report["parameters"]
    assert report["parameters"]["note"] == "[REDACTED]"
    assert SECRET not in captured.out


def test_token_truncation_override(monkeypatch, tmp_path, capsys):
    config = """
model = "gpt-6.1-sol"
tool_output_token_limit = 30
"""
    home = write_home(tmp_path, config, catalog_entry())
    assert run(monkeypatch, home, "--json") == 0
    report = json.loads(capsys.readouterr().out)
    assert report["parameters"]["truncation_policy"] == {"mode": "tokens", "limit": 30}


def test_profile_overlay_replaces_model_and_keeps_provider(monkeypatch, tmp_path, capsys):
    profile = """
model = "gpt-6.1-sol"
model_reasoning_effort = "xhigh"
model_context_window = 128000

[model_providers.proxy]
base_url = "https://profile.example/v1"
"""
    other = {
        "slug": "gpt-5.6-sol",
        "display_name": "GPT-5.6 Sol",
        "context_window": 1000,
        "max_context_window": 1000,
    }
    selected = json.loads(catalog_entry())["models"][0]
    home = write_home(
        tmp_path,
        'model = "gpt-5.6-sol"\nmodel_provider = "proxy"\n\n[model_providers.proxy]\nname = "Proxy"\nbase_url = "https://api.example.com/v1"\nwire_api = "responses"\n',
        json.dumps([other, selected]),
        profile=profile,
    )
    code = run(monkeypatch, home, "--profile", "work", "--json")
    report = json.loads(capsys.readouterr().out)

    assert code == 0
    assert report["slug"] == "gpt-6.1-sol"
    assert report["full_name"] == "GPT-6.1 Sol"
    assert report["profile"] == "work"
    assert report["profile_path"].endswith("work.config.toml")
    assert report["config_path"].endswith("config.toml")
    assert report["reasoning_effort"] == "xhigh"
    assert report["provider"]["id"] == "proxy"
    assert report["provider"]["name"] == "Proxy"
    assert report["provider"]["base_url"] == "https://profile.example/v1"
    assert report["parameters"]["context_window"] == 128000


def test_rejects_profile_path_traversal(monkeypatch, tmp_path, capsys):
    home = write_home(tmp_path, 'model = "gpt-6.1-sol"\n', catalog_entry())
    outside = tmp_path / "outside.config.toml"
    outside.write_text('model = "from-outside"\n', encoding="utf-8")
    code = run(monkeypatch, home, "--profile", "../outside")
    captured = capsys.readouterr()
    assert code == 3
    assert "profile 名称" in captured.err
    assert "from-outside" not in captured.out + captured.err


def test_missing_catalog_still_prints_known_model(monkeypatch, tmp_path, capsys):
    home = write_home(tmp_path, 'model = "gpt-6.1-sol"\nmodel_reasoning_effort = "low"\n', None)
    code = run(monkeypatch, home)
    captured = capsys.readouterr()
    assert code == 3
    assert captured.out.splitlines()[0] == "当前模型（全称）: gpt-6.1-sol"
    assert "未找到模型目录" in captured.err
    assert "未找到本地模型目录，因此没有参数条目。" in captured.out


def test_model_missing_from_catalog(monkeypatch, tmp_path, capsys):
    home = write_home(tmp_path, 'model = "missing-model"\n', catalog_entry())
    code = run(monkeypatch, home)
    captured = capsys.readouterr()
    assert code == 2
    assert "当前模型（全称）: missing-model" in captured.out
    assert "可用模型: gpt-6.1-sol" in captured.out
    assert "警告：模型 'missing-model' 不在模型目录中。可用模型: gpt-6.1-sol" in captured.err


def test_invalid_catalog_json(monkeypatch, tmp_path, capsys):
    home = write_home(tmp_path, 'model = "gpt-6.1-sol"\n', "{not json")
    code = run(monkeypatch, home, "--json")
    captured = capsys.readouterr()
    report = json.loads(captured.out)
    assert code == 3
    assert report["full_name"] == "gpt-6.1-sol"
    assert "不是有效的 JSON" in report["error"]
    assert "不是有效的 JSON" in captured.err


@pytest.mark.parametrize(
    "payload",
    [
        [{"slug": "gpt-6.1-sol", "display_name": "GPT-6.1 Sol"}],
        {"slug": "gpt-6.1-sol", "display_name": "GPT-6.1 Sol"},
        {"gpt-6.1-sol": {"slug": "gpt-6.1-sol", "display_name": "GPT-6.1 Sol"}},
    ],
)
def test_catalog_shapes(monkeypatch, tmp_path, capsys, payload):
    home = write_home(tmp_path, 'model = "gpt-6.1-sol"\n', json.dumps(payload))
    assert run(monkeypatch, home, "--json") == 0
    report = json.loads(capsys.readouterr().out)
    assert report["full_name"] == "GPT-6.1 Sol"


def test_casefold_match_is_unique_only(monkeypatch, tmp_path, capsys):
    payload = [{"slug": "gpt-6.1-sol", "display_name": "GPT-6.1 Sol"}]
    home = write_home(tmp_path, 'model = "GPT-6.1-SOL"\n', json.dumps(payload))
    assert run(monkeypatch, home, "--json") == 0
    report = json.loads(capsys.readouterr().out)
    assert report["full_name"] == "GPT-6.1 Sol"
    assert report["slug"] == "GPT-6.1-SOL"
    assert any("大小写" in warning for warning in report["warnings"])


def test_ambiguous_casefold_does_not_guess(monkeypatch, tmp_path, capsys):
    payload = [
        {"slug": "GPT", "display_name": "Upper"},
        {"slug": "gpt", "display_name": "Lower"},
    ]
    home = write_home(tmp_path, 'model = "GpT"\n', json.dumps(payload))
    code = run(monkeypatch, home, "--json")
    report = json.loads(capsys.readouterr().out)
    assert code == 2
    assert report["parameters"] == {}
    assert report["available_slugs"] == ["GPT", "gpt"]


def test_fallback_parser_reads_top_level_scalars(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(mod, "tomllib", None)
    config = """
model = "gpt-6.1-sol"
model_reasoning_effort = 'high'
model_context_window = 1_024
# model = "commented-out"
[broken
this is not toml
"""
    home = write_home(
        tmp_path,
        config,
        json.dumps([{"slug": "gpt-6.1-sol", "display_name": "GPT-6.1 Sol", "max_context_window": 2048}]),
    )
    assert run(monkeypatch, home, "--json") == 0
    report = json.loads(capsys.readouterr().out)
    assert report["slug"] == "gpt-6.1-sol"
    assert report["reasoning_effort"] == "high"
    assert report["parameters"]["context_window"] == 1024
    assert any("tomllib" in warning for warning in report["warnings"])


def test_missing_config_file(monkeypatch, tmp_path, capsys):
    home = tmp_path / "empty-codex"
    home.mkdir()
    code = run(monkeypatch, home)
    captured = capsys.readouterr()
    assert code == 3
    assert "未找到配置文件" in captured.err
    assert captured.out == ""


def test_new_thread_default_is_not_the_active_model(monkeypatch, tmp_path, capsys):
    config = """
[models.new_thread]
model = "gpt-6.1-sol"
model_reasoning_effort = "low"
"""
    home = write_home(tmp_path, config, catalog_entry())
    code = run(monkeypatch, home)
    captured = capsys.readouterr()
    assert code == 3
    assert "缺少顶层的 model" in captured.err
    assert "models.new_thread.model" in captured.err
    assert "GPT-6.1 Sol" not in captured.out


def test_legacy_profile_table_is_not_applied(monkeypatch, tmp_path, capsys):
    config = """
model = "gpt-6.1-sol"

[profiles.old]
model = "gpt-5.6-sol"
"""
    home = write_home(tmp_path, config, catalog_entry())
    assert run(monkeypatch, home, "--json") == 0
    report = json.loads(capsys.readouterr().out)
    assert report["slug"] == "gpt-6.1-sol"
    assert any("profiles.old" in warning for warning in report["warnings"])


def test_configured_catalog_falls_back_when_missing(monkeypatch, tmp_path, capsys):
    config = """
model = "gpt-6.1-sol"
model_catalog_json = "missing-catalog.json"
"""
    home = write_home(tmp_path, config, catalog_entry())
    assert run(monkeypatch, home, "--json") == 0
    report = json.loads(capsys.readouterr().out)
    assert report["full_name"] == "GPT-6.1 Sol"
    assert report["catalog"].endswith("models.json")
    assert any("model_catalog_json" in warning for warning in report["warnings"])


def test_cli_subprocess_json(tmp_path: Path):
    home = write_home(tmp_path, 'model = "gpt-6.1-sol"\n', catalog_entry())
    env = os.environ.copy()
    env["CODEX_HOME"] = str(home)
    completed = subprocess.run(
        [sys.executable, str(SCRIPT), "--json"],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    assert completed.returncode == 0
    report = json.loads(completed.stdout)
    assert report["full_name"] == "GPT-6.1 Sol"
    assert SECRET not in completed.stdout
    assert PROMPT not in completed.stdout

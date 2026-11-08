import json

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from token_agent.cli import app
from token_agent.config import ResearchPackage, SandboxSettings, Settings
from token_agent.diagnostics import diagnose

from .test_providers import live_settings


def test_invalid_fallback_or_attempt_ceiling_rejected(environment):
    base = live_settings(environment).model_dump()
    for update in [
        {"standard_fallbacks": ["unapproved/model"]},
        {"local_fallbacks": ["cloud/primary"]},
        {"standard_fallbacks": ["cloud/fallback"], "gateway_attempt_ceiling": 1},
        {"fast_model": "unapproved/model"},
    ]:
        with pytest.raises(ValidationError):
            Settings.model_validate({**base, **update})


def test_sandbox_and_research_configuration_are_strict():
    with pytest.raises(ValidationError):
        SandboxSettings(enabled=True, image="python:latest")
    with pytest.raises(ValidationError):
        SandboxSettings(verification_commands=[[]])
    with pytest.raises(ValidationError):
        ResearchPackage(name="python", domains=["https://docs.python.org"])


def test_doctor_does_not_echo_secrets_or_claim_live_verification(environment):
    data = diagnose(environment["settings"])
    assert data["ready"] and data["live_provider_verified"] is False
    assert environment["first"]["Authorization"].split()[1] not in json.dumps(data)


def test_doctor_reports_missing_prerequisites(environment, monkeypatch):
    monkeypatch.delenv("FIRST_TOKEN")
    monkeypatch.delenv("BIFROST_VIRTUAL_KEY", raising=False)
    data = diagnose(live_settings(environment))
    assert not data["ready"]
    assert any(not c["ok"] and c["required"] for c in data["checks"])


def test_doctor_invalid_config_is_safe(tmp_path):
    path = tmp_path / "invalid.toml"
    path.write_text('key = "do-not-echo-this-value"\n')
    result = CliRunner().invoke(app, ["doctor", "--config", str(path)])
    assert result.exit_code == 1
    assert "do-not-echo-this-value" not in result.output


def test_doctor_cli_emits_parseable_json_without_terminal_wrapping(environment, tmp_path):
    config = tmp_path / "doctor.toml"
    config.write_text(
        'mode="fixture"\n[[users]]\nid="first"\ntoken_env="FIRST_TOKEN"\nrepo_roots=["."]\n'
    )
    result = CliRunner().invoke(app, ["doctor", "--config", str(config)])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["ready"] and data["live_provider_verified"] is False

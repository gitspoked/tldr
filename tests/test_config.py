"""Tests for durable CLI/plugin configuration."""

import pytest

from tldreadme import config


def test_configuration_status_requires_setup(tmp_path):
    target = tmp_path / "config.json"

    status = config.configuration_status(target, include_environment=False)

    assert status["configured"] is False
    assert status["status"] == "setup_required"
    assert status["reason"] == "Must run Configuration - Setup first."
    assert status["setup_tool"] == "configuration_setup"
    assert status["setup_commands"]["installed_cli"]["interactive"] == "tldr setup"
    assert (
        status["setup_commands"]["plugin_uvx"]["ollama"] == "uvx --python 3.12 --from "
        "git+https://github.com/gitspoked/tldr.git@v0.1.4 "
        "tldr setup --provider ollama"
    )


def test_write_ollama_configuration_defaults_cloud_to_local_only(tmp_path):
    target = tmp_path / "config.json"

    result = config.write_configuration(provider="ollama", path=target)

    assert result["configured"] is True
    assert result["provider"] == "ollama"
    assert result["settings"]["TLDREADME_EMBED_MODEL"] == "ollama/mxbai-embed-large"
    assert result["settings"]["TLDREADME_CHAT_MODEL"].startswith("ollama/")
    assert result["inference_policy"]["allow_cloud_non_code"] is False
    assert result["inference_policy"]["allow_cloud_code"] is False
    assert result["inference_policy"]["credentials_stored"] is False
    assert result["inference_policy"]["provider_access_granted"] is False


def test_cloud_inference_requires_explicit_warning_acknowledgement(tmp_path):
    with pytest.raises(ValueError, match="explicit acknowledgement"):
        config.write_configuration(
            provider="ollama",
            allow_cloud_code=True,
            cloud_subscriptions=["codex"],
            path=tmp_path / "config.json",
        )


def test_subscription_selection_requires_expanded_inference_permission(tmp_path):
    with pytest.raises(ValueError, match="expanded cloud"):
        config.write_configuration(
            provider="ollama",
            cloud_subscriptions=["codex"],
            acknowledge_cloud_warning=True,
            path=tmp_path / "config.json",
        )


def test_litellm_configuration_uses_proxy_aliases(tmp_path):
    result = config.write_configuration(
        provider="litellm",
        litellm_url="http://localhost:4000",
        allow_cloud_non_code=True,
        cloud_subscriptions=["claude", "codex"],
        acknowledge_cloud_warning=True,
        path=tmp_path / "config.json",
    )

    assert result["settings"]["TLDREADME_EMBED_MODEL"] == "embed"
    assert result["settings"]["TLDREADME_CHAT_MODEL"] == "chat"
    assert result["inference_policy"]["cloud_subscriptions"] == ["claude", "codex"]


def test_cloud_permission_requires_a_subscription_target(tmp_path):
    with pytest.raises(ValueError, match="at least one subscription"):
        config.write_configuration(
            provider="ollama",
            allow_cloud_code=True,
            acknowledge_cloud_warning=True,
            path=tmp_path / "config.json",
        )


def test_configuration_rejects_invalid_service_url(tmp_path):
    with pytest.raises(ValueError, match="OLLAMA_URL"):
        config.write_configuration(
            provider="ollama",
            ollama_url="not-a-url",
            path=tmp_path / "config.json",
        )

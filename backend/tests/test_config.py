from app.config import Settings


def test_deepseek_settings_use_documented_unprefixed_environment_names(
    monkeypatch,
) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-secret")
    monkeypatch.setenv("DEEPSEEK_BASE_URL", "https://deepseek.test/v1")
    monkeypatch.setenv("DEEPSEEK_MODEL", "deepseek-test")

    settings = Settings(_env_file=None)

    assert settings.deepseek_api_key == "test-secret"
    assert settings.deepseek_base_url == "https://deepseek.test/v1"
    assert settings.deepseek_model == "deepseek-test"
    assert settings.agent_hard_step_limit == 50
    assert settings.agent_multi_agent_enabled is True
    assert settings.agent_max_delegations == 3
    assert settings.agent_child_step_limit == 10
    assert settings.agent_require_code_verification is True
    assert settings.agent_allow_unverified_code_with_reason is True
    assert settings.agent_require_resolved_test_failures is True


def test_agent_hard_step_limit_uses_prefixed_environment_name(monkeypatch) -> None:
    monkeypatch.setenv("COCODING_AGENT_HARD_STEP_LIMIT", "80")

    settings = Settings(_env_file=None)

    assert settings.agent_hard_step_limit == 80

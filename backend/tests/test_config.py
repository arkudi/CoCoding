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

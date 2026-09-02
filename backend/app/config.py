from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "CoCoding"
    database_url: str = "sqlite:///./data/cocoding.db"
    frontend_dist: Path = Path("frontend/dist")
    agent_hard_step_limit: int = Field(default=100, ge=1, le=500)
    agent_multi_agent_enabled: bool = True
    agent_max_delegations: int = Field(default=3, ge=1, le=10)
    agent_child_step_limit: int = Field(default=10, ge=1, le=50)
    agent_token_budget: int = Field(default=200_000, ge=1_000, le=10_000_000)
    agent_tool_call_limit: int = Field(default=300, ge=1, le=2_000)
    agent_wall_clock_limit_seconds: int = Field(default=900, ge=10, le=7_200)
    agent_require_code_verification: bool = True
    agent_allow_unverified_code_with_reason: bool = True
    agent_require_resolved_test_failures: bool = True
    deepseek_api_key: str | None = Field(default=None, validation_alias="DEEPSEEK_API_KEY")
    deepseek_base_url: str = Field(
        default="https://api.deepseek.com", validation_alias="DEEPSEEK_BASE_URL"
    )
    deepseek_model: str = Field(
        default="deepseek-v4-flash", validation_alias="DEEPSEEK_MODEL"
    )

    model_config = SettingsConfigDict(
        env_prefix="COCODING_",
        env_file=".env",
        extra="ignore",
        populate_by_name=True,
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()

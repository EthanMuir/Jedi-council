"""Central configuration. Never hardcode keys -- everything comes from the
environment / .env via pydantic-settings."""
from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    anthropic_api_key: str = ""
    alpha_vantage_api_key: str = ""
    fmp_api_key: str = ""

    no_llm: bool | None = None
    use_data_fixtures: bool | None = None

    council_db_path: str = "./data/council.db"
    cache_db_path: str = "./data/cache.db"

    seat_model: str = "claude-sonnet-5"
    synthesis_model: str = "claude-opus-5"

    # sampling / concurrency defaults referenced from Phase 2 onward
    n_samples_per_seat: int = 3
    max_concurrent_llm_calls: int = 8

    @property
    def resolved_no_llm(self) -> bool:
        """Fixture mode is forced whenever no Anthropic key is configured,
        unless explicitly overridden."""
        if self.no_llm is not None:
            return self.no_llm
        return not bool(self.anthropic_api_key)

    @property
    def resolved_use_data_fixtures(self) -> bool:
        if self.use_data_fixtures is not None:
            return self.use_data_fixtures
        return not bool(self.alpha_vantage_api_key or self.fmp_api_key)

    def ensure_dirs(self) -> None:
        for path in (self.council_db_path, self.cache_db_path):
            Path(path).parent.mkdir(parents=True, exist_ok=True)


def get_settings() -> Settings:
    return Settings()

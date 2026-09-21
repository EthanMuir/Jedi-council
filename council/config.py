"""Central configuration. Never hardcode keys -- everything comes from the
environment / .env via pydantic-settings."""
from __future__ import annotations

from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    anthropic_api_key: str = ""
    alpha_vantage_api_key: str = ""
    fmp_api_key: str = ""
    # Free (https://fred.stlouisfed.org/docs/api/api_key.html), 120
    # req/min with no hard daily cap -- the alternative to Alpha Vantage
    # for macro data, the one domain nothing else in the chain covers.
    fred_api_key: str = ""
    openai_api_key: str = ""
    google_api_key: str = ""

    # SEC EDGAR needs no key, but every request must carry a real contact
    # per SEC's fair-access policy -- blank (as .env.example ships it) falls
    # back to a placeholder via resolved_sec_edgar_user_agent below, which
    # "works" but risks a 403 or an IP ban under any real load.
    sec_edgar_user_agent: str | None = None

    no_llm: bool | None = None
    use_data_fixtures: bool | None = None

    @field_validator("no_llm", "use_data_fixtures", "sec_edgar_user_agent", mode="before")
    @classmethod
    def _blank_env_means_unset(cls, v):
        """.env.example ships these blank on purpose, to mean "auto-detect"
        / "use the fallback default" (see the resolved_* properties below)
        -- but an env var that's *present and empty* still reaches Pydantic
        as the string "", which fails bool parsing outright rather than
        falling back to the field's None default. Treat blank as unset."""
        return None if v == "" else v

    council_db_path: str = "./data/council.db"
    cache_db_path: str = "./data/cache.db"
    # Per-seat model overrides from the Settings pane (Task #74) -- a
    # separate small store on purpose, not a table in council_db_path: the
    # Crypt there is append-only/hash-chained by design (crypt/schema.sql),
    # the wrong place for a value that must be freely overwritten, and
    # cache_db_path's DiskCache entries expire on a TTL, wrong for a choice
    # meant to persist until the user changes it again.
    settings_db_path: str = "./data/settings.db"

    seat_model: str = "claude-sonnet-5"
    synthesis_model: str = "claude-opus-5"

    # sampling / concurrency defaults referenced from Phase 2 onward
    n_samples_per_seat: int = 3
    max_concurrent_llm_calls: int = 8

    # Phase 3: Tiers II-IV
    debate_rounds: int = 2
    # A fraction of seats actually CALLED at this horizon, not a fixed
    # absolute count (Task #69) -- the seat count called varies by horizon
    # already (competence 0.0 drops fundamentalist/macro_sage at 1d) and,
    # with senate_watcher/transcript_linguist now permanently NO_READ
    # (Option 1 on congress trades / earnings transcripts: no solid free
    # alternative exists), a fixed "6" quietly got harder to clear on every
    # single run for a reason that has nothing to do with that run's data
    # quality. Scaling to the called count means both effects wash out
    # automatically instead of needing a manual retune every time the
    # called/available seat count changes.
    min_participating_seats_pct: float = 0.5
    assumed_spread_bps: float = 5.0  # no live bid/ask feed yet -- see Cost Auditor
    risk_budget_pct: float = 1.0
    kelly_cap: float = 0.25  # quarter-Kelly default

    # Phase 4: Calibration Officer, extremizing, memory
    calibration_min_resolutions: int = 20
    calibration_min_cohort_for_exclusion: int = 8
    calibration_exclude_worst_pct: float = 0.30
    extremize_alpha: float = 1.0  # 1.0 = off; do not enable until 50+ resolutions show it helps
    memory_cap_per_seat: int = 200

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

    @property
    def resolved_sec_edgar_user_agent(self) -> str:
        if self.sec_edgar_user_agent is not None:
            return self.sec_edgar_user_agent
        return "The High Council (unconfigured contact -- set SEC_EDGAR_USER_AGENT)"

    def ensure_dirs(self) -> None:
        for path in (self.council_db_path, self.cache_db_path, self.settings_db_path):
            Path(path).parent.mkdir(parents=True, exist_ok=True)


def get_settings() -> Settings:
    return Settings()

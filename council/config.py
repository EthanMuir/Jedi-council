"""Central configuration. Never hardcode keys -- everything comes from the
environment / .env via pydantic-settings."""
from __future__ import annotations

import hashlib
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from council.key_store import apply_saved_keys


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    anthropic_api_key: str = ""
    # Task #77 -- no longer wired into the live provider chain
    # (build_data_service): its free tier's 25-requests/day cap made it
    # structurally unusable, and its options endpoints require a paid
    # ($199.99+/month) plan regardless of quota. Kept here (and the
    # provider class itself kept in council/data/providers/alpha_vantage.py)
    # only so a premium key could be wired back in later; setting this now
    # does nothing.
    alpha_vantage_api_key: str = ""
    fmp_api_key: str = ""
    # Free (https://fred.stlouisfed.org/docs/api/api_key.html), 120
    # req/min with no hard daily cap -- now the only source for macro data
    # in this chain; without it macro_sage has no live source at all.
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

    # Task #78 -- a single shared-password gate for exposing this app
    # beyond your own machine/LAN, ahead of real per-user accounts (a
    # separate, larger piece of work). Blank means auth is off entirely,
    # matching every prior local-only deployment's behaviour exactly (no
    # existing test or local workflow should have to know this feature
    # exists). Set a real value before putting this on the public internet
    # -- see the README's hosting section.
    app_password: str = ""
    # Cookies default to non-Secure so the password gate still works over
    # plain HTTP (e.g. testing locally, or before HTTPS is set up on a
    # fresh deployment) -- set true once served over HTTPS so the session
    # cookie can't leak over an unencrypted connection. bool | None (not a
    # plain bool default) for the same reason as no_llm/use_data_fixtures
    # below: .env.example ships this blank on purpose.
    cookie_secure: bool | None = None

    @field_validator(
        "no_llm", "use_data_fixtures", "sec_edgar_user_agent", "cookie_secure", mode="before"
    )
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
    # A fraction of horizon-COMPETENCE-weighted seats, not a plain headcount
    # (Task #76, building on Task #69) -- a seat only 20-30% competent at
    # this horizon abstaining is expected, correct behaviour, not a sign the
    # run lacks real conviction, so it shouldn't count against this gate as
    # heavily as a 90%-competent seat abstaining does. senate_watcher and
    # transcript_linguist are excluded from the weighted total entirely
    # (Option 1 on congress trades / earnings transcripts: no solid free
    # alternative exists, so they're permanently NO_READ regardless of their
    # own competence score). See orchestrator._gate_eligible_weight /
    # _directional_weight for the actual computation.
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
        """alpha_vantage_api_key dropped from this check (Task #77): it's no
        longer wired into the live chain at all (see its own comment above),
        so its presence/absence no longer says anything about whether a live
        run is possible -- yfinance and SEC EDGAR are free, keyless, and
        already wired in regardless of any key here."""
        if self.use_data_fixtures is not None:
            return self.use_data_fixtures
        return not bool(self.fmp_api_key)

    @property
    def resolved_sec_edgar_user_agent(self) -> str:
        if self.sec_edgar_user_agent is not None:
            return self.sec_edgar_user_agent
        return "The High Council (unconfigured contact -- set SEC_EDGAR_USER_AGENT)"

    @property
    def resolved_auth_enabled(self) -> bool:
        return bool(self.app_password)

    @property
    def resolved_cookie_secure(self) -> bool:
        if self.cookie_secure is not None:
            return self.cookie_secure
        return False

    @property
    def resolved_session_secret(self) -> str:
        """Derived from app_password itself (one-way, via sha256) rather
        than requiring a second env var -- signing session cookies needs a
        stable secret, and the password is already meant to be kept
        secret, so there's nothing a separate SESSION_SECRET would add
        except one more thing to configure and lose."""
        return hashlib.sha256(f"{self.app_password}:council-session-v1".encode()).hexdigest()

    def ensure_dirs(self) -> None:
        for path in (self.council_db_path, self.cache_db_path, self.settings_db_path):
            Path(path).parent.mkdir(parents=True, exist_ok=True)


def get_settings() -> Settings:
    """.env settings, with any API keys saved from the Settings screen
    layered on top (see council/key_store.py)."""
    return apply_saved_keys(Settings())

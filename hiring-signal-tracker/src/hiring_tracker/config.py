"""Configuration: environment settings + watchlist loader.

Secrets and connection strings come from the environment (.env). The watchlist
of companies lives in ``config/watchlist.yaml`` and is version-controlled.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal, Optional

import yaml
from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Repo root = two parents up from this file's package dir (src/hiring_tracker/).
PACKAGE_DIR = Path(__file__).resolve().parent
REPO_ROOT = PACKAGE_DIR.parent.parent
DEFAULT_WATCHLIST = REPO_ROOT / "config" / "watchlist.yaml"

AtsType = Literal["greenhouse", "lever", "ashby", "workable", "custom"]


class Settings(BaseSettings):
    """Runtime settings, populated from environment / .env."""

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    database_url: str = Field(
        default="postgresql+psycopg://postgres@localhost:5432/hiring_tracker",
        alias="DATABASE_URL",
    )
    anthropic_api_key: Optional[str] = Field(default=None, alias="ANTHROPIC_API_KEY")
    anthropic_model: str = Field(default="claude-opus-4-8", alias="ANTHROPIC_MODEL")

    repost_window_days: int = Field(default=45, alias="HST_REPOST_WINDOW_DAYS")
    stale_after_days: int = Field(default=3, alias="HST_STALE_AFTER_DAYS")
    user_agent: str = Field(
        default="hiring-signal-tracker/0.1 (+research)", alias="HST_USER_AGENT"
    )
    live_adapters: bool = Field(default=False, alias="HST_LIVE")

    @field_validator("live_adapters", mode="before")
    @classmethod
    def _coerce_bool(cls, v):
        if isinstance(v, str):
            return v.strip().lower() in {"1", "true", "yes", "on"}
        return v


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


class WatchlistEntry(BaseModel):
    """A single company as declared in watchlist.yaml."""

    ticker: Optional[str] = None
    name: str
    ats_type: AtsType
    ats_token: str
    careers_url: Optional[str] = None
    notes: Optional[str] = None

    @field_validator("ats_token")
    @classmethod
    def _token_nonempty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("ats_token must be a non-empty board token / slug")
        return v.strip()


def load_watchlist(path: Path | str | None = None) -> list[WatchlistEntry]:
    """Parse and validate ``config/watchlist.yaml``.

    Fails loudly on a malformed entry rather than silently dropping a company.
    """
    p = Path(path) if path else DEFAULT_WATCHLIST
    if not p.exists():
        raise FileNotFoundError(f"watchlist not found: {p}")
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    raw = data.get("companies", [])
    if not isinstance(raw, list):
        raise ValueError("watchlist.yaml: 'companies' must be a list")
    entries = [WatchlistEntry(**item) for item in raw]

    # Guard against ambiguous duplicates on the natural key we upsert by.
    seen: set[tuple[Optional[str], str] | str] = set()
    for e in entries:
        key = (e.ticker, e.ats_token) if e.ticker else f"__{e.ats_type}:{e.ats_token}"
        if key in seen:
            raise ValueError(
                f"duplicate watchlist entry for ticker={e.ticker} token={e.ats_token}"
            )
        seen.add(key)
    return entries

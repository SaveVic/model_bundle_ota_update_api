from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Repo-relative anchor: .../week-3/demo_api
APP_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    """Runtime configuration. Every field has a working default — a fresh
    checkout runs with no ``.env``."""

    model_config = SettingsConfigDict(
        env_prefix="DEMO_API_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = "sqlite:///./demo_api.db"
    storage_dir: Path = Path("storage")
    public_base_url: str = "https://3gfz53jt-8000.asse.devtunnels.ms"
    pinned_pubkey_path: Path = Path("keys/bundle_signing_key_pub.pem")

    require_signature: bool = True
    auto_promote_on_publish: bool = True
    index_cache_seconds: int = 60

    seed_channels: list[str] = Field(default_factory=lambda: ["stable", "beta"])

    # --- derived --------------------------------------------------------- #

    def resolved_storage_dir(self) -> Path:
        p = self.storage_dir
        return p if p.is_absolute() else (APP_ROOT / p)

    def resolved_pubkey_path(self) -> Path:
        p = self.pinned_pubkey_path
        return p if p.is_absolute() else (APP_ROOT / p)

    def base_url(self) -> str:
        return self.public_base_url.rstrip("/")


@lru_cache
def get_settings() -> Settings:
    return Settings()

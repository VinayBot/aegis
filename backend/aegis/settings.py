"""Process-level settings from the environment (AEGIS_*). Gateway behaviour lives in the YAML."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent
PROJECT_DIR = BACKEND_DIR.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AEGIS_", env_file=".env", extra="ignore")

    config_path: Path = BACKEND_DIR / "config" / "gateway.yaml"
    redis_url: str = "redis://127.0.0.1:6379/0"
    redis_prefix: str = "aegis"
    otlp_endpoint: str = "http://127.0.0.1:4318"
    jaeger_query_url: str = "http://127.0.0.1:16686"
    jaeger_ui_url: str = "http://127.0.0.1:16686"
    service_name: str = "aegis-gateway"
    ledger_path: Path = PROJECT_DIR / "var" / "ledger.sqlite3"
    run_dir: Path = PROJECT_DIR / "var" / "run"
    docs_dir: Path = PROJECT_DIR / "docs"
    project_dir: Path = PROJECT_DIR
    self_url: str = "http://127.0.0.1:8000"
    # Dev-only fault injection. Off unless explicitly enabled; every action is logged loudly.
    chaos_enabled: bool = False
    tracing_enabled: bool = True
    embedder_model: str = "BAAI/bge-small-en-v1.5"
    health_interval_seconds: float = 0.5
    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()

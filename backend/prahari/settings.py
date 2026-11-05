"""Process-level settings from the environment (PRAHARI_*). Gateway behaviour lives in the YAML."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent
PROJECT_DIR = BACKEND_DIR.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PRAHARI_", env_file=".env", extra="ignore")

    # Secure by default: "production" unless the environment says otherwise. Production requires an
    # admin key for every control-plane call and refuses chaos. `scripts/dev.sh` sets dev, where a
    # caller on this machine (loopback) is the local operator, so the demo site needs no key.
    env: Literal["dev", "production"] = "production"
    # Admin credential from the environment (a secret store in production), never from the YAML.
    admin_key: SecretStr | None = None
    # Production ignores plaintext keys in the YAML (they are readable by anyone who can read the
    # file, and the repo's demo keys are public): only `sha256:` entries authenticate. Set this
    # to accept plaintext keys anyway, e.g. for a local container demo.
    allow_plaintext_keys: bool = False
    # Short-lived JWTs (HS256) exchanged for an API key at POST /v1/auth/token. Unset: the
    # exchange is off and only API keys authenticate. scripts/dev.sh sets a fresh random secret.
    jwt_secret: SecretStr | None = None
    jwt_ttl_seconds: int = 900
    log_format: Literal["json", "text"] = "json"
    log_level: str = "INFO"
    config_path: Path = BACKEND_DIR / "config" / "gateway.yaml"
    redis_url: str = "redis://127.0.0.1:6379/0"
    redis_prefix: str = "prahari"
    otlp_endpoint: str = "http://127.0.0.1:4318"
    jaeger_query_url: str = "http://127.0.0.1:16686"
    jaeger_ui_url: str = "http://127.0.0.1:16686"
    service_name: str = "prahari-gateway"
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
    # Langfuse (cloud or self-hosted v3+): traces are also sent to its OTLP endpoint when both
    # keys are set. Jaeger keeps receiving the same spans.
    langfuse_host: str = "https://cloud.langfuse.com"
    langfuse_public_key: str | None = None
    langfuse_secret_key: SecretStr | None = None

    @property
    def is_dev(self) -> bool:
        return self.env == "dev"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()

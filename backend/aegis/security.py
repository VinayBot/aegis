"""Who is calling, and what they may do.

Data plane (`/v1/*`): an API key from the YAML resolves to a tenant. Keys may be stored as
`sha256:<hex>` so the config file holds no usable secret; every comparison is constant-time and
checks every key, so timing does not reveal which entry matched. Raw keys never leave this
module: the ledger, metrics, events, Redis key names and every API response carry the key's
public `id` instead.

Control plane (`/api/*`, `/ws/*`, `/metrics`), three levels:
- public:   liveness, readiness, the project docs, the scenario list.
- viewer:   dashboards and request data. An admin sees every tenant; a tenant key sees only its
            own tenant's rows (no cross-tenant reads by id).
- admin:    anything that changes state: chaos, breakers, cache, budgets, load generators.

In production (the default) the admin level needs an admin credential: a YAML key with
`role: admin`, or `AEGIS_ADMIN_KEY`. In dev (`AEGIS_ENV=dev`, set by scripts/dev.sh) a caller on
this machine's loopback interface is the local operator and acts as admin, so the demo site needs
no key. Loopback is never trusted in production, where a reverse proxy would make every caller
look local.

    uv run python -m aegis.security hash <key>    # prints the sha256: entry for the YAML
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import sys
from dataclasses import dataclass
from typing import Literal

from fastapi import HTTPException, Request, WebSocket

from aegis.config import GatewayConfig, KeyConfig
from aegis.settings import Settings

HASH_PREFIX = "sha256:"
ADMIN_ENV_KEY_ID = "admin-env"


def digest(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def plaintext_allowed(settings: Settings) -> bool:
    return settings.is_dev or settings.allow_plaintext_keys


def resolve_key(
    cfg: GatewayConfig, presented: str | None, allow_plaintext: bool = True
) -> KeyConfig | None:
    """Constant-time lookup across every key; None when nothing matches. With
    `allow_plaintext=False` (production) only `sha256:` entries can match."""
    if not presented:
        return None
    want = digest(presented)
    match: KeyConfig | None = None
    for entry, key_cfg in cfg.keys.items():
        hashed = entry.startswith(HASH_PREFIX)
        if not hashed and not allow_plaintext:
            continue
        stored = entry[len(HASH_PREFIX) :].lower() if hashed else digest(entry)
        if hmac.compare_digest(stored, want) and match is None:
            match = key_cfg
    return match


def plaintext_keys(cfg: GatewayConfig) -> list[str]:
    return [kc.id for entry, kc in cfg.keys.items() if not entry.startswith(HASH_PREFIX)]


@dataclass(frozen=True)
class Principal:
    kind: Literal["admin", "tenant", "local_operator"]
    tenant: str | None
    key_id: str

    @property
    def is_admin(self) -> bool:
        return self.kind in ("admin", "local_operator")

    def can_see(self, tenant: str | None) -> bool:
        return self.is_admin or (tenant is not None and tenant == self.tenant)


def bearer(headers: dict[str, str] | object) -> str | None:
    get = getattr(headers, "get", None)
    if get is None:
        return None
    value = get("authorization") or ""
    if value.lower().startswith("bearer "):
        return value[7:].strip() or None
    return get("x-api-key") or None


def _is_loopback(host: str | None) -> bool:
    if not host:
        return False
    if host in ("localhost", "testclient"):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def principal_for(
    settings: Settings,
    cfg: GatewayConfig,
    presented: str | None,
    client_host: str | None,
    origin: str | None = None,
) -> Principal | None:
    admin = settings.admin_key.get_secret_value() if settings.admin_key else None
    if presented and admin and hmac.compare_digest(digest(presented), digest(admin)):
        return Principal("admin", None, ADMIN_ENV_KEY_ID)
    key_cfg = resolve_key(cfg, presented, plaintext_allowed(settings))
    if key_cfg is not None:
        if key_cfg.role == "admin":
            return Principal("admin", key_cfg.tenant, key_cfg.id)
        return Principal("tenant", key_cfg.tenant, key_cfg.id)
    if (
        presented is None
        and settings.is_dev
        and _is_loopback(client_host)
        and _trusted_origin(settings, origin)
    ):
        return Principal("local_operator", None, "local")
    return None


def _trusted_origin(settings: Settings, origin: str | None) -> bool:
    """A browser names the page that made a request. In dev, loopback means "the operator" only
    for the AEGIS site itself (or a non-browser client, which sends no Origin), so another
    website open in the same browser cannot drive the gateway (no CSRF, no WebSocket hijack)."""
    if not origin:
        return True
    own = {settings.self_url.rstrip("/"), "http://127.0.0.1:8000", "http://localhost:8000"}
    return origin.rstrip("/") in own | {o.rstrip("/") for o in settings.cors_origins}


def _resolve(request: Request | WebSocket) -> Principal | None:
    state = request.app.state.state
    presented = bearer(request.headers)
    if presented is None and isinstance(request, WebSocket):
        # browsers cannot set headers on a WebSocket; the key may come as a query parameter
        presented = request.query_params.get("key") or None
    host = request.client.host if request.client else None
    origin = request.headers.get("origin")
    return principal_for(state.settings, state.config.get(), presented, host, origin)


def require_viewer(request: Request) -> Principal:
    principal = _resolve(request)
    if principal is None:
        raise HTTPException(
            401,
            "An API key is required (Authorization: Bearer <key>).",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return principal


def require_admin(request: Request) -> Principal:
    principal = require_viewer(request)
    if not principal.is_admin:
        raise HTTPException(403, "This action needs an admin key.")
    return principal


def websocket_principal(ws: WebSocket) -> Principal | None:
    """The live event stream carries every tenant's traffic: admins and the local operator only."""
    principal = _resolve(ws)
    return principal if principal is not None and principal.is_admin else None


def main(argv: list[str]) -> int:
    if len(argv) == 3 and argv[1] == "hash":
        print(f"{HASH_PREFIX}{digest(argv[2])}")
        return 0
    print("usage: python -m aegis.security hash <key>", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

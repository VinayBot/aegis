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
import time
import uuid
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal

import jwt
from fastapi import HTTPException, Request, WebSocket

from aegis.config import GatewayConfig, KeyConfig
from aegis.settings import Settings

HASH_PREFIX = "sha256:"
ADMIN_ENV_KEY_ID = "admin-env"


def digest(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


@dataclass
class FailureLimiter:
    """Slows down key guessing: a client address with `limit` failed authentications inside
    `window_s` is refused (429) until the oldest failure ages out. Behind a reverse proxy, run
    uvicorn with --proxy-headers and --forwarded-allow-ips so the address is the real client's."""

    limit: int = 20
    window_s: float = 60.0
    clock: Callable[[], float] = time.monotonic
    failures: dict[str, deque[float]] = field(default_factory=dict)

    def _recent(self, client: str) -> deque[float]:
        q = self.failures.setdefault(client, deque())
        cutoff = self.clock() - self.window_s
        while q and q[0] < cutoff:
            q.popleft()
        return q

    def retry_after(self, client: str | None) -> float:
        """Seconds until this client may try again; 0 when it is not blocked."""
        if not client:
            return 0.0
        q = self._recent(client)
        if len(q) < self.limit:
            return 0.0
        return max(0.1, q[0] + self.window_s - self.clock())

    def record(self, client: str | None) -> None:
        if client:
            self._recent(client).append(self.clock())
            if len(self.failures) > 10_000:  # bounded memory under a spray of addresses
                self.failures.pop(next(iter(self.failures)))


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


JWT_ISSUER = "aegis"
JWT_MAX_TTL = 86_400


def issue_token(
    settings: Settings, key: KeyConfig, ttl_seconds: int | None = None
) -> tuple[str, int]:
    """A short-lived HS256 token standing in for an API key: for browsers and mobile apps, which
    should not hold a long-lived key. It names the key (by id), the tenant and the role; it can
    never grant more than the key it was exchanged for."""
    if settings.jwt_secret is None:
        raise ValueError("JWT exchange is off (AEGIS_JWT_SECRET is not set)")
    ttl = max(60, min(ttl_seconds or settings.jwt_ttl_seconds, JWT_MAX_TTL))
    now = int(time.time())
    claims = {
        "iss": JWT_ISSUER,
        "sub": key.id,
        "tenant": key.tenant,
        "role": key.role,
        "iat": now,
        "exp": now + ttl,
        "jti": uuid.uuid4().hex,
    }
    return jwt.encode(claims, settings.jwt_secret.get_secret_value(), algorithm="HS256"), ttl


def verify_token(settings: Settings, cfg: GatewayConfig, token: str) -> KeyConfig | None:
    """The key a valid token stands for, or None. The key must still exist: removing it from the
    YAML revokes every token issued for it. The role is never above the key's own."""
    if settings.jwt_secret is None or token.count(".") != 2:
        return None
    try:
        claims = jwt.decode(
            token,
            settings.jwt_secret.get_secret_value(),
            algorithms=["HS256"],  # never "none", never an algorithm the token chooses
            issuer=JWT_ISSUER,
            options={"require": ["exp", "iat", "sub", "iss"]},
        )
    except jwt.PyJWTError:
        return None
    key = next((k for k in cfg.keys.values() if k.id == claims.get("sub")), None)
    if key is None or key.tenant != claims.get("tenant"):
        return None
    return key


def resolve_credential(
    settings: Settings, cfg: GatewayConfig, presented: str | None
) -> KeyConfig | None:
    """An API key, or a JWT exchanged for one."""
    if not presented:
        return None
    return resolve_key(cfg, presented, plaintext_allowed(settings)) or verify_token(
        settings, cfg, presented
    )


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
    key_cfg = resolve_credential(settings, cfg, presented)
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


def guard_failures(request: Request | WebSocket) -> None:
    """429 when this client has failed authentication too often (see FailureLimiter)."""
    limiter: FailureLimiter = request.app.state.state.auth_failures
    host = request.client.host if request.client else None
    wait = limiter.retry_after(host)
    if wait:
        raise HTTPException(
            429,
            "Too many failed authentications from this address. Retry later.",
            headers={"Retry-After": str(max(1, round(wait + 0.49)))},
        )


def note_failure(request: Request | WebSocket) -> None:
    limiter: FailureLimiter = request.app.state.state.auth_failures
    limiter.record(request.client.host if request.client else None)


def require_viewer(request: Request) -> Principal:
    guard_failures(request)
    principal = _resolve(request)
    if principal is None:
        if bearer(request.headers) is not None:
            note_failure(request)  # a wrong key, not merely a missing one
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

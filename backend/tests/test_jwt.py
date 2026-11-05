"""Short-lived JWTs exchanged for API keys: tenant context, roles, expiry, revocation."""

from __future__ import annotations

import time

import jwt
import pytest

from tests.conftest import Gateway

SECRET = "test-jwt-secret-0123456789abcdef0123"


async def exchange(gateway: Gateway, key: str, **body: object) -> dict[str, object]:
    r = await gateway.client.post(
        "/v1/auth/token", json=body or None, headers={"authorization": f"Bearer {key}"}
    )
    assert r.status_code == 200, r.text
    assert r.headers["cache-control"] == "no-store"
    return r.json()


def bearer(token: object) -> dict[str, str]:
    return {"authorization": f"Bearer {token}"}


async def test_a_token_stands_in_for_its_key(gateway: Gateway) -> None:
    tok = await exchange(gateway, "k-demo")
    assert tok["token_type"] == "bearer" and tok["tenant"] == "demo" and tok["expires_in"] == 900
    claims = jwt.decode(str(tok["access_token"]), SECRET, algorithms=["HS256"], issuer="aegis")
    assert claims["sub"] == "key_" + claims["sub"][4:] and "k-demo" not in str(
        claims
    )  # id, not key
    r = await gateway.chat("hello with a token", key=str(tok["access_token"]))
    assert r.status_code == 200
    rows = (await gateway.client.get("/api/requests", headers=bearer(tok["access_token"]))).json()
    assert rows and all(row["tenant"] == "demo" for row in rows)


@pytest.mark.parametrize("gateway", [{"env": "production"}], indirect=True)
async def test_a_tenant_token_cannot_reach_admin_routes(gateway: Gateway) -> None:
    tenant = await exchange(gateway, "k-demo")
    admin = await exchange(gateway, "k-admin")
    c = gateway.client
    assert (await c.get("/api/config", headers=bearer(tenant["access_token"]))).status_code == 403
    assert (await c.get("/api/config", headers=bearer(admin["access_token"]))).status_code == 200


async def test_expired_tampered_and_revoked_tokens_fail(gateway: Gateway) -> None:
    tok = str((await exchange(gateway, "k-demo"))["access_token"])
    claims = jwt.decode(tok, SECRET, algorithms=["HS256"], issuer="aegis")
    expired = jwt.encode({**claims, "exp": int(time.time()) - 5}, SECRET, algorithm="HS256")
    forged = jwt.encode({**claims, "tenant": "other"}, SECRET, algorithm="HS256")
    wrong_key = jwt.encode(claims, "not-the-secret-0123456789abcdef0123", algorithm="HS256")
    unsigned = jwt.encode(claims, key="", algorithm="none")
    for bad in (expired, forged, wrong_key, unsigned, tok[:-3] + "abc"):
        assert (await gateway.chat("bad token", key=bad)).status_code == 401
    # removing the key from the config revokes every token issued for it
    cfg = gateway.state.config.get()
    raw = cfg.model_dump()
    del raw["keys"]["k-demo"]
    gateway.state.config.replace(type(cfg).model_validate(raw))
    assert (await gateway.chat("revoked", key=tok)).status_code == 401


async def test_a_token_cannot_mint_a_token_and_ttl_is_bounded(gateway: Gateway) -> None:
    tok = str((await exchange(gateway, "k-demo", ttl_seconds=120))["access_token"])
    r = await gateway.client.post("/v1/auth/token", headers=bearer(tok))
    assert r.status_code == 401
    too_long = await gateway.client.post(
        "/v1/auth/token", json={"ttl_seconds": 10**6}, headers=bearer("k-demo")
    )
    assert too_long.status_code == 422


@pytest.mark.parametrize("gateway", [{"jwt_secret": None}], indirect=True)
async def test_exchange_is_off_without_a_secret(gateway: Gateway) -> None:
    r = await gateway.client.post("/v1/auth/token", headers=bearer("k-demo"))
    assert r.status_code == 501

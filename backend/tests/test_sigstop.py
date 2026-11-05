"""Integration: a real backend process is frozen with SIGSTOP (what the Chaos Lab does to the MLX
primary). Requests must keep succeeding via the fallback, the primary's breaker must open from
failed probes, and SIGCONT must let it recover."""

from __future__ import annotations

import asyncio
import os
import signal
import subprocess
import sys
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest
import yaml

from tests.conftest import Gateway, free_port, serve, stop, write_config

BACKEND_DIR = Path(__file__).resolve().parent.parent


@pytest.fixture
async def stoppable(
    tmp_path: Path, fakes: Any, require_redis: None
) -> AsyncIterator[tuple[Gateway, subprocess.Popen[bytes]]]:
    import uuid

    from prahari.main import create_app
    from prahari.settings import Settings

    port = free_port()
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "tests.fake_backend",
            "--port",
            str(port),
            "--model",
            "fake-primary",
        ],
        cwd=BACKEND_DIR,
    )
    async with httpx.AsyncClient() as c:
        for _ in range(100):
            try:
                if (await c.get(f"http://127.0.0.1:{port}/v1/models")).status_code == 200:
                    break
            except httpx.HTTPError:
                await asyncio.sleep(0.05)
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "primary.pid").write_text(str(proc.pid))
    cfg = write_config(tmp_path, {"primary": port, "fallback": fakes["fallback"].port})
    data = yaml.safe_load(cfg.read_text())
    data["backends"]["primary"]["process"] = "primary"  # lets chaos SIGSTOP the real process
    cfg.write_text(yaml.safe_dump(data))
    gw_port = free_port()
    settings = Settings(
        env="dev",  # chaos (SIGSTOP) is dev-only
        config_path=cfg,
        redis_prefix=f"praharitest-{uuid.uuid4().hex[:8]}",
        ledger_path=tmp_path / "l.sqlite3",
        run_dir=run_dir,
        docs_dir=tmp_path,
        project_dir=tmp_path,
        self_url=f"http://127.0.0.1:{gw_port}",
        chaos_enabled=True,
        tracing_enabled=False,
        health_interval_seconds=0.2,
    )
    app = create_app(settings)
    server, task = await serve(app, gw_port)
    async with httpx.AsyncClient(base_url=settings.self_url, timeout=20) as client:
        yield Gateway(client, app, settings.self_url), proc
    os.kill(proc.pid, signal.SIGCONT)
    proc.terminate()
    proc.wait(5)
    await stop(server, task)


async def test_sigstop_primary_breaker_opens_and_traffic_fails_over(
    stoppable: tuple[Gateway, Any],
) -> None:
    gw, proc = stoppable
    ok = await gw.chat("before", headers={"cache-control": "no-cache"})
    assert ok.headers["x-prahari-backend"] == "primary"

    r = await gw.client.post(
        "/api/chaos", json={"backend": "primary", "action": "kill", "value": True}
    )
    assert r.json()["applied"]["mode"] == "sigstop"
    state = subprocess.run(
        ["ps", "-o", "stat=", "-p", str(proc.pid)], capture_output=True, text=True
    )
    assert state.stdout.strip().startswith("T")  # really stopped by the kernel

    during = await gw.chat("during", headers={"cache-control": "no-cache"})
    assert during.status_code == 200
    assert during.headers["x-prahari-backend"] == "fallback"  # hedged or failed over, no user error

    breaker = gw.state.router.runtimes["primary"].breaker
    deadline = time.monotonic() + 5
    while breaker.current_state() != "open" and time.monotonic() < deadline:
        await asyncio.sleep(0.1)
    assert breaker.current_state() == "open"

    calls_before = (await gw.client.get("/api/architecture")).json()
    primary = next(n for n in calls_before["nodes"] if n["id"] == "backend:primary")
    assert primary["breaker"]["state"] == "open"
    skipped = await gw.chat("skips primary", headers={"cache-control": "no-cache"})
    assert skipped.headers["x-prahari-backend"] == "fallback"

    await gw.client.post(
        "/api/chaos", json={"backend": "primary", "action": "kill", "value": False}
    )
    breaker.opened_at = (breaker.opened_at or 0) - breaker.open_seconds  # fast-forward cool-down
    healed = await gw.chat("probe", headers={"cache-control": "no-cache"})
    assert healed.headers["x-prahari-backend"] == "primary"
    assert breaker.current_state() == "closed"

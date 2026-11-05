"""Measures the real resident memory (RSS) of every PRAHARI component on this Mac.

Processes are found by command line; Docker containers are measured inside the Colima VM with
`docker stats`, and the VM's own reservation is reported separately.
"""

from __future__ import annotations

import json
import subprocess

import psutil

MATCH = {
    "gateway (FastAPI + embedder + spaCy)": ["uvicorn", "prahari.main:app"],
    "mlx_lm.server (Qwen2.5-3B 4-bit)": ["mlx_lm.server"],
    "ollama (server + loaded llama3.2:3b)": ["ollama"],
    "vite dev server": ["vite"],
}


def main() -> None:
    rows: list[tuple[str, float]] = []
    for label, needles in MATCH.items():
        total = 0
        for p in psutil.process_iter(["cmdline", "name", "memory_info"]):
            cmd = " ".join(p.info.get("cmdline") or []) or (p.info.get("name") or "")
            if all(n in cmd for n in needles) and p.info.get("memory_info"):
                total += p.info["memory_info"].rss
        rows.append((label, total / 2**20))
    try:
        out = subprocess.run(["docker", "stats", "--no-stream", "--format", "{{json .}}"],
                             capture_output=True, text=True, timeout=20).stdout
        for line in out.splitlines():
            d = json.loads(line)
            used = d["MemUsage"].split("/")[0].strip()
            value = float(used[:-3]) * (1024 if used.endswith("GiB") else 1 / 1024 if used.endswith("KiB") else 1)
            rows.append((f"container {d['Name']}", value))
    except Exception as exc:
        rows.append((f"docker stats unavailable ({exc})", 0))
    width = max(len(r[0]) for r in rows)
    for label, mib in rows:
        print(f"{label:<{width}}  {mib:8.0f} MiB")
    total = sum(m for _, m in rows)
    print(f"{'total (processes + containers)':<{width}}  {total:8.0f} MiB  = {total / 1024:.2f} GiB")
    print("note: the Colima VM reserves 2 GiB for the containers; see docs/VALIDATION_REPORT.md")


if __name__ == "__main__":
    main()

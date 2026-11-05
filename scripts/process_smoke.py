"""Start Web and Worker roles briefly and verify Web readiness."""

from __future__ import annotations

import json
import os
import subprocess  # nosec B404
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _start(role: str, port: int) -> subprocess.Popen:
    environment = os.environ.copy()
    environment.update({"PROCESS_ROLE": role, "PORT": str(port)})
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return subprocess.Popen(  # nosec B603
        [sys.executable, "main.py"],
        cwd=ROOT,
        env=environment,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=flags,
    )


def _stop(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def main() -> int:
    port = int(os.environ.get("SMOKE_PORT", "17860"))
    web = _start("web", port)
    worker = _start("worker", port)
    ready = False
    try:
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if web.poll() is not None or worker.poll() is not None:
                break
            try:
                with urllib.request.urlopen(  # nosec B310
                    f"http://127.0.0.1:{port}/health/ready", timeout=2
                ) as response:
                    ready = response.status == 200
            except (urllib.error.URLError, TimeoutError):
                pass
            if ready:
                break
            time.sleep(0.5)
        result = {
            "ready": ready,
            "web_running": web.poll() is None,
            "worker_running": worker.poll() is None,
        }
        print(json.dumps(result))
        return 0 if all(result.values()) else 1
    finally:
        _stop(web)
        _stop(worker)


if __name__ == "__main__":
    raise SystemExit(main())

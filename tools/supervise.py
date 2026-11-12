from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path


def server_command(root: Path, host: str, port: int) -> list[str]:
    return [sys.executable, str(root / "run.py"), "--host", host, "--port", str(port)]


def supervise(root: Path, host: str, port: int, *, max_restarts: int | None = None) -> int:
    delays = (5, 15, 30, 60)
    restarts = 0
    while True:
        child = subprocess.Popen(server_command(root, host, port), cwd=root)
        try:
            return_code = child.wait()
        except KeyboardInterrupt:
            child.terminate()
            try:
                child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                child.kill()
            return 0
        if return_code == 0:
            return 0
        restarts += 1
        if max_restarts is not None and restarts > max_restarts:
            return return_code
        delay = delays[min(restarts - 1, len(delays) - 1)]
        print(f"Elite RF backend stopped unexpectedly. Restarting in {delay} seconds.", flush=True)
        time.sleep(delay)


def main() -> int:
    parser = argparse.ArgumentParser(description="Keep the Elite RF backend running after an unexpected exit.")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--max-restarts", type=int)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    return supervise(root, args.host, args.port, max_restarts=args.max_restarts)


if __name__ == "__main__":
    raise SystemExit(main())

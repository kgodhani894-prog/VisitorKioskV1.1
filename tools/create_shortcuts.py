"""Create local Windows shortcuts targeting an approved Python installation."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = ROOT.parent if ROOT.name.lower() == "app" else ROOT


def path_contains_codex_component(path: Path) -> bool:
    return any(part.lower() == ".codex" for part in path.parts)


def has_supported_python_version(python_path: Path) -> bool:
    result = subprocess.run(
        [
            str(python_path),
            "-c",
            "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode == 0


def authenticode_details(python_path: Path) -> tuple[str, str]:
    if sys.platform != "win32":
        return "Valid", "Non-Windows platform"
    literal_path = str(python_path).replace("'", "''")
    command = (
        f"$sig = Get-AuthenticodeSignature -LiteralPath '{literal_path}'; "
        "$signer = ''; "
        "if ($sig.SignerCertificate) { $signer = $sig.SignerCertificate.Subject }; "
        "[pscustomobject]@{ Status = $sig.Status.ToString(); Signer = $signer } | ConvertTo-Json -Compress"
    )
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-Command", command],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0 or not result.stdout.strip():
        return "Unknown", ""
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        return "Unknown", ""
    return str(payload.get("Status", "Unknown")), str(payload.get("Signer", ""))


def find_python(explicit: Path | None) -> Path:
    candidates = []
    if explicit is not None:
        candidates.append(explicit)
    candidates.append(Path(sys.executable))
    for name in ("python", "py"):
        found = shutil.which(name)
        if found:
            candidates.append(Path(found))
    for candidate in candidates:
        resolved = candidate.resolve()
        if path_contains_codex_component(resolved):
            continue
        if resolved.name.lower() != "python.exe" or not resolved.is_file():
            continue
        if not has_supported_python_version(resolved):
            continue
        status, _signer = authenticode_details(resolved)
        if status == "Valid":
            return resolved
    raise RuntimeError("A signed non-Codex Python 3.11+ interpreter was not found.")


def create_shortcut(path: Path, python_path: Path, arguments: str, description: str) -> None:
    # Windows creates the shortcut locally; the .lnk is intentionally machine-specific.
    def quote(value: str) -> str:
        return value.replace("'", "''")

    command = (
        "$shell = New-Object -ComObject WScript.Shell; "
        f"$shortcut = $shell.CreateShortcut('{quote(str(path))}'); "
        f"$shortcut.TargetPath = '{quote(str(python_path))}'; "
        f"$shortcut.Arguments = '{quote(arguments)}'; "
        f"$shortcut.WorkingDirectory = '{quote(str(ROOT))}'; "
        f"$shortcut.Description = '{quote(description)}'; "
        "$shortcut.Save()"
    )
    result = subprocess.run(["powershell.exe", "-NoProfile", "-Command", command], capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "Windows could not create the shortcut.")


def main() -> int:
    parser = argparse.ArgumentParser(description="Create local Elite RF kiosk shortcuts.")
    parser.add_argument("--python", type=Path, help="Approved Python executable to target.")
    parser.add_argument("--output-dir", type=Path, default=PACKAGE_ROOT)
    args = parser.parse_args()
    python_path = find_python(args.python)
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    run_file = ROOT / "run.py"
    production_args = f'"{run_file}" --host 0.0.0.0 --port 8080 --open-admin --pause-on-error'
    test_args = f'"{run_file}" --test-mode --host 127.0.0.1 --port 0 --open-admin --pause-on-error'
    create_shortcut(output_dir / "START ELITE RF VISITOR KIOSK.lnk", python_path, production_args, "Start the Elite RF production kiosk")
    create_shortcut(output_dir / "START ELITE RF SAFE TEST MODE.lnk", python_path, test_args, "Start disposable loopback-only test mode")
    print(f"Created local shortcuts targeting {python_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

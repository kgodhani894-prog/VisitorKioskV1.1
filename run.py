from __future__ import annotations

import argparse
import json
import socket
import os
import secrets
import tempfile
import sys
import webbrowser
from pathlib import Path
from kiosk_server.server import TEST_RUNTIME_MARKER, TEST_RUNTIME_MARKER_CONTENT, create_server, is_synthetic_test_config, load_local_env, owner_setup_required, validate_test_runtime_root
from kiosk_server.security import hash_password
from kiosk_server.server import is_loopback_host

ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parent if ROOT.name.lower() == "app" and (ROOT.parent / ".env.example").exists() else ROOT


def display_host_for_launch(host: str, tls_enabled: bool, public_hostname: str | None = None) -> str:
    if tls_enabled and public_hostname:
        return public_hostname
    if host in ("0.0.0.0", "::"):
        return socket.gethostname() if tls_enabled else "127.0.0.1"
    return host


def format_launch_base_url(scheme: str, display_host: str, backend_port: int, *, external_https: bool = False, tls_enabled: bool = False) -> str:
    """Report Apache's standard HTTPS origin when TLS terminates at the proxy."""
    if external_https and not tls_enabled and scheme == "https":
        return f"{scheme}://{display_host}"
    return f"{scheme}://{display_host}:{backend_port}"


def create_test_root(destination: Path | None = None) -> Path:
    root = Path(destination) if destination is not None else Path(tempfile.mkdtemp(prefix="elite-rf-kiosk-test-"))
    if destination is not None:
        validate_test_runtime_root(root)
    root.mkdir(parents=True, exist_ok=True)
    (root / TEST_RUNTIME_MARKER).write_text(TEST_RUNTIME_MARKER_CONTENT, encoding="utf-8")
    config = {
        "database_path": "data/visitor-kiosk.sqlite3",
        "photo_storage_path": "storage/photos",
        "admin_password_hash": hash_password("test-owner-password-1234"),
        "session_secret": secrets.token_urlsafe(32),
        "cookie_secure": False,
        "max_photo_bytes": 1_000_000,
        "automatic_backup_hours": 24,
        "notification_settings": {
            "enabled": False,
            "provider": "microsoft_graph_email",
            "message_template": "{visitor_name} from {company} is waiting for you at {location}.",
        },
        "employees": [{
            "name": "Test Host",
            "email": "test-host@example.invalid",
            "phone": "",
            "alert_method": "email",
            "active": 1,
        }],
    }
    config_path = root / "config.json"
    if config_path.exists():
        existing = json.loads(config_path.read_text(encoding="utf-8"))
        if not is_synthetic_test_config(existing):
            raise ValueError("Test mode runtime root already contains production-looking config.json.")
    else:
        config_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    return root


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Elite RF visitor kiosk backend.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--runtime-root", type=Path)
    parser.add_argument("--test-mode", action="store_true")
    parser.add_argument("--tls-cert", type=Path)
    parser.add_argument("--tls-key", type=Path)
    parser.add_argument("--open-admin", action="store_true", help="Open the admin page after startup.")
    parser.add_argument("--pause-on-error", action="store_true", help="Pause after a startup failure so it can be read.")
    args = parser.parse_args()

    server = None
    try:
        if args.test_mode and not is_loopback_host(args.host):
            raise ValueError("Test mode must bind to a loopback host such as 127.0.0.1.")

        runtime_root = args.runtime_root
        if args.test_mode and runtime_root is None:
            runtime_root = create_test_root()
            print("TEST MODE - NO REAL EMAIL - DISPOSABLE DATA")
            print(f"Test data root: {runtime_root}")
        elif args.test_mode:
            runtime_root = create_test_root(runtime_root)

        if not args.test_mode:
            if PROJECT_ROOT == ROOT:
                load_local_env()
            else:
                load_local_env(PROJECT_ROOT)
            tls_cert = args.tls_cert or (Path(os.environ["KIOSK_TLS_CERT_PATH"]) if os.environ.get("KIOSK_TLS_CERT_PATH") else None)
            tls_key = args.tls_key or (Path(os.environ["KIOSK_TLS_KEY_PATH"]) if os.environ.get("KIOSK_TLS_KEY_PATH") else None)
            public_hostname = os.environ.get("KIOSK_PUBLIC_HOSTNAME")
            if public_hostname:
                public_hostname = public_hostname.strip() or None
            external_https = os.environ.get("KIOSK_EXTERNAL_HTTPS", "").strip().lower() in {"1", "true", "yes", "on"}
        else:
            tls_cert = args.tls_cert
            tls_key = args.tls_key
            public_hostname = None
            external_https = False
        if bool(tls_cert) != bool(tls_key):
            raise ValueError("Both TLS certificate and key must be configured together.")

        tls_enabled = bool(tls_cert and tls_key)
        server = create_server(args.host, args.port, runtime_root=runtime_root, test_mode=args.test_mode, tls_cert_path=tls_cert, tls_key_path=tls_key)
        externally_secure = tls_enabled or external_https
        display_host = display_host_for_launch(args.host, externally_secure, public_hostname)
        scheme = "https" if externally_secure else "http"
        if scheme == "http" and not is_loopback_host(args.host):
            print("Warning: remote camera capture on the LAN kiosk requires trusted HTTPS.")
        elif tls_enabled and not public_hostname:
            print("Warning: set KIOSK_PUBLIC_HOSTNAME to the hostname on the certificate for the clearest HTTPS URLs.")
        if not args.test_mode and tls_enabled and owner_setup_required(server.app["config"], allow_environment=True):
            print("Warning: complete first-run owner setup over http://127.0.0.1 before relying on HTTPS for the remote kiosk.")
        base_url = format_launch_base_url(
            scheme,
            display_host,
            server.server_port,
            external_https=external_https,
            tls_enabled=tls_enabled,
        )
        admin_url = f"{base_url}/admin"
        print(f"Elite RF Visitor Kiosk running at {base_url}")
        print(f"Admin:  {admin_url}")
        print(f"Kiosk:  {base_url}/kiosk")
        print("Press Ctrl+C to stop.")
        if getattr(args, "open_admin", False):
            try:
                if not webbrowser.open(admin_url):
                    print(f"The server is ready. Open this URL manually: {admin_url}")
            except Exception:
                print(f"The server is ready. Open this URL manually: {admin_url}")
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping server.")
    except SystemExit:
        raise
    except Exception as exc:
        print(f"Startup failed: {exc}", file=sys.stderr)
        if getattr(args, "pause_on_error", False):
            try:
                input("Press Enter to close.")
            except EOFError:
                pass
        raise SystemExit(1) from exc
    finally:
        if server is not None:
            server.server_close()


if __name__ == "__main__":
    main()


